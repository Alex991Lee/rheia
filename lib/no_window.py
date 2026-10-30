# -*- coding: utf-8 -*-
"""no_window — 全局静默子进程守卫。

原则（设计约定）：加载链上的一切 terminal 活动都必须是后台静默，客户不得看见任何控制台窗口。
做法：在进程入口把 subprocess.Popen 包一层 —— 调用方没显式给 creationflags 的，
一律补 CREATE_NO_WINDOW（Windows）；已显式指定的（DETACHED_PROCESS 等）原样不动。
幂等：重复调用只装一次。
"""
from __future__ import annotations

import subprocess as _sp
import sys as _sys

_INSTALLED = False


def install() -> bool:
    global _INSTALLED
    if _INSTALLED:
        return True
    if _sys.platform != "win32":
        _INSTALLED = True
        return True
    try:
        _CNW = getattr(_sp, "CREATE_NO_WINDOW", 0x08000000)
        _orig = _sp.Popen
        if getattr(_orig, "_rheia_silent", False):
            _INSTALLED = True
            return True

        class _SilentPopen(_orig):  # type: ignore[misc, valid-type]
            def __init__(self, *args, **kwargs):
                if not kwargs.get("creationflags"):
                    kwargs["creationflags"] = _CNW
                super().__init__(*args, **kwargs)

        _SilentPopen.__name__ = "Popen"
        _SilentPopen.__qualname__ = "Popen"
        _SilentPopen._rheia_silent = True  # type: ignore[attr-defined]
        _sp.Popen = _SilentPopen  # type: ignore[assignment]
        _INSTALLED = True
        return True
    except Exception:
        return False
