"""Rheia 产品运行时 — Gateway 唤醒 + 探活 + 内置浏览器入口

setup.iss → install.py step1-9 → Gateway 启动 → main/main.py → 内置浏览器 → Control UI

关键：main/main.py 是用户日常入口（桌面 icon / 开始菜单）。
每次启动时负责：
  1. 唤醒 WSL VM（冷启动）
  2. 探活 Gateway（不依赖 install.py 生命周期）
  3. Gateway 不在则自动拉起
  4. 等待就绪
  5. 打开内置浏览器
"""

from __future__ import annotations

try:  # 全局静默子进程守卫（设计约定：加载链不得弹控制台）
    from lib.no_window import install as _install_no_window
    _install_no_window()
except Exception:  # noqa: BLE001
    pass


import json
import logging
import os
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime

# ── Chromium GPU 渲染（必须在任何 PySide6 import 之前设）────────────────
# 教训：QtWebEngine 6.11.2（Chromium 140）在 NVIDIA 上有 angle 同步 bug，
# 通用 Chromium flag（--disable-gpu / --disable-gpu-compositing / --use-angle=d3d9）
# 要么卡要么泄漏。Chrome 能正常显示同一套 Control UI，说明问题在 QtWebEngine 层，
# 不在 GPU 硬件。默认不注入任何 flag，交给 QtWebEngine 自身默认行为。
# gpu_render=false 才切纯软件渲染（应急用）。
_gpu_cfg_err = None
if not os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS"):
    # ⚠⚠ **不靠 CSS 补丁、不动页面** ——
    #   让 QtWebEngine 跟 Chrome 走**同一条腿**：ANGLE **D3D11**。
    #   依据：同一套 Control UI 在 Chrome 里不闪、在 QtWebEngine 6.11.2 里反复重绘；
    #   差别就在合成后端（Chrome 在 Windows 上就是 D3D11）。这不是"关 GPU"，
    #   只是指定后端；`gpu_render=false` 仍可应急切软件渲染（--disable-gpu）。
    #   ⚠⚠ d3d11 这条**并没有止住闪** ——
    #     账本：壳只有一个实例（pid 8648，18:55 起）、整页重载 0（18:55 之后）、
    #     自愈只有装机那一次 ⇒ 既不是重启也不是重载，仍在合成层；而带 d3d11 这版
    #     跑了一整晚照样闪。说明「指定 ANGLE 后端」不是根因解药。
    #     回到**验收过的默认**：不注入任何 GPU flag（交给 QtWebEngine 自身默认）。
    #     `gpu_render=false` 仍保留（应急切软件渲染）。
    # 页面里的 shimmer / spinner /
    #   打字微光 / 过渡动画**全部遵循 prefers-reduced-motion**；壳里这些持续重绘
    #   就是"看见的一直在动"。默认强制减弱动效，把无关重绘压住。
    #   gpu_render=false 仍可应急叠加 --disable-gpu（软件渲染）。
    _flags = ["--force-prefers-reduced-motion"]
    try:
        _gpu_cfg = os.path.join(os.environ.get("PROGRAMDATA", ""),
                                "Rheia", "gateway_config.json")
        if os.path.isfile(_gpu_cfg):
            with open(_gpu_cfg, "r", encoding="utf-8") as _f:
                _gpu_data = json.load(_f)
            if _gpu_data.get("gpu_render") is False:
                # ⚠ 2026-10-19（原型机试验）：关 GPU 的语义从"全软件"改成"关合成、保光栅"。
                #   实测 --disable-gpu 会让整页 WebEngine 的合成落在壳进程里：
                #   pythonw 静置 17%、一交互顶到 109%，QtWebEngineProcess 才 6% ⇒ 定格动画。
                #   --disable-gpu-compositing 只关 GPU 合成、光栅化仍走 GPU，是"不卡也少闪"的中间档。
                #   如果这一档仍闪，再回退/换档，不在产品树上做实验。
                _flags.append("--disable-gpu-compositing")
    except Exception as _gpu_e:
        # 读配置失败 → 只保留减弱动效这一条（安全默认）；错误留到 _stake 就绪后补账。
        _gpu_cfg_err = _gpu_e
    # ── 诊断覆盖（仅原型机用）：%PROGRAMDATA%\Rheia\webengine_flags.txt ──
    #   存在且非空 ⇒ 整行按空格拆成旗标，覆盖默认组合。这样 A/B 换旗标只需写文件 +
    #   重启壳，不必每次重打包。**产品版不带这个通道**（复制到 products 前会去掉）。
    try:
        _ov = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia",
                           "webengine_flags.txt")
        if os.path.isfile(_ov):
            with open(_ov, "r", encoding="utf-8") as _of:
                _oline = _of.read().strip()
            if _oline:
                _flags = _oline.split()
    except Exception:
        pass
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(_flags)

# Windows 的 DirectComposition 直通链上会出块状残影
#   （一块一块地冒、越跑越频繁、黑屏越久；关 GPU 就消失，但软件渲染速度不能接受）。
#   关掉 DirectComposition ⇒ GPU 全速、垛子消失；代价只是多一次拷贝，实测顺滑。
#   这条**不设开关**：它是 Windows 上的兼容默认，且必须在任何 PySide6 import 之前生效；
#   即使外部已设 QTWEBENGINE_CHROMIUM_FLAGS，也要把它补进去（否则又会被环境变量架空）。
_FORCED_FLAGS = [
    "--disable-direct-composition",
    "--disable-gpu-memory-buffer-compositor-resources",
    "--disable-gpu-memory-buffer-video-frames",
]
_cur_flags = (os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS") or "").strip()
_have = _cur_flags.split()
_missing = [f for f in _FORCED_FLAGS if f not in _have]
if _missing:
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (" ".join([_cur_flags] + _missing)).strip()

_STAKE_DIR = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia")
_STAKE_PATH = os.path.join(_STAKE_DIR, "runtime_stake.log")


def _stake(msg: str) -> None:
    """写一条带时间戳的桩日志。即使 logger 未就绪也能工作。"""
    try:
        os.makedirs(_STAKE_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        with open(_STAKE_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass  # 如果连写日志都失败，也没什么可做的了


# 诊断用：把"生效中的 WebEngine 旗标"记一行账 —— 确认环境变量到底进没进这个进程。
_stake("QTWEBENGINE_CHROMIUM_FLAGS=%r" % os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS"))

# 诊断：上面读 GPU 配置失败那一笔（此前 _stake 还没定义，只能到这里补账）。
if _gpu_cfg_err is not None:
    _stake("GPU 配置读取失败（按默认「不注入任何 flag」走）：%s" % _gpu_cfg_err)


# ── WSL 预唤醒：双击即并行叫醒 WSL（不等窗口 show）──
# ── 这一步同时是「打开程序的第一步」：像用户那样
#    `wsl` 进 Ubuntu 敲 `openclaw dashboard`，**并把会话一直开着**。──
# WSL 冷启动是启动链上最重的动作（实测 ~50s）。它不该排在「窗口 show 之后」串行
# 执行，而该从进程启动那一刻就并行开始，让它的冷启动被 PySide6 加载 + Browser
# 构造 + show 窗口这 2.5 秒掩盖掉。这里在模块导入阶段（尚未 import PySide6）就
# 起一个 daemon 线程去 wsl echo ok，把 WSL VM 从回收态叫醒。等官方服务/探针到场时
# 再正式激活 Gateway 时，WSL 已经热了/快热了，串行等待大幅缩短。
def _wsl_prewarm() -> None:
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        _stake("--- WSL prewarm: calling wsl echo ok (parallel) ---")
        subprocess.run(
            ["wsl", "-d", os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu", "--", "echo", "ok"],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=si,
        )

        # ── **壳 = 一个开着 WSL 会话的人** ──
        #   用户的做法：PowerShell 里 `wsl` 进 Ubuntu → 敲 `openclaw dashboard`。
        #   壳照做，只多一件事 —— **把那个会话一直开着**（用户的终端窗口本来也一直开着）：
        #   ① 常驻会话：`wsl -d Ubuntu -- sleep infinity`（壳的子进程，绑「父死子亡」作业对象）。
        #      依据（官方文档 platforms/windows.md `## Gateway auto-start before Windows login`
        #      的 Note）：WSL >= 2.6.1.0 的 regression（microsoft/WSL #13416）会在**最后一个
        #      客户端退出后 15~20 秒**把发行版 idle-terminate，**linger 也挡不住** ⇒ 本机
        #      WSL 3.0.3.0 命中，这就是网关「自己没了」的根因。挂着一个客户端（像开着终端
        #      那样）正是那条官方 Note 的解法形状。
        #   ② 会话里第一条命令 = 官方 `openclaw dashboard`（像用户那样）。
        #      用 `--json`：官方明文它"print one machine-readable connection object
        #      **without opening a browser, using the clipboard, prompting, or starting
        #      the Gateway**" ⇒ 天然就是「只探、不起、不提示、不等」，正好对上
        #      「探信号、在合适时机写入、无 timeout / 无 await」这条规矩。
        #      （⚠ 不用 `--yes`：那是"去 start 网关"，违背壳自己的"壳层绝不 stop/start"；
        #       也不用裸的：非交互管道下它的提示可能把进程挂住。）
        #   判据归官方（2026-10-05 令）：成不成看官方命令自己的返回，壳不另立标准。
        #   全程 daemon 线程 + CREATE_NO_WINDOW ⇒ 不挡窗口、不弹窗。
        _stake("--- WSL session: hold open (WSL #13416 workaround) ---")
        try:
            _hold = subprocess.Popen(
                ["wsl", "-d", os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu", "--", "sleep", "infinity"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=si,
            )
            _bind_child_to_parent(_hold.pid)
            _stake("--- WSL session held open (pid=%s, bound to shell) ---" % _hold.pid)
        except Exception as _he:                                      # noqa: BLE001
            _stake("WSL session hold failed (non-fatal): %s" % _he)

        _stake("--- WSL prewarm: probe signal via official openclaw dashboard --json ---")
        _drc, _dout, _derr = _run_official_cmd("dashboard", "open", "--json")
        _stake("--- WSL prewarm: dashboard --json rc=%s out=%s ---" % (_drc, (_dout or "")[:300]))

        _stake("--- WSL prewarm done ---")
    except Exception as _e:
        _stake("WSL prewarm failed (non-fatal): %s" % _e)


_stake("============================================================")
_stake("RUNTIME START  pid=%s  cwd=%s  python=%s" % (os.getpid(), os.getcwd(), sys.executable))
_stake("sys.argv: %s" % (sys.argv,))
_stake("sys.path head: %s" % (sys.path[:3],))

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_stake("_project_root = %s  (exists=%s)" % (_project_root, os.path.isdir(_project_root)))

_ICO_CANDIDATES = [
    os.path.join(_project_root, "Rheia.ico"),          # 安装后 {app}/
    os.path.join(_project_root, "extract", "Rheia.ico"),  # 开发环境
]

if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
    _stake("inserted _project_root into sys.path[0]")

# python site-packages 优先（PySide6 + WebEngine 运行时，联网下载后位于独立目录）
# Python 运行时独立在 C:\Program Files\PythonSide6（与制卡机共用），不随 {app}。
_runtime_dir = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "PythonSide6")
_site_packages = os.path.join(_runtime_dir, "Lib", "site-packages")
_stake("_site_packages = %s  (exists=%s)" % (_site_packages, os.path.isdir(_site_packages)))
if os.path.isdir(_site_packages) and _site_packages not in sys.path:
    sys.path.insert(0, _site_packages)
    _stake("inserted _site_packages into sys.path[0]")

# ── python Qt6 DLL 必须在 PATH 中，PySide6 C 扩展才能加载 Qt6Core/Gui/Widgets.dll ──
# PySide6 6.11 是扁平布局：DLL 直接放在 site-packages/PySide6/ 根目录（不是 Qt6/bin）
_pyside6_dir = os.path.join(_runtime_dir, "Lib", "site-packages", "PySide6")
if os.path.isdir(_pyside6_dir):
    os.environ["PATH"] = _pyside6_dir + os.pathsep + os.environ.get("PATH", "")
    _stake("PATH += PythonSide6/PySide6 (flat layout)")
else:
    _stake("WARNING: PythonSide6/PySide6 NOT FOUND — Qt DLL loading will likely fail")

_pyside6_platforms = os.path.join(_runtime_dir, "Lib", "site-packages",
                                   "PySide6", "plugins", "platforms")
if os.path.isdir(_pyside6_platforms):
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = _pyside6_platforms
    _stake("QT_QPA_PLATFORM_PLUGIN_PATH = %s" % _pyside6_platforms)
else:
    _stake("WARNING: python platforms dir NOT FOUND")

# ── 检查 Qt6 关键 DLL 是否存在（扁平布局：DLL 与 .pyd 同在 PySide6 根目录）──
_qt6_dlls = ["Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "Qt6WebEngineCore.dll"]
for _dll in _qt6_dlls:
    _dll_path = os.path.join(_pyside6_dir, _dll) if os.path.isdir(_pyside6_dir) else ""
    _stake("  %-30s %s" % (_dll, "FOUND" if (_dll_path and os.path.exists(_dll_path)) else "MISSING"))

# ── 开始导入 PySide6 ──
_stake("--- import stage 1: PySide6.QtCore ---")
try:
    from PySide6.QtCore import Qt, QCoreApplication
    _stake("  PySide6.QtCore.Qt OK")
    # ⚠️ WebEngine 要求 AA_ShareOpenGLContexts 在任何 QApplication 创建之前设置。
    # QCoreApplication 可能被任何 PySide6 子模块隐式创建，所以必须在 QtCore import
    # 之后、第一个 QWidgets/QtGui import 之前立即执行。
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    _stake("  AA_ShareOpenGLContexts set at module level")
except Exception as _e:
    _stake("  PySide6.QtCore.Qt FAILED: %s" % _e)
    _stake("  traceback: %s" % traceback.format_exc())
    raise

_stake("--- import stage 2: PySide6.QtWidgets ---")
try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QIcon
    _stake("  PySide6.QtWidgets.QApplication OK")
except Exception as _e:
    _stake("  PySide6.QtWidgets.QApplication FAILED: %s" % _e)
    _stake("  traceback: %s" % traceback.format_exc())
    raise

_stake("--- import stage 2b: PySide6.QtWebEngine ---")
try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEngineProfile
    _stake("  PySide6.QtWebEngineWidgets OK")
except Exception as _e:
    _stake("  PySide6.QtWebEngine FAILED: %s" % _e)
    _stake("  traceback: %s" % traceback.format_exc())
    raise

_stake("--- import stage 3: ui.theme ---")
try:
    from ui.theme import qtDarkPalette
    _stake("  ui.theme.qtDarkPalette OK")
except Exception as _e:
    _stake("  ui.theme.qtDarkPalette FAILED: %s" % _e)
    _stake("  traceback: %s" % traceback.format_exc())
    raise

logger = logging.getLogger(__name__)


# ⚠⚠ 为什么异常没痕迹：
#   因为壳**从来没配过 logging handler** —— 所有 `except: logger.warning(...)` 写的那一行
#   都被 Python 直接丢掉：不落盘、不进账、不弹窗 ⇒ 界面上就是"点了没反应"。
#   这里给**根 logger** 装一个文件 handler：从此**任何模块**的 logger.* 都落
#   `%PROGRAMDATA%\Rheia\shell.log`（>2MB 先改名保留一份）。这是"不许静默"的地基。
def _install_shell_log() -> None:
    try:
        _p = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia", "shell.log")
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        try:
            if os.path.isfile(_p) and os.path.getsize(_p) > 2 * 1024 * 1024:
                os.replace(_p, _p + ".1")
        except OSError:
            pass
        _root = logging.getLogger()
        if not any(isinstance(_h, logging.FileHandler) for _h in _root.handlers):
            _fh = logging.FileHandler(_p, encoding="utf-8", errors="replace")
            _fh.setFormatter(logging.Formatter(
                "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"))
            _root.addHandler(_fh)
        if _root.level > logging.INFO:
            _root.setLevel(logging.INFO)
    except Exception:                                                 # noqa: BLE001
        pass


_install_shell_log()

WSL_DISTRO = os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu"
GATEWAY_PORT = 18789
GATEWAY_URL = f"http://127.0.0.1:{GATEWAY_PORT}"

_stake("--- module-level imports complete ---")


def _read_glory() -> tuple[str, str]:
    """读 gateway 配置，返回 (url, token)。

    优先从 WSL 自动读 gateway 最新 token（openclaw.json 的 gateway.auth.token，
    相当于 openclaw dashboard 自动拿 token）；读不到再 fallback 到
    gateway_config.json（安装时写入的缓存）。不让用户手动去 WSL 找 token。
    """
    url = GATEWAY_URL
    token = ""
    # ① 优先从 WSL 读 gateway 最新 token（权威源）
    try:
        from kernel.scout import readGatewayAuth
        auth = readGatewayAuth(WSL_DISTRO)
        if auth.get("ok") and auth.get("token"):
            token = auth.get("token", "")
            if auth.get("url"):
                url = auth.get("url", url)
    except Exception:
        logger.debug("auto-read gateway token from WSL failed")
    # ② fallback：gateway_config.json（安装时写入）
    if not token:
        localdataDir = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia")
        gwCfg = os.path.join(localdataDir, "gateway_config.json")
        if os.path.exists(gwCfg):
            try:
                with open(gwCfg, "r", encoding="utf-8") as f:
                    cd = json.load(f)
                url = cd.get("url", url).rstrip("/")
                token = cd.get("token", "")
            except Exception:
                logger.debug("gateway_config.json read failed, using defaults")
    return url, token


def refresh_token() -> str:
    """网关重启后重新取 token（5ea9ac4 的取法，壳层唯一刷新入口）。"""
    _url, _tok = _read_glory()
    try:
        _stake("gw.token.refresh token=%s" % ("present" if _tok else "MISSING"))
    except Exception:
        pass
    return _tok


#: 单实例守卫的两道锁：命名互斥体句柄 + 锁文件句柄（都必须留全局引用，GC 掉就失效）
_SINGLETON_HANDLE = None
_SINGLETON_LOCK_FH = None

#: 两道门的名字。做成常量只为一件事：测试里换成唯一名字，才能真跑一遍
#: "第二个实例被挡在门外"（用真名字跑的话，机器上正在跑的壳会把测试自己也挡住）。
_SINGLETON_MUTEX_NAME = "Global\\Rheia.Shell.Singleton"
_SINGLETON_LOCK_NAME = "shell.lock"


def _acquire_single_instance() -> bool:
    """**只允许一个壳实例在跑**；第二个实例直接退出。

    为什么钉死它（2026-09-21 事故）：装机脚本最后一步"装完启动应用"会再起一个实例，
    而原来那个还活着 —— 两个实例都管**同一个网关**，一个要重启、一个要停，
    实测就是 `Stopping openclaw-gateway` 之后一直 `inactive (dead)`：网关被停死后
    再没人拉起，客户看到的就是"网关起不来"。

    ⚠ 2026-09-21 二次修（产品方报"安装又是双实例"）：第一版用
      `ctypes.windll.kernel32.CreateMutexW(...)` 之后读 `windll...GetLastError()`
      —— **ctypes 不保 last-error**，中间任何调用都会把它冲掉 ⇒ `ERROR_ALREADY_EXISTS`
      基本读不到 ⇒ 门形同没装。现在两道：
        ① 命名互斥体用 `WinDLL(..., use_last_error=True)` + `ctypes.get_last_error()`；
        ② **锁文件**（`%PROGRAMDATA%\\Rheia\\shell.lock`，`msvcrt.locking` 非阻塞独占）——
           进程一死锁自动放，不靠"记得删文件"，也不靠任何 ctypes 细节。
      任一道说"已有实例" ⇒ 本实例退出（不碰网关、不碰卡槽）。
    """
    global _SINGLETON_HANDLE, _SINGLETON_LOCK_FH
    if sys.platform != "win32":
        return True

    # ⓪ 交棒（自愈重启拉起的新进程）：先等老进程退干净，别抢在它前面敲门
    _handover_wait()

    # ① 命名互斥体（可靠写法：use_last_error + get_last_error）
    try:
        import ctypes
        _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _h = _k32.CreateMutexW(None, False, _SINGLETON_MUTEX_NAME)
        _err = ctypes.get_last_error()
        if _h:
            if _err == 183:                   # ERROR_ALREADY_EXISTS
                # ⚠ 别拿这个新句柄去顶掉 `_SINGLETON_HANDLE`：顶掉了，自己手上原来
                #   那个句柄就再没人关 ⇒ 门放不掉（连"让出再重进"都做不到）。
                #   旧写法就是这么写的（先赋值再判 183），一并修掉。
                try:
                    _k32.CloseHandle(_h)
                except Exception as _ce:                         # noqa: BLE001
                    # 只走异常清理路径、危害小，但别静默（旧写法是裸 pass）
                    _stake("单实例守卫：关掉多余的互斥体句柄失败（无害）：%s" % _ce)
                _stake("!!! 已有壳实例（互斥体）：本实例退出，不碰网关 !!!")
                return False
            _SINGLETON_HANDLE = _h            # 留全局引用，进程活着就一直持有
    except Exception as e:                                        # noqa: BLE001
        _stake(f"单实例守卫：互斥体不可用（继续看锁文件）：{e}")

    # ② 锁文件（第二道，独立于 ctypes）
    try:
        import msvcrt
        _p = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia", _SINGLETON_LOCK_NAME)
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        _fh = open(_p, "a+")
        try:
            msvcrt.locking(_fh.fileno(), msvcrt.LK_NBLCK, 1)
            _SINGLETON_LOCK_FH = _fh          # 握着不放；进程退出自动释放
        except OSError:
            try:
                _fh.close()
            except Exception:                                    # noqa: BLE001
                pass
            _stake("!!! 已有壳实例（锁文件被占）：本实例退出，不碰网关 !!!")
            return False
    except Exception as e:                                        # noqa: BLE001
        _stake(f"单实例守卫：锁文件不可用（放行）：{e}")
    return True


#: 交棒（自愈重启）时老进程 pid 的环境变量名：新进程据此等老进程退干净再进门。
_HANDOVER_ENV = "RHEIA_HANDOVER_PID"


# ⚠ 2026-10-17 逐行审计（该删删）：`_pid_alive()` 已删 —— 全仓零调用方，
#   交棒那条路早改走 `WaitForSingleObject` 等老进程句柄（不轮询 pid）。


def _handover_wait() -> None:
    """自愈重启交棒：新进程**阻塞等"门"**（单实例互斥体）—— 老进程一退，**OS 唤醒**。

    ⚠ 壳侧不许留任何 timeout / await / 轮询：
      原来这里是"轮询 `_pid_alive` + `time.sleep(0.2)` + 60 秒上限" —— 那是拿时限弥合竞态；
      现在改成**等内核信号**：老进程握着单实例互斥体，它 `os._exit(0)` 那一刻 OS 释放互斥体，
      新进程这边等的是**老进程终止这个内核信号**（`WaitForSingleObject(INFINITE)`）⇒ 系统唤醒。
      **没有时限、没有 sleep、没有轮询**；老进程只要还活着，这里就一直等，如实记一笔。

    ⚠ 旧写法等的是**单实例互斥体**，
      两处都不成立 ——
        ① `CreateMutexW(..., bInitialOwner=False)` 建的是**非持有**互斥体，没人 Wait 它 ⇒ 它**一直
          是 signaled**；拿它当"老进程还活着"的信号，等于没等；
        ② 判据方向也反了：互斥体是内核对象，**老进程一退（最后一个句柄关闭）它就连对象消失**
          ⇒ `OpenMutexW` 打不开**恰恰说明门已经让出**，旧代码却记成"老进程还握着"。
      现在直接等**老进程句柄**：`OpenProcess(SYNCHRONIZE)` 打不开 = 老进程早没了（门已让出）；
      打得开就等它终止。两种情形同一个结论，不再拿互斥体猜。
    """
    _raw = os.environ.get(_HANDOVER_ENV, "").strip()
    if not _raw.isdigit():
        return
    _pid = int(_raw)
    # 两盏灯，都不靠猜：
    #   ① 打不开老进程句柄 ⇒ 它早没了 ⇒ 门已让出（互斥体/锁文件都随它退出自动放）；
    #   ② 打得开 ⇒ 等它终止（内核信号，无时限）。
    _state = "门已让出，进门"
    try:
        # ⚠ 这一处曾**漏了 import ctypes**（同文件其余四处都有）——
        #   少了它，下面第一行就 NameError，被 except 吞成「交棒：等门异常」⇒ 新进程没等老进程
        #   交门就去抢 ⇒ 自愈重启死循环（用户看到的就是"屏闪"）。
        import ctypes
        _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _h = _k32.OpenProcess(0x00100000, False, _pid)                    # SYNCHRONIZE
        if _h:
            try:
                _k32.WaitForSingleObject(_h, 0xFFFFFFFF)                  # INFINITE：等"进程终止"
            finally:
                _k32.CloseHandle(_h)
    except Exception as _e:                                               # noqa: BLE001
        # ⚠⚠ 异常时**不许盲目"照走"** —— 那会让新进程去抢门
        #   （2026-09-21 那次"自愈后没自动拉起／屏闪"就是它：import ctypes 漏了 → NameError → 兜底）；
        #   也**不许直接拒进** —— 老进程真死了就会永久入口口。这里用**锁文件**再确认一次：
        #     拿得到（非阻塞独占成功）⇒ 门真空着 ⇒ 照走（随后由正常那道门去拿）；
        #     拿不到 ⇒ 老进程还在 ⇒ **不进门**（随后那道门自然会拒，不会两个壳并跑）。
        #   两种情形都如实记账；不掐表、不新增时限。
        _free = None
        try:
            import msvcrt as _mc
            _lp = os.path.join(_PROGRAMDATA, _SINGLETON_LOCK_NAME)
            with open(_lp, "a+b") as _fh:
                try:
                    _fh.seek(0)
                    _mc.locking(_fh.fileno(), _mc.LK_NBLCK, 1)
                    _free = True
                    try:
                        _fh.seek(0)
                        _mc.locking(_fh.fileno(), _mc.LK_UNLCK, 1)
                    except Exception:                                     # noqa: BLE001
                        pass
                except OSError:
                    _free = False
        except Exception as _pe:                                          # noqa: BLE001
            _free = None
            _e = "%s；锁文件确认也失败：%s" % (_e, _pe)
        if _free is True:
            _state = "等门异常（%s）⇒ 锁文件说门空着 ⇒ 进门" % _e
        elif _free is False:
            _state = "等门异常（%s）⇒ 锁文件被占（老进程还在）⇒ 不进门，交给门去拒" % _e
        else:
            _state = "等门异常（%s）⇒ 锁文件确认不了 ⇒ 照走（由门兜底）" % _e
    _stake("交棒：%s（老进程 pid=%d）" % (_state, _pid))
    # 这变量只管这一次交棒：从自己环境里摘掉，免得传给我们的子进程
    os.environ.pop(_HANDOVER_ENV, None)


def _release_single_instance() -> None:
    """**主动让出**单实例门（给"进程继续活着、但门要让出去"的场合）。

    ⚠ 2026-09-21 修正：自愈重启**不再走这里**。原来那版是"先交钥匙再 Popen"，但
      那个调用点写的是 `from main.main import _release_single_instance` —— 壳是以
      脚本身份跑的（`pythonw main.py`），`main.main` 是**再 import 一次的副本**，
      它的模块全局里 `_SINGLETON_HANDLE` / `_SINGLETON_LOCK_FH` 全是 None
      ⇒ 这个函数**一把钥匙都没放**，门一直握到进程真退。
      现在交棒改成"老进程先停保活、再拉新进程（带 pid）、最后 `os._exit(0)`"，
      门随进程退出自动放；新进程按 pid 等老进程退干净再进（`_handover_wait`）。
      本函数保留给"不退出也要让门"的场合。
    """
    global _SINGLETON_HANDLE, _SINGLETON_LOCK_FH
    if _SINGLETON_LOCK_FH is not None:
        try:
            _SINGLETON_LOCK_FH.close()
        except Exception:                                        # noqa: BLE001
            pass
        _SINGLETON_LOCK_FH = None
    if _SINGLETON_HANDLE is not None:
        try:
            import ctypes
            ctypes.WinDLL("kernel32").CloseHandle(_SINGLETON_HANDLE)
        except Exception:                                        # noqa: BLE001
            pass
        _SINGLETON_HANDLE = None


# ═══════════════════════════════════════════════════════════════════
#  内核重活子进程（壳进程不得影响页面）
#  scout.probe / injectCustomDemands / refresh_slot_declarations / large.run
#  与自愈采样·纠正 都是分钟级重活（要反复 spawn CLI/网关）⇒ 整段搬进
#  main/boot_worker.py 单独进程跑；UI 进程只发起、不参与，结论经落盘标记回读。
# ═══════════════════════════════════════════════════════════════════

def _spawn_boot_worker(mark_path: str):
    """起「内核重活」子进程（main/boot_worker.py），返回 Popen；起不来就抛给调用方。

    - 用 sys.executable（本机 = pythonw.exe）+ CREATE_NO_WINDOW ⇒ 无窗口、不弹黑框；
    - 绑「父死子亡」作业对象：壳一退，重活子进程跟着走（不留孤儿）；
    - 旧标记先删掉 —— 免得上一轮进程留下的结论被这一轮误读（通道只有这一个文件）。
    """
    _worker = os.path.join(_project_root, "main", "boot_worker.py")
    for _stale in (mark_path, mark_path + ".tmp"):
        try:
            os.remove(_stale)
        except OSError:
            pass
    _env = dict(os.environ)
    _env["PYTHONPATH"] = _project_root + os.pathsep + _env.get("PYTHONPATH", "")
    _env["PYTHONIOENCODING"] = "utf-8"
    _kwargs = {
        "cwd": _project_root,
        "env": _env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        _si = subprocess.STARTUPINFO()
        _si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        _si.wShowWindow = 0
        _kwargs["startupinfo"] = _si
        _kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    _proc = subprocess.Popen([sys.executable, "-u", _worker, "--mark", mark_path],
                             **_kwargs)
    try:
        _bind_child_to_parent(_proc.pid)
    except Exception as _be:                                      # noqa: BLE001
        _stake("boot_worker bind-to-parent failed (non-fatal): %s" % _be)
    return _proc


def _read_boot_marker(mark_path: str) -> dict:
    """读子进程落的结论标记；读不到 / 半截 / 坏掉 一律当「还没有结论」（不抛、不猜）。"""
    try:
        with open(mark_path, "r", encoding="utf-8") as f:
            _data = json.load(f)
        return _data if isinstance(_data, dict) else {}
    except Exception:                                            # noqa: BLE001
        return {}


def main():
    _stake("--- main() entered ---")

    # ── 单实例门：第二个实例不碰网关、不碰卡槽（2026-09-21 事故后加）──
    if not _acquire_single_instance():
        return

    # ── 任务栏图标：AppUserModelID 必须在 QApplication 之前设 ──
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Rheia.Browser")
        except Exception as _ae:
            # 失败只影响任务栏图标分组，危害轻微，但别静默（旧写法裸 pass）
            _stake("AppUserModelID 设置失败（只影响任务栏图标分组）：%s" % _ae)

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    _stake("QApplication created")

    def _fatal_box(msg: str) -> None:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(None, "Rheia", msg)

    # ── 红线唯一例外：提示文案硬编码日语（日本人看得懂），不依赖系统语言 API。
    # 日语文案不进 28 语言翻译体系，与 card.c 劝退页同一句。
    def _reject_text() -> str:
        return "本ソフトウェアは日本語をサポートしていません。インストーラーは自己破壊します。"

    # ── 不支持语言环境红线（in0001/in0002）：检测命中 japanese → 弹提示 + 自毁 + 退出 ──
    # 只有日语环境才拒载/自毁。非日语（中文/英文等）正常启动，不读 shell_destroyed.flag
    # —— 那是日语自毁的残留痕迹，不该拦正常用户（残留 flag 会在下方检测到非日语时忽略）。
    try:
        _guard_state, _guard_hits = check_detailed()
        _stake("i18n_guard.check() = %s hits=%s" % (_guard_state, _guard_hits))
    except Exception as _e:
        _stake("i18n_guard check skipped: %s" % _e)
        _guard_state = "none"
        _guard_hits = []
        _reject_msg = "本产品不支持日语语言环境"
    if _guard_state == "japanese":
        _fatal_box(_reject_text())
        _stake("i18n_guard: japanese environment detected, self-destructing shell")
        # 日文自毁 = 删壳（与防扩散自毁同一份 shell_self_destruct 逻辑），不碰 WSL。
        try:
            self_destruct()
        except Exception as _e:
            _stake("shell self_destruct dispatch failed: %s" % _e)
        sys.exit(0)

    # ── 非日语环境才预热 WSL：日语检测已通过，这里才安全拉起 WSL。
    # 不能在日语检测之前预热——日文系统切换后 Hyper-V 虚拟化层处于重载脆弱窗口，
    # 提前 wsl echo ok 会触发 VmSwitch/VFP 内核崩溃（实测 4 次蓝屏 0x3B）。 ──
    if sys.platform == "win32":
        threading.Thread(target=_wsl_prewarm, daemon=True, name="wsl-prewarm").start()

    _ico_found = False
    for _ico in _ICO_CANDIDATES:
        if os.path.isfile(_ico):
            app.setWindowIcon(QIcon(_ico))
            _stake("app icon set: %s" % _ico)
            _ico_found = True
            break
    if not _ico_found:
        _stake("app icon MISSING, candidates: %s" % _ICO_CANDIDATES)

    # ── Windows 标题栏深色 + Qt Fusion 深色画布 + 全局中文字体 ──
    if sys.platform == "win32":
        app.setStyle("Fusion")
        app.setPalette(qtDarkPalette())
        # 全局字体：微软雅黑（中文文案统一字体，替换系统默认宋体）
        try:
            from PySide6.QtGui import QFont
            _font = QFont("Microsoft YaHei UI", 9)
            app.setFont(_font)
            _stake("global font set: Microsoft YaHei UI")
        except Exception as _fe:
            _stake("global font set FAILED: %s" % _fe)
        _stake("Fusion + dark palette applied")

    # ── 全局异常钩子：任何未捕获的 Python/Qt/线程异常都会写入 stake.log ──
    try:
        install_crash_hooks()
        _stake("crash hooks installed (sys+threading+qt)")
    except Exception as _e:
        _stake("crash hooks install FAILED: %s" % _e)

    # ── 读 gateway_config.json（本地文件，瞬时操作）──
    url, token = _read_glory()
    if not token:
        # 2026-10-21：冷启动时 WSL 还没热，_read_glory() 会读空 ⇒ 内建浏览器不带 token
        #   ⇒ 页面上「连接失败 / 重试」。这里只预热一次、再读一次（仅开机这一次，不循环）。
        try:
            subprocess.run(["wsl.exe", "-d", WSL_DISTRO, "--", "echo", "ok"],
                           capture_output=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            url, token = _read_glory()
            _stake("gateway token retry after WSL warm: %s" % bool(token))
        except Exception as _we:
            _stake("gateway token retry failed: %s" % _we)
    _stake("gateway url=%s token=%s" % (url, bool(token)))
    stk("gw.token", f"token={'present' if token else 'MISSING'}", ok=bool(token))
    logger.info("Gateway: url=%s token=%s", url, bool(token))

    # ── 灾难恢复数据库初始化（所有模块统一入口，避免空壳 db）──
    _stake("--- ensureRecoveryTables ---")
    try:
        from kernel.scout import ensureRecoveryTables
        ensureRecoveryTables()
        _stake("  Recovery tables ensured OK")
    except Exception as _e:
        _stake("  Recovery tables ensure FAILED: %s" % _e)

    # ── 窗口先出，让用户看到界面再异步做重工作 ──
    _stake("--- constructing BuiltinBrowser ---")
    from ui.web.browser import BuiltinBrowser
    browser = BuiltinBrowser(gateway_url=url, token=token, start_page="/chat")
    _stake("BuiltinBrowser constructed, calling show()...")
    browser.show()
    _stake("browser.show() done")

    # ── Windows 标题栏深色（必须 show() 之后，窗口句柄才存在）──
    if sys.platform == "win32":
        try:
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            hwnd = int(browser.winId())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int))
            _stake("dark title bar applied")
        except Exception as _e:
            _stake("dark title bar skipped: %s" % _e)

    # ── 所有阻塞启动的重工作移到后台线程执行 ──
    # 浏览器已带加载占位界面，Gateway 未就绪时也有无限重试机制
    from PySide6.QtCore import QTimer

    # ── 内核重活子进程的通道（唯一）：落盘标记 + 既有账 ──
    # 子进程把结论写进这个标记，UI 侧一个 1 秒 QTimer 读它一次，只做 UI 动作（不做重活）。
    _BOOT_MARK_PATH = os.path.join(_STAKE_DIR, "boot_worker.json")
    boot_state = {"proc": None, "failed": False}   # 子进程句柄 / 起失败（后台线程写，主线程读）
    boot_seen: set = set()                         # 已消费过的结论（每条只办一次）

    def _bootstrap_deferred():
        """后台线程：探活→人格→TLC 早启；内核重活整段交给子进程（壳不再被拖住）。"""
        _stake("--- bootstrap deferred start ---")

        # 1) 网关探针：**按需**（常驻循环已删）——
        #    这里只在启动时探一次，把结果记进 Gateway 段（官方判据）；之后谁要
        #    「等就绪」（启动 / 装链）谁自己探 ⇒ 稳态下**零周期 wsl 调用**。
        _stake("--- gateway probe (on-demand) ---")
        try:
            gateway_probe().probe_once()     # 按需：启动期探一次（常驻循环已删）
        except Exception as e:
            _stake("gateway probe start FAILED: %s" % e)
            stk("gw.probe", "probe start exception", ok=False)

        # 1) WSL 保活：**已整段删除**（实测 3h55m 无回收、网关未掉线）。
        #    WSL 存活由官方 systemd 服务（openclaw-gateway.service，常驻进程）承担。

        # 2) 探活 Gateway：只探活，不 stop/start。
        # Gateway 开机自启归 systemd（openclaw gateway install + linger，Restart=always）。
        # 壳层绝不 stop/start——之前 recover_gateway 在探活失败时去 stop 正在启动的
        # Gateway，把它优雅停机卡死（2026-09-05 早上进不去系统的根因）。
        # 这里只轮询探活，给 systemd 冷启动时间；界面由 browser 的重连脉冲负责。
        try:
            if check_gateway_alive():
                stk("gw.wake", "Gateway listening on 18789")
            else:
                # 客户视角：网关没起来时壳自己恢复（内部 stop→doctor→start），不让客户敲命令。
                stk("gw.wake", "Gateway 未就绪 ⇒ 自动恢复（内部 stop→doctor→start）", ok=False)
                try:
                    _ok_r, _sum_r = recover_gateway(manual=True)
                    stk("gw.recover", str(_sum_r)[:160], ok=bool(_ok_r))
                    if _ok_r:
                        _u_r, _t_r = _read_glory()
                        if _t_r:
                            browser.token_ready.emit(_t_r)
                except Exception as _re:                              # noqa: BLE001
                    stk("gw.recover", f"failed: {_re}", ok=False)
        except Exception as e:
            _stake("gateway probe failed: %s" % e)
            stk("gw.wake", "gateway probe exception", ok=False)

        # 3) 人格注入（前：部署 KNOWLEDGE.md 到 PROGRAMDATA）
        try:
            import shutil
            proj_knowledge = os.path.join(_project_root, "main", "scripts", "KNOWLEDGE.md")
            progdata_knowledge = os.path.join(_STAKE_DIR, "KNOWLEDGE.md")
            if os.path.isfile(proj_knowledge):
                _sync_knowledge = False
                if not os.path.isfile(progdata_knowledge):
                    _sync_knowledge = True
                else:
                    s1 = os.path.getsize(proj_knowledge)
                    s2 = os.path.getsize(progdata_knowledge)
                    if s1 != s2:
                        _sync_knowledge = True
                if _sync_knowledge:
                    shutil.copy2(proj_knowledge, progdata_knowledge)
                    _stake("KNOWLEDGE.md synced to PROGRAMDATA")
        except Exception as e:
            _stake("KNOWLEDGE.md deploy skipped: %s" % e)

        try:
            _stake("--- injecting persona ---")
            from persona.indivi import injectPersona
            persona_name = injectPersona()
            if persona_name:
                _stake("persona loaded: %s" % persona_name)
                logger.info("Persona loaded: %s", persona_name)
                stk("persona.loaded", f"name={persona_name}", ok=True)
        except Exception as e:
            _stake("persona_injector skipped (non-fatal): %s" % e)
            logger.warning("persona_injector skipped (non-fatal): %s", e)

        # ── TLC 与重载车道：**在采集管线之前就上岗** ──
        #   TLC 是保命的东西（僵死/崩溃/慢任务 + 重载车道合并），它不该等采集
        #   管线跑完才启动 —— 那一等就是好几分钟，期间谁都没人管。
        #   消费端自己去要：先问客制化通道本体（user_prefs 两把 key），通道里写了
        #   立刻生效；没有才退回 mani 里的声明。这里是第一次，管线之后再刷新一次。
        try:
            # 先把干预声明写进客制化通道（不依赖载入/加载：TLC 是壳体的保命件），
            # 再让消费端自己去要 —— 声明与消费同一处完成。
            from persona.indivi import declare_tlc_consumer as _decl_tlc
            _decl_tlc()
        except Exception as e:
            _stake("tlc declaration write skipped (non-fatal): %s" % e)
        try:
            from main.tlc import (ensure_tlc_from_manifest as _tlc_up,
                                  ensure_reload_lanes_from_manifest as _lanes_up)
            if _tlc_up():
                _stake("tlc guard started (early，管线之前)")
            _lanes = _lanes_up()
            _stake("reload lanes early: %s" % (sorted(_lanes.keys()) or "（无声明）"))
        except Exception as e:
            _stake("tlc early start failed (non-fatal): %s" % e)

        # 4) 数据管线（重活）：**整段搬出 UI 进程**（壳进程不得影响页面）。
        #    scout.probe / injectCustomDemands / refresh_slot_declarations / large.run
        #    与随后的自愈采样·纠正 都在 main/boot_worker.py 里跑；本进程只发起、只读结论。
        #    起不来也不影响 UI（壳照常可用）—— 如实记一笔，由标记定时器兜底放行预装。
        #    时序不变：probe 先于 injectCustomDemands，声明先于 large.run（large 读这份声明）；
        #    自愈仍在管线之后（避免与 crawl 写 mani 并发），且每次启动只跑一次。
        _stake("--- kernel pipeline: handing off to boot_worker subprocess ---")
        try:
            boot_state["proc"] = _spawn_boot_worker(_BOOT_MARK_PATH)
            _stake("boot_worker spawned pid=%s" % boot_state["proc"].pid)
        except Exception as e:
            boot_state["failed"] = True
            _stake("boot_worker spawn FAILED (non-fatal): %s" % e)
            stk("boot.worker", "spawn failed: %s" % e, ok=False)

        _stake("--- bootstrap deferred done (bg thread) ---")

    def _bg_bootstrap() -> None:
        """后台线程入口：跑启动期轻活（探活/人格/TLC 早启）并发起重活子进程。"""
        try:
            _bootstrap_deferred()
        except Exception:
            _stake("_bootstrap_deferred crashed in bg thread!")
            _stake(traceback.format_exc())

    # 100ms 延迟让 UI 先渲染，然后切后台线程接启动期的活（重活已交给子进程）
    QTimer.singleShot(100, lambda: threading.Thread(
        target=_bg_bootstrap, daemon=True, name="bootstrap-bg").start())

    # ── 重活子进程的结论回读：1 秒一拍，只办 UI 动作（起 tlc/车道、弹文案、重启）──
    #    重活（probe / crawl / large.run / 自愈采样）全在子进程里 ⇒ 主线程只读一个小 json。
    def _after_kernel_pipeline() -> None:
        """管线跑完才做的轻活：按子进程刚重写过的 mani 起 tlc 守护 + 重载车道。"""
        try:
            from main.tlc import ensure_tlc_from_manifest
            if ensure_tlc_from_manifest():
                _stake("tlc guard started (mani 声明驱动)")
        except Exception as e:
            _stake("tlc guard start failed (non-fatal): %s" % e)
        try:
            from main.tlc import ensure_reload_lanes_from_manifest, reload_lane_stats
            _lanes = ensure_reload_lanes_from_manifest()
            _stake("reload lanes ready: %s" % (sorted(_lanes.keys()) or "（无声明）"))
        except Exception as e:
            _stake("reload lanes start failed (non-fatal): %s" % e)

    def _boot_marker_tick() -> None:
        """读子进程落的结论；只做 UI 动作。子进程没起/中途死 ⇒ 如实记账 + 放行预装。"""
        _proc = boot_state["proc"]
        if _proc is None:
            if not boot_state["failed"] or "self_heal" in boot_seen:
                return
            _stake("boot_worker never started — 放行预装（防永久挂起，沿用旧兜底）")
            boot_seen.add("self_heal")
            _boot_marker_timer.stop()
            try:
                browser.self_heal_done.emit()
            except Exception as _emit_e:
                _stake("self_heal done emit failed: %s" % _emit_e)
            return
        _mark = _read_boot_marker(_BOOT_MARK_PATH)
        if _mark.get("pipeline_done") and "pipeline" not in boot_seen:
            # 子进程已把 mani 落定 ⇒ 这边才按声明起 tlc 守护与重载车道
            boot_seen.add("pipeline")
            _after_kernel_pipeline()
        _sh = _mark.get("self_heal")
        if isinstance(_sh, dict) and _sh.get("done") and "self_heal" not in boot_seen:
            boot_seen.add("self_heal")
            _boot_marker_timer.stop()
            if _sh.get("healed"):
                # 纠正了偏差 ⇒ 弹文案并重启壳（动作与搬走前一致，只是判定来自子进程）
                stk("self_heal", "纠正了偏差，请求重启（子进程判定）", ok=True)
                try:
                    browser.self_heal_restart.emit()
                except Exception as _emit_e:
                    _stake("self_heal emit failed: %s" % _emit_e)
            else:
                # 健康 / 自愈异常：都放行预装（沿用旧语义，防永久挂起）
                try:
                    browser.self_heal_done.emit()
                except Exception as _emit2_e:
                    _stake("self_heal done emit failed: %s" % _emit2_e)
            return
        if _proc.poll() is not None:
            # 子进程中途死了：不许影响 UI（壳照常可用）—— 只在账上如实记 + 放行预装
            boot_seen.add("self_heal")
            _boot_marker_timer.stop()
            _stake("boot_worker exited without self_heal verdict: rc=%s" % _proc.returncode)
            if not _mark.get("pid"):
                _stake("  （标记里连启动那一笔都没有 ⇒ 子进程在导入/起飞阶段就没活下来）")
            stk("boot.worker", "exited without verdict rc=%s" % _proc.returncode, ok=False)
            try:
                browser.self_heal_done.emit()
            except Exception as _emit3_e:
                _stake("self_heal done emit failed: %s" % _emit3_e)

    _boot_marker_timer = QTimer()
    _boot_marker_timer.setInterval(1000)
    _boot_marker_timer.timeout.connect(_boot_marker_tick)
    _boot_marker_timer.start()

    # ── 退出时无需再停探针：常驻循环已删，探针只在需要时探一次；
    #    标记定时器随进程一起走（1 秒一拍、有结论或有子进程时才做事），无需收尾回调。──

    _stake("--- entering app.exec() ---")
    sys.exit(app.exec())


# ══════════════════════════════════════════════════════════════
# ═══ main.stake — 方法化并入 main.py ═══
# ══════════════════════════════════════════════════════════════
# 诊断桩 — 写关键节点到 %PROGRAMDATA%\Rheia\stake.log
#
# 两类桩:
#   - stk(tag, msg, ok): 主动打点，记录功能性节点
#   - 全局异常钩子: 捕获 Python/Qt/线程中任何未处理异常，落盘 stake.log
#
# 调用方: main/main.py, ui/web/browser.py, ui/web/provider_setup.py
#

import os
import sys
import traceback
from datetime import datetime

_STAKELOG_DIR = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia")
_STAKELOG_PATH = os.path.join(_STAKELOG_DIR, "stake.log")

def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def stk(tag: str, msg: str = "", ok: bool = True) -> None:
    """写一行桩子日志。

    tag: 来源标签, 如 "gw.wake", "gw.token", "browser.load", "provider.check"
    msg: 附加信息
    ok:  True→OK, False→FAIL
    """
    try:
        os.makedirs(_STAKELOG_DIR, exist_ok=True)
        flag = " OK" if ok else "FAIL"
        line = f"[{_now()}] [{tag}] {flag} {msg}\n"
        with open(_STAKELOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════════
#  全局异常钩子 — 任何未捕获的 Python/Qt/线程异常都会落盘
# ═══════════════════════════════════════════════════════════════════

_original_excepthook = None
_original_unraisablehook = None
_original_thread_excepthook = None

def _write_crash(label: str, etype: type, value: BaseException, tb) -> None:
    """将崩溃信息写入 stake.log，格式紧凑但关键信息完整。"""
    try:
        os.makedirs(_STAKELOG_DIR, exist_ok=True)
        tb_lines = traceback.format_exception(etype, value, tb)
        tb_compact = "".join(tb_lines[-4:])  # 最后 4 帧通常已足够定位
        line = (
            f"[{_now()}] [{label}] CRASH {etype.__name__}: {value}\n"
            f"{tb_compact.rstrip()}\n"
        )
        with open(_STAKELOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass

def _global_excepthook(etype, value, tb):
    """Python 全局未捕获异常 → stk + 保持原有行为。

    主线程未捕获异常 = 壳体将崩（壳级）→ 上报 tlc（守护只看壳体崩没崩）。
    """
    _write_crash("python.crash", etype, value, tb)
    try:
        from main.tlc import report_crash
        report_crash("shell_crash")
    except Exception:
        pass
    if _original_excepthook:
        _original_excepthook(etype, value, tb)

def _unraisablehook(args):
    """sys.unraisablehook：对象析构等处的不可达异常 → stk。"""
    etype = args.exc_type
    value = args.exc_value
    tb = args.exc_traceback
    if etype and value:
        _write_crash("unraisable.crash", etype, value, tb)
    if _original_unraisablehook:
        _original_unraisablehook(args)

def _thread_excepthook(args):
    """threading.excepthook：线程内未捕获异常 → stk（threading.Thread 默认直接死，不被 excepthook 覆盖）。"""
    etype = args.exc_type
    value = args.exc_value
    tb = args.exc_traceback
    if etype and value:
        _write_crash("thread.crash", etype, value, tb)
    if _original_thread_excepthook:
        _original_thread_excepthook(args)

def install_crash_hooks() -> None:
    """安装全局异常钩子。放在 main() 开头，QApplication 创建后调用。

    覆盖三个层级:
      - sys.excepthook: Python 主线程未捕获异常
      - threading.excepthook: 后台线程未捕获异常 (Python 3.8+)
      - Qt 层面: QApplication 有自己的一套异常处理，对 Python 层例外仍能拦截
    """
    global _original_excepthook, _original_unraisablehook, _original_thread_excepthook

    # 1) Python 全局
    _original_excepthook = sys.excepthook
    sys.excepthook = _global_excepthook

    # 2) sys.unraisablehook：对象析构等处的不可达异常
    if hasattr(sys, "unraisablehook"):
        _original_unraisablehook = getattr(sys, "unraisablehook", None)
        sys.unraisablehook = _unraisablehook

    # 3) threading.excepthook：后台线程未捕获异常 (Python 3.8+)
    import threading
    if hasattr(threading, "excepthook"):
        _original_thread_excepthook = threading.excepthook
        threading.excepthook = _thread_excepthook

    stk("stake.crash_hooks", "installed (sys+threading+qt)")

# ══════════════════════════════════════════════════════════════
# ═══ main.cli_usage — 方法化并入 main.py ═══
# ══════════════════════════════════════════════════════════════
# 从 mani.json 的 clis 查「这个动作该调哪个官方命令」。逻辑代码生代码的唯一查询口。
#
# 所有需要「找 CLI 要完整命令」的地方都走这里，禁止在代码里手抄 openclaw 命令。
# mani.json 的 clis 是 spider 从官方文档爬出来的（数据），这里只查索引：
#   cli_usage("models", "status")   -> "openclaw models status"
#   cli_usage("plugins", "install") -> "openclaw plugins install"
#
# 查不到返回空串，调用方跳过，不猜、不兜底。
#

import json
import os

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MANI_PATH = os.path.join(_PROJ, "kernel", "large", "mani.json")

_cache: dict | None = None

# ── 官方明文锚点（少数命令，官方聚合文档 llms-full.txt 缺对应段）──
# openclaw config 命令官方有独立页 cli/config.md（本地 OfficialDocument 有），
# 但 scout 拉的聚合文本 llms-full.txt 里没有 /cli/config 段（只有 /cli/configure），
# 所以 spider 抓不到、mani clis 里没有。这类命令作为「官方明文锚点」集中声明，
# 不散落在各 UI 文件里硬编。官方改了这里集中改一处。
_ANCHOR_COMMANDS: dict[tuple[str, str], str] = {
    ("config", "set"): "openclaw config set",
    ("config", "patch"): "openclaw config patch",
    ("config", "get"): "openclaw config get",
    ("sessions", "list"): "openclaw sessions",
    ("openclaw", "version"): "openclaw --version",
    # doctor --fix 官方 cli/doctor.md:52 明文有，但 spider 抓的聚合文本里 doctor
    # 段只长出了 --lint（--fix 没进 usage 清单），与 config set 同属「聚合文档缺段」。
    ("doctor", "fix"): "openclaw doctor --fix",
    ("doctor", "repair"): "openclaw doctor --fix",
    # gateway 生命周期：官方 cli/gateway/restart-and-supervision.md:19-33 明文
    # 「Restart the Gateway」= `openclaw gateway restart`（普通重启走 service-manager
    # 路径，即 stop+start 冷启动）；cli/gateway/service.md 明文有 start / stop。
    # 为什么单列在这里：这些是**壳自己的运维动作**，不是卡/manifest 长出来的消费端，
    # spider 抓不到 ⇒ mani 的 clis 里没有 ⇒ 不声明就会 `cli_usage 查不到，跳过`
    # （2026-09-21 实测：装完插件该重启网关加载，结果 rc=-1 跳过，靠后面自愈重启兜住）。
    #
    # ⚠ 只记**普通重启**，禁止把 `--force` 当默认：官方的口径是普通 restart 没把监听
    #   顶起来时，才由**外部终端**执行 `openclaw gateway restart --force`（同页:96-100）。
    #   把强启塞进侧边栏按钮 = 拿官方的兜底路径当常规手段，产品方 2026-09-21 明令不许。
    ("gateway", "restart"): "openclaw gateway restart",
    ("gateway", "status"): "openclaw gateway status",
    ("gateway", "start"): "openclaw gateway start",
    ("gateway", "stop"): "openclaw gateway stop",
    # 插件代码/清单改动后的官方动词（cli/plugins.md:50、cli/plugins/list.md:38 明文）：
    # `openclaw plugins reload <id>`。热重载列首位，冷启动只作兜底。
    ("plugins", "reload"): "openclaw plugins reload",
    # 打开程序第一步跑的官方 dashboard（见 _wsl_prewarm）。mani 的 clis 里 dashboard
    # 那条的 usages.action 写的是**整条命令**（"openclaw dashboard"）而不是动词，
    # cli_usage 按「command + action」拼会拼成两条命令 ⇒ 走锚点表，声明一次。
    # `--yes` = 官方明文「start/install the Gateway without prompting when needed」；
    # `--no-open` = 不在 WSL 里弹浏览器（壳只借它把网关拉起来）。
    ("dashboard", "open"): "openclaw dashboard",
}

def _load_clis() -> list[dict]:
    global _cache
    if _cache is None:
        _cache = {}
        try:
            with open(_MANI_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            _cache = (data.get("entries") or {}).get("clis") or []
        except (OSError, json.JSONDecodeError) as _e:
            # 读不到 mani.json ⇒ 索引退化为空表（相关动作照既定策略跳过），但别静默、排障要有账
            logger.warning("mani.json 读/解析失败（CLI 索引退化为空表）：%s", _e)
            _cache = []
    return _cache

def cli_usage(cli_id: str, action: str, option: str = "") -> str:
    """从 mani 的 clis 查「cli_id 这个命令的 action 动作」完整命令行。

    查不到时回落官方明文锚点表（少数命令官方聚合文档缺段）。
    option：可选，附加官方明文的 flag，不硬编新参数。
    """
    for c in _load_clis():
        if (c.get("id") or "") != cli_id:
            continue
        for u in c.get("usages") or []:
            if (u.get("action") or "") != action:
                continue
            cmd = (c.get("command") or "").strip()
            if not cmd:
                return ""
            suffix = f"{action} {option}".strip() if option else action
            return f"{cmd} {suffix}".strip()

    # 官方明文锚点兜底
    anchor = _ANCHOR_COMMANDS.get((cli_id, action))
    if anchor:
        return f"{anchor} {option}".strip() if option else anchor
    return ""

# ══════════════════════════════════════════════════════════════
# ═══ main.wsl_config — 方法化并入 main.py ═══
# ══════════════════════════════════════════════════════════════
# WSL ~/.openclaw/openclaw.json 直读直写 — 不走 CLI，不依赖 Gateway。
#
# 所有 openclaw.json 的直接文件操作统一走此模块。
# 不再调用 openclaw config set / openclaw config patch / openclaw onboard。
#

import json
import os
import subprocess
import tempfile
import threading
import time
from typing import Any

WSL_DISTRO = os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu"

# 进程级配置缓存：openclaw.json 直读很贵（每次 subprocess 起 wsl.exe 冷启动 VM，
# 面板呼出会卡）。同一份配置短时间内缓存，避免多个面板/多次呼出重复起 WSL。
# write() 成功后失效缓存，保证读到的一定是刚写的值。
_cache: dict[str, Any] | None = None
_cache_lock = threading.Lock()
_CACHE_TTL_SEC = 5.0
_cache_time: float = 0.0

def invalidate_cache() -> None:
    """写配置后失效缓存（write 内部已调用，供外部强制刷新）。"""
    global _cache, _cache_time
    with _cache_lock:
        _cache = None
        _cache_time = 0.0

def _wc_run_wsl(cmd: str) -> tuple[int, str, str]:
    """在 WSL 中执行一条命令。静默，无弹窗。

    无时限（时限是禁忌）：命令返回即往下走。
    """
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    r = subprocess.run(
        cmd, shell=True, capture_output=True,
        encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
        startupinfo=si,
    )
    return r.returncode, (r.stdout or ""), (r.stderr or "")

def win_to_wsl(win: str) -> str:
    """Windows 路径 → WSL /mnt/ 路径。"""
    if not win or len(win) < 2:
        return win
    if win[1] == ":":
        return f"/mnt/{win[0].lower()}{win[2:].replace(chr(92), '/')}"
    return win.replace("\\", "/")

def read() -> dict[str, Any]:
    """读取 WSL ~/.openclaw/openclaw.json 为 dict。文件不存在或解析失败返回空。

    带进程级缓存（短 TTL）：避免面板呼出时每次 subprocess 起 wsl.exe 冷启动
    VM 卡 UI。写配置后失效，保证读到刚写的值。
    """
    global _cache, _cache_time
    with _cache_lock:
        if _cache is not None and (time.time() - _cache_time) < _CACHE_TTL_SEC:
            return _cache

    rc, out, _ = _wc_run_wsl(
        f'wsl -d {WSL_DISTRO} -u root -- bash -c "cat ~/.openclaw/openclaw.json 2>/dev/null"')
    result: dict[str, Any] = {}
    if rc == 0 and out.strip():
        try:
            result = json.loads(out)
        except json.JSONDecodeError as _je:
            # ⚠ 解析失败退回空 dict：调用方可能以为「配置本来就是空的」而把真实配置覆写掉。
            #   这里至少留账（含原文前 200 字），排障时看得见。
            logger.warning("openclaw.json 解析失败（退回空 dict，写回有覆盖风险）：%s | %s",
                           _je, out.strip()[:200])
            result = {}
    with _cache_lock:
        _cache = result
        _cache_time = time.time()
    return result

def write(cfg: dict[str, Any]) -> bool:
    """直接覆写 WSL ~/.openclaw/openclaw.json，不走 CLI。

    流程：写临时文件到 Windows → win_to_wsl → cp 进 WSL。
    写成功后失效读缓存，保证下次 read() 拿到刚写的值。
    """
    tmp: str | None = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".json", prefix="openclaw-")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        wsl_tmp = win_to_wsl(tmp)
        rc, _, _werr = _wc_run_wsl(
            f'wsl -d {WSL_DISTRO} -u root -- bash -c '
            f'"mkdir -p \\$HOME/.openclaw && cp \'{wsl_tmp}\' \\$HOME/.openclaw/openclaw.json"',
        )
        if rc == 0:
            invalidate_cache()
        else:
            # 旧写法只 return False、不留账 ⇒ 写配置失败的原因查不到
            logger.warning("写 openclaw.json 失败（rc=%s）：%s", rc, (_werr or "").strip()[:200])
        return rc == 0
    except Exception as _we:
        logger.warning("写 openclaw.json 异常：%s: %s", type(_we).__name__, _we)
        return False
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass

# ══════════════════════════════════════════════════════════════
# ═══ main.i18n_guard — 方法化并入 main.py ═══
# ══════════════════════════════════════════════════════════════
# runtime/i18n_guard.py — 日语环境检测 + 拒载（红线 §2 收尾 + §3）
#
# 自包含，不依赖 ui/（避免循环导入）。installer/ 和 runtime/ 都能 import。
#
# 多源判定（规范 §3.1）：
#   语言类判定（任一条命中即候选）：
#     1. Windows 显示语言 GetUserDefaultUILanguage() ∈ {0x11, 0x411, 0xC11}
#     2. WSL 发行版 locale LANG/LC_ALL 为 ja_JP.*（只读探测，不写）
#     3. 环境覆盖 RHEIA_LOCALE ∈ {ja, ja-JP, ja_JP}
#   佐证（辅助，不单独触发）：
#     4. 系统时区为 Asia/Tokyo
#
# 判定三态（规范 §3.1 + 零容忍）：
#   japanese  — 系统 UI 语言命中（lang_id 单条即命中，零容忍），或 ≥2 条语言类，
#               或 1 条语言类 + 1 条佐证 → 拒载
#   suspected — 仅 wsl_locale 单条命中、无佐证 → 不拒载，只记日志
#   none      — 无命中
#   bypass    — RHEIA_LOCALE 被强制设 ja* 但系统/WSL 均非不支持语言（防绕过，返回 japanese）
#
# 拒载语义（规范 §3.2）：拒载 ≠ 回落。命中 japanese 时，调用方
# （main/main.py、installer/install.py）弹中文提示后 sys.exit / 中止安装。

import os
import sys
from datetime import datetime, timezone

# 日语 LANG ID（红线功能，必须保留）
_JA_LANG_IDS = {0x11, 0x411, 0xC11}

# 日语环境变量覆盖值
_JA_LOCALE_VALUES = {"ja", "ja-jp", "ja_jp"}

# 日本时区（佐证，不单独触发）
_JA_TIMEZONE = "Asia/Tokyo"

# 安全日志目录
_LOG_DIR = os.path.join(
    os.environ.get("PROGRAMDATA", ""), "Rheia", "log", "shell_i18n_guard"
)

def _detect_windows_lang() -> bool:
    """检测源 1：Windows 显示语言是否日语。"""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        lang_id = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())
        return lang_id in _JA_LANG_IDS
    except Exception as _le:
        # 红线判定失败时只能按「非日语」放行，但必须留账（旧写法静默无痕）
        _stake("i18n_guard：读系统 UI 语言失败（按非日语放行）：%s" % _le)
        return False

# 简体中文 LANG ID（0x0804 = zh-CN 简体中文；0x0004 = zh-Hans）
_ZH_CN_LANG_IDS = {0x0804, 0x0004}

def is_simplified_chinese() -> bool:
    """检测当前系统是否为简体中文（zh-CN）。

    用于「国内频道」模块门控：非简体中文系统 → 抛弃该模块。
    只认 Windows 显示语言（GetUserDefaultUILanguage）主源，最权威、最快。
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        lang_id = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())
        return lang_id in _ZH_CN_LANG_IDS
    except Exception:
        return False

def _detect_wsl_locale() -> bool:
    """检测源 2：已拉起 WSL 发行版内 LANG/LC_ALL 是否 ja_JP.*（只读探测，不写）。"""
    if sys.platform != "win32":
        return False
    try:
        import subprocess
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        r = subprocess.run(
            'wsl -d Ubuntu -- bash -c "echo LANG=$LANG LC_ALL=$LC_ALL"',
            shell=True, capture_output=True,
            encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=si,
        )
        out = (r.stdout or "").lower()
        return "ja_jp" in out or "ja-jp" in out
    except Exception as _le:
        # 探测源不可用 ⇒ 这一源不命中，但留账（旧写法静默无痕）
        _stake("i18n_guard：探测 WSL locale 失败（该源不参与判定）：%s" % _le)
        return False

def _detect_env_override() -> bool:
    """检测源 3：RHEIA_LOCALE 是否被置为 ja*。"""
    v = os.environ.get("RHEIA_LOCALE", "").strip().lower()
    return v in _JA_LOCALE_VALUES

def _detect_tz() -> bool:
    """佐证：系统时区是否 Asia/Tokyo。"""
    if sys.platform != "win32":
        return False
    try:
        # 读注册表 TimeZoneKeyName（如 "Tokyo Standard Time"），比 time.tzname 稳——
        # 后者是本地化名，可能随 UI 语言变化，不可靠。
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\TimeZoneInformation",
        )
        tz_name, _ = winreg.QueryValueEx(key, "TimeZoneKeyName")
        winreg.CloseKey(key)
        return tz_name in ("Tokyo Standard Time", "Osaka Standard Time",
                           "Sapporo Standard Time", "Yakutsk Standard Time")
    except Exception as _te:
        # 佐证源读不到 ⇒ 不据此判定，但留账（旧写法静默无痕）
        _stake("i18n_guard：读时区佐证失败（不据此判定）：%s" % _te)
        return False

def _write_log(state: str, sources: list[str], action: str) -> None:
    """写安全日志（规范 §6）：时间戳 UTC + 命中源 + 动作 + 结果，不含身份信息。"""
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        fname = datetime.now(timezone.utc).strftime("%Y%m%d") + ".log"
        line = (
            f"[{ts}] state={state} action={action} "
            f"sources={','.join(sources) if sources else '-'}\n"
        )
        with open(os.path.join(_LOG_DIR, fname), "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass

def check_detailed() -> tuple[str, list[str]]:
    """检测不支持的语言环境，返回 (state, hits)。

    state 三态：japanese / suspected / none；hits 是命中的语言类检测源名
    （lang_id / wsl_locale / env），供调用方（如自毁）写审计日志时记录命中源。

    命中 japanese 时写日志（action=reject）；suspected 只记日志（action=suspect）；
    none 不写日志。
    """
    hits: list[str] = []
    if _detect_windows_lang():
        hits.append("lang_id")
        # 零容忍：系统 UI 语言已是日语，直接判拒载，不再探测 WSL locale。
        # _detect_wsl_locale 会执行 wsl -d Ubuntu 拉起 WSL/Hyper-V，日文系统切换
        # 后系统处于不稳定过渡期，拉起 WSL 可能诱发 Hyper-V 虚拟化层内核崩溃
        # （2026-08-25 实测蓝屏 0x3B）。日语命中后根本不该碰 WSL。
    else:
        if _detect_wsl_locale():
            hits.append("wsl_locale")
    if _detect_env_override():
        hits.append("env")

    tz_hit = _detect_tz()

    # 防绕过（规范 §7）：RHEIA_LOCALE 被强制设 ja*，但系统语言与 WSL locale
    # 均非不支持语言 → 这是「人为强制环境变量绕过检测」的矛盾组合，判为疑似
    # 绕过：拒绝 + 留痕（action=bypass），而不是静默放行。三态协议不变，
    # 仍返回 "japanese"（调用方只看 japanese 即拒载），仅日志 action 区分。
    if "env" in hits and "lang_id" not in hits and "wsl_locale" not in hits:
        _write_log("japanese", ["env"], "bypass")
        return "japanese", ["env"]

    # 三态判定（规范 §3.1 + 零容忍）：
    #   lang_id 单条命中即 japanese —— 系统 UI 语言是不支持语言 = 直接拒载/自毁，
    #   不要求佐证或第二条。这是针对「装时改英文逃过、装完改回」的蓄意绕过，
    #   对蓄意行为防误伤没有意义，宁严勿松。
    #   其余语言类（wsl_locale）单条仍要佐证，避免误伤 WSL 内部 locale 的技术性设置。
    if "lang_id" in hits:
        state = "japanese"
    elif len(hits) >= 2:
        state = "japanese"
    elif len(hits) == 1 and tz_hit:
        state = "japanese"
    elif len(hits) == 1:
        state = "suspected"
    else:
        state = "none"

    if state == "japanese":
        _write_log(state, hits, "reject")
    elif state == "suspected":
        _write_log(state, hits, "suspect")

    return state, hits

def check() -> str:
    """检测不支持的语言环境，返回三态之一：japanese / suspected / none。

    调用方根据返回值决定拒载与否。需要命中源细节时用 check_detailed()。
    """
    state, _ = check_detailed()
    return state

def write_audit_log(action: str, sources: list[str], state: str = "japanese") -> None:
    """公共审计日志入口（供 shell_self_destruct 等复用 in0001 的日志函数）。

    action=self_destruct 时记 action，其余字段同 check() 的 reject/suspect 日志。
    """
    _write_log(state, sources, action)

# ── 拒载提示文案（规范 §3.2，中文，不可改）──
REJECT_MSG = "本产品不支持日语语言环境"

if __name__ == "__main__":
    # 自检：打印检测结果
    state = check()
    print(f"i18n_guard.check() = {state}")

# ══════════════════════════════════════════════════════════════
# ═══ main.shell_self_destruct — 方法化并入 main.py ═══
# ══════════════════════════════════════════════════════════════
# runtime/shell_self_destruct.py — 壳层自毁（红线 §4）
#
# 检测命中不支持的语言环境（in0001 判定 japanese）后，清掉壳层自己的东西。
#
# 铁律（规范 §4.3，代码评审点）：
#   ❌ 不 import / 不调用 main/license_anchor.py 的 self_destruct() / lock_wsl_instance()
#      （授权自毁锁 vhdx 头部，与本路径无关；两条自毁路径完全独立）
#   ❌ 不调任何 `wsl` 命令
#   ❌ 不触碰任何 .vhdx（vhdx 在 C:\wsl-distro，与壳层物理隔离）
#   ❌ 不改 WSL 内 ~/.openclaw/、openclaw 安装/运行目录
#   ✅ 作用域仅限壳层自己的 Windows 侧文件（ProgramData 数据 + {app} 目录）
#
# 自毁动作有序执行（规范 §4.2，任一失败不阻断后续）：
#   1. 文件级白名单删除 ProgramData 壳层数据
#   2. 提权删除 {app} 安装目录（pythonw 退出后清）
#   3. 写审计日志（复用 in0001 的日志函数，action=self_destruct）

import os
import shutil
import subprocess

# 壳层自身数据文件白名单（in0002 已实测清单，逐个 unlink，绝不用 rmdir /s 整目录）
_SHELL_FILES = [
    "baseline_pool.db",
    "gateway_config.json",
    "sidebar_custom.json",
    "user_identity.json",
    "stake.log",
    "runtime_stake.log",
    "install.log",
    "download.log",
    "installprogress.json",
    "bonus_skills_done",
    "failed_skills.json",
    "KNOWLEDGE.md",
    "license_anchor.bin",
    "pip_venv_done",
    "wsl_full_install.done",
]

# 壳层自建目录（白名单内的目录名，删目录内文件；不在此表的一律不碰）
_SHELL_DIRS = [
    "avatar",
    "avatars",
]

# 归属校验标志文件：{app} 安装目录下必须同时存在这些文件，才认定目标是壳层自身安装目录。
# 用标志文件做归属校验，而非硬编固定路径——安装目录可由 Inno Setup 客制（设计哲学零硬编），
# 但壳层入口 main/main.py 在任何合法安装目录下都必然存在，是判定"这就是壳层"的可靠信号
# （2026-09-07 方法化归并后 runtime/ 目录消亡：入口归 main/main.py，红线哨兵归 main/license_anchor.py）。
_SHELL_APP_MARKERS = [
    os.path.join("main", "main.py"),
    os.path.join("main", "license_anchor.py"),
]

def _progdata() -> str:
    return os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia")

def _delete_file_if_exists(path: str) -> None:
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError as _de:
        # 删壳尽力而为，但别静默（旧写法是裸 pass）
        logger.warning("自毁：删除壳层文件失败（继续）：%s (%s)", path, _de)

def _delete_progdata_shell_files() -> None:
    """文件级白名单删除 ProgramData 壳层数据。

    vhdx 已移到 C:\\wsl-distro（物理隔离壳层），不在 ProgramData 下，天然安全。
    """
    base = _progdata()
    for name in _SHELL_FILES:
        _delete_file_if_exists(os.path.join(base, name))
    for name in _SHELL_DIRS:
        d = os.path.join(base, name)
        if os.path.isdir(d):
            # ⚠ 这里用的是 shutil.rmtree：**递归删掉这个白名单目录本身**（头像等整棵子树），
            #   不是「只删目录内文件」—— 旧注释与实现相反，一并改对。
            #   目录名写死在 _SHELL_DIRS 白名单里，绝不越出白名单、不碰白名单外任何路径。
            try:
                shutil.rmtree(d, ignore_errors=True)
            except OSError:
                pass

def _resolve_app_dir() -> str:
    """解析 {app} 安装目录的绝对路径，并通过归属校验。

    app_dir 不得直接用 __file__ 相对推算当删除目标（文件位置异常时会算错删除对象）。
    这里先把候选路径绝对化（normpath + abspath），再用归属标志文件校验：
    候选目录下必须同时存在 main/main.py + main/license_anchor.py，
    才认定它是壳层自身安装目录，否则视为不可信、返回空串（调用方跳过删除）。

    无外部参数时，候选路径仍从本文件的 __file__ 推算（这是唯一可靠线索），
    但推算结果必须过归属校验才算数，杜绝"算错就误删"。
    """
    raw = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        cand = os.path.normpath(os.path.abspath(raw))
    except OSError:
        return ""
    # 归属校验：标志文件齐备才可信
    for rel in _SHELL_APP_MARKERS:
        if not os.path.isfile(os.path.join(cand, rel)):
            return ""
    return cand

def _schedule_app_dir_removal(app_dir: str) -> None:
    r"""提权删除 {app} 安装目录（pythonw 退出后清目录）。

    {app} 文件是 Inno Setup 以 admin 创建的，普通用户进程 rmdir 会「访问被拒绝」。
    用 ctypes ShellExecuteW 的 runas 动词提权执行 `cmd /c rmdir /s /q`（uninstall.c 验证过），
    弹一次 UAC 让用户确认，确认后以管理员权限删干净。
    不调 wsl、不碰 vhdx（vhdx 在 C:\wsl-distro，与 {app} 隔离）。

    删除前再次做归属校验：只有通过 _resolve_app_dir() 校验的绝对路径才允许删除。
    """
    if not app_dir or not os.path.isdir(app_dir):
        return
    # 二次归属校验（调用方传入的 app_dir 也必须过标志文件校验，防误删）
    if os.path.normpath(os.path.abspath(app_dir)) != _resolve_app_dir():
        return
    try:
        # 提权删 {app}：用 ShellExecuteW 的 runas 动词（uninstall.c 验证过能删 admin 文件），
        # 弹一次 UAC，确认后以管理员权限 rmdir 删干净。绝不调 wsl、不碰 vhdx。
        import ctypes
        args = '/c rmdir /s /q "{}"'.format(app_dir)
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", "cmd.exe", args, None, 0,  # SW_HIDE
        )
    except Exception as _se:
        # 提权删除失败别静默（旧写法是裸 pass）
        logger.warning("自毁：提权删 {app} 失败（继续）：%s", _se)

def self_destruct(app_dir: str | None = None, sources: list[str] | None = None) -> None:
    """执行壳层自毁（有序，任一失败不阻断后续）。

    app_dir: {app} 安装目录；None 时不延迟删（调用方可自算）。
    sources: 命中检测源名列表（lang_id / wsl_locale / env），写入审计日志。
    """
    _delete_progdata_shell_files()

    if app_dir is None:
        # 默认取本文件所在 runtime/ 的上一级 = {app}（安装后 main.py 同款算法），
        # 但必须过归属校验（标志文件齐备）才认定可信，否则返回空串、跳过删除。
        app_dir = _resolve_app_dir()
    _schedule_app_dir_removal(app_dir)

    # 写审计日志（复用 in0001 日志函数，action=self_destruct）
    try:
        write_audit_log("self_destruct", list(sources or []), state="japanese")
    except Exception as _ae:
        # 审计账写失败不阻断自毁，但别静默（旧写法是裸 pass）
        logger.warning("自毁：写审计日志失败（继续）：%s", _ae)

if __name__ == "__main__":
    # 自检入口：只打印计划，不实际执行（避免误触发）。真实调用走 self_destruct()。
    print("shell_self_destruct 自检（不执行）")
    print("  progdata =", _progdata())

# ══════════════════════════════════════════════════════════════
# ═══ main.gateway_recovery — 方法化并入 main.py ═══
# ══════════════════════════════════════════════════════════════
# Gateway 恢复模块 — 当 openclaw update 中途崩溃、openclaw 二进制损坏
# 或 Gateway 被意外停止时，执行诊断和自动修复。
#
# 恢复步骤:
#   1. 诊断 openclaw CLI 是否可用（openclaw --version）
#   2. 若损坏: npm repair（国内镜像优先）
#   3. 尝试 openclaw gateway start（systemd）
#   4. 若 systemd 不可用: 直接启动 openclaw gateway
#   5. 等待就绪 curl 探活
#
# 调用方: diag_panel._UpgradeWorker（升级失败后自动回滚恢复）
#          diag_panel._RestartWorker（重启失败后自动修复）
#          main/main.py（启动时 Gateway 不可用）
#
# 设计原则:
#   - 自包含，不依赖 ui/ 或 kernel/ 模块（避免循环导入）
#   - 所有 WSL 调用**无时限**（时限是禁忌）—— 命令返回即往下走；
#     阻塞成本由调用方自负（旧注释写\"带超时保护\"，与实现相反，一并改对）
#   - 国内镜像优先，直连作为回退
#

import subprocess
import threading
import time

WSL_DISTRO = os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu"
GATEWAY_PORT = 18789
_NPM_REGISTRY = "https://registry.npmmirror.com"

# ── Gateway 生命周期所有权锁 ─────────────────────────────────────────
# 多个入口（keepalive 守护激活 / UI 手动重启）都能改变 Gateway 进程状态。
# 若并发，两个启动命令会撞上 OpenClaw 单实例迁移锁，Gateway 永久起不来
# （2026-08-22 实际发生：配模型双重重启 → startup migrations already running）。
#
# 用「所有权」模型而非简单锁：谁在管理 Gateway 生命周期，别人就不能再动。
# 非阻塞 acquire——已有 owner 时直接放弃本轮，不排队、不阻塞，由 owner 完成后
# 的 reload 回调接管刷新。纯逻辑约束，无时间常量、无 provider 特例。
_lifecycle_guard = threading.Lock()
_lifecycle_owner: str | None = None
# 「owner 是不是还活着」的凭据 = acquire 的那条线程的 ident（只认线程活体，**不看秒数**）。
# ⚠ 旧写法是 `_LIFECYCLE_STALE_SECONDS = 180.0`：持有超过 180s 就判 owner 已死、强制接管 ——
#   那是拿秒数弥合竞态（壳侧不留时限）—— 判定改为看线程活体，不再靠时长；
#   （20s 比冷启动还短 ⇒ 还在跑的 owner 被误判已死、别人抢锁再发一次 start，两边撞单实例
#   迁移锁，网关卡在 inactive 起不来）。改成看线程活体，这类误判不可能再发生。
_lifecycle_owner_ident: int | None = None

def _owner_thread_alive() -> bool:
    """owner 那条线程还活着吗 —— 只认线程活体（**不掐表**）。"""
    _ident = _lifecycle_owner_ident
    if _ident is None:
        return False
    for _t in threading.enumerate():
        if _t.ident == _ident:
            return _t.is_alive()
    return False

def acquire_lifecycle(owner: str) -> bool:
    """非阻塞获取 Gateway 生命周期所有权。已有 owner 在管理则返回 False。

    锁泄漏兜底：**判「owner 已死」只认那条线程还在不在**（不等秒数、不掐表）——
    owner 线程中途崩断、被并发 WSL 段错误带走等 ⇒ 线程没了 ⇒ 强制接管，避免 Gateway
    死了永远没人能修（auto_repair 一直 lifecycle owned elsewhere skip）。
    ⚠ 如实记代价：owner 线程若「活着但被 WSL 调用挂死」，这里会一直等它（壳侧不留时限）；
    由调用方保证 owner 段的 WSL 调用不会永久挂死。
    """
    global _lifecycle_owner, _lifecycle_owner_ident
    with _lifecycle_guard:
        if _lifecycle_owner is not None:
            if _owner_thread_alive():
                return False
            # owner 线程已经没了（泄漏）→ 强制接管
            _lifecycle_owner = None
        _lifecycle_owner = owner
        _lifecycle_owner_ident = threading.get_ident()
        return True

def release_lifecycle(owner: str) -> None:
    """释放生命周期所有权（仅当 owner 匹配，防止误释放别人的所有权）。"""
    global _lifecycle_owner
    with _lifecycle_guard:
        if _lifecycle_owner == owner:
            _lifecycle_owner = None
            _lifecycle_owner_ident = None

def _gw_run_wsl(cmd: str) -> tuple[int, str, str]:
    """在 WSL 中执行命令，返回 (returncode, stdout, stderr)。

    无时限（时限是禁忌）：命令返回即往下走。
    """
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    r = subprocess.run(
        cmd, shell=True, capture_output=True,
        encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
        startupinfo=si,
    )
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()

def _run_official_cmd(cli_id: str, action: str, option: str = "") -> tuple[int, str, str]:
    """从 mani 长出一条官方命令并执行（逻辑代码生代码，不手抄命令）。

    命令来自 cli_usage(cli_id, action, option)，查不到返回空则跳过（不兜底、不猜）。
    统一带 PATH 前缀：openclaw 装在 /usr/local/bin，systemd 服务环境里可能不继承
    交互 shell 的 PATH。
    不给官方命令设时限：它按自己的节奏跑，返回即往下走。
    """
    cmd = cli_usage(cli_id, action, option)
    if not cmd:
        return -1, "", f"cli_usage({cli_id!r}, {action!r}) 查不到，跳过"
    return _gw_run_wsl(
        f"wsl -d {WSL_DISTRO} -u root -- bash -c "
        f"\"export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; {cmd} 2>&1\"",
    )

def _get_node_install_script() -> str:
    """Generate Node.js install bash script — single pipeline.

    Resolve /latest/ HTML (official) → download tarball (mirror) → extract.
    No index.json, no fallback, no version check, no apt.
    无时限（时限是禁忌）：脚本里的 curl 不带 --connect-timeout/--max-time。
    """

    return (
        'NODE_OFFICIAL="https://nodejs.org/dist"\n'
        'NODE_MIRROR="https://npmmirror.com/mirrors/node"\n'
        'echo "[gateway_recovery] resolving latest Node.js..."\n'
        # ——— Step 1: arch mapping ———
        'ARCH=$(uname -m)\n'
        'case "$ARCH" in\n'
        '  x86_64) NODE_ARCH="x64" ;;\n'
        '  aarch64|arm64) NODE_ARCH="arm64" ;;\n'
        '  armv7l) NODE_ARCH="armv7l" ;;\n'
        '  *) NODE_ARCH="x64" ;;\n'
        'esac\n'
        # ——— Step 2: 从官方 /latest/ HTML 目录列表抓 tarball 名 ———
        # nodejs.org /dist/latest/ 是 nginx 静态 HTML，不需要 JS
        'TARBALL=$(curl -fsSL '
        '"$NODE_OFFICIAL/latest/" 2>/dev/null | '
        'grep -oP "node-v[0-9]+\\.[0-9]+\\.[0-9]+-linux-${NODE_ARCH}\\.tar\\.xz" | head -1)\n'
        'if [ -z "$TARBALL" ]; then\n'
        '  echo "[gateway_recovery] FATAL: cannot resolve latest Node.js"\n'
        '  exit 1\n'
        'fi\n'
        # ——— Step 3: 提取版本号 ———
        'TARGET_VER=$(echo "$TARBALL" | grep -oP "[0-9]+\\.[0-9]+\\.[0-9]+")\n'
        'echo "[gateway_recovery] latest: v$TARGET_VER ($TARBALL)"\n'
        # ——— Step 4: 如实报状态（不再动手删；旧版本交 node / 官方包管理器处理）———
        # 原文记账：原破坏性动作 apt-get remove -y nodejs npm 2>/dev/null || true;
        #           rm -f /usr/bin/node /usr/bin/npm /usr/bin/npx /usr/local/bin/node /usr/local/bin/npm /usr/local/bin/npx 2>/dev/null || true
        'echo "[gateway_recovery] 原破坏性动作（已移除，不再执行）: apt-get remove -y nodejs npm; rm -f /usr/bin/node /usr/bin/npm /usr/bin/npx /usr/local/bin/node /usr/local/bin/npm /usr/local/bin/npx"\n'
        'echo "[gateway_recovery] 该修什么: 覆盖安装为 v$TARGET_VER（如需清旧版本，由 apt/dpkg 或 node 自身处理）"\n'
        'echo "[gateway_recovery] 现状 node: $(node --version 2>/dev/null || echo 未安装)"\n'
        'echo "[gateway_recovery] 现状 npm : $(npm --version 2>/dev/null || echo 未安装)"\n'
        'echo "[gateway_recovery] node 路径: $(command -v node || echo 无)"\n'
        'echo "[gateway_recovery] npm  路径: $(command -v npm || echo 无)"\n'
        # ——— Step 5: 从镜像下载 tarball（失败换 npmmirror fallback）———
        'TMPDIR=$(mktemp -d)\n'
        'URL="$NODE_MIRROR/v$TARGET_VER/$TARBALL"\n'
        'FALLBACK_URL="https://npmmirror.com/mirrors/node/v$TARGET_VER/$TARBALL"\n'
        'if ! curl -fsSL "$URL" -o "$TMPDIR/$TARBALL" 2>/dev/null; then\n'
        '  echo "[gateway_recovery] mirror failed, trying npmmirror..."\n'
        '  if ! curl -fsSL "$FALLBACK_URL" -o "$TMPDIR/$TARBALL" 2>/dev/null; then\n'
        '    echo "[gateway_recovery] FATAL: download failed - $URL"\n'
        '    rm -rf "$TMPDIR"\n'
        '    exit 2\n'
        '  fi\n'
        'fi\n'
        'echo "[gateway_recovery] download complete"\n'
        'tar -xJf "$TMPDIR/$TARBALL" -C "$TMPDIR"\n'
        'cp -rf "$TMPDIR/node-v${TARGET_VER}-linux-${NODE_ARCH}/"* /usr/local/ 2>/dev/null\n'
        'rm -rf "$TMPDIR"\n'
        'echo "[gateway_recovery] installed: $(node --version)"\n'
    )

def repair_node_version(log_callback=None) -> bool:
    """强制安装 Node.js 最新稳定版（国内镜像优先）。

    不做任何版本检测/跳过 —— 每次都从 /latest/ 目录抓最新版覆盖安装。
    理由: 云镜像自带 Node 可能恰好不满足 OpenClaw 引擎要求（如 v24.1.0
    落在 24.15.0 之下），检测门槛永远有盲区，强行拉最新一劳永逸。
    """

    def _log(msg: str) -> None:
        if log_callback:
            log_callback(msg)

    _ok, _prev, _why = _chain_prev("node")        # 安装链读链：先看上一步（wsl）
    if not _ok:
        _log(f"安装链：node 这一步缺上一步 {_prev} 的 ready（{_why}）⇒ 停")
        _stake(f"安装链：node 缺 {_prev} 的 ready（{_why}）⇒ 停")
        return False

    _log("强制安装 Node.js 最新稳定版（镜像优先）...")
    _log("正在下载并安装 Node.js...")

    script = _get_node_install_script()
    rc, out, err = _gw_run_wsl(
        f"wsl -d {WSL_DISTRO} -u root -- bash -c "
        f"\"{script}\"",
    )
    if rc == 0:
        _log("Node.js 安装完成")
        try:                                     # 产者自报：node 这一步 ready（信号账）
            signals().mark_install("node", note="node 安装脚本 rc=0")
        except Exception as _se:                                      # noqa: BLE001
            _log(f"信号：写 node 失败 {_se}")
        return True

    _log(f"Node.js 安装失败 (rc={rc})")
    if err:
        _log(f"  错误: {err[:200]}")
    return False

def check_openclaw_cli() -> bool:
    """检查 openclaw CLI 是否可用（二进制未损坏）。"""
    version_cmd = cli_usage("openclaw", "version")
    if not version_cmd:
        # 单一权威源：mani 查不到就跳过（不硬编兜底、不猜）
        _stake("check_openclaw_cli：mani 查不到 openclaw version 命令 ⇒ 跳过检查")
        return False
    rc, out, _ = _gw_run_wsl(
        f"wsl -d {WSL_DISTRO} -u root -- bash -c "
        f"\"export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; {version_cmd} 2>&1\"",
    )
    ok = rc == 0 and out.strip() != ""
    if ok:                                   # 产者自报：cli 跑得起来 ⇒ node 与 cli 两步 ready
        try:                                 # （openclaw 是 node CLI：它能应答就说明 node 在）
            signals().mark_install("node", note="openclaw --version 跑得起来（node 在）")
            signals().mark_install("cli", note=f"openclaw --version: {out.strip()[:60]}")
        except Exception as _se:                                      # noqa: BLE001
            _stake("信号：写 node/cli 失败 %s" % _se)
    return ok

def check_gateway_alive() -> bool:
    """探活 Gateway 18789 端口。"""
    rc, out, _ = _gw_run_wsl(
        f"wsl -d {WSL_DISTRO} -u root -- bash -c "
        f"\"curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{GATEWAY_PORT} 2>/dev/null\"",
    )
    return rc == 0 and out.strip() in ("200", "302", "401", "403")

def _gateway_signal() -> bool:
    """**Gateway 段信号**：按官方探针问一次，把结果记进信号账，返回记下来的 ready。

    判据来自官方（`openclaw gateway status` 的 rc + 输出含 `running`），壳不自己发明
    这是"上一步（网关）到位没"的**唯一判据**：谁要看网关，就读它，
    不用掐表猜它起没起来。
    """
    try:
        _st, _so, _se = _run_official_cmd("gateway", "status")
        _ok = (_st == 0 and "running" in str(_so or "").lower())
        signals().mark_gateway(_ok, pid=0,
                               port=int(os.environ.get("OPENCLAW_GATEWAY_PORT") or 18789),
                               probe="official: gateway status")
        _stake("信号：gateway.ready=%s（官方 status rc=%s）" % (_ok, _st))
        return _ok
    except Exception as _e:                                           # noqa: BLE001
        # 探不动（命令都跑不起来）也算"没 ready"，照样记一笔 —— 账本要有节拍，
        # 等账本的地方才不会被静默饿死。
        _stake("网关探针：探不动（%s）⇒ 记 ready=false" % _e)
        try:
            signals().mark_gateway(False, pid=0, port=0, probe="probe unavailable")
        except Exception:                                             # noqa: BLE001
            pass
        return False


def _gateway_unit_failed() -> bool:
    """网关这个服务**已经被判死**了没 —— 判决来自 systemd，不是壳拿秒数猜的。

    服务反复起不来，systemd 自己会把它置成 failed（起几次就放弃，系统默认），
    那时 `is-failed` 说 failed ⇒ 该走官方修复了。问不到（WSL 不通等）算"没判死"。
    """
    try:
        rc, out, _ = _gw_run_wsl(
            f"wsl -d {WSL_DISTRO} -u root -- bash -c "
            f"\"export XDG_RUNTIME_DIR=/run/user/0; "
            f"systemctl --user is-failed openclaw-gateway.service 2>&1\"",
        )
        return rc == 0 and "failed" in out.strip().lower()
    except Exception as _e:                                           # noqa: BLE001
        _stake("网关判决：问不到单元状态（%s）⇒ 当没判死" % _e)
        return False


def _wait_gateway_ready() -> str:
    """读网关 ready —— **探一次、读账本；不阻塞、不等、不掐表**。

    返回："ready"（到位）/ "broken"（官方判死，该修）/ "nowatch"（没到位，如实收手）。
    规矩：**判据归官方**（探一次官方 `gateway status`，把结果写进 Gateway 段账本）；
    读到什么就是什么 —— **壳不起网关、不阻塞等它起**（"起"与"保活"归官方 systemd
    + linger；界面由 browser 的重连脉冲收敛）。**没有 timeout、没有 await。**
    """
    if gateway_probe().probe_once():
        return "ready"
    return "broken" if _gateway_unit_failed() else "nowatch"


#: 2026-10-16：常驻节奏已删（探针改按需）—— 这里不再有任何"秒数"。


class GatewayProbe:
    """**网关探针（按需版）**：只盯一件事 —— 网关 ready 没 ready。

    ⚠ **常驻循环已删**。壳层保活（WslKeepalive）删除后，
      那条每 5 秒的 `wsl … openclaw gateway status` 已经没有常驻读者；而它每轮都会
      起一个 `wsl.exe` 子进程（宿主侧周期性负载 = 与"周期性闪"同量级的那个东西）。
      现在改成**按需探**：谁要「等网关就绪」（启动 / 装链），谁就自己 `probe_once()`。
      ⇒ 稳态下**零周期 wsl 调用**；掉线检测交给官方（systemd Restart=always + doctor）。
    """

    @staticmethod
    def probe_once() -> bool:
        """探一次，把结果记进 Gateway 段（唯一判据的产者）。"""
        return _gateway_signal()

    def ready(self) -> bool:
        """读探针记下的 ready —— **唯一判据**；读不到就是没 ready。"""
        try:
            return bool((signals().read().get("gateway") or {}).get("ready") is True)
        except Exception:                                             # noqa: BLE001
            return False


_GATEWAY_PROBE = None


def gateway_probe() -> "GatewayProbe":
    """网关探针（模块单例：全壳只盯一个网关）。"""
    global _GATEWAY_PROBE
    if _GATEWAY_PROBE is None:
        _GATEWAY_PROBE = GatewayProbe()
    return _GATEWAY_PROBE


def repair_openclaw_state(log_callback=None) -> bool:
    """OpenClaw 系统级异常的统一出口 —— 抛给官方 doctor --fix。

    边界（与壳层交付层分开）：
    - 壳层交付层：OpenClaw 本体没装好（CLI 不可用）→ 装 Node/包，壳层自己干。
    - OpenClaw 系统级：OpenClaw 装好了、但 Gateway 起不来（迁移锁/配置损坏/
      schema 过时/状态陈旧）→ 这里，统一抛 doctor --fix，壳层不自己诊断具体原因。

    doctor --fix 是官方「修复 + 迁移」工具（doctor.md:16），自动应用推荐修复、
    不提示。壳层不替 OpenClaw 决定「是哪一种坏」，只负责把「坏了」这件事抛给它。

    用完整 node 路径直接跑，不依赖软链接/PATH（软链接在交互 shell 下可能坏，
    但 systemd 用完整路径能跑）。
    """
    def _log(msg: str) -> None:
        if log_callback:
            log_callback(msg)

    # 注意：这里不自己 acquire 生命周期锁。repair 是「内部动作」，由顶层调用方
    # （keepalive._activate_gateway / browser._restart_gateway / browser._auto_repair_gateway）
    # 在持锁状态下调用，避免可重入死锁。顶层动作负责互斥，repair 只负责修。
    return _repair_openclaw_state_owned(_log)

def restart_gateway_safe(log_callback=None, *, manual: bool = False) -> bool:
    # ⚠⚠ **壳不自启网关** —— 为"系统级重启"让路；
    #   我们这边的网关重启**仅限**左侧 sidebar 的"手动重启 Gateway"那一个动作。
    #   其余任何调用（装插件/改配置/恢复备份/自愈/诊断面板）一律不下手，只记账。
    if not manual:
        try:
            stk("gw.restart", "非手动 ⇒ 不自启网关（为系统级重启让路）", ok=False)
        except Exception:                                             # noqa: BLE001
            pass
        return False
    """冷启动重启 Gateway —— 走官方 `gateway restart`（无 flag）。

    官方 gateway.md:132 明文：`gateway restart`（无 flag）保留 "existing
    service-manager restart behavior" = systemd stop+start = **全新进程冷启动**。

    为什么必须冷启动、不能用 `--safe`：
      `--safe` 发 SIGUSR1 做 in-process restart（gateway.md:46），进程内存里还留着
      旧插件清单快照，而装插件已改变磁盘 plugin inventory → 启动收敛期发现
      「迁移输入变了」→ 拒绝 ready → exit-code 崩（2026-09-04 journal 铁证：
      "OpenClaw plugin migration inputs changed during startup convergence; refusing
      to report the gateway ready"）。官方要的正是「冷启动让 state migrations 跑在
      最终 config + plugin inventory 上」。

    命令从 mani 长出（cli_usage），不手抄。正常重启（加载新配置）和装插件后重启
    都走这条——两者语义统一为「冷启动」，迁移都能收敛。
    """

    def _log(msg: str) -> None:
        if log_callback:
            log_callback(msg)

    _log("正在重启 Gateway...")
    rc, out, err = _run_official_cmd(
        "gateway", "restart", "")
    # 记账：重启是**改网关状态**的动作 —— 回调可能只喂 UI，成败都要落一笔。
    stk("gw.restart", f"官方 gateway restart rc={rc} {(out or err).strip()[:120]}", ok=(rc == 0))
    if rc == 0 and check_gateway_alive():
        return True
    # ── 兜底：CLI 走不通（查不到命令 / 返回失败 / 返回成功但网关没活）时，
    #    落到**同一个动作**的 systemd 形态。官方 gateway restart 内部做的就是
    #    `systemctl --user restart openclaw-gateway`（实测回显 "Restarted systemd
    #    service: openclaw-gateway.service"）。官方禁的是"stop 再 start"（恢复期），
    #    restart 语义一致 ⇒ 可作兜底，而不是把重启这件事押在 CLI 的心情上。
    _why = (f"rc={rc}（{(out or err).strip()[:140]}）" if rc != 0
            else "CLI 返回成功但网关没活")
    _log(f"openclaw gateway restart 没成（{_why}）→ 落到 systemd 同一动作")
    rc2, out2, err2 = _gw_run_wsl(
        f'wsl -d {WSL_DISTRO} -u root -- bash -c '
        f'"export XDG_RUNTIME_DIR=/run/user/0; '
        f'systemctl --user restart openclaw-gateway 2>&1; echo sysctl_rc=$?"',
    )
    _log(f"systemctl --user restart rc={rc2} {(out2 + err2).strip()[:160]}")
    stk("gw.restart.sysd",
        f"systemctl --user restart rc={rc2} {(out2 + err2).strip()[:120]}", ok=(rc2 == 0))
    return check_gateway_alive()

def _repair_openclaw_state_owned(_log) -> bool:
    # 爆破：门关着 → 停服务（压住 Restart=always 别抢）→ doctor --fix 炸开 → 再开门。
    # 全走官方命令（gateway stop --force / doctor --fix / gateway start），不手搓
    # systemctl/SIGKILL。doctor --fix 是官方修复+迁移工具，壳层只负责把「坏了」抛给它。
    #
    # ⚠ 2026-09-22（产品方报"频繁掉线"）：原来是**条件式**开门 —— `if ok: gateway start`，
    #   而 `doctor fix` 超时只给 30 秒（doctor 自己跑就是分钟级）⇒ 一旦超时，
    #   **网关就一直停着没人开**，只能等保活探活（30~50 秒后）把它拉回来。
    #   实测证据：`gateway-restart.log` 15:49:26 / 15:50:56 两次 `action=stop` 之后隔几十秒
    #   才被 systemd 拉起 —— 客户看到的就是"又掉线了"。
    #   规矩：**停下去就必须开回来**（无论 doctor 成不成），开门放 finally；
    #   另外网关**正在跑**时不做这套爆破（重启中的繁忙不算"坏了"）。
    _st = 1
    try:
        _st, _so, _se = _run_official_cmd("gateway", "status")
        # **Gateway 段信号**：判据来自**官方 status 的结果**（不自己发明判据），
        # 只覆写 Gateway 段；壳与后续步骤读这一栏，不用"掐表猜它起没起来"。
        try:
            _ok = (_st == 0 and "running" in str(_so or "").lower())
            signals().mark_gateway(_ok, pid=0,
                                   port=int(os.environ.get("OPENCLAW_GATEWAY_PORT") or 18789),
                                   probe="official: gateway status")
            _stake("信号：gateway.ready=%s（官方 status rc=%s）" % (_ok, _st))
        except Exception as _e:                                          # noqa: BLE001
            _stake("信号：写 gateway 段失败（%s）" % _e)
    except Exception:                                             # noqa: BLE001
        _st = 1
    if _st == 0:
        _log("gateway 在跑（不是在坏掉的状态）⇒ 跳过爆破，不做停-修-开")
        stk("gw.repair", "gateway 在跑 ⇒ 不做停-修-开（不误伤在跑的网关）", ok=True)
        return True

    # 每次**改网关状态**的动作都落账（2026-10-17 审计）：只走 log_callback 不够 ——
    # 那回调可能只喂 UI，事后查不到"谁停的、停成没成"。停/修/开各记一笔。
    _log("gateway stop --force")
    _rc_stop, _out_stop, _err_stop = _run_official_cmd("gateway", "stop", "--force")
    stk("gw.repair.stop",
        f"gateway stop --force rc={_rc_stop} {(_out_stop + _err_stop).strip()[:120]}",
        ok=(_rc_stop == 0))

    _log("doctor --fix")
    ok = False
    try:
        rc, out, err = _run_official_cmd("doctor", "fix")
        ok = rc == 0
        _log(f"doctor --fix rc={rc} ok={ok} {(err or out or '')[:120]}")
        stk("gw.repair.doctor", f"doctor --fix rc={rc} {(err or out or '')[:120]}", ok=ok)
        if "offline maintenance" in ((out or "") + (err or "")).lower():
            _log("doctor 仍报 offline maintenance ⇒ 再跑一次 doctor --fix")
            rc2, out2, err2 = _run_official_cmd("doctor", "fix")
            ok = rc2 == 0
            stk("gw.repair.doctor2", f"doctor --fix(2) rc={rc2} {(err2 or out2 or '')[:120]}", ok=ok)
    except Exception as _de:                                      # noqa: BLE001
        _log(f"doctor --fix 异常：{type(_de).__name__}: {_de}")
        stk("gw.repair.doctor", f"doctor --fix 异常：{type(_de).__name__}: {_de}", ok=False)
    finally:
        # 无论如何都要把门开回来 —— 停着不开就是客户掉线。
        _log("gateway start")
        try:
            _rc_start, _out_start, _err_start = _run_official_cmd("gateway", "start")
            stk("gw.repair.start",
                f"gateway start rc={_rc_start} {(_out_start + _err_start).strip()[:120]}",
                ok=(_rc_start == 0))
        except Exception as _se2:                                 # noqa: BLE001
            _log(f"gateway start 异常：{type(_se2).__name__}: {_se2}")
            stk("gw.repair.start", f"gateway start 异常：{type(_se2).__name__}: {_se2}", ok=False)
    return ok

# 向后兼容别名（旧调用点，如 install.py 曾用 clear_migration_lock）
clear_migration_lock = repair_openclaw_state

# ── 修复 openclaw 包 ──

def repair_openclaw_package(log_callback=None) -> bool:
    """Reinstall openclaw via npm — single pipeline.

    Clean cache -> uninstall old -> install @latest from mirror.
    No fallback to direct registry.
    """

    def _log(msg: str) -> None:
        if log_callback:
            log_callback(msg)

    _ok, _prev, _why = _chain_prev("cli")         # 安装链读链：先看上一步（node）
    if not _ok:
        _log(f"安装链：cli 这一步缺上一步 {_prev} 的 ready（{_why}）⇒ 停")
        _stake(f"安装链：cli 缺 {_prev} 的 ready（{_why}）⇒ 停")
        return False

    _log("Repairing openclaw package (npm install, mirror only)...")
    _log("  Cleaning old package + cache...")

    _PATH_PREFIX = "export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; "

    _gw_run_wsl(
        f"wsl -d {WSL_DISTRO} -u root -- bash -c "
        f"\"{_PATH_PREFIX} npm cache clean --force 2>&1 || true\"",
    )
    _gw_run_wsl(
        f"wsl -d {WSL_DISTRO} -u root -- bash -c "
        f"\"{_PATH_PREFIX} npm uninstall -g openclaw 2>&1 || true\"",
    )

    rc, out, err = _gw_run_wsl(
        f"wsl -d {WSL_DISTRO} -u root -- bash -c "
        f"\"{_PATH_PREFIX} npm install -g openclaw@latest --registry={_NPM_REGISTRY} 2>&1\"",
    )
    if rc == 0:
        _log("openclaw package repaired (mirror)")
        try:                                 # 产者自报：cli 这一步 ready（信号账）
            signals().mark_install("cli", note="npm install -g openclaw@latest ok")
        except Exception as _se:                                      # noqa: BLE001
            _stake("信号：写 cli 失败 %s" % _se)
        return True

    _log(f"openclaw package repair failed (rc={rc})")
    if err:
        _log(f"  Error: {err[:200]}")
    return False

def start_gateway(log_callback=None, *, manual: bool = False) -> bool:
    # ⚠ 起网关也是"系统级"动作 ⇒ 非手动一律让路（同 restart）。
    if not manual:
        try:
            stk("gw.start", "非手动 ⇒ 不自启网关（为系统级重启让路）", ok=False)
        except Exception:                                             # noqa: BLE001
            pass
        return False
    """启动 Gateway —— 走官方 systemd 托管（幂等、单一实例）。

    脉冲模型（不是等待模型）：systemctl start 是「开门的动作」，
    探活是「看一眼门开没开」。start 之后立刻探一次：
      - 门开 → 立刻返回 True，数据跟上。
      - 门关 → 立刻返回 False，交给调用方爆破（fix），不在这里干等。

    没有「等 45 秒看门会不会自己开」——门开就进，门关就走，绝不停留。
    """

    def _log(msg: str) -> None:
        if log_callback:
            log_callback(msg)

    # 开门动作：官方 gateway start（幂等，gateway.md:531 明文：服务已跑则复用、未跑则拉起）
    _log("gateway start")
    _rc, _out, _err = _run_official_cmd("gateway", "start")
    stk("gw.start", f"gateway start rc={_rc} {(_out + _err).strip()[:120]}", ok=(_rc == 0))

    # 引擎登记统一走壳接口层（persona/indivi.py::injectPersona），这里不重复。

    # 开门后立刻探一眼：门开就过，门关就走
    return check_gateway_alive()

# ── 全链路恢复 ──

def recover_gateway(log_callback=None, *, manual: bool = False) -> tuple[bool, str]:
    # ⚠ 全链路恢复会停-修-开 ⇒ 非手动一律让路（同 restart）。
    if not manual:
        try:
            stk("gw.recover", "非手动 ⇒ 不做全链路恢复（为系统级重启让路）", ok=False)
        except Exception:                                             # noqa: BLE001
            pass
        return False, "为系统级重启让路（非手动）"
    """一键恢复 Gateway: 诊断 → 修复 → 启动 → 探活。

    修复原则（逻辑约束，不硬编）：
    - 只在 openclaw CLI 本身不可用（Node 环境真坏）时才重装 Node/包。
    - 若 CLI 可用，说明只是 Gateway 启动慢，直接启动即可，绝不重装——
      否则慢机器上「Gateway 冷启动慢」会被误判成「环境坏」，白白重装
      Node.js + npm 拖 3 分多钟（2026-08-22 小机器实测）。
      所有权：本函数会改 Gateway 状态 ⇒ 先 acquire_lifecycle，退出时 release
      （与 main.gateway_recovery 的所有权约定一致）。
    """
    def _log(msg: str) -> None:
        if log_callback:
            log_callback(msg)

    _log("═ Gateway 全链路恢复 ═")

    # Step 1: 是否已经活着？
    if check_gateway_alive():
        _log("Gateway 已在线，无需恢复")
        stk("gw.recover", "Gateway 已在线 ⇒ 不碰（在跑一律不碰）", ok=True)
        return True, "Gateway 已在线"

    # ⚠ 2026-10-17 审计修：从这里往下都是**会改 Gateway 状态**的动作
    #   （repair_node_version / repair_openclaw_package / start_gateway / doctor --fix），
    #   按 main.gateway_recovery 的所有权约定必须先取生命周期锁 —— 旧写法裸调
    #   repair_openclaw_state（停-修-开）不持锁，可与别的 owner 并发撞 OpenClaw
    #   单实例迁移锁（网关卡在 inactive 起不来那一类事故）。
    _owner = "gateway_recovery.recover_gateway"
    if not acquire_lifecycle(_owner):
        _log("Gateway 生命周期已被别的 owner 接管 ⇒ 本轮不插手（它完成后自会收敛）")
        stk("gw.recover", "生命周期已被别的 owner 接管 ⇒ 本轮不插手", ok=False)
        return False, "Gateway 生命周期由其它入口接管中"
    stk("gw.recover", "取得生命周期所有权 ⇒ 开始全链路恢复", ok=True)
    try:
        # Step 2: openclaw CLI 是否可用？（决定要不要走「重装」路径）
        cli_ok = check_openclaw_cli()
        _log(f"openclaw CLI: {'可用' if cli_ok else '损坏 — 需要修复'}")

        # 门开 → 过；门关 → 看 CLI，坏了修包，没坏直接开门
        if not cli_ok:
            repair_node_version(_log)
            if not repair_openclaw_package(_log):
                return False, "openclaw 包修复失败，需手动重装"
            if not check_openclaw_cli():
                _log("修复后 openclaw CLI 仍不可用")
                return False, "openclaw CLI 修复后仍不可用"
            _log("Node.js/openclaw 已修复")

        # 开门（脉冲）：开门后立刻探一眼
        if start_gateway(_log):
            return True, "Gateway 已恢复"

        # 门关 → 直接爆破 fix（简单粗暴，不判断死活）
        _log("doctor --fix")
        repair_openclaw_state(_log)

        # 爆破后立刻再探：门开就过，门关就报失败
        if check_gateway_alive():
            return True, "Gateway 已恢复"

        return False, "Gateway 仍未就绪，请手动 openclaw gateway start"
    finally:
        # 无论成功失败/中途 return，都要把生命周期锁放回去。
        release_lifecycle(_owner)

# ══════════════════════════════════════════════════════════════
# ═══ main.keepalive — 方法化并入 main.py ═══
# ══════════════════════════════════════════════════════════════
# WSL 保活 — **本节实现已整段删除**。
#
# 原来这里是「探活→激活→保活」闭环守护：后台线程每 15s 一轮探活 Gateway，
# 探到死就激活，再挂一个 `wsl sleep infinity` 常驻会话防 WSL 被空闲回收。
#
# ⚠ 这节现在**没有实现**，也不再留旧设计的流程描述 —— 旧描述（15s 守护线程 / sleep
#   infinity / vmIdleTimeout ~5s /「调用侧只需 keepalive().start()」）与 2026-10-15 的
#   删除决定互相矛盾，只会误导后来人。
#
# （本节原还重复了一遍 logging/subprocess/sys/threading/time 的 import、logger、
#   WSL_DISTRO/GATEWAY_PORT/_CREATE_NO_WINDOW —— 单文件合并的残留，已一并删掉：这些名字
#   在文件头 1-20 行、main.wsl_config 与 main.bonus_skills 各节都已就位。）

# ── **壳层保活（WslKeepalive）已整段删除** ─────────────
#   依据：OpenClaw 官方把 gateway 装成 WSL2 里的 systemd 用户服务（= 常驻进程），
#   「有进程在跑」本身就挡 WSL 的空闲回收 ⇒ 壳层这套（wsl sleep infinity + 周期 wsl
#   调用）是重复建设，也是唯一被观察到的周期性宿主侧负载。
#   **实测（2026-10-15 18:28~21:23，3 小时 55 分）**：保活进程杀掉后 —— WSL 未被回收
#   （boot 连续、uptime 连续）、网关未掉线（systemd 服务一直 active）⇒ 确认可删。
#   原实现见 git 历史（commit 03ec4f6 的注释版、更早为未注释版）。


# ══════════════════════════════════════════════════════════════
# ═══ main.pip_venv — 方法化并入 main.py ═══
# ══════════════════════════════════════════════════════════════
# 后台安装 WSL pip + venv 运行环境。
#
# 背景：Ubuntu WSL 镜像自带 Python 3 但没有 pip/venv（PEP 668）。
# skills（如 office-toolkit）依赖 Python 运行时，需要在 WSL 里预先
# 打好 pip/venv 基础 + office 常用依赖。
#
# 设计要点:
#   - daemon 线程，不阻塞 UI
#   - 幂等标记文件 pip_venv_done 防止重复执行
#   - 安装 python3-pip python3-venv + 核心 office 依赖
#   - apt **不换源**（脚本里就是 apt-get update/install；也不再带 Acquire::*::Timeout 时间闸）
#   - pip 走 aliyun 镜像（失败回落 pip 默认源）
#   - 失败不阻断，所有异常被吞
#
# 调用方: runtime/bonus_skills.py _worker()
#

import os
import subprocess
import threading

_PROGRAMDATA = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia")
# ⚠⚠ 2026-10-17 逐行审计发现的**重绑 bug**：这个文件里有两个同名全局 `_DONE_MARKER`
#   —— 本处（pip/venv 的幂等门，`pip_venv_done`）与下面 bonus_skills 段那份
#   （`bonus_skills_done`，L2405）**互相覆盖**：谁后定义谁生效 ⇒ 本段读到的其实是
#   bonus 的标记、写的也是它 ⇒ pip/venv 静默不装、或 8 项预装被误判"已完成"整轮跳过。
#   ⇒ 各归各名：pip/venv 用 `_PIP_DONE_MARKER`，bonus 那份仍是 `_DONE_MARKER`。
_PIP_DONE_MARKER = os.path.join(_PROGRAMDATA, "pip_venv_done")
WSL_DISTRO = os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu"

def _pv_run_wsl(cmd: str) -> tuple[int, str, str]:
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    r = subprocess.run(
        cmd, shell=True, capture_output=True,
        encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
        startupinfo=si,
    )
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()

def _pv_is_wsl_responding() -> bool:
    rc, out, _ = _pv_run_wsl(
        f"wsl -d {WSL_DISTRO} -u root -- bash -c \"echo ok\"")
    if rc == 0 and "ok" in out:              # 产者自报：wsl 这一步 ready（信号账）
        try:
            signals().mark_install("wsl", note="wsl -u root echo ok")
        except Exception as _se:                                      # noqa: BLE001
            _stake("信号：写 wsl 失败 %s" % _se)
        return True
    return False

def _ensure_pip_venv() -> bool:
    """确保 WSL 内 python3 + pip + venv 可用。

    返回 True 表示环境已就绪（或之前已完成），False 表示不可恢复失败。
    """

    stk("pip.venv", "checking pip + venv...")

    bash = r"""#!/bin/bash
export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin
export DEBIAN_FRONTEND=noninteractive

# ── 1. 检测当前状态 ──
has_pip=0
has_venv=0
if python3 -m pip --version >/dev/null 2>&1; then has_pip=1; fi
if python3 -m venv -h >/dev/null 2>&1; then has_venv=1; fi

if [ "$has_pip" = "1" ] && [ "$has_venv" = "1" ]; then
    echo "OK pip+venv already ready"
    exit 0
fi

# ── 2. apt update + install（失败不整体退出，打印真实报错）──
echo "[pip-venv] apt update..."
apt-get update -y 2>&1 || echo "[pip-venv] apt update failed (continuing)"
echo "[pip-venv] apt install python3-pip python3-venv..."
apt-get install -y python3-pip python3-venv 2>&1 || echo "[pip-venv] apt install failed (continuing)"

# ── 3. 验证 ──
echo "python3: $(python3 --version 2>&1)"
echo "pip: $(python3 -m pip --version 2>&1)"
if python3 -m pip --version >/dev/null 2>&1; then
    echo "OK pip+venv installed"
else
    echo "FAIL pip still missing"
fi
"""

    rc, out, _ = _pv_run_wsl(
        f"wsl -d {WSL_DISTRO} -u root -- bash -c '{bash}'",
    )
    if rc == 0 and "OK" in out:
        stk("pip.venv", "pip + venv ready")
        try:                                 # 产者自报：wsl 这一步 ready（信号账）
            signals().mark_install("wsl", note="apt python3-pip python3-venv ok")
        except Exception as _se:                                      # noqa: BLE001
            stk("pip.venv", f"信号：写 wsl 失败 {_se}", ok=False)
        return True

    stk("pip.venv", f"FAIL rc={rc}: {out[:300]}", ok=False)
    return False

# ── 核心 office 依赖（skills 常用） ──
_OFFICE_PACKAGES = [
    "python-docx",    # Word 读写
    "python-pptx",    # PPT 读写
    "openpyxl",       # Excel 读写
    "pymupdf",        # PDF 读写（fitz）
    "reportlab",      # PDF 生成
]

def _install_office_deps() -> list[str]:
    """安装 office 核心依赖。返回失败的包名列表。

    全部走 pip install --break-system-packages（WSL 受限环境）。
    国内系统走 npmmirror 镜像。
    """

    # ⚠ 旧注释写「直接用 pip 默认源」，与实现相反，一并改对：先走 aliyun 镜像
    #   （对所有用户都写死这一条，属可接受的简化），失败那条命令回落 pip 默认源。
    index_flag = "-i https://mirrors.aliyun.com/pypi/simple/"

    failed: list[str] = []
    for pkg in _OFFICE_PACKAGES:
        stk("pip.venv", f"pip install {pkg}...")
        rc, out, err = _pv_run_wsl(
            f"wsl -d {WSL_DISTRO} -u root -- bash -c "
            f"\"export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; "
            f"pip install --break-system-packages {pkg} {index_flag} 2>&1 "
            f"|| pip install --break-system-packages {pkg} 2>&1\"",
        )
        if rc == 0:
            stk("pip.venv", f"  {pkg} OK")
        else:
            stk("pip.venv", f"  {pkg} FAIL: {(err or out)[:200]}", ok=False)
            failed.append(pkg)

    return failed

# ── 主入口 ──

def ensure_pip_venv_background() -> None:
    """后台线程：装 pip + venv + office 核心依赖。幂等，失败跳过。

    在 bonus_skills Phase 0 调用 — must 在所有 skill 安装之前完成。
    非阻塞：Phase 0 只下发后台线程，后续 Phase 1-4 继续执行。
    """
    if os.path.isfile(_PIP_DONE_MARKER):
        return
    if not _pv_is_wsl_responding():
        stk("pip.venv", "WSL not responding, defer", ok=False)
        return

    stk("pip.venv", "starting: pip + venv + office deps")

    def _worker() -> None:
        ok = _ensure_pip_venv()
        if not ok:
            stk("pip.venv", "pip+venv failed — office deps skipped", ok=False)
            # 不写标记 — 下次启动可重试
            return

        failed = _install_office_deps()
        if failed:
            stk("pip.venv", f"office deps done — {len(failed)} failed: {failed}", ok=False)
        else:
            stk("pip.venv", "office deps done — all installed")

        # 写标记（**pip/venv 自己的名** —— 不许和 8 项预装的 `_DONE_MARKER` 撞）
        try:
            os.makedirs(_PROGRAMDATA, exist_ok=True)
            with open(_PIP_DONE_MARKER, "w") as f:
                f.write("1")
        except OSError as _me:
            # 写完成标记失败别静默（旧写法裸 pass）：写不上只意味着下次重跑一遍
            stk("pip.venv", f"写幂等标记失败（下次重跑一遍）：{_me}", ok=False)

    t = threading.Thread(target=_worker, daemon=True, name="pip-venv")
    t.start()

# ══════════════════════════════════════════════════════════════
# ═══ main.bonus_skills — 方法化并入 main.py ═══
# ══════════════════════════════════════════════════════════════
# 后台静默安装预装 bonus skills + Tavily / mem9 插件。
#
# 设计要点:
#   - daemon 线程，不阻塞 UI
#   - Tavily / mem9 走 plugins install 安装
#   - 5 个 bonus skill 走 clawhub install @owner/slug（不使用 openclaw skills install，
#     因为 CLI 已知 bug 拒绝 @owner/slug 格式，裸 slug 可能命中错误 owner）
#   - 幂等标记文件 bonus_skills_done 防止重复执行
#   - failed_skills.json 记录失败 slug，供侧边栏展示
#   - 跨线程通知侧边栏刷新 via Qt signal
#
# 调用方: ui/web/browser.py _start_bonus_skills() — Gateway 连通立即触发
#

import json
import os
import subprocess
import threading
import time

from PySide6.QtCore import QObject, Signal

# ── 预装清单：只存名字（逻辑代码生代码，不硬编 skill/plugin 分类） ──
# 每个名字的分流（skill 还是 plugin）**问 CLI**（_cli_kind → openclaw skills/plugins
# search --json）判定，安装命令由 openclaw CLI 自己长出来。哪天 OpenClaw 把 skill
# 升级成 plugin、或 plugin 降级成 skill，名单不用改，问一次 CLI 重新判定即可。
# 2026-09-25 产品方令：此前这条走的是壳自建的 spider.resolve_install_kind
# （壳自己爬 npm registry + clawhub API），判据归 CLI 之后壳不再自建那一份。
_PREINSTALL_NAMES: tuple[str, ...] = (
    "@openclaw/tavily-plugin",
    "@mem9/mem9",
    "@spclaudehome/skill-vetter",
    "@guipi888/find-skills",
    "@cindypapa/agent-browser",
    "@pskoett/self-improving-agent",
    "@paudyyin/summarize",
    "@weiwei2027/office-toolkit",
)
# 预装配套依赖表（skill 本体之外的运行配套，直装不解释——缺配套 = 给客户留缺口）：
#   apt:     系统包（LibreOffice 全家，soffice 转换引擎）
#   npm:     npm 全局 CLI（agent-browser）
#   download: 自带下载器（agent-browser install → Chrome 内核）
_PREINSTALL_DEPS: dict[str, dict[str, list[str]]] = {
    "@weiwei2027/office-toolkit": {"apt": ["libreoffice"]},
    "@cindypapa/agent-browser": {
        "npm": ["agent-browser"],
        "download": ["agent-browser install"],
    },
}

def _apt_install(pkg: str) -> bool:
    """apt 直装系统包。"""
    rc, out, err = _wsl_run_argv(
        f"apt-get install -y {pkg} 2>&1",
    )
    return rc == 0

def _npm_global_install(pkg: str) -> bool:
    rc, out, err = _wsl_run_argv(f"npm install -g {pkg} 2>&1")
    return rc == 0

def _run_downloader(cmd: str) -> bool:
    rc, out, err = _wsl_run_argv(f"{cmd} 2>&1")
    return rc == 0

def _verify_prereq(name: str) -> bool:
    """配套 verify：soffice / agent-browser CLI / Chrome 内核存在。"""
    deps = _PREINSTALL_DEPS.get(name) or {}
    if "apt" in deps:
        rc, out, _ = _wsl_run_argv("which soffice && soffice --version 2>&1")
        if rc != 0:
            return False
    if "npm" in deps or "download" in deps:
        rc, out, _ = _wsl_run_argv("which agent-browser 2>&1")
        if rc != 0:
            return False
        rc2, _, _ = _wsl_run_argv(
            "ls -d $HOME/.agent-browser/browsers/chrome-*/chrome 2>/dev/null | head -1")
        if rc2 != 0:
            return False
    return True

def _install_all_prereqs() -> bool:
    """给全部需要配套的预装项直装配套。全成 True，任一失败 False（下次重试）。"""
    ok = True
    for name, deps in _PREINSTALL_DEPS.items():
        stk("bonus.skills", f"{name}: 装配套 {deps}")
        for pkg in deps.get("apt", []):
            if not _apt_install(pkg):
                stk("bonus.skills", f"{name}: apt 装 {pkg} 失败", ok=False)
                ok = False
        for pkg in deps.get("npm", []):
            if not _npm_global_install(pkg):
                stk("bonus.skills", f"{name}: npm 装 {pkg} 失败", ok=False)
                ok = False
        for cmd in deps.get("download", []):
            if not _run_downloader(cmd):
                stk("bonus.skills", f"{name}: {cmd} 失败", ok=False)
                ok = False
        if _verify_prereq(name):
            stk("bonus.skills", f"{name}: 配套齐（soffice/agent-browser/内核验证通过）")
        else:
            stk("bonus.skills", f"{name}: 配套验证未过", ok=False)
            ok = False
    return ok



_PROGRAMDATA = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia")
_DONE_MARKER = os.path.join(_PROGRAMDATA, "bonus_skills_done")
_FAILED_LIST = os.path.join(_PROGRAMDATA, "failed_skills.json")
# 失败状态：{name: {last_fail_at, fail_count}}，退避窗口 + 只重试失败名单用。
# 与 failed_skills.json（名字 list，侧边栏消费）分开存，不破坏侧边栏格式。
_FAILED_STATE = os.path.join(_PROGRAMDATA, "failed_state.json")
_TAVILY_KEY_FILE = os.path.join(_PROGRAMDATA, "tavily_api_key.txt")
WSL_DISTRO = os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu"

# 每个名字: 最多 _MAX_ATTEMPTS 次重试（失败记 failed_skills.json 后放弃）。
# 不再无限重试——plugins install 每次都会触发 gateway restart（provider_setup.py
# 注释铁证），死循环会反复重启 Gateway，叠加「旧进程优雅停机卡死」的竞态
# → gateway 起不来（2026-09-04 日志铁证）。
# 单条 CLI 命令**不设时限**（时限是禁忌）：它返回即往下走。
# （`_RETRY_DELAY = 5` 已删：并入单文件后全文件零 `time.sleep(_RETRY_DELAY)` 调用，是死常量。）
_MAX_ATTEMPTS = 5

# 跨会话退避窗口：距上次失败 < 24h 的名字本轮跳过（等人/显式重试），
# 避免源长期故障时每次开机全量重跑风暴（评审 P0-1）。
_RETRY_BACKOFF_SECONDS = 24 * 3600

# kind 判定缓存：**问 CLI** 得到的 skill/plugin 分流结果落盘，命中免再问（评审 P1-3）。
# {name: {kind, cached_at}}，缓存 24h 内有效，超时重新问。判空不落缓存。
# （2026-09-25：判据已从壳自建的 resolve_install_kind 改为 CLI search，见 _cli_kind。）
_KIND_CACHE = os.path.join(_PROGRAMDATA, "install_kind_cache.json")
_KIND_CACHE_TTL = 24 * 3600

# ── 跨线程信号 ──

class _BonusSkillsSignals(QObject):
    """安装线程完成后通知 UI 线程刷新侧边栏。"""
    finished = Signal()
    failed_refresh = Signal()

_signals = _BonusSkillsSignals()

def on_bonus_skills_finished(callback) -> None:
    """注册回调：全部 bonus skills 安装尝试完毕后触发（主线程安全）。"""
    _signals.finished.connect(callback)

def refresh_failed_skills(callback) -> None:
    """注册回调：需要刷新失败 skills 侧边栏列表。"""
    _signals.failed_refresh.connect(callback)

# ── WSL helpers ──

def _bind_child_to_parent(pid: int) -> bool:
    """把子进程绑进「父进程一死、子进程跟着死」的作业对象（Windows 标准做法）。

    ⚠ 为什么要有它（2026-09-20 产品方报「卸载 Rheia 侧进程结束不了，我这边看不见」）：
      壳的 WSL 保活是一个子进程（`wsl -d Ubuntu -- sleep infinity`）。壳被**硬杀**
      （卸载器直接删目录/杀进程，`app.aboutToQuit` 那套收尾根本轮不上）时，
      Windows **不会**连带杀子进程 ⇒ 保活变成孤儿，任务管理器里一堆 wsl.exe 挂着。
      产品方明令：「**仅卸载 Rheia** 这条路从来不碰 WSL，不能动」⇒ 卸载器那边不许用
      `wsl --terminate` 去收尸 ⇒ 只能让子进程**跟壳同生共死**：
      作业对象上开 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`，句柄随壳进程存活，
      壳一死句柄被系统关掉 ⇒ 子进程立刻被带走。

    做不到就返回 False（如实报，不假装做到）：调用方只记一笔告警，保活照旧。
    """
    try:
        import ctypes
        from ctypes import wintypes

        class _BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                        ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class _IO(ctypes.Structure):
            _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
                        ("WriteOperationCount", ctypes.c_ulonglong),
                        ("OtherOperationCount", ctypes.c_ulonglong),
                        ("ReadTransferCount", ctypes.c_ulonglong),
                        ("WriteTransferCount", ctypes.c_ulonglong),
                        ("OtherTransferCount", ctypes.c_ulonglong)]

        class _EXT(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", _BASIC),
                        ("IoInfo", _IO),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        _KILL_ON_CLOSE = 0x2000           # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        _EXT_INFO = 9                     # JobObjectExtendedLimitInformation
        _SET_QUOTA_TERMINATE = 0x0100 | 0x0200   # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return False
        info = _EXT()
        info.BasicLimitInformation.LimitFlags = _KILL_ON_CLOSE
        if not k32.SetInformationJobObject(job, _EXT_INFO, ctypes.byref(info),
                                           ctypes.sizeof(info)):
            k32.CloseHandle(job)
            return False
        h = k32.OpenProcess(_SET_QUOTA_TERMINATE, False, int(pid))
        if not h:
            k32.CloseHandle(job)
            return False
        ok = bool(k32.AssignProcessToJobObject(job, h))
        k32.CloseHandle(h)
        if ok:
            # ⚠ 句柄**留着不放**：一关就等于立刻杀子进程。随壳进程存活 ⇒ 壳死则子死。
            _JOB_HANDLES.append(job)
        else:
            k32.CloseHandle(job)
        return ok
    except Exception:
        logger.warning("作业对象绑定异常（保活硬杀时可能留孤儿）", exc_info=True)
        return False


#: 作业对象句柄（故意常驻：句柄一关，绑进去的子进程就被系统带走）
_JOB_HANDLES: list = []


#: （2026-09-20 撤）壳曾经在这里公开自己的运行环境给卡侧用 —— 产品方令：
#: 「不要在 Rheia 侧暴露设计意图」⇒ 解释器/依赖/路径翻译全归卡侧入口自己管，
#: 壳只认"卡侧 tools/ 下那个认 --print-policy 的入口"（见 persona/indivi.card_side_cli）。


def _wsl_run_argv(script: str) -> tuple[int, str, str]:
    """list 参数直跑 WSL bash -c（不经 cmd.exe shell，引号零损耗）。

    解决：Windows pythonw + subprocess shell=True 时，cmd.exe 会二次解析多层
    引号（bash -c "flock ... -c "openclaw ...""）→ 命令残缺 → 新版 openclaw
    掉进 TUI（"TUI needs an interactive TTY"）。list 参数绕开 cmd.exe 层。

    无时限：命令返回即往下走。
    """
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    try:
        r = subprocess.run(
            ["wsl", "-d", WSL_DISTRO, "-u", "root", "--", "bash", "-c", script],
            capture_output=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=si,
        )
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except Exception:
        return -1, "", ""


def _bs_is_wsl_responding() -> bool:
    # ⚠ 2026-10-17 审计修：本文件第四个 WSL runner（`_bs_run_wsl`，shell=True 单串、引号
    #   脆弱）已删，统一走 `_wsl_run_argv`（list 参数、不经 cmd.exe 二次解析 = 本文件
    #   自己认的"正确姿势"）。
    rc, out, _ = _wsl_run_argv("echo ok")
    if rc == 0 and "ok" in out:              # 产者自报：wsl 这一步 ready（信号账）
        try:
            signals().mark_install("wsl", note="WSL 应答（安装链入口探活）")
        except Exception as _se:                                      # noqa: BLE001
            _stake("信号：写 wsl 失败 %s" % _se)
        return True
    return False

# ── 预装：判据归 CLI，壳只落 log ──

# （2026-09-25 删）_http_get —— 它只服务壳自建的 kind 判定链路
# （kernel.filter.resolve_install_kind 的 http_get 注入），那条链路已掐掉，改问 CLI。
# 单一权威源，不另写一份：判据归 CLI 之后这里不留死码。

# ── CLI 原始输出落盘 ──
# 2026-09-25 产品方令：**判据全归 CLI**（装没装、是 skill 还是 plugin、缺什么 flag
# 全由 CLI 说了算），壳唯一要做的就是**让 CLI 把 log 写下来**。
#
# 为什么这么改（两条实测铁证，都是壳自己判判出来的）：
#   · skill-vetter 少装一个：壳自建的 npm+clawhub 判定链路一次抖动判空，
#     而 _install_one 那条 return 一个字都不落日志，翻遍 stake.log 查不出原因。
#   · --agent 缺旗：CLI 已经明说 "Pass --agent <id>"，壳的补旗机制没长这个，
#     多 agent 一配，8 项预装全挂。
# 壳自己判 = 壳自己出 bug 且无痕。现在 CLI 的原文一字不改落盘 → 出问题看这个文件。
# 默认 agent 的进程内缓存（问 CLI 一次就够 —— skills 子命令在多 agent 下必须指名 owner）
_AGENT_CACHE = ""
_AGENT_CACHE_DONE = False

_CLI_LOG = os.path.join(_PROGRAMDATA, "cli.log")   # _PROGRAMDATA 本身已是 …\ProgramData\Rheia


def _cli_log(tag: str, text: str) -> None:
    """把 CLI 的原始输出追加落盘。壳不加工、不截断、不改一字。"""
    try:
        os.makedirs(os.path.dirname(_CLI_LOG), exist_ok=True)
        with open(_CLI_LOG, "a", encoding="utf-8") as f:
            f.write("\n" + "=" * 78 + "\n")
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {tag}\n")
            f.write("=" * 78 + "\n")
            f.write(((text or "").rstrip()) + "\n")
    except OSError:
        pass


def _cli_search(name: str, sub: str) -> str:
    """问 CLI：这个名字在 skills / plugins 目录里有没有。返回 CLI 原始 JSON（''=没问到）。

    壳不认 npm、不认 clawhub API —— CLI 才是那两份目录的权威。
    """
    slug = name.split("/")[-1]
    cmd = f"openclaw {sub} search '{slug}' --json"
    try:
        rc, out, err = _wsl_run_argv(cmd)
    except Exception as e:
        _cli_log(f"kind: {cmd}", f"执行异常: {e}")
        return ""
    _cli_log(f"kind: {cmd}  (rc={rc})",
             (out or "") + (("\n[stderr]\n" + err) if err else ""))
    return out if rc == 0 else ""


def _norm_slug(v) -> str:
    """把名字归一成 slug 再比。

    实测坑：skill 落点是 `agent-browser`（连字符），但 SKILL.md 里写的是
    `name: Agent Browser`（空格）—— CLI 名单里回的就是带空格那个，
    按连字符硬比 ⇒ 明明装了却判成未装。
    """
    return str(v or "").strip().lower().replace(" ", "-").replace("_", "-")


def _agent_flag() -> str:
    """CLI 的 skills list / skills install 在多 agent 下必须指名 owner。

    CLI 原话：「Multiple agents are configured, but the skills command has no
    explicit owner. Pass --agent <id>.」—— 实测 @agent 只对 list/install 有效，
    skills search 不认这个选项，所以只在这两处加。
    """
    global _AGENT_CACHE, _AGENT_CACHE_DONE
    if not _AGENT_CACHE_DONE:
        _AGENT_CACHE = _default_agent()
        _AGENT_CACHE_DONE = True
    return f"--agent {_AGENT_CACHE}" if _AGENT_CACHE else ""


def _cli_kind(name: str) -> str:
    """判定 skill / plugin —— 拿 CLI 的 search 结果定，壳不自己爬。

    2026-09-25 产品方令：这些交给 CLI。此前壳自建一条 npm registry + clawhub API
    的链路（kernel.filter.resolve_install_kind），一次网络抖动就被 24h 缓存冻住。
    现在只问 CLI，且**要求精确命中**，不做子串撞：
      · plugins search 的 package.name / runtimeId == slug（或去 -plugin/-provider 的裸名）⇒ plugin
      · skills  search 的 canonicalUrl 以 /skills/<slug> 结尾                              ⇒ skill
    顺序先问 plugins：ClawHub 上同名 skill 会把 plugin 的裸 slug 撞走。
    注意 CLI 的实际返回形状（实测）：
      · plugins search 的 package.name 是**全名**（`@openclaw/tavily-plugin`），
        也可能是裸名或短 id —— 三种都比。
      · plugins search 只搜 **ClawHub 插件目录**，npm 装的包（如 @mem9/mem9）
        在这里是 0 条。⇒ 判不出就返回 ''，交给 _install_any 让 CLI 自己试两族。
    """
    slug = name.split("/")[-1].lower()
    bare = slug.replace("-provider", "").replace("-plugin", "")

    try:
        for r in (json.loads(_cli_search(name, "plugins") or "{}") or {}).get("results", []):
            pk = r.get("package") or {}
            got = str(pk.get("name") or r.get("runtimeId") or "").lower()
            if not got:
                continue
            if got == name.lower() or got.split("/")[-1] in (slug, bare):
                return "plugin"
    except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
        pass

    try:
        for r in (json.loads(_cli_search(name, "skills") or "{}") or {}).get("results", []):
            url = (r.get("canonicalUrl") or "").rstrip("/").lower()
            if slug and url.endswith("/skills/" + slug):
                return "skill"
    except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
        pass

    return ""


def _default_agent() -> str:
    """问 CLI：默认 agent 是谁。壳不硬编名字。

    2026-09-25 加：CLI 报 "Multiple agents are configured, but the skills command
    has no explicit owner. Pass --agent <id>." —— 一旦配了第二个 agent
    （实测 主 agent 与子 agent），不带 --agent 的 skills install 直接 rc=1，
    8 项预装会全挂。这里只问 CLI 要 isDefault 那个。
    """
    try:
        rc, out, err = _wsl_run_argv("openclaw agents list --json")
    except Exception as e:
        _cli_log("agent: openclaw agents list --json", f"执行异常: {e}")
        return ""
    if rc != 0:
        # 失败 → 原文全记（错误信息短，且正是要看的）
        _cli_log(f"agent: openclaw agents list --json  (rc={rc})",
                 (out or "") + (("\n[stderr]\n" + err) if err else ""))
        return ""
    try:
        agents = json.loads(out or "[]")
    except (json.JSONDecodeError, ValueError):
        _cli_log("agent: openclaw agents list --json  (rc=0，但 JSON 解析失败)", out or "")
        return ""
    if isinstance(agents, dict):
        agents = agents.get("agents") or []
    if not isinstance(agents, list):
        return ""
    aid = ""
    for a in agents:
        if isinstance(a, dict) and a.get("isDefault"):
            aid = str(a.get("id") or "")
            break
    # 成功 → 只记结论。JSON 全文每条含 base64 头像（几十 KB），全量落盘会把 log 淹掉、
    # 反而看不出装没装。要全文就手动跑 `wsl -d Ubuntu -u root -- openclaw agents list --json`。
    _cli_log(f"agent: openclaw agents list --json  (rc=0) → 默认 agent = {aid or '(未取到)'}",
             "（列出的 agent：" + ", ".join(
                 str(a.get("id")) for a in agents if isinstance(a, dict)) + "）")
    return aid


def _resolve_kind(name: str) -> str:
    """判定一个预装名字是 skill 还是 plugin。返回 'skill'/'plugin'/''。

    2026-09-25 产品方令：判据归 CLI —— 本体就是 _cli_kind（问 CLI search），
    壳不再自爬 npm registry + clawhub API 自己判一份。

    P1-3：判出来的结果落 install_kind_cache.json，24h 内命中免再问（离线友好）。
    **判空不落缓存**：空很可能是瞬时的，冻 24h = 一次抖动废掉一整天
    （实测 skill-vetter 就是这么少装一个的）。
    """
    # 缓存命中（TTL 内）→ 免网络
    try:
        if os.path.isfile(_KIND_CACHE):
            with open(_KIND_CACHE, "r", encoding="utf-8") as f:
                cache = json.load(f)
            if isinstance(cache, dict):
                entry = cache.get(name)
                if isinstance(entry, dict) and entry.get("kind"):
                    cached_at = entry.get("cached_at", 0)
                    if time.time() - cached_at < _KIND_CACHE_TTL:
                        return entry["kind"]
    except (json.JSONDecodeError, OSError):
        pass

    kind = _cli_kind(name)

    # 落缓存：只落判出来的结果（2026-09-25 修：判空不再落缓存）
    try:
        try:
            with open(_KIND_CACHE, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except (json.JSONDecodeError, OSError):
            cache = {}
        if not isinstance(cache, dict):
            cache = {}
        if kind:
            cache[name] = {"kind": kind, "cached_at": time.time()}
        else:
            # 判空很可能是瞬时的（网络抖动 / clawhub 那一下没回）⇒ 不冻进缓存，
            # 连同已存在的空条目一起清掉，留给下次重判。
            cache.pop(name, None)
        os.makedirs(_PROGRAMDATA, exist_ok=True)
        with open(_KIND_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except OSError:
        pass

    return kind

def _install_succeeded(rc: int, combined: str) -> bool:
    """判定安装是否成功。

    成功标志有两个，都来自官方 CLI 的权威反馈，不是壳层硬编：
      1. rc == 0（命令正常退出）
      2. 官方 CLI 的幂等成功语义：already exists / already installed / installed plugin
         （plugins.md / skills.md 明文：已安装时 CLI 报「already exists」并返回非 0，
         这是「已装好」的成功，不是失败）

    之前退化成只认 rc==0，漏了「already exists」幂等成功，导致已装的 skill
    被误判失败、重试 5 次（2026-09-05 日志铁证）。
    """
    if rc == 0:
        return True
    low = (combined or "").lower()
    return ("already exists" in low
            or "already installed" in low
            or "installed plugin" in low
            or "installed skill" in low)

def _is_terminal_error(combined: str) -> bool:
    """确定性错误判定：CLI 反馈「再试也无用」的信号 → 早停，不重试。

    评审 P2-7：404 / 包不存在 / 源不可达 / 权限拒绝 / 语法错误等，重试只会
    白耗 5×300s。只有「可能 transient」的错误（网络抖动、timeout、锁冲突）
    才值得重试。这里只认确定性失败信号，拿不准的一律继续重试（保守，不误杀）。
    """
    low = (combined or "").lower()
    terminal_markers = [
        "not found", "404", "no such package", "package not found",
        "does not exist", "unknown package", "invalid package",
        "permission denied", "eacces", "access denied", "forbidden",
        "401", "unauthorized", "invalid slug", "malformed",
        "unknown flag", "unknown option", "command not found",
        "enonet", "enxio", "source unreachable",
    ]
    return any(m in low for m in terminal_markers)

def _inject_local_plugins() -> None:
    """随壳本地插件注入 WSL：装壳即到位，不依赖外部源 / 手工部署。

    把随壳插件（`persona\\scripts\\`）落到 WSL 的 Linux 侧
    （~/.openclaw/plugins/persona-slot-engine，权限收紧），
    再把**那个落点**登记进 WSL 侧 Gateway 的 plugins.load.paths——
    `/mnt/c` 那份在 WSL 里显示成全局可写，会被官方加载器拒载。
    插件随壳走（persona/scripts 与 persona 树同生共死），不漂移。幂等由脚本内保证。

    **产品化口径（客户永远不用敲命令）**：
      · 这一步在每次开机都会跑（`injectPersona` 的第一件事，任何早退都挡不住）；
      · 退出码 0 = 无需改动；**10 = 登记结果变了** ⇒ 这里顺手重启一次 Gateway 让它加载；
      · 退出码 2 = 环境还没就绪（比如全新安装时 OpenClaw 配置尚未生成）⇒ 只记一笔，下次开机自然补上。
    """
    try:
        app_dir = _resolve_app_dir()
        if not app_dir or not os.path.isdir(app_dir):
            return
        wsl_app = "/mnt/" + app_dir[0].lower() + app_dir[2:].replace("\\", "/")
        plugin_dir = wsl_app + "/persona/scripts"
        script = (
            f"python3 '{wsl_app}/installer/inject_local_plugins.py' "
            f"'{plugin_dir}' 2>&1"
        )
        rc, out, err = _wsl_run_argv(script)
        if rc == 10:
            stk("bonus.local_plugin", f"inject rc=10 已登记 → 让它加载 {(out or '')[:80]}", ok=True)
            # ① 官方首选：`openclaw plugins reload <id>`（改插件代码/清单后的官方动词，
            #    cli/plugins.md:50）。热重载成了就不冷启动 —— 把"要重启"这件事压到最少。
            rcR, outR, errR = _run_official_cmd("plugins", "reload",
                                                "persona-slot-engine")
            if rcR == 0 and check_gateway_alive():
                stk("bonus.local_plugin.reload", f"plugins reload 成功（不重启） {(outR or '')[:100]}",
                    ok=True)
                return
            stk("bonus.local_plugin.reload",
                f"plugins reload 没成 rc={rcR}（{(outR + errR).strip()[:140]}）→ 冷启动兜底",
                ok=False)
            # ② 冷启动兜底（CLI 优先、systemd 兜底、验活）
            try:
                # ⚠ 必须看**返回值**（2026-09-21 实测）：重启失败时重启函数返回 False，
                #   以前这里一律记 ok=True —— 等于把"插件没加载上"报成成功。
                # 与 keepalive 的激活互斥：两边同时下发 start/restart 会撞单实例迁移锁。
                _owned = acquire_lifecycle("ui.plugin_inject")
                if not _owned:
                    # 生命周期已被别的 owner 接管 ⇒ **本轮不下发重启**。
                    # 旧写法不管 _owned 真假都照发 restart ⇒ 与正在管的 owner 并发下发
                    # start/restart，两边撞 OpenClaw 单实例迁移锁（网关卡在 inactive 起不来）。
                    stk("bonus.local_plugin.restart",
                        "生命周期已被别的 owner 接管 ⇒ 本轮不下发重启（插件等下次重启自会加载）",
                        ok=False)
                    return
                try:
                    _restarted = restart_gateway_safe(
                        lambda m: stk("bonus.local_plugin.restart", m, ok=True), manual=True)
                finally:
                    release_lifecycle("ui.plugin_inject")
                if _restarted:
                    stk("bonus.local_plugin.restart", "网关已重启并确认存活（插件已加载）", ok=True)
                else:
                    stk("bonus.local_plugin.restart",
                        "登记已变但网关**没重启/没起来** ⇒ 插件要等下次重启才加载（显式记账，不报成功）",
                        ok=False)
            except Exception as _re:
                stk("bonus.local_plugin.restart", f"重启失败（下次开机自会加载）: {_re}", ok=False)
        elif rc == 20:
            # 只有运行规格（数据）变了 ⇒ 写进了插件配置，官方 hybrid reload 自动热生效，
            # **不重启**。这就是"改声明不改插件、不看重启命令脸色"的那条路。
            stk("bonus.local_plugin", f"仅规格变更，已写进插件配置（热重载生效，不重启） {(out or '')[:80]}",
                ok=True)
        elif rc == 2:
            stk("bonus.local_plugin", f"环境未就绪，本次跳过（下次开机自动补）: {(out or '')[:100]}", ok=True)
        else:
            stk("bonus.local_plugin", f"persona-slot-engine inject rc={rc} {(out or '')[:120]}", ok=(rc == 0))
    except Exception as e:
        stk("bonus.local_plugin", f"inject 异常 {e}", ok=False)


def _install_any(name: str) -> tuple[bool, str]:
    """装一个名字 —— kind 问 CLI，**问不出就别猜**，两个族都让 CLI 自己试一遍。

    2026-09-25 产品方令：判据归 CLI。壳只有在 CLI 给得出 kind 时才用；
    CLI 给不出时（如 npm 装的 @mem9/mem9，ClawHub 插件目录里搜到 0 条），
    壳不替它猜 —— 直接让 CLI 的两个 install 子命令各试一次，CLI 说了算。
    实测教训：猜成 skill 会让 mem9 永远装不上（skills install 里没有它）。
    """
    kind = _resolve_kind(name)
    # CLI 判出来的族先试；装不上就把另一族也交给 CLI 试。
    # 判据归 CLI，但**最终说话的是 CLI 的 install**：实测 @mem9/mem9 会被
    # ClawHub 上同名 skill 的精确命中撞成 skill，只有真去装才知道它是 npm plugin。
    other = "skill" if kind == "plugin" else "plugin"
    order = [kind, other] if kind else ["plugin", "skill"]
    reasons: list[str] = []
    for k in order:
        ok, reason = _install_one(name, k)
        if ok:
            return True, ""
        reasons.append(f"{k}: {reason}")
    return False, " | ".join(reasons)


def _install_one(name: str, kind: str) -> tuple[bool, str]:
    """抛 CLI 装一个名字——CLI 驱动，flag 由 CLI 反馈长出（2026-10 通用版）。

    壳体只做：问一次 kind（有缓存）定命令族 → 裸抛 CLI → 读 CLI 反馈：
      - CLI 明示缺什么（"rerun with --force" / "requires capability consent ...
        with --accept-capabilities" / "security audit ... --acknowledge-..."）
        → 壳体按反馈补对应 flag，重试
      - 成功（rc==0 / already exists 幂等）→ 装后验证 → plugin 再 enable
      - 确定性失败（404/unauthorized/不存在…）→ 早停不空耗
    不预置任何 flag——CLI 怎么迭代，壳体就怎么跟（反馈长出，非手工拼命令）。
    """
    cli_id = "plugins" if kind == "plugin" else "skills"
    if kind == "plugin":
        # 安装链读链：装插件这一步先看上一步（cli）的 ready —— 缺就**停**，不猜、不等。
        # 账里没有就先按官方校验探一次（探到了自会签上 cli），再读一次；仍缺才停。
        _ok, _prev, _why = _chain_prev("plugin")
        if not _ok:
            check_openclaw_cli()
            _ok, _prev, _why = _chain_prev("plugin")
        if not _ok:
            stk("bonus.skills",
                f"{name}: 安装链：plugin 这一步缺上一步 {_prev} 的 ready（{_why}）⇒ 停",
                ok=False)
            return False, f"安装链缺上一步 {_prev} 的 ready"
    stk("bonus.skills", f"{name}: 抛 CLI（{kind}）安装，flag 由反馈长出...")

    accumulated_flags: list[str] = []
    last_reason = ""
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        flag_str = " ".join(accumulated_flags)
        # skills 子命令在多 agent 下必须指名 owner（CLI 原话：Pass --agent <id>）。
        # 问 CLI 要默认 agent，不硬编名字 —— 否则每项都要先白撞一次失败。
        if cli_id == "skills" and not any(f.startswith("--agent") for f in accumulated_flags):
            _af = _agent_flag()
            if _af:
                flag_str = (flag_str + " " + _af).strip()
        inner = f"openclaw {cli_id} install '{name}' {flag_str}".strip()
        script = f'flock -n /tmp/openclaw-install.lock -c "{inner} 2>&1"'
        try:
            rc, stdout, stderr = _wsl_run_argv(script)
        except Exception as e:
            _cli_log(f"{name} install (attempt {attempt}): {inner}", f"执行异常: {e}")
            return False, f"执行 CLI 异常: {e}"
        combined = (stdout or "") + (stderr or "")
        # ── CLI 原文落盘（壳不加工、不截断）──
        # 装没装、为什么没装、缺什么旗，全看这一段，不看壳的推断。
        _cli_log(f"{name} install (attempt {attempt}, {kind}): {inner}  (rc={rc})",
                 combined)

        # 跨进程锁被占 → 快速失败（不排队）
        if "resource temporarily unavailable" in combined.lower():
            last_reason = "跨进程锁被占（另一 install 进行中）"
            break

        # 装成功 = CLI 说成功（rc==0 / already exists 幂等）。
        # 2026-09-25 产品方令：装没装由 CLI 说了算，壳不再自己 test -d / inspect 复核
        # （壳复核 = 壳自己出一份判据，就是 skill-vetter 那种无痕误判的来源）。
        if _install_succeeded(rc, combined):
            if kind == "plugin":
                eid = name.split("/")[-1].replace("-plugin", "").replace("-provider", "")
                try:
                    rc2, o2, e2 = _wsl_run_argv(f"openclaw plugins enable '{eid}' 2>&1")
                    _cli_log(f"{name}: openclaw plugins enable {eid}  (rc={rc2})",
                             (o2 or "") + (("\n[stderr]\n" + e2) if e2 else ""))
                    stk("bonus.skills", f"{name}: plugin enable {'OK' if rc2 == 0 else 'FAIL'}",
                        ok=rc2 == 0)
                    if rc2 == 0:                      # 产者自报：插件这一步 ready（信号账）
                        try:
                            signals().mark_install("plugin")
                        except Exception as _se:                          # noqa: BLE001
                            stk("bonus.skills", f"信号：写 plugin 失败 {_se}", ok=False)
                except Exception as _ee:
                    stk("bonus.skills", f"{name}: plugin enable 异常 {_ee}", ok=False)
            stk("bonus.skills", f"{name}: {kind} 装完（CLI 直抛, attempt {attempt}）")
            return True, ""

        # ── flag 由 CLI 反馈长出：CLI 明示缺什么就补什么 ──
        low = combined.lower()
        new_flag = False
        # --agent：CLI 报「多 agent 但没指名 owner」时补（2026-09-25 加）
        if not any(f.startswith("--agent") for f in accumulated_flags) and (
                "has no explicit owner" in low or "pass --agent" in low
                or "multiple agents are configured" in low):
            aid = _default_agent()
            if aid:
                accumulated_flags.append(f"--agent {aid}")
                new_flag = True
            else:
                stk("bonus.skills", f"{name}: CLI 要 --agent，但问不到默认 agent", ok=False)
        if "--force" not in accumulated_flags and (
                "rerun with --force" in low or "outside clawhub" in low
                or "trust the publisher" in low or "confirm non-clawhub" in low
                or "not a clawhub" in low):
            accumulated_flags.append("--force")
            new_flag = True
        if "--accept-capabilities" not in accumulated_flags and (
                "--accept-capabilities" in combined or "capability consent" in low
                or "requires capability" in low or "accept-capabilities" in low):
            accumulated_flags.append("--accept-capabilities")
            new_flag = True
        if "--acknowledge-install-policy-warning" not in accumulated_flags and (
                "acknowledge" in low or "security audit" in low
                or "policy warning" in low or "interactive tty" in low
                or "acknowledge-install-policy-warning" in low):
            accumulated_flags.append("--acknowledge-install-policy-warning")
            new_flag = True
        if new_flag:
            stk("bonus.skills", f"{name}: CLI 反馈补 {' '.join(accumulated_flags)}，重试...")
            continue  # 补了 flag → 立即重试

        # 确定性错误早停（404/包不存在/权限拒绝…重试无益）
        if _is_terminal_error(combined):
            stk("bonus.skills", f"{name}: 确定性错误，早停", ok=False)
            break

        last_reason = combined
        stk("bonus.skills",
            f"{name}: attempt {attempt}/{_MAX_ATTEMPTS} 失败: {combined[:200]!r}", ok=False)
        if attempt < _MAX_ATTEMPTS:
            # 重试之前**等网关回到 ready**（等账本被写，内核叫醒）——不掐表歇秒数。
            # 网关被判死 / 等不成 ⇒ 不再空耗，如实把原因带回给调用方。
            _how = "ready" if _gateway_signal() else _wait_gateway_ready()
            if _how != "ready":
                last_reason = f"网关没回 ready（{_how}）：{last_reason}"
                break
    stk("bonus.skills", f"{name}: 装不上（{_MAX_ATTEMPTS} 次）", ok=False)
    return False, last_reason[:400]


def _read_tavily_api_key() -> str | None:
    """读取预置的 Tavily API key。
    优先级: TAVILY_API_KEY 环境变量 > key file。
    """
    env_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if env_key:
        return env_key
    try:
        with open(_TAVILY_KEY_FILE, "r", encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None

def _read_mem9_api_key() -> str | None:
    """读取 mem9 API key。优先级: MEM9_API_KEY 环境变量。"""
    env_key = os.environ.get("MEM9_API_KEY", "").strip()
    return env_key if env_key else None

def _patch_tavily_config(api_key: str) -> None:
    """直接写 WSL openclaw.json — 不走 CLI。"""
    try:
        cfg = read()
        # plugins.entries.tavily
        cfg.setdefault("plugins", {}).setdefault("entries", {}).setdefault("tavily", {})
        tav = cfg["plugins"]["entries"]["tavily"]
        tav["enabled"] = True
        tav.setdefault("config", {}).setdefault("webSearch", {})
        tav["config"]["webSearch"]["apiKey"] = api_key
        tav["config"]["webSearch"]["baseUrl"] = "https://api.tavily.com"
        # tools.web.search
        cfg.setdefault("tools", {}).setdefault("web", {}).setdefault("search", {})
        ws = cfg["tools"]["web"]["search"]
        ws["enabled"] = True
        ws["provider"] = "tavily"
        ws["maxResults"] = 5
        if write(cfg):
            stk("bonus.skills", "Tavily: config written to openclaw.json")
        else:
            stk("bonus.skills", "Tavily: write openclaw.json FAIL", ok=False)
    except Exception as e:
        stk("bonus.skills", f"Tavily: config write exception: {e}", ok=False)

# ── mem9 配置补写（有 key 才写，安装走 _install_one 统一分流） ──

def _patch_mem9_config(api_key: str) -> None:
    """mem9 有 key 时写 openclaw.json（安装命令与 skill/plugin 分流无关）。"""
    try:
        cfg = read()
        # 官方要求：plugins.slots.memory 必须指向 mem9（缺了这条 mem9 不生效）
        cfg.setdefault("plugins", {}).setdefault("slots", {})["memory"] = "mem9"
        cfg["plugins"].setdefault("entries", {}).setdefault("mem9", {})
        m9 = cfg["plugins"]["entries"]["mem9"]
        m9["enabled"] = True
        m9.setdefault("hooks", {})["allowConversationAccess"] = True
        m9.setdefault("config", {})
        m9["config"]["apiUrl"] = "https://api.mem9.ai"
        m9["config"]["apiKey"] = api_key
        if write(cfg):
            stk("bonus.skills", "mem9: config written to openclaw.json")
        else:
            stk("bonus.skills", "mem9: write openclaw.json FAIL", ok=False)
    except Exception as e:
        stk("bonus.skills", f"mem9: config write exception: {e}", ok=False)

# ── 幂等 + 失败列表读写 ──

def _already_done() -> bool:
    """检查是否本轮无事可做。

    两种情况跳过本轮：
      1. 无 done 标记（首次）→ 要跑。
      2. 失败名单为空 → 全部装完，跳过。
      3. 失败名单非空但全部都在退避窗口内 → 本轮跳过（等下次或显式重试）。
    """
    if not os.path.isfile(_DONE_MARKER):
        return False
    failed = _read_failed()
    if not failed:
        return True
    state = _read_failed_state()
    return not _should_retry_any(state, failed)

def _mark_done() -> None:
    """写 marker 文件标记完成 + 产者自报：done 这一步 ready（信号账）。"""
    try:
        os.makedirs(_PROGRAMDATA, exist_ok=True)
        with open(_DONE_MARKER, "w") as f:
            f.write("1")
    except OSError:
        pass
    try:
        signals().mark_install("done", note="安装收口完成")
    except Exception as _se:                                          # noqa: BLE001
        _stake("信号：写 done 失败 %s" % _se)

def _read_failed() -> list[str]:
    """读 failed_skills.json。"""
    try:
        with open(_FAILED_LIST, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

def _write_failed(slugs: list[str]) -> None:
    """写 failed_skills.json。"""
    try:
        os.makedirs(_PROGRAMDATA, exist_ok=True)
        with open(_FAILED_LIST, "w", encoding="utf-8") as f:
            json.dump(slugs, f, ensure_ascii=False, indent=2)
    except OSError:
        pass

def _read_failed_state() -> dict[str, dict]:
    """读 failed_state.json（{name: {last_fail_at, fail_count}}）。

    读之前先看**运行时版本有没有变**：变了就把上一版留下的失败账清掉（那批失败
    多半是版本落差），让它们本轮重排 —— 否则升完级还要白等 24h 退避。
    """
    _reset_failures_if_runtime_changed()
    try:
        with open(_FAILED_STATE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}

def _write_failed_state(state: dict[str, dict]) -> None:
    """写 failed_state.json。"""
    try:
        os.makedirs(_PROGRAMDATA, exist_ok=True)
        with open(_FAILED_STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except OSError:
        pass

#: 版本落差类失败的**短退避窗口**（等运行时升上来即可；不套 24h 长退避）
_RETRY_BACKOFF_VERSION_SECONDS = 10 * 60

#: 运行时版本记账：一变就清失败账
_RUNTIME_VER_FILE = os.path.join(_PROGRAMDATA, "runtime_version.txt")

#: 失败原因里属于「版本落差」的标记（条件性失败）
_VERSION_GATE_MARKERS = ("requires plugin api", "runtime exposes", "api >=")

def _is_version_gated(name: str) -> bool:
    """该名字上次失败是不是「版本落差」——判据取自 failed_reasons.json 的原文。"""
    try:
        with open(_FAILED_REASONS, "r", encoding="utf-8") as f:   # noqa: F821
            data = json.load(f)
        v = data.get(name) if isinstance(data, dict) else None
        txt = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        low = (txt or "").lower()
        return any(m in low for m in _VERSION_GATE_MARKERS)
    except Exception:
        return False

def _reset_failures_if_runtime_changed() -> None:
    """运行时版本一变（9.6→9.7 这种），清掉上一版的失败账，让失败项立刻重排一轮。"""
    try:
        rc, out, _ = _wsl_run_argv("openclaw --version 2>&1")
        ver = (out.strip().splitlines() or [""])[0].strip() if (rc == 0 and out) else ""
        if not ver:
            return
        prev = ""
        try:
            with open(_RUNTIME_VER_FILE, "r", encoding="utf-8") as f:
                prev = f.read().strip()
        except OSError:
            prev = ""
        if prev and prev != ver:
            _write_failed_state({})
            try:
                os.remove(_FAILED_LIST)
            except OSError:
                pass
            stk("bonus.skills", f"运行时版本 {prev} → {ver}：清失败账，失败项重排一轮", ok=True)
        try:
            os.makedirs(_PROGRAMDATA, exist_ok=True)
            with open(_RUNTIME_VER_FILE, "w", encoding="utf-8") as f:
                f.write(ver)
        except OSError:
            pass
    except Exception:
        pass

def _mark_fail(name: str, state: dict[str, dict]) -> None:
    """记录一次失败：更新时间戳 + 递增失败次数。"""
    now = time.time()
    prev = state.get(name) or {}
    state[name] = {
        "last_fail_at": now,
        "fail_count": int(prev.get("fail_count", 0)) + 1,
    }

def _should_retry(name: str, state: dict[str, dict]) -> bool:
    """退避窗口判定：距上次失败 >= 24h 才允许重试；从未失败过则允许。

    版本落差（`requires plugin API >=x` / `runtime exposes y`）是**条件性失败**：
    运行时升上去自然就能装 ⇒ 不套 24h 退避，用短窗口 10 分钟反复排。
    """
    prev = state.get(name)
    if not prev:
        return True
    last_fail = prev.get("last_fail_at", 0)
    window = (_RETRY_BACKOFF_VERSION_SECONDS if _is_version_gated(name)
              else _RETRY_BACKOFF_SECONDS)
    return (time.time() - last_fail) >= window

def _should_retry_any(state: dict[str, dict], names: list[str]) -> bool:
    """失败名单里是否还有「越过退避窗口、值得本轮重试」的名字。"""
    return any(_should_retry(n, state) for n in names)

# ── 失败原因：name → CLI 返回的完整 stderr/stdout ──
# 单独存 failed_reasons.json，不污染失败名单（侧边栏读的是名字列表）。
# CLI（配通模型的 Agent）读到这里，就知道哪个名字装失败、失败原因是什么、
# 截断在哪，自己拼命令在 WSL 里重装——可能一条命令的事。
_FAILED_REASONS = os.path.join(_PROGRAMDATA, "failed_reasons.json")

def _write_failed_reasons(reasons: dict[str, str]) -> None:
    """写 failed_reasons.json（name -> 失败原因）。"""
    try:
        os.makedirs(_PROGRAMDATA, exist_ok=True)
        with open(_FAILED_REASONS, "w", encoding="utf-8") as f:
            json.dump(reasons, f, ensure_ascii=False, indent=2)
    except OSError:
        pass

# KNOWLEDGE.md 动态段锚点：预装失败清单注入这里，Agent 每轮 system prompt 读到
_KNOWLEDGE_BEGIN = "<!-- PREINSTALL_FAILURES_BEGIN -->"
_KNOWLEDGE_END = "<!-- PREINSTALL_FAILURES_END -->"
_KNOWLEDGE_FILE = os.path.join(_PROGRAMDATA, "KNOWLEDGE.md")

def _inject_failures_into_knowledge(failed: list[str], reasons: dict[str, str]) -> None:
    """把预装失败清单写进 ProgramData 的 KNOWLEDGE.md 动态段。

    KNOWLEDGE.md 拼进 Gateway Agent 的 system prompt（每轮都读），Agent 不会主动
    读 failed_reasons.json。所以失败清单必须直接写进 KNOWLEDGE.md，Agent 才能从
    system prompt 看到「哪些没装上 + 失败原因」，自行在 WSL 里重装。
    """
    if not failed:
        return
    try:
        if not os.path.isfile(_KNOWLEDGE_FILE):
            return
        with open(_KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
            raw = f.read()
        if _KNOWLEDGE_BEGIN not in raw or _KNOWLEDGE_END not in raw:
            return
        lines = []
        for name in failed:
            reason = (reasons.get(name) or "").strip().replace("\n", " ")
            # 失败原因可能很长，截到 500 字，够 Agent 定位截断点
            reason_short = reason[:500]
            lines.append(f"- **{name}** 装失败：`{reason_short}`")
        block = "\n".join(lines)
        new_raw = raw.split(_KNOWLEDGE_BEGIN)[0] + _KNOWLEDGE_BEGIN \
            + "\n" + block + "\n" + _KNOWLEDGE_END \
            + raw.split(_KNOWLEDGE_END)[1]
        with open(_KNOWLEDGE_FILE, "w", encoding="utf-8") as f:
            f.write(new_raw)
        stk("bonus.skills", f"预装失败清单已注入 KNOWLEDGE.md（{len(failed)} 项）")
        # 注入后重新写 SOUL.md（Agent 读的是 SOUL.md，不是 KNOWLEDGE.md 本身）。
        # 不重写，Agent 本轮 system prompt 还是旧的、读不到失败清单。
        try:
            from persona.indivi import injectPersona
            injectPersona()
            stk("bonus.skills", "已重新注入 SOUL.md，Agent 下次会话可读到失败清单")
        except Exception as _ie:
            stk("bonus.skills", f"重新注入 SOUL.md 失败（非致命）: {_ie}", ok=False)
    except Exception as e:
        stk("bonus.skills", f"注入 KNOWLEDGE.md 失败（非致命）: {e}", ok=False)

# ── 启动时自动清理已修复的 skill ──

def _verify_installed(name: str) -> bool:
    """该名字是否已装 —— **只问 CLI**，壳不自己查落点。

    2026-09-25 产品方令：已装检测归 CLI，CLI 给出「已装」就不再重复耗费资源。
      · skill  → `openclaw skills list --json` 的 skills[] 里有它
      · plugin → `openclaw plugins list --json` 的 plugins[] 里有它的 id

    此前 skill 分支是壳自己 `test -d $HOME/.openclaw/workspace/skills/<slug>` 猜落点，
    还带着「@owner/slug 旧结构 + 扁平 slug 新结构」两代兼容分支 —— 落点一变就误判，
    而 CLI 早就把权威名单给全了。壳自建判据 = 壳自己出 bug 且无痕。
    """
    slug = name.split("/")[-1].lower()
    bare = slug.replace("-provider", "").replace("-plugin", "")

    try:
        # 多 agent 下 skills 子命令必须指名 owner，否则 CLI 直接 rc=1 返回 error JSON
        # （实测：裸跑 {"ok": false} rc=1；带 --agent main 才给名单）。
        cmd = f"openclaw skills list --json {_agent_flag()}".strip()
        rc, out, err = _wsl_run_argv(cmd)
        _cli_log(f"installed?: {name} ← {cmd}  (rc={rc})",
                 (out or "") + (("\n[stderr]\n" + err) if err else ""))
        if rc == 0:
            for s in (json.loads(out or "{}") or {}).get("skills", []):
                if slug and any(_norm_slug(s.get(k)) == _norm_slug(slug)
                                for k in ("name", "slug")):
                    return True
    except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
        pass

    try:
        rc, out, err = _wsl_run_argv("openclaw plugins list --json")
        _cli_log(f"installed?: {name} ← openclaw plugins list --json  (rc={rc})",
                 (out or "") + (("\n[stderr]\n" + err) if err else ""))
        if rc == 0:
            for p in (json.loads(out or "{}") or {}).get("plugins", []):
                if bare and bare in (str(p.get("id", "")).lower(),
                                     str(p.get("name", "")).lower()):
                    return True
    except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
        pass

    return False


#: 本轮装链的官方名单缓存（skills/plugins 两份，整轮**只问 CLI 一次**）。
_INSTALLED_CACHE: dict | None = None


def _reset_installed_cache() -> None:
    """新的一轮装链开始 ⇒ 丢掉上一轮的名单缓存（名单可能已经变了）。"""
    global _INSTALLED_CACHE
    _INSTALLED_CACHE = None


def _installed_cache() -> dict:
    """**只问一次 CLI**：把官方名单（skills/plugins）缓存下来。

    依据与 `_verify_installed` 同源 —— **判据归 CLI**，壳不自己查落点。
    唯一区别：整轮只跑两条 list 命令，而不是每个名字各跑两条。
    """
    global _INSTALLED_CACHE
    if _INSTALLED_CACHE is not None:
        return _INSTALLED_CACHE
    sk: set = set()
    pl: set = set()
    try:
        cmd = f"openclaw skills list --json {_agent_flag()}".strip()
        rc, out, _err = _wsl_run_argv(cmd)
        _cli_log(f"installed?（缓存）← {cmd}  (rc={rc})", out or "")
        if rc == 0:
            for s in (json.loads(out or "{}") or {}).get("skills", []):
                for k in ("name", "slug"):
                    v = s.get(k)
                    if v:
                        sk.add(_norm_slug(v))
    except Exception:
        pass
    try:
        rc, out, _err = _wsl_run_argv("openclaw plugins list --json")
        _cli_log(f"installed?（缓存）← openclaw plugins list --json  (rc={rc})", out or "")
        if rc == 0:
            for pg in (json.loads(out or "{}") or {}).get("plugins", []):
                pl.add(str(pg.get("id", "")).lower())
                pl.add(str(pg.get("name", "")).lower())
    except Exception:
        pass
    _INSTALLED_CACHE = {"skills": sk, "plugins": pl}
    return _INSTALLED_CACHE


def _verify_installed_fast(name: str) -> bool:
    """`_verify_installed` 的**缓存版**（同一轮里只问 CLI 一次），语义完全相同：

    官方名单里在 ⇒ True。**漏报是安全的**：官方漏报 ⇒ 她照旧装一遍（CLI 幂等）。
    """
    c = _installed_cache()
    slug = name.split("/")[-1].lower()
    bare = slug.replace("-provider", "").replace("-plugin", "")
    if slug and slug in c.get("skills", set()):
        return True
    if bare and bare in c.get("plugins", set()):
        return True
    return False


def cleanup_failed_skills() -> None:
    """检查 failed_skills.json 中是否有已通过其他途径安装成功的名字，
    自动移除，保持列表干净。**判据归 CLI**（_verify_installed 问 CLI 名单）。
    """
    current = _read_failed()
    if not current:
        return
    still_failed: list[str] = []
    for name in current:
        installed = _verify_installed(name)
        if installed:
            stk("bonus.skills", f"auto-cleaned: {name} is now installed")
        else:
            still_failed.append(name)
    if len(still_failed) != len(current):
        _write_failed(still_failed)

# ── 主入口 ──

def install_bonus_skills_background(*, run_sync: bool = False) -> None:
    """后台线程：装 clawhub CLI → Tavily → mem9 → 5 bonus skills。幂等，失败跳过。

    只应在 Gateway + token + 模型配置完成之后调用。
    全部尝试完毕写 done 标记 + failed_skills.json。
    """

    # ── 第一关：幂等检查 ──
    if _already_done():
        stk("bonus.skills", "already done, skip")
        return

    # ── 安装链入口（起 WSL 步骤之前）：开本次装机 ──
    # `install_id` 与账里不同 ⇒ `open_install` 把安装段**整段重写**（旧 ready 骗不了新装机）；
    # 相同 ⇒ 原样留着（安装段一经安装即固化，不覆写）。
    # wsl 是链上第一步（没有上一步），它的 ready 由"WSL 应答 / apt 装完"那两处自报。
    try:
        _iid = _install_id()
        _e = signals().open_install(_iid)
        stk("bonus.skills", f"安装链：开装 install_id={_iid}" + (f"（{_e}）" if _e else ""))
    except Exception as _e:                                           # noqa: BLE001
        stk("bonus.skills", f"安装链：开装写信号失败 {_e}", ok=False)

    # ── 第二关：WSL 是否存活 ──
    if not _bs_is_wsl_responding():
        stk("bonus.skills", "WSL not responding, defer", ok=False)
        return

    # ── 第三关：Gateway 是否稳定（CLI 在模型配置完成前没有任何能力）──
    # CLI 的能力依赖 Gateway + token + 模型配置完成。若 Gateway 还在重启窗口
    # （deactivating/activating），此刻调 CLI 会撞 state-lifecycle 锁（2026-09-04
    # 日志铁证：bonus.skills 在 Gateway deactivating 时启动，mem9 install 撞锁）。
    # 必须等 Gateway 探活稳定（curl 18789 返回 HTTP 200）才允许开始装。
    try:
        if not check_gateway_alive():
            stk("bonus.skills", "Gateway 未稳定，defer（等重启回来）", ok=False)
            return
    except Exception as e:
        stk("bonus.skills", f"Gateway 探活异常，defer: {e}", ok=False)
        return

    stk("bonus.skills", f"starting: {len(_PREINSTALL_NAMES)} 个预装名字（广域网分流 + CLI 拼命令）")
    _reset_installed_cache()   # 新一轮 ⇒ 丢掉上一轮的官方名单缓存

    def _worker() -> None:
        failed_names: list[str] = []
        failed_reasons: dict[str, str] = {}
        try:
            # ── Phase 0: pip + venv + office 核心依赖（skills Python 运行时环境）──
            try:
                ensure_pip_venv_background()
            except Exception as e:
                stk("bonus.skills", f"pip_venv init failed (non-fatal): {e}")

            # ── Phase 1: 只重试「越过退避窗口的失败名单」+ 首次未装过的名字 ──
            # P1-2：不再全量遍历 _PREINSTALL_NAMES。首次跑装全部；之后跑只挑
            # 失败名单里距上次失败 >= 24h 的（P0-1 退避窗口），其余跳过。
            state = _read_failed_state()
            prev_failed = set(_read_failed())
            names_to_run: list[str] = []
            for name in _PREINSTALL_NAMES:
                if name in prev_failed:
                    # 失败过的：只在退避窗口外重试
                    if _should_retry(name, state):
                        names_to_run.append(name)
                else:
                    # 从未失败过（且不在失败名单）→ 首次装或已成功，交给 CLI 幂等
                    names_to_run.append(name)

            if names_to_run:
                stk("bonus.skills", f"本轮重试 {len(names_to_run)} 个名字（跳过退避窗口内的失败项）")
            for name in names_to_run:
                # 装下一个之前，先看 **Gateway 段信号**（官方探针记下来的 ready）：
                # plugins/skills install 会触发 OpenClaw config reload + restart，网关没回到
                # ready 就动手，下一个 CLI 进程会和正在重启的 Gateway 抢 state-lifecycle 锁
                # → 撞锁失败（2026-09-04 死循环铁证）。
                # 没回 ready 就**等账本被写**（内核叫醒，不掐表）；等成什么样由官方判决：
                # 到位 ⇒ 接着装；被判死 ⇒ 本轮收工交下次；等不成 ⇒ 如实收手。
                # 装前**只读探针记的那一栏**
                #   （`gateway_probe().ready()`，零 `wsl` 调用）；只有读到"不 ready"时才走
                #   `_wait_gateway_ready()`（那里面才真探一次 + 必要时调官方阻塞命令）。
                #   原来是每条名字都 `_gateway_signal()` 探一次 ⇒ 8 个名字 = 8 次 `wsl`（~64 秒）。
                if not gateway_probe().ready():
                    _how = _wait_gateway_ready()
                    if _how != "ready":
                        stk("bonus.skills",
                            f"网关没回 ready（{_how}）⇒ 本轮到此为止，下次开机续装", ok=False)
                        break
                # 装没装、要不要装，全交给 CLI：install 本身幂等，已装的 CLI 会回
                # already exists / already installed（_install_succeeded 认这个语义）。
                # **先问官方名单，在册的直接跳过 install** ——
                #   依据 = CLI 自己的账（skills/plugins list --json；整轮只问一次，缓存）。
                #   与 2026-09-25 那条令同向：CLI 说已装，就不要再重复耗资源。
                #   漏报是安全的：官方漏报 ⇒ 照旧装一遍（CLI 幂等）；绝不会"谎报已装"。
                #   动机：装机后那一轮 8 个名字全量 install（含 search）≈ 5 分钟高负载，
                #   而其中大多数本来就已装 —— 高负载期正是「闪」反复冒头的时段。
                if _verify_installed_fast(name):
                    # **跳过也要如实标 ready** —— 收口前要读
                    #   「上一步 plugin 的 ready」（`_chain_prev("done")`）；只跳过不标 ⇒
                    #   `done` 永远写不上 ⇒ 每轮白跑一遍（实测 18:33 就报了这个 FAIL）。
                    try:
                        _slug = name.split("/")[-1].lower()
                        _bare = _slug.replace("-provider", "").replace("-plugin", "")
                        if _bare in (_installed_cache().get("plugins") or set()):
                            signals().mark_install("plugin", note="官方在册（跳过安装）")
                    except Exception:
                        pass
                    stk("bonus.skills", f"{name}: 官方在册，跳过 install")
                    state.pop(name, None)
                    continue
                # 2026-09-25 产品方令：壳不再自己 test -d / inspect 预检一份判据
                # （壳自建判据 = 壳自己出 bug 且无痕，skill-vetter 就是这么丢的）。
                ok, reason = _install_any(name)
                if not ok:
                    # ③ 壳体装不上 → 记 failed（侧边栏显示 + KNOWLEDGE 注入，抛给 CLI/Agent）
                    failed_names.append(name)
                    failed_reasons[name] = reason
                    _mark_fail(name, state)
                else:
                    # 装成功 → 从失败状态里清掉（P1-2：成功即移出）
                    state.pop(name, None)
            _write_failed_state(state)

            # ── Phase 2: 有 key 的插件补写 config（安装命令之外，是配置不是分类）──
            if _read_tavily_api_key():
                _patch_tavily_config(_read_tavily_api_key())
            if _read_mem9_api_key():
                _patch_mem9_config(_read_mem9_api_key())

            # ── Phase 3: 预装配套（LibreOffice / agent-browser CLI+内核）直装 ──
            # 缺配套 = 给客户留缺口 → 配套没全装不写 done，下次启动重试（本体已装会跳过）
            stk("bonus.skills", "Phase 3: 预装配套（LibreOffice / agent-browser 内核）...")
            if not _install_all_prereqs():
                prereq_failed = True
                stk("bonus.skills", "配套未全装，本次不写 done（下次启动重试）", ok=False)

        except Exception as e:
            import traceback
            _tb = traceback.format_exc().strip().splitlines()
            stk("bonus.skills", f"worker crashed: {e} | {' / '.join(_tb[-4:])}", ok=False)
        finally:
            # 写真实失败名单（装不上的名字保留，供侧边栏展示 + 下次重试），
            # 再写失败原因（供 CLI/Agent 读到就自己装）。
            # 配套未齐（prereq_failed）→ 不写 done：下次启动重试配套。
            # P0-1：保留「退避窗口内仍失败」的名字 + 本轮新失败的，合并写回。
            current_failed = set(_read_failed())
            current_failed.update(failed_names)   # 本轮新失败
            for name in names_to_run:             # 本轮成功的不再是失败
                if name in current_failed and name not in failed_names:
                    current_failed.discard(name)
            _write_failed(sorted(current_failed))
            _write_failed_reasons(failed_reasons)
            # 失败清单注入 KNOWLEDGE.md（Agent 每轮 system prompt 读它，不会主动读 json）
            _inject_failures_into_knowledge(sorted(current_failed), failed_reasons)
            if locals().get("prereq_failed"):
                stk("bonus.skills", "配套未齐：不写 done，下次启动重试配套", ok=False)
            else:
                # 收口之前**读链**：上一步（plugin）没 ready 就不收口 —— 不猜、不等，如实记。
                _ok, _prev, _why = _chain_prev("done")
                if not _ok:
                    stk("bonus.skills",
                        f"安装链：收口缺上一步 {_prev} 的 ready（{_why}）⇒ 不写 done（下次启动自然补）",
                        ok=False)
                else:
                    _mark_done()
                    stk("bonus.skills",
                        f"done — 本轮装 {len(names_to_run)} 个，失败 {len(failed_names)}"
                        + (f"：{failed_names}" if failed_names else "，全部装完"))

                # 通知 UI 线程：刷新 failed_skills（此时为空，提示消失）
                _signals.failed_refresh.emit()
                _signals.finished.emit()

    # ⚠ **安装链路不许影响页面**：搬出 UI 进程后，这条链在
    #   子进程里**同步**跑（含"装没装 / 配套齐没齐"两步校验），UI 进程一个字节不占。
    if run_sync:
        _worker()
        return
    t = threading.Thread(target=_worker, daemon=True, name="bonus-skills")
    t.start()

try:                                     # POSIX（WSL 侧）
    import fcntl as _fcntl
except Exception:                                                # noqa: BLE001
    _fcntl = None
try:                                     # Windows（壳侧）
    import msvcrt as _msvcrt
except Exception:                                                # noqa: BLE001
    _msvcrt = None


class Signals(object):
    """**信号账**（方法化）：安装链 ready + Gateway 段 ready，落一份 JSON。

    形状：信号以 **`ready`** 为锚点；安装时**按顺序读上一步的 ready**，
    缺一步就停（报因），不猜、不等、不重试；**安装段一经安装即固化**（`install_id` 变了才整段重写）；
    **只有 Gateway 段**在每次 Gateway 重启时覆写（判据来自**官方探针**，不自己发明）；
    **不落临时件**（同一份文件、文件锁 + 原位重写）；**等待靠"锁被释放"唤醒**（内核信号），
    **不用 sleep 轮询、不用时限**。
    """

    LOCK_BYTES = 1
    #: 安装链的顺序（`prev_ready` 就按它找"上一步"）
    STEPS = ("wsl", "node", "cli", "plugin", "done")

    def __init__(self, path: str = "") -> None:
        p = str(path or os.environ.get("RHEIA_SIGNALS") or "").strip()
        self.path = p or self.default_path()

    # ── 路径 ────────────────────────────────────────────────────────────
    @staticmethod
    def default_path() -> str:
        """**落在装好的 Rheia 自己的目录里**：
        `<Rheia>/kernel/large/signals.json` —— 跟着产品一起，不散落在别处。
        从 `__file__` 往上找"含 kernel 的那层"当 Rheia 根；找不到就用部署缺省路径。
        ⚠ `C:\\Program Files\\…` 平时不给普通用户写：写不进去由调用方**如实报权限问题**，
          不许静默、不许改落到别处。
        """
        for up in (1, 2):
            try:
                root = os.path.dirname(os.path.abspath(__file__))
                for _ in range(up):
                    root = os.path.dirname(root)
                if os.path.isdir(os.path.join(root, "kernel")):
                    return os.path.join(root, "kernel", "large", "signals.json")
            except Exception:                                      # noqa: BLE001
                pass
        return r"C:\Program Files\Rheia\kernel\large\signals.json"

    # ── 锁（内核对象；释放即"有变更"的信号）────────────────────────────
    def _lock(self, fh, blocking: bool = True) -> bool:
        try:
            if _fcntl is not None:
                flags = _fcntl.LOCK_EX if blocking else _fcntl.LOCK_EX | _fcntl.LOCK_NB
                _fcntl.flock(fh, flags)
                return True
            if _msvcrt is not None:
                fh.seek(0, os.SEEK_END)
                if fh.tell() == 0:
                    fh.write(b"\n")
                    fh.flush()
                fh.seek(0)
                _msvcrt.locking(fh.fileno(),
                                _msvcrt.LK_LOCK if blocking else _msvcrt.LK_NBLCK,
                                self.LOCK_BYTES)
                return True
        except OSError:
            return False
        return True

    def _unlock(self, fh) -> None:
        try:
            if _fcntl is not None:
                _fcntl.flock(fh, _fcntl.LOCK_UN)
            elif _msvcrt is not None:
                fh.seek(0)
                _msvcrt.locking(fh.fileno(), _msvcrt.LK_UNLCK, self.LOCK_BYTES)
        except OSError:
            pass

    # ── 读 ──────────────────────────────────────────────────────────────
    @staticmethod
    def empty() -> dict:
        return {"install_id": "", "install": {}, "gateway": {}}

    def read(self) -> dict:
        """读信号账；读不到 / 半写坏 ⇒ 当"没有信号"。"""
        try:
            with open(self.path, "rb") as fh:
                raw = fh.read()
            return json.loads(raw.decode("utf-8")) if raw.strip() else self.empty()
        except Exception:                                          # noqa: BLE001
            return self.empty()

    def ready(self, step: str, kind: str = "install") -> bool:
        """某一步的 `ready` 锚点是不是 true —— **唯一的判据**。"""
        seg = (self.read().get(kind) or {})
        return bool((seg.get(step) or {}).get("ready") is True)

    def prev_ready(self, step: str, steps=None, kind: str = "install"):
        """按顺序看**上一步**：返回 (ok, 上一步名)。第一步没有上一步 ⇒ (True, "")。"""
        order = list(steps or self.STEPS)
        if step not in order:
            return False, ""
        i = order.index(step)
        if i == 0:
            return True, ""
        pv = order[i - 1]
        return self.ready(pv, kind=kind), pv

    # ── 写（同文件 + 锁 + 原位重写；不落临时件）────────────────────────
    def _write(self, data: dict) -> str:
        d = os.path.dirname(self.path)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        blob = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        mode = "rb+" if os.path.exists(self.path) else "w+b"
        with open(self.path, mode) as fh:
            if not self._lock(fh, blocking=True):
                return "拿不到信号文件锁"
            try:
                fh.seek(0)
                fh.write(blob)
                fh.truncate()
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                self._unlock(fh)
        return ""

    def open_install(self, install_id: str) -> str:
        """开一次安装：`install_id` 与账里不同 ⇒ **整段重写安装段**（旧 ready 不许骗新流程）。"""
        if not str(install_id or "").strip():
            return "缺 install_id"
        d = self.read()
        if str(d.get("install_id") or "") != str(install_id):
            d["install_id"] = str(install_id)
            d["install"] = {}
            return self._write(d)
        return ""

    def mark_install(self, step: str, ready_: bool = True, note: str = "",
                     install_id: str = "") -> str:
        """安装某一步**产者自报** ready；固化：不许把已 true 的改回 false。"""
        d = self.read()
        if install_id and str(d.get("install_id") or "") != str(install_id):
            return "install_id 不符（先 open_install）"
        seg = d.setdefault("install", {})
        if step in seg and (seg[step] or {}).get("ready") is True and not ready_:
            return "已固化的 ready 不许改回"
        seg[step] = {"ready": bool(ready_),
                     "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "note": note}
        return self._write(d)

    def mark_gateway(self, ready_: bool, pid: int = 0, port: int = 0,
                     probe: str = "official") -> str:
        """**只覆写 Gateway 段**（每次 Gateway 重启都重写这段；判据来自官方探针）。"""
        d = self.read()
        d["gateway"] = {"ready": bool(ready_), "pid": int(pid or 0), "port": int(port or 0),
                        "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "probe": probe}
        return self._write(d)

    # ── 等（等的是"账本被写"这件事；内核叫醒，不 sleep、不轮询、无时限）──────
    @staticmethod
    def _arm_change(path: str):
        """武装一个「这个目录里的文件被改了」的内核通知句柄（Windows）。

        武装不成（非 Windows / 系统不给）返回 None —— 调用方据此**如实收手**。
        """
        if os.name != "nt":
            return None
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.FindFirstChangeNotificationW.restype = wintypes.HANDLE
            k32.FindFirstChangeNotificationW.argtypes = [wintypes.LPCWSTR,
                                                         wintypes.BOOL, wintypes.DWORD]
            d = os.path.dirname(os.path.abspath(path)) or "."
            # 建 / 改大小 / 改内容，三样都盯（原位重写会同时动大小与内容）
            h = k32.FindFirstChangeNotificationW(d, False,
                                                 0x00000001 | 0x00000008 | 0x00000010)
            return h or None
        except Exception:                                             # noqa: BLE001
            return None

    def wait_change(self) -> bool:
        """等到账本**下一次被写**为止（内核叫醒；不 sleep、不轮询、无时限）。

        返回 True = 真等到了（账本被改过）；False = **没看成**（拿不到通知对象，
        比如非 Windows）—— 调用方据此**如实收手**，不许把"没看成"当轮询用。

        ⚠ 通知是"闩住"的：武装之后发生的写一定会把等待叫醒；武装之前发生的写不算数。
          所以调用方一律"先查 → 再等 → 再查"（最多白等一轮写者的节拍），
          不会出现"写完了才去等、结果永远等下去"。
        """
        if os.name != "nt":
            return False
        h = self._arm_change(self.path)
        if not h:
            return False
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            k32.WaitForSingleObject.restype = wintypes.DWORD
            k32.WaitForSingleObject(h, 0xFFFFFFFF)      # INFINITE：等的是事件，不是时限
            return True
        except Exception:                                             # noqa: BLE001
            return False
        finally:
            try:
                import ctypes
                from ctypes import wintypes
                k32 = ctypes.WinDLL("kernel32", use_last_error=True)
                k32.FindCloseChangeNotification.argtypes = [wintypes.HANDLE]
                k32.FindCloseChangeNotification(h)
            except Exception:                                         # noqa: BLE001
                pass


_SIGNALS_SINGLETON = None


def signals(path: str = "") -> "Signals":
    """取信号账（缺省单例；传 path 则另开一份，测试用）。"""
    global _SIGNALS_SINGLETON
    if path:
        return Signals(path)
    if _SIGNALS_SINGLETON is None:
        _SIGNALS_SINGLETON = Signals()
    return _SIGNALS_SINGLETON


def _install_id() -> str:
    """**本次装机的戳**：装着这份壳的那份文件的安装时间（重装/升级 ⇒ 新戳）。

    与账里相同的戳 ⇒ 安装段原样固化；不同的戳 ⇒ `open_install` 整段重写。
    读不到文件时间就用此刻现取一个 —— 宁可翻新，也不让旧 ready 骗过新装机。
    """
    try:
        return time.strftime("%Y%m%d-%H%M%S", time.localtime(os.path.getmtime(__file__)))
    except Exception:                                                 # noqa: BLE001
        return time.strftime("%Y%m%d-%H%M%S")


def _chain_prev(step: str) -> tuple[bool, str, str]:
    """**安装链读链**：看上一步的 ready，返回 (ok, 上一步名, 说明)。

    ok=False 时调用方**就地停**：不猜、不等、不重试。
    第一步（wsl）没有上一步 ⇒ `prev_ready` 直接给 (True, "")。
    """
    try:
        ok, prev = signals().prev_ready(step)
    except Exception as _e:                                           # noqa: BLE001
        return False, "", f"读信号账失败：{_e}"
    if not prev:
        return ok, prev, ""
    return ok, prev, ("" if ok else f"上一步 {prev} 没有 ready")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        _stake("!!!!!!!!! UNHANDLED EXCEPTION IN main() !!!!!!!!!")
        _stake(traceback.format_exc())
        crashDir = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia")
        os.makedirs(crashDir, exist_ok=True)
        crashPath = os.path.join(crashDir, "browser_crash.log")
        with open(crashPath, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] traceback:\n")
            traceback.print_exc(file=f)
        raise
