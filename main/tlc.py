# -*- coding: utf-8 -*-
# L1-640：tlc — 壳体守护消费端（由 mani.json 声明驱动启动，非硬编常驻）
r"""tlc — 壳体守护消费端（由 mani.json 声明驱动启动，非硬编常驻）"""

























from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)


_STORM_WINDOW_SEC = 30.0
_STORM_THRESHOLD = 5


_HEARTBEAT_INTERVAL = 10.0
_HEARTBEAT_TIMEOUT = 35.0




_TASK_SLOW_SEC = 15.0
_TASK_STALL_SEC = 90.0


_instance: "TLCGuard | None" = None
_lock = threading.Lock()


# L57-286：TLCGuard — 壳体守护：监听异常退出码/崩溃预兆，保持壳体不崩溃。
class TLCGuard:
    r"""壳体守护：监听异常退出码/崩溃预兆，保持壳体不崩溃。"""

    def __init__(self, declaration: dict | None = None):
        self._decl = declaration or {}

        _d = self._decl if isinstance(self._decl, dict) else {}
        self._hb_interval = float(_d.get("heartbeat_interval_s") or _HEARTBEAT_INTERVAL)
        self._hb_timeout = float(_d.get("heartbeat_timeout_s") or _HEARTBEAT_TIMEOUT)
        self._task_slow = float(_d.get("task_slow_s") or _TASK_SLOW_SEC)
        self._task_stall = float(_d.get("task_stall_s") or _TASK_STALL_SEC)
        self._storm_threshold = int(_d.get("storm_threshold") or _STORM_THRESHOLD)

        self._exempt = frozenset(str(x) for x in (_d.get("exempt") or []))

        self._defer_on_conversation = bool(_d.get("defer_restart_on_conversation", True))
        self._conversation_active = False
        self._defer_since: float | None = None
        self._events: list[tuple[float, str]] = []
        self._storm_flagged = False
        self._last_beat = time.time()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._suppressed: tuple[float, str] | None = None
        self._hang_reported = False

        self._tasks: dict = {}
        self._task_reported: dict = {}
        self._actions: list = []
        self._thread: threading.Thread | None = None



    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="tlc-guard")
        self._thread.start()

    def stop_nowait(self) -> None:
        self._stop.set()
        self._wake.set()



    def suppress(self, reason: str) -> None:
        r"""受控重启抑制：标记壳体即将正常重启（如自愈重启），"""

        self._suppressed = (time.time(), reason)

    def resume(self) -> None:
        r"""解除抑制（一般不需要：重启后新进程是全新 tlc 实例）。"""
        self._suppressed = None

    def heartbeat(self) -> None:
        r"""主线程心跳（UI 每 10s 调一次）——壳体还活着的证据。"""
        self._last_beat = time.time()

    def report_crash(self, kind: str) -> None:
        r"""壳级崩溃上报（事件驱动：记录 + 风暴检测 + 唤醒守护立即处理）。"""
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
        self._wake.set()



    def task_start(self, name: str) -> None:
        r"""登记一个背景长任务（加载/注入/卡侧处理这类）。"""





        if not name:
            return
        self._tasks[str(name)] = time.time()
        self._task_reported.pop(str(name), None)
        self._wake.set()

    def task_end(self, name: str) -> None:
        r"""长任务收工（成功/失败/异常都要调，用 finally 保底）。"""
        if not name:
            return
        self._tasks.pop(str(name), None)
        self._task_reported.pop(str(name), None)

    def _check_tasks(self) -> None:
        r"""超时检查：慢 ⇒ 报一笔；僵死 ⇒ 报 + 走既有 hang 干预通道（找出谁挡着）。"""




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
        r"""异步调度动作：入队（守护线程消费），不阻塞上报方。"""
        self._actions.append((action, payload))

    def _log_storm(self, kind: str, count: int) -> None:
        r"""崩溃风暴预兆：写警示桩（不阻塞、不弹窗、不掺和业务）。"""
        try:
            from main.main import stk
            stk("tlc.storm",
                f"崩溃风暴预兆: {count} 次/{_STORM_WINDOW_SEC:.0f}s（最近 {kind}）",
                ok=False)
        except Exception:
            pass



    def _run(self) -> None:
        r"""守护主循环（事件驱动 + 超时兜底）。"""






        while True:
            try:

                self._wake.wait()
                self._wake.clear()
                if self._stop.is_set():
                    break

                while self._actions:
                    act, payload = self._actions.pop(0)
                    self._dispatch(act, payload)
            except Exception:
                pass

    def _dispatch(self, action: str, payload: object) -> None:
        r"""异步动作分发（守护线程消费）。"""



        from main.main import stk

        if action in ("hang", "slow"):
            self._on_hang(payload if isinstance(payload, dict) else {})
        else:
            stk("tlc.action", f"调度动作: {action} payload={payload!r}", ok=False)

    def _on_hang(self, info: dict) -> None:
        r"""僵死干预（分两级，先探后治）。"""










        secs = info.get("secs", 0)

        procs = self._find_blocking_procs()
        stk("tlc.hang.detail",
            f"僵死 {secs:.0f}s | 已知阻塞源={procs or '无'}",
            ok=False)
        if not procs:

            stk("tlc.hang.noaction",
                "未发现已知阻塞源，仅记录（不贸然重启）", ok=False)
            return



        stk("tlc.hang.waiting",
            f"正在等卡侧处理长任务（{procs}），非死锁，不干预", ok=True)

    def _find_blocking_procs(self) -> str:
        r"""找已知阻塞源进程（WSL 里的插槽 CLI）。返回描述串，无则空。"""



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


# L290-311：ensure_tlc — 按声明启动 tlc 守护（幂等单例）。声明为空也启动（默认配置）。
def ensure_tlc(declaration: dict | None = None) -> TLCGuard | None:
    r"""按声明启动 tlc 守护（幂等单例）。声明为空也启动（默认配置）。"""




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


# L315-322：report_crash — 崩溃 hooks → tlc 上报（tlc 未启动则 no-op，零侵入）。
def report_crash(kind: str) -> None:
    r"""崩溃 hooks → tlc 上报（tlc 未启动则 no-op，零侵入）。"""
    g = _instance
    if g is not None:
        try:
            g.report_crash(kind)
        except Exception:
            pass


# L326-336：task_start — 背景长任务开始 → tlc 记账（tlc 未启动则 no-op，零侵入）。
def task_start(name: str) -> None:
    r"""背景长任务开始 → tlc 记账（tlc 未启动则 no-op，零侵入）。"""



    g = _instance
    if g is not None:
        try:
            g.task_start(name)
        except Exception:
            pass


# L340-347：task_end — 背景长任务收工（记得放 finally）。
def task_end(name: str) -> None:
    r"""背景长任务收工（记得放 finally）。"""
    g = _instance
    if g is not None:
        try:
            g.task_end(name)
        except Exception:
            pass

# L350-357：suppress — 受控重启抑制（自愈重启动作前调；未启动 no-op）。
def suppress(reason: str) -> None:
    r"""受控重启抑制（自愈重启动作前调；未启动 no-op）。"""
    g = _instance
    if g is not None:
        try:
            g.suppress(reason)
        except Exception:
            pass


# L361-368：heartbeat — UI 主线程心跳 → tlc（未启动 no-op，零侵入）。
def heartbeat() -> None:
    r"""UI 主线程心跳 → tlc（未启动 no-op，零侵入）。"""
    g = _instance
    if g is not None:
        try:
            g.heartbeat()
        except Exception:
            pass


# L372-393：_channel_decls — 直接问客制化通道本体（user_prefs 的两把 key），不经 mani。
def _channel_decls() -> tuple[dict, list]:
    r"""直接问客制化通道本体（user_prefs 的两把 key），不经 mani。"""





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


# L397-409：report_signal — report_signal
def report_signal(kind: str, detail: str = "") -> bool:


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


# L413-454：ensure_tlc_from_manifest — 读 mani.json entries.tlc 声明 → 有则启动 tlc（声明驱动：mani 有才活）。
def ensure_tlc_from_manifest() -> bool:
    r"""读 mani.json entries.tlc 声明 → 有则启动 tlc（声明驱动：mani 有才活）。"""




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








_RELOAD_DECL: dict = {}
_reload_lanes: dict = {}
_reload_lock = threading.Lock()


# L469-520：ReloadLane — 一个重载车道：请求合并 + 不重入。
class ReloadLane:
    r"""一个重载车道：请求合并 + 不重入。"""

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
            return False
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


# L524-572：ensure_reload_lanes_from_manifest — 读声明 → 建/更新重载车道（幂等）。返回声明表。
def ensure_reload_lanes_from_manifest(mani_path: str = "") -> dict:
    r"""读声明 → 建/更新重载车道（幂等）。返回声明表。"""





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


# L576-585：register_reload_executor — UI 侧把"真正干活的重载函数"挂到车道上（车道没声明也能挂，便于先跑起来）。
def register_reload_executor(lane_id: str, executor) -> bool:
    r"""UI 侧把"真正干活的重载函数"挂到车道上（车道没声明也能挂，便于先跑起来）。"""
    if not lane_id:
        return False
    with _reload_lock:
        lane = _reload_lanes.get(lane_id)
        if lane is None:
            lane = _reload_lanes[lane_id] = ReloadLane(lane_id, True)
        lane.attach(executor)
    return True


# L589-598：request_reload — 请求某个车道重载（忙时同一车道的请求只标脏，跑完补一次）。
def request_reload(lane_id: str, reason: str = "") -> bool:
    r"""请求某个车道重载（忙时同一车道的请求只标脏，跑完补一次）。"""
    with _reload_lock:
        lane = _reload_lanes.get(lane_id)
    if lane is None:
        return False
    try:
        return lane.request(reason)
    except Exception:
        return False


# L602-604：reload_lane_stats — reload_lane_stats
def reload_lane_stats() -> dict:
    with _reload_lock:
        return {k: v.stats() for k, v in _reload_lanes.items()}


# L608-611：lane_decl — 取某条车道的声明（消费端自己来要，不靠别人推）。
def lane_decl(lane_id: str) -> dict:
    r"""取某条车道的声明（消费端自己来要，不靠别人推）。"""
    with _reload_lock:
        return dict(_RELOAD_DECL.get(str(lane_id or "")) or {})


# L615-633：lane_for_source — 消费端来问：这个来源该走哪条车道 —— 由声明里的 sources 说了算。
def lane_for_source(source: str, default: str = "shell-lane-page") -> str:
    r"""消费端来问：这个来源该走哪条车道 —— 由声明里的 sources 说了算。"""





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


# L637-640：request_reload_for — 按来源请求重载：先问声明这个来源属于哪条车道，再往那条车道请求。
def request_reload_for(source: str, reason: str = "") -> bool:
    r"""按来源请求重载：先问声明这个来源属于哪条车道，再往那条车道请求。"""
    lane_id = lane_for_source(source)
    return request_reload(lane_id, reason or source)
