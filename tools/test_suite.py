#!/usr/bin/env python3
"""OneKeyOpenClaw 模块检测套件
用法:
    python tools/test_suite.py          # 运行全部检测
    python tools/test_suite.py i18n     # 仅检测 i18n
    python tools/test_suite.py wizard   # 仅检测安装器
"""

import sys
import os
import unittest
import inspect
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent


# ============================================================
# 1. i18n 检测
# ============================================================

class TestI18n(unittest.TestCase):
    """检测 ui/i18n.py"""

    def setUp(self):
        sys.path.insert(0, str(_BASE / "ui"))
        from i18n import tr, _detect_locale
        self.tr = tr
        self.detect_locale = _detect_locale

    def test_detect_locale_returns_valid(self):
        """语言检测返回有效值"""
        locale = self.detect_locale()
        self.assertIn(locale, ["zh-CN", "en-US"])

    def test_tr_returns_string(self):
        """tr() 返回字符串"""
        result = self.tr("ui.main.title")
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_translation_files_exist(self):
        """翻译文件存在且可加载"""
        for lang in ["zh-CN", "en-US"]:
            trans_file = _BASE / "ui" / "translations" / f"{lang}.json"
            self.assertTrue(trans_file.exists(), f"{lang}.json 不存在")
            import json
            with open(trans_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertIsInstance(data, dict)
            self.assertGreater(len(data), 0, f"{lang}.json 为空")

    def test_chat_translations_present(self):
        """聊天相关翻译键存在"""
        keys = [
            "ui.chat.input_placeholder",
            "ui.chat.send",
            "ui.chat.thinking_mode",
            "ui.gateway.connected",
            "ui.gateway.disconnected",
        ]
        for key in keys:
            result = self.tr(key)
            self.assertNotEqual(result, key, f"翻译键 {key} 未被翻译")

    def test_wizard_translations_present(self):
        """向导相关翻译键存在"""
        keys = [
            "ui.wizard.title",
            "ui.wizard.btn.next",
            "ui.wizard.btn.cancel",
        ]
        for key in keys:
            result = self.tr(key)
            self.assertIsInstance(result, str)


# ============================================================
# 2. 安装器检测
# ============================================================

class TestWebView(unittest.TestCase):
    """检测 ui/web/__init__.py"""

    def test_module_imports(self):
        """模块可导入"""
        sys.path.insert(0, str(_BASE / "ui"))
        from web import verify_backend, launch_webview
        self.assertTrue(callable(verify_backend))
        self.assertTrue(callable(launch_webview))

    def test_verify_backend_returns_dict(self):
        """verify_backend 返回 dict"""
        sys.path.insert(0, str(_BASE / "ui"))
        from web import verify_backend
        result = verify_backend()
        self.assertIsInstance(result, dict)
        self.assertIn("available", result)


# ============================================================
# 5. 聊天页检测
# ============================================================

class TestChatPage(unittest.TestCase):
    """检测 ui/pages/chatP.py"""

    def test_module_imports(self):
        """模块可导入"""
        sys.path.insert(0, str(_BASE / "ui"))
        from pages.chatP import ChatPage
        self.assertTrue(ChatPage is not None)

    def test_chat_page_has_methods(self):
        """ChatPage 有核心方法"""
        sys.path.insert(0, str(_BASE / "ui"))
        from pages.chatP import ChatPage
        self.assertTrue(hasattr(ChatPage, "load"))
        self.assertTrue(hasattr(ChatPage, "render"))
        self.assertTrue(hasattr(ChatPage, "set_status"))
        self.assertTrue(hasattr(ChatPage, "thinking"))

    def test_js_injection_defined(self):
        """JS 注入代码存在"""
        sys.path.insert(0, str(_BASE / "ui"))
        from pages.chatP import _CHAT_JS
        self.assertTrue(len(_CHAT_JS) > 0)
        self.assertIn("fetch", _CHAT_JS)
        self.assertIn("WebSocket", _CHAT_JS)
        self.assertIn("XMLHttpRequest", _CHAT_JS)

    def test_css_injection_defined(self):
        """CSS 注入模板存在"""
        sys.path.insert(0, str(_BASE / "ui"))
        from pages.chatP import _CSS_JS
        self.assertTrue(len(_CSS_JS) > 0)
        self.assertIn("accent", _CSS_JS)
        self.assertIn("bg_app", _CSS_JS)

    def test_html_templates_defined(self):
        """HTML 模板存在"""
        sys.path.insert(0, str(_BASE / "ui"))
        from pages.chatP import _CHAT_HTML, _MSG_HTML
        self.assertTrue(len(_CHAT_HTML) > 0)
        self.assertTrue(len(_MSG_HTML) > 0)
        self.assertIn("cc", _CHAT_HTML)  # chat container id
        self.assertIn("ml", _CHAT_HTML)  # message list id
        self.assertIn("ib", _CHAT_HTML)  # input bar id


# ============================================================
# 6. 核心管线检测
# ============================================================

class TestKernel(unittest.TestCase):
    """检测 kernel 模块"""

    def test_scout_imports(self):
        """scout 可导入"""
        sys.path.insert(0, str(_BASE / "kernel"))
        import scout
        self.assertTrue(hasattr(scout, "probe"))
        self.assertTrue(hasattr(scout, "fetch"))
        # ⚠ 2026-10-17：`publishToManifest` 已按逐行审计删除（旧设计·该删），断言随之下线。

    def test_large_imports(self):
        """large 可导入"""
        sys.path.insert(0, str(_BASE / "kernel"))
        import large
        self.assertTrue(hasattr(large, "manifest"))
        self.assertTrue(hasattr(large, "am"))
        self.assertTrue(hasattr(large, "usm"))
        self.assertTrue(hasattr(large, "steak"))
        self.assertTrue(hasattr(large, "forge"))

    def test_glory_imports(self):
        """glory 可导入"""
        sys.path.insert(0, str(_BASE / "kernel"))
        import glory
        self.assertTrue(hasattr(glory, "adopt"))
        self.assertTrue(hasattr(glory, "status"))
        self.assertTrue(hasattr(glory, "verify"))
        self.assertTrue(hasattr(glory, "keepalive"))

    def test_manifest_exists(self):
        """mani.json 存在"""
        mani_path = _BASE / "kernel" / "large" / "mani.json"
        self.assertTrue(mani_path.exists(), "mani.json 不存在")

    def test_forge_config_exists(self):
        """forge.json 存在"""
        forge_path = _BASE / "kernel" / "large" / "forge.json"
        self.assertTrue(forge_path.exists(), "forge.json 不存在")

    def test_forge_json_readable(self):
        """forge.json 全部字段可读"""
        import json
        with open(_BASE / "kernel" / "large" / "forge.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertIn("visual_tokens", cfg)
        self.assertIn("widget_library", cfg)
        self.assertIn("composite_presets", cfg)
        self.assertIn("type_preset_map", cfg)
        self.assertIn("qss_template", cfg)


# ============================================================
# 7. NSIS 检测
# ============================================================

