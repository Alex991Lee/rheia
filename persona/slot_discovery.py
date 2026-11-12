# -*- coding: utf-8 -*-
# L1-485：slot_discovery — 在「Rheia」读卡口上跑的一次发现（产品树内）。
r"""slot_discovery — 在「Rheia」读卡口上跑的一次发现（产品树内）。"""








from __future__ import annotations

import glob
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import subprocess

PLUGIN_ID = "persona-slot-engine"


_MAC_CACHE = None
_LICENSE_CACHE: dict = {}


# L31-36：_injector — _injector
def _injector(app_root: str):
    p = os.path.join(app_root, "installer", "inject_local_plugins.py")
    spec = importlib.util.spec_from_file_location("rheia_inject_local_plugins", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# L40-50：_pointer — _pointer
def _pointer(app_root: str) -> str:
    p = os.path.join(app_root, "installer", "slot_consumers.json")
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        for c in (d.get("consumers") or []):
            if str(c.get("id") or "") == PLUGIN_ID:
                return str(c.get("source") or "")
    except Exception:
        pass
    return ""


# L54-68：_key_path — 授权 key 落点：环境变量 → <app>/slot.key → %PROGRAMDATA%/Rheia/slot.key。
def _key_path(app_root: str) -> str:
    r"""授权 key 落点：环境变量 → <app>/slot.key → %PROGRAMDATA%/Rheia/slot.key。"""
    cands = []
    env_k = (os.environ.get("RHEIA_SLOT_KEY_FILE") or "").strip()
    if env_k:
        cands.append(env_k)
    cands.append(os.path.join(app_root, "slot.key"))
    pd = os.environ.get("PROGRAMDATA") or ""
    if pd:
        cands.append(os.path.join(pd, "Rheia", "slot.key"))
    for c in cands:
        c = str(c or "").strip()
        if c and os.path.isfile(c):
            return c
    return ""


_PS_EXES = ("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe", "powershell.exe")

_PS_QUERIES = (

    "(Get-NetAdapter -Physical | Where-Object {$_.Status -ne 'Not Present'}).MacAddress",
    "(Get-CimInstance Win32_NetworkAdapterConfiguration | Where-Object {$_.IPEnabled}).MACAddress",
    "(Get-WmiObject Win32_NetworkAdapterConfiguration | Where-Object {$_.IPEnabled}).MACAddress",
)


# L82-88：_norm_mac — _norm_mac
def _norm_mac(s: str) -> str:
    s = (s or "").strip().lower()
    for ch in ("-", ":", ".", " ", "\t"):
        s = s.replace(ch, "")
    if len(s) == 12 and all(c in "0123456789abcdef" for c in s) and s != "0" * 12:
        return s
    return ""


# L92-112：_win_physical_macs — 客户机 Windows 物理网卡 MAC。
def _win_physical_macs() -> list:
    r"""客户机 Windows 物理网卡 MAC。"""





    for exe in _PS_EXES:
        if os.sep in exe and not os.path.isfile(exe):
            continue
        for q in _PS_QUERIES:
            try:
                r = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", q],
                                   capture_output=True)
            except Exception:
                continue
            out = (r.stdout or b"").decode("utf-8", "ignore") + (r.stderr or b"").decode("utf-8", "ignore")
            macs = [m for m in (_norm_mac(x) for x in out.split()) if m]
            if macs:
                return sorted(set(macs))
    return []


# L116-134：_mac_fingerprint — 客户机物理 MAC 指纹：Windows 物理网卡去重排序后 sha256 取前 16 位。
def _mac_fingerprint() -> str:
    r"""客户机物理 MAC 指纹：Windows 物理网卡去重排序后 sha256 取前 16 位。"""




    global _MAC_CACHE
    if _MAC_CACHE is not None:
        return _MAC_CACHE
    try:
        from persona import slot_state as _ss
        def _compute_mac():
            _m = _win_physical_macs()
            return hashlib.sha256("|".join(_m).encode("utf-8")).hexdigest()[:16] if _m else ""
        _MAC_CACHE = _ss.macFingerprint("", compute=_compute_mac)
    except Exception:
        macs = _win_physical_macs()
        _MAC_CACHE = hashlib.sha256("|".join(macs).encode("utf-8")).hexdigest()[:16] if macs else ""
    return _MAC_CACHE


# L138-145：_slot_id — key 的稳定编号：同一把 key 在 Windows（C:\...）与 WSL（/mnt/c/...）算出同一个值。
def _slot_id(key_path: str) -> str:
    r"""key 的稳定编号：同一把 key 在 Windows（C:\...）与 WSL（/mnt/c/...）算出同一个值。"""
    p = os.path.abspath(key_path).replace("\\", "/").lower()
    if p.startswith("/mnt/") and len(p) > 6:
        p = p[5:]
    if len(p) > 1 and p[1] == ":":
        p = p[0] + p[2:]
    return hashlib.sha256(p.encode("utf-8")).hexdigest()[:8]


# L149-159：_bind_side_candidates — 绑定记录的落点：优先 key 边上；写不进（Program Files 只读）就退 %PROGRAMDATA%。
def _bind_side_candidates(key_path: str) -> list:
    r"""绑定记录的落点：优先 key 边上；写不进（Program Files 只读）就退 %PROGRAMDATA%。"""
    out = [key_path + ".machine"]
    h = _slot_id(key_path)
    pd = (os.environ.get("PROGRAMDATA") or "").strip()
    if pd:
        out.append(os.path.join(pd, "Rheia", "slot-%s.machine" % h))

    if os.path.isdir("/mnt/c/ProgramData"):
        out.append(os.path.join("/mnt/c/ProgramData", "Rheia", "slot-%s.machine" % h))
    return out


# L163-173：_bound_fp — 读已有绑定记录 → (指纹, 落点文件)。没有记录时返回 ("", "")；读坏返回 (None, 文件)。
def _bound_fp(key_path: str):
    r"""读已有绑定记录 → (指纹, 落点文件)。没有记录时返回 ("", "")；读坏返回 (None, 文件)。"""
    for side in _bind_side_candidates(key_path):
        if not os.path.isfile(side):
            continue
        try:
            with open(side, encoding="utf-8") as f:
                return (f.read() or "").strip(), side
        except Exception:
            return None, side
    return "", ""


# L177-192：license_status — 授权在不在 → (ok, reason)。判据：key 在，且（有记录时）本机物理 MAC 对得上。
def license_status(app_root: str = "") -> tuple:
    r"""授权在不在 → (ok, reason)。判据：key 在，且（有记录时）本机物理 MAC 对得上。"""
    app_root = os.path.abspath(app_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    key = _key_path(app_root)
    if not key:
        return False, "缺授权 key（slot.key）"
    try:
        _ck = (key, os.path.getmtime(key),
               os.path.getmtime(key + ".machine") if os.path.exists(key + ".machine") else 0)
    except OSError:
        _ck = (key, 0, 0)
    if _ck in _LICENSE_CACHE:
        return _LICENSE_CACHE[_ck]
    _res = _license_status_uncached(app_root, key)
    _LICENSE_CACHE[_ck] = _res
    return _res


# L196-206：_license_status_uncached — _license_status_uncached
def _license_status_uncached(app_root: str, key: str) -> tuple:
    bound, side = _bound_fp(key)
    if bound is None:
        return False, "绑定记录读不了：%s" % side
    if bound:
        fp = _mac_fingerprint()
        if not fp:
            return False, "拿不到客户机物理 MAC（Windows 侧不回应）"
        if fp != bound:
            return False, "授权 key 已绑定其它机器（MAC 指纹不符）"
    return True, ""


# L210-238：bind_key_machine — 记录 / 校验 key 与本机物理 MAC 的绑定。
def bind_key_machine(key_path: str, record: bool = True) -> list:
    r"""记录 / 校验 key 与本机物理 MAC 的绑定。"""




    fp = _mac_fingerprint()
    if not fp:
        return ["拿不到客户机物理 MAC（Windows 侧不回应）—— 拒绝绑定"]
    bound, side = _bound_fp(key_path)
    if bound is None:
        return ["绑定记录读不了：%s" % side]
    if bound:
        if bound != fp:
            return ["授权 key 已绑定其它机器（MAC 指纹不符）"]
        return []
    if not record:
        return []
    for cand in _bind_side_candidates(key_path):
        try:
            d = os.path.dirname(cand)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(cand, "w", encoding="utf-8") as f:
                f.write(fp)
            return []
        except Exception:
            continue
    return ["绑定记录写不了（%s 与 %%PROGRAMDATA%% 都试过）" % key_path]


# L242-250：resolve_source — resolve_source
def resolve_source(app_root: str, source: str = "") -> str:
    for cand in (source, os.environ.get("RHEIA_PERSONAL_SLOT") or "", _pointer(app_root)):
        cand = str(cand or "").strip()
        if not cand:
            continue
        p = cand if os.path.isabs(cand) else os.path.join(app_root, cand)
        if os.path.isdir(p):
            return os.path.normpath(p)
    return ""


# L254-287：_grow_elevated — 普通用户不能写 Program Files 时，自动 UAC 提权跑 executor grow。
def _grow_elevated(app_root: str, key_path: str) -> tuple[bool, list]:
    r"""普通用户不能写 Program Files 时，自动 UAC 提权跑 executor grow。"""




    if os.name != "nt":
        return False, ["非 Windows，无法 UAC 提权"]
    exe = os.path.join(app_root, "executor.py")
    if not os.path.isfile(exe):
        return False, ["缺 executor.py（壳包不完整）"]
    py = sys.executable if "sys" in globals() else ""
    if not py:
        import sys as _sys
        py = _sys.executable
    args = [exe, "grow", "--root", app_root, "--file", "RheiaPersonalSlot",
            "--dest", "personal-slot"]
    if key_path:
        args += ["--key-file", key_path]
    def _psq(x: str) -> str:
        return "'" + str(x).replace("'", "''") + "'"
    ps = ("Start-Process -FilePath %s -ArgumentList @(%s) -Verb RunAs -Wait"
          % (_psq(py), ",".join(_psq(a) for a in args)))
    try:
        import subprocess



        subprocess.Popen(["powershell.exe", "-NoProfile", "-Command", ps],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return False, ["UAC 已发起（后台生长，不阻塞加载）"]
    except Exception as e:
        return False, ["UAC 提权异常：%s: %s" % (type(e).__name__, e)]


# L291-337：_grow_from_descriptor — 解释构造：把 OKOC 描述文件 RheiaPersonalSlot 用 key 解释开，长成 <app>/personal-slot。
def _grow_from_descriptor(app_root: str, key_path: str):
    r"""解释构造：把 OKOC 描述文件 RheiaPersonalSlot 用 key 解释开，长成 <app>/personal-slot。"""








    exe = os.path.join(app_root, "executor.py")
    seed = os.path.join(app_root, "RheiaPersonalSlot")
    if not os.path.isfile(exe):
        return "", ["缺 executor.py（壳包不完整）"]
    if not os.path.isfile(seed):
        return "", ["缺产品描述文件 RheiaPersonalSlot"]
    try:
        spec = importlib.util.spec_from_file_location("rheia_executor", exe)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        key = mod.load_key(key_path) if key_path else None
        mod.cmd_grow(app_root, "RheiaPersonalSlot", "personal-slot", key)
    except SystemExit as e:
        if os.name == "nt":
            _wsl_ok, _wsl_issues = _grow_via_wsl(app_root, key_path)
            if not _wsl_ok:
                _el_ok, _el_issues = _grow_elevated(app_root, key_path)
                if not _el_ok:
                    return "", ["Windows cryptography 不可用；WSL 回退失败：%s；%s"
                                % (_wsl_issues, _el_issues)]
        else:
            return "", ["构造中止：%s" % (e,)]
    except Exception as e:
        if os.name == "nt":

            _wsl_ok, _wsl_issues = _grow_via_wsl(app_root, key_path)
            if not _wsl_ok:
                _el_ok, _el_issues = _grow_elevated(app_root, key_path)
                if not _el_ok:
                    return "", ["Windows 写入失败：%s: %s；WSL：%s；%s"
                                % (type(e).__name__, e, _wsl_issues, _el_issues)]
        else:
            return "", ["构造异常：%s: %s" % (type(e).__name__, e)]
    dest = os.path.join(app_root, "personal-slot")
    if not os.path.isdir(dest):
        return "", ["构造后没有 personal-slot 目录"]
    return dest, []


# L341-366：_grow_via_wsl — Windows cryptography 不可用时，用 WSL python3 跑同一份 executor.py。
def _grow_via_wsl(app_root: str, key_path: str) -> tuple[bool, list]:
    r"""Windows cryptography 不可用时，用 WSL python3 跑同一份 executor.py。"""
    if os.name != "nt" or not key_path:
        return False, ["非 Windows 或缺 key"]
    distro = (os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu")

    def _wp(p: str) -> str:
        s = os.path.abspath(p).replace("\\", "/")
        if len(s) >= 2 and s[1] == ":":
            s = "/mnt/" + s[0].lower() + s[2:]
        return s

    exe_w, root_w, key_w = _wp(os.path.join(app_root, "executor.py")), _wp(app_root), _wp(key_path)
    try:
        r = subprocess.run(
            ["wsl.exe", "-d", distro, "-u", "root", "--", "python3", exe_w,
             "grow", "--root", root_w, "--file", "RheiaPersonalSlot",
             "--dest", "personal-slot", "--key-file", key_w],
            capture_output=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        return False, ["%s: %s" % (type(e).__name__, e)]
    if r.returncode != 0:
        return False, [(r.stdout or "").strip()[-300:] or (r.stderr or "").strip()[-300:]]
    return True, []


# L370-385：validate — validate
def validate(src: str) -> list:
    if not src or not os.path.isdir(src):
        return ["源目录不存在"]
    issues = []
    man = os.path.join(src, "openclaw.plugin.json")
    if not os.path.isfile(man):
        issues.append("缺 openclaw.plugin.json")
    else:
        try:
            with open(man, encoding="utf-8") as f:
                d = json.load(f)
            if str(d.get("id") or "") != PLUGIN_ID:
                issues.append("manifest id 对不上：%r" % (d.get("id"),))
        except Exception as e:
            issues.append("manifest 读不了：%s" % e)
    return issues


# L389-467：discover — discover
def discover(app_root: str = "", source: str = "", dry_run: bool = False) -> dict:
    app_root = os.path.abspath(app_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    src = resolve_source(app_root, source)


    _installed = os.path.join(app_root, "personal-slot")
    if (os.path.isfile(os.path.join(_installed, "openclaw.plugin.json"))
            and os.path.isdir(os.path.join(_installed, "slot"))):
        _rep = {"ok": True, "source": _installed, "target": _installed,
                "copied": False, "grown": False, "issues": []}
        try:
            from persona import slot_state as _ss
            _ss.patch(app_root, discover=_rep)
        except Exception:
            pass
        return _rep
    issues = []
    key = _key_path(app_root)
    _ok, _why = license_status(app_root)
    if not _ok:
        issues.append(_why)
    else:
        issues.extend(bind_key_machine(key, record=not dry_run))
    grown = False


    if (not os.path.isdir(src) or bool(validate(src))) and key:
        seed = os.path.join(app_root, "RheiaPersonalSlot")
        if os.path.isfile(seed):
            if dry_run:
                return {"ok": True, "source": seed, "target": os.path.join(app_root, "personal-slot"),
                        "copied": False, "issues": [], "would_grow": True}
            src, gissues = _grow_from_descriptor(app_root, key)
            grown = bool(src)
            issues.extend(gissues)
    if not src:
        issues.append("找不到可构造的插槽（既无源目录，也无可解释的描述文件）")
    else:
        issues.extend(validate(src))
    rep = {"ok": False, "source": src, "target": "", "copied": False,
           "grown": grown, "issues": issues}
    if issues:
        try:
            from persona import slot_state as _ss
            _ss.patch(app_root, discover=rep)
        except Exception:
            pass
        return rep
    installed = os.path.join(app_root, "personal-slot")
    staged = os.path.join(os.path.expanduser("~"), ".openclaw", "extensions", PLUGIN_ID)
    if dry_run:
        rep.update(ok=True, target=installed, staged=staged)
        return rep
    try:
        if os.path.normpath(installed) != os.path.normpath(src):
            if os.path.isdir(installed):
                shutil.rmtree(installed)
            shutil.copytree(src, installed,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))
            for _dp, _dn, _fn in os.walk(installed):
                os.chmod(_dp, 0o755)
                for _n in _fn:
                    os.chmod(os.path.join(_dp, _n), 0o755)
            rep["installed"] = installed
        inj = _injector(app_root)
        dst, copied = inj._stage_plugin(installed, PLUGIN_ID)
        if not dst:
            rep["issues"] = ["拷贝失败（看注入脚本输出）"]
            return rep
        rc = inj._register_one(PLUGIN_ID, dst, copied, inj._runtime_spec_for(PLUGIN_ID))
        rep.update(ok=(rc != 2), target=dst, copied=copied, rc=rc)
    except Exception as e:
        rep["issues"] = ["发现过程异常：%s: %s" % (type(e).__name__, e)]
    try:
        from persona import slot_state as _ss
        _ss.patch(app_root, discover=rep)
    except Exception:
        pass
    return rep


# L471-480：_main — _main
def _main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="slot_discovery")
    ap.add_argument("--app-root", default="")
    ap.add_argument("--source", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    rep = discover(app_root=a.app_root, source=a.source, dry_run=a.dry_run)
    print(json.dumps(rep, ensure_ascii=False))
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(_main())

