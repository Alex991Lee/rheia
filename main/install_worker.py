# -*- coding: utf-8 -*-
"""main/install_worker.py —— 装机链的后台进程入口。

跑整条装机链（预装 8 项 + Phase 3 配套 + 两步校验）到一个无窗口子进程，UI 只负责起它。
跑法：python install_worker.py [--progress <进度json>]
"""
from __future__ import annotations

try:  # 全局静默子进程守卫（设计约定：加载/安装链不得弹控制台）
    from lib.no_window import install as _install_no_window
    _install_no_window()
except Exception:  # noqa: BLE001
    pass


import argparse
import json
import os
import sys
import time


def _mark(path: str, step: str, *, done: bool = False, ok: bool = False) -> None:
    try:
        if not path:
            return
        _tmp = path + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as _f:
            json.dump({"at": time.time(), "step": step, "done": bool(done), "ok": bool(ok)},
                      _f, ensure_ascii=False)
        os.replace(_tmp, path)
    except Exception:                                                 # noqa: BLE001
        pass


def main() -> int:
    _ap = argparse.ArgumentParser()
    _ap.add_argument("--progress", default="")
    _a = _ap.parse_args()
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    _prog = _a.progress or os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia",
                                        "install_progress.json")
    _mark(_prog, "装机链开跑（子进程）")
    _ok = False
    try:
        from main.main import install_bonus_skills_background
        install_bonus_skills_background(run_sync=True)
        _ok = True
    except Exception as _e:                                           # noqa: BLE001
        try:
            from main.main import stk as _stk
            _stk("bonus.skills.worker",
                 "装机链异常：%s: %s" % (type(_e).__name__, _e), ok=False)
        except Exception:                                             # noqa: BLE001
            pass
    _mark(_prog, "装机链结束" if _ok else "装机链异常收尾", done=True, ok=_ok)
    try:
        from main.main import stk as _stk2
        _stk2("bonus.skills.worker", "install worker done（ok=%s）" % _ok, ok=_ok)
    except Exception:                                                 # noqa: BLE001
        pass
    return 0 if _ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
