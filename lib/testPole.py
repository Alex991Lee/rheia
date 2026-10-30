"""test_pole — 测试桩日志系统。

激活方式（旁通路径，任选其一）：
  1. 环境变量：ONEKEYCLAW_STAKE=1     （推荐，独立于业务代码）
  2. 哨兵文件：项目根/.stake_enabled     （适合 CI/自动化）
  3. 代码开关：test_pole.TEST_POLE_ENABLED = True  （适合开发调试）

优先级：3 > 2 > 1。任一激活即生效。
"""

import json
import os
import datetime

# ── 激活检测（旁通路径：环境变量 / 哨兵文件）──
_STAKE_BY_ENV = os.environ.get("ONEKEYCLAW_STAKE", "") == "1"
_STAKE_BY_SENTINEL = os.path.isfile(
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ".stake_enabled",
    )
)
_STAKE_BY_CODE = False  # 业务代码可手动设 True

TEST_POLE_ENABLED = _STAKE_BY_CODE or _STAKE_BY_SENTINEL or _STAKE_BY_ENV

if TEST_POLE_ENABLED:
    print(f"[stake] activated: env={_STAKE_BY_ENV} sentinel={_STAKE_BY_SENTINEL} code={_STAKE_BY_CODE}")

LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs",
    "test_pole",
)

_EMITTED_COUNT = 0
_MAX_BURST = 5000


def emitLog(tag, data):
    if not TEST_POLE_ENABLED:
        return
    global _EMITTED_COUNT
    _EMITTED_COUNT += 1
    if _EMITTED_COUNT > _MAX_BURST:
        return
    try:
        # 真正要写日志时才创建目录。模块加载阶段不碰文件系统，
        # 否则测试桩未启用时 import 也会无条件 makedirs，非 admin 场景直接崩。
        os.makedirs(LOG_DIR, exist_ok=True)
        entry = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "tag": tag,
            "data": data,
        }
        with open(os.path.join(LOG_DIR, f"pole_{datetime.date.today().isoformat()}.log"), "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def logEvent(tag, data=None):
    emitLog(tag or "unknown", data or {})


logEmit = logEvent
