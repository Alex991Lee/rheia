# L1-778：test_demand_pipeline — 客制区那条链：需求 → 采集 → 满足结论 → 声明。
r"""test_demand_pipeline — 客制区那条链：需求 → 采集 → 满足结论 → 声明。"""



















from __future__ import annotations

import copy
import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.dirname(_HERE)
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_passed = 0
_failed = 0


# L46-54：check — check
def check(desc: str, ok: bool, detail: str = "") -> bool:
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"  [OK] {desc}" + (f" — {detail}" if detail else ""))
    else:
        _failed += 1
        print(f"  [XX] {desc}" + (f" — {detail}" if detail else ""))
    return ok


# L58-60：_src — _src
def _src(rel: str) -> str:
    with open(os.path.join(_PROJ, rel), "r", encoding="utf-8") as f:
        return f.read()



# L65-86：_entries — _entries
def _entries() -> dict:
    return {
        "providers": [
            {"id": "deepseek", "canonical_id": "deepseek", "section_name": "DeepSeek",
             "source_url": "https://docs.openclaw.ai/providers/deepseek"},
            {"id": "openrouter", "canonical_id": "openrouter", "section_name": "OpenRouter",
             "source_url": "https://docs.openclaw.ai/providers/openrouter"},
        ],
        "channels": [
            {"id": "discord", "section_name": "Discord",
             "source_url": "https://docs.openclaw.ai/channels/discord"},
        ],
        "plugins": [
            {"id": "discord", "providers": ["discord"], "package": "@openclaw/discord",
             "install_route": "npm", "capabilities": ["chatProviders"],
             "source_url": "https://docs.openclaw.ai/plugins/discord"},
        ],
        "gateways": [], "memories": [], "automations": [],
        "web_searches": [{"id": "tavily", "section_name": "Tavily",
                          "source_url": "https://docs.openclaw.ai/tools/tavily"}],
        "tools": [], "clis": [],
    }


# L90-95：_demands — _demands
def _demands() -> list[dict]:
    return [
        {"id": "d1", "name": "deepseek", "description": "客户要接 DeepSeek 的模型"},
        {"id": "d2", "name": "接个聊天频道", "description": "客户想接 Discord 进客服"},
        {"id": "d3", "name": "接飞书", "description": "客户要飞书群机器人"},
    ]


# L99-111：_patch_lexicon_to_tmp — 把正式词典与候选池指到临时文件：测试不许改仓库里那份 forge.json。
def _patch_lexicon_to_tmp():
    r"""把正式词典与候选池指到临时文件：测试不许改仓库里那份 forge.json。"""
    from kernel import filter as fl
    tmp = tempfile.mkdtemp(prefix="demandpipe-")
    forge = os.path.join(tmp, "forge.json")
    cands = os.path.join(tmp, "ambiguity_candidates.json")
    with open(forge, "w", encoding="utf-8") as f:
        json.dump({"ambiguity_dict": {"demand_entities": {}}}, f, ensure_ascii=False)
    fl._FORGE_PATH = forge
    fl._CANDIDATES_PATH = cands

    fl._MECH_RAW_PATH = os.path.join(tmp, "mechanisms_raw.json")
    return fl, forge



# L116-170：test_demands_are_linked_to_official_entities — test_demands_are_linked_to_official_entities
def test_demands_are_linked_to_official_entities():
    print("\n[①] 需求进采集：对着本次采到的官方实体做实体链接")
    fl, _ = _patch_lexicon_to_tmp()
    entries = _entries()
    out = fl.satisfy_demands(entries, _demands())
    by_id = {r["id"]: r for r in out}

    a = by_id["d1"]["satisfied"]
    check("需求名精确命中 provider", bool(a) and a["entity_id"] == "deepseek",
          f"{a}")
    check("命中有官方出处（谁说的能查）",
          bool(a) and a["source_url"].endswith("/providers/deepseek"),
          (a or {}).get("source_url", ""))
    check("命中带 canonical_id（与消歧同一套真源词元）",
          bool(a) and a["canonical_id"] == "deepseek")

    b = by_id["d2"]["satisfied"]
    check("澄清全文里出现实体词 → 正文命中", bool(b) and b["entity_id"] == "discord",
          f"{b}")
    check("命中方式标清楚（lexicon/name_exact/text）",
          bool(b) and b["match"] == "text", (b or {}).get("match", ""))

    c = by_id["d3"]
    check("官方没有的实体 → 未命中", c["satisfied"] is None, f"{c.get('satisfied')!r}")
    check("未命中必须显式给原因（不静默留空）",
          isinstance(c.get("reason"), str) and len(c["reason"]) > 4, c.get("reason", ""))
    check("结论写进 entries.custom_demands（随 ot.json 流动）",
          len(entries.get("custom_demands") or []) == 3)


    e2 = _entries()
    out2 = fl.satisfy_demands(e2, [
        {"id": "persona-slot-engine", "type": "consumer",
         "spec": {"plugin": "persona-slot-engine", "entry": "./dist/index.js"},
         "installer": {"inject_script": "installer/inject_local_plugins.py"},
         "capabilities": ["critical-tool"]}])
    cons = out2[0]["satisfied"]
    check("消费端也拿到结论（不再只是登记）", bool(cons), f"{cons}")
    check("我们自己写的插件：真源里没有 → 显式判成**本地插件**（不是「未命中」）",
          cons.get("match") == "external" and cons.get("entity_type") == "local"
          and cons.get("present") is False, f"match={cons.get('match')} type={cons.get('entity_type')}")
    check("本地插件的装法写进结论（照声明里的 installer 走）",
          cons.get("install_route") == "installer/inject_local_plugins.py",
          str(cons.get("install_route")))


    e3 = _entries()
    out3 = fl.satisfy_demands(e3, [
        {"id": "want-discord", "type": "consumer", "spec": {"plugin": "discord"}}])
    off = out3[0]["satisfied"]
    check("官方插件 → 结论给官方路由与包名",
          off.get("entity_type") == "plugin" and off.get("install_route") == "npm"
          and off.get("package") == "@openclaw/discord", f"{off}")
    check("官方插件 → 带官方出处（谁说的能查）",
          str(off.get("source_url", "")).endswith("/plugins/discord"), str(off.get("source_url")))



# L175-184：test_satisfy_is_deterministic — test_satisfy_is_deterministic
def test_satisfy_is_deterministic():
    print("\n[②] 确定性：同一份输入两次结论必须字节级一致（铁律三）")
    fl, _ = _patch_lexicon_to_tmp()
    e1, e2 = _entries(), _entries()
    r1 = fl.satisfy_demands(e1, _demands())
    r2 = fl.satisfy_demands(e2, _demands())
    check("两次结论一致", json.dumps(r1, ensure_ascii=False, sort_keys=True)
          == json.dumps(r2, ensure_ascii=False, sort_keys=True))
    check("序列化也一致（ot→mani diff 有意义的前提）",
          fl._serialize(e1) == fl._serialize(e2))



# L189-226：test_lexicon_sediments_exact_only_and_survives_drift — test_lexicon_sediments_exact_only_and_survives_drift
def test_lexicon_sediments_exact_only_and_survives_drift():
    print("\n[③] 沉淀字典：精确命中固化，模糊命中不固化")
    fl, forge = _patch_lexicon_to_tmp()
    fl.satisfy_demands(_entries(), _demands())
    promoted = fl.promote_candidates()
    with open(forge, "r", encoding="utf-8") as f:
        known = json.load(f)["ambiguity_dict"].get("demand_entities") or {}

    check("精确命中（d1）已固化进正式词典", known.get("d1", {}).get("entity_id") == "deepseek",
          f"{known.get('d1')}")
    check("固化时连出处一起记（结论要能自证）",
          str(known.get("d1", {}).get("source_url", "")).endswith("/providers/deepseek"))
    check("正文命中（d2）**不**固化 —— 那是猜，固化了会污染词典",
          "d2" not in known, f"keys={sorted(known.keys())}")
    check("转正清单里有它（可用于日志/版本指纹）",
          any(p.startswith("demand:d1:") for p in promoted), f"{promoted}")


    starved = _entries()
    starved["providers"] = [p for p in starved["providers"] if p["id"] != "deepseek"]
    out = fl.satisfy_demands(starved, _demands())
    a = {r["id"]: r for r in out}["d1"]["satisfied"]
    check("实体本次没采到，字典结论仍在（不随正文漂移）",
          bool(a) and a["entity_id"] == "deepseek" and a["match"] == "lexicon", f"{a}")
    check("但显式标 present=False（看得见这条不是本次采到的）",
          bool(a) and a.get("present") is False, f"present={(a or {}).get('present')}")


    changed = _demands()
    changed[0]["description"] = "客户改主意了，要接 OpenRouter"
    out2 = fl.satisfy_demands(starved, changed)
    d1 = {r["id"]: r for r in out2}["d1"]
    check("需求文本一变，旧结论作废（d1 不再指向 deepseek）",
          not (d1.get("satisfied") and d1["satisfied"]["entity_id"] == "deepseek"),
          f"{d1.get('satisfied')}")
    check("作废后改指新实体（OpenRouter 在采集里）",
          bool(d1.get("satisfied")) and d1["satisfied"]["entity_id"] == "openrouter",
          f"{d1.get('satisfied')}")



# L231-303：test_scout_merges_satisfaction_into_manifest — test_scout_merges_satisfaction_into_manifest
def test_scout_merges_satisfaction_into_manifest():
    print("\n[④] 声明合并：injectCustomDemands 把结论并回 mani 的需求条目")
    from kernel import scout
    from kernel.filter import demand_fp

    tmp = tempfile.mkdtemp(prefix="demandmani-")
    mani = os.path.join(tmp, "mani.json")
    name, desc = "deepseek", "客户要接 DeepSeek 的模型"
    fp = demand_fp(name, desc)
    with open(mani, "w", encoding="utf-8") as f:
        json.dump({"entries": {"providers": [], "custom_demands": [
            {"id": "d1", "name": name, "description": desc, "fp": fp,
             "satisfied": {"entity_type": "provider", "entity_id": "deepseek",
                           "canonical_id": "deepseek", "source_url": "https://x/providers/deepseek",
                           "match": "name_exact", "present": True}},
        ]}}, f, ensure_ascii=False)

    _old_mani, _old_read = scout._MANIFEST_PATH, scout._readCustomDemands
    scout._MANIFEST_PATH = type(scout._MANIFEST_PATH)(mani)
    try:
        scout._readCustomDemands = lambda: [{"id": "d1", "name": name, "description": desc}]
        check("合并成功", scout.injectCustomDemands() is True)
        with open(mani, "r", encoding="utf-8") as f:
            got = json.load(f)["entries"]["custom_demands"]
        check("采集结论被保住（没被壳层覆写成空）",
              (got[0].get("satisfied") or {}).get("entity_id") == "deepseek", f"{got[0]}")


        before = os.path.getmtime(mani)
        import time as _t
        _t.sleep(0.01)
        scout.injectCustomDemands()
        check("幂等：内容一致不重写（不刷 mtime、不触发上游 diff）",
              os.path.getmtime(mani) == before)


        scout._readCustomDemands = lambda: [{"id": "d1", "name": name,
                                             "description": "改成接 OpenRouter 了"}]
        scout.injectCustomDemands()
        with open(mani, "r", encoding="utf-8") as f:
            got = json.load(f)["entries"]["custom_demands"]
        check("需求变了 → 结论作废并给出原因（不静默、不贴旧结论）",
              got[0]["satisfied"] is None and len(str(got[0].get("reason") or "")) > 4,
              f"{got[0]}")


        scout._readCustomDemands = lambda: [
            {"id": "d1", "name": name, "description": desc},
            {"id": "persona-slot-engine", "type": "consumer",
             "spec": {"plugin": "persona-slot-engine"}},
        ]
        scout.injectCustomDemands()
        with open(mani, "r", encoding="utf-8") as f:
            got = json.load(f)["entries"]["custom_demands"]
        cons = [r for r in got if r.get("id") == "persona-slot-engine"][0]
        check("消费端声明本体一字不动（spec 原样留着）",
              cons.get("spec") == {"plugin": "persona-slot-engine"}, f"{cons.get('spec')}")
        check("消费端也走同一套合并（还没采集 → 显式给结论/原因，不静默）",
              ("satisfied" in cons and cons.get("satisfied") is None
               and len(str(cons.get("reason") or "")) > 4)
              or bool(cons.get("declaration")),
              f"{cons.get('reason') or sorted(cons.keys())}")


        scout._readCustomDemands = lambda: [
            {"id": "persona-slot-engine", "type": "consumer", "spec": {"plugin": "persona-slot-engine"}}]
        scout.injectCustomDemands()
        with open(mani, "r", encoding="utf-8") as f:
            got = json.load(f)["entries"]["custom_demands"]
        check("客户删了需求 → 需求声明清空，消费端声明照留（不留幽灵、也不误删）",
              [r.get("id") for r in got] == ["persona-slot-engine"], f"{got}")
    finally:
        scout._MANIFEST_PATH, scout._readCustomDemands = _old_mani, _old_read



# L308-335：test_wired_so_demand_changes_land_immediately — test_wired_so_demand_changes_land_immediately
def test_wired_so_demand_changes_land_immediately():
    print("\n[⑤] 通路：客制区一改就过一遍声明，启动时也在 large 之前过一遍")
    cam = _src(os.path.join("ui", "web", "custom_area_manager.py"))
    main = _src(os.path.join("main", "main.py"))
    scout = _src(os.path.join("kernel", "scout.py"))
    filt = _src(os.path.join("kernel", "filter.py"))

    check("客制区写配置后跑**整条链**（客户提需求当场记账 + 递规格，不只看 scout 一眼）",
          "_write_config" in cam and "refresh_slot_declarations" in cam)
    check("启动时也过一遍声明", "scout.injectCustomDemands()" in main)
    check("且必须在 large.run() 之前（large 就是读这份声明实例化需求卡的）",
          main.index("scout.injectCustomDemands()") < main.index('_stake("large.run() done")'))
    check("probe 把需求传进 spider（需求真的进采集，不只是搬运）",
          "demands = _readCustomDemands()" in scout
          and "demands=demands" in scout)
    _mani_code = [ln.strip() for ln in filt.splitlines() if "mani" in ln and "open(" in ln]
    check("filter 里没有一处 open() 碰 mani（唯一写者仍是 scout）", not _mani_code,
          "、".join(_mani_code[:3]))
    check("所有权表自己写着：mani.json 归 kernel.scout",
          "kernel/large/mani.json" in filt and "kernel.scout" in filt)
    check("需求进采集的调用点在消歧之后（命中别名要给母体 id）",
          filt.index("_disambiguate_providers(entries, deployed)")
          < filt.index("satisfy_demands(entries, demands, facts=_facts, delta=_delta)"))
    check("机制事实与机制变化差分在 crawl 里就绪（插件设计要匹配的就是它）",
          "mechanism_facts(" in filt and "mechanism_delta(" in filt
          and 'result["mechanism_delta"]' in filt)
    check("probe 把上一版 mani 传下去做机制差分",
          "prev_manifest=prev_manifest" in scout)



# L340-366：test_satisfaction_reaches_the_consumer_end — test_satisfaction_reaches_the_consumer_end
def test_satisfaction_reaches_the_consumer_end():
    print("\n[⑥] 消费端：结论经 mani → large 扁平化 → 需求卡的声明")
    from kernel import large

    entries = _entries()
    fl, _ = _patch_lexicon_to_tmp()
    out = fl.satisfy_demands(entries, [
        {"id": "custom_900", "name": "deepseek", "description": "客户要接 DeepSeek 的模型"}])
    flat = large._flattenNewFormat({"entries": entries})
    decl = flat.get("custom_900") or {}
    check("需求条目被扁平化成声明（消费端拿得到）", bool(decl),
          f"{sorted(decl.keys())[:6]}")
    check("声明里带着满足结论（谁是官方那一个 + 出处）",
          (decl.get("satisfied") or {}).get("entity_id") == "deepseek"
          and str((decl.get("satisfied") or {}).get("source_url", "")).startswith("http"),
          f"{decl.get('satisfied')}")
    check("三轴打标成 demand（am 才认得出该给哪张卡）",
          decl.get("auth_kind") == "demand" and decl.get("transport") == "demand"
          and decl.get("model_source") == "demand", f"{decl.get('auth_kind')}")
    with open(os.path.join(_PROJ, "kernel", "large", "forge.json"), "r", encoding="utf-8") as f:
        fg = json.load(f)
    check("forge.json 里有 demand 的三轴模板（am 零改动就能长出卡）",
          "demand:demand:demand" in (fg.get("taxonomy_map") or {}),
          "demand:demand:demand")
    enr = large._enrich(flat)
    check("富化后结论仍在（_enrich 不许把它洗掉）",
          (enr.get("custom_900", {}).get("satisfied") or {}).get("entity_id") == "deepseek")




_HOOKS_DOC = """
## Registration and execution

| Hook | Budget | Failure policy |
| --- | --- | --- |
| `before_agent_run`, `before_tool_call` | 15 seconds | Fail closed: block the run, tool call, or install |
| `before_prompt_build`, `message_sending` | 15 seconds | Log and skip the failed handler |

### Message and delivery hooks

| Hook | Kind | Description |
| --- | --- | --- |
| `message_received` | Observe | Observe inbound content, sender, thread |
| `message_sending` | Modify / gate | Rewrite outbound content or cancel delivery |
| `message_sent` | Observe | Observe outbound delivery success or failure |

### Tool call policy hooks

| Hook | Kind | Description |
| --- | --- | --- |
| `before_tool_call` | Modify / gate | Rewrite tool params, block execution, or require approval |

### Prompt and session hooks

| Hook | Kind | Description |
| --- | --- | --- |
| `agent_end` | Observe | Observe final messages, success state, and run duration |

### Session lifecycle hooks

| Hook | Kind | Description |
| --- | --- | --- |
| `session_start` / `session_end` | Observe | Track session lifecycle boundaries |

## Choose a hook

| Task | Hook |
| --- | --- |
| Rewrite outgoing text or cancel a send | `message_sending` |
| Flush state after a turn or at shutdown | `agent_end` / `gateway_stop` |

### Permissions and scope

- Non-bundled plugins need explicit `plugins.entries.<id>.hooks.allowConversationAccess: true` for `before_model_resolve`, `agent_turn_prepare`, `before_prompt_build`, and `before_agent_run`. Bundled plugins are allowed unless this option is explicitly `false`.

The catalog is the registration API, not a promise that every runtime emits every hook.

`session_end.reason` is one of `new`, `reset`, `idle`, `daily`, `compaction`,
`deleted`, `shutdown`, `restart`, or `unknown`.

Shutdown and restart share one 2-second total `session_end` drain budget.
"""


# L426-428：_facts — _facts
def _facts(mechanisms: list) -> dict:
    from kernel import filter as fl
    return fl.mechanism_facts({"mechanisms": mechanisms})


# L432-485：test_mechanism_is_sorted_out_and_diffed — test_mechanism_is_sorted_out_and_diffed
def test_mechanism_is_sorted_out_and_diffed():
    print("\n[⑦] 机制面：官方钩子机制分拣 + 机制变化差分")
    from kernel import filter as fl
    url = "https://docs.openclaw.ai/plugins/hooks/reference"
    entry = fl.spider_mechanism("reference", _HOOKS_DOC, url)
    check("机制条目成条目（不是插件卡）", entry.get("type") == "mechanism", entry.get("type"))
    hooks = entry.get("hooks") or {}
    check("钩子名从表里分拣出来（一张也没硬编）",
          {"message_received", "message_sending", "message_sent",
           "session_start", "session_end"} <= set(hooks), f"{sorted(hooks)}")
    check("钩子性质认出来（观察 vs 改写）",
          hooks.get("message_sending", {}).get("kind") == "modify_gate"
          and hooks.get("message_received", {}).get("kind") == "observe",
          f"{hooks.get('message_sending')}")
    check("每钩子预算分拣出来（超时预算 = 插件设计的硬约束）",
          (entry.get("budgets") or {}).get("message_sending") == "15 seconds",
          f"{entry.get('budgets')}")
    check("session_end 的 reason 值域分拣出来",
          "shutdown" in (entry.get("session_end_reasons") or [])
          and len(entry.get("session_end_reasons") or []) >= 8,
          f"{entry.get('session_end_reasons')}")
    check("收尾排空预算分拣出来", entry.get("session_end_drain") == "2 second total",
          str(entry.get("session_end_drain")))
    check("机制条目带官方出处（谁说的能查）", entry.get("source_url") == url)


    old = json.loads(json.dumps(entry))
    old["hooks"].pop("message_sent")
    old["hooks"]["message_sending"]["kind"] = "observe"
    old["budgets"]["message_sending"] = "30 seconds"
    old["session_end_reasons"] = [r for r in old["session_end_reasons"] if r != "shutdown"]
    facts = _facts([entry])
    delta = fl.mechanism_delta({"mechanisms": [old]}, facts)
    check("机制变化：新增的钩子被差分出来",
          delta["hooks"]["added"] == ["message_sent"], f"{delta['hooks']['added']}")
    check("机制变化：性质变了被差分出来（观察→改写 = 挂法完全不同）",
          delta["hooks"]["changed"] == [{"id": "message_sending", "field": "kind",
                                         "from": "observe", "to": "modify_gate"}],
          f"{delta['hooks']['changed']}")
    check("机制变化：预算变了被差分出来",
          delta["budgets"]["changed"] == [{"id": "message_sending",
                                           "from": "30 seconds", "to": "15 seconds"}],
          f"{delta['budgets']['changed']}")
    check("机制变化：reason 值域多了什么被差分出来",
          delta["session_end_reasons"]["added"] == ["shutdown"],
          f"{delta['session_end_reasons']}")

    same = fl.mechanism_delta({"mechanisms": [entry]}, facts)
    check("没有变化 → 同形状的空结构（产物可比对、幂等）",
          same["hooks"]["added"] == [] and same["hooks"]["removed"] == []
          and same["hooks"]["changed"] == [] and same["budgets"]["changed"] == [])
    check("机制差分是确定性产物",
          json.dumps(fl.mechanism_delta({"mechanisms": [old]}, facts), sort_keys=True)
          == json.dumps(delta, sort_keys=True))



# L490-546：test_plugin_design_is_matched_against_mechanism — test_plugin_design_is_matched_against_mechanism
def test_plugin_design_is_matched_against_mechanism():
    print("\n[⑧] 完整插件声明：我们对插件的设计 × 官方机制（现状 + 变化）")
    from kernel import filter as fl
    url = "https://docs.openclaw.ai/plugins/hooks/reference"
    entry = fl.spider_mechanism("reference", _HOOKS_DOC, url)
    facts = _facts([entry])
    old = json.loads(json.dumps(entry))
    old["hooks"].pop("message_sent")
    old["hooks"]["message_sending"]["kind"] = "observe"
    delta = fl.mechanism_delta({"mechanisms": [old]}, facts)

    design = {
        "id": "persona-slot-engine", "type": "consumer",
        "spec": {"plugin": "persona-slot-engine", "entry": "./dist/index.js"},
        "installer": {"inject_script": "installer/inject_local_plugins.py",
                      "restart_required": True},
        "hooks": [
            {"id": "message_received", "role": "user"},
            {"id": "message_sending", "role": "assistant"},
            {"id": "message_sent", "role": "trigger_capture"},
            {"id": "session_end", "role": "trigger_distill"},
            {"id": "message_ended", "role": "typo"},
        ],
        "lifecycle": "critical", "capabilities": ["critical-tool"],
    }
    decl = fl.satisfy_demands(_entries(), [design], facts=facts, delta=delta)[0]
    m = decl.get("mechanism") or {}
    matched = {x["id"]: x for x in (m.get("hooks_matched") or [])}
    gaps = {x["id"]: x for x in (m.get("hooks_gaps") or [])}

    check("设计里在官方机制表里的钩子全部对上（带性质）",
          set(matched) == {"message_received", "message_sending", "message_sent", "session_end"},
          f"{sorted(matched)}")
    check("对上时带官方出处（每个钩子都能查到谁说的）",
          all(str(x.get("source_url", "")).startswith("http") for x in matched.values()))
    check("对上时带该钩子的预算（挂它就得守住这个预算）",
          matched.get("message_sending", {}).get("budget") == "15 seconds")
    check("官方机制表里**没有**的钩子 = 缺口（显式标出来，不静默）",
          set(gaps) == {"message_ended"}, f"{sorted(gaps)}")
    check("缺口说明为什么危险（挂在文档没写的内部行为上）",
          "一改就断" in str(gaps.get("message_ended", {}).get("why", "")),
          str(gaps.get("message_ended", {}).get("why", "")))
    impact = {(x["id"], x["change"]): x for x in (m.get("delta_impact") or [])}
    check("机制变化落在我们声明的钩子上 → 名单里有它（性质变了）",
          ("message_sending", "kind") in impact, f"{sorted(impact)}")
    check("变化里带上 从什么变成什么（不是一句'变了'）",
          impact.get(("message_sending", "kind"), {}).get("from") == "observe"
          and impact[("message_sending", "kind")].get("to") == "modify_gate")
    check("我们没挂的钩子变化不进我们的声明（不噪音）",
          ("session_start", "added") not in impact and len(impact) == 2,
          f"{sorted(impact)}")
    check("三轴给消费端专属取值（large 才认得出该长成什么）",
          decl.get("auth_kind") == "consumer" and decl.get("transport") == "plugin"
          and decl.get("model_source") == "local", f"{decl.get('auth_kind')}")
    check("身份/装法仍在（本地插件，装法照 installer）",
          (decl.get("satisfied") or {}).get("match") == "external"
          and "inject_local_plugins" in str((decl.get("satisfied") or {}).get("install_route")))



# L551-591：test_large_generates_the_consumer — test_large_generates_the_consumer
def test_large_generates_the_consumer():
    print("\n[⑨] Large 生成这个消费端（不再被当成需求卡 / generic_card）")
    from kernel import filter as fl, large
    from kernel.filter import classify, build_form_schema
    url = "https://docs.openclaw.ai/plugins/hooks/reference"
    facts = _facts([fl.spider_mechanism("reference", _HOOKS_DOC, url)])
    design = {"id": "persona-slot-engine", "type": "consumer",
              "spec": {"plugin": "persona-slot-engine"},
              "installer": {"inject_script": "installer/inject_local_plugins.py"},
              "hooks": [{"id": "message_sent", "role": "trigger_capture"}],
              "lifecycle": "critical", "capabilities": ["critical-tool"]}
    decl = fl.satisfy_demands(_entries(), [design], facts=facts, delta=None)[0]

    flat = large._flattenNewFormat({"entries": {"providers": [],
                                                "custom_demands": [decl]}})
    got = flat.get("persona-slot-engine") or {}
    check("扁平化后 type 仍是 consumer（以前这里被强写成 demand）",
          got.get("type") == "consumer", f"{got.get('type')}")
    check("三轴带过去（classify 直接读）",
          got.get("auth_kind") == "consumer" and got.get("transport") == "plugin")
    check("生命周期带过去（插槽是生命线：critical）", got.get("lifecycle") == "critical")

    cfg = large._loadForgeConfig()
    check("type_preset_map 里有 consumer（steak 不再掉 generic_card）",
          (cfg.get("type_preset_map") or {}).get("consumer") == "consumer_card")
    inst = large.steak("persona-slot-engine", got)
    check("steak 生成的实例 ui_template = consumer_card",
          inst.get("ui_template") == "consumer_card", f"{inst.get('ui_template')}")
    tags = classify(got)
    form = build_form_schema(tags, cfg)
    check("三轴命中 forge 的范式声明（consumer:plugin:local → consumer_card）",
          bool(form) and form.get("template_name") == "consumer_card"
          and form.get("preset_key") == "consumer:plugin:local", f"{form}")
    inst["_form"] = form
    rendered = large.forge([inst])[0]
    check("渲染用的是 consumer_card 那份 preset（widget=card）",
          (rendered.get("render") or rendered).get("widget") == "card"
          or "consumer_card" in json.dumps(rendered, ensure_ascii=False),
          f"{sorted(rendered.keys())[:8]}")
    check("presets 里确实有 consumer_card（否则 render 会 fallback 并告警）",
          "consumer_card" in (cfg.get("presets") or {}))



# L596-632：test_declaration_schema_is_derived_from_official_mechanism — test_declaration_schema_is_derived_from_official_mechanism
def test_declaration_schema_is_derived_from_official_mechanism():
    print("\n[⑩] 反推声明格式：挂什么钩子/能干什么/多久必须回/要不要权限 —— 全从官方机制来")
    from kernel import filter as fl
    facts = _facts([fl.spider_mechanism(
        "reference", _HOOKS_DOC, "https://docs.openclaw.ai/plugins/hooks/reference")])
    schema = fl.derive_declaration_schema(facts)
    hf = schema["hooks"]
    check("钩子取值域 = 官方目录里的名字（33 个那种）",
          "message_received" in schema["hook_domain"]
          and "session_end" in schema["hook_domain"], f"{schema['hook_domain']}")
    check("性质从官方表来，且**决定允许承担什么作用**",
          hf["message_received"]["allows"] == ["observe"]
          and "modify" in hf["message_sending"]["allows"],
          f"{hf['message_received']['allows']} vs {hf['message_sending']['allows']}")
    check("所属分组从官方小节标题来（投递层 vs 会话生命周期层）",
          hf["message_received"]["group"] == "message_and_delivery_hooks"
          and hf["session_end"]["group"] == "session_lifecycle_hooks",
          f"{hf['message_received']['group']} / {hf['session_end']['group']}")
    check("预算 + **失败策略**都从官方表来（fail closed 与 log and skip 不是一回事）",
          hf["message_sending"]["budget"] == "15 seconds"
          and "log_and_skip" in hf["message_sending"]["failure_policy"]
          and "fail_closed" in hf["before_tool_call"]["failure_policy"],
          f"{hf['before_tool_call']['failure_policy']}")
    check("权限门清单从官方正文来（哪些钩子要先开 allowConversationAccess）",
          "before_prompt_build" in (schema["perm_gate"].get("allowConversationAccess") or [])
          and "before_agent_run" in (schema["perm_gate"].get("allowConversationAccess") or []),
          f"{schema['perm_gate']}")
    check("官方那条总告警只留**标记**（原句不进包，出处在 source_url）",
          schema["emit_caveat"] is True, f"{schema['emit_caveat']}")
    check("官方「任务→钩子」推荐表被抽出来（作用→钩子的官方映射，标签形式）",
          any("rewrite_outgoing" in c["task"] and c["hook"] == "message_sending"
              for c in schema["task_chooser"]), f"{schema['task_chooser']}")
    check("每一条取值域都带官方出处（谁说的能查）",
          all(str(v.get("source_url", "")).startswith("http") for v in hf.values()))
    check("**声明里没有官方文档文字**（标签是我们自己的，原文另存运行期面）",
          not any(" " in str(v.get("kind", "")) for v in hf.values())
          and not any(" " in str(v.get("group", "")) for v in hf.values()))



# L637-665：test_requirement_is_derived_into_declaration — test_requirement_is_derived_into_declaration
def test_requirement_is_derived_into_declaration():
    print("\n[⑪] 需求 → 声明：作用按官方格式落位，性质不够/官方没有 = 显式缺口")
    from kernel import filter as fl
    facts = _facts([fl.spider_mechanism(
        "reference", _HOOKS_DOC, "https://docs.openclaw.ai/plugins/hooks/reference")])
    schema = fl.derive_declaration_schema(facts)
    design = {"id": "persona-slot-engine", "lifecycle": "critical", "hooks": [
        {"id": "message_received", "role": "接住用户话", "needs": "observe"},
        {"id": "message_received", "role": "改用户话", "needs": "modify"},
        {"id": "message_ended", "role": "打错的钩子名", "needs": "observe"},
        {"id": "message_sent", "role": "回合末冲刷状态", "needs": "observe",
         "task": "Flush state after a turn"},
    ]}
    decl = fl.derive_declaration(design, schema)
    got = {h["hook"]: h for h in decl["hooks"]}
    check("作用落位成官方钩子（带性质/预算/出处）",
          got.get("message_received", {}).get("kind") == "observe"
          and str(got.get("message_received", {}).get("source_url", "")).startswith("http"),
          f"{got.get('message_received')}")
    check("官方推荐优先：任务='Flush state after a turn' → 落位成官方推荐的 agent_end",
          "agent_end" in got and "message_sent" not in got, f"{sorted(got)}")
    check("换钩子这件事写在 notes 里（不偷偷换）",
          any("官方推荐钩子" in n for n in decl["notes"]), f"{decl['notes']}")
    why = "；".join(g["why"] for g in decl["gaps"])
    check("性质不够 → 不落位并说明官方给不了（Observe 不能 modify）",
          any("不允许 modify" in g["why"] for g in decl["gaps"]), why[:120])
    check("官方目录里没有的钩子 → 缺口", any(g["want"] == "message_ended" for g in decl["gaps"]))
    check("声明里带上官方的免责**标记**（别把目录当承诺；原句留在运行期面）",
          decl["emit_caveat"] is True)



# L670-696：test_declaration_compiles_to_runtime_spec — test_declaration_compiles_to_runtime_spec
def test_declaration_compiles_to_runtime_spec():
    print("\n[⑫] 声明 → 运行规格：插件宿主照办，不再把钩子硬编在 TS 里")
    from kernel import filter as fl
    facts = _facts([fl.spider_mechanism(
        "reference", _HOOKS_DOC, "https://docs.openclaw.ai/plugins/hooks/reference")])
    design = {"id": "persona-slot-engine", "type": "consumer",
              "spec": {"plugin": "persona-slot-engine"},
              "installer": {"inject_script": "installer/inject_local_plugins.py"},
              "hooks": [{"id": "message_received", "role": "user", "needs": "observe"},
                        {"id": "message_sending", "role": "assistant", "needs": "modify"},
                        {"id": "session_end", "role": "trigger_distill", "needs": "observe"}],
              "lifecycle": "critical"}
    rec = fl.satisfy_demands(_entries(), [design], facts=facts, delta=None)[0]
    rt = fl.plugin_runtime([rec])
    p = (rt.get("plugins") or {}).get("persona-slot-engine") or {}
    check("运行规格里有这个插件", bool(p), f"{list((rt.get('plugins') or {}))}")
    check("规格里的钩子用官方名 + 作用 + 承诺",
          [h["hook"] for h in p.get("hooks", [])] == ["message_received", "message_sending",
                                                      "session_end"]
          and p["hooks"][1].get("needs") == "modify",
          f"{p.get('hooks')}")
    check("规格带生命周期（生命线）与装法（本地插件走 installer）",
          p.get("lifecycle") == "critical"
          and "inject_local_plugins" in str(p.get("install", "")), f"{p.get('lifecycle')}")
    check("规格带语料去向（只进卡）", p.get("scope") == "card-only")
    check("声明里的缺口也随规格给到（插件知道自己哪里悬）",
          isinstance(p.get("gaps"), list))



# L701-754：test_mechanism_grows_without_hand_written_tables — test_mechanism_grows_without_hand_written_tables
def test_mechanism_grows_without_hand_written_tables():
    print("\n[⑬] 生长：官方加新任务/新性质/新措辞 → 现场长标签 + 进候选池转正")
    from kernel import filter as fl

    fl, forge = _patch_lexicon_to_tmp()

    doc = """
## Hook catalog

| Hook | Kind | Description |
| --- | --- | --- |
| `before_compaction` | Archive before | Compact history before a turn |

## Choose a hook

| Task | Hook |
| --- | --- |
| Compress conversation history before a turn | `before_compaction` |
"""
    entry = fl.spider_mechanism("brandnew", doc, "https://docs.openclaw.ai/plugins/hooks/new")
    hk = (entry.get("hooks") or {}).get("before_compaction") or {}
    check("没见过的**性质**也能长出一个标签（不是我手写表里的值）",
          hk.get("kind") == "archive_before", f"{hk}")
    check("没见过的**任务**也长成标签，并给出官方推荐的钩子",
          (entry.get("chooser") or [{}])[0].get("task")
          == "compress_conversation_history_before_a_turn",
          f"{entry.get('chooser')}")
    check("判不出能力就不瞎允许（allows 只认语义词根）",
          fl._allows_from(hk.get("kind", "")) == [], f"{fl._allows_from(hk.get('kind',''))}")


    check("原文不进声明：" + "声明里没有那句官方描述",
          "Compact history" not in json.dumps(entry, ensure_ascii=False))
    raw = {"pages": [{"id": "brandnew", "source_url": "https://docs.openclaw.ai/plugins/hooks/new",
                      "descs": {"before_compaction": "Compact history before a turn"}}]}
    fl._save_mechanisms_raw(raw["pages"])
    with open(fl._MECH_RAW_PATH, "r", encoding="utf-8") as f:
        saved = f.read()
    check("原文落本机运行期文件（生长原料留着）", "Compact history" in saved)
    check("运行期原文文件带出处（谁说的能查）", "docs.openclaw.ai" in saved)


    amb0 = fl._lex_load_ambiguity().get("mechanism_terms") or {}
    fl.observe_mechanism_term("kind", "Archive before")
    fl.observe_mechanism_term("kind", "Archive before")
    promoted = fl.promote_candidates()
    amb = fl._lex_load_ambiguity().get("mechanism_terms") or {}
    check("候选池攒够阈值 → 措辞转正进正式词典（下次直接查，不再现长）",
          (amb.get("kind") or {}).get("Archive before") == "archive_before"
          and any(p.startswith("mech:kind:") for p in promoted),
          f"{amb.get('kind')} / {promoted[:3]}")
    check("转正后同一措辞取到的标签稳定",
          fl.mechanism_label("kind", "Archive before")
          == fl.mechanism_label("kind", "Archive before") == "archive_before")


# L758-773：run_all — run_all
def run_all():
    test_demands_are_linked_to_official_entities()
    test_satisfy_is_deterministic()
    test_lexicon_sediments_exact_only_and_survives_drift()
    test_scout_merges_satisfaction_into_manifest()
    test_wired_so_demand_changes_land_immediately()
    test_satisfaction_reaches_the_consumer_end()
    test_mechanism_is_sorted_out_and_diffed()
    test_plugin_design_is_matched_against_mechanism()
    test_large_generates_the_consumer()
    test_declaration_schema_is_derived_from_official_mechanism()
    test_requirement_is_derived_into_declaration()
    test_declaration_compiles_to_runtime_spec()
    test_mechanism_grows_without_hand_written_tables()
    print(f"\n=== {_passed} passed, {_failed} failed ===")
    return _passed, _failed


if __name__ == "__main__":
    _p, _f = run_all()
    sys.exit(1 if _f else 0)
