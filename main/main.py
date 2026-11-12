# L1-4391：Rheia 产品运行时 — Gateway 唤醒 + 探活 + 内置浏览器入口
r"""Rheia 产品运行时 — Gateway 唤醒 + 探活 + 内置浏览器入口"""












from __future__ import annotations

try:
    from lib.no_window import install as _install_no_window
    _install_no_window()
except Exception:
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







_gpu_cfg_err = None
if not os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS"):















    _flags = ["--force-prefers-reduced-motion"]
    try:
        _gpu_cfg = os.path.join(os.environ.get("PROGRAMDATA", ""),
                                "Rheia", "gateway_config.json")
        if os.path.isfile(_gpu_cfg):
            with open(_gpu_cfg, "r", encoding="utf-8") as _f:
                _gpu_data = json.load(_f)
            if _gpu_data.get("gpu_render") is False:





                _flags.append("--disable-gpu-compositing")
    except Exception as _gpu_e:

        _gpu_cfg_err = _gpu_e



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


# L110-118：_stake — 写一条带时间戳的桩日志。即使 logger 未就绪也能工作。
def _stake(msg: str) -> None:
    r"""写一条带时间戳的桩日志。即使 logger 未就绪也能工作。"""
    try:
        os.makedirs(_STAKE_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        with open(_STAKE_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass



_stake("QTWEBENGINE_CHROMIUM_FLAGS=%r" % os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS"))


if _gpu_cfg_err is not None:
    _stake("GPU 配置读取失败（按默认「不注入任何 flag」走）：%s" % _gpu_cfg_err)










# L137-190：WSL 会话持有者 —— 壳起来后保一条 sleep infinity，WSL 不被回收；不碰 Gateway。
_WSL_HOLD_LOCK = threading.Lock()
_WSL_HOLD_PROC = None
_WSL_HOLD_THREAD = None


def _wslHoldProbe(distro: str) -> bool:
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        r = subprocess.run(
            ["wsl", "-d", distro, "--", "bash", "-lc",
             "pgrep -f 'sleep infinity' >/dev/null"],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=si,
        )
        return int(r.returncode) == 0
    except Exception:
        return False


def _ensureWslHold() -> None:
    global _WSL_HOLD_PROC, _WSL_HOLD_THREAD
    with _WSL_HOLD_LOCK:
        if _WSL_HOLD_PROC is not None and _WSL_HOLD_PROC.poll() is None:
            return
        try:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            distro = os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu"
            proc = subprocess.Popen(
                ["wsl", "-d", distro, "--", "sleep", "infinity"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=si,
            )
            _WSL_HOLD_PROC = proc
            _bind_child_to_parent(proc.pid)
            if not _wslHoldProbe(distro):
                _stake("--- WSL hold probe miss (pid=%s) ---" % proc.pid)
            else:
                _stake("--- WSL session held open (pid=%s, bound to shell) ---" % proc.pid)
        except Exception as e:
            _stake("WSL session hold failed: %s" % e)
            _WSL_HOLD_PROC = None
        if _WSL_HOLD_THREAD is None or not _WSL_HOLD_THREAD.is_alive():
            _WSL_HOLD_THREAD = threading.Thread(target=_watchWslHold, daemon=True,
                                                name="wsl-hold")
            _WSL_HOLD_THREAD.start()


def _watchWslHold() -> None:
    while True:
        with _WSL_HOLD_LOCK:
            proc = _WSL_HOLD_PROC
        if proc is None:
            _ensureWslHold()
            proc = _WSL_HOLD_PROC
            if proc is None:
                time.sleep(1.0)
                continue
        rc = proc.wait()
        _stake("--- WSL hold exited rc=%s; re-arming ---" % rc)
        with _WSL_HOLD_LOCK:
            if _WSL_HOLD_PROC is proc:
                _WSL_HOLD_PROC = None
        time.sleep(1.0)


# L191-237：_wsl_prewarm — 启动 WSL + 持有会话

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



















        _ensureWslHold()

        _stake("--- WSL prewarm: session ready; gateway wake deferred until startup barrier ---")
        _stake("--- WSL prewarm done ---")
    except Exception as _e:
        _stake("WSL prewarm failed (non-fatal): %s" % _e)


# L188-230：_wake_gateway — 所有启动组件到位后，才执行 dashboard + gateway start。
def _wake_gateway(on_done=None, on_start=None) -> None:
    r"""所有启动组件到位后，才执行 dashboard + gateway start。"""




    try:
        if callable(on_start):
            try:
                on_start()
            except Exception:
                pass
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        _stake("--- gateway wake: dashboard --yes --no-open + gateway start ---")
        _distro = os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu"
        _argv = ["wsl", "-d", _distro, "-u", "root", "--", "bash", "-lc",
                 "export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; "
                 "openclaw dashboard --yes --no-open; "
                 "openclaw gateway start"]
        try:
            _r = subprocess.run(
                _argv, capture_output=True, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=si,
            )
            _drc = int(_r.returncode)
            _dout = (_r.stdout or "") + (("\n" + _r.stderr) if _r.stderr else "")
        except Exception as _de:
            _drc, _dout = 1, "%s: %s" % (type(_de).__name__, _de)
        _stake("--- gateway wake: rc=%s out=%s ---" % (_drc, (_dout or "")[:300]))
        if callable(on_done):
            try:
                on_done(_drc == 0)
            except Exception:
                pass
    except Exception as _e:
        _stake("gateway wake failed (non-fatal): %s" % _e)
        if callable(on_done):
            try:
                on_done(False)
            except Exception:
                pass



_stake("============================================================")
_stake("RUNTIME START  pid=%s  cwd=%s  python=%s" % (os.getpid(), os.getcwd(), sys.executable))
_stake("sys.argv: %s" % (sys.argv,))
_stake("sys.path head: %s" % (sys.path[:3],))

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SLOT_RUNTIME_DIR = os.path.join(_project_root, "personal-slot")
if os.path.isdir(_SLOT_RUNTIME_DIR) and _SLOT_RUNTIME_DIR not in sys.path:
    sys.path.insert(0, _SLOT_RUNTIME_DIR)
def _spawn_tlc_supervisor() -> None:
    """壳启动时同步拉起进程外 TLC 守护：只等真实退出信号，不重启、不超时。"""
    _exe = os.path.join(_project_root, "tools", "tlcSupervisor.exe")
    if not os.path.isfile(_exe):
        _stake("tlc supervisor missing: %s" % _exe)
        return
    try:
        _log = os.path.join(_STAKE_DIR, "tlc_supervisor.log")
        _flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                  | getattr(subprocess, "CREATE_NO_WINDOW", 0))
        _p = subprocess.Popen(
            [_exe, "--pid", str(os.getpid()), "--log", _log],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=_flags, close_fds=True,
        )
        _stake("tlc supervisor started pid=%s log=%s" % (_p.pid, _log))
    except Exception as _e:
        _stake("tlc supervisor start failed: %s: %s" % (type(_e).__name__, _e))


_stake("_project_root = %s  (exists=%s)" % (_project_root, os.path.isdir(_project_root)))

_ICO_CANDIDATES = [
    os.path.join(_project_root, "Rheia.ico"),
    os.path.join(_project_root, "extract", "Rheia.ico"),
]

if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
    _stake("inserted _project_root into sys.path[0]")



_runtime_dir = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "PythonSide6")
_site_packages = os.path.join(_runtime_dir, "Lib", "site-packages")
_stake("_site_packages = %s  (exists=%s)" % (_site_packages, os.path.isdir(_site_packages)))
if os.path.isdir(_site_packages) and _site_packages not in sys.path:
    sys.path.insert(0, _site_packages)
    _stake("inserted _site_packages into sys.path[0]")



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


_qt6_dlls = ["Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "Qt6WebEngineCore.dll"]
for _dll in _qt6_dlls:
    _dll_path = os.path.join(_pyside6_dir, _dll) if os.path.isdir(_pyside6_dir) else ""
    _stake("  %-30s %s" % (_dll, "FOUND" if (_dll_path and os.path.exists(_dll_path)) else "MISSING"))


_stake("--- import stage 1: PySide6.QtCore ---")
try:
    from PySide6.QtCore import Qt, QCoreApplication
    _stake("  PySide6.QtCore.Qt OK")



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







# L336-354：_install_shell_log — _install_shell_log
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
    except Exception:
        pass


_install_shell_log()

WSL_DISTRO = os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu"
GATEWAY_PORT = 18789
GATEWAY_URL = f"http://127.0.0.1:{GATEWAY_PORT}"

_stake("--- module-level imports complete ---")


# L367-398：_read_glory — 读 gateway 配置，返回 (url, token)。
def _read_glory() -> tuple[str, str]:
    r"""读 gateway 配置，返回 (url, token)。"""





    url = GATEWAY_URL
    token = ""

    try:
        from kernel.scout import readGatewayAuth
        auth = readGatewayAuth(WSL_DISTRO)
        if auth.get("ok") and auth.get("token"):
            token = auth.get("token", "")
            if auth.get("url"):
                url = auth.get("url", url)
    except Exception:
        logger.debug("auto-read gateway token from WSL failed")

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


# L402-409：refresh_token — 网关重启后重新取 token（5ea9ac4 的取法，壳层唯一刷新入口）。
def refresh_token() -> str:
    r"""网关重启后重新取 token（5ea9ac4 的取法，壳层唯一刷新入口）。"""
    _url, _tok = _read_glory()
    try:
        _stake("gw.token.refresh token=%s" % ("present" if _tok else "MISSING"))
    except Exception:
        pass
    return _tok



_SINGLETON_HANDLE = None
_SINGLETON_LOCK_FH = None



_SINGLETON_MUTEX_NAME = "Global\\Rheia.Shell.Singleton"
_SINGLETON_LOCK_NAME = "shell.lock"


# L423-487：_acquire_single_instance — 只允许一个壳实例在跑；第二个实例直接退出。
def _acquire_single_instance() -> bool:
    r"""只允许一个壳实例在跑；第二个实例直接退出。"""















    global _SINGLETON_HANDLE, _SINGLETON_LOCK_FH
    if sys.platform != "win32":
        return True


    _handover_wait()


    try:
        import ctypes
        _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _h = _k32.CreateMutexW(None, False, _SINGLETON_MUTEX_NAME)
        _err = ctypes.get_last_error()
        if _h:
            if _err == 183:



                try:
                    _k32.CloseHandle(_h)
                except Exception as _ce:

                    _stake("单实例守卫：关掉多余的互斥体句柄失败（无害）：%s" % _ce)
                _stake("!!! 已有壳实例（互斥体）：本实例退出，不碰网关 !!!")
                return False
            _SINGLETON_HANDLE = _h
    except Exception as e:
        _stake(f"单实例守卫：互斥体不可用（继续看锁文件）：{e}")


    try:
        import msvcrt
        _p = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia", _SINGLETON_LOCK_NAME)
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        _fh = open(_p, "a+")
        try:
            msvcrt.locking(_fh.fileno(), msvcrt.LK_NBLCK, 1)
            _SINGLETON_LOCK_FH = _fh
        except OSError:
            try:
                _fh.close()
            except Exception:
                pass
            _stake("!!! 已有壳实例（锁文件被占）：本实例退出，不碰网关 !!!")
            return False
    except Exception as e:
        _stake(f"单实例守卫：锁文件不可用（放行）：{e}")
    return True



_HANDOVER_ENV = "RHEIA_HANDOVER_PID"






# L499-571：_handover_wait — 自愈重启交棒：新进程阻塞等"门"（单实例互斥体）—— 老进程一退，OS 唤醒。
def _handover_wait() -> None:
    r"""自愈重启交棒：新进程阻塞等"门"（单实例互斥体）—— 老进程一退，OS 唤醒。"""
















    _raw = os.environ.get(_HANDOVER_ENV, "").strip()
    if not _raw.isdigit():
        return
    _pid = int(_raw)



    _state = "门已让出，进门"
    try:



        import ctypes
        _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _h = _k32.OpenProcess(0x00100000, False, _pid)
        if _h:
            try:
                _k32.WaitForSingleObject(_h, 0xFFFFFFFF)
            finally:
                _k32.CloseHandle(_h)
    except Exception as _e:






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
                    except Exception:
                        pass
                except OSError:
                    _free = False
        except Exception as _pe:
            _free = None
            _e = "%s；锁文件确认也失败：%s" % (_e, _pe)
        if _free is True:
            _state = "等门异常（%s）⇒ 锁文件说门空着 ⇒ 进门" % _e
        elif _free is False:
            _state = "等门异常（%s）⇒ 锁文件被占（老进程还在）⇒ 不进门，交给门去拒" % _e
        else:
            _state = "等门异常（%s）⇒ 锁文件确认不了 ⇒ 照走（由门兜底）" % _e
    _stake("交棒：%s（老进程 pid=%d）" % (_state, _pid))

    os.environ.pop(_HANDOVER_ENV, None)


# L575-600：_release_single_instance — 主动让出单实例门（给"进程继续活着、但门要让出去"的场合）。
def _release_single_instance() -> None:
    r"""主动让出单实例门（给"进程继续活着、但门要让出去"的场合）。"""










    global _SINGLETON_HANDLE, _SINGLETON_LOCK_FH
    if _SINGLETON_LOCK_FH is not None:
        try:
            _SINGLETON_LOCK_FH.close()
        except Exception:
            pass
        _SINGLETON_LOCK_FH = None
    if _SINGLETON_HANDLE is not None:
        try:
            import ctypes
            ctypes.WinDLL("kernel32").CloseHandle(_SINGLETON_HANDLE)
        except Exception:
            pass
        _SINGLETON_HANDLE = None









# L611-646：_spawn_boot_worker — 起「内核重活」子进程（main/boot_worker.py），返回 Popen；起不来就抛给调用方。
def _spawn_boot_worker(mark_path: str):
    r"""起「内核重活」子进程（main/boot_worker.py），返回 Popen；起不来就抛给调用方。"""





    _worker = os.path.join(_project_root, "main", "boot_worker.py")
    for _stale in (mark_path, mark_path + ".tmp"):
        try:
            os.remove(_stale)
        except OSError:
            pass
    _env = dict(os.environ)
    _env["PYTHONPATH"] = _project_root + os.pathsep + _SLOT_RUNTIME_DIR + os.pathsep + _env.get("PYTHONPATH", "")
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
    except Exception as _be:
        _stake("boot_worker bind-to-parent failed (non-fatal): %s" % _be)
    return _proc


# L650-657：_read_boot_marker — 读子进程落的结论标记；读不到 / 半截 / 坏掉 一律当「还没有结论」（不抛、不猜）。
def _read_boot_marker(mark_path: str) -> dict:
    r"""读子进程落的结论标记；读不到 / 半截 / 坏掉 一律当「还没有结论」（不抛、不猜）。"""
    try:
        with open(mark_path, "r", encoding="utf-8") as f:
            _data = json.load(f)
        return _data if isinstance(_data, dict) else {}
    except Exception:
        return {}


# L681-746：_showFirstRunSupport — 安装结束首启 30 秒；之后每次开机 15 秒；同一开机只出现一次。
def _bootId() -> str:
    r"""返回本次 Windows 开机的稳定标识。"""
    try:
        import ctypes

        class _FT(ctypes.Structure):
            _fields_ = [("dwLowDateTime", ctypes.c_uint32),
                        ("dwHighDateTime", ctypes.c_uint32)]

        _k = ctypes.WinDLL("kernel32", use_last_error=True)
        _ft = _FT()
        _k.GetSystemTimeAsFileTime.argtypes = [ctypes.POINTER(_FT)]
        _k.GetSystemTimeAsFileTime(ctypes.byref(_ft))
        _k.GetTickCount64.restype = ctypes.c_ulonglong
        _now = (int(_ft.dwHighDateTime) << 32) | int(_ft.dwLowDateTime)
        _ticks = int(_k.GetTickCount64())
        return "win-%d" % (_now - _ticks * 10000)
    except Exception:
        return ""


def _showFirstRunSupport(browser) -> None:
    r"""安装结束首启 30 秒；之后每次开机 15 秒；同一开机只出现一次。"""
    try:
        import json as _json
        import os as _os
        from PySide6.QtCore import QTimer as _QTimer
        _pd = _os.path.join(_os.environ.get("PROGRAMDATA", ""), "Rheia")
        # 只有安装了 Slot 才隐藏支持页；仅安装壳必须保留 30 秒 / 每次开机 15 秒。
        _app = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        if _os.path.isfile(_os.path.join(_app, "personal-slot", "openclaw.plugin.json")):
            return
        _state = _os.path.join(_pd, "support_state.json")
        _pending = _os.path.join(_pd, "support_install_pending.json")
        _boot = _bootId()
        _old = {}
        try:
            with open(_state, "r", encoding="utf-8") as _f:
                _old = _json.load(_f) or {}
        except Exception:
            _old = {}
        _install = _os.path.exists(_pending)
        if _install:
            # 同一开机已经完成过这次安装后的 30 秒：清掉安装标记，不再重复。
            if _boot and _old.get("boot") == _boot and _old.get("install30"):
                try:
                    _os.remove(_pending)
                except OSError:
                    pass
                return
            _seconds = 30
        else:
            if _boot and _old.get("boot") == _boot:
                return
            _seconds = 15
        _page = _os.path.join(
            _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
            "ui", "web", "support_page.py",
        )
        if not _os.path.isfile(_page):
            return

        def _open() -> None:
            _ok = False
            try:
                from ui.web.support_page import SupportPanel
                _panel = SupportPanel(parent=browser, first_run=True, seconds=_seconds)
                _panel.exec()
                _ok = True
            except Exception as _e:
                _stake("support.first_run.failed: %s: %s" % (type(_e).__name__, _e))
            finally:
                if _ok:
                    try:
                        _os.makedirs(_pd, exist_ok=True)
                        _tmp = _state + ".tmp"
                        with open(_tmp, "w", encoding="utf-8") as _f:
                            _json.dump({"boot": _boot, "install30": bool(_install)}, _f)
                        _os.replace(_tmp, _state)
                        if _install:
                            _os.remove(_pending)
                    except Exception:
                        pass

        _QTimer.singleShot(0, _open)
    except Exception as _e:
        _stake("support.first_run.skipped: %s: %s" % (type(_e).__name__, _e))


def main():
    _stake("--- main() entered ---")


    if not _acquire_single_instance():
        return


    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Rheia.Browser")
        except Exception as _ae:

            _stake("AppUserModelID 设置失败（只影响任务栏图标分组）：%s" % _ae)

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    _stake("QApplication created")

    def _fatal_box(msg: str) -> None:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(None, "Rheia", msg)



    def _reject_text() -> str:
        return "本ソフトウェアは日本語をサポートしていません。インストーラーは自己破壊します。"




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

        try:
            self_destruct()
        except Exception as _e:
            _stake("shell self_destruct dispatch failed: %s" % _e)
        sys.exit(0)




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


    if sys.platform == "win32":
        app.setStyle("Fusion")
        app.setPalette(qtDarkPalette())

        try:
            from PySide6.QtGui import QFont
            _font = QFont("Microsoft YaHei UI", 9)
            app.setFont(_font)
            _stake("global font set: Microsoft YaHei UI")
        except Exception as _fe:
            _stake("global font set FAILED: %s" % _fe)
        _stake("Fusion + dark palette applied")


    try:
        install_crash_hooks()
        _stake("crash hooks installed (sys+threading+qt)")
        _spawn_tlc_supervisor()
    except Exception as _e:
        _stake("crash hooks install FAILED: %s" % _e)




    try:
        import faulthandler as _fh_mod
        _fh_path = os.path.join(_STAKE_DIR, "faulthandler.log")
        os.makedirs(_STAKE_DIR, exist_ok=True)
        _fh_file = open(_fh_path, "a", encoding="utf-8", buffering=1)
        _fh_mod.enable(file=_fh_file, all_threads=True)
        _stake("faulthandler enabled: %s" % _fh_path)
    except Exception as _e:
        _stake("faulthandler enable FAILED: %s" % _e)


    url, token = _read_glory()
    if not token:


        try:
            subprocess.run(["wsl.exe", "-d", WSL_DISTRO, "--", "echo", "ok"],
                           capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            url, token = _read_glory()
            _stake("gateway token retry after WSL warm: %s" % bool(token))
        except Exception as _we:
            _stake("gateway token retry failed: %s" % _we)
    _stake("gateway url=%s token=%s" % (url, bool(token)))
    stk("gw.token", f"token={'present' if token else 'MISSING'}", ok=bool(token))
    logger.info("Gateway: url=%s token=%s", url, bool(token))


    _stake("--- ensureRecoveryTables ---")
    try:
        from kernel.scout import ensureRecoveryTables
        ensureRecoveryTables()
        _stake("  Recovery tables ensured OK")
    except Exception as _e:
        _stake("  Recovery tables ensure FAILED: %s" % _e)



    _startup_pending = {"browser": False, "bootstrap": False, "md": False}
    _startup_lock = threading.Lock()
    _startup_wake_started = False

    def _startup_arrived(part: str) -> None:
        nonlocal _startup_wake_started
        try:
            with _startup_lock:
                _startup_pending[part] = True
                if not all(_startup_pending.values()) or _startup_wake_started:
                    return
                _startup_wake_started = True
            _stake("--- startup barrier complete: %s ---" % _startup_pending)
            threading.Thread(
                target=_wake_gateway,
                kwargs={
                    "on_start": lambda: browser.gateway_wake_started.emit(),
                    "on_done": lambda ok: browser.gateway_wake_finished.emit(bool(ok)),
                },
                daemon=True, name="gateway-wake",
            ).start()
        except Exception as _be:
            _stake("startup barrier failed (non-fatal): %s" % _be)


    _stake("--- constructing BuiltinBrowser ---")
    from ui.web.browser import BuiltinBrowser
    browser = BuiltinBrowser(gateway_url=url, token=token, start_page="/chat")
    _stake("BuiltinBrowser constructed, calling show()...")
    browser.show()
    _stake("browser.show() done")
    _showFirstRunSupport(browser)
    _startup_arrived("browser")



    try:
        from platform.slotApi import setStake as _setStake, setInjectPlugins as _setInjectPlugins
        _setStake(stk)
        _setInjectPlugins(_inject_local_plugins)
    except Exception as _wire_e:
        _stake("persona callback wiring skipped: %s" % _wire_e)
    try:
        import threading as _th_md
        from platform.slotApi import _reconcile_md_watch as _rmw
        _th_md.Thread(target=lambda: _rmw("壳启动补扫"), daemon=True,
                      name="md-sync-boot").start()
    except Exception as _e_md:
        _stake("md-sync boot reconcile skipped: %s" % _e_md)
    finally:
        _startup_arrived("md")


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



    from PySide6.QtCore import QFileSystemWatcher, QObject, Signal



    _BOOT_MARK_PATH = os.path.join(_STAKE_DIR, "boot_worker.json")
    boot_state = {"proc": None, "failed": False}
    boot_seen: set = set()

    class _BootSignals(QObject):
        changed = Signal()
        exited = Signal()

    _boot_sig = _BootSignals()

    def _bootstrap_deferred():
        r"""后台线程：探活→人格→TLC 早启；内核重活整段交给子进程（壳不再被拖住）。"""
        _stake("--- bootstrap deferred start ---")



        _stake("--- gateway probe deferred to startup barrier ---")


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
            from platform.slotApi import injectPersona
            persona_name = injectPersona()
            if persona_name:
                _stake("persona loaded: %s" % persona_name)
                logger.info("Persona loaded: %s", persona_name)
                stk("persona.loaded", f"name={persona_name}", ok=True)
        except Exception as e:
            _stake("persona_injector skipped (non-fatal): %s" % e)
            logger.warning("persona_injector skipped (non-fatal): %s", e)






        try:


            from platform.slotApi import declare_tlc_consumer as _decl_tlc
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







        _stake("--- kernel pipeline: handing off to boot_worker subprocess ---")
        try:
            boot_state["proc"] = _spawn_boot_worker(_BOOT_MARK_PATH)
            _stake("boot_worker spawned pid=%s" % boot_state["proc"].pid)

            def _wait_boot_proc(proc):
                proc.wait()
                _boot_sig.exited.emit()

            threading.Thread(target=_wait_boot_proc, args=(boot_state["proc"],),
                             daemon=True, name="boot-worker-wait").start()
        except Exception as e:
            boot_state["failed"] = True
            _stake("boot_worker spawn FAILED (non-fatal): %s" % e)
            stk("boot.worker", "spawn failed: %s" % e, ok=False)
            _boot_sig.exited.emit()

        _stake("--- bootstrap deferred done (bg thread) ---")
        _startup_arrived("bootstrap")

    def _bg_bootstrap() -> None:
        r"""后台线程入口：跑启动期轻活并发起重活子进程。"""
        try:
            _bootstrap_deferred()
        except Exception:
            _stake("_bootstrap_deferred crashed in bg thread!")
            _stake(traceback.format_exc())
            _startup_arrived("bootstrap")


    threading.Thread(target=_bg_bootstrap, daemon=True, name="bootstrap-bg").start()



    def _after_kernel_pipeline() -> None:
        r"""管线跑完才做的轻活：按子进程刚重写过的 mani 起 tlc 守护 + 重载车道。"""
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
        r"""读子进程落的结论；只做 UI 动作。子进程没起/中途死 ⇒ 如实记账 + 放行预装。"""
        _proc = boot_state["proc"]
        if _proc is None:
            if not boot_state["failed"] or "self_heal" in boot_seen:
                return
            _stake("boot_worker never started — 放行预装（防永久挂起，沿用旧兜底）")
            boot_seen.add("self_heal")
            try:
                browser.self_heal_done.emit()
            except Exception as _emit_e:
                _stake("self_heal done emit failed: %s" % _emit_e)
            return
        _mark = _read_boot_marker(_BOOT_MARK_PATH)
        if _mark.get("pipeline_done") and "pipeline" not in boot_seen:

            boot_seen.add("pipeline")
            _after_kernel_pipeline()
        _sh = _mark.get("self_heal")
        if isinstance(_sh, dict) and _sh.get("done") and "self_heal" not in boot_seen:
            boot_seen.add("self_heal")
            if _sh.get("healed"):

                stk("self_heal", "纠正了偏差，请求重启（子进程判定）", ok=True)
                try:
                    browser.self_heal_restart.emit()
                except Exception as _emit_e:
                    _stake("self_heal emit failed: %s" % _emit_e)
            else:

                try:
                    browser.self_heal_done.emit()
                except Exception as _emit2_e:
                    _stake("self_heal done emit failed: %s" % _emit2_e)
            return
        if _proc.poll() is not None:

            boot_seen.add("self_heal")
            _stake("boot_worker exited without self_heal verdict: rc=%s" % _proc.returncode)
            if not _mark.get("pid"):
                _stake("  （标记里连启动那一笔都没有 ⇒ 子进程在导入/起飞阶段就没活下来）")
            stk("boot.worker", "exited without verdict rc=%s" % _proc.returncode, ok=False)
            try:
                browser.self_heal_done.emit()
            except Exception as _emit3_e:
                _stake("self_heal done emit failed: %s" % _emit3_e)


    _boot_watcher = QFileSystemWatcher()
    if os.path.isdir(_STAKE_DIR):
        _boot_watcher.addPath(_STAKE_DIR)
    _boot_watcher.directoryChanged.connect(lambda _p: _boot_marker_tick())
    _boot_watcher.fileChanged.connect(lambda _p: _boot_marker_tick())
    _boot_sig.exited.connect(_boot_marker_tick)
    _boot_marker_tick()




    _stake("--- entering app.exec() ---")
    sys.exit(app.exec())














import os
import sys
import traceback
from datetime import datetime

_STAKELOG_DIR = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia")
_STAKELOG_PATH = os.path.join(_STAKELOG_DIR, "stake.log")

# L1064-1065：_now — _now
def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# L1068-1082：stk — 写一行桩子日志。
def stk(tag: str, msg: str = "", ok: bool = True) -> None:
    r"""写一行桩子日志。"""





    try:
        os.makedirs(_STAKELOG_DIR, exist_ok=True)
        flag = " OK" if ok else "FAIL"
        line = f"[{_now()}] [{tag}] {flag} {msg}\n"
        with open(_STAKELOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass





_original_excepthook = None
_original_unraisablehook = None
_original_thread_excepthook = None

# L1093-1106：_write_crash — 将崩溃信息写入 stake.log，格式紧凑但关键信息完整。
def _write_crash(label: str, etype: type, value: BaseException, tb) -> None:
    r"""将崩溃信息写入 stake.log，格式紧凑但关键信息完整。"""
    try:
        os.makedirs(_STAKELOG_DIR, exist_ok=True)
        tb_lines = traceback.format_exception(etype, value, tb)
        tb_compact = "".join(tb_lines[-4:])
        line = (
            f"[{_now()}] [{label}] CRASH {etype.__name__}: {value}\n"
            f"{tb_compact.rstrip()}\n"
        )
        with open(_STAKELOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass

# L1109-1121：_global_excepthook — Python 全局未捕获异常 → stk + 保持原有行为。
def _global_excepthook(etype, value, tb):
    r"""Python 全局未捕获异常 → stk + 保持原有行为。"""



    _write_crash("python.crash", etype, value, tb)
    try:
        from main.tlc import report_crash
        report_crash("shell_crash")
    except Exception:
        pass
    if _original_excepthook:
        _original_excepthook(etype, value, tb)

# L1124-1132：_unraisablehook — sys.unraisablehook：对象析构等处的不可达异常 → stk。
def _unraisablehook(args):
    r"""sys.unraisablehook：对象析构等处的不可达异常 → stk。"""
    etype = args.exc_type
    value = args.exc_value
    tb = args.exc_traceback
    if etype and value:
        _write_crash("unraisable.crash", etype, value, tb)
    if _original_unraisablehook:
        _original_unraisablehook(args)

# L1135-1143：_thread_excepthook — threading.excepthook：线程内未捕获异常 → stk（threading.Thread 默认直接死，不被 excepthook 覆盖）。
def _thread_excepthook(args):
    r"""threading.excepthook：线程内未捕获异常 → stk（threading.Thread 默认直接死，不被 excepthook 覆盖）。"""
    etype = args.exc_type
    value = args.exc_value
    tb = args.exc_traceback
    if etype and value:
        _write_crash("thread.crash", etype, value, tb)
    if _original_thread_excepthook:
        _original_thread_excepthook(args)

# L1146-1171：install_crash_hooks — 安装全局异常钩子。放在 main() 开头，QApplication 创建后调用。
def install_crash_hooks() -> None:
    r"""安装全局异常钩子。放在 main() 开头，QApplication 创建后调用。"""






    global _original_excepthook, _original_unraisablehook, _original_thread_excepthook


    _original_excepthook = sys.excepthook
    sys.excepthook = _global_excepthook


    if hasattr(sys, "unraisablehook"):
        _original_unraisablehook = getattr(sys, "unraisablehook", None)
        sys.unraisablehook = _unraisablehook


    import threading
    if hasattr(threading, "excepthook"):
        _original_thread_excepthook = threading.excepthook
        threading.excepthook = _thread_excepthook

    stk("stake.crash_hooks", "installed (sys+threading+qt)")














import json
import os

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MANI_PATH = os.path.join(_PROJ, "kernel", "large", "mani.json")

_cache: dict | None = None






_ANCHOR_COMMANDS: dict[tuple[str, str], str] = {
    ("config", "set"): "openclaw config set",
    ("config", "patch"): "openclaw config patch",
    ("config", "get"): "openclaw config get",
    ("sessions", "list"): "openclaw sessions",
    ("openclaw", "version"): "openclaw --version",


    ("doctor", "fix"): "openclaw doctor --fix",
    ("doctor", "repair"): "openclaw doctor --fix",










    ("gateway", "restart"): "openclaw gateway restart",
    ("gateway", "status"): "openclaw gateway status",
    ("gateway", "start"): "openclaw gateway start",
    ("gateway", "stop"): "openclaw gateway stop",


    ("plugins", "reload"): "openclaw plugins reload",





    ("dashboard", "open"): "openclaw dashboard",
}

# L1235-1247：_load_clis — _load_clis
def _load_clis() -> list[dict]:
    global _cache
    if _cache is None:
        _cache = {}
        try:
            with open(_MANI_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            _cache = (data.get("entries") or {}).get("clis") or []
        except (OSError, json.JSONDecodeError) as _e:

            logger.warning("mani.json 读/解析失败（CLI 索引退化为空表）：%s", _e)
            _cache = []
    return _cache

# L1250-1272：cli_usage — 从 mani 的 clis 查「cli_id 这个命令的 action 动作」完整命令行。
def cli_usage(cli_id: str, action: str, option: str = "") -> str:
    r"""从 mani 的 clis 查「cli_id 这个命令的 action 动作」完整命令行。"""




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


    anchor = _ANCHOR_COMMANDS.get((cli_id, action))
    if anchor:
        return f"{anchor} {option}".strip() if option else anchor
    return ""










import json
import os
import subprocess
import tempfile
import threading
import time
from typing import Any

WSL_DISTRO = os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu"




_cache: dict[str, Any] | None = None
_cache_lock = threading.Lock()
_CACHE_TTL_SEC = 5.0
_cache_time: float = 0.0

# L1302-1307：invalidate_cache — 写配置后失效缓存（write 内部已调用，供外部强制刷新）。
def invalidate_cache() -> None:
    r"""写配置后失效缓存（write 内部已调用，供外部强制刷新）。"""
    global _cache, _cache_time
    with _cache_lock:
        _cache = None
        _cache_time = 0.0

# L1310-1324：_wc_run_wsl — 在 WSL 中执行一条命令。静默，无弹窗。
def _wc_run_wsl(cmd: str) -> tuple[int, str, str]:
    r"""在 WSL 中执行一条命令。静默，无弹窗。"""



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

# L1327-1333：win_to_wsl — Windows 路径 → WSL /mnt/ 路径。
def win_to_wsl(win: str) -> str:
    r"""Windows 路径 → WSL /mnt/ 路径。"""
    if not win or len(win) < 2:
        return win
    if win[1] == ":":
        return f"/mnt/{win[0].lower()}{win[2:].replace(chr(92), '/')}"
    return win.replace("\\", "/")

# L1336-1362：read — 读取 WSL ~/.openclaw/openclaw.json 为 dict。文件不存在或解析失败返回空。
def read() -> dict[str, Any]:
    r"""读取 WSL ~/.openclaw/openclaw.json 为 dict。文件不存在或解析失败返回空。"""




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


            logger.warning("openclaw.json 解析失败（退回空 dict，写回有覆盖风险）：%s | %s",
                           _je, out.strip()[:200])
            result = {}
    with _cache_lock:
        _cache = result
        _cache_time = time.time()
    return result

# L1365-1395：write — 直接覆写 WSL ~/.openclaw/openclaw.json，不走 CLI。
def write(cfg: dict[str, Any]) -> bool:
    r"""直接覆写 WSL ~/.openclaw/openclaw.json，不走 CLI。"""




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


























import os
import sys
from datetime import datetime, timezone


_JA_LANG_IDS = {0x11, 0x411, 0xC11}


_JA_LOCALE_VALUES = {"ja", "ja-jp", "ja_jp"}


_JA_TIMEZONE = "Asia/Tokyo"


_LOG_DIR = os.path.join(
    os.environ.get("PROGRAMDATA", ""), "Rheia", "log", "shell_i18n_guard"
)

# L1441-1452：_detect_windows_lang — 检测源 1：Windows 显示语言是否日语。
def _detect_windows_lang() -> bool:
    r"""检测源 1：Windows 显示语言是否日语。"""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        lang_id = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())
        return lang_id in _JA_LANG_IDS
    except Exception as _le:

        _stake("i18n_guard：读系统 UI 语言失败（按非日语放行）：%s" % _le)
        return False


_ZH_CN_LANG_IDS = {0x0804, 0x0004}

# L1458-1471：is_simplified_chinese — 检测当前系统是否为简体中文（zh-CN）。
def is_simplified_chinese() -> bool:
    r"""检测当前系统是否为简体中文（zh-CN）。"""




    if sys.platform != "win32":
        return False
    try:
        import ctypes
        lang_id = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())
        return lang_id in _ZH_CN_LANG_IDS
    except Exception:
        return False

# L1474-1494：_detect_wsl_locale — 检测源 2：已拉起 WSL 发行版内 LANG/LC_ALL 是否 ja_JP.（只读探测，不写）。
def _detect_wsl_locale() -> bool:
    r"""检测源 2：已拉起 WSL 发行版内 LANG/LC_ALL 是否 ja_JP.（只读探测，不写）。"""
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

        _stake("i18n_guard：探测 WSL locale 失败（该源不参与判定）：%s" % _le)
        return False

# L1497-1500：_detect_env_override — 检测源 3：RHEIA_LOCALE 是否被置为 ja。
def _detect_env_override() -> bool:
    r"""检测源 3：RHEIA_LOCALE 是否被置为 ja。"""
    v = os.environ.get("RHEIA_LOCALE", "").strip().lower()
    return v in _JA_LOCALE_VALUES

# L1503-1522：_detect_tz — 佐证：系统时区是否 Asia/Tokyo。
def _detect_tz() -> bool:
    r"""佐证：系统时区是否 Asia/Tokyo。"""
    if sys.platform != "win32":
        return False
    try:


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

        _stake("i18n_guard：读时区佐证失败（不据此判定）：%s" % _te)
        return False

# L1525-1538：_write_log — 写安全日志（规范 §6）：时间戳 UTC + 命中源 + 动作 + 结果，不含身份信息。
def _write_log(state: str, sources: list[str], action: str) -> None:
    r"""写安全日志（规范 §6）：时间戳 UTC + 命中源 + 动作 + 结果，不含身份信息。"""
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

# L1541-1594：check_detailed — 检测不支持的语言环境，返回 (state, hits)。
def check_detailed(probeWsl: bool = True) -> tuple[str, list[str]]:
    r"""检测不支持的语言环境，返回 (state, hits)；probeWsl=False 时只查 Windows/env/tz。"""







    hits: list[str] = []
    if _detect_windows_lang():
        hits.append("lang_id")




    else:
        if probeWsl and _detect_wsl_locale():
            hits.append("wsl_locale")
    if _detect_env_override():
        hits.append("env")

    tz_hit = _detect_tz()





    if "env" in hits and "lang_id" not in hits and "wsl_locale" not in hits:
        _write_log("japanese", ["env"], "bypass")
        return "japanese", ["env"]






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

# L1597-1603：check — 检测不支持的语言环境，返回三态之一：japanese / suspected / none。
def check(probeWsl: bool = True) -> str:
    r"""检测不支持的语言环境，返回三态之一：japanese / suspected / none。"""
    state, _ = check_detailed(probeWsl=probeWsl)
    return state

# L1606-1611：write_audit_log — 公共审计日志入口（供 shell_self_destruct 等复用 in0001 的日志函数）。
def write_audit_log(action: str, sources: list[str], state: str = "japanese") -> None:
    r"""公共审计日志入口（供 shell_self_destruct 等复用 in0001 的日志函数）。"""



    _write_log(state, sources, action)


REJECT_MSG = "本产品不支持日语语言环境"

if __name__ == "__main__":

    state = check()
    print(f"i18n_guard.check() = {state}")





















import os
import shutil
import subprocess


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


_SHELL_DIRS = [
    "avatar",
    "avatars",
]





_SHELL_APP_MARKERS = [
    os.path.join("main", "main.py"),
    os.path.join("main", "license_anchor.py"),
]

# L1680-1681：_progdata — _progdata
def _progdata() -> str:
    return os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia")

# L1684-1690：_delete_file_if_exists — _delete_file_if_exists
def _delete_file_if_exists(path: str) -> None:
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError as _de:

        logger.warning("自毁：删除壳层文件失败（继续）：%s (%s)", path, _de)

# L1693-1710：_delete_progdata_shell_files — 文件级白名单删除 ProgramData 壳层数据。
def _delete_progdata_shell_files() -> None:
    r"""文件级白名单删除 ProgramData 壳层数据。"""



    base = _progdata()
    for name in _SHELL_FILES:
        _delete_file_if_exists(os.path.join(base, name))
    for name in _SHELL_DIRS:
        d = os.path.join(base, name)
        if os.path.isdir(d):



            try:
                shutil.rmtree(d, ignore_errors=True)
            except OSError:
                pass

# L1713-1733：_resolve_app_dir — 解析 {app} 安装目录的绝对路径，并通过归属校验。
def _resolve_app_dir() -> str:
    r"""解析 {app} 安装目录的绝对路径，并通过归属校验。"""









    raw = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        cand = os.path.normpath(os.path.abspath(raw))
    except OSError:
        return ""

    for rel in _SHELL_APP_MARKERS:
        if not os.path.isfile(os.path.join(cand, rel)):
            return ""
    return cand

# L1736-1761：_schedule_app_dir_removal — 提权删除 {app} 安装目录（pythonw 退出后清目录）。
def _schedule_app_dir_removal(app_dir: str) -> None:
    r"""提权删除 {app} 安装目录（pythonw 退出后清目录）。"""








    if not app_dir or not os.path.isdir(app_dir):
        return

    if os.path.normpath(os.path.abspath(app_dir)) != _resolve_app_dir():
        return
    try:


        import ctypes
        args = '/c rmdir /s /q "{}"'.format(app_dir)
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", "cmd.exe", args, None, 0,
        )
    except Exception as _se:

        logger.warning("自毁：提权删 {app} 失败（继续）：%s", _se)

# L1764-1783：self_destruct — 执行壳层自毁（有序，任一失败不阻断后续）。
def self_destruct(app_dir: str | None = None, sources: list[str] | None = None) -> None:
    r"""执行壳层自毁（有序，任一失败不阻断后续）。"""




    _delete_progdata_shell_files()

    if app_dir is None:


        app_dir = _resolve_app_dir()
    _schedule_app_dir_removal(app_dir)


    try:
        write_audit_log("self_destruct", list(sources or []), state="japanese")
    except Exception as _ae:

        logger.warning("自毁：写审计日志失败（继续）：%s", _ae)

if __name__ == "__main__":

    print("shell_self_destruct 自检（不执行）")
    print("  progdata =", _progdata())

























import subprocess
import threading
import time

WSL_DISTRO = os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu"
GATEWAY_PORT = 18789
_NPM_REGISTRY = "https://registry.npmmirror.com"









_lifecycle_guard = threading.Lock()
_lifecycle_owner: str | None = None





_lifecycle_owner_ident: int | None = None

# L1840-1848：_owner_thread_alive — owner 那条线程还活着吗 —— 只认线程活体（不掐表）。
def _owner_thread_alive() -> bool:
    r"""owner 那条线程还活着吗 —— 只认线程活体（不掐表）。"""
    _ident = _lifecycle_owner_ident
    if _ident is None:
        return False
    for _t in threading.enumerate():
        if _t.ident == _ident:
            return _t.is_alive()
    return False

# L1851-1869：acquire_lifecycle — 非阻塞获取 Gateway 生命周期所有权。已有 owner 在管理则返回 False。
def acquire_lifecycle(owner: str) -> bool:
    r"""非阻塞获取 Gateway 生命周期所有权。已有 owner 在管理则返回 False。"""







    global _lifecycle_owner, _lifecycle_owner_ident
    with _lifecycle_guard:
        if _lifecycle_owner is not None:
            if _owner_thread_alive():
                return False

            _lifecycle_owner = None
        _lifecycle_owner = owner
        _lifecycle_owner_ident = threading.get_ident()
        return True

# L1872-1878：release_lifecycle — 释放生命周期所有权（仅当 owner 匹配，防止误释放别人的所有权）。
def release_lifecycle(owner: str) -> None:
    r"""释放生命周期所有权（仅当 owner 匹配，防止误释放别人的所有权）。"""
    global _lifecycle_owner
    with _lifecycle_guard:
        if _lifecycle_owner == owner:
            _lifecycle_owner = None
            _lifecycle_owner_ident = None

# L1881-1895：_gw_run_wsl — 在 WSL 中执行命令，返回 (returncode, stdout, stderr)。
def _gw_run_wsl(cmd: str) -> tuple[int, str, str]:
    r"""在 WSL 中执行命令，返回 (returncode, stdout, stderr)。"""



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

# L1898-1912：_run_official_cmd — 从 mani 长出一条官方命令并执行（逻辑代码生代码，不手抄命令）。
def _run_official_cmd(cli_id: str, action: str, option: str = "") -> tuple[int, str, str]:
    r"""从 mani 长出一条官方命令并执行（逻辑代码生代码，不手抄命令）。"""






    cmd = cli_usage(cli_id, action, option)
    if not cmd:
        return -1, "", f"cli_usage({cli_id!r}, {action!r}) 查不到，跳过"
    return _gw_run_wsl(
        f"wsl -d {WSL_DISTRO} -u root -- bash -c "
        f"\"export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; {cmd} 2>&1\"",
    )

# L1915-1973：_get_node_install_script — Generate Node.js install bash script — single pipeline.
def _get_node_install_script() -> str:
    r"""Generate Node.js install bash script — single pipeline."""






    return (
        'NODE_OFFICIAL="https://nodejs.org/dist"\n'
        'NODE_MIRROR="https://npmmirror.com/mirrors/node"\n'
        'echo "[gateway_recovery] resolving latest Node.js..."\n'

        'ARCH=$(uname -m)\n'
        'case "$ARCH" in\n'
        '  x86_64) NODE_ARCH="x64" ;;\n'
        '  aarch64|arm64) NODE_ARCH="arm64" ;;\n'
        '  armv7l) NODE_ARCH="armv7l" ;;\n'
        '  *) NODE_ARCH="x64" ;;\n'
        'esac\n'


        'TARBALL=$(curl -fsSL '
        '"$NODE_OFFICIAL/latest/" 2>/dev/null | '
        'grep -oP "node-v[0-9]+\\.[0-9]+\\.[0-9]+-linux-${NODE_ARCH}\\.tar\\.xz" | head -1)\n'
        'if [ -z "$TARBALL" ]; then\n'
        '  echo "[gateway_recovery] FATAL: cannot resolve latest Node.js"\n'
        '  exit 1\n'
        'fi\n'

        'TARGET_VER=$(echo "$TARBALL" | grep -oP "[0-9]+\\.[0-9]+\\.[0-9]+")\n'
        'echo "[gateway_recovery] latest: v$TARGET_VER ($TARBALL)"\n'



        'echo "[gateway_recovery] 原破坏性动作（已移除，不再执行）: apt-get remove -y nodejs npm; rm -f /usr/bin/node /usr/bin/npm /usr/bin/npx /usr/local/bin/node /usr/local/bin/npm /usr/local/bin/npx"\n'
        'echo "[gateway_recovery] 该修什么: 覆盖安装为 v$TARGET_VER（如需清旧版本，由 apt/dpkg 或 node 自身处理）"\n'
        'echo "[gateway_recovery] 现状 node: $(node --version 2>/dev/null || echo 未安装)"\n'
        'echo "[gateway_recovery] 现状 npm : $(npm --version 2>/dev/null || echo 未安装)"\n'
        'echo "[gateway_recovery] node 路径: $(command -v node || echo 无)"\n'
        'echo "[gateway_recovery] npm  路径: $(command -v npm || echo 无)"\n'

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

# L1976-2013：repair_node_version — 强制安装 Node.js 最新稳定版（国内镜像优先）。
def repair_node_version(log_callback=None) -> bool:
    r"""强制安装 Node.js 最新稳定版（国内镜像优先）。"""






    def _log(msg: str) -> None:
        if log_callback:
            log_callback(msg)

    _ok, _prev, _why = _chain_prev("node")
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
        try:
            signals().mark_install("node", note="node 安装脚本 rc=0")
        except Exception as _se:
            _log(f"信号：写 node 失败 {_se}")
        return True

    _log(f"Node.js 安装失败 (rc={rc})")
    if err:
        _log(f"  错误: {err[:200]}")
    return False

# L2016-2034：check_openclaw_cli — 检查 openclaw CLI 是否可用（二进制未损坏）。
def check_openclaw_cli() -> bool:
    r"""检查 openclaw CLI 是否可用（二进制未损坏）。"""
    version_cmd = cli_usage("openclaw", "version")
    if not version_cmd:

        _stake("check_openclaw_cli：mani 查不到 openclaw version 命令 ⇒ 跳过检查")
        return False
    rc, out, _ = _gw_run_wsl(
        f"wsl -d {WSL_DISTRO} -u root -- bash -c "
        f"\"export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; {version_cmd} 2>&1\"",
    )
    ok = rc == 0 and out.strip() != ""
    if ok:
        try:
            signals().mark_install("node", note="openclaw --version 跑得起来（node 在）")
            signals().mark_install("cli", note=f"openclaw --version: {out.strip()[:60]}")
        except Exception as _se:
            _stake("信号：写 node/cli 失败 %s" % _se)
    return ok

# L2037-2043：check_gateway_alive — 探活 Gateway 18789 端口。
def check_gateway_alive() -> bool:
    r"""探活 Gateway 18789 端口。"""
    rc, out, _ = _gw_run_wsl(
        f"wsl -d {WSL_DISTRO} -u root -- bash -c "
        f"\"curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{GATEWAY_PORT} 2>/dev/null\"",
    )
    return rc == 0 and out.strip() in ("200", "302", "401", "403")

# L2046-2069：_gateway_signal — Gateway 段信号：按官方探针问一次，把结果记进信号账，返回记下来的 ready。
def _gateway_signal() -> bool:
    r"""Gateway 段信号：按官方探针问一次，把结果记进信号账，返回记下来的 ready。"""





    try:
        _st, _so, _se = _run_official_cmd("gateway", "status")
        _ok = (_st == 0 and "running" in str(_so or "").lower())
        signals().mark_gateway(_ok, pid=0,
                               port=int(os.environ.get("OPENCLAW_GATEWAY_PORT") or 18789),
                               probe="official: gateway status")
        _stake("信号：gateway.ready=%s（官方 status rc=%s）" % (_ok, _st))
        return _ok
    except Exception as _e:


        _stake("网关探针：探不动（%s）⇒ 记 ready=false" % _e)
        try:
            signals().mark_gateway(False, pid=0, port=0, probe="probe unavailable")
        except Exception:
            pass
        return False


# L2073-2088：_gateway_unit_failed — 网关这个服务已经被判死了没 —— 判决来自 systemd，不是壳拿秒数猜的。
def _gateway_unit_failed() -> bool:
    r"""网关这个服务已经被判死了没 —— 判决来自 systemd，不是壳拿秒数猜的。"""




    try:
        rc, out, _ = _gw_run_wsl(
            f"wsl -d {WSL_DISTRO} -u root -- bash -c "
            f"\"export XDG_RUNTIME_DIR=/run/user/0; "
            f"systemctl --user is-failed openclaw-gateway.service 2>&1\"",
        )
        return rc == 0 and "failed" in out.strip().lower()
    except Exception as _e:
        _stake("网关判决：问不到单元状态（%s）⇒ 当没判死" % _e)
        return False


# L2092-2102：_wait_gateway_ready — 读网关 ready —— 探一次、读账本；不阻塞、不等、不掐表。
def _wait_gateway_ready() -> str:
    r"""读网关 ready —— 探一次、读账本；不阻塞、不等、不掐表。"""






    if gateway_probe().probe_once():
        return "ready"
    return "broken" if _gateway_unit_failed() else "nowatch"





# L2109-2129：GatewayProbe — 网关探针（按需版）：只盯一件事 —— 网关 ready 没 ready。
class GatewayProbe:
    r"""网关探针（按需版）：只盯一件事 —— 网关 ready 没 ready。"""








    @staticmethod
    def probe_once() -> bool:
        r"""探一次，把结果记进 Gateway 段（唯一判据的产者）。"""
        return _gateway_signal()

    def ready(self) -> bool:
        r"""读探针记下的 ready —— 唯一判据；读不到就是没 ready。"""
        try:
            return bool((signals().read().get("gateway") or {}).get("ready") is True)
        except Exception:
            return False


_GATEWAY_PROBE = None


# L2136-2141：gateway_probe — 网关探针（模块单例：全壳只盯一个网关）。
def gateway_probe() -> "GatewayProbe":
    r"""网关探针（模块单例：全壳只盯一个网关）。"""
    global _GATEWAY_PROBE
    if _GATEWAY_PROBE is None:
        _GATEWAY_PROBE = GatewayProbe()
    return _GATEWAY_PROBE


# L2145-2166：repair_openclaw_state — OpenClaw 系统级异常的统一出口 —— 抛给官方 doctor --fix。
def repair_openclaw_state(log_callback=None) -> bool:
    r"""OpenClaw 系统级异常的统一出口 —— 抛给官方 doctor --fix。"""












    def _log(msg: str) -> None:
        if log_callback:
            log_callback(msg)




    return _repair_openclaw_state_owned(_log)

# L2169-2223：restart_gateway_safe — restart_gateway_safe
def restart_gateway_safe(log_callback=None, *, manual: bool = False) -> bool:



    if not manual:
        try:
            stk("gw.restart", "非手动 ⇒ 不自启网关（为系统级重启让路）", ok=False)
        except Exception:
            pass
        return False
    r"""冷启动重启 Gateway —— 走官方 gateway restart（无 flag）。"""
















    def _log(msg: str) -> None:
        if log_callback:
            log_callback(msg)

    _log("正在重启 Gateway...")
    rc, out, err = _run_official_cmd(
        "gateway", "restart", "")

    stk("gw.restart", f"官方 gateway restart rc={rc} {(out or err).strip()[:120]}", ok=(rc == 0))
    if rc == 0 and check_gateway_alive():
        return True





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

# L2226-2292：_repair_openclaw_state_owned — _repair_openclaw_state_owned
def _repair_openclaw_state_owned(_log) -> bool:











    _st = 1
    try:
        _st, _so, _se = _run_official_cmd("gateway", "status")


        try:
            _ok = (_st == 0 and "running" in str(_so or "").lower())
            signals().mark_gateway(_ok, pid=0,
                                   port=int(os.environ.get("OPENCLAW_GATEWAY_PORT") or 18789),
                                   probe="official: gateway status")
            _stake("信号：gateway.ready=%s（官方 status rc=%s）" % (_ok, _st))
        except Exception as _e:
            _stake("信号：写 gateway 段失败（%s）" % _e)
    except Exception:
        _st = 1
    if _st == 0:
        _log("gateway 在跑（不是在坏掉的状态）⇒ 跳过爆破，不做停-修-开")
        stk("gw.repair", "gateway 在跑 ⇒ 不做停-修-开（不误伤在跑的网关）", ok=True)
        return True



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
    except Exception as _de:
        _log(f"doctor --fix 异常：{type(_de).__name__}: {_de}")
        stk("gw.repair.doctor", f"doctor --fix 异常：{type(_de).__name__}: {_de}", ok=False)
    finally:

        _log("gateway start")
        try:
            _rc_start, _out_start, _err_start = _run_official_cmd("gateway", "start")
            stk("gw.repair.start",
                f"gateway start rc={_rc_start} {(_out_start + _err_start).strip()[:120]}",
                ok=(_rc_start == 0))
        except Exception as _se2:
            _log(f"gateway start 异常：{type(_se2).__name__}: {_se2}")
            stk("gw.repair.start", f"gateway start 异常：{type(_se2).__name__}: {_se2}", ok=False)
    return ok


clear_migration_lock = repair_openclaw_state



# L2300-2346：repair_openclaw_package — Reinstall openclaw via npm — single pipeline.
def repair_openclaw_package(log_callback=None) -> bool:
    r"""Reinstall openclaw via npm — single pipeline."""





    def _log(msg: str) -> None:
        if log_callback:
            log_callback(msg)

    _ok, _prev, _why = _chain_prev("cli")
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
        try:
            signals().mark_install("cli", note="npm install -g openclaw@latest ok")
        except Exception as _se:
            _stake("信号：写 cli 失败 %s" % _se)
        return True

    _log(f"openclaw package repair failed (rc={rc})")
    if err:
        _log(f"  Error: {err[:200]}")
    return False

# L2349-2382：start_gateway — start_gateway
def start_gateway(log_callback=None, *, manual: bool = False) -> bool:

    if not manual:
        try:
            stk("gw.start", "非手动 ⇒ 不自启网关（为系统级重启让路）", ok=False)
        except Exception:
            pass
        return False
    r"""启动 Gateway —— 走官方 systemd 托管（幂等、单一实例）。"""







    def _log(msg: str) -> None:
        if log_callback:
            log_callback(msg)


    _log("gateway start")
    _rc, _out, _err = _run_official_cmd("gateway", "start")
    stk("gw.start", f"gateway start rc={_rc} {(_out + _err).strip()[:120]}", ok=(_rc == 0))







    if _rc == 0:
        return True
    return check_gateway_alive()



# L2387-2460：recover_gateway — recover_gateway
def recover_gateway(log_callback=None, *, manual: bool = False) -> tuple[bool, str]:

    if not manual:
        try:
            stk("gw.recover", "非手动 ⇒ 不做全链路恢复（为系统级重启让路）", ok=False)
        except Exception:
            pass
        return False, "为系统级重启让路（非手动）"
    r"""一键恢复 Gateway: 诊断 → 修复 → 启动 → 探活。"""









    def _log(msg: str) -> None:
        if log_callback:
            log_callback(msg)

    _log("═ Gateway 全链路恢复 ═")


    if check_gateway_alive():
        _log("Gateway 已在线，无需恢复")
        stk("gw.recover", "Gateway 已在线 ⇒ 不碰（在跑一律不碰）", ok=True)
        return True, "Gateway 已在线"






    _owner = "gateway_recovery.recover_gateway"
    if not acquire_lifecycle(_owner):
        _log("Gateway 生命周期已被别的 owner 接管 ⇒ 本轮不插手（它完成后自会收敛）")
        stk("gw.recover", "生命周期已被别的 owner 接管 ⇒ 本轮不插手", ok=False)
        return False, "Gateway 生命周期由其它入口接管中"
    stk("gw.recover", "取得生命周期所有权 ⇒ 开始全链路恢复", ok=True)
    try:

        cli_ok = check_openclaw_cli()
        _log(f"openclaw CLI: {'可用' if cli_ok else '损坏 — 需要修复'}")


        if not cli_ok:
            repair_node_version(_log)
            if not repair_openclaw_package(_log):
                return False, "openclaw 包修复失败，需手动重装"
            if not check_openclaw_cli():
                _log("修复后 openclaw CLI 仍不可用")
                return False, "openclaw CLI 修复后仍不可用"
            _log("Node.js/openclaw 已修复")




        if start_gateway(_log, manual=True):
            return True, "Gateway 已恢复"


        _log("doctor --fix")
        repair_openclaw_state(_log)


        if check_gateway_alive():
            return True, "Gateway 已恢复"

        return False, "Gateway 仍未就绪，请手动 openclaw gateway start"
    finally:

        release_lifecycle(_owner)














































import os
import subprocess
import threading

_PROGRAMDATA = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia")





_PIP_DONE_MARKER = os.path.join(_PROGRAMDATA, "pip_venv_done")
WSL_DISTRO = os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu"

# L2521-2531：_pv_run_wsl — _pv_run_wsl
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

# L2534-2543：_pv_is_wsl_responding — _pv_is_wsl_responding
def _pv_is_wsl_responding() -> bool:
    rc, out, _ = _pv_run_wsl(
        f"wsl -d {WSL_DISTRO} -u root -- bash -c \"echo ok\"")
    if rc == 0 and "ok" in out:
        try:
            signals().mark_install("wsl", note="wsl -u root echo ok")
        except Exception as _se:
            _stake("信号：写 wsl 失败 %s" % _se)
        return True
    return False

# L2546-2597：_ensure_pip_venv — 确保 WSL 内 python3 + pip + venv 可用。
def _ensure_pip_venv() -> bool:
    r"""确保 WSL 内 python3 + pip + venv 可用。"""




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
        try:
            signals().mark_install("wsl", note="apt python3-pip python3-venv ok")
        except Exception as _se:
            stk("pip.venv", f"信号：写 wsl 失败 {_se}", ok=False)
        return True

    stk("pip.venv", f"FAIL rc={rc}: {out[:300]}", ok=False)
    return False


_OFFICE_PACKAGES = [
    "python-docx",
    "python-pptx",
    "openpyxl",
    "pymupdf",
    "reportlab",
]

# L2609-2635：_install_office_deps — 安装 office 核心依赖。返回失败的包名列表。
def _install_office_deps() -> list[str]:
    r"""安装 office 核心依赖。返回失败的包名列表。"""







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



# L2640-2677：ensure_pip_venv_background — 后台线程：装 pip + venv + office 核心依赖。幂等，失败跳过。
def ensure_pip_venv_background() -> None:
    r"""后台线程：装 pip + venv + office 核心依赖。幂等，失败跳过。"""




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

            return

        failed = _install_office_deps()
        if failed:
            stk("pip.venv", f"office deps done — {len(failed)} failed: {failed}", ok=False)
        else:
            stk("pip.venv", "office deps done — all installed")


        try:
            os.makedirs(_PROGRAMDATA, exist_ok=True)
            with open(_PIP_DONE_MARKER, "w") as f:
                f.write("1")
        except OSError as _me:

            stk("pip.venv", f"写幂等标记失败（下次重跑一遍）：{_me}", ok=False)

    t = threading.Thread(target=_worker, daemon=True, name="pip-venv")
    t.start()


















import json
import os
import subprocess
import threading
import time

from PySide6.QtCore import QObject, Signal







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




_PREINSTALL_DEPS: dict[str, dict[str, list[str]]] = {
    "@weiwei2027/office-toolkit": {"apt": ["libreoffice"]},
    "@cindypapa/agent-browser": {
        "npm": ["agent-browser"],
        "download": ["agent-browser install"],
    },
}

# L2733-2738：_apt_install — apt 直装系统包。
def _apt_install(pkg: str) -> bool:
    r"""apt 直装系统包。"""
    rc, out, err = _wsl_run_argv(
        f"apt-get install -y {pkg} 2>&1",
    )
    return rc == 0

# L2741-2743：_npm_global_install — _npm_global_install
def _npm_global_install(pkg: str) -> bool:
    rc, out, err = _wsl_run_argv(f"npm install -g {pkg} 2>&1")
    return rc == 0

# L2746-2748：_run_downloader — _run_downloader
def _run_downloader(cmd: str) -> bool:
    rc, out, err = _wsl_run_argv(f"{cmd} 2>&1")
    return rc == 0

# L2751-2766：_verify_prereq — 配套 verify：soffice / agent-browser CLI / Chrome 内核存在。
def _verify_prereq(name: str) -> bool:
    r"""配套 verify：soffice / agent-browser CLI / Chrome 内核存在。"""
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

# L2769-2791：_install_all_prereqs — 给全部需要配套的预装项直装配套。全成 True，任一失败 False（下次重试）。
def _install_all_prereqs() -> bool:
    r"""给全部需要配套的预装项直装配套。全成 True，任一失败 False（下次重试）。"""
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


_FAILED_STATE = os.path.join(_PROGRAMDATA, "failed_state.json")
_TAVILY_KEY_FILE = os.path.join(_PROGRAMDATA, "tavily_api_key.txt")
WSL_DISTRO = os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu"







_MAX_ATTEMPTS = 5



_RETRY_BACKOFF_SECONDS = 24 * 3600




_KIND_CACHE = os.path.join(_PROGRAMDATA, "install_kind_cache.json")
_KIND_CACHE_TTL = 24 * 3600



# L2825-2828：_BonusSkillsSignals — 安装线程完成后通知 UI 线程刷新侧边栏。
class _BonusSkillsSignals(QObject):
    r"""安装线程完成后通知 UI 线程刷新侧边栏。"""
    finished = Signal()
    failed_refresh = Signal()

_signals = _BonusSkillsSignals()

# L2833-2835：on_bonus_skills_finished — 注册回调：全部 bonus skills 安装尝试完毕后触发（主线程安全）。
def on_bonus_skills_finished(callback) -> None:
    r"""注册回调：全部 bonus skills 安装尝试完毕后触发（主线程安全）。"""
    _signals.finished.connect(callback)

# L2838-2840：refresh_failed_skills — 注册回调：需要刷新失败 skills 侧边栏列表。
def refresh_failed_skills(callback) -> None:
    r"""注册回调：需要刷新失败 skills 侧边栏列表。"""
    _signals.failed_refresh.connect(callback)



# L2845-2917：_bind_child_to_parent — 把子进程绑进「父进程一死、子进程跟着死」的作业对象（Windows 标准做法）。
def _bind_child_to_parent(pid: int) -> bool:
    r"""把子进程绑进「父进程一死、子进程跟着死」的作业对象（Windows 标准做法）。"""












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

        _KILL_ON_CLOSE = 0x2000
        _EXT_INFO = 9
        _SET_QUOTA_TERMINATE = 0x0100 | 0x0200
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

            _JOB_HANDLES.append(job)
        else:
            k32.CloseHandle(job)
        return ok
    except Exception:
        logger.warning("作业对象绑定异常（保活硬杀时可能留孤儿）", exc_info=True)
        return False



_JOB_HANDLES: list = []







# L2930-2950：_wsl_run_argv — list 参数直跑 WSL bash -c（不经 cmd.exe shell，引号零损耗）。
def _wsl_run_argv(script: str) -> tuple[int, str, str]:
    r"""list 参数直跑 WSL bash -c（不经 cmd.exe shell，引号零损耗）。"""







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


# L2954-2965：_bs_is_wsl_responding — _bs_is_wsl_responding
def _bs_is_wsl_responding() -> bool:



    rc, out, _ = _wsl_run_argv("echo ok")
    if rc == 0 and "ok" in out:
        try:
            signals().mark_install("wsl", note="WSL 应答（安装链入口探活）")
        except Exception as _se:
            _stake("信号：写 wsl 失败 %s" % _se)
        return True
    return False


















_AGENT_CACHE = ""
_AGENT_CACHE_DONE = False

_CLI_LOG = os.path.join(_PROGRAMDATA, "cli.log")


# L2991-3001：_cli_log — 把 CLI 的原始输出追加落盘。壳不加工、不截断、不改一字。
def _cli_log(tag: str, text: str) -> None:
    r"""把 CLI 的原始输出追加落盘。壳不加工、不截断、不改一字。"""
    try:
        os.makedirs(os.path.dirname(_CLI_LOG), exist_ok=True)
        with open(_CLI_LOG, "a", encoding="utf-8") as f:
            f.write("\n" + "=" * 78 + "\n")
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {tag}\n")
            f.write("=" * 78 + "\n")
            f.write(((text or "").rstrip()) + "\n")
    except OSError:
        pass


# L3005-3019：_cli_search — 问 CLI：这个名字在 skills / plugins 目录里有没有。返回 CLI 原始 JSON（''=没问到）。
def _cli_search(name: str, sub: str) -> str:
    r"""问 CLI：这个名字在 skills / plugins 目录里有没有。返回 CLI 原始 JSON（''=没问到）。"""



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


# L3023-3030：_norm_slug — 把名字归一成 slug 再比。
def _norm_slug(v) -> str:
    r"""把名字归一成 slug 再比。"""





    return str(v or "").strip().lower().replace(" ", "-").replace("_", "-")


# L3034-3045：_agent_flag — CLI 的 skills list / skills install 在多 agent 下必须指名 owner。
def _agent_flag() -> str:
    r"""CLI 的 skills list / skills install 在多 agent 下必须指名 owner。"""





    global _AGENT_CACHE, _AGENT_CACHE_DONE
    if not _AGENT_CACHE_DONE:
        _AGENT_CACHE = _default_agent()
        _AGENT_CACHE_DONE = True
    return f"--agent {_AGENT_CACHE}" if _AGENT_CACHE else ""


# L3049-3086：_cli_kind — 判定 skill / plugin —— 拿 CLI 的 search 结果定，壳不自己爬。
def _cli_kind(name: str) -> str:
    r"""判定 skill / plugin —— 拿 CLI 的 search 结果定，壳不自己爬。"""













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


# L3090-3127：_default_agent — 问 CLI：默认 agent 是谁。壳不硬编名字。
def _default_agent() -> str:
    r"""问 CLI：默认 agent 是谁。壳不硬编名字。"""






    try:
        rc, out, err = _wsl_run_argv("openclaw agents list --json")
    except Exception as e:
        _cli_log("agent: openclaw agents list --json", f"执行异常: {e}")
        return ""
    if rc != 0:

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


    _cli_log(f"agent: openclaw agents list --json  (rc=0) → 默认 agent = {aid or '(未取到)'}",
             "（列出的 agent：" + ", ".join(
                 str(a.get("id")) for a in agents if isinstance(a, dict)) + "）")
    return aid


# L3131-3178：_resolve_kind — 判定一个预装名字是 skill 还是 plugin。返回 'skill'/'plugin'/''。
def _resolve_kind(name: str) -> str:
    r"""判定一个预装名字是 skill 还是 plugin。返回 'skill'/'plugin'/''。"""









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


            cache.pop(name, None)
        os.makedirs(_PROGRAMDATA, exist_ok=True)
        with open(_KIND_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except OSError:
        pass

    return kind

# L3181-3199：_install_succeeded — 判定安装是否成功。
def _install_succeeded(rc: int, combined: str) -> bool:
    r"""判定安装是否成功。"""










    if rc == 0:
        return True
    low = (combined or "").lower()
    return ("already exists" in low
            or "already installed" in low
            or "installed plugin" in low
            or "installed skill" in low)

# L3202-3218：_is_terminal_error — 确定性错误判定：CLI 反馈「再试也无用」的信号 → 早停，不重试。
def _is_terminal_error(combined: str) -> bool:
    r"""确定性错误判定：CLI 反馈「再试也无用」的信号 → 早停，不重试。"""





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

# L3221-3296：_inject_local_plugins — 随壳本地插件注入 WSL：装壳即到位，不依赖外部源 / 手工部署。
def _inject_local_plugins() -> None:
    r"""随壳本地插件注入 WSL：装壳即到位，不依赖外部源 / 手工部署。"""












    try:
        app_dir = _resolve_app_dir()
        if not app_dir or not os.path.isdir(app_dir):
            return
        wsl_app = "/mnt/" + app_dir[0].lower() + app_dir[2:].replace("\\", "/")
        plugin_dir = wsl_app + "/personal-slot"
        script = (
            f"python3 '{wsl_app}/installer/inject_local_plugins.py' "
            f"'{plugin_dir}' 2>&1"
        )
        rc, out, err = _wsl_run_argv(script)
        if rc == 10:
            stk("bonus.local_plugin", f"inject rc=10 已登记 → 让它加载 {(out or '')[:80]}", ok=True)


            rcR, outR, errR = _run_official_cmd("plugins", "reload",
                                                "persona-slot-engine")
            if rcR == 0 and check_gateway_alive():
                stk("bonus.local_plugin.reload", f"plugins reload 成功（不重启） {(outR or '')[:100]}",
                    ok=True)
                return
            stk("bonus.local_plugin.reload",
                f"plugins reload 没成 rc={rcR}（{(outR + errR).strip()[:140]}）→ 冷启动兜底",
                ok=False)

            try:



                _owned = acquire_lifecycle("ui.plugin_inject")
                if not _owned:



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


            stk("bonus.local_plugin", f"仅规格变更，已写进插件配置（热重载生效，不重启） {(out or '')[:80]}",
                ok=True)
        elif rc == 2:
            stk("bonus.local_plugin", f"环境未就绪，本次跳过（下次开机自动补）: {(out or '')[:100]}", ok=True)
        else:
            stk("bonus.local_plugin", f"persona-slot-engine inject rc={rc} {(out or '')[:120]}", ok=(rc == 0))
    except Exception as e:
        stk("bonus.local_plugin", f"inject 异常 {e}", ok=False)


# L3300-3320：_install_any — 装一个名字 —— kind 问 CLI，问不出就别猜，两个族都让 CLI 自己试一遍。
def _install_any(name: str) -> tuple[bool, str]:
    r"""装一个名字 —— kind 问 CLI，问不出就别猜，两个族都让 CLI 自己试一遍。"""






    kind = _resolve_kind(name)



    other = "skill" if kind == "plugin" else "plugin"
    order = [kind, other] if kind else ["plugin", "skill"]
    reasons: list[str] = []
    for k in order:
        ok, reason = _install_one(name, k)
        if ok:
            return True, ""
        reasons.append(f"{k}: {reason}")
    return False, " | ".join(reasons)


# L3324-3338：_enable_skill — 装完/已在册后把 skill 显式打开（bundled 同名项可能带 enabled=false）。
def _enable_skill(name: str) -> bool:
    r"""装完/已在册后把 skill 显式打开（bundled 同名项可能带 enabled=false）。"""
    slug = name.split("/")[-1].lower()
    try:
        rc, out, err = _wsl_run_argv(
            f"openclaw config set 'skills.entries.{slug}.enabled' true "
            f"--strict-json 2>&1")
        _cli_log(f"{name}: enable skill {slug}  (rc={rc})",
                 (out or "") + (("\n[stderr]\n" + err) if err else ""))
        stk("bonus.skills", f"{name}: skill enable {'OK' if rc == 0 else 'FAIL'}",
            ok=rc == 0)
        return rc == 0
    except Exception as _ee:
        stk("bonus.skills", f"{name}: skill enable 异常 {_ee}", ok=False)
        return False


# L3342-3473：_install_one — _install_one
def _install_one(name: str, kind: str) -> tuple[bool, str]:
    r"""_install_one"""









    cli_id = "plugins" if kind == "plugin" else "skills"
    if kind == "plugin":


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


        _cli_log(f"{name} install (attempt {attempt}, {kind}): {inner}  (rc={rc})",
                 combined)


        if "resource temporarily unavailable" in combined.lower():
            last_reason = "跨进程锁被占（另一 install 进行中）"
            break




        if _install_succeeded(rc, combined):
            if kind == "plugin":
                eid = name.split("/")[-1].replace("-plugin", "").replace("-provider", "")
                try:
                    rc2, o2, e2 = _wsl_run_argv(f"openclaw plugins enable '{eid}' 2>&1")
                    _cli_log(f"{name}: openclaw plugins enable {eid}  (rc={rc2})",
                             (o2 or "") + (("\n[stderr]\n" + e2) if e2 else ""))
                    stk("bonus.skills", f"{name}: plugin enable {'OK' if rc2 == 0 else 'FAIL'}",
                        ok=rc2 == 0)
                    if rc2 == 0:
                        try:
                            signals().mark_install("plugin")
                        except Exception as _se:
                            stk("bonus.skills", f"信号：写 plugin 失败 {_se}", ok=False)
                except Exception as _ee:
                    stk("bonus.skills", f"{name}: plugin enable 异常 {_ee}", ok=False)
            else:



                _enable_skill(name)
            stk("bonus.skills", f"{name}: {kind} 装完（CLI 直抛, attempt {attempt}）")
            return True, ""


        low = combined.lower()
        new_flag = False

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
            continue


        if _is_terminal_error(combined):
            stk("bonus.skills", f"{name}: 确定性错误，早停", ok=False)
            break

        last_reason = combined
        stk("bonus.skills",
            f"{name}: attempt {attempt}/{_MAX_ATTEMPTS} 失败: {combined[:200]!r}", ok=False)
        if attempt < _MAX_ATTEMPTS:


            _how = "ready" if _gateway_signal() else _wait_gateway_ready()
            if _how != "ready":
                last_reason = f"网关没回 ready（{_how}）：{last_reason}"
                break
    stk("bonus.skills", f"{name}: 装不上（{_MAX_ATTEMPTS} 次）", ok=False)
    return False, last_reason[:400]


# L3477-3488：_read_tavily_api_key — 读取预置的 Tavily API key。
def _read_tavily_api_key() -> str | None:
    r"""读取预置的 Tavily API key。"""


    env_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if env_key:
        return env_key
    try:
        with open(_TAVILY_KEY_FILE, "r", encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None

# L3491-3494：_read_mem9_api_key — 读取 mem9 API key。优先级: MEM9_API_KEY 环境变量。
def _read_mem9_api_key() -> str | None:
    r"""读取 mem9 API key。优先级: MEM9_API_KEY 环境变量。"""
    env_key = os.environ.get("MEM9_API_KEY", "").strip()
    return env_key if env_key else None

# L3497-3519：_patch_tavily_config — 直接写 WSL openclaw.json — 不走 CLI。
def _patch_tavily_config(api_key: str) -> None:
    r"""直接写 WSL openclaw.json — 不走 CLI。"""
    try:
        cfg = read()

        cfg.setdefault("plugins", {}).setdefault("entries", {}).setdefault("tavily", {})
        tav = cfg["plugins"]["entries"]["tavily"]
        tav["enabled"] = True
        tav.setdefault("config", {}).setdefault("webSearch", {})
        tav["config"]["webSearch"]["apiKey"] = api_key
        tav["config"]["webSearch"]["baseUrl"] = "https://api.tavily.com"

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



# L3524-3542：_patch_mem9_config — mem9 有 key 时写 openclaw.json（安装命令与 skill/plugin 分流无关）。
def _patch_mem9_config(api_key: str) -> None:
    r"""mem9 有 key 时写 openclaw.json（安装命令与 skill/plugin 分流无关）。"""
    try:
        cfg = read()

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





_DONE_LEVEL = "2"


# L3552-3574：_already_done — 检查是否本轮无事可做。
def _already_done() -> bool:
    r"""检查是否本轮无事可做。"""








    if not os.path.isfile(_DONE_MARKER):
        return False
    try:
        with open(_DONE_MARKER, "r", encoding="utf-8") as _f:
            if (_f.read() or "").strip() != _DONE_LEVEL:
                return False
    except OSError:
        return False
    failed = _read_failed()
    if not failed:
        return True
    state = _read_failed_state()
    return not _should_retry_any(state, failed)


# L3578-3589：_mark_done — 写 marker 文件标记完成 + 产者自报：done 这一步 ready（信号账）。
def _mark_done() -> None:
    r"""写 marker 文件标记完成 + 产者自报：done 这一步 ready（信号账）。"""
    try:
        os.makedirs(_PROGRAMDATA, exist_ok=True)
        with open(_DONE_MARKER, "w") as f:
            f.write(_DONE_LEVEL)
    except OSError:
        pass
    try:
        signals().mark_install("done", note="安装收口完成")
    except Exception as _se:
        _stake("信号：写 done 失败 %s" % _se)

# L3592-3598：_read_failed — 读 failed_skills.json。
def _read_failed() -> list[str]:
    r"""读 failed_skills.json。"""
    try:
        with open(_FAILED_LIST, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

# L3601-3608：_write_failed — 写 failed_skills.json。
def _write_failed(slugs: list[str]) -> None:
    r"""写 failed_skills.json。"""
    try:
        os.makedirs(_PROGRAMDATA, exist_ok=True)
        with open(_FAILED_LIST, "w", encoding="utf-8") as f:
            json.dump(slugs, f, ensure_ascii=False, indent=2)
    except OSError:
        pass

# L3611-3623：_read_failed_state — 读 failed_state.json（{name: {last_fail_at, fail_count}}）。
def _read_failed_state() -> dict[str, dict]:
    r"""读 failed_state.json（{name: {last_fail_at, fail_count}}）。"""




    _reset_failures_if_runtime_changed()
    try:
        with open(_FAILED_STATE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}

# L3626-3633：_write_failed_state — 写 failed_state.json。
def _write_failed_state(state: dict[str, dict]) -> None:
    r"""写 failed_state.json。"""
    try:
        os.makedirs(_PROGRAMDATA, exist_ok=True)
        with open(_FAILED_STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


_RETRY_BACKOFF_VERSION_SECONDS = 10 * 60


_RUNTIME_VER_FILE = os.path.join(_PROGRAMDATA, "runtime_version.txt")


_VERSION_GATE_MARKERS = ("requires plugin api", "runtime exposes", "api >=")

# L3645-3655：_is_version_gated — 该名字上次失败是不是「版本落差」——判据取自 failed_reasons.json 的原文。
def _is_version_gated(name: str) -> bool:
    r"""该名字上次失败是不是「版本落差」——判据取自 failed_reasons.json 的原文。"""
    try:
        with open(_FAILED_REASONS, "r", encoding="utf-8") as f:
            data = json.load(f)
        v = data.get(name) if isinstance(data, dict) else None
        txt = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        low = (txt or "").lower()
        return any(m in low for m in _VERSION_GATE_MARKERS)
    except Exception:
        return False

# L3658-3685：_reset_failures_if_runtime_changed — 运行时版本一变（9.6→9.7 这种），清掉上一版的失败账，让失败项立刻重排一轮。
def _reset_failures_if_runtime_changed() -> None:
    r"""运行时版本一变（9.6→9.7 这种），清掉上一版的失败账，让失败项立刻重排一轮。"""
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

# L3688-3695：_mark_fail — 记录一次失败：更新时间戳 + 递增失败次数。
def _mark_fail(name: str, state: dict[str, dict]) -> None:
    r"""记录一次失败：更新时间戳 + 递增失败次数。"""
    now = time.time()
    prev = state.get(name) or {}
    state[name] = {
        "last_fail_at": now,
        "fail_count": int(prev.get("fail_count", 0)) + 1,
    }

# L3698-3710：_should_retry — 退避窗口判定：距上次失败 >= 24h 才允许重试；从未失败过则允许。
def _should_retry(name: str, state: dict[str, dict]) -> bool:
    r"""退避窗口判定：距上次失败 >= 24h 才允许重试；从未失败过则允许。"""




    prev = state.get(name)
    if not prev:
        return True
    last_fail = prev.get("last_fail_at", 0)
    window = (_RETRY_BACKOFF_VERSION_SECONDS if _is_version_gated(name)
              else _RETRY_BACKOFF_SECONDS)
    return (time.time() - last_fail) >= window

# L3713-3715：_should_retry_any — 失败名单里是否还有「越过退避窗口、值得本轮重试」的名字。
def _should_retry_any(state: dict[str, dict], names: list[str]) -> bool:
    r"""失败名单里是否还有「越过退避窗口、值得本轮重试」的名字。"""
    return any(_should_retry(n, state) for n in names)





_FAILED_REASONS = os.path.join(_PROGRAMDATA, "failed_reasons.json")

# L3724-3731：_write_failed_reasons — 写 failed_reasons.json（name -> 失败原因）。
def _write_failed_reasons(reasons: dict[str, str]) -> None:
    r"""写 failed_reasons.json（name -> 失败原因）。"""
    try:
        os.makedirs(_PROGRAMDATA, exist_ok=True)
        with open(_FAILED_REASONS, "w", encoding="utf-8") as f:
            json.dump(reasons, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


_KNOWLEDGE_BEGIN = "<!-- PREINSTALL_FAILURES_BEGIN -->"
_KNOWLEDGE_END = "<!-- PREINSTALL_FAILURES_END -->"
_KNOWLEDGE_FILE = os.path.join(_PROGRAMDATA, "KNOWLEDGE.md")

# L3739-3777：_inject_failures_into_knowledge — 把预装失败清单写进 ProgramData 的 KNOWLEDGE.md 动态段。
def _inject_failures_into_knowledge(failed: list[str], reasons: dict[str, str]) -> None:
    r"""把预装失败清单写进 ProgramData 的 KNOWLEDGE.md 动态段。"""





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

            reason_short = reason[:500]
            lines.append(f"- **{name}** 装失败：`{reason_short}`")
        block = "\n".join(lines)
        new_raw = raw.split(_KNOWLEDGE_BEGIN)[0] + _KNOWLEDGE_BEGIN \
            + "\n" + block + "\n" + _KNOWLEDGE_END \
            + raw.split(_KNOWLEDGE_END)[1]
        with open(_KNOWLEDGE_FILE, "w", encoding="utf-8") as f:
            f.write(new_raw)
        stk("bonus.skills", f"预装失败清单已注入 KNOWLEDGE.md（{len(failed)} 项）")


        try:
            from platform.slotApi import injectPersona
            injectPersona()
            stk("bonus.skills", "已重新注入 SOUL.md，Agent 下次会话可读到失败清单")
        except Exception as _ie:
            stk("bonus.skills", f"重新注入 SOUL.md 失败（非致命）: {_ie}", ok=False)
    except Exception as e:
        stk("bonus.skills", f"注入 KNOWLEDGE.md 失败（非致命）: {e}", ok=False)



# L3782-3833：_verify_installed — 该名字是否已装 —— 只问 CLI，壳不自己查落点。
def _verify_installed(name: str) -> bool:
    r"""该名字是否已装 —— 只问 CLI，壳不自己查落点。"""












    slug = name.split("/")[-1].lower()
    bare = slug.replace("-provider", "").replace("-plugin", "")

    try:


        cmd = f"openclaw skills list --json {_agent_flag()}".strip()
        rc, out, err = _wsl_run_argv(cmd)
        _cli_log(f"installed?: {name} ← {cmd}  (rc={rc})",
                 (out or "") + (("\n[stderr]\n" + err) if err else ""))
        if rc == 0:
            for s in (json.loads(out or "{}") or {}).get("skills", []):
                if s.get("bundled"):
                    continue
                if str(s.get("source", "")).lower() == "openclaw-bundled":
                    continue
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

                if str(p.get("origin", "")).lower() == "bundled":
                    continue
                if bare and bare in (str(p.get("id", "")).lower(),
                                     str(p.get("name", "")).lower()):
                    return True
    except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
        pass

    return False



_INSTALLED_CACHE: dict | None = None


# L3841-3844：_reset_installed_cache — 新的一轮装链开始 ⇒ 丢掉上一轮的名单缓存（名单可能已经变了）。
def _reset_installed_cache() -> None:
    r"""新的一轮装链开始 ⇒ 丢掉上一轮的名单缓存（名单可能已经变了）。"""
    global _INSTALLED_CACHE
    _INSTALLED_CACHE = None


# L3848-3889：_installed_cache — 只问一次 CLI：把官方名单（skills/plugins）缓存下来。
def _installed_cache() -> dict:
    r"""只问一次 CLI：把官方名单（skills/plugins）缓存下来。"""




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

                if s.get("bundled"):
                    continue
                if str(s.get("source", "")).lower() == "openclaw-bundled":
                    continue
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

                if str(pg.get("origin", "")).lower() == "bundled":
                    continue
                pl.add(str(pg.get("id", "")).lower())
                pl.add(str(pg.get("name", "")).lower())
    except Exception:
        pass
    _INSTALLED_CACHE = {"skills": sk, "plugins": pl}
    return _INSTALLED_CACHE


# L3893-3905：_verify_installed_fast — _verify_installed 的缓存版（同一轮里只问 CLI 一次），语义完全相同：
def _verify_installed_fast(name: str) -> bool:
    r"""_verify_installed 的缓存版（同一轮里只问 CLI 一次），语义完全相同："""



    c = _installed_cache()
    slug = name.split("/")[-1].lower()
    bare = slug.replace("-provider", "").replace("-plugin", "")
    if slug and slug in c.get("skills", set()):
        return True
    if bare and bare in c.get("plugins", set()):
        return True
    return False


# L3909-3924：cleanup_failed_skills — 检查 failed_skills.json 中是否有已通过其他途径安装成功的名字，
def cleanup_failed_skills() -> None:
    r"""检查 failed_skills.json 中是否有已通过其他途径安装成功的名字，"""


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



# L3929-4112：install_bonus_skills_background — 后台线程：装 clawhub CLI → Tavily → mem9 → 5 bonus skills。幂等，失败跳过。
def install_bonus_skills_background(*, run_sync: bool = False) -> None:
    r"""后台线程：装 clawhub CLI → Tavily → mem9 → 5 bonus skills。幂等，失败跳过。"""






    if _already_done():
        stk("bonus.skills", "already done, skip")
        return





    try:
        _iid = _install_id()
        _e = signals().open_install(_iid)
        stk("bonus.skills", f"安装链：开装 install_id={_iid}" + (f"（{_e}）" if _e else ""))
    except Exception as _e:
        stk("bonus.skills", f"安装链：开装写信号失败 {_e}", ok=False)


    if not _bs_is_wsl_responding():
        stk("bonus.skills", "WSL not responding, defer", ok=False)
        return






    try:
        if not check_gateway_alive():
            stk("bonus.skills", "Gateway 未稳定，defer（等重启回来）", ok=False)
            return
    except Exception as e:
        stk("bonus.skills", f"Gateway 探活异常，defer: {e}", ok=False)
        return

    stk("bonus.skills", f"starting: {len(_PREINSTALL_NAMES)} 个预装名字（广域网分流 + CLI 拼命令）")
    _reset_installed_cache()

    def _worker() -> None:
        failed_names: list[str] = []
        failed_reasons: dict[str, str] = {}
        try:

            try:
                ensure_pip_venv_background()
            except Exception as e:
                stk("bonus.skills", f"pip_venv init failed (non-fatal): {e}")




            state = _read_failed_state()
            prev_failed = set(_read_failed())
            names_to_run: list[str] = []
            for name in _PREINSTALL_NAMES:
                if name in prev_failed:

                    if _should_retry(name, state):
                        names_to_run.append(name)
                else:

                    names_to_run.append(name)

            if names_to_run:
                stk("bonus.skills", f"本轮重试 {len(names_to_run)} 个名字（跳过退避窗口内的失败项）")
            for name in names_to_run:










                if not gateway_probe().ready():
                    _how = _wait_gateway_ready()
                    if _how != "ready":
                        stk("bonus.skills",
                            f"网关没回 ready（{_how}）⇒ 本轮到此为止，下次开机续装", ok=False)
                        break








                if _verify_installed_fast(name):



                    try:
                        _slug = name.split("/")[-1].lower()
                        _bare = _slug.replace("-provider", "").replace("-plugin", "")
                        _cache_now = _installed_cache()
                        if _bare in (_cache_now.get("plugins") or set()):
                            signals().mark_install("plugin", note="官方在册（跳过安装）")
                        else:

                            _enable_skill(name)
                    except Exception:
                        pass
                    stk("bonus.skills", f"{name}: 官方在册，跳过 install")
                    state.pop(name, None)
                    continue


                ok, reason = _install_any(name)
                if not ok:

                    failed_names.append(name)
                    failed_reasons[name] = reason
                    _mark_fail(name, state)
                else:

                    state.pop(name, None)
            _write_failed_state(state)


            if _read_tavily_api_key():
                _patch_tavily_config(_read_tavily_api_key())
            if _read_mem9_api_key():
                _patch_mem9_config(_read_mem9_api_key())



            stk("bonus.skills", "Phase 3: 预装配套（LibreOffice / agent-browser 内核）...")
            if not _install_all_prereqs():
                prereq_failed = True
                stk("bonus.skills", "配套未全装，本次不写 done（下次启动重试）", ok=False)

        except Exception as e:
            import traceback
            _tb = traceback.format_exc().strip().splitlines()
            stk("bonus.skills", f"worker crashed: {e} | {' / '.join(_tb[-4:])}", ok=False)
        finally:




            current_failed = set(_read_failed())
            current_failed.update(failed_names)
            for name in names_to_run:
                if name in current_failed and name not in failed_names:
                    current_failed.discard(name)
            _write_failed(sorted(current_failed))
            _write_failed_reasons(failed_reasons)

            _inject_failures_into_knowledge(sorted(current_failed), failed_reasons)
            if locals().get("prereq_failed"):
                stk("bonus.skills", "配套未齐：不写 done，下次启动重试配套", ok=False)
            else:

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


                _signals.failed_refresh.emit()
                _signals.finished.emit()



    if run_sync:
        _worker()
        return
    t = threading.Thread(target=_worker, daemon=True, name="bonus-skills")
    t.start()

try:
    import fcntl as _fcntl
except Exception:
    _fcntl = None
try:
    import msvcrt as _msvcrt
except Exception:
    _msvcrt = None


# L4125-4333：Signals — 信号账（方法化）：安装链 ready + Gateway 段 ready，落一份 JSON。
class Signals(object):
    r"""信号账（方法化）：安装链 ready + Gateway 段 ready，落一份 JSON。"""








    LOCK_BYTES = 1

    STEPS = ("wsl", "node", "cli", "plugin", "done")

    def __init__(self, path: str = "") -> None:
        p = str(path or os.environ.get("RHEIA_SIGNALS") or "").strip()
        self.path = p or self.default_path()


    @staticmethod
    def default_path() -> str:
        r"""落在装好的 Rheia 自己的目录里："""





        for up in (1, 2):
            try:
                root = os.path.dirname(os.path.abspath(__file__))
                for _ in range(up):
                    root = os.path.dirname(root)
                if os.path.isdir(os.path.join(root, "kernel")):
                    return os.path.join(root, "kernel", "large", "signals.json")
            except Exception:
                pass
        return r"C:\Program Files\Rheia\kernel\large\signals.json"


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


    @staticmethod
    def empty() -> dict:
        return {"install_id": "", "install": {}, "gateway": {}}

    def read(self) -> dict:
        r"""读信号账；读不到 / 半写坏 ⇒ 当"没有信号"。"""
        try:
            with open(self.path, "rb") as fh:
                raw = fh.read()
            return json.loads(raw.decode("utf-8")) if raw.strip() else self.empty()
        except Exception:
            return self.empty()

    def ready(self, step: str, kind: str = "install") -> bool:
        r"""某一步的 ready 锚点是不是 true —— 唯一的判据。"""
        seg = (self.read().get(kind) or {})
        return bool((seg.get(step) or {}).get("ready") is True)

    def prev_ready(self, step: str, steps=None, kind: str = "install"):
        r"""按顺序看上一步：返回 (ok, 上一步名)。第一步没有上一步 ⇒ (True, "")。"""
        order = list(steps or self.STEPS)
        if step not in order:
            return False, ""
        i = order.index(step)
        if i == 0:
            return True, ""
        pv = order[i - 1]
        return self.ready(pv, kind=kind), pv


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
        r"""开一次安装：install_id 与账里不同 ⇒ 整段重写安装段（旧 ready 不许骗新流程）。"""
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
        r"""安装某一步产者自报 ready；固化：不许把已 true 的改回 false。"""
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
        r"""只覆写 Gateway 段（每次 Gateway 重启都重写这段；判据来自官方探针）。"""
        d = self.read()
        d["gateway"] = {"ready": bool(ready_), "pid": int(pid or 0), "port": int(port or 0),
                        "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "probe": probe}
        return self._write(d)


    @staticmethod
    def _arm_change(path: str):
        r"""武装一个「这个目录里的文件被改了」的内核通知句柄（Windows）。"""



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

            h = k32.FindFirstChangeNotificationW(d, False,
                                                 0x00000001 | 0x00000008 | 0x00000010)
            return h or None
        except Exception:
            return None

    def wait_change(self) -> bool:
        r"""等到账本下一次被写为止（内核叫醒；不 sleep、不轮询、无时限）。"""








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
            k32.WaitForSingleObject(h, 0xFFFFFFFF)
            return True
        except Exception:
            return False
        finally:
            try:
                import ctypes
                from ctypes import wintypes
                k32 = ctypes.WinDLL("kernel32", use_last_error=True)
                k32.FindCloseChangeNotification.argtypes = [wintypes.HANDLE]
                k32.FindCloseChangeNotification(h)
            except Exception:
                pass


_SIGNALS_SINGLETON = None


# L4340-4347：signals — 取信号账（缺省单例；传 path 则另开一份，测试用）。
def signals(path: str = "") -> "Signals":
    r"""取信号账（缺省单例；传 path 则另开一份，测试用）。"""
    global _SIGNALS_SINGLETON
    if path:
        return Signals(path)
    if _SIGNALS_SINGLETON is None:
        _SIGNALS_SINGLETON = Signals()
    return _SIGNALS_SINGLETON


# L4351-4360：_install_id — 本次装机的戳：装着这份壳的那份文件的安装时间（重装/升级 ⇒ 新戳）。
def _install_id() -> str:
    r"""本次装机的戳：装着这份壳的那份文件的安装时间（重装/升级 ⇒ 新戳）。"""




    try:
        return time.strftime("%Y%m%d-%H%M%S", time.localtime(os.path.getmtime(__file__)))
    except Exception:
        return time.strftime("%Y%m%d-%H%M%S")


# L4364-4376：_chain_prev — 安装链读链：看上一步的 ready，返回 (ok, 上一步名, 说明)。
def _chain_prev(step: str) -> tuple[bool, str, str]:
    r"""安装链读链：看上一步的 ready，返回 (ok, 上一步名, 说明)。"""




    try:
        ok, prev = signals().prev_ready(step)
    except Exception as _e:
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
