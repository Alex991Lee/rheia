# -*- coding: utf-8 -*-
# L1-4198：kernel.filter — 文档过滤管线单模块宿主（spider 数据驱动管线 v3）
r"""kernel.filter — 文档过滤管线单模块宿主（spider 数据驱动管线 v3）"""

















from __future__ import annotations













import json
import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)





_JSON_OWNER_MAP: dict[str, str] = {
    "kernel/glory/glory.json":   "kernel.glory",
    "kernel/large/forge.json":   "kernel.large",
    "kernel/large/mani.json":    "kernel.scout",
}



_caller_local = threading.local()

# L58-73：json_owner — 上下文管理器：声明当前代码块对 JSON 文件的写入身份。
class json_owner:
    r"""上下文管理器：声明当前代码块对 JSON 文件的写入身份。"""






    def __init__(self, module: str) -> None:
        self._module = module

    def __enter__(self) -> None:
        _caller_local.value = self._module

    def __exit__(self, *args: object) -> None:
        _caller_local.value = None



# L78-97：json_write_guard — 写入 JSON 文件前检查调用者身份 —— 非 owner 写入直接拒绝。
def json_write_guard(relpath: str, data: dict[str, Any]) -> None:
    r"""写入 JSON 文件前检查调用者身份 —— 非 owner 写入直接拒绝。"""




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


























import json
import os
from typing import Any

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FORGE_PATH = os.path.join(_BASE_DIR, "kernel", "large", "forge.json")
_COLLAB_FORGE_LOCK = threading.Lock()
_CANDIDATES_PATH = os.path.join(_BASE_DIR, "kernel", "large", "ambiguity_candidates.json")


_PROMOTE_THRESHOLD = 2

# L136-142：_load_candidates — 读候选池；不存在或损坏返回空。
def _load_candidates() -> dict:
    r"""读候选池；不存在或损坏返回空。"""
    try:
        with open(_CANDIDATES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}

# L145-147：_save_candidates — _save_candidates
def _save_candidates(candidates: dict) -> None:
    with open(_CANDIDATES_PATH, "w", encoding="utf-8") as f:
        json.dump(candidates, f, ensure_ascii=False, indent=2, sort_keys=True)

# L150-171：observe_field — 规则一：观察一个 Property 表字段。若 key 不被正式词典认识，
def observe_field(key: str, value: str) -> None:
    r"""规则一：观察一个 Property 表字段。若 key 不被正式词典认识，"""

    amb = _lex_load_ambiguity()
    field_aliases = amb.get("field_aliases", {})


    key_l = key.lower().strip()
    for std, aliases in field_aliases.items():
        for a in aliases:
            if key_l == a.lower() or key_l.startswith(a.lower()):
                return


    std_name = _classify_by_value(value)
    if not std_name:
        return

    candidates = _load_candidates()
    pool = candidates.setdefault("field_aliases", {}).setdefault(std_name, {})
    pool[key] = pool.get(key, 0) + 1
    _save_candidates(candidates)

# L174-195：observe_plugin_kind — 规则一：观察「已部署 OpenClaw 说某 provider 的真实 plugin kind」。
def observe_plugin_kind(pid: str, kind: str) -> None:
    r"""规则一：观察「已部署 OpenClaw 说某 provider 的真实 plugin kind」。"""






    amb = _lex_load_ambiguity()
    known = amb.get("plugin_kinds", {})



    known_key = next((k for k in known if str(k).lower() == pid.lower()), None)
    if known_key is not None and str(known[known_key]).lower() == kind.lower():
        return

    candidates = _load_candidates()
    pool = candidates.setdefault("plugin_kinds", {})
    pool[pid] = pool.get(pid, {})
    pool[pid][kind] = pool[pid].get(kind, 0) + 1
    _save_candidates(candidates)

# L198-225：observe_demand_entity — 规则一：观察「一条需求精确命中官方哪个实体」。
def observe_demand_entity(did: str, entity_type: str, entity_id: str, fp: str,
                          source_url: str = "") -> None:
    r"""规则一：观察「一条需求精确命中官方哪个实体」。"""









    amb = _lex_load_ambiguity()
    cur = (amb.get("demand_entities") or {}).get(did)
    if isinstance(cur, dict) and cur.get("fp") == fp and cur.get("entity_id") == entity_id:
        return

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

# L228-242：_classify_by_value — 值特征 → 标准字段名（用于候选词的归类）。
def _classify_by_value(value: str) -> str:
    r"""值特征 → 标准字段名（用于候选词的归类）。"""
    v = value.strip()
    if v.startswith(("http://", "https://")):
        return "base_url"
    if any(s in v for s in ("_API_KEY", "_TOKEN", "_SECRET")):
        return "auth"

    if any(s in v.lower() for s in ("included in openclaw", "npm", "clawhub", "source checkout")):
        return "install_route"
    if v.startswith("`") or (v and v[0].isalnum() and "-" in v and " " not in v):
        return "provider_id"
    if "plugin" in v.lower():
        return "plugin"
    return ""

# L245-250：_lex_load_ambiguity — _lex_load_ambiguity
def _lex_load_ambiguity() -> dict:
    try:
        with open(_FORGE_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("ambiguity_dict", {}) or {}
    except (OSError, json.JSONDecodeError):
        return {}

# L253-369：promote_candidates — 规则二：候选池里累计 >= 阈值的词，转正进正式词典。
def promote_candidates() -> list[str]:
    r"""规则二：候选池里累计 >= 阈值的词，转正进正式词典。"""



    candidates = _load_candidates()
    amb = _lex_load_ambiguity()
    promoted: list[str] = []
    try:
        from main.main import stk as _stk
        _stk("lexicon.promote", f"候选池={json.dumps(candidates, ensure_ascii=False)[:120]}", ok=True)
    except Exception:
        pass

    changed = False

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

        _write_ambiguity(amb)
        _save_candidates(candidates)

    if promoted:
        try:
            from main.main import stk as _stk
            _stk("lexicon.promote.done", f"转正 {len(promoted)} 词: {promoted[:8]}", ok=True)
        except Exception:
            pass
    return promoted

# L372-381：_write_ambiguity — _write_ambiguity
def _write_ambiguity(amb: dict) -> None:
    with open(_FORGE_PATH, "r", encoding="utf-8") as f:
        fg = json.load(f)
    fg["ambiguity_dict"] = amb


    with json_owner("kernel.large"):
        json_write_guard("kernel/large/forge.json", fg)
    with open(_FORGE_PATH, "w", encoding="utf-8") as f:
        json.dump(fg, f, ensure_ascii=False, indent=2, sort_keys=True)

if __name__ == "__main__":

    print("活词典自检")
    observe_field("Authentication Method", "DEEPSEEK_API_KEY")
    observe_field("Authentication Method", "DEEPSEEK_API_KEY")
    c = _load_candidates()
    print("候选池:", json.dumps(c, ensure_ascii=False))
    promoted = promote_candidates()
    print("转正:", promoted)






























from typing import Any






_DEFAULT_SUFFIXES = ["-provider", "-speech", "-plugin", "-embedding"]

# L432-446：_function_suffixes — 从 forge.json 读功能后缀表；缺省用内置兜底。
def _function_suffixes() -> list[str]:
    r"""从 forge.json 读功能后缀表；缺省用内置兜底。"""
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

# L449-455：normalize_name — 剥掉功能后缀，返回真源词元（归一后的名字）。
def normalize_name(name: str) -> str:
    r"""剥掉功能后缀，返回真源词元（归一后的名字）。"""
    n = name.strip()
    for suf in _function_suffixes():
        if n.endswith(suf):
            return n[: -len(suf)]
    return n

# L458-474：build_prior_graph — 从已部署 OpenClaw 的插件列表构建先验图（母体 → 别名）。
def build_prior_graph(plugins: list[dict]) -> dict[str, list[str]]:
    r"""从已部署 OpenClaw 的插件列表构建先验图（母体 → 别名）。"""





    graph: dict[str, list[str]] = {}
    for p in plugins:
        pids = p.get("providerIds", [])
        if len(pids) >= 1:
            canonical = pids[0]

            graph.setdefault(canonical, [])
            if len(pids) > 1:
                graph[canonical] = pids[1:]
    return graph

# L477-483：build_alias_index — 反查索引：别名 → 母体（用于 O(1) 归并）。
def build_alias_index(prior_graph: dict[str, list[str]]) -> dict[str, str]:
    r"""反查索引：别名 → 母体（用于 O(1) 归并）。"""
    index: dict[str, str] = {}
    for canonical, aliases in prior_graph.items():
        for a in aliases:
            index[a] = canonical
    return index

# L486-548：disambiguate_provider_ids — 把文档解析出的 provider id 列表，用先验图归并成真源词元。
def disambiguate_provider_ids(
    provider_ids: list[str],
    prior_graph: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    r"""把文档解析出的 provider id 列表，用先验图归并成真源词元。"""





    prior_graph = prior_graph or {}
    alias_index = build_alias_index(prior_graph)



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

            result.append({"id": pid, "relation": "canonical"})
        elif pid in alias_index or npid in norm_alias_index:

            canonical = alias_index.get(pid) or norm_alias_index.get(npid)


            result.append({"id": pid, "relation": "alias", "canonical_id": canonical})
            if canonical not in seen:
                seen.add(canonical)
        else:

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























import json
import re
from typing import Any

# L585-593：_collab_load_ambiguity — _collab_load_ambiguity
def _collab_load_ambiguity() -> dict:
    import os
    _base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _forge = os.path.join(_base, "kernel", "large", "forge.json")
    try:
        with open(_forge, "r", encoding="utf-8") as f:
            return json.load(f).get("ambiguity_dict", {}) or {}
    except (OSError, json.JSONDecodeError):
        return {}

# L596-599：known_collaborators — 返回字典里已翻译好的合作者标准形态（collaborator_terms.collaborators）。
def known_collaborators() -> dict:
    r"""返回字典里已翻译好的合作者标准形态（collaborator_terms.collaborators）。"""
    amb = _collab_load_ambiguity()
    return amb.get("collaborator_terms", {}).get("collaborators", {}) or {}

# L602-615：derive_official_doc_urls — 按品牌域名规律，从合作者名称推导官方文档候选 URL。
def derive_official_doc_urls(name: str) -> list[str]:
    r"""按品牌域名规律，从合作者名称推导官方文档候选 URL。"""







    return [
        f"https://docs.{name}.com/api/tags",
        f"https://docs.{name}.ai/en/latest/",
        f"https://{name}.ai/docs/developer/rest",
    ]

# L618-637：extract_discovery_endpoints — 从合作者官方文档文本里，提取「发现端点 + 解析结构」。
def extract_discovery_endpoints(doc_text: str) -> list[dict[str, Any]]:
    r"""从合作者官方文档文本里，提取「发现端点 + 解析结构」。"""





    endpoints: list[dict[str, Any]] = []

    for m in re.finditer(r"GET\s+(/\S*?(?:models|tags)\S*)", doc_text, re.IGNORECASE):
        ep = m.group(1).strip()
        if ep and ep not in [e["discover"] for e in endpoints]:

            parse = {}
            if "tags" in ep:
                parse = {"key": "models", "field": "name"}
            elif "models" in ep:
                parse = {"key": "data", "field": "id"}
            endpoints.append({"discover": ep, "parse": parse})
    return endpoints

# L640-693：fetch_and_translate — 广域网抓取合作者官方文档，提取发现端点/解析结构，翻译进字典。
def fetch_and_translate(name: str, http_get=None) -> dict[str, Any]:
    r"""广域网抓取合作者官方文档，提取发现端点/解析结构，翻译进字典。"""










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

# L696-713：_promote_to_dict — 把翻译结果写进字典 collaborator_terms.collaborators（字典自我丰富）。
def _promote_to_dict(name: str, spec: dict[str, Any]) -> None:
    r"""把翻译结果写进字典 collaborator_terms.collaborators（字典自我丰富）。"""
    with _COLLAB_FORGE_LOCK:
        try:
            with open(_FORGE_PATH, "r", encoding="utf-8") as f:
                fg = json.load(f)
            collabs = fg.setdefault("ambiguity_dict", {}).setdefault(
                "collaborator_terms", {}).setdefault("collaborators", {})
            collabs[name] = spec
            with json_owner("kernel.large"):
                json_write_guard("kernel/large/forge.json", fg)
            tmp = _FORGE_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(fg, f, ensure_ascii=False, indent=2, sort_keys=True)
            os.replace(tmp, _FORGE_PATH)
        except (OSError, json.JSONDecodeError):
            pass

if __name__ == "__main__":
    print("已知合作者:", list(known_collaborators().keys()))















import logging
from typing import Any



AUTH_KINDS = {
    "api_key",
    "oauth",
    "device_code",
    "cli_reuse",
    "local_marker",
    "custom_proxy",
    "aws_sdk",
    "no_key",
    "service_account",
}

TRANSPORTS = {
    "openai-compatible",
    "anthropic-messages",
    "local-runtime",
    "aws-bedrock",
    "google-gemini",
}

MODEL_SOURCES = {
    "live_discovery",
    "static_catalog",
    "local_runtime",
    "generic",
}







# L771-784：classify — 对一条声明打上三轴标签
def classify(entry: dict[str, Any]) -> dict[str, str]:
    r"""对一条声明打上三轴标签"""







    return {
        "auth_kind": entry.get("auth_kind", "unknown"),
        "transport": entry.get("transport", "openai-compatible"),
        "model_source": entry.get("model_source", "generic"),
    }






_FIELDS_BY_AUTH: dict[str, list[str]] = {
    "api_key": ["api_key_input"],


    "oauth": [],
    "device_code": ["device_code_display", "poll_button"],
    "cli_reuse": ["cli_detected_label"],
    "local_marker": ["endpoint_url_input", "port_input"],
    "custom_proxy": ["base_url_input", "api_key_input", "compat_mode_selector"],


    "aws_sdk": [],
    "no_key": [],
    "service_account": ["service_account_file"],
    "unknown": ["base_url_input", "api_key_input"],
}
_FIELDS_BY_TRANSPORT: dict[str, list[str]] = {

    "anthropic-messages": [],
    "local-runtime": ["endpoint_url_input", "port_input"],

    "aws-bedrock": [],
    "google-gemini": [],
    "openai-compatible": [],
}
_FIELDS_BY_MODEL_SOURCE: dict[str, list[str]] = {
    "live_discovery": [],
    "static_catalog": ["model_input_manual"],

    "local_runtime": [],
    "generic": [],
}

# L825-841：_derive_fields — 按三轴规则推导字段列表（一维度一规则，字段取并集）。
def _derive_fields(tags: dict[str, str]) -> list[str]:
    r"""按三轴规则推导字段列表（一维度一规则，字段取并集）。"""
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

# L844-892：resolve_forge_config — 三轴键 → forge 预设
def resolve_forge_config(tags: dict[str, str], forge_config: dict) -> dict | None:
    r"""三轴键 → forge 预设"""








    taxonomy_map = forge_config.get("taxonomy_map", {})


    key = f"{tags.get('auth_kind', 'unknown')}:{tags.get('transport', 'openai-compatible')}:{tags.get('model_source', 'generic')}"


    fields = _derive_fields(tags)

    entry = taxonomy_map.get(key)
    if entry:
        template_name = entry.get("preset", "provider_card")
        behavior_chain = entry.get("behavior_chain", "")
    else:

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

# L895-926：build_method_index — 从 spider 抓到的 CLI 方法生成 method 表（{cli_id/action: 完整命令}）。
def build_method_index(clis: list[dict]) -> dict[str, str]:
    r"""从 spider 抓到的 CLI 方法生成 method 表（{cli_id/action: 完整命令}）。"""





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

        for m in c.get("methods") or []:
            action = (m.get("action") or "").strip()
            if not action:
                continue
            index[f"{cid}/{action}"] = f"{cmd} {action}".strip()


    try:
        from main.main import stk as _stk
        _stk("taxonomy.method_index", f"{len(index)} 条", ok=len(index) > 0)
    except Exception:
        pass
    return index

# L929-948：build_form_schema — 按三轴键生成 UI 字段结构
def build_form_schema(tags: dict[str, str], forge_config: dict) -> dict[str, Any]:
    r"""按三轴键生成 UI 字段结构"""



    resolved = resolve_forge_config(tags, forge_config)
    if resolved:

        _expand_field_names(resolved, forge_config)
        return resolved


    return {
        "preset_key": "generic_fallback",
        "template_name": "generic_card",
        "behavior_chain": "",
        "fields": [
            {"name": "api_key", "label": "API Key", "type": "password", "required": True},
        ],
    }

# L951-981：_expand_field_names — 把 taxonomy_map 的 fields 字符串列表展开成结构化 dict。
def _expand_field_names(resolved: dict, forge_config: dict) -> None:
    r"""把 taxonomy_map 的 fields 字符串列表展开成结构化 dict。"""





    raw_fields = resolved.get("fields", [])
    if not raw_fields:
        return
    expanded = []
    scope_map = forge_config.get("scope_mappings", {})

    for fname in raw_fields:
        if isinstance(fname, dict):

            expanded.append(fname)
            continue

        _label = fname.replace("_", " ").title()
        field_def = {"name": fname, "label": _label, "type": fname}

        mapping = scope_map.get(fname, {})
        if mapping:
            field_def["widget"] = mapping.get("widget")
            if mapping.get("hints"):
                field_def["hints"] = dict(mapping["hints"])
            if mapping.get("behavior"):
                field_def["behavior"] = mapping["behavior"]
        expanded.append(field_def)
    resolved["fields"] = expanded











import re



_URL_RULES: list[tuple[str, str, float]] = [

    (r"/providers/", "provider", 0.9),

    (r"/channels/", "channel", 0.9),

    (r"/automation/", "automation", 0.9),

    (r"/gateway/", "gateway", 0.9),

    (r"/auth", "gateway", 0.9),
    (r"auth-", "gateway", 0.9),

    (r"/cli/onboard", "onboarding", 0.9),

    (r"/cli/config", "config", 0.85),

    (r"/cli/", "cli", 0.8),

    (r"/plugins/", "plugin", 0.9),

    (r"/web/", "web_search", 0.9),

    (r"/memory", "memory", 0.9),
    (r"/embedding", "memory", 0.9),

    (r"/models", "model", 0.85),
    (r"/concepts/models", "model", 0.85),

    (r"/help/environment", "env", 0.9),
    (r"/help/configuration", "gateway", 0.85),
    (r"/help", "reference", 0.7),

    (r"/concepts/", "other", 0.5),
    (r"/reference/", "other", 0.5),
    (r"/start/", "other", 0.5),
    (r"/maturity/", "other", 0.5),
    (r"/refactor/", "other", 0.5),

    (r"/announcements/", "other", 0.4),
]



_CONTENT_SIGNALS: list[tuple[set[str], str, float, int]] = [

    ({"api key", "api provider", "model provider", "registerprovider",
      "model catalog", "provider registration"}, "provider", 0.7, 2),

    ({"embedding model", "language model", "llm", "gpt",
      "model routing", "model configuration", "modelcatalog"}, "model", 0.7, 2),

    ({"vector database", "memory storage", "embedding storage",
      "pinecone", "chroma", "memorybackend", "registermemorybackend"}, "memory", 0.7, 2),

    ({"search engine", "web search", "brave search", "search results",
      "websearchprovider", "registerwebsearchprovider"}, "web_search", 0.7, 2),

    ({"messaging platform", "instant messaging", "chat application",
      "bot token", "webhook", "channel integration"}, "channel", 0.6, 2),

    ({"authentication", "authorization", "credential", "gateway",
      "openclaw.json", "configuration reference"}, "gateway", 0.6, 2),

    ({"automated task", "scheduled job", "cron", "event-driven",
      "workflow", "trigger"}, "automation", 0.6, 2),
]

# L1066-1072：classify_by_url — 从 URL 路径推断类型。返回 (type_name, confidence)。
def classify_by_url(source_url: str) -> tuple[str, float]:
    r"""从 URL 路径推断类型。返回 (type_name, confidence)。"""
    url_lower = source_url.lower()
    for pattern, type_name, confidence in _URL_RULES:
        if re.search(pattern, url_lower):
            return type_name, confidence
    return "", 0.0

# L1075-1082：classify_by_content — 从正文内容推断类型。仅当 URL 无法判断时使用。
def classify_by_content(body: str) -> tuple[str, float]:
    r"""从正文内容推断类型。仅当 URL 无法判断时使用。"""
    body_lower = body.lower()
    for keyword_set, type_name, confidence, min_hits in _CONTENT_SIGNALS:
        hits = sum(1 for kw in keyword_set if kw in body_lower)
        if hits >= min_hits:
            return type_name, confidence
    return "", 0.0

# L1085-1129：classify_url_type — 综合分类：URL 优先 → 内容辅助 → 段落名兜底。
def classify_url_type(source_url: str, section_name: str = "", body: str = "") -> tuple[str, float, str]:
    r"""综合分类：URL 优先 → 内容辅助 → 段落名兜底。"""






    url_type, url_conf = classify_by_url(source_url)
    if url_type and url_conf >= 0.8:
        return url_type, url_conf, "url"


    if body:
        content_type, content_conf = classify_by_content(body)
        if content_type:

            if url_type and url_type == content_type:
                return content_type, max(url_conf, content_conf), "url+content"
            return content_type, content_conf, "content"


    if url_type:
        return url_type, url_conf, "url"


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


    return "other", 0.0, "fallback"




































import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)




_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FORGE_PATH = os.path.join(_BASE_DIR, "kernel", "large", "forge.json")
_AMBIGUITY = None
_AMBIGUITY_STAMP = None

# L1183-1203：_load_ambiguity — 懒加载 forge.json 的 ambiguity_dict（按文件戳失效，无兜底表）。
def _load_ambiguity() -> dict:
    r"""懒加载 forge.json 的 ambiguity_dict（按文件戳失效，无兜底表）。"""






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


_AGGREGATE_PAGES = {
    "index", "models", "overview", "readme", "auth", "authentication",
    "getting-started", "quickstart",
}


_URL_ROUTES: list[tuple[str, str]] = [


    (r"/plugins/hooks", "mechanism"),
    (r"/plugins/sdk-overview/events-and-hooks", "mechanism"),



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

# L1236-1295：_extract_local_backends — 从 gateway/local-models.md 的 "Pick a backend" 表读本地后端清单。
def _extract_local_backends(raw_text: str) -> set[str]:
    r"""从 gateway/local-models.md 的 "Pick a backend" 表读本地后端清单。"""







    backends: set[str] = set()

    m = re.search(
        r"Source:\s*https://docs\.openclaw\.ai/gateway/local-models(.*?)(?=Source:\s*https?://)",
        raw_text, re.DOTALL,
    )
    if not m:
        return backends
    body = m.group(1)

    def _norm(name: str) -> str:
        r"""归一化合作者名称：小写 + 去空格，保留连字符。"""
        n = name.strip().lower().replace(" ", "")

        if re.match(r"^[a-z0-9][a-z0-9-]*$", n):
            return n
        return ""


    for link in re.findall(r"\[[^\]]*\]\(/providers/([^)]+)\)", body):
        slug = link.strip().strip("`").split("#")[0]
        n = _norm(slug)
        if n and not _is_aggregate(n):
            backends.add(n)



    for line in body.splitlines():
        if "|" not in line:
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 3:
            continue
        name_cell = cells[1]

        if name_cell.lower() in ("backend", "") or set(name_cell) <= {"-", " "}:
            continue

        if "proxy" in name_cell.lower() or "custom" in name_cell.lower():
            continue

        if "](/providers/" in name_cell:
            continue
        for part in re.split(r"[/、]", name_cell):
            n = _norm(part)


            if n and n not in ("custom", "oai-proxy", "openai-compatible", "proxy"):
                if len(n) >= 3:
                    backends.add(n)
    return backends

# L1298-1304：_route_type — 按 URL 大类分流，返回消费端类型。
def _route_type(source_url: str) -> str:
    r"""按 URL 大类分流，返回消费端类型。"""
    url = source_url.lower()
    for pattern, ttype in _URL_ROUTES:
        if re.search(pattern, url):
            return ttype
    return ""

# L1307-1358：resolve_install_kind — 广域网分流：判定一个预装名字是 skill 还是 plugin。
def resolve_install_kind(name: str, http_get=None) -> str:
    r"""广域网分流：判定一个预装名字是 skill 还是 plugin。"""


















    if http_get is None:
        return ""
    slug = name.split("/")[-1].lower()

    def _is_npm_plugin() -> bool:
        try:
            body = http_get(f"https://registry.npmjs.org/{name}")
            return bool(body and body.strip().startswith("{"))
        except Exception:
            return False



    if _is_npm_plugin():
        return "plugin"


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

# L1361-1364：_last_slug — URL 最后一段作为实体 id（如 /providers/deepseek → deepseek）。
def _last_slug(source_url: str) -> str:
    r"""URL 最后一段作为实体 id（如 /providers/deepseek → deepseek）。"""
    m = re.search(r"/([^/\s#]+)/?$", source_url.rstrip("/"))
    return (m.group(1) if m else "").strip()

# L1367-1368：_is_aggregate — _is_aggregate
def _is_aggregate(slug: str) -> bool:
    return slug.lower() in _AGGREGATE_PAGES

# L1371-1382：_extract_supported_channels — 从 llms-full.txt 的「Supported channels」段提取频道实体白名单。
def _extract_supported_channels(raw_text: str) -> set[str]:
    r"""从 llms-full.txt 的「Supported channels」段提取频道实体白名单。"""






    m = re.search(r"## Supported channels(.*?)## ", raw_text, re.DOTALL)
    if not m:
        return set()
    return set(re.findall(r"- \[[^\]]*\]\(/channels/([a-z-]+)\)", m.group(1)))





# L1389-1408：parse_frontmatter — 解析 YAML frontmatter（--- 包裹的那段），返回 dict。
def parse_frontmatter(body: str) -> dict[str, Any]:
    r"""解析 YAML frontmatter（--- 包裹的那段），返回 dict。"""








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

# L1411-1441：parse_property_table — 解析 Property 表（规范的 | key | value | 两列表格）。
def parse_property_table(body: str) -> dict[str, str]:
    r"""解析 Property 表（规范的 | key | value | 两列表格）。"""




    result: dict[str, str] = {}
    in_table = False
    for line in body.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            if in_table:
                break
            continue
        cells = [c.strip() for c in s.split("|")[1:-1]]
        if not cells or len(cells) < 2:
            continue

        joined = "|".join(c.lower() for c in cells)
        if any(kw in joined for kw in ("property", "value", "---", ":-")):
            in_table = True
            continue
        if not in_table:
            continue
        key, val = cells[0], cells[1]


        val = val.strip()
        if key:
            result[key] = val
    return result

# L1444-1469：_find_prop — 从 Property 表里查找字段值，用歧义词典归一化字段名。
def _find_prop(prop: dict[str, str], *keywords: str) -> str:
    r"""从 Property 表里查找字段值，用歧义词典归一化字段名。"""





    amb = _load_ambiguity()
    field_aliases = amb.get("field_aliases", {})

    for kw in keywords:

        candidates = [kw]
        if kw in field_aliases:
            candidates.extend(field_aliases[kw])

        for key, val in prop.items():
            k = key.lower().strip()
            for cand in candidates:
                cand_l = cand.lower().strip()



                if re.search(rf"\b{re.escape(cand_l)}\b", k):
                    return val.strip()
    return ""

# L1472-1484：extract_plugin_package — 从 ## Install plugin 节提取官方插件包名。
def extract_plugin_package(body: str) -> str:
    r"""从 ## Install plugin 节提取官方插件包名。"""




    m = re.search(r"openclaw\s+plugins\s+install\s+(@openclaw/[\w@./-]+)", body)
    if m:
        return m.group(1).strip()
    m = re.search(r"npm\s+(?:install|i)\s+(-g\s+)?(@openclaw/[\w@./-]+)", body)
    if m:
        return m.group(2).strip()
    return ""

# L1487-1495：extract_env_vars — 提取反引号包裹的 _API_KEY / _TOKEN / _SECRET 环境变量。
def extract_env_vars(body: str) -> list[str]:
    r"""提取反引号包裹的 _API_KEY / _TOKEN / _SECRET 环境变量。"""
    vars_found = re.findall(r"`([A-Z][A-Z0-9_]{2,})`", body)
    seen: list[str] = []
    for v in vars_found:
        if any(v.endswith(suf) for suf in ("_API_KEY", "_TOKEN", "_SECRET", "_KEY", "_ID")):
            if v not in seen:
                seen.append(v)
    return seen

# L1498-1511：extract_auth_choice — 提取 onboard 的 --auth-choice 值（官方 provider 配 key 的权威命令参数）。
def extract_auth_choice(body: str) -> str:
    r"""提取 onboard 的 --auth-choice 值（官方 provider 配 key 的权威命令参数）。"""








    m = re.search(r"--auth-choice\s+([a-z][\w-]*-api-key)", body)
    if m:
        return m.group(1)
    return ""

# L1514-1559：extract_model_table — 从 ## Built-in catalog 等节的表格提取模型目录。
def extract_model_table(body: str) -> list[dict[str, Any]]:
    r"""从 ## Built-in catalog 等节的表格提取模型目录。"""
    models: list[dict[str, Any]] = []

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





# L1566-1593：_parse_provider_ids — 解析 Provider 字段，提取干净的 provider id 列表。
def _parse_provider_ids(raw_ids: str) -> list[str]:
    r"""解析 Provider 字段，提取干净的 provider id 列表。"""










    without_parens = re.sub(r"\([^)]*\)", " ", raw_ids)

    ids: list[str] = []
    for segment in without_parens.split(","):
        segment = segment.strip()
        if not segment:
            continue

        backticked = re.findall(r"`([^`]+)`", segment)
        if backticked:
            pid = backticked[0].strip()
        else:
            pid = segment.strip()
        if pid and re.match(r"^[a-zA-Z][a-zA-Z0-9_.-]*$", pid):
            ids.append(pid)
    return ids

# L1596-1635：_normalize_api — 把 Property 表 API 字段的杂文归一成官方标准 request adapter。
def _normalize_api(raw: str) -> str:
    r"""把 Property 表 API 字段的杂文归一成官方标准 request adapter。"""









    if not raw:
        return ""
    text = raw.strip()
    amb = _load_ambiguity()
    api_signals = amb.get("api_signals") or {}


    low = text.lower()
    for std, words in api_signals.items():
        if std.startswith("_"):
            continue
        for w in words:
            if str(w).lower() in low:
                return std


    for std in api_signals:
        if std.startswith("_"):
            continue
        if re.search(r"`" + re.escape(std) + r"`", text):
            return std


    for std in api_signals:
        if std.startswith("_"):
            continue
        if std in text:
            return std
    return ""

# L1638-1654：_api_field — 取 provider 的 api 协议，Property 表优先，正文兜底。
def _api_field(prop: dict[str, str], body: str) -> str:
    r"""取 provider 的 api 协议，Property 表优先，正文兜底。"""




    raw = prop.get("API", "")
    if raw.strip():
        norm = _normalize_api(raw)
        if norm:
            return norm

    for m in re.findall(r'api:\s*["\']([^"\']+)["\']', body, re.IGNORECASE):
        norm = _normalize_api(m.strip())
        if norm:
            return norm
    return _normalize_api(raw)

# L1657-1669：_dynamic_by — 判本地服务的发现策略：implicit_env / wildcard。
def _dynamic_by(body: str) -> str:
    r"""判本地服务的发现策略：implicit_env / wildcard。"""







    low = body.lower()
    if re.search(r"##\s*model discovery \(implicit provider\)", low):
        return "implicit_env"
    return "wildcard"

# L1672-1741：_classify_auth_kind — 把 provider 的认证形态细分到 9 种 authKind（读官方文档权威信号，不扫正文泛化词）。
def _classify_auth_kind(body: str, env_vars: list[str], base_url: str, auth_raw: str = "") -> str:
    r"""把 provider 的认证形态细分到 9 种 authKind（读官方文档权威信号，不扫正文泛化词）。"""

















    auth_text = (auth_raw or "").lower()


    amb = _load_ambiguity()
    auth_signals = amb.get("auth_signals", {})

    def _hit(signal_kind: str) -> bool:
        r"""auth_text 是否命中词典里 signal_kind 的任一信号词。"""
        for word in auth_signals.get(signal_kind, []):
            if word.lower() in auth_text:
                return True
        return False


    has_api_key_env = any(
        v.endswith("_API_KEY") or v.endswith("_SECRET") or v.endswith("_TOKEN")
        for v in env_vars
    )
    has_service_acct_env = any(
        "SERVICE_ACCOUNT" in v or "GOOGLE_APPLICATION_CREDENTIALS" in v
        for v in env_vars
    )


    if has_service_acct_env or _hit("service_account"):
        return "service_account"



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

    if base_url and re.search(r"(127\.0\.0\.1|localhost|0\.0\.0\.0)", base_url, re.IGNORECASE):
        if not has_api_key_env:
            return "local_marker"

    if base_url and not has_api_key_env:
        return "custom_proxy"
    if has_api_key_env:
        return "api_key"
    return "unknown"

# L1744-1893：spider_provider — 按 provider 文档骨架提取：frontmatter + Property 表 + plugin + env + models。
def spider_provider(slug: str, body: str, source_url: str, local_backends: set[str] | None = None) -> dict[str, Any]:
    r"""按 provider 文档骨架提取：frontmatter + Property 表 + plugin + env + models。"""



    fm = parse_frontmatter(body)
    prop = parse_property_table(body)
    plugin_pkg = extract_plugin_package(body)
    local_backends = local_backends or set()







    raw_ids = _find_prop(prop, "provider id", "provider")
    if not raw_ids or len(raw_ids) > 200:
        raw_ids = slug
    provider_ids = _parse_provider_ids(raw_ids)
    if not provider_ids:
        provider_ids = [slug]










    plugin_kind = "unknown"






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

        pass
    elif plugin_pkg:
        plugin_kind = "official"

    elif ("not a bundled" in plugin_raw or "not an openclaw plugin" in plugin_raw
          or plugin_raw.startswith("none") or "config-only" in plugin_raw):
        plugin_kind = "none"
    elif "bundled" in plugin_raw:
        plugin_kind = "bundled"
    elif "official" in plugin_raw or "external" in plugin_raw:
        plugin_kind = "official"



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




    if not auth_choice:
        for _pid in provider_ids:
            _ac = _load_ambiguity().get("auth_choices", {}).get(_pid, "")
            if _ac:
                auth_choice = _ac
                break


    default_model = prop.get("Suggested chat default") or prop.get("Default model") or ""
    if not default_model and models:
        default_model = models[0]["ref"]








    base_url = _find_prop(prop, "base url", "endpoint")

    base_url = base_url.strip("`").strip()


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



    auth_raw = _find_prop(prop, "auth")
    auth_kind = _classify_auth_kind(body, env_vars, base_url, auth_raw)


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
            "_body": body,
        })
    return entries

# L1896-1925：_extract_channel_commands — 从 channel 文档正文抓接入命令（openclaw channels login/add / plugins install）。
def _extract_channel_commands(body: str) -> list[dict[str, str]]:
    r"""从 channel 文档正文抓接入命令（openclaw channels login/add / plugins install）。"""








    commands: list[dict[str, str]] = []
    seen: set[str] = set()
    for line in body.splitlines():
        s = line.strip()
        if not s.startswith("openclaw "):
            continue
        s = re.split(r"\s+#", s, 1)[0].rstrip("\\").strip()
        if not s or s in seen:
            continue


        if not re.search(r"openclaw (channels (login|add)|plugins install)", s):
            continue
        if re.search(r"channels (status|list|remove|resolve|capabilities|logs|logout)", s):
            continue
        if "--account " in s or "--force" in s or "@legacy" in s:
            continue
        seen.add(s)
        commands.append({"cmd": s})
    return commands

# L1928-1953：spider_channel — 按 channel 文档骨架提取。region 从字典 channel_region 长出来（不硬编 id）。
def spider_channel(slug: str, body: str, source_url: str) -> dict[str, Any]:
    r"""按 channel 文档骨架提取。region 从字典 channel_region 长出来（不硬编 id）。"""
    fm = parse_frontmatter(body)
    env_vars = extract_env_vars(body)


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

# L1956-2001：spider_gateway — 按 gateway 文档骨架提取；RPC 方法名按文档里的代码段抽出来。
def spider_gateway(slug: str, body: str, source_url: str) -> dict[str, Any]:
    r"""按 gateway 文档骨架提取；RPC 方法名按文档里的代码段抽出来。"""









    fm = parse_frontmatter(body)
    methods: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in re.finditer(r"`([a-z][a-z0-9]*)((?:\.[a-z][a-zA-Z0-9]*)+)`", body):
        family, rest = m.group(1), m.group(2).strip(".")
        segs = rest.split(".")
        if len(segs) == 1 and segs[0] in _gatewayMethodVerbs:
            action = segs[0]
        elif len(segs) == 2 and segs[0] == "files" and segs[1] in _gatewayMethodVerbs:
            action = "files." + segs[1]
        else:
            continue
        name = f"{family}.{action}"
        if name in seen:
            continue
        seen.add(name)
        item: dict[str, Any] = {
            "name": name,
            "family": family,
            "action": action,
            "transport": "rpc",
            "source_url": source_url,
        }
        cli = f"openclaw {family} add"
        if cli in body:
            item["cli"] = cli
        methods.append(item)
    return {
        "id": slug,
        "source_url": source_url,
        "section_name": fm.get("title", slug),
        "summary": fm.get("summary", ""),
        "type": "gateway",
        "methods": methods,
    }

# L2004-2059：spider_plugin — 从插件文档页提取 plugin 语义（package 名 + 它注册的 provider id）。
def spider_plugin(slug: str, body: str, source_url: str) -> dict[str, Any]:
    r"""从插件文档页提取 plugin 语义（package 名 + 它注册的 provider id）。"""







    fm = parse_frontmatter(body)
    package = ""
    m = re.search(r"Package:\s*`?(@?[\w@./-]+)`?", body)
    if m:
        package = m.group(1).strip("`")



    install_route = ""
    rm = re.search(r"Install route:\s*([^\n]+)", body)
    if rm:
        install_route = rm.group(1).strip().lower()




    provider_ids: list[str] = []


    capabilities: list[str] = []
    surface_m = re.search(r"##\s*Surface\s*\n(.*?)(?=\n##\s|\Z)", body, re.DOTALL | re.IGNORECASE)
    if surface_m:

        pm = re.search(r"providers?:\s*([^;]+)", surface_m.group(1), re.IGNORECASE)
        if pm:
            raw_ids = pm.group(1).replace("`", "").strip()
            provider_ids = [p.strip() for p in raw_ids.split(",") if p.strip()]

        cm = re.search(r"contracts?:\s*([^;\n]+)", surface_m.group(1), re.IGNORECASE)
        if cm:
            raw_caps = cm.group(1).replace("`", "").strip()
            capabilities = [c.strip() for c in raw_caps.split(",") if c.strip()]


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

# L2062-2114：_extract_memory_switches — 从 memory 类文档（如 dreaming.md）正文抓「能力开关」。
def _extract_memory_switches(body: str) -> dict[str, Any]:
    r"""从 memory 类文档（如 dreaming.md）正文抓「能力开关」。"""















    switches: dict[str, Any] = {}


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





    for name, actions in cmds.items():
        if "on" not in actions or "off" not in actions:
            continue
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

# L2117-2188：_extract_memory_knobs — 从 memory 配置文档（如 memory-config.md）正文提取「可操作 knob」。
def _extract_memory_knobs(body: str) -> list[dict[str, Any]]:
    r"""从 memory 配置文档（如 memory-config.md）正文提取「可操作 knob」。"""










    knobs: list[dict[str, Any]] = []



    _decl_re = re.compile(
        r"(?:configured|all)\s+(?:under\s+)?`([a-z][\w.-]*(?:\.[\w.-]+)+)`",
        re.I,
    )

    _base_re = re.compile(
        r"defaults?\s+use\s+`([a-z][\w.-]*(?:\.[\w.-]+)+)`",
        re.I,
    )



    current_path = ""
    for line in body.splitlines()[:60]:
        m = _base_re.search(line.strip())
        if m:
            current_path = m.group(1)
            break

    in_table = False

    for line in body.splitlines():
        s = line.strip()


        m = _decl_re.search(s)
        if m:
            current_path = m.group(1)


        if s.startswith("|") and "Key" in s and ("Type" in s or "Default" in s):
            in_table = True
            continue


        if s.startswith("|") and re.match(r"^\|[\s\-|]+\|$", s):
            continue


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

# L2191-2223：_extract_memory_tiers — 从 memory 架构文档（如 memory-architecture.md）正文提取「五层 tier」。
def _extract_memory_tiers(body: str) -> list[dict[str, Any]]:
    r"""从 memory 架构文档（如 memory-architecture.md）正文提取「五层 tier」。"""









    tiers: list[dict[str, Any]] = []
    in_table = False
    for line in body.splitlines():
        s = line.strip()

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

                files = re.findall(r"`([\w.-]+\.md)`", surface)
                if tier_name and tier_name not in ("Tier",):
                    tiers.append({"tier": tier_name, "files": files})
        else:
            in_table = False
    return tiers

# L2226-2264：_extract_memory_providers — 从 memory 搜索文档（如 memory-search.md）正文提取「embedding provider 清单」。
def _extract_memory_providers(body: str) -> list[dict[str, Any]]:
    r"""从 memory 搜索文档（如 memory-search.md）正文提取「embedding provider 清单」。"""










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

# L2267-2292：spider_memory — spider_memory
def spider_memory(slug: str, body: str, source_url: str) -> dict[str, Any]:
    fm = parse_frontmatter(body)
    entry = {
        "id": slug,
        "source_url": source_url,
        "section_name": fm.get("title", slug),
        "summary": fm.get("summary", ""),
        "type": "memory",
    }

    switches = _extract_memory_switches(body)
    if switches:
        entry["switches"] = switches

    knobs = _extract_memory_knobs(body)
    if knobs:
        entry["knobs"] = knobs

    tiers = _extract_memory_tiers(body)
    if tiers:
        entry["tiers"] = tiers

    providers = _extract_memory_providers(body)
    if providers:
        entry["providers"] = providers
    return entry

# L2295-2303：spider_automation — spider_automation
def spider_automation(slug: str, body: str, source_url: str) -> dict[str, Any]:
    fm = parse_frontmatter(body)
    return {
        "id": slug,
        "source_url": source_url,
        "section_name": fm.get("title", slug),
        "summary": fm.get("summary", ""),
        "type": "automation",
    }

# L2306-2382：_parse_session_contract — 解析 web/urls.md 的 session URL 契约（namespace / reserved 字 / 转义标记 / 路由表）。
def _parse_session_contract(body: str) -> dict[str, Any] | None:
    r"""解析 web/urls.md 的 session URL 契约（namespace / reserved 字 / 转义标记 / 路由表）。"""










    namespaces: list[str] = []
    m_ns = re.search(r"<namespace>`\s*is\s*either\s+(.+?)\.", body)
    if m_ns:
        namespaces = re.findall(r"/([a-z-]+)", m_ns.group(1))


    reserved_rest: list[str] = []
    m_res = re.search(
        r"reserved single-segment literal rest names are\s+(.+?)\.",
        body, re.DOTALL,
    )
    if m_res:
        reserved_rest = re.findall(r"`([a-z-]+)`", m_res.group(1))


    escape_markers: list[str] = []
    for mark in re.findall(r"`(~[a-z]+)`", body):
        if mark not in escape_markers:
            escape_markers.append(mark)


    routes: list[dict[str, Any]] = []
    in_route_table = False
    for line in body.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            if in_route_table:
                break
            continue
        cells = [c.strip() for c in s.split("|")[1:-1]]
        if not cells or len(cells) < 2:
            continue

        joined = "|".join(c.lower() for c in cells)
        if "canonical path" in joined or "page" in joined or "---" in joined or ":-" in joined:
            in_route_table = True
            continue
        if not in_route_table:
            continue

        page = cells[0] if len(cells) > 0 else ""
        path = cells[1] if len(cells) > 1 else ""
        aliases = cells[2] if len(cells) > 2 else ""
        params = cells[3] if len(cells) > 3 else ""

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

# L2385-2399：spider_web_search — spider_web_search
def spider_web_search(slug: str, body: str, source_url: str) -> dict[str, Any]:
    fm = parse_frontmatter(body)
    entry = {
        "id": slug,
        "source_url": source_url,
        "section_name": fm.get("title", slug),
        "summary": fm.get("summary", ""),
        "type": "web_search",
    }

    if slug == "urls":
        contract = _parse_session_contract(body)
        if contract:
            entry["session_contract"] = contract
    return entry

# L2402-2410：spider_tool — spider_tool
def spider_tool(slug: str, body: str, source_url: str) -> dict[str, Any]:
    fm = parse_frontmatter(body)
    return {
        "id": slug,
        "source_url": source_url,
        "section_name": fm.get("title", slug),
        "summary": fm.get("summary", ""),
        "type": "tool",
    }








_CLI_GRAMMAR: dict[str, Any] = {
    "usage_prefix": "openclaw",
    "effect_verbs_infix": ["writes", "sets", "updates", "stores", "persists"],
    "effect_verbs_leading": ["Adds/updates", "Writes", "Sets", "Updates", "Stores"],
    "comment_cutters": ["#", "|"],
}
_CONFIG_PATH_PAT = re.compile(r"^[a-z][\w.\-]*(\.[\w.\-*<>]+)+", re.I)



_gatewayMethodVerbs = {"create", "update", "delete", "list", "get", "set"}

# L2432-2434：_cli_grammar — CLI 方法的书写规律（提取逻辑，代码常量）。
def _cli_grammar() -> dict[str, Any]:
    r"""CLI 方法的书写规律（提取逻辑，代码常量）。"""
    return _CLI_GRAMMAR

# L2437-2450：_cli_patterns — 按字典里的规律现场构造提取式（用法行 / 中缀动词 / 前置动词）。
def _cli_patterns() -> tuple[re.Pattern, re.Pattern, re.Pattern]:
    r"""按字典里的规律现场构造提取式（用法行 / 中缀动词 / 前置动词）。"""
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

# L2453-2513：_extract_options_table — 从官方 CLI 文档的 Options 表格抓 flag，按子命令节归属。
def _extract_options_table(body: str, slug: str) -> dict[str, list[str]]:
    r"""从官方 CLI 文档的 Options 表格抓 flag，按子命令节归属。"""











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

# L2516-2553：_parse_cli_usage — 把命令行剩余部分拆成 (动作链, 位置参数, 选项)。
def _parse_cli_usage(rest: str) -> tuple[list[str], list[str], list[str]]:
    r"""把命令行剩余部分拆成 (动作链, 位置参数, 选项)。"""








    g = _cli_grammar()
    for cutter in (g.get("comment_cutters") or ["#", "|"]):
        rest = re.split(r"\s+" + re.escape(str(cutter)), rest, 1)[0]

    opts: list[str] = []
    args: list[str] = []
    actions: list[str] = []

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

# L2556-2647：spider_cli — 从 CLI 文档提取「有哪些操作方法、各自怎么调、落到哪个配置路径」。
def spider_cli(slug: str, body: str, source_url: str) -> dict[str, Any]:
    r"""从 CLI 文档提取「有哪些操作方法、各自怎么调、落到哪个配置路径」。"""











    fm = parse_frontmatter(body)
    usage_pat, infix_pat, leading_pat = _cli_patterns()




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


    options_by_action = _extract_options_table(body, slug)
    if options_by_action:
        for act, flgs in options_by_action.items():

            for u in usages:
                if (u.get("action") or "").split()[0] == act:
                    u.setdefault("options", [])
                    for f in flgs:
                        if f not in u["options"]:
                            u["options"].append(f)
                    u["options"] = sorted(u["options"])
                    break


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

















_HOOK_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(?::[a-z][a-z0-9_]*)?$")
# L2667-2675：_slug — 把官方一句话归一成我们自己的标签（不从手写表里查）。
def _slug(text: str, max_len: int = 64) -> str:
    r"""把官方一句话归一成我们自己的标签（不从手写表里查）。"""




    s = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "_", (text or "").strip().lower())
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:max_len]


# L2679-2694：mechanism_label — 取某个原始措辞的标签：词典优先（可人工纠正），没有就现场 slug 长一个。
def mechanism_label(bucket: str, raw: str) -> str:
    r"""取某个原始措辞的标签：词典优先（可人工纠正），没有就现场 slug 长一个。"""




    r = (raw or "").strip()
    if not r:
        return ""
    try:
        known = (_lex_load_ambiguity().get("mechanism_terms") or {}).get(bucket) or {}
        if r in known:
            return str(known[r])
    except Exception:
        pass
    return _slug(r)


# L2698-2715：observe_mechanism_term — 规则一：观察一个官方原始措辞（性质/失败策略/任务/分组）→ 候选池 + 返回标签。
def observe_mechanism_term(bucket: str, raw: str) -> str:
    r"""规则一：观察一个官方原始措辞（性质/失败策略/任务/分组）→ 候选池 + 返回标签。"""
    r = (raw or "").strip()
    if not r:
        return ""
    label = mechanism_label(bucket, r)
    try:
        known = (_lex_load_ambiguity().get("mechanism_terms") or {}).get(bucket) or {}
        if r in known:
            return label
        c = _load_candidates()
        pool = c.setdefault("mechanism_terms", {}).setdefault(bucket, {})
        rec = pool.get(r) if isinstance(pool.get(r), dict) else {}
        pool[r] = {"label": label, "hits": int(rec.get("hits", 0)) + 1}
        _save_candidates(c)
    except Exception:
        pass
    return label





# L2722-2735：_allows_from — _allows_from
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




_MECH_RAW_PATH = os.path.join(_BASE_DIR, "kernel", "large", "mechanisms_raw.json")



_MECH_RAW_ACC: list[dict] = []


# L2748-2764：_save_mechanisms_raw — 把机制面的原始文本落成本机运行期文件（mechanisms_raw.json）。
def _save_mechanisms_raw(entries: list[dict]) -> None:
    r"""把机制面的原始文本落成本机运行期文件（mechanisms_raw.json）。"""





    if not entries:
        return
    try:
        import datetime as _dt
        data = {"updated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                "pages": entries}
        with open(_MECH_RAW_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception as e:
        logger.warning("filter._saveMechanismsRaw: 写运行期原文失败 %s", e)

_BUDGET_RE = re.compile(r"^\d+\s*(?:seconds?|s|秒|毫秒)", re.IGNORECASE)


# L2770-2905：_extract_hook_mechanism — 从钩子机制页正文抽反推声明格式所需的全部官方事实：
def _extract_hook_mechanism(body: str) -> dict[str, Any]:
    r"""从钩子机制页正文抽反推声明格式所需的全部官方事实："""













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

        if _BUDGET_RE.match(second):
            for n in names:
                budgets.setdefault(n, second)
                if third and not third.lower().startswith(("hook", "---")):
                    fl = observe_mechanism_term("failure", third)
                    if fl:
                        failures.setdefault(n, fl)
                        raw_terms[f"failure:{fl}"] = third
            continue





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


    emit_caveat = bool(re.search(r"not a promise that every runtime emits", flat))
    caveat_text = ""
    if emit_caveat:
        m_cav = re.search(r"(The catalog is the registration API.{0,200}?hook\.)", flat)
        caveat_text = m_cav.group(1) if m_cav else ""

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

            if lab:
                for n in names:
                    caveats.setdefault(n, lab)
                    raw_caveat.setdefault(n, sent.strip()[:600])
    if _hook_names_seen and not caveats:

        logger.warning("[STAKE-MECH] 钩子页出现 %d 个钩子名但没命中任何派发告警措辞"
                       "（官方措辞可能已变，需核对）", _hook_names_seen)
    reasons: list[str] = []
    m = re.search(r"session_end\.reason\D{0,40}?(?:one of|其中之一)(.{0,400})",
                  body or "", re.DOTALL | re.IGNORECASE)
    if m:
        for r in re.findall(r"`([a-z_]+)`", m.group(1)):

            if r not in reasons and r != "session_end":
                reasons.append(r)
    drain = ""
    dm = re.search(r"(\d+)[- ]second total `session_end` drain budget", body or "")
    if dm:
        drain = f"{dm.group(1)} second total"
    return {"hooks": hooks, "budgets": budgets, "failures": failures, "chooser": chooser,
            "perm_gate": perm, "emit_caveat": emit_caveat, "dispatch_caveats": caveats,
            "session_end_reasons": reasons, "session_end_drain": drain,

            "_raw": {"descs": raw_desc, "caveats": raw_caveat, "terms": raw_terms,
                     "emit_caveat": caveat_text}}


# L2909-2934：spider_mechanism — 机制条目：官方机制（钩子名/性质/预算/reason 值域）+ 官方出处。
def spider_mechanism(slug: str, body: str, source_url: str) -> dict[str, Any]:
    r"""机制条目：官方机制（钩子名/性质/预算/reason 值域）+ 官方出处。"""



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

    raw = mech.get("_raw") or {}
    if any(raw.values()):
        _MECH_RAW_ACC.append({
            "id": slug,
            "source_url": source_url,
            "section_name": entry.get("section_name", slug),
            **raw,
        })
    return entry


# L2938-3000：mechanism_facts — 把机制条目并成一张事实表：钩子名 → 性质/预算/官方出处。
def mechanism_facts(manifest: dict) -> dict[str, Any]:
    r"""把机制条目并成一张事实表：钩子名 → 性质/预算/官方出处。"""





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


# L3004-3067：derive_declaration_schema — 反推声明格式：一份插件声明该有哪些字段、每个字段的取值域从哪来。
def derive_declaration_schema(facts: dict | None = None) -> dict[str, Any]:
    r"""反推声明格式：一份插件声明该有哪些字段、每个字段的取值域从哪来。"""














    f = facts or {}
    hooks = f.get("hooks") or {}
    budgets, failures = f.get("budgets") or {}, f.get("failures") or {}
    perm = f.get("perm_gate") or {}

    def _allows(kind: str) -> list[str]:
        return _allows_from(kind)

    hook_fields = {}
    source_of = (facts or {}).get("source_of") or {}
    dispatch_caveats = (facts or {}).get("dispatch_caveats") or {}



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


# L3071-3081：_compliant_hooks — 官方钩子目录里性质够用的钩子，按该 role 该用的那个排前面。
def _compliant_hooks(hf: dict, needs: str, role: str = "") -> list:
    r"""官方钩子目录里性质够用的钩子，按该 role 该用的那个排前面。"""







    return [k for k, v in (hf or {}).items()
            if isinstance(v, dict) and needs in (v.get("allows") or [])]


# L3085-3182：derive_declaration — 需求 → 声明：把"我们要求这个插件起的作用"经官方格式反推成一份完整声明。
def derive_declaration(design: dict, schema: dict) -> dict[str, Any]:
    r"""需求 → 声明：把"我们要求这个插件起的作用"经官方格式反推成一份完整声明。"""












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



        "blocking_gaps": [g for g in gaps if isinstance(g, dict) and g.get("blocking")],
        "notes": notes,
        "emit_caveat": schema.get("emit_caveat", ""),
        "lifecycle": design.get("lifecycle") or "critical",
    }


# L3186-3220：plugin_runtime — 声明 → 运行规格：把声明编译成插件宿主能直接照办的东西。
def plugin_runtime(declarations: list[dict]) -> dict[str, Any]:
    r"""声明 → 运行规格：把声明编译成插件宿主能直接照办的东西。"""





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

            "commands": [dict(c) for c in (rec.get("commands") or []) if isinstance(c, dict)],
            "commandGaps": [dict(g) for g in (rec.get("command_gaps") or []) if isinstance(g, dict)],
            "lifecycle": str(rec.get("lifecycle") or decl.get("lifecycle") or "critical"),
            "install": (rec.get("satisfied") or {}).get("install_route", ""),
            "scope": "card-only",
            "gaps": decl.get("gaps") or [],

            "ok": not _blocking,
            "blockingGaps": _blocking,
            "notes": decl.get("notes") or [],
        }
    return out


# L3224-3256：mechanism_delta — 机制变化：本轮机制事实 vs 上一版声明里的机制事实。
def mechanism_delta(prev_manifest: dict, facts: dict) -> dict[str, Any]:
    r"""机制变化：本轮机制事实 vs 上一版声明里的机制事实。"""





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

# L3273-3289：_serialize — 确定性序列化（铁律三）。
def _serialize(entries: dict[str, Any]) -> str:
    r"""确定性序列化（铁律三）。"""








    return json.dumps(
        entries,
        ensure_ascii=False,
        indent=2,
        separators=(",", ": "),
        sort_keys=True,
    )

# L3292-3496：crawl — spider 主入口：遍历所有文档段，按 URL 分流，产出 grouped 结构。
def crawl(
    raw_text: str,
    version_info: tuple[str, str] | None = None,
    deployed: dict[str, dict] | None = None,
    demands: list[dict] | None = None,
    prev_manifest: dict | None = None,
) -> dict[str, Any]:
    r"""spider 主入口：遍历所有文档段，按 URL 分流，产出 grouped 结构。"""

















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

    mechanisms: list[dict[str, Any]] = []

    seg_pattern = re.compile(
        r"(?:^#\s+.+\n)?Source:\s*(https?://\S+)", re.MULTILINE
    )
    matches = list(seg_pattern.finditer(raw_text))


    local_backends = _extract_local_backends(raw_text)


    supported_channels = _extract_supported_channels(raw_text)



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



        if _is_aggregate(slug) and ttype != "cli":
            continue

        if ttype == "channel":
            _cm = re.match(r"^https?://[^/]+/channels/([^/?#]+)/([^/?#]+)/?$", source_url)
            if _cm and (not supported_channels or _cm.group(1) in supported_channels):
                channel_subpages.setdefault(_cm.group(1), {})[_cm.group(2)] = body
                continue

        if ttype == "channel" and supported_channels and slug not in supported_channels:
            continue

        spider = _SPIDERS.get(ttype)
        if not spider:
            continue
        try:
            if ttype == "provider":
                result = spider(slug, body, source_url, local_backends)

                _observe_provider_fields(body)


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

            logger.warning("[STAKE-CRAWL] 段落解析失败已跳过：%s (%s): %s", slug, source_url, exc)
            continue




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

    _link_plugins_to_providers(entries)

    _resolve_unknown_plugins(entries)

    _finalize_outsider(entries)

    _annotate_capabilities(entries)

    _disambiguate_providers(entries, deployed)


    _facts = mechanism_facts({"mechanisms": mechanisms})
    _delta = mechanism_delta(prev_manifest or {}, _facts)


    try:
        _save_mechanisms_raw(_MECH_RAW_ACC)
    finally:

        _MECH_RAW_ACC.clear()



    satisfy_demands(entries, demands, facts=_facts, delta=_delta)

    try:
        promoted = promote_candidates()
        if promoted:
            logger.info("[LEXICON] promoted %d 词: %s", len(promoted), promoted[:10])
    except Exception as exc:

        logger.warning("[LEXICON] 候选池转正失败，本轮词典未生长: %s", exc)


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

    for p in entries["providers"]:
        p.pop("_body", None)

    result: dict[str, Any] = {"entries": entries}




    result["mechanisms"] = mechanisms
    result["mechanism_delta"] = _delta


    result["plugin_runtime"] = plugin_runtime(entries.get("custom_demands") or [])

    if version_info and version_info[0]:
        result["version"] = {
            "type": "_meta_version",
            "npm_latest": version_info[0],
            "npm_published_at": version_info[1],
        }
    return result

# L3499-3518：crawl_serialized — crawl + 确定性序列化，产出可直接写 ot.json 的字节串。
def crawl_serialized(
    raw_text: str,
    version_info: tuple[str, str] | None = None,
    deployed: dict[str, dict] | None = None,
    demands: list[dict] | None = None,
    prev_manifest: dict | None = None,
) -> str:
    r"""crawl + 确定性序列化，产出可直接写 ot.json 的字节串。"""






    try:
        return _serialize(crawl(raw_text, version_info, deployed, demands, prev_manifest))
    finally:


        _MECH_RAW_ACC.clear()

# L3521-3532：_observe_provider_fields — 心跳钩子：观察 provider 文档的 Property 表字段，把词典不认识的新字段名/
def _observe_provider_fields(body: str) -> None:
    r"""心跳钩子：观察 provider 文档的 Property 表字段，把词典不认识的新字段名/"""




    try:
        prop = parse_property_table(body)
        for key, val in prop.items():
            observe_field(key, val)
    except Exception:
        pass

# L3535-3548：_finalize_outsider — plugin 回填完成后，统一判定 outSider（无官方插件的 provider）。
def _finalize_outsider(entries: dict[str, list[dict[str, Any]]]) -> None:
    r"""plugin 回填完成后，统一判定 outSider（无官方插件的 provider）。"""






    for p in entries["providers"]:


        _plugin = p.get("plugin")
        _kind = _plugin.get("kind") if isinstance(_plugin, dict) else None
        p["outSider"] = _kind not in ("bundled", "official")

# L3551-3607：reconcile_plugin_kinds_with_deployed — 用已部署 OpenClaw 的真实插件状态，纠正 spider 抓取 + 沉淀进字典。
def reconcile_plugin_kinds_with_deployed(
    entries: dict[str, list[dict[str, Any]]],
    deployed: dict[str, dict] | None = None,
) -> list[str]:
    r"""用已部署 OpenClaw 的真实插件状态，纠正 spider 抓取 + 沉淀进字典。"""











    if not deployed:
        return []


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

            continue
        to_kind = _to_kind(info.get("origin", ""))
        if not to_kind:
            continue
        from_kind = p["plugin"].get("kind", "unknown")
        if from_kind == to_kind:
            continue

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

# L3610-3654：_annotate_capabilities — 能力类型分类：把 plugin 的 contracts（能力类型）回填到 provider。
def _annotate_capabilities(entries: dict[str, list[dict[str, Any]]]) -> None:
    r"""能力类型分类：把 plugin 的 contracts（能力类型）回填到 provider。"""














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

# L3657-3718：_disambiguate_providers — 实体消歧：用先验图标注 provider 的 alias/母体关系。
def _disambiguate_providers(
    entries: dict[str, list[dict[str, Any]]],
    deployed: dict[str, dict] | None = None,
) -> None:
    r"""实体消歧：用先验图标注 provider 的 alias/母体关系。"""










    try:
        prior_graph: dict[str, list[str]] = {}


        if deployed:
            for info in deployed.values():
                pids = info.get("providerIds") or []
                if not pids:
                    continue
                canonical = pids[0]
                prior_graph.setdefault(canonical, [])
                if len(pids) > 1:

                    merged = prior_graph[canonical] + [a for a in pids[1:] if a not in prior_graph[canonical]]
                    prior_graph[canonical] = merged


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

        logger.warning("[STAKE-DISAMB] provider 消歧失败（relation/canonical_id 未写）: %s", exc)











import hashlib

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


_MIN_KEY = 3


# L3749-3751：_norm_key — 归一 key：小写 + 只留字母数字与 CJK（去掉空格/连字符/点/下划线）。
def _norm_key(s: str) -> str:
    r"""归一 key：小写 + 只留字母数字与 CJK（去掉空格/连字符/点/下划线）。"""
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (s or "").lower())


# L3755-3761：demand_fp — 需求文本指纹：客户改了需求 ⇒ 旧满足结论作废（重判，不把旧结论贴到新需求上）。
def demand_fp(name: str, description: str) -> str:
    r"""需求文本指纹：客户改了需求 ⇒ 旧满足结论作废（重判，不把旧结论贴到新需求上）。"""




    return hashlib.sha1(f"{name}\n{description}".encode("utf-8")).hexdigest()[:8]


# L3765-3776：declaration_fp — 一条客制区声明的指纹（需求 + 消费端共用）。
def declaration_fp(rec: dict) -> str:
    r"""一条客制区声明的指纹（需求 + 消费端共用）。"""





    if str(rec.get("type") or "intent") == "intent":
        return demand_fp(str(rec.get("name") or ""), str(rec.get("description") or ""))
    core = {k: v for k, v in rec.items() if k not in ("satisfied", "reason", "fp")}
    blob = json.dumps(core, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:8]


# L3780-3796：_add_entity_keys — 把一组名字（字段原值 + 剥功能后缀版）登记进索引。同 key 已有 → 保留先到的。
def _add_entity_keys(index: dict[str, dict], values: tuple, target: dict,
                     suffixes: tuple[str, ...]) -> None:
    r"""把一组名字（字段原值 + 剥功能后缀版）登记进索引。同 key 已有 → 保留先到的。"""




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


# L3800-3850：_build_entity_index — 建「归一 key → 实体」表。
def _build_entity_index(entries: dict) -> dict[str, dict]:
    r"""建「归一 key → 实体」表。"""









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

                mother = prov.get(alias_index.get(eid, eid))
                if mother is not None:
                    target = _target("provider", mother)
            _add_entity_keys(index, tuple(e.get(f, "") for f in fields), target, suffixes)


    for alias, canonical in sorted(alias_index.items()):
        mother = prov.get(canonical)
        if mother is None:
            continue
        _add_entity_keys(index, (alias,), _target("provider", mother), suffixes)
    return index


# L3854-3996：_satisfy_consumer — 消费端声明（type=consumer）过关：身份/装法 + 机制对账 → 完整插件声明。
def _satisfy_consumer(rec: dict, plugin_index: dict[str, dict], index: dict[str, dict],
                      facts: dict | None = None, delta: dict | None = None,
                      cmdIndex: dict[str, dict] | None = None) -> None:
    r"""消费端声明（type=consumer）过关：身份/装法 + 机制对账 → 完整插件声明。"""

















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


    rec["declaration"] = derive_declaration(rec, schema)




    _specObj = spec if isinstance(spec, dict) else {}
    _cmdsSrc = rec.get("commands")
    if not isinstance(_cmdsSrc, list):
        _cmdsSrc = _specObj.get("commands")
    _declaredCmds: list = _cmdsSrc if isinstance(_cmdsSrc, list) else []
    _matchedCmd: list[dict] = []
    _cmdGaps: list[dict] = []
    for _c in _declaredCmds:
        if not isinstance(_c, dict):
            continue
        _cid = str(_c.get("id") or "")
        if not _cid:
            continue
        _found = (cmdIndex or {}).get(_cid)
        if _found is None:
            _cmdGaps.append({"id": _cid, "why": "官方文档里没抽到这条 RPC 方法"})
            continue
        _matchedCmd.append({
            "method": _found.get("name"),
            "transport": _found.get("transport", "rpc"),
            "params": list(_c.get("params") or []),
            "scope": _found.get("scope", ""),
            "source_url": _found.get("source_url", ""),
        })
    if _declaredCmds:
        rec["commands"] = _matchedCmd
        rec["command_gaps"] = _cmdGaps
        if _cmdGaps and isinstance(rec.get("declaration"), dict):
            rec["declaration"].setdefault("gaps", []).extend(
                [{"id": g["id"], "why": g["why"], "blocking": False} for g in _cmdGaps])



    rec.setdefault("auth_kind", "consumer")
    rec.setdefault("transport", "plugin")
    rec.setdefault("model_source", "local")
    rec.setdefault("lifecycle", "critical")


# L4000-4114：satisfy_demands — 把需求条目对着本次采集的官方实体做实体链接，产出「满足结果」。
def satisfy_demands(
    entries: dict[str, list[dict[str, Any]]],
    demands: list[dict] | None = None,
    facts: dict | None = None,
    delta: dict | None = None,
) -> list[dict]:
    r"""把需求条目对着本次采集的官方实体做实体链接，产出「满足结果」。"""

















    if not demands:
        return []
    index = _build_entity_index(entries)


    cmdIndex: dict[str, dict] = {}
    for g in (entries.get("gateways") or []):
        if not isinstance(g, dict):
            continue
        for m in (g.get("methods") or []):
            if isinstance(m, dict) and m.get("name") and m["name"] not in cmdIndex:
                cmdIndex[str(m["name"])] = dict(m)
    plugin_index: dict[str, dict] = {}
    if any(isinstance(d, dict) and str(d.get("type") or "intent") != "intent"
           for d in demands):

        plugin_index = _build_entity_index({"plugins": entries.get("plugins") or []})
    lex = _lex_load_ambiguity().get("demand_entities") or {}
    out: list[dict] = []
    for d in demands:
        if not isinstance(d, dict):
            continue
        rec = dict(d)


        if str(rec.get("type") or "intent") != "intent":
            _satisfy_consumer(rec, plugin_index, index, facts, delta, cmdIndex)
            out.append(rec)
            continue
        did = str(rec.get("id") or "")
        name = str(rec.get("name") or "")
        desc = str(rec.get("description") or "")
        fp = declaration_fp(rec)
        rec["fp"] = fp
        hit: dict | None = None



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

        if hit is None:
            nk = _norm_key(name)
            if len(nk) >= _MIN_KEY and nk in index:
                hit = {**index[nk], "match": "name_exact", "present": True}

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


# L4118-4136：_resolve_unknown_plugins — 对 plugin 关联后仍 unknown 的 provider，用正文精确信号兜底。
def _resolve_unknown_plugins(entries: dict[str, list[dict[str, Any]]]) -> None:
    r"""对 plugin 关联后仍 unknown 的 provider，用正文精确信号兜底。"""







    for p in entries["providers"]:

        _plugin = p.get("plugin")
        if not isinstance(_plugin, dict) or _plugin.get("kind") != "unknown":
            continue
        body = p.get("_body", "")
        if not body:
            continue
        if re.search(r"bundled\s+(?:[\w\s-]+\s+)?(?:provider|plugin|rows)", body, re.IGNORECASE):
            p["plugin"] = {"kind": "bundled", "package": ""}

# L4139-4189：_link_plugins_to_providers — 把 plugin 页的语义回填到 provider（解决 provider 文档里 plugin 信息缺失）。
def _link_plugins_to_providers(entries: dict[str, list[dict[str, Any]]]) -> None:
    r"""把 plugin 页的语义回填到 provider（解决 provider 文档里 plugin 信息缺失）。"""







    provider_by_id = {p["id"]: p for p in entries["providers"]}
    plugin_by_id = {pl["id"]: pl for pl in entries["plugins"]}

    def _kind_from_route(install_route: str) -> str:
        r"""Install route → plugin.kind（权威信号，不靠包名猜）。"""



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

            kind = "official" if pkg else "unknown"
        provider_by_id[pid]["plugin"] = {"kind": kind, "package": pkg}

    for pl in entries["plugins"]:
        pkg = pl.get("package", "")
        install_route = pl.get("install_route", "")

        for pid in pl.get("providers", []):
            _fill(pid, pkg, install_route)

        m = re.search(r"@openclaw/([\w-]+)-provider", pkg)
        if m:
            _fill(m.group(1), pkg, install_route)

        _fill(pl["id"], pkg, install_route)

if __name__ == "__main__":
    import sys
    raw = open(sys.argv[1], encoding="utf-8").read() if len(sys.argv) > 1 else ""
    result = crawl(raw)
    print(json.dumps(
        {k: len(v) for k, v in result["entries"].items()},
        ensure_ascii=False, indent=2,
    ))
