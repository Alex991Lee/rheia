#!/usr/bin/env python3
# L1-61：人格页独立宿主进程（崩溃域隔离）。
r"""人类可见的人格页跑在独立 Qt 进程里；主壳只发启动/重启信号。"""

from __future__ import annotations

import ctypes
import faulthandler
import logging
import os
import sys

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_slotEnv = (os.environ.get("RHEIA_PERSONAL_SLOT") or "").strip()
for _p in (_slotEnv, os.path.join(_APP_ROOT, "personal-slot"), _APP_ROOT):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

_STAKE_DIR = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia")


def main() -> int:
    try:
        os.makedirs(_STAKE_DIR, exist_ok=True)
        _fh = open(os.path.join(_STAKE_DIR, "persona_host_faulthandler.log"),
                   "a", encoding="utf-8", buffering=1)
        faulthandler.enable(file=_fh, all_threads=True)
        logging.basicConfig(
            filename=os.path.join(_STAKE_DIR, "persona_host.log"),
            level=logging.INFO,
            format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
        )
    except Exception:
        pass
    try:
        ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x0004)
    except Exception:
        pass

    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    try:
        from ui.theme import qtDarkPalette
        app.setPalette(qtDarkPalette())
    except Exception:
        pass
    try:
        from ui.theme import fitToScreen
    except Exception:
        fitToScreen = None

    from ui.web.persona_page import PersonaPage

    page = PersonaPage(None)
    if fitToScreen is not None:
        try:
            fitToScreen(page, 0.52, 0.62)
        except Exception:
            pass
    if "--smoke" in sys.argv:
        page.deleteLater()
        return 0
    page.show()
    page.raise_()
    page.activateWindow()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
