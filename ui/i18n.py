# L1-254：Rheia UI 国际化 — 语言检测 + 字符串翻译。
r"""Rheia UI 国际化 — 语言检测 + 字符串翻译。"""











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

_LANG: str | None = None
_STRINGS: dict[str, str] = {}
_EN_FALLBACK: dict[str, str] = {}

_BASE = Path(__file__).resolve().parent.parent
_TRANSLATIONS_DIR = _BASE / "ui" / "translations"


_QT_TRANSLATORS: list[QTranslator] = []



_UNSUPPORTED_LANG_IDS = {0x11, 0x411, 0xC11}




_LANG_ID_MAP: dict[int, str] = {
    0x01: "ar",
    0x02: "bg",
    0x03: "ca",
    0x05: "cs",
    0x06: "da",
    0x07: "de",
    0x08: "el",
    0x09: "en",
    0x0A: "es",
    0x0B: "fi",
    0x0C: "fr",
    0x0D: "he",
    0x0E: "hu",
    0x10: "it",
    0x12: "ko",
    0x13: "nl",
    0x14: "nn",
    0x15: "pl",
    0x16: "pt_BR",
    0x19: "ru",
    0x1A: "hr",
    0x1B: "sk",
    0x1D: "sv",
    0x1F: "tr",
    0x22: "uk",
    0x26: "lv",
    0x29: "fa",
    0x37: "ka",
}


_SUPPORTED_LANGS = set(_LANG_ID_MAP.values()) | {"zh"}




# L82-120：_detectLanguage — 用 GetUserDefaultUILanguage 检测系统显示语言。
def _detectLanguage() -> str:
    r"""用 GetUserDefaultUILanguage 检测系统显示语言。"""










    override = os.environ.get("RHEIA_LOCALE", "").strip().lower()
    if override in ("zh", "zh-cn", "zh_cn", "zh-tw", "zh_tw"):
        return "zh"
    if override in ("en", "en-us", "en_us"):
        return "en"
    if override in _SUPPORTED_LANGS:
        return override
    if override:

        return "en"

    if sys.platform != "win32":
        return "en"

    try:
        langId = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())
    except Exception:
        return "en"

    primary = langId & 0x3FF


    if primary == 0x04:
        return "zh"


    return _LANG_ID_MAP.get(primary, "en")


# L124-129：currentLanguage — 返回当前语言码 zh 或 en，首次调用时自动检测。
def currentLanguage() -> str:
    r"""返回当前语言码 zh 或 en，首次调用时自动检测。"""
    global _LANG
    if _LANG is None:
        _LANG = _detectLanguage()
    return _LANG


# L133-149：setupI18n — 初始化国际化 — 在 QApplication 创建后调用。
def setupI18n(lang: str | None = None) -> str:
    r"""初始化国际化 — 在 QApplication 创建后调用。"""







    global _LANG
    if lang:
        _LANG = str(lang).lower()
    else:
        _LANG = _detectLanguage()
    _loadStrings(_LANG)
    _installQtTranslator()
    return _LANG




# L155-159：_loadStrings — _loadStrings
def _loadStrings(lang: str) -> None:
    global _STRINGS, _EN_FALLBACK
    _STRINGS = _loadJson(f"{lang}.json")

    _EN_FALLBACK = _loadJson("en.json") if lang != "en" else {}


# L163-170：_loadJson — _loadJson
def _loadJson(filename: str) -> dict[str, str]:
    path = _TRANSLATIONS_DIR / filename
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}




# L176-190：_findQtTranslationDir — 找 PySide6 内嵌翻译文件目录（python/ 运行时内或 PySide6 安装路径）。
def _findQtTranslationDir() -> str | None:
    r"""找 PySide6 内嵌翻译文件目录（python/ 运行时内或 PySide6 安装路径）。"""
    import PySide6

    p = Path(PySide6.__file__).resolve().parent / "translations"
    if p.is_dir():
        return str(p)

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        for rel in ("PySide6/translations", "translations"):
            cand = Path(meipass) / rel
            if cand.is_dir():
                return str(cand)
    return None


# L194-224：_installQtTranslator — 安装 Qt 内置对话框翻译（仅中文时生效，不支持语言的翻译文件已删除）。
def _installQtTranslator() -> None:
    r"""安装 Qt 内置对话框翻译（仅中文时生效，不支持语言的翻译文件已删除）。"""
    global _QT_TRANSLATORS
    from PySide6.QtCore import QTranslator
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return


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


    for name in ("qtbase_zh_CN", "qt_zh_CN"):
        trans = QTranslator()
        if trans.load(name, td):
            app.installTranslator(trans)
            _QT_TRANSLATORS.append(trans)




# L230-254：tr — 翻译 dot-notation key（如 "ui.wizard.title"）。
def tr(key: str, **fmt: object) -> str:
    r"""翻译 dot-notation key（如 "ui.wizard.title"）。"""





    lang = currentLanguage()
    if not _STRINGS:
        _loadStrings(lang)

    s = _STRINGS.get(key)
    if s is None and lang != "en":

        s = _EN_FALLBACK.get(key)

    result = s if s is not None else key

    if fmt:
        try:
            return result.format(**fmt)
        except (KeyError, ValueError, TypeError):
            pass

    return result
