#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rheia executor —— 读产品描述文件，在 Rheia 上长出那块产品。

描述文件 = 产品名本身，没有扩展名（例：RheiaPersonalSlot）。
OKOC v1（明文载荷）/ v2（AES-256-GCM，需密钥）两种都读。

    "OKOC" | u32 版本 | u32 描述段长度 | [v1: zlib(描述)] / [v2: nonce(12)+AESGCM(zlib(描述))]
    每文件: v1 = u32 长度 + zlib(内容)；v2 = u32 长度 + nonce(12) + AESGCM(zlib(内容))

用法：
    python3 executor.py grow    [--root .] [--file RheiaPersonalSlot] [--dest personal-slot] [--key-file K]
    python3 executor.py pack    --root <源目录> --out <描述文件> [--profile slot|distro] [--key-file K]
    python3 executor.py unpack  --seed <描述文件> --dest <目录> [--key-file K]
    python3 executor.py inspect --seed <描述文件> [--key-file K]
    python3 executor.py verify  --seed <描述文件> --against <源目录> [--key-file K]
"""
import hashlib
import os
import struct
import sys
import zlib

MAGIC = b"OKOC"
V1, V2 = 1, 2

PROFILES = {
    "slot": {
        "dirs": ("bin", "slot"),
        "files": ("index.js", "cardTools.js", "contract.js", "package.json", "openclaw.plugin.json"),
        "first": ("bin/",),
    },
    "distro": {
        "dirs": ("installer", "kernel", "lib", "main", "persona", "ui"),
        "files": ("executor.py",),
        "first": ("installer/",),
    },
}

SKIP_DIRS = {"__pycache__", ".git", ".svn", "Released"}
SKIP_EXT = (".pyo", ".bak", ".tmp", ".c")   # .pyc 是产品件（Windows 侧 sourceless）


def _rel(root, p):
    return os.path.relpath(p, root).replace(os.sep, "/")


def _mode_for(rel, data):
    if rel.startswith("bin/") or data.startswith(b"#!"):
        return 0o755
    return 0o644


def load_key(path):
    raw = open(path, "rb").read()
    if len(raw) == 32:
        return raw
    txt = raw.strip().decode("ascii", "ignore")
    if len(txt) == 64:
        return bytes.fromhex(txt)
    raise SystemExit("[seed] 密钥文件必须是 32 字节原文或 64 位 hex")


def _aes(key):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except Exception:
        raise SystemExit("[seed] 缺 cryptography，装：pip install cryptography")
    return AESGCM(key)


def iter_files(root, profile):
    spec = PROFILES[profile]
    out = []
    for name in spec["files"]:
        p = os.path.join(root, name)
        if os.path.isfile(p):
            out.append(_rel(root, p))
    for d in spec["dirs"]:
        base = os.path.join(root, d)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(x for x in dirnames
                                 if x not in SKIP_DIRS and not x.startswith("."))
            for fn in sorted(filenames):
                if fn.startswith(".") or fn.endswith(SKIP_EXT):
                    continue
                p = os.path.join(dirpath, fn)
                if os.path.islink(p) or not os.path.isfile(p):
                    continue
                out.append(_rel(root, p))
    out = sorted(set(out))
    first = spec.get("first", ())
    return ([p for p in out if p.startswith(first)] +
            [p for p in out if not p.startswith(first)])


def write_seed(entries, out, key=None):
    man = struct.pack("<I", len(entries))
    datas = []
    for name, data in entries:
        nb = name.encode("utf-8")
        man += struct.pack("<H", len(nb)) + nb
        man += struct.pack("<Q", len(data))
        man += hashlib.sha256(data).digest()
        datas.append(data)
    mz = zlib.compress(man, 9)
    total = 0
    with open(out, "wb") as f:
        f.write(MAGIC)
        if key:
            a = _aes(key)
            n = os.urandom(12)
            ct = a.encrypt(n, mz, None)
            f.write(struct.pack("<II", V2, len(ct)))
            f.write(n)
            f.write(ct)
            total += len(ct)
            for data in datas:
                b = zlib.compress(data, 9)
                nn = os.urandom(12)
                cc = a.encrypt(nn, b, None)
                f.write(struct.pack("<I", len(cc)))
                f.write(nn)
                f.write(cc)
                total += len(cc)
        else:
            f.write(struct.pack("<II", V1, len(mz)))
            f.write(mz)
            total += len(mz)
            for data in datas:
                b = zlib.compress(data, 9)
                f.write(struct.pack("<I", len(b)))
                f.write(b)
                total += len(b)
    return len(man), len(mz), total


def read_seed(path, key=None):
    with open(path, "rb") as f:
        raw = f.read()
    if len(raw) < 12 or raw[:4] != MAGIC:
        raise SystemExit("[seed] 不是 OKOC 描述文件: " + path)
    ver, mlen = struct.unpack("<II", raw[4:12])
    a = _aes(key) if (ver == V2 and key) else None
    body = raw[12:]
    if ver == V1:
        man = zlib.decompress(body[:mlen])
        off = mlen
    elif ver == V2:
        if a is None:
            raise SystemExit("[seed] 这份描述文件是加密的（v2），需要 --key-file")
        if mlen < 12:
            raise SystemExit("[seed] 描述段损坏")
        man = zlib.decompress(a.decrypt(body[:12], body[12:12 + mlen], None))
        off = 12 + mlen
    else:
        raise SystemExit("[seed] 版本 %d 不认识（执行器支持 1/2）" % ver)
    n = struct.unpack("<I", man[:4])[0]
    p, entries = 4, []
    for _ in range(n):
        nl = struct.unpack("<H", man[p:p + 2])[0]
        p += 2
        name = man[p:p + nl].decode("utf-8")
        p += nl
        size = struct.unpack("<Q", man[p:p + 8])[0]
        p += 8
        digest = man[p:p + 32]
        p += 32
        entries.append((name, size, digest))
    if p != len(man):
        raise SystemExit("[seed] 描述段长度不符：解析 %d / 实际 %d" % (p, len(man)))
    files = []
    for name, size, digest in entries:
        clen = struct.unpack("<I", body[off:off + 4])[0]
        off += 4
        if ver == V2:
            nonce = body[off:off + 12]
            off += 12
            data = zlib.decompress(a.decrypt(nonce, body[off:off + clen], None))
            off += clen
        else:
            data = zlib.decompress(body[off:off + clen])
            off += clen
        if len(data) != size:
            raise SystemExit("[seed] %s 长度不符：%d / %d" % (name, len(data), size))
        if hashlib.sha256(data).digest() != digest:
            raise SystemExit("[seed] %s 校验失败" % name)
        files.append((name, data))
    return files


def _write(files, dest):
    for name, data in files:
        p = os.path.join(dest, name)
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
        os.chmod(p, _mode_for(name, data))


def _prune(dest, keep):
    removed = []
    for dirpath, dirnames, filenames in os.walk(dest, topdown=False):
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, dest).replace(os.sep, "/")
            if rel not in keep:
                try:
                    os.remove(p)
                    removed.append(rel)
                except OSError:
                    pass
        if dirpath != dest:
            try:
                if not os.listdir(dirpath):
                    os.rmdir(dirpath)
            except OSError:
                pass
    return removed


def _find_seed(root, seed_name):
    # ⚠ 相对名一律按 --root 解析：以前这里原样返回 "RheiaPersonalSlot"，
    #   os.path.isfile() 就变成按**进程 CWD** 找 —— 壳/grow 从别的目录跑时必然
    #   报“找不到产品描述文件”，扩展位永远长不出来。
    if seed_name:
        return seed_name if os.path.isabs(seed_name) else os.path.join(root, seed_name)
    for cand in (os.path.join(root, "RheiaPersonalSlot"),
                 os.path.join(os.path.dirname(os.path.abspath(root)),
                              "Rheia Personal Slot", "RheiaPersonalSlot")):
        if os.path.isfile(cand):
            return cand
    return os.path.join(root, "RheiaPersonalSlot")


def cmd_grow(root, seed_name, dest_rel, key=None):
    seed = _find_seed(root, seed_name)
    if not os.path.isfile(seed):
        raise SystemExit("[grow] 找不到产品描述文件: " + seed)
    files = read_seed(seed, key)
    dest = os.path.join(root, dest_rel)
    _write(files, dest)
    removed = _prune(dest, set(n for n, _ in files))
    print("[grow] %s -> %s：%d 个文件%s"
          % (os.path.basename(seed), dest, len(files),
             ("，清掉旧文件 %d 个" % len(removed)) if removed else ""))
    return 0


def cmd_pack(root, out, profile, key=None):
    entries = []
    for rel in iter_files(root, profile):
        with open(os.path.join(root, rel), "rb") as f:
            entries.append((rel, f.read()))
    mlen, mzlen, blen = write_seed(entries, out, key)
    print("[pack] %s: %d 个文件，%d B -> %d B（描述 %d B，载荷 %d B，%s）"
          % (profile, len(entries), sum(len(d) for _, d in entries),
             os.path.getsize(out), mlen, blen, "AES-256-GCM" if key else "明文"))
    return 0


def cmd_unpack(seed, dest, key=None):
    files = read_seed(seed, key)
    _write(files, dest)
    print("[unpack] %d 个文件 -> %s" % (len(files), dest))
    return 0


def cmd_inspect(seed, key=None):
    files = read_seed(seed, key)
    print("OKOC  文件=%d  内容=%d B  描述文件=%d B"
          % (len(files), sum(len(d) for _, d in files), os.path.getsize(seed)))
    for name, data in files:
        print("  %8d  %s  %s" % (len(data), hashlib.sha256(data).hexdigest()[:16], name))
    return 0


def cmd_verify(seed, against, key=None):
    files = read_seed(seed, key)
    bad, seen = [], set()
    for name, data in files:
        seen.add(name)
        p = os.path.join(against, name)
        if not os.path.isfile(p):
            bad.append("缺文件  " + name)
            continue
        with open(p, "rb") as f:
            cur = f.read()
        if cur != data:
            bad.append("内容不同 " + name)
        elif (data.startswith(b"#!") or name.startswith("bin/")) and not os.access(p, os.X_OK):
            bad.append("缺执行位 " + name)
    prof = "slot" if any(n.startswith("bin/") for n in seen) else "distro"
    for rel in iter_files(against, prof):
        if rel not in seen:
            bad.append("多出文件 " + rel)
    if bad:
        print("[verify] 不一致 %d 处：" % len(bad))
        for b in bad:
            print("  " + b)
        return 1
    print("[verify] 一致：%d 个文件" % len(files))
    return 0


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd, args, i = argv[1], {}, 2
    while i < len(argv):
        if argv[i].startswith("--"):
            args[argv[i][2:]] = argv[i + 1] if i + 1 < len(argv) else ""
            i += 2
        else:
            i += 1
    key = load_key(args["key-file"]) if args.get("key-file") else None
    if cmd == "grow":
        return cmd_grow(args.get("root", "."), args.get("file", ""),
                        args.get("dest", "personal-slot"), key)
    if cmd == "pack":
        return cmd_pack(args["root"], args["out"], args.get("profile", "distro"), key)
    if cmd == "unpack":
        return cmd_unpack(args["seed"], args["dest"], key)
    if cmd == "inspect":
        return cmd_inspect(args["seed"], key)
    if cmd == "verify":
        return cmd_verify(args["seed"], args["against"], key)
    print("未知命令: " + cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
