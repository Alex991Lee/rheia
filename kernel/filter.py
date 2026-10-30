# -*- coding: utf-8 -*-
"""
kernel.filter — 文档过滤管线单模块宿主（spider 数据驱动管线 v3）

方法化并入：gateKeeper / lexicon / disambiguate / collaborator / taxonomy /
typeClassifier / spider 七件套 + 原 __init__ 门面收敛为本文件
（原 kernel/filter/ 目录保留，仅放 docStructureNotes.md / providerFormatSurvey.md
两份设计文档）。纯标准库，零第三方、零 GUI、零包内相对导入。

归并改名（同名异款/异义，防静默覆盖）：
  - _load_ambiguity ×3（lexicon/collaborator/spider 实现各不相同）
    → _lex_load_ambiguity / _collab_load_ambiguity / spider 版保留原名（外部 import）
  - classify ×2（taxonomy 三轴打标 vs typeClassifier URL 综合分类）
    → taxonomy.classify 保留；typeClassifier.classify → classify_url_type
  - _BASE_DIR 位置常量：宿主落点 kernel/ 层 → 两级上溯（原三级）

外部 import 一律改指 from kernel.filter import ...（含 scout/glory/large/main/ui）。
"""

from __future__ import annotations


# ══════════════════════════════════════════════════════════════
# ═══ kernel.filter.gateKeeper（JSON 写入门禁） — 方法化并入 kernel/filter.py ═══
# ══════════════════════════════════════════════════════════════
# gate_keeper — JSON 写入门禁（轻量版）
#
# 防止运行时跨模块写入 JSON 配置文件导致数据被覆盖。
# 只拦截 JSON 文件写入，不涉及 QSettings 和环境变量。
#
# 参考：旧项目 cbd/sovereign_json.py 的 OWNER_MAP + threading.local + 非owner写入拒绝模式
#

import json
import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

# ── JSON 文件所有权表 ─────────────────────────
# key: 相对项目根的文件路径
# value: 拥有该文件写入权的模块名

_JSON_OWNER_MAP: dict[str, str] = {
    "kernel/glory/glory.json":   "kernel.glory",
    "kernel/large/forge.json":   "kernel.large",
    "kernel/large/mani.json":    "kernel.scout",
}

# ── 调用者身份声明 ─────────────────────────

_caller_local = threading.local()

class json_owner:
    """上下文管理器：声明当前代码块对 JSON 文件的写入身份。

    用法:
        with json_owner("kernel.glory"):
            write_json("kernel/glory/glory.json", data)
    """

    def __init__(self, module: str) -> None:
        self._module = module

    def __enter__(self) -> None:
        _caller_local.value = self._module

    def __exit__(self, *args: object) -> None:
        _caller_local.value = None

# ── 写入检查 ─────────────────────────

def json_write_guard(relpath: str, data: dict[str, Any]) -> None:
    """写入 JSON 文件前检查调用者身份 —— 非 owner 写入直接拒绝。

    门禁不再是装饰：owner 不匹配就抛 PermissionError（原来只 warning 一句
    然后照样放行，等于没有门禁，写坏别人的 JSON 不会有任何阻断）。
    """
    normalized = relpath.replace("\\", "/").lstrip("/")
    owner = _JSON_OWNER_MAP.get(normalized)
    caller = getattr(_caller_local, 'value', None)

    if owner and caller and caller != owner:
        logger.error(
            "gate_keeper: %s 试图写入 %s（属于 %s）—— 已拒绝",
            caller, normalized, owner,
        )
        raise PermissionError(
            "gate_keeper: %s 无权写入 %s（owner=%s）" % (caller, normalized, owner)
        )

    logger.debug("gate_keeper: %s 写入 %s（已放行）", caller or "未声明", normalized)

# ══════════════════════════════════════════════════════════════
# ═══ kernel.filter.lexicon（活词典） — 方法化并入 kernel/filter.py ═══
# ══════════════════════════════════════════════════════════════
# kernel/filter/lexicon.py
# 活词典 —— 让歧义词典自我生长，而非一次性写死的词表。
#
# 核心：词典分两层
#   1. 正式词典（forge.json 的 ambiguity_dict）—— 已多源查证、可信任的词
#   2. 候选池（ambiguity_candidates.json）—— 爬取时发现的新词，待验证
#
# 「生命」机制（三条规则）：
#   规则一 缺口检测：爬取时，字段名/信号词出现在「标准名该在的位置」但不被
#          正式词典认识 → 记入候选池，累计出现次数。
#   规则二 多源查证：候选词累计 >= 阈值（跨文档多次出现）才转正入正式词典，
#          避免单次孤例污染词典。
#   规则三 版本化：词典转正 = 词典内容变化 = bump 词典版本。该版本纳入
#          FILTER_VERSION 的 hash，保证铁律三（确定性序列化）不被破坏。
#
# 值特征归类（用于「候选词该对应哪个标准名」）：
#   - 值以 http:// 开头 → base_url 候选
#   - 值含 _API_KEY/_TOKEN/_SECRET → auth 候选
#   - 值是反引号包裹的标识符 → provider_id 候选
#   - 值含 plugin 字样 → plugin 候选
#

import json
import os
from typing import Any

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FORGE_PATH = os.path.join(_BASE_DIR, "kernel", "large", "forge.json")
_CANDIDATES_PATH = os.path.join(_BASE_DIR, "kernel", "large", "ambiguity_candidates.json")

# 转正阈值：候选词跨文档出现多少次才入正式词典
_PROMOTE_THRESHOLD = 2

def _load_candidates() -> dict:
    """读候选池；不存在或损坏返回空。"""
    try:
        with open(_CANDIDATES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}

def _save_candidates(candidates: dict) -> None:
    with open(_CANDIDATES_PATH, "w", encoding="utf-8") as f:
        json.dump(candidates, f, ensure_ascii=False, indent=2, sort_keys=True)

def observe_field(key: str, value: str) -> None:
    """规则一：观察一个 Property 表字段。若 key 不被正式词典认识，
    且 value 有可判定的特征，就把它记入候选池（对应到某标准名）。"""
    amb = _lex_load_ambiguity()
    field_aliases = amb.get("field_aliases", {})

    # key 已被正式词典认识？跳过
    key_l = key.lower().strip()
    for std, aliases in field_aliases.items():
        for a in aliases:
            if key_l == a.lower() or key_l.startswith(a.lower()):
                return  # 已认识

    # 用值特征判断它对应哪个标准名
    std_name = _classify_by_value(value)
    if not std_name:
        return

    candidates = _load_candidates()
    pool = candidates.setdefault("field_aliases", {}).setdefault(std_name, {})
    pool[key] = pool.get(key, 0) + 1
    _save_candidates(candidates)

def observe_plugin_kind(pid: str, kind: str) -> None:
    """规则一：观察「已部署 OpenClaw 说某 provider 的真实 plugin kind」。

    装通后，scout 采到 `openclaw plugins list` 的 origin（bundled/global）是权威真值，
    与 spider 抓取的 plugin.kind 对照。不一致时调这里，把「pid 真实是 kind」沉淀进
    候选池，累计 >= 阈值转正进正式词典 ambiguity_dict.plugin_kinds。
    下次 spider 抓取查词典直接抓对，不再依赖部署真值在场。
    """
    amb = _lex_load_ambiguity()
    known = amb.get("plugin_kinds", {})
    # 候选池/正式词典的键都按**原样 pid** 写（spider 查词典也用原样 pid）：
    # 这里必须用同一把钥匙比对，原来用 pid.lower() 查，大小写一变就永远"不认识"，
    # 同一个 pid 反复进候选池（判定静默失效）。
    known_key = next((k for k in known if str(k).lower() == pid.lower()), None)
    if known_key is not None and str(known[known_key]).lower() == kind.lower():
        return  # 已认识且一致

    candidates = _load_candidates()
    pool = candidates.setdefault("plugin_kinds", {})
    pool[pid] = pool.get(pid, {})
    pool[pid][kind] = pool[pid].get(kind, 0) + 1
    _save_candidates(candidates)

def observe_demand_entity(did: str, entity_type: str, entity_id: str, fp: str,
                          source_url: str = "") -> None:
    """规则一：观察「一条需求精确命中官方哪个实体」。

    只有**精确命中**（id/别名/显示名归一后完全相等）才走这里 ——
    精确命中不是猜，一次就该固化（与 plugin_kinds 同一判据：权威真值不设阈值）。
    模糊命中（词重叠）**不沉淀**：那是猜，固化了下轮会把需求指到错实体（词典被污染
    比词典为空更糟 —— 空词典只是没结论，脏词典是错结论）。

    fp = 需求文本指纹：客户改了需求文本，旧映射自动作废（不把旧结论贴到新需求上）。
    source_url 一并固化：结论要能自证出处，哪怕下次采集没抓到那个实体页。
    """
    amb = _lex_load_ambiguity()
    cur = (amb.get("demand_entities") or {}).get(did)
    if isinstance(cur, dict) and cur.get("fp") == fp and cur.get("entity_id") == entity_id:
        return  # 已固化且同一结论（幂等，不刷候选池）

    candidates = _load_candidates()
    pool = candidates.setdefault("demand_entities", {})
    prev = pool.get(did) if isinstance(pool.get(did), dict) else {}
    pool[did] = {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "source_url": source_url,
        "fp": fp,
        "hits": int(prev.get("hits", 0)) + 1,
    }
    _save_candidates(candidates)

def _classify_by_value(value: str) -> str:
    """值特征 → 标准字段名（用于候选词的归类）。"""
    v = value.strip()
    if v.startswith(("http://", "https://")):
        return "base_url"
    if any(s in v for s in ("_API_KEY", "_TOKEN", "_SECRET")):
        return "auth"
    # Install route 值：included in OpenClaw / npm; ClawHub / source checkout only
    if any(s in v.lower() for s in ("included in openclaw", "npm", "clawhub", "source checkout")):
        return "install_route"
    if v.startswith("`") or (v and v[0].isalnum() and "-" in v and " " not in v):
        return "provider_id"
    if "plugin" in v.lower():
        return "plugin"
    return ""

def _lex_load_ambiguity() -> dict:
    try:
        with open(_FORGE_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("ambiguity_dict", {}) or {}
    except (OSError, json.JSONDecodeError):
        return {}

def promote_candidates() -> list[str]:
    """规则二：候选池里累计 >= 阈值的词，转正进正式词典。

    返回本次转正的词清单（用于日志 / bump 版本）。
    """
    candidates = _load_candidates()
    amb = _lex_load_ambiguity()
    promoted: list[str] = []
    try:
        from main.main import stk as _stk
        _stk("lexicon.promote", f"候选池={json.dumps(candidates, ensure_ascii=False)[:120]}", ok=True)
    except Exception:
        pass

    changed = False
    # field_aliases 候选转正（用 list() 快照迭代，避免删除时改 dict 大小）
    field_pool = candidates.get("field_aliases", {})
    for std_name in list(field_pool.keys()):
        words = field_pool[std_name]
        for word in list(words.keys()):
            if words[word] >= _PROMOTE_THRESHOLD:
                aliases = amb.setdefault("field_aliases", {}).setdefault(std_name, [])
                if word not in aliases:
                    aliases.append(word)
                    promoted.append(f"field:{std_name}:{word}")
                del words[word]
                changed = True
        if not words:
            del field_pool[std_name]

    # auth_signals 候选转正
    signal_pool = candidates.get("auth_signals", {})
    for kind in list(signal_pool.keys()):
        words = signal_pool[kind]
        for word in list(words.keys()):
            if words[word] >= _PROMOTE_THRESHOLD:
                signals = amb.setdefault("auth_signals", {}).setdefault(kind, [])
                if word not in signals:
                    signals.append(word)
                    promoted.append(f"signal:{kind}:{word}")
                del words[word]
                changed = True
        if not words:
            del signal_pool[kind]

    # plugin_kinds 候选转正（部署真值纠偏：pid → 真实 kind）
    # 部署真值（openclaw plugins list 的 origin）是权威真值，一次观察就该转正，
    # 不走「多源查证 2 次」的阈值——否则 crawl 每次用文档抓错、自愈每次纠正、
    # 反复重启（2026-09-04 死循环实测：候选池永远卡在 bundled:1 转正不了）。
    kind_pool = candidates.get("plugin_kinds", {})
    for pid in list(kind_pool.keys()):
        kinds = kind_pool[pid]
        for kind in list(kinds.keys()):
            if kinds[kind] >= 1:
                known = amb.setdefault("plugin_kinds", {})
                if known.get(pid) != kind:
                    known[pid] = kind
                    promoted.append(f"plugin_kind:{pid}:{kind}")
                del kinds[kind]
                changed = True
        if not kinds:
            del kind_pool[pid]

    # demand_entities 候选转正（需求 → 官方实体：精确命中的映射）    # 与 plugin_kinds 同理：精确命中不是猜，一次观察即固化 —— 下次同一条需求
    # （哪怕客户把话换个说法）直接查字典落位，不再重新靠文本猜。
    demand_pool = candidates.get("demand_entities", {})
    for did in list(demand_pool.keys()):
        rec = demand_pool[did]
        if int(rec.get("hits", 0)) >= 1:
            known = amb.setdefault("demand_entities", {})
            new_rec = {
                "entity_type": rec.get("entity_type", ""),
                "entity_id": rec.get("entity_id", ""),
                "source_url": rec.get("source_url", ""),
                "fp": rec.get("fp", ""),
            }
            if known.get(did) != new_rec:
                known[did] = new_rec
                promoted.append(f"demand:{did}:{rec.get('entity_id', '')}")
            del demand_pool[did]
            changed = True
    if not demand_pool and "demand_entities" in candidates:
        del candidates["demand_entities"]

    # mechanism_terms 候选转正（官方措辞 → 我们的标签：**"长"的第二段**）
    #   与 field_aliases 同阈值：同一措辞跨文档见到 2 次就固化进正式词典，
    #   于是官方出个新性质/新任务，先入池、转正后声明与规格跟着长（不靠手写表）。
    term_pool = candidates.get("mechanism_terms", {})
    for bucket in list(term_pool.keys()):
        words = term_pool[bucket]
        for raw in list(words.keys()):
            rec = words[raw] if isinstance(words.get(raw), dict) else {}
            if int(rec.get("hits", 0)) >= _PROMOTE_THRESHOLD:
                known = amb.setdefault("mechanism_terms", {}).setdefault(bucket, {})
                lab = str(rec.get("label") or _slug(raw))
                if known.get(raw) != lab:
                    known[raw] = lab
                    promoted.append(f"mech:{bucket}:{raw}->{lab}")
                del words[raw]
                changed = True
        if not words:
            del term_pool[bucket]
    if not term_pool and "mechanism_terms" in candidates:
        del candidates["mechanism_terms"]

    if changed:
        # 写回正式词典 + 候选池
        _write_ambiguity(amb)
        _save_candidates(candidates)

    if promoted:
        try:
            from main.main import stk as _stk
            _stk("lexicon.promote.done", f"转正 {len(promoted)} 词: {promoted[:8]}", ok=True)
        except Exception:
            pass
    return promoted

def _write_ambiguity(amb: dict) -> None:
    with open(_FORGE_PATH, "r", encoding="utf-8") as f:
        fg = json.load(f)
    fg["ambiguity_dict"] = amb
    # forge.json 在 ownership 表里归 kernel.large：写前如实声明身份、过门禁，
    # 不再是"绕开 OWNER_MAP 直写"（归属与实际写者不一致时门禁会当场拒绝）
    with json_owner("kernel.large"):
        json_write_guard("kernel/large/forge.json", fg)
    with open(_FORGE_PATH, "w", encoding="utf-8") as f:
        json.dump(fg, f, ensure_ascii=False, indent=2, sort_keys=True)

if __name__ == "__main__":
    # 自检：模拟官方文档出现一个新字段名，看候选池能不能记下、多轮转正
    print("活词典自检")
    observe_field("Authentication Method", "DEEPSEEK_API_KEY")
    observe_field("Authentication Method", "DEEPSEEK_API_KEY")
    c = _load_candidates()
    print("候选池:", json.dumps(c, ensure_ascii=False))
    promoted = promote_candidates()
    print("转正:", promoted)

# ══════════════════════════════════════════════════════════════
# ═══ kernel.filter.disambiguate（实体消歧） — 方法化并入 kernel/filter.py ═══
# ══════════════════════════════════════════════════════════════
# kernel/filter/disambiguate.py
# 实体消歧层 —— 借鉴搜索引擎实体链接（entity linking）的两阶段内核。
#
# 学逻辑，不学舌。搜索引擎消歧的核心是：
#   1. 候选生成：别名表 + 锚文本，把 mention 扩成候选实体集合
#   2. 候选排序：先验概率 + 上下文相似度 + 图一致性，从候选中挑真值
#
# 本项目有现成的「闭合先验」——已部署 OpenClaw 的 providerIds：
#   插件声明 google 这个母体，下面挂 google-gemini-cli / google-vertex 两个子 id。
#   这比 Wikipedia 锚文本还权威，因为它是「官方程序自己说谁是 alias」。
#
# 消歧目标：把文档里散落的 id / alias / 指代，归并成「一个真源词元」，
# 而不是把「出现几次」当「有几个实体」。
#
# 先验图结构（从 CLI providerIds 构建）：
#   {母体: [别名1, 别名2, ...]}
#   如 {"google": ["google-gemini-cli", "google-vertex"],
#       "novita": ["novita-ai", "novitaai"],
#       "xiaomi": ["xiaomi-token-plan"]}
#
# 规则（先验概率 commonness 的等价物）：
#   - 一个 id 命中某母体的别名 → 归并到母体，标注 alias
#   - 一个 id 就是母体本身 → 保持，标注 canonical
#   - 未命中 → 独立 provider，置信度低，标 pending（供人工/词典裁决）
#

from typing import Any

# 命名归一：package/provider 名常见功能后缀，剥掉后得到真源词元。
# 例：@openclaw/fish-audio-speech → fish-audio；@openclaw/perplexity-plugin → perplexity
# 官方命名规律：xxx-provider / xxx-speech / xxx-plugin / xxx-embedding 的 xxx 是真源。
# 后缀表沉淀进 forge.json ambiguity_dict.function_suffixes（数据驱动），
# 代码只留兜底默认——官方改命名规律时改字典，不改代码。
_DEFAULT_SUFFIXES = ["-provider", "-speech", "-plugin", "-embedding"]

def _function_suffixes() -> list[str]:
    """从 forge.json 读功能后缀表；缺省用内置兜底。"""
    import json
    import os
    _base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _forge = os.path.join(_base, "kernel", "large", "forge.json")
    try:
        with open(_forge, "r", encoding="utf-8") as f:
            amb = json.load(f).get("ambiguity_dict", {}) or {}
        suffixes = amb.get("function_suffixes")
        if isinstance(suffixes, list) and suffixes:
            return [str(s) for s in suffixes]
    except (OSError, json.JSONDecodeError):
        pass
    return _DEFAULT_SUFFIXES

def normalize_name(name: str) -> str:
    """剥掉功能后缀，返回真源词元（归一后的名字）。"""
    n = name.strip()
    for suf in _function_suffixes():
        if n.endswith(suf):
            return n[: -len(suf)]
    return n

def build_prior_graph(plugins: list[dict]) -> dict[str, list[str]]:
    """从已部署 OpenClaw 的插件列表构建先验图（母体 → 别名）。

    插件 providerIds 的第一个是母体（canonical id），其余是别名/子 id。
    单个 id 的插件（如 anthropic/providerIds=['anthropic']）也是母体，
    只是没有别名——母体本身必须进图，否则它会落到 pending 被误判。
    """
    graph: dict[str, list[str]] = {}
    for p in plugins:
        pids = p.get("providerIds", [])
        if len(pids) >= 1:
            canonical = pids[0]
            # 母体无论有没有别名都进图（aliases 可为空列表）
            graph.setdefault(canonical, [])
            if len(pids) > 1:
                graph[canonical] = pids[1:]
    return graph

def build_alias_index(prior_graph: dict[str, list[str]]) -> dict[str, str]:
    """反查索引：别名 → 母体（用于 O(1) 归并）。"""
    index: dict[str, str] = {}
    for canonical, aliases in prior_graph.items():
        for a in aliases:
            index[a] = canonical
    return index

def disambiguate_provider_ids(
    provider_ids: list[str],
    prior_graph: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """把文档解析出的 provider id 列表，用先验图归并成真源词元。

    返回 [{id, relation}]:
      id        — 真源词元（母体 id）
      relation  — "canonical"（母体本身）| "alias"（归并到母体）| "pending"（未命中先验）
    """
    prior_graph = prior_graph or {}
    alias_index = build_alias_index(prior_graph)

    # 归一后的先验索引：把母体和别名都归一（剥功能后缀），
    # 这样 fish-audio（文档 id）能对上 fish-audio-speech（插件 package 剥后缀后）。
    norm_alias_index: dict[str, str] = {}
    norm_canonicals: set[str] = set()
    for canonical, aliases in prior_graph.items():
        nc = normalize_name(canonical)
        norm_canonicals.add(nc)
        for a in aliases:
            norm_alias_index[normalize_name(a)] = nc

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        from main.main import stk as _stk
        _stk("disambiguate.enter", f"待消歧 {len(provider_ids)} 个 id，先验图母体 {len(prior_graph)} 个", ok=True)
    except Exception:
        pass

    for pid in provider_ids:
        if pid in seen:
            continue
        seen.add(pid)

        npid = normalize_name(pid)

        if pid in prior_graph or npid in norm_canonicals:
            # 是母体（原始或归一后命中）
            result.append({"id": pid, "relation": "canonical"})
        elif pid in alias_index or npid in norm_alias_index:
            # 是别名 → 归并到母体
            canonical = alias_index.get(pid) or norm_alias_index.get(npid)
            # id 保留别名自己，alias_of 记归并目标，canonical 由其自身条目负责，
            # 避免母体被 alias 分支重复记录一次
            result.append({"id": pid, "relation": "alias", "canonical_id": canonical})
            if canonical not in seen:
                seen.add(canonical)
        else:
            # 未命中先验 → 独立，置信度低
            result.append({"id": pid, "relation": "pending"})

    try:
        from main.main import stk as _stk
        rel = {}
        for r in result:
            rel[r.get("relation")] = rel.get(r.get("relation"), 0) + 1
        _stk("disambiguate.done", f"归位: {rel}", ok=("pending" not in rel) or rel.get("pending", 0) <= 5)
    except Exception:
        pass

    return result

if __name__ == "__main__":
    import json
    plugins = json.load(open("_tmp_plugins.json", encoding="utf-8")).get("plugins", [])
    graph = build_prior_graph(plugins)
    print("先验图（母体 → 别名）:")
    for k, v in sorted(graph.items()):
        print(f"  {k} → {v}")

# ══════════════════════════════════════════════════════════════
# ═══ kernel.filter.collaborator（合作者发现） — 方法化并入 kernel/filter.py ═══
# ══════════════════════════════════════════════════════════════
# kernel/filter/collaborator.py
# 合作者发现 —— spider 的多真源通道之一。
#
# OpenClaw 文档里会提到一批「合作者」（ollama/vllm/lmstudio/sglang 等），
# 它们不是 OpenClaw 自己的东西，是 OpenClaw 接入的外部服务。
# spider 撞见这些合作者名称，要截取名称去它们的官网读，把「OpenClaw 接入
# 它们时需要的参数」（发现端点、解析结构、默认 baseUrl、env 标记）拉回来。
#
# 这些参数是合作者官方定义的、稳定的，但写法异构：
#   - ollama 官网：GET /api/tags 返回 models[].name
#   - vllm 官网：GET /v1/models 返回 data[].id
# 字典（ambiguity_dict.collaborator_terms）负责把这些异构术语翻译成
# OpenClaw 标准形态（discover/parse/default_base/env_key/local_api_key）。
#
# 数据流：
#   OpenClaw 文档 → 识别合作者名称 → 推导官方文档 URL → 广域网拉取
#     → 提取发现端点/解析结构 → 字典翻译分类 → 产出标准形态
#

import json
import re
from typing import Any

def _collab_load_ambiguity() -> dict:
    import os
    _base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _forge = os.path.join(_base, "kernel", "large", "forge.json")
    try:
        with open(_forge, "r", encoding="utf-8") as f:
            return json.load(f).get("ambiguity_dict", {}) or {}
    except (OSError, json.JSONDecodeError):
        return {}

def known_collaborators() -> dict:
    """返回字典里已翻译好的合作者标准形态（collaborator_terms.collaborators）。"""
    amb = _collab_load_ambiguity()
    return amb.get("collaborator_terms", {}).get("collaborators", {}) or {}

def derive_official_doc_urls(name: str) -> list[str]:
    """按品牌域名规律，从合作者名称推导官方文档候选 URL。

    不靠搜索引擎，靠「合作者名称 → 品牌域名」的稳定规律：
      ollama → https://docs.ollama.com/api/tags
      vllm   → https://docs.vllm.ai/en/latest/
      lmstudio → https://lmstudio.ai/docs/developer/rest
    返回候选 URL 列表（探活分级，能通就用）。
    """
    return [
        f"https://docs.{name}.com/api/tags",
        f"https://docs.{name}.ai/en/latest/",
        f"https://{name}.ai/docs/developer/rest",
    ]

def extract_discovery_endpoints(doc_text: str) -> list[dict[str, Any]]:
    """从合作者官方文档文本里，提取「发现端点 + 解析结构」。

    只认稳定的、可结构化的信号，不扫正文泛化词：
      - `GET /api/tags` / `GET /v1/models` 这种明确的 HTTP 端点
      - 返回结构里的 `models` / `data` 数组 + `name` / `id` 字段
    """
    endpoints: list[dict[str, Any]] = []
    # 发现端点：明确的 GET /xxx/models 或 /xxx/tags
    for m in re.finditer(r"GET\s+(/\S*?(?:models|tags)\S*)", doc_text, re.IGNORECASE):
        ep = m.group(1).strip()
        if ep and ep not in [e["discover"] for e in endpoints]:
            # 解析结构：看端点后面附近有没有 models/data + name/id
            parse = {}
            if "tags" in ep:
                parse = {"key": "models", "field": "name"}
            elif "models" in ep:
                parse = {"key": "data", "field": "id"}
            endpoints.append({"discover": ep, "parse": parse})
    return endpoints

def fetch_and_translate(name: str, http_get=None) -> dict[str, Any]:
    """广域网抓取合作者官方文档，提取发现端点/解析结构，翻译进字典。

    这是 spider 多真源通道的「联网」环节：
      1. 按品牌域名规律推导候选 URL（不靠搜索引擎）
      2. 逐源探活（用传入的 http_get，urllib 优先 curl 兜底）
      3. 从官网文档提取「发现端点 + 解析结构」
      4. 翻译进字典 collaborator_terms.collaborators（字典自我丰富）

    返回翻译后的标准形态；失败返回空 dict（不破坏调用方）。
    """
    # 已知的（字典里已有）直接返回，不重复抓
    known = known_collaborators()
    if name in known:
        return known[name]

    if http_get is None:
        return {}

    try:
        from main.main import stk as _stk
        _stk("collaborator.fetch", f"抓取合作者 {name}（字典未命中，广域网）", ok=True)
    except Exception:
        pass

    for url in derive_official_doc_urls(name):
        try:
            doc = http_get(url)
        except Exception:
            continue
        eps = extract_discovery_endpoints(doc)
        if not eps:
            continue
        # 提取到发现端点 → 翻译进字典
        result = {
            "discover": eps[0]["discover"],
            "parse": eps[0]["parse"],
            "default_base": "",
            "env_key": "",
            "local_api_key": "",
        }
        _promote_to_dict(name, result)
        try:
            from main.main import stk as _stk
            _stk("collaborator.fetch.done", f"{name} 翻译进字典: discover={result['discover']}", ok=True)
        except Exception:
            pass
        return result
    try:
        from main.main import stk as _stk
        _stk("collaborator.fetch.fail", f"{name} 全部源抓取失败", ok=False)
    except Exception:
        pass
    return {}

def _promote_to_dict(name: str, spec: dict[str, Any]) -> None:
    """把翻译结果写进字典 collaborator_terms.collaborators（字典自我丰富）。"""
    try:
        # 用模块级 _FORGE_PATH（两级上溯）：原来这里自己三级上溯拼路径，
        # 拼到 kernel/ 之外（仓库根上一层），字典自我丰富其实一直静默失败
        with open(_FORGE_PATH, "r", encoding="utf-8") as f:
            fg = json.load(f)
        collabs = fg.setdefault("ambiguity_dict", {}).setdefault(
            "collaborator_terms", {}).setdefault("collaborators", {})
        collabs[name] = spec
        with json_owner("kernel.large"):
            json_write_guard("kernel/large/forge.json", fg)
        with open(_FORGE_PATH, "w", encoding="utf-8") as f:
            # sort_keys 与 _write_ambiguity 同一字节序（同一份 forge.json 两种写法
            # 会让增量 diff 次次不同 ⇒ 退化成每次全量）
            json.dump(fg, f, ensure_ascii=False, indent=2, sort_keys=True)
    except (OSError, json.JSONDecodeError):
        pass

if __name__ == "__main__":
    print("已知合作者:", list(known_collaborators().keys()))

# ══════════════════════════════════════════════════════════════
# ═══ kernel.filter.taxonomy（三轴分发器） — 方法化并入 kernel/filter.py ═══
# ══════════════════════════════════════════════════════════════
# taxonomy — 标的分发器（Taxonomy Dispatcher）
# 三轴交叉匹配引擎：auth_kind × transport × model_source → forge_preset
#
# 职责:
#   classify(entry)    — 解析 scout 输出的声明，打上三轴标签
#   match(tags)        — 三轴键 → forge.json 里的预设模板
#
# 位置:
#   scout → taxonomy → forge
#

import logging
from typing import Any

# ── 三轴标准值 ────────────────────────────────────

AUTH_KINDS = {
    "api_key",          # 单API Key输入 + Test按钮
    "oauth",            # OAuth跳转/扫码
    "device_code",      # 显示code + 用户在其他设备确认
    "cli_reuse",        # 自动检测本机CLI
    "local_marker",     # URL + 端口绑定
    "custom_proxy",     # Base URL + API兼容模式
    "aws_sdk",          # AWS CLI凭证链
    "no_key",           # 无需凭据（如本地模型）
    "service_account",  # 服务帐号
}

TRANSPORTS = {
    "openai-compatible",     # ~40 providers，80% 配置面
    "anthropic-messages",    # Anthropic 原生协议
    "local-runtime",         # 自动发现端点
    "aws-bedrock",           # AWS Bedrock
    "google-gemini",         # Gemini 原生
}

MODEL_SOURCES = {
    "live_discovery",   # 运行时 openclaw models list 拉取
    "static_catalog",   # 内置在 manifest/插件里的固定列表
    "local_runtime",    # 用户指定 URL/端口后自动探测
    "generic",          # 用户手动输入模型名
}

# ── 官方文档特征表已移除 ──
# 三张硬编码签名表（auth/transport/model_source 关键词→类型）是死代码：
# spider 已产出扁平三轴标签（auth_kind/transport/model_source），classify()
# 直接读标签，永不走到「扫文本关键词」的降级分支。分类的唯一真相源是
# spider 从官方文档权威位置提取的标签，不是这里的关键词猜测。

def classify(entry: dict[str, Any]) -> dict[str, str]:
    """对一条声明打上三轴标签

    输入：scout 输出的 manifest entry
    输出：{auth_kind, transport, model_source} 三轴标签

    直接读 entry 里 spider 已产出的三轴标签（唯一真相源），
    缺省时给默认值，不再扫文本关键词猜测。
    """
    return {
        "auth_kind": entry.get("auth_kind", "unknown"),
        "transport": entry.get("transport", "openai-compatible"),
        "model_source": entry.get("model_source", "generic"),
    }

# ── 表单生成逻辑（加工厂）────────────────────────
# 所有表单由逻辑生成，不再查笛卡尔积表。每个维度贡献它自己的字段，字段取并集。
# forge.json 的 taxonomy_map 只是范式声明（控件样式），不是生成逻辑——
# 逻辑活在代码里，查不到范式声明时靠规则兜底，而不是反过来拆范式。

_FIELDS_BY_AUTH: dict[str, list[str]] = {
    "api_key": ["api_key_input"],
    # oauth：官方走 onboard --auth-choice <id> 完整流程（chutes.md），
    # 不是配置卡上的孤立「登录」按钮。不长死按钮。
    "oauth": [],
    "device_code": ["device_code_display", "poll_button"],
    "cli_reuse": ["cli_detected_label"],
    "local_marker": ["endpoint_url_input", "port_input"],
    "custom_proxy": ["base_url_input", "api_key_input", "compat_mode_selector"],
    # aws_sdk（Bedrock）：官方靠 AWS 环境变量 + 自动发现（providers/bedrock.md），
    # 无「UI 选区域」这一步，region 是 AWS_REGION 环境变量。不长空下拉框。
    "aws_sdk": [],
    "no_key": [],
    "service_account": ["service_account_file"],
    "unknown": ["base_url_input", "api_key_input"],
}
_FIELDS_BY_TRANSPORT: dict[str, list[str]] = {
    # anthropic-messages 的「思考层级」是运行时 /think 命令，不是配置卡下拉框。
    "anthropic-messages": [],
    "local-runtime": ["endpoint_url_input", "port_input"],
    # aws-bedrock：同 aws_sdk，环境变量 + 自动发现，无 UI 下拉。
    "aws-bedrock": [],
    "google-gemini": [],
    "openai-compatible": [],
}
_FIELDS_BY_MODEL_SOURCE: dict[str, list[str]] = {
    "live_discovery": [],
    "static_catalog": ["model_input_manual"],
    # local_runtime 的「发现模型」按钮无接线（死按钮），官方靠 endpoint 自动发现。
    "local_runtime": [],
    "generic": [],
}

def _derive_fields(tags: dict[str, str]) -> list[str]:
    """按三轴规则推导字段列表（一维度一规则，字段取并集）。"""
    auth_kind = tags.get("auth_kind", "unknown")
    transport = tags.get("transport", "openai-compatible")
    model_source = tags.get("model_source", "generic")

    fields: list[str] = []
    for f in _FIELDS_BY_AUTH.get(auth_kind, []):
        if f not in fields:
            fields.append(f)
    for f in _FIELDS_BY_TRANSPORT.get(transport, []):
        if f not in fields:
            fields.append(f)
    for f in _FIELDS_BY_MODEL_SOURCE.get(model_source, []):
        if f not in fields:
            fields.append(f)
    return fields

def resolve_forge_config(tags: dict[str, str], forge_config: dict) -> dict | None:
    """三轴键 → forge 预设

    输入：classify() 输出的三轴标签 + forge.json 配置
    输出：匹配到的预设模板配置（None 表示无匹配，fallback 到 generic）

    生成逻辑（代码）优先：按三轴规则推导字段；taxonomy_map 只是范式声明，
    命中时补它的 behavior_chain/preset 等控件样式，不决定字段。
    """
    # 先从范式声明查控件样式（preset / behavior_chain）
    taxonomy_map = forge_config.get("taxonomy_map", {})
    # 缺省值必须与 classify() 同一套（auth_kind 缺省 unknown）：原来缺省取 api_key
    # 会拼出一个**能命中**的键，auth_kind 缺失时静默命中错预设
    key = f"{tags.get('auth_kind', 'unknown')}:{tags.get('transport', 'openai-compatible')}:{tags.get('model_source', 'generic')}"

    # 规则推导字段（生成逻辑，不查表）
    fields = _derive_fields(tags)

    entry = taxonomy_map.get(key)
    if entry:
        template_name = entry.get("preset", "provider_card")
        behavior_chain = entry.get("behavior_chain", "")
    else:
        # 降级匹配控件样式：只按 auth_kind + transport（忽略 model_source）
        template_name = "provider_card"
        behavior_chain = ""
        for stored_key, val in taxonomy_map.items():
            parts = stored_key.split(":")
            if len(parts) == 3 and parts[0] == tags.get("auth_kind") and parts[1] == tags.get("transport"):
                template_name = val.get("preset", "provider_card")
                behavior_chain = val.get("behavior_chain", "")
                break

    if not fields and not behavior_chain and template_name == "provider_card":
        return None

    # ── 检测桩：字段是规则推导还是查表命中 ──
    try:
        from main.main import stk as _stk
        _stk("taxonomy.derive", f"{key} -> {fields}", ok=bool(fields))
    except Exception:
        pass

    return {
        "preset_key": key,
        "template_name": template_name,
        "behavior_chain": behavior_chain,
        "fields": fields,
    }

def build_method_index(clis: list[dict]) -> dict[str, str]:
    """从 spider 抓到的 CLI 方法生成 method 表（{cli_id/action: 完整命令}）。

    方法表供消费端直查直用——UI 只表达意图（装插件/重启/设默认），
    查这张表拿到完整命令，不手抄命令字面量。spider 已砍掉修饰
    （反引号/<key>占位/--前缀），这里组装成可直查的映射。
    """
    index: dict[str, str] = {}
    for c in clis or []:
        cid = (c.get("id") or "").strip()
        cmd = (c.get("command") or "").strip()
        if not cid or not cmd:
            continue
        for u in c.get("usages") or []:
            action = (u.get("action") or "").strip()
            if not action:
                continue
            index[f"{cid}/{action}"] = f"{cmd} {action}".strip()
        # methods（配置落点）也进表：action 可能是空（前置动词形态），跳过空 action
        for m in c.get("methods") or []:
            action = (m.get("action") or "").strip()
            if not action:
                continue
            index[f"{cid}/{action}"] = f"{cmd} {action}".strip()

    # ── 检测桩：method 表条数 ──
    try:
        from main.main import stk as _stk
        _stk("taxonomy.method_index", f"{len(index)} 条", ok=len(index) > 0)
    except Exception:
        pass
    return index

def build_form_schema(tags: dict[str, str], forge_config: dict) -> dict[str, Any]:
    """按三轴键生成 UI 字段结构

    返回结构可直接喂给 forge.render()，产出带字段的消费端对象
    """
    resolved = resolve_forge_config(tags, forge_config)
    if resolved:
        # 展开字段：taxonomy_map 用字符串名，需要转成结构化 dict
        _expand_field_names(resolved, forge_config)
        return resolved

    # 完全 fallback：返回通用的空字段
    return {
        "preset_key": "generic_fallback",
        "template_name": "generic_card",
        "behavior_chain": "",
        "fields": [
            {"name": "api_key", "label": "API Key", "type": "password", "required": True},
        ],
    }

def _expand_field_names(resolved: dict, forge_config: dict) -> None:
    """把 taxonomy_map 的 fields 字符串列表展开成结构化 dict。

    taxonomy_map 存的是字段类型名（如 "api_key_input"），
    这些名字对应 field_renderer 的注册表。
    实际只查 forge.json 的 scope_mappings 补 widget/hints/behavior（presets 从没被读过）。
    """
    raw_fields = resolved.get("fields", [])
    if not raw_fields:
        return
    expanded = []
    scope_map = forge_config.get("scope_mappings", {})

    for fname in raw_fields:
        if isinstance(fname, dict):
            # 已经是结构化 dict，直接保留
            expanded.append(fname)
            continue
        # 字符串类型名 → 结构化 dict（标签直接由字段名生成；i18n 是空头承诺，删掉）
        _label = fname.replace("_", " ").title()
        field_def = {"name": fname, "label": _label, "type": fname}
        # 尝试从 scope_mappings 找 widget 提示 + 行为语义
        mapping = scope_map.get(fname, {})
        if mapping:
            field_def["widget"] = mapping.get("widget")
            if mapping.get("hints"):
                field_def["hints"] = dict(mapping["hints"])
            if mapping.get("behavior"):
                field_def["behavior"] = mapping["behavior"]
        expanded.append(field_def)
    resolved["fields"] = expanded

# ══════════════════════════════════════════════════════════════
# ═══ kernel.filter.typeClassifier（URL 类型分类） — 方法化并入 kernel/filter.py ═══
# ══════════════════════════════════════════════════════════════
# kernel/filter/type_classifier.py
# 基于 URL 路径优先、内容特征辅助的精确类型分类器。
#
# 替换 scout.py 的 _guessType()。
# URL 由官方文档导航结构决定，比文本关键词可靠。
#

import re

# ── URL 路径模板 — 按优先级从高到低 ──
# (pattern, type_name, confidence)
_URL_RULES: list[tuple[str, str, float]] = [
    # providers: /providers/<name>
    (r"/providers/", "provider", 0.9),
    # channels: /channels/<name>
    (r"/channels/", "channel", 0.9),
    # automation: /automation/<name>
    (r"/automation/", "automation", 0.9),
    # gateway config: /gateway/xxx
    (r"/gateway/", "gateway", 0.9),
    # auth: /auth/xxx or auth-xxx
    (r"/auth", "gateway", 0.9),
    (r"auth-", "gateway", 0.9),
    # CLI onboarding: /cli/onboard
    (r"/cli/onboard", "onboarding", 0.9),
    # CLI config: /cli/config
    (r"/cli/config", "config", 0.85),
    # CLI 其他
    (r"/cli/", "cli", 0.8),
    # plugins: 看名字细分
    (r"/plugins/", "plugin", 0.9),
    # web search: /web/xxx
    (r"/web/", "web_search", 0.9),
    # memory: /memory/xxx, /embedding/xxx
    (r"/memory", "memory", 0.9),
    (r"/embedding", "memory", 0.9),
    # models: /models/xxx, /concepts/models
    (r"/models", "model", 0.85),
    (r"/concepts/models", "model", 0.85),
    # help/env, help/configuration
    (r"/help/environment", "env", 0.9),
    (r"/help/configuration", "gateway", 0.85),
    (r"/help", "reference", 0.7),
    # concepts, reference, start, maturity, refactor → noise/other
    (r"/concepts/", "other", 0.5),
    (r"/reference/", "other", 0.5),
    (r"/start/", "other", 0.5),
    (r"/maturity/", "other", 0.5),
    (r"/refactor/", "other", 0.5),
    # announcements
    (r"/announcements/", "other", 0.4),
]

# ── 内容特征辅助匹配 — 仅当 URL 无法判断时使用 ──
# (keyword_set, type_name, confidence, min_hits)
_CONTENT_SIGNALS: list[tuple[set[str], str, float, int]] = [
    # provider 信号
    ({"api key", "api provider", "model provider", "registerprovider",
      "model catalog", "provider registration"}, "provider", 0.7, 2),
    # model 信号
    ({"embedding model", "language model", "llm", "gpt",
      "model routing", "model configuration", "modelcatalog"}, "model", 0.7, 2),
    # memory 信号
    ({"vector database", "memory storage", "embedding storage",
      "pinecone", "chroma", "memorybackend", "registermemorybackend"}, "memory", 0.7, 2),
    # web_search 信号
    ({"search engine", "web search", "brave search", "search results",
      "websearchprovider", "registerwebsearchprovider"}, "web_search", 0.7, 2),
    # channel 信号
    ({"messaging platform", "instant messaging", "chat application",
      "bot token", "webhook", "channel integration"}, "channel", 0.6, 2),
    # gateway/auth 信号
    ({"authentication", "authorization", "credential", "gateway",
      "openclaw.json", "configuration reference"}, "gateway", 0.6, 2),
    # automation 信号
    ({"automated task", "scheduled job", "cron", "event-driven",
      "workflow", "trigger"}, "automation", 0.6, 2),
]

def classify_by_url(source_url: str) -> tuple[str, float]:
    """从 URL 路径推断类型。返回 (type_name, confidence)。"""
    url_lower = source_url.lower()
    for pattern, type_name, confidence in _URL_RULES:
        if re.search(pattern, url_lower):
            return type_name, confidence
    return "", 0.0

def classify_by_content(body: str) -> tuple[str, float]:
    """从正文内容推断类型。仅当 URL 无法判断时使用。"""
    body_lower = body.lower()
    for keyword_set, type_name, confidence, min_hits in _CONTENT_SIGNALS:
        hits = sum(1 for kw in keyword_set if kw in body_lower)
        if hits >= min_hits:
            return type_name, confidence
    return "", 0.0

def classify_url_type(source_url: str, section_name: str = "", body: str = "") -> tuple[str, float, str]:
    """综合分类：URL 优先 → 内容辅助 → 段落名兜底。

    Returns:
        (resolved_type, confidence, source)
        source: "url" | "content" | "name" | "fallback"
    """
    # 第1步：URL 路径推断
    url_type, url_conf = classify_by_url(source_url)
    if url_type and url_conf >= 0.8:
        return url_type, url_conf, "url"

    # 第2步：内容特征辅助
    if body:
        content_type, content_conf = classify_by_content(body)
        if content_type:
            # 如果 URL 也有结果但置信度不高，合并判断
            if url_type and url_type == content_type:
                return content_type, max(url_conf, content_conf), "url+content"
            return content_type, content_conf, "content"

    # 第3步：URL 低置信度结果仍胜过纯猜测
    if url_type:
        return url_type, url_conf, "url"

    # 第4步：段落名兜底
    if section_name:
        name_lower = section_name.lower().replace("-", " ").replace("/", " ")
        if "provider" in name_lower:
            return "provider", 0.5, "name"
        if "model" in name_lower:
            return "model", 0.5, "name"
        if "channel" in name_lower:
            return "channel", 0.5, "name"
        if "memory" in name_lower:
            return "memory", 0.5, "name"
        if "search" in name_lower:
            return "web_search", 0.5, "name"
        if "auth" in name_lower or "gateway" in name_lower:
            return "gateway", 0.5, "name"
        if "automation" in name_lower:
            return "automation", 0.5, "name"

    # 兜底
    return "other", 0.0, "fallback"

# ══════════════════════════════════════════════════════════════
# ═══ kernel.filter.spider（文档骨架解析主） — 方法化并入 kernel/filter.py ═══
# ══════════════════════════════════════════════════════════════
# kernel/filter/spider.py
# spider — 文档骨架解析器（按 URL 大类分流，逐类结构化提取）
#
# 替代旧的「正则猜谜」式提取。核心思路：
#   - 每个官方文档页都有稳定 frontmatter + URL，先按 URL 大类分流，
#     再交给对应类型的解析器按「文档骨架」提取，不做字段名别名猜谜。
#
# 分流规则（对照官方文档 26 大类 → 消费端类型）：
#   /providers/<id>   → provider   （排除 index/models 等聚合页）
#   /channels/<id>    → channel
#   /gateway/<id>     → gateway
#   /automation/<id>  → automation
#   /concepts/models  → model
#   /memory|/embedding → memory
#   /web/             → web_search
#
# 产物统一是 grouped 结构：{entries: {providers:[...], channels:[...], ...}}
# 写进 ot.json → settleManifest() 增量替代 mani.json（增量变量已就位）。
#
# 原则：零 import 应用模块；读到什么就结构化什么，不多不少。
#
# ════════ 增量铁律（铁律三：确定性序列化）════════
# filter 对同一份文档，产出的 ot 必须字节级一致。这是 ot→mani diff 有意义的前提，
# 也是「增量」是真还是假的地基：
#   - 序列化必须走 _serialize()：固定 key 顺序、indent/separators/ensure_ascii 定死
#   - 禁止依赖 dict 插入序 / 环境 locale / 未排序的 json.dumps
#   - 验收：同输入跑两遍，_serialize(crawl(raw)) 必须 byte-identical
# 违背此铁律 → diff 每次判「不同」→ 整体替换 + signal → 增量退化成「每次全量」，
# 「自生长」名存实亡。
# ════════════════════════════════════════════════════
#

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── 歧义词典（第一轮转译用）：从 forge.json 读 ambiguity_dict ──
# 文档是人类写的，同一件事有多种说法。词典把「字段名变体」归一化，
# 把「auth 信号词」映射到标准 authKind。数据驱动：词典在 forge.json，这里只读。
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FORGE_PATH = os.path.join(_BASE_DIR, "kernel", "large", "forge.json")
_AMBIGUITY = None
_AMBIGUITY_STAMP = None

def _load_ambiguity() -> dict:
    """懒加载 forge.json 的 ambiguity_dict（按文件戳失效，无兜底表）。

    词典是**活的**：lexicon 转正 / 自愈会重写 forge.json。原来一旦填上缓存就永不
    失效 —— 同进程永远读第一份快照（新词要重启才生效）；首次读取失败还会把空 dict
    永久缓存下去（一次 IO 抖动 ⇒ 整个进程恒无词典，静默退化）。现在按
    (mtime_ns, size) 判失效；读失败**不缓存**，本轮按无词典处理并在下次调用重试。
    """
    global _AMBIGUITY, _AMBIGUITY_STAMP
    try:
        st = os.stat(_FORGE_PATH)
        stamp = (st.st_mtime_ns, st.st_size)
        if _AMBIGUITY is None or stamp != _AMBIGUITY_STAMP:
            with open(_FORGE_PATH, "r", encoding="utf-8") as f:
                fg = json.load(f)
            _AMBIGUITY = fg.get("ambiguity_dict", {}) or {}
            _AMBIGUITY_STAMP = stamp
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("filter._load_ambiguity: 读 forge.json 失败，本轮按无词典处理（%s）", exc)
        return {}
    return _AMBIGUITY

# ── 聚合页 / 非实体页，必须排除（否则会提取出 Auth、models 等假 provider）──
_AGGREGATE_PAGES = {
    "index", "models", "overview", "readme", "auth", "authentication",
    "getting-started", "quickstart",
}

# ── URL 大类 → 消费端类型 ──
_URL_ROUTES: list[tuple[str, str]] = [
    # 机制面必须先分流：/plugins/hooks* 讲的是**机制**（钩子名/性质/预算），
    # 不是某个插件实体；混进 plugin 会变成一张没人认识的"插件卡"。
    (r"/plugins/hooks", "mechanism"),
    (r"/plugins/sdk-overview/events-and-hooks", "mechanism"),
    # 渠道入站 SDK：官方在那儿写明「message_sent 只在 adapter opt-in 时由 core 观测」
    # —— 这条**派发告警**正是我们踩过的血泪（本地 WebChat 上 message_sent 一次都不发），
    # 不抓它，声明就会把每轮触发押在一个官方明说不保证的钩子上。
    (r"/plugins/sdk-channel-inbound", "mechanism"),
    (r"/plugins/reference/", "plugin"),
    (r"/providers/", "provider"),
    (r"/channels/", "channel"),
    (r"/gateway/", "gateway"),
    (r"/automation/", "automation"),
    (r"/concepts/models", "model"),
    (r"/concepts/dreaming", "memory"),
    (r"/memory", "memory"),
    (r"/embedding", "memory"),
    (r"/web/", "web_search"),
    (r"/tools/", "tool"),
    (r"/cli/", "cli"),
]

def _extract_local_backends(raw_text: str) -> set[str]:
    """从 gateway/local-models.md 的 "Pick a backend" 表读本地后端清单。

    名称形态异构，三种都要认：
      1. [ds4](/providers/ds4)              → 链接，id 在 URL 里
      2. [LM Studio](/providers/lmstudio)   → 链接，显示名 vs id（大小写/空格差异）
      3. MLX / vLLM / SGLang                → 纯文本，/ 分隔多个名称，无链接
    归一化：小写、去空格，让 "LM Studio" → "lmstudio"、"vLLM" → "vllm"。
    """
    backends: set[str] = set()
    # 定位 local-models 页
    m = re.search(
        r"Source:\s*https://docs\.openclaw\.ai/gateway/local-models(.*?)(?=Source:\s*https?://)",
        raw_text, re.DOTALL,
    )
    if not m:
        return backends
    body = m.group(1)

    def _norm(name: str) -> str:
        """归一化合作者名称：小写 + 去空格，保留连字符。"""
        n = name.strip().lower().replace(" ", "")
        # 只认合理 id 字符
        if re.match(r"^[a-z0-9][a-z0-9-]*$", n):
            return n
        return ""

    # 形态 1+2：链接 [名字](/providers/xxx) → 取 URL 里的 id（权威，不受显示名干扰）
    for link in re.findall(r"\[[^\]]*\]\(/providers/([^)]+)\)", body):
        slug = link.strip().strip("`").split("#")[0]
        n = _norm(slug)
        if n and not _is_aggregate(n):
            backends.add(n)

    # 形态 3：纯文本 "MLX / vLLM / SGLang"，/ 或 、 分隔多个名称
    # 只认 Pick a backend 表的数据行第一列，跳过表头、分隔行、描述性代理文字。
    for line in body.splitlines():
        if "|" not in line:
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 3:
            continue
        name_cell = cells[1]
        # 跳过表头行（Backend）和分隔行（---）
        if name_cell.lower() in ("backend", "") or set(name_cell) <= {"-", " "}:
            continue
        # 跳过描述性代理文字（不是具体合作者名）
        if "proxy" in name_cell.lower() or "custom" in name_cell.lower():
            continue
        # 已含链接的，由形态1+2处理，这里跳过
        if "](/providers/" in name_cell:
            continue
        for part in re.split(r"[/、]", name_cell):
            n = _norm(part)
            # 排除的是"不是具体后端"的代理词；mlx 是官方 Pick a backend 表里与
            # vLLM / SGLang 并列的真后端 —— 原来把它写进排除名单，MLX 被静默丢弃
            if n and n not in ("custom", "oai-proxy", "openai-compatible", "proxy"):
                if len(n) >= 3:
                    backends.add(n)
    return backends

def _route_type(source_url: str) -> str:
    """按 URL 大类分流，返回消费端类型。"""
    url = source_url.lower()
    for pattern, ttype in _URL_ROUTES:
        if re.search(pattern, url):
            return ttype
    return ""

def resolve_install_kind(name: str, http_get=None) -> str:
    """广域网分流：判定一个预装名字是 skill 还是 plugin。

    「逻辑代码生代码」的判定真源——不硬编 skill/plugin 分类，而是去参考源
    查，让机器自己长出来。clawhub 是首选参考源（不是唯一）：
      1. clawhub search（skill 目录真源）：canonicalUrl 含 /skills/ → skill，
         含 /plugins/ → plugin。
      2. npm 官方 registry（plugin 目录真源）：带 @scope/ 的包名先查 npm，
         HTTP 200 且返回 JSON → plugin；裸名也兜底查。
      3. 都判不出 → 空串（调用方跳过，不硬猜）。

    判定顺序：带 @scope/ 前缀的（npm 包名形态）先查 npm registry，避免
    clawhub search 把 plugin 的裸 slug（如 mem9）误配到同名 skill。裸名先
    查 clawhub search（skill 是主流），再查 npm 兜底。

    http_get: 联网函数 (url, timeout) -> str，成功返回 body、失败抛异常。
    由调用方注入——kernel 层零 import 应用模块，不自己持联网栈。

    返回 'skill' / 'plugin' / ''。
    """
    if http_get is None:
        return ""
    slug = name.split("/")[-1].lower()

    def _is_npm_plugin() -> bool:
        try:
            body = http_get(f"https://registry.npmjs.org/{name}")
            return bool(body and body.strip().startswith("{"))
        except Exception:
            return False

    # npm registry 是 plugin 目录真源（包名无论带不带 @scope/，命中即 plugin）。
    # 先查它，避免 clawhub search 把 plugin 的裸 slug（如 mem9）误配到同名 skill。
    if _is_npm_plugin():
        return "plugin"

    # npm 没命中 → 查 clawhub search（skill 目录真源）
    try:
        body = http_get(f"https://clawhub.ai/api/v1/search?q={slug}")
        data = json.loads(body) if body else {}
        for r in data.get("results", []):
            url = (r.get("canonicalUrl") or "").lower()
            rslug = (r.get("slug") or "").lower()
            if slug == rslug or slug in url:
                if "/skills/" in url:
                    return "skill"
                if "/plugins/" in url:
                    return "plugin"
    except Exception as exc:
        logger.warning("resolve_install_kind: clawhub 探活失败（%s）→ 判不出，返回空", exc)

    return ""

def _last_slug(source_url: str) -> str:
    """URL 最后一段作为实体 id（如 /providers/deepseek → deepseek）。"""
    m = re.search(r"/([^/\s#]+)/?$", source_url.rstrip("/"))
    return (m.group(1) if m else "").strip()

def _is_aggregate(slug: str) -> bool:
    return slug.lower() in _AGGREGATE_PAGES

def _extract_supported_channels(raw_text: str) -> set[str]:
    """从 llms-full.txt 的「Supported channels」段提取频道实体白名单。

    raw_text 是 scout 联网拉的官方聚合文档（唯一真源）。channels/ 下既有
    「频道实体」（wechat/feishu/qqbot 等，在 Supported channels 列表里），
    也有「功能说明页」（groups/pairing/troubleshooting 等，只是正文引用
    频道名、不是可接入的聊天平台）。用 Supported channels 列表做实体白名单。
    """
    m = re.search(r"## Supported channels(.*?)## ", raw_text, re.DOTALL)
    if not m:
        return set()
    return set(re.findall(r"- \[[^\]]*\]\(/channels/([a-z-]+)\)", m.group(1)))

# ═══════════════════════════════════════════════════════════════
#  骨架解析：frontmatter + 表格
# ═══════════════════════════════════════════════════════════════

def parse_frontmatter(body: str) -> dict[str, Any]:
    """解析 YAML frontmatter（--- 包裹的那段），返回 dict。

    官方文档每页开头是：
    ---
    summary: "..."
    title: "..."
    read_when: [...]
    ---
    """
    m = re.match(r"^---\s*\n(.*?)\n---", body, re.DOTALL)
    if not m:
        return {}
    fm: dict[str, Any] = {}
    for line in m.group(1).splitlines():
        kv = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)\s*$", line)
        if kv:
            key, val = kv.group(1), kv.group(2).strip().strip('"').strip("'")
            fm[key] = val
    return fm

def parse_property_table(body: str) -> dict[str, str]:
    """解析 Property 表（规范的 | key | value | 两列表格）。

    不做字段名别名猜谜：第一列就是键、第二列就是值，原样保留。
    只认「第一列是字段名、第二列是值」的两列表格，避免误吞。
    """
    result: dict[str, str] = {}
    in_table = False
    for line in body.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            if in_table:
                break  # 表格结束
            continue
        cells = [c.strip() for c in s.split("|")[1:-1]]
        if not cells or len(cells) < 2:
            continue
        # 表头 / 分隔行
        joined = "|".join(c.lower() for c in cells)
        if any(kw in joined for kw in ("property", "value", "---", ":-")):
            in_table = True
            continue
        if not in_table:
            continue
        key, val = cells[0], cells[1]
        # 保留反引号——反引号是 id 结构标记，由 _parse_provider_ids 统一解析，
        # 这里提前剥掉会破坏「`id1`, `id2`」这种多 id 的结构。
        val = val.strip()
        if key:
            result[key] = val
    return result

def _find_prop(prop: dict[str, str], *keywords: str) -> str:
    """从 Property 表里查找字段值，用歧义词典归一化字段名。

    keywords 是标准字段名（如 "base_url"、"auth"、"provider_id"），
    词典 field_aliases 把文档里的变体（"Suggested base URL" 等）映射回标准名。
    匹配规则：标准名或其变体是字段名的「完整或前缀」匹配，避免子串误命中。
    """
    amb = _load_ambiguity()
    field_aliases = amb.get("field_aliases", {})

    for kw in keywords:
        # 标准名 + 词典里的变体，一起去匹配
        candidates = [kw]
        if kw in field_aliases:
            candidates.extend(field_aliases[kw])

        for key, val in prop.items():
            k = key.lower().strip()
            for cand in candidates:
                cand_l = cand.lower().strip()
                # 词边界匹配：cand 作为独立单词出现在字段名里（如 "base url" 命中
                # "Default base URL"、"Suggested base URL"），但不会子串误命中
                # （如 "provider" 不会命中 "provider/model" 这种非字段名）。
                if re.search(rf"\b{re.escape(cand_l)}\b", k):
                    return val.strip()
    return ""

def extract_plugin_package(body: str) -> str:
    """从 ## Install plugin 节提取官方插件包名。

    只认「@openclaw/ 前缀」的官方插件包。裸名（如 claude-max-api-proxy 这种
    社区包，没有 @openclaw/ 前缀）不是官方插件，不算。
    """
    m = re.search(r"openclaw\s+plugins\s+install\s+(@openclaw/[\w@./-]+)", body)
    if m:
        return m.group(1).strip()
    m = re.search(r"npm\s+(?:install|i)\s+(-g\s+)?(@openclaw/[\w@./-]+)", body)
    if m:
        return m.group(2).strip()
    return ""

def extract_env_vars(body: str) -> list[str]:
    """提取反引号包裹的 _API_KEY / _TOKEN / _SECRET 环境变量。"""
    vars_found = re.findall(r"`([A-Z][A-Z0-9_]{2,})`", body)
    seen: list[str] = []
    for v in vars_found:
        if any(v.endswith(suf) for suf in ("_API_KEY", "_TOKEN", "_SECRET", "_KEY", "_ID")):
            if v not in seen:
                seen.append(v)
    return seen

def extract_auth_choice(body: str) -> str:
    """提取 onboard 的 --auth-choice 值（官方 provider 配 key 的权威命令参数）。

    官方 provider 文档的 Getting started 节有明文：
      openclaw onboard --auth-choice deepseek-api-key
    这是「配 key 该用哪个 auth-choice」的唯一权威来源，不是 --{pid}-api-key 的
    简单规律（moonshot 就有 moonshot-api-key 和 moonshot-api-key-cn 两个，硬编会错）。

    只提取第一个 --auth-choice 值（Getting started 主路径）；抓不到返回空串。
    """
    m = re.search(r"--auth-choice\s+([a-z][\w-]*-api-key)", body)
    if m:
        return m.group(1)
    return ""

def extract_model_table(body: str) -> list[dict[str, Any]]:
    """从 ## Built-in catalog 等节的表格提取模型目录。"""
    models: list[dict[str, Any]] = []
    # 定位 catalog 节
    m = re.search(r"##\s*(?:Built-in\s+catalog|Model\s+IDs?|Available\s+models?)\s*\n(.*?)(?=\n##\s|\Z)", body, re.DOTALL | re.IGNORECASE)
    section = m.group(1) if m else body
    lines = section.splitlines()
    header: list[str] = []
    in_table = False
    for line in lines:
        s = line.strip()
        if not s.startswith("|"):
            if in_table:
                break
            continue
        cells = [c.strip() for c in s.split("|")[1:-1]]
        if not cells:
            continue
        joined = "|".join(c.lower() for c in cells)
        if any(kw in joined for kw in ("model", "ref", "name")) and not header:
            header = [c.lower() for c in cells]
            in_table = True
            continue
        if all(re.match(r"^[-:.]+$", c) for c in cells if c):
            continue
        if not in_table or not header:
            continue
        if len(cells) != len(header):
            continue
        row = dict(zip(header, cells))
        ref = row.get("model ref") or row.get("model") or row.get("ref") or ""
        ref = ref.strip("`").strip()
        if not ref or not re.match(r"^[a-zA-Z0-9_./-]+$", ref):
            continue
        entry: dict[str, Any] = {"ref": ref}
        for k, v in row.items():
            if "name" in k and k not in ("model ref",):
                entry["name"] = v
            elif "context" in k:
                num = re.search(r"[\d,]+", v)
                if num:
                    entry["context_window"] = int(num.group(0).replace(",", ""))
            elif "input" in k:
                entry["input"] = v
        models.append(entry)
    return models

# ═══════════════════════════════════════════════════════════════
#  各类 spider
# ═══════════════════════════════════════════════════════════════

def _parse_provider_ids(raw_ids: str) -> list[str]:
    """解析 Provider 字段，提取干净的 provider id 列表。

    处理三种官方格式：
      1. `id`                       → ['id']
      2. `id` (alias: `xx`)         → ['id']（丢 alias 括号）
      3. `id1` (desc), `id2` (desc) → ['id1', 'id2']（逗号拆分）

    关键：先剥掉括号内容（alias/描述，它们的逗号不是 provider 分隔符），
    再按逗号拆，最后取反引号内第一个 token 作为 id。
    """
    # 1. 剥掉所有括号内容（含 alias 描述，它们内部可能有逗号，会干扰拆分）
    without_parens = re.sub(r"\([^)]*\)", " ", raw_ids)
    # 2. 按逗号拆（真 provider 的逗号在括号外）
    ids: list[str] = []
    for segment in without_parens.split(","):
        segment = segment.strip()
        if not segment:
            continue
        # 3. 取反引号内第一个 token 作为 id
        backticked = re.findall(r"`([^`]+)`", segment)
        if backticked:
            pid = backticked[0].strip()
        else:
            pid = segment.strip()
        if pid and re.match(r"^[a-zA-Z][a-zA-Z0-9_.-]*$", pid):
            ids.append(pid)
    return ids

def _normalize_api(raw: str) -> str:
    """把 Property 表 API 字段的杂文归一成官方标准 request adapter。

    官方 config-tools.md:532 明文列出 12 种标准 adapter；文档 Property 表的 API
    字段是描述性杂文（'OpenAI-compatible (`openai-completions`)'、'Anthropic
    Messages compatible'），真值被反引号/描述包裹。查字典 api_signals 归一——
    跟 auth_kind 消歧是同一套机制，api 也是会漂移、会过度修饰的字段。

    优先级：先精确匹配信号词；兜底扫反引号里的标准名；再兜底正文信号词。
    归一不到就返回空串（诚实：查不到不猜）。
    """
    if not raw:
        return ""
    text = raw.strip()
    amb = _load_ambiguity()
    api_signals = amb.get("api_signals") or {}

    # 1) 精确匹配信号词（含标准名本身 + 描述变体）
    low = text.lower()
    for std, words in api_signals.items():
        if std.startswith("_"):
            continue
        for w in words:
            if str(w).lower() in low:
                return std

    # 2) 反引号里直接出现标准名（`openai-completions`）
    for std in api_signals:
        if std.startswith("_"):
            continue
        if re.search(r"`" + re.escape(std) + r"`", text):
            return std

    # 3) 兜底：裸标准名
    for std in api_signals:
        if std.startswith("_"):
            continue
        if std in text:
            return std
    return ""

def _api_field(prop: dict[str, str], body: str) -> str:
    """取 provider 的 api 协议，Property 表优先，正文兜底。

    Property 表有 API 字段的（vllm/sglang）直接归一；没有的（ollama/lmstudio），
    从正文 JSON 配置例子里抓 `api: "xxx"`。两处都归一成官方标准 adapter。
    """
    raw = prop.get("API", "")
    if raw.strip():
        norm = _normalize_api(raw)
        if norm:
            return norm
    # 正文兜底：api: "openai-completions" / api: "ollama" 形态
    for m in re.findall(r'api:\s*["\']([^"\']+)["\']', body, re.IGNORECASE):
        norm = _normalize_api(m.strip())
        if norm:
            return norm
    return _normalize_api(raw)

def _dynamic_by(body: str) -> str:
    """判本地服务的发现策略：implicit_env / wildcard。

    官方文档章节标题是稳定信号：
      ollama/vllm/sglang 有 `## Model discovery (implicit provider)` 节 → implicit_env
      lmstudio 无该节（走显式 Configuration）→ wildcard
    注意：vllm/sglang 的 `## Explicit configuration` 是可选进阶路径，不是默认；
    判定只看有没有 implicit 节，不碰 explicit 节。
    """
    low = body.lower()
    if re.search(r"##\s*model discovery \(implicit provider\)", low):
        return "implicit_env"
    return "wildcard"

def _classify_auth_kind(body: str, env_vars: list[str], base_url: str, auth_raw: str = "") -> str:
    """把 provider 的认证形态细分到 9 种 authKind（读官方文档权威信号，不扫正文泛化词）。

    9 种形态（taxonomy.AUTH_KINDS）：
      api_key         有 *_API_KEY / *_SECRET env var（云 API key，最常见）
      oauth           Auth 字段含 OAuth / sign-in / login flow
      device_code     Auth 字段含 device code / device flow
      cli_reuse       Auth 字段含 CLI / credential，且无 env var（复用本机 CLI 凭据）
      local_marker    baseUrl 是本地地址且无 env var（本地服务，靠 URL 端口标记）
      custom_proxy    baseUrl + openai-compatible，用户自填代理地址
      aws_sdk         Auth 含 AWS / IAM / credential chain
      no_key          Auth 含 no auth / key-free / not required / signed-in local
      service_account Auth 含 service account / GOOGLE_APPLICATION_CREDENTIALS

    关键：oauth/device_code/no_key 等模糊形态，只在 auth_raw（Property 表的 Auth 字段）
    里判定，绝不扫 body 正文——正文里提到 "oauth" 不代表该 provider 是 oauth。
    优先级：aws_sdk > service_account > oauth > device_code > no_key >
            cli_reuse > local_marker > custom_proxy > api_key > unknown
    """
    auth_text = (auth_raw or "").lower()

    # 从歧义词典读 auth 信号词（数据驱动，词典加词分类自动扩展）
    amb = _load_ambiguity()
    auth_signals = amb.get("auth_signals", {})

    def _hit(signal_kind: str) -> bool:
        """auth_text 是否命中词典里 signal_kind 的任一信号词。"""
        for word in auth_signals.get(signal_kind, []):
            if word.lower() in auth_text:
                return True
        return False

    # 有 env var 的形态，先按 var 名细分
    has_api_key_env = any(
        v.endswith("_API_KEY") or v.endswith("_SECRET") or v.endswith("_TOKEN")
        for v in env_vars
    )
    has_service_acct_env = any(
        "SERVICE_ACCOUNT" in v or "GOOGLE_APPLICATION_CREDENTIALS" in v
        for v in env_vars
    )

    # 模糊形态只在 auth_raw（Auth 字段）里判定，且信号词来自词典
    if has_service_acct_env or _hit("service_account"):
        return "service_account"
    # oauth 只在「Auth 字段明确是 OAuth 且无 api key env var」时判 oauth。
    # 有 api key env var（如 chutes 的 CHUTES_API_KEY）说明官方给了 api key 路径，
    # oauth 只是可选路径之一，此时应走 api_key（官方 auth_choice 也是 *-api-key）。
    if _hit("oauth") and not has_api_key_env:
        return "oauth"
    if _hit("device_code"):
        return "device_code"
    if _hit("no_key"):
        return "no_key"
    if _hit("cli_reuse") and not has_api_key_env:
        return "cli_reuse"
    if _hit("aws_sdk"):
        return "aws_sdk"
    if _hit("custom_proxy") and not has_api_key_env:
        return "custom_proxy"
    # local_marker：本地 baseUrl + 无 env var
    if base_url and re.search(r"(127\.0\.0\.1|localhost|0\.0\.0\.0)", base_url, re.IGNORECASE):
        if not has_api_key_env:
            return "local_marker"
    # custom_proxy：有 base url 但非本地，且无标准 env var
    if base_url and not has_api_key_env:
        return "custom_proxy"
    if has_api_key_env:
        return "api_key"
    return "unknown"

def spider_provider(slug: str, body: str, source_url: str, local_backends: set[str] | None = None) -> dict[str, Any]:
    """按 provider 文档骨架提取：frontmatter + Property 表 + plugin + env + models。

    local_backends: 从 gateway/local-models.md 读出的本地后端 id 集合，
    用于标记 config_shape（本地模型 vs 云）。"""
    fm = parse_frontmatter(body)
    prop = parse_property_table(body)
    plugin_pkg = extract_plugin_package(body)
    local_backends = local_backends or set()

    # 官方 provider id：Property 表的 Provider / Provider ID / Provider ids 字段是权威 id，
    # URL slug 只是文件名（如 bedrock 的 slug 是 bedrock，官方 id 是 amazon-bedrock）。
    # 三种格式都要处理：
    #   1. `id`                       → 单个 id
    #   2. `id` (alias: `xx`)         → 只取 id，丢 alias 括号
    #   3. `id1` (desc), `id2` (desc) → 逗号拆分多个 id
    raw_ids = _find_prop(prop, "provider id", "provider")
    if not raw_ids or len(raw_ids) > 200:
        raw_ids = slug
    provider_ids = _parse_provider_ids(raw_ids)
    if not provider_ids:
        provider_ids = [slug]

    # plugin 语义（信号只来自 Property 表，绝不在正文扫 bundled——正文的 "bundled"
    # 是修饰别的对象的形容词，如 openai 的 "bundled Codex runtime"、bedrock-mantle 的
    # "bundled provider"（其实是 official external 插件），会误判）：
    #   - 有官方包名 → official
    #   - Property 表 Plugin 字段明确 bundled → bundled
    #   - Property 表 Plugin 字段 "none/config-only" → none
    #   - Property 表写 official/external → official
    #   - 无 Plugin 字段但有 auth: aws-sdk → none（走 AWS SDK 凭证链，非插件）
    #   - 其余 → unknown，交给插件文档页（_link_plugins_to_providers）回填
    plugin_kind = "unknown"
    # 部署真值词典优先：self_heal 用 openclaw plugins list 的 origin 纠正后，把
    # 「pid 真实是 kind」写进词典 plugin_kinds。这里先查词典，命中即信任部署真值，
    # 不再走文档抓取（文档会误判 anthropic=official，导致每次 crawl 抓错、每次
    # 自愈纠正、反复重启的死循环——2026-09-04 实测）。词典是权威真值沉淀，优先于文档。
    # 用 provider_ids（权威 id）查词典，不是 slug（slug 是 URL 文件名，如 bedrock
    # 的官方 id 是 amazon-bedrock，两者不一致会查不到）。
    _deployed_kind = ""
    for _pid in provider_ids:
        _k = _load_ambiguity().get("plugin_kinds", {}).get(_pid, "")
        if _k:
            _deployed_kind = _k
            break
    if _deployed_kind:
        plugin_kind = _deployed_kind
    plugin_raw = prop.get("Plugin", "").lower()
    body_lower = body.lower()
    body_intro = body[:600].lower()
    if _deployed_kind:
        # 词典命中，跳过文档抓取判定
        pass
    elif plugin_pkg:
        plugin_kind = "official"
    # 「无插件」的负向声明优先于 "bundled"（inferrs 的 "none — not a bundled" 必须判 none）
    elif ("not a bundled" in plugin_raw or "not an openclaw plugin" in plugin_raw
          or plugin_raw.startswith("none") or "config-only" in plugin_raw):
        plugin_kind = "none"
    elif "bundled" in plugin_raw:
        plugin_kind = "bundled"
    elif "official" in plugin_raw or "external" in plugin_raw:
        plugin_kind = "official"
    # 有 plugins.entries.<自身id> 配置键 → 有 bundled 插件（AWS bedrock 等）。
    # 精确匹配当前 provider 的 id/slug，不用通配——通配会误伤 openai/google 等
    # （它们的正文也有 plugins.entries.xxx，但那是别家插件的配置键）。
    elif any(
        re.search(rf"plugins\.entries\.{re.escape(name)}", body_lower)
        for name in {slug, *provider_ids}
    ):
        plugin_kind = "bundled"
    elif "aws-sdk" in body_intro or "aws credential" in body_intro or "iam credential" in body_intro:
        plugin_kind = "none"

    env_vars = extract_env_vars(body)
    models = extract_model_table(body)
    auth_choice = extract_auth_choice(body)
    # 词典优先：self_heal 用 onboard --help 真值补全 auth_choice 后固化进词典
    # auth_choices。这里先查词典，命中即信任部署真值，不走文档抓取（speech 类
    # 插件文档无 auth_choice 明文，文档抓空会导致每次 crawl 抓空、自愈每次补、
    # 反复重启的死循环）。词典命中优先于文档抓取结果。
    if not auth_choice:
        for _pid in provider_ids:
            _ac = _load_ambiguity().get("auth_choices", {}).get(_pid, "")
            if _ac:
                auth_choice = _ac
                break

    # 默认模型：优先 Property 表，其次 Built-in catalog 第一个
    default_model = prop.get("Suggested chat default") or prop.get("Default model") or ""
    if not default_model and models:
        default_model = models[0]["ref"]

    # 配置形态：本地模型 vs 云，决定界面暴露给客户的最简字段。
    #   本地模型命门是 baseUrl（模型靠 /v1/models 自动发现），只给地址框；
    #   云模型命门是 apiKey，给 key 框。
    #   信号三个权威来源（都不扫正文，避免误伤 openai 等云服务）：
    #     1. 官方 local-models.md 归类清单（最强信号）
    #     2. Property 表的 Base URL 字段是本地地址（127.0.0.1/localhost/0.0.0.0）
    #     3. frontmatter summary 明确声明 local model / local runtime
    base_url = _find_prop(prop, "base url", "endpoint")
    # baseUrl 是 URL 不是 id 列表，剥掉反引号（反引号只在 provider_id 解析时需要）
    base_url = base_url.strip("`").strip()
    # 兜底：Property 表没有 baseUrl 的（如 ollama/lmstudio），从正文 JSON 配置例子里
    # 提取本地默认地址（127.0.0.1/localhost 开头），不取远程/示例 host。
    if not base_url:
        body_base_urls = re.findall(r'baseUrl:\s*["\']([^"\']+)["\']', body, re.IGNORECASE)
        for u in body_base_urls:
            if re.search(r"(127\.0\.0\.1|localhost|0\.0\.0\.0)", u, re.IGNORECASE):
                base_url = u.strip()
                break
    config_shape = "api_key"
    summary_lower = fm.get("summary", "").lower()
    is_local_summary = ("local model" in summary_lower or "local runtime" in summary_lower
                        or "local llm" in summary_lower or "self-host" in summary_lower)
    is_local_base_url = bool(base_url and re.search(r"(127\.0\.0\.1|localhost|0\.0\.0\.0)", base_url, re.IGNORECASE))
    if slug in local_backends or is_local_summary or is_local_base_url:
        config_shape = "local_runtime"

    # auth 语义：细分到 9 种 authKind（读官方文档权威信号，不猜）
    # auth_raw = Property 表的 Auth 字段原文（oauth/device_code 等模糊形态只在这里判）
    auth_raw = _find_prop(prop, "auth")
    auth_kind = _classify_auth_kind(body, env_vars, base_url, auth_raw)

    # 一个文档页可能声明多个 provider id，逐个生成 entry
    entries = []
    for pid in provider_ids:
        entries.append({
            "id": pid,
            "source_url": source_url,
            "section_name": fm.get("title", slug),
            "config_shape": config_shape,
            "auth": {
                "kind": auth_kind,
                "env_vars": [{"var": v, "priority": i + 1} for i, v in enumerate(env_vars)],
                "raw": prop.get("Auth env var", ""),
            },
            "plugin": {
                "kind": plugin_kind,
                "package": plugin_pkg,
            },
            "default_model": default_model.strip("`"),
            "default_base_url": base_url,
            "api": _api_field(prop, body),
            "dynamic_by": _dynamic_by(body),
            "auth_choice": auth_choice,
            "models": models,
            "_body": body,  # 供后置兜底（plugin 语义精修）使用，发布时剔除
        })
    return entries

def _extract_channel_commands(body: str) -> list[dict[str, str]]:
    """从 channel 文档正文抓接入命令（openclaw channels login/add / plugins install）。

    官方 channel 文档的接入命令是稳定形态（实测）：
        openclaw channels login --channel openclaw-weixin
        openclaw channels add --channel qqbot --token "AppID:AppSecret"
        openclaw plugins install @openclaw/qqbot
    这些是「怎么做」的方法，spider 抓进 mani 的 channel.commands，UI 据此长按钮。
    只抓 openclaw 开头的命令行，不扫正文泛化描述。
    """
    commands: list[dict[str, str]] = []
    seen: set[str] = set()
    for line in body.splitlines():
        s = line.strip()
        if not s.startswith("openclaw "):
            continue
        s = re.split(r"\s+#", s, 1)[0].rstrip("\\").strip()
        if not s or s in seen:
            continue
        # 只收真正的接入命令：channels login / channels add / plugins install。
        # 排除 status/probe（调试）、--account 示例变体、--force/legacy 变体。
        if not re.search(r"openclaw (channels (login|add)|plugins install)", s):
            continue
        if re.search(r"channels (status|list|remove|resolve|capabilities|logs|logout)", s):
            continue
        if "--account " in s or "--force" in s or "@legacy" in s:
            continue
        seen.add(s)
        commands.append({"cmd": s})
    return commands

def spider_channel(slug: str, body: str, source_url: str) -> dict[str, Any]:
    """按 channel 文档骨架提取。region 从字典 channel_region 长出来（不硬编 id）。"""
    fm = parse_frontmatter(body)
    env_vars = extract_env_vars(body)

    # 地域分拣：正文命中字典 channel_region.cn 的信号词 → 判中国频道
    amb = _load_ambiguity()
    cn_signals = (amb.get("channel_region") or {}).get("cn") or []
    body_lower = body.lower()
    region = "cn" if any(s.lower() in body_lower for s in cn_signals) else ""

    entry = {
        "id": slug,
        "source_url": source_url,
        "section_name": fm.get("title", slug),
        "summary": fm.get("summary", ""),
        "auth": {
            "kind": "api_key" if env_vars else "unknown",
            "env_vars": [{"var": v, "priority": i + 1} for i, v in enumerate(env_vars)],
        },
        "commands": _extract_channel_commands(body),
        "type": "channel",
    }
    if region:
        entry["region"] = region
    return entry

def spider_gateway(slug: str, body: str, source_url: str) -> dict[str, Any]:
    """按 gateway 文档骨架提取。"""
    fm = parse_frontmatter(body)
    return {
        "id": slug,
        "source_url": source_url,
        "section_name": fm.get("title", slug),
        "summary": fm.get("summary", ""),
        "type": "gateway",
    }

def spider_plugin(slug: str, body: str, source_url: str) -> dict[str, Any]:
    """从插件文档页提取 plugin 语义（package 名 + 它注册的 provider id）。

    插件页 ## Distribution 节格式：
      - Package: `@openclaw/deepseek-provider`
      - Install route: npm; ClawHub: `clawhub:@openclaw/deepseek-provider`
    ## Surface 节：
      providers: `deepseek`
    """
    fm = parse_frontmatter(body)
    package = ""
    m = re.search(r"Package:\s*`?(@?[\w@./-]+)`?", body)
    if m:
        package = m.group(1).strip("`")

    # Install route（权威信号）：included in OpenClaw → bundled；npm/ClawHub → external；
    # source checkout only → 源码检出（不装包）。据此定 plugin.kind，不靠包名反推。
    install_route = ""
    rm = re.search(r"Install route:\s*([^\n]+)", body)
    if rm:
        install_route = rm.group(1).strip().lower()

    # Surface 节里注册的 provider id（把插件关联回 provider）
    # 注意：有的插件（speech/embedding 类）没有 providers: 字段，只有 contracts:，
    # 它们的 provider id 就是插件自身 slug（azure-speech 插件 → provider azure-speech）。
    provider_ids: list[str] = []
    # 能力类型（contracts）：这个插件提供什么 provider 能力
    # speechProviders / imageGenerationProviders / webSearchProviders / embeddingProviders 等
    capabilities: list[str] = []
    surface_m = re.search(r"##\s*Surface\s*\n(.*?)(?=\n##\s|\Z)", body, re.DOTALL | re.IGNORECASE)
    if surface_m:
        # providers: `xxx`, `yyy`; contracts: `zzz` —— 取 providers 段（分号前）
        pm = re.search(r"providers?:\s*([^;]+)", surface_m.group(1), re.IGNORECASE)
        if pm:
            raw_ids = pm.group(1).replace("`", "").strip()
            provider_ids = [p.strip() for p in raw_ids.split(",") if p.strip()]
        # contracts: `speechProviders`, `imageGenerationProviders` —— 能力类型
        cm = re.search(r"contracts?:\s*([^;\n]+)", surface_m.group(1), re.IGNORECASE)
        if cm:
            raw_caps = cm.group(1).replace("`", "").strip()
            capabilities = [c.strip() for c in raw_caps.split(",") if c.strip()]

    # 若 Surface 没有 providers 字段，插件自身 slug 就是 provider id（非 chat 类）
    if not provider_ids and slug:
        provider_ids = [slug]

    return {
        "id": slug,
        "source_url": source_url,
        "section_name": fm.get("title", slug),
        "package": package,
        "install_route": install_route,
        "providers": provider_ids,
        "capabilities": capabilities,
        "type": "plugin",
    }

def _extract_memory_switches(body: str) -> dict[str, Any]:
    """从 memory 类文档（如 dreaming.md）正文抓「能力开关」。

    官方 dreaming.md 的稳定形态（实测）：
      ## Slash command 段：
        /dreaming on
        /dreaming off
        /dreaming status
        /dreaming help
      ## Key defaults 段：
        <ParamField path="enabled" ... default="true">
      或 Note：「enabled by default」。

    只抓 / 开头的 slash 命令 + default 值，不扫正文泛化词。
    提取逻辑活在代码里（规律），不硬编具体开关名——任何 memory 文档
    只要写 /xxx on/off + default，就能被这里提取出来。
    """
    switches: dict[str, Any] = {}

    # 1) slash 命令：/xxx on|off|status|help 的行
    cmds: dict[str, str] = {}
    for line in body.splitlines():
        s = line.strip()
        m = re.match(r"^/(\S+)\s+(on|off|status|help)\b", s)
        if m:
            name = m.group(1)
            action = m.group(2)
            if name not in cmds:
                cmds[name] = {}
            cmds[name][action] = s

    # 2) 默认值：只认与**本开关名绑定**的 default 明文（ParamField 的 path 含该开关名）。
    #    原来用 re.search 在全文档搜第一个 default=，会取到别的 ParamField 的值；
    #    查不到时还把「官方多数能力默认开」这种猜测写成了结论。
    #    现在查不到就**不写**该字段 —— 没明文不下结论。
    for name, actions in cmds.items():
        if "on" not in actions or "off" not in actions:
            continue  # 只有 on/off 成对的才算「开关」，否则只是普通命令
        sw: dict[str, Any] = {
            "on": actions.get("on", ""),
            "off": actions.get("off", ""),
            "status": actions.get("status", ""),
            "help": actions.get("help", ""),
        }
        m = re.search(
            r'path="[^"]*' + re.escape(name) + r'[^"]*"[^>]*?default="(true|false)"',
            body,
        )
        if m:
            sw["enabled_default"] = (m.group(1).lower() == "true")
        switches[name] = sw

    return switches

def _extract_memory_knobs(body: str) -> list[dict[str, Any]]:
    """从 memory 配置文档（如 memory-config.md）正文提取「可操作 knob」。

    官方 memory-config.md 的稳定形态：
      1) 配置路径在正文明文写出，且常带明确声明动词：
         「configured under `X`」「All under `X`」「live under `X`」
         「defaults use `X`」—— 这些是可靠锚点
      2) knob 用 markdown 表格声明：| Key | Type | Default | Description |

    路径归属：优先匹配「under/use 等声明词 + 反引号路径」；无声明词时，
    退化为「最近一次看到的反引号路径」。不硬编 knob 名、不硬编路径。
    """
    knobs: list[dict[str, Any]] = []

    # 路径覆盖声明：只认明确的节声明（configured under / All under），
    # 不认 Note 里顺带提的 live under（那会污染 baseline）
    _decl_re = re.compile(
        r"(?:configured|all)\s+(?:under\s+)?`([a-z][\w.-]*(?:\.[\w.-]+)+)`",
        re.I,
    )
    # 文档级 baseline：defaults use + 反引号路径（只认带点的，跳过顶层 memory）
    _base_re = re.compile(
        r"defaults?\s+use\s+`([a-z][\w.-]*(?:\.[\w.-]+)+)`",
        re.I,
    )

    # 文档级 baseline 路径（如 memory-config.md 开头 "Search defaults use memory.search"）
    # 只扫前 60 行，且要求带点（排除顶层 `memory`）
    current_path = ""
    for line in body.splitlines()[:60]:
        m = _base_re.search(line.strip())
        if m:
            current_path = m.group(1)
            break

    in_table = False

    for line in body.splitlines():
        s = line.strip()

        # 明确的路径覆盖声明 → 覆盖当前路径
        m = _decl_re.search(s)
        if m:
            current_path = m.group(1)

        # 表头行
        if s.startswith("|") and "Key" in s and ("Type" in s or "Default" in s):
            in_table = True
            continue

        # 分隔行 |---|---|
        if s.startswith("|") and re.match(r"^\|[\s\-|]+\|$", s):
            continue

        # 表格数据行
        if in_table and s.startswith("|"):
            cells = [c.strip() for c in s.split("|")[1:-1]]
            if len(cells) >= 2:
                key = cells[0].strip("`").strip()
                if key and " " not in key and not key.startswith("-"):
                    typ = cells[1].strip("`").strip() if len(cells) > 1 else ""
                    default = cells[2].strip("`").strip() if len(cells) > 2 else ""
                    knobs.append({
                        "key": key,
                        "type": typ,
                        "default": default,
                        "path": current_path,
                    })
        else:
            in_table = False

    return knobs

def _extract_memory_tiers(body: str) -> list[dict[str, Any]]:
    """从 memory 架构文档（如 memory-architecture.md）正文提取「五层 tier」。

    官方 memory-architecture.md 的稳定形态（## The tier model 段）：
      | Tier | Surface | Written by | Injected |
      | Instructions | `AGENTS.md` and workspace instruction files | Human only | Always |
      | Curated core | `MEMORY.md`, `USER.md` | Dreaming consolidation | Always |
      ...

    提取 Tier 名 + Surface 列里反引号包裹的文件名。不硬编 tier 名/文件名。
    """
    tiers: list[dict[str, Any]] = []
    in_table = False
    for line in body.splitlines():
        s = line.strip()
        # 表头（含 Tier 和 Surface）
        if s.startswith("|") and "Tier" in s and "Surface" in s:
            in_table = True
            continue
        if s.startswith("|") and re.match(r"^\|[\s\-|]+\|$", s):
            continue
        if in_table and s.startswith("|"):
            cells = [c.strip() for c in s.split("|")[1:-1]]
            if len(cells) >= 2:
                tier_name = cells[0].strip()
                surface = cells[1]
                # 提取反引号文件名
                files = re.findall(r"`([\w.-]+\.md)`", surface)
                if tier_name and tier_name not in ("Tier",):
                    tiers.append({"tier": tier_name, "files": files})
        else:
            in_table = False
    return tiers

def _extract_memory_providers(body: str) -> list[dict[str, Any]]:
    """从 memory 搜索文档（如 memory-search.md）正文提取「embedding provider 清单」。

    官方 memory-search.md 的稳定形态（## Supported providers 段）：
      | Provider | ID | Needs API key | Notes |
      | OpenAI   | `openai` | Yes | Default |
      | Ollama   | `ollama` | No  | Local/self-hosted server |

    提取 ID（反引号）+ Needs API key + Notes（依赖信号词，如 self-hosted / auto-download /
    credential chain / subscription）。不硬编 provider 名，也不硬编「依赖类型」——
    依赖类型由 Notes 信号词在消费端判定。
    """
    providers: list[dict[str, Any]] = []
    in_table = False
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("|") and "Provider" in s and "ID" in s and "API key" in s:
            in_table = True
            continue
        if s.startswith("|") and re.match(r"^\|[\s\-|]+\|$", s):
            continue
        if in_table and s.startswith("|"):
            cells = [c.strip() for c in s.split("|")[1:-1]]
            if len(cells) >= 3:
                pid = ""
                m = re.search(r"`([\w.-]+)`", cells[1])
                if m:
                    pid = m.group(1)
                needs_key = cells[2].strip()
                notes = cells[3].strip() if len(cells) > 3 else ""
                if pid:
                    providers.append({
                        "id": pid,
                        "needs_key": needs_key.lower(),
                        "notes": notes,
                    })
        else:
            in_table = False
    return providers

def spider_memory(slug: str, body: str, source_url: str) -> dict[str, Any]:
    fm = parse_frontmatter(body)
    entry = {
        "id": slug,
        "source_url": source_url,
        "section_name": fm.get("title", slug),
        "summary": fm.get("summary", ""),
        "type": "memory",
    }
    # 能力开关（如 dreaming）：从正文长出来，不硬编开关名
    switches = _extract_memory_switches(body)
    if switches:
        entry["switches"] = switches
    # 可操作 knob（如 memory.search.enabled / dreaming.frequency）：从表格长出来
    knobs = _extract_memory_knobs(body)
    if knobs:
        entry["knobs"] = knobs
    # 五层 tier（如 Instructions/Curated core）：从 tier 表长出来
    tiers = _extract_memory_tiers(body)
    if tiers:
        entry["tiers"] = tiers
    # embedding provider 清单（如 openai/gemini/ollama）：从 provider 表长出来
    providers = _extract_memory_providers(body)
    if providers:
        entry["providers"] = providers
    return entry

def spider_automation(slug: str, body: str, source_url: str) -> dict[str, Any]:
    fm = parse_frontmatter(body)
    return {
        "id": slug,
        "source_url": source_url,
        "section_name": fm.get("title", slug),
        "summary": fm.get("summary", ""),
        "type": "automation",
    }

def _parse_session_contract(body: str) -> dict[str, Any] | None:
    """解析 web/urls.md 的 session URL 契约（namespace / reserved 字 / 转义标记 / 路由表）。

    数据从官方文档长出来（不是硬编）：
      - namespace：`<namespace>` is either `/chat` or `/dashboard`
      - reserved 字：The reserved single-segment literal rest names are `main`...
      - 转义标记：`~dot` / `~dotdot` / `~key`
      - 路由表：| Page | Canonical path | Aliases | Parameters | 四列表格

    解析失败返回 None，调用方据此不写 session_contract 字段。
    """
    # 1. namespace：`<namespace>` is either /chat or /dashboard（/xxx 是裸路径，不在反引号里）
    namespaces: list[str] = []
    m_ns = re.search(r"<namespace>`\s*is\s*either\s+(.+?)\.", body)
    if m_ns:
        namespaces = re.findall(r"/([a-z-]+)", m_ns.group(1))

    # 2. reserved 单段字：`main`, `global`, `boot`, `sessions`（跨行，到句号止）
    reserved_rest: list[str] = []
    m_res = re.search(
        r"reserved single-segment literal rest names are\s+(.+?)\.",
        body, re.DOTALL,
    )
    if m_res:
        reserved_rest = re.findall(r"`([a-z-]+)`", m_res.group(1))

    # 3. 转义标记：~dot / ~dotdot / ~key（正文里以 `~xxx` 形式出现）
    escape_markers: list[str] = []
    for mark in re.findall(r"`(~[a-z]+)`", body):
        if mark not in escape_markers:
            escape_markers.append(mark)

    # 4. 路由表：| Page | Canonical path | Aliases | Parameters | 四列表格
    routes: list[dict[str, Any]] = []
    in_route_table = False
    for line in body.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            if in_route_table:
                break  # 路由表结束
            continue
        cells = [c.strip() for c in s.split("|")[1:-1]]
        if not cells or len(cells) < 2:
            continue
        # 表头 / 分隔行
        joined = "|".join(c.lower() for c in cells)
        if "canonical path" in joined or "page" in joined or "---" in joined or ":-" in joined:
            in_route_table = True
            continue
        if not in_route_table:
            continue
        # 四列：Page | Canonical path | Aliases | Parameters（可能缺列）
        page = cells[0] if len(cells) > 0 else ""
        path = cells[1] if len(cells) > 1 else ""
        aliases = cells[2] if len(cells) > 2 else ""
        params = cells[3] if len(cells) > 3 else ""
        # 剥掉 path/aliases 的反引号
        path = path.strip("`")
        aliases = [a.strip("`").strip() for a in aliases.split(",") if a.strip() and a.strip() != "-"]
        if not path:
            continue
        routes.append({
            "page": page,
            "path": path,
            "aliases": aliases,
            "params": params.strip(),
        })

    if not namespaces and not reserved_rest and not routes:
        return None

    return {
        "namespaces": namespaces,
        "reserved_rest": reserved_rest,
        "escape_markers": escape_markers,
        "routes": routes,
    }

def spider_web_search(slug: str, body: str, source_url: str) -> dict[str, Any]:
    fm = parse_frontmatter(body)
    entry = {
        "id": slug,
        "source_url": source_url,
        "section_name": fm.get("title", slug),
        "summary": fm.get("summary", ""),
        "type": "web_search",
    }
    # urls.md 额外解析 session URL 契约，长成 mani 数据（去硬编：namespace/reserved/路由）
    if slug == "urls":
        contract = _parse_session_contract(body)
        if contract:
            entry["session_contract"] = contract
    return entry

def spider_tool(slug: str, body: str, source_url: str) -> dict[str, Any]:
    fm = parse_frontmatter(body)
    return {
        "id": slug,
        "source_url": source_url,
        "section_name": fm.get("title", slug),
        "summary": fm.get("summary", ""),
        "type": "tool",
    }

# ═══════════════════════════════════════════════════════════════
#  CLI 方法提取 —— 第 9 类消费端 clis
# ═══════════════════════════════════════════════════════════════
# 提取「怎么做」：官方 CLI 文档里声明的命令用法 + 配置落点。
# 这里是**逻辑**（提取规律），活在代码里。forge.json 只放控件样式和范式声明，
# 不放逻辑——官方 CLI 文档换了书写形态，改这里的常量即可。

_CLI_GRAMMAR: dict[str, Any] = {
    "usage_prefix": "openclaw",
    "effect_verbs_infix": ["writes", "sets", "updates", "stores", "persists"],
    "effect_verbs_leading": ["Adds/updates", "Writes", "Sets", "Updates", "Stores"],
    "comment_cutters": ["#", "|"],
}
_CONFIG_PATH_PAT = re.compile(r"^[a-z][\w.\-]*(\.[\w.\-\*<>]+)+", re.I)

def _cli_grammar() -> dict[str, Any]:
    """CLI 方法的书写规律（提取逻辑，代码常量）。"""
    return _CLI_GRAMMAR

def _cli_patterns() -> tuple[re.Pattern, re.Pattern, re.Pattern]:
    """按字典里的规律现场构造提取式（用法行 / 中缀动词 / 前置动词）。"""
    g = _cli_grammar()
    prefix = re.escape(str(g.get("usage_prefix") or "openclaw"))
    infix = [re.escape(str(v)) for v in (g.get("effect_verbs_infix") or []) if v]
    leading = [re.escape(str(v)) for v in (g.get("effect_verbs_leading") or []) if v]
    usage = re.compile(rf"^{prefix}\s+([a-z][\w-]*)\s+(.+?)\s*$", re.MULTILINE)
    infix_pat = re.compile(
        r"`([^`\n]{1,60})`\s+(?:" + "|".join(infix or ["writes"]) + r")\s+`([^`\n]{1,90})`"
    )
    leading_pat = re.compile(
        r"(?:" + "|".join(leading or ["Writes"]) + r")\s+`([^`\n]{1,90})`"
    )
    return (usage, infix_pat, leading_pat)

def _extract_options_table(body: str, slug: str) -> dict[str, list[str]]:
    """从官方 CLI 文档的 Options 表格抓 flag，按子命令节归属。

    官方结构（cli/models.md）：
        ### Status
        Options:
        | `--probe` | ... |          ← 这些 flag 属于 status 子命令
        ### List
        Options: `--all` ...          ← 行内形态，属于 list

    flag 必须归属到「离它最近的 ### 节标题」对应的 action，不能整页全局挂。
    返回 {action: [flags]}。action 从节标题归一（Status→status、Set default→set）。
    """
    result: dict[str, list[str]] = {}
    current_action = ""
    in_table = False

    def _normalize_action(title: str) -> str:
        t = title.strip().lower()
        return t.split()[0] if t else ""

    for line in body.splitlines():
        s = line.strip()
        m_head = re.match(r"^###\s+(.+)$", s)
        if m_head:
            current_action = _normalize_action(m_head.group(1))
            in_table = False
            continue
        m_head2 = re.match(r"^##\s+", s)
        if m_head2:
            current_action = ""
            in_table = False
            continue
        m_inline = re.match(r"^Options:\s*(.+)$", s, re.I)
        if m_inline:
            for f in re.findall(r"`(--[\w-]+)", m_inline.group(1)):
                if current_action:
                    result.setdefault(current_action, [])
                    if f not in result[current_action]:
                        result[current_action].append(f)
            continue
        if s.lower() in ("options:", "options"):
            in_table = True
            continue
        if in_table:
            if not s.startswith("|"):
                if not s:
                    continue
                in_table = False
                continue
            cells = [c.strip() for c in s.split("|")[1:-1]]
            if not cells:
                continue
            first = cells[0]
            for m in re.finditer(r"`(--[\w-]+)", first):
                f = m.group(1)
                if current_action:
                    result.setdefault(current_action, [])
                    if f not in result[current_action]:
                        result[current_action].append(f)
    return result

def _parse_cli_usage(rest: str) -> tuple[list[str], list[str], list[str]]:
    """把命令行剩余部分拆成 (动作链, 位置参数, 选项)。

    判定形态从字典 token_forms 读，不写死：
      optional_block [...]  整块摘——否则 `[--marketplace <source>]` 里的 `<source>]`
                          会漏成位置参数
      flag --name          选项
      positional <...>     位置参数
      action 裸词          动作（位于位置参数与选项之前）
    """
    g = _cli_grammar()
    for cutter in (g.get("comment_cutters") or ["#", "|"]):
        rest = re.split(r"\s+" + re.escape(str(cutter)), rest, 1)[0]

    opts: list[str] = []
    args: list[str] = []
    actions: list[str] = []
    # 可选段整块处理，避免闭合括号跟着参数漏出去
    for block in re.findall(r"\[([^\]]*)\]", rest):
        for o in re.findall(r"--[\w-]+", block):
            if o not in opts:
                opts.append(o)
    rest = re.sub(r"\[[^\]]*\]", " ", rest)

    for tok in rest.split():
        t = tok.strip()
        if not t:
            continue
        if t.startswith("-"):
            for o in re.findall(r"--[\w-]+", t):
                if o not in opts:
                    opts.append(o)
        elif t.startswith("<"):
            if t not in args:
                args.append(t)
        elif re.fullmatch(r"[a-z][\w-]*", t) and not args and not opts:
            actions.append(t)
    return (actions, args, opts)

def spider_cli(slug: str, body: str, source_url: str) -> dict[str, Any]:
    """从 CLI 文档提取「有哪些操作方法、各自怎么调、落到哪个配置路径」。

    两类信号，都取自官方文档的稳定书写形态：
      1. 用法清单 —— 行首命令行
             openclaw plugins install <path-or-spec> [--link] [--force]
             openclaw models auth paste-api-key --provider <id>
      2. 配置落点 —— 正文里的 writes/sets/updates 表述
             `set` writes `agents.defaults.model.primary`

    只收「值确实长得像配置路径」的（含点分段），避免把示例 JSON、报错文案
    当成配置路径——这是反复踩过的「扫正文误吞」坑。
    """
    fm = parse_frontmatter(body)
    usage_pat, infix_pat, leading_pat = _cli_patterns()

    # ── 用法清单 ──
    # 官方同一个命令会分多行展示不同用法（restart / restart --safe / restart --force），
    # 按动作合并：位置参数取并集、选项取并集，信息才最全。
    by_action: dict[str, dict[str, Any]] = {}
    for cmd_word, rest in usage_pat.findall(body):
        if cmd_word != slug:
            continue
        actions, args, opts = _parse_cli_usage(rest)
        if not actions:
            continue
        key = " ".join(actions)
        u = by_action.setdefault(key, {"action": key, "args": [], "options": []})
        for a in args:
            if a not in u["args"]:
                u["args"].append(a)
        for o in opts:
            if o not in u["options"]:
                u["options"].append(o)

    usages: list[dict[str, Any]] = []
    for key in sorted(by_action):
        u = by_action[key]
        item: dict[str, Any] = {"action": u["action"]}
        if u["args"]:
            item["args"] = u["args"]
        if u["options"]:
            item["options"] = sorted(u["options"])
        usages.append(item)

    # ── Options 表格补抓：官方把 flag 写在表格里，按子命令节归属 ──
    options_by_action = _extract_options_table(body, slug)
    if options_by_action:
        for act, flgs in options_by_action.items():
            # 归属到对应 action 的 options（action 名可能带空格如 "set default"，匹配 usage 的 action）
            for u in usages:
                if (u.get("action") or "").split()[0] == act:
                    u.setdefault("options", [])
                    for f in flgs:
                        if f not in u["options"]:
                            u["options"].append(f)
                    u["options"] = sorted(u["options"])
                    break

    # ── 配置落点 ──
    methods: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(action: str, target: str) -> None:
        target = target.strip()
        if not _CONFIG_PATH_PAT.match(target):
            return
        path = re.split(r"\s*[=:]\s*", target, 1)[0].strip()
        if not path:
            return
        key = (action.strip(), path)
        if key in seen:
            return
        seen.add(key)
        methods.append({"action": action.strip(), "writes": path})

    for action, target in infix_pat.findall(body):
        _add(action, target)
    for target in leading_pat.findall(body):
        _add("", target)
    methods.sort(key=lambda m: (m["writes"], m["action"]))

    return {
        "id": slug,
        "source_url": source_url,
        "section_name": fm.get("title", slug),
        "summary": fm.get("summary", ""),
        "type": "cli",
        "command": f"openclaw {slug}",
        "usages": usages,
        "methods": methods,
    }

# ═══════════════════════════════════════════════════════════════
#  统一入口
# ═══════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════
# ═══ 机制面：OpenClaw 的机制（插件赖以生存的那一层） — 并入 kernel/filter.py ═══
# ══════════════════════════════════════════════════════════════
# 插件不是"装上去就行"：它挂在官方的**钩子机制**上 —— 哪些钩子存在、是观察还是改写、
# 每钩子超时预算多少、session_end 的 reason 值域、收尾排空预算几秒。这些是官方文档里的
# **权威面**（plugins/hooks、plugins/hooks/reference、plugins/sdk-overview/events-and-hooks）。
#
# 本段把它们分拣成机制条目，随每轮 scout 采集更新；于是「**机制变化**」可以被差分出来，
# 再拿去匹配我们对插件的设计（需求），合成一份**完整插件声明**交给 large 长消费端。
# 钩子名一张也不硬编 —— 硬编了，官方改了机制我们不会知道，那正是要防的事。

#: 钩子名形态：`message_received` / `session_end` / `command:new`
_HOOK_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(?::[a-z][a-z0-9_]*)?$")
def _slug(text: str, max_len: int = 64) -> str:
    """把官方一句话归一成**我们自己的标签**（不从手写表里查）。

    官方新措辞（新性质 / 新失败策略 / 新任务 / 新分组）当场生成稳定标签，并进候选池；
    转正后进正式词典。不做关键词映射表：表写死就长不出新词。
    """
    s = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "_", (text or "").strip().lower())
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:max_len]


def mechanism_label(bucket: str, raw: str) -> str:
    """取某个原始措辞的标签：**词典优先（可人工纠正）**，没有就现场 slug 长一个。

    词典落点：`forge.json` 的 `ambiguity_dict.mechanism_terms[bucket][raw] = label`。
    候选池（`ambiguity_candidates.json` 的 `mechanism_terms`）攒够阈值转正。
    """
    r = (raw or "").strip()
    if not r:
        return ""
    try:
        known = (_lex_load_ambiguity().get("mechanism_terms") or {}).get(bucket) or {}
        if r in known:
            return str(known[r])
    except Exception:                                               # noqa: BLE001
        pass
    return _slug(r)


def observe_mechanism_term(bucket: str, raw: str) -> str:
    """规则一：观察一个官方原始措辞（性质/失败策略/任务/分组）→ 候选池 + 返回标签。"""
    r = (raw or "").strip()
    if not r:
        return ""
    label = mechanism_label(bucket, r)
    try:
        known = (_lex_load_ambiguity().get("mechanism_terms") or {}).get(bucket) or {}
        if r in known:
            return label                       # 已入正式词典：不再刷候选池
        c = _load_candidates()
        pool = c.setdefault("mechanism_terms", {}).setdefault(bucket, {})
        rec = pool.get(r) if isinstance(pool.get(r), dict) else {}
        pool[r] = {"label": label, "hits": int(rec.get("hits", 0)) + 1}
        _save_candidates(c)
    except Exception:                                               # noqa: BLE001
        pass
    return label


#: 性质 → 允许承担的作用。这不是"官方措辞映射表"，是**我们的语义判定**：
#: 看标签里有没有 observe/modify/gate/claim 这些词（中英都认），
#: 官方换个说法只要含这几个词根，照样判对；判不出就不给（不许瞎允许）。
def _allows_from(kind_label: str) -> list[str]:
    k = (kind_label or "").lower()
    out: list[str] = []
    if "observe" in k or "观察" in k:
        out.append("observe")
    if "modify" in k or "改写" in k:
        out.append("modify")
    if "gate" in k or "拦截" in k:
        out.append("gate")
    if "claim" in k or "认领" in k:
        out.append("claim")
    if "evaluate" in k or "评估" in k:
        out.append("evaluate")
    return out


#: 官方原文留档（本机运行期面，**不进包**）：这是"长"的原料，程序要用它，
#: 但随包发给客户的东西里不许出现官方文档的文字。
_MECH_RAW_PATH = os.path.join(_BASE_DIR, "kernel", "large", "mechanisms_raw.json")

#: 本轮 crawl 攒下的机制原文（crawl 收尾统一落盘；模块级是接口约束下的务实选择：
#: spider 的签名统一为 (slug, body, url) -> entry，不改接口就靠这个口子带原文出来）
_MECH_RAW_ACC: list[dict] = []


def _save_mechanisms_raw(entries: list[dict]) -> None:
    """把机制面的**原始文本**落成本机运行期文件（`mechanisms_raw.json`）。

    为什么单独立一份：清单/声明里只能放我们自己的结构（钩子名/标签/预算/出处），
    但"生长"需要原文——官方加了新性质、新任务、新措辞，先从这儿被观察到，
    再经候选池转正进词典。落点随 Excludes 排除，不进安装包。
    """
    if not entries:
        return
    try:
        import datetime as _dt
        data = {"updated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                "pages": entries}
        with open(_MECH_RAW_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception as e:                                          # noqa: BLE001
        logger.warning("filter._saveMechanismsRaw: 写运行期原文失败 %s", e)
#: 预算列形态（"15 seconds" / "2 seconds"）
_BUDGET_RE = re.compile(r"^\d+\s*(?:seconds?|s|秒|毫秒)", re.IGNORECASE)


def _extract_hook_mechanism(body: str) -> dict[str, Any]:
    """从钩子机制页正文抽**反推声明格式所需的全部官方事实**：

      · hooks          —— 钩子名 → 性质（Observe/Modify/Gate/Claim/Evaluate）+ 所属**分组**
                          （按小节标题：Message and delivery / Prompt and session / …）
      · budgets        —— 每钩子异步超时预算
      · failures       —— 每钩子的**失败策略**（fail closed / log and skip / log and continue）
      · chooser        —— 官方「Choose a hook」表：**任务 → 推荐钩子**（作用→钩子的官方映射，
                          需求反推就靠这张表，不靠我们自己编）
      · perm_gate      —— 权限门清单（哪些钩子要先开 allowConversationAccess / allowPromptInjection）
      · emit_caveat    —— 官方原话：钩子表是注册接口，不保证每个运行时都发每个钩子
      · session_end 的 reason 值域与收尾排空预算

    抽不到就返回空 —— 调用方据此**不写**机制条目（宁可没有机制结论，也不编一张假表）。
    """
    hooks: dict[str, dict] = {}
    budgets: dict[str, str] = {}
    failures: dict[str, str] = {}
    chooser: list[dict] = []
    raw_desc: dict[str, str] = {}
    raw_caveat: dict[str, str] = {}
    raw_terms: dict[str, str] = {}
    group = ""
    in_chooser = False
    for line in (body or "").splitlines():
        s = line.strip()
        mh = re.match(r"^#{2,4}\s+(.+?)\s*$", s)
        if mh:
            group = mh.group(1)
            in_chooser = group.lower().startswith("choose a hook")
            continue
        if not s.startswith("|"):
            if s:
                in_chooser = False
            continue
        cells = [c.strip() for c in s.split("|")[1:-1]]
        if len(cells) < 2:
            continue
        # ① 官方任务→钩子推荐表：任务**现场长标签**（官方加新任务，这里自动长出新的）
        if in_chooser:
            hk = [h for h in re.findall(r"`([^`]+)`", cells[1]) if _HOOK_NAME_RE.match(h)]
            if hk and cells[0] and not cells[0].lower().startswith(("task", "---")):
                task_label = observe_mechanism_term("task", cells[0])
                chooser.append({"task": task_label, "hook": hk[0]})
                raw_terms[f"task:{task_label}"] = cells[0]
            continue
        names = [n for n in re.findall(r"`([^`]+)`", cells[0]) if _HOOK_NAME_RE.match(n)]
        if not names:
            continue
        second = cells[1]
        third = cells[2] if len(cells) > 2 else ""
        # ② 预算 + 失败策略表（失败策略现场长标签；原句进原文留档）
        if _BUDGET_RE.match(second):
            for n in names:
                budgets.setdefault(n, second)
                if third and not third.lower().startswith(("hook", "---")):
                    fl = observe_mechanism_term("failure", third)
                    if fl:
                        failures.setdefault(n, fl)
                        raw_terms[f"failure:{fl}"] = third
            continue
        # ③ 钩子目录表：性质/分组现场长标签；描述原句只进**原文留档**（不进包）
        #    ⚠ 护栏（2026-09-21 实测）：同一页上还有"注册选项表"（`matcher` / `priority` /
        #    `timeoutMs` …），它们的第二列是**长说明句**，不设护栏就会被当成钩子登记进来
        #    （实测多出 10 条假钩子）。判据：要么这一列含语义词根（observe/modify/… ），
        #    要么它是**短词**（≤24 字且 ≤3 个词，如 "Archive before" 这种新写法）。
        kl = observe_mechanism_term("kind", second)
        _looks_like_kind = bool(_allows_from(kl)) or (len(second) <= 24
                                                     and len(second.split()) <= 3)
        if kl and _looks_like_kind:
            for n in names:
                hooks.setdefault(n, {"kind": kl, "group": observe_mechanism_term("group", group)})
                raw_terms.setdefault(f"kind:{kl}", second)
                raw_terms.setdefault(f"group:{hooks[n]['group']}", group)
                if third:
                    raw_desc.setdefault(n, third)
    # ④ 权限门（写在正文里，不是表格）：整段摊平后按句子抓
    flat = re.sub(r"\s+", " ", body or "")
    perm: dict[str, list[str]] = {}
    for key, pat in (
        ("allowConversationAccess",
         r"allowConversationAccess`?\s*:?\s*true`?\s*for\s+(.{0,600}?)\.\s"),
        ("allowPromptInjection",
         r"allowPromptInjection`?\s*:?\s*false`?\s*blocks\s+(.{0,300}?)\.\s"),
    ):
        m = re.search(pat, flat)
        if m:
            perm[key] = [n for n in re.findall(r"`([a-z_]+)`", m.group(1))
                         if _HOOK_NAME_RE.match(n)]
    # 官方那条总告警（"目录是注册接口，不保证每个运行时都发每个钩子"）只留**一个标记**：
    # 随包内容不抄官方文档原句；出处看 source_url。
    emit_caveat = bool(re.search(r"not a promise that every runtime emits", flat))
    caveat_text = ""
    if emit_caveat:
        m_cav = re.search(r"(The catalog is the registration API.{0,200}?hook\.)", flat)
        caveat_text = m_cav.group(1) if m_cav else ""
    # ⑤ 派发告警：某个钩子"不一定发得出来" → 现场长标签；原句进**原文留档**
    caveats: dict[str, str] = {}
    _hook_names_seen = 0
    for sent in re.split(r"(?<=[.。])\s+", flat):
        names = [n for n in re.findall(r"`([a-z_]+)`", sent) if _HOOK_NAME_RE.match(n)]
        if not names:
            continue
        _hook_names_seen += len(names)
        low = sent.lower()
        if any(w in low for w in ("when the adapter opts in", "not a promise",
                                  "does not emit", "not dispatched", "only when",
                                  "opt in", "may be absent")):
            lab = observe_mechanism_term("dispatch", sent)
            # 一句里点到的钩子**全都**记告警（原来只取前 3 个，第 4 个起静默丢）
            if lab:
                for n in names:
                    caveats.setdefault(n, lab)
                    raw_caveat.setdefault(n, sent.strip()[:600])
    if _hook_names_seen and not caveats:
        # 上面那串是硬编英文措辞：官方改词就再也命中不了 ⇒ 至少不许静默
        logger.warning("[STAKE-MECH] 钩子页出现 %d 个钩子名但没命中任何派发告警措辞"
                       "（官方措辞可能已变，需核对）", _hook_names_seen)
    reasons: list[str] = []
    m = re.search(r"session_end\.reason\D{0,40}?(?:one of|其中之一)(.{0,400})",
                  body or "", re.DOTALL | re.IGNORECASE)
    if m:
        for r in re.findall(r"`([a-z_]+)`", m.group(1)):
            # 匹配段里会带进 `session_end.reason` 自己那个词，它不是 reason 值域的一员
            if r not in reasons and r != "session_end":
                reasons.append(r)
    drain = ""
    dm = re.search(r"(\d+)[- ]second total `session_end` drain budget", body or "")
    if dm:
        drain = f"{dm.group(1)} second total"
    return {"hooks": hooks, "budgets": budgets, "failures": failures, "chooser": chooser,
            "perm_gate": perm, "emit_caveat": emit_caveat, "dispatch_caveats": caveats,
            "session_end_reasons": reasons, "session_end_drain": drain,
            # ↓ 原文留档（**不进包**）：只写运行期文件，供"生长"用
            "_raw": {"descs": raw_desc, "caveats": raw_caveat, "terms": raw_terms,
                     "emit_caveat": caveat_text}}


def spider_mechanism(slug: str, body: str, source_url: str) -> dict[str, Any]:
    """机制条目：官方机制（钩子名/性质/预算/reason 值域）+ 官方出处。

    抽不到任何机制事实 → 返回空 dict（调用方跳过），不产空壳条目污染声明。
    """
    mech = _extract_hook_mechanism(body)
    if not any(mech.values()):
        return {}
    fm = parse_frontmatter(body)
    entry: dict[str, Any] = {
        "id": slug,
        "source_url": source_url,
        "section_name": fm.get("title", slug),
        "type": "mechanism",
    }
    entry.update({k: v for k, v in mech.items() if not k.startswith("_")})
    # 原文留档：攒起来，crawl 收尾时统一写运行期文件（**不进包**）
    raw = mech.get("_raw") or {}
    if any(raw.values()):
        _MECH_RAW_ACC.append({
            "id": slug,
            "source_url": source_url,
            "section_name": entry.get("section_name", slug),
            **raw,
        })
    return entry


def mechanism_facts(manifest: dict) -> dict[str, Any]:
    """把机制条目并成一张**事实表**：钩子名 → 性质/预算/官方出处。

    形参收**manifest 顶层**（`{"mechanisms": [...]}`），既接受采集态的 entries 形态
    （`{"mechanisms": [...]}` 也行），也接受已落盘的 mani。
    多页合并时同名钩子取**首个**（页序固定 ⇒ 确定性），预算与 reason 值域取并集。
    """
    src = manifest.get("mechanisms") if isinstance(manifest, dict) else None
    if src is None and isinstance(manifest, dict):
        src = (manifest.get("entries") or {}).get("mechanisms")
    hooks: dict[str, dict] = {}
    budgets: dict[str, str] = {}
    failures: dict[str, str] = {}
    page_of: dict[str, str] = {}
    caveats: dict[str, str] = {}
    emit_caveat = False
    chooser: list[dict] = []
    perm: dict[str, list[str]] = {}
    caveat = ""
    reasons: list[str] = []
    drain = ""
    for m in (src or []):
        if not isinstance(m, dict):
            continue
        url = str(m.get("source_url") or "")
        for name, info in (m.get("hooks") or {}).items():
            page_of.setdefault(name, url)
            if name not in hooks and isinstance(info, dict):
                hooks[name] = {"kind": str(info.get("kind") or ""),
                               "desc": str(info.get("desc") or ""),
                               "group": str(info.get("group") or ""),
                               "source_url": url}
        for name, b in (m.get("budgets") or {}).items():
            page_of.setdefault(name, url)
            budgets.setdefault(name, str(b))
        for name, f in (m.get("failures") or {}).items():
            page_of.setdefault(name, url)
            failures.setdefault(name, str(f))
        seen_task = {c.get("task") for c in chooser}
        for c in (m.get("chooser") or []):
            if isinstance(c, dict) and c.get("task") and c.get("task") not in seen_task:
                chooser.append({"task": str(c["task"]), "hook": str(c.get("hook") or "")})
                seen_task.add(c["task"])
        for k, names in (m.get("perm_gate") or {}).items():
            cur = perm.setdefault(k, [])
            for n in (names or []):
                if n not in cur:
                    cur.append(n)
        if not emit_caveat and m.get("emit_caveat"):
            emit_caveat = True
        for n, c in (m.get("dispatch_caveats") or {}).items():
            page_of.setdefault(n, url)
            caveats.setdefault(n, str(c))
        for r in (m.get("session_end_reasons") or []):
            if r not in reasons:
                reasons.append(r)
        if not drain and m.get("session_end_drain"):
            drain = str(m["session_end_drain"])
    return {"hooks": hooks, "budgets": budgets, "failures": failures,
            "chooser": chooser, "perm_gate": perm, "emit_caveat": emit_caveat,
            "dispatch_caveats": caveats,
            "source_of": page_of,
            "session_end_reasons": reasons, "session_end_drain": drain}


def derive_declaration_schema(facts: dict | None = None) -> dict[str, Any]:
    """**反推声明格式**：一份插件声明该有哪些字段、每个字段的取值域从哪来。

   一律从官方机制面反推，不人定：
     · 挂什么钩子 —— 取值域 = 官方钩子目录里的名字（33 个那种），别的一个都不许写；
     · 能干什么   —— 由钩子**性质**决定：Observe 只能看/触发；Modify·Gate·Claim 才能改内容、
                     取消发送、拦回合。所以"作用"能不能落在某个钩子上，是官方定的；
     · 怎么落     —— 官方「Choose a hook」那张**任务→钩子**表就是映射表（改写出话→message_sending、
                     回合末冲刷状态→agent_end/gateway_stop…），需求照它反推；
     · 多久必须回 —— 每钩子异步预算 + **失败策略**（fail closed / log and skip / log and continue），
                     这是声明里必须承担的时限承诺；
     · 要不要先开权限 —— `allowConversationAccess` / `allowPromptInjection` 门清单；
     · 哪些不可靠 —— 官方原话：钩子表是注册接口，**不保证每个运行时都发每个钩子**。

    产出即"声明格式"（数据，不是代码判断）：字段 + 取值域 + 每条取值域的官方出处。
    """
    f = facts or {}
    hooks = f.get("hooks") or {}
    budgets, failures = f.get("budgets") or {}, f.get("failures") or {}
    perm = f.get("perm_gate") or {}

    def _allows(kind: str) -> list[str]:
        return _allows_from(kind)

    hook_fields = {}
    source_of = (facts or {}).get("source_of") or {}
    dispatch_caveats = (facts or {}).get("dispatch_caveats") or {}
    # 取值域 = 目录里的钩子 ∪ 只在预算/失败策略表里出现的钩子。
    # 后者性质未知时**不许假设**：allows 空 = 不能拿它承担任何作用（落位时会被 gaps 抓出来），
    # 但它的预算与失败策略照样可见（那是官方给的硬约束）。
    all_names = list(hooks.keys())
    for n in list(budgets.keys()) + list(failures.keys()):
        if n not in all_names:
            all_names.append(n)
    for name in all_names:
        info = hooks.get(name) or {}
        hook_fields[name] = {
            "kind": info.get("kind", ""),
            "group": info.get("group", ""),
            "allows": _allows(info.get("kind", "")) if info.get("kind") else [],
            "budget": budgets.get(name, ""),
            "failure_policy": failures.get(name, ""),
            "needs_perm": [k for k, names in perm.items() if name in (names or [])],
            "dispatch_caveat": dispatch_caveats.get(name, ""),
            "source_url": info.get("source_url") or source_of.get(name, ""),
        }
    return {
        "version": 1,
        "derived_from": "scout+spider 分拣的官方机制面（plugins/hooks、plugins/hooks/reference）",
        "fields": {
            "id": "插件 id（本地插件就是我们自己起的；官方插件用官方 id）",
            "hooks": "要挂的官方钩子（取值域=hook 目录；每个钩子带作用与承诺）",
            "lifecycle": "生命周期（生命线=critical，不许被挤出）",
            "install": "装法（官方路由 install_route/package，或本地插件走 installer）",
            "scope": "语料去向（只进卡，不许进宿主池）",
        },
        "hooks": hook_fields,
        "hook_domain": sorted(hook_fields.keys()),
        "task_chooser": f.get("chooser") or [],
        "perm_gate": perm,
        "emit_caveat": f.get("emit_caveat") or "",
        "session_end_reasons": f.get("session_end_reasons") or [],
        "session_end_drain": f.get("session_end_drain") or "",
    }


def _compliant_hooks(hf: dict, needs: str, role: str = "") -> list:
    """官方钩子目录里**性质够用**的钩子，按该 role 该用的那个排前面。

    声明点名的钩子性质不合时，按 needs 换一条够用的顶上，并在 notes 留痕；
    排序按官方钩子目录顺序，不硬编偏好表。
    """
    # 钩子名一张也不硬编（本文件铁律）：次序直接取官方目录顺序（dict 保序 ⇒ 确定性），
    # 原来写死 ("agent_end","message_received","session_end","session_start") 偏好表，
    # 官方改名后这条偏好静默失效（退化成字典序），与自述冲突。
    return [k for k, v in (hf or {}).items()
            if isinstance(v, dict) and needs in (v.get("allows") or [])]


def derive_declaration(design: dict, schema: dict) -> dict[str, Any]:
    """**需求 → 声明**：把"我们要求这个插件起的作用"经官方格式反推成一份完整声明。

    需求侧每条作用声明它**要干什么**（`needs`: observe / modify / gate）+ 可选
    `prefer`（想挂哪个钩子）。这里按官方格式逐条落位：

      · 想要的钩子在官方目录里、且**性质够用** → 落位（带性质/预算/失败策略/权限/出处）；
      · 性质不够（让 Observe 去改内容）→ **显式不落位** + 说出官方给不了这能力；
      · 官方目录里没这个钩子 → 缺口；
      · 若官方「任务→钩子」表给了**推荐钩子**（task_chooser 命中），优先用官方的推荐，
        而不是我们自己挑的那个（官方说该这么做，就按官方来）。

    产出：`declaration.hooks[]`（官方名 + 作用 + 承诺）+ `declaration.gaps` + `declaration.notes`。
    """
    decl_hooks: list[dict] = []
    gaps: list[dict] = []
    notes: list[str] = []
    hf = schema.get("hooks") or {}
    chooser = schema.get("task_chooser") or []
    for h in (design.get("hooks") or []):
        if not isinstance(h, dict):
            continue
        want = str(h.get("id") or "")
        needs = str(h.get("needs") or "observe").lower()
        prefer = str(h.get("prefer") or want)
        target = prefer or want
        info = hf.get(target)
        # 官方任务→钩子推荐表命中：官方说该挂哪个就挂哪个。
        # 比对用**标签包含**（我们声明里的任务说法不必与官方一字不差）：
        # 官方加新任务时它自己就是新标签，声明里写个近义说法照样能对上。
        _dt = _slug(str(h.get("task") or ""))
        rec = None
        if _dt:
            rec = next((c for c in chooser
                        if c.get("task") and (_dt == c["task"] or _dt in c["task"]
                                              or c["task"] in _dt)), None)
        if rec and rec.get("hook") and rec["hook"] != target:
            notes.append(f"「{h.get('role') or want}」官方推荐钩子是 {rec['hook']}（任务：{rec['task']}），"
                         f"已按官方推荐落位，而不是我们自己挑的 {target}")
            target = rec["hook"]
            info = hf.get(target)
        if info is None:
            gaps.append({"role": h.get("role", ""), "want": want,
                         "blocking": True,
                         "why": "官方钩子目录里没有这个钩子名"})
            continue
        if needs not in (info.get("allows") or []):
            # 声明不许静默降级：先按 needs 找一条性质够用的钩子顶上并留痕；
            # 实在没有合规钩子才记 blocking gap（注入端会报 FAIL）。
            _cands = _compliant_hooks(hf, needs, str(h.get("role") or ""))
            if _cands:
                _sub = _cands[0]
                notes.append(f"「{h.get('role') or want}」声明的 {target} 官方给的性质是"
                             f"「{info.get('kind')}」⇒ 不允许 {needs}；"
                             f"按性质改挂 {_sub}（{hf[_sub].get('kind')}，允许"
                             f"{'/'.join(hf[_sub].get('allows') or [])}）")
                target = _sub
                info = hf[_sub]
            else:
                gaps.append({"role": h.get("role", ""), "want": target,
                             "blocking": True,
                             "why": (f"官方给这个钩子的性质是「{info.get('kind')}」⇒ 不允许 {needs}"
                                     f"（允许：{'/'.join(info.get('allows') or [])}），"
                                     f"且官方目录里没有别的钩子能承担 {needs}")})
                continue
        # 官方说过这个钩子**不一定发得出来**（如 message_sent 要 adapter opt-in）：
        # 拿它当"每轮触发"就是把插件押在一条官方明说不保证的路上 —— 显式说，别装没看见。
        caveat = str(info.get("dispatch_caveat") or "")
        if caveat and str(h.get("role") or "").startswith("trigger"):
            notes.append(f"{target} 官方有派发告警（{caveat[:80]}…）⇒ 不适合当触发点")
        if info.get("needs_perm"):
            notes.append(f"{target} 要先在插件配置里开 {'/'.join(info['needs_perm'])}")
        decl_hooks.append({
            "hook": target,
            "role": h.get("role", ""),
            "needs": needs,
            "kind": info.get("kind", ""),
            "group": info.get("group", ""),
            "budget": info.get("budget", ""),
            "failure_policy": info.get("failure_policy", ""),
            "needs_perm": info.get("needs_perm") or [],
            "dispatch_caveat": caveat,
            "source_url": info.get("source_url", ""),
        })
    return {
        "id": design.get("id") or "",
        "type": "consumer",
        "derived_from": schema.get("derived_from", ""),
        "hooks": decl_hooks,
        "gaps": gaps,
        # ⚠ 2026-09-23：`blocking` 的缺口 = **卡上/声明里要的行为没落点**（不是"少挂一条
        #   无所谓"）。运行规格里会带 `ok:false`，注入端据此**显式报 FAIL**，
        #   不许静默降级：声明的要求必须真跑。
        "blocking_gaps": [g for g in gaps if isinstance(g, dict) and g.get("blocking")],
        "notes": notes,
        "emit_caveat": schema.get("emit_caveat", ""),
        "lifecycle": design.get("lifecycle") or "critical",
    }


def plugin_runtime(declarations: list[dict]) -> dict[str, Any]:
    """**声明 → 运行规格**：把声明编译成插件宿主能直接照办的东西。

    插件不再把钩子硬编在 TS 里：它读这份运行规格注册（缺省回退自带那套）。
    于是"改声明 = 改插件行为" —— 这就是 large 那条链上"动态生成 plugin"的落地形态：
    声明是规格，宿主是执行器，规格变了宿主不用重写。
    """
    out: dict[str, Any] = {"version": 1, "plugins": {}}
    for rec in (declarations or []):
        if not isinstance(rec, dict) or str(rec.get("type") or "") != "consumer":
            continue
        did = str(rec.get("id") or "")
        if not did:
            continue
        decl = rec.get("declaration") if isinstance(rec.get("declaration"), dict) else {}
        hooks = decl.get("hooks") or []
        _blocking = [g for g in (decl.get("gaps") or [])
                     if isinstance(g, dict) and g.get("blocking")]
        out["plugins"][did] = {
            "hooks": [{"hook": h.get("hook"), "role": h.get("role"), "needs": h.get("needs"),
                       "budget": h.get("budget", ""), "failure_policy": h.get("failure_policy", "")}
                      for h in hooks if isinstance(h, dict) and h.get("hook")],
            "lifecycle": str(rec.get("lifecycle") or decl.get("lifecycle") or "critical"),
            "install": (rec.get("satisfied") or {}).get("install_route", ""),
            "scope": "card-only",
            "gaps": decl.get("gaps") or [],
            # ok=False ⇒ 声明里有行为**没有落点**：注入端要显式报 FAIL（不许静默降级）。
            "ok": not _blocking,
            "blockingGaps": _blocking,
            "notes": decl.get("notes") or [],
        }
    return out


def mechanism_delta(prev_manifest: dict, facts: dict) -> dict[str, Any]:
    """机制变化：本轮机制事实 vs 上一版声明里的机制事实。

    这是「scout+消歧分拣出的 OpenClaw 的机制**变化**」那一步：新增了什么钩子、
    删掉了什么、谁的**性质**变了（观察↔改写：挂法完全不同）、谁的**预算**变了、
    reason 值域多了什么。空变化返回**同形状的空结构**（产物确定性，settleManifest 好比对）。
    """
    prev = mechanism_facts(prev_manifest or {})
    ph, nh = prev.get("hooks") or {}, facts.get("hooks") or {}
    added = sorted(set(nh) - set(ph))
    removed = sorted(set(ph) - set(nh))
    changed = []
    for name in sorted(set(nh) & set(ph)):
        a, b = ph[name], nh[name]
        if str(a.get("kind", "")) != str(b.get("kind", "")):
            changed.append({"id": name, "field": "kind",
                            "from": str(a.get("kind", "")), "to": str(b.get("kind", ""))})
    pb, nb = prev.get("budgets") or {}, facts.get("budgets") or {}
    bchanged = []
    for name in sorted(set(nb) & set(pb)):
        if pb[name] != nb[name]:
            bchanged.append({"id": name, "from": pb[name], "to": nb[name]})
    pr = list(prev.get("session_end_reasons") or [])
    nr = list(facts.get("session_end_reasons") or [])
    return {
        "hooks": {"added": added, "removed": removed, "changed": changed},
        "budgets": {"added": sorted(set(nb) - set(pb)), "removed": sorted(set(pb) - set(nb)),
                    "changed": bchanged},
        "session_end_reasons": {"added": [r for r in nr if r not in pr],
                                "removed": [r for r in pr if r not in nr]},
        "session_end_drain": {"from": str(prev.get("session_end_drain") or ""),
                              "to": str(facts.get("session_end_drain") or "")},
    }


_SPIDERS = {
    "provider": spider_provider,
    "channel": spider_channel,
    "gateway": spider_gateway,
    "plugin": spider_plugin,
    "memory": spider_memory,
    "automation": spider_automation,
    "web_search": spider_web_search,
    "tool": spider_tool,
    "cli": spider_cli,
    "mechanism": spider_mechanism,
}

def _serialize(entries: dict[str, Any]) -> str:
    """确定性序列化（铁律三）。

    同一份 entries 必须产出字节级一致的字符串：
      - key 排序固定（sort_keys）
      - 缩进/分隔符写死（indent=2, separators）
      - ensure_ascii 定死为 False（中文原文保留，不依赖转义）

    这是 ot→mani diff 有意义的前提。禁止用裸 json.dumps（dict 序可能受环境扰动）。
    """
    return json.dumps(
        entries,
        ensure_ascii=False,
        indent=2,
        separators=(",", ": "),
        sort_keys=True,
    )

def crawl(
    raw_text: str,
    version_info: tuple[str, str] | None = None,
    deployed: dict[str, dict] | None = None,
    demands: list[dict] | None = None,
    prev_manifest: dict | None = None,
) -> dict[str, Any]:
    """spider 主入口：遍历所有文档段，按 URL 分流，产出 grouped 结构。

    version_info: (npm_latest, npm_published_at)，来自 npm 锚点（scout 传入），
    写入产物 version 元数据，供 large 的 usm._checkSkippedVersions 用。

    deployed: {plugin_id: {origin, providerIds}}，来自已部署 OpenClaw 的
    plugins list 真值。装通后才可采到；传 None 则跳过自我纠正（纯文档抓取，
    确定性不受影响）。

    demands: 自定义区的需求条目（scout 从 user_prefs 读出，见 scout._readCustomDemands）。
    传了就做实体链接/机制对账 → entries["custom_demands"] 带满足结果（命中哪个官方实体 +
    出处 / 未命中原因 / 插件设计的机制对账），随 ot.json 回 mani 声明。传 None 则不带需求段。

    prev_manifest: 上一版 mani（scout 读盘传入）—— 用来差分**机制变化**：
    本轮从官方文档分拣出的机制事实 vs 上一版声明里的，谁新增/删掉/性质或预算变了。

    返回 {entries: {...}, mechanisms: [...], mechanism_delta: {...}, version: {...}}
    """
    entries: dict[str, list[dict[str, Any]]] = {
        "providers": [],
        "channels": [],
        "gateways": [],
        "plugins": [],
        "memories": [],
        "automations": [],
        "web_searches": [],
        "tools": [],
        "clis": [],
    }
    # 机制面单独收（不进 entries）：机制不是消费端，不该被 large 扁平化成卡。
    mechanisms: list[dict[str, Any]] = []
    # 文档段：Source: URL 行分隔
    seg_pattern = re.compile(
        r"(?:^#\s+.+\n)?Source:\s*(https?://\S+)", re.MULTILINE
    )
    matches = list(seg_pattern.finditer(raw_text))

    # 前置：读官方 local-models 归类，得到本地后端清单（供 provider 标记 config_shape）
    local_backends = _extract_local_backends(raw_text)
    # 频道实体白名单：从 llms-full.txt「Supported channels」长出，channels/ 下的
    # 功能页（groups/pairing/troubleshooting 等）不是可接入频道，不进 channels 列表。
    supported_channels = _extract_supported_channels(raw_text)
    # channel 子页暂存：主页常是「Where each page covers / Where each section
    # moved」的导航页，一条接入命令都没有；真正的命令写在 /<实体>/setup 这类
    # 子页里。子页会被实体白名单挡掉，所以先在这儿收着，留给主实体补命令。
    channel_subpages: dict[str, dict[str, str]] = {}

    for i, m in enumerate(matches):
        source_url = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw_text)
        body = raw_text[start:end]

        ttype = _route_type(source_url)
        slug = _last_slug(source_url)
        if not ttype or not slug:
            continue
        # 聚合页排除对 provider/channel/gateway 等一律生效（不变），
        # 唯独 cli 放行同名页——/cli/models 是「openclaw models 命令」文档，
        # 含 set/set-image 等方法，不能当目录页滤掉。
        if _is_aggregate(slug) and ttype != "cli":
            continue
        # channel 子页（/channels/<实体>/<子页>）：收进暂存，不走实体分支。
        if ttype == "channel":
            _cm = re.match(r"^https?://[^/]+/channels/([^/?#]+)/([^/?#]+)/?$", source_url)
            if _cm and (not supported_channels or _cm.group(1) in supported_channels):
                channel_subpages.setdefault(_cm.group(1), {})[_cm.group(2)] = body
                continue
        # channel 实体白名单：只保留 Supported channels 列表里的真频道
        if ttype == "channel" and supported_channels and slug not in supported_channels:
            continue

        spider = _SPIDERS.get(ttype)
        if not spider:
            continue
        try:
            if ttype == "provider":
                result = spider(slug, body, source_url, local_backends)
                # 心跳钩子：观察 Property 表字段，发现词典不认识的新字段名/信号词就沉淀候选池
                _observe_provider_fields(body)
                # provider 可能返回多个 entry（复数 Provider ids，如 tencent 的
                # tokenhub + tokenplan），逐个展开
                if isinstance(result, list):
                    for entry in result:
                        if entry and entry.get("id"):
                            entries["providers"].append(entry)
                elif result and result.get("id"):
                    entries["providers"].append(result)
                continue
            entry = spider(slug, body, source_url)
            if entry and entry.get("id"):
                if ttype == "channel":
                    entries["channels"].append(entry)
                elif ttype == "gateway":
                    entries["gateways"].append(entry)
                elif ttype == "plugin":
                    entries["plugins"].append(entry)
                elif ttype == "memory":
                    entries["memories"].append(entry)
                elif ttype == "automation":
                    entries["automations"].append(entry)
                elif ttype == "web_search":
                    entries["web_searches"].append(entry)
                elif ttype == "tool":
                    entries["tools"].append(entry)
                elif ttype == "cli":
                    entries["clis"].append(entry)
                elif ttype == "mechanism":
                    mechanisms.append(entry)
        except Exception as exc:
            # 单段解析失败不许无痕跳过（原来 except: continue 什么都不留）
            logger.warning("[STAKE-CRAWL] 段落解析失败已跳过：%s (%s): %s", slug, source_url, exc)
            continue

    # channel 命令补全：主页没命令时，从 /<实体>/setup（或同类安装页）取。
    # 只取安装类子页，不扫 troubleshooting/access-control 那类，免得把排障命令
    # 当成接入步骤塞进卡片。
    for _ch in entries["channels"]:
        if _ch.get("commands"):
            continue
        _subs = channel_subpages.get(_ch.get("id")) or {}
        for _page in ("setup", "quickstart", "getting-started", "install"):
            if _subs.get(_page):
                _cmds = _extract_channel_commands(_subs[_page])
                if _cmds:
                    _ch["commands"] = _cmds
                    break
    if channel_subpages:
        logger.info("[STAKE-CRAWL] channel 子页暂存：%d 个实体、%d 页",
                    len(channel_subpages), sum(len(v) for v in channel_subpages.values()))
    # 二段关联：把 plugin 页的 package/providers 回填到对应 provider
    _link_plugins_to_providers(entries)
    # 后置兜底：对仍 unknown 的 provider，用正文精确信号下结论
    _resolve_unknown_plugins(entries)
    # 后置：outSider 最终判定——plugin 回填完成后，无官方插件（none/unknown）的就是 outSider
    _finalize_outsider(entries)
    # 能力类型分类：把 plugin 的 contracts 回填到 provider（model/speech/image/embedding/...）
    _annotate_capabilities(entries)
    # 实体消歧：先验图优先用已部署 OpenClaw 的 providerIds，其次文档 Surface providers
    _disambiguate_providers(entries, deployed)
    # 机制面：把钩子机制的权威面并成事实表，并与上一版声明做**机制变化**差分
    # （这是「scout+消歧机制后分拣出的 OpenClaw 的机制变化」那一步）
    _facts = mechanism_facts({"mechanisms": mechanisms})
    _delta = mechanism_delta(prev_manifest or {}, _facts)
    # 机制**原文**落本机运行期文件（不进包）：它是"生长"的原料 ——
    # 官方加新性质/新任务/新措辞，先在这儿被观察到，再经候选池转正进词典。
    try:
        _save_mechanisms_raw(_MECH_RAW_ACC)
    finally:
        # 落盘成败都要清：模块级累积器只有这一处清，异常残留会串到下一轮
        _MECH_RAW_ACC.clear()
    # 需求满足：客户经客制化提的需求 + 我们对插件的设计（消费端声明），
    # 对着本次采集的官方实体与官方机制做对账
    # （必须在消歧之后——命中别名要给母体 id；必须在 promote 之前——本次观察本次转正）
    satisfy_demands(entries, demands, facts=_facts, delta=_delta)
    # 心跳钩子：候选池里累计够阈值的词转正进正式词典（词典自我生长）
    try:
        promoted = promote_candidates()
        if promoted:
            logger.info("[LEXICON] promoted %d 词: %s", len(promoted), promoted[:10])
    except Exception as exc:
        # 转正失败不许无声：词典生长断了必须看得见
        logger.warning("[LEXICON] 候选池转正失败，本轮词典未生长: %s", exc)

    # ── 检测桩：蜘蛛产出全量计数（含新增的 clis 方法抓取）──
    try:
        from main.main import stk as _stk
        _stk("spider.crawl",
             f"providers={len(entries.get('providers', []))} "
             f"gateways={len(entries.get('gateways', []))} "
             f"clis={len(entries.get('clis', []))} "
             f"cli_usages={sum(len(c.get('usages', [])) for c in entries.get('clis', []))} "
             f"cli_methods={sum(len(c.get('methods', [])) for c in entries.get('clis', []))}",
             ok=True)
    except Exception:
        pass
    # 发布前剔除内部字段 _body
    for p in entries["providers"]:
        p.pop("_body", None)

    result: dict[str, Any] = {"entries": entries}
    # 机制面随产物一起落盘（顶层，不在 entries 里）：
    #   mechanisms      —— 本轮从官方文档分拣出的机制事实（钩子名/性质/预算/reason 值域）
    #   mechanism_delta —— 与上一版声明的**机制变化**差分（谁新增/删掉/性质或预算变了）
    # 声明里那些挂机制的消费端（插件设计）会各自带上与自己相关的对账结果。
    result["mechanisms"] = mechanisms
    result["mechanism_delta"] = _delta
    # 声明 → **运行规格**（插件宿主直接照办的东西）：改声明 = 改插件行为。
    # 插件读它注册钩子（读不到就回退自带那套），于是 large 那条链就成了"动态生成 plugin"。
    result["plugin_runtime"] = plugin_runtime(entries.get("custom_demands") or [])
    # version 元数据（来自 npm 锚点，供 large usm 版本检查用）
    if version_info and version_info[0]:
        result["version"] = {
            "type": "_meta_version",
            "npm_latest": version_info[0],
            "npm_published_at": version_info[1],
        }
    return result

def crawl_serialized(
    raw_text: str,
    version_info: tuple[str, str] | None = None,
    deployed: dict[str, dict] | None = None,
    demands: list[dict] | None = None,
    prev_manifest: dict | None = None,
) -> str:
    """crawl + 确定性序列化，产出可直接写 ot.json 的字节串。

    这是 scout 接回管线时唯一该用的入口：保证 ot 的字节级确定性（铁律三）。
    deployed 传已部署 OpenClaw 的真实插件状态（origin + providerIds，装通后才采）。
    demands 传自定义区的需求条目（scout._readCustomDemands 读出），做需求满足并随 ot 流动。
    prev_manifest 传上一版 mani：用来差分**机制变化**（插件设计要匹配的就是它）。
    """
    try:
        return _serialize(crawl(raw_text, version_info, deployed, demands, prev_manifest))
    finally:
        # crawl 中途抛异常时收尾那段清不到累积器 ⇒ 残留会串进下一轮。
        # 这里是 scout 接管的唯一入口：异常也保证清干净（原料暂存不跨轮）。
        _MECH_RAW_ACC.clear()

def _observe_provider_fields(body: str) -> None:
    """心跳钩子：观察 provider 文档的 Property 表字段，把词典不认识的新字段名/
    信号词沉淀进候选池（活词典的「观察」环节）。

    只做观察，不做任何分类决策——分类仍走正式词典 + 兜底表。观察失败静默。
    """
    try:
        prop = parse_property_table(body)
        for key, val in prop.items():
            observe_field(key, val)
    except Exception:
        pass

def _finalize_outsider(entries: dict[str, list[dict[str, Any]]]) -> None:
    """plugin 回填完成后，统一判定 outSider（无官方插件的 provider）。

    判据简单可靠：plugin.kind 是 bundled/official 的 → 入列（outSider=False）；
    其余（none/unknown）→ outSider（outSider=True）。
    官方未来给某个 outSider 加了插件，plugin.kind 会变 bundled/official，
    spider 下次爬自动转正，无需改代码。
    """
    for p in entries["providers"]:
        # 缺 plugin 键 / plugin 不是 dict 时不许 KeyError（crawl 里是裸调用，
        # 一条缺键就能把整轮 crawl 打挂）
        _plugin = p.get("plugin")
        _kind = _plugin.get("kind") if isinstance(_plugin, dict) else None
        p["outSider"] = _kind not in ("bundled", "official")

def reconcile_plugin_kinds_with_deployed(
    entries: dict[str, list[dict[str, Any]]],
    deployed: dict[str, dict] | None = None,
) -> list[str]:
    """用已部署 OpenClaw 的真实插件状态，纠正 spider 抓取 + 沉淀进字典。

    deployed: {plugin_id: {origin, providerIds}}，来自 `openclaw plugins list --json`。
    这是「装通后」才有的权威真值（壳层是乙方，照甲方 OpenClaw 的实际来）。

    流程：
      1. 对照每个 provider 的 spider 抓取 kind vs 部署 origin，不一致的纠正。
      2. 每个纠正结论调 lexicon.observe_plugin_kind 沉淀进候选池（阈值转正进词典）。
      3. 返回纠正清单（供日志/检测桩）。

    返回 [{id, from_kind, to_kind}]。deployed 为空（没装通）时静默跳过，不阻塞。
    """
    if not deployed:
        return []

    # origin → spider kind 的映射
    def _to_kind(origin: str) -> str:
        o = (origin or "").lower()
        if o == "bundled":
            return "bundled"
        if o == "global":
            return "official"
        return ""

    corrected: list[dict[str, str]] = []
    for p in entries["providers"]:
        pid = p.get("id", "")
        info = deployed.get(pid)
        if not info:
            # 部署真值里没有这个 provider（可能没启用），跳过
            continue
        to_kind = _to_kind(info.get("origin", ""))
        if not to_kind:
            continue
        from_kind = p["plugin"].get("kind", "unknown")
        if from_kind == to_kind:
            continue
        # 不一致 → 纠正 + 沉淀
        p["plugin"]["kind"] = to_kind
        corrected.append({"id": pid, "from_kind": from_kind, "to_kind": to_kind})
        try:
            observe_plugin_kind(pid, to_kind)
        except Exception:
            pass

    try:
        from main.main import stk as _stk
        _stk("spider.reconcile",
             f"纠正 {len(corrected)} 个: {[c['id'] for c in corrected]}",
             ok=True)
    except Exception:
        pass
    return corrected

def _annotate_capabilities(entries: dict[str, list[dict[str, Any]]]) -> None:
    """能力类型分类：把 plugin 的 contracts（能力类型）回填到 provider。

    能力是可叠加的集合，不是互斥单选——ollama 既是模型推理（providers 字段有 id）
    又提供 embedding（contracts 有 memoryEmbeddingProviders）。provider 有 providers
    字段 → 必有 model 能力，contracts 里的 *Providers 再追加其他能力。

    能力维度（官方 contracts 字段）：
      model       — 模型推理（chat 类，默认）
      speech      — 语音
      image       — 图像生成
      embedding   — 嵌入
      web_search  — 网络搜索
      media       — 媒体理解
    """
    # 从 plugin 页的 contracts 建 provider_id → 能力集合 映射
    capability_map: dict[str, set[str]] = {}
    for pl in entries["plugins"]:
        caps = pl.get("capabilities", [])
        provider_ids = pl.get("providers", [])
        for pid in provider_ids:
            capability_map.setdefault(pid, set())
            for c in caps:
                capability_map[pid].add(c)

    def _classify(caps: set[str]) -> set[str]:
        joined = " ".join(caps).lower()
        result: set[str] = set()
        # 有 providers 字段的，默认就是模型推理
        result.add("model")
        if "speech" in joined:
            result.add("speech")
        if "imagegeneration" in joined or "musicgeneration" in joined or "videogeneration" in joined:
            result.add("image")
        if "embedding" in joined:
            result.add("embedding")
        if "websearch" in joined:
            result.add("web_search")
        if "mediaunderstanding" in joined:
            result.add("media")
        return result

    for p in entries["providers"]:
        caps = capability_map.get(p["id"], set())
        p["capabilities"] = sorted(_classify(caps))

def _disambiguate_providers(
    entries: dict[str, list[dict[str, Any]]],
    deployed: dict[str, dict] | None = None,
) -> None:
    """实体消歧：用先验图标注 provider 的 alias/母体关系。

    先验图来源（按权威优先级）：
      1. 已部署 OpenClaw 的 plugins list providerIds（装通后的运行时真实注册，最权威）
      2. 文档 plugin 页的 Surface providers（spider 抓取，次权威）

    每个 provider entry 写入 relation 字段：
      canonical — 母体本身
      alias     — 归并到母体（几个说法指向同一真源词元）
      pending   — 未命中先验（非官方插件，靠 models.providers 手动配置）
    """
    try:
        prior_graph: dict[str, list[str]] = {}

        # 优先：已部署 OpenClaw 的 providerIds（运行时真实注册的母体→别名）
        if deployed:
            for info in deployed.values():
                pids = info.get("providerIds") or []
                if not pids:
                    continue
                canonical = pids[0]
                prior_graph.setdefault(canonical, [])
                if len(pids) > 1:
                    # 合并别名（部署真值可能和文档有重叠，取并集去重）
                    merged = prior_graph[canonical] + [a for a in pids[1:] if a not in prior_graph[canonical]]
                    prior_graph[canonical] = merged

        # 次优先：文档 plugin 页的 Surface providers（补部署真值没覆盖的）
        for pl in entries["plugins"]:
            pids = pl.get("providers", [])
            if not pids:
                pids = [pl["id"]]
            canonical = pids[0]
            prior_graph.setdefault(canonical, [])
            if len(pids) > 1:
                merged = prior_graph[canonical] + [a for a in pids[1:] if a not in prior_graph[canonical]]
                prior_graph[canonical] = merged

        provider_ids = [p["id"] for p in entries["providers"]]
        resolved = disambiguate_provider_ids(provider_ids, prior_graph)

        relation_map = {r["id"]: r for r in resolved}

        for p in entries["providers"]:
            pid = p["id"]
            r = relation_map.get(pid)
            if r is None:
                p["relation"] = "pending"
                p["canonical_id"] = pid
            elif r["relation"] == "alias":
                p["relation"] = "alias"
                p["canonical_id"] = r.get("canonical_id", pid)
            else:
                p["relation"] = r["relation"]
                p["canonical_id"] = pid
    except Exception as exc:
        # 消歧一失败 ⇒ 所有 provider 的 relation/canonical_id 全缺：不许静默
        logger.warning("[STAKE-DISAMB] provider 消歧失败（relation/canonical_id 未写）: %s", exc)

# ══════════════════════════════════════════════════════════════
# ═══ 需求满足（自定义区需求 → 官方实体链接） — 并入 kernel/filter.py ═══
# ══════════════════════════════════════════════════════════════
# 客户/人格经客制化提的需求（自定义区 type=intent 条目 + 扩展位消费端声明）**不是**搬进
# mani 当一张卡就算完 —— 它要先**进采集**：由 spider 抓到的官方实体来判「这条需求指向
# 官方哪个能力、满足没满足、官方出处在哪」，结论随 ot.json 回到 mani 声明里。
#
# 铁律守住：filter 仍不写 mani（ownership 表里 mani 归 kernel.scout），本模块只把结论
# 放进 ot.json 的 entries.custom_demands，由 scout 的 inject 阶段并进 mani 声明。

import hashlib
#: 实体来源表（顺序固定 ⇒ 产物确定性）：(entity_type, entries 键, 参与比对的字段)
_ENTITY_SOURCES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("provider", "providers", ("id", "canonical_id", "display_name", "section_name")),
    ("channel", "channels", ("id", "display_name", "section_name")),
    ("plugin", "plugins", ("id", "package", "display_name")),
    ("gateway", "gateways", ("id", "display_name")),
    ("memory", "memories", ("id", "display_name")),
    ("automation", "automations", ("id", "display_name")),
    ("web_search", "web_searches", ("id", "display_name")),
    ("tool", "tools", ("id", "display_name")),
    ("cli", "clis", ("id", "display_name")),
)

#: 词元最短长度：太短的 key（如 "ai"）会在正文里乱命中
_MIN_KEY = 3


def _norm_key(s: str) -> str:
    """归一 key：小写 + 只留字母数字与 CJK（去掉空格/连字符/点/下划线）。"""
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (s or "").lower())


def demand_fp(name: str, description: str) -> str:
    """需求文本指纹：客户改了需求 ⇒ 旧满足结论作废（重判，不把旧结论贴到新需求上）。

    公开名：scout 合并声明时要用**同一个**指纹判「这条需求变没变」，
    两处各算一套迟早会分叉。
    """
    return hashlib.sha1(f"{name}\n{description}".encode("utf-8")).hexdigest()[:8]


def declaration_fp(rec: dict) -> str:
    """一条自定义区声明的指纹（需求 + 消费端共用）。

    需求（type=intent）：看 `name` + 澄清全文 —— 那是客户的话。
    消费端（type=consumer）：没有正文字段，看**整条声明**（插件/入口/钩子/装法都在里面）——
    声明里改了任何一项，旧满足结论就该作废。
    """
    if str(rec.get("type") or "intent") == "intent":
        return demand_fp(str(rec.get("name") or ""), str(rec.get("description") or ""))
    core = {k: v for k, v in rec.items() if k not in ("satisfied", "reason", "fp")}
    blob = json.dumps(core, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:8]


def _add_entity_keys(index: dict[str, dict], values: tuple, target: dict,
                     suffixes: tuple[str, ...]) -> None:
    """把一组名字（字段原值 + 剥功能后缀版）登记进索引。同 key 已有 → 保留先到的。

    suffixes 由调用方读一次传进来：`normalize_name` 每次都去读 forge.json，
    2000 个实体乘两个字段会把一次 crawl 变成几千次文件读。
    """
    for v in values:
        s = (v or "").strip()
        bare = s
        for suf in suffixes:
            if bare.endswith(suf):
                bare = bare[: -len(suf)]
                break
        for k in (_norm_key(s), _norm_key(bare)):
            if len(k) >= _MIN_KEY and k not in index:
                index[k] = target


def _build_entity_index(entries: dict) -> dict[str, dict]:
    """建「归一 key → 实体」表。

    先验图与别名索引走**既有消歧算法**（`build_prior_graph` / `build_alias_index` /
    `normalize_name`），不另造一套别名归并 —— 官方文档或部署真值将来多给一个别名，
    这里自动跟着生效。命中别名时给的是**母体** id（与 `_disambiguate_providers`
    同一套真源词元），需求卡上写的就是官方认的那一个。

    同一 key 多实体时取**首个**（来源表顺序固定 ⇒ 产物确定性）。
    实体除了身份，还带上「怎么装、什么能力、官方出处」：消费端（插件声明）要的正是这些。
    """
    suffixes = tuple(_function_suffixes())
    index: dict[str, dict] = {}
    plugins = [pl for pl in (entries.get("plugins") or []) if isinstance(pl, dict)]
    prior_graph = build_prior_graph(plugins)
    alias_index = build_alias_index(prior_graph)
    prov = {p["id"]: p for p in entries.get("providers", [])
            if isinstance(p, dict) and p.get("id")}

    def _target(etype: str, e: dict) -> dict:
        eid = str(e.get("id") or "")
        return {
            "entity_type": etype,
            "entity_id": eid,
            "canonical_id": str(e.get("canonical_id") or eid),
            "source_url": str(e.get("source_url") or ""),
            "package": str(e.get("package") or ""),
            "install_route": str(e.get("install_route") or ""),
            "capabilities": [str(c) for c in (e.get("capabilities") or [])],
        }

    for etype, key, fields in _ENTITY_SOURCES:
        for e in entries.get(key, []) or []:
            if not isinstance(e, dict) or not e.get("id"):
                continue
            eid = str(e["id"])
            target = _target(etype, e)
            if etype == "provider":
                # 既有消歧算法的产物：别名 → 母体
                mother = prov.get(alias_index.get(eid, eid))
                if mother is not None:
                    target = _target("provider", mother)
            _add_entity_keys(index, tuple(e.get(f, "") for f in fields), target, suffixes)

    # 别名 key：先验图里的别名（可能没有自己的 provider 条目，如 tencent 的 tokenplan）
    for alias, canonical in sorted(alias_index.items()):
        mother = prov.get(canonical)
        if mother is None:
            continue
        _add_entity_keys(index, (alias,), _target("provider", mother), suffixes)
    return index


def _satisfy_consumer(rec: dict, plugin_index: dict[str, dict], index: dict[str, dict],
                      facts: dict | None = None, delta: dict | None = None) -> None:
    """消费端声明（type=consumer）过关：**身份/装法** + **机制对账** → 完整插件声明。

    它问的不是「官方哪个实体满足客户这条需求」，而是「我们对插件的设计，跟官方现在
    这套机制（以及这轮变了什么）对得上吗、装法是什么」—— 结论写进声明，供消费端照办
    （不再靠人手工注入）：

      · satisfied —— 身份/装法：真源里有（官方插件/provider/channel）就给官方路由
        install_route、包名、能力、出处；真源里没有 → **本地插件**（随壳分发的那类），
        显式 match="external" + present=False，装法照声明里的 installer 走。
        这不是「未命中」——「官方文档里没有我们自己写的插件」是**结论**。
      · mechanism —— 机制对账：设计里声明的每个钩子，官方机制表里有没有、是观察还是
        改写、超时预算多少、官方出处在哪；**官方没有的钩子 = 缺口**（说明我们的插件挂在
        文档没写的内部行为上，官方一改就断，必须显式盯住）；再看本轮**机制变化**里
        有哪些落在我们声明的钩子上（新增/删除/性质变了/预算变了）。

    查表先查**插件表**：同一个名字既是频道又是插件时（discord 就是），
    消费端要的是**插件**那条（带 install_route/package 的），不是频道那条。
    """
    spec = rec.get("spec") if isinstance(rec.get("spec"), dict) else {}
    pid = str(spec.get("plugin") or rec.get("id") or "")
    installer = rec.get("installer") if isinstance(rec.get("installer"), dict) else {}
    k = _norm_key(pid)
    hit = (plugin_index.get(k) or index.get(k)) if len(k) >= _MIN_KEY else None
    if hit is not None:
        rec["satisfied"] = {
            **hit,
            "match": "name_exact",
            "present": True,
            "note": "真源里有这个消费端：照官方路由装（install_route/package）",
        }
    else:
        rec["satisfied"] = {
            "entity_type": "local",
            "entity_id": pid,
            "canonical_id": pid,
            "source_url": "",
            "package": "",
            "install_route": str(installer.get("inject_script") or "local-bundled"),
            "capabilities": [str(c) for c in (rec.get("capabilities") or [])],
            "match": "external",
            "present": False,
            "note": ("真源（官方文档）里没有它 ⇒ 本地插件（随壳分发）：装法照声明里的 "
                     "installer 走，不走官方路由"),
        }

    # ── 机制对账（这把"我们对插件的设计"与"官方机制现状/变化"接上）──
    schema = derive_declaration_schema(facts)
    ohooks = schema.get("hooks") or {}
    budgets = (facts or {}).get("budgets") or {}
    failures = (facts or {}).get("failures") or {}
    hd = ((delta or {}).get("hooks") or {})
    bd = ((delta or {}).get("budgets") or {}).get("changed") or []
    matched: list[dict] = []
    gaps: list[dict] = []
    impact: list[dict] = []
    for h in (rec.get("hooks") or []):
        if not isinstance(h, dict):
            continue
        hid = str(h.get("id") or "")
        if not hid:
            continue
        info = ohooks.get(hid)
        if info:
            matched.append({"id": hid, "role": str(h.get("role") or ""),
                            "kind": info.get("kind", ""), "group": info.get("group", ""),
                            "budget": budgets.get(hid, ""),
                            "failure_policy": failures.get(hid, ""),
                            "source_url": info.get("source_url", "")})
        else:
            gaps.append({"id": hid, "role": str(h.get("role") or ""),
                         "why": ("官方机制表里没有这个钩子名 ⇒ 我们挂在文档没写的内部行为上，"
                                 "官方一改就断，必须显式盯住")})
        if hid in (hd.get("removed") or []):
            impact.append({"id": hid, "change": "removed"})
        if hid in (hd.get("added") or []):
            impact.append({"id": hid, "change": "added"})
        for c in (hd.get("changed") or []):
            if c.get("id") == hid:
                impact.append({"id": hid, "change": "kind",
                               "from": c.get("from", ""), "to": c.get("to", "")})
        for c in bd:
            if c.get("id") == hid:
                impact.append({"id": hid, "change": "budget",
                               "from": c.get("from", ""), "to": c.get("to", "")})
    rec["mechanism"] = {
        "source": schema.get("derived_from", ""),
        "schema_version": schema.get("version"),
        "hook_domain_size": len(schema.get("hook_domain") or []),
        "hooks_matched": matched,
        "hooks_gaps": gaps,
        "delta_impact": impact,
        "hooks_total": len(ohooks),
        "session_end_reasons": list((facts or {}).get("session_end_reasons") or []),
        "session_end_drain": str((facts or {}).get("session_end_drain") or ""),
        "emit_caveat": schema.get("emit_caveat", ""),
    }
    # 反推出来的**完整插件声明**：作用落位到官方钩子（官方名 + 性质 + 预算 + 失败策略 + 承诺），
    # 落不下去的（性质不够 / 官方没这个钩子）显式进 gaps。
    rec["declaration"] = derive_declaration(rec, schema)
    # 三轴：消费端不是 provider，但 am/classify/build_form_schema 只认这三轴标签。
    # 给它一套专属取值（consumer:plugin:local），forge.json 里对应一条范式声明
    # （preset + 字段），于是 large 能把它**生成成消费端实例**，而不是掉进 generic_card。
    rec.setdefault("auth_kind", "consumer")
    rec.setdefault("transport", "plugin")
    rec.setdefault("model_source", "local")
    rec.setdefault("lifecycle", "critical")


def satisfy_demands(
    entries: dict[str, list[dict[str, Any]]],
    demands: list[dict] | None = None,
    facts: dict | None = None,
    delta: dict | None = None,
) -> list[dict]:
    """把需求条目对着本次采集的官方实体做实体链接，产出「满足结果」。

    判据（依次，确定性；命中即止）：
      ① 字典 ambiguity_dict.demand_entities 记过这条需求（文本指纹一致 + 实体本次也在）
         —— 沉淀的结论优先，不再靠文本猜。
      ② 精确：需求名归一后 == 某实体 key（id / 别名 / 显示名）
      ③ 正文：某实体 key 出现在「需求名 + 澄清全文」里（取**最长** key，同长取字典序）
    结论必带官方出处 source_url —— 「谁说的」要能查。

    命中 → demand["satisfied"]={entity_type,entity_id,canonical_id,source_url,match}；
           并沉淀进词典（**仅精确命中**；正文命中是猜，不固化）。
    未命中 → demand["satisfied"]=None + demand["reason"]（显式，不静默）。

    结果写进 entries["custom_demands"]（随 ot.json 流动，filter 不碰 mani）。
    facts/delta：本轮机制事实与机制变化（crawl 里由 mechanism_facts/mechanism_delta 产出），
    用来对账"我们对插件的设计"。
    返回富化后的需求/消费端声明清单。
    """
    if not demands:
        return []
    index = _build_entity_index(entries)
    plugin_index: dict[str, dict] = {}
    if any(isinstance(d, dict) and str(d.get("type") or "intent") != "intent"
           for d in demands):
        # 消费端要查的是**插件**那条（带 install_route/package）：同名时不被频道抢走
        plugin_index = _build_entity_index({"plugins": entries.get("plugins") or []})
    lex = _lex_load_ambiguity().get("demand_entities") or {}
    out: list[dict] = []
    for d in demands:
        if not isinstance(d, dict):
            continue
        rec = dict(d)
        # 消费端声明（扩展位自报要什么插件/入口/钩子）**也走这条链** —— 只是它问的
        # 不是「官方哪个实体满足客户这条需求」，而是「这东西真源里怎么给的、装法是什么」。
        if str(rec.get("type") or "intent") != "intent":
            _satisfy_consumer(rec, plugin_index, index, facts, delta)
            out.append(rec)
            continue
        did = str(rec.get("id") or "")
        name = str(rec.get("name") or "")
        desc = str(rec.get("description") or "")
        fp = declaration_fp(rec)
        rec["fp"] = fp
        hit: dict | None = None

        # ① 字典（沉淀优先）：结论固化后不再随正文漂移；实体本次没抓到也照给结论，
        #    但显式标 present=False —— 让人看得见「这条结论不是本次采到的」。
        known = lex.get(did)
        if isinstance(known, dict) and known.get("fp") == fp and known.get("entity_id"):
            tgt = index.get(_norm_key(known["entity_id"]))
            if tgt is not None:
                hit = {**tgt, "match": "lexicon", "present": True}
            else:
                hit = {
                    "entity_type": known.get("entity_type", ""),
                    "entity_id": known["entity_id"],
                    "canonical_id": str(known.get("canonical_id") or known["entity_id"]),
                    "source_url": known.get("source_url", ""),
                    "match": "lexicon",
                    "present": False,
                }
        # ② 精确
        if hit is None:
            nk = _norm_key(name)
            if len(nk) >= _MIN_KEY and nk in index:
                hit = {**index[nk], "match": "name_exact", "present": True}
        # ③ 正文（最长优先，确定性）
        if hit is None:
            blob = _norm_key(f"{name}{desc}")
            cands = sorted((k for k in index if len(k) >= _MIN_KEY and k in blob),
                           key=lambda k: (-len(k), k))
            if cands:
                hit = {**index[cands[0]], "match": "text", "present": True}

        if hit is not None:
            rec["satisfied"] = hit
            rec.pop("reason", None)
            if did and hit["match"] in ("lexicon", "name_exact"):
                try:
                    observe_demand_entity(did, hit["entity_type"], hit["entity_id"], fp,
                                          source_url=hit.get("source_url", ""))
                except Exception:
                    pass
        else:
            rec["satisfied"] = None
            rec["reason"] = ("本次采集的官方实体里没命中（需求可能指向非官方能力，"
                             "或实体名还没沉淀进词典）")
        out.append(rec)

    entries["custom_demands"] = out
    _hit = sum(1 for r in out if r.get("satisfied"))
    logger.info("[DEMAND] 需求 %d 条，命中 %d 条", len(out), _hit)
    try:
        from main.main import stk as _stk
        _stk("demand.satisfy",
             f"需求={len(out)} 命中={_hit} 未命中={[r.get('name') for r in out if not r.get('satisfied')][:5]}",
             ok=True)
    except Exception:
        pass
    return out


def _resolve_unknown_plugins(entries: dict[str, list[dict[str, Any]]]) -> None:
    """对 plugin 关联后仍 unknown 的 provider，用正文精确信号兜底。

    只在此兜底（不提前），避免误伤有 package 名的官方 provider。
    精确信号：正文有 "bundled ... provider/rows" 措辞 → bundled。
    注意：不用 plugins.entries.<id> 当信号——它只说明「存在某插件的配置项」，
    可能是 bundled、可能是 external 插件、也可能是别的插件引用它，与 plugin.kind
    无可靠映射（openai 文档里就混着 voice-call/codex 等别家插件的配置键）。
    """
    for p in entries["providers"]:
        # 缺键护栏（原来直取 p["plugin"].get ⇒ 缺 plugin 键就 TypeError/KeyError）
        _plugin = p.get("plugin")
        if not isinstance(_plugin, dict) or _plugin.get("kind") != "unknown":
            continue
        body = p.get("_body", "")
        if not body:
            continue
        if re.search(r"bundled\s+(?:[\w\s-]+\s+)?(?:provider|plugin|rows)", body, re.IGNORECASE):
            p["plugin"] = {"kind": "bundled", "package": ""}

def _link_plugins_to_providers(entries: dict[str, list[dict[str, Any]]]) -> None:
    """把 plugin 页的语义回填到 provider（解决 provider 文档里 plugin 信息缺失）。

    三个方向（按优先级）：
      1. plugin 页 Surface 声明了 providers: `xxx` → 直接回填到 xxx 的 provider
      2. plugin 页 package 名 @openclaw/xxx-provider → 按 xxx 匹配 provider id
      3. plugin 页 slug（URL 最后一段）精确等于某 provider id → 直接回填
         （覆盖 azure-speech / elevenlabs 这类 package 不带 -provider 后缀的）
    """
    provider_by_id = {p["id"]: p for p in entries["providers"]}
    plugin_by_id = {pl["id"]: pl for pl in entries["plugins"]}

    def _kind_from_route(install_route: str) -> str:
        """Install route → plugin.kind（权威信号，不靠包名猜）。
        included in OpenClaw → bundled；npm/ClawHub → official；source checkout → none；
        无 route 声明 → 回退按包名（有 @openclaw/ 前缀的官方 external 兜底 official）。
        """
        if not install_route:
            return ""
        if "included in openclaw" in install_route:
            return "bundled"
        if "npm" in install_route or "clawhub" in install_route:
            return "official"
        if "source checkout" in install_route:
            return "none"
        return ""

    def _fill(pid: str, pkg: str, install_route: str) -> None:
        if pid not in provider_by_id:
            return
        cur = provider_by_id[pid]["plugin"].get("kind")
        if cur not in ("unknown",):
            return
        kind = _kind_from_route(install_route)
        if not kind:
            # 无 Install route 声明 → 回退：有 package 名按 official，无则保持 unknown
            kind = "official" if pkg else "unknown"
        provider_by_id[pid]["plugin"] = {"kind": kind, "package": pkg}

    for pl in entries["plugins"]:
        pkg = pl.get("package", "")
        install_route = pl.get("install_route", "")
        # 方向1：Surface 里的 providers 列表
        for pid in pl.get("providers", []):
            _fill(pid, pkg, install_route)
        # 方向2：package 名反推（@openclaw/xxx-provider）
        m = re.search(r"@openclaw/([\w-]+)-provider", pkg)
        if m:
            _fill(m.group(1), pkg, install_route)
        # 方向3：slug 精确匹配 provider id（azure-speech、elevenlabs 等）
        _fill(pl["id"], pkg, install_route)

if __name__ == "__main__":
    import sys
    raw = open(sys.argv[1], encoding="utf-8").read() if len(sys.argv) > 1 else ""
    result = crawl(raw)
    print(json.dumps(
        {k: len(v) for k, v in result["entries"].items()},
        ensure_ascii=False, indent=2,
    ))
