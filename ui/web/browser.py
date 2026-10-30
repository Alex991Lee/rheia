"""
Built-in browser view — QWebEngineView wrapper that connects to Gateway.

Usage:
    browser = BuiltinBrowser(gateway_url="http://127.0.0.1:18789", token="...")
    browser.show()

webview.json provides window dimensions, UA, retry, and loading behavior.

状态指示灯语义（严格对齐 glory 三态）:
  green  #5a9e6f — connected     (连上了，glory.connected=true)
  yellow #d9a840 — retrying      (加载中/重试)
  red    #e85a4a — disconnected  (连接失败)
  gray   #6a5e52 — idle          (未开始 / 闲置)
"""

from __future__ import annotations

try:  # 全局静默子进程守卫（设计约定：加载/安装链不得弹控制台）
    from lib.no_window import install as _install_no_window
    _install_no_window()
except Exception:  # noqa: BLE001
    pass


import base64
import http.server
from functools import partial
import json
import logging
import os
import shutil
import subprocess
import sys
import threading

from PySide6.QtCore import QTimer, QUrl, Qt, Signal, QObject, QThread
from PySide6.QtGui import QFont, QDragEnterEvent, QDropEvent, QIcon
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineSettings, QWebEngineScript
try:
    from PySide6.QtWebEngineCore import QWebEngineLoadingInfo
except Exception:                                             # noqa: BLE001
    QWebEngineLoadingInfo = None
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# ── 高频模块导入缓存，避免每次热路径 import 走 machinery ──
# 这些模块加载开销极小（<1ms），全程稳定，提前导入消除 stutter
from main.main import stk  # noqa: E402 — 项目约定 lazy import，但 stk 被调 12 次，提上来零开销

# ── i18n 翻译函数（壳层 UI 文案统一走 tr）──
from ui.i18n import tr  # noqa: E402

logger = logging.getLogger(__name__)

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 发行版名一处定：env 可覆盖；不再逐处写死 "Ubuntu"。
_WSL_DISTRO = os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu"
_WEBVIEW_CFG_PATH = os.path.join(_BASE, "ui", "web", "webview.json")

# ── 用户身份持久化（头像 + 显示名，非 OpenClaw agent identity）──
_USER_IDENTITY_PATH = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia", "user_identity.json")

# ── GPU 渲染开关持久化（gateway_config.json 的 gpu_render 字段）──
_GW_CFG_PATH = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia", "gateway_config.json")


def _rheia_app_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _slot_license_ok(app: str = "") -> bool:
    """授权在不在：key 在，且没被绑到别的机器上（绑定判据在 persona.slot_discovery）。

    判不了就当没有 —— 界面宁可少挂一栏，不可白送。
    """
    try:
        app = app or _rheia_app_root()
        if app not in sys.path:
            sys.path.insert(0, app)
        from persona.slot_discovery import license_status
        ok, _why = license_status(app)
        return bool(ok)
    except Exception:                                             # noqa: BLE001
        return False


def _slot_seed_present(app: str = "") -> bool:
    try:
        app = app or _rheia_app_root()
        seed = (os.environ.get("RHEIA_PERSONAL_SLOT_SEED") or "").strip() or os.path.join(app, "RheiaPersonalSlot")
        return os.path.isfile(seed)
    except Exception:                                             # noqa: BLE001
        return False


def _personal_slot_available() -> bool:
    """付费模块在该不该出现 —— 照 10-27 产品树的三条判据：

    ① <app>/personal-slot/openclaw.plugin.json 已在（长出来了）；
    ② 环境变量 RHEIA_PERSONAL_SLOT 指着一个源目录；
    ③ 种子 + 授权 key 都在（还没长，点入口时现长）。
    key 绑在别的机器上 ⇒ 不算（授权判据见 persona.slot_discovery）。
    """
    try:
        app = _rheia_app_root()
        # 授权先行：没 key、或 key 绑在别的机器上 ⇒ 一律不出现。
        # （否则把别人的 personal-slot 拷过来就能看见入口、"免费"加载人格。）
        if not _slot_license_ok(app):
            return False
        if os.path.isfile(os.path.join(app, "personal-slot", "openclaw.plugin.json")):
            return True
        src = (os.environ.get("RHEIA_PERSONAL_SLOT") or "").strip()
        if src and os.path.isdir(src):
            return True
        return _slot_seed_present(app)
    except Exception:                                             # noqa: BLE001
        return False


def _gpu_render_enabled() -> bool:
    """GPU 渲染是否开启（默认开）。读 gateway_config.json 的 gpu_render 字段。

    默认中间态 --disable-gpu-compositing 仍是 GPU 渲染（光栅化走 GPU），开关默认勾选。
    gpu_render=false 才显式关闭。
    """
    try:
        if os.path.isfile(_GW_CFG_PATH):
            with open(_GW_CFG_PATH, "r", encoding="utf-8") as f:
                return json.load(f).get("gpu_render", True) is not False
    except Exception:
        pass
    return True


def _set_gpu_render(enabled: bool) -> None:
    """写 gpu_render 到 gateway_config.json（读-改-写，不覆盖 url/token）。"""
    try:
        os.makedirs(os.path.dirname(_GW_CFG_PATH), exist_ok=True)
        cfg = {}
        if os.path.isfile(_GW_CFG_PATH):
            try:
                with open(_GW_CFG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except (OSError, json.JSONDecodeError):
                cfg = {}
        cfg["gpu_render"] = enabled
        with open(_GW_CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _read_user_identity() -> dict:
    # 优先 baseline_pool.db
    try:
        from kernel.scout import getPref, ensureRecoveryTables
        ensureRecoveryTables()
        raw = getPref("user_identity")
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    try:
        with open(_USER_IDENTITY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _write_user_identity(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_USER_IDENTITY_PATH), exist_ok=True)
        with open(_USER_IDENTITY_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    # 同步到 baseline_pool.db
    try:
        from kernel.scout import setPref, ensureRecoveryTables
        ensureRecoveryTables()
        setPref("user_identity", json.dumps(data, ensure_ascii=False))
    except Exception:
        pass


# ── 用户头像本地 http 服务 ──────────────────────────────────────────
# 官方 Control UI 的用户头像 mu() 校验只认 URL（≤2MB）或 ≤16 字符短串，
# data URI（base64 大串）会被静默丢弃 → 头像必须给 http:// URL。
# 壳层起一个仅监听 127.0.0.1 的静态服务，serve %PROGRAMDATA% 的 avatars 目录。
_AVATAR_HTTP_PORT: int = 0
_AVATAR_HTTP_LOCK = threading.Lock()


class _AvatarHTTPHandler(http.server.SimpleHTTPRequestHandler):
    """只服务本机 avatars 目录的静态文件（127.0.0.1，进程退出即停）。"""

    def log_message(self, *args):
        pass  # 静默


def _avatar_base_url() -> str:
    """确保头像 http 服务在跑，返回 base（http://127.0.0.1:<port>）；失败返回空串。"""
    global _AVATAR_HTTP_PORT
    if _AVATAR_HTTP_PORT:
        return f"http://127.0.0.1:{_AVATAR_HTTP_PORT}"
    with _AVATAR_HTTP_LOCK:
        if _AVATAR_HTTP_PORT:
            return f"http://127.0.0.1:{_AVATAR_HTTP_PORT}"
        avatars_dir = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia", "avatars")
        try:
            os.makedirs(avatars_dir, exist_ok=True)
            handler = partial(_AvatarHTTPHandler, directory=avatars_dir)
            srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
            _AVATAR_HTTP_PORT = srv.server_address[1]
            threading.Thread(target=srv.serve_forever, daemon=True, name="avatar-http").start()
            return f"http://127.0.0.1:{_AVATAR_HTTP_PORT}"
        except Exception:
            return ""


# ── Control UI 官方路由表 ──
# path 值从官方 web/urls.md 长出来的契约读（scout/filter → mani session_contract.routes）。
# key/i18n/primary/is_local/emblem 是主项目 UI 层业务属性，保留硬编。
# 「主项目 key → 官方 page 名」是业务绑定（和 i18n key 同性质），非官方数据。
_NAV_ROUTES: list[dict] = [
    {"key": "chat",            "i18n": "ui.nav.chat",            "path": "/chat",                       "primary": True},
    {"key": "ai_agents",       "i18n": "ui.nav.ai_agents",       "path": "/settings/ai-agents",         "primary": True},
    {"key": "channels",        "i18n": "ui.nav.channels",        "path": "/settings/channels",          "primary": True},
    {"key": "skills",          "i18n": "ui.nav.skills",          "path": "/skills",                     "primary": False},
    {"key": "automation",      "i18n": "ui.nav.automation",      "path": "/cron",                       "primary": False},
    {"key": "sessions",        "i18n": "ui.nav.sessions",        "path": "/sessions",                   "primary": False},
    {"key": "mcp",             "i18n": "ui.nav.mcp",             "path": "/settings/mcp",               "primary": False},
    {"key": "appearance",      "i18n": "ui.nav.appearance",      "path": "/settings/appearance",        "primary": False},
    {"key": "provider_setup",  "i18n": "ui.nav.provider_setup",  "path": "",                             "primary": False, "is_local": True},
    {"key": "cn_channels",     "i18n": "ui.nav.cn_channels",     "path": "",                             "primary": False, "is_local": True},
    {"key": "memory",          "i18n": "ui.nav.memory",          "path": "",                             "primary": False, "is_local": True},
]

# 主项目导航 key → 官方 route table 的 page 名（业务绑定，非官方数据）
_NAV_ROUTE_PAGES = {
    "chat": "Chat",
    "ai_agents": "AI and agents",
    "channels": "Channels",
    "skills": "Skills",
    "automation": "Automations",
    "sessions": "Sessions",
    "mcp": "MCP",
    "appearance": "Appearance",
}


def _nav_routes() -> list[dict]:
    """返回导航路由：path 从 mani 官方 route table 长出（去硬编），
    其余 UI 属性保留 _NAV_ROUTES 的硬编。官方改 path，scout 重跑后自动跟走。
    mani 未长出契约时回退 _NAV_ROUTES 的 path 兜底。
    """
    # 从 mani 读官方 route table，建立 page 名 → path 映射
    page_to_path: dict[str, str] = {}
    try:
        import json as _json
        _proj = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        with open(os.path.join(_proj, "kernel", "large", "mani.json"), "r", encoding="utf-8") as f:
            web_searches = (_json.load(f).get("entries") or {}).get("web_searches") or []
        for e in web_searches:
            if isinstance(e, dict) and e.get("id") == "urls":
                contract = e.get("session_contract")
                if isinstance(contract, dict):
                    for rt in contract.get("routes", []):
                        page = rt.get("page", "")
                        p = rt.get("path", "")
                        if page and p:
                            page_to_path[page] = p
                break
    except Exception:
        pass

    if not page_to_path:
        return _NAV_ROUTES  # mani 还没长出契约，回退硬编

    # 用官方 page 的 path 覆盖主项目导航的 path（官方改 path 自动跟走）
    merged = []
    for route in _NAV_ROUTES:
        r = dict(route)
        page = _NAV_ROUTE_PAGES.get(route["key"])
        if page and page in page_to_path:
            r["path"] = page_to_path[page]
        merged.append(r)
    return merged


_SIDEBAR_BG = "#1a1a1a"
_SIDEBAR_HOVER = "#333333"
_SIDEBAR_ACTIVE = "#444444"
_SIDEBAR_TEXT = "#cccccc"
_SIDEBAR_ACCENT = "#f37021"
_SIDEBAR_WIDTH = 56
_SIDEBAR_EXPANDED = 160


def _load_webview_config() -> dict:
    try:
        with open(_WEBVIEW_CFG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _isChinese() -> bool:
    try:
        # 确保项目根目录在 sys.path 中以解析 ui.i18n
        _pkg_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if _pkg_root not in sys.path:
            sys.path.insert(0, _pkg_root)
        from ui.i18n import currentLanguage
        return currentLanguage() == "zh"
    except Exception:
        return True


def _is_simplified_chinese() -> bool:
    """操作系统是否为简体中文（zh-CN）。非简体中文系统 → 抛弃国内频道模块。"""
    try:
        from main.main import is_simplified_chinese
        return is_simplified_chinese()
    except Exception:
        # 判断失败时保守：不挂载国内频道（宁可缺，不误挂）
        return False


# ── 导航按钮 ──

class _NavButton(QPushButton):
    def __init__(self, label: str, icon: str, parent=None):
        super().__init__(parent)
        self._label = label
        self._icon = icon
        self._expanded = False
        self.setCheckable(True)
        # ⚠ 2026-10-17 逐行审计（屏闪·少刷）：这张样式表**与折叠态无关** —— 原先每次
        #   _updateStyle() 都重设一遍（折叠/展开一次 × 每颗按钮 = 十几回 style 重解析 +
        #   repolish 重绘，打的正是唯一那块可见画面）⇒ 只在构造期设一次。
        self.setStyleSheet(f"""
            _NavButton {{
                background: transparent;
                color: {_SIDEBAR_TEXT};
                border: none;
                border-radius: 6px;
                font-size: 13px;
                text-align: left;
                padding-left: 8px;
            }}
            _NavButton:hover {{
                background: {_SIDEBAR_HOVER};
                color: #e0e0e0;
            }}
            _NavButton:checked {{
                background: {_SIDEBAR_ACTIVE};
                color: {_SIDEBAR_ACCENT};
                border-left: 2px solid {_SIDEBAR_ACCENT};
            }}
        """)
        self._updateStyle(False)

    def _updateStyle(self, collapsed: bool) -> None:
        self._expanded = not collapsed
        if collapsed:
            # 折叠：严格隐藏一切文案（不显示 emoji/图标），仅保留窄色块占位
            self.setText("")
            self.setToolTip(self._label)
            self.setFixedSize(40, 36)
        else:
            self.setText(f"  {self._label}")
            self.setToolTip("")
            self.setFixedHeight(36)
            self.setMinimumWidth(140)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


def _run_wsl_check(cmd: str) -> bool | None:
    """跑一条 WSL 命令：True/False = 命令成败；None = **壳自己的子进程没跑起来**。

    ⚠ 2026-10-17 逐行审计（缺陷）：原先只有 `_run_wsl_for_avatar()` 这一个 bool 口 ——
    它把「子进程异常」与「命令返回非 0」压成同一个 False ⇒ 调用方分不清「确实没装」
    和「CLI 根本没起来」，后者被误报成「没装」。这里把两者分开。

    ⚠ 命令走 **argv 直传（shell=False）**：不再拼 bash -c 的双引号串，
    路径/参数含空格或引号也不会断链。
    """
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    try:
        r = subprocess.run(
            ["wsl", "-d", "Ubuntu", "-u", "root", "--", "bash", "-c", cmd],
            capture_output=True,
            encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
            startupinfo=si,
        )
    except Exception:
        return None
    return r.returncode == 0


def _run_wsl_for_avatar(cmd: str) -> bool:
    """在 WSL 中执行一条 CLI 命令，返回是否成功。

    ⚠ 壳层不留时间闸：不把时间交给子进程，命令返回即信号。
    ⚠ 2026-10-17 逐行审计（缺陷）：签名上的 `timeout`（及其调用方传的 30 / 3）是**死参** ——
    收下就丢，调用方以为有 15s/30s 保护，实际无闸。已删除，语义与行为一致。
    """
    return _run_wsl_check(cmd) is True


def _run_wsl_out(cmd: str) -> str:
    """跑一条 WSL 命令并回它的 stdout（失败 ⇒ 空串）。

    ⚠ 2026-10-17 逐行审计（缺陷）：同 `_run_wsl_check` —— 改走 **argv 直传（shell=False）**，
    不再拼 bash -c 的双引号串（路径含空格/引号即断链）。
    """
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    try:
        r = subprocess.run(
            ["wsl", "-d", "Ubuntu", "-u", "root", "--", "bash", "-c", cmd],
            capture_output=True,
            encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
            startupinfo=si,
        )
        return r.stdout or ""
    except Exception:
        return ""


def _win_to_wsl_path(p: str) -> str:
    """把 Windows 盘路径换成 WSL 里的 /mnt/<盘>/…（给 WSL 内 cp 用）；已是 WSL 路径 ⇒ 原样。"""
    _p = str(p or "")
    if _p.startswith("/"):
        return _p
    if len(_p) > 1 and _p[1] == ":":
        return "/mnt/" + _p[0].lower() + "/" + _p[2:].replace(chr(92), "/").lstrip("/")
    return _p.replace(chr(92), "/")


def _slots_current_agent() -> str:
    """**当前该被改身份的那个号**。

    判据 = 壳自己记的槽账（`slot_window.json`）：活跃卡（`active_persona`）的 basename 对上哪条，
    就取哪条的 `agent_id`；对不上退回"最后插的那张"；一条都没有 ⇒ `main`。
    多 Agent 并存下"换头像/改名"改的必须是**这张卡自己的号**，不许写死 main。
    """
    try:
        from persona.indivi import read_slot_window
        _sw = read_slot_window() or {}
    except Exception:
        _sw = {}
    _card = ""
    try:
        from kernel.scout import getPref, ensureRecoveryTables
        ensureRecoveryTables()
        _raw = getPref("active_persona")
        if _raw:
            _card = os.path.basename(str((json.loads(_raw) or {}).get("filepath") or ""))
    except Exception:
        _card = ""
    if _card:
        for _s in (_sw.get("slots") or []):
            if isinstance(_s, dict) and os.path.basename(str(_s.get("card") or "")) == _card:
                _aid = str(_s.get("agent_id") or "")
                if _aid:
                    return _aid
    return str(_sw.get("agent_id") or "") or "main"


# 号 → 工作区 的进程内缓存（同一个号只问一次官方 CLI）
_AGENT_WS_CACHE: dict[str, str] = {}


def _agent_workspace_wsl(agent_id: str) -> str:
    """那个号的**工作区**（WSL 绝对路径）。

    真相源 = 官方 `agents list --json`（config 里 `agents.entries.<id>.workspace` 说了算）；
    读不到 ⇒ 官方默认 `~/.openclaw/workspace-<id>`。

    ⚠ 2026-10-17 逐行审计（缺陷）：这条查询原先在 **UI 线程同步跑子进程**、且失败
    **静默**回落默认路径（谁也查不出头像拷去了哪）。现在：①按号缓存，同一个号只问一次；
    ②解析失败 / 官方名单里查不到该号时**各留一行日志**，不再无声无息。
    """
    _id = str(agent_id or "").strip() or "main"
    _cached = _AGENT_WS_CACHE.get(_id)
    if _cached:
        return _cached
    try:
        _rows = json.loads(_run_wsl_out("openclaw agents list --json") or "{}")
    except Exception as _e:
        logger.warning("_agent_workspace_wsl: agents list --json 解析失败（%s）", _e)
        _rows = {}
    _list = _rows.get("agents") if isinstance(_rows, dict) else _rows
    for _a in (_list or []):
        if isinstance(_a, dict) and str(_a.get("id") or "") == _id:
            _ws = str(_a.get("workspace") or "")
            if _ws.startswith("~"):
                _ws = "/root" + _ws[1:]
            if _ws:
                _AGENT_WS_CACHE[_id] = _ws
                return _ws
            break
    logger.warning(
        "_agent_workspace_wsl: 官方 agents list 里查不到 %s 的工作区 ⇒ 回落默认路径", _id)
    return f"/root/.openclaw/workspace-{_id}"


# ── 未安装技能侧边栏组件 ──

class _FailedSkillsWidget(QWidget):
    """侧边栏尾部：显示 bonus skills 安装失败的 skill slug。

    初始隐藏。安装线程发现有失败项时发 signal → refresh() 读取
    failed_skills.json → 展开点击复制命令。
    """

    _FAILED_LIST = os.path.join(
        os.environ.get("PROGRAMDATA", ""), "Rheia", "failed_skills.json")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._expanded = False
        self._collapsed = True
        self._slugs: list[str] = []
        self._dirty = False      # 收拢期攒下的"名单变了"，等用户展开再建（不动前台）

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(1)

        # ⚠ 未安装技能 header
        self._header_btn = QPushButton(tr("ui.browser.uninstalled_skills"))
        self._header_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header_btn.setFixedHeight(28)
        self._header_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {_SIDEBAR_ACCENT};
                border: none;
                border-radius: 4px;
                font-size: 12px;
                text-align: left;
                padding-left: 8px;
            }}
            QPushButton:hover {{ color: #ff8833; }}
        """)
        self._header_btn.clicked.connect(self._toggle)
        layout.addWidget(self._header_btn)

        # 子列表容器
        self._list_widget = QWidget()
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(8, 0, 0, 0)
        self._list_layout.setSpacing(2)
        self._list_widget.setVisible(False)
        layout.addWidget(self._list_widget)

        self.setVisible(False)

    def _load_failed(self) -> list[str]:
        # 优先 baseline_pool.db
        try:
            from kernel.scout import getRecoveryState, ensureRecoveryTables
            ensureRecoveryTables()
            raw = getRecoveryState("failed_skills")
            if raw:
                return json.loads(raw)
        except Exception:
            pass
        # fallback 旧 JSON 文件
        try:
            with open(self._FAILED_LIST, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

    def refresh(self) -> None:
        """显示逻辑（CLI 印证版）：问 WSL CLI 要已安装名单 ↔ 预装名单印证。

        预装名单里 CLI 确认真没装的才显示「未安装技能」；全印证上 → 不显示。
        CLI 查询失败（wsl 异常）回退 failed_skills.json 兜底（保持有提示）。
        单装壳接驳已有 OpenClaw 环境时，WSL 侧已装 skills/plugins 不被误报。
        后台线程查 CLI（不卡 UI），完成后切主线程建条目。
        """
        # ⚠ 2026-10-17 逐行审计（屏闪·刷新不可见）：只在**真的可见**时才翻不可见 ——
        #   原来无条件 setVisible(False)，即便它本来就藏着，也要在主线程动一次布局。
        if self.isVisible():
            self.setVisible(False)
        self._slugs = []
        import threading as _th
        _th.Thread(target=self._verify_async, daemon=True,
                   name="failed-skills-verify").start()

    def _verify_async(self) -> None:
        """后台：逐项问 CLI 验证预装名单，得出真缺列表。None=CLI 验证失败。"""
        missing: list[str] | None = None
        try:
            pre = self._load_preinstalled()
            if not pre:
                missing = []
            else:
                missing = [n for n in pre if not self._check_installed(n)]
        except Exception:
            missing = None
        from PySide6.QtCore import QTimer as _QtTimer2
        _QtTimer2.singleShot(0, lambda: self._apply_missing(missing))

    def _load_preinstalled(self) -> list[str]:
        """预装名单：单一真相 = main.py bonus_skills 分节的 _PREINSTALL_NAMES。"""
        try:
            from main.main import _PREINSTALL_NAMES
            return [str(x) for x in _PREINSTALL_NAMES]
        except Exception:
            return []

    def _check_installed(self, name: str) -> bool:
        """问 WSL：该预装项是否已装（skill 落点 test -d / plugin CLI list 印证）。

        ⚠ 2026-10-17 逐行审计（缺陷）：①grep 用 -F 固定串 + --（slug 里的 . 或 [ 不再
        被当正则）；②CLI **没跑起来**（`_run_wsl_check` 回 None）时**不判「没装」** ——
        宁可漏报，也不把「壳自己的子进程失败」算成「用户没装」（原来异常一律 return False）。
        """
        try:
            # 1) skill 落点（兼容两代）：@owner/slug 旧结构 + 扁平 slug 新结构
            #    （openclaw 2026.9.1 起装到 skills/<slug>）
            slug = name.split("/")[-1]
            _rc = _run_wsl_check(
                f"test -d \"$HOME/.openclaw/workspace/skills/{name}\" && echo OK "
                f"|| test -d \"$HOME/.openclaw/workspace/skills/{slug}\" && echo OK")
            if _rc is None or _rc:
                return True
            # 2) plugin：CLI list 印证（@openclaw/xxx 与裸名都试）
            try:
                from main.main import cli_usage
                plist = cli_usage("plugins", "list") or "openclaw plugins list"
            except Exception:
                plist = "openclaw plugins list"
            bare = name.split("/")[-1]
            for cand in (bare, bare.replace("-provider", "").replace("-plugin", "")):
                if not cand:
                    continue
                _rc2 = _run_wsl_check(f"{plist} 2>/dev/null | grep -qiF -- '{cand}'")
                if _rc2 is None or _rc2:
                    return True
        except Exception:
            # 连判定本身都出错 ⇒ 不误报成「没装」（宁可漏报）
            return True
        return False

    def _apply_missing(self, missing: list[str] | None) -> None:
        """主线程：按真缺名单构建子列表。missing=None（CLI 验证失败）→ 回退 failed_skills.json。

        ⚠⚠ **装链不得扰动前台**：收拢态**一个部件都不许动** ——
        原来先 `deleteLater()` 清空、再判断折叠 ⇒ 每次装机收尾都在主线程重建部件、翻可见性，
        打的正是唯一那块可见画面。现在：收拢态只记数据（`_dirty`），等用户**自己展开**侧栏再建。
        """
        if missing is None:
            missing = self._load_failed()
        _changed = list(missing) != list(self._slugs)
        self._slugs = list(missing)
        if getattr(self, '_collapsed', True):
            if _changed:
                self._dirty = True
            return
        if not _changed and self._list_layout.count():
            return                                       # 名单没变 ⇒ 不重建（零扰动）
        # 清空旧子按钮
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._slugs:
            self.setVisible(False)
            return

        from main.main import cli_usage
        cmd_prefix = cli_usage("skills", "install") + " " if cli_usage("skills", "install") else "openclaw skills install "

        for slug in self._slugs:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 2, 0, 2)
            row_layout.setSpacing(4)

            label = QLabel(slug)
            label.setStyleSheet(f"color: {_SIDEBAR_TEXT}; font-size: 11px; background: transparent;")
            row_layout.addWidget(label)

            copy_btn = QPushButton(tr("ui.btn.copy"))
            copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            copy_btn.setFixedSize(24, 20)
            copy_btn.setToolTip(f"{tr('ui.browser.copy_command')}: {cmd_prefix}{slug}")
            copy_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {_SIDEBAR_TEXT};
                    border: none;
                    border-radius: 2px;
                    font-size: 12px;
                }}
                QPushButton:hover {{ color: {_SIDEBAR_ACCENT}; }}
            """)
            full_cmd = f"{cmd_prefix}{slug}"
            copy_btn.clicked.connect(partial(QApplication.clipboard().setText, full_cmd))
            row_layout.addWidget(copy_btn)

            del_btn = QPushButton("×")
            del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            del_btn.setFixedSize(20, 20)
            del_btn.setToolTip(tr("ui.browser.remove_from_list"))
            del_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {_SIDEBAR_TEXT};
                    border: none;
                    border-radius: 2px;
                    font-size: 11px;
                }}
                QPushButton:hover {{ color: #e85a4a; }}
            """)
            del_btn.clicked.connect(partial(self._remove_slug, slug))
            row_layout.addWidget(del_btn)

            row_layout.addStretch()

            self._list_layout.addWidget(row)

        self.setVisible(True)
        if self._expanded:
            self._list_widget.setVisible(True)
            self._header_btn.setText(tr("ui.browser.uninstalled_skills_down"))
        else:
            self._list_widget.setVisible(False)
            self._header_btn.setText(tr("ui.browser.uninstalled_skills_right"))

    def _remove_slug(self, slug: str) -> None:
        """从失败列表中移除指定 slug 并刷新。"""
        current = self._load_failed()
        if slug in current:
            current.remove(slug)
        # 主存储：baseline_pool.db
        try:
            from kernel.scout import setRecoveryState, ensureRecoveryTables
            ensureRecoveryTables()
            setRecoveryState("failed_skills", json.dumps(current, ensure_ascii=False))
        except Exception:
            pass
        # fallback 旧 JSON 文件
        try:
            os.makedirs(os.path.dirname(self._FAILED_LIST), exist_ok=True)
            with open(self._FAILED_LIST, "w", encoding="utf-8") as f:
                json.dump(current, f, ensure_ascii=False, indent=2)
        except OSError:
            pass
        self.refresh()

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._list_widget.setVisible(self._expanded)
        self._header_btn.setText(tr("ui.browser.uninstalled_skills_down") if self._expanded else tr("ui.browser.uninstalled_skills_right"))

    def set_collapsed(self, collapsed: bool) -> None:
        """侧边栏折叠时完全隐藏，展开时才显示（有 failed slugs 时）。"""
        self._collapsed = collapsed
        if collapsed:
            self.setVisible(False)
        else:
            if getattr(self, "_dirty", False):
                self._dirty = False
                self._apply_missing(self._slugs)     # 用户**自己**展开了 ⇒ 此刻才建（前台是自己点的）
            self._list_widget.setVisible(self._expanded)
            self._header_btn.setText(
                tr("ui.browser.uninstalled_skills_down") if self._expanded else tr("ui.browser.uninstalled_skills_right"))
            self._header_btn.setToolTip("")
            # ⚠ 2026-10-17 逐行审计（屏闪·别动布局）：尺寸没变就别再 setFixedSize ——
            #   同一尺寸重设一样会触发布局重排。
            if self._header_btn.minimumWidth() != 148:
                self._header_btn.setFixedSize(148, 28)
            self.setVisible(bool(self._slugs))


# ── 侧边栏 ──

class _Sidebar(QWidget):
    # 用户身份（显示名/头像）变更信号 → BuiltinBrowser 监听并同步官方对话框
    identity_changed = Signal()

    def __init__(self, gateway_url: str, navigate_fn, parent=None):
        super().__init__(parent)
        self._gateway_url = gateway_url
        self._navigate = navigate_fn
        self._collapsed = True
        self._buttons: list[_NavButton] = []
        self._more_buttons: list[_NavButton] = []
        self._active_key: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 8, 6, 8)
        layout.setSpacing(2)

        zh = _isChinese()
        # ── 第一栏：Rheia（人格 / 拳头产品，独占一栏；文案硬编码，不走 i18n）──
        #   付费模块未安装 ⇒ 不挂这一栏（壳包不含付费件）；后买时由定时器动态挂上。
        self._rheia_btn = None
        if _personal_slot_available():
            _rheia_btn = _NavButton("Rheia", "")
            _rheia_btn._route_key = "persona"
            _rheia_btn.clicked.connect(
                lambda: self._dispatch_custom_action({"id": "persona", "type": "preset"})
            )
            layout.addWidget(_rheia_btn)
            self._buttons.append(_rheia_btn)
            self._rheia_btn = _rheia_btn
            # ⚠ **折叠态一律不许出文案** —— 建完立刻按当前折叠态定形，
            #   不等后头那圈循环（它只扫 self._buttons，任何提前 return 都会漏掉这里）。
            _rheia_btn._updateStyle(self._collapsed)

        self._sep = QLabel()
        self._sep.setFixedHeight(1)
        self._sep.setStyleSheet(f"background: {_SIDEBAR_HOVER}; margin: 4px 8px;")
        self._sep.setVisible(False)
        layout.addWidget(self._sep)

        for route in _nav_routes():
            # 原「主区」三键（对话/AI代理/频道）已并入第二栏，不再按 primary 分组
            # 国内频道模块门控：非简体中文系统直接抛弃该入口（不挂载）
            if route.get("key") == "cn_channels" and not _is_simplified_chinese():
                continue
            label = tr(route["i18n"])
            icon = route.get("emblem", "")
            btn = _NavButton(label, icon)
            btn._route_key = route["key"]
            btn.clicked.connect(
                partial((self._dispatch_local_action if route.get("is_local") else self._on_navigate), route))
            btn.setVisible(False)
            layout.addWidget(btn)
            self._more_buttons.append(btn)

        # ── 未安装技能提示（初始隐藏；位置由 _refresh_custom_area 动态插到
        #    自定义区之后、底部工具栏之前，不在此静态挂载）──
        self._failed_skills = _FailedSkillsWidget()

        # ── 自定义区 ──
        self._custom_sep = QLabel()
        self._custom_sep.setFixedHeight(1)
        self._custom_sep.setStyleSheet(f"background: {_SIDEBAR_HOVER}; margin: 4px 8px;")
        self._custom_sep.setVisible(False)
        layout.addWidget(self._custom_sep)

        self._custom_buttons: list[_NavButton] = []
        self._custom_slot: _NavButton | None = None

        # ── 底部工具栏：ID 菜单 | … 折叠 ──
        self._avatar_btn = QPushButton()
        self._avatar_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._avatar_btn.setFixedSize(28, 28)
        self._avatar_btn.setToolTip(tr("ui.browser.identity_settings"))
        self._avatar_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_SIDEBAR_HOVER};
                color: {_SIDEBAR_TEXT};
                border: 2px solid {_SIDEBAR_ACTIVE};
                border-radius: 14px;
                font-size: 11px;
                font-weight: bold;
                padding: 0px;
                text-align: center;
            }}
            QPushButton:hover {{ border-color: {_SIDEBAR_ACCENT}; color: {_SIDEBAR_ACCENT}; }}
            QPushButton::menu-indicator {{ width: 0px; image: none; }}
        """)
        self._avatar_btn.setText("ID")
        self._build_id_menu()
        self._refresh_avatar_button()  # 启动时加载已有用户头像
        layout.addWidget(self._avatar_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addSpacing(6)

        self._toggle_btn = QPushButton("…")
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.setFixedSize(28, 28)
        self._toggle_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {_SIDEBAR_TEXT};
                border: none;
                border-radius: 4px;
                font-size: 16px;
                font-weight: bold;
            }}
            QPushButton:hover {{ color: #e0e0e0; }}
        """)
        self._toggle_btn.clicked.connect(self._toggle)
        layout.addWidget(self._toggle_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # 从 custom_area_manager 读配置 → 渲染自定义区条目
        # 必须在 _avatar_btn 创建之后调用，因为 _refresh_custom_area 需要它的 layout index
        self._refresh_custom_area()

        self.setFixedWidth(_SIDEBAR_WIDTH)
        self.setStyleSheet(f"_Sidebar {{ background: {_SIDEBAR_BG}; }}")

        # 初始折叠：只清空 primary 按钮的文字（折叠态隐藏文案），不动任何可见性
        for btn in self._buttons:
            btn._updateStyle(True)

    def _on_navigate(self, route: dict) -> None:
        self._active_key = route["key"]
        self._navigate(route["path"])
        self._apply_active(self._active_key)

    def _apply_active(self, key: str | None) -> None:
        """按 key 精准高亮对应按钮，其他按钮全部熄灭。

        key 为空则全部熄灭（弹窗关闭后使用）。
        所有按钮都挂了 _route_key 属性，通过比较定位。
        """
        all_btns = self._buttons + self._more_buttons + self._custom_buttons
        if self._custom_slot:
            all_btns.append(self._custom_slot)
        for btn in all_btns:
            btn.setChecked(hasattr(btn, "_route_key") and btn._route_key == key)

    def _clear_active(self) -> None:
        """弹窗关闭后清除所有侧边栏高亮。"""
        self._active_key = None
        self._apply_active(None)

    def set_active(self, key: str) -> None:
        self._active_key = key
        self._apply_active(key)

    def _find_button_by_route_key(self, key: str) -> _NavButton | None:
        """在侧边栏三个按钮列表中按路由 key 查找按钮。"""
        all_btns = self._buttons + self._more_buttons + self._custom_buttons
        for btn in all_btns:
            if hasattr(btn, "_route_key") and btn._route_key == key:
                return btn
        return None

    # ── 本地面板分发 ──

    def _set_panel_open(self, open_: bool) -> None:
        """功能页打开期间禁用侧边栏所有按钮，关闭后恢复。

        用「禁用侧边栏」替代「模态 exec 阻塞」，既防叠加（开不了第二个），
        又不阻塞主窗口（客户仍能操作文档/复制 API key）。
        """
        all_btns = self._buttons + self._more_buttons + self._custom_buttons
        for btn in all_btns:
            btn.setEnabled(not open_)
        if self._custom_slot is not None:
            self._custom_slot.setEnabled(not open_)

    def _dispatch_local_action(self, route: dict) -> None:
        """侧边栏本地面板分发（is_local 条目）。

        弹窗不改变原生页的高亮：弹窗按钮临时加高亮，关闭后恢复。
        """
        key = route.get("key", "")
        try:
            from main.main import stk as _stk
            _stk("sidebar.local", f"点击本地面板入口 key={key}", ok=True)
        except Exception:
            pass
        self._apply_active(key)
        try:
            if key == "provider_setup":
                self._open_provider_setup()
            elif key == "cn_channels":
                self._open_cn_channels()
            elif key == "memory":
                self._open_memory()
        finally:
            # 恢复原生页高亮，只灭当前弹窗按钮
            self._apply_active(self._active_key)

    def _open_memory(self) -> None:
        """打开记忆面板（非模态 + 侧边栏禁用 + 居中于主窗口）。"""
        try:
            from ui.web.memory_panel import MemoryPanel
            parent = self.parent()
            panel = MemoryPanel(parent=parent if parent else self)
            self._set_panel_open(True)
            panel.finished.connect(lambda _: self._set_panel_open(False))
            panel.show()
            self._center_over_parent(panel, parent)
            panel.raise_()
            panel.activateWindow()
        except Exception as e:
            logger.warning("_Sidebar: _open_memory failed (non-fatal): %s", e)

    def _center_over_parent(self, panel, parent) -> None:
        """非模态面板 show() 不会像 exec() 那样自动居中，手动把它居中到父窗口。"""
        if parent is None:
            return
        try:
            fg = panel.frameGeometry()
            center = parent.frameGeometry().center()
            fg.moveCenter(center)
            panel.move(fg.topLeft())
        except Exception:
            pass

    def _open_cn_channels(self) -> None:
        """打开中国国内频道配置面板（非模态 + 侧边栏禁用 + 居中于主窗口）。"""
        try:
            from ui.web.cn_channel_panel import CnChannelPanel
            parent = self.parent()
            panel = CnChannelPanel(parent=parent if parent else self)
            self._set_panel_open(True)
            panel.finished.connect(lambda _: self._set_panel_open(False))
            panel.show()
            self._center_over_parent(panel, parent)
            panel.raise_()
            panel.activateWindow()
        except Exception as e:
            logger.warning("_Sidebar: _open_cn_channels failed (non-fatal): %s", e)

    def _build_id_menu(self) -> None:
        """构造 ID 按钮的 QMenu：Agent（头像+显示名）一组 / 用户（头像+显示名）一组。"""
        try:
            from main.main import stk as _stk
            _stk("sidebar.id_menu", "点击 ID 图标（构造菜单）", ok=True)
        except Exception:
            pass
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background: #2b2b2b; color: {_SIDEBAR_TEXT}; border: 1px solid {_SIDEBAR_ACTIVE};
                padding: 4px;
            }}
            QMenu::item {{
                padding: 6px 20px;
            }}
            QMenu::item:selected {{
                background: {_SIDEBAR_HOVER}; color: {_SIDEBAR_ACCENT};
            }}
            QMenu::separator {{
                height: 1px; background: {_SIDEBAR_ACTIVE}; margin: 4px 8px;
            }}
        """)

        # ── 身份分组（产品方定：Agent 一组、用户一组，勿按属性拆开）──
        # Agent：头像 + 显示名
        act_agent_avatar = menu.addAction(tr("ui.browser.agent_avatar"))
        act_agent_avatar.triggered.connect(self._pick_agent_avatar)
        act_agent_name = menu.addAction(tr("ui.browser.agent_display_name"))
        act_agent_name.triggered.connect(self._edit_agent_name)
        menu.addSeparator()
        # 用户：头像 + 显示名
        act_user_avatar = menu.addAction(tr("ui.browser.user_avatar"))
        act_user_avatar.triggered.connect(self._pick_user_avatar)
        act_user_name = menu.addAction(tr("ui.browser.user_display_name"))
        act_user_name.triggered.connect(self._edit_user_name)
        menu.addSeparator()
        # GPU 渲染开关（默认开；关闭写 gpu_render=false，下次启动生效）
        act_gpu = menu.addAction(tr("ui.browser.gpu_render"))
        act_gpu.setCheckable(True)
        act_gpu.setChecked(_gpu_render_enabled())
        act_gpu.triggered.connect(self._toggle_gpu_render)

        self._avatar_btn.setMenu(menu)

    def _toggle_gpu_render(self, enabled: bool) -> None:
        """切换 GPU 渲染开关：写配置 + 提示重启生效。"""
        _set_gpu_render(enabled)
        QMessageBox.information(
            self,
            tr("ui.browser.gpu_render"),
            tr("ui.browser.gpu_restart_hint"),
        )

    def _refresh_avatar_button(self) -> None:
        """把用户头像显示到 ID 按钮上（有头像则覆盖文字，无头像则显示 ID）。

        头像来源：user_identity.json 的 avatar 字段（绝对路径）。
        启动时和保存头像后各调用一次，让 ID 按钮反映最新用户头像。
        """
        try:
            identity = _read_user_identity()
            avatar_path = identity.get("avatar", "")
            if avatar_path and os.path.isfile(avatar_path):
                icon = QIcon(avatar_path)
                if not icon.isNull():
                    self._avatar_btn.setIcon(icon)
                    self._avatar_btn.setIconSize(self._avatar_btn.size())
                    self._avatar_btn.setText("")
                    return
        except Exception:
            pass
        # 无头像 → 回退为文字 ID
        self._avatar_btn.setIcon(QIcon())
        self._avatar_btn.setText("ID")

    # ── Agent 头像 ──

    def _pick_agent_avatar(self) -> None:
        """打开文件选择器，将选中图片拷入 WSL workspace/avatars/，执行 agents set-identity。"""
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("ui.browser.choose_agent_avatar"),
            "",
            "Images (*.png *.jpg *.jpeg *.gif *.webp *.bmp *.svg);;All Files (*)"
        )
        if not path or not os.path.isfile(path):
            return

        ext = os.path.splitext(path)[1] or ".png"
        dest_name = f"avatar{ext}"

        # ⚠⚠ 2026-10-17 逐行审计：**旧的"往宿主（main）工作区硬编 UNC 建目录"已删** ——
        #   那是单 Agent 覆盖模式的遗产（下面这条注释正好否定它）：图片只进**她自己的工作区**。
        # ⚠⚠ 图片进**这个号自己的工作区**的 avatars/，
        #   号也不写死 —— 认当前那张卡自己的 agent（槽账）+ 官方 `agents list --json` 的 workspace。
        _agent_id = _slots_current_agent()
        _ws_wsl = _agent_workspace_wsl(_agent_id)
        if not _run_wsl_for_avatar(
                f"mkdir -p '{_ws_wsl}/avatars' && cp -f '{_win_to_wsl_path(path)}' "
                f"'{_ws_wsl}/avatars/{dest_name}'"):
            QMessageBox.warning(
                self,
                tr("ui.browser.copy_failed"),
                f"{tr('ui.browser.cannot_copy_avatar')}{_ws_wsl}/avatars/{dest_name}",
            )
            return
        # ── 灾难恢复：同一张图另存 %PROGRAMDATA%\Rheia\avatar\（壳自己的备份，不动任何工作区）──
        try:
            _pg_avatar_dir = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia", "avatar")
            os.makedirs(_pg_avatar_dir, exist_ok=True)
            shutil.copy2(path, os.path.join(_pg_avatar_dir, dest_name))
        except OSError:
            pass

        # 调 CLI 设置
        avatar_ws_path = f"avatars/{dest_name}"
        from main.main import cli_usage
        set_identity = cli_usage("agents", "set-identity") or "openclaw agents set-identity"
        result = _run_wsl_for_avatar(
            f"{set_identity} --agent {_agent_id} --avatar {avatar_ws_path}"
        )
        if result:
            QMessageBox.information(
                self,
                tr("ui.browser.agent_avatar_updated"),
                f"{tr('ui.browser.avatar_set_to')}{avatar_ws_path}\n{tr('ui.browser.refresh_to_see')}",
            )
        else:
            QMessageBox.warning(
                self,
                tr("ui.browser.setup_failed"),
                f"{tr('ui.browser.cli_failed_run_manually')}\n{set_identity} --agent {_agent_id} --avatar {avatar_ws_path}",
            )

    # ── 用户头像 ──

    def _pick_user_avatar(self) -> None:
        r"""选择用户头像 → 拷贝到 %PROGRAMDATA%\Rheia\avatars\ → 写入 user_identity.json。"""
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("ui.browser.choose_user_avatar"),
            "",
            "Images (*.png *.jpg *.jpeg *.gif *.webp *.bmp *.svg);;All Files (*)"
        )
        if not path or not os.path.isfile(path):
            return

        ext = os.path.splitext(path)[1] or ".png"
        dest_name = f"user_avatar{ext}"

        user_avatar_dir = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia", "avatars")
        try:
            os.makedirs(user_avatar_dir, exist_ok=True)
        except OSError:
            pass

        dest = os.path.join(user_avatar_dir, dest_name)
        try:
            shutil.copy2(path, dest)
        except OSError as e:
            QMessageBox.warning(
                self,
                tr("ui.browser.copy_failed"),
                f"{tr('ui.browser.cannot_save_user_avatar')}{e}",
            )
            return

        # 写入 user_identity.json
        identity = _read_user_identity()
        identity["avatar"] = dest
        _write_user_identity(identity)

        # 刷新 ID 按钮 + 通知 BuiltinBrowser 同步官方对话框身份
        self._refresh_avatar_button()
        self.identity_changed.emit()

        QMessageBox.information(
            self,
            tr("ui.browser.user_avatar_updated"),
            f"{tr('ui.browser.user_avatar_saved')}{dest_name}",
        )

    # ── Agent 显示名 ──

    def _edit_agent_name(self) -> None:
        """QInputDialog 输入 Agent 显示名 → 官方 `agents set-identity --agent <她的号> --name`。

        ⚠ 多 Agent 并存下**不许写死 main** ——
        认当前那张卡自己的号（槽账 → `_slots_current_agent()`），否则改的是 main。
        """
        name, ok = QInputDialog.getText(
            self,
            tr("ui.browser.agent_display_name"),
            tr("ui.browser.enter_agent_name"),
        )
        if not ok or not name.strip():
            return

        name = name.strip()
        _agent_id = _slots_current_agent()
        from main.main import cli_usage
        set_identity = cli_usage("agents", "set-identity") or "openclaw agents set-identity"
        result = _run_wsl_for_avatar(
            f"{set_identity} --agent {_agent_id} --name \"{name}\""
        )
        if result:
            QMessageBox.information(
                self,
                tr("ui.browser.agent_name_updated"),
                f"{tr('ui.browser.name_set_to')}{name}",
            )
        else:
            QMessageBox.warning(
                self,
                tr("ui.browser.setup_failed"),
                f"{tr('ui.browser.cli_failed_run_manually')}\n{set_identity} --agent {_agent_id} --name \"{name}\"",
            )

    # ── 用户显示名 ──

    def _edit_user_name(self) -> None:
        """QInputDialog 输入用户显示名 → 写入 user_identity.json。"""
        name, ok = QInputDialog.getText(
            self,
            tr("ui.browser.user_display_name"),
            tr("ui.browser.enter_user_name"),
        )
        if not ok or not name.strip():
            return

        name = name.strip()
        identity = _read_user_identity()
        identity["display_name"] = name
        _write_user_identity(identity)
        # 刷新 ID 按钮 + 通知 BuiltinBrowser 同步官方对话框身份
        self._refresh_avatar_button()
        self.identity_changed.emit()

        QMessageBox.information(
            self,
            tr("ui.browser.user_name_updated"),
            f"{tr('ui.browser.user_name_saved')}{name}",
        )

    def _toggle(self) -> None:
        """收放侧栏。

        ⚠⚠ 2026-10-17 逐行审计（屏闪根因 2 第一步）：收放要动**整窗布局**（侧栏宽度 + 每个
        按钮尺寸），原来每改一步都各自触发一次布局/重绘 —— 右侧那块**唯一可见的 web 视图**
        于是被反复 resize（客户看到的就是闪）。这里把整段裹进"暂停更新"，**只落一遍**，
        最后一次性重画 ⇒ 中间态一帧都不出（少刷）。
        """
        self._collapsed = not self._collapsed
        _win = self.window()
        _side_prev = self.updatesEnabled()
        _win_prev = _win.updatesEnabled() if _win is not None else False
        self.setUpdatesEnabled(False)
        _win_frozen = False
        try:
            if _win is not None:
                _win.setUpdatesEnabled(False)
                _win_frozen = True
        except Exception:                                             # noqa: BLE001
            _win_frozen = False
        try:
            if self._collapsed:
                self.setFixedWidth(_SIDEBAR_WIDTH)
                self._sep.setVisible(False)
                for btn in self._more_buttons:
                    btn.setVisible(False)
            else:
                self.setFixedWidth(_SIDEBAR_EXPANDED)
                self._sep.setVisible(True)
                for btn in self._more_buttons:
                    btn.setVisible(True)
            for btn in self._buttons + self._more_buttons:
                btn._updateStyle(self._collapsed)
            self._toggle_btn.setText("…" if self._collapsed else "×")
            # 未安装技能 widget 联动折叠
            if hasattr(self, "_failed_skills"):
                self._failed_skills.set_collapsed(self._collapsed)
            # 自定义区联动折叠
            self._custom_sep.setVisible(not self._collapsed and len(self._custom_buttons) > 0)
            for btn in self._custom_buttons:
                btn.setVisible(not self._collapsed)
            if self._custom_slot:
                self._custom_slot.setVisible(not self._collapsed)
        finally:
            try:
                if _win_frozen and _win is not None:
                    _win.setUpdatesEnabled(_win_prev)
            except Exception:                                         # noqa: BLE001
                pass
            self.setUpdatesEnabled(_side_prev)
            # 一次性重画（收放只落这一帧）
            self.update()
            try:
                if _win is not None:
                    _win.update()
            except Exception:                                         # noqa: BLE001
                pass

    def _open_provider_setup(self) -> None:
        try:
            from ui.web.provider_setup import ProviderSetupCard
            parent = self.parent()
            card = ProviderSetupCard(parent=parent if parent else self)
            card.setup_completed.connect(self._on_provider_updated)
            # 模型配置卡片被 route 的 _dispatch_local_action 调用前已高亮
            # destroyed 兜底：用户直接关窗也清高亮 + 恢复侧边栏
            self._set_panel_open(True)
            card.destroyed.connect(lambda: (self._clear_active(), self._set_panel_open(False)))
            card.show()
            card.raise_()
            card.activateWindow()
        except Exception as e:
            logger.warning("_Sidebar: _open_provider_setup failed (non-fatal): %s", e)
            self._clear_active()

    def _on_provider_updated(self, _token: str) -> None:
        """Provider 配置保存后触发；找到带 _view 的父直接 reload（页面级硬编）。"""
        try:
            stk("provider.saved", f"token={'present' if _token else 'MISSING'}", ok=bool(_token))
            p = self.parent()
            while p is not None and not hasattr(p, "_view"):
                p = p.parent()
            if p is not None and hasattr(p, "_view"):
                # ⚠⚠ 2026-10-17 逐行审计：**整页 reload = 唯一可见画面白闪** ——
                #   改走父窗那条统一入口（`_request_reload` 里"页面已就绪就跳过整页重载"，
                #   并带 15 秒节流 + 重载前钉深色底）。
                #   ⚠ 2026-10-17 收敛：父窗没有那入口时，原先退回**裸 reload** —— 那正是
                #   本根因的唯一白闪源，现改为**宁可不刷也不裸 reload**（父窗恒为
                #   BuiltinBrowser、必有该入口，此分支实际不可达）。
                if hasattr(p, "_request_reload"):
                    p._request_reload("provider.saved")
                else:
                    logger.warning(
                        "_Sidebar: 父窗无 _request_reload，跳过整页重载（避免白闪）")
            # 模型配置完成 → 触发预装 skills/plugins（侧边栏入口同样要激活预装）
            if p is not None and hasattr(p, "_start_bonus_skills"):
                p._start_bonus_skills()
        except Exception as e:
            logger.warning("_Sidebar: _on_provider_updated failed (non-fatal): %s", e)

    # ── 自定义区 ──

    def _refresh_custom_area(self) -> None:
        """从 sidebar_custom.json 读配置，重建自定义区条目按钮。

        ⚠ 2026-10-17 逐行审计（屏闪·少刷）：**配置没变就一个部件都不动** ——
        重建 = deleteLater() 清空 + 逐条 insertWidget()，动的是可见侧栏的布局；
        连开几次管理面板就会连刷几次。
        """
        try:
            from ui.web.custom_area_manager import get_enabled_entries
            entries = get_enabled_entries()
        except Exception:
            entries = []

        _sig = [(e.get("id"), e.get("label_zh"), e.get("label_en"), e.get("emblem"))
                for e in entries]
        if _sig == getattr(self, "_custom_entries_sig", None):
            # 配置没变 ⇒ 零扰动（只补一次「未安装技能」落位，幂等）
            if self.layout().indexOf(self._failed_skills) < 0:
                av_idx = self.layout().indexOf(self._avatar_btn)
                if av_idx >= 0:
                    self.layout().insertWidget(av_idx, self._failed_skills)
            return

        self._custom_entries_sig = _sig
        for btn in self._custom_buttons:
            btn.deleteLater()
        self._custom_buttons.clear()
        if self._custom_slot:
            self._custom_slot.deleteLater()
            self._custom_slot = None

        zh = _isChinese()

        if not entries:
            self._custom_sep.setVisible(False)
        else:
            self._custom_sep.setVisible(not self._collapsed)
            for entry in entries:
                if entry.get("id") == "persona":
                    continue  # 已提到第一栏（Rheia），自定义区不再重复
                _lkey = str(entry.get("label_key") or "")
                # 28 语言：有 key 就走 tr；老配置没有 key 才回落到 zh/en 字段
                label = tr(_lkey) if _lkey else (entry.get("label_zh") if zh
                        else entry.get("label_en", entry.get("label_zh", "")))
                emblem = entry.get("emblem", "")
                btn = _NavButton(label, emblem)
                btn._route_key = entry.get("id", "")
                btn.clicked.connect(partial(self._dispatch_custom_action, entry))
                btn.setVisible(not self._collapsed)
                idx = self.layout().indexOf(self._custom_sep) + 1 + len(self._custom_buttons)
                self.layout().insertWidget(idx, btn)
                self._custom_buttons.append(btn)

        # ── 未安装技能提示插到自定义区之后、底部工具栏（_avatar_btn）之前 ──
        if self.layout().indexOf(self._failed_skills) < 0:
            av_idx = self.layout().indexOf(self._avatar_btn)
            if av_idx >= 0:
                self.layout().insertWidget(av_idx, self._failed_skills)
            else:
                self.layout().addWidget(self._failed_skills)

        # 「添加功能」入口已移除（上架初期不开放客户自定义功能），不再创建 _custom_slot。

    def ensure_rheia_entry(self) -> None:
        """客户后买扩展位：种子+key 到位后不重启，下一拍把入口挂上。"""
        if getattr(self, "_rheia_btn", None) is not None:
            return
        app = _rheia_app_root()
        if not _personal_slot_available():
            return
        try:
            btn = _NavButton("Rheia", "")
            btn._route_key = "persona"
            btn.clicked.connect(
                lambda: self._dispatch_custom_action({"id": "persona", "type": "preset"})
            )
            self.layout().insertWidget(0, btn)
            self._buttons.insert(0, btn)
            self._rheia_btn = btn
            # ⚠ 2026-10-23 设计约定：后买扩展位挂进来的这一颗，同样按当前折叠态定形。
            btn._updateStyle(self._collapsed)
        except Exception:                                         # noqa: BLE001
            pass

    def _dispatch_custom_action(self, entry: dict) -> None:
        """自定义区条目点击分发。按 type 派发（设计本意：preset/script/url/intent）。

          预设模块（eid 映射）  —— persona / provider_panel / diag_panel / restore_backup
          type: script          —— 跑外部命令（subprocess.Popen）
          type: url             —— 开网页（QDesktopServices.openUrl）
          type: intent          —— AI 意图类（需求已澄清 → 回面板继续/交付）
          其余                  —— 未实现，忽略（不静默假装成功）
        """
        eid = entry.get("id", "")
        etype = entry.get("type", "")
        try:
            from main.main import stk as _stk
            _stk("sidebar.custom", f"点击自定义区入口 eid={eid} type={etype}", ok=True)
        except Exception:
            pass
        self._apply_active(eid)
        try:
            if eid == "persona":
                if not _personal_slot_available():
                    return
                # 读卡口先跑一次“发现”——后台线程，不挡界面；失败如实记账，绝不假装装上了。
                # （与 10-27 产品树一致：点入口 = grow + 注入，随后开人格页。）
                try:
                    def _wsl_path(_p: str) -> str:
                        _p = str(_p or "")
                        if len(_p) > 1 and _p[1] == ":":
                            return "/mnt/" + _p[0].lower() + _p[2:].replace("\\", "/")
                        return _p

                    def _fire_slot_discover() -> None:
                        try:
                            _app_l = _wsl_path(_rheia_app_root())
                            _py = _app_l + "/persona/slot_discovery.py"
                            _src_w = (os.environ.get("RHEIA_PERSONAL_SLOT") or "").strip()
                            _argv = ["wsl.exe", "-d", _WSL_DISTRO, "-u", "root", "--",
                                     "python3", _py, "--app-root", _app_l]
                            if _src_w:
                                _argv += ["--source", _wsl_path(_src_w)]
                            _r = subprocess.run(_argv, capture_output=True, encoding="utf-8",
                                                errors="replace",
                                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                            _lines = [x for x in (_r.stdout or "").strip().splitlines() if x.strip()]
                            _tail = _lines[-1][:400] if _lines else ((_r.stderr or "").strip()[:200])
                            stk("sidebar.persona.discover",
                                "发现 Personal Slot：rc=%s %s" % (_r.returncode, _tail),
                                ok=(_r.returncode == 0))
                        except Exception as _de:                      # noqa: BLE001
                            try:
                                stk("sidebar.persona.discover",
                                    "发现异常：%s: %s" % (type(_de).__name__, _de), ok=False)
                            except Exception:                         # noqa: BLE001
                                pass

                    threading.Thread(target=_fire_slot_discover, daemon=True,
                                     name="slot-discover").start()
                except Exception:                                     # noqa: BLE001
                    pass
                try:
                    from ui.web.persona_page import PersonaPage
                    dlg = PersonaPage(self)
                    dlg.exec()
                except Exception as e:
                    # ⚠⚠ 「Rheia 键（卡槽入口）点了没反应」的定位：
                    #   这里原来只 logger.warning，而壳不配 root handler ⇒ 账上空白、
                    #   界面上就是"点了没反应"。现在把异常原文 + 栈尾三行记进账，
                    #   一次就能定位是谁抛的。
                    import traceback as _tb_p
                    _tail_p = " / ".join(_tb_p.format_exc().strip().splitlines()[-3:])
                    logger.warning("_Sidebar: PersonaPage failed (non-fatal): %s", e)
                    try:
                        stk("sidebar.persona",
                            f"打开人格页失败：{type(e).__name__}: {e} | {_tail_p}", ok=False)
                    except Exception:                                     # noqa: BLE001
                        pass
            elif eid == "provider_panel":
                try:
                    from ui.web.provider_panel import ProviderPanel
                    dlg = ProviderPanel(self)
                    parent_browser = self.parent()
                    dlg.provider_saved.connect(self._on_sidebar_provider_saved)
                    # 装了插件需要重启 → 走统一入口（带生命周期锁 + 探活 + reload），
                    # 不再在 provider_panel 里独立重启或裸 reload。
                    dlg.restart_requested.connect(
                        lambda: parent_browser._restart_gateway()
                        if parent_browser and hasattr(parent_browser, '_restart_gateway') else None,
                    )
                    dlg.exec()
                except Exception as e:
                    logger.warning("_Sidebar: ProviderPanel failed (non-fatal): %s", e)
                    self._open_provider_setup()
            elif eid == "diag_panel":
                try:
                    from ui.web.diag_panel import DiagPanel
                    dlg = DiagPanel(self)
                    dlg.exec()
                except Exception as e:
                    logger.warning("_Sidebar: DiagPanel failed (non-fatal): %s", e)
            elif eid == "restore_backup":
                try:
                    from ui.web.restore_backup import BackupRestoreDialog
                    dlg = BackupRestoreDialog(parent=self.parent() or self)
                    # 后台恢复结束后立即 reload（DB 替换在 import_db 已同步完成，无需缓冲）
                    dlg.imported.connect(self._reload_after_restore)
                    dlg.exec()
                except Exception as e:
                    logger.warning("_Sidebar: restore_backup failed (non-fatal): %s", e)
                    QMessageBox.warning(self, tr("ui.browser.open_failed"), str(e))
            elif etype == "script":
                self._run_custom_script(entry)
            elif etype == "url":
                act = entry.get("action") or {}
                u = str(act.get("url") or "")
                if u:
                    from PySide6.QtGui import QDesktopServices as _QDS
                    _QDS.openUrl(QUrl(u))
                else:
                    logger.warning("_Sidebar: url 条目缺 action.url，忽略 eid=%s", eid)
            elif etype == "intent":
                self._open_intent_entry(entry)
            else:
                logger.info("_Sidebar: unknown custom action '%s' (type=%s), ignoring",
                            eid, etype)
        finally:
            self._apply_active(self._active_key)

    def _run_custom_script(self, entry: dict) -> None:
        """执行自定义区 script 条目：Popen 外部命令。首次执行弹确认（可记住）。"""
        act = entry.get("action") or {}
        cmd = str(act.get("command") or "")
        args = act.get("args") or []
        if not cmd:
            logger.warning("_Sidebar: script 条目缺 command，忽略 id=%s", entry.get("id"))
            return
        if not getattr(self, "_custom_script_confirmed", False):
            r = QMessageBox.question(
                self, tr("ui.browser.notice"),
                f"{cmd} {' '.join(map(str, args))}".strip())
            if r != QMessageBox.StandardButton.Yes:
                return
            self._custom_script_confirmed = True
        try:
            # ⚠ 2026-09-23（产品方令：「所有 terminal 窗体必须是在后台静默运行，不得
            #   显示在前台」）：自定义区自定义脚本也不许弹终端窗口 ——
            #   CREATE_NO_WINDOW + STARTUPINFO(SW_HIDE)，stdio 全丢。
            #   **不许和 DETACHED_PROCESS 一起给**（同用时 CREATE_NO_WINDOW 被忽略
            #   ⇒ 控制台黑窗照弹）。
            _kw = {}
            if os.name == "nt":
                _kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                try:
                    _si = subprocess.STARTUPINFO()
                    _si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    _si.wShowWindow = 0
                    _kw["startupinfo"] = _si
                except Exception:
                    pass
            subprocess.Popen([cmd, *[str(a) for a in args]],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, **_kw)
            logger.info("_Sidebar: 执行客制命令（后台静默）%s %s", cmd, args)
        except Exception as e:
            logger.warning("_Sidebar: script 执行失败 %s — %s", cmd, e)
            QMessageBox.warning(self, tr("ui.browser.open_failed"), str(e))

    def _open_intent_entry(self, entry: dict) -> None:
        """打开 intent 条目：回到需求澄清面板（继续对话 / 交付生成）。

        intent 是「AI 帮客户把需求问清楚」的产物（execute_intent 追问到 [READY]）。
        这里把它送回 CustomAreaManager 编辑态——客户可继续对话澄清，
        或点交付让成熟机制（kernel 的 scout→mani→消费端）据此生成。
        """
        try:
            from ui.web.custom_area_manager import CustomAreaManager
            dlg = CustomAreaManager(parent=self.parent() or self,
                                    edit_entry_id=entry.get("id", ""))
            dlg.changed.connect(self._refresh_custom_area)
            dlg.exec()
        except Exception as e:
            logger.warning("_Sidebar: intent 面板打开失败 — %s", e)

    def _on_sidebar_provider_saved(self, _token: str) -> None:
        """侧边栏 provider_panel 保存成功后，标记父浏览器已配置，避免重复弹配置卡。"""
        parent_browser = self.parent()
        if isinstance(parent_browser, BuiltinBrowser):
            parent_browser._provider_configured_once = True

    def _open_custom_manager(self) -> None:
        """打开图形化管理面板，变更后自动刷新自定义区。"""
        try:
            from main.main import stk as _stk
            _stk("sidebar.add_feature", "点击添加功能入口", ok=True)
        except Exception:
            pass
        try:
            from ui.web.custom_area_manager import CustomAreaManager
            dlg = CustomAreaManager(parent=self.parent() or self)
            dlg.changed.connect(self._refresh_custom_area)
            dlg.exec()
        except Exception as e:
            logger.warning("_Sidebar: _open_custom_manager failed — %s", e)
            QMessageBox.warning(
                self,
                tr("ui.browser.open_failed"),
                f"{tr('ui.browser.cannot_open_custom_manager')}{e}",
            )

    def _reload_after_restore(self) -> None:
        """导入备份后：先重启 Gateway（写回的 key 需重启才生效），
        2 秒后走父窗统一入口请求一次重载（页面已就绪即跳过 + 节流 ⇒ 重启窗口里不反复重载）。

        ⚠ 2026-10-17 逐行审计（缺陷）：原先注释承诺「探活成功后延迟 2 秒再 reload」，
        实际**只重启、不 reload** —— 导入备份后的刷新路径根本不存在。现在两件事都做。
        """
        try:
            p = self.parent()
            while p is not None and not hasattr(p, "_view"):
                p = p.parent()
            if p is not None and hasattr(p, "_view"):
                p._restart_gateway()
                logger.info("_Sidebar: restore done — gateway restart triggered")
                # 整页重载一律走父窗统一入口（已就绪即跳过 + 15s 节流 + 重载前钉深色底）；
                # 父窗没有该入口时**宁可不刷也不裸 reload** —— 裸 reload = 唯一可见画面
                # 白闪（屏闪根因 1），非必要不刷。
                _req = getattr(p, "_request_reload", None)
                if _req is not None:
                    QTimer.singleShot(2000, lambda: _req("restore"))
                else:
                    logger.warning(
                        "_Sidebar: 父窗无 _request_reload，导入后跳过整页重载（避免白闪）")
            else:
                logger.warning("_Sidebar: BuiltinBrowser not found for reload after restore")
        except Exception as e:
            logger.warning("_Sidebar: _reload_after_restore failed: %s", e)


# ── BuiltinBrowser ──

class BuiltinBrowser(QMainWindow):
    """QWebEngineView-based browser that renders the Gateway Control UI.

    左侧导航栏 + scout 驱动入口分流（无 API key → 模型配置页）。
    """

    # 自愈校准完成 → 请求重启（后台线程 emit，主线程槽弹文案 + 重启）
    self_heal_restart = Signal()
    # 自愈判定完成且健康（无需重启）→ 预装 skills/plugins 的前置信号：
    # 时序稳当（产品方定）：预装必须等自愈跑完（环境/mani 对齐）才触发，
    # 避免在"即将重启 / 未对齐"的进程里装一半。
    self_heal_done = Signal()
    token_ready = Signal(str)   # 网关自愈后拿到的 token（后台线程 emit，主线程应用）

    def __init__(
        self,
        gateway_url: str = "http://127.0.0.1:18789",
        token: str = "",
        start_page: str = "/chat",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        # 自愈信号 → 主线程槽（弹文案 + 重启）。后台线程 emit，Qt 自动 queued 切主线程。
        self.self_heal_restart.connect(self._on_self_heal_restart)
        cfg = _load_webview_config()
        win = cfg.get("window", {})
        loading_cfg = cfg.get("loading", {})
        features = cfg.get("features", {})
        ua = cfg.get("user_agent", {})

        self._gateway_url = gateway_url.rstrip("/")
        self._token = token
        self._start_page = start_page
        # ── **壳记住"上次那条会话路径"** ──
        #   官方设计（docs/web/urls.md）：会话身份就在 URL 的可读路径里 ——
        #   `/chat/<agentId>` 落 main，`/chat/<agentId>/<sessionRef>` 落具体会话；
        #   Control UI **有意**不在浏览器本地存"当前会话"（它靠 URL 寻址/收藏/分享）。
        #   壳是替用户 load 的那一方，写死 `/chat` 就必然每次回 main ⇒ 由壳记：
        #   导航变化时把**路径**存进自家偏好（token 在 query 里、**不落盘**）；
        #   启动时优先按它 load，取不到才回落 start_page。
        self._last_path = ""
        try:
            from kernel.scout import getPref
            _saved = (getPref("web_last_path") or "").strip()
            if _saved.startswith("/") and len(_saved) < 512:
                self._start_page = _saved
                self._last_path = _saved
                stk("browser.start_page", "restored %s" % _saved, ok=True)
        except Exception as _e:
            stk("browser.start_page", "restore failed: %s" % _e, ok=False)
        self._retry_cfg = cfg.get("retry", {"max_attempts": 3, "delay_ms": 2000})
        self._retry_count = 0
        self._path_fallback_done = False   # 2026-10-21：死路回退只做一次
        self._load_fail_count = 0          # 2026-10-21：加载失败计数（到上限停，不再无限闪）
        self._provider_check_done = False
        self._provider_configured_once = False
        self._provider_unknown_hits = 0  # unknown 重试计数（CLI 抖动时只重试、不弹卡）
        self._self_heal_ok = False      # 自愈判定完成且健康（预装前置）
        self._bonus_pending = False     # 自愈未完成时的预装挂起
        self.self_heal_done.connect(self._on_self_heal_done)
        self.token_ready.connect(self._apply_recovered_token)
        # 模型配置完成、但 Gateway 还在重启窗口里时，预装 skills/plugins 不能立刻
        # 触发（CLI 无能力、撞 state-lifecycle 锁）。置 True，等重启探活成功再触发。
        self._bonus_skills_pending = False

        self.setWindowTitle(win.get("title", "Rheia"))
        # 打开尺寸随屏幕自适应（大屏大、小屏小），用户手动调整过的以 db 记录为准（_restore_appearance）
        from ui.theme import fitToScreen
        fitToScreen(self, 0.8, 0.85)

        h_layout = QHBoxLayout()
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(0)

        self._sidebar = _Sidebar(self._gateway_url, self._navigate_to)
        # 客户后买扩展位：每 5 秒看一次，种子+key 到位就把 Rheia 入口挂上（不重启）。
        try:
            self._slot_entry_timer = QTimer(self)
            self._slot_entry_timer.timeout.connect(self._sidebar.ensure_rheia_entry)
            self._slot_entry_timer.start(5000)
        except Exception:                                         # noqa: BLE001
            pass
        # 用户身份变更（头像/显示名）→ 同步进官方个人资料（users.setDisplayName/setAvatar）
        self._sidebar.identity_changed.connect(self._sync_identity_to_rpc)
        h_layout.addWidget(self._sidebar)

        # ── 外观恢复：从 baseline_pool.db 读上次窗口尺寸 + 侧边栏状态 ──
        self._restore_appearance()

        # ── 跨线程 signal：bonus skills 安装完毕/失败刷新侧边栏 ──
        try:
            from main.main import (
                refresh_failed_skills)
            refresh_failed_skills(self._sidebar._failed_skills.refresh)
            # 启动时自动清理已手动安装的 skill（后台线程，不阻塞 UI）
            self._cleanup_failed_skills_on_connect()
        except Exception:
            pass

        main_widget = QWidget()
        layout = QVBoxLayout(main_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Status bar
        self._status_bar = QWidget()
        self._status_bar.setFixedHeight(28)
        self._status_bar.setStyleSheet("background-color:#1a1a1a;")
        status_layout = QHBoxLayout(self._status_bar)
        status_layout.setContentsMargins(12, 0, 12, 0)
        status_layout.setSpacing(8)

        # ── 连接态：圆环 + 中心网点，两者同轴 ──
        self._dot_container = QWidget()
        self._dot_container.setFixedSize(22, 22)
        # 用 CSS background 画圆环，● 字符叠在中心
        self._dot_container.setStyleSheet("""
            QWidget {
                background: transparent;
                border: 2px solid #6a5e52;
                border-radius: 11px;
            }
        """)
        self._connection_dot = QLabel("●", self._dot_container)
        self._connection_dot.setStyleSheet("color:#6a5e52;font-size:8px;background:transparent;border:none;")
        self._connection_dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._connection_dot.setGeometry(0, 0, 22, 22)
        status_layout.addWidget(self._dot_container, 0, Qt.AlignmentFlag.AlignVCenter)

        self._connection_status = QLabel(f"Gateway · {self._gateway_url}")
        self._connection_status.setStyleSheet("color:#b0b0b0;font-size:11px;")
        status_layout.addWidget(self._connection_status, 0, Qt.AlignmentFlag.AlignVCenter)

        status_layout.addStretch()

        # ── retry 手动按钮（默认隐藏，_wait_and_retry 时显示）──
        self._retry_manual_btn = QPushButton(tr("ui.btn.retry"))
        self._retry_manual_btn.setFixedHeight(20)
        self._retry_manual_btn.setStyleSheet(
            "QPushButton{background:#3a3a3a;color:#ccc;border:1px solid #555;border-radius:3px;font-size:10px;padding:0 8px;}"
            "QPushButton:hover{background:#4a4a4a;color:#fff;}"
        )
        self._retry_manual_btn.clicked.connect(self._manual_retry)
        self._retry_manual_btn.setVisible(False)
        status_layout.addWidget(self._retry_manual_btn)

        self._retry_restart_btn = QPushButton(tr("ui.btn.restart_gateway"))
        self._retry_restart_btn.setFixedHeight(20)
        self._retry_restart_btn.setStyleSheet(
            "QPushButton{background:#3a3a3a;color:#ccc;border:1px solid #555;border-radius:3px;font-size:10px;padding:0 8px;}"
            "QPushButton:hover{background:#4a4a4a;color:#fff;}"
        )
        self._retry_restart_btn.clicked.connect(lambda: self._restart_gateway(manual=True))   # 唯一允许的手动重启
        self._retry_restart_btn.setVisible(False)
        status_layout.addWidget(self._retry_restart_btn)

        status_layout.addStretch()

        self._token_indicator = QLabel(tr("ui.browser.token_yes") if token else tr("ui.browser.token_no"))
        token_color = "#5a9e6f" if token else "#6a5e52"
        self._token_indicator.setStyleSheet(f"color:{token_color};font-size:11px;")
        status_layout.addWidget(self._token_indicator)

        layout.addWidget(self._status_bar)

        # Progress bar
        self._progress = QProgressBar()
        self._progress.setFixedHeight(3)
        self._progress.setTextVisible(False)
        self._progress.setStyleSheet(
            "QProgressBar { background-color:#2a2a2a; border:none; }"
            "QProgressBar::chunk { background-color:#f37021; }"
        )
        self._progress.setVisible(loading_cfg.get("show_progress_bar", True))
        layout.addWidget(self._progress)

        # Placeholder
        self._placeholder = QLabel(loading_cfg.get("placeholder_text", tr("ui.browser.loading")))
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("color:#b0b0b0;font-size:14px;")
        layout.addWidget(self._placeholder, stretch=1)

        # WebEngine
        profile = QWebEngineProfile.defaultProfile()
        if ua.get("override", ""):
            profile.setHttpUserAgent(ua["override"])

        # ⚠⚠ **页面是网关发的，Browser 只显示、不干预**：
        #   这里原来往页面里注入一段 CSS（把 backdrop-filter 全禁掉）来治 QtWebEngine 的闪——
        #   那属于**壳在改页面**，已撤。页面保持网关原样。（后续实测若确认"闪"来自
        #   引擎渲染层而非壳进程忙，再议：修法必须放在不干预页面的框架里。）

        # ── Chromium 缓存资源优化 ──
        # 缓存路径收进壳层自己的 ProgramData（可控、可清），并设上限，
        # 避免 Chromium 默认在用户目录无限缓存、吃内存。
        try:
            _cache_dir = os.path.join(
                os.environ.get("PROGRAMDATA", ""), "Rheia", "webcache")
            profile.setCachePath(_cache_dir)
            profile.setHttpCacheMaximumSize(100 * 1024 * 1024)  # 100MB
        except Exception:
            pass

        settings = profile.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled,
                              features.get("javascript", True))
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows,
                              features.get("javascript_can_open_windows", False))
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled,
                              features.get("local_storage", True))
        settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled,
                              features.get("plugins", False))
        # GPU 渲染关闭时：关 2D/WebGL 硬加速（与 QTWEBENGINE_CHROMIUM_FLAGS 的
        # --disable-gpu 双保险）；开 GPU 时保持默认（硬件加速）。
        if not _gpu_render_enabled():
            try:
                # ⚠ 这里是**跷跷板**，不是开关（2026-09-16 记）：
                #   开硬件加速 → 闪动（它本身就是闪动元凶之一）；
                #   关硬件加速 → 卡帧。
                #   所以**别再拨这个开关**：要治得换手段 ——
                #   QTWEBENGINE_CHROMIUM_FLAGS 用 `--disable-gpu-compositing`
                #   （光栅化仍走 GPU → 不卡帧；合成走软件 → 不闪）。
                settings.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, False)
                settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, False)
            except Exception:
                pass

        # 2026-10-15：普通 QWebEngineView —— 拖放由**官方页面自己**处理（壳不再抢）
        self._view = QWebEngineView()
        # ── 治闪（2026-10-13）：整页 load/reload 时 QtWebEngine 先铺**白底**再画新页
        #   ⇒ 整窗白闪；把页面底色调成壳的深色底（`_SIDEBAR_BG`）即可。
        #   ⚠ 2026-10-17 收敛：这段「钉深色底」原散在两处（构造期 + 重载前各写一遍），
        #   现只留 `_pin_dark_background` 一处实现，构造期与所有重载路径都调它。
        self._pin_dark_background()
        self._view.setAcceptDrops(True)
        self._view.setVisible(False)

        # ── 闪动取证器 ──────────────────────────
        #   账上已经没有「运行期重载 / 渲染进程崩溃」，那就要抓**部件级**的现场：
        #   唯一可见画面（web 视图）、主窗、以及侧栏那个浮层，每次
        #   **尺寸变化 / 移动 / 显示 / 隐藏** 都记一行（带时间戳，前 200 条），
        #   客户说「就是这一下」时，对齐时间就知道是谁在动它。
        try:
            from PySide6.QtCore import QEvent as _QE, QObject as _QO
            _EVN = {
                int(_QE.Type.Resize): "resize", int(_QE.Type.Move): "move",
                int(_QE.Type.Show): "show", int(_QE.Type.Hide): "hide",
                int(_QE.Type.WindowStateChange): "winstate",
                int(_QE.Type.LayoutRequest): "layout",
            }

            class _FlickerSpy(_QO):
                """只记「画面被动了」的四类事件，不碰业务、不阻塞（只写账）。"""

                def __init__(self, tag, target):
                    super().__init__(target)
                    self._tag = tag
                    self._n = 0

                def eventFilter(self, obj, ev):
                    try:
                        _nm = _EVN.get(int(ev.type()))
                        if _nm:
                            self._n += 1
                            if self._n <= 200:
                                stk("browser.spy", "%s %s" % (self._tag, _nm))
                    except Exception:                                     # noqa: BLE001
                        pass
                    return False

            self._spy = _FlickerSpy("view", self._view)
            self._view.installEventFilter(self._spy)
            self._spy_win = _FlickerSpy("win", self)
            self.installEventFilter(self._spy_win)
            _ov = getattr(getattr(self, "_sidebar", None), "_overlay", None)
            if _ov is not None:
                self._spy_ov = _FlickerSpy("overlay", _ov)
                _ov.installEventFilter(self._spy_ov)
        except Exception:                                             # noqa: BLE001
            pass

        # 用自定义 page（重写 javaScriptConsoleMessage 桥接提示音）

        self._view.loadStarted.connect(self._on_load_started)
        self._view.loadProgress.connect(self._progress.setValue)
        self._view.loadFinished.connect(self._on_load_finished)
        try:
            if QWebEngineLoadingInfo is not None:
                self._view.page().loadingChanged.connect(self._on_loading_changed)
        except Exception:                                     # noqa: BLE001
            pass
        # ── 闪动监护信号（声明驱动：tlc_consumer.watch 里写了 render_term 才记账）──
        #   渲染进程异常退出 = 白屏/闪 的直接证据；本行只"上报信号"，判断/聚合在 TLC。
        try:
            from main.tlc import report_signal as _sig
            self._view.renderProcessTerminated.connect(
                lambda status, code: _sig("render_term",
                                          "status=%s code=%s" % (status, code)))
        except Exception:
            pass
        # ── 闪动溯源：页面每次开始/结束加载都记一行桩 ──
        #   "跟他说话时一直闪" = 页面在反复重载，而壳只在启动期写日志、
        #   运行期什么都不记 —— 那就永远查不出是谁触发的。这两个钩子补上这一环。
        try:
            self._view.loadStarted.connect(
                lambda: stk("browser.load.started", "page load started (flash)"))
            self._view.loadFinished.connect(
                lambda ok: stk("browser.load.finished", f"ok={bool(ok)}"))
            self._view.renderProcessTerminated.connect(
                lambda status, code: stk("browser.render.dead",
                                         f"status={status} code={code}", ok=False))
        except Exception:
            pass
        self._view.urlChanged.connect(self._on_url_changed)

        layout.addWidget(self._view, stretch=1)

        h_layout.addWidget(main_widget, stretch=1)

        central = QWidget()
        central.setLayout(h_layout)
        self.setCentralWidget(central)

        # tlc 心跳：UI 主循环活着 = 壳体活着（10s 打点，tlc 超 35s 无心跳判僵死）
        try:
            _beat = QTimer(self)
            _beat.setInterval(10000)
            _beat.timeout.connect(self._tlc_heartbeat)
            _beat.start()
        except Exception:
            pass
        # 未安装技能：UI 就绪后主动跑一次 CLI 印证查询（不依赖 bonus 完成信号——
        # worker 若提前完成/秒崩，信号会早于本构造发出而错过）
        try:
            QTimer.singleShot(1500, self._sidebar._failed_skills.refresh)
        except Exception:
            pass

        # 记：导航一变就把**路径**存进壳偏好（只存路径；token 在 query 里，不落盘）
        try:
            self._view.urlChanged.connect(self._remember_path)
        except Exception:
            pass

        # Navigate: 首次加载加上 start_page 路径
        url = QUrl(self._gateway_url + self._start_page)
        if self._token:
            url.setQuery(f"token={self._token}")
        self._view.load(url)

    def _remember_path(self, url) -> None:
        """把当前页面的**路径**记进壳偏好（官方设计：会话身份 = URL 路径）。

        只记 path —— token 在 query 里，**不进盘**。
        记不上就静默放过：记忆是尽力而为，不影响导航。
        """
        try:
            p = url.path() or ""
            if not p.startswith("/") or len(p) > 512 or p == self._last_path:
                return
            from kernel.scout import setPref
            setPref("web_last_path", p)
            self._last_path = p
        except Exception:
            pass

    # ── 用户身份 → 官方资料同步（走官方 RPC；不再注入 document_start 脚本）──

    def _tlc_heartbeat(self) -> None:
        """tlc 心跳：UI 事件循环活着 = 壳体没僵死。"""
        try:
            from main.tlc import heartbeat
            heartbeat()
        except Exception:
            pass

    def _sync_identity_to_rpc(self) -> None:
        """把用户身份（显示名 + 头像）同步到 OpenClaw 官方个人资料。

        技术债5：替换原来的 document_start 注入脚本 + paint JS（脆弱 DOM 依赖），
        改走官方 RPC（users.setDisplayName / users.setAvatar），头像/名字由官方渲染，
        主程序只当调接口的客户端（node 脚本 set_identity.js）。
        """
        try:
            identity = _read_user_identity()
            name = (identity.get("display_name") or "").strip()
            avatar_path = (identity.get("avatar") or "").strip()
            if not name and not avatar_path:
                return

            gw_cfg = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia", "gateway_config.json")
            token = ""
            if os.path.isfile(gw_cfg):
                import json as _json
                try:
                    with open(gw_cfg, "r", encoding="utf-8") as f:
                        token = _json.load(f).get("token", "")
                except Exception:
                    token = ""
            if not token:
                return

            script = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "main", "scripts", "set_identity.js")
            wsl_script = "/mnt/" + script.replace("\\", "/").replace(":", "").lower()
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0

            def _run(display_name="", avatar_base64="", mime=""):
                try:
                    out = subprocess.run(
                        ["wsl", "-d", "Ubuntu", "--", "node", wsl_script, token, display_name, avatar_base64, mime],
                        capture_output=True, encoding="utf-8", errors="replace",
                        creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=si,
                    )
                    return (out.stdout or "").strip()
                except Exception:
                    return ""

            if name:
                _run(display_name=name)
            if avatar_path and os.path.isfile(avatar_path):
                import base64 as _b64
                ext = os.path.splitext(avatar_path)[1].lower()
                mime = "image/jpeg" if ext in (".jpg", ".jpeg") else ("image/webp" if ext == ".webp" else "image/png")
                try:
                    with open(avatar_path, "rb") as f:
                        avatar_base64 = _b64.b64encode(f.read()).decode("ascii")
                    _run(avatar_base64=avatar_base64, mime=mime)
                except OSError:
                    pass
        except Exception:
            pass

    def _restore_appearance(self) -> None:
        """启动时从 baseline_pool.db 读外观状态并应用。"""
        try:
            from kernel.scout import getAllPrefs, ensureRecoveryTables
            ensureRecoveryTables()
            prefs = getAllPrefs()
            if not prefs:
                return
            # 窗口尺寸
            w = int(prefs.get("window_width", 0) or 0)
            h_val = int(prefs.get("window_height", 0) or 0)
            if w > 640 and h_val > 400:
                self.resize(w, h_val)
            # 侧边栏折叠状态
            collapsed = prefs.get("sidebar_collapsed", "")
            if collapsed == "true" and self._sidebar._collapsed:
                pass  # 默认已是折叠
            elif collapsed == "false" and self._sidebar._collapsed:
                self._sidebar._toggle()
        except Exception:
            pass

    def _save_appearance(self) -> None:
        """关闭时快照 — 把窗口状态 + openclaw.json 中全部 key 和偏好写回 baseline_pool.db。

        写入内容：
          - 窗口尺寸 / 侧边栏折叠状态
          - models.providers.*.apiKey → credentials 表
          - ui.prefs 全部 → user_prefs 表
        """
        try:
            from kernel.scout import setPref, setCredential, ensureRecoveryTables
            ensureRecoveryTables()

            # ── 窗口状态 ──
            sz = self.size()
            setPref("window_width", str(sz.width()))
            setPref("window_height", str(sz.height()))
            c = "true" if self._sidebar._collapsed else "false"
            setPref("sidebar_collapsed", c)

            # ── openclaw.json → DB ──
            try:
                from main.main import read as _rcfg
                # 关闭时 WSL 常已停止，硬读会触发冷启动卡死：先探活，不可达则跳过
                if _run_wsl_for_avatar("echo ok"):
                    cfg = _rcfg()
                else:
                    # ⚠ 2026-10-17 逐行审计（缺陷）：这条「跳过」原先**无日志无提示** ——
                    #   用户偏好/密钥静默不落盘，事后查据全无痕迹。现在留一行警告。
                    logger.warning(
                        "BuiltinBrowser: WSL 不可达 ⇒ 本次关闭跳过 openclaw.json 落盘"
                        "（偏好/密钥未同步）")
                    cfg = {}
            except Exception:
                cfg = {}

            # provider API keys → credentials
            if cfg:
                models = cfg.get("models", {}).get("providers", {})
                for pid, pv in models.items():
                    ak = pv.get("apiKey", "") if isinstance(pv, dict) else ""
                    if ak:
                        setCredential(f"{pid}_api_key", ak, pid)

                # ui.prefs → user_prefs
                prefs = cfg.get("ui", {}).get("prefs", {})
                for k, v in prefs.items():
                    if v is not None:
                        setPref(f"ui_{k}", str(v).lower() if isinstance(v, bool) else str(v))

                # identity.theme → user_prefs
                agents = cfg.get("agents", {}).get("list", [])
                for agent in agents:
                    ident = agent.get("identity", {})
                    if ident.get("name"):
                        setPref("agent_name", ident["name"])
                    if ident.get("theme"):
                        setPref("agent_theme", ident["theme"])

                # thinking/reasoning/verbose defaults → user_prefs
                defaults = cfg.get("agents", {}).get("defaults", {})
                for key in ("thinkingDefault", "reasoningDefault", "verboseDefault"):
                    val = defaults.get(key)
                    if val:
                        setPref(f"agent_{key}", val)

        except Exception:
            pass

    def closeEvent(self, event) -> None:
        """拦截关闭 → 先异步快照进 DB → 再真正关闭。"""
        # 已经保存完毕，直接放行
        if getattr(self, '_save_done', False):
            event.accept()
            return
        # 如果用户按了多次关闭，只处理第一次
        if getattr(self, '_closing', False):
            event.ignore()
            return
        self._closing = True

        # ── 弹出保存中浮层 ──
        toast = QLabel(tr("ui.browser.saving"), None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        toast.setAlignment(Qt.AlignmentFlag.AlignCenter)
        toast.setStyleSheet("""
            QLabel {
                background: #2d2d2d;
                color: #e0e0e0;
                font-size: 15px;
                padding: 18px 36px;
                border-radius: 10px;
                border: 1px solid #555;
            }
        """)
        toast.adjustSize()
        # 定位于主窗口中央
        if self.isVisible():
            c = self.geometry().center()
            toast.move(c.x() - toast.width() // 2, c.y() - toast.height() // 2)
        toast.show()
        QApplication.processEvents()

        # ── 后台线程执行快照 ──
        class _SaveSignals(QObject):
            finished = Signal()

        sigs = _SaveSignals()

        def _save_worker() -> None:
            self._save_appearance()
            sigs.finished.emit()

        sigs.finished.connect(lambda: self._on_save_done(toast))
        t = threading.Thread(target=_save_worker, daemon=True, name="shutdown-save")
        t.start()

        # ── 硬性兜底：保存最多等 5 秒，到点强制关闭，绝不让窗口卡死 ──
        # 后台线程是 daemon，即使还卡在 WSL 超时里，进程退出时会被一起回收。
        self._save_fallback_timer = QTimer(self)
        self._save_fallback_timer.setSingleShot(True)
        self._save_fallback_timer.timeout.connect(lambda: self._on_save_done(toast, timed_out=True))
        self._save_fallback_timer.start(5000)

        event.ignore()  # 拦截本次关闭事件

    def _on_save_done(self, toast: QLabel, timed_out: bool = False) -> None:
        """快照完成（或兜底超时）→ 关浮层 → 真正关闭。"""
        # 两条路径（正常完成 / 兜底超时）可能先后触发，只处理第一次
        if getattr(self, '_save_done', False):
            return
        timer = getattr(self, '_save_fallback_timer', None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
        toast.hide()
        toast.deleteLater()
        self._save_done = True  # 通知下一轮 closeEvent 直接放行
        QApplication.processEvents()
        self.close()

    # ── Agent 头像恢复 ──

    @staticmethod
    def _restore_agent_avatar_from_progdata() -> None:
        """启动时检查 %PROGRAMDATA%/Rheia/avatar/ 并恢复到 WSL。

        此方法在 BuiltinBrowser 构造完成、Gateway 已就绪后的后台线程调用。
        """
        avatar_dir = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia", "avatar")
        if not os.path.isdir(avatar_dir):
            return
        candidates = [
            f for f in os.listdir(avatar_dir)
            if os.path.isfile(os.path.join(avatar_dir, f))
            and f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg'))
        ]
        if not candidates:
            return
        src = os.path.join(avatar_dir, candidates[0])
        dst_name = f"avatar{os.path.splitext(candidates[0])[1] or '.png'}"
        # ⚠ **不写死 main、不写死宿主工作区** ——
        #   认当前那张卡自己的号，图片进**它自己的工作区** avatars/（官方 `agents list --json` 说了算）。
        _agent_id = _slots_current_agent()
        _ws_wsl = _agent_workspace_wsl(_agent_id)
        try:
            if not _run_wsl_for_avatar(
                    f"mkdir -p '{_ws_wsl}/avatars' && cp -f '{_win_to_wsl_path(src)}' "
                    f"'{_ws_wsl}/avatars/{dst_name}'"):
                return
            from main.main import cli_usage
            set_identity = cli_usage("agents", "set-identity") or "openclaw agents set-identity"
            _run_wsl_for_avatar(
                f"{set_identity} --agent {_agent_id} --avatar avatars/{dst_name}"
            )
        except Exception:
            pass

    # ── Navigation ──

    def _navigate_to(self, path: str) -> None:
        # ⚠⚠ 页面是网关发的，Browser 只显示、不干预：
        #   原来这里往页面里 runJavaScript 去驱动它的 SPA 路由（pushState / location 改写）——
        #   那是**壳在改页面**，撤掉。现在只做"显示"该做的事：让浏览器去那个 URL；
        #   **已经在那个路径上就什么都不做**（少刷，不给它添一次无谓的整页加载）。
        try:
            _cur = (self._view.url().path() or "/").rstrip("/") or "/"
            _tgt = (path or "/").rstrip("/") or "/"
            if _cur == _tgt:
                return
        except Exception:                                             # noqa: BLE001
            _tgt = (path or "/")
        _u = QUrl(self._gateway_url + _tgt)
        if self._token:
            _u.setQuery(f"token={self._token}")
        self._view.load(_u)
        logger.info("BuiltinBrowser: navigate to %s", path)
        try:
            stk("browser.navigate", f"path={path}")
        except Exception:
            pass

    def _on_url_changed(self, url: QUrl) -> None:
        path = url.path().rstrip("/") or "/"
        for route in _nav_routes():
            if route["path"] == path:
                self._sidebar.set_active(route["key"])
                return

    # ── Retry ──

    def _set_dot_state(self, color: str) -> None:
        """连接态灯（绿/黄/红/灰）：**状态没变就一个字节都不碰**。

        ⚠⚠ 2026-10-17 逐行审计（闪）：原来探活/重连脉冲（333ms 或 200ms 一拍）里每次都
        重设**两处样式表**（点 + 圆环）—— 每设一次都要重算样式并重绘该子树，脉冲期间就是
        **重绘风暴**，客户看到的就是闪。现在先比再设，同色直接返回。
        """
        try:
            _dot = "color:%s;font-size:8px;background:transparent;border:none;" % color
            _ring = "background:transparent;border:2px solid %s;border-radius:11px;" % color
            if self._connection_dot.styleSheet() != _dot:
                self._connection_dot.setStyleSheet(_dot)
            if self._dot_container.styleSheet() != _ring:
                self._dot_container.setStyleSheet(_ring)
        except Exception:                                             # noqa: BLE001
            pass

    def _set_conn_text(self, text: str) -> None:
        """状态栏文字：**变了才设**（同文本 setText 也会触发布局/重绘）。"""
        try:
            if self._connection_status.text() != text:
                self._connection_status.setText(text)
        except Exception:                                             # noqa: BLE001
            pass

    def _wait_and_retry(self) -> None:
        """Gateway 不可达时的重连脉冲：curl 探活一秒三发，探通即 load，探不通就接着探。

        脉冲模型，不是 delay 等待。Gateway 是 localhost 本地进程，探活就是 curl
        一下毫秒级，属于「狭窄通道」——探通即走，绝不躺在递增 delay 里干等。
        指数退避是给远程服务防雪崩用的，用在本地进程上是张冠李戴。

        探活由后台线程 curl（不阻塞 UI），结果回主线程。探通 → load 完整 URL
        （不是 reload：load 失败时页面是 Chromium 错误页/占位页）；三秒
        （一秒三发 ≈ 9 拍）不通也**不再回落 doctor --fix**（壳不越权修网关，Gateway
        起不来交给 systemd 重拉），脉冲只看不修、不停。
        """
        self._retry_manual_btn.setVisible(True)
        self._retry_restart_btn.setVisible(True)
        self._connection_dot.setStyleSheet("color:#d9a840;font-size:8px;background:transparent;border:none;")
        self._dot_container.setStyleSheet("background:transparent;border:2px solid #d9a840;border-radius:11px;")
        self._reconnect_probe_count = 0
        try:
            stk("browser.retry", "reconnect probe started (1s=3 beats, 3s→systemd)", ok=False)
        except Exception:
            pass
        self._do_reconnect_probe()

    def _do_reconnect_probe(self) -> None:
        """单次重连探活：curl 根路径看 HTTP 200，通了就 load，不通继续脉冲。"""
        import subprocess as _sp
        import threading

        class _ProbeSig(QObject):
            result = Signal(bool)

        sig = _ProbeSig()  # 主线程创建，thread affinity 正确
        sig.result.connect(self._on_reconnect_result)

        def _probe() -> None:
            si = _sp.STARTUPINFO()
            si.dwFlags |= _sp.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            alive = False
            try:
                r = _sp.run(
                    f"wsl -d {_WSL_DISTRO} -- bash -c \"curl -s -o /dev/null -w '%{{http_code}}' {self._gateway_url}\"",
                    shell=True, capture_output=True, timeout=3,
                    encoding="utf-8", errors="replace",
                    creationflags=_sp.CREATE_NO_WINDOW,
                    startupinfo=si,
                )
                code = (r.stdout or "").strip()
                alive = code in ("200", "302", "401", "403")
            except Exception:
                pass
            sig.result.emit(alive)

        threading.Thread(target=_probe, daemon=True, name="gw-reconnect-probe").start()

    def _on_reconnect_result(self, alive: bool) -> None:
        """主线程回调：处理重连探活结果。探通 load，不通继续脉冲（壳层不替用户修网关）。"""
        if alive:
            self._reconnect_probe_count = 0
            self._retry_manual_btn.setVisible(False)
            self._retry_restart_btn.setVisible(False)
            self._connection_status.setText(f"Gateway · {self._gateway_url}")
            logger.info("BuiltinBrowser: reconnect probe alive, loading page")
            try:
                stk("browser.reconnect", "alive → load", ok=True)
            except Exception:
                pass
            # ⚠ 2026-10-17 逐行审计（屏闪·根因 1 的**不可免处 1**）：这里是真 load，
            #   **不走统一入口** —— 探活成功恰恰说明页面**本就没就绪**（断线期间是
            #   Chromium 错误页/占位页，`connection_healthy()` 为假）；整页 load 是把它
            #   换回真页面的唯一手段，`_request_reload` 换不回新页面。唯一能减闪的
            #   就是重载前钉深色底（下面这一行），否则换页那一瞬仍铺白底。
            self._pin_dark_background()
            url = QUrl(self._gateway_url + self._start_page)
            if self._token:
                url.setQuery(f"token={self._token}")
            self._view.load(url)
            return

        self._reconnect_probe_count += 1
        self._connection_status.setText(
            tr("ui.browser.retrying", n=self._reconnect_probe_count)
        )
        # 三秒不通（一秒三发 ≈ 9 拍）：**不回落 doctor --fix**（壳不越权修网关，且
        # doctor 内含 stop→start，会打断 systemd 的 Restart=always）——只把计数清零，
        # 脉冲继续探；Gateway 起不来交给 systemd 自己重拉。
        if self._reconnect_probe_count >= 9:
            self._reconnect_probe_count = 0
        # 继续脉冲：一秒三发 = 333ms 一拍
        QTimer.singleShot(333, self._do_reconnect_probe)

    def _manual_retry(self) -> None:
        """手动触发重连脉冲：立即拍一发（不等下一拍），探通即 load。"""
        try:
            stk("browser.manual_retry", "reconnect probe triggered")
        except Exception:
            pass
        self._reconnect_probe_count = 0
        self._load_fail_count = 0
        self._do_reconnect_probe()

    def reconnect_gateway(self) -> None:
        """Gateway 就绪后重连 —— 延迟后 reload 当前页面，让配置视图/模型列表刷新。

        供 main/main.py 在 keepalive 激活 Gateway 后调用。
        启动场景：浏览器已首次 load('/chat')，这里延迟 3 秒（等 gateway 完全
        就绪、模型配置加载完）再 reload，等价右键刷新，让模型列表真正刷出来。
        """
        QTimer.singleShot(3000, lambda: self._request_reload("probe"))

    def _request_reload(self, source: str = "page") -> None:
        """页面重载**统一走 TLC 的"重载车道"**：

        触发方只报"我是谁"（source），**该走哪条道由声明说了算**——消费端自己
        去问声明（`request_reload_for`），不靠别处推给它。车道正忙时这一下只
        标脏、不重载，忙完补一次 ⇒ 同一波请求只产生一次重载（消闪）。
        TLC 不在（旧包/声明缺失）时退回直接重载，行为不倒退。
        """
        # 消白闪（2026-10-15）：页面已就绪时，"刷新列表"类的整页重载直接丢掉 ——
        #   一次整页重载 = 内容空白一瞬（用户看到的就是"闪"）。真掉线那条不走这里
        #   （_do_reconnect_probe 直连 load，且那时页面本就没就绪）。
        try:
            if self.connection_healthy():
                stk("browser.reload.skip", "页面已就绪，跳过整页重载（source=%s）" % source)
                return
        except Exception:
            pass
        try:
            from main.tlc import (request_reload_for as _rrf,
                                  register_reload_executor as _reg,
                                  lane_for_source as _lfs)
            _lane = _lfs(source)
            # ⚠ 2026-10-17 逐行审计（屏闪·少刷）：executor **每条车道只注册一次** ——
            #   原先每次调用都重注册一遍（同一车道反复登记，纯属无谓扰动）。
            _lane_key = str(_lane)
            _lanes = getattr(self, "_reload_exec_lanes", None)
            if _lanes is None:
                _lanes = self._reload_exec_lanes = set()
            if _lane_key not in _lanes:
                _reg(_lane, lambda _r: self._do_reload_page())
                _lanes.add(_lane_key)
            _rrf(source, "来源=%s 车道=%s" % (source, _lane))
        except Exception:
            self._do_reload_page()

    def _pin_dark_background(self) -> None:
        """把页面底色钉成壳的深色底（`_SIDEBAR_BG`）—— 全壳**唯一实现**。

        ⚠ 治闪（2026-10-13）原理：整页 load/reload 时 QtWebEngine 先铺**白底**再画
        新页 ⇒ 整窗白闪。底色改成深色后，重载那一瞬看到的就是深色而非白闪。
        ⚠ 2026-10-17 收敛：原先「构造期设一次」+「重载前再设一次」两处各写一遍
        `setBackgroundColor(_SIDEBAR_BG)`；现只此处一处实现，构造期与所有重载
        路径统一调它。不改任何加载/刷新逻辑，只改这一层底色。
        """
        try:
            from PySide6.QtGui import QColor as _QC
            self._view.page().setBackgroundColor(_QC(_SIDEBAR_BG))
        except Exception:
            pass

    def _do_reload_page(self) -> None:
        """刷新当前页面（等价右键 reload），让 Control UI 重新从 Gateway 拉取配置，
        从而刷新右下角输入框旁的「已配置模型列表」。

        ⚠ 治闪动（2026-09-16）：**节流**。
          本函数被多处 singleShot(500ms / 3000ms) 调度，一次调用 = 整页重载 =
          整窗白闪；几处凑在一起排队就是连环白闪（"经常闪动"）。
          2.5 秒内只放一次，重复请求直接丢掉（配置变更本来也不需要连刷）。
          2026-10-01：窗口由 2.5s 放大到 15s —— 说话期间仍被反复触发（间隔 >2.5s），
          每次都是整页重载 = 整窗白闪，表现为"一直闪"；15s 合并后一轮里最多白闪一次。
        ⚠ 2026-10-17 逐行审计（缺陷）：这段说明原先写在**可执行语句之后** —— 那不是
        docstring，而是一个被丢弃的字符串字面量（文档不生效，还容易被误读成函数说明）。
        已上移到函数头，并把打点那几行挪到说明之后。
        """
        # 记一行：闪动溯源要知道是**谁**点的这一下（多处 singleShot 都会调它）
        try:
            import traceback as _tb
            _who = [f.name for f in _tb.extract_stack()[:-1]][-3:]
            stk("browser.reload_page", "reload by " + "<-".join(reversed(_who)))
        except Exception:
            pass
        import time as _t
        _now = _t.monotonic()
        if _now - getattr(self, "_last_reload_ts", 0.0) < 15.0:
            return                       # 节流：连着来的丢掉，不再白闪
        self._last_reload_ts = _now
        try:
            stk("browser.reload", "reload triggered", ok=True)
        except Exception:
            pass
        # 重载前钉一次深色底（唯一实现见 `_pin_dark_background`）——
        #   否则加载那一瞬仍是白底铺满 = 整窗白闪。
        self._pin_dark_background()
        # ⚠ 2026-10-17 逐行审计（屏闪·根因 1 的**不可免处 2**）：这一下整页 reload 保留。
        #   本函数是「重载车道」的**执行端**：网关重启完成后必须整页 reload 才能让新配置
        #   （模型注册表冷读）生效；此时页面虽"可见"，但内容与 Gateway 已不一致，
        #   跳过重载 = 新配置永不生效。所以它是"必须真刷"的那一下。
        self._view.reload()

    def _restart_gateway(self, *, manual: bool = False) -> None:
        # ⚠⚠ **只有左侧 sidebar 那个"手动重启 Gateway"能动网关**；
        #   其余自动路径（装插件/改配置/恢复备份/自愈/诊断）一律让路给系统级重启。
        if not manual:
            try:
                stk("browser.gw.restart",
                    "非手动 ⇒ 不自启网关（为系统级重启让路；只允许 sidebar 手动重启）", ok=False)
            except Exception:                                         # noqa: BLE001
                pass
            return
        """重启 Gateway 加载新配置 —— 官方 gateway restart（冷启动）。

        配置模型/Character 后必须重启 Gateway 才能让新配置生效（模型注册表热读，
        不重启则冷读磁盘有、运行时没有 → Unknown model）。

        走官方 gateway restart（无 flag）= service-manager restart = systemd
        stop+start 冷启动，不裸 systemctl restart（09-04 教训：裸 restart 会和
        systemd Restart=always 打架导致反复 stop/start）。重启在后台线程执行
        （restart 阻塞最长 60s），完成后回主线程探活 reload。
        """
        # 拿 Gateway 生命周期所有权（与其它重启链互斥）
        try:
            from main.main import acquire_lifecycle
            if not acquire_lifecycle("browser"):
                stk("browser.gw.restart", "lifecycle owned elsewhere, skip", ok=False)
                return
            self._gw_lifecycle_owned = True
        except Exception:
            pass

        try:
            stk("browser.gw.restart", "官方 gateway restart（冷启动）", ok=True)
        except Exception:
            pass

        class _Sig(QObject):
            done = Signal(bool)

        sig = _Sig()  # 主线程创建，thread affinity 正确
        sig.done.connect(self._on_gw_restart_done)

        def _do_restart() -> None:
            ok = False
            try:
                from main.main import restart_gateway_safe
                ok = restart_gateway_safe(manual=True)   # 已过本函数的 manual 闸
            except Exception:
                ok = False
            sig.done.emit(ok)

        threading.Thread(target=_do_restart, daemon=True, name="gw-restart").start()

    def _restart_gateway_owned(self) -> None:
        """已持有生命周期锁时重启 Gateway（复用 _restart_gateway 的重启逻辑）。"""
        self._restart_gateway()

    def _on_gw_restart_done(self, ok: bool) -> None:
        """Gateway 重启完成回主线程：探通 reload，否则脉冲探活兜底。

        restart_gateway_safe 返回的是 restart 后 check_gateway_alive 的结果：
        ok=True 说明重启完且起来了 → reload 让新配置生效；
        ok=False 可能 systemd 还在收敛 → 脉冲探活兜底（_on_probe_result 探通后 reload）。
        """
        if ok:
            self._release_gw_lifecycle()
            try:
                stk("browser.gw.restart", "restart ok → reload", ok=True)
            except Exception:
                pass
            QTimer.singleShot(500, lambda: self._request_reload("pulse"))
        else:
            try:
                stk("browser.gw.restart", "restart 未探活，脉冲探活兜底", ok=False)
            except Exception:
                pass
            self._gateway_probe_count = 30
            self._repair_attempted = False
            self._do_gateway_probe()

    def _do_gateway_probe(self) -> None:
        """单次探活：curl 根路径看 HTTP 200，通了就恢复并重载。

        Probe 在后台线程执行，结果通过 signal 回主线程处理，不直接碰 Qt 对象。"""
        import subprocess as _sp
        import threading

        class _ProbeSig(QObject):
            result = Signal(bool)

        sig = _ProbeSig()  # 主线程创建，thread affinity 正确
        sig.result.connect(self._on_probe_result)

        def _probe() -> None:
            si = _sp.STARTUPINFO()
            si.dwFlags |= _sp.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            alive = False
            try:
                r = _sp.run(
                    f"wsl -d {_WSL_DISTRO} -- bash -c \"curl -s -o /dev/null -w '%{{http_code}}' {self._gateway_url}\"",
                    shell=True, capture_output=True, timeout=3,
                    encoding="utf-8", errors="replace",
                    creationflags=_sp.CREATE_NO_WINDOW,
                    startupinfo=si,
                )
                code = (r.stdout or "").strip()
                alive = code in ("200", "302", "401", "403")
            except Exception:
                pass
            sig.result.emit(alive)

        threading.Thread(target=_probe, daemon=True, name="gw-probe").start()

    def _on_probe_result(self, alive: bool) -> None:
        """主线程回调：处理探活结果。"""
        interval = getattr(self, "_gateway_probe_interval", 0.2)
        self._gateway_probe_count -= 1
        self._connection_status.setText(tr("ui.browser.gw_waiting"))
        if alive:
            logger.info("BuiltinBrowser: gateway probe alive after restart")
            self._set_dot_state("#5a9e6f")   # 同色不重设（探活每拍都到这）
            self._connection_status.setText(f"Gateway · {self._gateway_url}")
            self._retry_manual_btn.setVisible(False)
            self._retry_restart_btn.setVisible(False)
            self._retry_count = 0
            self._repair_attempted = False
            self._notify_glory_connected()
            self._release_gw_lifecycle()
            try:
                stk("browser.gw.probe", "alive", ok=True)
            except Exception:
                pass
            # 模型配置完成挂起的预装：Gateway 现在稳定了，触发预装 skills/plugins
            if getattr(self, '_bonus_skills_pending', False):
                self._bonus_skills_pending = False
                self._start_bonus_skills()
            # 探通就 reload（Gateway HTTP 200 即就绪）
            QTimer.singleShot(500, lambda: self._request_reload("pulse"))
        elif self._gateway_probe_count > 0:
            QTimer.singleShot(int(interval * 1000), self._do_gateway_probe)
        else:
            logger.warning("BuiltinBrowser: gateway restart probe exhausted")
            self._set_dot_state("#e85a4a")   # 同色不重设
            self._retry_manual_btn.setVisible(False)
            self._retry_restart_btn.setVisible(False)
            try:
                stk("browser.gw.probe", "exhausted", ok=False)
            except Exception:
                pass
            # ⚠⚠ 2026-10-17 逐行审计发现的**死端**：原来探活第一次耗尽就调
            #   `_repair_gateway_then_reprobe()` —— 那是个**已禁用的 no-op**（那个空壳
            #   本次已删）⇒ 探活耗尽后**什么都不做了**：界面永远停在红点，也不再重试。
            #   现在**直接兜底脉冲**：Gateway 起不来交给 systemd 自己重拉
            #   （壳不越权修网关，免得打断 `Restart=always` 的节奏），壳只负责一直探活 +
            #   如实提示。
            self._repair_attempted = False
            self._release_gw_lifecycle()
            self._wait_and_retry()

    def _release_gw_lifecycle(self) -> None:
        """释放 Gateway 生命周期所有权（重启探活链结束，无论成败）。"""
        if getattr(self, "_gw_lifecycle_owned", False):
            try:
                from main.main import release_lifecycle
                release_lifecycle("browser")
            except Exception:
                pass
            self._gw_lifecycle_owned = False

    def _cleanup_failed_skills_on_connect(self) -> None:
        """Gateway 连通后执行一次 failed skills 清理（后台线程，不阻塞 UI）。

        Sig 在主线程创建 → 后台线程 emit → Qt queued connection → 主线程回调。"""
        class _Sig(QObject):
            done = Signal()

        sig = _Sig()  # 创建于主线程，thread affinity 正确
        sig.done.connect(self._sidebar._failed_skills.refresh)

        def _bg_cleanup() -> None:
            try:
                from main.main import cleanup_failed_skills
                cleanup_failed_skills()
            except Exception:
                pass
            sig.done.emit()  # 后台线程 emit → Qt 自动 queued → 主线程 refresh()

        threading.Thread(
            target=_bg_cleanup, daemon=True, name="cleanup-failed-skills").start()

    # ── 后台安装 bonus skills + 插件：Gateway 连通即触发，不等 provider 检查 ──

    def _on_self_heal_restart(self) -> None:
        """自愈校准完成后，主线程槽：告知用户 + 重启 Windows 程序。

        由后台线程 self_heal_restart 信号触发（Qt queued 投递回主线程）。

        通知用原生 Win32 MessageBox，不用 Qt 的 QMessageBox——自愈采样/预装/
        PySide6 渲染三峰并发时内存吃紧，此刻创建 Qt 模态窗口会走进 Qt 内部
        abort() 路径（qBadAlloc → __fastfail(FAST_FAIL_FATAL_APP_EXIT)，
        实测崩溃码 0xc0000409 偏移 Qt6Core!0x1c8d8）。重启是系统动作，不该
        依赖 GUI 分配：原生弹窗零 Qt 依赖、抗压；通知失败也照常重启。
        """
        try:
            from ui.i18n import tr
            from main.main import stk as _stk
            _stk("self_heal", "主线程弹文案请求重启", ok=True)
            # 原生 Win32 弹窗（Qt-free）。失败静默继续，绝不阻塞重启。
            try:
                import ctypes
                _title = str(tr("ui.browser.notice"))
                _body = str(tr("ui.browser.self_heal_restart"))
                ctypes.windll.user32.MessageBoxW(
                    0, _body, _title, 0x40,  # MB_ICONINFORMATION
                )
            except Exception as _ne:
                _stk("self_heal", "原生弹窗失败（non-fatal，继续重启）: %s" % _ne)
            # 文案确认后重启（先拉新进程 → 我们退出放门）
            # tlc 抑制：自愈重启是受控动作不是崩溃，重启期间不报僵死
            try:
                from main.tlc import suppress as _tlc_suppress
                _tlc_suppress("self_heal_restart")
            except Exception:
                pass
            import subprocess as _sp
            import os as _os
            import sys as _sys
            _stk("self_heal", "restarting self...", ok=True)

            # ⚠ 顺序（2026-09-21 实测「自愈后没有自动拉起壳体」后改）：
            #   旧顺序是"先 Popen 新进程 → 再 stop_nowait() → 再 _os._exit(0)"，
            #   而 stop_nowait() 里两条 wsl 调用各带 30s 超时（WSL 一慢就是好几秒），
            #   单实例门还握在自己手里 ⇒ 新进程导入完 PySide6 来敲门时被挡在门外，
            #   它**默默退出**，壳就没了。stake 原样证据：
            #     22:33:09 [self_heal] restarting self...
            #     22:33:16 !!! 已有壳实例（互斥体）：本实例退出，不碰网关 !!!
            #   现在两头都补上：
            #   ① 新进程带上我们的 pid（`RHEIA_HANDOVER_PID`）——它进门之前**先等我们退干净**
            #      （main._handover_wait），所以"我们比它慢"不再等于"它进不来"；
            #   ② 于是可以并行：先拉新进程（它去导入 PySide6，~6s），我们这边同时把慢活
            #      （停保活，两条 wsl 调用）干完，最后 `_os._exit(0)` ——
            #      门随进程退出自动放，新进程那边刚好等到，接手。
            #      **不主动交钥匙**：交早了会留出"第三个实例抢门"的窗口。
            #   顺序换了但安全性没换：慢活仍然由**老进程自己**干完才退，
            #   不会半路撒手留下孤儿保活。
            try:
                _env = dict(_os.environ)
                _env["RHEIA_HANDOVER_PID"] = str(_os.getpid())
                _sp.Popen(
                    [_sys.executable,
                     _os.path.abspath(_sys.argv[0]) if _sys.argv and _sys.argv[0] else __file__],
                    cwd=_os.getcwd(),
                    env=_env,
                    creationflags=getattr(_sp, "DETACHED_PROCESS", 0)
                    | getattr(_sp, "CREATE_NO_WINDOW", 0),
                    close_fds=True,
                )
            except Exception as _pe:
                # 拉不起来就**别退**：退了就真没壳了。旧进程继续活着（客户面前还有窗口）。
                _stk("self_heal", "拉新进程失败，旧进程不退（壳还活着）: %s" % _pe, ok=False)
                return
            _os._exit(0)
        except Exception as _e:
            try:
                from main.main import stk as _stk
                _stk("self_heal", f"restart prompt failed: {_e}", ok=False)
            except Exception:
                pass

    def _on_self_heal_done(self) -> None:
        """自愈判定完成且健康 → 若预装曾挂起则触发。

        ⚠ 2026-09-21 修（产品方报「预安装 skills 和 plugin 呢？为何没有启动？」）：
          预装有**两个前置**——「模型已配置」和「自愈判定完成」，各自挂起：
            · `_bonus_skills_pending`：模型配置完成、但 Gateway 还在重启窗口（见 _on_provider_check_ready）
            · `_bonus_pending`        ：自愈还没判完（_start_bonus_skills 里挂的）
          原来这里**只看自愈那一个**，而"模型已配置"那个挂起只在
          `_on_probe_result(alive=True)` 里被消费 —— 而重启成功那条路
          （`_on_gw_restart_done(ok=True)` → `_do_reload_page`）**不走探活**
          ⇒ 两个挂起各自等人来取，谁也等不到：**两个前置都满足也永远不装**。
          实测（stake.log 22:43:40）：`self_heal done，预装前置条件满足` 之后
          什么都没有（既没有 deferred 也没有 triggering），预装一次没跑过。
          现在两个挂起一起看：谁先到都行，到齐就装。
          （自愈判完时 Gateway 必然是通的——它排在 kernel bootstrap 之后，
          而 bootstrap 本身就是拿 Gateway 干的活；真要是撞上重启窗口，
          预装自己是后台任务、失败会落 failed_skills.json、侧边栏可重试。）
        """
        self._self_heal_ok = True
        stk("bonus.skills.browser", "self_heal done，预装前置条件满足")
        if getattr(self, '_bonus_pending', False) or getattr(self, '_bonus_skills_pending', False):
            self._bonus_skills_pending = False
            self._start_bonus_skills()

    def _start_bonus_skills(self) -> None:
        """后台安装预装 skills 和 Tavily/mem9 插件（模型配置完成 + 自愈完成后触发）。

        时序门槛（产品方定，稳当优先）：
          - 自愈判定未完成（_self_heal_ok=False）→ 挂起，等 self_heal_done 再触发
          - 自愈 healed=True 的进程会重启 → 不装（旧进程将死）；重启后新进程
            healed=False → 发 self_heal_done → 这里才真正装
        """
        if not getattr(self, '_self_heal_ok', False):
            stk("bonus.skills.browser", "deferred: 等自愈判定完成再装")
            self._bonus_pending = True
            return
        self._bonus_pending = False
        if getattr(self, '_bonus_skills_started', False):
            stk("bonus.skills.browser", "already started, skip")
            return
        self._bonus_skills_started = True
        try:
            # ⚠⚠ **安装链路都不许影响页面**：整条装机链
            #   （预装 + 配套 + 两步校验）交给**无窗口子进程**跑；UI 进程只负责起它。
            #   起不来才落回原位跑（行为不变，只是会占 UI 进程）。
            stk("bonus.skills.browser", "triggering install worker（子进程）")
            _root_i = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            _wk_i = os.path.join(_root_i, "main", "install_worker.py")
            _kw_i = {}
            if os.name == "nt":
                _kw_i["creationflags"] = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                          | getattr(subprocess, "DETACHED_PROCESS", 0))
                try:
                    _si_i = subprocess.STARTUPINFO()
                    _si_i.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    _si_i.wShowWindow = 0
                    _kw_i["startupinfo"] = _si_i
                except Exception:                                     # noqa: BLE001
                    pass
            subprocess.Popen([sys.executable, _wk_i], cwd=_root_i, close_fds=True, **_kw_i)
        except Exception as e:
            stk("bonus.skills.browser", f"起装机子进程失败（落回原位跑）: {e}", ok=False)
            logger.warning("BuiltinBrowser: bonus_skills worker failed (fallback in-proc): %s", e)
            try:
                from main.main import install_bonus_skills_background
                install_bonus_skills_background()
            except Exception as e2:
                stk("bonus.skills.browser", f"exception: {e2}", ok=False)

    # ── Provider 探测：浏览器加载成功后判定是否需要配 key ──

    def _check_provider_setup(self) -> None:
        """检查是否需要首次 provider 配置 — 只负责 provider 配置卡片，不管技能安装。

        用 QThread（而非裸 threading.Thread + 局部 QObject）做后台检查：
        裸线程里 emit 的信号在 PySide6 下不可靠地丢投递，导致回调永不触发、
        配置页永不弹出。QThread 的 finished/自定义 Signal 是 Qt 原生跨线程机制。"""
        if self._provider_check_done:
            return

        # ── 一轮只弹一次，避免重复 ──
        if self._provider_configured_once:
            self._provider_check_done = True
            return
        class _ProviderCheckThread(QThread):
            ready = Signal(str)  # "configured" / "unconfigured" / "unknown"

            def run(self) -> None:
                state = "unknown"
                try:
                    from ui.web.provider_setup import provider_setup_state
                    state = provider_setup_state()
                except Exception:
                    state = "unknown"
                self.ready.emit(state)

        self._provider_check_thread = _ProviderCheckThread(self)
        self._provider_check_thread.ready.connect(self._on_provider_check_ready)
        self._provider_check_thread.start()

    def _on_provider_check_ready(self, state: str) -> None:
        """QThread 三态回主线程：unknown 不弹卡（CLI 查不到 ≠ 没配过），轮询重试。"""
        if self._provider_check_done:
            return
        if state == "unknown":
            self._provider_unknown_hits = getattr(self, "_provider_unknown_hits", 0) + 1
            if self._provider_unknown_hits <= 15:
                try:
                    stk("provider.check",
                        f"state=unknown（第 {self._provider_unknown_hits} 次）⇒ 2 秒后重试", ok=False)
                except Exception:
                    pass
                QTimer.singleShot(2000, self._check_provider_setup)
                return
            try:
                stk("provider.check", "unknown 连续 15 次 ⇒ 按未配置处理（弹卡）", ok=False)
            except Exception:
                pass
            state = "unconfigured"
        self._provider_unknown_hits = 0
        self._provider_check_done = True
        try:
            if state == "configured":
                stk("provider.check", "apiKey already present, skip setup card")
                self._provider_configured_once = True
                # 模型已配置（本来就有 key）→ 现在才装预装 skills/plugins
                self._start_bonus_skills()
                return

            stk("provider.check", "no apiKey — showing setup card")
            logger.info("BuiltinBrowser: no apiKey found — showing provider setup card")

            def _reload_page(_token: str):
                stk("provider.saved", f"token={'present' if _token else 'MISSING'}", ok=bool(_token))
                self._provider_configured_once = True
                self._provider_check_done = False
                # ── 更新 token（配置后可能刷新）──
                if _token:
                    self._token = _token
                    self._token_indicator.setText(tr("ui.browser.token_yes"))
                    self._token_indicator.setStyleSheet("color:#5a9e6f;font-size:11px;")
                # ── 重启 Gateway 加载新 openclaw.json ──
                self._restart_gateway()
                # 模型配置完成 → 预装 skills/plugins。但不能现在装：重启是异步的，
                # Gateway 还在重启窗口，CLI 无能力（撞 state-lifecycle 锁）。置 pending，
                # 等 _on_probe_result 探活成功（Gateway 稳定）后再触发。
                self._bonus_skills_pending = True

            from ui.web.provider_setup import ProviderSetupCard
            card = ProviderSetupCard(parent=self)
            card.setup_completed.connect(_reload_page)
            card.show()
        except Exception as e:
            self._provider_check_done = False
            logger.warning("BuiltinBrowser: _check_provider_setup failed (non-fatal): %s", e)
            QMessageBox.warning(
                self,
                tr("ui.browser.notice"),
                f"{tr('ui.browser.cannot_open_setup_card')}{e}",
            )

    # ── Slots ──

    def _on_load_started(self) -> None:
        self._connection_status.setText(f"{tr('ui.browser.gw_url')} · {self._gateway_url}  ({tr('ui.browser.loading_status')})")
        self._connection_dot.setStyleSheet("color:#d9a840;font-size:8px;background:transparent;border:none;")
        self._dot_container.setStyleSheet("background:transparent;border:2px solid #d9a840;border-radius:11px;")

    def _on_load_finished(self, ok: bool) -> None:
        self._progress.setValue(0)
        if ok:
            self._placeholder.setVisible(False)
            self._view.setVisible(True)
            self._set_conn_text(f"Gateway · {self._gateway_url}")
            self._set_dot_state("#5a9e6f")
            self._retry_manual_btn.setVisible(False)
            self._retry_restart_btn.setVisible(False)
            self._retry_count = 0
            self._load_fail_count = 0
            self._notify_glory_connected()
            logger.info("BuiltinBrowser: Gateway loaded successfully")
            stk("browser.load", "page loaded (Gateway+token verified)")
            # ── 七层推演拆分已下线（技术债2）：SL splitter 的 MutationObserver 高频全量扫描
            # 是输入框周边闪烁的根因；即使去 characterData + 防抖，scanAll 全量 querySelectorAll 仍重排。
            # 按「Browser 不加戏」彻底移除，七层推演标记如需拆分后续交页面/服务端处理。
            # ── 提示音已下线（技术债4）：DOM class 检测(.chat-bubble.streaming)随 OpenClaw 升级失效，
            # 静默失效无提示。按「Browser 不加戏」移除注入；需提示音后续用 WebSocket 层可靠检测重做。
            # 恢复 Agent 头像（如果 avatar/ 目录存在）
            try:
                QTimer.singleShot(200, lambda: self._restore_agent_avatar_from_progdata())
            except Exception:
                pass
            # 用户身份同步改走官方接口（技术债5），不再 document_start 注入 + reload。
            # Gateway+token 连通性已验证 → 清理 failed skills → 探 provider 状态。
            # bonus skills 安装不在这里触发：必须等模型配置完成后才装（见 _on_provider_check_ready）
            QTimer.singleShot(300, self._cleanup_failed_skills_on_connect)
            QTimer.singleShot(500, self._check_provider_setup)
        else:
            # 2026-10-21：上次会话路径可能是死路（agent/会话已删）⇒ 页面永远加载失败，
            #   壳在“加载中/连接失败”之间闪。这里只做一次回退：清偏好 → 回 /chat。
            try:
                _p = self._view.url().path() or ""
                if (not self._path_fallback_done) and self._last_path and _p == self._last_path:
                    self._path_fallback_done = True
                    self._last_path = ""
                    self._start_page = "/chat"
                    try:
                        from kernel.scout import setPref
                        setPref("web_last_path", "")
                    except Exception:
                        pass
                    stk("browser.start_page", "stale path cleared, fallback /chat", ok=True)
                    _u = QUrl(self._gateway_url + "/chat")
                    if self._token:
                        _u.setQuery(f"token={self._token}")
                    self._view.load(_u)
                    return
            except Exception:
                pass
            self._load_fail_count += 1
            if self._load_fail_count > 10:
                self._set_dot_state("#e85a4a")
                self._connection_status.setText(tr("ui.browser.connect_fail"))
                self._retry_manual_btn.setVisible(True)
                self._retry_restart_btn.setVisible(True)
                try:
                    stk("browser.load_giveup", f"after {self._load_fail_count} failures", ok=False)
                except Exception:
                    pass
                return
            # Gateway 不可达 — 自动重试，Chromium 显示默认错误页
            self._set_dot_state("#e85a4a")
            self._connection_status.setText(tr("ui.browser.connect_fail"))
            self._notify_glory_disconnected()
            logger.warning("BuiltinBrowser: Gateway load failed, retrying…")
            try:
                stk("browser.load", "page load FAILED", ok=False)
            except Exception:
                pass
            self._wait_and_retry()

    def _on_loading_changed(self, info) -> None:
        """2026-10-21：把加载失败的真实错误码写进账（原来只有 ok=False，查不出原因）。"""
        try:
            if QWebEngineLoadingInfo is not None and info.status == QWebEngineLoadingInfo.LoadFailedStatus:
                stk("browser.load_fail",
                    f"url={info.url.toString()} code={info.errorCode} err={info.errorString}",
                    ok=False)
        except Exception:                                     # noqa: BLE001
            pass

    # ── Glory 同步 ──

    def _notify_glory_connected(self) -> None:
        try:
            from kernel import glory
            glory.dwell(self._gateway_url, self._token)
            logger.info("BuiltinBrowser: glory dwell url=%s", self._gateway_url)
        except Exception as exc:
            logger.warning("BuiltinBrowser: _notify_glory_connected failed: %s", exc)

    def _notify_glory_disconnected(self) -> None:
        try:
            from kernel import glory
            glory.leave()
            logger.info("BuiltinBrowser: glory leave")
        except Exception as exc:
            logger.warning("BuiltinBrowser: _notify_glory_disconnected failed: %s", exc)

    # ── Public API ──

    def _apply_recovered_token(self, token: str) -> None:
        """网关自愈后拿到 token：更新并只加载一次（主线程槽）。"""
        try:
            if not token:
                return
            self.set_token(token)
            url = QUrl(self._gateway_url + "/chat")
            url.setQuery("token=" + self._token)
            self._pin_dark_background()
            self._view.load(url)
        except Exception as exc:                                     # noqa: BLE001
            logger.warning("BuiltinBrowser: apply recovered token failed: %s", exc)

    def go_home(self) -> None:
        """保存配置后回首页（/chat），带 token 一次加载。"""
        try:
            url = QUrl(self._gateway_url + "/chat")
            if self._token:
                url.setQuery("token=" + self._token)
            self._pin_dark_background()
            self._view.load(url)
        except Exception as exc:                                     # noqa: BLE001
            logger.warning("BuiltinBrowser: go_home failed: %s", exc)

    def set_token(self, token: str) -> None:
        self._token = token
        self._token_indicator.setText(tr("ui.browser.token_yes"))
        self._token_indicator.setStyleSheet("color:#5a9e6f;font-size:11px;")

    def refresh_token_from_gateway(self) -> str:
        """网关重启后重新取 token（壳层唯一刷新口；取不到就保留旧值）。"""
        try:
            from main.main import refresh_token as _refresh
            token = _refresh()
            if token:
                self.set_token(token)
                try:
                    stk("browser.token_refresh", "ok", ok=True)
                except Exception:
                    pass
                return token
        except Exception as exc:
            try:
                stk("browser.token_refresh", f"failed: {exc}", ok=False)
            except Exception:
                pass
        return ""

    def _load_with_new_token(self) -> None:
        """用刚取到的 token 重新载入（网关重启完成后的唯一正确重载口）。"""
        url = QUrl(self._gateway_url + self._start_page)
        if self._token:
            url.setQuery(f"token={self._token}")
        self._pin_dark_background()
        self._view.load(url)

    def navigate(self, url: str) -> None:
        self._gateway_url = url
        self._view.load(QUrl(url))

    def connection_healthy(self) -> bool:
        return self._view.isVisible() and self._placeholder.isHidden()
