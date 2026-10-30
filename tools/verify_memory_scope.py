#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_memory_scope — 记忆边界（memory_scope）端到端验证

验证三件事（对应三层落地）：
  ① 格式层：配置包 frontmatter 带 memory_scope 声明
  ② 加载层：自足卡的语料只在**卡自己**的地盘（卡内 corpus 区；旧布局那张卡旁的
            `<卡名>.corpus` 目录），不混宿主语料目录
  ③ 行为层：自足卡的 system prompt 含边界条款

⚠ 2026-09-18：② 那份**落点不再由壳算** —— 壳只是读卡器，卡自己的语料在哪回卡侧要
  （卡侧 CLI 的 `--corpus-targets`，只读、无副作用）。壳里那份算"卡旁语料池路径"的
  函数已经拆掉；壳这边只留一条判断：**卡侧给的那份落点有没有混进宿主语料目录**。

用法：
    python3 tools/verify_memory_scope.py <配置包.indm>

不带参数 → 用 E:\\baseline 基线配置包做样例。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

_PASS, _FAIL = [], []

#: 卡外语料**可能**落的地方（宿主的地盘）。这份名单属于读卡器要认得的东西 ——
#: 它跟"卡自己语料在哪"是两件事：前者是壳/宿主的池子，后者回卡侧问。
HOST_CORPUS_DIRS = ('/root/.openclaw/workspace/memory/.persona-slot/corpus',
                    '/root/.openclaw/workspace/memory/.dreams/session-corpus')

#: 卡侧安装根（壳只认这个"根"，不认卡侧的工具叫什么名字）——
#: 与 persona/indivi.py 的 `_CARD_ROOTS` 同一份落点。
_CARD_ROOTS = ("/mnt/c/Program Files/RheiaCreator",
               r"E:\Coding\RheiaCreator",
               r"C:\Program Files\RheiaCreator")


def check(name: str, ok: bool, detail: str = "") -> None:
    (_PASS if ok else _FAIL).append(name)
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  —— {detail}" if detail else ""))


def _card_side_cli() -> str:
    """找卡侧 CLI：认带 `--corpus-targets` 的那个（壳⇄卡侧的口子之一）。

    环境变量 `RHEIA_CARD_CLI` 可显式指定（运维/测试用）。
    """
    env = os.environ.get("RHEIA_CARD_CLI", "")
    if env and os.path.isfile(env):
        return env
    for root in _CARD_ROOTS:
        d = os.path.join(root, "tools")
        try:
            names = sorted(n for n in os.listdir(d)
                           if n.lower().endswith((".py", ".cmd", ".bat", ".exe")))
        except OSError:
            continue
        for n in names:
            p = os.path.join(d, n)
            try:
                with open(p, "rb") as f:
                    if b"--corpus-targets" in f.read(400000):
                        return p
            except OSError:
                continue
    return ""


def _card_entry_argv(cli: str, *args) -> tuple[list[str], str | None]:
    """按文件类型起那个入口（`.py` ⇒ 手上的 python；`.exe` ⇒ 直接起；
    `.cmd`/`.bat` ⇒ 经 `cmd.exe /c`，工作目录设在入口所在目录、只递文件名）。
    返回 `(argv, cwd)`。
    """
    low = str(cli).lower()
    if low.endswith((".cmd", ".bat")):
        exe = "cmd.exe" if os.name == "nt" else "/mnt/c/Windows/System32/cmd.exe"
        return [exe, "/c", os.path.basename(cli), *args], os.path.dirname(cli)
    if low.endswith(".exe"):
        return [cli, *args], None
    return [sys.executable or "python3", cli, *args], None


def card_corpus_manifest(card: str) -> tuple[dict, str]:
    """问卡侧：这张卡自己语料的落点清单（只读；壳里不算、不猜）。

    返回 `(清单, 为什么拿不到)`；拿到了原因就是空串。卡侧读不通时清单里带 `errors`，
    这里**照样把它交出去**（如实报，不替卡侧下结论）。
    """
    cli = _card_side_cli()
    if not cli:
        return {}, "找不到卡侧 CLI（设 RHEIA_CARD_CLI，或装 RheiaCreator）"
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    argv, cwd = _card_entry_argv(cli, "--card", card, "--corpus-targets")
    try:
        r = subprocess.run(argv, cwd=cwd,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, timeout=60)
    except Exception as e:
        return {}, f"问卡侧失败：{e}"
    try:
        t = json.loads((r.stdout or "").strip() or "{}")
    except Exception:
        return {}, f"卡侧没给出可读的落点清单（rc={r.returncode}）"
    if not isinstance(t, dict) or not t:
        return {}, f"卡侧没给出落点清单（rc={r.returncode}）"
    return t, ""


def _norm(p: str) -> str:
    return str(p or "").replace("\\", "/").rstrip("/")


def main() -> int:
    card = sys.argv[1] if len(sys.argv) > 1 else "./baseline.indm"
    if not os.path.isfile(card):
        print(f"[verify] 找不到配置包: {card}", file=sys.stderr)
        return 2
    print(f"[verify] 配置包: {card}\n")

    from persona.indivi import (
        read_indm, read_memory_scope, validate_card_scope,
        MEMORY_SCOPE_SELF, MEMORY_SCOPE_HOST,
    )

    # soul 区是公开段：无 token 时走无锁读取（不解隐私段），授权锁配置包也能验边界
    try:
        personality, *_ = read_indm(card)
    except ValueError:
        import struct
        import zstandard as _zs
        _d = open(card, "rb").read()
        _hdr = {2: 88, 3: 100, 4: 112}.get(_d[4])
        if not _hdr:
            print(f"[verify] 不支持的 .indm 版本: {_d[4]}", file=sys.stderr)
            return 2
        _o, _bl, _rl = struct.unpack("<III", _d[24:36])
        personality = _zs.ZstdDecompressor().decompress(
            _d[_hdr + _o:_hdr + _o + _bl], max_output_size=4 * 1024 * 1024
        )
        print("[verify] （授权锁配置包，走无锁读 soul 区）")
    p_text = personality.decode("utf-8", "replace")

    print("① 格式层")
    ok, msg = validate_card_scope(card)
    scope = read_memory_scope(card)
    check("memory_scope 已声明", ok, msg)
    check("值为 self_contained（.indm 自足）", scope == MEMORY_SCOPE_SELF,
          f"{scope}（宿主绑定卡是 {MEMORY_SCOPE_HOST}）")

    print("\n② 加载层（卡自己的语料在哪 —— 回卡侧问）")
    t, why = card_corpus_manifest(card)
    if not t:
        print(f"  ⏭ 拿不到卡侧落点，这一层跳过：{why}")
    else:
        errs = t.get("errors") or []
        check("卡侧给出了这张卡语料的落点清单", not errs,
              "；".join(str(e) for e in errs) if errs else str(t.get("card") or card))
        check("自足卡的语料在卡内 corpus 区（不是宿主池）",
              isinstance(t.get("zone_bytes"), int) and t["zone_bytes"] >= 0,
              f"corpus 区 {t.get('zone_bytes')} 字节")
        side = str(t.get("sidecar_dir") or "")
        if side:
            check("卡旁的旧布局侧车目录紧贴配置包（同目录、<卡名>.corpus）",
                  _norm(side).endswith(".corpus")
                  and _norm(os.path.dirname(_norm(side))) == _norm(os.path.dirname(_norm(card))),
                  side)
            check("卡侧落点未混入宿主语料目录",
                  all(_norm(h) not in _norm(side) for h in HOST_CORPUS_DIRS),
                  str(HOST_CORPUS_DIRS))
        else:
            print("  · 盘上没有旧的侧车目录（现代卡：语料就在卡内 corpus 区）")

    print("\n③ 行为层（对模型的约束）")
    check("soul 区含边界条款", "【我的记忆边界】" in p_text,
          f"soul 区 {len(p_text)} 字符")
    check("条款声明配置包外内容不算记忆", "不是我的记忆" in p_text)

    print(f"\n结果：{len(_PASS)} 通过 / {len(_FAIL)} 失败")
    if _FAIL:
        for f in _FAIL:
            print(f"  ❌ {f}")
        return 1
    print("记忆边界三层全部生效。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
