# -*- coding: utf-8 -*-
# L1-71：main/install_worker.py —— 装机链的后台进程入口。
r"""main/install_worker.py —— 装机链的后台进程入口。"""




from __future__ import annotations

try:
    from lib.no_window import install as _install_no_window
    _install_no_window()
except Exception:
    pass


import argparse
import json
import os
import sys
import time


# L25-35：_mark — _mark
def _mark(path: str, step: str, *, done: bool = False, ok: bool = False) -> None:
    try:
        if not path:
            return
        _tmp = path + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as _f:
            json.dump({"at": time.time(), "step": step, "done": bool(done), "ok": bool(ok)},
                      _f, ensure_ascii=False)
        os.replace(_tmp, path)
    except Exception:
        pass


# L39-67：main — main
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
    except Exception as _e:
        try:
            from main.main import stk as _stk
            _stk("bonus.skills.worker",
                 "装机链异常：%s: %s" % (type(_e).__name__, _e), ok=False)
        except Exception:
            pass
    _mark(_prog, "装机链结束" if _ok else "装机链异常收尾", done=True, ok=_ok)
    try:
        from main.main import stk as _stk2
        _stk2("bonus.skills.worker", "install worker done（ok=%s）" % _ok, ok=_ok)
    except Exception:
        pass
    return 0 if _ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
