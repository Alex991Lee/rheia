
"""
large - Logic-Adaptive Reallocate-General bus Execute unit
逻辑自适应重分配通用总线执行单元

用法:
  scout 写入mani.json并发送signal
  manifest()      -> 读取mani.json并富化              返回声明/覆写
  am()            -> 调用 steak() 实例化                      返回渲染
  steak(name,decl) -> 实例化消费端                           交给 forge 渲染
  forge(instances) -> 渲染                                     返回渲染
  usm()           -> 读取mani.json                                返回生命周期报告

原则:
  无 import 驱动
  纯数据驱动管线
"""

try:  # 全局静默子进程守卫（设计约定：加载/安装链不得弹控制台）
    from lib.no_window import install as _install_no_window
    _install_no_window()
except Exception:  # noqa: BLE001
    pass

import json
import logging
import os
import sqlite3
from datetime import datetime

from kernel.filter import json_owner, json_write_guard

logger = logging.getLogger(__name__)

_BASE = os.path.dirname(os.path.abspath(__file__))
_LARGE_DIR = os.path.join(_BASE, "large")
_MANI_PATH = os.path.join(_LARGE_DIR, "mani.json")
_SIGNAL_PATH = os.path.join(_LARGE_DIR, "manifest.signal")
_MISS_PATH = os.path.join(_LARGE_DIR, "miss.json")
_FORGE_PATH = os.path.join(_LARGE_DIR, "forge.json")
_forge_config = None

# baseline_pool.db 统一放 %PROGRAMDATA%\Rheia\（与 scout.py 对齐）。
# 之前放项目根（{app}）是错的：安装目录可客制、重装会丢失、且与卸载保留逻辑错位。
_PROGRAMDATA = os.environ.get("PROGRAMDATA", "")
if _PROGRAMDATA:
    _DB_DIR = os.path.join(_PROGRAMDATA, "Rheia")
else:
    _DB_DIR = os.path.dirname(_BASE)
_DB_PATH = os.path.join(_DB_DIR, "baseline_pool.db")


def _loadForgeConfig():
    global _forge_config
    if _forge_config is None:
        with open(_FORGE_PATH, "r", encoding="utf-8") as f:
            _forge_config = json.load(f)
    return _forge_config
_LIFECYCLE_PROTECTION_THRESHOLD = 3


def _transport_from_api(api: str) -> str:
    """从官方 api 字段（request adapter 标准名）映射到 transport 家族。

    api 是官方 config-tools.md:532 明文列的标准 adapter，是 transport 的真相源。
    这里做的是「adapter 家族 → transport 语义」的归类，不硬编 provider、不查死表。
    """
    a = (api or "").lower()
    if a in ("anthropic-messages",):
        return "anthropic-messages"
    if a in ("bedrock-converse-stream", "bedrock-converse"):
        return "aws-bedrock"
    if a in ("google-generative-ai", "google-vertex"):
        return "google-gemini"
    if a in ("ollama", "openai-completions", "openai-responses",
             "openai-chatgpt-responses", "github-copilot", "azure-openai-responses"):
        return "openai-compatible"
    return "openai-compatible"


def _transport_from_plugin(package: str) -> str:
    """从官方插件包名（@openclaw/xxx-provider）推导 transport。

    bundled/official external provider 的 api 字段常为空（靠插件自带协议），
    但插件包名是官方声明，跟 api 一样是真相源。anthropic-provider → anthropic-messages。
    """
    p = (package or "").lower()
    if "anthropic" in p:
        return "anthropic-messages"
    if "bedrock" in p:
        return "aws-bedrock"
    if "gemini" in p or "google" in p:
        return "google-gemini"
    return ""

# ==============================================================
#  数据驱动核心管线
# ==============================================================

def manifest(overrides=None):
    """读mani.json -> enrich -> 检查scout信号"""
    from lib.testPole import logEvent; logEvent("manifest.enter")
    _checkSignal()
    if not os.path.exists(_MANI_PATH):
        logger.warning("large.manifest: mani.json not found")
        return {}

    declarations = _loadMani()
    if not declarations:
        return {}
    if overrides is not None and isinstance(overrides, dict):
        for name, patch in overrides.items():
            if name in declarations and isinstance(declarations[name], dict):
                declarations[name].update(patch)
        with json_owner("kernel.large"):
            json_write_guard("kernel/large/mani.json", declarations)
        with open(_MANI_PATH, "w", encoding="utf-8") as f:
            json.dump(declarations, f, ensure_ascii=False, indent=2)
        logger.info("large.manifest: applied overrides for %d entries", len(overrides))
    enriched = _enrich(declarations)
    valid_count = sum(1 for v in enriched.values()
                      if isinstance(v, dict) and "type" in v)
    if valid_count == 0 and declarations:
        logger.warning("[STAKE-CNT] large.manifest: declarations loaded but no valid entries")
   # MA-5 计数桩
    logger.info("[STAKE-CNT] manifest: %d enriched entries returned", len(enriched))
    logEvent("manifest.exit", len(enriched))
    return enriched


def _loadMani() -> dict:
    """Pure IO: read mani.json with error handling.

    自动检测格式:
      - 旧 flat 格式: {name: {type, ...}, version: ..., updated_at: ...} → 直接返回
      - 新 grouped 格式: {entries: {providers: [...]}, version: ..., updated_at: ...} → 扁平化返回
    """
    if not os.path.exists(_MANI_PATH):
        logger.warning("[STAKE-NON] large.manifest: mani.json not found")
        return {}
    try:
        with open(_MANI_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("large._loadMani: failed to read mani.json: %s", exc)
        return {}

    # ── 新 grouped 格式: 扁平化映射回旧 {name: decl} 结构 ──
    if "entries" in raw and isinstance(raw.get("entries"), dict):
        logger.info("large._loadMani: detected grouped format, flattening...")
        return _flattenNewFormat(raw)

    return raw


def _flattenNewFormat(raw: dict) -> dict:
    """将 grouped 新格式扁平化为旧 {name: decl} 结构。

    保真 auth_kind/version 元数据，使得 _enrich()/am()/usm() 零改动继续运行。
    """
    result: dict = {}

    # 1. 保留版本元数据（usm._checkSkippedVersions 依赖 version.npm_published_at）
    version_meta = raw.get("version")
    if isinstance(version_meta, dict):
        result["version"] = dict(version_meta)
    result["updated_at"] = raw.get("updated_at", "")

    # 新格式可能只有 npm_latest 缺少 npm_published_at → 从 updated_at 推导
    if isinstance(result.get("version"), dict) and "npm_published_at" not in result["version"]:
        ua = str(raw.get("updated_at", ""))
        derived = ua[:10].replace("-", "") if ua else "20260731"
        result["version"]["npm_published_at"] = derived
        logger.info("large._flattenNewFormat: derived npm_published_at=%s from updated_at", derived)

    # 2. 扁平化 providers → {provider_id: provider_decl}
    entries = raw.get("entries", {})
    providers = entries.get("providers", [])
    for p in providers:
        pid = p.get("id", "")
        if not pid:
            continue
        auth = p.get("auth", {})
        auth_kind = auth.get("kind", "unknown")
        # transport 的真相源是官方 api 字段（config-tools.md:532 的 request adapter），
        # api 空时（bundled 插件不写 api）用插件包名兜底（官方声明的真相源之二）。
        api = p.get("api", "")
        transport = _transport_from_api(api)
        if not api and transport == "openai-compatible":
            _pt = _transport_from_plugin((p.get("plugin") or {}).get("package", ""))
            if _pt:
                transport = _pt
        # model_source：本地服务靠端点自动发现；官方插件运行时发布目录；静态目录；兜底 generic。
        # 四种都从已有字段推导（官方语义），不硬编 provider。
        _pkg = (p.get("plugin") or {}).get("package", "")
        if p.get("config_shape") == "local_runtime":
            model_source = "local_runtime"
        elif _pkg:
            model_source = "live_discovery"
        elif p.get("models"):
            model_source = "static_catalog"
        else:
            model_source = "generic"

        # 保真透传 grouped 源 provider 的全部字段（源头逻辑），只覆盖派生字段。
        # 不手抄白名单——spider 未来新增的任何字段（如 auth_choice）自动透传。
        decl = dict(p)
        decl.update({
            "type": "provider",
            "id": pid,
            "display_name": p.get("section_name", ""),
            # 三轴标签 — taxonomy.classify() 优先读这些直接标签
            "auth_kind": auth_kind,
            "transport": transport,
            "model_source": model_source,
            # _deriveInvocation 域
            "auth_required": auth_kind not in ("no_key", "unknown"),
            "auth_style": auth_kind if auth_kind in ("oauth", "device_code", "aws_sdk") else None,
            "lifecycle": "transient",
            "ui_hints": {},
            "capabilities": p.get("capabilities", []) or (["auth"] if auth_kind not in ("no_key", "unknown") else []),
            "api": api,
        })
        result[pid] = decl

    provider_count = sum(1 for k, v in result.items()
                         if isinstance(v, dict) and v.get("type") == "provider")
    logger.info("large._flattenNewFormat: flattened %d providers from grouped format (%d total entries)",
                provider_count, len(result))

    # 3. 扁平化 custom_demands → {demand_id: demand_decl}
    # 客户/人格模块经客制化提的需求（scout 从 user_prefs 读出、注入 mani）。
    # 按 am 的格式转换：am() 只认三轴（auth_kind/transport/model_source）→
    # 查 taxonomy_map 出模板。所以 demand 也带上三轴——用一套专属值
    # （demand:demand:demand，与真实 provider 的取值空间不冲突），
    # taxonomy_map 里加对应条目即可，am() 一行不用改。
    demands = entries.get("custom_demands") or []
    for d in demands:
        did = d.get("id", "")
        if not did:
            continue
        decl = dict(d)
        if str(d.get("type") or "") == "consumer":
            # 消费端声明（我们对插件的设计）：**不许被当成需求**——以前这里一律
            # 强写 type=demand/三轴 demand，于是 large 永远把它生成成"需求卡"，
            # 那个插件消费端根本没被生成。现在按它自己的身份走专属三轴，
            # forge.json 里对应一条范式声明（preset/字段），large 才真的长出消费端。
            decl.update({
                "type": "consumer",
                "display_name": d.get("display_name") or d.get("id", did),
                "auth_kind": "consumer",
                "transport": "plugin",
                "model_source": "local",
                "lifecycle": d.get("lifecycle") or "critical",
                "ui_hints": d.get("ui_hints") or {},
            })
        else:
            decl.update({
                "type": "demand",
                "display_name": d.get("name", did),
                # 按 am 的格式：三轴标签（am 的 classify() 直接读这三个字段）
                "auth_kind": "demand",
                "transport": "demand",
                "model_source": "demand",
                "lifecycle": "transient",
                "ui_hints": {},
            })
        result[did] = decl
    if demands:
        logger.info("large._flattenNewFormat: flattened %d custom demands", len(demands))

    return result


def _enrich(declarations: dict) -> dict:
    """Pure logic: enrich declarations with invocation metadata."""
    enriched = {}
    meta_keys = {"updated_at", "version", "_meta"}
    for name, decl in declarations.items():
        if name in meta_keys or not isinstance(decl, dict):
            enriched[name] = decl
            continue
        invocation = _deriveInvocation(name, decl)
        # MA-3 类型桩
        if invocation.get("style") == "generic":
            logger.warning("[STAKE-TYP] _deriveInvocation fell to generic for '%s' (type='%s')", name, decl.get("type", ""))
        enriched[name] = {**decl, "_invocation": invocation}
    logger.info("large._enrich: enriched %d entries",
                sum(1 for v in enriched.values() if isinstance(v, dict) and "_invocation" in v))
    return enriched


def _checkSignal():
    """检查scout发送的信号，清除陈旧信号文件"""
    # MA-1 非空桩
    if not os.path.exists(_SIGNAL_PATH):
        logger.info("[STAKE-NON] no scout signal found, manifest will use existing mani.json")
    if os.path.exists(_SIGNAL_PATH):
        try:
            os.remove(_SIGNAL_PATH)
            logger.info("large._checkSignal: scout signal consumed")
        except OSError:
            pass


def _deriveInvocation(name, declaration):
    """
    从声明中派生调用方法

    从声明ui_hints中合并structural signature，
    使type特定hints覆盖（而非替换）声明级hints。
    根据type决定调用风格:
      channels  -> setup_form
      automation -> config_panel
      gateway   -> connection_form
      auth-credential-semantics -> auth_resolver
      其他      -> generic
    """
    decl_type = declaration.get("type", "")
    auth_req = declaration.get("auth_required")
    caps = declaration.get("capabilities", [])
    decl_hints = declaration.get("ui_hints", {})

    inv = {"style": "generic", "params": {}, "hints": dict(decl_hints)}
    if auth_req is not None:
        inv["auth_required"] = auth_req
    inv["capabilities"] = list(caps) if caps else []

    if decl_type == "channel":
        inv["style"] = "setup_form"
        inv["params"]["needs_auth"] = bool(auth_req) if auth_req is not None else False
        inv["hints"]["card_group"] = True
        inv["hints"]["has_steps"] = True
    elif decl_type == "automation":
        inv["style"] = "config_panel"
        inv["params"]["needs_auth"] = bool(auth_req) if auth_req is not None else False
        inv["hints"]["has_cli"] = True
    elif decl_type == "gateway":
        inv["style"] = "connection_form"
        inv["params"]["needs_auth"] = True
        inv["hints"]["connection_based"] = True
    elif decl_type == "auth-credential-semantics":
        inv["style"] = "auth_resolver"
        inv["params"]["needs_auth"] = False
        inv["hints"]["internal_only"] = True
    elif decl_type == "cli":
        inv["style"] = "cli_command"
    elif decl_type == "provider":
        inv["style"] = "setup_form"
        inv["params"]["needs_auth"] = bool(auth_req) if auth_req is not None else False
    elif decl_type == "model":
        inv["style"] = "model_selection"
    elif decl_type == "memory":
        inv["style"] = "setup_form"
    elif decl_type == "web_search":
        inv["style"] = "setup_form"
    elif decl_type in ("demand", "consumer"):
        # 需求 / 消费端声明：**不是可调用的消费端** —— 一条需求没法"被调用"，
        # 它是给客户看（需求卡）和给消费端照办的（装法/钩子）。风格单列 declaration，
        # 于是既不掉进 generic（MA-3/MA-4 桩不再误报未知类型），也不冒充某种表单。
        inv["style"] = "declaration"
        inv["hints"]["is_declaration"] = True

    # MA-4 类型桩
    if inv["style"] == "generic" and declaration.get("type", "") not in ("", "cli"):
        logger.warning("[STAKE-TYP] unknown type '%s' for '%s', using generic invocation", declaration.get("type", ""), name)
    if caps:
        inv["params"]["capabilities"] = caps

    return inv


# ==============================================================
#  预制件（泛化方法） -- steak() 实例化消费端
# ==============================================================

def steak(name, declaration):
    """实例化消费端
    根据声明生成可消费实例
    交给 am() 管理生命周期, forge() 负责渲染
    """
    cfg = _loadForgeConfig()
    tpm = cfg.get("type_preset_map", {})
    p = tpm.get(declaration.get("type", ""), "generic_card")
    # SA-2 映射桩
    _decl_type = declaration.get("type", "")
    if _decl_type and _decl_type not in tpm:
        logger.warning("[STAKE-MAP] type '%s' not in type_preset_map, falling back to generic_card", _decl_type)
    # 保真透传声明源的全部字段（源头逻辑），只覆盖/派生少数实例化字段。
    # 不手抄白名单——spider 未来新增的任何字段（如 auth_choice）自动透传，
    # 无需再改这里。内部中间产物 _invocation / _body 剔除。
    _result = dict(declaration)
    _result.pop("_invocation", None)
    _result.pop("_body", None)
    _result.setdefault("id", name)
    _result.setdefault("display_name", name)
    _result.update({
        "ui_template": p,
        "status": "consumable",
        "provider_id": name,
        "ui_hints": declaration.get("_invocation", {}).get("hints", {}),
    })
    _REQUIRED_KEYS = {"id", "type", "ui_template", "display_name"}
    _missing = _REQUIRED_KEYS - {k for k, v in _result.items() if v is not None and v != ""}
    if _missing:
        logger.warning("[STAKE-INT] steak() result for '%s' has null/empty required keys: %s", name, _missing)
    return _result



# ==============================================================
#  抽象类管理器 -- am() 调用 steak() 实例化
# ==============================================================

def am(enriched=None):
    from lib.testPole import logEvent; logEvent("am.enter")
    """读manifest的enriched数据 -> taxonomy分类 -> steak()实例化 -> forge()渲染"""
    if enriched is None:
        enriched = manifest()
    if not enriched:
        return []
    metadata_keys = {"updated_at", "version", "_meta"}

    # ===== 标的分发器：遍历每个声明，打三轴标签 =====
    from kernel.filter import classify, build_form_schema, build_method_index
    cfg = _loadForgeConfig()

    # method 表：从 mani 的 clis 生成（{cli_id/action: 完整命令}），供消费端直查
    _method_index = {}
    try:
        with open(_MANI_PATH, "r", encoding="utf-8") as f:
            _clis = (json.load(f).get("entries") or {}).get("clis") or []
        _method_index = build_method_index(_clis)
        # ── 检测桩：method 表装载 ──
        try:
            from main.main import stk as _stk
            _stk("large.method_index", f"{len(_method_index)} 条", ok=len(_method_index) > 0)
        except Exception:
            pass
    except (OSError, json.JSONDecodeError):
        _method_index = {}

    instances = []
    for name, decl in enriched.items():
        if name in metadata_keys or not isinstance(decl, dict):
            continue
        inst = steak(name, decl)
        # 对每个声明按 section_name/source_url/type 进行分类
        tags = classify(decl)
        inst["_taxonomy"] = tags
        # 按三轴键匹配 forge 表单结构
        form = build_form_schema(tags, cfg)
        if form:
            inst["_form"] = form
            inst["ui_template"] = form.get("template_name", inst.get("ui_template", "generic_card"))
        instances.append(inst)

    # method 表挂在返回里，供下游（UI）直查
    if _method_index:
        for inst in instances:
            if isinstance(inst, dict):
                inst["_methods"] = _method_index

    logger.info("[STAKE-CNT] am: instantiated %d consumers with taxonomy", len(instances))
    return forge(instances)


# ==============================================================
#  渲染引擎
# ==============================================================

def forge(instances):
    """渲染引擎：根据ui_template注入渲染参数，合并ui_hints"""
    if not instances:
        return instances
    cfg = _loadForgeConfig()
    presets = cfg.get("presets", {})
    scope_map = cfg.get("scope_mappings", {})
    _field_type_default = scope_map.get("string", {})
    # FO-4 值桩-快照
    _presets_snapshot = {k: len(v.get("fields", [])) if isinstance(v, dict) else 0 for k, v in presets.items()}
    for inst in instances:
        tpl = inst.get("ui_template", "generic_card")
        # 字段来源：taxonomy 规则推导的 _form（语义决定字段），presets 只提供兜底样式。
        # am() 已经通过 build_form_schema 把三轴语义 → 字段清单存进 inst["_form"]，
        # forge 这里消费它，而不是读 presets 里写死的通用字段。
        _form = inst.get("_form") or {}
        derived_fields = _form.get("fields") or []
        render = dict(presets.get(tpl, presets.get("generic_card", {})))
        if tpl not in presets:
            logger.warning("[STAKE-MAP] ui_template '%s' not in presets, falling back to generic_card", tpl)
        hints = inst.get("ui_hints", {})
        if isinstance(hints, dict):
            for k, v in hints.items():
                if k not in render:
                    render[k] = v
        # 优先用推导字段（语义长出），没有才回退 presets 静态字段
        if derived_fields:
            render["fields"] = derived_fields
        # 解析 fields widget 类型
        fields = render.get("fields", [])
        if fields:
            resolved = []
            for f in fields:
                if isinstance(f, str):
                    # 裸字符串 → 展开为结构化 dict（scope_map 或 presets 中找 widget 定义）
                    ftype = f
                    mapping = scope_map.get(ftype, _field_type_default)
                    if ftype not in scope_map:
                        logger.warning("[STAKE-MAP] field '%s' not in scope_mappings, falling back to string/QLineEdit", ftype)
                    resolved.append({
                        "name": ftype,
                        "type": ftype,
                        "label": ftype.replace("_", " ").title(),
                        "widget": mapping.get("widget", "QLineEdit"),
                        "hints": mapping.get("hints", {}),
                    })
                elif isinstance(f, dict):
                    ftype = f.get("type", "string")
                    mapping = scope_map.get(ftype, _field_type_default)
                    # FO-2 映射桩
                    if ftype not in scope_map:
                        logger.warning("[STAKE-MAP] field_type '%s' not in scope_mappings, falling back to string/QLineEdit", ftype)
                    f["widget"] = mapping.get("widget", "QLineEdit")
                    mhints = mapping.get("hints", {})
                    if mhints:
                        f.setdefault("hints", {}).update(mhints)
                    resolved.append(f)
            render["fields"] = resolved
        inst["render"] = render
        # FO-3 非空桩
        if not render or not render.get("fields"):
            logger.warning("[STAKE-NON] inst '%s' render is empty or has no fields", inst.get("id", "?"))
    # FO-4 值桩-检查
    for _pk, _pv in presets.items():
        _cur = len(_pv.get("fields", [])) if isinstance(_pv, dict) else 0
        if _cur != _presets_snapshot.get(_pk, 0):
            logger.warning("[STAKE-VAL] preset '%s' fields count changed: %d -> %d (cache mutation)", _pk, _presets_snapshot.get(_pk, 0), _cur)
            break
    return instances



# ==============================================================
#  统一状态机
# ==============================================================

def usm(enriched=None):
    """读manifest的enriched数据 -> 生命周期管理 + 版本跳过检查"""
    if enriched is None:
        enriched = manifest()
    if not enriched:
        return {}
    meta_keys = {"updated_at", "version", "_meta"}
    declarations = {}
    report = {
        "alive": [], "persistent": [], "critical": [],
        "transient": [], "total": 0,
        "lifecycle_protection": True, "skipped": {}, "skipped_versions": [],
    }
    for name, decl in enriched.items():
        if name in meta_keys or not isinstance(decl, dict):
            if name in meta_keys:
                declarations[name] = decl
            continue
        declarations[name] = decl
        report["total"] += 1
        lc = _applyLifecycle(name, decl)
        report[lc["bucket"]].append(name)
        report["alive"].append(name)

    skipR = _checkSkippedVersions(declarations)
    report["skipped"] = skipR
    if skipR.get("total_misses", 0) >= _LIFECYCLE_PROTECTION_THRESHOLD:
        report["lifecycle_protection"] = False
        logger.warning(
            "usm: lifecycle protection disabled after %d skipped versions: %s",
            skipR["total_misses"],
            skipR.get("skipped_versions", []),
        )

    logger.info(
        "large.usm: total=%d alive=%d persistent=%d critical=%d",
        report["total"], len(report["alive"]),
        len(report["persistent"]), len(report["critical"]),
    )
    # US-1 计数桩
    logger.info("[STAKE-CNT] usm lifecycle: persistent=%d critical=%d transient=%d",
                len(report.get("persistent", [])), len(report.get("critical", [])),
                len(report.get("transient", [])))
    # US-2 完整性桩
    _REQUIRED_REPORT_KEYS = {"total", "alive", "persistent", "critical", "transient"}
    _missing_report = _REQUIRED_REPORT_KEYS - report.keys()
    if _missing_report:
        logger.warning("[STAKE-INT] usm report missing keys: %s", _missing_report)
    return report


def _applyLifecycle(name, declaration):
    """
    根据 lifecycle 字段 + capabilities 应用维护策略

    如果 capabilities 包含 auth/gateway/token_probe → 强制 persistent
    如果 capabilities 包含 critical-tool/security → 强制 critical
    """
    lc = declaration.get("lifecycle", "transient")
    caps = declaration.get("capabilities", [])
    cap_based = False

    # capability-based lifecycle override
    if any(c in ("auth", "gateway", "token_probe") for c in caps):
        lc = "persistent"
        cap_based = True
    elif any(c in ("critical-tool", "security") for c in caps):
        lc = "critical"
        cap_based = True

    if lc not in ("transient", "persistent", "critical"):
        lc = "transient"

    strategy = {"watchdog": True, "keepalive": False, "drop_on_pressure": True}
    if lc == "persistent":
        strategy = {"watchdog": True, "keepalive": True, "drop_on_pressure": False}
    elif lc == "critical":
        strategy = {"watchdog": True, "keepalive": True, "drop_on_pressure": False, "never_drop": True}

    result = {"name": name, "lifecycle": lc, "strategy": strategy, "bucket": lc}
    if cap_based:
        result["capability_based"] = True
    return result


# ==============================================================
#  版本跳过检测
# ==============================================================

def _checkSkippedVersions(declarations: dict) -> dict:
    """从SQLite检查被跳过的版本
    超过3个 -> USM自动关闭生命周期保护
    同版本内的新增/删除 -> 正常处理
    """
    if not os.path.exists(_MANI_PATH):
        return {}

    current_ver = ""
    ver_meta = declarations.get("version")
    if isinstance(ver_meta, dict):
        current_ver = str(ver_meta.get("npm_published_at") or "").strip()

    if not os.path.exists(_DB_PATH):
        return {}
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'doc_entries_%' ORDER BY name DESC"
        )
        tables = [r[0] for r in cur.fetchall()]
        conn.close()
    except Exception:
        tables = []

    newer_tables = []
    for tn in tables:
        vid = tn.replace("doc_entries_", "")
        if current_ver and vid > current_ver:
            newer_tables.append(vid)
        elif not current_ver:
            newer_tables.append(vid)

    if not newer_tables:
        return {"newer_versions": 0}

    miss = {"misses": 0, "current_version": current_ver, "last_skipped_at": ""}
    if os.path.exists(_MISS_PATH):
        try:
            with open(_MISS_PATH, "r", encoding="utf-8") as f:
                miss = json.load(f)
        except (json.JSONDecodeError, OSError):
            miss = {"misses": 0, "current_version": "", "last_skipped_at": ""}

    miss["misses"] += len(newer_tables)
    miss["current_version"] = current_ver
    miss["last_skipped_at"] = datetime.now().isoformat()

    with open(_MISS_PATH, "w", encoding="utf-8") as f:
        json.dump(miss, f, ensure_ascii=False, indent=2)

    if miss["misses"] >= _LIFECYCLE_PROTECTION_THRESHOLD:
        logger.warning(
            "[STAKE-VAL] USM: %d skipped versions >= %d, consumer lifecycle protection disabled. "
            "Skipped: %s "
            "Upgrade required to restore full functionality.",
            miss["misses"], _LIFECYCLE_PROTECTION_THRESHOLD,
            newer_tables,
        )

    logger.info(
        "large._checkSkippedVersions: current=%s, newer=%d, total_misses=%d",
        current_ver or "(none)", len(newer_tables), miss["misses"],
    )
    return {"newer_versions": len(newer_tables), "total_misses": miss["misses"], "skipped_versions": newer_tables}


def resetMisses():
    """客户确认升级后调用：清空miss.json计数器"""
    miss = {"misses": 0, "current_version": "", "last_skipped_at": ""}
    try:
        with open(_MISS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            miss["current_version"] = data.get("current_version", "")
    except (json.JSONDecodeError, OSError):
        pass
    miss["misses"] = 0
    miss["last_skipped_at"] = ""
    with open(_MISS_PATH, "w", encoding="utf-8") as f:
        json.dump(miss, f, ensure_ascii=False, indent=2)
    logger.info("large.resetMisses: miss counter cleared")


# ==============================================================
#  运行入口
# ==============================================================

def run():
    """large 运行入口: manifest() -> usm() -> am()"""
    enriched = manifest()
# RU-1 时序桩
    if not enriched:
        logger.warning("[STAKE-SEQ] run(): manifest() returned empty, pipeline may produce no instances")
    lifecycle_report = usm(enriched)
    instances = am(enriched)
# RU-2 非空桩
    if not instances:
        logger.warning("[STAKE-NON] run(): instances is empty, downstream wizard will show no pages")
    return {"lifecycle": lifecycle_report, "instances": instances}


if __name__ == "__main__":
    result = run()
    print(f"Lifecycle: {len(result['lifecycle'].get('alive', []))} alive")
    print(f"Instances: {len(result['instances'])} rendered")