# -*- coding: utf-8 -*-
# L1-199：SupportPanel — 支持开发面板（侧边栏入口 / 首次安装锁屏）。
r"""SupportPanel —— 支持开发（侧边栏入口 / 首次安装锁屏）。"""









from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)

_proj = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _proj not in sys.path:
    sys.path.insert(0, _proj)

try:
    from ui.i18n import tr
except ImportError:
    def tr(key, **fmt):
        return key

try:
    from ui.theme import BG, TEXT, ACCENT
except ImportError:
    BG = {"deep": "#1a1a1a", "card": "#2a2a2a", "hover": "#333333"}
    TEXT = {"primary": "#e0e0e0", "secondary": "#b0b0b0", "hint": "#a0a0a0"}
    ACCENT = "#f37021"

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

_ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "support")
FIRST_RUN_SECONDS = 30


# L49-53：_c — 读取颜色/文本表，缺省返回 fallback。
def _c(table, key, fallback):
    try:
        return table.get(key, fallback)
    except Exception:
        return fallback


# L57-83：_QRCard — 二维码卡片控件。
class _QRCard(QFrame):
    def __init__(self, filename: str, caption: str, parent=None):
        super().__init__(parent)
        self.setObjectName("qrCard")
        self.setFixedSize(196, 226)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 8)
        lay.setSpacing(6)

        img = QLabel()
        img.setAlignment(Qt.AlignCenter)
        img.setFixedSize(160, 160)
        path = os.path.join(_ASSETS, filename)
        if os.path.isfile(path):
            pm = QPixmap(path)
            if not pm.isNull():
                img.setPixmap(pm.scaled(160, 160, Qt.KeepAspectRatio,
                                        Qt.SmoothTransformation))
        if img.pixmap() is None or img.pixmap().isNull():
            img.setText("QR")
            img.setStyleSheet("color:%s;font-size:22px;" % _c(TEXT, "hint", "#a0a0a0"))
        lay.addWidget(img, 0, Qt.AlignHCenter)

        cap = QLabel(caption)
        cap.setAlignment(Qt.AlignCenter)
        cap.setStyleSheet("color:%s;font-size:12px;" % _c(TEXT, "secondary", "#b0b0b0"))
        lay.addWidget(cap)


# L87-199：SupportPanel — 支持开发面板。
class SupportPanel(QDialog):
    def __init__(self, parent=None, first_run: bool = False,
                 seconds: int = FIRST_RUN_SECONDS):
        super().__init__(parent)
        self._first_run = bool(first_run)
        self._remaining = max(0, int(seconds))
        self.setWindowTitle(tr("ui.support.title"))
        self.setMinimumWidth(470)
        self.setStyleSheet(
            "QDialog{background:%s;}"
            "QFrame#qrCard{background:%s;border:1px solid %s;border-radius:10px;}"
            % (_c(BG, "deep", "#1a1a1a"), _c(BG, "card", "#2a2a2a"),
               _c(BG, "hover", "#333333"))
        )

        if self._first_run:

            self.setModal(True)
            self.setWindowModality(Qt.ApplicationModal)

            self.setWindowFlags(self.windowFlags()
                                & ~Qt.WindowCloseButtonHint
                                | Qt.WindowStaysOnTopHint)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 18, 28, 20)
        root.setSpacing(12)


        self._count = QLabel("")
        if self._first_run:
            self._count.setText(str(self._remaining))
            self._count.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._count.setStyleSheet(
                "color:#ffffff;font-size:15px;font-weight:600;letter-spacing:1px;"
            )
        head = QHBoxLayout()
        head.addStretch(1)
        head.addWidget(self._count)
        root.addLayout(head)

        title = QLabel(tr("ui.support.title"))
        title.setStyleSheet("color:%s;font-size:18px;font-weight:600;"
                            % _c(TEXT, "primary", "#e0e0e0"))
        root.addWidget(title)

        body = QLabel(tr("ui.support.body"))
        body.setWordWrap(True)
        body.setStyleSheet("color:%s;font-size:13px;line-height:150%%;"
                           % _c(TEXT, "secondary", "#b0b0b0"))
        root.addWidget(body)

        cards = []
        for filename, caption in (("wechat.png", "WeChat Pay"), ("paypal.png", "PayPal")):
            if os.path.isfile(os.path.join(_ASSETS, filename)):
                cards.append(_QRCard(filename, caption))
        if cards:
            row = QHBoxLayout()
            row.setSpacing(18)
            row.addStretch(1)
            for card in cards:
                row.addWidget(card)
            row.addStretch(1)
            root.addLayout(row)

        hint = QLabel(tr("ui.support.qr_hint"))
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("color:%s;font-size:12px;" % _c(TEXT, "hint", "#a0a0a0"))
        root.addWidget(hint)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self._close_btn = QPushButton(tr("ui.support.close"))
        self._close_btn.setCursor(Qt.PointingHandCursor)
        self._close_btn.setStyleSheet(
            "QPushButton{background:%s;color:#ffffff;border:none;border-radius:6px;"
            "padding:7px 22px;font-size:13px;}"
            "QPushButton:hover{background:#ff8833;}" % ACCENT
        )
        self._close_btn.clicked.connect(self.reject)
        btns.addWidget(self._close_btn)
        root.addLayout(btns)
        if self._first_run:
            # 前 30 秒关闭键可见但不可用；30 秒后由 _tick 放开，
            # 客户不点关闭就保持打开，不自动退出。
            self._close_btn.setEnabled(False)

        if self._first_run:
            self._timer = QTimer(self)
            self._timer.setInterval(1000)
            self._timer.timeout.connect(self._tick)
            self._timer.start()

    def _tick(self) -> None:
        self._remaining -= 1
        self._count.setText(str(max(0, self._remaining)))
        if self._remaining <= 0:
            try:
                self._timer.stop()
            except Exception:
                pass
            try:
                self._close_btn.setEnabled(True)
            except Exception:
                pass
            # 不自动关闭：客户必须自己点生效后的关闭键。


    def keyPressEvent(self, event):
        if self._first_run and self._remaining > 0 and event.key() == Qt.Key_Escape:
            event.ignore()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        if self._first_run and self._remaining > 0:
            event.ignore()
            return
        super().closeEvent(event)
