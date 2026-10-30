"""Rheia UI 国际化 — 语言检测 + 字符串翻译。

检测逻辑对标 installer/install.py 的 _isChineseSystem()：
  - 用 GetUserDefaultUILanguage() 读取 Windows 显示语言
  - zh-CN/zh-TW/zh-HK 等中文族 → 中文
  - 不支持的语言（LANG ID 0x11/0x411/0xC11）→ 回落英文
  - 其他语言 → 英文
  - 可用 RHEIA_LOCALE 环境变量覆盖

翻译文件路径：ui/translations/{lang}.json
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtCore import QTranslator
    from PySide6.QtWidgets import QApplication

_LANG: str | None = None  # 延迟检测
_STRINGS: dict[str, str] = {}
_EN_FALLBACK: dict[str, str] = {}

_BASE = Path(__file__).resolve().parent.parent
_TRANSLATIONS_DIR = _BASE / "ui" / "translations"

# Qt 翻译加载记录，切换语言时先卸载
_QT_TRANSLATORS: list[QTranslator] = []

# 不支持的语言 — 这些 LANG ID 对应的 Qt 翻译文件已删除，
# 这里再加一层白名单兜底：直接 fallback 到英文
_UNSUPPORTED_LANG_IDS = {0x11, 0x411, 0xC11}  # 不支持的语言 LANG ID

# ── 白名单：primary language id → 语言码（只覆盖 PySide6 包内已有语言）──
# 0x11 故意不在此表内 → 检测时回落英文，该语言在任何层都出不来。
# 中文 primary = 0x04 需按 sub 区分简/繁，不在此表，单独处理。
_LANG_ID_MAP: dict[int, str] = {
    0x01: "ar",   # Arabic
    0x02: "bg",   # Bulgarian
    0x03: "ca",   # Catalan
    0x05: "cs",   # Czech
    0x06: "da",   # Danish
    0x07: "de",   # German
    0x08: "el",   # Greek（el.json 早就在，白名单漏了 → 希腊语系统一直被回落成英文）
    0x09: "en",   # English
    0x0A: "es",   # Spanish
    0x0B: "fi",   # Finnish
    0x0C: "fr",   # French
    0x0D: "he",   # Hebrew
    0x0E: "hu",   # Hungarian
    0x10: "it",   # Italian
    0x12: "ko",   # Korean
    0x13: "nl",   # Dutch
    0x14: "nn",   # Norwegian (Nynorsk)
    0x15: "pl",   # Polish
    0x16: "pt_BR",  # Portuguese (Brazil)
    0x19: "ru",   # Russian
    0x1A: "hr",   # Croatian
    0x1B: "sk",   # Slovak
    0x1D: "sv",   # Swedish
    0x1F: "tr",   # Turkish
    0x22: "uk",   # Ukrainian
    0x26: "lv",   # Latvian
    0x29: "fa",   # Persian (Farsi)
    0x37: "ka",   # Georgian
}

# 支持的语言码集合（用于环境变量覆盖时过白名单，不支持的语言不在内 → 回落英文）
_SUPPORTED_LANGS = set(_LANG_ID_MAP.values()) | {"zh"}


# ── 语言检测 ──

def _detectLanguage() -> str:
    """用 GetUserDefaultUILanguage 检测系统显示语言。

    白名单机制：只认 _LANG_ID_MAP 里的语言（+ 中文简/繁），
    其余（含 0x11）一律回落英文。不支持的语言永不出现。

    Returns:
        "zh"    — 中文系统（简/繁统一归 zh，翻译文件用 zh.json）
        "en"    — 英文，或白名单外（不支持的语言等）兜底
        "ar"/"de"/"fr"/... — PySide6 包内其它语言（对应 {lang}.json，tr003 起补）
    """
    # 环境变量优先（同样过白名单：只有支持的 28 种语言码才透传，其余回落英文）
    override = os.environ.get("RHEIA_LOCALE", "").strip().lower()
    if override in ("zh", "zh-cn", "zh_cn", "zh-tw", "zh_tw"):
        return "zh"
    if override in ("en", "en-us", "en_us"):
        return "en"
    if override in _SUPPORTED_LANGS:
        return override
    if override:
        # 显式设置了语言码但不被支持 → 强制回落英文，不 fall through 到系统检测
        return "en"

    if sys.platform != "win32":
        return "en"

    try:
        langId = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())
    except Exception:
        return "en"  # 检测失败兜底英文（与「非中文一律英文」口径一致）

    primary = langId & 0x3FF

    # 中文族：primary 0x04，按 sub 区分简/繁，统一归 "zh"
    if primary == 0x04:
        return "zh"

    # 白名单命中 → 对应语言码；未命中（含 0x11）→ 英文
    return _LANG_ID_MAP.get(primary, "en")


def currentLanguage() -> str:
    """返回当前语言码 ``zh`` 或 ``en``，首次调用时自动检测。"""
    global _LANG
    if _LANG is None:
        _LANG = _detectLanguage()
    return _LANG


def setupI18n(lang: str | None = None) -> str:
    """初始化国际化 — 在 QApplication 创建后调用。

    Args:
        lang: 语言码（"zh"/"en"/"de"/"fr"/...），None 时自动检测

    Returns:
        生效的语言码
    """
    global _LANG
    if lang:
        _LANG = str(lang).lower()
    else:
        _LANG = _detectLanguage()
    _loadStrings(_LANG)
    _installQtTranslator()
    return _LANG


# ── 翻译加载 ──

def _loadStrings(lang: str) -> None:
    global _STRINGS, _EN_FALLBACK
    _STRINGS = _loadJson(f"{lang}.json")
    # 非英文语言都加载英文兜底（zh/de/fr 缺 key 时回落英文）
    _EN_FALLBACK = _loadJson("en.json") if lang != "en" else {}


def _loadJson(filename: str) -> dict[str, str]:
    path = _TRANSLATIONS_DIR / filename
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


# ── Qt 翻译 ──

def _findQtTranslationDir() -> str | None:
    """找 PySide6 内嵌翻译文件目录（python/ 运行时内或 PySide6 安装路径）。"""
    import PySide6
    # PySide6 6.11 扁平布局：translations 直接在 PySide6/translations（不是 Qt6/translations）
    p = Path(PySide6.__file__).resolve().parent / "translations"
    if p.is_dir():
        return str(p)
    # PyInstaller 打包路径
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        for rel in ("PySide6/translations", "translations"):
            cand = Path(meipass) / rel
            if cand.is_dir():
                return str(cand)
    return None


def _installQtTranslator() -> None:
    """安装 Qt 内置对话框翻译（仅中文时生效，不支持语言的翻译文件已删除）。"""
    global _QT_TRANSLATORS
    from PySide6.QtCore import QTranslator
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return

    # 先卸载旧翻译
    for t in _QT_TRANSLATORS:
        try:
            app.removeTranslator(t)
        except Exception:
            pass
    _QT_TRANSLATORS.clear()

    if _LANG != "zh":
        return

    td = _findQtTranslationDir()
    if td is None:
        return

    # 只加载 zh_CN 的 qtbase 和 qt 核心翻译
    for name in ("qtbase_zh_CN", "qt_zh_CN"):
        trans = QTranslator()
        if trans.load(name, td):
            app.installTranslator(trans)
            _QT_TRANSLATORS.append(trans)


# ── 翻译函数 ──

def tr(key: str, **fmt: object) -> str:
    """翻译 dot-notation key（如 ``"ui.wizard.title"``）。

    无匹配时回落英文，再不行返回 key 本身。
    支持 ``str.format`` 参数填充。
    """
    # 懒加载
    lang = currentLanguage()
    if not _STRINGS:
        _loadStrings(lang)

    s = _STRINGS.get(key)
    if s is None and lang != "en":
        # 非英文 key 未翻译 → 回落英文
        s = _EN_FALLBACK.get(key)

    result = s if s is not None else key

    if fmt:
        try:
            return result.format(**fmt)
        except (KeyError, ValueError, TypeError):
            pass

    return result
