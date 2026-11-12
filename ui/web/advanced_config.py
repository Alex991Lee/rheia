# L1-905：Rheia 高级配置子页面
r"""Rheia 高级配置子页面"""
















from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Any, Callable

_proj = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _proj not in sys.path:
    sys.path.insert(0, _proj)

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ui.theme import BG, TEXT, ACCENT, ACCENT_HOVER, STATUS, BORDER

WSL_DISTRO = os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu"
try:
    from ui.i18n import tr
except ImportError:
    tr = lambda key, **fmt: key

_SERVICES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local_services.json")

_CARD_BG = BG["deep"]
_TEXT_PRIMARY = TEXT["primary"]
_TEXT_HINT = TEXT["hint"]
_GREEN = STATUS["green"]
_YELLOW = STATUS["yellow"]
_RED = STATUS["red"]

# L56-71：_protocol_choices — 协议下拉框选项：从字典 api_signals 读官方标准 adapter 清单。
def _protocol_choices() -> list[str]:
    r"""协议下拉框选项：从字典 api_signals 读官方标准 adapter 清单。"""





    try:
        from kernel.filter import _load_ambiguity
        api_signals = _load_ambiguity().get("api_signals") or {}
        stds = [k for k in api_signals if not k.startswith("_")]
        if stds:
            return stds
    except Exception:
        pass
    return ["openai-completions", "openai-responses", "anthropic-messages"]






# L79-84：_stake — _stake
def _stake(tag: str, msg: str, ok: bool = True) -> None:
    try:
        from main.main import stk
        stk(f"advcfg.{tag}", msg, ok=ok)
    except Exception:
        pass






_MANI_PATH = os.path.join(_proj, "kernel", "large", "mani.json")





# L98-149：load_services — 从 mani 的 provider 活数据读本地服务清单，替代手写 local_services.json。
def load_services() -> list[dict[str, Any]]:
    r"""从 mani 的 provider 活数据读本地服务清单，替代手写 local_services.json。"""





    services: list[dict[str, Any]] = []
    try:
        from kernel.filter import known_collaborators
        collabs = known_collaborators()

        api_by_id: dict[str, str] = {}
        dyn_by_id: dict[str, str] = {}
        try:
            with open(_MANI_PATH, "r", encoding="utf-8") as f:
                provs = (json.load(f).get("entries") or {}).get("providers") or []
            for p in provs:
                if isinstance(p, dict) and p.get("id"):
                    api_by_id[p["id"]] = (p.get("api") or "")
                    dyn_by_id[p["id"]] = (p.get("dynamic_by") or "")
        except (OSError, json.JSONDecodeError):
            pass

        for name, spec in collabs.items():
            svc = {
                "id": name,
                "label": name.title(),
                "native": True,
                "api": api_by_id.get(name, ""),
                "default_base": spec.get("default_base", ""),
                "discover": spec.get("discover", ""),
                "parse": spec.get("parse", {}),
                "dynamic_by": dyn_by_id.get(name, "wildcard"),
                "env_key": spec.get("env_key", ""),
                "local_api_key": spec.get("local_api_key", ""),
            }
            services.append(svc)
        if services:
            return services
    except Exception as e:
        _stake("load_services", f"活数据加载失败，降级手写 json: {e}", ok=False)


    try:
        with open(_SERVICES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("services", [])
        return [s for s in raw if isinstance(s, dict) and s.get("id")]
    except (OSError, json.JSONDecodeError) as e:
        _stake("load_services", f"failed: {e}", ok=False)
        return []


# L153-169：_run_wsl — 在 WSL 里同步跑一条命令，返回 (rc, stdout, stderr)。
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









# L180-240：_config_set_merge — 写入 models.providers.<id> 的字段，走官方 openclaw config patch --file。
def _config_set_merge(path: str, value: dict[str, Any]) -> tuple[bool, str]:
    r"""写入 models.providers.<id> 的字段，走官方 openclaw config patch --file。"""









    import json as _json
    import tempfile as _tmp


    parts = path.split(".")
    patch: dict[str, Any] = value
    for part in reversed(parts):
        patch = {part: patch}

    fd, tmp_path = _tmp.mkstemp(suffix=".json5", prefix="okoc-patch-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            _json.dump(patch, f, ensure_ascii=False, indent=2)

        wsl_path = tmp_path.replace("\\", "/")
        if len(wsl_path) > 1 and wsl_path[1] == ":":
            wsl_path = "/mnt/" + wsl_path[0].lower() + wsl_path[2:]

        from main.main import cli_usage
        patch_cmd = cli_usage("config", "patch")
        if not patch_cmd:
            return (False, "config patch 命令不可用")
        bash_script = (
            "export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; "
            f"{patch_cmd} --file {wsl_path} 2>&1"
        )
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0


        r = subprocess.run(
            ["wsl", "-d", WSL_DISTRO, "-u", "root", "--", "bash", "-c", bash_script],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
            startupinfo=si,
        )
        rc = r.returncode
        out = (r.stdout or "")
        err = (r.stderr or "")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    ok = rc == 0
    _stake("config_set", f"{path} rc={rc} out={out[:300]!r} err={err[:300]!r}", ok=ok)
    return (ok, (out + err).strip()[:300] if not ok else "ok")






# L248-280：discover_local_models — 按解释层的 discover/parse 声明，从本地服务发现端点拉模型列表。
def discover_local_models(base_url: str, spec: dict[str, Any]) -> list[str]:
    r"""按解释层的 discover/parse 声明，从本地服务发现端点拉模型列表。"""



    base = (base_url or "").rstrip("/")
    if not base:
        return []
    endpoint = base + spec["discover"]
    rc, out, _ = _run_wsl(
        f'wsl -d {WSL_DISTRO} -- bash -c "curl -s \'{endpoint}\'"'
    )

    _stake("discover", f"{spec['id']} endpoint={endpoint} rc={rc} raw={out[:300]!r}",
           ok=(rc == 0 and bool(out.strip())))
    if rc != 0 or not out.strip():
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        _stake("discover.parse", f"{spec['id']} JSON decode failed: {e}", ok=False)
        return []
    key, field = spec["parse"]["key"], spec["parse"]["field"]
    items = data.get(key) if isinstance(data, dict) else []
    if not isinstance(items, list):
        _stake("discover.parse", f"{spec['id']} key={key} not a list, got {type(items).__name__}", ok=False)
        return []
    ids = []
    for it in items:
        if isinstance(it, dict) and it.get(field):
            ids.append(str(it[field]))
    _stake("discover.parse", f"{spec['id']} extracted {len(ids)} ids via {key}/{field}", ok=bool(ids))
    return ids






# L288-304：_write_implicit_env — implicit_env 策略：写 env.vars.<ENV_KEY>，不写 models.providers.<id>，官方隐式发现。
def _write_implicit_env(spec: dict[str, Any], base_url: str, default_model: str | None) -> tuple[bool, str]:
    r"""implicit_env 策略：写 env.vars.<ENV_KEY>，不写 models.providers.<id>，官方隐式发现。"""







    env_key = spec.get("env_key", "")
    local_key = spec.get("local_api_key", "")
    if not env_key:
        return (False, f"{spec['id']} 缺少 env_key")

    ok, msg = _config_set_merge("env.vars", {env_key: local_key})
    _stake("write.implicit_env", f"{spec['id']} env={env_key} ok={ok}", ok=ok)
    return (ok, msg)


# L308-334：_write_wildcard — wildcard 策略：显式配置 + env 引用 + agents.defaults.models["<id>/"] 通配符。
def _write_wildcard(spec: dict[str, Any], base_url: str, default_model: str | None) -> tuple[bool, str]:
    r"""wildcard 策略：显式配置 + env 引用 + agents.defaults.models["<id>/"] 通配符。"""






    env_key = spec.get("env_key", "")
    local_key = spec.get("local_api_key", "")
    value: dict[str, Any] = {
        "api": spec["api"],
    }
    if env_key:
        value["apiKey"] = f"${{{env_key}}}"
    if base_url:
        value["baseUrl"] = base_url
    ok, msg = _config_set_merge(f"models.providers.{spec['id']}", value)
    if ok:

        if env_key and local_key:
            _config_set_merge("env.vars", {env_key: local_key})

        wildcard = {f"{spec['id']}/*": {}}
        _config_set_merge("agents.defaults.models", wildcard)
    _stake("write.wildcard", f"{spec['id']} ok={ok}", ok=ok)
    return (ok, msg)


# L338-355：_write_fixed — fixed 策略：写死全部模型进 models[]。
def _write_fixed(spec: dict[str, Any], base_url: str, models: list[str]) -> tuple[bool, str]:
    r"""fixed 策略：写死全部模型进 models[]。"""





    value: dict[str, Any] = {
        "api": spec["api"],
        "models": [{"id": m, "name": m} for m in models],
    }
    if spec.get("local_api_key"):
        value["apiKey"] = spec["local_api_key"]
    if base_url:
        value["baseUrl"] = base_url
    ok, msg = _config_set_merge(f"models.providers.{spec['id']}", value)
    _stake("write.fixed", f"{spec['id']} models={len(models)} ok={ok}", ok=ok)
    return (ok, msg)



# L360-413：write_custom_provider — 写 models.providers.<pid>。
def write_custom_provider(pid: str, fields: dict[str, Any], *,
                          backup: bool = True) -> tuple[bool, str]:
    r"""写 models.providers.<pid>。"""



    value: dict[str, Any] = {}
    for k, v in fields.items():
        if v is not None and v != "":

            if k == "models" and isinstance(v, list):
                norm_models = []
                for m in v:
                    if isinstance(m, dict) and m.get("id"):
                        m2 = dict(m)
                        m2.setdefault("name", m2["id"])
                        norm_models.append(m2)
                value[k] = norm_models
            else:
                value[k] = v
    ok, msg = _config_set_merge(f"models.providers.{pid}", value)

    db_warn = ""
    if ok and backup:
        api_key = value.get("apiKey")
        base_url = value.get("baseUrl", "")
        api = value.get("api", "")
        model_name = ""
        models = value.get("models")
        if isinstance(models, list) and models:
            m0 = models[0]
            if isinstance(m0, dict):
                model_name = m0.get("id", "")
        if api_key:
            try:
                from kernel.scout import setCredential, ensureRecoveryTables
                ensureRecoveryTables()
                setCredential(f"{pid}_api_key", api_key, pid)
                _stake("write.custom.db", f"{pid} apiKey 已同步到 DB")
            except Exception as e:
                _stake("write.custom.db", f"{pid} 同步 DB 失败: {e}", ok=False)
                db_warn += f" credentials({e});"

        try:
            from kernel.scout import setAdvancedProvider, ensureRecoveryTables
            ensureRecoveryTables()
            setAdvancedProvider(pid, base_url, api, api_key or "", model_name)
            _stake("write.custom.advdb", f"{pid} 高级配置已同步到 advanced_providers")
        except Exception as e:
            _stake("write.custom.advdb", f"{pid} 同步 advanced_providers 失败: {e}", ok=False)
            db_warn += f" advanced_providers({e});"
    _stake("write.custom", f"{pid} ok={ok} db_warn={db_warn!r}", ok=ok)

    return (ok, f"{msg} ⚠ 备份库未同步:{db_warn}" if (ok and db_warn) else msg)


# L417-434：_configured_provider_ids — 配置里现有的 models.providers 的 id 集合。
def _configured_provider_ids() -> set[str]:
    r"""配置里现有的 models.providers 的 id 集合。"""




    rc, out, _ = _run_wsl(
        f'wsl -d {WSL_DISTRO} -u root -- bash -c '
        f'"export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; '
        f'cat ~/.openclaw/openclaw.json 2>/dev/null"')
    if rc != 0 or not out.strip():
        return set()
    try:
        cfg = json.loads(out)
    except json.JSONDecodeError:
        return set()
    provs = ((cfg.get("models") or {}).get("providers") or {})
    return set(provs.keys()) if isinstance(provs, dict) else set()


# L438-445：_derive_pid — _derive_pid
def _derive_pid(base_url: str) -> str:
    if not base_url:
        return ""
    m = re.search(r"://([^/]+)", base_url)
    host = m.group(1) if m else base_url
    host = host.split(":")[0]
    host = re.sub(r"[^a-zA-Z0-9-]", "-", host).strip("-")
    return f"custom-{host}" if host else ""







_BUCKET_LABEL = {
    "ok": "ui.advcfg.probe_ok",
    "auth": "ui.advcfg.probe_auth",
    "no_model": "ui.advcfg.probe_no_model",
    "rate_limit": "ui.advcfg.probe_rate_limit",
    "billing": "ui.advcfg.probe_billing",
    "timeout": "ui.advcfg.probe_timeout",
    "format": "ui.advcfg.probe_format",
    "unknown": "ui.advcfg.probe_unknown",
}


# L466-494：probe_provider — 调用官方 openclaw models status --probe --probe-provider 验证连通性。
def probe_provider(pid: str) -> tuple[bool, str]:
    r"""调用官方 openclaw models status --probe --probe-provider 验证连通性。"""




    from main.main import cli_usage
    base = cli_usage("models", "status")
    if not base:
        return (False, "unknown")

    rc, out, err = _run_wsl(
        f'wsl -d {WSL_DISTRO} -u root -- bash -c '
        f'"export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; '
        f'{base} --probe --probe-provider \'{pid.replace(chr(39), "")}\' 2>&1"'
    )
    text = (out + err).lower()

    _stripped = re.sub(r"\x1b\[[0-9;]*m", "", out + err)
    _stake("probe", f"{pid} rc={rc} out={_stripped[:200]!r}", ok=(rc == 0))

    if rc == 0 and re.search(r"\b(ok|probed)\b", text):
        return (True, "ok")


    for bucket in ("auth", "no_model", "rate_limit", "billing", "timeout", "format"):
        if re.search(rf"\b{re.escape(bucket)}\b", text):
            return (False, bucket)
    return (False, "unknown")






# L502-558：_DiscoverResultDialog — 发现模型列表后的中间层：选默认 + 决定固定 or 动态。
class _DiscoverResultDialog(QDialog):
    r"""发现模型列表后的中间层：选默认 + 决定固定 or 动态。"""

    def __init__(self, svc_label: str, models: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("ui.advcfg.found_result"))
        from ui.theme import fitToScreen
        fitToScreen(self, 0.4, 0.4)
        self.setStyleSheet(f"QDialog{{background:{_CARD_BG};color:{_TEXT_PRIMARY};}} QLabel{{background:transparent;}}")
        self.choice = (None, None)
        self._models = models

        lo = QVBoxLayout(self)
        lo.setContentsMargins(16, 14, 16, 12)
        lo.setSpacing(8)

        t = QLabel(tr("ui.advcfg.found_models", svc=svc_label, n=len(models)))
        t.setStyleSheet(f"color:{_TEXT_PRIMARY};font-size:14px;font-weight:bold;")
        lo.addWidget(t)

        self._combo = QComboBox()
        self._combo.addItems(models)
        lo.addWidget(self._combo)

        fixed = QLabel(tr("ui.advcfg.deploy_local"))
        fixed.setWordWrap(True)
        fixed.setStyleSheet(f"color:{_TEXT_HINT};font-size:10px;")
        lo.addWidget(fixed)

        dyn = QLabel(tr("ui.advcfg.discover_more"))
        dyn.setWordWrap(True)
        dyn.setStyleSheet(f"color:{_TEXT_HINT};font-size:10px;")
        lo.addWidget(dyn)

        br = QHBoxLayout()
        br.addStretch()
        c = QPushButton(tr("ui.btn.cancel"))
        c.setStyleSheet(f"QPushButton{{background:transparent;color:{_TEXT_HINT};border:1px solid {BORDER};border-radius:6px;padding:6px 12px;}}")
        c.clicked.connect(self.reject)
        br.addWidget(c)
        fb = QPushButton(tr("ui.advcfg.deploy_local_btn"))
        fb.setStyleSheet(f"QPushButton{{background:{ACCENT};color:#0a0a0a;border-radius:6px;padding:6px 12px;font-weight:600;}}")
        fb.clicked.connect(self._on_fixed)
        br.addWidget(fb)
        db = QPushButton(tr("ui.advcfg.discover_more_btn"))
        db.setStyleSheet(f"QPushButton{{background:{ACCENT};color:#0a0a0a;border-radius:6px;padding:6px 12px;font-weight:600;}}")
        db.clicked.connect(self._on_dynamic)
        br.addWidget(db)
        lo.addLayout(br)

    def _on_fixed(self):
        self.choice = ("fixed", self._combo.currentText())
        self.accept()

    def _on_dynamic(self):
        self.choice = ("dynamic", self._combo.currentText())
        self.accept()






# L566-899：AdvancedConfigDialog — AdvancedConfigDialog
class AdvancedConfigDialog(QDialog):


    reload_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("ui.advcfg.title"))

        self.setFixedWidth(430)
        self.setStyleSheet(f"QDialog{{background:{_CARD_BG};color:{_TEXT_PRIMARY};}} QLabel{{background:transparent;}}")
        self._services = load_services()
        self._discovered: list[str] = []
        self._dirty = False
        self._reload_emitted = False
        self._build()

    def _mark_saved(self, pid: str) -> None:
        self._dirty = True
        _stake("advcfg.saved", f"pid={pid} dirty=True", ok=True)

    def _emit_reload(self, reason: str) -> None:
        r"""reload 的唯一出口：一次会话只发一次。"""





        if self._reload_emitted:
            _stake("advcfg.reload", f"skip: already emitted ({reason})", ok=True)
            return
        self._reload_emitted = True
        self.reload_requested.emit()
        _stake("advcfg.reload", f"reload_requested emitted ({reason})", ok=True)

    def closeEvent(self, event) -> None:
        r"""关闭配置页时，若本次保存过配置，发 reload_requested（只发一次）。"""
        if self._dirty:
            self._emit_reload("close")
        else:
            _stake("advcfg.close", "no changes, skip reload", ok=True)
        super().closeEvent(event)

    def _build(self) -> None:
        lo = QVBoxLayout(self)
        lo.setContentsMargins(16, 14, 16, 12)
        lo.setSpacing(10)

        t = QLabel(tr("ui.advcfg.title"))
        t.setStyleSheet(f"color:{_TEXT_PRIMARY};font-size:16px;font-weight:bold;")
        lo.addWidget(t)

        d = QLabel(tr("ui.advcfg.desc"))
        d.setWordWrap(True)
        d.setStyleSheet(f"color:{_TEXT_HINT};font-size:11px;")
        lo.addWidget(d)


        sec = QLabel(tr("ui.advcfg.local_service"))
        sec.setStyleSheet(f"color:{_TEXT_PRIMARY};font-size:13px;font-weight:600;")
        lo.addWidget(sec)

        self._local_edits: dict[str, QLineEdit] = {}
        for svc in self._services:
            lo.addWidget(self._build_local_row(svc))

        lo.addSpacing(4)


        sec2 = QLabel(tr("ui.advcfg.custom_endpoint"))
        sec2.setStyleSheet(f"color:{_TEXT_PRIMARY};font-size:13px;font-weight:600;")
        lo.addWidget(sec2)

        pr = QHBoxLayout()
        pr.addWidget(QLabel(tr("ui.advcfg.protocol")))
        self._proto = QComboBox()
        for p in _protocol_choices():
            self._proto.addItem(p, p)
        self._proto.setMaximumWidth(200)
        pr.addWidget(self._proto, alignment=Qt.AlignmentFlag.AlignLeft)
        pr.addStretch()
        lo.addLayout(pr)

        self._base = QLineEdit()
        self._base.setPlaceholderText(tr("ui.advcfg.base_ph"))
        self._base.setStyleSheet(self._input_qss())
        self._base.setMinimumWidth(340)
        lo.addWidget(self._base, alignment=Qt.AlignmentFlag.AlignLeft)

        self._key = QLineEdit()
        self._key.setEchoMode(QLineEdit.EchoMode.Password)
        self._key.setPlaceholderText(tr("ui.advcfg.key_ph"))
        self._key.setStyleSheet(self._input_qss())
        self._key.setMinimumWidth(380)
        lo.addWidget(self._key, alignment=Qt.AlignmentFlag.AlignLeft)

        self._mid = QLineEdit()
        self._mid.setPlaceholderText(tr("ui.advcfg.model_ph"))
        self._mid.setStyleSheet(self._input_qss())
        self._mid.setMinimumWidth(220)
        lo.addWidget(self._mid, alignment=Qt.AlignmentFlag.AlignLeft)


        act = QHBoxLayout()
        probe = QPushButton(tr("ui.advcfg.test_conn"))
        probe.setStyleSheet(self._accent_btn_qss())
        probe.clicked.connect(self._do_probe)
        act.addWidget(probe)
        act.addStretch()
        save = QPushButton(tr("ui.btn.save"))
        save.setStyleSheet(self._accent_btn_qss())
        save.clicked.connect(self._do_save)
        act.addWidget(save)
        lo.addLayout(act)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f"color:{_TEXT_HINT};font-size:11px;")
        lo.addWidget(self._status)
        lo.addStretch()

    def _build_local_row(self, svc: dict[str, Any]) -> QWidget:
        w = QWidget()
        w.setStyleSheet(f"background:{BG['card']};border-radius:8px;")
        h = QHBoxLayout(w)
        h.setContentsMargins(10, 6, 10, 6)
        h.setSpacing(8)
        lbl = QLabel(svc["label"])
        lbl.setStyleSheet(f"color:{_TEXT_PRIMARY};font-size:12px;font-weight:600;")
        lbl.setMinimumWidth(60)
        h.addWidget(lbl)
        e = QLineEdit(svc["default_base"])
        e.setStyleSheet(self._input_qss())
        e.setMinimumWidth(340)
        h.addWidget(e, alignment=Qt.AlignmentFlag.AlignLeft)
        self._local_edits[svc["id"]] = e
        b = QPushButton(tr("ui.advcfg.discover_btn"))
        b.setStyleSheet(self._accent_btn_qss())
        b.clicked.connect(lambda _=False, s=svc: self._do_discover(s))
        h.addWidget(b, alignment=Qt.AlignmentFlag.AlignLeft)
        h.addStretch()
        return w

    def _input_qss(self) -> str:
        return f"""
            QLineEdit {{ background:{BG['card']}; color:{_TEXT_PRIMARY};
                         border:1px solid {BORDER}; border-radius:6px; padding:6px 10px; font-size:12px; }}
            QLineEdit:focus {{ border-color:{ACCENT}; }}
        """

    def _accent_btn_qss(self) -> str:
        return f"""
            QPushButton {{ background:{ACCENT}; color:#0a0a0a; border:none;
                           border-radius:6px; font-size:12px; font-weight:600; padding:6px 12px; }}
            QPushButton:hover {{ background:{ACCENT_HOVER}; }}
        """



    def _do_discover(self, svc: dict[str, Any]) -> None:
        base = self._local_edits[svc["id"]].text().strip()
        if not base:
            self._set_status(tr("ui.advcfg.need_baseurl"), _YELLOW)
            return
        if getattr(self, "_discovering", False):
            return
        self._discovering = True
        self._set_status(tr("ui.advcfg.discovering", label=svc['label']), _TEXT_HINT)
        QApplication_process_events()

        self._discover_worker = self._DiscoverWorker(svc, base)
        self._discover_worker.done.connect(self._on_discover_done)
        self._discover_worker.start()

    def _on_discover_done(self, svc: dict, base: str, models: list) -> None:
        self._discovering = False
        if not models:
            self._set_status(tr("ui.advcfg.no_models", label=svc['label']), _RED)
            return
        dlg = _DiscoverResultDialog(svc["label"], models, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            self._set_status(tr("ui.advcfg.found_n", n=len(models)), _TEXT_HINT)
            return
        mode, default_model = dlg.choice
        if mode == "fixed":
            ok, msg = _write_fixed(svc, base, models)
        else:
            strategy = {"implicit_env": _write_implicit_env, "wildcard": _write_wildcard}.get(svc["dynamic_by"])
            if strategy is None:


                _stake("discover.strategy",
                       f"{svc['id']} dynamic_by={svc['dynamic_by']!r} 无策略，回退 fixed", ok=False)
                ok, msg = _write_fixed(svc, base, models)
            else:
                ok, msg = strategy(svc, base, default_model)
        if ok:
            self._set_status(tr("ui.advcfg.saved_mode", m=tr("ui.advcfg.fixed") if mode == "fixed" else tr("ui.advcfg.dynamic")), _GREEN)
            self._mark_saved(svc["id"])
        else:
            self._set_status(tr("ui.advcfg.save_failed", msg=msg), _RED)

    def _do_probe(self) -> None:
        base = self._base.text().strip()
        key = self._key.text().strip()
        mid = self._mid.text().strip()
        if not base:
            self._set_status(tr("ui.advcfg.need_baseurl"), _YELLOW)
            return
        if getattr(self, "_probing", False):
            return
        pid = _derive_pid(base)
        fields = {"baseUrl": base, "api": self._proto.currentData(),
                  "apiKey": key or None, "models": [{"id": mid}] if mid else None}

        self._probing = True
        self._set_status(tr("ui.advcfg.probing"), _TEXT_HINT)
        QApplication_process_events()

        self._probe_worker = self._ProbeWorker(pid, fields)
        self._probe_worker.done.connect(self._on_probe_done)
        self._probe_worker.start()

    def _on_probe_done(self, pid: str, ok: bool, bucket: str) -> None:
        self._probing = False
        if ok:
            self._set_status(tr("ui.advcfg.probe_ok"), _GREEN)
        else:
            self._set_status(tr(_BUCKET_LABEL.get(bucket, bucket)), _YELLOW if bucket == "no_model" else _RED)

    class _ProbeWorker(QThread):
        r"""后台探测连通性，不阻塞 UI 主线程。"""
        done = Signal(str, bool, str)

        def __init__(self, pid: str, fields: dict):
            super().__init__()
            self._pid = pid
            self._fields = fields

        def run(self):




            if self._pid not in _configured_provider_ids():
                ok, _ = write_custom_provider(self._pid, self._fields, backup=False)
                if not ok:
                    self.done.emit(self._pid, False, "unknown")
                    return
            ok, bucket = probe_provider(self._pid)
            self.done.emit(self._pid, ok, bucket)

    def _do_save(self) -> None:
        base = self._base.text().strip()
        key = self._key.text().strip()
        mid = self._mid.text().strip()
        if not mid:
            self._set_status(tr("ui.advcfg.need_model"), _YELLOW)
            return
        if getattr(self, "_saving", False):
            return


        pid = _derive_pid(base)
        if not pid:
            self._set_status(tr("ui.advcfg.need_baseurl"), _YELLOW)
            return
        fields = {"baseUrl": base or None, "api": self._proto.currentData(),
                  "apiKey": key or None, "models": [{"id": mid}]}
        self._saving = True
        self._set_status(tr("ui.advcfg.configuring"), _TEXT_HINT)
        QApplication_process_events()

        self._worker = self._SaveWorker(pid, fields)
        self._worker.progress.connect(lambda t: self._set_status(t, _TEXT_HINT))
        self._worker.done.connect(self._on_save_done)
        self._worker.start()

    def _on_save_done(self, pid: str, ok: bool) -> None:
        self._saving = False
        if ok:
            self._set_status(tr("ui.advcfg.saved_provider", pid=pid), _GREEN)
            self._mark_saved(pid)


            self._emit_reload("saved")
            QMessageBox.information(
                self, tr("ui.advcfg.config_ok"),
                tr("ui.advcfg.config_ok_msg", pid=pid),
            )
            self.accept()
        else:
            self._set_status(tr("ui.advcfg.save_failed", msg=pid), _RED)

    class _SaveWorker(QThread):
        r"""后台写 models.providers，不阻塞 UI。"""





        progress = Signal(str)
        done = Signal(str, bool)

        def __init__(self, pid: str, fields: dict):
            super().__init__()
            self._pid = pid
            self._fields = fields

        def run(self):
            try:
                self.progress.emit(tr("ui.advcfg.writing"))
                ok, _ = write_custom_provider(self._pid, self._fields)
                self.done.emit(self._pid, ok)
            except Exception as _e:
                _stake("save_worker", f"异常 {type(_e).__name__}: {_e}", ok=False)
                self.done.emit(self._pid, False)

    class _DiscoverWorker(QThread):
        r"""后台拉本地服务发现端点，不阻塞 UI 主线程。"""
        done = Signal(object, str, list)

        def __init__(self, svc: dict, base: str):
            super().__init__()
            self._svc = svc
            self._base = base

        def run(self):
            models = discover_local_models(self._base, self._svc)
            self.done.emit(self._svc, self._base, models)

    def _set_status(self, text: str, color: str) -> None:
        self._status.setText(text)
        self._status.setStyleSheet(f"color:{color};font-size:11px;")


# L903-905：QApplication_process_events — QApplication_process_events
def QApplication_process_events() -> None:
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
