# L1-1101：Rheia 侧边栏客制区 — 配置存储 + 图形化管理面板
r"""Rheia 侧边栏客制区 — 配置存储 + 图形化管理面板"""






from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from typing import Any

logger = logging.getLogger(__name__)



try:
    from ui.i18n import tr
except ImportError:
    tr = lambda key, **fmt: key

_LOCALDATA = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia")
_CONFIG_PATH = os.path.join(_LOCALDATA, "sidebar_custom.json")



_PRESET_ENTRIES: list[dict] = [
    {"id": "persona",        "type": "preset", "enabled": True, "order": 1,
     "label_key": "ui.nav.persona",  "label_zh": "人格",            "label_en": "Persona",         "emblem": ""},
    {"id": "provider_panel", "type": "preset", "enabled": True, "order": 2,
     "label_key": "ui.nav.provider", "label_zh": "Provider 控制台", "label_en": "Provider Panel",  "emblem": ""},
    {"id": "diag_panel",     "type": "preset", "enabled": True, "order": 3,
     "label_key": "ui.nav.diag",     "label_zh": "Gateway 诊断",    "label_en": "Gateway Diag",    "emblem": ""},
    {"id": "restore_backup", "type": "preset", "enabled": True, "order": 4,
     "label_key": "ui.nav.backup",   "label_zh": "备份与恢复",      "label_en": "Backup & Restore", "emblem": ""},
]


_ID_LABEL_KEY = {e["id"]: e["label_key"] for e in _PRESET_ENTRIES}






# L52-54：_ensure_default — 返回默认配置。
def _ensure_default() -> dict:
    r"""返回默认配置。"""
    return {"entries": list(_PRESET_ENTRIES)}



# L59-70：_backfill_label_keys — 给旧配置补 label_key（幂等）；客户自定义条目不动。
def _backfill_label_keys(cfg: dict) -> dict:
    r"""给旧配置补 label_key（幂等）；客户自定义条目不动。"""
    try:
        for ent in (cfg.get("entries") or []):
            if not isinstance(ent, dict):
                continue
            _id = str(ent.get("id") or "")
            if not ent.get("label_key") and _id in _ID_LABEL_KEY:
                ent["label_key"] = _ID_LABEL_KEY[_id]
    except Exception:
        pass
    return cfg

# L73-121：load_config — 读客制区配置：优先 baseline_pool.db → fallback sidebar_custom.json。
def load_config() -> dict:
    r"""读客制区配置：优先 baseline_pool.db → fallback sidebar_custom.json。"""





    try:
        from kernel.scout import getPref, ensureRecoveryTables
        ensureRecoveryTables()
        raw = getPref("sidebar_custom")
        if raw:
            cfg = json.loads(raw)
            if isinstance(cfg, dict) and isinstance(cfg.get("entries"), list):

                existing_ids = {e.get("id") for e in cfg["entries"]}
                dirty = False
                for preset in _PRESET_ENTRIES:
                    if preset["id"] not in existing_ids:
                        cfg["entries"].append(dict(preset))
                        dirty = True
                if dirty:
                    _write_config(cfg)
                return _backfill_label_keys(cfg)
    except Exception:
        pass


    try:
        if os.path.isfile(_CONFIG_PATH):
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if isinstance(cfg, dict) and isinstance(cfg.get("entries"), list):

                existing_ids = {e.get("id") for e in cfg["entries"]}
                dirty = False
                for preset in _PRESET_ENTRIES:
                    if preset["id"] not in existing_ids:
                        cfg["entries"].append(dict(preset))
                        dirty = True
                if dirty:
                    _write_config(cfg)
                return _backfill_label_keys(cfg)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("custom_area_manager: config corrupt, rebuilding — %s", e)

    default = _ensure_default()
    _write_config(default)
    return default


# L125-154：_write_config — 写 sidebar_custom 到 baseline_pool.db + fallback sidebar_custom.json。
def _write_config(cfg: dict) -> bool:
    r"""写 sidebar_custom 到 baseline_pool.db + fallback sidebar_custom.json。"""
    success = True

    try:
        from kernel.scout import setPref, ensureRecoveryTables
        ensureRecoveryTables()
        setPref("sidebar_custom", json.dumps(cfg, ensure_ascii=False))
    except Exception as e:
        logger.warning("custom_area_manager: DB write failed, fallback to JSON — %s", e)
        success = False

    try:
        os.makedirs(_LOCALDATA, exist_ok=True)
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.error("custom_area_manager: write failed — %s", e)
        success = False




    try:
        from platform.slotApi import refresh_slot_declarations
        _r = refresh_slot_declarations()
        logger.info("custom_area_manager: 声明链已刷新 %s", _r)
    except Exception as e:
        logger.warning("custom_area_manager: demand refresh skipped — %s", e)
    return success






# L162-167：get_enabled_entries — 返回 enabled=true 的条目，按 order 升序排列。
def get_enabled_entries() -> list[dict]:
    r"""返回 enabled=true 的条目，按 order 升序排列。"""
    cfg = load_config()
    entries = [e for e in cfg.get("entries", []) if e.get("enabled", True)]
    entries.sort(key=lambda e: e.get("order", 999))
    return entries


# L171-176：get_all_entries — 返回全部条目（含 disabled），按 order 升序。
def get_all_entries() -> list[dict]:
    r"""返回全部条目（含 disabled），按 order 升序。"""
    cfg = load_config()
    entries = list(cfg.get("entries", []))
    entries.sort(key=lambda e: e.get("order", 999))
    return entries


# L180-182：reset_to_default — 恢复默认配置（四个预装模块全部 enabled，删除所有自定义条目）。
def reset_to_default() -> bool:
    r"""恢复默认配置（四个预装模块全部 enabled，删除所有自定义条目）。"""
    return _write_config(_ensure_default())


# L186-193：update_entry — 更新单条目的字段（enabled / order / label_zh / label_en 等）。
def update_entry(entry_id: str, updates: dict) -> bool:
    r"""更新单条目的字段（enabled / order / label_zh / label_en 等）。"""
    cfg = load_config()
    for e in cfg.get("entries", []):
        if e.get("id") == entry_id:
            e.update(updates)
            return _write_config(cfg)
    return False


# L197-216：add_custom_entry — 添加一条自定义条目。自动分配 id 和最大 order+1。
def add_custom_entry(entry: dict) -> bool:
    r"""添加一条自定义条目。自动分配 id 和最大 order+1。"""
    cfg = load_config()
    entries = cfg.get("entries", [])
    max_order = max((e.get("order", 0) for e in entries), default=0)
    existing_ids = {e.get("id") for e in entries}


    idx = 1
    while f"custom_{idx:03d}" in existing_ids:
        idx += 1
    entry["id"] = f"custom_{idx:03d}"
    entry["order"] = max_order + 1
    if "type" not in entry:
        entry["type"] = "script"
    if "enabled" not in entry:
        entry["enabled"] = True

    entries.append(entry)
    return _write_config(cfg)


# L220-231：remove_entry — 删除一条自定义条目。preset 类型不可删除。
def remove_entry(entry_id: str) -> bool:
    r"""删除一条自定义条目。preset 类型不可删除。"""
    cfg = load_config()
    entries = cfg.get("entries", [])
    for e in entries:
        if e.get("id") == entry_id:
            if e.get("type") == "preset":
                logger.warning("custom_area_manager: refuse to remove preset '%s'", entry_id)
                return False
            entries.remove(e)
            return _write_config(cfg)
    return False


# L235-243：reorder_entries — 按给定 id 顺序重排所有条目的 order 字段。
def reorder_entries(ordered_ids: list[str]) -> bool:
    r"""按给定 id 顺序重排所有条目的 order 字段。"""
    cfg = load_config()
    entries = cfg.get("entries", [])
    id_map = {e["id"]: e for e in entries}
    for new_order, eid in enumerate(ordered_ids, start=1):
        if eid in id_map:
            id_map[eid]["order"] = new_order
    return _write_config(cfg)






# L251-264：_get_gateway_token — 读 gateway token 用于 API 调用。
def _get_gateway_token() -> str:
    r"""读 gateway token 用于 API 调用。"""
    token = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "").strip()
    if token:
        return token
    token_file = os.path.join(_LOCALDATA, "gateway_config.json")
    try:
        if os.path.isfile(token_file):
            with open(token_file, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            return cfg.get("token", "") or ""
    except (json.JSONDecodeError, OSError):
        pass
    return ""


# L268-274：_stake — 写桩子到 stake.log，记录 CLI 调用真实状态。
def _stake(tag: str, msg: str = "", ok: bool = True) -> None:
    r"""写桩子到 stake.log，记录 CLI 调用真实状态。"""
    try:
        from main.main import stk
        stk(f"custom.{tag}", msg, ok=ok)
    except Exception:
        pass


# L278-319：_discover_available_model — 从 openclaw models list --json 发现一个可用模型（排除 claude-cli 系统登录）。
def _discover_available_model() -> str:
    r"""从 openclaw models list --json 发现一个可用模型（排除 claude-cli 系统登录）。"""






    import subprocess
    from main.main import cli_usage
    models_list = cli_usage("models", "list") or "openclaw models list"
    try:
        result = subprocess.run(
            [
                "wsl", "-d", os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu",
                "-u", "root", "--", "bash", "-c",
                "export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; "
                f"{models_list} --json 2>/dev/null",
            ],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        _stake("discover", f"rc={result.returncode} stdout_len={len(result.stdout or '')}", ok=(result.returncode == 0))
        if result.returncode != 0 or not result.stdout.strip():
            _stake("discover", f"stderr={ (result.stderr or '')[:200]!r}", ok=False)
            return ""
        data = json.loads(result.stdout)
        models = data.get("models", []) if isinstance(data, dict) else []
        for m in models:
            if not isinstance(m, dict) or not m.get("available"):
                continue
            key = m.get("key", "")
            if not key or key.startswith("claude-cli/"):
                continue
            _stake("discover", f"found model={key}")
            return key
        _stake("discover", "no available non-claude-cli model", ok=False)
    except Exception as e:
        _stake("discover", f"exception {type(e).__name__}: {e}", ok=False)
        logger.warning("custom_area_manager: discover model failed — %s", e)
    return ""


# L323-333：_intent_agent — 客制区的 AI 需求分析交给当前那张卡自己的号（多 Agent 并存，不写死 main）。
def _intent_agent() -> str:
    r"""客制区的 AI 需求分析交给当前那张卡自己的号（多 Agent 并存，不写死 main）。"""




    try:
        from ui.web.browser import _slots_current_agent
        return _slots_current_agent() or "main"
    except Exception:
        return "main"


# L337-488：execute_intent — 发送 prompt 到 AI：优先走 Gateway HTTP API，404 时 fallback 到 openclaw agent CLI。
def execute_intent(prompt: str) -> str:
    r"""发送 prompt 到 AI：优先走 Gateway HTTP API，404 时 fallback 到 openclaw agent CLI。"""
    if not prompt or not prompt.strip():
        return ""

    model = _discover_available_model()
    _stake("execute_intent", f"model={model!r}")


    try:
        import urllib.request
        import urllib.error

        token = _get_gateway_token()
        body = json.dumps({
            "model": "openclaw/default",
            "messages": [
                {"role": "system", "content": (
                    "你是需求分析师。理解用户意图，追问缺失细节（触发方式、具体行为等），"
                    "引导把需求说清楚。回复简短友好，用中文。如果需求已足够清晰，末尾加 [READY]。"
                    "不要执行操作，只做需求交流。"
                )},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 600,
            "temperature": 0.7,
        }).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }

        if model:
            headers["x-openclaw-model"] = model
        req = urllib.request.Request(
            "http://127.0.0.1:18789/v1/chat/completions",
            data=body, headers=headers, method="POST",
        )

        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            choices = result.get("choices", [])
            if choices:
                msg = choices[0].get("message", {})
                out = (msg.get("content") or "").strip()
                if out:
                    logger.info("custom_area_manager: HTTP response — %s", out[:80])
                    return out
        return tr("ui.custom.ai_no_content")
    except urllib.error.HTTPError as e:
        _stake("http", f"HTTPError {e.code} {e.reason}", ok=False)
        logger.warning("custom_area_manager: HTTP %s — %s", e.code, e.reason)
        if e.code == 401:
            return tr("ui.custom.gw_auth_failed")
        if e.code not in (404,):
            return tr("ui.custom.gw_error", code=e.code, reason=e.reason)

    except urllib.error.URLError as e:
        _stake("http", f"URLError {e.reason}", ok=False)
        logger.error("custom_area_manager: Gateway unreachable — %s", e.reason)
        return tr("ui.custom.gw_unreachable")
    except Exception as e:
        _stake("http", f"exception {type(e).__name__}: {e}", ok=False)
        logger.error("custom_area_manager: HTTP failed — %s", e)
        return tr("ui.custom.exec_error", e=str(e))


    logger.info("custom_area_manager: HTTP 404, falling back to openclaw agent CLI")
    try:
        import subprocess



        msg_file = None
        try:
            fd, msg_file = tempfile.mkstemp(suffix=".txt", prefix="okoc-intent-")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(prompt)

            wsl_path = msg_file.replace("\\", "/")
            if len(wsl_path) > 1 and wsl_path[1] == ":":
                wsl_path = "/mnt/" + wsl_path[0].lower() + wsl_path[2:]



            cmd = [
                "wsl", "-d", os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu",
                "-u", "root", "openclaw", "agent",
                "--agent", _intent_agent(),
                "--session-key", "custom-area-intent",
                "--message-file", wsl_path,
                "--json",
            ]
            if model:
                cmd += ["--model", model]
            result = subprocess.run(
                cmd,
                capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        finally:
            if msg_file and os.path.exists(msg_file):
                try:
                    os.remove(msg_file)
                except OSError:
                    pass
        _stake("cli", f"rc={result.returncode} stdout_len={len(result.stdout or '')} stderr_len={len(result.stderr or '')}")
        if result.returncode == 0 and result.stdout.strip():
            try:
                data = json.loads(result.stdout)


                payloads = None
                if isinstance(data, dict):
                    result_block = data.get("result")
                    if isinstance(result_block, dict):
                        payloads = result_block.get("payloads")
                    if payloads is None:
                        payloads = data.get("payloads")
                if isinstance(payloads, list) and payloads:
                    first = payloads[0]
                    if isinstance(first, dict):
                        out = (first.get("text") or "").strip()
                        if out:
                            logger.info("custom_area_manager: CLI response — %s", out[:80])
                            return out

                if isinstance(data, dict):
                    fallback = data.get("final") or data.get("text") or ""
                    if fallback:
                        logger.info("custom_area_manager: CLI response (fallback) — %s", str(fallback)[:80])
                        return str(fallback).strip()
                _stake("cli", "parsed but no text found", ok=False)
            except json.JSONDecodeError:
                _stake("cli", "stdout not valid json", ok=False)
        else:
            _stake("cli", f"rc={result.returncode} stderr={(result.stderr or '')[:300]!r}", ok=False)
        stderr_tail = (result.stderr or "").strip()
        if stderr_tail:
            logger.error("custom_area_manager: CLI stderr — %s", stderr_tail[:200])
        return tr("ui.custom.cli_no_content")
    except FileNotFoundError:
        logger.error("custom_area_manager: wsl not found")
        return tr("ui.custom.wsl_not_found")
    except subprocess.TimeoutExpired:
        logger.error("custom_area_manager: CLI timeout")
        return tr("ui.custom.cli_timeout")
    except Exception as e:
        logger.error("custom_area_manager: CLI failed — %s", e)
        return tr("ui.custom.cli_error", e=str(e))






try:
    from PySide6.QtCore import Qt, Signal
    from PySide6.QtWidgets import (
        QCheckBox, QDialog, QHBoxLayout, QLabel,
        QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
        QPushButton, QTextEdit, QVBoxLayout, QWidget, QScrollArea,
        QSizePolicy,
    )
    try:
        from ui.theme import BG, TEXT, ACCENT, ACCENT_HOVER, BORDER
    except ImportError:
        BG = {"deep": "#1a1a1a", "card": "#2a2a2a", "hover": "#333333"}
        TEXT = {"primary": "#e0e0e0", "secondary": "#b0b0b0", "hint": "#a0a0a0"}
        ACCENT = "#f37021"
        ACCENT_HOVER = "#ff8833"
        BORDER = "#444444"

    _HAS_PYQT = True
except ImportError:
    _HAS_PYQT = False
    QDialog = object
    Signal = object


# L520-1101：CustomAreaManager — 客制区图形化管理面板。
class CustomAreaManager(QDialog):
    r"""客制区图形化管理面板。"""








    changed = Signal()
    ai_reply = Signal(str)

    def __init__(self, parent: QWidget | None = None,
                 edit_entry_id: str = "") -> None:
        if not _HAS_PYQT:
            raise RuntimeError("PySide6 not available")

        super().__init__(parent)
        self.ai_reply.connect(self._on_ai_reply)

        self._want_edit_id = edit_entry_id or ""
        self.setWindowTitle(tr("ui.custom.title"))
        from ui.theme import fitToScreen
        fitToScreen(self, 0.28, 0.35)
        self.setStyleSheet(f"""
            QDialog {{ background: {BG['deep']}; color: {TEXT['primary']}; }}
            QLabel {{ color: {TEXT['primary']}; background: transparent; }}
            QListWidget {{
                background: {BG['card']};
                color: {TEXT['primary']};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 4px;
            }}
            QListWidget::item {{ padding: 2px 0; }}
            QLineEdit {{
                background: {BG['card']};
                color: {TEXT['primary']};
                border: 1px solid {BORDER};
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 13px;
            }}
            QTextEdit {{
                background: {BG['card']};
                color: {TEXT['primary']};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 8px;
                font-size: 12px;
            }}
            QCheckBox {{ color: {TEXT['primary']}; spacing: 6px; }}
        """)

        self._entries: list[dict] = []
        self._form_visible = False
        self._editing_id: str | None = None
        self._build()



    def _build(self) -> None:
        lo = QVBoxLayout(self)
        lo.setSpacing(10)


        lo.addWidget(QLabel(tr("ui.custom.preset_modules")))
        lo.addWidget(self._sectionHint(tr("ui.custom.drag_sort")))

        self._preset_list = QListWidget()
        self._preset_list.setDragDropMode(self._preset_list.DragDropMode.InternalMove)
        self._preset_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._preset_list.setMaximumHeight(200)
        lo.addWidget(self._preset_list)


        custom_header = QHBoxLayout()
        custom_header.addWidget(QLabel(tr("ui.custom.custom_items")))
        custom_header.addStretch()
        edit_last_btn = QPushButton(tr("ui.btn.edit"))
        edit_last_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        edit_last_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {TEXT['hint']};
                border: 1px solid {BORDER}; border-radius: 4px;
                padding: 2px 10px; font-size: 11px;
            }}
            QPushButton:hover {{ color: {ACCENT}; border-color: {ACCENT}; }}
        """)
        edit_last_btn.clicked.connect(self._on_edit_selected_custom)
        custom_header.addWidget(edit_last_btn)
        lo.addLayout(custom_header)
        lo.addWidget(self._sectionHint(tr("ui.custom.edit_hint")))

        self._custom_list = QListWidget()
        self._custom_list.setDragDropMode(self._custom_list.DragDropMode.InternalMove)
        self._custom_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._custom_list.setMaximumHeight(100)
        lo.addWidget(self._custom_list)


        self._add_btn = QPushButton(tr("ui.custom.add_item"))
        self._add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._add_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ACCENT}; color: #0a0a0a;
                border: none; border-radius: 6px;
                padding: 6px 16px; font-size: 12px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {ACCENT_HOVER}; }}
        """)
        self._add_btn.clicked.connect(self._toggle_form)

        add_row = QHBoxLayout()
        add_row.addStretch()
        add_row.addWidget(self._add_btn)
        add_row.addStretch()
        lo.addLayout(add_row)


        self._form = QWidget()
        self._form.setVisible(False)
        form_lo = QVBoxLayout(self._form)
        form_lo.setSpacing(8)


        name_row = QHBoxLayout()
        name_row.addWidget(QLabel(tr("ui.custom.func_name")))
        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText(tr("ui.custom.func_name_ph"))
        name_row.addWidget(self._name_input)
        form_lo.addLayout(name_row)


        form_lo.addWidget(QLabel(tr("ui.custom.req_chat")))
        self._chat_scroll = QScrollArea()
        self._chat_scroll.setWidgetResizable(True)
        self._chat_scroll.setStyleSheet(f"""
            QScrollArea {{
                background: {BG['deep']};
                border: 1px solid {BORDER};
                border-radius: 8px;
            }}
            QScrollArea > QWidget > QWidget {{
                background: transparent;
            }}
        """)
        self._chat_container = QWidget()
        self._chat_container.setStyleSheet("background: transparent;")
        self._chat_layout = QVBoxLayout(self._chat_container)
        self._chat_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._chat_layout.setSpacing(8)
        self._chat_layout.setContentsMargins(10, 10, 10, 10)
        self._chat_scroll.setWidget(self._chat_container)
        self._chat_scroll.setMinimumHeight(240)

        self._chat_scroll.verticalScrollBar().rangeChanged.connect(
            lambda _min, _max: self._chat_scroll.verticalScrollBar().setValue(_max))

        form_lo.addWidget(self._chat_scroll, stretch=1)


        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self._chat_input = QLineEdit()
        self._chat_input.setPlaceholderText(tr("ui.custom.req_chat_ph"))
        self._chat_input.setMinimumHeight(36)
        self._chat_input.setStyleSheet(f"""
            QLineEdit {{
                background: {BG['card']};
                color: {TEXT['primary']};
                border: 1px solid {BORDER};
                border-radius: 18px;
                padding: 6px 14px;
                font-size: 13px;
            }}
            QLineEdit:focus {{ border-color: {ACCENT}; }}
        """)
        self._chat_input.returnPressed.connect(self._on_chat_send)
        input_row.addWidget(self._chat_input, stretch=1)
        send_btn = QPushButton(tr("ui.custom.send"))
        send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        send_btn.setStyleSheet(self._btnSendStyle())
        send_btn.clicked.connect(self._on_chat_send)
        input_row.addWidget(send_btn)
        form_lo.addLayout(input_row)


        form_btns = QHBoxLayout()
        form_btns.addStretch()
        cancel = QPushButton(tr("ui.btn.cancel"))
        cancel.clicked.connect(self._toggle_form)
        cancel.setStyleSheet(self._btnStyle(""))
        form_btns.addWidget(cancel)
        save_btn = QPushButton(tr("ui.custom.save_func"))
        save_btn.clicked.connect(self._on_save_intent)
        save_btn.setStyleSheet(self._btnStyle(ACCENT))
        form_btns.addWidget(save_btn)
        form_lo.addLayout(form_btns)

        lo.addWidget(self._form, stretch=1)


        bot = QHBoxLayout()
        reset = QPushButton(tr("ui.custom.reset_default"))
        reset.clicked.connect(self._on_reset)
        reset.setStyleSheet(self._btnStyle(""))
        bot.addWidget(reset)
        bot.addStretch()
        close = QPushButton(tr("ui.btn.close"))
        close.clicked.connect(self.accept)
        close.setStyleSheet(self._btnStyle(ACCENT))
        bot.addWidget(close)
        lo.addLayout(bot)


        self._refresh_lists()

        if self._want_edit_id:
            self._select_entry_by_id(self._want_edit_id)
            self._want_edit_id = ""

    def _select_entry_by_id(self, eid: str) -> None:
        r"""在自定义列表里选中指定条目，并展开编辑（回填已有对话）。"""
        for i in range(self._custom_list.count()):
            it = self._custom_list.item(i)
            if it and (it.data(Qt.ItemDataRole.UserRole) or {}).get("id") == eid:
                self._custom_list.setCurrentItem(it)
                self._on_edit_selected_custom()
                return



    def _refresh_lists(self) -> None:
        self._entries = get_all_entries()
        presets = [e for e in self._entries if e.get("type") == "preset"]
        customs = [e for e in self._entries if e.get("type") != "preset"]

        self._preset_list.clear()
        for e in presets:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, e)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if e.get("enabled", True) else Qt.CheckState.Unchecked)
            label = e.get("label_zh", e.get("label_en", e.get("id", "")))
            item.setText(label)
            self._preset_list.addItem(item)

        self._custom_list.clear()
        for e in customs:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, e)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if e.get("enabled", True) else Qt.CheckState.Unchecked)
            label = e.get("label_zh", e.get("label_en", e.get("id", "")))
            item.setText(label)
            self._custom_list.addItem(item)



    def _on_edit_selected_custom(self) -> None:
        r"""选中自定义条目 → 展开编辑表单（携带已有名称和描述回填聊天）。"""
        current = self._custom_list.currentItem()
        if not current:
            QMessageBox.information(self, tr("ui.browser.notice"), tr("ui.custom.select_item_hint"))
            return
        entry = current.data(Qt.ItemDataRole.UserRole)
        if not entry:
            return
        eid = entry.get("id", "")
        self._editing_id = eid
        self._name_input.setText(entry.get("label_zh", ""))
        self._form_visible = True
        self._form.setVisible(True)
        self._add_btn.setText(tr("ui.custom.collapse"))
        self._fit_height()
        self._clear_chat()
        self._add_bubble(
            "AI", tr("ui.custom.current_req"),
            is_ai=True,
        )
        action = entry.get("action", {})
        desc = action.get("description", "") if isinstance(action, dict) else ""
        if desc:
            self._add_bubble(tr("ui.custom.req"), desc, is_ai=False, accent="hint")
        self._chat_input.setFocus()

    def _fit_height(self) -> None:
        r"""按实际内容自适应窗口高度：表单收起时紧凑、展开时撑高。"""





        from PySide6.QtWidgets import QApplication
        avail = QApplication.primaryScreen().availableGeometry()
        if self._form_visible:
            new_h = int(avail.height() * 0.82)
        else:
            new_h = int(avail.height() * 0.35)
        new_w = self.width()

        x = self.x()
        y = self.y() + (self.height() - new_h) // 2
        self.setGeometry(x, y, new_w, new_h)

    def _toggle_form(self) -> None:
        self._form_visible = not self._form_visible
        self._form.setVisible(self._form_visible)
        self._add_btn.setText(
            tr("ui.custom.collapse_btn") if self._form_visible else tr("ui.custom.add_entry_btn"))
        self._fit_height()
        if self._form_visible:
            self._clear_chat()
            self._chat_input.clear()
            self._chat_input.setEnabled(True)
            self._add_bubble(
                "AI", tr("ui.custom.welcome_prompt"),
                is_ai=True,
            )
            self._name_input.setFocus()
        else:
            self._editing_id = None



    def _clear_chat(self) -> None:
        r"""清空聊天气泡区。"""
        while self._chat_layout.count():
            child = self._chat_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    def _add_bubble(self, sender: str, text: str,
                    is_ai: bool = False,
                    accent: str = "") -> None:
        r"""添加一条聊天气泡到聊天区域。"""








        bubble = QWidget()
        bubble_lo = QVBoxLayout(bubble)
        bubble_lo.setContentsMargins(0, 0, 0, 0)
        bubble_lo.setSpacing(2)


        sender_color = "#4da6ff" if is_ai else (accent or ACCENT)
        sender_lb = QLabel(sender)
        sender_lb.setStyleSheet(f"""
            color: {sender_color}; font-size: 11px; font-weight: 600;
            background: transparent; padding: 0 4px;
        """)
        if is_ai:
            sender_lb.setAlignment(Qt.AlignmentFlag.AlignLeft)
        else:
            sender_lb.setAlignment(Qt.AlignmentFlag.AlignRight)
        bubble_lo.addWidget(sender_lb)


        bubble_bg = "#1e3a5f" if is_ai else "#3a2a1a"
        bubble_border = "#2d5a8a" if is_ai else "#5a3a2a"
        text_color = TEXT["primary"] if is_ai else TEXT["primary"]
        msg_lb = QLabel(text)
        msg_lb.setWordWrap(True)
        msg_lb.setStyleSheet(f"""
            QLabel {{
                background: {bubble_bg};
                color: {text_color};
                border: 1px solid {bubble_border};
                border-radius: 10px;
                padding: 8px 12px;
                font-size: 13px;
                line-height: 1.5;
            }}
        """)
        msg_lb.setTextFormat(Qt.TextFormat.PlainText)
        if is_ai:
            msg_lb.setAlignment(Qt.AlignmentFlag.AlignLeft)
        else:
            msg_lb.setAlignment(Qt.AlignmentFlag.AlignRight)
        bubble_lo.addWidget(msg_lb)


        wrapper = QHBoxLayout()
        wrapper.setContentsMargins(0, 0, 0, 0)
        if is_ai:
            wrapper.addWidget(bubble, stretch=0)
            wrapper.addStretch(1)

            bubble.setMaximumWidth(int(self.width() * 0.72))
        else:
            wrapper.addStretch(1)
            wrapper.addWidget(bubble, stretch=0)
            bubble.setMaximumWidth(int(self.width() * 0.72))

        self._chat_layout.addLayout(wrapper)


    def _on_chat_send(self) -> None:
        user_msg = self._chat_input.text().strip()
        if not user_msg:
            return
        self._add_bubble(tr("ui.custom.you"), user_msg, is_ai=False)
        self._chat_input.clear()
        self._chat_input.setEnabled(False)


        name_hint = self._name_input.text().strip() or tr("ui.custom.pending")
        prompt = (
            tr("ui.custom.user_wants_feature", name_hint=name_hint)
            + tr("ui.custom.user_said", user_msg=user_msg)
        )

        def _worker():
            reply = execute_intent(prompt)
            self.ai_reply.emit(reply)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_ai_reply(self, reply: str) -> None:
        r"""主线程回调：AI 回复到达后安全更新聊天气泡。"""
        self._add_bubble("AI", reply, is_ai=True)
        self._chat_input.setEnabled(True)
        self._chat_input.setFocus()

    def _on_save_intent(self) -> None:
        name = self._name_input.text().strip()
        if not name:
            QMessageBox.warning(self, tr("ui.browser.notice"), tr("ui.custom.enter_func_name"))
            return

        lines: list[str] = []
        for i in range(self._chat_layout.count()):
            item = self._chat_layout.itemAt(i)
            if not item:
                continue

            wrapper_lo = item.layout()
            if not wrapper_lo:
                continue
            for j in range(wrapper_lo.count()):
                w_item = wrapper_lo.itemAt(j)
                if not w_item:
                    continue
                bubble = w_item.widget()
                if not bubble:
                    continue

                inner = bubble.layout()
                if inner and inner.count() >= 2:
                    sender_lb = inner.itemAt(0).widget()
                    msg_lb = inner.itemAt(1).widget()
                    if isinstance(sender_lb, QLabel) and isinstance(msg_lb, QLabel):
                        sender_text = sender_lb.text()
                        msg_text = msg_lb.text()
                        if sender_text and msg_text:
                            lines.append(f"{sender_text}: {msg_text}")
        raw = "\n".join(lines)
        if not raw.strip():
            QMessageBox.warning(self, tr("ui.browser.notice"), tr("ui.custom.describe_req"))
            return

        entry: dict[str, Any] = {
            "type": "intent",
            "label_zh": name,
            "label_en": name,
            "action": {"description": raw},
        }


        if getattr(self, "_editing_id", None):
            update_entry(self._editing_id, entry)
            self._editing_id = None
        else:
            add_custom_entry(entry)

        self._name_input.clear()
        self._clear_chat()
        self._chat_input.clear()
        self._refresh_lists()
        self._toggle_form()
        self._emit_changed()

    def _on_reset(self) -> None:
        reply = QMessageBox.question(
            self, tr("ui.custom.confirm"),
            tr("ui.custom.reset_confirm"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        reset_to_default()
        self._refresh_lists()
        self._emit_changed()

    def _emit_changed(self) -> None:

        self._sync_from_lists()
        self.changed.emit()

    def _sync_from_lists(self) -> None:
        r"""把两个列表的 toggle + 顺序一次写回。"""






        cfg = load_config()
        entries = cfg.get("entries") or []
        by_id = {str(e.get("id") or ""): e for e in entries if isinstance(e, dict)}
        ordered: list[str] = []
        for lst in (self._preset_list, self._custom_list):
            for i in range(lst.count()):
                item = lst.item(i)
                entry = item.data(Qt.ItemDataRole.UserRole)
                if not entry:
                    continue
                eid = str(entry.get("id") or "")
                if not eid:
                    continue
                tgt = by_id.get(eid)
                if isinstance(tgt, dict):
                    tgt["enabled"] = (item.checkState() == Qt.CheckState.Checked)
                ordered.append(eid)
        for new_order, eid in enumerate(ordered, start=1):
            tgt = by_id.get(eid)
            if isinstance(tgt, dict):
                tgt["order"] = new_order
        _write_config(cfg)

    def closeEvent(self, event) -> None:
        self._sync_from_lists()
        super().closeEvent(event)



    @staticmethod
    def _sectionHint(text: str) -> QLabel:
        lb = QLabel(text)
        lb.setStyleSheet(f"color: {TEXT['hint']}; font-size: 11px; background: transparent;")
        return lb

    @staticmethod
    def _btnStyle(bg: str) -> str:
        if bg:
            return f"""
                QPushButton {{
                    background: {bg}; color: #0a0a0a; border: none;
                    border-radius: 6px; padding: 6px 16px; font-size: 12px; font-weight: 600;
                }}
                QPushButton:hover {{ background: #ff8833; }}
            """
        return f"""
            QPushButton {{
                background: transparent; color: {TEXT['secondary']};
                border: 1px solid {BORDER}; border-radius: 6px;
                padding: 6px 16px; font-size: 12px;
            }}
            QPushButton:hover {{ color: {TEXT['primary']}; border-color: {ACCENT}; }}
        """

    @staticmethod
    def _btnSendStyle() -> str:
        return f"""
            QPushButton {{
                background: {ACCENT}; color: #0a0a0a; border: none;
                border-radius: 14px; padding: 6px 14px;
                font-size: 12px; font-weight: 600; min-height: 30px;
            }}
            QPushButton:hover {{ background: #ff8833; }}
        """
