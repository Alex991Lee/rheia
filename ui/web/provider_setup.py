"""
Rheia 模型配置中间层
==============================
当 Gateway 就绪但无可用模型提供商时弹出本地 PyQt 配置卡片。
读 kernel/large/mani.json → 展示主流 provider → 用户填 API key →
直接写入 WSL openclaw.json → 重启 Gateway。

架构边界：
- 只负责「第一次配置」—— 无任何 API key 时弹出
- 只在 models.providers.<id>.apiKey 写密钥
- 不动 Gateway 自身的 token / auth / locale
- 配完即退，后续所有配置走 Control UI
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from typing import Any

# 确保能 import kernel 和同级模块
_proj = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _proj not in sys.path:
    sys.path.insert(0, _proj)

try:
    from ui.i18n import tr
except ImportError:
    tr = lambda key, **fmt: key

_MANI_PATH = os.path.join(_proj, "kernel", "large", "mani.json")

# 上一次 _load_providers() 的失败原因：卡片上要能说明「是没拉到」而不是「没有 provider」。
_LOAD_ERROR = ""


def _cli_usage(cli_id: str, action: str, option: str = "") -> str:
    """从 mani 的 clis 查「这个动作该调哪个命令」。查不到返回空串，不猜。"""
    from main.main import cli_usage
    return cli_usage(cli_id, action, option)


from PySide6.QtCore import Qt, QThread, QTimer, QRectF, Signal
from PySide6.QtGui import QColor, QPainterPath, QRegion
from PySide6.QtWidgets import (
    QApplication, QComboBox, QGraphicsDropShadowEffect,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

# ═══════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════

WSL_DISTRO = os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu"
GATEWAY_PORT = 18789
_LOCALDATA = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia")

# ── 视觉规格（统一深色主题）──
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


# ── provider 配置：直接读写 WSL openclaw.json models.providers.<id>.apiKey ──


# ═══════════════════════════════════════════════
# WSL helpers
# ═══════════════════════════════════════════════

def _sh_single_quote(value: str) -> str:
    """把值包成 shell 单引号字面量（内部单引号按 '\\'' 转义）。

    key 里带引号时不再破坏命令结构（原来是把值直接拼进 '...'）。
    """
    return "'" + value.replace("'", "'\\''") + "'"


def _run_wsl(cmd: str) -> tuple[int, str, str]:
    """在 WSL 里同步跑一条命令，返回 (rc, stdout, stderr)。

    ⚠ 壳层不留时间闸：不传 timeout（命令返回即信号），
    原先那个「签名留着、值不生效」的死 timeout 参数已去掉。
    """
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


def _read_wsl_config() -> dict[str, Any]:
    rc, out, _ = _run_wsl(
        f'wsl -d {WSL_DISTRO} -u root -- bash -c "cat ~/.openclaw/openclaw.json 2>/dev/null"')
    if rc == 0 and out.strip():
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            pass
    return {}


def _onboard_provider(pid: str, key: str, auth_choice: str = "") -> tuple[bool, str]:
    """官方 provider 通过 openclaw onboard --auth-choice <value> 完整配置。

    官方语义（providers/deepseek.md Getting started）：onboard 是完整注册——写 auth
    + 设默认模型 + 发布模型目录到 Gateway 运行时。paste-api-key 只写 auth profile、
    不发布模型目录，导致 models list 有（静态目录）但 infer/聊天 Unknown model（实测）。

    auth_choice 从 mani 长出来（spider 提取官方文档 --auth-choice 值），不硬编
    --{pid}-api-key 规律（moonshot 就有 moonshot-api-key / moonshot-api-key-cn 两个）。
    """
    onboard_cmd = _cli_usage("onboard", "") or "openclaw onboard"
    if not auth_choice:
        return (False, tr("ui.ppanel.method_not_declared"))
    # 壳层不留时间闸：不重试、不 sleep；onboard 一次跑到底，
    # 结果只看它自己的 rc（命令返回即信号）。
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


def _install_provider_plugin(package: str) -> tuple[bool, str]:
    """装 official external 的 provider 插件（@openclaw/xxx-provider）。

    官方文档铁律：official external provider 要先 plugins install 装插件，
    再配 key，插件才能发布模型目录。bundled 的（无 package）跳过。
    """
    if not package:
        return (True, tr("ui.ppanel.bundled_no_plugin"))
    plugins_install = _cli_usage("plugins", "install")
    plugins_list = _cli_usage("plugins", "list")
    if not plugins_install or not plugins_list:
        return (False, tr("ui.ppanel.method_not_declared"))

    # 幂等：插件已装则不重装、不 restart。避免测试连接反复装插件 + 反复重启 Gateway
    # （plugins install 对已装的插件会报 "plugin already exists"，且每次 install 都
    # 触发 config reload → gateway restart，测试连接点几下就重启几下）。
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

    # 官方铁律（providers/deepseek.md Install plugin 节）：plugins install 之后必须
    # gateway restart 才会 loaded（不重启 → 聊天执行时 "Unknown model"）。
    # 但这里**不再自己起第三条重启入口**：重启统一由卡片走 browser 的统一入口
    # （_on_worker_done → _restart_browser → browser._restart_gateway：生命周期锁 +
    # 探活 + reload），避免「provider_panel 自己 restart / provider_setup 调
    # restart_gateway_safe / browser 统一入口」三套口径打架。

    return (ok, msg)


def _cli_models_json() -> str:
    """官方 models list --json 原样输出（拿不到返回空串，不猜）。"""
    models_list = _cli_usage("models", "list")
    if not models_list:
        return ""
    rc, out, _err = _run_wsl(
        f'wsl -d {WSL_DISTRO} -u root -- bash -c '
        f'"export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; '
        f'{models_list} --json 2>/dev/null"'
    )
    return out if rc == 0 else ""


def _provider_available(pid: str) -> bool:
    """热重载判据：models list --json 里属于该 provider 的模型出现 available=true。"""
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


def _wait_provider_available(pid: str, tries: int = 15) -> bool:
    """轮询等热重载生效：官方表里 models / auth.* 属热加载，本来就不该重启网关。"""
    for _ in range(max(1, tries)):
        if _provider_available(pid):
            return True
        time.sleep(2)
    return False


def _write_key_narrow(pid: str, key: str) -> tuple[bool, str]:
    """窄写：只动 models.providers.<id>，一个 gateway.* 字段都不碰。

    官方 hot-reload 表：models / auth.profiles = 不需要重启；
    gateway.mode / bind / port / tailscale = 必须重启。
    所以配 key 绝不能走 openclaw onboard（它会写 gateway.mode=local 等监听配置）。
    """
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


def _write_provider_overlay(pid: str, key: str, base_url: str = "", model_id: str = "") -> tuple[bool, str]:
    """窄写 models.providers.<id>，不碰任何 gateway.*。

    - 内置/带插件的 provider：只写 apiKey（官方允许 bundled provider 的 overlay）
    - 非内置（真·自定义）：官方 schema 强制要求 baseUrl + models，缺一个就拒
    """
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


def _write_wsl_provider_key(pid: str, key: str, package: str = "", auth_choice: str = "",
                            base_url: str = "", model_id: str = "") -> tuple[bool, str]:
    """写入 provider key，分两条路径：
      - 官方 provider（manifest 有插件包名 package）→ 先装插件再 openclaw onboard
      - 自定义 provider（无 package）→ openclaw config set models.providers.<id> --merge

    判据用 package（plugin.package 非空 = official external 插件），插件存在就行。
    auth_choice 从 mani 长出来（官方 --auth-choice 值），传给 onboard 用。
    """
    if package:
        # 先装插件（official external 必须有插件才能发布模型）
        _install_provider_plugin(package)
        # ── 不再走 openclaw onboard --mode local ──
        # 官方 hot-reload 表：models / auth.* 热加载（不需要重启）；
        # gateway.mode / bind / port / tailscale 改一次就要求重启网关。
        # onboard 会把 gateway.mode=local 等监听配置一起写进去 ⇒ 点一次「保存并启动」
        # 就把自己写进"需要重启"的类别里（journal 实测：gateway.port/bind/tailscale）。
        # 现在只窄写 apiKey，写完轮询 models list 等它自己热生效。
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
    # ── 自定义 provider（无插件包）：官方 schema 要求 baseUrl + models ──
    if not base_url or not model_id:
        return (False, "自定义 provider 必须同时填 baseUrl 与模型 id（官方 schema 要求，缺一个会被校验拒绝）")
    ok, msg = _write_provider_overlay(pid, key, base_url=base_url, model_id=model_id)
    if ok:
        _wait_provider_available(pid)
    return (ok, msg)


def _get_gateway_token() -> str:
    cfg = _read_wsl_config()
    return (cfg.get("gateway") or {}).get("auth", {}).get("token", "")


def _sync_token_to_local(token: str) -> None:
    """把新 token 写入本地缓存 gateway_config.json（供 main/main.py 读取）。

    token 是 gateway 的运行时凭据，权威来源是 openclaw.json 的 gateway.auth.token。
    不进 baseline_pool.db（不进备份），只做本地缓存，避免备份旧 token 恢复时覆写新 token 导致失联。
    """
    try:
        os.makedirs(_LOCALDATA, exist_ok=True)
        cfg = os.path.join(_LOCALDATA, "gateway_config.json")
        # ⚠ 2026-10-19 修（真 bug）：原来整文件覆盖成 {url, token}，把 gpu_render
        #   等其它键一起抹掉 ⇒ 客户关掉 GPU 渲染后，任何一次 token 同步都让开关"自己弹回去"。
        #   改成读-改-写，保留其它键。
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


def _sync_provider_key_to_db(pid: str, key: str) -> None:
    """把 provider API key 备份到 baseline_pool.db credentials 表。"""
    try:
        from kernel.scout import setCredential, ensureRecoveryTables
        ensureRecoveryTables()
        setCredential(f"{pid}_api_key", key, pid)
    except Exception as e:
        # 原来连桩都不打（彻底静默）——落盘账要能看出「DB 那份没写成」。
        try:
            from main.main import stk
            stk("provider_setup.db_sync", f"{pid} setCredential failed: {type(e).__name__}: {e}", ok=False)
        except Exception:
            pass


# ═══════════════════════════════════════════════
# 数据加载
# ═══════════════════════════════════════════════

def _load_providers() -> list[dict[str, Any]]:
    """从 large.run() 产出的 render 加载 provider 列表。

    这是「语义→字段→控件」链的出口：large 已经把每个 provider 的三轴语义
    （authKind/transport/model_source）推导成字段清单，每个字段带 widget 类型。
    UI 只读这份 render，控件自己长出来，不再硬画。
    """
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
        # 本卡只服务「填 API Key + onboard」的 provider。aws_sdk（Bedrock）靠 AWS
        # 环境变量 + 自动发现，local_marker（本地服务）靠端点，官方都不是填 key
        # 的流程，不出现在这里（否则长出无意义的 api key 输入框）。
        if inst.get("auth_kind", "") != "api_key":
            continue
        render = inst.get("render") or {}
        fields = render.get("fields") or []
        if not fields:
            continue
        # 配 key 需要：插件包名（装 official external 插件）、auth env（提示用）
        plugin = inst.get("plugin") or {}
        auth = inst.get("auth") or {}
        env_vars = auth.get("env_vars") or []
        env = env_vars[0].get("var", "") if env_vars else ""
        out.append({
            "id": pid,
            "name": inst.get("display_name") or inst.get("section_name") or pid.title(),
            "env": env,
            "package": (plugin.get("package") or "").strip(),
            "fields": fields,          # render 里的字段（含 widget 类型）
            "transport": inst.get("transport", ""),
            "config_shape": inst.get("config_shape", ""),
            "auth_choice": inst.get("auth_choice") or "",
        })

    # 不硬编排序优先级——按 manifest 原顺序（spider 按官方文档顺序产出）。
    return out


class _ProvidersLoadWorker(QThread):
    """后台拉 provider 清单（large.run() 实测十几秒，不能在 GUI 线程里跑）。"""

    loaded = Signal(list)

    def run(self) -> None:
        try:
            self.loaded.emit(_load_providers())
        except Exception as _e:      # 兜底：异常绝不留在子线程里
            try:
                from main.main import stk as _stk
                _stk("provider_setup.load_worker", f"failed: {_e}", ok=False)
            except Exception:
                pass
            self.loaded.emit([])


# ═══════════════════════════════════════════════
# Provider 行
# ═══════════════════════════════════════════════

class _ProviderRow(QWidget):
    """单行 provider 条目：控件从 render.fields 长出来（不硬画）。

    large.run() 已按三轴语义推导出字段清单，每个字段带 widget 类型。
    这里遍历 fields，按 widget 类型生成控件：
      QLineEdit → 输入框（api_key_input 存引用供 key_value()）
      QPushButton → 按钮
      QComboBox → 下拉（互斥单选）
      QLabel → 只读展示
    语义决定控件，UI 不写死。
    """

    save_requested = Signal()  # 点击本行「保存并启动」按钮

    def __init__(self, info: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pid = info["id"]
        self._env = info.get("env", "")
        self._package = info.get("package", "")
        self._auth_choice = info.get("auth_choice", "")
        self._fields = info.get("fields") or []
        self._key_input: QLineEdit | None = None
        self._field_inputs: dict[str, QWidget] = {}  # 字段名 → 控件（自定义 provider 要 baseUrl/模型 id）
        self._save_row_btn: QPushButton | None = None
        self._dot = None
        self.setStyleSheet("_ProviderRow { background: transparent; }")

        # 条目用垂直布局：名称在上，字段在下自动换行
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 6, 12, 6)
        outer.setSpacing(6)

        # 名称行
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

        # 字段区：垂直布局，每个字段一行、靠左排列
        field_box = QVBoxLayout()
        field_box.setSpacing(6)
        action_fields = []
        for f in self._fields:
            name = f.get("name", "")
            widget_type = f.get("widget", "QLineEdit")
            # 展示类字段由名称行承担，不重复长
            if name in ("provider_name", "provider_icon", "status"):
                continue
            w = self._make_widget(name, widget_type, f)
            if w is not None:
                self._field_inputs[name] = w
                action_fields.append(w)

        # 行内「保存并启动」按钮：跟在字段右侧同一行
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
            # 行为语义（scope_mappings 声明）：credential_input 存引用供 key_value()
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
            # 行为语义：无 test_connection（配置阶段官方无测试连接，见 model-providers.md：
            # 测连接需先 onboard 模型，配置阶段模型未在册，CLI 无从 probe）
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
        # 其他类型（QLabel 等展示类）由左侧承担，这里返回 None 不长
        return None

    def key_value(self) -> str:
        if self._key_input is None:
            return ""
        return self._key_input.text().strip()

    def field_value(self, *names: str) -> str:
        """按字段名取输入值（QLineEdit / QComboBox 都认），取不到返回空串。"""
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
        """进入配置中状态：禁用本行按钮并提示。"""
        if self._save_row_btn is not None:
            self._save_row_btn.setEnabled(False)
            self._save_row_btn.setText(tr("ui.provider.configuring"))

    def reset_save_btn(self) -> None:
        """恢复本行按钮到可点状态。"""
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


# ═══════════════════════════════════════════════
# 主卡片
# ═══════════════════════════════════════════════

class ProviderSetupCard(QWidget):
    """本地 PyQt 深色卡片 — 首次模型配置。

    用法:
        card = ProviderSetupCard()
        card.setup_completed.connect(lambda token: ...)
        card.show()
    """

    setup_completed = Signal(str)  # token

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

    # ── UI ──

    def _build(self) -> None:
        lo = QVBoxLayout(self)
        lo.setContentsMargins(20, 16, 20, 12)
        lo.setSpacing(0)

        # 标题
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

        # 区段标题
        sec = QLabel(tr("ui.provider.recommended"))
        sec.setStyleSheet(
            f"color:{TEXT_PRIMARY};font-size:13px;font-weight:600;background:transparent;padding:8px 0 4px;")
        lo.addWidget(sec)

        # 滚动区
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

        # provider 清单改到后台线程拉（large.run() 实测十几秒）——卡片先出来，
        # 清单回来再一次性填行，不在 GUI 线程里同步等（原来一弹出就卡住不动）。
        self._rows_lo = sc_lo
        self._loading_lbl = QLabel(tr("ui.ppanel.loading"))
        self._loading_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_lbl.setStyleSheet(
            f"color:{TEXT_HINT};font-size:12px;background:transparent;padding:20px;")
        sc_lo.addWidget(self._loading_lbl)
        sc_lo.addStretch()
        sc.setWidget(sc_content)
        lo.addWidget(sc, stretch=1)

        # 起后台加载（见 _start_load_providers / _on_providers_loaded）
        self._start_load_providers()

        # 底部
        bot = QHBoxLayout()
        bot.setContentsMargins(0, 12, 0, 0)

        cpr = QLabel(tr("ui.provider.copyright"))
        cpr.setStyleSheet("color:#555;font-size:10px;background:transparent;")
        bot.addWidget(cpr, alignment=Qt.AlignmentFlag.AlignVCenter)

        # 版权标签在左，剩余空间推到右侧：高级配置 + 跳过 两个按钮统一靠右、间隔小
        bot.addStretch()

        # ── 高级配置入口（增量化追加，不触碰上方任何现有逻辑）──
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
        """起后台线程拉 provider 清单（QThread + 原生 Signal 回主线程填行）。"""
        self._load_thread = QThread(self)
        self._load_worker = _ProvidersLoadWorker()
        self._load_worker.moveToThread(self._load_thread)
        self._load_thread.started.connect(self._load_worker.run)
        self._load_worker.loaded.connect(self._on_providers_loaded)
        self._load_thread.start()

    def _on_providers_loaded(self, providers: list) -> None:
        """主线程回调：一次性把行填进去（GUI 操作必须在主线程）。"""
        sc_lo = self._rows_lo
        # 一批只重绘一次：整列行 + 撤加载标签一起做完再放行重绘（少一次跳变）。
        holder = sc_lo.parentWidget()
        if holder is not None:
            holder.setUpdatesEnabled(False)
        try:
            for p in providers:
                row = _ProviderRow(p)
                row.save_requested.connect(lambda checked=False, r=row: self._on_save_row(r))
                self._rows.append(row)
                sc_lo.insertWidget(sc_lo.count() - 1, row)   # 插在 stretch 之前
            if not providers:
                # 空清单要说清是「没拉到」还是「没有可配的 provider」（原来是一片空白）。
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
        # 不 wait()：线程已跑完，quit() 让它收尾即可（不在 GUI 线程里阻塞等）。
        self._load_thread.quit()

    # ── 窗口事件 ──

    def mousePressEvent(self, ev) -> None:
        # 拖拽热区按卡片高度取比例（原来写死 140px，换尺寸/换字号就错位）
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
        # 遮罩只在尺寸真的变了才重设：每次 resize 都 setMask = 每次都强制窗口重新合成。
        if getattr(self, "_mask_size", None) == self.size():
            return
        self._mask_size = self.size()
        p = QPainterPath()
        p.addRoundedRect(QRectF(self.rect()), CARD_RADIUS, CARD_RADIUS)
        self.setMask(QRegion(p.toFillPolygon().toPolygon()))

    def showEvent(self, ev) -> None:
        # 先算好居中位置再交给基类显示（原来是显示完再 move 一次，肉眼就是窗口跳一下）。
        screen = self.screen() or QApplication.primaryScreen()
        if screen:
            fg = self.frameGeometry()
            fg.moveCenter(screen.availableGeometry().center())
            self.move(fg.topLeft())
        super().showEvent(ev)

    # ── 保存（直接写 openclaw.json → Gateway 热重载）──

    class _OnboardWorker(QThread):
        """后台逐个 onboard provider，不阻塞 UI。"""
        progress = Signal(str)
        finished = Signal(list)  # list[str] — 失败的 pid 列表

        def __init__(self, patches: dict[str, dict[str, str]]):
            super().__init__()
            self._patches = patches

        def run(self):
            failed: list[str] = []
            for pid, pinfo in self._patches.items():
                self.progress.emit(pid)
                # ── 先落 DB（本地文件，不依赖 WSL）──
                _sync_provider_key_to_db(pid, pinfo["apiKey"])
                ok, msg = _write_wsl_provider_key(
                    pid, pinfo["apiKey"], pinfo.get("package", ""), pinfo.get("auth_choice", ""),
                    pinfo.get("base_url", ""), pinfo.get("model_id", ""))
                if not ok:
                    failed.append(f"{pid}: {msg[:80]}")
            # 不再在后台线程重启 Gateway——统一由主线程 _on_worker_done 触发
            # browser 的统一重启入口（重启→探活→reload），避免多入口、避免信号链断裂
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
            # 点了保存却什么都不发生 = 像没反应：明确提示要先填 key。
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
        # 进度 ≠ 完成：原来按 pid 就把该行标成「已就绪」，其实 key 还没写成功。
        # 这里只保持「正在配置…」，成不成由 _on_worker_done 说了算。
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
            # 失败要说清是哪一条、为什么（原来只把 pid 收进 failed，界面只看到按钮回弹）。
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
        # 收拢：配 key 后触发 browser 统一重启入口（重启→探活→reload），
        # 不再依赖 setup_completed 信号链（parent 链可能断）。
        self._restart_browser()
        self.setup_completed.emit(new_token)
        # 不再 QTimer 定时关窗（用户可能来不及看结果，而且「定时关窗 + 整页重载」
        # 挨在一起最容易看见闪）：把底部那颗按钮改成「关闭」，由用户自己收尾。
        if getattr(self, "_close_btn", None) is not None:
            self._close_btn.setText(tr("ui.btn.close"))

    def _restart_browser(self) -> None:
        """配置保存后的收尾：不碰网关，只如实记账（2026-10-19 定稿）。

        2026-10-17 令：只有 sidebar 那个手动重启能动网关，装插件/改配置一律让路。
        原来这里调 browser._restart_gateway()，闸会拦下并只记"让路"，界面却显示
        "已保存" ⇒ 等于假装重启过。现在把话说清：配置已落盘，下次网关重启后生效。
        """
        try:
            from main.main import stk
            stk("provider_setup.restart",
                "配置已保存；按令不自动重启网关（插件/配置在下次网关重启后生效）", ok=True)
        except Exception:
            pass

    def _open_advanced_config(self) -> None:
        """打开高级配置子页面（增量化追加）。"""
        import traceback as _tb
        try:
            from ui.web.advanced_config import AdvancedConfigDialog
            dlg = AdvancedConfigDialog(parent=self.window() if self.window() else self)
            # ── 关闭配置页时（若有保存过）才 reload，不是保存瞬间 ──
            dlg.reload_requested.connect(self._reload_browser)
            dlg.exec()
        except Exception as e:
            # ── 桩子：异常详情落 stake.log，不再只弹框吞掉 ──
            try:
                from main.main import stk
                stk("advcfg.open", f"exception {type(e).__name__}: {e}", ok=False)
                stk("advcfg.open.traceback", _tb.format_exc()[-2000:], ok=False)
            except Exception:
                pass
            QMessageBox.warning(self, tr("ui.browser.notice"), tr("ui.provider.cannot_open_adv", e=e))

    def _reload_browser(self) -> None:
        """高级配置关闭（且本次保存过）时，收拢到 browser 的统一入口。

        走 _request_reload（统一重载车道：页面已就绪就**跳过**整页重载、同一波
        请求只重载一次 ⇒ 少刷、刷新不可见）；父窗没有该入口才退回 _restart_gateway。
        """
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
            # models.providers 由 Gateway 热重载 ⇒ 保存后不重启网关。
            else:
                stk("advcfg.reload", "browser not found, skip reload", ok=False)
        except Exception as e:
            from main.main import stk
            stk("advcfg.reload", f"reload failed: {e}", ok=False)


# ═══════════════════════════════════════════════
# 公开 API
# ═══════════════════════════════════════════════

def provider_setup_state() -> str:
    """三态判定：configured / unconfigured / unknown。

    unknown = CLI 拿不到（Gateway 正在重启、生命周期锁被占、网络故障）。
    这时**不许**当成"没配过"去弹配置卡 —— 那是误报（2026-10-28 定）。
    判据来源不变：官方 openclaw models list --json 里有没有 available=true。
    """
    count, known = _count_available_models()
    if not known:
        return "unknown"
    return "configured" if count > 0 else "unconfigured"


def needs_provider_setup() -> bool:
    """兼容旧调用：只有**确定没配**才 True；unknown 不弹卡（交调用方轮询重试）。"""
    return provider_setup_state() == "unconfigured"


def _count_available_models() -> tuple[int, bool]:
    """统计 available=true 的模型数量，并明确回答"这次拿到确定答案了吗"。

    返回 (count, known)。known=False 表示 CLI 不可用/输出不可解析 ——
    此时 count 无意义，调用方必须按 unknown 处理，不能当 0 用。
    """
    models_list = _cli_usage("models", "list")
    if not models_list:
        return (0, False)
    rc, out, err = _run_wsl(
        f'wsl -d {WSL_DISTRO} -u root -- bash -c '
        f'"export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; '
        f'{models_list} --json 2>/dev/null"'
    )
    if rc != 0 or not out.strip():
        # 这里返回 0 会被 needs_provider_setup() 当成「没配过」→ 弹配置卡。Gateway/CLI
        # 不可用时那是误报，至少落一笔账，别让网络故障无声无息变成「未配置」。
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
