"""
Rheia 高级配置子页面
==============================
附着于模型配置页（provider_setup.py）的子页面，由「高级配置」按钮触发。

设计：统一内核 + 解释层 + 策略分发（Linux 式：内核统一，差异入配置）。
  - 内核：models.providers（官方 schema），写入只走 runtime.wsl_config
  - 解释层：local_services.json（字段逐项抄官方文档 providers/{id}.md）
  - 消费：一个通用解析器按 parse 声明取字段，一个策略注册表按 dynamic_by 分发
  - 桩子：runtime.stake.stk() 记录第一手现场信息，仅限本模块，不碰现有逻辑

架构边界（增量，只追加不改现有）：
  - 不 import / 不修改 provider_setup.py 的任何现有类与函数
  - 写 openclaw.json 只走 runtime.wsl_config.read/write
  - 桩子只用 runtime.stake.stk，不新起日志文件
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Any, Callable

_proj = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _proj not in sys.path:
    sys.path.insert(0, _proj)

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ui.theme import BG, TEXT, ACCENT, ACCENT_HOVER, STATUS, BORDER

WSL_DISTRO = os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu"
try:
    from ui.i18n import tr
except ImportError:
    tr = lambda key, **fmt: key

_SERVICES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local_services.json")

_CARD_BG = BG["deep"]
_TEXT_PRIMARY = TEXT["primary"]
_TEXT_HINT = TEXT["hint"]
_GREEN = STATUS["green"]
_YELLOW = STATUS["yellow"]
_RED = STATUS["red"]

def _protocol_choices() -> list[str]:
    """协议下拉框选项：从字典 api_signals 读官方标准 adapter 清单。

    官方 config-tools.md:532 列了 12 种 request adapter，字典已收录。
    协议是数据不是逻辑，从字典读，官方加新 adapter 自动跟上。
    读不到时回落最小的 openai 兼容两档，保证下拉框不空。
    """
    try:
        from kernel.filter import _load_ambiguity
        api_signals = _load_ambiguity().get("api_signals") or {}
        stds = [k for k in api_signals if not k.startswith("_")]
        if stds:
            return stds
    except Exception:
        pass
    return ["openai-completions", "openai-responses", "anthropic-messages"]


# ═══════════════════════════════════════════════
# 桩子（仅限本模块，用 runtime.stake.stk）
# ═══════════════════════════════════════════════

def _stake(tag: str, msg: str, ok: bool = True) -> None:
    try:
        from main.main import stk
        stk(f"advcfg.{tag}", msg, ok=ok)
    except Exception:
        pass


# ═══════════════════════════════════════════════
# method 表：从 kernel 长出来的方法索引里查命令（不硬编）
# ═══════════════════════════════════════════════

_MANI_PATH = os.path.join(_proj, "kernel", "large", "mani.json")

# ═══════════════════════════════════════════════
# 解释层：加载 local_services.json
# ═══════════════════════════════════════════════

def load_services() -> list[dict[str, Any]]:
    """从 mani 的 provider 活数据读本地服务清单，替代手写 local_services.json。

    provider 官方定义的稳定属性（discover/parse/default_base）由字典翻译层提供；
    api 由 spider 从官方文档抓取并归一（Property 表优先、正文兜底），这里直接读。
    手写 json 仅作兜底（活数据缺失时降级）。
    """
    services: list[dict[str, Any]] = []
    try:
        from kernel.filter import known_collaborators
        collabs = known_collaborators()
        # api / dynamic_by 从 mani 的 provider entry 读（spider 已抓、已归一）
        api_by_id: dict[str, str] = {}
        dyn_by_id: dict[str, str] = {}
        try:
            with open(_MANI_PATH, "r", encoding="utf-8") as f:
                provs = (json.load(f).get("entries") or {}).get("providers") or []
            for p in provs:
                if isinstance(p, dict) and p.get("id"):
                    api_by_id[p["id"]] = (p.get("api") or "")
                    dyn_by_id[p["id"]] = (p.get("dynamic_by") or "")
        except (OSError, json.JSONDecodeError):
            pass

        for name, spec in collabs.items():
            svc = {
                "id": name,
                "label": name.title(),
                "native": True,
                "api": api_by_id.get(name, ""),
                "default_base": spec.get("default_base", ""),
                "discover": spec.get("discover", ""),
                "parse": spec.get("parse", {}),
                "dynamic_by": dyn_by_id.get(name, "wildcard"),
                "env_key": spec.get("env_key", ""),
                "local_api_key": spec.get("local_api_key", ""),
            }
            services.append(svc)
        if services:
            return services
    except Exception as e:
        _stake("load_services", f"活数据加载失败，降级手写 json: {e}", ok=False)

    # 兜底：手写 local_services.json
    try:
        with open(_SERVICES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("services", [])
        return [s for s in raw if isinstance(s, dict) and s.get("id")]
    except (OSError, json.JSONDecodeError) as e:
        _stake("load_services", f"failed: {e}", ok=False)
        return []


def _run_wsl(cmd: str) -> tuple[int, str, str]:
    """在 WSL 里同步跑一条命令，返回 (rc, stdout, stderr)。

    ⚠ 壳层不留时间闸：不传 timeout（命令返回即信号），
    原先那个「签名留着、值不生效」的死 timeout 参数已去掉。
    """
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True,
                           encoding="utf-8", errors="replace",
                           creationflags=subprocess.CREATE_NO_WINDOW,
                           startupinfo=si)
        return r.returncode, (r.stdout or ""), (r.stderr or "")
    except Exception:
        return -1, "", "error"


# ═══════════════════════════════════════════════
# 官方 CLI 写入：config set --strict-json --merge
# ═══════════════════════════════════════════════
# 官方安全写入方式（config-tools.md）：走 openclaw config set --merge，
# 由 CLI 自己 validate，且 models 字段热重载（configuration.md），
# 无需手动 restart。避免 direct file edit 被判 untrusted 而跳过热重载。

def _config_set_merge(path: str, value: dict[str, Any]) -> tuple[bool, str]:
    """写入 models.providers.<id> 的字段，走官方 openclaw config patch --file。

    path 形如 models.providers.<id>；value 是该路径下要合并的 JSON 对象。
    官方 config patch 语义：Objects merge recursively（官方 config.md），
    validate + 热重载由 Gateway watcher 自动完成，无需手动 restart。

    实现：构造一个 {path: value} 的 JSON5 patch 文件（Windows 临时文件），
    用 subprocess list 形式直接调 wsl.exe（不经 Windows cmd 剥引号），
    config patch --file 读文件。JSON 内容完全不进 shell 命令行。
    """
    import json as _json
    import tempfile as _tmp
    # 构造 patch 文件：{ "models": { "providers": { <id>: {...} } } }
    # 但 path 已经是完整路径（如 models.providers.xxx），需要把它转成嵌套 dict
    parts = path.split(".")
    patch: dict[str, Any] = value
    for part in reversed(parts):
        patch = {part: patch}

    fd, tmp_path = _tmp.mkstemp(suffix=".json5", prefix="okoc-patch-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            _json.dump(patch, f, ensure_ascii=False, indent=2)
        # Windows 路径 → WSL /mnt/ 路径
        wsl_path = tmp_path.replace("\\", "/")
        if len(wsl_path) > 1 and wsl_path[1] == ":":
            wsl_path = "/mnt/" + wsl_path[0].lower() + wsl_path[2:]

        from main.main import cli_usage
        patch_cmd = cli_usage("config", "patch")
        if not patch_cmd:
            return (False, "config patch 命令不可用")
        bash_script = (
            "export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; "
            f"{patch_cmd} --file {wsl_path} 2>&1"
        )
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        # 壳层不留时间闸：不再传 timeout（
        # 是真闸，超时直接判 "timeout"），命令返回即信号。
        r = subprocess.run(
            ["wsl", "-d", WSL_DISTRO, "-u", "root", "--", "bash", "-c", bash_script],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
            startupinfo=si,
        )
        rc = r.returncode
        out = (r.stdout or "")
        err = (r.stderr or "")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    ok = rc == 0
    _stake("config_set", f"{path} rc={rc} out={out[:300]!r} err={err[:300]!r}", ok=ok)
    return (ok, (out + err).strip()[:300] if not ok else "ok")


# ═══════════════════════════════════════════════
# 通用消费：按 parse 声明取字段（第 2 层）
# ═══════════════════════════════════════════════

def discover_local_models(base_url: str, spec: dict[str, Any]) -> list[str]:
    """按解释层的 discover/parse 声明，从本地服务发现端点拉模型列表。

    返回原始 JSON 也通过桩子落盘，便于 parse 与真实结构不符时定位。
    """
    base = (base_url or "").rstrip("/")
    if not base:
        return []
    endpoint = base + spec["discover"]
    rc, out, _ = _run_wsl(
        f'wsl -d {WSL_DISTRO} -- bash -c "curl -s --max-time 10 \'{endpoint}\'"'
    )
    # ── 桩子：记录原始返回（截断）──
    _stake("discover", f"{spec['id']} endpoint={endpoint} rc={rc} raw={out[:300]!r}",
           ok=(rc == 0 and bool(out.strip())))
    if rc != 0 or not out.strip():
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        _stake("discover.parse", f"{spec['id']} JSON decode failed: {e}", ok=False)
        return []
    key, field = spec["parse"]["key"], spec["parse"]["field"]
    items = data.get(key) if isinstance(data, dict) else []
    if not isinstance(items, list):
        _stake("discover.parse", f"{spec['id']} key={key} not a list, got {type(items).__name__}", ok=False)
        return []
    ids = []
    for it in items:
        if isinstance(it, dict) and it.get(field):
            ids.append(str(it[field]))
    _stake("discover.parse", f"{spec['id']} extracted {len(ids)} ids via {key}/{field}", ok=bool(ids))
    return ids


# ═══════════════════════════════════════════════
# 写入策略注册表（第 3 层：按 dynamic_by 分发）
# ═══════════════════════════════════════════════

def _write_implicit_env(spec: dict[str, Any], base_url: str, default_model: str | None) -> tuple[bool, str]:
    """implicit_env 策略：写 env.vars.<ENV_KEY>，不写 models.providers.<id>，官方隐式发现。

    适用于 ollama / lmstudio。官方文档（ollama.md / lmstudio.md）：
    隐式发现的前提是「设 ENV_KEY 且 models.providers.<id> 未定义」。
    一旦显式写 models.providers.<id> 就停用自动发现，models 必须手动列出。
    所以这里只写 env.vars，让 Gateway 启动时自动 /api/tags 或 /api/v1/models
    发现模型，模型自动进 picker。不设 primary，不覆写默认模型。
    """
    env_key = spec.get("env_key", "")
    local_key = spec.get("local_api_key", "")
    if not env_key:
        return (False, f"{spec['id']} 缺少 env_key")
    # 写 env.vars.<ENV_KEY> = local_key（本地非 secret 标记）
    ok, msg = _config_set_merge("env.vars", {env_key: local_key})
    _stake("write.implicit_env", f"{spec['id']} env={env_key} ok={ok}", ok=ok)
    return (ok, msg)


def _write_wildcard(spec: dict[str, Any], base_url: str, default_model: str | None) -> tuple[bool, str]:
    """wildcard 策略：显式配置 + env 引用 + agents.defaults.models["<id>/*"] 通配符。

    适用于 vllm / sglang。官方文档（vllm.md / sglang.md）：
    显式写 models.providers.<id>，apiKey 用 ${ENV_KEY} 引用 env 变量；
    再写 env.vars 提供实际值，通配符 "<id>/*" 保动态发现。
    不设 primary —— 模型并列出现在列表，不覆写默认模型。
    """
    env_key = spec.get("env_key", "")
    local_key = spec.get("local_api_key", "")
    value: dict[str, Any] = {
        "api": spec["api"],
    }
    if env_key:
        value["apiKey"] = f"${{{env_key}}}"
    if base_url:
        value["baseUrl"] = base_url
    ok, msg = _config_set_merge(f"models.providers.{spec['id']}", value)
    if ok:
        # 写 env.vars 提供实际值
        if env_key and local_key:
            _config_set_merge("env.vars", {env_key: local_key})
        # 通配符保动态发现
        wildcard = {f"{spec['id']}/*": {}}
        _config_set_merge("agents.defaults.models", wildcard)
    _stake("write.wildcard", f"{spec['id']} ok={ok}", ok=ok)
    return (ok, msg)


def _write_fixed(spec: dict[str, Any], base_url: str, models: list[str]) -> tuple[bool, str]:
    """fixed 策略：写死全部模型进 models[]。

    官方文档（models.md）：models.providers.*.models 让模型并列出现在 picker。
    不设 primary —— 模型并列出现在列表，不覆写默认模型。
    name 是 schema 必填字段（实测 config patch 校验要求），缺省回填 id。
    """
    value: dict[str, Any] = {
        "api": spec["api"],
        "models": [{"id": m, "name": m} for m in models],
    }
    if spec.get("local_api_key"):
        value["apiKey"] = spec["local_api_key"]
    if base_url:
        value["baseUrl"] = base_url
    ok, msg = _config_set_merge(f"models.providers.{spec['id']}", value)
    _stake("write.fixed", f"{spec['id']} models={len(models)} ok={ok}", ok=ok)
    return (ok, msg)


# 自定义端点（非本地服务）：通用写入，走官方 config patch --file
def write_custom_provider(pid: str, fields: dict[str, Any], *,
                          backup: bool = True) -> tuple[bool, str]:
    """写 models.providers.<pid>。

    backup=False 时只写配置、不落 baseline_pool.db（「测试连接」用：测试不该污染备份库）。
    """
    value: dict[str, Any] = {}
    for k, v in fields.items():
        if v is not None and v != "":
            # models[] 里每个条目补 name（schema 必填）
            if k == "models" and isinstance(v, list):
                norm_models = []
                for m in v:
                    if isinstance(m, dict) and m.get("id"):
                        m2 = dict(m)
                        m2.setdefault("name", m2["id"])
                        norm_models.append(m2)
                value[k] = norm_models
            else:
                value[k] = v
    ok, msg = _config_set_merge(f"models.providers.{pid}", value)
    # 配置成功后同步到 baseline_pool.db（灾难恢复备份）
    db_warn = ""
    if ok and backup:
        api_key = value.get("apiKey")
        base_url = value.get("baseUrl", "")
        api = value.get("api", "")
        model_name = ""
        models = value.get("models")
        if isinstance(models, list) and models:
            m0 = models[0]
            if isinstance(m0, dict):
                model_name = m0.get("id", "")
        if api_key:
            try:
                from kernel.scout import setCredential, ensureRecoveryTables
                ensureRecoveryTables()
                setCredential(f"{pid}_api_key", api_key, pid)
                _stake("write.custom.db", f"{pid} apiKey 已同步到 DB")
            except Exception as e:
                _stake("write.custom.db", f"{pid} 同步 DB 失败: {e}", ok=False)
                db_warn += f" credentials({e});"
        # 高级配置专属表：base_url / api / api_key / model_name 一并备份
        try:
            from kernel.scout import setAdvancedProvider, ensureRecoveryTables
            ensureRecoveryTables()
            setAdvancedProvider(pid, base_url, api, api_key or "", model_name)
            _stake("write.custom.advdb", f"{pid} 高级配置已同步到 advanced_providers")
        except Exception as e:
            _stake("write.custom.advdb", f"{pid} 同步 advanced_providers 失败: {e}", ok=False)
            db_warn += f" advanced_providers({e});"
    _stake("write.custom", f"{pid} ok={ok} db_warn={db_warn!r}", ok=ok)
    # DB 备份失败原来只落桩、返回值不变，界面照样显示「保存成功」——把警告带回界面。
    return (ok, f"{msg} ⚠ 备份库未同步:{db_warn}" if (ok and db_warn) else msg)


def _configured_provider_ids() -> set[str]:
    """配置里现有的 models.providers 的 id 集合。

    「测试连接」用它判断要不要先写配置（已在配置里就别再写一遍）。
    读不到就返回空集（宁可多写一次，也不要漏写导致探测必然失败）。
    """
    rc, out, _ = _run_wsl(
        f'wsl -d {WSL_DISTRO} -u root -- bash -c '
        f'"export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; '
        f'cat ~/.openclaw/openclaw.json 2>/dev/null"')
    if rc != 0 or not out.strip():
        return set()
    try:
        cfg = json.loads(out)
    except json.JSONDecodeError:
        return set()
    provs = ((cfg.get("models") or {}).get("providers") or {})
    return set(provs.keys()) if isinstance(provs, dict) else set()


def _derive_pid(base_url: str) -> str:
    if not base_url:
        return ""
    m = re.search(r"://([^/]+)", base_url)
    host = m.group(1) if m else base_url
    host = host.split(":")[0]
    host = re.sub(r"[^a-zA-Z0-9-]", "-", host).strip("-")
    return f"custom-{host}" if host else ""


# ═══════════════════════════════════════════════
# 连通性探测（官方 openclaw models status --probe）
# ═══════════════════════════════════════════════

# probe 结果分桶 → 人话（文案走 tr，此处存 key）
_BUCKET_LABEL = {
    "ok": "ui.advcfg.probe_ok",
    "auth": "ui.advcfg.probe_auth",
    "no_model": "ui.advcfg.probe_no_model",
    "rate_limit": "ui.advcfg.probe_rate_limit",
    "billing": "ui.advcfg.probe_billing",
    "timeout": "ui.advcfg.probe_timeout",
    "format": "ui.advcfg.probe_format",
    "unknown": "ui.advcfg.probe_unknown",
}


def probe_provider(pid: str) -> tuple[bool, str]:
    """调用官方 openclaw models status --probe --probe-provider 验证连通性。

    返回 (ok, bucket)。bucket 经 _BUCKET_LABEL 翻译成人话。
    这是「测试连接」按钮的真探测，不是只写配置。
    """
    from main.main import cli_usage
    base = cli_usage("models", "status")
    if not base:
        return (False, "unknown")
    # pid 包成单引号字面量（原来是直接拼进命令行；pid 里带引号即破结构）。
    rc, out, err = _run_wsl(
        f'wsl -d {WSL_DISTRO} -u root -- bash -c '
        f'"export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; '
        f'{base} --probe --probe-provider \'{pid.replace(chr(39), "")}\' 2>&1"'
    )
    text = (out + err).lower()
    # 剥 ANSI 颜色/控制码，日志才可读（不剥会存进一堆 \x1b[xxm 转义）
    _stripped = re.sub(r"\x1b\[[0-9;]*m", "", out + err)
    _stake("probe", f"{pid} rc={rc} out={_stripped[:200]!r}", ok=(rc == 0))
    # 命中 ok 关键字（必须 rc == 0：rc != 0 时输出里出现 ok 字样不算成功）
    if rc == 0 and re.search(r"\b(ok|probed)\b", text):
        return (True, "ok")
    # 分桶判断（顺序敏感：先具体后兜底）。用词边界匹配，别拿子串当判据
    # （原来 "auth" 之类裸子串匹配，任何一句含这几个字母的话都会被算成该桶）。
    for bucket in ("auth", "no_model", "rate_limit", "billing", "timeout", "format"):
        if re.search(rf"\b{re.escape(bucket)}\b", text):
            return (False, bucket)
    return (False, "unknown")


# ═══════════════════════════════════════════════
# 发现结果选择对话框（A+B 中间层）
# ═══════════════════════════════════════════════

class _DiscoverResultDialog(QDialog):
    """发现模型列表后的中间层：选默认 + 决定固定 or 动态。"""

    def __init__(self, svc_label: str, models: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("ui.advcfg.found_result"))
        from ui.theme import fitToScreen
        fitToScreen(self, 0.4, 0.4)
        self.setStyleSheet(f"QDialog{{background:{_CARD_BG};color:{_TEXT_PRIMARY};}} QLabel{{background:transparent;}}")
        self.choice = (None, None)
        self._models = models

        lo = QVBoxLayout(self)
        lo.setContentsMargins(16, 14, 16, 12)
        lo.setSpacing(8)

        t = QLabel(tr("ui.advcfg.found_models", svc=svc_label, n=len(models)))
        t.setStyleSheet(f"color:{_TEXT_PRIMARY};font-size:14px;font-weight:bold;")
        lo.addWidget(t)

        self._combo = QComboBox()
        self._combo.addItems(models)
        lo.addWidget(self._combo)

        fixed = QLabel(tr("ui.advcfg.deploy_local"))
        fixed.setWordWrap(True)
        fixed.setStyleSheet(f"color:{_TEXT_HINT};font-size:10px;")
        lo.addWidget(fixed)

        dyn = QLabel(tr("ui.advcfg.discover_more"))
        dyn.setWordWrap(True)
        dyn.setStyleSheet(f"color:{_TEXT_HINT};font-size:10px;")
        lo.addWidget(dyn)

        br = QHBoxLayout()
        br.addStretch()
        c = QPushButton(tr("ui.btn.cancel"))
        c.setStyleSheet(f"QPushButton{{background:transparent;color:{_TEXT_HINT};border:1px solid {BORDER};border-radius:6px;padding:6px 12px;}}")
        c.clicked.connect(self.reject)
        br.addWidget(c)
        fb = QPushButton(tr("ui.advcfg.deploy_local_btn"))
        fb.setStyleSheet(f"QPushButton{{background:{ACCENT};color:#0a0a0a;border-radius:6px;padding:6px 12px;font-weight:600;}}")
        fb.clicked.connect(self._on_fixed)
        br.addWidget(fb)
        db = QPushButton(tr("ui.advcfg.discover_more_btn"))
        db.setStyleSheet(f"QPushButton{{background:{ACCENT};color:#0a0a0a;border-radius:6px;padding:6px 12px;font-weight:600;}}")
        db.clicked.connect(self._on_dynamic)
        br.addWidget(db)
        lo.addLayout(br)

    def _on_fixed(self):
        self.choice = ("fixed", self._combo.currentText())
        self.accept()

    def _on_dynamic(self):
        self.choice = ("dynamic", self._combo.currentText())
        self.accept()


# ═══════════════════════════════════════════════
# 子页面
# ═══════════════════════════════════════════════

class AdvancedConfigDialog(QDialog):
    # 保存成功后不再立即发 reload；改为「关闭配置页时」统一发一次。
    # 客户可能连续配多个模型，配完一起关页再刷新，避免每配一个就 reload 一次。
    reload_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("ui.advcfg.title"))
        # 固定合理宽度（容纳 380px API key + 边距），高度自适应内容，不撑满屏幕
        self.setFixedWidth(430)
        self.setStyleSheet(f"QDialog{{background:{_CARD_BG};color:{_TEXT_PRIMARY};}} QLabel{{background:transparent;}}")
        self._services = load_services()
        self._discovered: list[str] = []
        self._dirty = False  # 本次会话是否保存过配置
        self._reload_emitted = False  # reload 只发一次（保存即发，closeEvent 兜底同走这里）
        self._build()

    def _mark_saved(self, pid: str) -> None:
        self._dirty = True
        _stake("advcfg.saved", f"pid={pid} dirty=True", ok=True)

    def _emit_reload(self, reason: str) -> None:
        """reload 的唯一出口：一次会话只发一次。

        原来保存路径（_on_save_done）和 closeEvent 各发一条，两条路并存有竞态隐患
        （accept() 通常不触发 closeEvent，但反过来就会重复 reload）。现在两条路都
        走这里，谁先到谁发，后来者静默丢弃。
        """
        if self._reload_emitted:
            _stake("advcfg.reload", f"skip: already emitted ({reason})", ok=True)
            return
        self._reload_emitted = True
        self.reload_requested.emit()
        _stake("advcfg.reload", f"reload_requested emitted ({reason})", ok=True)

    def closeEvent(self, event) -> None:
        """关闭配置页时，若本次保存过配置，发 reload_requested（只发一次）。"""
        if self._dirty:
            self._emit_reload("close")
        else:
            _stake("advcfg.close", "no changes, skip reload", ok=True)
        super().closeEvent(event)

    def _build(self) -> None:
        lo = QVBoxLayout(self)
        lo.setContentsMargins(16, 14, 16, 12)
        lo.setSpacing(10)

        t = QLabel(tr("ui.advcfg.title"))
        t.setStyleSheet(f"color:{_TEXT_PRIMARY};font-size:16px;font-weight:bold;")
        lo.addWidget(t)

        d = QLabel(tr("ui.advcfg.desc"))
        d.setWordWrap(True)
        d.setStyleSheet(f"color:{_TEXT_HINT};font-size:11px;")
        lo.addWidget(d)

        # ── 本地服务（按解释层动态渲染）──
        sec = QLabel(tr("ui.advcfg.local_service"))
        sec.setStyleSheet(f"color:{_TEXT_PRIMARY};font-size:13px;font-weight:600;")
        lo.addWidget(sec)

        self._local_edits: dict[str, QLineEdit] = {}
        for svc in self._services:
            lo.addWidget(self._build_local_row(svc))

        lo.addSpacing(4)

        # ── 自定义端点 ──
        sec2 = QLabel(tr("ui.advcfg.custom_endpoint"))
        sec2.setStyleSheet(f"color:{_TEXT_PRIMARY};font-size:13px;font-weight:600;")
        lo.addWidget(sec2)

        pr = QHBoxLayout()
        pr.addWidget(QLabel(tr("ui.advcfg.protocol")))
        self._proto = QComboBox()
        for p in _protocol_choices():
            self._proto.addItem(p, p)
        self._proto.setMaximumWidth(200)
        pr.addWidget(self._proto, alignment=Qt.AlignmentFlag.AlignLeft)
        pr.addStretch()
        lo.addLayout(pr)

        self._base = QLineEdit()
        self._base.setPlaceholderText(tr("ui.advcfg.base_ph"))
        self._base.setStyleSheet(self._input_qss())
        self._base.setMinimumWidth(340)
        lo.addWidget(self._base, alignment=Qt.AlignmentFlag.AlignLeft)

        self._key = QLineEdit()
        self._key.setEchoMode(QLineEdit.EchoMode.Password)
        self._key.setPlaceholderText(tr("ui.advcfg.key_ph"))
        self._key.setStyleSheet(self._input_qss())
        self._key.setMinimumWidth(380)
        lo.addWidget(self._key, alignment=Qt.AlignmentFlag.AlignLeft)

        self._mid = QLineEdit()
        self._mid.setPlaceholderText(tr("ui.advcfg.model_ph"))
        self._mid.setStyleSheet(self._input_qss())
        self._mid.setMinimumWidth(220)
        lo.addWidget(self._mid, alignment=Qt.AlignmentFlag.AlignLeft)


        act = QHBoxLayout()
        probe = QPushButton(tr("ui.advcfg.test_conn"))
        probe.setStyleSheet(self._accent_btn_qss())
        probe.clicked.connect(self._do_probe)
        act.addWidget(probe)
        act.addStretch()
        save = QPushButton(tr("ui.btn.save"))
        save.setStyleSheet(self._accent_btn_qss())
        save.clicked.connect(self._do_save)
        act.addWidget(save)
        lo.addLayout(act)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f"color:{_TEXT_HINT};font-size:11px;")
        lo.addWidget(self._status)
        lo.addStretch()

    def _build_local_row(self, svc: dict[str, Any]) -> QWidget:
        w = QWidget()
        w.setStyleSheet(f"background:{BG['card']};border-radius:8px;")
        h = QHBoxLayout(w)
        h.setContentsMargins(10, 6, 10, 6)
        h.setSpacing(8)
        lbl = QLabel(svc["label"])
        lbl.setStyleSheet(f"color:{_TEXT_PRIMARY};font-size:12px;font-weight:600;")
        lbl.setMinimumWidth(60)
        h.addWidget(lbl)
        e = QLineEdit(svc["default_base"])
        e.setStyleSheet(self._input_qss())
        e.setMinimumWidth(340)
        h.addWidget(e, alignment=Qt.AlignmentFlag.AlignLeft)
        self._local_edits[svc["id"]] = e
        b = QPushButton(tr("ui.advcfg.discover_btn"))
        b.setStyleSheet(self._accent_btn_qss())
        b.clicked.connect(lambda _=False, s=svc: self._do_discover(s))
        h.addWidget(b, alignment=Qt.AlignmentFlag.AlignLeft)
        h.addStretch()
        return w

    def _input_qss(self) -> str:
        return f"""
            QLineEdit {{ background:{BG['card']}; color:{_TEXT_PRIMARY};
                         border:1px solid {BORDER}; border-radius:6px; padding:6px 10px; font-size:12px; }}
            QLineEdit:focus {{ border-color:{ACCENT}; }}
        """

    def _accent_btn_qss(self) -> str:
        return f"""
            QPushButton {{ background:{ACCENT}; color:#0a0a0a; border:none;
                           border-radius:6px; font-size:12px; font-weight:600; padding:6px 12px; }}
            QPushButton:hover {{ background:{ACCENT_HOVER}; }}
        """

    # ── 动作 ──

    def _do_discover(self, svc: dict[str, Any]) -> None:
        base = self._local_edits[svc["id"]].text().strip()
        if not base:
            self._set_status(tr("ui.advcfg.need_baseurl"), _YELLOW)
            return
        if getattr(self, "_discovering", False):
            return
        self._discovering = True
        self._set_status(tr("ui.advcfg.discovering", label=svc['label']), _TEXT_HINT)
        QApplication_process_events()

        self._discover_worker = self._DiscoverWorker(svc, base)
        self._discover_worker.done.connect(self._on_discover_done)
        self._discover_worker.start()

    def _on_discover_done(self, svc: dict, base: str, models: list) -> None:
        self._discovering = False
        if not models:
            self._set_status(tr("ui.advcfg.no_models", label=svc['label']), _RED)
            return
        dlg = _DiscoverResultDialog(svc["label"], models, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            self._set_status(tr("ui.advcfg.found_n", n=len(models)), _TEXT_HINT)
            return
        mode, default_model = dlg.choice
        if mode == "fixed":
            ok, msg = _write_fixed(svc, base, models)
        else:  # dynamic
            strategy = {"implicit_env": _write_implicit_env, "wildcard": _write_wildcard}.get(svc["dynamic_by"])
            if strategy is None:
                # 原来是死胡同：只红字提示「未知策略」，用户点了「动态部署」却一个字没写。
                # 数据没声明策略时退回「固定列表」写入（同组里最保守的落法），至少落底。
                _stake("discover.strategy",
                       f"{svc['id']} dynamic_by={svc['dynamic_by']!r} 无策略，回退 fixed", ok=False)
                ok, msg = _write_fixed(svc, base, models)
            else:
                ok, msg = strategy(svc, base, default_model)
        if ok:
            self._set_status(tr("ui.advcfg.saved_mode", m=tr("ui.advcfg.fixed") if mode == "fixed" else tr("ui.advcfg.dynamic")), _GREEN)
            self._mark_saved(svc["id"])
        else:
            self._set_status(tr("ui.advcfg.save_failed", msg=msg), _RED)

    def _do_probe(self) -> None:
        base = self._base.text().strip()
        key = self._key.text().strip()
        mid = self._mid.text().strip()
        if not base:
            self._set_status(tr("ui.advcfg.need_baseurl"), _YELLOW)
            return
        if getattr(self, "_probing", False):
            return
        pid = _derive_pid(base)
        fields = {"baseUrl": base, "api": self._proto.currentData(),
                  "apiKey": key or None, "models": [{"id": mid}] if mid else None}

        self._probing = True
        self._set_status(tr("ui.advcfg.probing"), _TEXT_HINT)
        QApplication_process_events()

        self._probe_worker = self._ProbeWorker(pid, fields)
        self._probe_worker.done.connect(self._on_probe_done)
        self._probe_worker.start()

    def _on_probe_done(self, pid: str, ok: bool, bucket: str) -> None:
        self._probing = False
        if ok:
            self._set_status(tr("ui.advcfg.probe_ok"), _GREEN)
        else:
            self._set_status(tr(_BUCKET_LABEL.get(bucket, bucket)), _YELLOW if bucket == "no_model" else _RED)

    class _ProbeWorker(QThread):
        """后台探测连通性，不阻塞 UI 主线程。"""
        done = Signal(str, bool, str)  # (pid, ok, bucket)

        def __init__(self, pid: str, fields: dict):
            super().__init__()
            self._pid = pid
            self._fields = fields

        def run(self):
            # 「测试连接」不是保存，副作用收到最小：
            #   - 配置里已有该 provider → 直接探，一个字都不写；
            #   - 没有才写一次（官方 --probe-provider 要求 provider 已在配置里），
            #     而且这次写 backup=False —— 测试不落到备份库。
            if self._pid not in _configured_provider_ids():
                ok, _ = write_custom_provider(self._pid, self._fields, backup=False)
                if not ok:
                    self.done.emit(self._pid, False, "unknown")
                    return
            ok, bucket = probe_provider(self._pid)
            self.done.emit(self._pid, ok, bucket)

    def _do_save(self) -> None:
        base = self._base.text().strip()
        key = self._key.text().strip()
        mid = self._mid.text().strip()
        if not mid:
            self._set_status(tr("ui.advcfg.need_model"), _YELLOW)
            return
        if getattr(self, "_saving", False):
            return
        # 与探测路径用同一套 pid 派生（原来这里 base 可空 → pid 落成 "custom"，
        # 探测却用 _derive_pid(base)，同一入口两套 provider id）。
        pid = _derive_pid(base)
        if not pid:
            self._set_status(tr("ui.advcfg.need_baseurl"), _YELLOW)
            return
        fields = {"baseUrl": base or None, "api": self._proto.currentData(),
                  "apiKey": key or None, "models": [{"id": mid}]}
        self._saving = True
        self._set_status(tr("ui.advcfg.configuring"), _TEXT_HINT)
        QApplication_process_events()

        self._worker = self._SaveWorker(pid, fields)
        self._worker.progress.connect(lambda t: self._set_status(t, _TEXT_HINT))
        self._worker.done.connect(self._on_save_done)
        self._worker.start()

    def _on_save_done(self, pid: str, ok: bool) -> None:
        self._saving = False
        if ok:
            self._set_status(tr("ui.advcfg.saved_provider", pid=pid), _GREEN)
            self._mark_saved(pid)
            # 保存成功 → 触发 reload（等同右键刷新）+ 关闭子窗口。
            # 走唯一出口 _emit_reload（与 closeEvent 那条路互斥，不重复 reload）。
            self._emit_reload("saved")
            QMessageBox.information(
                self, tr("ui.advcfg.config_ok"),
                tr("ui.advcfg.config_ok_msg", pid=pid),
            )
            self.accept()
        else:
            self._set_status(tr("ui.advcfg.save_failed", msg=pid), _RED)

    class _SaveWorker(QThread):
        """后台写 models.providers，不阻塞 UI。

        只负责写配置，不重启 Gateway——重启统一由 reload_requested →
        browser._restart_gateway（唯一入口，带探活+reload）触发，
        避免「写配置时重启一次 + 保存完成后又重启一次」的双重启竞态。
        """
        progress = Signal(str)  # 进度提示
        done = Signal(str, bool)

        def __init__(self, pid: str, fields: dict):
            super().__init__()
            self._pid = pid
            self._fields = fields

        def run(self):
            try:
                self.progress.emit(tr("ui.advcfg.writing"))
                ok, _ = write_custom_provider(self._pid, self._fields)
                self.done.emit(self._pid, ok)
            except Exception as _e:
                _stake("save_worker", f"异常 {type(_e).__name__}: {_e}", ok=False)
                self.done.emit(self._pid, False)

    class _DiscoverWorker(QThread):
        """后台拉本地服务发现端点，不阻塞 UI 主线程。"""
        done = Signal(object, str, list)  # (svc, base, models)

        def __init__(self, svc: dict, base: str):
            super().__init__()
            self._svc = svc
            self._base = base

        def run(self):
            models = discover_local_models(self._base, self._svc)
            self.done.emit(self._svc, self._base, models)

    def _set_status(self, text: str, color: str) -> None:
        self._status.setText(text)
        self._status.setStyleSheet(f"color:{color};font-size:11px;")


def QApplication_process_events() -> None:
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
