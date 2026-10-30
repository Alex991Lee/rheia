# -*- coding: utf-8 -*-
"""tlc — 壳体守护消费端（由 mani.json 声明驱动启动，非硬编常驻）

职责：
  - 监听异常退出码与崩溃预兆，保持壳体不崩溃
  - 单一职责，不掺和业务

声明（settleManifest 覆写 mani 后由 injectTlcDeclaration 注入 entries.tlc）：
  {
    "id": "tlc",
    "type": "tlc_guard",
    "scope": "shell",
    "watch": ["exit_code", "crash_precursor"],
    "policy": "keep_alive",
    "lifecycle": "persistent",
  }
mani 里有 tlc 声明 → bootstrap 调 ensure_tlc() 启动守护；mani 没有 → 不启动
（与 usm 的「mani 有就保持、没有就销毁」同一语义——声明是存在许可证）。

守护机制（进程内，最小可靠）：
  1. 崩溃事件流：crash hooks（global/thread/unraisable）每次触发 → report_crash(kind)
     ——tlc 记录 + 做风暴检测（窗口内崩溃频发 = 预兆）
  2. 预兆检测：短窗口内崩溃 ≥ 阈值 → 判为「崩溃风暴」预兆 → 写警示桩；
     持续风暴 → 尝试温和兜底（记录并建议重启，不阻塞、不掺和业务）
  3. 保活原则：tlc 自己不抛异常、不阻塞主流程；任何异常静默降级
"""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

# 风暴检测参数：30 秒内壳级崩溃 ≥ 5 次 → 判「崩溃风暴」预兆
_STORM_WINDOW_SEC = 30.0
_STORM_THRESHOLD = 5

# UI 心跳监护：主线程每 10s 打一次心跳，超过 35s 无心跳 → 壳体僵死预兆
_HEARTBEAT_INTERVAL = 10.0
_HEARTBEAT_TIMEOUT = 35.0

#: 背景长任务告警档：慢 ⇒ 记一笔；僵死 ⇒ 记一笔并走 hang 干预通道。
#:   15 秒：加载链正常 10 秒量级，超过即记账。
#:   90 秒：超过即视为"点了没反应"，必须上报。
_TASK_SLOW_SEC = 15.0
_TASK_STALL_SEC = 90.0

# 守护单例（幂等启动）
_instance: "TLCGuard | None" = None
_lock = threading.Lock()


class TLCGuard:
    """壳体守护：监听异常退出码/崩溃预兆，保持壳体不崩溃。"""

    def __init__(self, declaration: dict | None = None):
        self._decl = declaration or {}
        # 声明驱动的参数：mani 的 entries.tlc 写什么用什么，缺省用默认值。
        _d = self._decl if isinstance(self._decl, dict) else {}
        self._hb_interval = float(_d.get("heartbeat_interval_s") or _HEARTBEAT_INTERVAL)
        self._hb_timeout = float(_d.get("heartbeat_timeout_s") or _HEARTBEAT_TIMEOUT)
        self._task_slow = float(_d.get("task_slow_s") or _TASK_SLOW_SEC)
        self._task_stall = float(_d.get("task_stall_s") or _TASK_STALL_SEC)
        self._storm_threshold = int(_d.get("storm_threshold") or _STORM_THRESHOLD)
        #: 豁免名单：这些任务不进队列、不判慢（保活、心跳这类不算工人）
        self._exempt = frozenset(str(x) for x in (_d.get("exempt") or []))
        #: 对话中禁止重启（防"重启把消息切断 ⇒ 未发送"）
        self._defer_on_conversation = bool(_d.get("defer_restart_on_conversation", True))
        self._conversation_active = False
        self._defer_since: float | None = None
        self._events: list[tuple[float, str]] = []      # (ts, kind) 壳级崩溃事件流
        self._storm_flagged = False
        self._last_beat = time.time()
        self._stop = threading.Event()
        self._wake = threading.Event()      # 异步唤醒：崩溃上报立即处理，不等轮询
        self._suppressed: tuple[float, str] | None = None  # 受控重启抑制（自愈重启等）
        self._hang_reported = False     # 本轮僵死是否已入队（防每 10 秒重复报）
        #: 背景长任务：name → 开始时刻。加载/注入这类长活自行登记，tlc 才能发现卡住。
        self._tasks: dict = {}
        self._task_reported: dict = {}  # name → 已经报过的档位集合（防重复刷）
        self._actions: list = []            # 待调度动作（跨线程安全队列）
        self._thread: threading.Thread | None = None

    # ── 生命周期 ──

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="tlc-guard")
        self._thread.start()

    def stop_nowait(self) -> None:
        self._stop.set()

    # ── 崩溃事件入口（crash hooks 上报）──

    def suppress(self, reason: str) -> None:
        """受控重启抑制：标记壳体即将正常重启（如自愈重启），
        期间不做僵死/风暴警示——重启是受控动作，不是崩溃。"""
        self._suppressed = (time.time(), reason)

    def resume(self) -> None:
        """解除抑制（一般不需要：重启后新进程是全新 tlc 实例）。"""
        self._suppressed = None

    def heartbeat(self) -> None:
        """主线程心跳（UI 每 10s 调一次）——壳体还活着的证据。"""
        self._last_beat = time.time()

    def report_crash(self, kind: str) -> None:
        """壳级崩溃上报（事件驱动：记录 + 风暴检测 + 唤醒守护立即处理）。"""
        now = time.time()
        self._events.append((now, kind))
        cutoff = now - _STORM_WINDOW_SEC
        self._events = [e for e in self._events if e[0] >= cutoff]
        recent = len(self._events)
        if recent >= self._storm_threshold and not self._storm_flagged:
            self._storm_flagged = True
            self._log_storm(kind, recent)
            self._schedule("storm", recent)
        elif recent < self._storm_threshold:
            self._storm_flagged = False
        self._wake.set()   # 异步唤醒守护（立即处理，不等心跳轮询）

    # ── 背景长任务 ──

    def task_start(self, name: str) -> None:
        """登记一个**背景长任务**（加载/注入/卡侧处理这类）。

        ⚠ 为什么必须有它：UI 心跳只证明"壳还活着"。后台线程里跑的活再慢、再卡，
          心跳照样在跳 ⇒ tlc 眼里一切正常 ⇒ 客户那边"点了没反应"它一声不吭
          （实测就是这么漏的）。报到这儿来，tlc 才看得见"这一步卡住了"。
        """
        if not name:
            return
        self._tasks[str(name)] = time.time()
        self._task_reported.pop(str(name), None)
        self._wake.set()                      # 异步唤醒守护，立即纳入监控

    def task_end(self, name: str) -> None:
        """长任务收工（成功/失败/异常都要调，用 finally 保底）。"""
        if not name:
            return
        self._tasks.pop(str(name), None)
        self._task_reported.pop(str(name), None)

    def _check_tasks(self) -> None:
        """超时检查：慢 ⇒ 报一笔；僵死 ⇒ 报 + 走既有 hang 干预通道（找出谁挡着）。

        节流：每个任务每个档只报一次（跨过 _TASK_SLOW_SEC / _TASK_STALL_SEC 各一次），
        否则每 10 秒复查会把同一条刷几十遍（心跳僵死当年就踩过这个坑）。
        """
        now = time.time()
        for name, t0 in list(self._tasks.items()):
            secs = now - float(t0 or now)
            done = self._task_reported.setdefault(name, set())
            if secs >= _TASK_STALL_SEC and "stall" not in done:
                done.add("stall")
                self._log_task(name, secs, stalled=True)
                self._schedule("hang", {"secs": secs, "task": name})
            elif secs >= self._task_slow and "slow" not in done:
                done.add("slow")
                self._log_task(name, secs, stalled=False)
                # 偏慢也要动手，不只记账：进干预通道（让出等待 / 提示 UI / 挪开该步），动作入账。
                self._schedule("slow", {"secs": secs, "task": name})

    def _log_task(self, name: str, secs: float, *, stalled: bool) -> None:
        try:
            from main.main import stk
            stk("tlc.task.stall" if stalled else "tlc.task.slow",
                ("%s：%s 已经跑了 %.0f 秒（后台卡住，客户那边只会看到'没反应'）"
                 % ("僵死" if stalled else "偏慢", name, secs)),
                ok=not stalled)
        except Exception:
            pass

    def _schedule(self, action: str, payload: object = None) -> None:
        """异步调度动作：入队（守护线程消费），不阻塞上报方。"""
        self._actions.append((action, payload))

    def _log_storm(self, kind: str, count: int) -> None:
        """崩溃风暴预兆：写警示桩（不阻塞、不弹窗、不掺和业务）。"""
        try:
            from main.main import stk
            stk("tlc.storm",
                f"崩溃风暴预兆: {count} 次/{_STORM_WINDOW_SEC:.0f}s（最近 {kind}）",
                ok=False)
        except Exception:
            pass

    # ── 守护主循环 ──

    def _run(self) -> None:
        """守护主循环（事件驱动 + 超时兜底）。

        - 事件唤醒（崩溃上报/动作入队）→ 立即处理
        - 心跳超时检查：Event.wait(timeout) 被唤醒则提前返回，不被唤醒则
          每 _HEARTBEAT_INTERVAL 检查一次壳体僵死（超 35s 无心跳 → 预兆）
        - 异步动作队列消费（调度不阻塞上报方）
        """
        while True:
            try:
                woke = self._wake.wait(self._hb_interval)
                self._wake.clear()
                if self._stop.is_set():
                    break
                # 消费异步动作队列
                while self._actions:
                    act, payload = self._actions.pop(0)
                    self._dispatch(act, payload)
                if not woke:
                    # 超时醒来 → 心跳僵死检查 + 事件流打点
                    from main.main import stk
                    if self._suppressed:
                        # 受控重启中（自愈重启等）——不打僵死警示，重启是正常动作
                        stk("tlc.suppress", f"受控重启中: {self._suppressed[1]}，跳过僵死检查", ok=True)
                        self._suppressed = None   # 只提示一次
                        continue
                    since_beat = time.time() - self._last_beat
                    if since_beat > _HEARTBEAT_TIMEOUT:
                        stk("tlc.hang", f"壳体僵死预兆: {since_beat:.0f}s 无 UI 心跳", ok=False)
                        # 入队干预动作（异步，守护线程自己消费）。
                        # ⚠ 节流：心跳超时是每 10 秒复查一次，不节流会把
                        #   同一个僵死事件重复入队几十次（实测一次僵死报了 9 条）。
                        #   每个僵死周期只报一次，恢复后再允许下一次。
                        if not self._hang_reported:
                            self._hang_reported = True
                            self._schedule("hang", {"secs": since_beat})
                    else:
                        self._hang_reported = False    # 心跳恢复 → 允许下次上报
                    # 背景长任务超时检查（加载/注入这类"心跳正常但活卡住了"）
                    self._check_tasks()
                    if self._events:
                        stk("tlc.watch", f"窗口内壳级崩溃 {len(self._events)} 条",
                            ok=len(self._events) < _STORM_THRESHOLD)
            except Exception:
                pass

    def _dispatch(self, action: str, payload: object) -> None:
        """异步动作分发（守护线程消费）。

        在守护线程里跑，不占 UI 线程 —— 所以即使主线程僵死，这里照样能干活。
        """
        from main.main import stk
        # 偏慢（slow）与僵死（hang）走同一条干预通道：先抓现场，再按动作表处理并记账。
        if action in ("hang", "slow"):
            self._on_hang(payload if isinstance(payload, dict) else {})
        else:
            stk("tlc.action", f"调度动作: {action} payload={payload!r}", ok=False)

    def _on_hang(self, info: dict) -> None:
        """僵死干预（分两级，先探后治）。

        为什么要分级：僵死时「谁卡着」并不确定，贸然重启壳可能把正在写的
        配置包／卡侧正在跑的处理打断，损失更大。所以先抓现场（零风险），
        只在确认是【已知阻塞源】时才动手。

        已知阻塞源：扩展位的卡侧处理子进程。
          它跑一次最长 42 分钟（283 条轨迹 × 274 次串行 LLM），
          期间壳的同步 WSL 调用全被拖住 → UI 冻住。
          （这条已在扩展位 CLI 侧加了非阻塞锁，但存量进程仍需兜底。）
        """
        secs = info.get("secs", 0)
        # ① 抓现场：卡了多久、是第几次、有没有已知阻塞源在跑
        procs = self._find_blocking_procs()
        stk("tlc.hang.detail",
            f"僵死 {secs:.0f}s | 已知阻塞源={procs or '无'}",
            ok=False)
        if not procs:
            # 不是已知阻塞源 → 只记录，不干预（不知道是什么，动了更危险）
            stk("tlc.hang.noaction",
                "未发现已知阻塞源，仅记录（不贸然重启）", ok=False)
            return
        # ② 已知阻塞源存在 → 它是长任务，不是死锁。
        #    不杀（杀了丢卡侧处理进度），只记明「正在等它，属正常长耗时」，
        #    让排查时不再把这种情况误判成 bug。
        stk("tlc.hang.waiting",
            f"正在等卡侧处理长任务（{procs}），非死锁，不干预", ok=True)

    def _find_blocking_procs(self) -> str:
        """找已知阻塞源进程（WSL 里的扩展位 CLI）。返回描述串，无则空。

        走 wsl + pgrep；任何失败都静默返回空（看门狗自己不抛异常）。
        """
        try:
            import os
            import subprocess
            _distro = os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu"
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            r = subprocess.run(
                ["wsl", "-d", _distro, "-u", "root", "--", "bash", "-c",
                 "pgrep -fc indivi[.]py || true"],
                capture_output=True, encoding="utf-8",
                errors="replace", creationflags=subprocess.CREATE_NO_WINDOW,
                startupinfo=si,
            )
            n = (r.stdout or "").strip()
            if n and n != "0":
                return f"indivi.py×{n}"
        except Exception:
            pass
        return ""


def ensure_tlc(declaration: dict | None = None) -> TLCGuard | None:
    """按声明启动 tlc 守护（幂等单例）。声明为空也启动（默认配置）。

    声明里的参数当场生效：mani 的 entries.tlc 写什么用什么（心跳间隔/僵死线、
    任务档位、崩溃风暴阈值），不写沿用默认。
    """
    global _instance, _HEARTBEAT_INTERVAL, _HEARTBEAT_TIMEOUT
    global _TASK_SLOW_SEC, _TASK_STALL_SEC, _STORM_THRESHOLD
    _d = declaration if isinstance(declaration, dict) else {}
    try:
        _HEARTBEAT_INTERVAL = float(_d.get("heartbeat_interval_s") or _HEARTBEAT_INTERVAL)
        _HEARTBEAT_TIMEOUT = float(_d.get("heartbeat_timeout_s") or _HEARTBEAT_TIMEOUT)
        _TASK_SLOW_SEC = float(_d.get("task_slow_s") or _TASK_SLOW_SEC)
        _TASK_STALL_SEC = float(_d.get("task_stall_s") or _TASK_STALL_SEC)
        _STORM_THRESHOLD = int(_d.get("storm_threshold") or _STORM_THRESHOLD)
    except Exception:
        pass
    with _lock:
        if _instance is None:
            _instance = TLCGuard(declaration)
            _instance.start()
        return _instance


def report_crash(kind: str) -> None:
    """崩溃 hooks → tlc 上报（tlc 未启动则 no-op，零侵入）。"""
    g = _instance
    if g is not None:
        try:
            g.report_crash(kind)
        except Exception:
            pass


def task_start(name: str) -> None:
    """背景长任务开始 → tlc 记账（tlc 未启动则 no-op，零侵入）。

    加载/注入这类后台活自己报到这儿来，tlc 才看得见它们卡住了（心跳正常不代表活没卡）。
    """
    g = _instance
    if g is not None:
        try:
            g.task_start(name)
        except Exception:
            pass


def task_end(name: str) -> None:
    """背景长任务收工（记得放 finally）。"""
    g = _instance
    if g is not None:
        try:
            g.task_end(name)
        except Exception:
            pass

def suppress(reason: str) -> None:
    """受控重启抑制（自愈重启动作前调；未启动 no-op）。"""
    g = _instance
    if g is not None:
        try:
            g.suppress(reason)
        except Exception:
            pass


def heartbeat() -> None:
    """UI 主线程心跳 → tlc（未启动 no-op，零侵入）。"""
    g = _instance
    if g is not None:
        try:
            g.heartbeat()
        except Exception:
            pass


def _channel_decls() -> tuple[dict, list]:
    """**直接问客制化通道本体**（user_prefs 的两把 key），不经 mani。

    为什么要有这一手：mani 是采集管线走完才写出来的，而 TLC 是"保命"的东西，
    不该等管线。消费端自己去要 ⇒ 通道里写了就能立刻生效，不用等下一轮采集。
    返回 `(tlc 声明 dict, 车道声明 list)`；读不到就给空。
    """
    _tlc, _lanes = {}, []
    try:
        import json as _json
        from kernel.scout import getPref
        _raw = getPref("tlc_consumer") or ""
        if _raw:
            _d = _json.loads(_raw)
            _tlc = _d if isinstance(_d, dict) else {}
        _raw2 = getPref("reload_consumer") or ""
        if _raw2:
            _d2 = _json.loads(_raw2)
            _lanes = _d2 if isinstance(_d2, list) else ([_d2] if isinstance(_d2, dict) else [])
    except Exception:
        pass
    return _tlc, _lanes


def report_signal(kind: str, detail: str = "") -> bool:
    # 消费端上报一条「监护信号」——声明驱动：watch 里没写这个 kind ⇒ 不记账。
    # 通用能力：采什么由声明决定（tlc_consumer.watch），代码不写死任何观测项。
    try:
        _tlc, _ = _channel_decls()
        _watch = [str(x) for x in ((_tlc or {}).get("watch") or [])]
        if kind not in _watch:
            return False
        from main.main import stk as _stk_sig
        _stk_sig("tlc.signal", "%s %s" % (kind, detail), ok=(kind != "render_term"))
        return True
    except Exception:
        return False


def ensure_tlc_from_manifest() -> bool:
    """读 mani.json entries.tlc 声明 → 有则启动 tlc（声明驱动：mani 有才活）。

    返回是否已启动（True=守护在跑；False=mani 无 tlc 声明或读取失败）。
    """
    # ① 先直接问客制化通道本体（通道里写了就立刻生效，不等采集管线/mani）
    try:
        _ch_tlc, _ = _channel_decls()
        if _ch_tlc:
            ensure_tlc(_ch_tlc)
            return True
    except Exception:
        pass
    try:
        import json
        import os
        mani_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "kernel", "large", "mani.json")
        if not os.path.isfile(mani_path):
            return False
        with open(mani_path, "r", encoding="utf-8") as f:
            mani = json.load(f)
        # 客制化通道也算声明来源：custom_demands 写一条 type: "tlc_guard" 就用它，
        # 没写才退回壳注入的 entries.tlc。
        decl = None
        for item in ((mani.get("entries") or {}).get("custom_demands") or []):
            if isinstance(item, dict) and str(item.get("type") or "") == "tlc_guard":
                decl = item
                break
        if decl is None:
            tlc_list = ((mani.get("entries") or {}).get("tlc")) or []
            for item in tlc_list:
                if isinstance(item, dict) and item.get("id") == "tlc":
                    decl = item
                    break
        if decl is None:
            return False
        ensure_tlc(decl)
        return True
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════
#  重载车道
#  一次重载 = 一个车道对象；同一车道的多次请求合并成一次，不同车道互不牵连。
#  车道由 mani 的 entries.reload 声明；重载函数由 UI 侧注册（register_reload_executor）。
#  这样"多个 singleShot 打同一个整页重载"就变成"各自车道、各自去重、统一调度"。
# ══════════════════════════════════════════════════════════════
_RELOAD_DECL: dict = {}
_reload_lanes: dict = {}
_reload_lock = threading.Lock()


class ReloadLane:
    """一个重载车道：请求合并 + 不重入。"""

    def __init__(self, lane_id: str, coalesce: bool = True) -> None:
        self.id = lane_id
        self.coalesce = coalesce
        self._exec = None
        self._busy = False
        self._dirty = False
        self._requests = 0
        self._runs = 0
        self._last_reason = ""

    def attach(self, executor) -> None:
        self._exec = executor

    def request(self, reason: str = "") -> bool:
        self._requests += 1
        self._last_reason = reason or ""
        self._beat()
        if self._busy:
            self._dirty = True
            return False          # 合并掉了：这一下不重载（这就是消闪的关键）
        self._run(reason)
        return True

    def _run(self, reason: str) -> None:
        if self._exec is None:
            return
        self._busy = True
        try:
            self._exec(reason)
            self._runs += 1
        finally:
            self._busy = False
            if self._dirty and self.coalesce:
                self._dirty = False
                self._run("补一次（合并期间来过请求）")

    def _beat(self) -> None:
        try:
            from main.main import stk
            stk("tlc.reload.request",
                "车道 %s：请求 %d 次 / 实跑 %d 次（合并掉 %d 次）原因=%s"
                % (self.id, self._requests, self._runs,
                   max(0, self._requests - self._runs), self._last_reason), ok=True)
        except Exception:
            pass

    def stats(self) -> dict:
        return {"id": self.id, "requests": self._requests, "runs": self._runs,
                "coalesced": max(0, self._requests - self._runs), "busy": self._busy}


def ensure_reload_lanes_from_manifest(mani_path: str = "") -> dict:
    """读声明 → 建/更新重载车道（幂等）。返回声明表。

    声明来源按"消费端自己去要"的顺序：① 客制化通道本体（user_prefs 两把 key，
    通道里写了立刻生效）→ ② mani 里 `custom_demands` 的 reload_lane → ③ 壳自己
    注入的 `entries.reload`。哪一层有就用哪一层，越靠前越优先。
    """
    _decls: list = []
    try:
        _, _ch_lanes = _channel_decls()
        _decls = [d for d in _ch_lanes if isinstance(d, dict)]
    except Exception:
        _decls = []
    try:
        import json as _json
        import os as _os
        _p = mani_path or _os.path.join(
            _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
            "kernel", "large", "mani.json")
        if _os.path.isfile(_p):
            with open(_p, "r", encoding="utf-8") as f:
                mani = _json.load(f)
            _entries = (mani.get("entries") or {})
            if not _decls:
                _decls = [d for d in (_entries.get("custom_demands") or [])
                          if isinstance(d, dict) and str(d.get("type") or "") == "reload_lane"]
            if not _decls:
                _decls = list((_entries.get("reload")) or [])
    except Exception:
        pass
    if not _decls:
        return {}
    try:
        with _reload_lock:
            for item in _decls:
                if not isinstance(item, dict):
                    continue
                _id = str(item.get("id") or "").strip()
                if not _id:
                    continue
                _RELOAD_DECL[_id] = item
                lane = _reload_lanes.get(_id)
                if lane is None:
                    lane = _reload_lanes[_id] = ReloadLane(_id, bool(item.get("coalesce", True)))
                else:
                    lane.coalesce = bool(item.get("coalesce", True))
        return dict(_RELOAD_DECL)
    except Exception:
        return {}


def register_reload_executor(lane_id: str, executor) -> bool:
    """UI 侧把"真正干活的重载函数"挂到车道上（车道没声明也能挂，便于先跑起来）。"""
    if not lane_id:
        return False
    with _reload_lock:
        lane = _reload_lanes.get(lane_id)
        if lane is None:
            lane = _reload_lanes[lane_id] = ReloadLane(lane_id, True)
        lane.attach(executor)
    return True


def request_reload(lane_id: str, reason: str = "") -> bool:
    """请求某个车道重载（忙时同一车道的请求只标脏，跑完补一次）。"""
    with _reload_lock:
        lane = _reload_lanes.get(lane_id)
    if lane is None:
        return False
    try:
        return lane.request(reason)
    except Exception:
        return False


def reload_lane_stats() -> dict:
    with _reload_lock:
        return {k: v.stats() for k, v in _reload_lanes.items()}


def lane_decl(lane_id: str) -> dict:
    """取某条车道的声明（消费端**自己来要**，不靠别人推）。"""
    with _reload_lock:
        return dict(_RELOAD_DECL.get(str(lane_id or "")) or {})


def lane_for_source(source: str, default: str = "shell-lane-page") -> str:
    """**消费端来问**：这个来源该走哪条车道 —— 由声明里的 `sources` 说了算。

    这样"分配"就在声明里：改 `entries.reload` 的 sources 就能把某个触发从一条道
    挪到另一条道，代码一行都不用动。声明里没写这个来源 ⇒ 用 default。
    （车道 id 带 `shell-lane-` 前缀：避免跟官方真源里的名字撞上被判成官方消费端。）
    """
    s = str(source or "").strip()
    if not s:
        return default
    with _reload_lock:
        for lid, decl in _RELOAD_DECL.items():
            try:
                srcs = [str(x) for x in (decl.get("sources") or [])]
            except Exception:
                srcs = []
            if s in srcs:
                return lid
    return default


def request_reload_for(source: str, reason: str = "") -> bool:
    """按**来源**请求重载：先问声明这个来源属于哪条车道，再往那条车道请求。"""
    lane_id = lane_for_source(source)
    return request_reload(lane_id, reason or source)
