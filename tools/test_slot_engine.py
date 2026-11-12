# L1-149：test_slot_engine — 人格插槽引擎 P0 离线验证（P1 回归基线）
r"""test_slot_engine — 人格插槽引擎 P0 离线验证（P1 回归基线）"""













from __future__ import annotations

import os
import sys


_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.dirname(_HERE)
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)


# L29-58：_test_card — 一张中性测试卡带：杂货铺 NPC。所有具体内容都在这份「数据」里，不在引擎里。
def _test_card():
    r"""一张中性测试卡带：杂货铺 NPC。所有具体内容都在这份「数据」里，不在引擎里。"""
    return {
        "name": "npc_shopkeeper",
        "version": "1.0.0",
        "system_prompt": "你是一位杂货铺老板。",
        "states": ["陌生", "熟悉", "亲近"],
        "variables": {
            "affinity": {"min": 0, "max": 100, "init": 0},
            "mood": {"min": 0, "max": 10, "init": 5},
        },
        "transitions": [
            {"from": "陌生", "to": "熟悉", "when": {"counter": "interaction_count", "op": "gte", "value": 3}},
            {"from": "熟悉", "to": "亲近", "when": {"variable": "affinity", "op": "gte", "value": 80}},
            {"from": "*", "to": "陌生", "when": {"flag": "reset_relationship", "op": "eq", "value": True}},
        ],
        "detectors": [
            {"name": "farewell", "type": "keyword", "keywords": ["再见", "拜拜", "下次聊"],
             "action": "direct_emit", "reply_template": "farewell_lines"},
            {"name": "complaint", "type": "keyword", "keywords": ["投诉", "举报"],
             "action": "block", "reply_template": "escalate"},
            {"name": "quest", "type": "keyword", "keywords": ["帮我个忙", "委托"],
             "action": "transition", "target_state": "任务进行中", "threshold": 2},
        ],
        "templates": {
            "farewell_lines": {"mode": "stream", "lines": ["好嘞，那您慢走。", "有需要随时回来。"], "line_delay_ms": 60},
            "escalate": {"mode": "static", "lines": ["好的，我帮您联系负责人处理。"]},
        },
        "memory_path": None,
    }


# L62-144：run_all — 返回 (passed, failed) 计数。断言引擎行为，不校验业务语义。
def run_all():
    r"""返回 (passed, failed) 计数。断言引擎行为，不校验业务语义。"""
    from persona.indivi import SlotEngine, load_card, has_dynamic_layers

    passed = 0
    failed = 0

    def check(name, cond):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"[PASS] {name}")
        else:
            failed += 1
            print(f"[FAIL] {name}")

    card_dict = _test_card()
    card = load_card(card_dict)


    check("load_card 保留 name/version", card.name == "npc_shopkeeper" and card.version == "1.0.0")
    check("load_card 编译 states", card.states == ("陌生", "熟悉", "亲近"))
    check("load_card 编译 variables", set(card.variables) == {"affinity", "mood"})
    check("load_card 编译 transitions", len(card.transitions) == 3)
    check("load_card 编译 detectors", len(card.detectors) == 3)
    check("load_card 编译 templates", set(card.templates) == {"farewell_lines", "escalate"})
    check("has_dynamic_layers=True", has_dynamic_layers(card) is True)


    eng = SlotEngine(card)
    check("初始状态 = states[0]", eng.state == "陌生")
    eng.step("今天天气不错")
    eng.step("来瓶水")
    check("互动 <3 次仍陌生", eng.state == "陌生")

    eng.snapshot["counters"]["interaction_count"] = 3
    eng.step("再来一袋盐")
    check("counter 满 3 → 升熟悉", eng.state == "熟悉")


    eng.snapshot["variables"]["affinity"] = 80
    eng.step("老板最近生意怎么样")
    check("affinity ≥80 → 升亲近", eng.state == "亲近")


    eng.snapshot["flags"]["reset_relationship"] = True
    eng.step("我走了")
    check("reset flag → 回陌生", eng.state == "陌生")


    eng2 = SlotEngine(card)
    r = eng2.step("再见老板")
    check("direct_emit 命中 farewell", r["event"]["action"] == "direct_emit")
    check("direct_emit 返回模板行", r["reply"] is not None and len(r["reply"]["lines"]) == 2)


    eng3 = SlotEngine(card)
    r = eng3.step("我要投诉你")
    check("block 命中 complaint", r["event"]["action"] == "block")
    check("block 返回 escalate 模板", r["reply"] is not None and r["reply"]["lines"][0].startswith("好的"))


    eng4 = SlotEngine(card)
    r1 = eng4.step("帮我个忙行吗")
    check("transition 第1次不触发", eng4.state == "陌生")
    r2 = eng4.step("再委托你一件事")
    check("transition 第2次触发", eng4.state == "任务进行中")


    anchor = eng4.anchor()
    check("锚点含状态", "任务进行中" in anchor)
    check("锚点含变量", "affinity" in anchor)


    legacy = load_card({"name": "plain", "version": "1.0", "system_prompt": "你好"})
    check("旧卡带 has_dynamic_layers=False", has_dynamic_layers(legacy) is False)
    leg_eng = SlotEngine(legacy)
    r = leg_eng.step("随便说点什么")
    check("旧卡带 step 无事件", r["event"] is None and r["reply"] is None)
    check("旧卡带锚点为空", leg_eng.anchor() == "")

    print(f"\n=== {passed} passed, {failed} failed ===")
    return passed, failed


if __name__ == "__main__":
    p, f = run_all()
    sys.exit(1 if f else 0)
