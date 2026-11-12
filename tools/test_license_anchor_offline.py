# -*- coding: utf-8 -*-
# L1-92：license_anchor 红线哨兵离线测试（不碰实机，不触发真删壳）
r"""license_anchor 红线哨兵离线测试（不碰实机，不触发真删壳）"""










import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="la_test_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main.license_anchor as la


la._ANCHOR_PATH = os.path.join(TMP, "license_anchor.bin")
la._DESTROYED_FLAG = os.path.join(TMP, "license_destroyed.flag")
la._APP_DATA_DIR = TMP

passed = failed = 0


# L32-39：check — check
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


la.detect_ja_signals = lambda: []
ok, reason = la.check_license()
check("首次合规放行", (ok, reason), (True, "first"))
check("基线锚点已写入", os.path.isfile(la._ANCHOR_PATH), True)


ok, reason = la.check_license()
check("二次合规放行", (ok, reason), (True, "ok"))


la.detect_ja_signals = lambda: ["windows_lang"]
ok, reason = la.check_license()
check("日语回改拒绝", (ok, reason), (False, "japanese"))


if os.path.isfile(la._ANCHOR_PATH):
    os.remove(la._ANCHOR_PATH)
la.detect_ja_signals = lambda: ["tz"]
ok, reason = la.check_license()
check("首次即日语拒绝", (ok, reason), (False, "japanese"))
check("日语环境不烙锚点", os.path.isfile(la._ANCHOR_PATH), False)


with open(la._DESTROYED_FLAG, "w", encoding="utf-8") as f:
    f.write("1")
ok, reason = la.check_license()
check("已自毁拒绝", (ok, reason), (False, "destroyed"))
os.remove(la._DESTROYED_FLAG)


# L77-78：_boom — _boom
def _boom():
    raise RuntimeError("signal collection failed")

la.detect_ja_signals = _boom
ok, reason = la.check_license()
check("采集异常容错放行", (ok, reason), (True, "ok_noenv"))


la.detect_ja_signals = lambda: []
if os.path.isfile(la._ANCHOR_PATH):
    os.remove(la._ANCHOR_PATH)
ok, reason = la.check_license()
check("锚点自愈重烙", (ok, reason), (True, "first"))

print(f"\n结果: {passed} 通过 / {failed} 失败")
sys.exit(0 if failed == 0 else 1)
