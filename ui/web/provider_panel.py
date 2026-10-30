"""
Rheia Provider 完整模式面板
====================================
通过自定义区「Provider 控制台」条目进入的 QDialog 完整管理面板。

每行展示 provider 状态灯（绿/黄/灰），点击展开可配 API key 或启用/禁用。
数据源：mani.json（provider 元数据）+ openclaw.json（已配置 key），交叉对比。
只写 models.providers.<id>.apiKey 和 enabled，不动其他字段。

架构边界：
- 完整模式是 provider_setup.py 快速模式的超集，独立文件
- 快速模式保留不动（首次无 key 时自动弹出）
"""

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

# 确保能 import kernel 和同级模块
_proj = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _proj not in sys.path:
    sys.path.insert(0, _proj)

# ── 主题（独立于 PySide6，允许无 GUI 导入）──
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
    QDialog = QWidget = QFrame = QScrollArea = QLabel = QLineEdit = object  # type: ignore
    QPushButton = QCheckBox = QApplication = QSizePolicy = object  # type: ignore
    QHBoxLayout = QVBoxLayout = QMessageBox = QTimer = object  # type: ignore
    Qt = type("_QtStub", (), {})  # type: ignore
    Signal = object  # type: ignore
    QObject = object  # type: ignore
    QThread = object  # type: ignore


# ═══════════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════════

WSL_DISTRO = os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu"
GATEWAY_PORT = 18789
try:
    from ui.i18n import tr
except ImportError:
    tr = lambda key, **fmt: key

_LOCALDATA = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia")
_MANIFEST_PATH = os.path.join(_proj, "kernel", "large", "mani.json")

# ── 视觉 ──
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

# 折叠/展开两种外观预先算好：不在点击回调里现拼 CSS（每次 setStyleSheet 都要重新
# 解析样式表；和 setVisible/改箭头挤在同一帧，叠在唯一可见的 Web 视图上就是一次闪）。
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


# ═══════════════════════════════════════════════════════════════════
# WSL helpers（直接实现，不依赖 provider_setup 内部函数）
# ═══════════════════════════════════════════════════════════════════

def _sh_single_quote(value: str) -> str:
    """把值包成 shell 单引号字面量（内部单引号按 '\\'' 转义）。

    key 里带引号时不再破坏命令结构（原来是把值直接拼进 '...'）。
    """
    return "'" + value.replace("'", "'\\''") + "'"


def _run_wsl(cmd: str) -> tuple[int, str, str]:
    """在 WSL 里同步跑一条命令，返回 (rc, stdout, stderr)。

    ⚠ 壳层不留时间闸：不传 timeout（命令返回即信号），
    原先那个「签名留着只为兼容调用点、值不生效」的死 timeout 参数已去掉。
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


def _to_wsl_path(win: str) -> str:
    if len(win) > 1 and win[1] == ":":
        return f"/mnt/{win[0].lower()}{win[2:].replace(chr(92), '/')}"
    return win.replace("\\", "/")


def _read_wsl_config() -> dict[str, Any]:
    rc, out, _ = _run_wsl(
        f'wsl -d {WSL_DISTRO} -u root -- bash -c "cat ~/.openclaw/openclaw.json 2>/dev/null"')
    if rc == 0 and out.strip():
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            pass
    return {}


def _cli_usage(cli_id: str, action: str, option: str = "") -> str:
    """从 mani 的 clis 查「这个动作该调哪个命令」。查不到返回空串，不猜。"""
    from main.main import cli_usage
    return cli_usage(cli_id, action, option)


def _onboard_provider(pid: str, key: str, auth_choice: str = "") -> tuple[bool, str]:
    """官方 provider 通过 openclaw onboard --auth-choice <value> 完整配置。

    官方语义（providers/deepseek.md Getting started）：onboard 是完整注册——写 auth
    + 设默认模型 + 发布模型目录到 Gateway 运行时。paste-api-key 只写 auth profile、
    不发布模型目录，导致 models list 有（静态目录）但 infer/聊天 Unknown model（实测）。

    auth_choice 从 mani 长出来（spider 提取官方文档 --auth-choice 值），不硬编。
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
        stk("provider_panel.onboard", f"{pid} rc={rc} msg={msg[:120]!r}", ok=rc == 0)
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

    # 幂等：插件已装则不重装、不 restart
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

    # 官方铁律：plugins install 之后要 gateway restart 才会 loaded —— 但这里
    # 不再自己起独立重启入口：由调用方（面板保存链发 restart_requested / 卡片）
    # 走 browser 的统一入口（_restart_gateway：生命周期锁+探活+reload），消除多入口。

    return (ok, msg)


def _write_wsl_provider_key(pid: str, key: str, package: str = "", auth_choice: str = "") -> tuple[bool, str]:
    """写入 provider key，分两条路径：
      - 官方 provider（manifest 有插件包名 package）→ 先装插件再 openclaw onboard
      - 自定义 provider（无 package）→ openclaw config set models.providers.<id> --merge

    判据用 package（plugin.package 非空 = official external 插件），插件存在就行。
    auth_choice 从 mani 长出来（官方 --auth-choice 值），传给 onboard 用。
    """
    if package:
        # 先装插件（official external 必须有插件才能发布模型），再配 key
        _install_provider_plugin(package)
        return _onboard_provider(pid, key, auth_choice)
    import json as _json
    payload = _json.dumps({"apiKey": key}, ensure_ascii=False)
    config_set = _cli_usage("config", "set")
    if not config_set:
        return (False, tr("ui.ppanel.method_not_declared"))
    # payload 是 JSON（自带双引号），直接拼进 bash 双引号串会破坏结构 —— 改走
    # base64（只含 [A-Za-z0-9+/=]，不经过任何引号层），到 WSL 里再解回文件。
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


def _sync_token_to_local(token: str) -> None:
    """把新 token 写入本地缓存 gateway_config.json（供 main/main.py 读取）。

    注意：token 是 gateway 的运行时凭据，官方权威来源是 openclaw.json 的
    gateway.auth.token。token 不进 baseline_pool.db（不进备份），只做本地缓存，
    避免「备份旧 token → 恢复时覆写新 token」导致的失联。
    """
    try:
        os.makedirs(_LOCALDATA, exist_ok=True)
        cfg = os.path.join(_LOCALDATA, "gateway_config.json")
        # ⚠ 2026-10-19 修（真 bug）：整文件覆盖会抹掉 gpu_render 等键；改成读-改-写。
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
    """把 provider API key 备份到 baseline_pool.db credentials 表。

    重启后 browser.py 的 _check_provider_setup 从 DB 读到 key→写入 Gateway，
    不需要用户重新输入。key 以 provider 维度存储：credentials<pid>_api_key。
    """
    try:
        from kernel.scout import setCredential, ensureRecoveryTables
        ensureRecoveryTables()
        setCredential(f"{pid}_api_key", key, pid)
    except Exception as e:
        # 原来连桩都不打（彻底静默）——落盘账要能看出「DB 那份没写成」。
        try:
            from main.main import stk
            stk("provider_panel.db_sync", f"{pid} setCredential failed: {type(e).__name__}: {e}", ok=False)
        except Exception:
            pass


def _get_gateway_token() -> str:
    cfg = _read_wsl_config()
    return (cfg.get("gateway") or {}).get("auth", {}).get("token", "")


def _fetch_provider_models(pid: str) -> list[dict[str, Any]]:
    """通过 openclaw models list --provider <id> --json 获取 provider 的真实模型列表。

    mani.json 中大量 provider 的 models 数组为空，因为模型目录由各 provider
    插件在运行时动态返回。对已配 API key 的 provider，从 Gateway 拉取真实目录。
    """
    models_list = _cli_usage("models", "list")
    if not models_list:
        return []
    # 2>/dev/null（原来 2>&1 会把 stderr 混进 JSON 里，解析失败又静默返回空）。
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
        # 解析失败的静默 return [] 会让人以为「这个 provider 没有模型」——落桩。
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


def _model_short_name(ref: str) -> str:
    """从 'deepseek/deepseek-v4-flash' 取 'deepseek-v4-flash'。"""
    return ref.rsplit("/", 1)[-1] if "/" in ref else ref


# ═══════════════════════════════════════════════════════════════════
# 数据加载：mani.json + openclaw.json 交叉对比
# ═══════════════════════════════════════════════════════════════════

def _stake_load(tag: str, msg: str, ok: bool = True) -> None:
    """检测桩：provider 控制台后台加载的进度打点。"""
    try:
        from main.main import stk
        stk(f"provider_panel.load.{tag}", msg, ok=ok)
    except Exception:
        pass


class _ProviderLoadWorker(QObject):
    """后台线程加载 provider 列表，加载完发 loaded 信号回主线程。"""
    loaded = Signal(list)

    def run(self) -> None:
        try:
            providers = _load_provider_list()
            self.loaded.emit(providers)
        except Exception as _e:
            _stake_load("failed", f"{type(_e).__name__}: {_e}", ok=False)
            self.loaded.emit([])


def _load_provider_list() -> list[dict[str, Any]]:
    """从 mani.json 加载 provider 列表，取名称/模型/环境变量等信息。

    模型列表来源优先级：
      1. mani.json entries.providers.<p>.models（静态目录）
      2. openclaw models list --provider <id> --json（Gateway 动态目录）
      3. 空列表（无可用数据）
    """
    # 先读取 Gateway 侧的 openclaw.json 判断哪些 provider 有 key
    wsl_cfg = _read_wsl_config()
    cfg_providers = wsl_cfg.get("models", {}).get("providers", {})

    # 对已配 key 的 provider 串行拉取动态模型目录。
    # 之前用 ThreadPoolExecutor 并发 8 个 WSL subprocess，在 Gateway 不稳定时
    # 触发 C 层段错误（shell=True + 并发 WSL 的底层句柄竞态），进程直接崩溃。
    # 改成串行：已配 key 的 provider 数量很少，串行的额外耗时远小于崩溃风险。
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
            # 不静默丢：这类 provider 不属「填 API key」流程，记一笔落盘账，
            # 免得界面「没有这个 provider」查无实据。
            _stake_load("skip", f"{pid} auth.kind={kind!r} 不属填 key 流程，跳过", ok=True)
            continue

        # 环境变量（从 manifest 长出，无硬编兜底表）
        env_vars = auth.get("env_vars") or []
        env = env_vars[0].get("var", "") if env_vars else ""

        # ── 模型列表：动态源优先 ──
        dm = dynamic_models.get(pid) or []
        static_models = p.get("models") or []
        models = dm if dm else static_models

        # 当前配置状态
        pcfg = cfg_providers.get(pid, {})
        pcfg = pcfg if isinstance(pcfg, dict) else {}
        has_key = bool((pcfg.get("apiKey") or pcfg.get("api_key") or "").strip())

        # 如果有动态模型列表，提取 model_tags
        model_tags: list[str] = []
        for m in models:
            tag = m.get("display_name") or m.get("name") or m.get("ref") or m.get("id", "")
            if not tag:
                continue
            short = _model_short_name(tag)
            # 只显示非废弃模型
            if m.get("status") != "deprecated":
                model_tags.append(short)

        if len(model_tags) > 6:
            model_tags = model_tags[:6]
            model_tags.append(f"+{len(models) - 6}")
        enabled = pcfg.get("enabled", True) if has_key else None  # 无 key 时不判断 enabled

        # 状态灯
        if has_key:
            if enabled is not False:
                status = "green"   # 已配置且启用
            else:
                status = "gray"    # 已配置但禁用
        elif kind == "api_key":
            status = "yellow"      # 需要 API key
        else:
            status = "gray"        # 不可配置

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

    # 不硬编排序优先级——按 manifest 原顺序（spider 按官方文档顺序产出）。
    return result


# ═══════════════════════════════════════════════════════════════════
# Provider 行 — 可展开/收起
# ═══════════════════════════════════════════════════════════════════

class _ProviderRow(QWidget):
    """单行 provider：折叠展示名称+模型标签+状态灯；展开展示 API key 输入+模型列表。"""

    provider_saved = Signal(str)  # token
    # ⚠⚠ 2026-10-17 逐行审计修：这一行以前**没有** —— 而下面保存成功那条路会
    #   `self.restart_requested.emit()`（那是 ProviderPanel 的信号）⇒ 装了插件的 provider
    #   一保存就 AttributeError，保存按钮永久禁用、状态复位整段不跑。
    #   行不跨类发信号：行发 `restart_needed`，由面板转成自己的 `restart_requested`。
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

        # ── 折叠头部 bar ──
        self._bar = QFrame()
        self._bar.setCursor(Qt.CursorShape.PointingHandCursor)
        self._bar.setFixedHeight(44)
        self._bar.setStyleSheet(_BAR_STYLE_CLOSED)
        bar_lo = QHBoxLayout(self._bar)
        bar_lo.setContentsMargins(10, 4, 10, 4)
        bar_lo.setSpacing(8)

        # 名称
        nm = QLabel(self._info["name"])
        nm.setStyleSheet(f"color:{_TEXT_PRIMARY};font-size:13px;font-weight:600;background:transparent;")
        nm.setMaximumWidth(140)
        nm.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bar_lo.addWidget(nm)

        # 模型标签（折叠态显示前几个）
        tags_text = ", ".join(self._info["model_tags"][:2]) if self._info["model_tags"] else ""
        if len(self._info["model_tags"]) > 2:
            tags_text += f" +{len(self._info['model_tags']) - 2}"
        self._tags_label = QLabel(tags_text)
        self._tags_label.setStyleSheet(f"color:{_TEXT_HINT};font-size:11px;background:transparent;")
        self._tags_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._tags_label.setWordWrap(False)
        bar_lo.addWidget(self._tags_label, stretch=1)

        # 状态灯
        dot_color = {"green": _GREEN, "yellow": _YELLOW, "gray": _GRAY}.get(self._info["status"], _GRAY)
        self._dot = QLabel("●")
        self._dot.setFixedWidth(18)
        self._dot.setStyleSheet(f"color:{dot_color};font-size:14px;background:transparent;")
        self._dot.setToolTip({"green": tr("ui.ppanel.configured"), "yellow": tr("ui.ppanel.need_key"), "gray": tr("ui.ppanel.disabled")}.get(self._info["status"], ""))
        bar_lo.addWidget(self._dot)

        # 展开箭头
        self._arrow = QLabel("▸")
        self._arrow.setFixedWidth(14)
        self._arrow.setStyleSheet(_ARROW_STYLE_CLOSED)
        bar_lo.addWidget(self._arrow)

        self._outer.addWidget(self._bar)
        self._bar.mousePressEvent = self._on_bar_click

        # ── 展开区（初始隐藏）──
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

        # ── 模型列表（已废弃模型暗色显示）──
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

        # ── 分隔 ──
        sep = QLabel("")
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{_BORDER};")
        lo.addWidget(sep)

        # ── 配置行 ──
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

        # 启用/禁用
        self._enabled_cb = QCheckBox(tr("ui.ppanel.enable"))
        self._enabled_cb.setChecked(info.get("enabled", True) if info["has_key"] else False)
        self._enabled_cb.setEnabled(info["has_key"])
        self._enabled_cb.setStyleSheet(f"""
            QCheckBox {{ color:{_TEXT_PRIMARY}; font-size:11px; spacing:4px; background:transparent; }}
            QCheckBox::indicator {{ width:14px; height:14px; }}
        """)
        cfg_row.addWidget(self._enabled_cb)

        # 保存按钮
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

        # 状态反馈
        self._status_lbl = QLabel("")
        self._status_lbl.setFixedWidth(18)
        self._status_lbl.setStyleSheet("background:transparent;font-size:12px;")
        cfg_row.addWidget(self._status_lbl)

        lo.addLayout(cfg_row)

        # 底部提示
        hint = QLabel(info["base_url"]) if info.get("base_url") else QLabel("")
        hint.setStyleSheet(f"color:{_TEXT_HINT};font-size:9px;background:transparent;padding:0 0 0 172px;")
        lo.addWidget(hint)

    def _on_bar_click(self, ev) -> None:
        self._expanded = not self._expanded
        # 展开/收起本身要改布局（这是功能），但样式一律用预置常量：不再当场拼 CSS、
        # 不再为同一次点击多跑一遍样式解析 —— 少一处重绘就少一次和 Web 视图抢帧。
        self._expand_widget.setVisible(self._expanded)
        self._arrow.setText("▾" if self._expanded else "▸")
        self._arrow.setStyleSheet(_ARROW_STYLE_OPEN if self._expanded else _ARROW_STYLE_CLOSED)
        self._bar.setStyleSheet(_BAR_STYLE_OPEN if self._expanded else _BAR_STYLE_CLOSED)

    def _on_save(self) -> None:
        info = self._info
        key_val = self._key_input.text().strip()

        if not key_val and not info["has_key"]:
            return  # nothing changed

        self._save_btn.setEnabled(False)
        self._save_btn.setText("...")
        self._status_lbl.setText("⟳")
        self._status_lbl.setStyleSheet(f"color:{_YELLOW};font-size:12px;background:transparent;")
        QApplication.processEvents()

        # ── 先落 DB（本地文件，不依赖 WSL）──
        # Gateway 失败 DB 已在，下次启动读到自动回填
        _sync_provider_key_to_db(info["id"], key_val)

        # 直接写 WSL openclaw.json models.providers.<id>.apiKey
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

        # 装过 provider 插件的，需要重启 Gateway 让插件加载（否则 enabled 但不 loaded）。
        # 不再在这里独立重启——发 restart_requested 信号，由 browser 统一入口
        # （带生命周期锁 + 探活 + reload）执行，消除独立重启入口与并发竞态。
        need_restart = bool((info.get("package") or "").strip())
        if need_restart:
            self._status_lbl.setToolTip(tr("ui.ppanel.restarting_gw"))
            QApplication.processEvents()

        # 同步 token
        new_token = _get_gateway_token()
        if new_token:
            _sync_token_to_local(new_token)

        # 通知 browser 页面刷新（Gateway 已重载，auth store 已初始化）
        self.provider_saved.emit(new_token or "")
        # 装了插件 → 通知 browser 统一重启入口（带锁+探活+reload）
        if need_restart:
            self.restart_needed.emit()   # 行自己的信号；面板收到后转成 restart_requested（走 browser 统一入口）

        # 更新 UI 状态
        self._status_lbl.setText("OK")
        self._status_lbl.setStyleSheet(f"color:{_GREEN};font-size:12px;background:transparent;")
        self._save_btn.setEnabled(True)
        self._save_btn.setText(tr("ui.btn.save"))

        # 更新状态灯
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

        # 更新内存状态
        self._info["has_key"] = True
        self._info["enabled"] = enabled
        self._info["status"] = "green" if enabled else "gray"
        self._enabled_cb.setEnabled(True)


# ═══════════════════════════════════════════════════════════════════
# ProviderPanel QDialog
# ═══════════════════════════════════════════════════════════════════

class ProviderPanel(QDialog):
    """Provider 完整模式面板 — 自定义区「Provider 控制台」入口。

    展示所有 provider 的状态灯、模型标签、API key 配置和启用/禁用。
    provider_saved 信号在成功保存后发出，让 browser 知道配置已更新无需再弹窗。
    """

    provider_saved = Signal(str)  # token
    restart_requested = Signal()  # 装了插件需要重启 Gateway 时发出，走 browser 统一入口

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

        # ── 标题 ──
        title = QLabel(tr("ui.ppanel.title"))
        title.setStyleSheet(f"color:{_TEXT_PRIMARY};font-size:16px;font-weight:bold;")
        lo.addWidget(title)

        desc = QLabel(tr("ui.ppanel.desc"))
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color:{_TEXT_HINT};font-size:11px;line-height:1.5;")
        lo.addWidget(desc)

        # ── 滚动区 ──
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sc_content = QWidget()
        sc_content.setStyleSheet("background:transparent;")
        sc_lo = QVBoxLayout(sc_content)
        sc_lo.setContentsMargins(0, 0, 0, 0)
        sc_lo.setSpacing(6)

        # 加载态标签（后台加载 provider 列表时显示，避免假死）
        self._loading_lbl = QLabel(tr("ui.ppanel.loading"))
        self._loading_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_lbl.setStyleSheet(f"color:{_TEXT_HINT};font-size:12px;padding:20px;")
        sc_lo.addWidget(self._loading_lbl)
        sc_lo.addStretch()
        sc.setWidget(sc_content)
        lo.addWidget(sc, stretch=1)

        # ── 底部 ──
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

        # 后台加载 provider 列表，避免主线程同步跑 WSL 命令导致假死
        self._load_sc_lo = sc_lo
        self._start_load_providers(sc_lo)

    def _start_load_providers(self, sc_lo) -> None:
        """后台线程加载 provider 列表，加载完回主线程填充。

        用 QThread + 原生 Signal（不是裸 threading.Thread + 局部 QObject）——
        后者信号丢投递、且闭包在非主线程操作 GUI，正是 provider_setup 修过的坑。
        """
        self._load_thread = QThread(self)
        self._load_worker = _ProviderLoadWorker()
        self._load_worker.moveToThread(self._load_thread)
        self._load_thread.started.connect(self._load_worker.run)
        self._load_worker.loaded.connect(self._on_providers_loaded)
        self._load_thread.start()
        _stake_load("started", "thread started")

    def _on_providers_loaded(self, providers: list) -> None:
        """主线程回调：填充 provider 行（GUI 操作必须在主线程）。"""
        _stake_load("loaded", f"{len(providers)} providers")
        sc_lo = self._load_sc_lo
        # 一批只重绘一次：整列行 + 撤加载标签一起做完再放行重绘 ——
        # 原写法每 insertWidget 一行就重排一次（首屏内容跳变 = 可见的动）。
        holder = sc_lo.parentWidget()
        if holder is not None:
            holder.setUpdatesEnabled(False)
        try:
            for p in providers:
                row = _ProviderRow(p)
                self._rows.append(row)
                row.provider_saved.connect(self.provider_saved)
                row.restart_needed.connect(self.restart_requested.emit)   # 行的"要重启"转成面板的（browser 统一入口）
                sc_lo.insertWidget(sc_lo.count() - 1, row)  # 插在 stretch 之前
            # 撤加载标签：先隐藏、延后销毁（原来 setParent(None) 当场触发布局重排）
            if hasattr(self, "_loading_lbl"):
                self._loading_lbl.hide()
                self._loading_lbl.deleteLater()
        finally:
            if holder is not None:
                holder.setUpdatesEnabled(True)
        # 底部摘要
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
        # 不 wait()：线程本已跑完，quit() 让它收尾即可，不在 GUI 线程里阻塞等
        # （原 wait() 无超时 = 主线程不可控等待）。
        self._load_thread.quit()
