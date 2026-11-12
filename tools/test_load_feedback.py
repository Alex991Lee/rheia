#!/usr/bin/env python3
# L1-85：test_load_feedback — 点「加载」必须看得见在走，且不许静默吞掉第二次点击。

r"""test_load_feedback — 点「加载」必须看得见在走，且不许静默吞掉第二次点击。"""
















from __future__ import annotations

import os
import sys



for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_PAGE = os.path.join(_ROOT, "ui", "web", "persona_page.py")
_TLC = os.path.join(_ROOT, "main", "tlc.py")

passed = failed = 0


# L43-50：check — check
def check(name: str, ok: bool) -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"[PASS] {name}")
    else:
        failed += 1
        print(f"[FAIL] {name}")


# L54-80：main — main
def main() -> int:
    page = open(_PAGE, encoding="utf-8").read()
    tlc = open(_TLC, encoding="utf-8").read()

    check("① 卡片行有加载状态控件（_load_hint）",
          "self._load_hint = QLabel(\"\")" in page and "_set_load_hint" in page)
    check("② 加载期间按钮禁用（防连点）",
          "btn.setEnabled(not running)" in page)
    check("③ 还在跑时再点 ⇒ 明确告知，不静默吞",
          "别重复点击" in page and "persona.load.busy" in page)
    check("④ 记账口径不再是『像正常跳过』的 load.skip",
          "persona.load.skip" not in page)
    check("⑤ 三段进度都有更新点",
          "① 备份宿主原有文件" in page and "② 注入卡带" in page and "③ 新建会话" in page)
    check("⑥ tlc 有背景长任务入口（task_start / task_end）",
          "def task_start" in tlc and "def task_end" in tlc)
    check("⑥ tlc 有慢 / 僵死两档阈值",
          "_TASK_SLOW_SEC" in tlc and "_TASK_STALL_SEC" in tlc)
    check("⑥ tlc 主循环里真的检查它们（心跳正常也看得见）",
          "self._check_tasks()" in tlc)
    check("⑥ 僵死走既有 hang 干预通道（找出谁挡着）",
          'self._schedule("hang", {"secs": secs, "task": name})' in tlc)
    check("⑥ 加载这条路把活报给了 tlc（含 finally 收工）",
          'task_start' in page and '"persona.load:%s"' in page and "_t_end(_task)" in page)

    print(f"\n=== {passed} passed, {failed} failed ===")
    return passed, failed


if __name__ == "__main__":
    p, f = main()
    sys.exit(1 if f else 0)
