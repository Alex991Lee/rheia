#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""inject_memory_scope — 给已有配置包补写记忆边界（老卡迁移，幂等）

在配置包 soul 区 frontmatter 写死 memory_scope + 正文末尾追加边界条款，其余各区原样不动。

    .indm → memory_scope: self_contained（内置记忆体，自足）
    .indv → memory_scope: host_bound（无记忆，依托宿主）

用法：
    python3 tools/inject_memory_scope.py <配置包> --dry-run
    python3 tools/inject_memory_scope.py <配置包>            # 原地写回（先备份 .bak）
    python3 tools/inject_memory_scope.py <配置包> -o <新路径>
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

_SCOPE_VALUE = {"indm": "self_contained", "indv": "host_bound"}

_SCOPE_CLAUSE = (
    "\n\n---\n\n"
    "【我的记忆边界】\n"
    "我的记忆只在本配置包之内（T 轨迹 / M 事实 / U 用户 / S 状态 / K 内核）。\n"
    "- 配置包之外的内容（宿主工作区、其他目录的存档、别的配置包）不是我的记忆，"
    "不得据此声称「我记得」，也不得把它们当作我的经历。\n"
    "- 配置包内没有相关内容时，如实说「我的记忆里没有这段」。\n"
    "- 我的记忆以本配置包为唯一来源。\n"
)

_CLAUSE_MARK = "【我的记忆边界】"


def _relock_indm_bytes(data: bytes, new_p_text: str) -> bytes:
    """字节级无锁迁移：只重写 soul 区（公开段），locus/mem/user/id/dist 压缩体原样搬运。

    为什么不得用 read_indm/write_indm：那需要授权 token（要解全部区）。
    补记忆边界只需改 soul 区（唯一无 INDPRV 前缀的公开段），
    授权指纹与隐私段字节级不变 → 不需要 token、不碰用户数据。
    """
    import struct
    import hashlib

    try:
        import zstandard as zstd
    except ImportError:
        return b""

    if data[:4] != b"INDM":
        return b""
    ver = data[4]
    hdr_size = {2: 88, 3: 100, 4: 112}.get(ver)
    if not hdr_size:
        return b""
    seg_count = data[6]
    total_body = struct.unpack("<I", data[16:20])[0]
    header = bytearray(data[:hdr_size])
    body = data[hdr_size:hdr_size + total_body]
    footer = data[hdr_size + total_body:hdr_size + total_body + 8]

    # 逐区取出压缩体（bytes 原样，不解密/不解压隐私区）
    descs, blobs = [], []
    for i in range(seg_count):
        off = 24 + i * 12
        o, bl, rl = struct.unpack("<III", header[off:off + 12])
        descs.append((o, bl, rl))
        blobs.append(body[o:o + bl])

    # 解压 soul 区（非隐私，无 INDPRV 前缀）
    dctx = zstd.ZstdDecompressor()
    try:
        p_raw = dctx.decompress(blobs[0], max_output_size=4 * 1024 * 1024)
    except Exception:
        return b""
    p_new = new_p_text.encode("utf-8")
    if p_new == p_raw:
        return data  # 无需改动
    cctx = zstd.ZstdCompressor(level=19, write_content_size=False)
    blobs[0] = cctx.compress(p_new)

    # 重组 body + 重算偏移表
    new_body = b""
    offsets = []
    for b in blobs:
        offsets.append(len(new_body))
        new_body += b
    for i, (bl, raw) in enumerate(zip([len(b) for b in blobs],
                                      [len(p_new)] + [d[2] for d in descs[1:]])):
        o = 24 + i * 12
        header[o:o + 12] = struct.pack("<III", offsets[i], bl, raw)

    total_raw = sum([len(p_new)] + [d[2] for d in descs[1:]])
    header[16:20] = struct.pack("<I", len(new_body))
    header[20:24] = struct.pack("<I", total_raw)

    chk_src = bytes(header[:hdr_size - 8])
    header[hdr_size - 8:hdr_size] = hashlib.sha256(chk_src).digest()[:8]
    new_footer = hashlib.sha256(chk_src + new_body).digest()[:8]
    return bytes(header) + new_body + new_footer


def _inject_scope(text: str, scope: str) -> str:
    """写 frontmatter 的 memory_scope（幂等）+ 正文末追加条款（幂等）。"""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if m:
        fm_lines = [ln for ln in m.group(1).splitlines()
                    if not ln.strip().startswith("memory_scope:")]
        fm_lines.append(f"memory_scope: {scope}")
        text = "---\n" + "\n".join(fm_lines) + "\n---\n" + text[m.end():]
    else:
        text = f"---\nmemory_scope: {scope}\n---\n\n" + text
    if _CLAUSE_MARK not in text:
        text = text.rstrip() + _SCOPE_CLAUSE
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description="给已有配置包补写记忆边界")
    ap.add_argument("card", help="配置包路径 (.indm / .indv)")
    ap.add_argument("-o", "--out", help="输出路径（缺省原地写回，原文件备份为 .bak）")
    ap.add_argument("--dry-run", action="store_true", help="只打印不写盘")
    args = ap.parse_args()

    if not os.path.isfile(args.card):
        print(f"[scope] 找不到配置包: {args.card}", file=sys.stderr)
        return 2

    from persona.indivi import (
        is_indm_file, is_indv_file, read_indm, write_indm, read_indv, write_indv,
        _auth_token,
    )

    fmt = "indm" if is_indm_file(args.card) else ("indv" if is_indv_file(args.card) else "")
    if not fmt:
        print("[scope] 无法识别格式（需 .indm / .indv）", file=sys.stderr)
        return 2
    scope = _SCOPE_VALUE[fmt]

    if fmt == "indm":
        # 授权 token 未知的配置包（如交付前基线）走字节级无锁迁移：只改公开段 P。
        try:
            personality, trajectory, memory_md, user_md, state, kernel = read_indm(args.card)
            locked_ok = True
        except ValueError:
            locked_ok = False

        if locked_ok:
            new_p = _inject_scope(personality.decode("utf-8", "replace"), scope).encode("utf-8")
            if new_p == personality:
                print("[scope] 已是目标状态，无需改动")
                return 0
            print(f"[scope] {fmt}: memory_scope={scope}（授权解锁路径）")
            print(f"[scope] soul 区 {len(personality)} → {len(new_p)} 字节")
            if args.dry_run:
                print("[scope] --dry-run，未写盘")
                return 0
            out = args.out or args.card
            if not args.out:
                shutil.copy2(args.card, args.card + ".bak")
                print(f"[scope] 已备份 → {args.card}.bak")
            write_indm(new_p, trajectory, memory_md, user_md, state, kernel, out)
            print(f"[scope] 已写回: {out}")
            return 0

        # 无锁路径：不解隐私段、不需 token，soul 区重写 + 其余字节原样
        data = open(args.card, "rb").read()
        import struct as _st
        hdr_size = {2: 88, 3: 100, 4: 112}.get(data[4], 0)
        if not hdr_size:
            print("[scope] 不支持的 .indm 版本", file=sys.stderr)
            return 2
        total_body = _st.unpack("<I", data[16:20])[0]
        o, bl, rl = _st.unpack("<III", data[24:36])
        import zstandard as _zs
        try:
            p_raw = _zs.ZstdDecompressor().decompress(
                data[hdr_size + o:hdr_size + o + bl], max_output_size=4 * 1024 * 1024
            )
        except Exception as e:
            print(f"[scope] soul 区解压失败: {e}", file=sys.stderr)
            return 2
        new_text = _inject_scope(p_raw.decode("utf-8", "replace"), scope)
        new_data = _relock_indm_bytes(data, new_text)
        if not new_data:
            print("[scope] 无锁迁移失败", file=sys.stderr)
            return 2
        if new_data == data:
            print("[scope] 已是目标状态，无需改动")
            return 0
        print(f"[scope] {fmt}: memory_scope={scope}（无锁路径，授权指纹/隐私段原样）")
        print(f"[scope] soul 区 {len(p_raw)} → {len(new_text.encode('utf-8'))} 字节")
        if args.dry_run:
            print("[scope] --dry-run，未写盘")
            return 0
        out = args.out or args.card
        if not args.out:
            shutil.copy2(args.card, args.card + ".bak")
            print(f"[scope] 已备份 → {args.card}.bak")
        open(out, "wb").write(new_data)
        print(f"[scope] 已写回: {out}")
        return 0
    else:
        raw = read_indv(args.card).decode("utf-8", "replace")
        new_p = _inject_scope(raw, scope)
        if new_p == raw:
            print("[scope] 已是目标状态，无需改动")
            return 0
        print(f"[scope] {fmt}: memory_scope={scope}")
        print(f"[scope] 人格层 {len(raw)} → {len(new_p)} 字符")
        if args.dry_run:
            print("[scope] --dry-run，未写盘")
            return 0
        out = args.out or args.card
        if not args.out:
            shutil.copy2(args.card, args.card + ".bak")
            print(f"[scope] 已备份 → {args.card}.bak")
        write_indv(new_p.encode("utf-8"), out)
        print(f"[scope] 已写回: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
