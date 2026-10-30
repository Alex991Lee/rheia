# -*- coding: utf-8 -*-
"""Scout - Data-driven probe/filter/publish module.

Architecture:
  scout.fetch()  -> download/read official doc corpus
  scout.filter() -> classify by syntactic structure
     Copy-A -> large/mani.json (consumer + gateway, clean)
     Copy-B -> SQLite baseline_pool (all entries incl. noise)
  scout.probe() -> combined fetch+filter+store

Version: based on YYYYMMDD format.
Table naming: doc_entries_YYYYMMDD

Disaster recovery tables (same DB, added by ensureRecoveryTables):
  user_prefs   — 外观配置 (key/value)
  credentials  — API keys（**明文**存 encrypted_value 列：加密尚未实现）
  recovery_state — 标记文件模拟 (key/status)

Iron rules:
- scout is the ONLY writer to large/mani.json
- scout communicates to large via file system (mani.json + signal)
- zero import of application modules
- noise is never discarded (stored in SQLite)
- baseline_pool.db lives at project root (above kernel/),
  so uninstall preserves it alongside PROGRAMDATA avatar/
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.testPole import logEvent


def _knownTypes() -> set:
    """已知类型词表：直接读**分类器自己的**规则表，不再维护一份会漂移的硬编码清单。

    2026-10-28：旧清单只有 10 个类型，漏了 other/plugin/config/env/reference/
    onboarding 等分类器实际会返回的类型 ⇒ 一次安装刷 517 条 STAKE-TYP 假警。
    """
    names = {"", "other", "noise", "gateway", "cli"}
    try:
        from kernel import filter as _f
        for _row in getattr(_f, "_URL_RULES", []):
            names.add(_row[1])
        for _row in getattr(_f, "_CONTENT_SIGNALS", []):
            names.add(_row[1])
    except Exception:
        pass
    return names


_KNOWN_TYPES = _knownTypes()
_UNKNOWN_TYPES: dict = {}

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE = Path(__file__).resolve().parent
_PROJECT_ROOT = _BASE.parent

# baseline_pool.db 是灾难恢复数据库，属于用户数据，必须放在固定的
# %PROGRAMDATA%\Rheia\（卸载不删、与可客制的安装目录 {app} 无关）。
# 之前放在项目根目录（{app}）是错的：安装目录可客制，且重装会丢失。
_PROGRAMDATA = os.environ.get("PROGRAMDATA", "")
if _PROGRAMDATA:
    _DB_DIR = Path(_PROGRAMDATA) / "Rheia"
else:
    # 兜底：环境变量缺失时退回项目根（开发环境）
    _DB_DIR = _PROJECT_ROOT
_DB_PATH = _DB_DIR / "baseline_pool.db"

CLASS_CONSUMER = "consumer"
CLASS_GATEWAY = "gateway"
CLASS_NOISE = "noise"

_OFFICIAL_DOC_DIR = _BASE / "Official text"
_OFFICIAL_DOC_FILE = _OFFICIAL_DOC_DIR / "llms-full.txt"

_MANIFEST_PATH = _BASE / "large" / "mani.json"
_MANIFEST_SIGNAL = _BASE / "large" / "manifest.signal"
_OT_PATH = _BASE / "large" / "ot.json"

# 自愈文件：独立文件，记录「壳体已对齐到哪个 OpenClaw 版本」的固化锚点。
# 放 ProgramData（用户数据，卸载不删），不是 kernel/large（活数据宿主）。
# 版本号由 WSL 内 OpenClaw 给到壳体，在「固化后、重启前」写入，只记这一个值。
_ALIGNMENT_PATH = _DB_DIR / "alignment.json"
# v3-spider-004：channel 接入命令改从 /<实体>/setup 子页取（主页是导航页，
# 一条命令都没有）。提取器变了 ⇒ 参与 rawHash 的版本必须跟着走，否则 SQLite
# 的 version_registry 会把这次改动当"文档没变"直接短路掉。
FILTER_VERSION = "v3-spider-004"
_HEADER_SOURCE_RE = re.compile(r"^#\s+(.+?)\nSource:\s*(https?://\S+)", re.MULTILINE)



# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class DocEntry:
    section_name: str = ""
    source_url: str = ""
    content: str = ""
    classification: str = ""
    consumer_name: str = ""
    method: str = ""
    category: str = ""
    fetched_at: str = ""
    signature: dict = field(default_factory=dict)
# ===== 新增 =====
    guessed_type: str = ""
    guess_confidence: float = 0.0
    external_definition: str = ""
    resolved_type: str = ""
    type_source: str = ""
    # ===== 三轴标签 =====
    auth_kind: str = "api_key"
    transport: str = "openai-compatible"
    model_source: str = "generic"


@dataclass
class _Segment:
    """filter() 内部段落解析结果"""
    name: str = ""
    source_url: str = ""
    body: str = ""
    start: int = 0
    end: int = 0
    index: int = 0


# ---------------------------------------------------------------------------
# 碳黑过滤器 — 烧掉描述性形容词，只保留确定性CLI信号
# ---------------------------------------------------------------------------

# CLI 确定性信号的正则模式
_RE_ENV_VAR = re.compile(r"^[A-Z][A-Z0-9_]+(?:_API_KEY|_SECRET|_TOKEN|_KEY|_ID)$", re.MULTILINE)
_RE_CLI_CMD = re.compile(r"^(?:\$\s*)?openclaw\s+\S+", re.MULTILINE)
_RE_MODEL_REF = re.compile(r"\b[a-zA-Z0-9_]+/[a-zA-Z0-9._-]+\b")
_RE_PLUGIN_REF = re.compile(r"@openclaw/[a-z-]+")
_RE_URL = re.compile(r"https?://[^\s<>\"']+")
_RE_CONFIG_KEY = re.compile(r"\b[a-z]+\.[a-z]+\.[a-zA-Z]+\b")
_RE_TABLE_ROW = re.compile(r"^\|.+\|$", re.MULTILINE)
_RE_CODE_BLOCK = re.compile(r"```[\s\S]*?```")


def _carbonFilter(raw_text: str) -> dict[str, list[str]]:
    """碳黑（虚光）过滤器。

    第一遍采样：只从原文中提取确定性信号——代码块、表格行、CLI命令、环境变量、URL、模型ref。
    丢弃所有描述性自然语言段落（"provides powerful AI models"、"is a"、"we recommend"等）。
    返回的 dict 里只有纯机械信号，没有形容词。
    """
    signals: dict[str, list[str]] = {
        "env_vars": [],
        "cli_commands": [],
        "model_refs": [],
        "plugins": [],
        "urls": [],
        "config_keys": [],
        "table_rows": [],
        "code_blocks": [],
    }

    # 先提取代码块（隔离自然语言）
    signals["code_blocks"] = [m.group() for m in _RE_CODE_BLOCK.finditer(raw_text)]

    # 代码块内的信号优先提取；但也不用排它，正文里的信号也采集
    for env in _RE_ENV_VAR.finditer(raw_text):
        if env.group() not in signals["env_vars"]:
            signals["env_vars"].append(env.group())
    for cli in _RE_CLI_CMD.finditer(raw_text):
        if cli.group() not in signals["cli_commands"]:
            signals["cli_commands"].append(cli.group().lstrip("$ "))
    for ref in _RE_MODEL_REF.finditer(raw_text):
        if ref.group() not in signals["model_refs"]:
            signals["model_refs"].append(ref.group())
    for p in _RE_PLUGIN_REF.finditer(raw_text):
        if p.group() not in signals["plugins"]:
            signals["plugins"].append(p.group())
    for u in _RE_URL.finditer(raw_text):
        if u.group() not in signals["urls"]:
            signals["urls"].append(u.group())
    for ck in _RE_CONFIG_KEY.finditer(raw_text):
        if ck.group() not in signals["config_keys"]:
            signals["config_keys"].append(ck.group())
    for tr in _RE_TABLE_ROW.finditer(raw_text):
        row = tr.group().strip()
        if row not in signals["table_rows"]:
            signals["table_rows"].append(row)

    logger.info("carbonFilter: %d env_vars, %d cli_cmds, %d model_refs, %d plugins, %d urls, %d config_keys, %d table_rows, %d code_blocks",
                len(signals["env_vars"]), len(signals["cli_commands"]), len(signals["model_refs"]),
                len(signals["plugins"]), len(signals["urls"]), len(signals["config_keys"]),
                len(signals["table_rows"]), len(signals["code_blocks"]))
    return signals


def _reverseClassify(signals: dict[str, list[str]]) -> dict[str, str]:
    """从确定性信号反推三轴标签（auth_kind, transport, model_source）。

    不需要在自然语言段落里猜关键词，直接从CLI命令和环境变量里读。
    """
    tags: dict[str, str] = {
        "auth_kind": "api_key",
        "transport": "openai-compatible",
        "model_source": "generic",
    }

    # 从环境变量推 auth_kind
    for env in signals.get("env_vars", []):
        upper = env.upper()
        if "NO_KEY" in upper or "NONE" in upper:
            tags["auth_kind"] = "no_key"
        elif "OAUTH" in upper or "OIDC" in upper:
            tags["auth_kind"] = "oauth"
        elif "AWS_" in upper or "BEDROCK" in upper:
            tags["auth_kind"] = "aws_sdk"
        elif "SERVICE_ACCOUNT" in upper or "GOOGLE_APPLICATION_CREDENTIALS" in upper:
            tags["auth_kind"] = "service_account"
        elif "CUSTOM" in upper and ("BASE" in upper or "URL" in upper):
            tags["auth_kind"] = "custom_proxy"
        elif "_API_KEY" in upper or "_SECRET" in upper or "_TOKEN" in upper or "_KEY" in upper:
            tags["auth_kind"] = "api_key"
        # 兜底: 有 _API_KEY 后缀的基本都是 api_key

    # 从CLI命令推 transport 和 auth_kind 细化
    for cmd in signals.get("cli_commands", []):
        lower_cmd = cmd.lower()
        if "anthropic" in lower_cmd or "claude" in lower_cmd:
            tags["auth_kind"] = "api_key"
            tags["transport"] = "anthropic-messages"
        elif "ollama" in lower_cmd or "lmstudio" in lower_cmd:
            tags["auth_kind"] = "no_key"
            tags["transport"] = "local-runtime"
        elif "bedrock" in lower_cmd:
            tags["auth_kind"] = "aws_sdk"
            tags["transport"] = "aws-bedrock"
        elif "google" in lower_cmd or "gemini" in lower_cmd:
            tags["transport"] = "google-gemini"
        elif "openai" in lower_cmd or "compatible" in lower_cmd:
            tags["transport"] = "openai-compatible"
        # 看models list结构推 model_source
        if "models list" in lower_cmd or "model list" in lower_cmd:
            tags["model_source"] = "live_discovery"

    # 从插件引用推 model_source
    for p in signals.get("plugins", []):
        lower_p = p.lower()
        if "provider" in lower_p:
            tags["model_source"] = "live_discovery"

    # 从表格行找模型列表 → model_source = static_catalog
    table_text = "\n".join(signals.get("table_rows", []))
    if signals.get("table_rows") and ("| Model ref" in table_text or "model" in table_text.lower()):
        tags["model_source"] = "static_catalog"

    logger.info("reverseClassify: auth_kind=%s transport=%s model_source=%s",
                tags["auth_kind"], tags["transport"], tags["model_source"])
    return tags

def _repair_if_corrupt() -> None:
    """baseline_pool.db 损坏时，备份后重建空库。

    灾难恢复库的文件头不是 SQLite magic（实测全 0x00）会导致后续
    executescript 抛「file is not a database」。损坏文件已无有效数据，
    保留一份 .corrupt 作证，再删掉让 sqlite 按新库重建。
    """
    try:
        if not _DB_PATH.exists():
            return
        if _DB_PATH.stat().st_size == 0:
            return  # 空文件是合法的新库
        with open(_DB_PATH, "rb") as f:
            header = f.read(16)
        if header == b"SQLite format 3\x00":
            return  # 有效库，不动
        backup = _DB_PATH.with_suffix(".db.corrupt")
        try:
            _DB_PATH.replace(backup)
            # 原来是静默改名 + 静默重建：凭证/偏好被清掉用户什么都不知道。
            logger.error("scout._repair_if_corrupt: %s 不是有效 SQLite 库，"
                         "已改名保留为 %s 并重建空库（原有凭证/偏好不再可用）",
                         _DB_PATH, backup)
        except OSError as exc:
            logger.error("scout._repair_if_corrupt: 损坏库改名失败（%s），按新库继续", exc)
    except OSError as exc:
        logger.warning("scout._repair_if_corrupt: 损坏检查失败（%s）", exc)


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _repair_if_corrupt()
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _getDb() -> sqlite3.Connection:
    """开库并备好**通用**表（版本化的条目表由 _detectVersion/store 按版本建）。

    原来这里还顺手建一张无版本的 doc_entries 旧表（+两条旧索引）：真实写入只走
    doc_entries_<ver>，那张表没有任何读者（旧设计遗留，删）。
    """
    conn = _connect()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS version_registry (
            version_id TEXT PRIMARY KEY,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            is_current INTEGER NOT NULL DEFAULT 0
        );
    """)
    return conn


# ═══════════════════════════════════════════════════════════════════
#  灾难恢复库表
# ═══════════════════════════════════════════════════════════════════

def ensureRecoveryTables() -> None:
    """建立 baseline_pool.db 中的灾难恢复三表。

    user_prefs     — 外观 + 偏好 (key/value)
    credentials    — API key / token (值**明文**存 encrypted_value 列；加密尚未实现)
    recovery_state — 标记文件替代 (key/status/updated_at)
    """
    conn = _connect()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS user_prefs (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS credentials (
                key TEXT PRIMARY KEY,
                encrypted_value TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS recovery_state (
                key TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS advanced_providers (
                provider_id TEXT PRIMARY KEY,
                base_url TEXT NOT NULL DEFAULT '',
                api TEXT NOT NULL DEFAULT '',
                api_key TEXT NOT NULL DEFAULT '',
                model_name TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS workspace_files (
                rel_path TEXT PRIMARY KEY,
                content TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)
        conn.commit()
    finally:
        conn.close()


def setPref(key: str, value: str) -> None:
    """写一个偏好项。"""
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO user_prefs (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def getPref(key: str) -> str:
    """读一个偏好项。"""
    conn = _connect()
    try:
        cur = conn.execute("SELECT value FROM user_prefs WHERE key = ?", (key,))
        row = cur.fetchone()
        return row[0] if row else ""
    finally:
        conn.close()


def getAllPrefs() -> dict[str, str]:
    """读全部偏好项。"""
    conn = _connect()
    try:
        cur = conn.execute("SELECT key, value FROM user_prefs ORDER BY key")
        return {r[0]: r[1] for r in cur.fetchall()}
    finally:
        conn.close()


def setCredential(key: str, plaintext: str, provider: str = "") -> None:
    """写凭证（**明文**写进 encrypted_value 列 —— 加密尚未实现，别当它是密文）。
    DB 不存在则自动建。"""
    ensureRecoveryTables()
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO credentials (key, encrypted_value, provider, updated_at) VALUES (?, ?, ?, datetime('now'))",
            (key, plaintext, provider),
        )
        conn.commit()
    finally:
        conn.close()


def getCredential(key: str) -> str:
    """读凭证（原样返回 encrypted_value 列的值，没有解密步骤）。DB 不存在或表未建时返回空。"""
    ensureRecoveryTables()
    conn = _connect()
    try:
        cur = conn.execute("SELECT encrypted_value FROM credentials WHERE key = ?", (key,))
        row = cur.fetchone()
        return row[0] if row else ""
    finally:
        conn.close()


def getAllCredentials() -> dict[str, tuple[str, str]]:
    """读全部凭证 → {key: (encrypted_value, provider)}。DB 不存在或表未建时返回空。"""
    ensureRecoveryTables()
    conn = _connect()
    try:
        cur = conn.execute("SELECT key, encrypted_value, provider FROM credentials ORDER BY key")
        return {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    finally:
        conn.close()


def deleteCredential(key: str) -> None:
    """删除一条凭证。DB 不存在则无操作。"""
    ensureRecoveryTables()
    conn = _connect()
    try:
        conn.execute("DELETE FROM credentials WHERE key = ?", (key,))
        conn.commit()
    finally:
        conn.close()


def setAdvancedProvider(provider_id: str, base_url: str, api: str,
                        api_key: str, model_name: str) -> None:
    """写一条高级配置 provider（base_url / api / api_key / model_name）。

    api_key 与凭证同理：**明文**落盘（加密尚未实现）。
    """
    ensureRecoveryTables()
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO advanced_providers "
            "(provider_id, base_url, api, api_key, model_name, updated_at) "
            "VALUES (?, ?, ?, ?, ?, datetime('now'))",
            (provider_id, base_url, api, api_key, model_name),
        )
        conn.commit()
    finally:
        conn.close()


def getAdvancedProvider(provider_id: str) -> dict[str, str]:
    """读一条高级配置 provider，返回 dict；不存在返回空 dict。"""
    ensureRecoveryTables()
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT base_url, api, api_key, model_name FROM advanced_providers "
            "WHERE provider_id = ?", (provider_id,))
        row = cur.fetchone()
        if row:
            return {"provider_id": provider_id, "base_url": row[0],
                    "api": row[1], "api_key": row[2], "model_name": row[3]}
        return {}
    finally:
        conn.close()


def getAllAdvancedProviders() -> dict[str, dict[str, str]]:
    """读全部高级配置 provider，返回 {provider_id: {base_url,api,api_key,model_name}}。"""
    ensureRecoveryTables()
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT provider_id, base_url, api, api_key, model_name "
            "FROM advanced_providers ORDER BY provider_id")
        return {
            r[0]: {"provider_id": r[0], "base_url": r[1], "api": r[2],
                   "api_key": r[3], "model_name": r[4]}
            for r in cur.fetchall()
        }
    finally:
        conn.close()


def setWorkspaceFile(rel_path: str, content: str) -> None:
    """写一条 workspace 文件（相对路径 → 内容）。"""
    ensureRecoveryTables()
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO workspace_files (rel_path, content, updated_at) "
            "VALUES (?, ?, datetime('now'))",
            (rel_path, content),
        )
        conn.commit()
    finally:
        conn.close()


def getAllWorkspaceFiles() -> dict[str, str]:
    """读全部 workspace 文件，返回 {rel_path: content}。"""
    ensureRecoveryTables()
    conn = _connect()
    try:
        cur = conn.execute("SELECT rel_path, content FROM workspace_files")
        return {r[0]: r[1] for r in cur.fetchall()}
    finally:
        conn.close()


def setRecoveryState(key: str, status: str) -> None:
    """设一个恢复状态。"""
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO recovery_state (key, status, updated_at) VALUES (?, ?, datetime('now'))",
            (key, status),
        )
        conn.commit()
    finally:
        conn.close()


def getRecoveryState(key: str) -> str:
    """读一个恢复状态。"""
    conn = _connect()
    try:
        cur = conn.execute("SELECT status FROM recovery_state WHERE key = ?", (key,))
        row = cur.fetchone()
        return row[0] if row else ""
    finally:
        conn.close()


def dbExists() -> bool:
    """baseline_pool.db 是否已存在。重装/恢复判断用。"""
    return _DB_PATH.exists()


def dbPath() -> str:
    """返回 baseline_pool.db 的完整路径。"""
    return str(_DB_PATH)


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

# ── 信任锚点（唯一硬编码，其余全部运行时派生）──
# npm registry 是互联网基础设施级锚点，几十年不动。openclaw 是 npm 包，
# metadata 里官方自己维护 homepage / repository / version。域名、仓库、docs host
# 变了，官方改这里，我们自动跟走 —— 代码里不再堆任何 doc URL。
# 官方源 + 国内镜像（淘宝 npmmirror，官方 registry 的事实标准镜像）并列：
# 简体中文系统走国内源（避开 urllib 对 registry.npmjs.org 的 SSL 卡顿），
# 非简体系统走官方源。两者都是互联网基础设施，不是写死的 doc URL。
_NPM_ANCHOR_OFFICIAL = "https://registry.npmjs.org/openclaw"
_NPM_ANCHOR_DOMESTIC = "https://registry.npmmirror.com/openclaw"

# 文档文件名候选（从 docs 域名派生，逐个探活）
_DOC_FILENAME_CANDIDATES = ["llms-full.txt", "llms.txt"]

# 官方文档域名（信任锚点，与 npm registry 同级）。docs.openclaw.ai 是官方文档
# 站，每段文档正文的 Source: 行都标注它。它直接进 sources 池，不再拉 GitHub README。
_DOC_HOST_OFFICIAL = "https://docs.openclaw.ai"


#: 内核层的等待上限：内核**不是**壳层 —— 无限等待会把调用方（probe / crawl 链）钉死。
#: urllib 用单次网络操作上限；curl 兜底再给一个总时长上限（大文件慢网也下得完）。
_HTTP_TIMEOUT_S = 30
_CURL_MAX_TIME_S = 600


def _http_get_etag(url: str, etag: str = "") -> tuple[str, str]:
    """带 ETag 条件请求拉取，返回 (etag, body)。

    用 If-None-Match 做「文档版本探测」：文档没变时服务器回 304，etag 为空、
    body 为空（不拉全文）；变了回 200，返回新 etag + 全文。
    etag 为空时无条件拉取。**设超时**：内核层不许无限等待（对端一挂起就永久阻塞调用方）。
    """
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    if etag:
        headers["If-None-Match"] = etag
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            new_etag = resp.headers.get("ETag", "")
            body = resp.read().decode("utf-8", errors="replace")
            return new_etag, body
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            # 文档未变：返回 (原 etag, "") 表示未变
            return etag, ""
        logger.warning("scout._http_get_etag: HTTP %d", exc.code)
        return "", ""
    except Exception as exc:
        logger.warning("scout._http_get_etag: 失败（%s）", exc)
        return "", ""


def _http_get(url: str) -> str:
    """统一 HTTP GET（urllib 优先，curl 兜底）。

    **设超时**：这是内核层（调用方是 probe / crawl 链），无限等待会把整条链钉死。
    urllib 用单次网络操作上限 _HTTP_TIMEOUT_S；curl 兜底用总时长上限
    _CURL_MAX_TIME_S（llms-full 13.7MB 慢网也下得完，但不会永远挂着）。
    400/404 等 HTTP 状态码是服务器立即返回的，直接抛 HTTPError，由调用方换源。

    自生长在传输层的投影：不押单一 HTTP 客户端。
      - 主：Python urllib（零外部依赖，随 Python 走）
      - 兜底：系统 curl（完整 TLS/代理/证书链，成熟网络栈）

    两者都失败才返回空串。不新增硬编码信任——curl 是系统已有工具，不是写死的 URL。
    返回 body 文本；失败抛异常，由调用方降级。
    """
    # 主路径：urllib（带 _HTTP_TIMEOUT_S 超时）
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logger.warning("scout._http_get: urllib 失败（%s），转 curl 兜底", exc)

    # 兜底路径：系统 curl（连接 + 总时长双上限）
    try:
        r = subprocess.run(
            ["curl", "-sS", "-L",
             "--connect-timeout", str(_HTTP_TIMEOUT_S),
             "--max-time", str(_CURL_MAX_TIME_S),
             "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", url],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        if r.returncode == 0 and r.stdout:
            return r.stdout.decode("utf-8", errors="replace")
        logger.warning("scout._http_get: curl 失败 rc=%d", r.returncode)
    except Exception as exc:
        logger.warning("scout._http_get: curl 不可用（%s）", exc)

    raise RuntimeError(f"urllib 和 curl 均失败: {url}")


def _isChineseSystem() -> bool:
    """判断系统是否为「简体中文」——只有简体才走国内镜像，繁体/其它走官方源。

    Windows LCID：低 10 位 primary language id，高 6 位 sublanguage id。
    中文 primary = 0x04；SUBLANG_CHINESE_SIMPLIFIED(0x02) = 简体，
    SUBLANG_CHINESE_TRADITIONAL(0x01) = 繁体（台/港/澳）。
    仅 sub == 0x02 判简体 True；繁体（0x01）及其它语言一律 False → 官方源。
    非 Windows 或无 ctypes 时返回 False（走官方源，宁慢勿错，不猜成国内）。
    """
    try:
        import ctypes
        lang = ctypes.windll.kernel32.GetUserDefaultUILanguage()
    except Exception:
        return False
    primary = lang & 0x3FF
    sub = (lang >> 10) & 0x3F
    return primary == 0x04 and sub == 0x02


def _npmAnchor() -> str:
    """返回 npm 锚点：简体中文系统→国内镜像，非简体→官方源。"""
    return _NPM_ANCHOR_DOMESTIC if _isChineseSystem() else _NPM_ANCHOR_OFFICIAL


def _fetch_npm_metadata() -> dict:
    """从 npm 锚点读 openclaw 的 metadata，返回 dict；失败返回空 dict。

    这是真源发现的唯一信任入口：homepage / repository / version 全是官方自述。
    走 _http_get（urllib 优先、curl 兜底），不设时限、随下随走。
    """
    try:
        raw = _http_get(_npmAnchor())
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("scout._fetch_npm_metadata: npm 锚点不可达（%s）", exc)
        return {}


def _derive_doc_sources(npm: dict) -> list[dict]:
    """从官方 docs 域名（信任锚点）派生文档真源池。

    官方文档站域名 docs.openclaw.ai 是信任锚点（与 npm registry 同级），
    每段文档正文 Source: 行都标它。文档文件名候选 × 域名 = 完整 URL。
    不再拉 GitHub README 提取 docs 域名——GitHub CDN 在国内常不通，那条路
    会卡 89% 预热（三源串行连接超时）。

    npm 元数据（锚点）在这里**真的用上**：官方把文档站挂在 npm 的 homepage 上时，
    就用它的 host 当 docs host（域名变了官方改 metadata 自动跟走）；homepage 不是
    docs host（如官网首页）或取不到 → 用信任锚点常量。（原来入参一次都没看，
    fetch / probe 每轮白拉一次 npm metadata，网络往返零收益。）
    返回 [{name, url}, ...]，name 用于日志标注降级来源。
    """
    host = _DOC_HOST_OFFICIAL
    home = str((npm or {}).get("homepage") or "").strip().rstrip("/")
    if home.startswith("https://") and "docs." in home:
        host = home
    sources: list[dict] = []
    for fn in _DOC_FILENAME_CANDIDATES:
        sources.append({"name": f"docs-{fn}", "url": f"{host}/{fn}"})
    return sources


def _fetch_collaborators(raw: str) -> None:
    """合作者抓取：从 OpenClaw 文档识别本地模型服务的合作者，去广域网抓官方文档，
    提取「发现端点/解析结构」翻译进字典。

    完整闭环：
      1. 从 OpenClaw 文档枚举本地后端名称（local-models 的 Pick a backend 表）
      2. 逐个去广域网抓官方文档（品牌域名规律推导 URL）
      3. 提取发现端点/解析结构 → 翻译进字典（字典里没有的自动写入）
    在 spider crawl 之前执行（保证 crawl 用到的字典已是抓取后的活数据）。
    失败静默——字典里已有的合作者兜底，抓不到不阻断。
    """
    try:
        from kernel.filter import _extract_local_backends
        from kernel.filter import fetch_and_translate, known_collaborators

        # 枚举 OpenClaw 文档里的本地后端名称
        candidates = _extract_local_backends(raw)
        # 合并字典里已知的（保证已知的也刷新）
        candidates.update(known_collaborators().keys())

        for name in sorted(candidates):
            try:
                spec = fetch_and_translate(name, http_get=_http_get)
                if spec:
                    logger.info("scout._fetch_collaborators: %s → discover=%s",
                                name, spec.get("discover"))
            except Exception as e:
                # 抓不到不该只留 debug：联网却一无所获必须在日志里看得见
                logger.warning("scout._fetch_collaborators: %s 抓取失败（%s）", name, e)
    except Exception as e:
        logger.warning("scout._fetch_collaborators: 跳过（%s）", e)


def fetch(source_url: str = "") -> str:
    """Fetch official doc corpus，真源自 npm 锚点派生，探活分级，缓存兜底。

    链路：
      1. npm 锚点读 metadata → 派生候选源池（域名/仓库变了，官方改 metadata 自动跟走）
      2. 逐源探活：200 且非空 → 采用；失败切下一个（写日志，显式降级）
      3. 全部失败 → 回退本地缓存上一版文档（绝不静默断供）

    内核层设超时（见 _HTTP_TIMEOUT_S / _CURL_MAX_TIME_S），不设 await。
    若显式传 source_url，则绕过派生、直连该 URL（调试用）。
    """
    if source_url:
        sources = [{"name": "explicit", "url": source_url}]
    else:
        npm = _fetch_npm_metadata()
        sources = _derive_doc_sources(npm)
        if not sources:
            logger.warning("scout.fetch: npm 锚点未派生到任何真源，将尝试本地缓存")

    last_err = ""
    for src in sources:
        try:
            raw = _http_get(src["url"])
            if not raw or len(raw.strip()) < 100:
                last_err = f"{src['name']}: empty/short body"
                logger.warning("scout.fetch: %s", last_err)
                continue
            if src is not sources[0]:
                logger.info("scout.fetch: 已降级到备源 %s", src["name"])
            raw = re.sub(r"<[^>]+>", " ", raw)
            return raw.strip()
        except Exception as exc:
            last_err = f"{src['name']}: {exc}"
            logger.warning("scout.fetch: 源 %s 失败，切下一个（%s）", src["name"], exc)

    # 兜底：本地缓存上一版文档（抵抗热寂的最后一道）
    if _OFFICIAL_DOC_FILE.exists():
        try:
            cached = _OFFICIAL_DOC_FILE.read_text(encoding="utf-8")
            if len(cached.strip()) >= 100:
                logger.warning(
                    "scout.fetch: 所有真源失败（%s），回退本地缓存 %d 字节",
                    last_err, len(cached),
                )
                return cached.strip()
        except OSError:
            pass

    logger.error("scout.fetch: 所有真源失败且无本地缓存，最后错误: %s", last_err)
    return ""


def _structuralSignature(body: str) -> dict[str, Any]:
    """Extract structural features from a document chunk."""
    return {
        "has_cardgroup": bool(re.search(r"\bCardGroup\b", body)),
        "has_quick_setup": bool(re.search(r"##\s*Quick\s+(start|setup|guide)", body, re.IGNORECASE)),
        "has_steps": len(re.findall(r"^\d+\.\s+", body, re.MULTILINE)) >= 3,
        "has_mermaid": bool(re.search(r"```mermaid", body)),
    }


def _isCliOnly(body: str) -> bool:
    """Detect if a chunk is purely CLI reference (noise)."""
    bl = [l for l in body.split("\n") if l.strip()]
    if not bl:
        return False
    cli = sum(1 for l in bl if l.startswith("openclaw ") or l.startswith("$ openclaw "))
    return cli / len(bl) > 0.8


def _parseSegments(raw_text: str) -> list[_Segment]:
    """Parse raw text into ordered segment list using _HEADER_SOURCE_RE."""
    segs: list[_Segment] = []
    for idx, m in enumerate(_HEADER_SOURCE_RE.finditer(raw_text)):
        name = m.group(1).strip()
        source_url = m.group(2).strip()
        ss = m.start()
        nn = _HEADER_SOURCE_RE.search(raw_text, m.end())
        ee = nn.start() if nn else len(raw_text)
        body = raw_text[ss:ee]
        segs.append(_Segment(
            name=name,
            source_url=source_url,
            body=body,
            start=ss,
            end=ee,
            index=idx,
        ))
    return segs



def filter(raw_text: str) -> tuple[list[DocEntry], list[DocEntry], list[DocEntry]]:
    """Classify document entries using multi-step semantic analysis.

    Step 0: _structuredFilter → 用新 filter 提取器做精确类型分类 + 结构化数据
    Step 0b: _reverseClassify → 从碳黑信号反推三轴标签
    Step 1: classify_url_type（新 type_classifier，四级判据、零网络零模型）。
    Step 2/3: 旧的爬虫背调 + 向量映射（_crawlIdentity / _mapType）已删 —— 只留
      globals() 注入通道（外部真要塞自定义实现时仍可从模块级注入）。

    Returns (consumer_entries, gateway_entries, noise_entries).
    """
    from kernel.filter import classify_url_type

    con: list[DocEntry] = []
    gw: list[DocEntry] = []
    noise: list[DocEntry] = []

    segments = _parseSegments(raw_text)
    logger.info("scout.filter: parsed %d segments", len(segments))

    # Step 0: 碳黑过滤 — 全文档采样确定性信号
    carbon_signals = _carbonFilter(raw_text)
    # Step 0b: 从确定性信号反推三轴标签
    cluster_tags = _reverseClassify(carbon_signals)
    logger.info("scout.filter: carbonFilter + reverseClassify done: %s", cluster_tags)

    # 防御性加载后台模块函数
    _crawl_fn = globals().get("_crawlIdentity")
# ⚠ 2026-10-17 逐行审计（该删删）：_map_fn 取用已删 —— 用它的类型消歧分支随 type_space
    #   空转链一并清掉，全文件再无调用点；_crawl_fn 仍在使用，保留。

    for seg in segments:
        if _isCliOnly(seg.body):
            noise.append(DocEntry(
                section_name=seg.name,
                source_url=seg.source_url,
                content=seg.body,
                classification=CLASS_NOISE,
                consumer_name=seg.name.split("/")[-1] if "/" in seg.name else seg.name,
                category=CLASS_NOISE,
                fetched_at=datetime.now(timezone.utc).isoformat(),
                signature=_structuralSignature(seg.body),
            ))
            continue

        # 第1步：新 type_classifier（四级判据，恒有结论："other" + 置信度 0 就是它的兜底）
        # —— 旧 _guessType 启发式分类器已删（被它取代，留着只会两套判据打架）
        resolved_type, confidence, type_src = classify_url_type(seg.source_url, seg.name, seg.body)
        guessed_type = resolved_type  # 兼容下游
        type_src = f"type_classifier_{type_src}"
        # SC-2 类型桩
        # 清单在模块级 _knownTypes() 里从分类器词表推导（见文件头）
        if guessed_type not in _KNOWN_TYPES:
            _UNKNOWN_TYPES[guessed_type] = _UNKNOWN_TYPES.get(guessed_type, 0) + 1
            logger.debug("[STAKE-TYP] unknown type '%s' for segment '%s'", guessed_type, seg.name)

        # 第2步：爬虫背调（防御性）
        external_def = ""
        if _crawl_fn:
            try:
                external_def = _crawl_fn(seg.name, seg.source_url) or ""
            except Exception:
                logger.debug("_crawlIdentity failed for %s", seg.source_url)
                external_def = ""

        # 第3步：向量映射（_mapType + type_space/model 消歧通道）已删
        resolved_type = guessed_type
        type_source = "context_fallback"

        # 粗分类
        if resolved_type == "gateway":
            classification = CLASS_GATEWAY
        elif resolved_type in ("", "cli", "noise", "infrastructure"):
            classification = CLASS_NOISE
        else:
            classification = CLASS_CONSUMER

        # 提取 consumer_name
        if classification == CLASS_CONSUMER:
            consumer_name = seg.name.split("/")[-1] if "/" in seg.name else seg.name
        elif classification == CLASS_GATEWAY:
            consumer_name = seg.name.split("/")[-1] if "/" in seg.name else seg.name
        else:
            consumer_name = ""

        entry = DocEntry(
            section_name=seg.name,
            source_url=seg.source_url,
            content=seg.body,
            classification=classification,
            consumer_name=consumer_name,
            category=classification,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            signature=_structuralSignature(seg.body),
            guessed_type=guessed_type,
            guess_confidence=confidence,
            external_definition=external_def,
            resolved_type=resolved_type,
            type_source=type_source,
            # 三轴标签 — 从碳黑过滤信号反推
            auth_kind=cluster_tags.get("auth_kind", "api_key"),
            transport=cluster_tags.get("transport", "openai-compatible"),
            model_source=cluster_tags.get("model_source", "generic"),
        )

        if classification == CLASS_CONSUMER:
            con.append(entry)
        elif classification == CLASS_GATEWAY:
            gw.append(entry)
        else:
            noise.append(entry)

    logger.info("scout.filter: consumer=%d, gateway=%d, noise=%d", len(con), len(gw), len(noise))
    # SC-4 计数桩
    _type_counts = {}
    for e in con:
        t = e.resolved_type if hasattr(e, 'resolved_type') else 'unknown'
        _type_counts[t] = _type_counts.get(t, 0) + 1
    logger.info("[STAKE-CNT] filter output: consumer=%d gateway=%d noise=%d | type_breakdown=%s",
                len(con), len(gw), len(noise), _type_counts)
    _zero_types = [t for t in ("provider", "model", "memory", "web_search") if _type_counts.get(t, 0) == 0]
    if _zero_types:
        logger.warning("[STAKE-CNT] zero-count types: %s", _zero_types)
    return con, gw, noise



def _detectVersion(conn: sqlite3.Connection, rawText: str, npmDate: str = "") -> tuple[str, str]:
    """Detect version ID from cached official doc corpus.
    Returns (version_id, table_name) tuple.
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS version_registry (
            version_id TEXT PRIMARY KEY,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            is_current INTEGER NOT NULL DEFAULT 0
        );
    """)
    raw_hash = hashlib.sha256(rawText.encode("utf-8")).hexdigest()
    base_vid = npmDate or datetime.now(timezone.utc).strftime("%Y%m%d")

    cur = conn.execute(
        "SELECT version_id, content_hash FROM version_registry WHERE version_id LIKE ?",
        (f"{base_vid}%",),
    )
    existing = cur.fetchone()
    if existing and existing[1] == raw_hash:
        return existing[0], f"doc_entries_{existing[0]}"

    if existing:
        suffix = 2
        while conn.execute(
            "SELECT 1 FROM version_registry WHERE version_id = ?",
            (f"{base_vid}_{suffix}",),
        ).fetchone():
            suffix += 1
        vid = f"{base_vid}_{suffix}"
    else:
        vid = base_vid

    conn.execute(
        "INSERT OR REPLACE INTO version_registry (version_id, content_hash, created_at, is_current) VALUES (?, ?, datetime('now'), 1)",
        (vid, raw_hash),
    )
    conn.commit()
    return vid, f"doc_entries_{vid}"


def store(entries: list[DocEntry], tname: str = "") -> tuple[int, int]:
    """Store entries into versioned SQLite table."""
    if not tname:
        tname = f"doc_entries_{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    vid = tname.replace("doc_entries_", "")
    # sanitize: table name must be alphanumeric + underscore only
    if not all(c.isalnum() or c == '_' for c in tname):
        raise ValueError(f"store: invalid table name {tname!r}")
 
    conn = _getDb()
    conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS {tname} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            section_name TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            classification TEXT NOT NULL DEFAULT 'noise',
            consumer_name TEXT NOT NULL DEFAULT '',
            method TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT '',
            fetched_at TEXT NOT NULL DEFAULT '',
            version_id TEXT NOT NULL DEFAULT '{vid}'
        );
    """)

    # executemany：几千条时逐条 execute 慢一个量级（每条一次语句解析）
    rows = [
        (e.section_name, e.source_url, e.content, e.classification, e.consumer_name,
         e.method, e.category, e.fetched_at, vid)
        for e in entries
    ]
    conn.executemany(
        f"INSERT INTO {tname} (section_name, source_url, content, classification, consumer_name, method, category, fetched_at, version_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    stored = len(rows)

    # 版本上限：超过 max_allowed 丢最旧的（原注释写 4-version，与实际 max_allowed=10 不符）
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'doc_entries_%' ORDER BY name"
    )
    tables = [r[0] for r in cur.fetchall()]
    max_allowed = 10
    if len(tables) > max_allowed:
        to_drop = tables[:-max_allowed]
        for tname_drop in to_drop:
            vid = tname_drop.replace("doc_entries_", "")
            conn.execute(f"DROP TABLE IF EXISTS {tname_drop}")
            conn.execute("DELETE FROM version_registry WHERE version_id = ?", (vid,))
        logger.info("scout.store: dropped %d old table(s) to enforce %d-version limit", len(to_drop), max_allowed)

    conn.commit()
    conn.close()
    logger.info("scout.store: stored=%d (table=%s)", stored, tname)
    # 第二返回值是"排队待处理条数"：本实现同步落盘、没有队列 ⇒ 恒 0。
    # （调用方别把它当"还有多少条在路上"的真实值读。）
    return stored, 0



def _fetchNpmLatestVersion() -> tuple[str, str]:
    """Query npm registry for latest OpenClaw version + publish date.
    Returns (version_string, yymmdd) e.g. ("1.2.3", "20260624").
    Returns ("", "") on failure.
    """
    headers = {
        "Accept": "application/json",
        "User-Agent": "Rheia-scout-version",
    }
    npm_url = _npmAnchor()
    try:
        req = urllib.request.Request(npm_url, headers=headers)
        with urllib.request.urlopen(req) as resp:
            npm_data = json.loads(resp.read().decode("utf-8", errors="replace"))
        if not isinstance(npm_data, dict):
            return "", ""
        ver = str(npm_data.get("version") or "").strip()
        if not ver:
            tags = npm_data.get("dist-tags")
            if isinstance(tags, dict):
                ver = str(tags.get("latest") or "").strip()
        if not ver:
            return "", ""
        yymmdd = ""
        times = npm_data.get("time")
        if isinstance(times, dict):
            for key in (ver, f"v{ver}"):
                raw = times.get(key)
                if isinstance(raw, str) and raw.strip():
                    iso = raw.strip()
                    try:
                        if iso.endswith("Z"):
                            iso = iso[:-1] + "+00:00"
                        dt = datetime.fromisoformat(iso)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        yymmdd = dt.astimezone(timezone.utc).strftime("%Y%m%d")
                    except ValueError:
                        pass
                    break
        return ver, yymmdd
    except Exception as exc:
        logger.debug("_fetchNpmLatestVersion fail: %s", exc)
        return "", ""


def probe(source_url: str = "") -> dict[str, Any]:
    """Probe: fetch, filter, dual-output pipeline.

    数据动力守卫（设计哲学：抗热寂 = 保持流动）：
      拉文档前先核验本机 OpenClaw 版本 vs npm 官方 latest。官方没迭代 →
      短路返回（不 fetch 全文、不爬虫）；官方迭代了 → 才 fetch + crawl。

    Copy-A: clean consumer/gateway -> ot.json + settleManifest().
    Copy-B: full (incl. noise) -> SQLite baseline_pool.

    Returns {changed, storage, manifest, version}
    """
    logEvent("probe.start")
    start = time.time()

    # ── 文档版本探测：用 ETag 条件请求做轻量短路 ──
    # 官方 llms-full.txt 带 ETag（文档内容版本标识）。上次成功拉取后把 etag 存进
    # mani 的 version 段。这次 fetch 带 If-None-Match：文档没变 → 服务器回 304，
    # 直接撤（不拉 13.7MB 全文、不 crawl）；变了 → 回 200，才走完整拉取。

    # 1) 派生真源（与 fetch 相同）
    if source_url:
        sources = [{"name": "explicit", "url": source_url}]
    else:
        npm = _fetch_npm_metadata()
        sources = _derive_doc_sources(npm)
        if not sources:
            logger.warning("scout.probe: npm 锚点未派生到任何真源，将尝试本地缓存")

    prev_etag = _readManifestEtag()
    new_etag = ""
    raw = ""

    # 2) 逐源 ETag 条件请求
    last_err = ""
    for src in sources:
        try:
            new_etag, raw = _http_get_etag(src["url"], prev_etag)
            if raw:  # 200，拿到正文
                if src is not sources[0]:
                    logger.info("scout.probe: 已降级到备源 %s", src["name"])
                # 拿到新 etag 就立刻落盘：内容没变（hash 短路提前 return）时也要存，
                # 否则下次又是无条件拉全文，ETag 短路永远不激活。
                if new_etag:
                    _writeManifestEtag(new_etag)
                raw = re.sub(r"<[^>]+>", " ", raw).strip()
                break
            # raw 为空：要么 304（文档未变），要么失败
            if new_etag == prev_etag and prev_etag:
                # 304 未变：直接撤
                local = checkLocalVersion()
                logger.info("scout.probe: 文档 ETag 未变（%s），跳过拉取+crawl", prev_etag)
                return {
                    "changed": False, "storage": (0, 0),
                    "manifest": {"consumer": 0, "gateway": 0},
                    "version": {"local": local["local_version"], "latest": local["latest_version"], "behind": local.get("behind", False)},
                }
            last_err = f"{src['name']}: 304/empty"
        except Exception as exc:
            last_err = f"{src['name']}: {exc}"
            logger.warning("scout.probe: 源 %s 失败（%s）", src["name"], exc)

    # 3) 所有源都没拿到正文 → 兜底本地缓存
    if not raw:
        if _OFFICIAL_DOC_FILE.exists():
            try:
                cached = _OFFICIAL_DOC_FILE.read_text(encoding="utf-8")
                if len(cached.strip()) >= 100:
                    logger.warning("scout.probe: 所有真源失败（%s），回退本地缓存", last_err)
                    raw = cached.strip()
            except OSError:
                pass
    if not raw:
        logger.warning("scout.probe: no data from fetch")
        local = checkLocalVersion()
        return {
            "changed": False, "storage": (0, 0),
            "manifest": {"consumer": 0, "gateway": 0},
            "version": {"local": local["local_version"], "latest": local["latest_version"], "behind": local.get("behind", False)},
        }

    # SC-1 计数桩
    logger.info("[STAKE-CNT] fetch returned %d bytes", len(raw))

    # Hash comparison: skip pipeline if content unchanged
    rawHash = hashlib.sha256(f"{raw}:{FILTER_VERSION}".encode("utf-8")).hexdigest()
    # 本机版本核验结果（供 version 段写入 + 短路返回时报告）
    local = checkLocalVersion()
    latest = local.get("latest_version", "")
    conn = _getDb()
    cur = conn.execute(
        "SELECT version_id FROM version_registry WHERE content_hash = ? AND is_current = 1",
        (rawHash,),
    )
    existing = cur.fetchone()
    conn.close()
    if existing:
        logger.info("scout.probe: version unchanged (%s), discarding download", existing[0])
        return {
            "changed": False, "storage": (0, 0),
            "manifest": {"consumer": 0, "gateway": 0},
            "version": {"local": local["local_version"], "latest": latest, "behind": local.get("behind", False)},
        }

    # New content: cache locally, then proceed with pipeline
    _OFFICIAL_DOC_DIR.mkdir(parents=True, exist_ok=True)
    _OFFICIAL_DOC_FILE.write_text(raw, encoding="utf-8")
    logger.info("scout.probe: new version cached %d bytes", len(raw))

    # Copy-A: spider 读懂文档 → 确定性序列化 → 写 ot.json → settleManifest 增量替代
    # （旧 filter() + publishToManifest 已由 spider.crawl_serialized 取代）
    # 先拿 npm 版本（锚点），供 spider 产物 version 元数据 + Copy-B 的 version_registry
    npm_ver, npm_date = _fetchNpmLatestVersion()
    version_info = (npm_ver, npm_date) if npm_ver else None
    if npm_ver and not latest:
        latest = npm_ver

    # 合作者抓取（spider 之前，联网）：对本地模型服务，去广域网抓官方文档，
    # 提取「发现端点/解析结构」翻译进字典。失败静默，spider 用字典兜底。
    _fetch_collaborators(raw)

    # 采已部署 OpenClaw 的真实插件 origin（装通后才有），供 spider 自我纠正。
    # 未装通时返回空，spider 走纯文档抓取（确定性不受影响）。
    deployed = _readDeployedPlugins()

    from kernel.filter import crawl_serialized
    # 需求进采集：客户/人格经客制化提的需求（自定义区 intent + 扩展位消费端声明）随本次
    # crawl 做实体链接 —— 命中哪个官方实体、官方出处在哪、没命中为什么，都进 ot。
    # 需求不是搬进 mani 当一张卡就算完，它得在采集里被「办」。
    demands = _readCustomDemands()
    # 上一版声明：用来差分**机制变化**（本轮机制事实 vs 上一版）。
    # 读不到就差分空跑（不许静默当成"没变化"而不记日志）。
    prev_manifest: dict = {}
    try:
        if _MANIFEST_PATH.exists():
            prev_manifest = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("scout.probe: 读上一版 mani 失败，机制变化差分将按空算: %s", exc)
    ot_content = crawl_serialized(raw, version_info=version_info, deployed=deployed,
                                  demands=demands, prev_manifest=prev_manifest)
    # 把本机版本核验结果一并写进 ot.json 的 version 段（manifest 唯一声明源），
    # usm 通过 mani.json 感知「本机版本 vs 官方 latest」。
    ot_content = _injectLocalVersion(ot_content, local, latest, etag=new_etag)
    _OT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OT_PATH.write_text(ot_content, encoding="utf-8")
    logger.info("scout.probe: spider wrote ot.json (%d bytes)", len(ot_content))
    settleManifest()

    # Copy-B: store ALL entries (incl. noise) to SQLite
    # 旧 filter 的 con/gw/noise 三元组由 spider 的 grouped 结构取代；
    # noise 在此保留兼容：spider 未识别的段落仍进 SQLite 兜底。
    try:
        con, gw, noise = filter(raw)
    except Exception as exc:
        # 不许把异常当"没有条目"无声吞掉（原来 except: pass 后照常往下走）
        logger.warning("scout.probe: Copy-B filter() 失败，本次不落 SQLite 兜底: %s", exc)
        con, gw, noise = [], [], []
    all_entries = con + gw + noise
    conn = _connect()
    vid, tname = _detectVersion(conn, raw, npmDate=npm_date)
    conn.close()
    stored, queued = store(all_entries, tname=tname)

    elapsed = time.time() - start
    if _UNKNOWN_TYPES:
        # 一条汇总代替几百条重复告警：真出现新类型仍然看得见
        logger.warning("[STAKE-TYP] 未登记类型汇总: %s（如确属新类型，请并入 typeSpace）",
                       ", ".join("%s=%d" % kv for kv in sorted(_UNKNOWN_TYPES.items())))
        _UNKNOWN_TYPES.clear()
    logger.info("scout.probe: spider crawl done, stored=%d, queued=%d, elapsed=%.2fs", stored, queued, elapsed)
    return {
        "changed": True, "storage": (stored, queued),
        # 真实计数（原来恒写 0，调用方拿不到任何真实值）
        "manifest": {"consumer": len(con), "gateway": len(gw)},
        "version": {"local": local["local_version"], "latest": latest, "behind": local.get("behind", False)},
    }


def _readManifestEtag() -> str:
    """读 mani.json version.etag（上次成功拉取文档的 ETag），不存在返回空。

    ETag 是官方 llms-full.txt 的「文档版本标识」——用它做轻量短路：下次 fetch
    带 If-None-Match，文档没变服务器回 304，不用拉 13.7MB 全文。
    """
    try:
        if not _MANIFEST_PATH.exists():
            return ""
        with open(_MANIFEST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        ver = data.get("version")
        if isinstance(ver, dict):
            return str(ver.get("etag") or "").strip()
        return ""
    except (OSError, json.JSONDecodeError):
        return ""


def _writeManifestEtag(etag: str) -> None:
    """把本次成功拉取拿到的 ETag 写进 mani.json version.etag。

    只写 etag 这一个字段，不动 version 段其它键（local_version / latest_version /
    behind 由 _injectLocalVersion 在内容变化时才写）。失败只告警——写不进 etag 只是
    下次退回无条件拉取，不阻断本次 probe。读改写后确定性序列化（sort_keys），
    与 _injectLocalVersion / settleManifest 一致；**内容没变不落盘**（不抖 mtime）。
    """
    if not etag:
        return
    try:
        if _MANIFEST_PATH.exists():
            with open(_MANIFEST_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {}
        if not isinstance(data, dict):
            data = {}
        ver = data.get("version")
        if not isinstance(ver, dict):
            ver = {}
        ver["etag"] = etag
        data["version"] = ver
        _MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        new_text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
        # 幂等：内容没变就不落盘 —— 每写一次 mtime 必变，壳侧监视 mani.json 的
        # 消费端会把"文件变了"当新声明 ⇒ 无谓刷新（屏闪嫌疑）。etag 通常几轮不变。
        try:
            if _MANIFEST_PATH.read_text(encoding="utf-8") == new_text:
                logger.info("scout._writeManifestEtag: etag 未变（%s），跳过写入", etag)
                return
        except OSError:
            pass
        with open(_MANIFEST_PATH, "w", encoding="utf-8") as f:
            f.write(new_text)
        logger.info("scout._writeManifestEtag: etag 已写 mani.json (%s)", etag)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("scout._writeManifestEtag: 写 etag 失败（%s）", exc)


def _injectLocalVersion(ot_content: str, local: dict, latest: str, etag: str = "") -> str:
    """把本机版本核验结果写进 ot.json 的 version 段（不破坏 spider 已写的 npm 元数据）。

    version 段从 {npm_latest, npm_published_at, type} 扩展为也含 local_version /
    latest_version / behind，供 usm 读 mani 感知版本差。spider 未写 version 段时
    也补上（保证 mani 始终带版本信息，不再是 null）。

    etag 也一并带上：成功路径上 mani 是被 ot 覆写的（settleManifest），etag 若只写在
    覆写前的 mani 里，一次成功抓取就把它冲掉 ⇒ ETag 短路下次失效（白拉 13.7MB）。
    """
    try:
        data = json.loads(ot_content)
    except json.JSONDecodeError:
        return ot_content
    if not isinstance(data, dict):
        return ot_content

    ver = data.get("version")
    if not isinstance(ver, dict):
        ver = {}
    ver.setdefault("npm_latest", latest or ver.get("npm_latest", ""))
    ver["latest_version"] = latest or ""
    ver["local_version"] = local.get("local_version", "")
    ver["behind"] = bool(local.get("behind", False))
    if etag:
        ver["etag"] = etag
    data["version"] = ver
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)


# ── tlc 声明注入：settleManifest 覆写成功后幂等写入 mani ──
# tlc 只认确定性信号（退出码 / 崩溃预兆），声明里不带任何时间闸字段。
_TLC_DECL = {
    "id": "tlc",
    "type": "tlc_guard",
    "display_name": "tlc 守护",
    "scope": "shell",
    "watch": ["exit_code", "crash_precursor"],
    "normal_exit": ["gateway_systemd_restart", "self_heal_restart"],
    "policy": "keep_alive",
    "lifecycle": "persistent",
    "exempt": ["glory_keepalive", "tlc"],   # 保活/心跳类不进队列、不判慢
    "defer_restart_on_conversation": True,  # 对话进行中禁止重启（防消息被切断）
}

#: 重载车道声明：一个重载对象一条。同车道请求忙时只标脏（合并成一次）⇒ 消闪。
#: `sources` = 哪些触发来源走这条道（**消费端自己来问**，不靠别人推）——
#: 想把某个触发从一条道挪到另一条道，改这里的 sources 就行，代码不用动。
#: ⚠ 2026-10-13：id 统一加 `shell-lane-` 前缀 —— 原来叫 chat 的那条**跟官方
#:   Control UI 的 chat 页撞名**，滤镜按官方真源判成"官方有这个消费端、走官方路由"；
#:   加前缀后跟插件一样落进"真源里没有 ⇒ 本地"那一支（滤镜一个字不用动）。
# ⚠ 2026-10-17：原来还有一条 "shell-lane-page"（display_name「整页重载」，
# sources = probe / pulse / page / gateway_restart）—— 那是**整页重载**车道：
# 消费端照它把唯一可见的 QWebEngineView 整页 reload = 整页重绘/白屏闪。
# 已删。留下两条都是**局部**刷新道（侧栏 / 聊天区），不做整页重载。
_RELOAD_DECLS = [
    {"id": "shell-lane-sidebar", "type": "reload_lane", "scope": "shell", "coalesce": True,
     "display_name": "侧栏刷新",
     "sources": ["sidebar", "skills"]},
    {"id": "shell-lane-chat", "type": "reload_lane", "scope": "shell", "coalesce": True,
     "display_name": "聊天区刷新",
     "sources": ["chat", "session"]},
]


def injectReloadDeclarations() -> bool:
    """把重载车道声明 upsert 进 mani.json 的 entries.reload（幂等）。"""
    try:
        if not _MANIFEST_PATH.exists():
            return False
        with open(_MANIFEST_PATH, "r", encoding="utf-8") as f:
            mani = json.load(f)
        if not isinstance(mani, dict):
            return False
        entries = mani.setdefault("entries", {})
        if not isinstance(entries, dict):
            entries = mani["entries"] = {}
        rl = entries.setdefault("reload", [])
        if not isinstance(rl, list):
            rl = entries["reload"] = []
        for decl in _RELOAD_DECLS:
            for i, item in enumerate(rl):
                if isinstance(item, dict) and item.get("id") == decl["id"]:
                    rl[i] = decl
                    break
            else:
                rl.append(decl)
        new_text = json.dumps(mani, ensure_ascii=False, indent=2)
        # 幂等：与盘上一致就不落盘。原来每次 settle 后无条件重写 = mtime 必抖，
        # 壳侧监视 mani.json 的消费端会把这当"新声明" ⇒ 再来一次无谓刷新。
        try:
            if _MANIFEST_PATH.read_text(encoding="utf-8") == new_text:
                logger.info("scout.injectReloadDeclarations: 声明无变化，跳过写入")
                return False
        except OSError:
            pass
        with open(_MANIFEST_PATH, "w", encoding="utf-8") as f:
            f.write(new_text)
        logger.info("scout.injectReloadDeclarations: 重载车道声明已注入 entries.reload")
        return True
    except Exception as e:
        logger.warning("scout.injectReloadDeclarations failed: %s", e)
        return False


def injectTlcDeclaration() -> bool:
    """把 tlc 声明 upsert 进 mani.json 的 entries.tlc。返回是否写入。"""
    try:
        if not _MANIFEST_PATH.exists():
            return False
        with open(_MANIFEST_PATH, "r", encoding="utf-8") as f:
            mani = json.load(f)
        if not isinstance(mani, dict):
            return False
        entries = mani.setdefault("entries", {})
        if not isinstance(entries, dict):
            entries = mani["entries"] = {}
        tlc_list = entries.setdefault("tlc", [])
        if not isinstance(tlc_list, list):
            tlc_list = entries["tlc"] = []
        # upsert：已有 tlc（同 id）→ 更新；无 → 追加
        for i, item in enumerate(tlc_list):
            if isinstance(item, dict) and item.get("id") == "tlc":
                tlc_list[i] = _TLC_DECL
                break
        else:
            tlc_list.append(_TLC_DECL)
        new_text = json.dumps(mani, ensure_ascii=False, indent=2)
        # 幂等：与盘上一致就不落盘（同 injectReloadDeclarations：写一次 mtime 必抖）
        try:
            if _MANIFEST_PATH.read_text(encoding="utf-8") == new_text:
                logger.info("scout.injectTlcDeclaration: 声明无变化，跳过写入")
                return False
        except OSError:
            pass
        with open(_MANIFEST_PATH, "w", encoding="utf-8") as f:
            f.write(new_text)
        logger.info("scout.injectTlcDeclaration: tlc 声明已注入 mani.json entries.tlc")
        return True
    except Exception as e:
        logger.warning("scout.injectTlcDeclaration failed: %s", e)
        return False


def injectCustomDemands() -> bool:
    """把客制化的需求条目 upsert 进 mani.json 的 entries.custom_demands。

    与 injectTlcDeclaration 同一位置、同一语义（settleManifest 覆写后注入，
    幂等，每次覆写后都在）——消费端声明由壳层注入，ot 覆写不冲掉。

    **满足结论合并**（2026-09-21）：spider 的实体链接结论经 ot.json 落进 mani，
    这里按 `id + 文本指纹` 并回需求条目 ——
      · 需求本体以**自定义区**为准（客户的话），满足结论以**采集**为准（官方的账）；
      · 客户改了需求文本（指纹不符）→ 旧结论作废、标「待重判」，绝不把旧结论
        贴到新需求上（那是拿昨天的答案回答今天的问题）；
      · 还没跑过采集 → 显式写 `satisfied: null` + reason，不静默留空。

    读不到需求条目 → 清掉旧声明（客户删了条目，声明也该消失，不留幽灵）。
    内容与现有声明一致 → 不写盘（不刷 mtime、不触发上游 diff）。
    """
    try:
        if not _MANIFEST_PATH.exists():
            return False
        from kernel.filter import declaration_fp
        demands = _readCustomDemands()
        with open(_MANIFEST_PATH, "r", encoding="utf-8") as f:
            mani = json.load(f)
        if not isinstance(mani, dict):
            return False
        entries = mani.setdefault("entries", {})
        if not isinstance(entries, dict):
            entries = mani["entries"] = {}
        prev = {}
        for d in (entries.get("custom_demands") or []):
            if isinstance(d, dict) and d.get("id"):
                prev[str(d["id"])] = d
        merged: list[dict] = []
        for d in demands:
            rec = dict(d)
            # 需求与消费端声明用同一套合并规则：本体以自定义区为准，满足结论以采集为准；
            # 指纹不符（声明变了）→ 旧结论作废重判。消费端的结论是「真源里怎么给的、
            # 装法是什么」（本地插件会显式标 match=external），同样归采集的账。
            did = str(rec.get("id") or "")
            fp = declaration_fp(rec)
            rec["fp"] = fp
            old = prev.get(did) if did else None
            if old and old.get("fp") == fp:
                # 结论原样搬回来：**凡是采集给的、自定义区没有的键**（satisfied / mechanism /
                # declaration / runtime…）都归采集的账。手抄键名清单迟早漏，所以按"不在本体里"认。
                for k, v in old.items():
                    if k not in rec and k != "fp":
                        rec[k] = v
                if old.get("satisfied") is None and old.get("reason"):
                    rec["reason"] = old["reason"]
            else:
                rec["satisfied"] = None
                rec["reason"] = "待采集重判（需求新加/已变更，或本次未跑到采集）"
            merged.append(rec)
        if entries.get("custom_demands") == merged:
            return True  # 幂等：声明一致就不重写（规格只在有新声明时才重算）
        # ── 声明与运行规格**当场反推**（不等官方迭代）──────────────────
        # 机制事实已经在 mani 里（上一轮采到了），所以需求一到就能对着它反推声明；
        # 否则"客户提了需求"又要躺到下次官方改版才有结论 —— 那就是装死。
        # ⚠ 只反推**还没有结论**的条目（新加/刚变更的）：
        #   已有结论的一律原样保留 —— 重算既会刷盘（幂等破掉），
        #   又可能在"这份 mani 里没有语料"的场合把好结论算成"没命中"（实测踩过）。
        need = [r for r in merged if not r.get("declaration")]
        if need:
            try:
                from kernel.filter import (mechanism_facts, mechanism_delta,
                                           satisfy_demands as _satisfy, plugin_runtime)
                _facts = mechanism_facts(mani)
                _delta = mechanism_delta(mani, _facts)
                _fresh = {str(x.get("id")): x
                          for x in _satisfy(entries, need, facts=_facts, delta=_delta)}
                merged = [_fresh.get(str(r.get("id")), r) for r in merged]
                mani["plugin_runtime"] = plugin_runtime(merged)
            except Exception as exc:
                logger.warning("scout.injectCustomDemands: 声明/规格反推失败（声明仍照写）: %s", exc)
        entries["custom_demands"] = merged
        with open(_MANIFEST_PATH, "w", encoding="utf-8") as f:
            json.dump(mani, f, ensure_ascii=False, indent=2)
        _n_hit = sum(1 for r in merged if r.get("satisfied"))
        logger.info("scout.injectCustomDemands: %d 条需求已注入 mani entries.custom_demands（命中 %d）",
                    len(merged), _n_hit)
        logEvent("demand.inject", {"count": len(merged), "satisfied": _n_hit})
        return True
    except Exception as e:
        logger.warning("scout.injectCustomDemands failed: %s", e)
        return False


def settleManifest() -> bool:
    """Compare ot.json with mani.json, settle the difference.
    Same -> delete ot.json (no update)
    Different -> overwrite mani.json with ot.json, write signal, delete ot.json
    Returns True if manifest was updated, False otherwise.
    """
    if not _OT_PATH.exists():
        return False
    otContent = _OT_PATH.read_text(encoding="utf-8")
    if _MANIFEST_PATH.exists():
        maniContent = _MANIFEST_PATH.read_text(encoding="utf-8")
        if otContent == maniContent:
            _OT_PATH.unlink()
            logger.info("scout.settleManifest: identical, removed ot.json")
            return False
    manDir = _MANIFEST_PATH.parent
    manDir.mkdir(parents=True, exist_ok=True)
    # SC-6 计数桩：在删除旧 mani.json 之前保存 old_count
    _old_count = 0
    if _MANIFEST_PATH.exists():
        try:
            _old_data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
            _old_count = _count_manifest_entries(_old_data)
        except Exception:
            pass
        _MANIFEST_PATH.unlink()
    _OT_PATH.rename(_MANIFEST_PATH)
    manifestData = json.loads(otContent)
    now = datetime.now(timezone.utc).isoformat()
    conCount = _count_manifest_entries(manifestData)
    gwCount = _count_gateway_entries(manifestData)
    signal = {"updated_at": now, "consumer": conCount, "gateway": gwCount}
    if "version" in manifestData:
        vInfo = manifestData["version"]
        if isinstance(vInfo, dict) and vInfo.get("npm_latest"):
            signal["version"] = vInfo["npm_latest"]
    _new_count = conCount + gwCount
    _delta = _new_count - _old_count
    logger.info("[STAKE-CNT] manifest update: old=%d new=%d delta=%d", _old_count, _new_count, _delta)
    if abs(_delta) > 50 and _old_count > 0:
        logger.warning("[STAKE-CNT] large manifest delta: %d entries changed", _delta)
    logEvent("settle.signal", signal)
    _MANIFEST_SIGNAL.write_text(json.dumps(signal, ensure_ascii=False), encoding="utf-8")
    logger.info("scout.settleManifest: promoted ot.json -> mani.json, signal raised")
    # ── ot 覆写 mani 完成后：注入 tlc 守护消费端声明（幂等，每次覆写后都在）──
    try:
        injectTlcDeclaration()
    except Exception:
        pass
    # ── 同一处再注入"重载车道"声明 ──
    try:
        injectReloadDeclarations()
    except Exception:
        pass
    # ── 同样注入客制化需求声明：客户经客制化提的需求，落进 mani 供消费端读 ──
    try:
        injectCustomDemands()
    except Exception:
        pass
    return True


#: entries 下**不是消费端**的键（声明车道 / 守护 / 需求结论）：计数必须排除，
#: 否则 entries.reload / entries.tlc / entries.custom_demands 被当成消费端 ⇒ 计数虚高
#: （settleManifest 的 signal 与 STAKE-CNT 都读这个数）。
_NON_CONSUMER_ENTRY_KEYS = {"reload", "tlc", "custom_demands", "mechanisms"}


def _count_manifest_entries(data: dict) -> int:
    """数 grouped 结构里的消费端总数（entries 下各类 list 的长度和，排除声明车道）。"""
    entries = data.get("entries", {})
    if isinstance(entries, dict):
        return sum(len(v) for k, v in entries.items()
                   if k not in _NON_CONSUMER_ENTRY_KEYS and isinstance(v, list))
    return 0


def _count_gateway_entries(data: dict) -> int:
    """数 grouped 结构里的 gateway 消费端数。"""
    entries = data.get("entries", {})
    if isinstance(entries, dict):
        return len(entries.get("gateways", []) or [])
    return 0


def health_check() -> dict:
    """Report current probe status: manifest, db, consumer/gateway counts."""
    result = {
        "mani_exists": _MANIFEST_PATH.exists(),
        "db_exists": _DB_PATH.exists(),
        "consumer_count": 0,
        "gateway_count": 0,
    }
    if _MANIFEST_PATH.exists():
        try:
            data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                # mani 现在是 entries 分组结构：按顶层 type 数（老写法）恒为 0
                _gw = _count_gateway_entries(data)
                result["gateway_count"] = _gw
                result["consumer_count"] = max(0, _count_manifest_entries(data) - _gw)
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
    return result


# ── Gateway 状态侦察 ──

_WSL_DISTRO = os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu"  # 兜底；实际发行版由 findOpenClawDistro 探测

#: 内核层跑 wsl 命令的等待上限：一条挂死的 wsl 不许把调用方永远钉住（原来无上限）。
_WSL_CMD_TIMEOUT_S = 60


def _runWslCheck(cmd: str) -> tuple[int, str]:
    """跑一条 WSL 命令，返回 (returncode, stdout_stripped)。

    统一在 WSL 侧设 LC_ALL=C.UTF-8，从源头保证输出是 UTF-8——openclaw 的
    --json / --help 输出编码在 WSL 默认 locale 下不稳定（有时 UTF-16LE），
    与其在解码层猜编码，不如源头钉死 UTF-8。**带超时**（内核层调用：一条挂死的
    wsl 命令不许把调用方永远钉住，见 _WSL_CMD_TIMEOUT_S）。
    """
    import subprocess as _sp
    si = _sp.STARTUPINFO()
    si.dwFlags |= _sp.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    try:
        # 在 bash -c 内部统一 UTF-8（源头），外层命令由调用方拼好；
        # 拼法不匹配时**不做手术**（宁可不设 locale，也不把命令改坏）
        _mark = 'bash -c "'
        if _mark in cmd and "LC_ALL" not in cmd:
            cmd = cmd.replace(_mark, _mark + "export LC_ALL=C.UTF-8; ", 1)
        r = _sp.run(
            cmd, shell=True, capture_output=True,
            timeout=_WSL_CMD_TIMEOUT_S,
            creationflags=_sp.CREATE_NO_WINDOW,
            startupinfo=si,
        )
        raw = r.stdout
        if not raw:
            # stdout 空时（如 help 输出到 stderr），取 stderr
            raw = r.stderr or b""
        # 源头已是 UTF-8，直接解码；仍留 utf-16-le 兜底（防旧命令没带 LC_ALL）
        for enc in ("utf-8", "utf-16-le", "gbk", "cp1252"):
            try:
                out = raw.decode(enc)
                out = out.replace("\x00", "").strip()
                if out:
                    return r.returncode, out
            except (UnicodeDecodeError, LookupError):
                continue
        return r.returncode, raw.decode("utf-8", errors="replace").replace("\x00", "").strip()
    except Exception as exc:
        # 原来连异常类型都不留：超时/命令不存在/编码炸都长一个样
        logger.warning("scout._runWslCheck: 命令失败或超时（%s）: %s", exc, cmd[:120])
        return -1, ""


def _self_heal() -> bool:
    """自愈：全量对齐 OpenClaw，只在「尚未对齐」或「版本迭代」时触发。

    正确模型：
      - 版本号由 WSL 内 OpenClaw 给到壳体（openclaw --version）
      - 固化锚点存在独立的「自愈文件」alignment.json，只记「已对齐的版本号」这一个值
      - 流程：采样真值 → 逐层纠正 mani → 写盘（固化）→ 读 WSL OpenClaw 版本号
        → 写自愈文件 → 返回 True（触发唯一一次重启）

    启动时：
      读当前 OpenClaw 版本 vs 自愈文件里固化的锚点版本
        锚点不存在（新装/从未对齐）→ 全量对齐 + 固化 + 重启
        版本一致 → 已对齐，不纠正、不重启
        版本不同（OpenClaw 迭代）→ 重新全量对齐 + 固化 + 重启

    采不到真值静默跳过（返回 False）。
    """
    # 1) 读当前 OpenClaw 版本（WSL 内 openclaw --version）
    current = checkLocalVersion().get("local_version", "")
    if not current:
        return False
    # 读固化锚点（自愈文件里的 aligned_version）
    anchored = ""
    try:
        if _ALIGNMENT_PATH.exists():
            anchored = (json.loads(_ALIGNMENT_PATH.read_text(encoding="utf-8"))
                        .get("aligned_version", ""))
    except (OSError, json.JSONDecodeError):
        anchored = ""

    # 2) 已对齐且版本一致 → 不纠正、不重启
    if anchored and anchored == current:
        return False

    # 3) 未对齐（锚点空）或版本迭代 → 全量对齐
    ground_truth = _sample_deployed_truth()
    if not ground_truth:
        return False
    try:
        with open(_MANIFEST_PATH, "r", encoding="utf-8") as f:
            mani = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    providers = (mani.get("entries") or {}).get("providers") or []
    if not providers:
        return False

    fixed: list[str] = []
    for corrector in (_correct_plugin_kind, _correct_auth_choice, _correct_capabilities):
        try:
            fixed.extend(corrector(providers, ground_truth))
        except Exception as exc:
            logger.warning("scout._self_heal: 纠正器 %s 异常（%s）", corrector.__name__, exc)

    # 4) 固化：写回 mani（有纠偏才写）
    if fixed:
        try:
            with open(_MANIFEST_PATH, "w", encoding="utf-8") as f:
                json.dump(mani, f, ensure_ascii=False, indent=2, sort_keys=True)
            logger.info("scout._self_heal: 自愈 %d 项: %s", len(fixed), fixed[:20])
        except OSError as exc:
            logger.warning("scout._self_heal: 写 mani 失败（%s）", exc)
            return False

    # 5) 固化后、重启前：把 WSL OpenClaw 版本号写进自愈文件
    try:
        _ALIGNMENT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _ALIGNMENT_PATH.write_text(
            json.dumps({"aligned_version": current}, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except OSError as exc:
        logger.warning("scout._self_heal: 写自愈文件失败（%s）", exc)
        return False

    logger.info("scout._self_heal: 对齐 OpenClaw %s（锚点 %s），自愈 %d 项，触发重启",
                current, anchored or "(none)", len(fixed))
    return True


def _sample_deployed_truth(distro: str | None = None) -> dict:
    """统一采样：一次采全已部署 OpenClaw 的真值源。

    返回 {plugins: {plugin_id: {origin, providerIds}}, auth_choices: {choice: 1}}。
    OpenClaw 未装 / 任何一项采不到时返回空 dict（自愈整体跳过）。
    """
    d = distro or findOpenClawDistro()
    if not d:
        return {}
    plugins = _readDeployedPlugins(d)
    if not plugins:
        return {}
    auth_choices = _readDeployedAuthChoices(d)
    return {
        "plugins": plugins,
        "auth_choices": auth_choices,
    }


def _correct_plugin_kind(providers: list[dict], truth: dict) -> list[str]:
    """纠正器：plugin.kind（用部署 origin 真值）+ 连带 outSider。"""
    plugins = truth.get("plugins") or {}

    def _to_kind(origin: str) -> str:
        o = (origin or "").lower()
        if o == "bundled":
            return "bundled"
        if o == "global":
            return "official"
        return ""

    fixed: list[str] = []
    for p in providers:
        pid = p.get("id", "")
        info = plugins.get(pid)
        if not info:
            continue
        to_kind = _to_kind(info.get("origin", ""))
        if not to_kind:
            continue
        from_kind = (p.get("plugin") or {}).get("kind", "unknown")
        if from_kind == to_kind:
            continue
        (p.setdefault("plugin", {}))["kind"] = to_kind
        p["outSider"] = to_kind not in ("bundled", "official")
        fixed.append(f"kind:{pid}:{from_kind}->{to_kind}")
    return fixed


def _correct_auth_choice(providers: list[dict], truth: dict) -> list[str]:
    """纠正器：auth_choice 缺失补全（用 onboard --help 真值）。

    从真值里找「以 pid 开头、以 -api-key 结尾」的 auth_choice，取最短者
    （最短 = 最接近 pid 本体 = 官方主路径形态）。数据驱动，不硬编 `{pid}-api-key`
    规律——那样对 alibaba（真值 alibaba-model-studio-api-key）、moonshot（有两个
    moonshot-api-key / moonshot-api-key-cn）会错。
    """
    choices = truth.get("auth_choices") or {}
    if not choices:
        return []
    fixed: list[str] = []
    for p in providers:
        pid = p.get("id", "")
        if p.get("auth_choice"):
            continue
        # 官方 external（有 package）才有 auth_choice；bundled 不需要
        if not (p.get("plugin") or {}).get("package"):
            continue
        # 从真值里筛「pid 前缀 + -api-key 后缀」，取最短（官方主路径）
        candidates = [c for c in choices
                      if c.startswith(pid) and c.endswith("-api-key")]
        if not candidates:
            continue
        best = min(candidates, key=len)
        p["auth_choice"] = best
        fixed.append(f"auth_choice:{pid}:{best}")
    return fixed


def _correct_capabilities(providers: list[dict], truth: dict) -> list[str]:
    """纠正器：capabilities 用部署真值（各 ProviderIds 维度）覆盖。

    壳层照甲方 OpenClaw 实际来：capabilities 该反映「运行时真实注册」的能力，
    不是 spider 抓文档猜的能力。用 plugins list 的各 ProviderIds 维度，反查每个
    provider 实际注册了哪些能力，覆盖 mani 的 capabilities（文档多抓的删掉）。

    维度 → 能力映射（官方 plugins list 字段 → mani capabilities 值）：
      providerIds → model
      embeddingProviderIds → embedding
      speechProviderIds → speech
      mediaUnderstandingProviderIds → media
      imageGenerationProviderIds / videoGenerationProviderIds / musicGenerationProviderIds → image
      webSearchProviderIds / webFetchProviderIds → web_search
    """
    plugins = truth.get("plugins") or {}
    # 维度 → capability 值
    dim_to_cap = (
        ("providerIds", "model"),
        ("embeddingProviderIds", "embedding"),
        ("speechProviderIds", "speech"),
        ("mediaUnderstandingProviderIds", "media"),
        ("imageGenerationProviderIds", "image"),
        ("videoGenerationProviderIds", "image"),
        ("musicGenerationProviderIds", "image"),
        ("webSearchProviderIds", "web_search"),
        ("webFetchProviderIds", "web_search"),
    )
    # 反查：每个 provider 在部署真值里实际注册了哪些能力
    deployed_caps: dict[str, set[str]] = {}
    for info in plugins.values():
        for dim, cap in dim_to_cap:
            for pid in info.get(dim, []):
                deployed_caps.setdefault(pid, set()).add(cap)

    fixed: list[str] = []
    for p in providers:
        pid = p.get("id", "")
        if pid not in deployed_caps:
            continue
        to_caps = sorted(deployed_caps[pid])
        from_caps = sorted(p.get("capabilities") or [])
        if to_caps == from_caps:
            continue
        p["capabilities"] = to_caps
        fixed.append(f"capabilities:{pid}:{from_caps}->{to_caps}")
    return fixed


def _readCustomDemands() -> list[dict]:
    """读客制化区里的【需求条目】（客户/人格模块经客制化提的需求）。

    数据源：user_prefs 表的 sidebar_custom（客制化的配置本体）。
    只取 type=intent 的条目——那是「需求已澄清、等着被满足」的。
    返回 [{id, name, description}]；读不到/无 intent 条目 → 空列表。

    为什么走这条路：客制化本来就是「向 kernel 提需求」的口子
    （设计文档 re260803001.md：核心理念 = kernel 三条通路 + 客制化外联），
    它把需求存成 user_prefs，scout 读出来当参数去官方文档办——不另造机制。
    """
    try:
        out = []
        # ① 客户手写的需求（自定义区 sidebar_custom 里的 intent 条目）
        raw = getPref("sidebar_custom")
        if raw:
            cfg = json.loads(raw)
            for e in (cfg.get("entries") or []):
                if not isinstance(e, dict) or e.get("type") != "intent":
                    continue
                if e.get("enabled") is False:
                    continue
                act = e.get("action") or {}
                out.append({
                    "id": str(e.get("id", "")),
                    "name": str(e.get("label_zh") or e.get("label_en") or ""),
                    "description": str(act.get("description") or "") if isinstance(act, dict) else "",
                })
        # ② 扩展位自己声明的消费端（人格模块写进同一通道的另一把 key）
        #    带 plugin spec / installer / hooks / lifecycle，供 mani 声明消费端。
        sc = getPref("slot_consumer")
        if sc:
            try:
                d = json.loads(sc)
                if isinstance(d, dict) and d.get("id"):
                    out.append(d)
            except Exception:
                pass
        # ③ 干预声明同走此通道：TLC、重载车道、nameGuard 各有自己的 key，消费端自己来要。
        #    nameGuard 声明：watch=agent_identity，名字不一致即校成卡名。
        for _key, _want in (("tlc_consumer", "tlc_guard"),
                            ("reload_consumer", "reload_lane"),
                            ("nameguard_consumer", "nameGuard")):
            _raw = getPref(_key)
            if not _raw:
                continue
            try:
                _d = json.loads(_raw)
            except Exception:
                continue
            _items = _d if isinstance(_d, list) else [_d]
            for _it in _items:
                if isinstance(_it, dict) and _it.get("id"):
                    _rec = dict(_it)
                    _rec.setdefault("type", _want)
                    out.append(_rec)
        return out
    except Exception:
        return []


def _readDeployedPlugins(distro: str | None = None) -> dict[str, dict]:
    """采已部署 OpenClaw 的真实插件状态（装通后才可采）。

    `openclaw plugins list --json` 是权威真值，每个插件暴露多个 providerIds 维度：
      origin                    — bundled（随 OpenClaw 自带）/ global（npm 装的外部插件）
      providerIds               — 模型 provider
      embeddingProviderIds      — 嵌入 provider
      speechProviderIds         — 语音 provider
      mediaUnderstandingProviderIds — 媒体理解 provider
      imageGenerationProviderIds — 图像生成 provider
      videoGenerationProviderIds — 视频生成 provider
      musicGenerationProviderIds — 音乐生成 provider
      webSearchProviderIds      — 网页搜索 provider
      webFetchProviderIds       — 网页抓取 provider
      channelIds                — 频道

    返回 {plugin_id: {origin, providerIds, ...各维度}}。OpenClaw 未装/采不到返回空 dict。
    """
    d = distro or findOpenClawDistro()
    if not d:
        return {}
    rc, out = _runWslCheck(
        f'wsl -d {d} -u root -- bash -c '
        f'"export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; '
        f'openclaw plugins list --json 2>/dev/null"'
    )
    if rc != 0 or not out.strip():
        return {}
    try:
        data = json.loads(out)
        # --json 输出不一定是对象（可能是数组/标量）：非对象按"采不到"处理，
        # 不能让 AttributeError 冒泡 —— probe 的调用处没包 try，会整条 probe 打挂
        if not isinstance(data, dict):
            logger.warning("scout._readDeployedPlugins: --json 输出不是对象（%s），按采不到处理",
                           type(data).__name__)
            return {}
        plugins = data.get("plugins") or []
        result: dict[str, dict] = {}
        for p in plugins:
            if not isinstance(p, dict):
                continue
            pid = p.get("id")
            if not pid:
                continue
            # 保真采全所有 providerIds 维度（对齐层划分的依据）
            result[pid] = {
                "origin": p.get("origin", ""),
                "providerIds": p.get("providerIds") or [],
                "embeddingProviderIds": p.get("embeddingProviderIds") or [],
                "speechProviderIds": p.get("speechProviderIds") or [],
                "mediaUnderstandingProviderIds": p.get("mediaUnderstandingProviderIds") or [],
                "imageGenerationProviderIds": p.get("imageGenerationProviderIds") or [],
                "videoGenerationProviderIds": p.get("videoGenerationProviderIds") or [],
                "musicGenerationProviderIds": p.get("musicGenerationProviderIds") or [],
                "webSearchProviderIds": p.get("webSearchProviderIds") or [],
                "webFetchProviderIds": p.get("webFetchProviderIds") or [],
                "channelIds": p.get("channelIds") or [],
            }
        return result
    except json.JSONDecodeError as exc:
        logger.warning("scout._readDeployedPlugins: --json 解析失败（%s），按采不到处理", exc)
        return {}


def _readDeployedAuthChoices(distro: str | None = None) -> dict[str, str]:
    """采已部署 OpenClaw 的 onboard --auth-choice 真值（装通后才可采）。

    `openclaw onboard --help` 的 --auth-choice 段列出所有合法 auth-choice 值，
    这是比 spider 抓文档更权威的真源（现场真实 CLI）。返回 {auth_choice值: 1}，
    用于补全 spider 抓不到 auth_choice 的 provider。
    """
    d = distro or findOpenClawDistro()
    if not d:
        return {}
    rc, out = _runWslCheck(
        f'wsl -d {d} -u root -- bash -c '
        f'"export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; '
        f'openclaw onboard --help 2>/dev/null"'
    )
    if rc != 0 or not out.strip():
        return {}
    m = re.search(r"--auth-choice\s+<choice>\s+Auth:\s*([^\n]+)", out)
    if not m:
        return {}
    choices: dict[str, str] = {}
    for c in m.group(1).split("|"):
        c = c.strip()
        if c and c not in ("custom-api-key", "setup-token", "token", "apiKey", "skip"):
            choices[c] = c
    return choices


def findOpenClawDistro() -> str:
    """探针：遍历 `wsl --list --verbose` 所有发行版，找到有 OpenClaw 的那个。

    返回发行版名；找不到返回空字符串。每步都栽桩子，失败也能看见断在哪。
    """
    rc, out = _runWslCheck("wsl --list --verbose")
    if rc != 0 or not out.strip():
        return ""

    # 解析发行版名（每行格式："  Ubuntu-24.04   Running  2" 或 "* Ubuntu  Running  2"）。
    # 首列 `*` 是默认发行版标记，须剥掉再取发行版名（否则 `*` 被当第一列，
    # 真正的发行版名 Ubuntu 永远进不了候选）。
    candidates = []
    for line in out.splitlines():
        line = line.replace("\x00", "").strip()
        if not line:
            continue
        # 剥掉默认标记 `*`，再取第一列
        name = line.lstrip("*").split()[0]
        # 跳过表头（"NAME" 那行）——发行版名不会叫 NAME
        if name and name.upper() != "NAME":
            candidates.append(name)

    for d in candidates:
        rc2, o2 = _runWslCheck(
            f'wsl -d {d} -- bash -c "command -v openclaw >/dev/null 2>&1 && echo OK"',
        )
        if rc2 == 0 and "OK" in o2:
            return d

    return ""


def readGatewayAuth(distro: str) -> dict:
    """探针：读指定发行版 ~/.openclaw/openclaw.json 的 gateway.auth 段，反推 mode。

    返回 {"distro", "url", "token", "password", "mode", "ok"}；每步栽桩子。
    """
    result = {"distro": distro, "url": "http://127.0.0.1:18789", "token": "", "password": "", "mode": "", "ok": False}

    rc, out = _runWslCheck(
        f'wsl -d {distro} -u root -- bash -c "cat ~/.openclaw/openclaw.json 2>/dev/null"',
    )
    if rc != 0 or not out.strip():
        return result

    try:
        cfg = json.loads(out)
    except json.JSONDecodeError as e:
        return result

    auth = cfg.get("gateway", {}).get("auth", {})
    result["token"] = auth.get("token", "")
    result["password"] = auth.get("password", "")
    result["mode"] = auth.get("mode", "")

    # 反推 mode（官方警告：token+password 都配但 mode 没显式设会启动失败）
    if result["mode"]:
        pass  # 已有显式 mode，尊重它
    elif result["token"] and not result["password"]:
        result["mode"] = "token"
    elif result["password"] and not result["token"]:
        result["mode"] = "password"
    elif result["token"] and result["password"]:
        result["mode"] = "token"  # 歧义态：默认 token（官方要求显式选一）
    else:
        result["mode"] = "none"

    result["ok"] = True
    return result


def checkGatewayConfig(distro: str = "") -> tuple[bool, str]:
    """检查 Gateway 配置是否就绪。

    读 WSL 内 ``~/.openclaw/openclaw.json``，判断 models.providers 下
    是否存在至少一个 provider 有非空的 apiKey。

    Returns:
        (ready, start_page)
        - ready=True → 用户已配置 API key，建议打开 /chat
        - ready=False → 无 API key，建议打开 /settings/model-providers
    """
    d = distro or _WSL_DISTRO
    rc, out = _runWslCheck(
        f'wsl -d {d} -u root -- bash -c "cat ~/.openclaw/openclaw.json 2>/dev/null"',
    )
    if rc != 0 or not out.strip():
        return False, "/settings/model-providers"

    try:
        cfg = json.loads(out)
    except json.JSONDecodeError:
        return False, "/settings/model-providers"

    providers = cfg.get("models", {}).get("providers", {})
    if not providers:
        return False, "/settings/model-providers"

    for pid, pcfg in providers.items():
        if not isinstance(pcfg, dict):
            continue
        key = pcfg.get("apiKey", pcfg.get("api_key", ""))
        if key and str(key).strip():
            return True, "/chat"

    return False, "/settings/model-providers"


# ── 本机 OpenClaw 版本核验（scout 使命的触发前提）──
#
# 设计哲学：数据动力来自官方迭代。scout 的使命不只在「拉官方文档」，
# 更在「核验本机 OpenClaw 版本 vs 官方 latest」，据此决定要不要拉新文档。
# 之前缺了这一步，导致 mani.json 一旦生成就永不再探（热寂）。

# OpenClaw 版本号形如 "2026.8.1"（年月.主次），转元组比较，避免字符串比较
# 把 "2026.10.1" < "2026.9.1" 判错。
_VERSION_RE = re.compile(r"(\d{4})\.(\d{1,3})\.(\d{1,3})")

# 完整版本号：保留第四段和 -N / .N 后缀（如 2026.9.1-1、2026.9.1.1）。
# local_version（自愈锚点）用它提取，任何版本变化都要能被识别，不截断。
_FULL_VERSION_RE = re.compile(r"(\d{4}\.\d{1,3}\.\d{1,3}(?:[.-]\d+)*)")


def _parse_version(ver: str) -> tuple[int, int, int]:
    """解析 OpenClaw 版本号 → (年, 主, 次) 元组；解析失败返回 (0,0,0)。

    只取前三段做大小比较（behind 判断），第四段/后缀不参与大小比较，
    但会体现在 local_version 全文里（自愈锚点按全文对比）。
    """
    if not ver:
        return (0, 0, 0)
    m = _VERSION_RE.search(str(ver))
    if not m:
        return (0, 0, 0)
    try:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return (0, 0, 0)


def _extract_full_version(out: str) -> str:
    """从 openclaw --version 输出提取完整版本号（含第四段/后缀）。"""
    m = _FULL_VERSION_RE.search(out)
    return m.group(1) if m else ""


def _version_older(local: str, remote: str) -> bool:
    """本机版本是否落后于远端版本（语义化比较，非字符串比较）。"""
    return _parse_version(local) < _parse_version(remote)


def checkLocalVersion(distro: str = "") -> dict:
    """核验本机 OpenClaw 版本 vs npm 官方 latest。

    使命入口：scout 判断「要不要拉最新文档」的触发前提。轻量（一次本地
    openclaw --version + 一次 npm 联网），比整段 probe（fetch 全文 + 爬虫）
    便宜几个数量级。

    返回 {"local_version", "latest_version", "local_distro", "behind", "ok"}。
    behind=True 表示本机落后官方（官方迭代了，数据动力该流动）。
    """
    result = {
        "local_version": "",
        "latest_version": "",
        "local_distro": "",
        "behind": False,
        "ok": False,
    }

    # 1. 本机版本：遍历 WSL 找到 OpenClaw 发行版，读 openclaw --version
    d = distro or findOpenClawDistro()
    result["local_distro"] = d
    if not d:
        return result

    # openclaw --version 输出是 UTF-8/ASCII（bash 内 Linux 进程），不能用
    # _runWslCheck（它 utf-16-le 优先，会把 ASCII 解成乱码）。这里专用 UTF-8 读。
    try:
        import subprocess as _sp
        si = _sp.STARTUPINFO()
        si.dwFlags |= _sp.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        r = _sp.run(
            f'wsl -d {d} -- bash -c "openclaw --version 2>&1"',
            shell=True, capture_output=True,
            creationflags=_sp.CREATE_NO_WINDOW, startupinfo=si,
        )
        out = r.stdout.decode("utf-8", errors="replace").strip()
    except Exception:
        out = ""
    if out:
        result["local_version"] = _extract_full_version(out)

    # 2. 官方 latest：npm 锚点（唯一可信源，官方自述）
    latest, _date = _fetchNpmLatestVersion()
    if latest:
        result["latest_version"] = latest

    # 3. 比对：本机落后官方 → behind
    if result["local_version"] and result["latest_version"]:
        result["behind"] = _version_older(result["local_version"], result["latest_version"])
        result["ok"] = True
    elif result["latest_version"]:
        # 本机读不到但能拿到官方 latest：无法判 behind，但至少知道官方版本
        result["ok"] = True

    logger.info(
        "scout.checkLocalVersion: local=%s latest=%s behind=%s distro=%s",
        result["local_version"] or "(none)", result["latest_version"] or "(none)",
        result["behind"], d,
    )
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = probe()
    print(json.dumps(result, ensure_ascii=False, indent=2))
