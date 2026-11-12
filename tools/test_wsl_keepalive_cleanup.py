#!/usr/bin/env python3
# L1-130：test_wsl_keepalive_cleanup — 卸载后不许留下 WSL 孤儿进程，且卸载器不许碰 WSL。

r"""test_wsl_keepalive_cleanup — 卸载后不许留下 WSL 孤儿进程，且卸载器不许碰 WSL。"""













from __future__ import annotations

import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_MAIN = os.path.join(_ROOT, "main", "main.py")
_ISS = os.path.join(_ROOT, "extract", "setup.iss")

passed = failed = 0


# L33-40：check — check
def check(name: str, ok: bool) -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"[PASS] {name}")
    else:
        failed += 1
        print(f"[FAIL] {name}")


# L44-61：_code_only — 去掉注释行与 docstring —— 只在真代码上做判断。
def _code_only(text: str) -> str:
    r"""去掉注释行与 docstring —— 只在真代码上做判断。"""




    out, in_doc, in_single = [], False, False
    for ln in str(text or "").splitlines():
        s = ln.strip()
        if s.count('"""') % 2 == 1:
            in_doc = not in_doc
            continue
        if in_doc:
            continue
        if s.startswith("#") or in_single:
            continue
        out.append(ln)
    return "\n".join(out)


# L65-125：main — main
def main() -> int:
    main_py = open(_MAIN, encoding="utf-8").read()
    iss = open(_ISS, encoding="utf-8").read()

    k = main_py.find("class WslKeepalive")

    seg = main_py[k:k + 24000] if k >= 0 else ""

    _ka0 = main_py.find("# ═══ main.keepalive")
    _ka1 = main_py.find("# ═══ main.pip_venv")
    ka_src = main_py[_ka0:_ka1] if (_ka0 >= 0 and _ka1 > _ka0) else seg
    stop = _code_only(ka_src[ka_src.find("def stop("):ka_src.find("def stop_nowait(")])
    nowait = _code_only(ka_src[ka_src.find("def stop_nowait("):])
    start = _code_only(ka_src[ka_src.find("def start("):ka_src.find("def stop(")])

    check("库里确有 _kill_process_tree（复用，不另造轮子）",
          "def _kill_process_tree" in main_py and "taskkill" in main_py)






    check("起法=PTB 原样：裸 Popen + CREATE_NO_WINDOW（不带别的 flag）",
          "creationflags=_CREATE_NO_WINDOW," in main_py
          and "_CREATE_BREAKAWAY_FROM_JOB" not in main_py)
    check("保活仍是常驻 sleep infinity（PTB 那版就这一条命令）",
          '["wsl", "-d", self._distro, "--", "sleep", "infinity"]' in main_py)
    check("stop() 收尾照 PTB：terminate → wait → kill",
          "proc.terminate()" in stop and "proc.kill()" in stop)
    check("stop_nowait() 只 kill（非阻塞，不 join、不等 WSL）",
          "proc.kill()" in nowait and "join(" not in nowait)
    check("退出路径没有 wsl 调用（stop 那条）", '"wsl"' not in stop)
    check("退出路径没有 wsl 调用（stop_nowait 那条）", '"wsl"' not in nowait)
    check("闭环在：探活 Gateway + 激活 + 死→活回调（PTB 的 探活→激活→保活）",
          "_is_gateway_listening" in ka_src and "_activate_gateway" in ka_src
          and "_gateway_was_alive" in ka_src and "_gateway_ready_cb" in ka_src)
    check("激活走生命周期锁互斥（PTB 的 acquire/release_lifecycle）",
          'acquire_lifecycle("keepalive")' in ka_src
          and 'release_lifecycle("keepalive")' in ka_src)
    check("守护循环里 Gateway 死则激活、占位进程死则补",
          "self._activate_gateway()" in ka_src
          and "keepalive process dead — respawning" in ka_src)
    check("`_ensure_keepalive_unit` 这个名字还在（出包门禁按名字找它）",
          "def _ensure_keepalive_unit" in main_py)
    check("`_spawn_sleep_infinity` 只定义一次（重复定义=相互递归，保活曾一个都没起）",
          main_py.count("def _spawn_sleep_infinity") == 1)


    _cmds = "\n".join(ln for ln in iss.splitlines() if ln.strip().startswith("Filename:"))
    check("setup.iss 里没有 `--terminate`（不在这里关 WSL）",
          "--terminate" not in _cmds)
    check("setup.iss 里没有 `--unregister`（不在这里注销 WSL）",
          "--unregister" not in _cmds)
    check("注释里写明『两个行为分开』这条令", "两个行为" in iss)
    check("uninstall.c 里那道门还在（勾了不碰 / 不勾才清）",
          "g_keepOpenclaw" in open(os.path.join(_ROOT, "extract", "uninstall.c"),
                                   encoding="utf-8", errors="replace").read())

    print(f"\n=== {passed} passed, {failed} failed ===")
    return passed, failed


if __name__ == "__main__":
    p, f = main()
    sys.exit(1 if f else 0)
