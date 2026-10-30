"""
Rheia Gateway 热诊断面板
=================================
通过自定义区「Gateway 诊断」条目进入的 QDialog 实时诊断面板。

四个区块：WSL 探活 / Gateway 进程状态 / Token 摘要 / 日志尾行。
打开时实时探活不缓存，绿/黄/红三色编码。
底部操作：重启 Gateway（pkill 精准化）+ 复制诊断报告。

pkill 精确化：
  不用裸 pkill -f 'openclaw gateway'。
  改为 pgrep 先确认 PID，再 kill 精确匹配的那个。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

_proj = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _proj not in sys.path:
    sys.path.insert(0, _proj)

# ── 主题（独立于 PySide6，允许无 GUI 导入）──
try:
    from ui.theme import fit_button_text, BG, TEXT, ACCENT, ACCENT_HOVER, STATUS, BORDER as _B
    from ui.i18n import tr
except ImportError:
    BG = {"deep": "#1a1a1a", "card": "#2a2a2a", "hover": "#333333"}
    TEXT = {"primary": "#e0e0e0", "secondary": "#b0b0b0", "hint": "#a0a0a0"}
    ACCENT = "#f37021"
    ACCENT_HOVER = "#ff8833"
    STATUS = {"green": "#5a9e6f", "yellow": "#d9a840", "red": "#e85a4a", "gray": "#6a5e52"}
    _B = "#444444"

try:
    from PySide6.QtCore import Qt, QThread, QTimer, Signal
    from PySide6.QtWidgets import (
        QDialog, QHBoxLayout, QLabel, QMessageBox,
        QPushButton, QScrollArea, QVBoxLayout, QWidget, QTextEdit,
        QProgressBar, QApplication, QFrame,
    )
    _HAS_PYQT = True
except ImportError:
    _HAS_PYQT = False
    QDialog = QWidget = QTextEdit = QProgressBar = QFrame = QApplication = object  # type: ignore
    QPushButton = QLabel = QScrollArea = QVBoxLayout = QHBoxLayout = object  # type: ignore
    QMessageBox = QTimer = object  # type: ignore
    Qt = type("_QtStub", (), {})  # type: ignore
    Signal = object  # type: ignore


# ═══════════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════════

WSL_DISTRO = os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu"
GATEWAY_PORT = 18789
GATEWAY_UNIT = "openclaw-gateway"   # systemd 单元名（探活/重启都问它，不 pgrep 匹配安装路径）
_LOCALDATA = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia")

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

# ═══════════════════════════════════════════════════════════════════
# WSL helpers
# ═══════════════════════════════════════════════════════════════════

def _run_wsl(cmd: str) -> tuple[int, str, str]:
    """跑一条 WSL 命令，返回 (rc, stdout, stderr)。

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


# ═══════════════════════════════════════════════════════════════════
# 诊断数据采集
# ═══════════════════════════════════════════════════════════════════

class _DiagData:
    """实时采集的诊断数据容器。"""

    def __init__(self) -> None:
        self.collected_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # WSL
        self.wsl_alive = False
        self.wsl_detail = ""

        # Gateway 进程
        self.gw_pid = ""
        self.gw_port = GATEWAY_PORT
        self.gw_crash_loop = False
        self.gw_crash_count = 0
        self.gw_running = False
        self.gw_detail = ""

        # Token（来自 glory 模块）
        self.token_snippet = ""
        self.token_valid = False
        self.token_verified = ""

        # 日志
        self.log_lines = ""

    def to_report(self) -> str:
        """拼成纯文本诊断报告。"""
        lines = [
            tr("ui.diag.report_title"),
            tr("ui.diag.report_time", ts=self.collected_at),
            f"{'='*50}",
            f"",
            f"[WSL] {tr('ui.diag.st_running') if self.wsl_alive else tr('ui.diag.st_unreachable')}",
            f"  {self.wsl_detail}",
            f"",
            f"[Gateway] {tr('ui.diag.st_running') if self.gw_running else tr('ui.diag.st_not_running')}",
            f"  PID: {self.gw_pid or 'N/A'}",
            tr("ui.diag.report_port", port=self.gw_port),
            tr("ui.diag.report_crash_loop", v=tr('ui.diag.yes') if self.gw_crash_loop else tr('ui.diag.no')),
            tr("ui.diag.report_crash_count", n=self.gw_crash_count),
            f"",
            f"[Token]",
            tr("ui.diag.report_token_snippet", s=self.token_snippet or 'N/A'),
            tr("ui.diag.report_token_state", v=tr('ui.diag.st_valid') if self.token_valid else tr('ui.diag.st_invalid')),
            tr("ui.diag.report_token_verified", v=self.token_verified or 'N/A'),
            f"",
            tr("ui.diag.report_log_tail"),
        ]
        if self.log_lines:
            for ln in self.log_lines.strip().split("\n"):
                lines.append(f"  {ln}")
        else:
            lines.append("  " + tr("ui.diag.no_log"))
        lines.append(f"")
        lines.append(f"{'='*50}")
        lines.append(tr("ui.diag.report_end"))
        return "\n".join(lines)


def _probe_wsl(diag: _DiagData) -> None:
    """WSL 探活 —— 无时间闸（命令返回即信号；下面只把耗时当观测值记下来）。"""
    start = time.monotonic()
    rc, out, err = _run_wsl(f"wsl -d {WSL_DISTRO} -- echo ok")
    elapsed = time.monotonic() - start
    diag.wsl_alive = (rc == 0 and "ok" in out.lower())
    diag.wsl_detail = tr("ui.diag.wsl_resp_time", s=f"{elapsed:.2f}")
    if not diag.wsl_alive:
        diag.wsl_detail += f" | rc={rc} err={(err or '').strip()[:80]}"


def _probe_gateway(diag: _DiagData) -> None:
    """Gateway 进程探活 — systemd MainPID + 端口 + 崩溃循环（NRestarts）。"""
    if not diag.wsl_alive:
        return

    # 1. 进程探活：问 systemd 要 MainPID（原来 pgrep 匹配
    #    "openclaw[/]dist/index.js gateway" 这个安装路径/文件名 —— 官方一改打包
    #    结构或换入口就永久探不到）。
    rc, out, _ = _run_wsl(
        f"wsl -d {WSL_DISTRO} -u root -- bash -c "
        f"\"systemctl show -p MainPID --value {GATEWAY_UNIT} 2>/dev/null\""
    )
    mainpid = (out or "").strip()
    if rc == 0 and mainpid and mainpid != "0":
        diag.gw_pid = mainpid
        diag.gw_running = True
        diag.gw_detail = tr("ui.diag.gw_pid_port", p=diag.gw_pid, port=diag.gw_port)
    else:
        diag.gw_pid = ""
        diag.gw_running = False
        diag.gw_detail = tr("ui.diag.gw_not_found")

    # 2. 端口检查
    rc2, out2, _ = _run_wsl(
        f"wsl -d {WSL_DISTRO} -- bash -c "
        f"\"ss -tlnp 2>/dev/null | grep ':{GATEWAY_PORT}' || "
        f"netstat -tlnp 2>/dev/null | grep ':{GATEWAY_PORT}'\"")
    if rc2 == 0 and out2.strip():
        diag.gw_detail += tr("ui.diag.gw_port_listening", port=GATEWAY_PORT)

    # 3. Gateway 探活（HTTP）
    try:
        from kernel.glory import verify as glory_verify
        glory_verify()
        from kernel.glory import status as glory_status
        gs = glory_status()
        diag.gw_running = diag.gw_running or gs.get("connected", False)
    except Exception:
        pass

    # 4. 崩溃循环：取 systemd 自己的重启计数 NRestarts。
    #    原来 grep 日志里 "crash|panic|SIGSEGV|OOM" 的行数 >= 3 就当崩溃循环 ——
    #    关键词计数既会漏（换个措辞就数不到）也会误报（历史日志里提一句就算）。
    try:
        rc3, out3, _ = _run_wsl(
            f"wsl -d {WSL_DISTRO} -u root -- bash -c "
            f"\"systemctl show -p NRestarts --value {GATEWAY_UNIT} 2>/dev/null\""
        )
        if rc3 == 0 and out3.strip().isdigit():
            diag.gw_crash_count = int(out3.strip())
            diag.gw_crash_loop = diag.gw_crash_count >= 3
    except Exception:
        pass


def _probe_token(diag: _DiagData) -> None:
    """Token 摘要 — 前8位…后4位 + 有效性。

    ⚠ token 显示为空的修复：
      原来这里只读 `kernel.glory.status()["token"]` —— 那是 glory **自己那份记录**，
      靠 `glory.adopt()/verify()` 流程写入。重装/重引导之后没人跑过 adopt ⇒ 它那份是空的
      ⇒ 面板显示 `token=none`，而**真实 token 好端端躺在 WSL 的 openclaw.json 里**
      （主路 `gw.token` 报的就是 present）。**读的地方不对，不是丢了。**
      现在：摘要取**真实那份**（与壳连网关用的是同一份：WSL 配置 → 壳缓存兜底）；
      glory 那份只用来表示"验证状态"（连没连上、什么时候验过）。
    """
    try:
        token = ""
        cache_err = ""       # 兜底缓存读失败的原因（区分「没配」与「读失败」）
        try:
            from main.main import _read_glory
            _url, token = _read_glory()
        except Exception:                                        # noqa: BLE001
            token = ""
        if not token:
            # 兜底：读壳缓存（与主路同一口径）
            try:
                import json as _json
                _p = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia",
                                  "gateway_config.json")
                with open(_p, encoding="utf-8") as _f:
                    _cfg = _json.load(_f) or {}
                token = str(_cfg.get("token") or "")
            except Exception as _e:                              # noqa: BLE001
                cache_err = f"{type(_e).__name__}: {_e}"

        # 验证状态仍归 glory（它记的是"住户连上/验过"这件事，不是 token 本身）
        verified, verified_at = False, 0.0
        try:
            from kernel.glory import status as glory_status
            gs = glory_status() or {}
            verified = bool(gs.get("connected"))
            verified_at = float(gs.get("verified_at") or 0)
        except Exception:                                        # noqa: BLE001
            pass

        if token:
            diag.token_snippet = (f"{token[:8]}…{token[-4:]}" if len(token) >= 16
                                  else (f"{token[:4]}…" if len(token) > 8 else token))
            diag.token_valid = True          # 有效 = 拿到了真实那份（能不能连，见验证状态）
            diag.token_verified = (verified
                                   and datetime.fromtimestamp(verified_at).strftime("%Y-%m-%d %H:%M:%S")
                                   or tr("ui.diag.never_verified"))
        else:
            diag.token_snippet = ""
            diag.token_valid = False
            # 「没配 token」和「缓存读失败」分开说（原来两种都只报 token_empty，
            # 用户看不出是没配还是读坏了）。
            diag.token_verified = (tr("ui.diag.token_read_fail", e=cache_err)
                                   if cache_err else tr("ui.diag.token_empty"))
    except Exception as e:
        diag.token_snippet = ""
        diag.token_valid = False
        diag.token_verified = tr("ui.diag.token_read_fail", e=str(e))


def _probe_logs(diag: _DiagData) -> None:
    """读取 Gateway 日志尾 30 行（展示最新 10 行）。"""
    if not diag.wsl_alive:
        return
    rc, out, _ = _run_wsl(
        f"wsl -d {WSL_DISTRO} -- bash -c "
        f"\"tail -30 ~/.openclaw/gateway.log 2>/dev/null\"")
    if rc == 0 and out.strip():
        lines = out.strip().split("\n")
        diag.log_lines = "\n".join(lines[-10:])
    else:
        diag.log_lines = ""


def collect_diagnostics() -> _DiagData:
    """实时采集全部诊断数据。"""
    diag = _DiagData()
    _probe_wsl(diag)
    if diag.wsl_alive:
        _probe_gateway(diag)
        _probe_token(diag)
        _probe_logs(diag)

    # 写 stake 桩
    try:
        from main.main import stk
        stk("gw.diag", f"wsl={diag.wsl_alive} gw={diag.gw_running} "
            f"pid={diag.gw_pid} token={'valid' if diag.token_valid else 'none'}",
            ok=diag.wsl_alive and diag.gw_running)
    except Exception:
        pass

    return diag


# ═══════════════════════════════════════════════════════════════════
# 重启 Gateway（pkill 精准化）
# ═══════════════════════════════════════════════════════════════════

def _restart_gateway_precise(log_callback=None) -> bool:
    """重启 Gateway —— 统一走 systemd 托管（stop→确认死透→start）。

    不再 pgrep + kill + 裸 Popen：裸 Popen 会绕过 systemd 单实例，起一个
    systemd 看不到的游离 gateway 进程，它攥着 state-lifecycle 锁不撒手，
    后续所有 systemctl start 都撞锁 → gateway 起不来（2026-09-04 日志铁证：
    游离 pid 6370 从 10:08 起占 18789 + state 锁，systemd 侧反复
    Failed exit-code）。installer/install.py:1578 早已纠正为 systemd 托管，
    这里是漏网的裸启动。

    log_callback(msg: str) 用于向 UI 线程报告进度。
    """

    def _log(msg: str) -> None:
        if log_callback:
            log_callback(msg)
        logger.info("diag_panel restart: %s", msg)

    try:
        from main.main import restart_gateway_safe, check_gateway_alive
    except ImportError as _ie:
        _log(f"恢复模块不可用: {_ie}")
        return False

    _log("正在重启 Gateway...")
    # ⚠⚠ 2026-10-17（屏闪根因 4 的连带缺口）：动网关状态必须在**生命周期锁**里 ——
    #   原来这里裸调 restart_gateway_safe，会跟 keepalive/UI 那条重启链**并发撞
    #   OpenClaw 单实例迁移锁**。拿不到锁就不下手（交给在管的 owner），并记账。
    try:
        from main.main import acquire_lifecycle as _acq, release_lifecycle as _rel
    except Exception:                                                 # noqa: BLE001
        _acq = _rel = None
    _owned = False
    if _acq is not None:
        try:
            _owned = bool(_acq("diag_panel.restart"))
        except Exception:                                             # noqa: BLE001
            _owned = False
        if not _owned:
            _log("另一条重启链在跑 ⇒ 本次不并发重启（交给它）")
            try:
                from main.main import stk as _stk_dp
                _stk_dp("diag.gw.restart", "锁被别的 owner 占着 ⇒ 不下发 restart", ok=False)
            except Exception:                                         # noqa: BLE001
                pass
            return False
    try:
        # 2026-10-19：客户亲手点的「重启 Gateway」= 手动动作，必须 manual=True
        alive = restart_gateway_safe(_log, manual=True)
    except Exception as _e:
        _log(f"安全重启异常: {_e}")
        alive = False
    finally:
        if _owned and _rel is not None:
            try:
                _rel("diag_panel.restart")
            except Exception:                                         # noqa: BLE001
                pass

    if alive:
        _log("Gateway 已就绪")
        return True

    # 安全重启后仍不通 → 全链路恢复（doctor --fix 清锁 + start）
    _log("安全重启未就绪，启动全链路恢复...")
    try:
        from main.main import recover_gateway
        recovered, summary = recover_gateway(log_callback=_log, manual=True)
        _log(f"恢复结果: {summary}")
        return recovered
    except ImportError as _ie:
        _log(f"恢复模块不可用: {_ie}")
        return False


# ═══════════════════════════════════════════════════════════════════
# 修复 Gateway（停 → 读状态 → doctor --fix → 读状态 → 没起才 start）
# ═══════════════════════════════════════════════════════════════════

def _gw_alive() -> bool:
    """问一次网关活没活 —— 走 main.main 那条判据，壳不另造一套。"""
    try:
        from main.main import check_gateway_alive
        return bool(check_gateway_alive())
    except Exception:                                                 # noqa: BLE001
        return False


def _repair_gateway(log_callback=None) -> bool:
    """修网关：`gateway stop` → 读状态确认已停 → `doctor --fix` → 读状态 →
    doctor 自己把网关拉起来就交给它跑；没起来才 `gateway start`。

    这条链只做这几步，不夹带别的动作。
    """
    def _log(msg: str) -> None:
        if log_callback:
            log_callback(msg)
        logger.info("diag_panel repair: %s", msg)

    _prefix = ("wsl -d %s -u root -- bash -c "
               "\"export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; "
               % WSL_DISTRO)

    def _wsl(cmd: str) -> tuple:
        return _run_wsl(_prefix + cmd + ' 2>&1"')

    # ① 停
    _log("① 正在停止 Gateway…")
    rc, out, err = _wsl("openclaw gateway stop")
    _log("   stop 回执 rc=%s %s" % (rc, (out + err).strip()[:200]))

    # ② 读状态（stop 命令返回 = 系统层面已停；这里只读一眼，不轮询、不掐表）
    alive = _gw_alive()
    _log("② 网关状态：%s" % ("仍在运行" if alive else "已停止"))

    # ③ doctor --fix
    _log("③ 正在执行 openclaw doctor --fix…")
    rc2, out2, err2 = _wsl("openclaw doctor --fix")
    for line in (out2 + err2).strip().splitlines()[-10:]:
        if line.strip():
            _log("   " + line.strip())
    _log("   doctor 回执 rc=%s" % rc2)

    # ④ 读状态（doctor 结束时已自己重启并验证；这里只读一眼，不轮询）
    alive2 = _gw_alive()
    if alive2:
        _log("④ doctor 之后网关已自行运行，交给它跑")
        return True

    _log("④ 网关没有自己起来 ⇒ 执行 openclaw gateway start…")
    rc3, out3, err3 = _wsl("openclaw gateway start")
    _log("   start 回执 rc=%s %s" % (rc3, (out3 + err3).strip()[:200]))
    _up = _gw_alive()
    _log("   启动后状态：%s" % ("已运行" if _up else "仍未起来"))
    return _up


# ═══════════════════════════════════════════════════════════════════
# 异步 Worker — QThread 避免主线程同步 WSL 导致 UI 假死
# ═══════════════════════════════════════════════════════════════════

class _RestartWorker(QThread):
    """后台执行 Gateway 重启 — 先精确重启，失败则全链路恢复。"""
    progress = Signal(str)  # 日志行
    finished = Signal(bool)  # ok

    def run(self):
        def _log(msg: str) -> None:
            self.progress.emit(msg)

        # 「失败 → 全链路恢复」只写一处：在 _restart_gateway_precise 里（它自己收尾）。
        # 原来 worker 里又抄了一遍同样的兜底，同一件事两份实现必然漂移。
        ok = _restart_gateway_precise(log_callback=_log)

        try:
            from main.main import stk
            stk("gw.diag.restart", f"ok={ok}", ok=ok)
        except Exception:
            pass
        self.finished.emit(ok)


class _RepairWorker(QThread):
    """后台执行「修复 Gateway」— 停 → 读状态 → doctor --fix → 读状态 → 按需 start。"""
    progress = Signal(str)
    finished = Signal(bool)

    def run(self):
        def _log(msg: str) -> None:
            self.progress.emit(msg)

        ok = _repair_gateway(log_callback=_log)
        try:
            from main.main import stk
            stk("gw.diag.repair", f"ok={ok}", ok=ok)
        except Exception:                                             # noqa: BLE001
            pass
        self.finished.emit(ok)


class _UpgradeWorker(QThread):
    """后台执行 Gateway 升级 —— 只走官方 update 通道。

    官方 update 自带版本检查、依赖处理、守卫与失败回滚，所以这里**不再自己**
    跑 npm cache clean / npm uninstall / npm install（那是绕过官方的第二套升级
    入口，没有守卫也没有回滚），也不再手动停服 / 15 秒轮询判超时。
    """
    progress = Signal(str)
    finished = Signal(bool, str)

    def run(self):
        def _log(msg: str) -> None:
            self.progress.emit(msg)

        # ── 走官方 update 通道 ──
        # 原来这里是自带的第二套升级：npm cache clean --force + npm uninstall -g openclaw
        # + npm install -g openclaw@latest（镜像失败再直连）+ 手动停服（fuser -k 按端口杀）
        # —— 整套绕开官方 update 的守卫与回滚。现在只调官方 update，收尾也归它。
        from main.main import cli_usage
        update_cmd = cli_usage("update", "") or "openclaw update"
        _log(f"执行官方升级：{update_cmd} --yes")
        ok = True
        rc2, out2, err2 = _run_wsl(
            f'wsl -d {WSL_DISTRO} -u root -- bash -c '
            f'"export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; '
            f'{update_cmd} --yes 2>&1"'
        )

        # 末尾几行输出给用户看（官方 update 自己会把过程和结果打出来）
        msg = (out2 + err2).strip()
        for line in msg.split("\n")[-8:]:
            if line.strip():
                _log(f"  {line.strip()}")
        ok = rc2 == 0
        try:
            from main.main import stk
            stk("gw.upgrade", f"official update rc={rc2}", ok=ok)
        except Exception:
            pass

        # ── 报告结论 ──
        # 不再自己 start_gateway + 15 秒轮询探活（那是时间闸 + 忙轮询）：启动/重启
        # 是官方 update 自己的收尾动作，成没成看它的 rc。
        if ok:
            _log("升级完成（官方 update 已收尾）")
            self.finished.emit(True, "")
        else:
            _log("升级失败")
            self.finished.emit(False, f"official update rc={rc2}")


# ═══════════════════════════════════════════════════════════════════
# 区块 UI 组件
# ═══════════════════════════════════════════════════════════════════

class _CollectWorker(QThread):
    """后台采集诊断数据（6 条 WSL 命令，不能在 GUI 线程里同步跑）。"""
    done = Signal(object)   # _DiagData | None

    def run(self) -> None:
        try:
            self.done.emit(collect_diagnostics())
        except Exception as _e:      # 兜底：异常绝不留子线程
            try:
                from main.main import stk
                stk("gw.diag.collect", f"failed: {_e}", ok=False)
            except Exception:
                pass
            self.done.emit(None)


class _StatusBlock(QWidget):
    """统一状态区块：标题 + 状态灯 + 内容 + 附加信息。"""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background:transparent;")

        lo = QVBoxLayout(self)
        lo.setContentsMargins(12, 8, 12, 8)
        lo.setSpacing(4)

        # 标题行
        hdr = QHBoxLayout()
        hdr.setSpacing(6)
        tl = QLabel(title)
        tl.setStyleSheet(f"color:{_TEXT_PRIMARY};font-size:13px;font-weight:600;")
        hdr.addWidget(tl)
        self._dot = QLabel("")
        self._dot.setStyleSheet("font-size:14px;background:transparent;")
        hdr.addWidget(self._dot)
        hdr.addStretch()
        self._status_text = QLabel("")
        self._status_text.setStyleSheet(f"color:{_TEXT_HINT};font-size:11px;")
        hdr.addWidget(self._status_text)
        lo.addLayout(hdr)

        # 内容
        self._content = QLabel("")
        self._content.setWordWrap(True)
        self._content.setStyleSheet(f"color:{_TEXT_PRIMARY};font-size:12px;background:{BG['hover']};"
                                     f"border-radius:4px;padding:6px 8px;")
        self._content.setMinimumHeight(24)
        self._content.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lo.addWidget(self._content)

    def set_status(self, color: str, icon: str, text: str) -> None:
        self._dot.setText(icon)
        self._dot.setStyleSheet(f"color:{color};font-size:14px;background:transparent;")
        self._status_text.setText(text)

    def set_content(self, text: str) -> None:
        self._content.setText(text)


class _LogBlock(QWidget):
    """日志区块：可滚动、可选中复制。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lo = QVBoxLayout(self)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(4)

        tl = QLabel(tr("ui.diag.recent_logs"))
        tl.setStyleSheet(f"color:{_TEXT_PRIMARY};font-size:13px;font-weight:600;padding:0 12px;")
        lo.addWidget(tl)

        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setMaximumHeight(160)
        self._text.setStyleSheet(f"""
            QTextEdit {{
                background: {BG['hover']};
                color: {_TEXT_HINT};
                border: 1px solid {_BORDER};
                border-radius: 4px;
                padding: 6px 8px;
                font-family: 'Cascadia Code', 'Consolas', monospace;
                font-size: 11px;
                line-height: 1.4;
            }}
            QScrollBar:vertical {{ background: transparent; width: 5px; margin: 0; }}
            QScrollBar::handle:vertical {{ background: #444; border-radius: 2px; min-height: 16px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)
        lo.addWidget(self._text)

    def set_log(self, text: str) -> None:
        self._text.setPlainText(text)


# ═══════════════════════════════════════════════════════════════════
# DiagPanel QDialog
# ═══════════════════════════════════════════════════════════════════

class DiagPanel(QDialog):
    """Gateway 热诊断面板 — 自定义区「Gateway 诊断」入口。

    打开时实时探活，不缓存。四区块 + 底部操作按钮。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        if not _HAS_PYQT:
            raise RuntimeError("PySide6 not available")

        super().__init__(parent)
        self.setWindowTitle(tr("ui.diag.title"))
        from ui.theme import fitToScreen
        fitToScreen(self, 0.32, 0.62)

        self.setStyleSheet(f"""
            QDialog {{ background: {_CARD_BG}; color: {_TEXT_PRIMARY}; }}
            QLabel {{ background: transparent; }}
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{ background: transparent; width: 6px; margin: 0; }}
            QScrollBar::handle:vertical {{ background: #444; border-radius: 3px; min-height: 20px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

        self._data: _DiagData | None = None
        self._building = False
        self._build()

    def _build(self) -> None:
        lo = QVBoxLayout(self)
        lo.setContentsMargins(16, 12, 16, 12)
        lo.setSpacing(8)

        # ── 标题 ──
        title = QLabel(tr("ui.diag.title"))
        title.setStyleSheet(f"color:{_TEXT_PRIMARY};font-size:16px;font-weight:bold;")
        lo.addWidget(title)

        desc = QLabel(tr("ui.diag.desc"))
        desc.setStyleSheet(f"color:{_TEXT_HINT};font-size:11px;")
        lo.addWidget(desc)

        # ── 滚动区（四区块）──
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sc_content = QWidget()
        sc_content.setStyleSheet("background:transparent;")
        sc_lo = QVBoxLayout(sc_content)
        sc_lo.setContentsMargins(0, 0, 0, 0)
        sc_lo.setSpacing(6)

        self._wsl_block = _StatusBlock(tr("ui.diag.wsl"))
        sc_lo.addWidget(self._wsl_block)

        self._gw_block = _StatusBlock(tr("ui.diag.gw_process"))
        sc_lo.addWidget(self._gw_block)

        self._token_block = _StatusBlock(tr("ui.diag.token"))
        sc_lo.addWidget(self._token_block)

        self._log_block = _LogBlock()
        sc_lo.addWidget(self._log_block)

        sc_lo.addStretch()
        sc.setWidget(sc_content)
        lo.addWidget(sc, stretch=1)

        # ── 收集时间 ──
        self._time_label = QLabel("")
        self._time_label.setStyleSheet(f"color:{_TEXT_HINT};font-size:10px;padding:0 12px;")
        lo.addWidget(self._time_label)

        # ── 进度条（初始隐藏）──
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        # 不用 setRange(0,0) 的「不确定进度」：那种模式每帧自绘滚动 chunk（持续动画），
        # 和下方按钮的可见性翻转挤在一起就是肉眼可见的扫动/闪；这里只用静态底条。
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._progress.setFixedHeight(4)
        self._progress.setStyleSheet(f"""
            QProgressBar {{ background: {BG['hover']}; border: none; border-radius: 2px; }}
            QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}
        """)
        lo.addWidget(self._progress)

        # ── 底部按钮 ──
        bot = QHBoxLayout()
        bot.setContentsMargins(0, 4, 0, 0)

        copy_btn = QPushButton(tr("ui.diag.copy_report"))
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.setFixedSize(104, 30)
        fit_button_text(copy_btn, width=104)
        copy_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {_TEXT_SEC};
                           border: 1px solid {_BORDER}; border-radius: 6px;
                           padding: 4px 14px; font-size: 11px; }}
            QPushButton:hover {{ color: {_TEXT_PRIMARY}; border-color: {ACCENT}; }}
        """)
        copy_btn.clicked.connect(self._on_copy)
        bot.addWidget(copy_btn)

        bot.addStretch()

        refresh_btn = QPushButton(tr("ui.btn.refresh"))
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.setFixedSize(64, 30)
        fit_button_text(refresh_btn, width=64)
        refresh_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {_TEXT_SEC};
                           border: 1px solid {_BORDER}; border-radius: 6px; font-size: 11px; }}
            QPushButton:hover {{ color: {_TEXT_PRIMARY}; border-color: {ACCENT}; }}
        """)
        refresh_btn.clicked.connect(self._refresh)
        bot.addWidget(refresh_btn)

        self._restart_btn = QPushButton(tr("ui.btn.restart_gateway"))
        self._restart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._restart_btn.setFixedSize(128, 30)
        fit_button_text(self._restart_btn, width=128)
        self._restart_btn.setStyleSheet(f"""
            QPushButton {{ background: {ACCENT}; color: #0a0a0a; border: none;
                           border-radius: 6px; padding: 4px 16px; font-size: 11px; font-weight: 600; }}
            QPushButton:hover {{ background: {ACCENT_HOVER}; }}
            QPushButton:disabled {{ background: #444; color: #888; }}
        """)
        self._restart_btn.clicked.connect(self._on_restart)

        # ── 修复 Gateway（夹在「重启」与「升级」之间，向左撑开）──
        self._repair_btn = QPushButton(tr("ui.diag.repair"))
        self._repair_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._repair_btn.setFixedSize(118, 30)
        fit_button_text(self._repair_btn, width=118)
        self._repair_btn.setStyleSheet(f"""\
            QPushButton {{ background: transparent; color: {_TEXT_SEC};
                           border: 1px solid {_BORDER}; border-radius: 6px;
                           padding: 4px 16px; font-size: 11px; font-weight: 600; }}
            QPushButton:hover {{ color: {_TEXT_PRIMARY}; border-color: {ACCENT}; }}
            QPushButton:disabled {{ border-color: #444; color: #555; }}
        """)
        self._repair_btn.clicked.connect(self._on_repair)

        self._upgrade_btn = QPushButton(tr("ui.diag.upgrade_openclaw"))
        self._upgrade_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._upgrade_btn.setFixedSize(128, 30)
        fit_button_text(self._upgrade_btn, width=128)
        self._upgrade_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {_TEXT_SEC};
                           border: 1px solid {ACCENT}; border-radius: 6px;
                           padding: 4px 16px; font-size: 11px; font-weight: 600; }}
            QPushButton:hover {{ background: {ACCENT}; color: #0a0a0a; }}
            QPushButton:disabled {{ border-color: #444; color: #555; }}
        """)
        self._upgrade_btn.clicked.connect(self._on_upgrade)

        close_btn = QPushButton(tr("ui.btn.close"))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setFixedSize(56, 30)
        fit_button_text(close_btn, width=56)
        close_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {_TEXT_SEC};
                           border: 1px solid {_BORDER}; border-radius: 6px; font-size: 11px; }}
            QPushButton:hover {{ color: {_TEXT_PRIMARY}; border-color: {_BORDER}; }}
        """)
        close_btn.clicked.connect(self.accept)
        bot.addWidget(close_btn)

        # 第二行：三个长按钮单独一行，避免与第一行挤在一起（俄语等长文案）
        bot2 = QHBoxLayout()
        bot2.setContentsMargins(0, 6, 0, 0)
        bot2.setSpacing(8)
        bot2.addStretch()
        bot2.addWidget(self._restart_btn)
        bot2.addWidget(self._repair_btn)
        bot2.addWidget(self._upgrade_btn)
        lo.addLayout(bot2)

        lo.addLayout(bot)

        # ── 初始加载 ──
        self._refresh()

    # ── 刷新 ──

    def _refresh(self) -> None:
        """刷新：采集放后台线程。

        原来是在 GUI 线程里同步跑 6 条 WSL 命令（每条冷启动几百 ms）——打开面板即假死。
        现在只起线程，回来再渲染。
        """
        if getattr(self, "_collecting", False):
            return
        self._collecting = True
        self._collect_worker = _CollectWorker(self)
        self._collect_worker.done.connect(self._on_collected)
        self._collect_worker.start()

    def _on_collected(self, diag) -> None:
        """采集线程回调（主线程）：只做渲染。"""
        self._collecting = False
        if diag is None:
            return
        self._data = diag
        self._render(self._data)

    def _render(self, d) -> None:
        """把一份诊断数据铺到四个区块上（纯 UI，不采集）。"""

        # WSL
        if d.wsl_alive:
            self._wsl_block.set_status(_GREEN, "●", tr("ui.diag.st_running"))
        else:
            self._wsl_block.set_status(_RED, "●", tr("ui.diag.st_unreachable"))
        self._wsl_block.set_content(d.wsl_detail)

        # Gateway
        if d.gw_running:
            if d.gw_crash_loop:
                self._gw_block.set_status(_YELLOW, "●", tr("ui.diag.st_running_crash"))
            else:
                self._gw_block.set_status(_GREEN, "●", tr("ui.diag.st_running"))
        elif d.wsl_alive:
            self._gw_block.set_status(_RED, "●", tr("ui.diag.st_not_running"))
        else:
            self._gw_block.set_status(_GRAY, "●", tr("ui.diag.st_wsl_unreachable"))
        gw_text = d.gw_detail
        if d.gw_crash_count > 0:
            gw_text += tr("ui.diag.crash_count", n=d.gw_crash_count)
        self._gw_block.set_content(gw_text)

        # Token
        if d.token_valid:
            self._token_block.set_status(_GREEN, "●", tr("ui.diag.st_valid"))
        elif d.token_snippet:
            self._token_block.set_status(_YELLOW, "●", tr("ui.diag.st_unverified"))
        else:
            self._token_block.set_status(_GRAY, "●", tr("ui.diag.st_missing"))
        tok_text = tr("ui.diag.token_summary", s=d.token_snippet or tr("ui.diag.none"), v=d.token_verified)
        self._token_block.set_content(tok_text)

        # 日志
        self._log_block.set_log(d.log_lines if d.log_lines else tr("ui.diag.no_log"))

        # 时间
        self._time_label.setText(tr("ui.diag.collected_at", ts=d.collected_at))

    # ── 操作 ──

    def _on_restart(self) -> None:
        reply = QMessageBox.question(
            self, tr("ui.diag.confirm_restart_title"),
            tr("ui.diag.confirm_restart_body"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._restart_btn.setEnabled(False)
        self._restart_btn.setText(tr("ui.diag.restarting"))
        self._progress.setVisible(True)
        self._log_block.set_log(tr("ui.diag.restarting_gw") + "\n")

        logs: list[str] = []

        def _on_progress(msg: str) -> None:
            logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
            self._log_block.set_log("\n".join(logs[-12:]))
            QApplication.processEvents()

        def _on_done(ok: bool) -> None:
            if ok:
                logs.append(tr("ui.diag.restart_done"))
                self._log_block.set_log("\n".join(logs[-12:]))
                # 同步 token
                try:
                    from ui.web.provider_setup import _get_gateway_token, _sync_token_to_local
                    new_token = _get_gateway_token()
                    if new_token:
                        _sync_token_to_local(new_token)
                except Exception:
                    pass
            else:
                logs.append(tr("ui.diag.restart_timeout"))
                self._log_block.set_log("\n".join(logs[-12:]))
            self._progress.setVisible(False)
            self._restart_btn.setEnabled(True)
            self._restart_btn.setText(tr("ui.btn.restart_gateway"))
            # 不再 QTimer.singleShot(500, ...)：那会让界面在用户没动作时自己动一下。
            # 就地刷新一次（异步采集，不阻塞）。
            self._refresh()

        self._restart_worker = _RestartWorker()
        self._restart_worker.progress.connect(_on_progress)
        self._restart_worker.finished.connect(_on_done)
        self._restart_worker.start()

    def _on_repair(self) -> None:
        """修复 Gateway：停 → 确认已停 → doctor --fix → 看它自己起没起 → 没起才 start。"""
        self._repair_btn.setEnabled(False)
        self._repair_btn.setText(tr("ui.diag.repairing"))
        self._progress.setVisible(True)
        self._log_block.set_log("开始修复 Gateway…\n")

        logs: list[str] = []

        def _on_progress(msg: str) -> None:
            logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
            self._log_block.set_log("\n".join(logs[-16:]))
            QApplication.processEvents()

        def _on_done(ok: bool) -> None:
            logs.append("修复完成 ✓" if ok else "修复结束，但网关没起来 ✗")
            self._log_block.set_log("\n".join(logs[-16:]))
            self._progress.setVisible(False)
            self._repair_btn.setEnabled(True)
            self._repair_btn.setText(tr("ui.diag.repair"))
            self._refresh()

        self._repair_worker = _RepairWorker()
        self._repair_worker.progress.connect(_on_progress)
        self._repair_worker.finished.connect(_on_done)
        self._repair_worker.start()

    def _on_upgrade(self) -> None:
        # 不再用 npm view 查最新版（那是绕过官方 update 的第二套版本判据）：有没有新版、
        # 要不要升，交给官方 update 自己判（它有守卫与回滚）。这里只报当前本地版本。
        from main.main import cli_usage
        version_cmd = cli_usage("openclaw", "version") or "openclaw --version"
        rc_cur, out_cur, _ = _run_wsl(
            f'wsl -d {WSL_DISTRO} -u root -- bash -c '
            f'"export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; '
            f'{version_cmd} 2>&1"'
        )
        cur_ver = ""
        if rc_cur == 0:
            import re as _re
            m = _re.search(r'(\d+\.\d+\.\d+[\-\w.]*)', out_cur)
            if m:
                cur_ver = m.group(1)

        self._log_block.set_log("")

        reply = QMessageBox.question(
            self, tr("ui.diag.confirm_upgrade_title"),
            tr("ui.diag.confirm_upgrade_body", cur=cur_ver or tr("ui.diag.unknown"),
               latest=tr("ui.diag.unknown")),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._upgrade_btn.setEnabled(False)
        self._upgrade_btn.setText(tr("ui.diag.upgrading"))
        self._progress.setVisible(True)

        logs: list[str] = []

        def _on_progress(msg: str) -> None:
            logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
            self._log_block.set_log("\n".join(logs[-12:]))
            QApplication.processEvents()

        def _on_done(ok: bool, err_msg: str) -> None:
            if ok:
                logs.append(tr("ui.diag.upgrade_done"))
                self._log_block.set_log("\n".join(logs[-12:]))
                try:
                    from ui.web.provider_setup import _get_gateway_token, _sync_token_to_local
                    new_token = _get_gateway_token()
                    if new_token:
                        _sync_token_to_local(new_token)
                except Exception:
                    pass
            else:
                logs.append(err_msg)
                self._log_block.set_log("\n".join(logs[-12:]))
            self._upgrade_btn.setEnabled(True)
            self._upgrade_btn.setText(tr("ui.diag.upgrade_openclaw"))
            self._progress.setVisible(False)
            # 不再 QTimer.singleShot(500, ...)（用户没动作时界面自己动一下）。
            self._refresh()

        self._upgrade_worker = _UpgradeWorker()
        self._upgrade_worker.progress.connect(_on_progress)
        self._upgrade_worker.finished.connect(_on_done)
        self._upgrade_worker.start()

    def _on_copy(self) -> None:
        if self._data:
            report = self._data.to_report()
        else:
            report = "诊断数据不可用"
        QApplication.clipboard().setText(report)
        # 短暂视觉反馈
        self._time_label.setText(tr("ui.diag.copied_to_clipboard"))
        # 原来 2 秒后再把整个面板重刷一遍（四个区块全 set 一次）—— 用户没要求，去掉。
