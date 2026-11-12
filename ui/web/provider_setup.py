# L1-1143：Rheia 模型配置中间层
r"""Rheia 模型配置中间层"""













from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from typing import Any


_proj = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _proj not in sys.path:
    sys.path.insert(0, _proj)

try:
    from ui.i18n import tr
except ImportError:
    tr = lambda key, **fmt: key

_MANI_PATH = os.path.join(_proj, "kernel", "large", "mani.json")


_LOAD_ERROR = ""


# L43-46：_cli_usage — 从 mani 的 clis 查「这个动作该调哪个命令」。查不到返回空串，不猜。
def _cli_usage(cli_id: str, action: str, option: str = "") -> str:
    r"""从 mani 的 clis 查「这个动作该调哪个命令」。查不到返回空串，不猜。"""
    from main.main import cli_usage
    return cli_usage(cli_id, action, option)


from PySide6.QtCore import Qt, QThread, QTimer, QRectF, Signal
from PySide6.QtGui import QColor, QPainterPath, QRegion
from PySide6.QtWidgets import (
    QApplication, QComboBox, QGraphicsDropShadowEffect,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)





WSL_DISTRO = os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu"
GATEWAY_PORT = 18789
_LOCALDATA = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia")


from ui.theme import fit_button_text, BG, TEXT, ACCENT, ACCENT_HOVER, STATUS, BORDER as _THEME_BORDER

CARD_WIDTH = 540
CARD_HEIGHT = 580
CARD_RADIUS = 14.0
CARD_BG = BG["deep"]
BRAND_COLOR = "#ffffff"
SUB_COLOR = TEXT["secondary"]
ACCENT = ACCENT
ACCENT_HOVER = ACCENT_HOVER
BG_INPUT = BG["card"]
TEXT_PRIMARY = TEXT["primary"]
TEXT_HINT = TEXT["hint"]
BORDER = _THEME_BORDER
GREEN = STATUS["green"]
RED = STATUS["red"]

EXCLUDE = {"azure-speech"}









# L94-99：_sh_single_quote — 把值包成 shell 单引号字面量（内部单引号按 '\'' 转义）。
def _sh_single_quote(value: str) -> str:
    r"""把值包成 shell 单引号字面量（内部单引号按 '\'' 转义）。"""



    return "'" + value.replace("'", "'\\''") + "'"


# L103-119：_run_wsl — 在 WSL 里同步跑一条命令，返回 (rc, stdout, stderr)。
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


# L123-131：_read_wsl_config — _read_wsl_config
def _read_wsl_config() -> dict[str, Any]:
    rc, out, _ = _run_wsl(
        f'wsl -d {WSL_DISTRO} -u root -- bash -c "cat ~/.openclaw/openclaw.json 2>/dev/null"')
    if rc == 0 and out.strip():
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            pass
    return {}


# L135-162：_onboard_provider — 官方 provider 通过 openclaw onboard --auth-choice <value> 完整配置。
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
        stk("provider_setup.onboard", f"{pid} rc={rc} msg={msg[:120]!r}", ok=rc == 0)
    except Exception:
        pass
    return (rc == 0, msg)


# L166-216：_install_provider_plugin — 装 official external 的 provider 插件（@openclaw/xxx-provider）。
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
        try:
            from main.main import stk
            stk("provider_setup.install_plugin", f"{package} already installed, skip", ok=True)
        except Exception:
            pass
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
        stk("provider_setup.install_plugin", f"{package} rc={rc} ok={ok}", ok=ok)
    except Exception:
        pass








    return (ok, msg)


# L220-230：_cli_models_json — 官方 models list --json 原样输出（拿不到返回空串，不猜）。
def _cli_models_json() -> str:
    r"""官方 models list --json 原样输出（拿不到返回空串，不猜）。"""
    models_list = _cli_usage("models", "list")
    if not models_list:
        return ""
    rc, out, _err = _run_wsl(
        f'wsl -d {WSL_DISTRO} -u root -- bash -c '
        f'"export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; '
        f'{models_list} --json 2>/dev/null"'
    )
    return out if rc == 0 else ""


# L234-255：_provider_available — 热重载判据：models list --json 里属于该 provider 的模型出现 available=true。
def _provider_available(pid: str) -> bool:
    r"""热重载判据：models list --json 里属于该 provider 的模型出现 available=true。"""
    raw = _cli_models_json()
    if not raw.strip():
        return False
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return False
    rows = data.get("models") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("available") is not True:
            continue
        prov = str(row.get("provider") or "")
        ref = str(row.get("id") or row.get("model") or row.get("ref") or "")
        if prov == pid or ref.startswith(pid + "/") or pid in ref:
            return True
    return False


# L259-261：_wait_provider_available — 写完后读一眼官方表：config set 的回执就是热加载信号，不再按秒重试。
def _wait_provider_available(pid: str) -> bool:
    r"""写完后读一眼官方表：config set 的回执就是热加载信号，不再按秒重试。"""
    return _provider_available(pid)


# L265-289：_write_key_narrow — 窄写：只动 models.providers.<id>，一个 gateway. 字段都不碰。
def _write_key_narrow(pid: str, key: str) -> tuple[bool, str]:
    r"""窄写：只动 models.providers.<id>，一个 gateway. 字段都不碰。"""





    config_set = _cli_usage("config", "set")
    if not config_set:
        return (False, tr("ui.ppanel.method_not_declared"))
    payload = json.dumps({"apiKey": key}, ensure_ascii=False)
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
        stk("provider_setup.narrow_set", f"{pid} rc={rc} err={(out + err)[:120]!r}", ok=ok)
    except Exception:
        pass
    return (ok, "ok" if ok else (out + err).strip()[:200])


# L293-321：_write_provider_overlay — 窄写 models.providers.<id>，不碰任何 gateway.。
def _write_provider_overlay(pid: str, key: str, base_url: str = "", model_id: str = "") -> tuple[bool, str]:
    r"""窄写 models.providers.<id>，不碰任何 gateway.。"""




    config_set = _cli_usage("config", "set")
    if not config_set:
        return (False, tr("ui.ppanel.method_not_declared"))
    body: dict[str, Any] = {"apiKey": key}
    if base_url:
        body["baseUrl"] = base_url
    if model_id:
        body["models"] = [{"id": model_id, "name": model_id}]
    b64 = base64.b64encode(json.dumps(body, ensure_ascii=False).encode("utf-8")).decode("ascii")
    rc, out, err = _run_wsl(
        f'wsl -d {WSL_DISTRO} -u root -- bash -c '
        f'"export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; '
        f'echo {b64} | base64 -d > /tmp/oc_provider_payload.json; '
        f'{config_set} models.providers.{pid} \\"$(cat /tmp/oc_provider_payload.json)\\" --strict-json --merge 2>&1; '
        f'rm -f /tmp/oc_provider_payload.json"'
    )
    ok = rc == 0
    try:
        from main.main import stk
        stk("provider_setup.overlay", f"{pid} rc={rc} keys={sorted(body)} err={(out + err)[:120]!r}", ok=ok)
    except Exception:
        pass
    return (ok, "ok" if ok else (out + err).strip()[:300])


# L325-359：_write_wsl_provider_key — 写入 provider key，分两条路径：
def _write_wsl_provider_key(pid: str, key: str, package: str = "", auth_choice: str = "",
                            base_url: str = "", model_id: str = "") -> tuple[bool, str]:
    r"""写入 provider key，分两条路径："""






    if package:

        _install_provider_plugin(package)






        ok0, msg0 = _write_provider_overlay(pid, key)
        if ok0 and _wait_provider_available(pid):
            return (True, msg0)
        try:
            from main.main import stk
            stk("provider_setup.narrow",
                f"{pid} 窄写后未见 available ⇒ 走 onboard 兜底（会写 gateway.*）", ok=False)
        except Exception:
            pass
        return _onboard_provider(pid, key, auth_choice)

    if not base_url or not model_id:
        return (False, "自定义 provider 必须同时填 baseUrl 与模型 id（官方 schema 要求，缺一个会被校验拒绝）")
    ok, msg = _write_provider_overlay(pid, key, base_url=base_url, model_id=model_id)
    if ok:
        _wait_provider_available(pid)
    return (ok, msg)


# L363-365：_get_gateway_token — _get_gateway_token
def _get_gateway_token() -> str:
    cfg = _read_wsl_config()
    return (cfg.get("gateway") or {}).get("auth", {}).get("token", "")


# L369-394：_sync_token_to_local — 把新 token 写入本地缓存 gateway_config.json（供 main/main.py 读取）。
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


# L398-410：_sync_provider_key_to_db — 把 provider API key 备份到 baseline_pool.db credentials 表。
def _sync_provider_key_to_db(pid: str, key: str) -> None:
    r"""把 provider API key 备份到 baseline_pool.db credentials 表。"""
    try:
        from kernel.scout import setCredential, ensureRecoveryTables
        ensureRecoveryTables()
        setCredential(f"{pid}_api_key", key, pid)
    except Exception as e:

        try:
            from main.main import stk
            stk("provider_setup.db_sync", f"{pid} setCredential failed: {type(e).__name__}: {e}", ok=False)
        except Exception:
            pass






# L418-475：_load_providers — 从 large.run() 产出的 render 加载 provider 列表。
def _load_providers() -> list[dict[str, Any]]:
    r"""从 large.run() 产出的 render 加载 provider 列表。"""





    global _LOAD_ERROR
    try:
        import kernel.large as large
        result = large.run()
        instances = result.get("instances") or []
        if isinstance(instances, dict):
            instances = list(instances.values())
    except Exception as _e:
        _LOAD_ERROR = f"{type(_e).__name__}: {_e}"
        try:
            from main.main import stk as _stk
            _stk("provider_setup.load_providers", f"large.run() 失败: {_e}", ok=False)
        except Exception:
            pass
        return []
    _LOAD_ERROR = ""

    out: list[dict[str, Any]] = []
    for inst in instances:
        if not isinstance(inst, dict):
            continue
        pid = inst.get("id") or inst.get("provider_id") or ""
        if not pid or pid in EXCLUDE:
            continue



        if inst.get("auth_kind", "") != "api_key":
            continue
        render = inst.get("render") or {}
        fields = render.get("fields") or []
        if not fields:
            continue

        plugin = inst.get("plugin") or {}
        auth = inst.get("auth") or {}
        env_vars = auth.get("env_vars") or []
        env = env_vars[0].get("var", "") if env_vars else ""
        out.append({
            "id": pid,
            "name": inst.get("display_name") or inst.get("section_name") or pid.title(),
            "env": env,
            "package": (plugin.get("package") or "").strip(),
            "fields": fields,
            "transport": inst.get("transport", ""),
            "config_shape": inst.get("config_shape", ""),
            "auth_choice": inst.get("auth_choice") or "",
        })


    return out


# L479-493：_ProvidersLoadWorker — 后台拉 provider 清单（large.run() 实测十几秒，不能在 GUI 线程里跑）。
class _ProvidersLoadWorker(QThread):
    r"""后台拉 provider 清单（large.run() 实测十几秒，不能在 GUI 线程里跑）。"""

    loaded = Signal(list)

    def run(self) -> None:
        try:
            self.loaded.emit(_load_providers())
        except Exception as _e:
            try:
                from main.main import stk as _stk
                _stk("provider_setup.load_worker", f"failed: {_e}", ok=False)
            except Exception:
                pass
            self.loaded.emit([])






# L501-700：_ProviderRow — 单行 provider 条目：控件从 render.fields 长出来（不硬画）。
class _ProviderRow(QWidget):
    r"""单行 provider 条目：控件从 render.fields 长出来（不硬画）。"""










    save_requested = Signal()

    def __init__(self, info: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pid = info["id"]
        self._env = info.get("env", "")
        self._package = info.get("package", "")
        self._auth_choice = info.get("auth_choice", "")
        self._fields = info.get("fields") or []
        self._key_input: QLineEdit | None = None
        self._field_inputs: dict[str, QWidget] = {}
        self._save_row_btn: QPushButton | None = None
        self._dot = None
        self.setStyleSheet("_ProviderRow { background: transparent; }")


        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 6, 12, 6)
        outer.setSpacing(6)


        head = QHBoxLayout()
        head.setSpacing(6)
        nm = QLabel(info["name"])
        nm.setStyleSheet(f"color:{TEXT_PRIMARY};font-size:12px;font-weight:600;background:transparent;")
        head.addWidget(nm)
        if self._env:
            ev = QLabel(self._env)
            ev.setStyleSheet(f"color:{TEXT_HINT};font-size:10px;background:transparent;")
            head.addWidget(ev)
        head.addStretch()
        self._dot = QLabel("")
        self._dot.setFixedWidth(18)
        self._dot.setStyleSheet("background:transparent;font-size:12px;")
        head.addWidget(self._dot)
        outer.addLayout(head)


        field_box = QVBoxLayout()
        field_box.setSpacing(6)
        action_fields = []
        for f in self._fields:
            name = f.get("name", "")
            widget_type = f.get("widget", "QLineEdit")

            if name in ("provider_name", "provider_icon", "status"):
                continue
            w = self._make_widget(name, widget_type, f)
            if w is not None:
                self._field_inputs[name] = w
                action_fields.append(w)


        row_line = QHBoxLayout()
        row_line.setSpacing(8)
        for w in action_fields:
            row_line.addWidget(w, alignment=Qt.AlignmentFlag.AlignLeft)
        row_line.addStretch()
        self._save_row_btn = QPushButton(tr("ui.provider.save_start"))
        self._save_row_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_row_btn.setFixedHeight(32)
        fit_button_text(self._save_row_btn)
        self._save_row_btn.setMinimumWidth(96)
        self._save_row_btn.setStyleSheet(
            f"QPushButton {{ background:{ACCENT}; color:#0a0a0a; border:none;"
            f"border-radius:6px; font-size:11px; font-weight:600; padding:0 12px; }}"
            f"QPushButton:hover {{ background:{ACCENT_HOVER}; }}")
        self._save_row_btn.clicked.connect(self.save_requested.emit)
        row_line.addWidget(self._save_row_btn, alignment=Qt.AlignmentFlag.AlignRight)
        outer.addLayout(row_line)

    def _make_widget(self, name: str, widget_type: str, f: dict) -> QWidget | None:
        hints = f.get("hints") or {}
        label = f.get("label") or name
        behavior = f.get("behavior", "")
        if widget_type == "QLineEdit":
            w = QLineEdit()
            echo = hints.get("echo_mode", "").lower()
            if echo == "password":
                w.setEchoMode(QLineEdit.EchoMode.Password)
            w.setPlaceholderText(hints.get("placeholder", label))
            w.setStyleSheet(f"""
                QLineEdit {{
                    background:{BG_INPUT}; color:{TEXT_PRIMARY};
                    border:1px solid {BORDER}; border-radius:6px;
                    padding:5px 10px; font-size:11px;
                }}
                QLineEdit:focus {{ border-color:{ACCENT}; }}
            """)

            if behavior == "credential_input":
                self._key_input = w
                w.setFixedWidth(220)
            return w
        if widget_type == "QPushButton":
            w = QPushButton(hints.get("label", label))
            w.setCursor(Qt.CursorShape.PointingHandCursor)
            w.setFixedHeight(32)
            w.setStyleSheet(f"""
                QPushButton {{ background:transparent; color:{TEXT_HINT}; border:1px solid {BORDER};
                               border-radius:6px; font-size:11px; padding:0 12px; }}
                QPushButton:hover {{ background:{BG_INPUT}; color:{TEXT_PRIMARY}; }}
            """)


            return w
        if widget_type == "QComboBox":
            w = QComboBox()
            w.setStyleSheet(f"""
                QComboBox {{ background:{BG_INPUT}; color:{TEXT_PRIMARY};
                             border:1px solid {BORDER}; border-radius:6px;
                             padding:5px 10px; font-size:11px; }}
            """)
            return w
        if widget_type == "QSpinBox":
            w = QSpinBox()
            if "min" in hints:
                w.setMinimum(int(hints["min"]))
            if "max" in hints:
                w.setMaximum(int(hints["max"]))
            return w

        return None

    def key_value(self) -> str:
        if self._key_input is None:
            return ""
        return self._key_input.text().strip()

    def field_value(self, *names: str) -> str:
        r"""按字段名取输入值（QLineEdit / QComboBox 都认），取不到返回空串。"""
        for n in names:
            w = self._field_inputs.get(n)
            if w is None:
                continue
            if isinstance(w, QLineEdit):
                v = w.text().strip()
            elif isinstance(w, QComboBox):
                v = (w.currentText() or "").strip()
            else:
                continue
            if v:
                return v
        return ""

    def mark_saved(self) -> None:
        if self._dot is not None:
            self._dot.setText("OK")
            self._dot.setStyleSheet(f"color:{GREEN};font-size:12px;background:transparent;")
        if self._key_input is not None:
            self._key_input.setEnabled(False)
            self._key_input.setStyleSheet(f"""
                QLineEdit {{
                    background:#1a2a1a; color:{GREEN};
                    border:1px solid #2a4a2a; border-radius:6px;
                    padding:5px 10px; font-size:11px;
                }}
            """)
        if self._save_row_btn is not None:
            self._save_row_btn.setEnabled(False)
            self._save_row_btn.setText(tr("ui.provider.ready"))
            self._save_row_btn.setStyleSheet(
                f"QPushButton {{ background:{GREEN}; color:#0a0a0a; border:none;"
                f"border-radius:6px; font-size:11px; font-weight:600; padding:0 12px; }}")

    def set_saving(self) -> None:
        r"""进入配置中状态：禁用本行按钮并提示。"""
        if self._save_row_btn is not None:
            self._save_row_btn.setEnabled(False)
            self._save_row_btn.setText(tr("ui.provider.configuring"))

    def reset_save_btn(self) -> None:
        r"""恢复本行按钮到可点状态。"""
        if self._save_row_btn is not None:
            self._save_row_btn.setEnabled(True)
            self._save_row_btn.setText(tr("ui.provider.save_start"))
            self._save_row_btn.setStyleSheet(
                f"QPushButton {{ background:{ACCENT}; color:#0a0a0a; border:none;"
                f"border-radius:6px; font-size:11px; font-weight:600; padding:0 12px; }}"
                f"QPushButton:hover {{ background:{ACCENT_HOVER}; }}")

    @property
    def pid(self) -> str:
        return self._pid

    @property
    def package(self) -> str:
        return self._package






# L708-1079：ProviderSetupCard — 本地 PyQt 深色卡片 — 首次模型配置。
class ProviderSetupCard(QWidget):
    r"""本地 PyQt 深色卡片 — 首次模型配置。"""







    setup_completed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Dialog
        )
        self.setWindowTitle("okoc_provider_setup")
        self.setFixedSize(CARD_WIDTH, CARD_HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("ProviderSetupCard")
        self.setStyleSheet(
            f"#ProviderSetupCard {{ background-color:{CARD_BG}; border-radius:{int(CARD_RADIUS)}px; }}")

        s = QGraphicsDropShadowEffect(self)
        s.setBlurRadius(32)
        s.setOffset(0, 12)
        s.setColor(QColor(0, 0, 0, 150))
        self.setGraphicsEffect(s)

        self._drag = None
        self._rows: list[_ProviderRow] = []
        self._saving = False
        self._active_row: _ProviderRow | None = None
        self._build()



    def _build(self) -> None:
        lo = QVBoxLayout(self)
        lo.setContentsMargins(20, 16, 20, 12)
        lo.setSpacing(0)


        b = QLabel("Rheia")
        b.setStyleSheet(f"color:{BRAND_COLOR};font-size:18px;font-weight:bold;background:transparent;")
        lo.addWidget(b)

        s = QLabel(tr("ui.provider.config_api_key"))
        s.setStyleSheet(f"color:{SUB_COLOR};font-size:12px;background:transparent;")
        lo.addWidget(s)

        d = QLabel(tr("ui.provider.config_desc"))
        d.setWordWrap(True)
        d.setStyleSheet(f"color:{TEXT_HINT};font-size:11px;background:transparent;padding:8px 0 2px;")
        lo.addWidget(d)


        sec = QLabel(tr("ui.provider.recommended"))
        sec.setStyleSheet(
            f"color:{TEXT_PRIMARY};font-size:13px;font-weight:600;background:transparent;padding:8px 0 4px;")
        lo.addWidget(sec)


        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sc.setStyleSheet(f"""
            QScrollArea {{ background:transparent; border:1px solid {BORDER}; border-radius:8px; }}
            QScrollBar:vertical {{ background:transparent; width:6px; margin:0; }}
            QScrollBar::handle:vertical {{ background:#444; border-radius:3px; min-height:20px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
        """)
        sc_content = QWidget()
        sc_content.setStyleSheet("background:transparent;")
        sc_lo = QVBoxLayout(sc_content)
        sc_lo.setContentsMargins(0, 0, 0, 0)
        sc_lo.setSpacing(0)



        self._rows_lo = sc_lo
        self._loading_lbl = QLabel(tr("ui.ppanel.loading"))
        self._loading_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_lbl.setStyleSheet(
            f"color:{TEXT_HINT};font-size:12px;background:transparent;padding:20px;")
        sc_lo.addWidget(self._loading_lbl)
        sc_lo.addStretch()
        sc.setWidget(sc_content)
        lo.addWidget(sc, stretch=1)


        self._start_load_providers()


        bot = QHBoxLayout()
        bot.setContentsMargins(0, 12, 0, 0)

        cpr = QLabel(tr("ui.provider.copyright"))
        cpr.setStyleSheet("color:#555;font-size:10px;background:transparent;")
        bot.addWidget(cpr, alignment=Qt.AlignmentFlag.AlignVCenter)


        bot.addStretch()


        adv_btn = QPushButton(tr("ui.provider.advanced"))
        adv_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        adv_btn.setFixedSize(88, 32)
        fit_button_text(adv_btn, width=88)
        adv_btn.setStyleSheet(f"""
            QPushButton {{ background:transparent; color:{TEXT_HINT}; border:1px solid {BORDER};
                           border-radius:6px; font-size:12px; }}
            QPushButton:hover {{ background:{BG_INPUT}; color:{TEXT_PRIMARY}; }}
        """)
        adv_btn.clicked.connect(self._open_advanced_config)
        bot.addWidget(adv_btn)
        bot.addSpacing(8)

        self._close_btn = QPushButton(tr("ui.provider.skip"))
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setFixedSize(72, 32)
        fit_button_text(self._close_btn, width=72)
        self._close_btn.setStyleSheet(f"""
            QPushButton {{ background:transparent; color:{TEXT_HINT}; border:1px solid {BORDER};
                           border-radius:6px; font-size:12px; }}
            QPushButton:hover {{ background:{BG_INPUT}; color:{TEXT_PRIMARY}; }}
        """)
        self._close_btn.clicked.connect(self.close)
        bot.addWidget(self._close_btn)
        lo.addLayout(bot)

    def _start_load_providers(self) -> None:
        r"""起后台线程拉 provider 清单（QThread + 原生 Signal 回主线程填行）。"""
        self._load_thread = QThread(self)
        self._load_worker = _ProvidersLoadWorker()
        self._load_worker.moveToThread(self._load_thread)
        self._load_thread.started.connect(self._load_worker.run)
        self._load_worker.loaded.connect(self._on_providers_loaded)
        self._load_thread.start()

    def _on_providers_loaded(self, providers: list) -> None:
        r"""主线程回调：一次性把行填进去（GUI 操作必须在主线程）。"""
        sc_lo = self._rows_lo

        holder = sc_lo.parentWidget()
        if holder is not None:
            holder.setUpdatesEnabled(False)
        try:
            for p in providers:
                row = _ProviderRow(p)
                row.save_requested.connect(lambda checked=False, r=row: self._on_save_row(r))
                self._rows.append(row)
                sc_lo.insertWidget(sc_lo.count() - 1, row)
            if not providers:

                empty_hint = QLabel(f"{tr('ui.ppanel.no_provider')}\n{_LOAD_ERROR}".strip())
                empty_hint.setWordWrap(True)
                empty_hint.setStyleSheet(
                    f"color:{TEXT_HINT};font-size:11px;background:transparent;padding:12px;")
                sc_lo.insertWidget(sc_lo.count() - 1, empty_hint)
            if getattr(self, "_loading_lbl", None) is not None:
                self._loading_lbl.hide()
                self._loading_lbl.deleteLater()
                self._loading_lbl = None
        finally:
            if holder is not None:
                holder.setUpdatesEnabled(True)

        self._load_thread.quit()



    def mousePressEvent(self, ev) -> None:

        if ev.button() == Qt.MouseButton.LeftButton and ev.position().y() < self.height() * 0.24:
            self._drag = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:
        if self._drag is not None and ev.buttons() & Qt.MouseButton.LeftButton:
            self.move(ev.globalPosition().toPoint() - self._drag)
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:
        self._drag = None
        super().mouseReleaseEvent(ev)

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)

        if getattr(self, "_mask_size", None) == self.size():
            return
        self._mask_size = self.size()
        p = QPainterPath()
        p.addRoundedRect(QRectF(self.rect()), CARD_RADIUS, CARD_RADIUS)
        self.setMask(QRegion(p.toFillPolygon().toPolygon()))

    def showEvent(self, ev) -> None:

        screen = self.screen() or QApplication.primaryScreen()
        if screen:
            fg = self.frameGeometry()
            fg.moveCenter(screen.availableGeometry().center())
            self.move(fg.topLeft())
        super().showEvent(ev)



    class _OnboardWorker(QThread):
        r"""后台逐个 onboard provider，不阻塞 UI。"""
        progress = Signal(str)
        finished = Signal(list)

        def __init__(self, patches: dict[str, dict[str, str]]):
            super().__init__()
            self._patches = patches

        def run(self):
            failed: list[str] = []
            for pid, pinfo in self._patches.items():
                self.progress.emit(pid)

                _sync_provider_key_to_db(pid, pinfo["apiKey"])
                ok, msg = _write_wsl_provider_key(
                    pid, pinfo["apiKey"], pinfo.get("package", ""), pinfo.get("auth_choice", ""),
                    pinfo.get("base_url", ""), pinfo.get("model_id", ""))
                if not ok:
                    failed.append(f"{pid}: {msg[:80]}")


            try:
                from main.main import stk
                stk("provider_setup.onboard",
                    f"patches={len(self._patches)} failed={len(failed)}",
                    ok=len(failed) == 0)
            except Exception:
                pass
            self.finished.emit(failed)

    def _on_save_row(self, row: _ProviderRow) -> None:
        if self._saving:
            return

        v = row.key_value()
        if not v:

            QMessageBox.information(self, tr("ui.provider.config_api_key"),
                                    tr("ui.provider.enter_key_first"))
            return
        patches: dict[str, dict[str, str]] = {
            row.pid: {
                "apiKey": v,
                "package": row.package,
                "auth_choice": row._auth_choice,
                "base_url": row.field_value("base_url_input", "base_url"),
                "model_id": row.field_value("model_input_manual", "model_input", "model_name"),
            }
        }

        self._saving = True
        self._active_row = row
        row.set_saving()
        QApplication.processEvents()

        self._worker = self._OnboardWorker(patches)
        self._worker.progress.connect(self._on_worker_progress)
        self._worker.finished.connect(self._on_worker_done)
        self._worker.start()

    def _on_worker_progress(self, pid: str) -> None:


        QApplication.processEvents()
        for row in self._rows:
            if row.pid == pid:
                row.set_saving()

    def _on_worker_done(self, failed: list[str]) -> None:
        row = self._active_row
        self._active_row = None
        self._saving = False
        if failed:
            if row is not None:
                row.reset_save_btn()
            try:
                from main.main import stk
                stk("provider_setup.save", f"failed: {failed}", ok=False)
            except Exception:
                pass

            QMessageBox.warning(self, tr("ui.provider.partial_fail"), "\n".join(failed)[:600])
            return

        new_token = _get_gateway_token()
        if new_token:
            _sync_token_to_local(new_token)

        if row is not None:
            row.mark_saved()
        try:
            from main.main import stk
            stk("provider_setup.saved", f"gw_alive={bool(new_token)}", ok=bool(new_token))
        except Exception:
            pass


        self._restart_browser()
        self.setup_completed.emit(new_token)


        if getattr(self, "_close_btn", None) is not None:
            self._close_btn.setText(tr("ui.btn.close"))

    def _restart_browser(self) -> None:
        r"""_restart_browser"""





        try:
            from main.main import stk
            stk("provider_setup.restart",
                "配置已保存；按令不自动重启网关（插件/配置在下次网关重启后生效）", ok=True)
        except Exception:
            pass

    def _open_advanced_config(self) -> None:
        r"""打开高级配置子页面（增量化追加）。"""
        import traceback as _tb
        try:
            from ui.web.advanced_config import AdvancedConfigDialog
            dlg = AdvancedConfigDialog(parent=self.window() if self.window() else self)

            dlg.reload_requested.connect(self._reload_browser)
            dlg.exec()
        except Exception as e:

            try:
                from main.main import stk
                stk("advcfg.open", f"exception {type(e).__name__}: {e}", ok=False)
                stk("advcfg.open.traceback", _tb.format_exc()[-2000:], ok=False)
            except Exception:
                pass
            QMessageBox.warning(self, tr("ui.browser.notice"), tr("ui.provider.cannot_open_adv", e=e))

    def _reload_browser(self) -> None:
        r"""高级配置关闭（且本次保存过）时，收拢到 browser 的统一入口。"""




        try:
            p = self.window() if self.window() else self
            while p is not None and not (hasattr(p, "_request_reload")
                                         or hasattr(p, "_restart_gateway")):
                p = p.parent()
            from main.main import stk
            if p is not None and hasattr(p, "go_home"):
                p.go_home()
                stk("advcfg.reload", "已保存 ⇒ 回首页 /chat（热重载生效，不重启网关）")
            elif p is not None and hasattr(p, "_request_reload"):
                p._request_reload("advcfg.saved")
                stk("advcfg.reload", "统一重载入口已触发（已就绪即跳过整页重载）")

            else:
                stk("advcfg.reload", "browser not found, skip reload", ok=False)
        except Exception as e:
            from main.main import stk
            stk("advcfg.reload", f"reload failed: {e}", ok=False)






# L1087-1097：provider_setup_state — 三态判定：configured / unconfigured / unknown。
def provider_setup_state() -> str:
    r"""三态判定：configured / unconfigured / unknown。"""





    count, known = _count_available_models()
    if not known:
        return "unknown"
    return "configured" if count > 0 else "unconfigured"


# L1101-1103：needs_provider_setup — 兼容旧调用：只有确定没配才 True；unknown 不弹卡（交调用方轮询重试）。
def needs_provider_setup() -> bool:
    r"""兼容旧调用：只有确定没配才 True；unknown 不弹卡（交调用方轮询重试）。"""
    return provider_setup_state() == "unconfigured"


# L1107-1143：_count_available_models — 统计 available=true 的模型数量，并明确回答"这次拿到确定答案了吗"。
def _count_available_models() -> tuple[int, bool]:
    r"""统计 available=true 的模型数量，并明确回答"这次拿到确定答案了吗"。"""




    models_list = _cli_usage("models", "list")
    if not models_list:
        return (0, False)
    rc, out, err = _run_wsl(
        f'wsl -d {WSL_DISTRO} -u root -- bash -c '
        f'"export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; '
        f'{models_list} --json 2>/dev/null"'
    )
    if rc != 0 or not out.strip():


        try:
            from main.main import stk
            stk("provider_setup.count_models", f"rc={rc} err={err[:120]!r} → unknown", ok=False)
        except Exception:
            pass
        return (0, False)
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError:
        return (0, False)
    models = []
    if isinstance(parsed, dict):
        models = parsed.get("models", []) or parsed.get("rows", [])
    elif isinstance(parsed, list):
        models = parsed
    count = 0
    for m in models:
        if isinstance(m, dict) and m.get("available"):
            count += 1
    return (count, True)
