"""Rheia 安装引擎 — Step 5-9 串行安装（Inno Setup 完成 Step 1-4 前置）"""

try:  # 全局静默子进程守卫（设计约定：加载/安装链不得弹控制台）
    from lib.no_window import install as _install_no_window
    _install_no_window()
except Exception:  # noqa: BLE001
    pass

import json
import logging
import os
import subprocess
import sys
import time

# 无头运行（直接 python install.py）时，脚本目录是 installer/，需把项目根加入 sys.path，
# 否则 step0SelfCheck / step85 里 import kernel.* 会找不到包。
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

# ── signal 统一日志：安装层各阶段按时间顺序 append 写，card 内嵌 transplay 读最后一行 ──
# 格式：文案 | 进度值 | complete/done；放 %TEMP%（临时通信文件，卸载不会删 TEMP）
_signalFile = os.path.join(os.environ.get("TEMP", ""), "okoc_signal.txt")
# 取消标志：card.exe 点「确认取消」会写，install.py 检测到后回滚
_cancelFlag = os.path.join(os.environ.get("TEMP", ""), "Rheia_cancel.flag")
# 精确进度区间（解压 0-5，下载 5-25，dism 26-40，Ubuntu 41-60，node 61-65，
#   npm openclaw 66-85，systemd 81-90，gateway 91-95，拉起主界面 96-100）
_STEP_PROGRESS = {
    2: 26,   # dism 拉起 WSL
    3: 33,   # dism 拉起 VMP
    5: 60,   # 安装 Ubuntu（导入完成）
    55: 62,  # 配置 pip/venv 环境（pip 装好）
    6: 67,   # 安装 Node.js（装好）
    7: 70,   # npm 安装 OpenClaw（装好）
    8: 88,   # systemd + Gateway（就绪）
    85: 89,  # 预热核心数据
    9: 90,   # 提取 Gateway 信息
}
# 文案映射：每个 step 对应的文案（由安装层动态写，card 只显示）
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


def _writeSignal(text: str, pct: int, mark: str = "complete") -> None:
    """向 signal 统一日志追加一行「文案 | 进度值 | complete/done」。失败静默。"""
    try:
        os.makedirs(os.path.dirname(_signalFile), exist_ok=True)
        with open(_signalFile, "a", encoding="utf-8") as f:
            f.write("%s | %d | %s\n" % (text, pct, mark))
    except OSError:
        pass


def _isCancelled() -> bool:
    """card.exe 点了「确认取消」→ 取消标记存在。install.py 检测到后回滚。"""
    return os.path.exists(_cancelFlag)


class RebootRequired(Exception):
    """安装需要重启，重启后续装"""
    pass

# ── 日志 ──
logger = logging.getLogger(__name__)
def log(msg: str, *args) -> None:
    """统一日志：调用 logger.info。"""
    logger.info(msg, *args)


# ── 日志引导（原 loggingBootstrap.py，逻辑不变）──

def bootstrapInstallLogging(
    log_path: str,
    file_level: int = logging.DEBUG,
) -> None:
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 容错：日志文件可能被 admin 创建、普通用户写不进去（Permission denied）。
    # 写不进 install.log 时，降级到 %TEMP% 下的日志，绝不因日志崩溃中断安装。
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


# ── 安装前自检（原 selfCheck.py，逻辑不变）──

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


def checkFileIntegrity() -> list[str]:
    """逐项检查 _REQUIRED_FILES 是否存在，返回缺失项列表。
    _WARN_IF_MISSING_FILES 中缺失仅打 warning，不阻止安装。"""
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


def checkModuleSymbols() -> list[str]:
    """用 importlib 加载关键模块，检查必需导出符号。返回缺失符号列表。"""
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


def _checkMani(data: object) -> list[str]:
    """校验 mani.json 最小 schema 约束。兼容旧 flat 格式和新 grouped 格式。"""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["mani.json must be a dict"]
    # 新 grouped 格式: {"entries": {"providers": [...]}, "version": ..., "updated_at": ...}
    if "entries" in data:
        entries = data["entries"]
        if isinstance(entries, dict) and isinstance(entries.get("providers"), list):
            logger.info("selfCheck: mani.json grouped format detected, schema OK")
            return errors
        else:
            errors.append("mani.json: 'entries' must be {providers: [...]}")
            return errors
    # 旧 flat 格式: {"name": {"type": "provider", ...}, ...}
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


def _checkForge(data: object) -> list[str]:
    """校验 forge.json 最小 schema 约束。"""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["forge.json must be a dict"]
    required_top = {"visual_tokens", "widget_library", "composite_presets"}
    missing_keys = required_top - data.keys()
    if missing_keys:
        errors.append(f"forge.json missing keys: {missing_keys}")
    return errors


def validateJsonSchemas() -> list[str]:
    """校验 mani.json 和 forge.json 的 schema。返回错误列表。"""
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


def checkAll() -> list[str]:
    """运行全部三项自检，聚合错误。"""
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


def step0SelfCheck() -> None:
    """Layer 1 安装前自检入口，失败时抛出 RuntimeError。"""
    errors = checkAll()
    if errors:
        msg = "; ".join(errors)
        logger.critical("self_check: 自检失败: %s", msg)
        raise RuntimeError(f"自检失败: {msg}")


# ── 进度管理 ──

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

def markProgress(stepNum):
    # 取消检测：card.exe 点了「确认取消」→ 回滚（清 WSL/Ubuntu/PROGRAMDATA）
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
    # 同步写 signal 统一日志（文案 + 进度值 + complete 标记，card 内嵌 transplay 读）
    pct = _STEP_PROGRESS.get(stepNum)
    stage = _STEP_STAGE.get(stepNum)
    if pct is not None and stage is not None:
        _writeSignal(stage, pct, "complete")


# ── RunOnce 续装管理 ──

def writeRunonce():
    # 续装指向 install.py --continue；card.exe 已打进 {app}，
    # resume() 开头会拉起 {app}\card.exe --progress 显示续装进度。
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

def clearRunonce():
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            key = winreg.OpenKey(root, runonceKey, 0, winreg.KEY_SET_VALUE)
            winreg.DeleteValue(key, runonceValue)
            winreg.CloseKey(key)
        except OSError:
            pass
    log("RunOnce 条目已删除")


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


# ── Shell 工具 ──

def runCmd(cmd, timeout=None):
    result = subprocess.run(cmd, shell=True, capture_output=True, timeout=timeout, encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)
    return result.returncode, result.stdout, result.stderr


_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def runPolled(cmd, stageText="", pct=None, tick=0.5, env=None, ramp=None):
    """跑一条命令，轮询问"退了没有"；没退就每 2 秒写一次信号。

     2026-10-28 令：安装期不设 await / timeout。
    进程的"条件"就是**已退出**：没退出就继续问、继续报等待秒数，绝不静默干等。
    """
    import tempfile
    outF = tempfile.TemporaryFile()
    errF = tempfile.TemporaryFile()
    p = subprocess.Popen(
        cmd, shell=True, stdout=outF, stderr=errF,
        creationflags=_NO_WINDOW, env=env,
    )
    t0 = time.monotonic()
    lastBeat = -10.0
    while p.poll() is None:
        if _isCancelled():
            try:
                p.kill()
            except Exception:
                pass
            raise RuntimeError("安装已取消")
        now = time.monotonic()
        if stageText and pct is not None and (now - lastBeat) >= 2.0:
            _p = pct
            if ramp and ramp > 0:
                _p = min(pct + 0.9, pct + (now - t0) / ramp)
            _writeSignal(stageText, _p, "complete")
            lastBeat = now
        time.sleep(tick)
    outF.seek(0); errF.seek(0)
    out = outF.read().decode("utf-8", errors="replace").replace("\\x00", "")
    err = errF.read().decode("utf-8", errors="replace").replace("\\x00", "")
    outF.close(); errF.close()
    return p.returncode, out, err


def runDism(args):
    """直接调 System32\\dism.exe（不经 PATH / shell），与官方命令行逐字对应。"""
    dism = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "dism.exe")
    r = subprocess.run([dism] + list(args), capture_output=True, encoding="utf-8",
                       errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)
    return r.returncode, r.stdout, r.stderr


def _featureEnabled(featureName):
    """检测 Windows 功能是否已启用。

    用 DISM 加 /English 参数强制英文输出，匹配 "State : Enabled"。
    不依赖 PowerShell Get-WindowsOptionalFeature 的 State 文本——日语等非英文
    系统下 PowerShell 会把 State 本地化（如「有効」），导致 'Enabled' in out 误判，
    进而误判「功能未启用」触发不必要的重启。
    """
    rc, out, _ = runCmd(
        f'dism /online /get-featureinfo /featurename:{featureName} /English')
    # State 行形如 "State : Enabled" / "State : Disabled"
    for line in out.splitlines():
        if 'State' in line:
            return 'Enabled' in line
    return False


# ── 镜像策略：不按语言、不按 IP，只按实测吞吐（source_map.json）──
# 语言只决定界面文字。原来的 _isChineseSystem()/_isChinaIP()/_useDomesticMirror()
# 已删除：德语系统在国内被判定成"走官方源"，官方在境内不可达，正是这次卡死的由来。
# 选源统一走 _loadMirrors() -> _orderMirrorsByProbe()，一张表喂所有步骤。


# ── 镜像源（统一 mirrors.json，按 node/npm/rootfs 字段分组）──
_MIRRORS_FILE = os.path.join(os.path.dirname(__file__), "mirrors.json")
_mirrorsCache: dict[str, list] = {}

_MIRROR_FALLBACK = {
    "node":   [{"name": "官方", "base": "https://nodejs.org/dist", "domestic": False}],
    "npm":    [{"name": "官方", "registry": "https://registry.npmjs.org", "domestic": False}],
    "rootfs": [{"name": "Ubuntu 官方", "url": "https://cloud-images.ubuntu.com/{codename}/current/{codename}-server-cloudimg-amd64-root.tar.xz", "domestic": False}],
}


def _loadMirrors(kind: str) -> list:
    """加载 mirrors.json 中指定字段（node/npm/rootfs）的镜像列表。

    每个字段下的条目字段结构各自独立（node 用 base、npm 用 registry、rootfs 用 url），
    不做强制统一。读文件失败时回退到对应字段的官方源兜底。
    """
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


def _sourceSpeeds() -> dict:
    """读取 download_python.ps1 一次探针写下的 source_map.json（主机 -> KB/s）。

    探针在解压那一刻并发跑完，产出这一张表；这里只读表、不再探测，
    保证「一次探到底、每步按表取最快」。
    """
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


def _orderMirrorsByProbe(mirrors: list) -> list:
    """按实测吞吐给镜像重排：测得快的在前，没测过的按原顺序跟在后面。"""
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
# 兜底 codename：当前仍受支持的最稳 LTS（仅在真源探测失败时显式降级，且写日志，不静默）
_FALLBACK_CODENAME = "noble"


def _detectLatestUbuntuCodename() -> tuple[str, bool]:
    """从官方 releases/ 页枚举「活着的 LTS」，取最新 codename（数据驱动，零硬编码字典）。

    返回 (codename, degraded)：
      - degraded=False：从真源成功解析出最新 LTS codename
      - degraded=True：真源探测失败，显式降级到 _FALLBACK_CODENAME

    官方页格式（Apache 目录列表，稳定）：
      <a href="26.04/">26.04/</a> ... Ubuntu Server 26.04 LTS (Resolute Raccoon) released builds
      codename 在整行括号内；[END OF LIFE]/[END OF REGULAR SUPPORT] 标记 EOL，跳过。
    """
    try:
        req = urllib.request.Request(
            _OFFICIAL_RELEASES_URL,
            headers={"User-Agent": "Rheia-installer/1"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        alive_lts = []
        for line in html.splitlines():
            m = re.search(r'href="(\d{2}\.\d{2})/"', line)
            if not m:
                continue
            ver = m.group(1)
            if "LTS" not in line:
                continue  # 只要 LTS
            if ("END OF LIFE" in line) or ("END OF REGULAR SUPPORT" in line):
                continue  # 跳过 EOL
            cn = re.search(r"\(([A-Z][a-z]+) [A-Z][a-z]+\)", line)
            if not cn:
                continue
            alive_lts.append((ver, cn.group(1).lower()))

        if alive_lts:
            # 按版本号取最新
            latest = sorted(alive_lts, key=lambda x: [int(n) for n in x[0].split(".")])[-1]
            log(f"Ubuntu 最新 LTS: {latest[0]} ({latest[1]})")
            return latest[1], False

        log("官方 releases/ 页未解析到可用 LTS，显式降级到 %s" % _FALLBACK_CODENAME)
        return _FALLBACK_CODENAME, True

    except Exception as e:
        log(f"Ubuntu codename 探测失败（{e}），显式降级到 {_FALLBACK_CODENAME}")
        return _FALLBACK_CODENAME, True

def _resolveUbuntuUrl(templateUrl: str, codename: str) -> str:
    """将 URL 中的 {codename} 替换为实际 codename。"""
    return templateUrl.replace("{codename}", codename)


def _loadRootfsMirrors():
    """加载 rootfs 镜像列表（从统一 mirrors.json 的 rootfs 字段），
    并联网探测最新 LTS codename、替换 URL 模板中的 {codename} 占位符。"""
    global _mirrorsCache
    if "rootfs_resolved" in _mirrorsCache:
        return _mirrorsCache["rootfs_resolved"]
    raw = _loadMirrors("rootfs")

    # 检测最新 LTS codename（枚举真源，失败显式降级）
    codename, degraded = _detectLatestUbuntuCodename()
    if degraded:
        log("WARNING: 已显式降级到 %s（真源探测失败）" % codename)

    # 用 codename 替换 URL 模板中的占位符
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


def _probeReachable(url: str, perTry: float = 3.0, fresh: bool = False) -> bool:
    """非阻塞可达探测：能连上端口就算可达（只连，不下载、不等响应体）。

    不是"超时即失败"：这一次没通只影响排序/换源，下一次照样再问。
     2026-10-28 令：安装期的判断一律"轮询问到可达/已有/符合条件就走"。
    """
    if not url:
        return False
    if not fresh and url in _probeCache:
        return _probeCache[url]
    import socket
    import urllib.parse
    import select as _select
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
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except Exception:
        infos = []
    for family, socktype, proto, _canon, addr in infos:
        s = None
        try:
            s = socket.socket(family, socktype, proto)
            s.setblocking(False)
            if s.connect_ex(addr) == 0:
                ok = True
                break
            # 连接进行中：0.1 秒一轮问一次，问到就绪就走
            end = time.monotonic() + perTry
            while time.monotonic() < end:
                if _select.select([], [s], [], 0.1)[1]:
                    ok = (s.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0)
                    break
            if ok:
                break
        except Exception:
            ok = False
        finally:
            try:
                if s is not None:
                    s.close()
            except Exception:
                pass
    _probeCache[url] = ok
    return ok


def _applyMirrorPolicy(mirrors: list) -> list:
    """镜像排序：语言只作提示，绝不作"禁用"判据（ 2026-10-28 令）。

    事故背景：德语系统 ⇒ 只留官方源 ⇒ 官方在国内不可达 ⇒ 只有一个源、没有回退
    ⇒ 安装器直接死掉。现在**所有源一律保留**：先做一次非阻塞可达探测，可达的排
    前面；探测没通也留在列表里，下载阶段照样会重试（回退镜像源）。
    """
    speeds = _sourceSpeeds()  # 一次探针的表，本步不再自己探
    probed = []
    for idx, m in enumerate(mirrors):
        url = m.get("url") or m.get("base") or m.get("registry") or ""
        host = url.split("//", 1)[-1].split("/", 1)[0].lower() if "//" in url else ""
        kbs = speeds.get(host, -1)
        if kbs < 0:
            # not measured by the shared probe: fall back to a cheap connect test
            kbs = 1 if _probeReachable(url) else 0
        probed.append((kbs, idx, m))
    probed.sort(key=lambda t: (-t[0], t[1]))
    log("  镜像顺序: %s" % ", ".join(
        "%s=%s" % (m.get("name", "?"), ("%d KB/s" % k) if k > 1 else ("可达" if k == 1 else "未通"))
        for k, _i, m in probed))
    return [m for _k, _i, m in probed]
_ROOTFS_MIN_XZ = 48 * 1024 * 1024
_ROOTFS_EXPECT_XZ = 254 * 1024 * 1024  # 拿不到 Content-Length 时的估算分母
#  2026-10-28 令：安装期不设 await / timeout。
# 「多久没来字节」只用作**换源**判据（不是判安装成败、不是判死安装器）。
_ROOTFS_STALL_SEC = 20
_ROOTFS_MAX_ROUNDS = 3  # 全部源轮完仍不可达 ⇒ 报错（写信号，可见，不静默）
_ROOTFS_IMPORT_MIN_FREE = 2 * 1024**3


def _probeMirror(url, timeout=10):
    """探活镜像 URL，分级返回状态。

    返回字符串（不再是 True/False）：
      "ok"       — 200~399，文件可达，可用
      "dead"     — 404，URL 结构对当前 codename 是坏的（源不提供/路径错），淘汰并明示
      "net"      — 超时/连接失败，网络问题，可降级重试
      "other"    — 其他状态码（5xx 等），暂时不可用
    """
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Rheia-installer/1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
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


def _fetchOfficialSha256(codename: str) -> str:
    """从官方 cloud-images.ubuntu.com/{codename}/current/SHA256SUMS 抓 amd64-root 的权威 sha256。

    这是真伪唯一裁判：返回 64 位 hex 字符串；抓不到返回空串（下载后跳过校验，仅告警）。
    """
    return _fetchSha256FromSums(
        f"https://cloud-images.ubuntu.com/{codename}/current/SHA256SUMS"
    )


def _deriveSumsUrl(rootfs_url: str) -> str:
    """从 rootfs 的完整 URL 推导同目录的 SHA256SUMS 地址。

    .../{codename}/current/{codename}-server-cloudimg-amd64-root.tar.xz
      → .../{codename}/current/SHA256SUMS
    """
    parts = rootfs_url.rsplit("/", 1)
    return parts[0] + "/SHA256SUMS"


def _fetchSha256FromSums(sumsUrl: str) -> str:
    """抓指定 SHA256SUMS 文件中 amd64-root 的 sha256，返回 64 位 hex；失败返回空串。"""
    try:
        req = urllib.request.Request(sumsUrl, headers={"User-Agent": "Rheia-installer/1"})
        with urllib.request.urlopen(req, timeout=15) as resp:
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


def _downloadRootfs(url, destPath, codename: str = ""):
    destDir = os.path.dirname(destPath)
    os.makedirs(destDir, exist_ok=True)
    partPath = destPath + ".part"
    if os.path.exists(partPath): os.remove(partPath)

    # 权威哈希：优先用「下载源自己」的 SHA256SUMS（与 rootfs 同目录，自洽，能校验
    # 下载是否完整/损坏），抓不到才退回官方 SHA256SUMS。避免用官方哈希去判国内镜像
    # 的文件——国内镜像 build 号与官方不同步时会导致 sha256 系统性不匹配、反复重下。
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

    # 连接层：非阻塞轮询问到「可达」就走（不问到不判死，客户看得见每次 Try）
    _try = 0
    while not _probeReachable(url, fresh=True):
        _try += 1
        _writeSignal(tr("ui.install.sig.probe", n=_try, total="-",
                        name=url.split("/")[2] if "//" in url else url), 42, "complete")
        log("  连接尝试 %d 未通，1 秒后再问" % _try)
        if _try >= 3:
            # 连问 3 次仍不通 ⇒ 交给上层换下一个源（不是判死安装器）
            raise TimeoutError("源暂不可达，换下一个")
        time.sleep(1.0)

    # 读层：socket 等待给 _ROOTFS_STALL_SEC，只为「多久没来字节 ⇒ 换源」，不判安装成败
    resp = urllib.request.urlopen(req, timeout=_ROOTFS_STALL_SEC)
    total = int(resp.headers.get("Content-Length") or 0) or _ROOTFS_EXPECT_XZ
    done = 0; chunk = 512 * 1024
    lastWrite = time.monotonic(); lastPct = -1; lastLog = 0
    with resp, open(partPath, "wb") as out:
        while True:
            try:
                buf = resp.read(chunk)
            except Exception as _se:
                raise TimeoutError("源停滞，换源（%s）" % _se)
            if not buf: break
            out.write(buf)
            if hasher is not None:
                hasher.update(buf)  # 边下载边累加哈希，避免下载后再读第二遍
            done += len(buf)
            now = time.monotonic()
            # 进度按**真实总大小**算（42~58），不再用 48MB 当分母封顶
            pct = min(58, 42 + int(done * 16 / total))
            if pct != lastPct or (now - lastWrite) >= 0.5:
                _writeSignal(tr("ui.install.sig.download"), pct, "complete")
                lastPct = pct; lastWrite = now
            if done - lastLog >= 10 * 1024 * 1024:
                log("  下载进度: %d MB / %d MB" % (done // (1024*1024), total // (1024*1024)))
                lastLog = done
    if done < _ROOTFS_MIN_XZ: raise RuntimeError("rootfs 文件过小")

    # sha256 已随下载增量算好，此处只做内存比对，毫秒级，不写独立「校验」步骤/文案
    if hasher is not None:
        actual = hasher.hexdigest()
        if actual != official_sha:
            os.remove(partPath)
            log(f"镜像校验失败：sha256 不匹配（期望 {official_sha[:16]}…，实际 {actual[:16]}…）")
            raise RuntimeError("Ubuntu 镜像 sha256 校验失败，文件可能被投毒或损坏")

    os.replace(partPath, destPath)
    log("  下载完成: %d MB" % (done // (1024*1024)))
    return done


def _downloadRootfsWithFallback(mirrors, destPath, codename: str = ""):
    """镜像轮换下载：所有源都试，连不上就换下一个，轮完再从头来。

     2026-10-28 令：
      · 官方源不可达**不许**判死安装器 —— 必须回退到镜像源继续试；
      · 每一次 Try 都写信号，客户看得见"我们在不断试"；
      · 不设 await/timeout 语义：只有"这个源暂时没通 ⇒ 换下一个"。
    """
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


def _loadNpmMirrors():
    """\u52a0\u8f7d npm \u955c\u50cf\u5217\u8868\uff08\u4ece\u7edf\u4e00 mirrors.json \u7684 npm \u5b57\u6bb5\uff09\u3002"""
    return _loadMirrors("npm")


# WSL script helpers
def _writeWslScript(name, content):
    dest = os.path.join(localdataDir, name)
    os.makedirs(localdataDir, exist_ok=True)
    with open(dest, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    return dest

def _wslPath(windowsPath):
    absPath = os.path.abspath(windowsPath)
    parts = absPath.split(os.sep)
    drive = parts[0][0].lower()
    rest = "/".join(parts[1:])
    return f"/mnt/{drive}/{rest}"


def _buildNodeBash(minVersion="18.0.0", nodeMirrorBases=None):
    """Generate bash script to install latest stable Node.js inside WSL Ubuntu.

    不再锁死任何 major 版本 — 始终取 Node.js 最新稳定版。
    如果已有 node >= minVersion 则跳过（用 dpkg 语义化版本比较），
    否则下载/安装最新 stable。

    nodeMirrorBases: 国内镜像 base URL 列表（如 https://npmmirror.com/mirrors/node），
    传了就走国内源不碰国外，脚本内多源轮换；不传则 nodesource apt + tarball fallback。
    """
    if nodeMirrorBases:
        # ── 国内镜像路径：直下 tarball，多源轮换，不走 apt ──
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

apt-get install -y -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 ca-certificates curl xz-utils 2>/dev/null || true

# 从 index.json 取最新版本（轮换所有源，第一个给出有效版本号的胜出）
LATEST_VER=""
for B in $NODE_BASES; do
  LATEST_VER=$(curl --connect-timeout 10 --max-time 30 -fsSL "$B/index.json" 2>/dev/null | grep -oP '"version":"v\\K[0-9]+\\.[0-9]+\\.[0-9]+"' | head -1 | tr -d '"')
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
  if curl --connect-timeout 15 --max-time 120 -fsSL "$B/v$LATEST_VER/node-v$LATEST_VER-linux-x64.tar.xz" -o "$TMP/node.tar.xz" 2>/dev/null; then
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

    # ── 国际用户：nodesource apt → tarball fallback ──
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

apt-get update -y -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 || true
apt-get install -y -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 ca-certificates curl gnupg || true

# setup_current.x — 始终指向最新稳定版，不锁任何 major
curl --connect-timeout 15 --max-time 60 -fsSL https://deb.nodesource.com/setup_current.x | bash -
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
LATEST_VER=$(curl --connect-timeout 10 --max-time 30 -fsSL "https://nodejs.org/dist/index.json" 2>/dev/null | grep -oP '"version":"v\\K[0-9]+\\.[0-9]+\\.[0-9]+"' | head -1 | tr -d '"')
if [ -z "$LATEST_VER" ]; then
  LATEST_VER=$(curl --connect-timeout 10 --max-time 30 -fsSL "https://nodejs.org/dist/latest/" 2>/dev/null | grep -oP 'node-v\\K[0-9]+\\.[0-9]+\\.[0-9]+' | head -1)
fi
if [ -z "$LATEST_VER" ]; then
  # 最后尝试淘宝镜像
  LATEST_VER=$(curl --connect-timeout 10 --max-time 30 -fsSL "https://npmmirror.com/mirrors/node/index.json" 2>/dev/null | grep -oP '"version":"v\\K[0-9]+\\.[0-9]+\\.[0-9]+"' | head -1 | tr -d '"')
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
  if curl --connect-timeout 15 --max-time 120 -fsSL "$URL" -o "$TMP/node.tar.xz"; then
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



def _buildOpenclawBash(registryUrls):
    """Generate bash script to install openclaw CLI from npm registry.
    多源轮换：按顺序逐个试 registry，失败切下一个，全部失败才报错（不盯死一个源）。
    同时配置 WSL 内 npm 全局 registry，确保后续 update/plugins 也走镜像。
    """
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
  VERSION_OUTPUT="$(timeout 10 "$OPENCLAW_PATH" --version 2>&1 || echo 'timeout')"
  echo "[ok] $OPENCLAW_PATH: $VERSION_OUTPUT"
  echo "OKVERSION: $VERSION_OUTPUT"
  echo "[ok] openclaw ready"
  exit 0
fi

# Last resort: try direct exec
if [ -x "$BIN_DIR/openclaw" ]; then
  VERSION_OUTPUT="$(timeout 10 "$BIN_DIR/openclaw" --version 2>&1 || echo 'timeout')" && {{ echo "OKVERSION: $VERSION_OUTPUT"; echo "[ok] $BIN_DIR/openclaw: $VERSION_OUTPUT"; exit 0; }}
fi

echo "[error] openclaw binary not found after install"
exit 1
"""


def _pickImportDir():
    # WSL 发行版（Ubuntu 的 ext4.vhdx）放 C:\wsl-distro，与壳层目录树物理隔离。
    # 这样壳层自毁/卸载（删 {app} 或 ProgramData\Rheia）天然碰不到
    # vhdx，红线资产安全不依赖白名单排除清单。路径与 license_anchor 的
    # _WSL_IMPORT_DIR 必须严格一致。
    importDir = r"C:\wsl-distro"
    os.makedirs(importDir, exist_ok=True)
    log("  WSL 导入目录: %s" % importDir)
    return importDir
def runWsl(cmd, timeout=None):
    env = os.environ.copy()
    env["WSL_UTF8"] = "1"
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    try:
        raw = subprocess.run(cmd, shell=True, capture_output=True, timeout=timeout, env=env,
                            creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=si)
        for enc in ("utf-8", "gbk", "cp1252"):
            try:
                out = raw.stdout.decode(enc)
                err = raw.stderr.decode(enc)
                # WSL 有时输出 UTF-16 LE，解码后含 null 字节，导致 "ok" in out 为 False
                out = out.replace("\x00", "").strip()
                err = err.replace("\x00", "").strip()
                return raw.returncode, out, err
            except (UnicodeDecodeError, LookupError):
                continue
        out = raw.stdout.decode("utf-8", errors="replace").replace("\x00", "").strip()
        err = raw.stderr.decode("utf-8", errors="replace").replace("\x00", "").strip()
        return raw.returncode, out, err
    except subprocess.TimeoutExpired:
        return -1, "", "Timeout"
    except Exception as e:
        return -1, "", str(e)


_keepaliveProc = None


def _holdDistroOpen() -> None:
    """握住 WSL 发行版，防止它空闲关停把 Gateway 一起带走。

    根因（2026-10-28 实测，官方 WSL #13416）：WSL >= 2.6.1 在**最后一个 wsl.exe 客户端
    退出后 15~20 秒**关掉发行版，即便开了 linger。安装期 Step 8.5/9 是 Windows 侧 Python
    在跑，几分钟没有任何 wsl.exe 客户端 ⇒ 发行版直接关机 ⇒ systemd 把 Gateway SIGTERM。
    这里用一条常驻 `sleep infinity`（child-of-init）把发行版按住，装完再放。
    """
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


def _releaseDistro() -> None:
    """放开发行版：安装结束，让壳自己的保活接管。"""
    global _keepaliveProc
    if _keepaliveProc is not None:
        try:
            _keepaliveProc.kill()
        except Exception:
            pass
        _keepaliveProc = None


def _wslEnv():
    """给 wsl.exe 调用统一加上 WSL_UTF8=1（runPolled 用）。"""
    e = os.environ.copy()
    e["WSL_UTF8"] = "1"
    return e


# ── 回滚 ──

_preExistingWsl = None


def _snapshotPreExistingDistros() -> None:
    """装之前拍一张发行版快照：回滚只清本次自己建的，客户原有的一个都不碰。

    事故背景（2026-10-28）：回滚无条件 wsl --unregister，而"已经有 WSL+VMP"的
    机器上本就有客户的 Ubuntu —— 下载失败一次就把客户原有发行版注销了。
    """
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


def rollback():
    log("执行回滚...")
    # 只清本次自己建的发行版：装前就存在的（客户资产）一律不动
    if _preExistingWsl and wslDistro in _preExistingWsl:
        log(f"  发行版 {wslDistro} 装前就存在 ⇒ 回滚不注销（客户资产）")
    else:
        for distro in (wslDistro,):
            try:
                runCmd(f"wsl --terminate {distro}", timeout=10)
            except Exception:
                log(f"wsl terminate {distro} 失败，继续回滚")
            try:
                runCmd(f"wsl --unregister {distro}", timeout=30)
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


### GATE: steps 1-4 (WSL/VMP detection + reboot) finalized, DO NOT MODIFY ###

def step2EnableWsl():
    """Step 2: 检测并启用 WSL2"""
    log("Step 2: 检测 WSL2...")
    # 用 DISM 独立检测 WSL 功能是否启用（不借助 wsl --version 间接判断）
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

def step3EnableVmp():
    """Step 3: 检测并启用 VMP"""
    log("Step 3: 检测 VMP...")
    # 用 DISM 独立检测 VMP 是否启用，不再用 wsl --version 间接推断。
    # WSL 功能启用不代表 VMP 启用，两者必须都启用才能装 Ubuntu。
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

def step4RequestReboot():
    """Step 4: 写 RunOnce + 自动重启"""
    writeRunonce()
    flushLogs()
    log("=== WSL/VMP 已启用，正在自动重启电脑，重启后将自动继续安装 ===")
    # 重启前先在卡面留一句：原来这里直接 shutdown /t 5，卡面一黑就重启，客户不知道发生了什么
    _writeSignal(tr("ui.install.reboot_comment"), 34, "done")
    flushLogs()
    time.sleep(3)
    flushLogs()
    # 静默重启：CREATE_NO_WINDOW 避免 os.system 闪 cmd 黑框，/t 5 给 RunOnce 落盘缓冲
    subprocess.run(
        ["shutdown", "/r", "/t", "10", "/c", tr("ui.install.reboot_comment")],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    os._exit(0)


# ── 管线入口 ──

def _launchCardProgress():
    r"""续装时拉起 {app}\card.exe --progress 显示进度（修复重启后壳消失）。

    card.exe 已随包打进 {app}（setup.iss [Files] 里有 DestDir:{app} 那条）。
    card 只读 signal 显示进度，done 自毁，不实例化任何安装进程。
    """
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


def _launchBrowser():
    """安装完成 → 启动 main/main.py（内置浏览器 → Control UI）。

    替代原 installui.py 的 _launchWizard（card.exe 已接管安装卡，无 GUI 卡）。
    PySide6 扁平布局：DLL 在独立运行时目录的 site-packages/PySide6 根目录。
    Python 运行时独立（与制卡机共用），不随 {app}。

    注：主入口已方法化归并到 main/main.py（2026-09-07 前为 runtime/main.py）。
    """
    try:
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
        # DETACHED_PROCESS：让浏览器进程完全独立，不继承 install.py 的任何句柄，
        # 这样 install.py 退出后浏览器照常运行，也不会有残留进程纠缠。
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


def _isShellOnly() -> bool:
    """勾选「仅装壳」时，setup.exe 写了标记文件。True = 跳过 OpenClaw 环境安装，只接驳已有环境。"""
    flag = os.path.join(os.environ.get("TEMP", ""), "Rheia_shellonly.flag")
    return os.path.exists(flag)


def _grantProgramDataUsers():
    r"""给 ProgramData\Rheia 授权 Users 完全控制。

    install.py 以 admin 运行，创建的 baseline_pool.db 等文件默认 Users 只有读权限，
    导致后续主界面（普通用户）读写 db 报 WinError 5。安装时一次性授权根治。"""
    try:
        import subprocess as _sp
        os.makedirs(localdataDir, exist_ok=True)
        users_sid = "*S-1-5-32-545"  # built-in Users group
        _sp.run(
            ["icacls", localdataDir, "/grant", f"{users_sid}:(OI)(CI)F", "/T", "/Q"],
            capture_output=True, timeout=60,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        log("ProgramData Users 授权完成")
    except Exception as e:
        log(f"ProgramData Users 授权失败（非致命）: {e}")


def _rejectIfUnsupported() -> None:
    """不支持语言环境红线（in0001）：命中 japanese → 写中文提示 signal 并中止安装。

    只中止、不触发 rollback（rollback 会 wsl --terminate/unregister，语义是
    「安装中途失败清理」，而这里是「拒绝安装」，两者不同）。check() 内部已写
    审计日志（action=reject）。
    """
    try:
        from main.main import check as _i18n_check, REJECT_MSG as _reject_msg
        _state = _i18n_check()
        log(f"i18n_guard.check() = {_state}")
    except Exception as e:
        log(f"i18n_guard check 跳过: {e}")
        return
    if _state == "japanese":
        # 红线唯一例外：提示文案硬编码日语，不依赖系统语言 API。
        msg = "本ソフトウェアは日本語をサポートしていません。インストーラーは自己破壊します。"
        _writeSignal(msg, 0, "done")
        log("i18n_guard: 检测到不支持的语言环境，中止安装")
        os._exit(0)


def _grantKernelUsers():
    r"""给 {app}\kernel 授权 Users 写权限。

    scout 运行时写 kernel\large\mani.json / ot.json / manifest.signal + 文档缓存
    kernel\Official text\llms-full.txt，都在 Program Files 下，普通进程（主界面
    pythonw）默认无写权限 → probe 抛 Permission denied（2026-09-04 实测）。安装时
    一次性授权 Users 完全控制，根治。目标目录 kernel 及其子目录 large/。
    """
    try:
        import subprocess as _sp
        users_sid = "*S-1-5-32-545"  # built-in Users group
        kernel_dir = os.path.join(appDir, "kernel")
        for sub in ("", "large", "Official text"):
            target = os.path.join(kernel_dir, sub)
            try:
                os.makedirs(target, exist_ok=True)
            except OSError:
                continue
            _sp.run(
                ["icacls", target, "/grant", f"{users_sid}:(OI)(CI)F", "/T", "/Q"],
                capture_output=True, timeout=60,
                creationflags=0x08000000,  # CREATE_NO_WINDOW
            )
        log("kernel Users 授权完成")
    except Exception as e:
        log(f"kernel Users 授权失败（非致命）: {e}")


# ── 组件声明（描述式安装）────────────────────────────────────────────
def loadComponents() -> list:
    """读 installer/components.json -> 组件清单。读不到/不合法 => [] 并记账（不假装装过）。"""
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


def _appDirToWsl() -> str:
    """安装目录 -> WSL 视图（C:\\x -> /mnt/c/x）。"""
    a = appDir or ""
    if len(a) > 1 and a[1] == ":":
        return "/mnt/" + a[0].lower() + a[2:].replace("\\", "/")
    return a


def applyComponents() -> list:
    """按声明分发：runtime 交给既有步骤，plugin 交给发现程序。动作只在消费端。"""
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
                # 2026-10-29：付费件未安装 ⇒ 静默跳过（免费壳不挂扩展位，不是装坏了）。
                #   判据：<app>/personal-slot 存在，或 <app>/RheiaPersonalSlot + slot.key 都在。
                _app = appDir or ""
                _has_slot = os.path.isfile(os.path.join(_app, "personal-slot", "openclaw.plugin.json"))
                _has_seed = (os.path.isfile(os.path.join(_app, "RheiaPersonalSlot"))
                             and os.path.isfile(os.path.join(_app, "slot.key")))
                if not _has_slot and not _has_seed:
                    log(f"components: 插件 {cid} 未购买（无 personal-slot / 无种子+key）—— 跳过，不注入")
                    done.append({"id": cid, "ok": True, "how": "not-purchased"})
                    continue
                cmd = f"python3 '{wsl_app}/persona/slot_discovery.py' --app-root '{wsl_app}' --source '{src_w}'"
                rc, out, err = runWsl(f"wsl -d {wslDistro} -u root -- bash -c \"{cmd}\"", timeout=180)
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


def run():
    """全新安装入口 — Step 0-9 串行执行"""
    bootstrapInstallLogging(logFile)
    log("=== 全新安装开始 ===")
    _snapshotPreExistingDistros()
    try:
        _rejectIfUnsupported()
        step0SelfCheck()
        _grantProgramDataUsers()
        _grantKernelUsers()
        # 仅装壳：不装 WSL/Ubuntu/Node/OpenClaw，直接接驳已有 OpenClaw 环境
        if _isShellOnly():
            log("=== 仅装壳模式：跳过 OpenClaw 环境安装，直接接驳 ===")
            _writeSignal(tr("ui.install.sig.shell_only"), 41, "complete")
            # 仅装壳：也要按声明把插件注进去（否则壳装好了、扩展位没接上）
            applyComponents()
            step9Handoff()
            log("=== 安装完成（仅装壳） ===")
            _writeSignal(tr("ui.install.stage.launch"), 100, "done")
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
            # WSL+VMP 都已就绪（无需重启）：写文案，card 显示「检测到已有 WSL+VMP 环境」并快进
            _writeSignal(tr("ui.install.sig.wsl_vmp_exist"), 40, "complete")
        _runSteps5to9()
        log("=== 安装完成 ===")
        _writeSignal(tr("ui.install.stage.launch"), 100, "done")
        _launchBrowser()
    except RebootRequired:
        log("=== 需要重启，重启后续装 ===")
        raise
    except Exception as e:
        log(f"!!! 安装失败: {e}")
        # 失败必须显式：写 error 信号，card 上看得见原因（不许静默死）
        _writeSignal(tr("ui.install.sig.error", msg=str(e)[:120]), 0, "error")
        time.sleep(1.0)
        rollback()
        raise


def _runSteps5to9():
    """执行 Steps 5-9（WSL2/VMP 已就绪或无需重启）"""
    step5InstallUbuntu()
    # 发行版这时才存在：从这一刻起握到安装结束（Step 8.5/9 有几分钟不碰 wsl.exe）
    _holdDistroOpen()
    step55InstallAptPip()
    step6InstallNodejs()
    step7InstallOpenclaw()
    step8GatewaySystemd()
    step85ScoutLargePrep()
    # 2026-10-18：描述式安装 —— 按 components.json 分发组件（插件注入走发现程序）
    applyComponents()
    step9Handoff()


def resume():
    """续装入口 — 由 RunOnce 重启后调用"""
    bootstrapInstallLogging(logFile)
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
        _launchBrowser()
    except Exception as e:
        log(f"!!! 续装失败: {e}")
        rollback()
        raise

### GATE: steps 5-9 finalized DO NOT MODIFY ###

# ── Step 5: 装 Ubuntu ──

def step5InstallUbuntu():
    """镜像站下载 rootfs.xz -> wsl --import，替代脆弱的 wsl --install

    全量安装 = 无条件覆盖：不保留已有 Ubuntu，直接 unregister 旧的 + import 新的。
    已有 distro 保留/接驳是「仅装壳」模式的职责（_isShellOnly 走 step9Handoff），
    全量安装必须重新从网络拉 distro 部署，覆盖 C:\\wsl-distro。
    """
    log("Step 5: 装 Ubuntu (镜像站下载 + wsl --import，无条件覆盖)...")

    importDir = _pickImportDir()
    rootfsXz = os.path.join(localdataDir, "wsl-rootfs.tar.xz")
    codename, _degraded = _detectLatestUbuntuCodename()
    policyMirrors = _applyMirrorPolicy(_loadRootfsMirrors())
    log("  镜像策略: 按实测吞吐排序（source_map.json），共 %d 个源" % len(policyMirrors))
    # 镜像轮换下载：失败/停滞/校验不通过自动切下一个源，不盯死一个
    _downloadRootfsWithFallback(policyMirrors, rootfsXz, codename=codename)

    log("  准备 WSL 导入环境...")
    log(f"  全量覆盖：unregister 旧 {wslDistro}，重新 import 覆盖 C:\\wsl-distro...")
    runPolled(f"wsl --unregister {wslDistro}", tr("ui.install.sig.import_wait"), 58)

    log(f"  wsl --import {wslDistro}...")
    _writeSignal(tr("ui.install.sig.import_ubuntu"), 59, "complete")
    cmd = f'wsl --import {wslDistro} "' + importDir + '" "' + rootfsXz + '" --version 2'
    # 导入几分钟很正常：轮询问"退没退"，每 2 秒把等待秒数写给 card（不设 timeout）
    rc, out, err = runPolled(cmd, tr("ui.install.sig.import_wait"), 59, ramp=300)
    if rc != 0: raise RuntimeError("WSL 导入失败: rc=%d %s" % (rc, err.strip()[:200]))
    _writeSignal(tr("ui.install.sig.import_ubuntu"), 60, "complete")
    log("  wsl --import rc=%d" % rc)

    log("  验证 WSL 发行版...")
    while True:  # 轮询问"已有"，问到就走；不问死、不判死
        rc, out, _ = runWsl(f"wsl -d {wslDistro} -u root -- echo ok", timeout=30)
        if rc == 0 and "ok" in out:
            log("  WSL 初始化完成"); break
        _writeSignal(tr("ui.install.sig.import_verify"), 60, "complete")
        time.sleep(3)

    rc, _, _ = runCmd("ubuntu config --default-user root", timeout=30)
    log("  ubuntu config: rc=%d" % rc)
    rc, out, _ = runWsl(f"wsl -d {wslDistro} -u root -- lsb_release -a", timeout=30)
    if rc != 0: raise RuntimeError("Ubuntu 验证失败")
    log("  lsb_release: %s" % out.strip())

    try: os.remove(rootfsXz)
    except OSError: pass
    markProgress(5)
    ### GATE: step 5 finalized ###


# ── Step 5.5: 装 apt + pip/venv（WSL 刚装好、Node 之前）──

def step55InstallAptPip():
    """WSL 刚装好、装 Node 之前，先 apt 换镜像 → update → 装 pip/venv。

    面向过程、零竞态：换镜像、update、install 全部塞进「同一条 bash」串行执行，
    一条命令一次进 WSL，进程内顺序跑完，中间没有 Python 层插入，不可能出现
    update 被 kill 后 install 撞 dpkg 锁的竞态。不设硬超时（低性能机器首次
    重建 apt 索引就是慢，随装随走）。pip 是 office skills 的可选运行时，装不上
    不阻断主链，只记日志、写标记让运行时 pip_venv.py 跳过。
    """
    log("Step 5.5: apt update + pip/venv...")

    _writeSignal(tr("ui.install.stage.pip"), 61, "complete")

    # 单条 bash：换镜像（国内）→ update → install，串行、无竞态、无硬超时
    # 注意：bash 内部全部用单引号，避免与外层 bash -c "..." 的双引号在 cmd 解析时冲突
    # 镜像选择不看系统语言：谁可达走谁（阿里镜像全球可达，官方源在国内常常不通）
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

    # 验证：装上了才写标记，让运行时 pip_venv.py 跳过
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
    ### GATE: step 5.5 finalized ###


# ── Step 6: 装 Node.js ──



# Step 6: Install Node.js via WSL (nodesource apt -> tarball fallback)

def step6InstallNodejs():
    """Install Node.js >= 22 inside WSL Ubuntu.

    中文系统：跳过 nodesource apt，直接从国内镜像下 tarball。
    非中文系统：仍走 nodesource apt → official tarball fallback。
    """
    log("Step 6: Install Node.js...")

    # 语言只作提示、不作禁用：所有源都进轮换（德语系统在国内也能走国内源）
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

    rc, out, _ = runWsl(f'wsl -d {wslDistro} -- bash -c "node --version"', timeout=30)
    if rc != 0:
        raise RuntimeError("node --version failed after installation")
    log(f"  node --version: {out.strip()}")

    try: os.remove(scriptPath)
    except OSError: pass
    markProgress(6)
    ### GATE: step 6 finalized ###


# Step 7: Install OpenClaw CLI via npm registry rotation

def _backupWslOpenclaw() -> str:
    """动手前把 WSL 侧 `~/.openclaw` 整份备份到 `%PROGRAMDATA%\\Rheia\\backup\\`。

    为什么必须有它（2026-09-21 事故）：重装走到 Step 7/8 那条"完整安装"路时，
    WSL 侧的 `~/.openclaw`（agents／会话库／插件落点／slot 文件／配置）会被重建，
    而**当时没有任何备份** —— 一次重装就把宿主状态清空且不可恢复。
    铁律（跟"失败必须显式"同一条）：**要动就先备份；备份失败就别动**
    （宁可停下报错，也不静默清库）。

    返回 "fresh"（本来就没有，全新机器）/ "ok:<备份文件>" / "fail:<原因>"。
    """
    probe_cmd = (f'wsl -d {wslDistro} -u root -- bash -c '
                 f'"test -d /root/.openclaw && echo yes || echo no"')
    rc, out, _ = runWsl(probe_cmd, timeout=30)
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
    rc, out, err = runWsl(cmd, timeout=1800)
    if rc != 0 or "BACKUP_OK" not in (out or ""):
        return f"fail:{(err or out or '').strip()[:200]}"
    winFile = os.path.join(winDir, f"openclaw-{ts}.tar.gz")
    try:
        sz = os.path.getsize(winFile)
    except OSError:
        sz = 0
    if sz < 1024:
        return f"fail:备份文件异常（{sz} 字节）"
    log(f"  WSL 侧 ~/.openclaw 已备份 -> {winFile}（{sz // 1024} KB）")
    return f"ok:{winFile}"


def step7InstallOpenclaw():
    """Install openclaw CLI globally via npm registry mirror rotation"""
    log("Step 7: Install OpenClaw CLI...")

    # ⚠ 硬门（2026-09-21 事故后加）：动 WSL 之前先备份宿主状态；备份失败就**不动手**。
    _bk = _backupWslOpenclaw()
    if str(_bk).startswith("fail:"):
        raise RuntimeError(f"WSL 侧状态备份失败，拒绝继续安装（不许清库）：{str(_bk)[5:]}")
    log(f"  WSL 侧状态: {_bk}")

    # ── 检测当前版本 ──
    rc, out, _ = runWsl(f'wsl -d {wslDistro} -- bash -c "openclaw --version 2>&1"', timeout=30)
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
    ### GATE: step 7 finalized ###





def step8GatewaySystemd():
    """启用 WSL systemd，安装并启动 OpenClaw gateway（脱壳持久进程）"""
    log("Step 8: Gateway systemd...")

    # ── 幂等逐层检测：systemd → gateway install → gateway 已启动 ──
    # 1. systemd 已启用？
    rc, out, _ = runWsl(f'wsl -d {wslDistro} -u root -- bash -c "cat /etc/wsl.conf 2>/dev/null"', timeout=15)
    systemd_enabled = (rc == 0 and "systemd=true" in out)
    if systemd_enabled:
        log("  systemd 已启用，跳过 wsl.conf 写入")
    # 1.5. linger 已启用？（用于重启后 systemd fallback）
    rc, out, _ = runWsl(f'wsl -d {wslDistro} -u root -- bash -c "loginctl show-user root 2>/dev/null | grep ^Linger="', timeout=15)
    linger_enabled = (rc == 0 and "Linger=yes" in out)
    # 2. gateway 已启动？
    rc, out, _ = runWsl(f'wsl -d {wslDistro} -u root -- bash -c "openclaw gateway status"', timeout=30)
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

    # ── 否则走完整安装流程 ──
    # Step 8.1: 启用 WSL systemd
    if not systemd_enabled:
        log("  启用 WSL systemd...")
        runWsl(f'wsl -d {wslDistro} -u root -- bash -c "echo -e \'[boot]\\nsystemd=true\' > /etc/wsl.conf"', timeout=30)
        # wsl.conf 生效必须 terminate + 重新启动
        runCmd(f"wsl --terminate {wslDistro}", timeout=30)
        time.sleep(3)
        _releaseDistro()
        _holdDistroOpen()
        # 重新启动 WSL，等待 systemd 初始化
        while True:  # 轮询问"已有"，问到就走
            rc, out, _ = runWsl(f"wsl -d {wslDistro} -u root -- echo ok", timeout=30)
            if rc == 0 and "ok" in out:
                log("  WSL systemd 就绪")
                break
            _writeSignal(tr("ui.install.sig.enable_systemd"), 82, "complete")
            time.sleep(3)

    # Step 8.1b: 启用 linger（重启后 systemd 兜底）
    if not linger_enabled:
        log("  启用 loginctl linger root（重启后 systemd 兜底）...")
        _writeSignal(tr("ui.install.sig.enable_systemd"), 82, "complete")
        rc, out, _ = runWsl(f'wsl -d {wslDistro} -u root -- bash -c "loginctl enable-linger root"', timeout=30)
        if rc != 0:
            log(f"  Warning: enable-linger 失败: {out[:200]}")
        else:
            log("  linger 已启用")

    # Step 8.1d: 安装 gateway service（systemd 兜底——重启后自动拉起）
    # 必须在 _writeMinimalGatewayConfig 之前：gateway install 会重写 openclaw.json
    log("  安装 Gateway systemd service（重启兜底）...")
    _writeSignal(tr("ui.install.sig.install_gw"), 84, "complete")
    rc, out, err = runPolled(f'wsl -d {wslDistro} -u root -- bash -c "openclaw gateway install"',
                             tr("ui.install.sig.install_gw"), 84, env=_wslEnv())
    if rc != 0:
        log(f"  Warning: gateway install rc={rc} (非致命): {err.strip()[:200]}")

    # Step 8.1c: 清理可能残留的迁移锁（防止 "migrations already running" 死循环）
    # 必须在 gateway install 之后：install 会 restart service，若先清锁再 install，
    # install 的 restart 会打断清锁刚拉起的 migration，留下 5 分钟 TTL 的孤儿锁，
    # 后面 gateway start 全撞 "migrations already running"（2026-08-23 实测）。
    # 清锁 = stop → doctor --fix → start，这是最后一次 start，之后不再有 restart 打断。
    # 迁移锁记录在 SQLite state 里，不是独立文件，所以不能用 rm .migration-lock。
    # 统一走 gateway_recovery.clear_migration_lock（openclaw doctor --fix）。
    log("  清理迁移锁...")
    # ⚠⚠ 2026-10-17（屏闪根因 4 的连带缺口）：清迁移锁 = 停-修-开，**动网关状态**，
    #   必须在**生命周期锁**里（否则与 keepalive/UI 的重启链并发撞单实例迁移锁）。
    #   拿不到锁就不下手 —— 如实报、留给在管的 owner。
    try:
        from main.main import acquire_lifecycle as _acq_i, release_lifecycle as _rel_i
    except Exception:                                                 # noqa: BLE001
        _acq_i = _rel_i = None
    _owned_i = False
    if _acq_i is not None:
        try:
            _owned_i = bool(_acq_i("installer.migration_lock"))
        except Exception:                                             # noqa: BLE001
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
                except Exception:                                     # noqa: BLE001
                    pass

    # Step 8.1e: 在 Gateway 启动前写入最小配置（locale + provider 声明，API key 由用户填）
    # gateway install 在前，写配置在后：Gateway 启动后只追加 token 不覆盖已有 key
    _writeMinimalGatewayConfig()

    # Step 8.3: 启动 Gateway —— 走官方 systemd 托管（gateway install 已装服务）。
    # 不再用 subprocess.Popen 直接起独立 gateway 进程：那会绕过 systemd 单实例
    # 管理，与 systemd 服务抢 18789 端口/迁移锁，导致 Gateway 长时间起不来。
    # 官方 gateway start 幂等（gateway.md:605）：服务已跑则复用，未跑则拉起。
    log("  gateway start")
    # 轮询问"退了没有"，每 2 秒把等待秒数写给 card（不设 timeout）
    rc, out, err = runPolled(
        f'wsl -d {wslDistro} -u root -- bash -c '
        f'"export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; '
        f'openclaw gateway start 2>&1"',
        tr("ui.install.sig.start_gw"), 86, env=_wslEnv(),
    )
    if rc != 0:
        log(f"  Warning: gateway start rc={rc} (非致命): {(out + err).strip()[:200]}")

    # Step 8.3b: 等待 Gateway 真正 listening
    log("  等待 Gateway 就绪...")
    _writeSignal(tr("ui.install.sig.start_gw"), 86, "complete")
    _gwWait = 0
    while True:  # 轮询问"可达"，问到就走（不设 await / timeout）
        rc, out, _ = runWsl(f'wsl -d {wslDistro} -- bash -c "curl -s -o /dev/null -w \'%{{http_code}}\' http://127.0.0.1:18789"', timeout=10)
        if rc == 0 and out.strip() in ("200", "302", "401", "403"):
            log(f"  Gateway ready (HTTP {out.strip()}, {_gwWait}s)")
            break
        _gwWait += 2
        _writeSignal(tr("ui.install.sig.start_gw"), min(88, 86 + _gwWait // 20), "complete")
        time.sleep(2)

    # "HTTP 通了"不等于"稳了"：官方首启会落一次配置迁移（gateway.port/bind/tailscale）
    # 并**重启一次**。这里再轮询 PID，连续 3 次不变才认作就绪，避免壳刚接上又被换进程。
    _stablePid = ""
    _stableHits = 0
    while _stableHits < 3:
        rc, out, _ = runWsl(
            f'wsl -d {wslDistro} -- bash -c "pgrep -f \'index.js gateway\' | head -1"', timeout=10)
        _pid = (out.strip().splitlines()[0] if out.strip() else "")
        if _pid and _pid == _stablePid:
            _stableHits += 1
        else:
            _stablePid = _pid
            _stableHits = 0
        _writeSignal(tr("ui.install.sig.start_gw"), min(90, 88 + _stableHits), "complete")
        time.sleep(2)
    log(f"  Gateway 进程稳定（pid={_stablePid}）")

    # Step 8.4: 验证 gateway status
    rc, out, _ = runPolled(f'wsl -d {wslDistro} -u root -- bash -c "openclaw gateway status"', tr("ui.install.sig.start_gw"), 88, env=_wslEnv())
    if rc != 0:
        log(f"  Warning: gateway status 异常 (非致命): {out.strip()[:200]}")
    else:
        log(f"  gateway status: {out.strip()[:200]}")

    # 写入完整安装标记
    wslMarker = os.path.join(localdataDir, "wsl_full_install.done")
    with open(wslMarker, "w", encoding="utf-8") as f:
        f.write("ok")
    markProgress(8)
    ### GATE: step 8 finalized ###

# ── Step 8.5: 预热核心数据（SCOUT+LARGE 静默管线）──

def _scoutWarmupWorker():
    """后台预热核心数据：probe + settle + large.run（不阻塞安装）。

    实测这一趟是全量文档爬取（fetch 28MB + spider 1383 段 + 协作器探测），
    在慢网/DNS 不稳的机器上能拖 6~10 分钟；以前同步跑在 Step 8.5 里，
    把安装卡在 89% 近十分钟，客户以为死了。现在放进 daemon 线程，
    安装立刻推进到 Step 9 → 完成；核心数据装完后台补上（与运行时自补同一语义）。"""
    try:
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from kernel import scout, large
        from kernel.scout import _MANIFEST_PATH
        scout.probe()
        scout.settleManifest()
        if _MANIFEST_PATH.exists() and _MANIFEST_PATH.stat().st_size > 10:
            large.run()
            log("  Step 8.5(后台): 核心数据就绪")
        else:
            log("  Step 8.5(后台): mani.json 尚未生成，运行时自动补充")
    except Exception as exc:
        log(f"  Step 8.5(后台): 跳过 ({exc}) — 运行时将自动补充")


def step85ScoutLargePrep():
    """Step 8.5：预热核心数据（后台线程，不阻塞安装）。

    scout 全量爬取在慢网机器上 6~10 分钟，同步跑会把安装卡死在 89%。
    改成 daemon 线程：安装主链立刻走完，预热在后台补数据。
    卡在 89% 的 10 分钟卡顿即根治于此。"""
    log("Step 8.5: 预热核心数据（后台）...")
    _writeSignal(tr("ui.install.stage.warmup"), 89, "complete")
    try:
        import threading
        t = threading.Thread(target=_scoutWarmupWorker, name="scout-warmup", daemon=True)
        t.start()
    except Exception as exc:
        log(f"  Step 8.5: 后台线程起不来（{exc}），退回同步预热")
        _scoutWarmupWorker()
    markProgress(85)
    ### GATE: step 8.5 finalized ###



# ── Step 9: Handoff ──

def _writeMinimalGatewayConfig():
    """（已退役）不写任何 provider 声明。

    官方 provider 靠插件自发现（官方文档 model-providers.md 明确：官方 provider 插件
    自己发布模型目录，不需要 models.providers 条目）。此前把 kernel 活算出的 provider
    写进 models.providers，导致 OpenClaw config 校验崩溃——official external 插件
    不允许「只写声明不带 baseUrl/models」，只有 bundled 才允许。

    正确接驳：kernel 的活产出不直接写 openclaw.json，而是靠 spider 爬取的官方文档
    + OpenClaw 插件自发现，让 provider 在 OpenClaw 实例里自己长出来。
    """
    log("  _writeMinimalGatewayConfig: 已退役，官方 provider 靠插件自发现，不写 openclaw.json")
    return


def step9Handoff():
    """Step 9: Handoff — 探针找发行版 → 读 token/mode → glory.adopt()

    用 scout 的探针替代写死的 wslDistro，适应客户已有 OpenClaw 环境。"""
    log("Step 9: Handoff...")
    _writeSignal(tr("ui.install.sig.extract_gw"), 90, "complete")

    gwUrl = "http://127.0.0.1:18789"
    gwToken = ""
    gwMode = "token"

    # 仅装壳场景：探针找已有 OpenClaw 的发行版；全装场景用刚装好的 wslDistro
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
            # 全装：用刚装好的发行版读 token
            auth = readGatewayAuth(wslDistro)
            if auth.get("ok"):
                gwUrl = auth.get("url", gwUrl)
                gwToken = auth.get("token", "")
                gwMode = auth.get("mode", "token")
    except SystemExit:
        raise
    except Exception:
        pass

    # 兼容旧路径：探针没拿到 token 时，回退直接读
    if not gwToken:
        rc, out, _ = runWsl(f'wsl -d {wslDistro} -u root -- bash -c "cat ~/.openclaw/openclaw.json 2>/dev/null"', timeout=30)
        if rc == 0:
            try:
                cfg = json.loads(out)
                gwToken = cfg.get("gateway", {}).get("auth", {}).get("token", "")
                gwMode = cfg.get("gateway", {}).get("auth", {}).get("mode", gwMode)
            except json.JSONDecodeError:
                pass

    # 从 gateway status 提取 Dashboard URL
    rc, out, _ = runWsl(f'wsl -d {wslDistro} -- bash -c "openclaw gateway status"', timeout=None)
    if rc == 0:
        for line in out.splitlines():
            ll = line.lower()
            if "dashboard" in ll or "control ui" in ll:
                parts = line.strip().split(None, 1)
                if len(parts) > 1 and "http" in parts[1].lower():
                    gwUrl = parts[1].strip()
                break

    # 显式确保 glory 目录存在
    import kernel.glory as _glory_mod
    _glory_mod._ensureGloryDir()
    from kernel.glory import adopt
    adopt(gwUrl, gwToken)
    log(f"  glory 已接管连接: url={gwUrl} mode={gwMode}")
    markProgress(9)

### GATE: step 9 finalized DO NOT MODIFY ###



def parseArgs():
    if "--continue" in sys.argv:
        resume()
    elif loadProgress():
        # 进度文件已存在且有已完成步骤 → 自动续装（重启后走 setup.exe 静默实例，
        # [Run] 里不带 --continue，但已有进度就续装，避免重新检测 WSL/VMP 死循环）
        log("检测到已有安装进度，自动进入续装")
        resume()
    else:
        run()
    # 安装流程走完：放开发行版，强制退出 install.py 进程，不留任何残留
    _releaseDistro()
    os._exit(0)
def _prune_stale_session_path() -> None:
    """2026-10-21：升级/安装时剪掉“上次会话路径”。

    这条偏好存在 ProgramData\\Rheia\\baseline_pool.db 里，卸载/重装都不清；
    一旦它指向已删的 agent/会话，壳每次启动都加载死路 → 页面永远“连接失败/重试”。
    安装时清一次，保证已经坏掉的机器**重装即恢复**，不用人肉进 DB 删行。
    """
    try:
        import sqlite3
        p = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia", "baseline_pool.db")
        if not os.path.isfile(p):
            return
        con = sqlite3.connect(p, timeout=5)
        try:
            con.execute("DELETE FROM user_prefs WHERE key='web_last_path'")
            con.commit()
        finally:
            con.close()
    except Exception:                                         # noqa: BLE001
        pass


if __name__ == "__main__":
    _prune_stale_session_path()
    parseArgs()