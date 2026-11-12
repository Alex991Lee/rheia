# L1-69：test_pole — 测试桩日志系统。
r"""test_pole — 测试桩日志系统。"""









import json
import os
import datetime


_STAKE_BY_ENV = os.environ.get("ONEKEYCLAW_STAKE", "") == "1"
_STAKE_BY_SENTINEL = os.path.isfile(
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ".stake_enabled",
    )
)
_STAKE_BY_CODE = False

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


# L42-61：emitLog — emitLog
def emitLog(tag, data):
    if not TEST_POLE_ENABLED:
        return
    global _EMITTED_COUNT
    _EMITTED_COUNT += 1
    if _EMITTED_COUNT > _MAX_BURST:
        return
    try:


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


# L65-66：logEvent — logEvent
def logEvent(tag, data=None):
    emitLog(tag or "unknown", data or {})


logEmit = logEvent
