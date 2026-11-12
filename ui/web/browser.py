# L1-3450：Built-in browser view — QWebEngineView wrapper that connects to Gateway.
r"""Built-in browser view — QWebEngineView wrapper that connects to Gateway."""















from __future__ import annotations

try:
    from lib.no_window import install as _install_no_window
    _install_no_window()
except Exception:
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

from PySide6.QtCore import QFileSystemWatcher, QUrl, Qt, Signal, QObject, QThread, QTimer
from PySide6.QtGui import QFont, QDragEnterEvent, QDropEvent, QIcon
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineSettings, QWebEngineScript
try:
    from PySide6.QtWebEngineCore import QWebEngineLoadingInfo
except Exception:
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



from main.main import stk


from ui.i18n import tr

logger = logging.getLogger(__name__)

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_WSL_DISTRO = os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu"
_WEBVIEW_CFG_PATH = os.path.join(_BASE, "ui", "web", "webview.json")


_USER_IDENTITY_PATH = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia", "user_identity.json")


_GW_CFG_PATH = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia", "gateway_config.json")


# L83-84：_rheia_app_root — _rheia_app_root
def _rheia_app_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# L88-101：_slot_license_ok — 授权在不在：key 在，且没被绑到别的机器上（绑定判据在 persona.slot_discovery）。
def _slot_license_ok(app: str = "") -> bool:
    r"""授权在不在：key 在，且没被绑到别的机器上（绑定判据在 persona.slot_discovery）。"""



    try:
        app = app or _rheia_app_root()
        if app not in sys.path:
            sys.path.insert(0, app)
        from persona.slot_discovery import license_status
        ok, _why = license_status(app)
        return bool(ok)
    except Exception:
        return False


# L105-111：_slot_seed_present — _slot_seed_present
def _slot_seed_present(app: str = "") -> bool:
    try:
        app = app or _rheia_app_root()
        seed = (os.environ.get("RHEIA_PERSONAL_SLOT_SEED") or "").strip() or os.path.join(app, "RheiaPersonalSlot")
        return os.path.isfile(seed)
    except Exception:
        return False


# L115-136：_personal_slot_available — 付费插槽在该不该出现 —— 照 10-27 产品树的三条判据：
def _personal_slot_available() -> bool:
    r"""付费插槽在该不该出现 —— 照 10-27 产品树的三条判据："""






    try:
        app = _rheia_app_root()


        if not _slot_license_ok(app):
            return False
        if os.path.isfile(os.path.join(app, "personal-slot", "openclaw.plugin.json")):
            return True
        src = (os.environ.get("RHEIA_PERSONAL_SLOT") or "").strip()
        if src and os.path.isdir(src):
            return True
        return _slot_seed_present(app)
    except Exception:
        return False


# L140-152：_gpu_render_enabled — GPU 渲染是否开启（默认开）。读 gateway_config.json 的 gpu_render 字段。
def _gpu_render_enabled() -> bool:
    r"""GPU 渲染是否开启（默认开）。读 gateway_config.json 的 gpu_render 字段。"""




    try:
        if os.path.isfile(_GW_CFG_PATH):
            with open(_GW_CFG_PATH, "r", encoding="utf-8") as f:
                return json.load(f).get("gpu_render", True) is not False
    except Exception:
        pass
    return True


# L156-171：_set_gpu_render — 写 gpu_render 到 gateway_config.json（读-改-写，不覆盖 url/token）。
def _set_gpu_render(enabled: bool) -> None:
    r"""写 gpu_render 到 gateway_config.json（读-改-写，不覆盖 url/token）。"""
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


# L175-189：_read_user_identity — _read_user_identity
def _read_user_identity() -> dict:

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


# L193-206：_write_user_identity — _write_user_identity
def _write_user_identity(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_USER_IDENTITY_PATH), exist_ok=True)
        with open(_USER_IDENTITY_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass

    try:
        from kernel.scout import setPref, ensureRecoveryTables
        ensureRecoveryTables()
        setPref("user_identity", json.dumps(data, ensure_ascii=False))
    except Exception:
        pass






_AVATAR_HTTP_PORT: int = 0
_AVATAR_HTTP_LOCK = threading.Lock()


# L218-222：_AvatarHTTPHandler — 只服务本机 avatars 目录的静态文件（127.0.0.1，进程退出即停）。
class _AvatarHTTPHandler(http.server.SimpleHTTPRequestHandler):
    r"""只服务本机 avatars 目录的静态文件（127.0.0.1，进程退出即停）。"""

    def log_message(self, *args):
        pass


# L226-243：_avatar_base_url — 确保头像 http 服务在跑，返回 base（http://127.0.0.1:<port>）；失败返回空串。
def _avatar_base_url() -> str:
    r"""确保头像 http 服务在跑，返回 base（http://127.0.0.1:<port>）；失败返回空串。"""
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
    {"key": "support",         "i18n": "ui.support.title",       "path": "",                             "primary": False, "is_local": True},
]


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


# L278-314：_nav_routes — 返回导航路由：path 从 mani 官方 route table 长出（去硬编），
def _nav_routes() -> list[dict]:
    r"""返回导航路由：path 从 mani 官方 route table 长出（去硬编），"""




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
        return _NAV_ROUTES


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


# L327-332：_load_webview_config — _load_webview_config
def _load_webview_config() -> dict:
    try:
        with open(_WEBVIEW_CFG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


# L336-345：_isChinese — _isChinese
def _isChinese() -> bool:
    try:

        _pkg_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if _pkg_root not in sys.path:
            sys.path.insert(0, _pkg_root)
        from ui.i18n import currentLanguage
        return currentLanguage() == "zh"
    except Exception:
        return True


# L349-356：_is_simplified_chinese — 操作系统是否为简体中文（zh-CN）。非简体中文系统 → 抛弃国内频道模块。
def _is_simplified_chinese() -> bool:
    r"""操作系统是否为简体中文（zh-CN）。非简体中文系统 → 抛弃国内频道模块。"""
    try:
        from main.main import is_simplified_chinese
        return is_simplified_chinese()
    except Exception:

        return False




# L362-406：_NavButton — _NavButton
class _NavButton(QPushButton):
    def __init__(self, label: str, icon: str, parent=None):
        super().__init__(parent)
        self._label = label
        self._icon = icon
        self._expanded = False
        self.setCheckable(True)



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

            self.setText("")
            self.setToolTip(self._label)
            self.setFixedSize(40, 36)
        else:
            self.setText(f"  {self._label}")
            self.setToolTip("")
            self.setFixedHeight(36)
            self.setMinimumWidth(140)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


# L410-433：_run_wsl_check — 跑一条 WSL 命令：True/False = 命令成败；None = 壳自己的子进程没跑起来。
def _run_wsl_check(cmd: str) -> bool | None:
    r"""跑一条 WSL 命令：True/False = 命令成败；None = 壳自己的子进程没跑起来。"""








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


# L437-444：_run_wsl_for_avatar — 在 WSL 中执行一条 CLI 命令，返回是否成功。
def _run_wsl_for_avatar(cmd: str) -> bool:
    r"""在 WSL 中执行一条 CLI 命令，返回是否成功。"""





    return _run_wsl_check(cmd) is True


# L448-467：_run_wsl_out — 跑一条 WSL 命令并回它的 stdout（失败 ⇒ 空串）。
def _run_wsl_out(cmd: str) -> str:
    r"""跑一条 WSL 命令并回它的 stdout（失败 ⇒ 空串）。"""




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


# L471-478：_win_to_wsl_path — 把 Windows 盘路径换成 WSL 里的 /mnt/<盘>/…（给 WSL 内 cp 用）；已是 WSL 路径 ⇒ 原样。
def _win_to_wsl_path(p: str) -> str:
    r"""把 Windows 盘路径换成 WSL 里的 /mnt/<盘>/…（给 WSL 内 cp 用）；已是 WSL 路径 ⇒ 原样。"""
    _p = str(p or "")
    if _p.startswith("/"):
        return _p
    if len(_p) > 1 and _p[1] == ":":
        return "/mnt/" + _p[0].lower() + "/" + _p[2:].replace(chr(92), "/").lstrip("/")
    return _p.replace(chr(92), "/")


# L482-509：_slots_current_agent — 当前该被改身份的那个号。
def _slots_current_agent() -> str:
    r"""当前该被改身份的那个号。"""





    try:
        from platform.slotApi import read_slot_window
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



_AGENT_WS_CACHE: dict[str, str] = {}


# L517-548：_agent_workspace_wsl — 那个号的工作区（WSL 绝对路径）。
def _agent_workspace_wsl(agent_id: str) -> str:
    r"""那个号的工作区（WSL 绝对路径）。"""








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




# L554-841：_FailedSkillsWidget — 侧边栏尾部：显示 bonus skills 安装失败的 skill slug。
class _FailedSkillsWidget(QWidget):
    r"""侧边栏尾部：显示 bonus skills 安装失败的 skill slug。"""





    missingReady = Signal(object)

    _FAILED_LIST = os.path.join(
        os.environ.get("PROGRAMDATA", ""), "Rheia", "failed_skills.json")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._expanded = False
        self._collapsed = True
        self._slugs: list[str] = []
        self._dirty = False
        self.missingReady.connect(self._apply_missing)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(1)


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


        self._list_widget = QWidget()
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(8, 0, 0, 0)
        self._list_layout.setSpacing(2)
        self._list_widget.setVisible(False)
        layout.addWidget(self._list_widget)

        self.setVisible(False)

    def _load_failed(self) -> list[str]:

        try:
            from kernel.scout import getRecoveryState, ensureRecoveryTables
            ensureRecoveryTables()
            raw = getRecoveryState("failed_skills")
            if raw:
                return json.loads(raw)
        except Exception:
            pass

        try:
            with open(self._FAILED_LIST, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

    def refresh(self) -> None:
        r"""显示逻辑（CLI 印证版）：问 WSL CLI 要已安装名单 ↔ 预装名单印证。"""








        if self.isVisible():
            self.setVisible(False)
        self._slugs = []
        import threading as _th
        _th.Thread(target=self._verify_async, daemon=True,
                   name="failed-skills-verify").start()

    def _verify_async(self) -> None:
        r"""后台：逐项问 CLI 验证预装名单，得出真缺列表。None=CLI 验证失败。"""
        missing: list[str] | None = None
        try:
            pre = self._load_preinstalled()
            if not pre:
                missing = []
            else:
                missing = [n for n in pre if not self._check_installed(n)]
        except Exception:
            missing = None
        self.missingReady.emit(missing)

    def _load_preinstalled(self) -> list[str]:
        r"""预装名单：单一真相 = main.py bonus_skills 分节的 _PREINSTALL_NAMES。"""
        try:
            from main.main import _PREINSTALL_NAMES
            return [str(x) for x in _PREINSTALL_NAMES]
        except Exception:
            return []

    def _check_installed(self, name: str) -> bool:
        r"""问 WSL：该预装项是否已装（skill 落点 test -d / plugin CLI list 印证）。"""





        try:


            slug = name.split("/")[-1]
            _rc = _run_wsl_check(
                f"test -d \"$HOME/.openclaw/workspace/skills/{name}\" && echo OK "
                f"|| test -d \"$HOME/.openclaw/workspace/skills/{slug}\" && echo OK")
            if _rc is None or _rc:
                return True



            try:
                from main.main import cli_usage
                plist = cli_usage("plugins", "list") or "openclaw plugins list"
            except Exception:
                plist = "openclaw plugins list"
            bare = name.split("/")[-1]
            cands = {bare.lower(),
                     bare.lower().replace("-provider", "").replace("-plugin", "")}
            raw = _run_wsl_out(f"{plist} --json 2>/dev/null")
            if not raw:
                return True
            try:
                plugins = (json.loads(raw) or {}).get("plugins", [])
            except (json.JSONDecodeError, TypeError, ValueError):
                return True
            for p in plugins:
                if str(p.get("origin", "")).lower() == "bundled":
                    continue
                names = {str(p.get(k, "")).lower() for k in ("id", "name")}
                pkg = p.get("package") or {}
                if isinstance(pkg, dict):
                    names.add(str(pkg.get("name", "")).lower())
                for got in list(names):
                    if got and (got in cands or got.split("/")[-1] in cands):
                        return True
        except Exception:

            return True
        return False

    def _apply_missing(self, missing: list[str] | None) -> None:
        r"""主线程：按真缺名单构建子列表。missing=None（CLI 验证失败）→ 回退 failed_skills.json。"""





        if missing is None:
            missing = self._load_failed()
        _changed = list(missing) != list(self._slugs)
        self._slugs = list(missing)
        if getattr(self, '_collapsed', True):
            if _changed:
                self._dirty = True
            return
        if not _changed and self._list_layout.count():
            return

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
        r"""从失败列表中移除指定 slug 并刷新。"""
        current = self._load_failed()
        if slug in current:
            current.remove(slug)

        try:
            from kernel.scout import setRecoveryState, ensureRecoveryTables
            ensureRecoveryTables()
            setRecoveryState("failed_skills", json.dumps(current, ensure_ascii=False))
        except Exception:
            pass

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
        r"""侧边栏折叠时完全隐藏，展开时才显示（有 failed slugs 时）。"""
        self._collapsed = collapsed
        if collapsed:
            self.setVisible(False)
        else:
            if getattr(self, "_dirty", False):
                self._dirty = False
                self._apply_missing(self._slugs)
            self._list_widget.setVisible(self._expanded)
            self._header_btn.setText(
                tr("ui.browser.uninstalled_skills_down") if self._expanded else tr("ui.browser.uninstalled_skills_right"))
            self._header_btn.setToolTip("")


            if self._header_btn.minimumWidth() != 148:
                self._header_btn.setFixedSize(148, 28)
            self.setVisible(bool(self._slugs))




# L847-1752：_Sidebar — _Sidebar
class _Sidebar(QWidget):

    identity_changed = Signal()

    def __init__(self, gateway_url: str, navigate_fn, parent=None):
        super().__init__(parent)
        self._gateway_url = gateway_url
        self._navigate = navigate_fn
        self._collapsed = True
        self._buttons: list[_NavButton] = []
        self._more_buttons: list[_NavButton] = []
        self._active_key: str | None = None
        self._persona_dialog = None
        self._persona_host = None
        self._persona_host_restarts = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 8, 6, 8)
        layout.setSpacing(2)

        zh = _isChinese()




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

            _rheia_btn._updateStyle(self._collapsed)

        self._sep = QLabel()
        self._sep.setFixedHeight(1)
        self._sep.setStyleSheet(f"background: {_SIDEBAR_HOVER}; margin: 4px 8px;")
        self._sep.setVisible(False)
        layout.addWidget(self._sep)

        for route in _nav_routes():


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



        self._failed_skills = _FailedSkillsWidget()


        self._custom_sep = QLabel()
        self._custom_sep.setFixedHeight(1)
        self._custom_sep.setStyleSheet(f"background: {_SIDEBAR_HOVER}; margin: 4px 8px;")
        self._custom_sep.setVisible(False)
        layout.addWidget(self._custom_sep)

        self._custom_buttons: list[_NavButton] = []
        self._custom_slot: _NavButton | None = None


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
        self._refresh_avatar_button()
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



        self._refresh_custom_area()

        self.setFixedWidth(_SIDEBAR_WIDTH)
        self.setStyleSheet(f"_Sidebar {{ background: {_SIDEBAR_BG}; }}")


        for btn in self._buttons:
            btn._updateStyle(True)

    def _on_navigate(self, route: dict) -> None:
        self._active_key = route["key"]
        self._navigate(route["path"])
        self._apply_active(self._active_key)

    def _apply_active(self, key: str | None) -> None:
        r"""按 key 精准高亮对应按钮，其他按钮全部熄灭。"""




        all_btns = self._buttons + self._more_buttons + self._custom_buttons
        if self._custom_slot:
            all_btns.append(self._custom_slot)
        for btn in all_btns:
            btn.setChecked(hasattr(btn, "_route_key") and btn._route_key == key)

    def _clear_active(self) -> None:
        r"""弹窗关闭后清除所有侧边栏高亮。"""
        self._active_key = None
        self._apply_active(None)

    def set_active(self, key: str) -> None:
        self._active_key = key
        self._apply_active(key)

    def _find_button_by_route_key(self, key: str) -> _NavButton | None:
        r"""在侧边栏三个按钮列表中按路由 key 查找按钮。"""
        all_btns = self._buttons + self._more_buttons + self._custom_buttons
        for btn in all_btns:
            if hasattr(btn, "_route_key") and btn._route_key == key:
                return btn
        return None



    def _set_panel_open(self, open_: bool) -> None:
        r"""功能页打开期间禁用侧边栏所有按钮，关闭后恢复。"""




        all_btns = self._buttons + self._more_buttons + self._custom_buttons
        for btn in all_btns:
            btn.setEnabled(not open_)
        if self._custom_slot is not None:
            self._custom_slot.setEnabled(not open_)

    def _dispatch_local_action(self, route: dict) -> None:
        r"""侧边栏本地面板分发（is_local 条目）。"""



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
            elif key == "support":
                self._open_support()
        finally:

            self._apply_active(self._active_key)

    def _open_memory(self) -> None:
        r"""打开记忆面板（非模态 + 侧边栏禁用 + 居中于主窗口）。"""
        try:
            from platform.slotApi import memoryPanelClass
            MemoryPanel = memoryPanelClass()
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

    def _open_support(self) -> None:
        r"""打开支持开发面板（非模态 + 侧边栏禁用 + 居中于主窗口）。"""
        try:
            from ui.web.support_page import SupportPanel
            parent = self.parent()
            panel = SupportPanel(parent=parent if parent else self)
            self._set_panel_open(True)
            panel.finished.connect(lambda _: self._set_panel_open(False))
            panel.show()
            self._center_over_parent(panel, parent)
            panel.raise_()
            panel.activateWindow()
        except Exception as e:
            logger.warning("_Sidebar: _open_support failed (non-fatal): %s", e)

    def _center_over_parent(self, panel, parent) -> None:
        r"""非模态面板 show() 不会像 exec() 那样自动居中，手动把它居中到父窗口。"""
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
        r"""打开中国国内频道配置面板（非模态 + 侧边栏禁用 + 居中于主窗口）。"""
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
        r"""构造 ID 按钮的 QMenu：Agent（头像+显示名）一组 / 用户（头像+显示名）一组。"""
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



        act_agent_avatar = menu.addAction(tr("ui.browser.agent_avatar"))
        act_agent_avatar.triggered.connect(self._pick_agent_avatar)
        act_agent_name = menu.addAction(tr("ui.browser.agent_display_name"))
        act_agent_name.triggered.connect(self._edit_agent_name)
        menu.addSeparator()

        act_user_avatar = menu.addAction(tr("ui.browser.user_avatar"))
        act_user_avatar.triggered.connect(self._pick_user_avatar)
        act_user_name = menu.addAction(tr("ui.browser.user_display_name"))
        act_user_name.triggered.connect(self._edit_user_name)
        menu.addSeparator()

        act_gpu = menu.addAction(tr("ui.browser.gpu_render"))
        act_gpu.setCheckable(True)
        act_gpu.setChecked(_gpu_render_enabled())
        act_gpu.triggered.connect(self._toggle_gpu_render)

        self._avatar_btn.setMenu(menu)

    def _toggle_gpu_render(self, enabled: bool) -> None:
        r"""切换 GPU 渲染开关：写配置 + 提示重启生效。"""
        _set_gpu_render(enabled)
        QMessageBox.information(
            self,
            tr("ui.browser.gpu_render"),
            tr("ui.browser.gpu_restart_hint"),
        )

    def _refresh_avatar_button(self) -> None:
        r"""把用户头像显示到 ID 按钮上（有头像则覆盖文字，无头像则显示 ID）。"""




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

        self._avatar_btn.setIcon(QIcon())
        self._avatar_btn.setText("ID")



    def _pick_agent_avatar(self) -> None:
        r"""打开文件选择器，将选中图片拷入 WSL workspace/avatars/，执行 agents set-identity。"""
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

        try:
            _pg_avatar_dir = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia", "avatar")
            os.makedirs(_pg_avatar_dir, exist_ok=True)
            shutil.copy2(path, os.path.join(_pg_avatar_dir, dest_name))
        except OSError:
            pass


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


        identity = _read_user_identity()
        identity["avatar"] = dest
        _write_user_identity(identity)


        self._refresh_avatar_button()
        self.identity_changed.emit()

        QMessageBox.information(
            self,
            tr("ui.browser.user_avatar_updated"),
            f"{tr('ui.browser.user_avatar_saved')}{dest_name}",
        )



    def _edit_agent_name(self) -> None:
        r"""QInputDialog 输入 Agent 显示名 → 官方 agents set-identity --agent <她的号> --name。"""




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



    def _edit_user_name(self) -> None:
        r"""QInputDialog 输入用户显示名 → 写入 user_identity.json。"""
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

        self._refresh_avatar_button()
        self.identity_changed.emit()

        QMessageBox.information(
            self,
            tr("ui.browser.user_name_updated"),
            f"{tr('ui.browser.user_name_saved')}{name}",
        )

    def _toggle(self) -> None:
        r"""收放侧栏。"""






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
        except Exception:
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

            if hasattr(self, "_failed_skills"):
                self._failed_skills.set_collapsed(self._collapsed)

            self._custom_sep.setVisible(not self._collapsed and len(self._custom_buttons) > 0)
            for btn in self._custom_buttons:
                btn.setVisible(not self._collapsed)
            if self._custom_slot:
                self._custom_slot.setVisible(not self._collapsed)
        finally:
            try:
                if _win_frozen and _win is not None:
                    _win.setUpdatesEnabled(_win_prev)
            except Exception:
                pass
            self.setUpdatesEnabled(_side_prev)

            self.update()
            try:
                if _win is not None:
                    _win.update()
            except Exception:
                pass

    def _open_provider_setup(self) -> None:
        try:
            from ui.web.provider_setup import ProviderSetupCard
            parent = self.parent()
            card = ProviderSetupCard(parent=parent if parent else self)
            card.setup_completed.connect(self._on_provider_updated)


            self._set_panel_open(True)
            card.destroyed.connect(lambda: (self._clear_active(), self._set_panel_open(False)))
            card.show()
            card.raise_()
            card.activateWindow()
        except Exception as e:
            logger.warning("_Sidebar: _open_provider_setup failed (non-fatal): %s", e)
            self._clear_active()

    def _on_provider_updated(self, _token: str) -> None:
        r"""Provider 配置保存后触发；找到带 _view 的父直接 reload（页面级硬编）。"""
        try:
            stk("provider.saved", f"token={'present' if _token else 'MISSING'}", ok=bool(_token))
            p = self.parent()
            while p is not None and not hasattr(p, "_view"):
                p = p.parent()
            if p is not None and hasattr(p, "_view"):






                if hasattr(p, "_request_reload"):
                    p._request_reload("provider.saved")
                else:
                    logger.warning(
                        "_Sidebar: 父窗无 _request_reload，跳过整页重载（避免白闪）")

            if p is not None and hasattr(p, "_start_bonus_skills"):
                p._start_bonus_skills()
        except Exception as e:
            logger.warning("_Sidebar: _on_provider_updated failed (non-fatal): %s", e)



    def _refresh_custom_area(self) -> None:
        r"""从 sidebar_custom.json 读配置，重建客制区条目按钮。"""





        try:
            from ui.web.custom_area_manager import get_enabled_entries
            entries = get_enabled_entries()
        except Exception:
            entries = []

        _sig = [(e.get("id"), e.get("label_zh"), e.get("label_en"), e.get("emblem"))
                for e in entries]
        if _sig == getattr(self, "_custom_entries_sig", None):

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
                    continue
                _lkey = str(entry.get("label_key") or "")

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


        if self.layout().indexOf(self._failed_skills) < 0:
            av_idx = self.layout().indexOf(self._avatar_btn)
            if av_idx >= 0:
                self.layout().insertWidget(av_idx, self._failed_skills)
            else:
                self.layout().addWidget(self._failed_skills)



    def ensure_rheia_entry(self) -> None:
        r"""两文件到位后不重启壳：文件事件进来时补挂 Rheia 入口。"""




        if getattr(self, "_rheia_btn", None) is not None:
            return
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

            btn._updateStyle(self._collapsed)
        except Exception:
            pass

    def _logPersonaHost(self, msg: str, ok: bool = True) -> None:
        try:
            from main.main import stk as _stk_host
            _stk_host("persona.host", msg, ok=ok)
        except Exception:
            pass

    def _startPersonaHost(self) -> None:
        """在独立进程打开人格页：子进程 abort 只死子进程，壳不动。"""
        _proc = getattr(self, "_persona_host", None)
        if _proc is not None and _proc.poll() is None:
            return
        _app = _rheia_app_root()
        _script = os.path.join(_app, "ui", "web", "personaHost.py")
        if not os.path.isfile(_script):
            self._logPersonaHost("宿主脚本不存在：%s" % _script, ok=False)
            return
        _env = dict(os.environ)
        _env["PYTHONPATH"] = _app + os.pathsep + _env.get("PYTHONPATH", "")
        _flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            _proc = subprocess.Popen(
                [sys.executable, _script],
                cwd=_app, env=_env, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=_flags, close_fds=True,
            )
        except Exception as _e:
            self._logPersonaHost("启动失败：%s: %s" % (type(_e).__name__, _e), ok=False)
            return
        self._persona_host = _proc
        self._logPersonaHost("已启动 pid=%s" % _proc.pid, ok=True)
        _th = threading.Thread(target=self._watchPersonaHost, args=(_proc,),
                               daemon=True, name="persona-host-watch")
        _th.start()

    def _watchPersonaHost(self, proc) -> None:
        """子进程退出是确定信号；非零退出补一个新宿主，正常关闭不补。"""
        _rc = proc.wait()
        if getattr(self, "_persona_host", None) is proc:
            self._persona_host = None
        if _rc == 0:
            return
        self._logPersonaHost("宿主异常退出 rc=%s" % _rc, ok=False)
        if getattr(self, "_persona_host_restarts", 0) >= 3:
            self._logPersonaHost("连续异常已达上限，停止补进程", ok=False)
            return
        self._persona_host_restarts = int(getattr(self, "_persona_host_restarts", 0)) + 1
        self._startPersonaHost()

    def _clear_persona_dialog(self, dlg) -> None:
        if getattr(self, "_persona_dialog", None) is dlg:
            self._persona_dialog = None

    def _dispatch_custom_action(self, entry: dict) -> None:
        r"""客制区条目点击分发。按 type 派发（设计本意：preset/script/url/intent）。"""







        eid = entry.get("id", "")
        etype = entry.get("type", "")
        try:
            from main.main import stk as _stk
            _stk("sidebar.custom", f"点击客制区入口 eid={eid} type={etype}", ok=True)
        except Exception:
            pass
        self._apply_active(eid)
        try:
            if eid == "persona":

                if not _personal_slot_available():
                    return


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
                        except Exception as _de:
                            try:
                                stk("sidebar.persona.discover",
                                    "发现异常：%s: %s" % (type(_de).__name__, _de), ok=False)
                            except Exception:
                                pass

                    threading.Thread(target=_fire_slot_discover, daemon=True,
                                     name="slot-discover").start()
                except Exception:
                    pass
                self._startPersonaHost()
                return
            elif eid == "provider_panel":
                try:
                    from ui.web.provider_panel import ProviderPanel
                    dlg = ProviderPanel(self)
                    parent_browser = self.parent()
                    dlg.provider_saved.connect(self._on_sidebar_provider_saved)


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
        r"""执行客制区 script 条目：Popen 外部命令。首次执行弹确认（可记住）。"""
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
        r"""打开 intent 条目：回到需求澄清面板（继续对话 / 交付生成）。"""





        try:
            from ui.web.custom_area_manager import CustomAreaManager
            dlg = CustomAreaManager(parent=self.parent() or self,
                                    edit_entry_id=entry.get("id", ""))
            dlg.changed.connect(self._refresh_custom_area)
            dlg.exec()
        except Exception as e:
            logger.warning("_Sidebar: intent 面板打开失败 — %s", e)

    def _on_sidebar_provider_saved(self, _token: str) -> None:
        r"""侧边栏 provider_panel 保存成功后，标记父浏览器已配置，避免重复弹配置卡。"""
        parent_browser = self.parent()
        if isinstance(parent_browser, BuiltinBrowser):
            parent_browser._provider_configured_once = True

    def _open_custom_manager(self) -> None:
        r"""打开图形化管理面板，变更后自动刷新客制区。"""
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
        r"""导入备份后：先重启 Gateway（写回的 key 需重启才生效），"""





        try:
            p = self.parent()
            while p is not None and not hasattr(p, "_view"):
                p = p.parent()
            if p is not None and hasattr(p, "_view"):
                p._restart_gateway()
                logger.info("_Sidebar: restore done — gateway restart triggered")



                _req = getattr(p, "_request_reload", None)
                if _req is not None:
                    _req("restore")
                else:
                    logger.warning(
                        "_Sidebar: 父窗无 _request_reload，导入后跳过整页重载（避免白闪）")
            else:
                logger.warning("_Sidebar: BuiltinBrowser not found for reload after restore")
        except Exception as e:
            logger.warning("_Sidebar: _reload_after_restore failed: %s", e)




# L1758-3450：BuiltinBrowser — QWebEngineView-based browser that renders the Gateway Control UI.
class BuiltinBrowser(QMainWindow):
    r"""QWebEngineView-based browser that renders the Gateway Control UI."""





    self_heal_restart = Signal()

    gateway_wake_started = Signal()
    gateway_wake_finished = Signal(bool)



    self_heal_done = Signal()
    token_ready = Signal(str)

    def __init__(
        self,
        gateway_url: str = "http://127.0.0.1:18789",
        token: str = "",
        start_page: str = "/chat",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.self_heal_restart.connect(self._on_self_heal_restart)
        self.gateway_wake_started.connect(self._on_gateway_wake_started)
        self.gateway_wake_finished.connect(self._on_gateway_wake_finished)
        cfg = _load_webview_config()
        win = cfg.get("window", {})
        loading_cfg = cfg.get("loading", {})
        features = cfg.get("features", {})
        ua = cfg.get("user_agent", {})

        self._gateway_url = gateway_url.rstrip("/")


        self._startup_gateway_preparing = True


        self._gateway_wake_finished = False
        self._token = token
        self._start_page = start_page







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
        self._path_fallback_done = False
        self._load_fail_count = 0

        self._reconnect_pulse_active = False
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.setInterval(1000)
        self._reconnect_timer.timeout.connect(self._do_reconnect_probe)
        self._provider_check_done = False
        self._provider_configured_once = False
        self._provider_unknown_hits = 0
        self._self_heal_ok = False
        self._bonus_pending = False
        self.self_heal_done.connect(self._on_self_heal_done)
        self.token_ready.connect(self._apply_recovered_token)


        self._bonus_skills_pending = False

        self.setWindowTitle(win.get("title", "Rheia"))

        from ui.theme import fitToScreen
        fitToScreen(self, 0.8, 0.85)

        h_layout = QHBoxLayout()
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(0)

        self._sidebar = _Sidebar(self._gateway_url, self._navigate_to)

        try:
            self._sidebar.ensure_rheia_entry()
        except Exception:
            pass


        try:
            self._slot_watcher = QFileSystemWatcher(self)
            _watch_root = _rheia_app_root()
            if os.path.isdir(_watch_root):
                self._slot_watcher.addPath(_watch_root)
            self._slot_watcher.directoryChanged.connect(self._on_slot_files_changed)
            self._slot_watcher.fileChanged.connect(self._on_slot_files_changed)
        except Exception:
            self._slot_watcher = None

        self._sidebar.identity_changed.connect(self._sync_identity_to_rpc)
        h_layout.addWidget(self._sidebar)


        self._restore_appearance()


        try:
            from main.main import (
                refresh_failed_skills)
            refresh_failed_skills(self._sidebar._failed_skills.refresh)

            self._cleanup_failed_skills_on_connect()
        except Exception:
            pass

        main_widget = QWidget()
        layout = QVBoxLayout(main_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)


        self._status_bar = QWidget()
        self._status_bar.setFixedHeight(28)
        self._status_bar.setStyleSheet("background-color:#1a1a1a;")
        status_layout = QHBoxLayout(self._status_bar)
        status_layout.setContentsMargins(12, 0, 12, 0)
        status_layout.setSpacing(8)


        self._dot_container = QWidget()
        self._dot_container.setFixedSize(22, 22)

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

        self._connection_status = QLabel(tr("ui.browser.gw_preparing"))
        self._connection_status.setStyleSheet("color:#b0b0b0;font-size:11px;")
        status_layout.addWidget(self._connection_status, 0, Qt.AlignmentFlag.AlignVCenter)

        status_layout.addStretch()


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
        self._retry_restart_btn.clicked.connect(lambda: self._restart_gateway(manual=True))
        self._retry_restart_btn.setVisible(False)
        status_layout.addWidget(self._retry_restart_btn)

        status_layout.addStretch()

        self._token_indicator = QLabel(tr("ui.browser.token_yes") if token else tr("ui.browser.token_no"))
        token_color = "#5a9e6f" if token else "#6a5e52"
        self._token_indicator.setStyleSheet(f"color:{token_color};font-size:11px;")
        status_layout.addWidget(self._token_indicator)

        layout.addWidget(self._status_bar)


        self._progress = QProgressBar()
        self._progress.setFixedHeight(3)
        self._progress.setTextVisible(False)
        self._progress.setStyleSheet(
            "QProgressBar { background-color:#2a2a2a; border:none; }"
            "QProgressBar::chunk { background-color:#f37021; }"
        )
        self._progress_enabled = bool(loading_cfg.get("show_progress_bar", True))
        self._progress_epoch = 0

        self._progress.setVisible(False)



        self._progress_target = 0
        self._progress_shown = 0
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(60)
        self._progress_timer.timeout.connect(self._tick_load_progress)
        layout.addWidget(self._progress)


        self._placeholder = QLabel(loading_cfg.get("placeholder_text", tr("ui.browser.loading")))
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("color:#b0b0b0;font-size:14px;")
        layout.addWidget(self._placeholder, stretch=1)


        profile = QWebEngineProfile.defaultProfile()
        if ua.get("override", ""):
            profile.setHttpUserAgent(ua["override"])









        try:
            _cache_dir = os.path.join(
                os.environ.get("PROGRAMDATA", ""), "Rheia", "webcache")
            profile.setCachePath(_cache_dir)
            profile.setHttpCacheMaximumSize(100 * 1024 * 1024)
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


        if not _gpu_render_enabled():
            try:






                settings.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, False)
                settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, False)
            except Exception:
                pass


        self._view = QWebEngineView()




        self._pin_dark_background()
        self._view.setAcceptDrops(True)
        self._view.setVisible(False)






        try:
            from PySide6.QtCore import QEvent as _QE, QObject as _QO
            _EVN = {
                int(_QE.Type.Resize): "resize", int(_QE.Type.Move): "move",
                int(_QE.Type.Show): "show", int(_QE.Type.Hide): "hide",
                int(_QE.Type.WindowStateChange): "winstate",
                int(_QE.Type.LayoutRequest): "layout",
            }

            class _FlickerSpy(_QO):
                r"""只记「画面被动了」的四类事件，不碰业务、不阻塞（只写账）。"""

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
                    except Exception:
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
        except Exception:
            pass



        self._view.loadStarted.connect(self._on_load_started)
        self._view.loadProgress.connect(self._on_load_progress)
        self._view.loadFinished.connect(self._on_load_finished)
        try:
            if QWebEngineLoadingInfo is not None:
                self._view.page().loadingChanged.connect(self._on_loading_changed)
        except Exception:
            pass


        try:
            from main.tlc import report_signal as _sig
            self._view.renderProcessTerminated.connect(
                lambda status, code: _sig("render_term",
                                          "status=%s code=%s" % (status, code)))
        except Exception:
            pass



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



        try:
            self._sidebar._failed_skills.refresh()
        except Exception:
            pass


        try:
            self._view.urlChanged.connect(self._remember_path)
        except Exception:
            pass


        url = QUrl(self._gateway_url + self._start_page)
        if self._token:
            url.setQuery(f"token={self._token}")
        self._view.load(url)

    def _remember_path(self, url) -> None:
        r"""把当前页面的路径记进壳偏好（官方设计：会话身份 = URL 路径）。"""




        try:
            p = url.path() or ""
            if not p.startswith("/") or len(p) > 512 or p == self._last_path:
                return
            from kernel.scout import setPref
            setPref("web_last_path", p)
            self._last_path = p
        except Exception:
            pass



    def _tlc_heartbeat(self) -> None:
        r"""tlc 心跳：UI 事件循环活着 = 壳体没僵死。"""
        try:
            from main.tlc import heartbeat
            heartbeat()
        except Exception:
            pass

    def _sync_identity_to_rpc(self) -> None:
        r"""把用户身份（显示名 + 头像）同步到 OpenClaw 官方个人资料。"""





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
        r"""启动时读外观状态：优先本地 appearance.json，旧库只作兜底。"""
        try:
            import json as _json
            p = self._appearance_path()
            prefs = {}
            try:
                with open(p, "r", encoding="utf-8") as f:
                    prefs = _json.load(f) or {}
            except Exception:
                prefs = {}
            if not prefs:
                from kernel.scout import getAllPrefs, ensureRecoveryTables
                ensureRecoveryTables()
                prefs = getAllPrefs()
            if not prefs:
                return
            w = int(prefs.get("window_width", 0) or 0)
            h_val = int(prefs.get("window_height", 0) or 0)
            if w > 640 and h_val > 400:
                self.resize(w, h_val)
            collapsed = prefs.get("sidebar_collapsed", "")
            if collapsed in (True, "true") and self._sidebar._collapsed:
                pass
            elif collapsed in (False, "false") and self._sidebar._collapsed:
                self._sidebar._toggle()
        except Exception:
            pass

    def _appearance_path(self) -> str:
        return os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia", "appearance.json")

    def _save_appearance(self) -> None:
        r"""关闭时只做本地原子写：窗口状态 + 侧边栏状态。"""




        import json as _json
        p = self._appearance_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        sz = self.size()
        data = {
            "window_width": int(sz.width()),
            "window_height": int(sz.height()),
            "sidebar_collapsed": bool(self._sidebar._collapsed),
        }
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
        logger.info("BuiltinBrowser: appearance saved locally (%s)", p)

    def closeEvent(self, event) -> None:
        r"""关闭：本地原子写完成就放行；不再等 WSL/DB。"""
        if getattr(self, '_save_done', False):
            event.accept()
            return

        if getattr(self, '_closing', False):
            event.ignore()
            return
        self._closing = True
        try:
            self._save_appearance()
        except Exception as _e:
            logger.warning("BuiltinBrowser: local appearance save failed: %s", _e)
        self._save_done = True
        event.accept()

    def _on_save_done(self, toast: QLabel, timed_out: bool = False) -> None:
        r"""兼容旧调用：本地保存已完成，直接关窗。"""
        if getattr(self, '_save_done', False):
            return
        toast.hide()
        toast.deleteLater()
        self._save_done = True
        self.close()



    @staticmethod
    def _restore_agent_avatar_from_progdata() -> None:
        r"""启动时检查 %PROGRAMDATA%/Rheia/avatar/ 并恢复到 WSL。"""



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



    def _navigate_to(self, path: str) -> None:




        try:
            _cur = (self._view.url().path() or "/").rstrip("/") or "/"
            _tgt = (path or "/").rstrip("/") or "/"
            if _cur == _tgt:
                return
        except Exception:
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



    def _set_dot_state(self, color: str) -> None:
        r"""连接态灯（绿/黄/红/灰）：状态没变就一个字节都不碰。"""





        try:
            _dot = "color:%s;font-size:8px;background:transparent;border:none;" % color
            _ring = "background:transparent;border:2px solid %s;border-radius:11px;" % color
            if self._connection_dot.styleSheet() != _dot:
                self._connection_dot.setStyleSheet(_dot)
            if self._dot_container.styleSheet() != _ring:
                self._dot_container.setStyleSheet(_ring)
        except Exception:
            pass

    def _set_conn_text(self, text: str) -> None:
        r"""状态栏文字：变了才设；启动准备期统一遮成“正在准备 Gateway”。"""
        try:
            if getattr(self, "_startup_gateway_preparing", False):
                text = tr("ui.browser.gw_preparing")
            if self._connection_status.text() != text:
                self._connection_status.setText(text)
        except Exception:
            pass

    def _wait_and_retry(self) -> None:
        r"""Gateway 不可达时的重连脉冲：curl 探活一秒三发，探通即 load，探不通就接着探。"""










        _allow_retry = (not getattr(self, "_startup_gateway_preparing", False)
                        or bool(getattr(self, "_gateway_wake_finished", False)))
        self._retry_manual_btn.setVisible(_allow_retry)
        self._retry_restart_btn.setVisible(_allow_retry)
        self._connection_dot.setStyleSheet("color:#d9a840;font-size:8px;background:transparent;border:none;")
        self._dot_container.setStyleSheet("background:transparent;border:2px solid #d9a840;border-radius:11px;")
        self._reconnect_probe_count = 0
        self._reconnect_pulse_active = True
        if self._reconnect_timer.isActive():
            self._reconnect_timer.stop()
        try:
            stk("browser.retry", "reconnect probe started (auto pulse 1s)", ok=False)
        except Exception:
            pass
        self._do_reconnect_probe()

    def _do_reconnect_probe(self) -> None:
        r"""单次重连探活：curl 根路径看 HTTP 200，通了就 load，不通继续脉冲。"""
        import subprocess as _sp
        import threading

        class _ProbeSig(QObject):
            result = Signal(bool)

        sig = _ProbeSig()
        sig.result.connect(self._on_reconnect_result)

        def _probe() -> None:
            si = _sp.STARTUPINFO()
            si.dwFlags |= _sp.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            alive = False
            try:
                r = _sp.run(
                    f"wsl -d {_WSL_DISTRO} -- bash -c \"curl -s -o /dev/null -w '%{{http_code}}' {self._gateway_url}\"",
                    shell=True, capture_output=True,
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
        r"""主线程回调：处理重连探活结果。探通 load，不通继续脉冲（壳层不替用户修网关）。"""
        if alive:
            self._reconnect_probe_count = 0
            self._reconnect_pulse_active = False
            try:
                self._reconnect_timer.stop()
            except Exception:
                pass
            self._retry_manual_btn.setVisible(False)
            self._retry_restart_btn.setVisible(False)
            self._set_conn_text(f"Gateway · {self._gateway_url}")
            logger.info("BuiltinBrowser: reconnect probe alive, loading page")
            try:
                stk("browser.reconnect", "alive → load", ok=True)
            except Exception:
                pass





            self._pin_dark_background()
            url = QUrl(self._gateway_url + self._start_page)
            if self._token:
                url.setQuery(f"token={self._token}")
            self._view.load(url)
            return

        self._reconnect_probe_count += 1


        self._set_conn_text(tr("ui.browser.gw_waiting"))



        if self._reconnect_probe_count >= 9:
            self._reconnect_probe_count = 0


        if getattr(self, "_reconnect_pulse_active", False) and not self._reconnect_timer.isActive():
            self._reconnect_timer.start()

    def _manual_retry(self) -> None:
        r"""手动触发重连脉冲：立即拍一发（不等下一拍），探通即 load。"""
        try:
            stk("browser.manual_retry", "reconnect probe triggered")
        except Exception:
            pass
        self._reconnect_probe_count = 0
        self._load_fail_count = 0
        self._reconnect_pulse_active = True
        if self._reconnect_timer.isActive():
            self._reconnect_timer.stop()
        self._do_reconnect_probe()

    def reconnect_gateway(self) -> None:
        r"""Gateway 就绪后重连 —— 延迟后 reload 当前页面，让配置视图/模型列表刷新。"""





        self._request_reload("probe")

    def _request_reload(self, source: str = "page") -> None:
        r"""页面重载统一走 TLC 的"重载车道"："""









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
        r"""把页面底色钉成壳的深色底（_SIDEBAR_BG）—— 全壳唯一实现。"""







        try:
            from PySide6.QtGui import QColor as _QC
            self._view.page().setBackgroundColor(_QC(_SIDEBAR_BG))
        except Exception:
            pass

    def _do_reload_page(self) -> None:
        r"""刷新当前页面（等价右键 reload），让 Control UI 重新从 Gateway 拉取配置，"""













        try:
            import traceback as _tb
            _who = [f.name for f in _tb.extract_stack()[:-1]][-3:]
            stk("browser.reload_page", "reload by " + "<-".join(reversed(_who)))
        except Exception:
            pass
        import time as _t
        _now = _t.monotonic()
        if _now - getattr(self, "_last_reload_ts", 0.0) < 15.0:
            return
        self._last_reload_ts = _now
        try:
            stk("browser.reload", "reload triggered", ok=True)
        except Exception:
            pass


        self._pin_dark_background()




        self._view.reload()

    def _restart_gateway(self, *, manual: bool = False) -> None:


        if not manual:
            try:
                stk("browser.gw.restart",
                    "非手动 ⇒ 不自启网关（为系统级重启让路；只允许 sidebar 手动重启）", ok=False)
            except Exception:
                pass
            return
        r"""重启 Gateway 加载新配置 —— 官方 gateway restart（冷启动）。"""










        try:
            from main.main import acquire_lifecycle
            if not acquire_lifecycle("browser"):
                stk("browser.gw.restart", "lifecycle owned elsewhere, skip", ok=False)
                return
            self._gw_lifecycle_owned = True
        except Exception:
            pass


        self.begin_gateway_preparation()
        try:
            stk("browser.gw.restart", "官方 gateway restart（冷启动）", ok=True)
        except Exception:
            pass

        class _Sig(QObject):
            done = Signal(bool)

        sig = _Sig()
        sig.done.connect(self._on_gw_restart_done)

        def _do_restart() -> None:
            ok = False
            try:
                from main.main import restart_gateway_safe
                ok = restart_gateway_safe(manual=True)
            except Exception:
                ok = False
            sig.done.emit(ok)

        threading.Thread(target=_do_restart, daemon=True, name="gw-restart").start()

    def _restart_gateway_owned(self) -> None:
        r"""已持有生命周期锁时重启 Gateway（复用 _restart_gateway 的重启逻辑）。"""
        self._restart_gateway()

    def _on_gw_restart_done(self, ok: bool) -> None:
        r"""Gateway 重启完成回主线程：探通 reload，否则脉冲探活兜底。"""





        if ok:
            self._release_gw_lifecycle()
            try:
                stk("browser.gw.restart", "restart ok → reload", ok=True)
            except Exception:
                pass


            try:
                self._view.reload()
            except Exception:
                self._request_reload("pulse")
        else:
            try:
                stk("browser.gw.restart", "restart 未探活，脉冲探活兜底", ok=False)
            except Exception:
                pass
            self._gateway_probe_count = 30
            self._repair_attempted = False
            self._do_gateway_probe()

    def _do_gateway_probe(self) -> None:
        r"""单次探活：curl 根路径看 HTTP 200，通了就恢复并重载。"""


        import subprocess as _sp
        import threading

        class _ProbeSig(QObject):
            result = Signal(bool)

        sig = _ProbeSig()
        sig.result.connect(self._on_probe_result)

        def _probe() -> None:
            si = _sp.STARTUPINFO()
            si.dwFlags |= _sp.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            alive = False
            try:
                r = _sp.run(
                    f"wsl -d {_WSL_DISTRO} -- bash -c \"curl -s -o /dev/null -w '%{{http_code}}' {self._gateway_url}\"",
                    shell=True, capture_output=True,
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
        r"""主线程回调：处理探活结果。"""
        self._gateway_probe_count -= 1
        self._set_conn_text(tr("ui.browser.gw_waiting"))
        if alive:
            logger.info("BuiltinBrowser: gateway probe alive after restart")
            self._set_dot_state("#5a9e6f")
            self._set_conn_text(f"Gateway · {self._gateway_url}")
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

            if getattr(self, '_bonus_skills_pending', False):
                self._bonus_skills_pending = False
                self._start_bonus_skills()

            self._request_reload("pulse")
        else:
            logger.warning("BuiltinBrowser: gateway restart probe exhausted")
            self._set_dot_state("#e85a4a")
            self._retry_manual_btn.setVisible(False)
            self._retry_restart_btn.setVisible(False)
            try:
                stk("browser.gw.probe", "exhausted", ok=False)
            except Exception:
                pass






            self._repair_attempted = False
            self._release_gw_lifecycle()
            self._wait_and_retry()

    def _release_gw_lifecycle(self) -> None:
        r"""释放 Gateway 生命周期所有权（重启探活链结束，无论成败）。"""
        if getattr(self, "_gw_lifecycle_owned", False):
            try:
                from main.main import release_lifecycle
                release_lifecycle("browser")
            except Exception:
                pass
            self._gw_lifecycle_owned = False

    def _cleanup_failed_skills_on_connect(self) -> None:
        r"""Gateway 连通后执行一次 failed skills 清理（后台线程，不阻塞 UI）。"""


        class _Sig(QObject):
            done = Signal()

        sig = _Sig()
        sig.done.connect(self._sidebar._failed_skills.refresh)

        def _bg_cleanup() -> None:
            try:
                from main.main import cleanup_failed_skills
                cleanup_failed_skills()
            except Exception:
                pass
            sig.done.emit()

        threading.Thread(
            target=_bg_cleanup, daemon=True, name="cleanup-failed-skills").start()



    def _on_self_heal_restart(self) -> None:
        r"""自愈校准完成后，主线程槽：告知用户 + 重启 Windows 程序。"""









        try:
            from ui.i18n import tr
            from main.main import stk as _stk
            _stk("self_heal", "主线程弹文案请求重启", ok=True)

            try:
                import ctypes
                _title = str(tr("ui.browser.notice"))
                _body = str(tr("ui.browser.self_heal_restart"))
                ctypes.windll.user32.MessageBoxW(
                    0, _body, _title, 0x40,
                )
            except Exception as _ne:
                _stk("self_heal", "原生弹窗失败（non-fatal，继续重启）: %s" % _ne)


            try:
                from main.tlc import suppress as _tlc_suppress
                _tlc_suppress("self_heal_restart")
            except Exception:
                pass
            import subprocess as _sp
            import os as _os
            import sys as _sys
            _stk("self_heal", "restarting self...", ok=True)

















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
        r"""自愈判定完成且健康 → 若预装曾挂起则触发。"""
















        self._self_heal_ok = True
        stk("bonus.skills.browser", "self_heal done，预装前置条件满足")
        if getattr(self, '_bonus_pending', False) or getattr(self, '_bonus_skills_pending', False):
            self._bonus_skills_pending = False
            self._start_bonus_skills()

    def _start_bonus_skills(self) -> None:
        r"""后台安装预装 skills 和 Tavily/mem9 插件（模型配置完成 + 自愈完成后触发）。"""






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
                except Exception:
                    pass
            _proc_i = subprocess.Popen([sys.executable, _wk_i], cwd=_root_i, close_fds=True, **_kw_i)


            class _InstallDone(QObject):
                done = Signal()

            _done_i = _InstallDone(self)
            self._bonus_done_sig = _done_i
            _done_i.done.connect(self._refresh_skills_after_install)

            def _wait_install_worker() -> None:
                try:
                    _proc_i.wait()
                finally:
                    try:
                        _done_i.done.emit()
                    except Exception:
                        pass

            threading.Thread(target=_wait_install_worker, daemon=True,
                             name="bonus-skills-wait").start()
        except Exception as e:
            stk("bonus.skills.browser", f"起装机子进程失败（落回原位跑）: {e}", ok=False)
            logger.warning("BuiltinBrowser: bonus_skills worker failed (fallback in-proc): %s", e)
            try:
                from main.main import install_bonus_skills_background
                install_bonus_skills_background()
            except Exception as e2:
                stk("bonus.skills.browser", f"exception: {e2}", ok=False)
            self._refresh_skills_after_install()

    def _refresh_skills_after_install(self) -> None:
        r"""装链进程退出后，在主线程重新问 CLI 并刷新“未安装技能”。"""
        try:
            self._sidebar._failed_skills.refresh()
        except Exception as _e:
            logger.warning("BuiltinBrowser: refresh failed skills after install: %s", _e)



    def _check_provider_setup(self) -> None:
        r"""检查是否需要首次 provider 配置 — 只负责 provider 配置卡片，不管技能安装。"""




        if self._provider_check_done:
            return


        if self._provider_configured_once:
            self._provider_check_done = True
            return
        class _ProviderCheckThread(QThread):
            ready = Signal(str)

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
        r"""QThread 三态回主线程：unknown 不弹卡（CLI 查不到 ≠ 没配过），轮询重试。"""
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
                self._check_provider_setup()
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

                self._start_bonus_skills()
                return

            stk("provider.check", "no apiKey — showing setup card")
            logger.info("BuiltinBrowser: no apiKey found — showing provider setup card")

            def _reload_page(_token: str):
                stk("provider.saved", f"token={'present' if _token else 'MISSING'}", ok=bool(_token))
                self._provider_configured_once = True
                self._provider_check_done = False

                if _token:
                    self._token = _token
                    self._token_indicator.setText(tr("ui.browser.token_yes"))
                    self._token_indicator.setStyleSheet("color:#5a9e6f;font-size:11px;")

                self._restart_gateway()



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



    def _on_load_progress(self, value: int) -> None:
        r"""接收页面加载进度，交给定时器缓动；启动准备期不驱动进度条。"""
        if getattr(self, "_startup_gateway_preparing", False):
            return
        try:
            _v = max(0, min(100, int(value)))
        except Exception:
            return


        if _v < self._progress_shown and _v != 0:
            return
        self._progress_target = _v
        if not self._progress_timer.isActive():
            self._progress_timer.start()

    def _tick_load_progress(self) -> None:
        r"""小步缓动：每 60ms 最多推进 3%，接近目标时更慢。"""
        try:
            _tgt = int(getattr(self, "_progress_target", 0))
            _cur = int(getattr(self, "_progress_shown", 0))
            if _cur == _tgt:
                self._progress_timer.stop()
                return
            _delta = _tgt - _cur
            if _delta > 0:
                _step = max(1, min(3, int(_delta * 0.12) + 1))
                _cur = min(_tgt, _cur + _step)
                self._progress_shown = _cur
                self._progress.setValue(_cur)
            else:

                self._progress_timer.stop()
        except Exception:
            try:
                self._progress_timer.stop()
            except Exception:
                pass

    def _reset_load_progress(self) -> None:
        try:
            self._progress_timer.stop()
        except Exception:
            pass
        self._progress_target = 0
        self._progress_shown = 0
        try:
            self._progress.setValue(0)
        except Exception:
            pass

    def _hide_progress_if_current(self, epoch: int) -> None:
        try:
            if int(epoch) != int(getattr(self, "_progress_epoch", -1)):
                return
            if self._progress_timer.isActive():
                return
            self._progress.setVisible(False)
        except Exception:
            pass

    def showHandoverReport(self, title: str, body: str) -> None:
        r"""壳层统一承载交接报告（i18n 文案由调用方传入）。"""




        try:
            from PySide6.QtWidgets import QMessageBox as _MB
            _MB.information(self, str(title), str(body))
        except Exception as exc:
            try:
                logger.warning("BuiltinBrowser: handover report failed: %s", exc)
            except Exception:
                pass

    def begin_gateway_preparation(self) -> None:
        r"""进入一次新的 Gateway 冷启动准备态（开机/重启都走这里）。"""




        self._startup_gateway_preparing = True
        self._gateway_wake_finished = False
        try:
            self._retry_manual_btn.setVisible(False)
            self._retry_restart_btn.setVisible(False)
            self._progress.setVisible(False)
        except Exception:
            pass
        self._set_conn_text(tr("ui.browser.gw_preparing"))

    def _on_gateway_wake_started(self) -> None:
        self.begin_gateway_preparation()

    def _on_gateway_wake_finished(self, ok: bool) -> None:
        r"""dashboard + gateway start 已收尾；准备期仍可显示重试/重启按钮。"""




        self._gateway_wake_finished = True
        if getattr(self, "_startup_gateway_preparing", False):


            try:
                if self.connection_healthy():
                    self._startup_gateway_preparing = False
                    self._retry_manual_btn.setVisible(False)
                    self._retry_restart_btn.setVisible(False)
                    self._set_conn_text(f"Gateway · {self._gateway_url}")
                    return
            except Exception:
                pass
            self._retry_manual_btn.setVisible(True)
            self._retry_restart_btn.setVisible(True)
            self._set_conn_text(tr("ui.browser.gw_preparing"))

    def _on_load_started(self) -> None:
        self._reset_load_progress()
        self._progress_epoch = int(getattr(self, "_progress_epoch", 0)) + 1
        if getattr(self, "_startup_gateway_preparing", False):

            self._progress.setVisible(False)
        elif getattr(self, "_progress_enabled", True):
            self._progress.setVisible(True)
        self._set_conn_text(f"{tr('ui.browser.gw_url')} · {self._gateway_url}  ({tr('ui.browser.loading_status')})")
        self._connection_dot.setStyleSheet("color:#d9a840;font-size:8px;background:transparent;border:none;")
        self._dot_container.setStyleSheet("background:transparent;border:2px solid #d9a840;border-radius:11px;")

    def _on_slot_files_changed(self, _path: str = "") -> None:
        r"""根目录文件事件：两文件到位后补挂入口（不重启壳、不轮询）。"""
        try:
            self._sidebar.ensure_rheia_entry()
        except Exception:
            pass

        try:
            if self._slot_watcher is not None:
                _root = _rheia_app_root()
                if os.path.isdir(_root) and _root not in self._slot_watcher.directories():
                    self._slot_watcher.addPath(_root)
        except Exception:
            pass

    def _on_load_finished(self, ok: bool) -> None:
        try:
            self._progress_timer.stop()
        except Exception:
            pass
        _progress_epoch = int(getattr(self, "_progress_epoch", 0))
        _was_preparing = bool(getattr(self, "_startup_gateway_preparing", False))
        if ok:

            self._startup_gateway_preparing = False
            self._progress_target = 100
            self._progress_shown = 100
            self._progress.setValue(100)


            if _was_preparing:
                self._progress.setVisible(False)
            else:
                QTimer.singleShot(500, lambda e=_progress_epoch: self._hide_progress_if_current(e))
        else:
            self._reset_load_progress()
            try:
                self._progress.setVisible(False)
            except Exception:
                pass
        if ok:
            self._placeholder.setVisible(False)
            self._view.setVisible(True)
            self._set_conn_text(f"Gateway · {self._gateway_url}")
            self._set_dot_state("#5a9e6f")
            self._retry_manual_btn.setVisible(False)
            self._retry_restart_btn.setVisible(False)
            self._retry_count = 0
            self._load_fail_count = 0
            self._reconnect_pulse_active = False
            self._gateway_wake_finished = True
            try:
                self._reconnect_timer.stop()
            except Exception:
                pass
            self._notify_glory_connected()
            logger.info("BuiltinBrowser: Gateway loaded successfully")
            stk("browser.load", "page loaded (Gateway+token verified)")

            try:
                self._sidebar.ensure_rheia_entry()
            except Exception:
                pass






            try:
                self._restore_agent_avatar_from_progdata()
            except Exception:
                pass



            self._cleanup_failed_skills_on_connect()
            self._check_provider_setup()
        else:


            try:
                _p = self._view.url().path() or ""
                if (not self._path_fallback_done) and self._last_path and _p == self._last_path:



                    _aid = ""
                    _parts = [x for x in str(self._last_path).split("/") if x]
                    if _parts and _parts[0] == "chat" and len(_parts) > 1:
                        _aid = _parts[1]
                    _in_roster = False
                    try:
                        from platform.slotApi import agentInRoster as _air
                        _in_roster = bool(_aid) and bool(_air(_aid))
                    except Exception:
                        _in_roster = False
                    if _in_roster and self._load_fail_count < 6:



                        self._load_fail_count += 1
                        _u0 = QUrl(self._gateway_url + str(self._start_page or "/chat"))
                        if self._token:
                            _u0.setQuery(f"token={self._token}")
                        self._view.load(_u0)
                        return
                    self._path_fallback_done = True
                    stk("browser.start_page", "agent 不在册 ⇒ fallback /chat（偏好保留）", ok=True)
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
                self._set_conn_text(tr("ui.browser.connect_fail"))
                _allow_retry = (not getattr(self, "_startup_gateway_preparing", False)
                                or bool(getattr(self, "_gateway_wake_finished", False)))
                self._retry_manual_btn.setVisible(_allow_retry)
                self._retry_restart_btn.setVisible(_allow_retry)
                try:
                    stk("browser.load_giveup", f"after {self._load_fail_count} failures", ok=False)
                except Exception:
                    pass

                def _auto_recover():
                    try:
                        self._load_fail_count = 0
                        self._path_fallback_done = False
                    except Exception:
                        pass
                    try:
                        self._wait_and_retry()
                    except Exception:
                        pass
                _auto_recover()
                return

            self._set_dot_state("#e85a4a")
            self._set_conn_text(tr("ui.browser.connect_fail"))
            self._notify_glory_disconnected()
            logger.warning("BuiltinBrowser: Gateway load failed, retrying…")
            try:
                stk("browser.load", "page load FAILED", ok=False)
            except Exception:
                pass
            self._wait_and_retry()

    def _on_loading_changed(self, info) -> None:
        r"""_on_loading_changed"""
        try:
            if QWebEngineLoadingInfo is not None and info.status == QWebEngineLoadingInfo.LoadFailedStatus:
                stk("browser.load_fail",
                    f"url={info.url.toString()} code={info.errorCode} err={info.errorString}",
                    ok=False)
        except Exception:
            pass



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



    def _apply_recovered_token(self, token: str) -> None:
        r"""网关自愈后拿到 token：更新并只加载一次（主线程槽）。"""
        try:
            if not token:
                return
            self.set_token(token)
            url = QUrl(self._gateway_url + "/chat")
            url.setQuery("token=" + self._token)
            self._pin_dark_background()
            self._view.load(url)
        except Exception as exc:
            logger.warning("BuiltinBrowser: apply recovered token failed: %s", exc)

    def go_home(self) -> None:
        r"""保存配置后回首页（/chat），带 token 一次加载。"""
        try:
            url = QUrl(self._gateway_url + "/chat")
            if self._token:
                url.setQuery("token=" + self._token)
            self._pin_dark_background()
            self._view.load(url)
        except Exception as exc:
            logger.warning("BuiltinBrowser: go_home failed: %s", exc)

    def set_token(self, token: str) -> None:
        self._token = token
        self._token_indicator.setText(tr("ui.browser.token_yes"))
        self._token_indicator.setStyleSheet("color:#5a9e6f;font-size:11px;")

    def refresh_token_from_gateway(self) -> str:
        r"""网关重启后重新取 token（壳层唯一刷新口；取不到就保留旧值）。"""
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
        r"""用刚取到的 token 重新载入（网关重启完成后的唯一正确重载口）。"""
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
