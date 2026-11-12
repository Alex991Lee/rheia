# L1-2293：Rheia 安装引擎 — Step 5-9 串行安装（Inno Setup 完成 Step 1-4 前置）
r"""Rheia 安装引擎 — Step 5-9 串行安装（Inno Setup 完成 Step 1-4 前置）"""

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
import time



_installRoot = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _installRoot not in sys.path:
    sys.path.insert(0, _installRoot)

import ctypes
import importlib
import winreg
import urllib.request
from urllib.request import urlopen
import re

try:
    from ui.i18n import tr
except ImportError:
    tr = lambda key, **fmt: key


appName = "Rheia"
appDir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
localdataDir = os.path.join(os.environ.get("PROGRAMDATA", ""), appName)
progressFile = os.path.join(localdataDir, "installprogress.json")
runonceKey = r"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce"
runonceValue = "Rheia-Resume"
logFile = os.path.join(localdataDir, "install.log")
wslDistro = os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu"



_signalFile = os.path.join(os.environ.get("TEMP", ""), "okoc_signal.txt")

_cancelFlag = os.path.join(os.environ.get("TEMP", ""), "Rheia_cancel.flag")


_STEP_PROGRESS = {
    2: 26,
    3: 33,
    5: 60,
    55: 62,
    6: 67,
    7: 70,
    8: 88,
    85: 89,
    9: 90,
}

_STEP_STAGE = {
    2: tr("ui.install.stage.wsl"),
    3: tr("ui.install.stage.vmp"),
    5: tr("ui.install.stage.ubuntu"),
    55: tr("ui.install.stage.pip"),
    6: tr("ui.install.stage.node"),
    7: tr("ui.install.stage.openclaw"),
    8: tr("ui.install.stage.systemd"),
    85: tr("ui.install.stage.warmup"),
    9: tr("ui.install.stage.launch"),
}


# L78-85：_writeSignal — 向 signal 统一日志追加一行「文案 | 进度值 | complete/done」。失败静默。
def _writeSignal(text: str, pct: int, mark: str = "complete") -> None:
    r"""向 signal 统一日志追加一行「文案 | 进度值 | complete/done」。失败静默。"""
    try:
        os.makedirs(os.path.dirname(_signalFile), exist_ok=True)
        with open(_signalFile, "a", encoding="utf-8") as f:
            f.write("%s | %d | %s\n" % (text, pct, mark))
    except OSError:
        pass


# L89-91：_isCancelled — card.exe 点了「确认取消」→ 取消标记存在。install.py 检测到后回滚。
def _isCancelled() -> bool:
    r"""card.exe 点了「确认取消」→ 取消标记存在。install.py 检测到后回滚。"""
    return os.path.exists(_cancelFlag)


# L95-97：RebootRequired — 安装需要重启，重启后续装
class RebootRequired(Exception):
    r"""安装需要重启，重启后续装"""
    pass


logger = logging.getLogger(__name__)
# L102-104：log — 统一日志：调用 logger.info。
def log(msg: str, *args) -> None:
    r"""统一日志：调用 logger.info。"""
    logger.info(msg, *args)




# L110-138：bootstrapInstallLogging — bootstrapInstallLogging
def bootstrapInstallLogging(
    log_path: str,
    file_level: int = logging.DEBUG,
) -> None:
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )



    try:
        fh = logging.FileHandler(log_path, encoding="utf-8")
    except (OSError, PermissionError):
        alt = os.path.join(os.environ.get("TEMP", ""), "Rheia_install.log")
        try:
            fh = logging.FileHandler(alt, encoding="utf-8")
        except (OSError, PermissionError):
            fh = logging.NullHandler()

    fh.setLevel(file_level)
    fh.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG)
    root.addHandler(fh)




_REQUIRED_FILES = [
    "kernel/large.py",
    "kernel/scout.py",
    "kernel/glory.py",
    "kernel/large/forge.json",
]

_WARN_IF_MISSING_FILES = [
    "kernel/large/mani.json",
    "kernel/large/ot.json",
]

SYMBOL_CHECKS = [
    ("kernel.scout", ["probe", "settleManifest"]),
    ("kernel.large", ["manifest", "am", "steak", "forge", "run"]),
    ("kernel.glory", ["adopt", "verify", "status", "keepalive"]),
]


# L163-179：checkFileIntegrity — 逐项检查 _REQUIRED_FILES 是否存在，返回缺失项列表。
def checkFileIntegrity() -> list[str]:
    r"""逐项检查 _REQUIRED_FILES 是否存在，返回缺失项列表。"""

    root = appDir
    missing: list[str] = []
    for rel_path in _REQUIRED_FILES:
        full = os.path.join(root, rel_path)
        if not os.path.exists(full):
            missing.append(rel_path)
            logger.error("self_check: missing required file: %s", rel_path)
    for rel_path in _WARN_IF_MISSING_FILES:
        full = os.path.join(root, rel_path)
        if not os.path.exists(full):
            logger.warning("self_check: missing non-essential file: %s", rel_path)
    if not missing:
        logger.info("self_check: all %d required files present", len(_REQUIRED_FILES))
    return missing


# L183-199：checkModuleSymbols — 用 importlib 加载关键模块，检查必需导出符号。返回缺失符号列表。
def checkModuleSymbols() -> list[str]:
    r"""用 importlib 加载关键模块，检查必需导出符号。返回缺失符号列表。"""
    missing: list[str] = []
    for mod_name, symbols in SYMBOL_CHECKS:
        try:
            mod = importlib.import_module(mod_name)
        except Exception as exc:
            missing.append(f"{mod_name}: import failed: {exc}")
            logger.error("self_check: cannot import %s: %s", mod_name, exc)
            continue
        for sym in symbols:
            if not hasattr(mod, sym):
                missing.append(f"{mod_name}.{sym}")
                logger.error("self_check: missing symbol: %s.%s", mod_name, sym)
    if not missing:
        logger.info("self_check: all module symbols present")
    return missing


# L203-229：_checkMani — 校验 mani.json 最小 schema 约束。兼容旧 flat 格式和新 grouped 格式。
def _checkMani(data: object) -> list[str]:
    r"""校验 mani.json 最小 schema 约束。兼容旧 flat 格式和新 grouped 格式。"""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["mani.json must be a dict"]

    if "entries" in data:
        entries = data["entries"]
        if isinstance(entries, dict) and isinstance(entries.get("providers"), list):
            logger.info("selfCheck: mani.json grouped format detected, schema OK")
            return errors
        else:
            errors.append("mani.json: 'entries' must be {providers: [...]}")
            return errors

    for key, decl in data.items():
        if key.startswith("_") or key in ("updated_at", "version"):
            continue
        if not isinstance(decl, dict):
            errors.append(f"mani.json: declaration '{key}' must be a dict")
            continue
        if "type" not in decl:
            errors.append(f"mani.json: 'type' required in declaration '{key}'")
            continue
        if decl["type"] not in ("channel", "automation", "gateway", "provider", "model", "memory", "web_search"):
            errors.append(f"mani.json: unknown type '{decl['type']}' in declaration '{key}'")
    return errors


# L233-242：_checkForge — 校验 forge.json 最小 schema 约束。
def _checkForge(data: object) -> list[str]:
    r"""校验 forge.json 最小 schema 约束。"""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["forge.json must be a dict"]
    required_top = {"visual_tokens", "widget_library", "composite_presets"}
    missing_keys = required_top - data.keys()
    if missing_keys:
        errors.append(f"forge.json missing keys: {missing_keys}")
    return errors


# L246-277：validateJsonSchemas — 校验 mani.json 和 forge.json 的 schema。返回错误列表。
def validateJsonSchemas() -> list[str]:
    r"""校验 mani.json 和 forge.json 的 schema。返回错误列表。"""
    root = appDir
    errors: list[str] = []

    mani_path = os.path.join(root, "kernel", "large", "mani.json")
    forge_path = os.path.join(root, "kernel", "large", "forge.json")

    for label, path, checker in [
        ("mani.json", mani_path, _checkMani),
        ("forge.json", forge_path, _checkForge),
    ]:
        if not os.path.exists(path):
            errors.append(f"{label}: file not found")
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{label}: read/parse failed: {exc}")
            continue
        errs = checker(data)
        if errs:
            errors.extend(errs)
            for e in errs:
                logger.error("self_check: %s", e)
        else:
            logger.info("self_check: %s schema OK", label)

    if not errors:
        logger.info("self_check: JSON schema validation passed")
    return errors


# L281-303：checkAll — 运行全部三项自检，聚合错误。
def checkAll() -> list[str]:
    r"""运行全部三项自检，聚合错误。"""
    all_errors: list[str] = []

    logger.info("self_check: === Layer 1 自检开始 ===")

    missing_files = checkFileIntegrity()
    for f in missing_files:
        all_errors.append(f"missing file: {f}")

    missing_symbols = checkModuleSymbols()
    for s in missing_symbols:
        all_errors.append(f"missing symbol: {s}")

    schema_errors = validateJsonSchemas()
    all_errors.extend(schema_errors)

    if all_errors:
        logger.error("self_check: === %d 项检测失败 ===", len(all_errors))
    else:
        logger.info("self_check: === 全部自检通过 ===")

    return all_errors


# L307-313：step0SelfCheck — Layer 1 安装前自检入口，失败时抛出 RuntimeError。
def step0SelfCheck() -> None:
    r"""Layer 1 安装前自检入口，失败时抛出 RuntimeError。"""
    errors = checkAll()
    if errors:
        msg = "; ".join(errors)
        logger.critical("self_check: 自检失败: %s", msg)
        raise RuntimeError(f"自检失败: {msg}")




# L319-330：loadProgress — loadProgress
def loadProgress():
    if not os.path.exists(progressFile):
        log(f"进度文件不存在: {progressFile}")
        return set()
    try:
        with open(progressFile, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return set(data)
            return set()
    except (json.JSONDecodeError, OSError):
        return set()

# L333-354：markProgress — markProgress
def markProgress(stepNum):

    if _isCancelled():
        log("检测到取消标记，执行回滚")
        rollback()
        raise RuntimeError("安装已取消")

    completed = loadProgress()
    completed.add(stepNum)
    os.makedirs(localdataDir, exist_ok=True)
    fd = os.open(progressFile, os.O_CREAT | os.O_WRONLY | os.O_TRUNC)
    try:
        os.write(fd, json.dumps(sorted(completed)).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    log(f"  markProgress({stepNum})")

    pct = _STEP_PROGRESS.get(stepNum)
    stage = _STEP_STAGE.get(stepNum)
    if pct is not None and stage is not None:
        _writeSignal(stage, pct, "complete")




# L360-379：writeRunonce — writeRunonce
def writeRunonce():


    installpy = os.path.join(appDir, "installer", "install.py")
    cmd = f'"{sys.executable}" "{installpy}" --continue'
    try:
        key = winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, runonceKey)
        winreg.SetValueEx(key, runonceValue, 0, winreg.REG_SZ, cmd)
        winreg.CloseKey(key)
        log("RunOnce 写入成功 (HKLM)")
    except PermissionError:
        if not ctypes.IsUserAnAdmin():
            log("警告: 非管理员模式续装可能失败，建议以管理员身份运行安装包")
        try:
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, runonceKey)
            winreg.SetValueEx(key, runonceValue, 0, winreg.REG_SZ, cmd)
            winreg.CloseKey(key)
            log("RunOnce 写入成功 (HKCU)")
        except Exception as e2:
            log(f"RunOnce 写入失败: {e2}")

# L382-390：clearRunonce — clearRunonce
def clearRunonce():
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            key = winreg.OpenKey(root, runonceKey, 0, winreg.KEY_SET_VALUE)
            winreg.DeleteValue(key, runonceValue)
            winreg.CloseKey(key)
        except OSError:
            pass
    log("RunOnce 条目已删除")


# L394-403：flushLogs — flushLogs
def flushLogs():
    try:
        if os.path.exists(logFile):
            fd = os.open(logFile, os.O_RDWR)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    except OSError:
        pass




# L409-411：runCmd — runCmd
def runCmd(cmd):
    result = subprocess.run(cmd, shell=True, capture_output=True, encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)
    return result.returncode, result.stdout, result.stderr


_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# L418-437：runPolled — 跑一条命令：进程退出就是信号，不再按秒轮询 / 不设上限。
def runPolled(cmd, stageText="", pct=None, tick=0.5, env=None, ramp=None):
    r"""跑一条命令：进程退出就是信号，不再按秒轮询 / 不设上限。"""



    import tempfile
    outF = tempfile.TemporaryFile()
    errF = tempfile.TemporaryFile()
    p = subprocess.Popen(
        cmd, shell=True, stdout=outF, stderr=errF,
        creationflags=_NO_WINDOW, env=env,
    )
    if stageText and pct is not None:
        _writeSignal(stageText, pct, "complete")
    rc = p.wait()
    outF.seek(0); errF.seek(0)
    out = outF.read().decode("utf-8", errors="replace").replace("\\x00", "")
    err = errF.read().decode("utf-8", errors="replace").replace("\\x00", "")
    outF.close(); errF.close()
    return rc, out, err


# L441-446：runDism — 直接调 System32\dism.exe（不经 PATH / shell），与官方命令行逐字对应。
def runDism(args):
    r"""直接调 System32\dism.exe（不经 PATH / shell），与官方命令行逐字对应。"""
    dism = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "dism.exe")
    r = subprocess.run([dism] + list(args), capture_output=True, encoding="utf-8",
                       errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)
    return r.returncode, r.stdout, r.stderr


# L450-464：_featureEnabled — 检测 Windows 功能是否已启用。
def _featureEnabled(featureName):
    r"""检测 Windows 功能是否已启用。"""






    rc, out, _ = runCmd(
        f'dism /online /get-featureinfo /featurename:{featureName} /English')

    for line in out.splitlines():
        if 'State' in line:
            return 'Enabled' in line
    return False









_MIRRORS_FILE = os.path.join(os.path.dirname(__file__), "mirrors.json")
_mirrorsCache: dict[str, list] = {}

_MIRROR_FALLBACK = {
    "node":   [{"name": "官方", "base": "https://nodejs.org/dist", "domestic": False}],
    "npm":    [{"name": "官方", "registry": "https://registry.npmjs.org", "domestic": False}],
    "rootfs": [{"name": "Ubuntu 官方", "url": "https://cloud-images.ubuntu.com/{codename}/current/{codename}-server-cloudimg-amd64-root.tar.xz", "domestic": False}],
}


# L485-501：_loadMirrors — 加载 mirrors.json 中指定字段（node/npm/rootfs）的镜像列表。
def _loadMirrors(kind: str) -> list:
    r"""加载 mirrors.json 中指定字段（node/npm/rootfs）的镜像列表。"""




    if kind in _mirrorsCache:
        return _mirrorsCache[kind]
    try:
        with open(_MIRRORS_FILE, "r", encoding="utf-8-sig") as f:
            raw = json.load(f)
        mirrors = raw.get(kind) or _MIRROR_FALLBACK[kind]
    except Exception:
        mirrors = _MIRROR_FALLBACK[kind]
    mirrors = _orderMirrorsByProbe(mirrors)
    _mirrorsCache[kind] = mirrors
    return mirrors


# L505-542：_sourceSpeeds — 读取 download_python.ps1 一次探针写下的 source_map.json（主机 -> KB/s）。
def _sourceSpeeds() -> dict:
    r"""读取 download_python.ps1 一次探针写下的 source_map.json（主机 -> KB/s）。"""




    cached = getattr(_sourceSpeeds, "_cache", None)
    if cached is not None:
        return cached
    table: dict = {}
    try:
        from urllib.parse import urlsplit
        base = os.environ.get("ProgramData") or r"C:\ProgramData"
        path = os.path.join(base, "Rheia", "source_map.json")
        with open(path, "r", encoding="utf-8-sig") as f:
            doc = json.load(f)
        inv = doc.get("inventory") or {}
        if inv:
            have = [f"{k}={v}" for k, v in inv.items() if v and k not in ("python", "python_version")]
            log("  本机已有依赖（探针回报）: " + (", ".join(have) if have else "无"))
        for row in doc.get("sources") or []:
            try:
                host = urlsplit(str(row.get("url") or "")).netloc.lower()
                kbs = int(row.get("kbs") or 0)
            except Exception:
                continue
            if host and kbs > table.get(host, -1):
                table[host] = kbs
        if table:
            log("源测速表: %d 个主机（source_map.json）" % len(table))
        else:
            log("源测速表为空，沿用默认顺序")
    except FileNotFoundError:
        log("源测速表不存在（source_map.json），沿用默认顺序")
    except Exception as e:
        log(f"源测速表读取失败（{e}），沿用默认顺序")
    _sourceSpeeds._cache = table
    return table


# L546-569：_orderMirrorsByProbe — 按实测吞吐给镜像重排：测得快的在前，没测过的按原顺序跟在后面。
def _orderMirrorsByProbe(mirrors: list) -> list:
    r"""按实测吞吐给镜像重排：测得快的在前，没测过的按原顺序跟在后面。"""
    try:
        speeds = _sourceSpeeds()
        if not speeds or not mirrors:
            return mirrors
        from urllib.parse import urlsplit

        def kbsOf(item) -> int:
            url = item.get("url") or item.get("base") or item.get("registry") or ""
            host = urlsplit(str(url)).netloc.lower()
            return speeds.get(host, -1)

        ordered = sorted(enumerate(mirrors), key=lambda p: (-kbsOf(p[1]), p[0]))
        result = [m for _, m in ordered]
        labels = []
        for m in result:
            url = m.get("url") or m.get("base") or m.get("registry") or ""
            labels.append("%s(%s KB/s)" % (m.get("name", "?"), kbsOf(m)))
        log("  镜像顺序（按实测）: " + ", ".join(labels))
        return result
    except Exception as e:
        log(f"镜像重排失败（{e}），沿用原顺序")
        return mirrors

_OFFICIAL_RELEASES_URL = "https://cloud-images.ubuntu.com/releases/"

_FALLBACK_CODENAME = "noble"


# L577-622：_detectLatestUbuntuCodename — 从官方 releases/ 页枚举「活着的 LTS」，取最新 codename（数据驱动，零硬编码字典）。
def _detectLatestUbuntuCodename() -> tuple[str, bool]:
    r"""从官方 releases/ 页枚举「活着的 LTS」，取最新 codename（数据驱动，零硬编码字典）。"""









    try:
        req = urllib.request.Request(
            _OFFICIAL_RELEASES_URL,
            headers={"User-Agent": "Rheia-installer/1"},
        )
        with urllib.request.urlopen(req) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        alive_lts = []
        for line in html.splitlines():
            m = re.search(r'href="(\d{2}\.\d{2})/"', line)
            if not m:
                continue
            ver = m.group(1)
            if "LTS" not in line:
                continue
            if ("END OF LIFE" in line) or ("END OF REGULAR SUPPORT" in line):
                continue
            cn = re.search(r"\(([A-Z][a-z]+) [A-Z][a-z]+\)", line)
            if not cn:
                continue
            alive_lts.append((ver, cn.group(1).lower()))

        if alive_lts:

            latest = sorted(alive_lts, key=lambda x: [int(n) for n in x[0].split(".")])[-1]
            log(f"Ubuntu 最新 LTS: {latest[0]} ({latest[1]})")
            return latest[1], False

        log("官方 releases/ 页未解析到可用 LTS，显式降级到 %s" % _FALLBACK_CODENAME)
        return _FALLBACK_CODENAME, True

    except Exception as e:
        log(f"Ubuntu codename 探测失败（{e}），显式降级到 {_FALLBACK_CODENAME}")
        return _FALLBACK_CODENAME, True

# L625-627：_resolveUbuntuUrl — 将 URL 中的 {codename} 替换为实际 codename。
def _resolveUbuntuUrl(templateUrl: str, codename: str) -> str:
    r"""将 URL 中的 {codename} 替换为实际 codename。"""
    return templateUrl.replace("{codename}", codename)


# L631-656：_loadRootfsMirrors — 加载 rootfs 镜像列表（从统一 mirrors.json 的 rootfs 字段），
def _loadRootfsMirrors():
    r"""加载 rootfs 镜像列表（从统一 mirrors.json 的 rootfs 字段），"""

    global _mirrorsCache
    if "rootfs_resolved" in _mirrorsCache:
        return _mirrorsCache["rootfs_resolved"]
    raw = _loadMirrors("rootfs")


    codename, degraded = _detectLatestUbuntuCodename()
    if degraded:
        log("WARNING: 已显式降级到 %s（真源探测失败）" % codename)


    mirrors = []
    for m in raw:
        rawUrl = m.get("url", "")
        finalUrl = _resolveUbuntuUrl(rawUrl, codename)
        mirrors.append({
            "name": m["name"],
            "url": finalUrl,
            "domestic": m.get("domestic", False),
        })

    _mirrorsCache["rootfs_resolved"] = mirrors
    return mirrors


_probeCache: dict[str, bool] = {}


# L663-689：_probeReachable — 可达探测：阻塞到 connect 成功/失败；成功/异常就是信号。
def _probeReachable(url: str, perTry: float = 0, fresh: bool = False) -> bool:
    r"""可达探测：阻塞到 connect 成功/失败；成功/异常就是信号。"""
    if not url:
        return False
    if not fresh and url in _probeCache:
        return _probeCache[url]
    import socket
    import urllib.parse
    ok = False
    try:
        u = urllib.parse.urlsplit(url if "//" in url else "https://" + url)
        host = u.hostname
        port = u.port or (443 if u.scheme == "https" else 80)
    except Exception:
        _probeCache[url] = False
        return False
    if not host:
        _probeCache[url] = False
        return False
    try:
        s = socket.create_connection((host, port))
        s.close()
        ok = True
    except OSError:
        ok = False
    _probeCache[url] = ok
    return ok


# L693-714：_applyMirrorPolicy — _applyMirrorPolicy
def _applyMirrorPolicy(mirrors: list) -> list:
    r"""_applyMirrorPolicy"""





    speeds = _sourceSpeeds()
    probed = []
    for idx, m in enumerate(mirrors):
        url = m.get("url") or m.get("base") or m.get("registry") or ""
        host = url.split("//", 1)[-1].split("/", 1)[0].lower() if "//" in url else ""
        kbs = speeds.get(host, -1)
        if kbs < 0:

            kbs = 1 if _probeReachable(url) else 0
        probed.append((kbs, idx, m))
    probed.sort(key=lambda t: (-t[0], t[1]))
    log("  镜像顺序: %s" % ", ".join(
        "%s=%s" % (m.get("name", "?"), ("%d KB/s" % k) if k > 1 else ("可达" if k == 1 else "未通"))
        for k, _i, m in probed))
    return [m for _k, _i, m in probed]
_ROOTFS_MIN_XZ = 48 * 1024 * 1024
_ROOTFS_EXPECT_XZ = 254 * 1024 * 1024
_ROOTFS_MAX_ROUNDS = 3
_ROOTFS_IMPORT_MIN_FREE = 2 * 1024**3


# L722-738：_probeMirror — 探活镜像 URL，分级返回状态；HEAD 响应/异常就是信号，不设时间闸。
def _probeMirror(url):
    r"""探活镜像 URL，分级返回状态；HEAD 响应/异常就是信号，不设时间闸。"""
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Rheia-installer/1"})
        with urllib.request.urlopen(req) as resp:
            code = resp.status
            if 200 <= code < 400:
                return "ok"
            if code == 404:
                return "dead"
            return "other"
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return "dead"
        return "other"
    except Exception:
        return "net"


# L742-749：_fetchOfficialSha256 — 从官方 cloud-images.ubuntu.com/{codename}/current/SHA256SUMS 抓 amd64-root 的权威 sha256。
def _fetchOfficialSha256(codename: str) -> str:
    r"""从官方 cloud-images.ubuntu.com/{codename}/current/SHA256SUMS 抓 amd64-root 的权威 sha256。"""



    return _fetchSha256FromSums(
        f"https://cloud-images.ubuntu.com/{codename}/current/SHA256SUMS"
    )


# L753-760：_deriveSumsUrl — 从 rootfs 的完整 URL 推导同目录的 SHA256SUMS 地址。
def _deriveSumsUrl(rootfs_url: str) -> str:
    r"""从 rootfs 的完整 URL 推导同目录的 SHA256SUMS 地址。"""




    parts = rootfs_url.rsplit("/", 1)
    return parts[0] + "/SHA256SUMS"


# L764-781：_fetchSha256FromSums — 抓指定 SHA256SUMS 文件中 amd64-root 的 sha256，返回 64 位 hex；失败返回空串。
def _fetchSha256FromSums(sumsUrl: str) -> str:
    r"""抓指定 SHA256SUMS 文件中 amd64-root 的 sha256，返回 64 位 hex；失败返回空串。"""
    try:
        req = urllib.request.Request(sumsUrl, headers={"User-Agent": "Rheia-installer/1"})
        with urllib.request.urlopen(req) as resp:
            text = resp.read().decode("utf-8", errors="replace")
        for line in text.splitlines():
            if "server-cloudimg-amd64-root.tar.xz" in line:
                parts = line.strip().split()
                if parts:
                    sha = parts[0]
                    if re.fullmatch(r"[0-9a-f]{64}", sha):
                        return sha
        log("SHA256SUMS 中未找到 amd64-root 条目: %s" % sumsUrl)
        return ""
    except Exception as e:
        log(f"抓取 SHA256SUMS 失败（{e}）: {sumsUrl}")
        return ""


# L785-849：_downloadRootfs — _downloadRootfs
def _downloadRootfs(url, destPath, codename: str = ""):
    destDir = os.path.dirname(destPath)
    os.makedirs(destDir, exist_ok=True)
    partPath = destPath + ".part"
    if os.path.exists(partPath): os.remove(partPath)




    official_sha = _fetchSha256FromSums(_deriveSumsUrl(url))
    if not official_sha and codename:
        log("源 SHA256SUMS 不可用，退回官方 SHA256SUMS")
        official_sha = _fetchOfficialSha256(codename)
    if not official_sha:
        log("WARNING: 未获取到 sha256，下载后将跳过真伪校验")

    import hashlib as _hl
    hasher = _hl.sha256() if official_sha else None

    req = urllib.request.Request(url, headers={"User-Agent": "Rheia-installer/1"})
    log("正在下载...")
    _writeSignal(tr("ui.install.sig.download"), 42, "complete")


    if not _probeReachable(url, fresh=True):
        raise RuntimeError("源暂不可达，换下一个")


    resp = urllib.request.urlopen(req)
    total = int(resp.headers.get("Content-Length") or 0) or _ROOTFS_EXPECT_XZ
    done = 0; chunk = 512 * 1024
    lastWrite = time.monotonic(); lastPct = -1; lastLog = 0
    with resp, open(partPath, "wb") as out:
        while True:
            try:
                buf = resp.read(chunk)
            except Exception as _se:
                raise RuntimeError("源读失败，换源（%s）" % _se)
            if not buf: break
            out.write(buf)
            if hasher is not None:
                hasher.update(buf)
            done += len(buf)
            now = time.monotonic()

            pct = min(58, 42 + int(done * 16 / total))
            if pct != lastPct or (now - lastWrite) >= 0.5:
                _writeSignal(tr("ui.install.sig.download"), pct, "complete")
                lastPct = pct; lastWrite = now
            if done - lastLog >= 10 * 1024 * 1024:
                log("  下载进度: %d MB / %d MB" % (done // (1024*1024), total // (1024*1024)))
                lastLog = done
    if done < _ROOTFS_MIN_XZ: raise RuntimeError("rootfs 文件过小")


    if hasher is not None:
        actual = hasher.hexdigest()
        if actual != official_sha:
            os.remove(partPath)
            log(f"镜像校验失败：sha256 不匹配（期望 {official_sha[:16]}…，实际 {actual[:16]}…）")
            raise RuntimeError("Ubuntu 镜像 sha256 校验失败，文件可能被投毒或损坏")

    os.replace(partPath, destPath)
    log("  下载完成: %d MB" % (done // (1024*1024)))
    return done


# L853-878：_downloadRootfsWithFallback — 镜像轮换下载：所有源都试，连不上就换下一个，轮完再从头来。
def _downloadRootfsWithFallback(mirrors, destPath, codename: str = ""):
    r"""镜像轮换下载：所有源都试，连不上就换下一个，轮完再从头来。"""






    last_err = None
    total = len(mirrors)
    for roundNo in range(1, _ROOTFS_MAX_ROUNDS + 1):
        if roundNo > 1:
            _writeSignal(tr("ui.install.sig.retry_round", n=roundNo), 41, "complete")
            log("  第 %d 轮重试全部镜像源..." % roundNo)
        for i, mirror in enumerate(mirrors):
            _writeSignal(tr("ui.install.sig.probe", n=i + 1, total=total,
                            name=mirror.get("name", "")), 41, "complete")
            log("  下载源 [%d/%d] 第 %d 轮: %s" % (i + 1, total, roundNo, mirror["name"]))
            try:
                _downloadRootfs(mirror["url"], destPath, codename=codename)
                log("  下载成功: %s" % mirror["name"])
                return
            except Exception as e:
                last_err = e
                log("  源 %s 失败（%s），切下一个" % (mirror["name"], e))
    raise RuntimeError("全部镜像源 %d 轮均不可达，最后错误: %s" % (_ROOTFS_MAX_ROUNDS, last_err))


# L882-884：_loadNpmMirrors — 加载 npm 镜像列表（从统一 mirrors.json 的 npm 字段）。
def _loadNpmMirrors():
    r"""加载 npm 镜像列表（从统一 mirrors.json 的 npm 字段）。"""
    return _loadMirrors("npm")



# L889-894：_writeWslScript — _writeWslScript
def _writeWslScript(name, content):
    dest = os.path.join(localdataDir, name)
    os.makedirs(localdataDir, exist_ok=True)
    with open(dest, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    return dest

# L897-902：_wslPath — _wslPath
def _wslPath(windowsPath):
    absPath = os.path.abspath(windowsPath)
    parts = absPath.split(os.sep)
    drive = parts[0][0].lower()
    rest = "/".join(parts[1:])
    return f"/mnt/{drive}/{rest}"


# L906-1071：_buildNodeBash — Generate bash script to install latest stable Node.js inside WSL Ubuntu.
def _buildNodeBash(minVersion="18.0.0", nodeMirrorBases=None):
    r"""Generate bash script to install latest stable Node.js inside WSL Ubuntu."""








    if nodeMirrorBases:

        bases = " ".join(nodeMirrorBases)
        return f"""#!/bin/bash
set -eu
export DEBIAN_FRONTEND=noninteractive
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
NODE_BASES="{bases}"
MIN_VER="{minVersion}"

# 属主修正（2026-10-14）：Node 官方 tarball 内文件属主为 uid 1000，cp -a 会把 1000
# 原样带进 /usr/local；而 OpenClaw 官方更新器以 root 运行，会判 "Package publication
# object has an unsafe identity" 拒绝换包。无论本次是否跳过安装，先把属主拉回 root。
chown -R root:root /usr/local/bin /usr/local/lib 2>/dev/null || true

# 已有够新的 node 就跳过（用 dpkg 做语义化版本比较，不锁 major）
NODE_VER=$(node -v 2>/dev/null | sed 's/^v//' || echo "")
if [ -n "$NODE_VER" ] && dpkg --compare-versions "$NODE_VER" ge "$MIN_VER" 2>/dev/null; then
  echo "[ok] node $NODE_VER >= $MIN_VER, skip"
  exit 0
fi
if [ -n "$NODE_VER" ]; then
  echo "[upgrade] node $NODE_VER < $MIN_VER, upgrading to latest stable..."
else
  echo "[install] Installing latest stable Node.js (mirror rotation)..."
fi

apt-get install -y ca-certificates curl xz-utils 2>/dev/null || true

# 从 index.json 取最新版本（轮换所有源，第一个给出有效版本号的胜出）
LATEST_VER=""
for B in $NODE_BASES; do
  LATEST_VER=$(curl -fsSL "$B/index.json" 2>/dev/null | grep -oP '"version":"v\\K[0-9]+\\.[0-9]+\\.[0-9]+"' | head -1 | tr -d '"')
  [ -n "$LATEST_VER" ] && break
done
if [ -z "$LATEST_VER" ]; then
  echo "[error] cannot determine latest Node.js version from any mirror"
  exit 2
fi
echo "[install] Latest stable: $LATEST_VER"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
# 下载 tarball：轮换所有源，失败切下一个，不盯死一个
DOWNLOAD_OK=0
for B in $NODE_BASES; do
  if curl -fsSL "$B/v$LATEST_VER/node-v$LATEST_VER-linux-x64.tar.xz" -o "$TMP/node.tar.xz" 2>/dev/null; then
    echo "[ok] tarball: $B"
    DOWNLOAD_OK=1
    break
  fi
  echo "[warn] $B failed, next"
done
if [ "$DOWNLOAD_OK" != "1" ]; then
  echo "[error] tarball download failed for v$LATEST_VER on all mirrors"
  exit 3
fi
if [ "$DOWNLOAD_OK" != "1" ]; then
  echo "[error] tarball download failed for v$LATEST_VER"
  exit 3
fi

tar -xJf "$TMP/node.tar.xz" -C "$TMP"
DIR=$(find "$TMP" -maxdepth 1 -type d -name 'node-*' | head -1)
if [ -z "$DIR" ]; then echo "[error] tarball extract empty"; exit 3; fi
cp -r "$DIR"/bin/* /usr/local/bin/
cp -r "$DIR"/lib/* /usr/local/lib/ 2>/dev/null || true
hash -r
NODE_VER=$(node -v 2>/dev/null | sed 's/^v//' || echo "")
echo "[ok] node $NODE_VER installed"
"""


    return f"""#!/bin/bash
set -eu
export DEBIAN_FRONTEND=noninteractive
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
MIN_VER="{minVersion}"

# 属主修正（2026-10-14）：同国内路径——别让 Node tarball 的 uid 1000 污染 /usr/local。
chown -R root:root /usr/local/bin /usr/local/lib 2>/dev/null || true

# 已有够新的 node 就跳过
NODE_VER=$(node -v 2>/dev/null | sed 's/^v//' || echo "")
if [ -n "$NODE_VER" ] && dpkg --compare-versions "$NODE_VER" ge "$MIN_VER" 2>/dev/null; then
  echo "[ok] node $NODE_VER >= $MIN_VER, skip"
  exit 0
fi
if [ -n "$NODE_VER" ]; then
  echo "[upgrade] node $NODE_VER < $MIN_VER, upgrading to latest stable..."
else
  echo "[install] Installing latest stable Node.js..."
fi

apt-get update -y || true
apt-get install -y ca-certificates curl gnupg || true

# setup_current.x — 始终指向最新稳定版，不锁任何 major
curl -fsSL https://deb.nodesource.com/setup_current.x | bash -
apt-get install -y nodejs

NODE_VER=$(node -v 2>/dev/null | sed 's/^v//' || echo "")
if [ -n "$NODE_VER" ] && dpkg --compare-versions "$NODE_VER" ge "$MIN_VER" 2>/dev/null; then
  echo "[ok] apt install node $NODE_VER"
  exit 0
fi

echo "[fallback] apt gave old node, trying tarball..."
apt-get remove -y nodejs npm 2>/dev/null || true

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
# 从 index.json 取最新版本
LATEST_VER=$(curl -fsSL "https://nodejs.org/dist/index.json" 2>/dev/null | grep -oP '"version":"v\\K[0-9]+\\.[0-9]+\\.[0-9]+"' | head -1 | tr -d '"')
if [ -z "$LATEST_VER" ]; then
  LATEST_VER=$(curl -fsSL "https://nodejs.org/dist/latest/" 2>/dev/null | grep -oP 'node-v\\K[0-9]+\\.[0-9]+\\.[0-9]+' | head -1)
fi
if [ -z "$LATEST_VER" ]; then
  # 最后尝试淘宝镜像
  LATEST_VER=$(curl -fsSL "https://npmmirror.com/mirrors/node/index.json" 2>/dev/null | grep -oP '"version":"v\\K[0-9]+\\.[0-9]+\\.[0-9]+"' | head -1 | tr -d '"')
fi
if [ -z "$LATEST_VER" ]; then
  echo "[error] cannot determine latest Node.js version"
  exit 2
fi
echo "[install] Latest stable: $LATEST_VER"

# 先国内镜像，后官方
for URL in \
  "https://npmmirror.com/mirrors/node/v${{LATEST_VER}}/node-v${{LATEST_VER}}-linux-x64.tar.xz" \
  "https://nodejs.org/dist/v${{LATEST_VER}}/node-v${{LATEST_VER}}-linux-x64.tar.xz"; do
  if curl -fsSL "$URL" -o "$TMP/node.tar.xz"; then
    echo "[ok] downloaded tarball"
    break
  fi
done

if [ ! -f "$TMP/node.tar.xz" ]; then
  echo "[error] tarball download failed"
  exit 3
fi

tar -xJf "$TMP/node.tar.xz" -C "$TMP"
DIR=$(find "$TMP" -maxdepth 1 -type d -name 'node-*' | head -1)
if [ -z "$DIR" ]; then echo "[error] tarball extract empty"; exit 3; fi
cp -r "$DIR"/bin/* /usr/local/bin/
cp -r "$DIR"/lib/* /usr/local/lib/ 2>/dev/null || true
hash -r
NODE_VER=$(node -v 2>/dev/null | sed 's/^v//' || echo "")
if [ -n "$NODE_VER" ] && dpkg --compare-versions "$NODE_VER" ge "$MIN_VER" 2>/dev/null; then
    echo "[ok] tarball install node $NODE_VER"
    exit 0
fi
echo "[error] after tarball node version insufficient: $NODE_VER"
exit 3
"""



# L1076-1132：_buildOpenclawBash — Generate bash script to install openclaw CLI from npm registry.
def _buildOpenclawBash(registryUrls):
    r"""Generate bash script to install openclaw CLI from npm registry."""



    regs = " ".join(registryUrls)
    return f"""#!/bin/bash
set -e
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
REGISTRIES="{regs}"
echo "[install] npm cache clean --force"
npm cache clean --force 2>&1 || true
echo "[install] npm uninstall -g openclaw (clean slate)"
npm uninstall -g openclaw 2>&1 || true

INSTALL_OK=0
for R in $REGISTRIES; do
  echo "[install] try registry: $R"
  if npm install -g openclaw@latest --registry "$R" 2>&1; then
    INSTALL_OK=1
    npm config set registry "$R" 2>&1
    echo "[ok] installed via $R"
    break
  fi
  echo "[warn] registry $R failed, next"
done
if [ "$INSTALL_OK" != "1" ]; then
  echo "[error] npm install failed on all registries"
  exit 1
fi

# Use npm root -g to reliably find the global .bin directory
BIN_DIR="$(npm root -g 2>/dev/null)/.bin"
echo "[check] bin dir: $BIN_DIR"
if [ -x "$BIN_DIR/openclaw" ]; then
  echo "[link] found openclaw at $BIN_DIR/openclaw"
  ln -sf "$BIN_DIR/openclaw" /usr/local/bin/openclaw 2>/dev/null || true
  hash -r 2>/dev/null || true
fi

if command -v openclaw >/dev/null 2>&1; then
  OPENCLAW_PATH="$(command -v openclaw)"
  VERSION_OUTPUT="$("$OPENCLAW_PATH" --version 2>&1 || echo 'version failed')"
  echo "[ok] $OPENCLAW_PATH: $VERSION_OUTPUT"
  echo "OKVERSION: $VERSION_OUTPUT"
  echo "[ok] openclaw ready"
  exit 0
fi

# Last resort: try direct exec
if [ -x "$BIN_DIR/openclaw" ]; then
  VERSION_OUTPUT="$("$BIN_DIR/openclaw" --version 2>&1 || echo 'version failed')" && {{ echo "OKVERSION: $VERSION_OUTPUT"; echo "[ok] $BIN_DIR/openclaw: $VERSION_OUTPUT"; exit 0; }}
fi

echo "[error] openclaw binary not found after install"
exit 1
"""


# L1136-1144：_pickImportDir — _pickImportDir
def _pickImportDir():




    importDir = r"C:\wsl-distro"
    os.makedirs(importDir, exist_ok=True)
    log("  WSL 导入目录: %s" % importDir)
    return importDir
# L1146-1169：runWsl — runWsl
def runWsl(cmd):
    env = os.environ.copy()
    env["WSL_UTF8"] = "1"
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    try:
        raw = subprocess.run(cmd, shell=True, capture_output=True, env=env,
                            creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=si)
        for enc in ("utf-8", "gbk", "cp1252"):
            try:
                out = raw.stdout.decode(enc)
                err = raw.stderr.decode(enc)

                out = out.replace("\x00", "").strip()
                err = err.replace("\x00", "").strip()
                return raw.returncode, out, err
            except (UnicodeDecodeError, LookupError):
                continue
        out = raw.stdout.decode("utf-8", errors="replace").replace("\x00", "").strip()
        err = raw.stderr.decode("utf-8", errors="replace").replace("\x00", "").strip()
        return raw.returncode, out, err
    except Exception as e:
        return -1, "", str(e)


_keepaliveProc = None


# L1176-1195：_holdDistroOpen — 握住 WSL 发行版，防止它空闲关停把 Gateway 一起带走。
def _holdDistroOpen() -> None:
    r"""握住 WSL 发行版，防止它空闲关停把 Gateway 一起带走。"""






    global _keepaliveProc
    if _keepaliveProc is not None and _keepaliveProc.poll() is None:
        return
    try:
        _keepaliveProc = subprocess.Popen(
            f"wsl -d {wslDistro} -- sleep infinity",
            shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
        )
        log("  已握住 WSL 发行版（防空闲关停带走 Gateway）")
    except Exception as e:
        log(f"  握发行版失败（非致命）: {e}")


# L1199-1207：_releaseDistro — 放开发行版：安装结束，让壳自己的保活接管。
def _releaseDistro() -> None:
    r"""放开发行版：安装结束，让壳自己的保活接管。"""
    global _keepaliveProc
    if _keepaliveProc is not None:
        try:
            _keepaliveProc.kill()
        except Exception:
            pass
        _keepaliveProc = None


# L1211-1215：_wslEnv — 给 wsl.exe 调用统一加上 WSL_UTF8=1（runPolled 用）。
def _wslEnv():
    r"""给 wsl.exe 调用统一加上 WSL_UTF8=1（runPolled 用）。"""
    e = os.environ.copy()
    e["WSL_UTF8"] = "1"
    return e




_preExistingWsl = None


# L1224-1244：_snapshotPreExistingDistros — 装之前拍一张发行版快照：回滚只清本次自己建的，客户原有的一个都不碰。
def _snapshotPreExistingDistros() -> None:
    r"""装之前拍一张发行版快照：回滚只清本次自己建的，客户原有的一个都不碰。"""




    global _preExistingWsl
    if _preExistingWsl is not None:
        return
    names = []
    try:
        rc, out, _ = runWsl("wsl -l -q")
        if rc == 0:
            for line in out.splitlines():
                n = line.strip().replace("\\x00", "")
                if n and not n.startswith("*"):
                    names.append(n)
    except Exception:
        names = []
    _preExistingWsl = names
    log("  装前已有发行版: %s" % (", ".join(names) if names else "无"))


# L1248-1274：rollback — rollback
def rollback():
    log("执行回滚...")

    if _preExistingWsl and wslDistro in _preExistingWsl:
        log(f"  发行版 {wslDistro} 装前就存在 ⇒ 回滚不注销（客户资产）")
    else:
        for distro in (wslDistro,):
            try:
                runCmd(f"wsl --terminate {distro}")
            except Exception:
                log(f"wsl terminate {distro} 失败，继续回滚")
            try:
                runCmd(f"wsl --unregister {distro}")
            except Exception:
                log(f"wsl unregister {distro} 失败，继续回滚")
    try:
        clearRunonce()
    except Exception:
        log("RunOnce 清理失败，继续回滚")
    try:
        if os.path.exists(localdataDir):
            import shutil
            shutil.rmtree(localdataDir, ignore_errors=True)
            log("PROGRAMDATA 清理完成")
    except Exception:
        log("PROGRAMDATA 清理失败，继续回滚")
    log("回滚完成")




# L1280-1297：step2EnableWsl — Step 2: 检测并启用 WSL2
def step2EnableWsl():
    r"""Step 2: 检测并启用 WSL2"""
    log("Step 2: 检测 WSL2...")

    if _featureEnabled("Microsoft-Windows-Subsystem-Linux"):
        log("Step 2: WSL 功能已启用")
        markProgress(2)
        _writeSignal(tr("ui.install.stage.wsl"), 26, "complete")
        return False

    log("  WSL2 未启用，正在启用...")
    rc, _, err = runDism(["/online", "/enable-feature", "/featurename:Microsoft-Windows-Subsystem-Linux", "/all", "/norestart"])
    if rc != 0 and rc != 3010:
        raise RuntimeError(f"WSL2 启用失败: rc={rc} {err.strip()[:200]}")
    log("Step 2: WSL2 已启用（需要重启）")
    markProgress(2)
    _writeSignal(tr("ui.install.stage.wsl"), 26, "complete")
    return True

# L1300-1317：step3EnableVmp — Step 3: 检测并启用 VMP
def step3EnableVmp():
    r"""Step 3: 检测并启用 VMP"""
    log("Step 3: 检测 VMP...")


    if _featureEnabled("VirtualMachinePlatform"):
        log("Step 3: VMP 已启用")
        markProgress(3)
        _writeSignal(tr("ui.install.stage.vmp"), 30, "complete")
        return False
    log("Step 3: VMP 未启用，正在启用...")
    rc, _, err = runDism(["/online", "/enable-feature", "/featurename:VirtualMachinePlatform", "/all", "/norestart"])
    if rc != 0 and rc != 3010:
        raise RuntimeError(f"VMP 启用失败: rc={rc} {err.strip()[:200]}")
    log("Step 3: VMP 已启用（需要重启）")
    markProgress(3)
    _writeSignal(tr("ui.install.stage.vmp"), 30, "complete")
    return True

# L1320-1333：step4RequestReboot — Step 4: 写 RunOnce + 自动重启
def step4RequestReboot():
    r"""Step 4: 写 RunOnce + 自动重启"""
    writeRunonce()
    flushLogs()
    log("=== WSL/VMP 已启用，正在自动重启电脑，重启后将自动继续安装 ===")

    _writeSignal(tr("ui.install.reboot_comment"), 34, "done")
    flushLogs()

    subprocess.run(
        ["shutdown", "/r", "/t", "10", "/c", tr("ui.install.reboot_comment")],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    os._exit(0)




# L1339-1356：_launchCardProgress — 续装时拉起 {app}\card.exe --progress 显示进度（修复重启后壳消失）。
def _launchCardProgress():
    r"""续装时拉起 {app}\card.exe --progress 显示进度（修复重启后壳消失）。"""




    try:
        card_exe = os.path.join(appDir, "card.exe")
        if os.path.isfile(card_exe):
            subprocess.Popen(
                [card_exe, "--progress"],
                cwd=appDir,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
                close_fds=True,
            )
            log("card --progress 已启动（续装显示）")
    except Exception as e:
        log(f"card --progress 启动失败（非致命）: {e}")


# L1360-1376：_shellAlreadyRunning — 安装器启动前问内核：壳的单实例互斥体是否已被占用。
def _shellAlreadyRunning() -> bool:
    r"""安装器启动前问内核：壳的单实例互斥体是否已被占用。"""




    if sys.platform != "win32":
        return False
    try:
        _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _h = _k32.CreateMutexW(None, False, "Global\\Rheia.Shell.Singleton")
        _err = ctypes.get_last_error()
        if _h:
            _k32.CloseHandle(_h)
        return _err == 183
    except Exception:
        return False


# L1380-1420：_launchBrowser — 安装完成 → 启动 main/main.py（内置浏览器 → Control UI）。
# L...：_markSupportInstallEnd — 安装结束标记：壳首次启动强制 30 秒支持页，之后每次开机 15 秒。
def _markSupportInstallEnd() -> None:
    r"""安装结束标记：壳首次启动强制 30 秒支持页，之后每次开机 15 秒。"""
    try:
        import json as _json
        _p = os.path.join(localdataDir, "support_install_pending.json")
        os.makedirs(localdataDir, exist_ok=True)
        _tmp = _p + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as _f:
            _json.dump({"install_end": True}, _f)
        os.replace(_tmp, _p)
    except Exception as _e:
        log(f"  support install marker failed (non-fatal): {_e}")


def _launchBrowser():
    r"""安装完成 → 启动 main/main.py（内置浏览器 → Control UI）。"""







    try:
        if _shellAlreadyRunning():
            log("已有壳实例在运行：安装器不再拉起第二个（避免双实例）")
            return
        mainPy = os.path.join(appDir, "main", "main.py")
        runtime_dir = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "PythonSide6")
        pythonw = os.path.join(runtime_dir, "pythonw.exe")
        inherit_env = dict(os.environ)
        pyside6_dir = os.path.join(runtime_dir, "Lib", "site-packages", "PySide6")
        if os.path.isdir(pyside6_dir):
            inherit_env["PATH"] = pyside6_dir + os.pathsep + inherit_env.get("PATH", "")
        pyside6_platforms = os.path.join(runtime_dir, "Lib", "site-packages",
                                        "PySide6", "plugins", "platforms")
        if os.path.isdir(pyside6_platforms):
            inherit_env["QT_QPA_PLATFORM_PLUGIN_PATH"] = pyside6_platforms


        if os.path.exists(pythonw):
            subprocess.Popen(
                [pythonw, mainPy], cwd=appDir, env=inherit_env,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
                close_fds=True,
            )
        else:
            subprocess.Popen(
                [sys.executable, mainPy], cwd=appDir, env=inherit_env,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
                close_fds=True,
            )
        log("浏览器已启动（DETACHED）")
    except Exception as e:
        log(f"浏览器启动失败（非致命）: {e}")


# L1424-1427：_isShellOnly — 勾选「仅装壳」时，setup.exe 写了标记文件。True = 跳过 OpenClaw 环境安装，只接驳已有环境。
def _isShellOnly() -> bool:
    r"""勾选「仅装壳」时，setup.exe 写了标记文件。True = 跳过 OpenClaw 环境安装，只接驳已有环境。"""
    flag = os.path.join(os.environ.get("TEMP", ""), "Rheia_shellonly.flag")
    return os.path.exists(flag)


# L1431-1447：_grantProgramDataUsers — 给 ProgramData\Rheia 授权 Users 完全控制。
def _grantProgramDataUsers():
    r"""给 ProgramData\Rheia 授权 Users 完全控制。"""



    try:
        import subprocess as _sp
        os.makedirs(localdataDir, exist_ok=True)
        users_sid = "*S-1-5-32-545"
        _sp.run(
            ["icacls", localdataDir, "/grant", f"{users_sid}:(OI)(CI)F", "/T", "/Q"],
            capture_output=True,
            creationflags=0x08000000,
        )
        log("ProgramData Users 授权完成")
    except Exception as e:
        log(f"ProgramData Users 授权失败（非致命）: {e}")


# L1451-1470：_rejectIfUnsupported — 不支持语言环境红线（in0001）：命中 japanese → 写中文提示 signal 并中止安装。
def _rejectIfUnsupported() -> None:
    r"""不支持语言环境红线（in0001）：命中 japanese → 写中文提示 signal 并中止安装。"""





    try:
        from main.main import check as _i18n_check, REJECT_MSG as _reject_msg
        _state = _i18n_check(probeWsl=False)
        log(f"i18n_guard.check() = {_state}")
    except Exception as e:
        log(f"i18n_guard check 跳过: {e}")
        return
    if _state == "japanese":

        msg = "本ソフトウェアは日本語をサポートしていません。インストーラーは自己破壊します。"
        _writeSignal(msg, 0, "done")
        log("i18n_guard: 检测到不支持的语言环境，中止安装")
        os._exit(0)


# L1474-1499：_grantKernelUsers — 给 {app}\kernel 授权 Users 写权限。
def _grantKernelUsers():
    r"""给 {app}\kernel 授权 Users 写权限。"""






    try:
        import subprocess as _sp
        users_sid = "*S-1-5-32-545"
        kernel_dir = os.path.join(appDir, "kernel")
        for sub in ("", "large", "Official text"):
            target = os.path.join(kernel_dir, sub)
            try:
                os.makedirs(target, exist_ok=True)
            except OSError:
                continue
            _sp.run(
                ["icacls", target, "/grant", f"{users_sid}:(OI)(CI)F", "/T", "/Q"],
                capture_output=True,
                creationflags=0x08000000,
            )
        log("kernel Users 授权完成")
    except Exception as e:
        log(f"kernel Users 授权失败（非致命）: {e}")



# L1504-1523：loadComponents — 读 installer/components.json -> 组件清单。读不到/不合法 => [] 并记账（不假装装过）。
def loadComponents() -> list:
    r"""读 installer/components.json -> 组件清单。读不到/不合法 => [] 并记账（不假装装过）。"""
    p = os.path.join(appDir, "installer", "components.json")
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        log(f"components: 读不到声明 {p}: {exc}")
        return []
    items = data.get("components") if isinstance(data, dict) else None
    if not isinstance(items, list):
        log("components: 声明不是 {components: [...]} 形状，按空处理")
        return []
    out = []
    for it in items:
        if not isinstance(it, dict) or not it.get("id") or not it.get("type"):
            log(f"components: 跳过不完整条目 {it!r}")
            continue
        out.append(it)
    return out


# L1527-1532：_appDirToWsl — 安装目录 -> WSL 视图（C:\x -> /mnt/c/x）。
def _appDirToWsl() -> str:
    r"""安装目录 -> WSL 视图（C:\x -> /mnt/c/x）。"""
    a = appDir or ""
    if len(a) > 1 and a[1] == ":":
        return "/mnt/" + a[0].lower() + a[2:].replace("\\", "/")
    return a


# L1536-1578：applyComponents — 按声明分发：runtime 交给既有步骤，plugin 交给发现程序。动作只在消费端。
def applyComponents() -> list:
    r"""按声明分发：runtime 交给既有步骤，plugin 交给发现程序。动作只在消费端。"""
    comps = loadComponents()
    done = []
    if not comps:
        return done
    wsl_app = _appDirToWsl()
    for c in comps:
        cid, ctype = c.get("id"), c.get("type")
        try:
            if ctype == "runtime":
                log(f"components: {cid} 由既有安装步骤负责（声明只登记，不重复装）")
                done.append({"id": cid, "ok": True, "how": "existing-step"})
                continue
            if ctype == "plugin":
                src = str(c.get("source") or "").strip()
                if not src:
                    log(f"components: {cid} 缺 source，跳过")
                    done.append({"id": cid, "ok": False, "why": "缺 source"})
                    continue
                src_w = src if src.startswith("/") else (wsl_app + "/" + src)


                _app = appDir or ""
                _has_slot = os.path.isfile(os.path.join(_app, "personal-slot", "openclaw.plugin.json"))
                _has_seed = (os.path.isfile(os.path.join(_app, "RheiaPersonalSlot"))
                             and os.path.isfile(os.path.join(_app, "slot.key")))
                if not _has_slot and not _has_seed:
                    log(f"components: 插件 {cid} 未购买（无 personal-slot / 无种子+key）—— 跳过，不注入")
                    done.append({"id": cid, "ok": True, "how": "not-purchased"})
                    continue
                cmd = f"python3 '{wsl_app}/persona/slot_discovery.py' --app-root '{wsl_app}' --source '{src_w}'"
                rc, out, err = runWsl(f"wsl -d {wslDistro} -u root -- bash -c \"{cmd}\"")
                ok = (rc == 0)
                log(f"components: 插件 {cid} {'已注入' if ok else '注入失败'} rc={rc} {((out or '') + (err or '')).strip()[:200]}")
                done.append({"id": cid, "ok": ok, "rc": rc})
                continue
            log(f"components: 未知类型 {ctype}（{cid}）—— 忽略，不假装成功")
            done.append({"id": cid, "ok": False, "why": f"未知类型 {ctype}"})
        except Exception as exc:
            log(f"components: {cid} 异常 {type(exc).__name__}: {exc}")
            done.append({"id": cid, "ok": False, "why": str(exc)[:160]})
    return done


# L1582-1637：run — 全新安装入口 — Step 0-9 串行执行
def run():
    r"""全新安装入口 — Step 0-9 串行执行"""
    bootstrapInstallLogging(logFile)
    _resetAlignment()
    _resetCoreWarmed()
    log("=== 全新安装开始 ===")
    _snapshotPreExistingDistros()
    try:
        _rejectIfUnsupported()
        step0SelfCheck()
        _grantProgramDataUsers()
        _grantKernelUsers()

        if _isShellOnly():
            log("=== 仅装壳模式：跳过 OpenClaw 环境安装，直接接驳 ===")


            try:
                os.remove(progressFile)
            except OSError:
                pass
            _writeSignal(tr("ui.install.sig.shell_only"), 41, "complete")

            applyComponents()
            step85ScoutLargePrep()
            step9Handoff()


            try:
                os.remove(progressFile)
            except OSError:
                pass
            log("=== 安装完成（仅装壳） ===")
            _writeSignal(tr("ui.install.stage.launch"), 100, "done")
            _markSupportInstallEnd()
            _launchBrowser()
            return
        needReboot = False
        if step2EnableWsl():
            needReboot = True
        if step3EnableVmp():
            needReboot = True
        if needReboot:
            step4RequestReboot()
        else:

            _writeSignal(tr("ui.install.sig.wsl_vmp_exist"), 40, "complete")
        _runSteps5to9()
        log("=== 安装完成 ===")
        _writeSignal(tr("ui.install.stage.launch"), 100, "done")
        _markSupportInstallEnd()
        _launchBrowser()
    except RebootRequired:
        log("=== 需要重启，重启后续装 ===")
        raise
    except Exception as e:
        log(f"!!! 安装失败: {e}")

        _writeSignal(tr("ui.install.sig.error", msg=str(e)[:120]), 0, "error")
        rollback()
        raise


# L1641-1653：_runSteps5to9 — 执行 Steps 5-9（WSL2/VMP 已就绪或无需重启）
def _runSteps5to9():
    r"""执行 Steps 5-9（WSL2/VMP 已就绪或无需重启）"""
    step5InstallUbuntu()

    _holdDistroOpen()
    step55InstallAptPip()
    step6InstallNodejs()
    step7InstallOpenclaw()
    step8GatewaySystemd()
    step85ScoutLargePrep()

    applyComponents()
    step9Handoff()


# L1657-1699：resume — 续装入口 — 由 RunOnce 重启后调用
def resume():
    r"""续装入口 — 由 RunOnce 重启后调用"""
    bootstrapInstallLogging(logFile)
    _resetAlignment()




    if _isShellOnly():
        log("=== 仅装壳模式（续装入口）：跳过 WSL/Ubuntu/OpenClaw，直接接驳 ===")
        run()
        return
    log("=== 续装开始 ===")
    _snapshotPreExistingDistros()
    _rejectIfUnsupported()
    _launchCardProgress()
    completed = loadProgress()
    log(f"已完成步骤: {sorted(completed)}")
    steps = [
        (2, step2EnableWsl),
        (3, step3EnableVmp),
        (5, step5InstallUbuntu),
        (55, step55InstallAptPip),
        (6, step6InstallNodejs),
        (7, step7InstallOpenclaw),
        (8, step8GatewaySystemd),
        (85, step85ScoutLargePrep),
        (9, step9Handoff),
    ]
    try:
        for stepNum, stepFn in steps:
            if stepNum in completed:
                log(f"Step {stepNum} 已跳过")
                continue
            log(f"Step {stepNum}...")
            stepFn()
        log("=== 续装完成 ===")
        clearRunonce()
        _writeSignal(tr("ui.install.stage.launch"), 100, "done")
        _markSupportInstallEnd()
        _launchBrowser()
    except Exception as e:
        log(f"!!! 续装失败: {e}")
        rollback()
        raise





# L1706-1750：step5InstallUbuntu — 镜像站下载 rootfs.xz -> wsl --import，替代脆弱的 wsl --install
def step5InstallUbuntu():
    r"""镜像站下载 rootfs.xz -> wsl --import，替代脆弱的 wsl --install"""





    log("Step 5: 装 Ubuntu (镜像站下载 + wsl --import，无条件覆盖)...")

    importDir = _pickImportDir()
    rootfsXz = os.path.join(localdataDir, "wsl-rootfs.tar.xz")
    codename, _degraded = _detectLatestUbuntuCodename()
    policyMirrors = _applyMirrorPolicy(_loadRootfsMirrors())
    log("  镜像策略: 按实测吞吐排序（source_map.json），共 %d 个源" % len(policyMirrors))

    _downloadRootfsWithFallback(policyMirrors, rootfsXz, codename=codename)

    log("  准备 WSL 导入环境...")
    log(f"  全量覆盖：unregister 旧 {wslDistro}，重新 import 覆盖 C:\\wsl-distro...")
    runPolled(f"wsl --unregister {wslDistro}", tr("ui.install.sig.import_wait"), 58)

    log(f"  wsl --import {wslDistro}...")
    _writeSignal(tr("ui.install.sig.import_ubuntu"), 59, "complete")
    cmd = f'wsl --import {wslDistro} "' + importDir + '" "' + rootfsXz + '" --version 2'

    rc, out, err = runPolled(cmd, tr("ui.install.sig.import_wait"), 59, ramp=300)
    if rc != 0: raise RuntimeError("WSL 导入失败: rc=%d %s" % (rc, err.strip()[:200]))
    _writeSignal(tr("ui.install.sig.import_ubuntu"), 60, "complete")
    log("  wsl --import rc=%d" % rc)

    log("  验证 WSL 发行版...")
    rc, out, _ = runWsl(f"wsl -d {wslDistro} -u root -- echo ok")
    if rc != 0 or "ok" not in out:
        raise RuntimeError("WSL 初始化失败: rc=%d out=%s" % (rc, out[:120]))
    log("  WSL 初始化完成")

    rc, _, _ = runCmd("ubuntu config --default-user root")
    log("  ubuntu config: rc=%d" % rc)
    rc, out, _ = runWsl(f"wsl -d {wslDistro} -u root -- lsb_release -a")
    if rc != 0: raise RuntimeError("Ubuntu 验证失败")
    log("  lsb_release: %s" % out.strip())

    try: os.remove(rootfsXz)
    except OSError: pass
    markProgress(5)





# L1757-1828：step55InstallAptPip — WSL 刚装好、装 Node 之前，先 apt 换镜像 → update → 装 pip/venv。
def step55InstallAptPip():
    r"""WSL 刚装好、装 Node 之前，先 apt 换镜像 → update → 装 pip/venv。"""







    log("Step 5.5: apt update + pip/venv...")

    _writeSignal(tr("ui.install.stage.pip"), 61, "complete")




    _useAli = _probeReachable("https://mirrors.aliyun.com/ubuntu/")
    if _useAli:
        mirror_sed = (
            "if [ -f /etc/apt/sources.list.d/ubuntu.sources ]; then "
            "sed -i 's|http://archive.ubuntu.com|http://mirrors.aliyun.com|g; "
            "s|https://archive.ubuntu.com|https://mirrors.aliyun.com|g; "
            "s|http://security.ubuntu.com|http://mirrors.aliyun.com|g; "
            "s|https://security.ubuntu.com|https://mirrors.aliyun.com|g' "
            "/etc/apt/sources.list.d/ubuntu.sources 2>/dev/null || true; "
            "fi; "
            "if [ -f /etc/apt/sources.list ]; then "
            "sed -i 's|http://archive.ubuntu.com|http://mirrors.aliyun.com|g; "
            "s|https://archive.ubuntu.com|https://mirrors.aliyun.com|g; "
            "s|http://security.ubuntu.com|http://mirrors.aliyun.com|g; "
            "s|https://security.ubuntu.com|https://mirrors.aliyun.com|g' "
            "/etc/apt/sources.list 2>/dev/null || true; "
            "fi; "
        )
        log("  apt 源：阿里镜像（探测可达）")
    else:
        mirror_sed = ""
        log("  apt 源：非国内 → 官方源")

    bash = (
        "export DEBIAN_FRONTEND=noninteractive; "
        "export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; "
        + mirror_sed +
        "echo '[pip-venv] apt update...'; "
        "apt-get update; "
        "echo '[pip-venv] apt install python3-pip python3-venv...'; "
        "apt-get install -y python3-pip python3-venv; "
        "echo '[pip-venv] verify:'; "
        "python3 -m pip --version 2>&1 || echo 'PIP_MISSING'"
    )
    rc, out, err = runWsl(
        f'wsl -d {wslDistro} -u root -- bash -c "{bash}"'
    )
    combined = (out or "") + (err or "")
    log(f"  apt/pip 结果: rc={rc} {(combined).strip()[:300]}")
    _writeSignal(tr("ui.install.stage.pip"), 62, "complete")


    if rc == 0 and "PIP_MISSING" not in combined and "pip" in combined.lower():
        log("  pip ready")
        try:
            import os as _os
            _os.makedirs(localdataDir, exist_ok=True)
            marker = _os.path.join(localdataDir, "pip_venv_done")
            with open(marker, "w") as f:
                f.write("1")
        except OSError:
            pass
    else:
        log(f"  Warning: pip 未装成功（rc={rc}），运行时后台补装")

    markProgress(55)









# L1839-1871：step6InstallNodejs — Install Node.js >= 22 inside WSL Ubuntu.
def step6InstallNodejs():
    r"""Install Node.js >= 22 inside WSL Ubuntu."""




    log("Step 6: Install Node.js...")


    mirrors = _applyMirrorPolicy(_loadMirrors("node"))
    nodeMirrorBases = [m["base"].rstrip("/") for m in mirrors]
    log("  Node.js 镜像（轮换）: %s" % ", ".join(nodeMirrorBases))

    bash = _buildNodeBash(nodeMirrorBases=nodeMirrorBases)
    scriptPath = _writeWslScript("install_node.sh", bash)
    wslPath = _wslPath(scriptPath)

    log("  Running Node.js installer inside WSL...")
    _writeSignal(tr("ui.install.stage.node"), 63, "complete")
    rc, out, err = runPolled(f'wsl -d {wslDistro} -u root -- bash "{wslPath}"',
                            tr("ui.install.stage.node"), 63, env=_wslEnv())
    if rc != 0:
        raise RuntimeError(f"Node.js installation failed: rc={rc} {err.strip()[:200]}")
    _writeSignal(tr("ui.install.stage.node"), 67, "complete")

    rc, out, _ = runWsl(f'wsl -d {wslDistro} -- bash -c "node --version"')
    if rc != 0:
        raise RuntimeError("node --version failed after installation")
    log(f"  node --version: {out.strip()}")

    try: os.remove(scriptPath)
    except OSError: pass
    markProgress(6)





# L1878-1916：_backupWslOpenclaw — 动手前把 WSL 侧 ~/.openclaw 整份备份到 %PROGRAMDATA%\Rheia\backup\。
def _backupWslOpenclaw() -> str:
    r"""动手前把 WSL 侧 ~/.openclaw 整份备份到 %PROGRAMDATA%\Rheia\backup\。"""









    probe_cmd = (f'wsl -d {wslDistro} -u root -- bash -c '
                 f'"test -d /root/.openclaw && echo yes || echo no"')
    rc, out, _ = runWsl(probe_cmd)
    if rc != 0 or "yes" not in (out or ""):
        return "fresh"
    import datetime as _dt
    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    winDir = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia", "backup")
    try:
        os.makedirs(winDir, exist_ok=True)
    except OSError as e:
        return f"fail:建备份目录失败 {e}"
    wslTgt = f"/mnt/c/ProgramData/Rheia/backup/openclaw-{ts}.tar.gz"
    cmd = (f'wsl -d {wslDistro} -u root -- bash -c '
           f'"mkdir -p /mnt/c/ProgramData/Rheia/backup && '
           f'tar -czf {wslTgt} -C /root .openclaw && echo BACKUP_OK"')
    rc, out, err = runWsl(cmd)
    if rc != 0 or "BACKUP_OK" not in (out or ""):
        return f"fail:{(err or out or '').strip()[:200]}"
    winFile = os.path.join(winDir, f"openclaw-{ts}.tar.gz")
    try:
        sz = os.path.getsize(winFile)
    except OSError:
        sz = 0
    if sz <= 0:
        return f"fail:备份文件异常（{sz} 字节）"
    log(f"  WSL 侧 ~/.openclaw 已备份 -> {winFile}（{sz // 1024} KB）")
    return f"ok:{winFile}"


# L1920-1954：step7InstallOpenclaw — Install openclaw CLI globally via npm registry mirror rotation
def step7InstallOpenclaw():
    r"""Install openclaw CLI globally via npm registry mirror rotation"""
    log("Step 7: Install OpenClaw CLI...")


    _bk = _backupWslOpenclaw()
    if str(_bk).startswith("fail:"):
        raise RuntimeError(f"WSL 侧状态备份失败，拒绝继续安装（不许清库）：{str(_bk)[5:]}")
    log(f"  WSL 侧状态: {_bk}")


    rc, out, _ = runWsl(f'wsl -d {wslDistro} -- bash -c "openclaw --version 2>&1"')
    if rc == 0 and out.strip():
        log(f"  现有 openclaw: {out.strip()}，重新安装 latest 覆盖...")
    else:
        log("  openclaw 未安装，全新安装...")

    mirrors = _loadNpmMirrors()
    policyMirrors = _applyMirrorPolicy(mirrors)
    log("  npm 镜像策略: 全部源按可达性排序（语言不作禁用判据）")
    registryUrls = [m["registry"].rstrip("/") for m in policyMirrors]
    log("  npm 镜像（轮换）: %s" % ", ".join(registryUrls))
    _writeSignal(tr("ui.install.sig.openclaw"), 70, "complete")
    bash = _buildOpenclawBash(registryUrls)
    scriptPath = _writeWslScript("install_openclaw.sh", bash)
    wslPath = _wslPath(scriptPath)
    rc, out, err = runPolled(f'wsl -d {wslDistro} -u root -- bash "{wslPath}"',
                             tr("ui.install.sig.openclaw_wait"), 70, env=_wslEnv())
    if rc != 0:
        log(f"  openclaw 安装失败 (rc={rc}): {(err+out).strip()[:2000]}")
        raise RuntimeError(f"openclaw 安装失败: {(err+out).strip()[:2000]}")
    log(f"  openclaw --version: {out.strip()}")
    try: os.remove(scriptPath)
    except OSError: pass
    markProgress(7)






# L1962-2105：step8GatewaySystemd — 启用 WSL systemd，安装并启动 OpenClaw gateway（脱壳持久进程）
def step8GatewaySystemd():
    r"""启用 WSL systemd，安装并启动 OpenClaw gateway（脱壳持久进程）"""
    log("Step 8: Gateway systemd...")



    rc, out, _ = runWsl(f'wsl -d {wslDistro} -u root -- bash -c "cat /etc/wsl.conf 2>/dev/null"')
    systemd_enabled = (rc == 0 and "systemd=true" in out)
    if systemd_enabled:
        log("  systemd 已启用，跳过 wsl.conf 写入")

    rc, out, _ = runWsl(f'wsl -d {wslDistro} -u root -- bash -c "loginctl show-user root 2>/dev/null | grep ^Linger="')
    linger_enabled = (rc == 0 and "Linger=yes" in out)

    rc, out, _ = runWsl(f'wsl -d {wslDistro} -u root -- bash -c "openclaw gateway status"')
    out_lower = out.lower()
    gateway_ok = (rc == 0 and "disabled" not in out_lower and "not running" not in out_lower)
    if gateway_ok and linger_enabled:
        log(f"  Gateway 已就绪: {out.strip()[:200]}")
        wslMarker = os.path.join(localdataDir, "wsl_full_install.done")
        with open(wslMarker, "w", encoding="utf-8") as f:
            f.write("ok")
        markProgress(8)
        log("  Step 8: 跳过（Gateway 已就绪）")
        return
    elif rc == 0 and not gateway_ok:
        log(f"  Gateway 状态异常（非就绪），执行完整安装: {out.strip()[:200]}")



    if not systemd_enabled:
        log("  启用 WSL systemd...")
        runWsl(f'wsl -d {wslDistro} -u root -- bash -c "echo -e \'[boot]\\nsystemd=true\' > /etc/wsl.conf"')

        runCmd(f"wsl --terminate {wslDistro}")
        _releaseDistro()
        _holdDistroOpen()

        rc, out, _ = runWsl(f"wsl -d {wslDistro} -u root -- echo ok")
        if rc != 0 or "ok" not in out:
            raise RuntimeError("WSL systemd 未就绪: rc=%d out=%s" % (rc, out[:120]))
        log("  WSL systemd 就绪")


    if not linger_enabled:
        log("  启用 loginctl linger root（重启后 systemd 兜底）...")
        _writeSignal(tr("ui.install.sig.enable_systemd"), 82, "complete")
        rc, out, _ = runWsl(f'wsl -d {wslDistro} -u root -- bash -c "loginctl enable-linger root"')
        if rc != 0:
            log(f"  Warning: enable-linger 失败: {out[:200]}")
        else:
            log("  linger 已启用")



    log("  安装 Gateway systemd service（重启兜底）...")
    _writeSignal(tr("ui.install.sig.install_gw"), 84, "complete")
    rc, out, err = runPolled(f'wsl -d {wslDistro} -u root -- bash -c "openclaw gateway install"',
                             tr("ui.install.sig.install_gw"), 84, env=_wslEnv())
    if rc != 0:
        log(f"  Warning: gateway install rc={rc} (非致命): {err.strip()[:200]}")








    log("  清理迁移锁...")



    try:
        from main.main import acquire_lifecycle as _acq_i, release_lifecycle as _rel_i
    except Exception:
        _acq_i = _rel_i = None
    _owned_i = False
    if _acq_i is not None:
        try:
            _owned_i = bool(_acq_i("installer.migration_lock"))
        except Exception:
            _owned_i = False
    if _acq_i is not None and not _owned_i:
        log("  另一条重启链在跑 ⇒ 本次不并发清锁（交给它）")
    else:
        try:
            from main.main import clear_migration_lock
            clear_migration_lock(lambda msg: log("    " + msg))
        except Exception as _e:
            log(f"  Warning: 清理迁移锁异常（非致命）: {_e}")
        finally:
            if _owned_i and _rel_i is not None:
                try:
                    _rel_i("installer.migration_lock")
                except Exception:
                    pass



    _writeMinimalGatewayConfig()





    log("  gateway start")

    rc, out, err = runPolled(
        f'wsl -d {wslDistro} -u root -- bash -c '
        f'"export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; '
        f'openclaw gateway start 2>&1"',
        tr("ui.install.sig.start_gw"), 86, env=_wslEnv(),
    )
    if rc != 0:
        log(f"  Warning: gateway start rc={rc} (非致命): {(out + err).strip()[:200]}")


    log("  等待 Gateway 就绪...")
    _writeSignal(tr("ui.install.sig.start_gw"), 86, "complete")
    rc, out, _ = runWsl(
        f'wsl -d {wslDistro} -- bash -c "curl -s -o /dev/null -w \'%{{http_code}}\' http://127.0.0.1:18789"')
    if rc != 0 or out.strip() not in ("200", "302", "401", "403"):
        raise RuntimeError("Gateway 未就绪: rc=%d code=%s" % (rc, out.strip()[:40]))
    log("  Gateway ready (HTTP %s)" % out.strip())


    rc, out, _ = runWsl(
        f'wsl -d {wslDistro} -- bash -c "pgrep -f \'index.js gateway\' | head -1"')
    _stablePid = (out.strip().splitlines()[0] if out.strip() else "")
    log("  Gateway 进程 pid=%s" % _stablePid)


    rc, out, _ = runPolled(f'wsl -d {wslDistro} -u root -- bash -c "openclaw gateway status"', tr("ui.install.sig.start_gw"), 88, env=_wslEnv())
    if rc != 0:
        log(f"  Warning: gateway status 异常 (非致命): {out.strip()[:200]}")
    else:
        log(f"  gateway status: {out.strip()[:200]}")


    wslMarker = os.path.join(localdataDir, "wsl_full_install.done")
    with open(wslMarker, "w", encoding="utf-8") as f:
        f.write("ok")
    markProgress(8)




# L2111-2145：安装预热核心数据 — 安装器真实等待 probe/settle/large 完成，再写 core_warmed.json。
def _resetAlignment() -> None:
    r"""重装壳后清掉上一包的对齐锚点，保证首启重新自愈。"""
    try:
        os.remove(os.path.join(localdataDir, "alignment.json"))
    except OSError:
        pass


def _resetCoreWarmed() -> None:
    r"""全新安装开始时清掉旧预热标记；续装不能动它。"""
    try:
        os.remove(os.path.join(localdataDir, "core_warmed.json"))
    except OSError:
        pass


def _writeCoreWarmed() -> None:
    r"""写预热完成标记；boot_worker 据此跳过远程 scout。"""
    try:
        mani = os.path.join(appDir, "kernel", "large", "mani.json")
        st = os.stat(mani)
        os.makedirs(localdataDir, exist_ok=True)
        target = os.path.join(localdataDir, "core_warmed.json")
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({
                "ok": True,
                "mani_size": st.st_size,
                "mani_mtime_ns": st.st_mtime_ns,
            }, f, ensure_ascii=False)
        os.replace(tmp, target)
    except OSError as exc:
        log(f"  core_warmed 标记写入失败（非致命）: {exc}")


# L...：_spawnCollaboratorsWarmup — 核心预热完成后，用脱离进程补字典，不再阻塞安装。
def _spawnCollaboratorsWarmup() -> None:
    r"""核心预热完成后，用脱离进程补字典，不再阻塞安装。"""
    try:
        exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.isfile(exe):
            exe = sys.executable
        script = os.path.join(appDir, "installer", "collaborators_warmup.py")
        subprocess.Popen(
            [exe, script],
            cwd=appDir,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0),
            close_fds=True,
        )
    except Exception as exc:
        log(f"  collaborators warmup 子进程启动失败（非致命）: {exc}")


def _scoutWarmupWorker() -> bool:
    r"""同步预热核心数据：probe + settle + large；完成才写标记。"""
    try:
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from kernel import scout, large
        from kernel.scout import _MANIFEST_PATH
        scout.probe(fetchCollaborators=False)
        scout.settleManifest()
        if _MANIFEST_PATH.exists() and _MANIFEST_PATH.stat().st_size > 10:
            large.run()
            _writeCoreWarmed()
            _spawnCollaboratorsWarmup()
            log("  Step 8.5: 核心数据就绪")
            return True
        log("  Step 8.5: mani.json 尚未生成，运行时自动补充")
        return False
    except Exception as exc:
        log(f"  Step 8.5: 预热失败 ({exc}) — 运行时将自动补充")
        return False

# L2146-2159：step85ScoutLargePrep — Step 8.5：同步预热核心数据；89% 只在实际完成后上报。
def step85ScoutLargePrep():
    r"""Step 8.5：同步预热核心数据；89% 只在实际完成后上报。"""
    log("Step 8.5: 预热核心数据（同步）...")
    _writeSignal(tr("ui.install.stage.warmup"), 89, "")
    _ok = _scoutWarmupWorker()
    _writeSignal(tr("ui.install.stage.warmup"), 89, "complete")
    if not _ok:
        log("  Step 8.5: 预热未完成，首启将回退 scout.probe")
    markProgress(85)

# L2159-2171：_writeMinimalGatewayConfig — （已退役）不写任何 provider 声明。
def _writeMinimalGatewayConfig():
    r"""（已退役）不写任何 provider 声明。"""









    log("  _writeMinimalGatewayConfig: 已退役，官方 provider 靠插件自发现，不写 openclaw.json")
    return


# L2175-2243：step9Handoff — Step 9: Handoff — 探针找发行版 → 读 token/mode → glory.adopt()
def step9Handoff():
    r"""Step 9: Handoff — 探针找发行版 → 读 token/mode → glory.adopt()"""


    log("Step 9: Handoff...")
    _writeSignal(tr("ui.install.sig.extract_gw"), 90, "complete")

    gwUrl = "http://127.0.0.1:18789"
    gwToken = ""
    gwMode = "token"


    if appDir not in sys.path:
        sys.path.insert(0, appDir)
    try:
        from kernel.scout import findOpenClawDistro, readGatewayAuth
        if _isShellOnly():
            probed = findOpenClawDistro()
            if probed:
                auth = readGatewayAuth(probed)
                if auth.get("ok"):
                    gwUrl = auth.get("url", gwUrl)
                    gwToken = auth.get("token", "")
                    gwMode = auth.get("mode", "token")
                else:
                    raise RuntimeError(f"接驳失败：发行版 {probed} 无有效 gateway.auth")
            else:
                raise RuntimeError("接驳失败：未找到可接驳的 OpenClaw 发行版")
        else:

            auth = readGatewayAuth(wslDistro)
            if auth.get("ok"):
                gwUrl = auth.get("url", gwUrl)
                gwToken = auth.get("token", "")
                gwMode = auth.get("mode", "token")
    except SystemExit:
        raise
    except Exception:
        pass


    if not gwToken:
        rc, out, _ = runWsl(f'wsl -d {wslDistro} -u root -- bash -c "cat ~/.openclaw/openclaw.json 2>/dev/null"')
        if rc == 0:
            try:
                cfg = json.loads(out)
                gwToken = cfg.get("gateway", {}).get("auth", {}).get("token", "")
                gwMode = cfg.get("gateway", {}).get("auth", {}).get("mode", gwMode)
            except json.JSONDecodeError:
                pass


    rc, out, _ = runWsl(f'wsl -d {wslDistro} -- bash -c "openclaw gateway status"')
    if rc == 0:
        for line in out.splitlines():
            ll = line.lower()
            if "dashboard" in ll or "control ui" in ll:
                parts = line.strip().split(None, 1)
                if len(parts) > 1 and "http" in parts[1].lower():
                    gwUrl = parts[1].strip()
                break


    import kernel.glory as _glory_mod
    _glory_mod._ensureGloryDir()
    from kernel.glory import adopt
    adopt(gwUrl, gwToken)
    log(f"  glory 已接管连接: url={gwUrl} mode={gwMode}")
    markProgress(9)





# L2250-2267：parseArgs — parseArgs
def parseArgs():


    if _isShellOnly():
        log("检测到「仅安装 Rheia」标记：跳过续装，走壳安装")
        run()
    elif "--continue" in sys.argv:
        resume()
    elif loadProgress():


        log("检测到已有安装进度，自动进入续装")
        resume()
    else:
        run()

    _releaseDistro()
    os._exit(0)
# L2269-2288：_prune_stale_session_path — _prune_stale_session_path
def _prune_stale_session_path() -> None:
    r"""_prune_stale_session_path"""





    try:
        import sqlite3
        p = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia", "baseline_pool.db")
        if not os.path.isfile(p):
            return
        con = sqlite3.connect(p)
        try:
            con.execute("DELETE FROM user_prefs WHERE key='web_last_path'")
            con.commit()
        finally:
            con.close()
    except Exception:
        pass


if __name__ == "__main__":
    _prune_stale_session_path()
    parseArgs()