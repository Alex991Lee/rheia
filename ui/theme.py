"""Rheia 统一深色主题 — 所有 UI 文件的唯一配色来源

用法:
    from ui.theme import BG, TEXT, ACCENT, textOnBg, qtDarkPalette

    label.setStyleSheet(f"color: {TEXT['primary']}; background: {BG['card']};")
    # 或者：不确定底色的地方
    some_color = "#2a2a2a"
    label.setStyleSheet(f"color: {textOnBg(some_color)}; background: {some_color};")
"""

from PySide6.QtGui import QColor, QPalette


def fitToScreen(
    widget,
    width_ratio: float = 0.7,
    height_ratio: float = 0.7,
    min_width_ratio: float | None = None,
    min_height_ratio: float | None = None,
) -> None:
    """按屏幕可用区域比例自适应窗口尺寸（打开即随屏大小，不写死像素）。

    - resize = 可用区 × (width_ratio, height_ratio)
    - setMinimumSize = 可用区 × (min_width_ratio, min_height_ratio)，
      默认取初始尺寸的一半，小屏跟着降、不超高溢出。
    - 全部 clamp 到可用区内，避免超出屏幕。
    """
    from PySide6.QtWidgets import QApplication

    screen = widget.screen() or QApplication.primaryScreen()
    if screen is None:
        return
    avail = screen.availableGeometry()
    w = int(avail.width() * width_ratio)
    h = int(avail.height() * height_ratio)
    widget.resize(w, h)

    mw_ratio = min_width_ratio if min_width_ratio is not None else width_ratio * 0.5
    mh_ratio = min_height_ratio if min_height_ratio is not None else height_ratio * 0.5
    mw = max(1, int(avail.width() * mw_ratio))
    mh = max(1, int(avail.height() * mh_ratio))
    widget.setMinimumSize(mw, mh)

# ═══════════════════════════════════════════════
# 色板
# ═══════════════════════════════════════════════

BG = {
    "deep":    "#1a1a1a",   # 最底层（窗口/侧边栏底色）
    "card":    "#2a2a2a",   # 卡片 / 输入框底
    "hover":   "#333333",   # hover 态
    "active":  "#444444",   # active / 按下
}

TEXT = {
    "primary":   "#e0e0e0",  # 主文字
    "secondary": "#b0b0b0",  # 副文字
    "hint":      "#a0a0a0",  # 提示文字
    "faint":     "#555555",  # 极淡（版权行等）
}

ACCENT = "#f37021"
ACCENT_HOVER = "#ff8833"

STATUS = {
    "green":  "#5a9e6f",
    "yellow": "#d9a840",
    "red":    "#e85a4a",
    "gray":   "#6a5e52",
}

BORDER = "#444444"


# ═══════════════════════════════════════════════
# 判定：深色背景 → 浅色字
# ═══════════════════════════════════════════════

def textOnBg(bgHex: str) -> str:
    """传入背景色 hex（如 '#1a1a1a'），返回适合该背景的文字色。

    用 WCAG 相对亮度公式：亮度 > 0.45 → 深色字，否则浅色字。
    """
    r = int(bgHex[1:3], 16)
    g = int(bgHex[3:5], 16)
    b = int(bgHex[5:7], 16)

    def _linear(c: int) -> float:
        v = c / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    L = 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)
    return "#0a0a0a" if L > 0.45 else "#e0e0e0"


# ═══════════════════════════════════════════════
# Qt 全局深色调色板
# ═══════════════════════════════════════════════

def qtDarkPalette() -> QPalette:
    """返回完整的 Qt Fusion 深色调色板，覆盖全部 ColorRole。

    调用处：main/main.py 在 QApplication 创建后立即设置，确保
    QMessageBox / QToolTip / QDialog 等系统组件不会黑底黑字。
    """
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window,          QColor(BG["deep"]))
    p.setColor(QPalette.ColorRole.WindowText,      QColor(TEXT["primary"]))
    p.setColor(QPalette.ColorRole.Base,            QColor(BG["card"]))
    p.setColor(QPalette.ColorRole.AlternateBase,   QColor("#222222"))
    p.setColor(QPalette.ColorRole.Text,            QColor(TEXT["primary"]))
    p.setColor(QPalette.ColorRole.Button,          QColor(BG["hover"]))
    p.setColor(QPalette.ColorRole.ButtonText,      QColor(TEXT["primary"]))
    p.setColor(QPalette.ColorRole.ToolTipBase,     QColor(BG["card"]))
    p.setColor(QPalette.ColorRole.ToolTipText,     QColor(TEXT["primary"]))
    p.setColor(QPalette.ColorRole.BrightText,      QColor(STATUS["red"]))
    p.setColor(QPalette.ColorRole.Highlight,       QColor(ACCENT))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#0a0a0a"))
    p.setColor(QPalette.ColorRole.Link,            QColor(ACCENT))
    p.setColor(QPalette.ColorRole.LinkVisited,     QColor("#c05a1a"))
    return p


def fit_button(btn, *, pad: int = 28, min_w: int = 0, height: int | None = None) -> None:
    """按当前字体量出文字宽度给按钮留足空间（任何语言都不裁字）。"""
    try:
        from PySide6.QtGui import QFontMetrics
        _fm = QFontMetrics(btn.font())
        _w = _fm.horizontalAdvance(btn.text()) + int(pad)
        try:
            _ico = btn.icon()
            if _ico is not None and not _ico.isNull():
                _w += btn.iconSize().width() + 8
        except Exception:
            pass
        if min_w:
            _w = max(_w, int(min_w))
        btn.setMinimumWidth(_w)
        if height:
            btn.setMinimumHeight(int(height))
        try:
            from PySide6.QtWidgets import QSizePolicy
            btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        except Exception:
            pass
    except Exception:
        pass


def fit_button_text(btn, *, width: int = 0, pad: int = 14, min_pt: float = 7.5, wrap: bool = True) -> None:
    """按钮尺寸不动，让文案适应：先缩字号，仍放不下就按词换行（高度随之长）。"""
    try:
        from PySide6.QtGui import QFontMetrics
        _w = int(width or btn.width() or btn.sizeHint().width())
        _avail = max(24, _w - int(pad))
        _f = btn.font()
        _pt = _f.pointSizeF() if _f.pointSizeF() > 0 else 9.0
        _txt = btn.text()
        _fm = QFontMetrics(_f)
        while _fm.horizontalAdvance(_txt) > _avail and _pt > min_pt:
            _pt -= 0.5
            _f.setPointSizeF(_pt)
            _fm = QFontMetrics(_f)
        if _fm.horizontalAdvance(_txt) > _avail and wrap:
            _words = _txt.replace('\n', ' ').split()
            _lines, _cur = [], ''
            for _wd in _words:
                _cand = (_cur + ' ' + _wd).strip()
                if _fm.horizontalAdvance(_cand) <= _avail or not _cur:
                    _cur = _cand
                else:
                    _lines.append(_cur); _cur = _wd
            if _cur:
                _lines.append(_cur)
            _txt = '\n'.join(_lines)
        if _txt != btn.text():
            btn.setText(_txt)
        btn.setFont(_f)
        _h = _fm.height() * (1 + _txt.count('\n')) + 12
        if _h > btn.minimumHeight():
            btn.setMinimumHeight(_h)
    except Exception:
        pass
