# -*- coding: utf-8 -*-
# L1-184：boot_worker — 内核重活的独立子进程。
r"""boot_worker — 内核重活的独立子进程。"""







from __future__ import annotations

try:
    from lib.no_window import install as _install_no_window
    _install_no_window()
except Exception:
    pass


import argparse
import json
import os
import sys
import traceback
from datetime import datetime


_PROGRAMDATA = os.environ.get("PROGRAMDATA", "")
_STAKE_DIR = os.path.join(_PROGRAMDATA, "Rheia")
_RUNTIME_STAKE_PATH = os.path.join(_STAKE_DIR, "runtime_stake.log")
_STAKE_PATH = os.path.join(_STAKE_DIR, "stake.log")


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


_runtime_dir = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "PythonSide6")
_site_packages = os.path.join(_runtime_dir, "Lib", "site-packages")
if os.path.isdir(_site_packages) and _site_packages not in sys.path:
    sys.path.insert(0, _site_packages)


# L46-54：_stake — 写一条带时间戳的过程账 —— 格式与 main/main.py 的 _stake 一致（多一个来源前缀）。
def _stake(msg: str) -> None:
    r"""写一条带时间戳的过程账 —— 格式与 main/main.py 的 _stake 一致（多一个来源前缀）。"""
    try:
        os.makedirs(_STAKE_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        with open(_RUNTIME_STAKE_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] [boot_worker] {msg}\n")
    except Exception:
        pass


# L58-67：stk — 写一行桩子日志 —— 与 main/main.py 的 stk 同格式（stake.log 是既有账）。
def stk(tag: str, msg: str = "", ok: bool = True) -> None:
    r"""写一行桩子日志 —— 与 main/main.py 的 stk 同格式（stake.log 是既有账）。"""
    try:
        os.makedirs(_STAKE_DIR, exist_ok=True)
        flag = " OK" if ok else "FAIL"
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{tag}] {flag} {msg}\n"
        with open(_STAKE_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


# L71-96：_write_marker — 写落盘标记：读-并-写 + 原子替换（UI 永远读不到半截 json）。
def _write_marker(mark_path: str, **fields) -> None:
    r"""写落盘标记：读-并-写 + 原子替换（UI 永远读不到半截 json）。"""




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




# L98-112：_corePrewarmed — 安装器是否已经完成 scout + settle + large 的预热。
_CORE_WARMED_PATH = os.path.join(_STAKE_DIR, "core_warmed.json")


def _corePrewarmed() -> bool:
    r"""安装器是否已经完成 scout + settle + large 的预热。"""
    try:
        with open(_CORE_WARMED_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not data.get("ok"):
            return False
        mani = os.path.join(_PROJECT_ROOT, "kernel", "large", "mani.json")
        return os.path.isfile(mani) and os.path.getsize(mani) > 10
    except Exception:
        return False

# L114-150：_run_pipeline — 重活本体：安装器已预热时跳过远程 scout，只做本地声明与 large。
def _run_pipeline() -> None:
    r"""重活本体：与搬走前 main/main.py 里那几段逐条一致（顺序、异常容忍度都不动）。"""
    _stake("--- kernel init (boot_worker pid=%s) ---" % os.getpid())
    from kernel import scout, large
    from kernel.scout import _MANIFEST_PATH



    if _corePrewarmed():
        _stake("scout.probe skipped — installer prewarm valid")
    else:
        try:
            scout.probe()
            _stake("scout.probe() done")
        except Exception as e:
            _stake("scout.probe() failed (non-fatal): %s" % e)



    try:
        scout.injectCustomDemands()
    except Exception as e:
        _stake("scout.injectCustomDemands() failed (non-fatal): %s" % e)


    try:
        from platform.slotApi import refresh_slot_declarations
        _r = refresh_slot_declarations()
        _stake("slot declarations refreshed: %s" % _r)
    except Exception as e:
        _stake("slot declarations refresh skipped (non-fatal): %s" % e)
    try:
        large.run()
        _stake("large.run() done")
    except Exception as e:
        _stake("large.run() failed (non-fatal): %s" % e)


# L136-151：_run_self_heal — 运行时自愈（每次启动只做一次）：采样部署真值、覆盖 mani 偏差。
def _run_self_heal(mark_path: str) -> None:
    r"""运行时自愈（每次启动只做一次）：采样部署真值、覆盖 mani 偏差。"""




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


# L155-180：main — main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Rheia 内核重活子进程（无窗口）")
    ap.add_argument("--mark", required=True, help="结论落盘标记的路径（与 UI 的唯一通道）")
    args = ap.parse_args(argv)


    _write_marker(args.mark, pid=os.getpid(), stage="running",
                  pipeline_done=False, self_heal=None)
    _stake("boot_worker start pid=%s mark=%s" % (os.getpid(), args.mark))

    try:
        _run_pipeline()

        _write_marker(args.mark, pipeline_done=True)
        _stake("pipeline done — marker written (UI 侧据此起 tlc/车道)")
        _run_self_heal(args.mark)
    except Exception:

        _stake("boot_worker crashed:")
        _stake(traceback.format_exc())
        _write_marker(args.mark, stage="crashed")
        return 1

    _write_marker(args.mark, stage="exited")
    _stake("boot_worker done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
