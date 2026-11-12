# -*- coding: utf-8 -*-
# L1-46：no_window — 全局静默子进程守卫。
r"""no_window — 全局静默子进程守卫。"""






from __future__ import annotations

import subprocess as _sp
import sys as _sys

_INSTALLED = False


# L19-46：install — install
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

        class _SilentPopen(_orig):
            def __init__(self, *args, **kwargs):
                if not kwargs.get("creationflags"):
                    kwargs["creationflags"] = _CNW
                super().__init__(*args, **kwargs)

        _SilentPopen.__name__ = "Popen"
        _SilentPopen.__qualname__ = "Popen"
        _SilentPopen._rheia_silent = True
        _sp.Popen = _SilentPopen
        _INSTALLED = True
        return True
    except Exception:
        return False
