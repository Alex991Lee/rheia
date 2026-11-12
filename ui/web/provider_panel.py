# L1-945：Rheia Provider 完整模式面板
r"""Rheia Provider 完整模式面板"""













from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import sys
import time
from typing import Any

logger = logging.getLogger(__name__)


_proj = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _proj not in sys.path:
    sys.path.insert(0, _proj)


try:
    from ui.theme import BG, TEXT, ACCENT, ACCENT_HOVER, STATUS, BORDER as _B
except ImportError:
    BG = {"deep": "#1a1a1a", "card": "#2a2a2a", "hover": "#333333"}
    TEXT = {"primary": "#e0e0e0", "secondary": "#b0b0b0", "hint": "#a0a0a0"}
    ACCENT = "#f37021"
    ACCENT_HOVER = "#ff8833"
    STATUS = {"green": "#5a9e6f", "yellow": "#d9a840", "red": "#e85a4a", "gray": "#6a5e52"}
    _B = "#444444"

try:
    from PySide6.QtCore import Qt, QTimer, Signal, QObject, QThread
    from PySide6.QtWidgets import (
        QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
        QPushButton, QScrollArea, QVBoxLayout, QWidget, QCheckBox,
        QSizePolicy, QFrame, QApplication,
    )

    _HAS_PYQT = True
except ImportError:
    _HAS_PYQT = False
    QDialog = QWidget = QFrame = QScrollArea = QLabel = QLineEdit = object
    QPushButton = QCheckBox = QApplication = QSizePolicy = object
    QHBoxLayout = QVBoxLayout = QMessageBox = QTimer = object
    Qt = type("_QtStub", (), {})
    Signal = object
    QObject = object
    QThread = object






WSL_DISTRO = os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu"
GATEWAY_PORT = 18789
try:
    from ui.i18n import tr
except ImportError:
    tr = lambda key, **fmt: key

_LOCALDATA = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia")
_MANIFEST_PATH = os.path.join(_proj, "kernel", "large", "mani.json")


_CARD_BG      = BG["deep"]
_CARD_HEADER  = BG["card"]
_TEXT_PRIMARY = TEXT["primary"]
_TEXT_SEC     = TEXT["secondary"]
_TEXT_HINT    = TEXT["hint"]
_BORDER       = _B
_GREEN        = STATUS["green"]
_YELLOW       = STATUS["yellow"]
_RED          = STATUS["red"]
_GRAY         = STATUS["gray"]

EXCLUDE_PROVIDER = {"azure-speech", "claude-max-api-proxy"}



_BAR_STYLE_CLOSED = (
    f"QFrame {{ background: {_CARD_HEADER}; border: 1px solid {_BORDER};\n"
    f"          border-radius: 6px; }}\n"
    f"QFrame:hover {{ background: {BG['hover']}; }}"
)
_BAR_STYLE_OPEN = (
    f"QFrame {{ background: {BG['hover']}; border: 1px solid {ACCENT};\n"
    f"          border-radius: 6px 6px 0 0; border-bottom: none; }}"
)
_ARROW_STYLE_OPEN = f"color:{ACCENT};font-size:12px;background:transparent;"
_ARROW_STYLE_CLOSED = f"color:{_TEXT_HINT};font-size:12px;background:transparent;"






# L113-118：_sh_single_quote — 把值包成 shell 单引号字面量（内部单引号按 '\'' 转义）。
def _sh_single_quote(value: str) -> str:
    r"""把值包成 shell 单引号字面量（内部单引号按 '\'' 转义）。"""



    return "'" + value.replace("'", "'\\''") + "'"


# L122-138：_run_wsl — 在 WSL 里同步跑一条命令，返回 (rc, stdout, stderr)。
def _run_wsl(cmd: str) -> tuple[int, str, str]:
    r"""在 WSL 里同步跑一条命令，返回 (rc, stdout, stderr)。"""




    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True,
                           encoding="utf-8", errors="replace",
                           creationflags=subprocess.CREATE_NO_WINDOW,
                           startupinfo=si)
        return r.returncode, (r.stdout or ""), (r.stderr or "")
    except Exception:
        return -1, "", "error"


# L142-145：_to_wsl_path — _to_wsl_path
def _to_wsl_path(win: str) -> str:
    if len(win) > 1 and win[1] == ":":
        return f"/mnt/{win[0].lower()}{win[2:].replace(chr(92), '/')}"
    return win.replace("\\", "/")


# L149-157：_read_wsl_config — _read_wsl_config
def _read_wsl_config() -> dict[str, Any]:
    rc, out, _ = _run_wsl(
        f'wsl -d {WSL_DISTRO} -u root -- bash -c "cat ~/.openclaw/openclaw.json 2>/dev/null"')
    if rc == 0 and out.strip():
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            pass
    return {}


# L161-164：_cli_usage — 从 mani 的 clis 查「这个动作该调哪个命令」。查不到返回空串，不猜。
def _cli_usage(cli_id: str, action: str, option: str = "") -> str:
    r"""从 mani 的 clis 查「这个动作该调哪个命令」。查不到返回空串，不猜。"""
    from main.main import cli_usage
    return cli_usage(cli_id, action, option)


# L168-194：_onboard_provider — 官方 provider 通过 openclaw onboard --auth-choice <value> 完整配置。
def _onboard_provider(pid: str, key: str, auth_choice: str = "") -> tuple[bool, str]:
    r"""官方 provider 通过 openclaw onboard --auth-choice <value> 完整配置。"""







    onboard_cmd = _cli_usage("onboard", "") or "openclaw onboard"
    if not auth_choice:
        return (False, tr("ui.ppanel.method_not_declared"))


    rc, out, err = _run_wsl(
        f'wsl -d {WSL_DISTRO} -u root -- bash -c '
        f'"export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; '
        f'{onboard_cmd} --non-interactive --mode local '
        f'--auth-choice {auth_choice} --{auth_choice} {_sh_single_quote(key)} --skip-health --accept-risk 2>&1"'
    )
    msg = (out + err)[:800]
    try:
        from main.main import stk
        stk("provider_panel.onboard", f"{pid} rc={rc} msg={msg[:120]!r}", ok=rc == 0)
    except Exception:
        pass
    return (rc == 0, msg)


# L198-238：_install_provider_plugin — 装 official external 的 provider 插件（@openclaw/xxx-provider）。
def _install_provider_plugin(package: str) -> tuple[bool, str]:
    r"""装 official external 的 provider 插件（@openclaw/xxx-provider）。"""




    if not package:
        return (True, tr("ui.ppanel.bundled_no_plugin"))
    plugins_install = _cli_usage("plugins", "install")
    plugins_list = _cli_usage("plugins", "list")
    if not plugins_install or not plugins_list:
        return (False, tr("ui.ppanel.method_not_declared"))


    plugin_id = package.split("/")[-1]
    _rc, _out, _err = _run_wsl(
        f'wsl -d {WSL_DISTRO} -u root -- bash -c '
        f'"export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; '
        f'{plugins_list} 2>&1"'
    )
    if _rc == 0 and plugin_id in (_out + _err).lower():
        return (True, tr("ui.ppanel.bundled_no_plugin"))

    rc, out, err = _run_wsl(
        f'wsl -d {WSL_DISTRO} -u root -- bash -c '
        f'"export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; '
        f'{plugins_install} {package} --accept-capabilities 2>&1"'
    )
    ok = rc == 0 or "already" in (out + err).lower()
    msg = (out + err).strip()[:200]
    try:
        from main.main import stk
        stk("provider_panel.install_plugin", f"{package} rc={rc} ok={ok}", ok=ok)
    except Exception:
        pass





    return (ok, msg)


# L242-274：_write_wsl_provider_key — 写入 provider key，分两条路径：
def _write_wsl_provider_key(pid: str, key: str, package: str = "", auth_choice: str = "") -> tuple[bool, str]:
    r"""写入 provider key，分两条路径："""






    if package:

        _install_provider_plugin(package)
        return _onboard_provider(pid, key, auth_choice)
    import json as _json
    payload = _json.dumps({"apiKey": key}, ensure_ascii=False)
    config_set = _cli_usage("config", "set")
    if not config_set:
        return (False, tr("ui.ppanel.method_not_declared"))


    b64 = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    rc, out, err = _run_wsl(
        f'wsl -d {WSL_DISTRO} -u root -- bash -c '
        f'"export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; '
        f'echo {b64} | base64 -d > /tmp/oc_provider_payload.json; '
        f'{config_set} models.providers.{pid} \\"$(cat /tmp/oc_provider_payload.json)\\" --strict-json --merge 2>&1; rm -f /tmp/oc_provider_payload.json"'
    )
    ok = rc == 0
    try:
        from main.main import stk
        stk("provider_panel.config_set", f"{pid} rc={rc} err={(out+err)[:120]!r}", ok=ok)
    except Exception:
        pass
    return (ok, "ok" if ok else (out + err).strip()[:200])


# L278-302：_sync_token_to_local — 把新 token 写入本地缓存 gateway_config.json（供 main/main.py 读取）。
def _sync_token_to_local(token: str) -> None:
    r"""把新 token 写入本地缓存 gateway_config.json（供 main/main.py 读取）。"""





    try:
        os.makedirs(_LOCALDATA, exist_ok=True)
        cfg = os.path.join(_LOCALDATA, "gateway_config.json")

        data = {}
        try:
            with open(cfg, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            if not isinstance(data, dict):
                data = {}
        except (OSError, json.JSONDecodeError):
            data = {}
        data["url"] = f"http://127.0.0.1:{GATEWAY_PORT}"
        data["token"] = token
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


# L306-322：_sync_provider_key_to_db — 把 provider API key 备份到 baseline_pool.db credentials 表。
def _sync_provider_key_to_db(pid: str, key: str) -> None:
    r"""把 provider API key 备份到 baseline_pool.db credentials 表。"""




    try:
        from kernel.scout import setCredential, ensureRecoveryTables
        ensureRecoveryTables()
        setCredential(f"{pid}_api_key", key, pid)
    except Exception as e:

        try:
            from main.main import stk
            stk("provider_panel.db_sync", f"{pid} setCredential failed: {type(e).__name__}: {e}", ok=False)
        except Exception:
            pass


# L326-328：_get_gateway_token — _get_gateway_token
def _get_gateway_token() -> str:
    cfg = _read_wsl_config()
    return (cfg.get("gateway") or {}).get("auth", {}).get("token", "")


# L332-364：_fetch_provider_models — 通过 openclaw models list --provider <id> --json 获取 provider 的真实模型列表。
def _fetch_provider_models(pid: str) -> list[dict[str, Any]]:
    r"""通过 openclaw models list --provider <id> --json 获取 provider 的真实模型列表。"""




    models_list = _cli_usage("models", "list")
    if not models_list:
        return []

    rc, out, err = _run_wsl(
        f'wsl -d {WSL_DISTRO} -u root -- bash -c '
        f'"export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; '
        f'{models_list} --provider {pid} --json 2>/dev/null"'
    )
    if rc != 0 or not out.strip():
        return []
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError:

        try:
            from main.main import stk
            stk("provider_panel.models_parse",
                f"{pid} rc={rc} out={out[:120]!r} err={err[:120]!r}", ok=False)
        except Exception:
            pass
        return []
    if isinstance(parsed, dict):
        return parsed.get("models", []) or parsed.get("rows", []) or parsed.get("entries", [])
    if isinstance(parsed, list):
        return parsed
    return []


# L368-370：_model_short_name — 从 'deepseek/deepseek-v4-flash' 取 'deepseek-v4-flash'。
def _model_short_name(ref: str) -> str:
    r"""从 'deepseek/deepseek-v4-flash' 取 'deepseek-v4-flash'。"""
    return ref.rsplit("/", 1)[-1] if "/" in ref else ref






# L378-384：_stake_load — 检测桩：provider 控制台后台加载的进度打点。
def _stake_load(tag: str, msg: str, ok: bool = True) -> None:
    r"""检测桩：provider 控制台后台加载的进度打点。"""
    try:
        from main.main import stk
        stk(f"provider_panel.load.{tag}", msg, ok=ok)
    except Exception:
        pass


# L388-398：_ProviderLoadWorker — 后台线程加载 provider 列表，加载完发 loaded 信号回主线程。
class _ProviderLoadWorker(QObject):
    r"""后台线程加载 provider 列表，加载完发 loaded 信号回主线程。"""
    loaded = Signal(list)

    def run(self) -> None:
        try:
            providers = _load_provider_list()
            self.loaded.emit(providers)
        except Exception as _e:
            _stake_load("failed", f"{type(_e).__name__}: {_e}", ok=False)
            self.loaded.emit([])


# L402-517：_load_provider_list — 从 mani.json 加载 provider 列表，取名称/模型/环境变量等信息。
def _load_provider_list() -> list[dict[str, Any]]:
    r"""从 mani.json 加载 provider 列表，取名称/模型/环境变量等信息。"""







    wsl_cfg = _read_wsl_config()
    cfg_providers = wsl_cfg.get("models", {}).get("providers", {})





    dynamic_models: dict[str, list[dict[str, Any]]] = {}
    keyed = [
        (pid, pcfg) for pid, pcfg in cfg_providers.items()
        if isinstance(pcfg, dict) and (pcfg.get("apiKey") or pcfg.get("api_key"))
    ]
    for pid, _pcfg in keyed:
        try:
            _dm = _fetch_provider_models(pid)
        except Exception:
            _dm = []
        if _dm:
            dynamic_models[pid] = _dm
    try:
        with open(_MANIFEST_PATH, "r", encoding="utf-8") as f:
            mf = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []

    entries = mf.get("entries", {})
    raw: list[dict] = []
    if isinstance(entries, dict):
        raw = entries.get("providers", [])
    elif isinstance(entries, list):
        raw = entries

    if not isinstance(raw, list):
        return []

    result: list[dict[str, Any]] = []
    for p in raw:
        pid = (p.get("id") or "").strip()
        if not pid or pid in EXCLUDE_PROVIDER:
            continue
        auth = p.get("auth") or {}
        kind = auth.get("kind", "")
        if kind not in ("api_key", "unknown"):


            _stake_load("skip", f"{pid} auth.kind={kind!r} 不属填 key 流程，跳过", ok=True)
            continue


        env_vars = auth.get("env_vars") or []
        env = env_vars[0].get("var", "") if env_vars else ""


        dm = dynamic_models.get(pid) or []
        static_models = p.get("models") or []
        models = dm if dm else static_models


        pcfg = cfg_providers.get(pid, {})
        pcfg = pcfg if isinstance(pcfg, dict) else {}
        has_key = bool((pcfg.get("apiKey") or pcfg.get("api_key") or "").strip())


        model_tags: list[str] = []
        for m in models:
            tag = m.get("display_name") or m.get("name") or m.get("ref") or m.get("id", "")
            if not tag:
                continue
            short = _model_short_name(tag)

            if m.get("status") != "deprecated":
                model_tags.append(short)

        if len(model_tags) > 6:
            model_tags = model_tags[:6]
            model_tags.append(f"+{len(models) - 6}")
        enabled = pcfg.get("enabled", True) if has_key else None


        if has_key:
            if enabled is not False:
                status = "green"
            else:
                status = "gray"
        elif kind == "api_key":
            status = "yellow"
        else:
            status = "gray"

        result.append({
            "id": pid,
            "name": p.get("section_name", pid.title()),
            "env": env,
            "base_url": (p.get("default_base_url") or "").strip("`").strip(),
            "default_model": p.get("default_model", ""),
            "model_tags": model_tags,
            "models": models,
            "status": status,
            "has_key": has_key,
            "enabled": enabled if enabled is not False else False,
            "kind": kind,
            "package": ((p.get("plugin") or {}).get("package") or "").strip(),
            "auth_choice": p.get("auth_choice") or "",
        })


    return result






# L525-796：_ProviderRow — 单行 provider：折叠展示名称+模型标签+状态灯；展开展示 API key 输入+模型列表。
class _ProviderRow(QWidget):
    r"""单行 provider：折叠展示名称+模型标签+状态灯；展开展示 API key 输入+模型列表。"""

    provider_saved = Signal(str)




    restart_needed = Signal()

    def __init__(self, info: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._info = info
        self._expanded = False
        self._build()

    def _build(self) -> None:
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)


        self._bar = QFrame()
        self._bar.setCursor(Qt.CursorShape.PointingHandCursor)
        self._bar.setFixedHeight(44)
        self._bar.setStyleSheet(_BAR_STYLE_CLOSED)
        bar_lo = QHBoxLayout(self._bar)
        bar_lo.setContentsMargins(10, 4, 10, 4)
        bar_lo.setSpacing(8)


        nm = QLabel(self._info["name"])
        nm.setStyleSheet(f"color:{_TEXT_PRIMARY};font-size:13px;font-weight:600;background:transparent;")
        nm.setMaximumWidth(140)
        nm.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bar_lo.addWidget(nm)


        tags_text = ", ".join(self._info["model_tags"][:2]) if self._info["model_tags"] else ""
        if len(self._info["model_tags"]) > 2:
            tags_text += f" +{len(self._info['model_tags']) - 2}"
        self._tags_label = QLabel(tags_text)
        self._tags_label.setStyleSheet(f"color:{_TEXT_HINT};font-size:11px;background:transparent;")
        self._tags_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._tags_label.setWordWrap(False)
        bar_lo.addWidget(self._tags_label, stretch=1)


        dot_color = {"green": _GREEN, "yellow": _YELLOW, "gray": _GRAY}.get(self._info["status"], _GRAY)
        self._dot = QLabel("●")
        self._dot.setFixedWidth(18)
        self._dot.setStyleSheet(f"color:{dot_color};font-size:14px;background:transparent;")
        self._dot.setToolTip({"green": tr("ui.ppanel.configured"), "yellow": tr("ui.ppanel.need_key"), "gray": tr("ui.ppanel.disabled")}.get(self._info["status"], ""))
        bar_lo.addWidget(self._dot)


        self._arrow = QLabel("▸")
        self._arrow.setFixedWidth(14)
        self._arrow.setStyleSheet(_ARROW_STYLE_CLOSED)
        bar_lo.addWidget(self._arrow)

        self._outer.addWidget(self._bar)
        self._bar.mousePressEvent = self._on_bar_click


        self._expand_widget = QWidget()
        self._expand_widget.setVisible(False)
        self._expand_widget.setStyleSheet(f"""
            QWidget {{ background: {BG['deep']}; border: 1px solid {_BORDER};
                      border-top: none; border-radius: 0 0 6px 6px; }}
        """)
        self._build_expanded()
        self._outer.addWidget(self._expand_widget)

    def _build_expanded(self) -> None:
        lo = QVBoxLayout(self._expand_widget)
        lo.setContentsMargins(14, 8, 14, 10)
        lo.setSpacing(6)

        info = self._info


        if info["models"]:
            model_label = QLabel(tr("ui.ppanel.available_models"))
            model_label.setStyleSheet(f"color:{_TEXT_PRIMARY};font-size:12px;font-weight:600;background:transparent;")
            lo.addWidget(model_label)

            model_container = QWidget()
            model_container.setStyleSheet("background:transparent;")
            mc_lo = QHBoxLayout(model_container)
            mc_lo.setContentsMargins(0, 0, 0, 0)
            mc_lo.setSpacing(6)

            shown = 0
            for m in info["models"]:
                if shown >= 8:
                    mc_lo.addStretch()
                    more = QLabel(tr("ui.ppanel.more", n=len(info['models']) - shown))
                    more.setStyleSheet(f"color:{_TEXT_HINT};font-size:10px;background:transparent;")
                    mc_lo.addWidget(more)
                    break
                ref = m.get("ref", "")
                short = ref.rsplit("/", 1)[-1] if "/" in ref else ref
                tag = QLabel(short)
                is_dep = m.get("status") == "deprecated"
                tag.setStyleSheet(
                    f"color:{_TEXT_HINT if is_dep else _TEXT_PRIMARY};"
                    f"font-size:10px;background:{BG['hover']};"
                    f"border-radius:4px;padding:2px 6px;")
                tag.setToolTip(f"{ref}\n{tr('ui.ppanel.deprecated') if is_dep else tr('ui.ppanel.available')}")
                mc_lo.addWidget(tag)
                shown += 1
            mc_lo.addStretch()
            lo.addWidget(model_container)


        sep = QLabel("")
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{_BORDER};")
        lo.addWidget(sep)


        cfg_row = QHBoxLayout()
        cfg_row.setSpacing(8)

        env_label = QLabel(info["env"] if info["env"] else "API Key")
        env_label.setStyleSheet(f"color:{_TEXT_HINT};font-size:10px;background:transparent;")
        env_label.setMaximumWidth(160)
        env_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        cfg_row.addWidget(env_label)

        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText(tr("ui.ppanel.enter_key_save") if not info["has_key"]
                                           else tr("ui.ppanel.key_configured"))
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background:{BG['card']}; color:{_TEXT_PRIMARY};
                border:1px solid {_BORDER}; border-radius:6px;
                padding:5px 10px; font-size:11px;
            }}
            QLineEdit:focus {{ border-color:{ACCENT}; }}
        """)
        cfg_row.addWidget(self._key_input, stretch=1)


        self._enabled_cb = QCheckBox(tr("ui.ppanel.enable"))
        self._enabled_cb.setChecked(info.get("enabled", True) if info["has_key"] else False)
        self._enabled_cb.setEnabled(info["has_key"])
        self._enabled_cb.setStyleSheet(f"""
            QCheckBox {{ color:{_TEXT_PRIMARY}; font-size:11px; spacing:4px; background:transparent; }}
            QCheckBox::indicator {{ width:14px; height:14px; }}
        """)
        cfg_row.addWidget(self._enabled_cb)


        save_btn = QPushButton(tr("ui.btn.save"))
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setFixedSize(56, 28)
        save_btn.setStyleSheet(f"""
            QPushButton {{ background:{ACCENT}; color:#0a0a0a; border:none;
                           border-radius:5px; font-size:11px; font-weight:600; }}
            QPushButton:hover {{ background:{ACCENT_HOVER}; }}
            QPushButton:disabled {{ background:#444; color:#888; }}
        """)
        save_btn.clicked.connect(self._on_save)
        self._save_btn = save_btn
        cfg_row.addWidget(save_btn)


        self._status_lbl = QLabel("")
        self._status_lbl.setFixedWidth(18)
        self._status_lbl.setStyleSheet("background:transparent;font-size:12px;")
        cfg_row.addWidget(self._status_lbl)

        lo.addLayout(cfg_row)


        hint = QLabel(info["base_url"]) if info.get("base_url") else QLabel("")
        hint.setStyleSheet(f"color:{_TEXT_HINT};font-size:9px;background:transparent;padding:0 0 0 172px;")
        lo.addWidget(hint)

    def _on_bar_click(self, ev) -> None:
        self._expanded = not self._expanded


        self._expand_widget.setVisible(self._expanded)
        self._arrow.setText("▾" if self._expanded else "▸")
        self._arrow.setStyleSheet(_ARROW_STYLE_OPEN if self._expanded else _ARROW_STYLE_CLOSED)
        self._bar.setStyleSheet(_BAR_STYLE_OPEN if self._expanded else _BAR_STYLE_CLOSED)

    def _on_save(self) -> None:
        info = self._info
        key_val = self._key_input.text().strip()

        if not key_val and not info["has_key"]:
            return

        self._save_btn.setEnabled(False)
        self._save_btn.setText("...")
        self._status_lbl.setText("⟳")
        self._status_lbl.setStyleSheet(f"color:{_YELLOW};font-size:12px;background:transparent;")
        QApplication.processEvents()



        _sync_provider_key_to_db(info["id"], key_val)


        ok, msg = _write_wsl_provider_key(info["id"], key_val, info.get("package", ""), info.get("auth_choice", ""))
        if not ok:
            self._status_lbl.setText("!")
            self._status_lbl.setStyleSheet(f"color:{_YELLOW};font-size:12px;background:transparent;")
            self._status_lbl.setToolTip(tr("ui.ppanel.saved_local_sync_fail", msg=msg[:160]))
            self._save_btn.setEnabled(True)
            self._save_btn.setText(tr("ui.ppanel.retry_gw"))
            try:
                from main.main import stk
                stk("provider_panel.save", f"id={info['id']} msg={msg[:80]} db_ok=True gw_failed=True", ok=False)
            except Exception:
                pass
            return




        need_restart = bool((info.get("package") or "").strip())
        if need_restart:
            self._status_lbl.setToolTip(tr("ui.ppanel.restarting_gw"))
            QApplication.processEvents()


        new_token = _get_gateway_token()
        if new_token:
            _sync_token_to_local(new_token)


        self.provider_saved.emit(new_token or "")

        if need_restart:
            self.restart_needed.emit()


        self._status_lbl.setText("OK")
        self._status_lbl.setStyleSheet(f"color:{_GREEN};font-size:12px;background:transparent;")
        self._save_btn.setEnabled(True)
        self._save_btn.setText(tr("ui.btn.save"))


        enabled = self._enabled_cb.isChecked()
        if enabled:
            self._dot.setStyleSheet(f"color:{_GREEN};font-size:14px;background:transparent;")
            self._dot.setToolTip(tr("ui.ppanel.configured"))
        else:
            self._dot.setStyleSheet(f"color:{_GRAY};font-size:14px;background:transparent;")
            self._dot.setToolTip(tr("ui.ppanel.disabled"))

        try:
            from main.main import stk
            action = "save" if (key_val or info["has_key"]) else "disable"
            stk(
                "provider_panel." + action,
                f"id={info['id']} enabled={enabled} key={'present' if key_val else 'none'}",
            )
        except Exception:
            pass


        self._info["has_key"] = True
        self._info["enabled"] = enabled
        self._info["status"] = "green" if enabled else "gray"
        self._enabled_cb.setEnabled(True)






# L804-945：ProviderPanel — Provider 完整模式面板 — 客制区「Provider 控制台」入口。
class ProviderPanel(QDialog):
    r"""Provider 完整模式面板 — 客制区「Provider 控制台」入口。"""





    provider_saved = Signal(str)
    restart_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        if not _HAS_PYQT:
            raise RuntimeError("PySide6 not available")

        super().__init__(parent)
        self.setWindowTitle(tr("ui.ppanel.title"))
        from ui.theme import fitToScreen
        fitToScreen(self, 0.32, 0.58)

        self.setStyleSheet(f"""
            QDialog {{ background: {_CARD_BG}; color: {_TEXT_PRIMARY}; }}
            QLabel {{ background: transparent; }}
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{ background: transparent; width: 6px; margin: 0; }}
            QScrollBar::handle:vertical {{ background: #444; border-radius: 3px; min-height: 20px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

        self._rows: list[_ProviderRow] = []
        self._build()

    def _build(self) -> None:
        lo = QVBoxLayout(self)
        lo.setContentsMargins(16, 12, 16, 12)
        lo.setSpacing(10)


        title = QLabel(tr("ui.ppanel.title"))
        title.setStyleSheet(f"color:{_TEXT_PRIMARY};font-size:16px;font-weight:bold;")
        lo.addWidget(title)

        desc = QLabel(tr("ui.ppanel.desc"))
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color:{_TEXT_HINT};font-size:11px;line-height:1.5;")
        lo.addWidget(desc)


        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sc_content = QWidget()
        sc_content.setStyleSheet("background:transparent;")
        sc_lo = QVBoxLayout(sc_content)
        sc_lo.setContentsMargins(0, 0, 0, 0)
        sc_lo.setSpacing(6)


        self._loading_lbl = QLabel(tr("ui.ppanel.loading"))
        self._loading_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_lbl.setStyleSheet(f"color:{_TEXT_HINT};font-size:12px;padding:20px;")
        sc_lo.addWidget(self._loading_lbl)
        sc_lo.addStretch()
        sc.setWidget(sc_content)
        lo.addWidget(sc, stretch=1)


        bot = QHBoxLayout()
        bot.setContentsMargins(0, 4, 0, 0)

        self._summary = QLabel("")
        self._summary.setStyleSheet(f"color:{_TEXT_HINT};font-size:11px;")
        bot.addWidget(self._summary, alignment=Qt.AlignmentFlag.AlignVCenter)
        bot.addStretch()

        close_btn = QPushButton(tr("ui.btn.close"))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setFixedSize(72, 30)
        close_btn.setStyleSheet(f"""
            QPushButton {{ background:{ACCENT}; color:#0a0a0a; border:none;
                           border-radius:6px; font-size:12px; font-weight:600; }}
            QPushButton:hover {{ background:{ACCENT_HOVER}; }}
        """)
        close_btn.clicked.connect(self.accept)
        bot.addWidget(close_btn)
        lo.addLayout(bot)


        self._load_sc_lo = sc_lo
        self._start_load_providers(sc_lo)

    def _start_load_providers(self, sc_lo) -> None:
        r"""后台线程加载 provider 列表，加载完回主线程填充。"""




        self._load_thread = QThread(self)
        self._load_worker = _ProviderLoadWorker()
        self._load_worker.moveToThread(self._load_thread)
        self._load_thread.started.connect(self._load_worker.run)
        self._load_worker.loaded.connect(self._on_providers_loaded)
        self._load_thread.start()
        _stake_load("started", "thread started")

    def _on_providers_loaded(self, providers: list) -> None:
        r"""主线程回调：填充 provider 行（GUI 操作必须在主线程）。"""
        _stake_load("loaded", f"{len(providers)} providers")
        sc_lo = self._load_sc_lo


        holder = sc_lo.parentWidget()
        if holder is not None:
            holder.setUpdatesEnabled(False)
        try:
            for p in providers:
                row = _ProviderRow(p)
                self._rows.append(row)
                row.provider_saved.connect(self.provider_saved)
                row.restart_needed.connect(self.restart_requested.emit)
                sc_lo.insertWidget(sc_lo.count() - 1, row)

            if hasattr(self, "_loading_lbl"):
                self._loading_lbl.hide()
                self._loading_lbl.deleteLater()
        finally:
            if holder is not None:
                holder.setUpdatesEnabled(True)

        green_n = sum(1 for p in providers if p["status"] == "green")
        yellow_n = sum(1 for p in providers if p["status"] == "yellow")
        gray_n = sum(1 for p in providers if p["status"] == "gray")
        parts = []
        if green_n:
            parts.append(tr("ui.ppanel.summary_configured", n=green_n))
        if yellow_n:
            parts.append(tr("ui.ppanel.summary_pending", n=yellow_n))
        if gray_n:
            parts.append(tr("ui.ppanel.summary_disabled", n=gray_n))
        self._summary.setText("  ".join(parts) if parts else tr("ui.ppanel.no_provider"))


        self._load_thread.quit()
