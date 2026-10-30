# -*- coding: utf-8 -*-
"""boot_worker — 内核重活的独立子进程。

任务：scout.probe → injectCustomDemands → refresh_slot_declarations → large.run → 自愈采样·纠正。
启动：main/main.py 用 sys.executable + CREATE_NO_WINDOW。
通道：结论写 --mark 指向的 json（先写 .tmp 再 os.replace）；过程账写 runtime_stake.log / stake.log。
约束：顺序固定、逐条 try/except；本进程崩了不影响壳；不 import main.main（它模块级 import PySide6）。
"""

from __future__ import annotations

try:  # 全局静默子进程守卫（设计约定：加载/安装链不得弹控制台）
    from lib.no_window import install as _install_no_window
    _install_no_window()
except Exception:  # noqa: BLE001
    pass


import argparse
import json
import os
import sys
import traceback
from datetime import datetime

# ── 与 main/main.py 同一套路径口径 ──
_PROGRAMDATA = os.environ.get("PROGRAMDATA", "")
_STAKE_DIR = os.path.join(_PROGRAMDATA, "Rheia")
_RUNTIME_STAKE_PATH = os.path.join(_STAKE_DIR, "runtime_stake.log")
_STAKE_PATH = os.path.join(_STAKE_DIR, "stake.log")

# 项目根：本文件在 <root>/main/ 下 ⇒ 上一级即根（kernel / persona / main 都从这儿进）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# python site-packages 优先（第三方依赖在这儿；Qt 不需要，本进程不碰 UI）
_runtime_dir = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "PythonSide6")
_site_packages = os.path.join(_runtime_dir, "Lib", "site-packages")
if os.path.isdir(_site_packages) and _site_packages not in sys.path:
    sys.path.insert(0, _site_packages)


def _stake(msg: str) -> None:
    """写一条带时间戳的过程账 —— 格式与 main/main.py 的 _stake 一致（多一个来源前缀）。"""
    try:
        os.makedirs(_STAKE_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        with open(_RUNTIME_STAKE_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] [boot_worker] {msg}\n")
    except Exception:
        pass


def stk(tag: str, msg: str = "", ok: bool = True) -> None:
    """写一行桩子日志 —— 与 main/main.py 的 stk 同格式（stake.log 是既有账）。"""
    try:
        os.makedirs(_STAKE_DIR, exist_ok=True)
        flag = " OK" if ok else "FAIL"
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{tag}] {flag} {msg}\n"
        with open(_STAKE_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _write_marker(mark_path: str, **fields) -> None:
    """写落盘标记：读-并-写 + 原子替换（UI 永远读不到半截 json）。

    本进程是唯一写手（UI 只读）⇒ 读改写无并发问题；靠 os.replace 的原子性，
    UI 读到的要么是旧结论、要么是新结论。
    """
    try:
        _dir = os.path.dirname(mark_path)
        if _dir:
            os.makedirs(_dir, exist_ok=True)
        data: dict = {}
        if os.path.isfile(mark_path):
            try:
                with open(mark_path, "r", encoding="utf-8") as f:
                    _old = json.load(f)
                if isinstance(_old, dict):
                    data = _old
            except Exception:
                data = {}
        data.update(fields)
        tmp = mark_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, mark_path)
    except Exception as e:
        _stake("marker write failed: %s" % e)


def _run_pipeline() -> None:
    """重活本体：与搬走前 main/main.py 里那几段**逐条一致**（顺序、异常容忍度都不动）。"""
    _stake("--- kernel init (boot_worker pid=%s) ---" % os.getpid())
    from kernel import scout, large
    from kernel.scout import _MANIFEST_PATH           # noqa: F401  （搬过来时照旧带着）
    # 数据动力守卫：每次启动都调 probe，靠 probe 内部「本机版本 vs 官方
    # latest」核验决定要不要拉文档（官方没迭代则短路秒过，不重爬）。
    # 之前「mani 存在就 skip」会让 scout 永久停摆、走向热寂，已废弃。
    try:
        scout.probe()
        _stake("scout.probe() done")
    except Exception as e:
        _stake("scout.probe() failed (non-fatal): %s" % e)
    # 需求声明每次启动都过一遍：probe 在官方没迭代时短路返回（不产 ot），
    # 但客户可能刚在自定义区提了需求/换了卡 —— 声明要落地，且必须在 large.run()
    # 之前（large 就是读这份声明实例化需求卡的）。幂等：没变不写盘。
    try:
        scout.injectCustomDemands()
    except Exception as e:
        _stake("scout.injectCustomDemands() failed (non-fatal): %s" % e)
    # ── 声明链的**单一入口**：需求 → mani（反推声明 + 运行规格）→ 递规格给插件
    #    →（规格真变了才）重启网关让插件重挂。启动时跑一次，自定义区一改也跑一次。
    try:
        from persona.indivi import refresh_slot_declarations
        _r = refresh_slot_declarations()
        _stake("slot declarations refreshed: %s" % _r)
    except Exception as e:
        _stake("slot declarations refresh skipped (non-fatal): %s" % e)
    try:
        large.run()
        _stake("large.run() done")
    except Exception as e:
        _stake("large.run() failed (non-fatal): %s" % e)


def _run_self_heal(mark_path: str) -> None:
    """运行时自愈（每次启动只做一次）：采样部署真值、覆盖 mani 偏差。

    healed=True → 结论写进标记，UI 弹文案并重启壳；
    异常 → 判 healed=False 并记账（旧实现的语义是「自愈异常也放行预装，防永久挂起」）。
    """
    try:
        from kernel.scout import _self_heal
        healed = _self_heal()
        _stake("runtime self_heal: healed=%s" % healed)
        _write_marker(mark_path,
                      self_heal={"done": True, "healed": bool(healed), "error": None})
    except Exception as e:
        _stake("runtime self_heal failed (non-fatal): %s" % e)
        _write_marker(mark_path,
                      self_heal={"done": True, "healed": False, "error": str(e)})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Rheia 内核重活子进程（无窗口）")
    ap.add_argument("--mark", required=True, help="结论落盘标记的路径（与 UI 的唯一通道）")
    args = ap.parse_args(argv)

    # 第一笔就落标记：UI 据此知道「子进程确实起来了」（起不来/导入就死 ⇒ 标记里连 pid 都没有）
    _write_marker(args.mark, pid=os.getpid(), stage="running",
                  pipeline_done=False, self_heal=None)
    _stake("boot_worker start pid=%s mark=%s" % (os.getpid(), args.mark))

    try:
        _run_pipeline()
        # 管线跑完就把 mani 落定这件事告诉 UI（UI 才据此起 tlc 守护与重载车道）
        _write_marker(args.mark, pipeline_done=True)
        _stake("pipeline done — marker written (UI 侧据此起 tlc/车道)")
        _run_self_heal(args.mark)
    except Exception:
        # 不许把崩当哑巴：账上留全，然后如实退出（壳照常可用）
        _stake("boot_worker crashed:")
        _stake(traceback.format_exc())
        _write_marker(args.mark, stage="crashed")
        return 1

    _write_marker(args.mark, stage="exited")
    _stake("boot_worker done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
