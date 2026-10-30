# -*- coding: utf-8 -*-
"""license_anchor 红线哨兵离线测试（不碰实机，不触发真删壳）

正式版语义（2026-09-07 定调）：无 24 天 / 无换机指纹；承担「日语绕行删壳」——
安装基线 + 启动比对。本测试：
- 用临时目录隔离锚点 + destroyed flag，绝不污染 %PROGRAMDATA%
- mock detect_ja_signals（不真调 ctypes/winreg/wsl）
- 只测 check_license 判定逻辑，不触发 self_destruct（不删壳）

运行：python tools/test_license_anchor_offline.py（Windows）
     PYTHONPATH=. python3 tools/test_license_anchor_offline.py（WSL 开发环境）
"""
import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="la_test_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main.license_anchor as la

# 隔离：锚点 / flag / 数据目录全指到临时目录
la._ANCHOR_PATH = os.path.join(TMP, "license_anchor.bin")
la._DESTROYED_FLAG = os.path.join(TMP, "license_destroyed.flag")
la._APP_DATA_DIR = TMP

passed = failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"  [PASS] {name}: {got}")
    else:
        failed += 1
        print(f"  [FAIL] {name}: got={got!r} want={want!r}")


print(f"测试模块: {la.__file__}")
print(f"临时隔离目录: {TMP}\n")

# ── 用例1：首次运行（无锚点）+ 无日语信号 → 放行 + 烙基线锚点 ──
la.detect_ja_signals = lambda: []
ok, reason = la.check_license()
check("首次合规放行", (ok, reason), (True, "first"))
check("基线锚点已写入", os.path.isfile(la._ANCHOR_PATH), True)

# ── 用例2：二次启动，环境一致（无日语）→ ok ──
ok, reason = la.check_license()
check("二次合规放行", (ok, reason), (True, "ok"))

# ── 用例3：绕行回改——基线合规，当前出现日语信号 → 拒绝 ──
la.detect_ja_signals = lambda: ["windows_lang"]
ok, reason = la.check_license()
check("日语回改拒绝", (ok, reason), (False, "japanese"))

# ── 用例4：首次即日语（install 拒载被绕过）→ 拒绝且不烙锚点 ──
if os.path.isfile(la._ANCHOR_PATH):
    os.remove(la._ANCHOR_PATH)
la.detect_ja_signals = lambda: ["tz"]
ok, reason = la.check_license()
check("首次即日语拒绝", (ok, reason), (False, "japanese"))
check("日语环境不烙锚点", os.path.isfile(la._ANCHOR_PATH), False)

# ── 用例5：已自毁（destroyed flag）→ 永久拒绝 ──
with open(la._DESTROYED_FLAG, "w", encoding="utf-8") as f:
    f.write("1")
ok, reason = la.check_license()
check("已自毁拒绝", (ok, reason), (False, "destroyed"))
os.remove(la._DESTROYED_FLAG)

# ── 用例6：采集异常 → 容错放行（不误杀）──
def _boom():
    raise RuntimeError("signal collection failed")

la.detect_ja_signals = _boom
ok, reason = la.check_license()
check("采集异常容错放行", (ok, reason), (True, "ok_noenv"))

# ── 用例7：锚点损坏/不存在 + 合规 → 重新烙基线放行（自愈）──
la.detect_ja_signals = lambda: []
if os.path.isfile(la._ANCHOR_PATH):
    os.remove(la._ANCHOR_PATH)
ok, reason = la.check_license()
check("锚点自愈重烙", (ok, reason), (True, "first"))

print(f"\n结果: {passed} 通过 / {failed} 失败")
sys.exit(0 if failed == 0 else 1)
