#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_wsl_keepalive_cleanup — 卸载后不许留下 WSL 孤儿进程，**且卸载器不许碰 WSL**。

卸载 Rheia 侧进程结束不了；同一天又明令：
「**「仅卸载 Rheia」这条路从来不碰 WSL，这是对的，不能动**」。

所以收尸**只能在壳侧做**，规矩钉四条：
  ① 保活进程停止要杀**整棵树**（`taskkill /T /F`，库里已有 `_kill_process_tree`）；
  ② 保活进程要绑进「父死子亡」的作业对象（`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`）——
     壳被**硬杀**（卸载器直接杀进程）时，`aboutToQuit` 收尾轮不上，只能靠它；
  ③ `stop_nowait()`（退出钩子那条路）也不能少整棵树那道；
  ④ **卸载器（setup.iss）里一个字节都不许碰 WSL**：没有 `--terminate`、没有
     `--unregister`、也不许出现"关 Ubuntu"的收尸动作。
"""

from __future__ import annotations

import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_MAIN = os.path.join(_ROOT, "main", "main.py")
_ISS = os.path.join(_ROOT, "extract", "setup.iss")

passed = failed = 0


def check(name: str, ok: bool) -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"[PASS] {name}")
    else:
        failed += 1
        print(f"[FAIL] {name}")


def _code_only(text: str) -> str:
    """去掉注释行与 docstring —— 只在**真代码**上做判断。

    ⚠ 实测踩到：注释里写着"原来只 proc.terminate()/kill()"，按字符串一搜就误报
      （把说明当成代码了）。
    """
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


def main() -> int:
    main_py = open(_MAIN, encoding="utf-8").read()
    iss = open(_ISS, encoding="utf-8").read()

    k = main_py.find("class WslKeepalive")
    # 窗口给足：这个类里注释写得多，切太短会把 stop_nowait() 切在窗口外（实测踩过）
    seg = main_py[k:k + 24000] if k >= 0 else ""
    # 精确切到本节边界（下一个分节标记）：退出路径那两条断言必须只看本节
    _ka0 = main_py.find("# ═══ main.keepalive")
    _ka1 = main_py.find("# ═══ main.pip_venv")
    ka_src = main_py[_ka0:_ka1] if (_ka0 >= 0 and _ka1 > _ka0) else seg
    stop = _code_only(ka_src[ka_src.find("def stop("):ka_src.find("def stop_nowait(")])
    nowait = _code_only(ka_src[ka_src.find("def stop_nowait("):])
    start = _code_only(ka_src[ka_src.find("def start("):ka_src.find("def stop(")])

    check("库里确有 _kill_process_tree（复用，不另造轮子）",
          "def _kill_process_tree" in main_py and "taskkill" in main_py)
    # keepalive 按原型机一比一复原回 Rheia
    #   ⇒ 这一节以 PTB 20260826-001 的 runtime/keepalive.py 为准：
    #     · 起法：裸 Popen（只 CREATE_NO_WINDOW）+ 隐藏 startupinfo + 三个 DEVNULL；
    #     · 收尾：terminate → wait(5) → kill → wait(5)；退出钩子只 kill 客户端；
    #     · 闭环：探活 WSL / 探活 Gateway / 死了就激活（生命周期锁互斥）/ 死→活回调 / 补占位进程。
    #   之前那几条（整棵树杀、作业对象父死子亡、breakaway、启动扫残骸）随复原一并撤掉。
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
    # ④ 「这是两个行为，不勾选『仅卸载 Rheia』直接删除 wsl 也是对的」
    #    ⇒ 判断归 `uninstall.c`（按 g_keepOpenclaw），**setup.iss 里不许关/删 WSL**
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
