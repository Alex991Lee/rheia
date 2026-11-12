# -*- coding: utf-8 -*-
# L1-2604：Scout - Data-driven probe/filter/publish module.
r"""Scout - Data-driven probe/filter/publish module."""

























from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.testPole import logEvent


# L50-65：_knownTypes — 已知类型词表：直接读分类器自己的规则表，不再维护一份会漂移的硬编码清单。
def _knownTypes() -> set:
    r"""已知类型词表：直接读分类器自己的规则表，不再维护一份会漂移的硬编码清单。"""




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





_BASE = Path(__file__).resolve().parent
_PROJECT_ROOT = _BASE.parent




_PROGRAMDATA = os.environ.get("PROGRAMDATA", "")
if _PROGRAMDATA:
    _DB_DIR = Path(_PROGRAMDATA) / "Rheia"
else:

    _DB_DIR = _PROJECT_ROOT
_DB_PATH = _DB_DIR / "baseline_pool.db"



_DOC_CACHE_PATH = _DB_DIR / "doc_cache.json"

CLASS_CONSUMER = "consumer"
CLASS_GATEWAY = "gateway"
CLASS_NOISE = "noise"

_OFFICIAL_DOC_DIR = _BASE / "Official text"
_OFFICIAL_DOC_FILE = _OFFICIAL_DOC_DIR / "llms-full.txt"

_MANIFEST_PATH = _BASE / "large" / "mani.json"
_MANIFEST_SIGNAL = _BASE / "large" / "manifest.signal"
_OT_PATH = _BASE / "large" / "ot.json"





_MANIFEST_LOCK = threading.RLock()


# L114-122：_manifestLocked — _manifestLocked
def _manifestLocked(fn):
    def _wrapped(*args, **kwargs):
        with _MANIFEST_LOCK:
            return fn(*args, **kwargs)
    try:
        import functools
        return functools.wraps(fn)(_wrapped)
    except Exception:
        return _wrapped




_ALIGNMENT_PATH = _DB_DIR / "alignment.json"



FILTER_VERSION = "v3-spider-004"
_HEADER_SOURCE_RE = re.compile(r"^#\s+(.+?)\nSource:\s*(https?://\S+)", re.MULTILINE)







@dataclass
# L142-161：DocEntry — DocEntry
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

    guessed_type: str = ""
    guess_confidence: float = 0.0
    external_definition: str = ""
    resolved_type: str = ""
    type_source: str = ""

    auth_kind: str = "api_key"
    transport: str = "openai-compatible"
    model_source: str = "generic"


@dataclass
# L166-173：_Segment — filter() 内部段落解析结果
class _Segment:
    r"""filter() 内部段落解析结果"""
    name: str = ""
    source_url: str = ""
    body: str = ""
    start: int = 0
    end: int = 0
    index: int = 0







_RE_ENV_VAR = re.compile(r"^[A-Z][A-Z0-9_]+(?:_API_KEY|_SECRET|_TOKEN|_KEY|_ID)$", re.MULTILINE)
_RE_CLI_CMD = re.compile(r"^(?:\$\s*)?openclaw\s+\S+", re.MULTILINE)
_RE_MODEL_REF = re.compile(r"\b[a-zA-Z0-9_]+/[a-zA-Z0-9._-]+\b")
_RE_PLUGIN_REF = re.compile(r"@openclaw/[a-z-]+")
_RE_URL = re.compile(r"https?://[^\s<>\"']+")
_RE_CONFIG_KEY = re.compile(r"\b[a-z]+\.[a-z]+\.[a-zA-Z]+\b")
_RE_TABLE_ROW = re.compile(r"^\|.+\|$", re.MULTILINE)
_RE_CODE_BLOCK = re.compile(r"```[\s\S]*?```")


# L192-241：_carbonFilter — 碳黑（虚光）过滤器。
def _carbonFilter(raw_text: str) -> dict[str, list[str]]:
    r"""碳黑（虚光）过滤器。"""





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


    signals["code_blocks"] = [m.group() for m in _RE_CODE_BLOCK.finditer(raw_text)]


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


# L245-306：_reverseClassify — 从确定性信号反推三轴标签（auth_kind, transport, model_source）。
def _reverseClassify(signals: dict[str, list[str]]) -> dict[str, str]:
    r"""从确定性信号反推三轴标签（auth_kind, transport, model_source）。"""



    tags: dict[str, str] = {
        "auth_kind": "api_key",
        "transport": "openai-compatible",
        "model_source": "generic",
    }


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

        if "models list" in lower_cmd or "model list" in lower_cmd:
            tags["model_source"] = "live_discovery"


    for p in signals.get("plugins", []):
        lower_p = p.lower()
        if "provider" in lower_p:
            tags["model_source"] = "live_discovery"


    table_text = "\n".join(signals.get("table_rows", []))
    if signals.get("table_rows") and ("| Model ref" in table_text or "model" in table_text.lower()):
        tags["model_source"] = "static_catalog"

    logger.info("reverseClassify: auth_kind=%s transport=%s model_source=%s",
                tags["auth_kind"], tags["transport"], tags["model_source"])
    return tags

# L309-335：_repair_if_corrupt — baseline_pool.db 损坏时，备份后重建空库。
def _repair_if_corrupt() -> None:
    r"""baseline_pool.db 损坏时，备份后重建空库。"""





    try:
        if not _DB_PATH.exists():
            return
        if _DB_PATH.stat().st_size == 0:
            return
        with open(_DB_PATH, "rb") as f:
            header = f.read(16)
        if header == b"SQLite format 3\x00":
            return
        backup = _DB_PATH.with_suffix(".db.corrupt")
        try:
            _DB_PATH.replace(backup)

            logger.error("scout._repair_if_corrupt: %s 不是有效 SQLite 库，"
                         "已改名保留为 %s 并重建空库（原有凭证/偏好不再可用）",
                         _DB_PATH, backup)
        except OSError as exc:
            logger.error("scout._repair_if_corrupt: 损坏库改名失败（%s），按新库继续", exc)
    except OSError as exc:
        logger.warning("scout._repair_if_corrupt: 损坏检查失败（%s）", exc)


# L339-345：_connect — _connect
def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _repair_if_corrupt()
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# L349-364：_getDb — 开库并备好通用表（版本化的条目表由 _detectVersion/store 按版本建）。
def _getDb() -> sqlite3.Connection:
    r"""开库并备好通用表（版本化的条目表由 _detectVersion/store 按版本建）。"""




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






# L372-418：ensureRecoveryTables — 建立 baseline_pool.db 中的灾难恢复三表。
def ensureRecoveryTables() -> None:
    r"""建立 baseline_pool.db 中的灾难恢复三表。"""





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


# L422-432：setPref — 写一个偏好项。
def setPref(key: str, value: str) -> None:
    r"""写一个偏好项。"""
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO user_prefs (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


# L436-444：getPref — 读一个偏好项。
def getPref(key: str) -> str:
    r"""读一个偏好项。"""
    conn = _connect()
    try:
        cur = conn.execute("SELECT value FROM user_prefs WHERE key = ?", (key,))
        row = cur.fetchone()
        return row[0] if row else ""
    finally:
        conn.close()


# L448-455：getAllPrefs — 读全部偏好项。
def getAllPrefs() -> dict[str, str]:
    r"""读全部偏好项。"""
    conn = _connect()
    try:
        cur = conn.execute("SELECT key, value FROM user_prefs ORDER BY key")
        return {r[0]: r[1] for r in cur.fetchall()}
    finally:
        conn.close()


# L459-471：setCredential — 写凭证（明文写进 encrypted_value 列 —— 加密尚未实现，别当它是密文）。
def setCredential(key: str, plaintext: str, provider: str = "") -> None:
    r"""写凭证（明文写进 encrypted_value 列 —— 加密尚未实现，别当它是密文）。"""

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


# L475-484：getCredential — 读凭证（原样返回 encrypted_value 列的值，没有解密步骤）。DB 不存在或表未建时返回空。
def getCredential(key: str) -> str:
    r"""读凭证（原样返回 encrypted_value 列的值，没有解密步骤）。DB 不存在或表未建时返回空。"""
    ensureRecoveryTables()
    conn = _connect()
    try:
        cur = conn.execute("SELECT encrypted_value FROM credentials WHERE key = ?", (key,))
        row = cur.fetchone()
        return row[0] if row else ""
    finally:
        conn.close()


# L488-496：getAllCredentials — 读全部凭证 → {key: (encrypted_value, provider)}。DB 不存在或表未建时返回空。
def getAllCredentials() -> dict[str, tuple[str, str]]:
    r"""读全部凭证 → {key: (encrypted_value, provider)}。DB 不存在或表未建时返回空。"""
    ensureRecoveryTables()
    conn = _connect()
    try:
        cur = conn.execute("SELECT key, encrypted_value, provider FROM credentials ORDER BY key")
        return {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    finally:
        conn.close()


# L500-508：deleteCredential — 删除一条凭证。DB 不存在则无操作。
def deleteCredential(key: str) -> None:
    r"""删除一条凭证。DB 不存在则无操作。"""
    ensureRecoveryTables()
    conn = _connect()
    try:
        conn.execute("DELETE FROM credentials WHERE key = ?", (key,))
        conn.commit()
    finally:
        conn.close()


# L512-529：setAdvancedProvider — 写一条高级配置 provider（base_url / api / api_key / model_name）。
def setAdvancedProvider(provider_id: str, base_url: str, api: str,
                        api_key: str, model_name: str) -> None:
    r"""写一条高级配置 provider（base_url / api / api_key / model_name）。"""



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


# L533-547：getAdvancedProvider — 读一条高级配置 provider，返回 dict；不存在返回空 dict。
def getAdvancedProvider(provider_id: str) -> dict[str, str]:
    r"""读一条高级配置 provider，返回 dict；不存在返回空 dict。"""
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


# L551-565：getAllAdvancedProviders — 读全部高级配置 provider，返回 {provider_id: {base_url,api,api_key,model_name}}。
def getAllAdvancedProviders() -> dict[str, dict[str, str]]:
    r"""读全部高级配置 provider，返回 {provider_id: {base_url,api,api_key,model_name}}。"""
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


# L569-581：setWorkspaceFile — 写一条 workspace 文件（相对路径 → 内容）。
def setWorkspaceFile(rel_path: str, content: str) -> None:
    r"""写一条 workspace 文件（相对路径 → 内容）。"""
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


# L585-593：getAllWorkspaceFiles — 读全部 workspace 文件，返回 {rel_path: content}。
def getAllWorkspaceFiles() -> dict[str, str]:
    r"""读全部 workspace 文件，返回 {rel_path: content}。"""
    ensureRecoveryTables()
    conn = _connect()
    try:
        cur = conn.execute("SELECT rel_path, content FROM workspace_files")
        return {r[0]: r[1] for r in cur.fetchall()}
    finally:
        conn.close()


# L597-607：setRecoveryState — 设一个恢复状态。
def setRecoveryState(key: str, status: str) -> None:
    r"""设一个恢复状态。"""
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO recovery_state (key, status, updated_at) VALUES (?, ?, datetime('now'))",
            (key, status),
        )
        conn.commit()
    finally:
        conn.close()


# L611-619：getRecoveryState — 读一个恢复状态。
def getRecoveryState(key: str) -> str:
    r"""读一个恢复状态。"""
    conn = _connect()
    try:
        cur = conn.execute("SELECT status FROM recovery_state WHERE key = ?", (key,))
        row = cur.fetchone()
        return row[0] if row else ""
    finally:
        conn.close()


# L623-625：dbExists — baseline_pool.db 是否已存在。重装/恢复判断用。
def dbExists() -> bool:
    r"""baseline_pool.db 是否已存在。重装/恢复判断用。"""
    return _DB_PATH.exists()


# L629-631：dbPath — 返回 baseline_pool.db 的完整路径。
def dbPath() -> str:
    r"""返回 baseline_pool.db 的完整路径。"""
    return str(_DB_PATH)













_NPM_ANCHOR_OFFICIAL = "https://registry.npmjs.org/openclaw"
_NPM_ANCHOR_DOMESTIC = "https://registry.npmmirror.com/openclaw"




_DOC_FILENAME_CANDIDATES = ["llms-full.txt"]



_DOC_HOST_OFFICIAL = "https://docs.openclaw.ai"


# L659-702：_http_get_etag — 带条件请求拉取，返回 (etag, body, last_modified)。
def _http_get_etag(url: str, etag: str = "", last_modified: str = "") -> tuple[str, str, str]:
    r"""带条件请求拉取，返回 (etag, body, last_modified)。"""





    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",

        "Accept-Encoding": "identity",
    }
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    try:
        req = urllib.request.Request(url, headers=headers)



        with urllib.request.urlopen(req) as resp:
            new_etag = resp.headers.get("ETag", "")
            new_lm = resp.headers.get("Last-Modified", "")
            raw = resp.read()
            _clen = resp.headers.get("Content-Length")
            if _clen:
                try:
                    _want = int(_clen)
                except ValueError:
                    _want = -1
                if _want >= 0 and len(raw) != _want:
                    raise IOError("Content-Length 不符：收到 %d / 声明 %d" % (len(raw), _want))
            body = raw.decode("utf-8", errors="replace")
            return new_etag, body, new_lm
    except urllib.error.HTTPError as exc:
        if exc.code == 304:

            return etag, "", last_modified
        logger.warning("scout._http_get_etag: HTTP %d", exc.code)
        return "", "", ""
    except Exception as exc:
        logger.warning("scout._http_get_etag: 失败（%s）", exc)
        return "", "", ""


# L706-749：_http_get — 统一 HTTP GET（urllib 优先，curl 兜底）。
def _http_get(url: str) -> str:
    r"""统一 HTTP GET（urllib 优先，curl 兜底）。"""









    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            _clen = resp.headers.get("Content-Length")
            if _clen:
                try:
                    _want = int(_clen)
                except ValueError:
                    _want = -1
                if _want >= 0 and len(raw) != _want:
                    raise IOError("Content-Length 不符：收到 %d / 声明 %d" % (len(raw), _want))
            return raw.decode("utf-8", errors="replace")
    except Exception as exc:
        logger.warning("scout._http_get: urllib 失败（%s），转 curl 兜底", exc)


    try:
        r = subprocess.run(
            ["curl", "-sS", "-L",
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


# L753-769：_isChineseSystem — 判断系统是否为「简体中文」——只有简体才走国内镜像，繁体/其它走官方源。
def _isChineseSystem() -> bool:
    r"""判断系统是否为「简体中文」——只有简体才走国内镜像，繁体/其它走官方源。"""







    try:
        import ctypes
        lang = ctypes.windll.kernel32.GetUserDefaultUILanguage()
    except Exception:
        return False
    primary = lang & 0x3FF
    sub = (lang >> 10) & 0x3F
    return primary == 0x04 and sub == 0x02


# L773-775：_npmAnchor — 返回 npm 锚点：简体中文系统→国内镜像，非简体→官方源。
def _npmAnchor() -> str:
    r"""返回 npm 锚点：简体中文系统→国内镜像，非简体→官方源。"""
    return _NPM_ANCHOR_DOMESTIC if _isChineseSystem() else _NPM_ANCHOR_OFFICIAL

_NPM_META_CACHE: dict[str, dict] = {}
_NPM_META_LOCK = threading.Lock()


# L779-791：_fetch_npm_metadata — 从 npm 锚点读 openclaw 的 metadata，返回 dict；失败返回空 dict。
def _fetch_npm_metadata() -> dict:
    r"""从 npm 锚点读 openclaw 的 metadata，返回 dict；失败返回空 dict。"""




    url = _npmAnchor()
    with _NPM_META_LOCK:
        cached = _NPM_META_CACHE.get(url)
    if cached is not None:
        return cached
    try:
        raw = _http_get(url)
        data = json.loads(raw)
        if isinstance(data, dict):
            with _NPM_META_LOCK:
                _NPM_META_CACHE[url] = data
            return data
        return {}
    except Exception as exc:
        logger.warning("scout._fetch_npm_metadata: npm 锚点不可达（%s）", exc)
        return {}


# L795-816：_derive_doc_sources — 从官方 docs 域名（信任锚点）派生文档真源池。
def _derive_doc_sources(npm: dict) -> list[dict]:
    r"""从官方 docs 域名（信任锚点）派生文档真源池。"""












    host = _DOC_HOST_OFFICIAL
    home = str((npm or {}).get("homepage") or "").strip().rstrip("/")
    if home.startswith("https://") and "docs." in home:
        host = home
    sources: list[dict] = []
    for fn in _DOC_FILENAME_CANDIDATES:
        sources.append({"name": f"docs-{fn}", "url": f"{host}/{fn}"})
    return sources


# L820-850：_fetch_collaborators — 合作者抓取：从 OpenClaw 文档识别本地模型服务的合作者，去广域网抓官方文档，
def _fetch_collaborators(raw: str) -> None:
    r"""合作者抓取：从 OpenClaw 文档识别本地模型服务的合作者，去广域网抓官方文档，"""









    try:
        from kernel.filter import _extract_local_backends
        from kernel.filter import fetch_and_translate, known_collaborators


        candidates = _extract_local_backends(raw)

        candidates.update(known_collaborators().keys())

        names = sorted(candidates)
        if not names:
            return

        def _one(name: str) -> None:
            try:
                spec = fetch_and_translate(name, http_get=_http_get)
                if spec:
                    logger.info("scout._fetch_collaborators: %s → discover=%s",
                                name, spec.get("discover"))
            except Exception as e:
                logger.warning("scout._fetch_collaborators: %s 抓取失败（%s）", name, e)

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(8, len(names)),
                                thread_name_prefix="collab") as pool:
            list(pool.map(_one, names))
    except Exception as e:
        logger.warning("scout._fetch_collaborators: 跳过（%s）", e)


# L854-903：fetch — Fetch official doc corpus，真源自 npm 锚点派生，探活分级，缓存兜底。
def fetch(source_url: str = "") -> str:
    r"""Fetch official doc corpus，真源自 npm 锚点派生，探活分级，缓存兜底。"""









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


# L907-914：_structuralSignature — Extract structural features from a document chunk.
def _structuralSignature(body: str) -> dict[str, Any]:
    r"""Extract structural features from a document chunk."""
    return {
        "has_cardgroup": bool(re.search(r"\bCardGroup\b", body)),
        "has_quick_setup": bool(re.search(r"##\s*Quick\s+(start|setup|guide)", body, re.IGNORECASE)),
        "has_steps": len(re.findall(r"^\d+\.\s+", body, re.MULTILINE)) >= 3,
        "has_mermaid": bool(re.search(r"```mermaid", body)),
    }


# L918-924：_isCliOnly — Detect if a chunk is purely CLI reference (noise).
def _isCliOnly(body: str) -> bool:
    r"""Detect if a chunk is purely CLI reference (noise)."""
    bl = [l for l in body.split("\n") if l.strip()]
    if not bl:
        return False
    cli = sum(1 for l in bl if l.startswith("openclaw ") or l.startswith("$ openclaw "))
    return cli / len(bl) > 0.8


# L928-946：_parseSegments — Parse raw text into ordered segment list using _HEADER_SOURCE_RE.
def _parseSegments(raw_text: str) -> list[_Segment]:
    r"""Parse raw text into ordered segment list using _HEADER_SOURCE_RE."""
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



# L951-1074：filter — Classify document entries using multi-step semantic analysis.
def filter(raw_text: str) -> tuple[list[DocEntry], list[DocEntry], list[DocEntry]]:
    r"""Classify document entries using multi-step semantic analysis."""









    from kernel.filter import classify_url_type

    con: list[DocEntry] = []
    gw: list[DocEntry] = []
    noise: list[DocEntry] = []

    segments = _parseSegments(raw_text)
    logger.info("scout.filter: parsed %d segments", len(segments))


    carbon_signals = _carbonFilter(raw_text)

    cluster_tags = _reverseClassify(carbon_signals)
    logger.info("scout.filter: carbonFilter + reverseClassify done: %s", cluster_tags)


    _crawl_fn = globals().get("_crawlIdentity")



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



        resolved_type, confidence, type_src = classify_url_type(seg.source_url, seg.name, seg.body)
        guessed_type = resolved_type
        type_src = f"type_classifier_{type_src}"


        if guessed_type not in _KNOWN_TYPES:
            _UNKNOWN_TYPES[guessed_type] = _UNKNOWN_TYPES.get(guessed_type, 0) + 1
            logger.debug("[STAKE-TYP] unknown type '%s' for segment '%s'", guessed_type, seg.name)


        external_def = ""
        if _crawl_fn:
            try:
                external_def = _crawl_fn(seg.name, seg.source_url) or ""
            except Exception:
                logger.debug("_crawlIdentity failed for %s", seg.source_url)
                external_def = ""


        resolved_type = guessed_type
        type_source = "context_fallback"


        if resolved_type == "gateway":
            classification = CLASS_GATEWAY
        elif resolved_type in ("", "cli", "noise", "infrastructure"):
            classification = CLASS_NOISE
        else:
            classification = CLASS_CONSUMER


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



# L1079-1118：_detectVersion — Detect version ID from cached official doc corpus.
def _detectVersion(conn: sqlite3.Connection, rawText: str, npmDate: str = "") -> tuple[str, str]:
    r"""Detect version ID from cached official doc corpus."""


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


# L1122-1178：store — Store entries into versioned SQLite table.
def store(entries: list[DocEntry], tname: str = "") -> tuple[int, int]:
    r"""Store entries into versioned SQLite table."""
    if not tname:
        tname = f"doc_entries_{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    vid = tname.replace("doc_entries_", "")

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


    return stored, 0



# L1183-1226：_fetchNpmLatestVersion — Query npm registry for latest OpenClaw version + publish date.
def _fetchNpmLatestVersion() -> tuple[str, str]:
    r"""Query npm registry for latest OpenClaw version + publish date."""



    try:
        npm_data = _fetch_npm_metadata()
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


# L...：warmCollaboratorsFromCache — 独立后台进程只从本地文档缓存补充合作者字典。
def warmCollaboratorsFromCache() -> None:
    r"""独立后台进程只从本地文档缓存补充合作者字典。"""
    try:
        raw = _OFFICIAL_DOC_FILE.read_text(encoding="utf-8")
        if raw.strip():
            _fetch_collaborators(raw)
    except (OSError, Exception):
        pass

# L1230-1422：probe — Probe: fetch, filter, dual-output pipeline.
def probe(source_url: str = "", fetchCollaborators: bool = True) -> dict[str, Any]:
    r"""Probe: fetch, filter, dual-output pipeline."""










    logEvent("probe.start")
    start = time.time()







    if source_url:
        sources = [{"name": "explicit", "url": source_url}]
    else:
        npm = _fetch_npm_metadata()
        sources = _derive_doc_sources(npm)
        if not sources:
            logger.warning("scout.probe: npm 锚点未派生到任何真源，将尝试本地缓存")

    prev_meta = _readManifestCacheMeta()
    prev_etag = prev_meta["etag"]
    prev_lm = prev_meta["last_modified"]
    new_etag = ""
    new_lm = ""
    cache_lm = ""
    raw = ""


    last_err = ""
    for src in sources:
        try:
            new_etag, raw, new_lm = _http_get_etag(src["url"], prev_etag, prev_lm)
            if raw:
                if src is not sources[0]:
                    logger.info("scout.probe: 已降级到备源 %s", src["name"])


                cache_lm = new_lm or prev_lm
                _writeManifestEtag(new_etag, cache_lm)


                if prev_etag and new_etag and new_etag == prev_etag:
                    local = checkLocalVersion()
                    logger.info("scout.probe: 200 但 ETag 未变（%s），跳过 crawl", new_etag)
                    return {
                        "changed": False, "storage": (0, 0),
                        "manifest": {"consumer": 0, "gateway": 0},
                        "version": {"local": local["local_version"], "latest": local["latest_version"], "behind": local.get("behind", False)},
                    }
                raw = re.sub(r"<[^>]+>", " ", raw).strip()
                break

            if ((prev_etag and new_etag and new_etag == prev_etag)
                    or (prev_lm and new_lm and new_lm == prev_lm)):

                local = checkLocalVersion()
                logger.info("scout.probe: 文档验证器未变（etag=%s lm=%s），跳过拉取+crawl",
                            prev_etag or "-", prev_lm or "-")
                return {
                    "changed": False, "storage": (0, 0),
                    "manifest": {"consumer": 0, "gateway": 0},
                    "version": {"local": local["local_version"], "latest": local["latest_version"], "behind": local.get("behind", False)},
                }
            last_err = f"{src['name']}: 304/empty"
        except Exception as exc:
            last_err = f"{src['name']}: {exc}"
            logger.warning("scout.probe: 源 %s 失败（%s）", src["name"], exc)


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


    logger.info("[STAKE-CNT] fetch returned %d bytes", len(raw))


    rawHash = hashlib.sha256(f"{raw}:{FILTER_VERSION}".encode("utf-8")).hexdigest()

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


    _OFFICIAL_DOC_DIR.mkdir(parents=True, exist_ok=True)
    _OFFICIAL_DOC_FILE.write_text(raw, encoding="utf-8")
    logger.info("scout.probe: new version cached %d bytes", len(raw))




    npm_ver, npm_date = _fetchNpmLatestVersion()
    version_info = (npm_ver, npm_date) if npm_ver else None
    if npm_ver and not latest:
        latest = npm_ver



    if fetchCollaborators:
        _fetch_collaborators(raw)



    deployed = _readDeployedPlugins()

    from kernel.filter import crawl_serialized



    demands = _readCustomDemands()


    prev_manifest: dict = {}
    try:
        if _MANIFEST_PATH.exists():
            prev_manifest = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("scout.probe: 读上一版 mani 失败，机制变化差分将按空算: %s", exc)
    ot_content = crawl_serialized(raw, version_info=version_info, deployed=deployed,
                                  demands=demands, prev_manifest=prev_manifest)


    ot_content = _injectLocalVersion(ot_content, local, latest, etag=new_etag,
                                     last_modified=cache_lm)
    _OT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OT_PATH.write_text(ot_content, encoding="utf-8")
    logger.info("scout.probe: spider wrote ot.json (%d bytes)", len(ot_content))
    settleManifest()




    try:
        con, gw, noise = filter(raw)
    except Exception as exc:

        logger.warning("scout.probe: Copy-B filter() 失败，本次不落 SQLite 兜底: %s", exc)
        con, gw, noise = [], [], []
    all_entries = con + gw + noise
    conn = _connect()
    vid, tname = _detectVersion(conn, raw, npmDate=npm_date)
    conn.close()
    stored, queued = store(all_entries, tname=tname)

    elapsed = time.time() - start
    if _UNKNOWN_TYPES:

        logger.warning("[STAKE-TYP] 未登记类型汇总: %s（如确属新类型，请并入 typeSpace）",
                       ", ".join("%s=%d" % kv for kv in sorted(_UNKNOWN_TYPES.items())))
        _UNKNOWN_TYPES.clear()
    logger.info("scout.probe: spider crawl done, stored=%d, queued=%d, elapsed=%.2fs", stored, queued, elapsed)
    return {
        "changed": True, "storage": (stored, queued),

        "manifest": {"consumer": len(con), "gateway": len(gw)},
        "version": {"local": local["local_version"], "latest": latest, "behind": local.get("behind", False)},
    }


# L1426-1456：_readManifestCacheMeta — 读文档验证器（etag / last_modified）：ProgramData 缓存优先，mani 兜底。
def _readManifestCacheMeta() -> dict:
    r"""读文档验证器（etag / last_modified）：ProgramData 缓存优先，mani 兜底。"""





    out = {"etag": "", "last_modified": ""}
    try:
        if _DOC_CACHE_PATH.exists():
            with open(_DOC_CACHE_PATH, "r", encoding="utf-8") as f:
                c = json.load(f)
            if isinstance(c, dict):
                out["etag"] = str(c.get("etag") or "").strip()
                out["last_modified"] = str(c.get("last_modified") or "").strip()
        if out["etag"] or out["last_modified"]:
            return out
    except (OSError, json.JSONDecodeError):
        pass
    try:
        if not _MANIFEST_PATH.exists():
            return out
        with open(_MANIFEST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        ver = data.get("version")
        if isinstance(ver, dict):
            out["etag"] = str(ver.get("etag") or "").strip()
            out["last_modified"] = str(ver.get("last_modified") or "").strip()
    except (OSError, json.JSONDecodeError):
        pass
    return out


# L1460-1462：_readManifestEtag — 兼容旧调用：只取 ETag。
def _readManifestEtag() -> str:
    r"""兼容旧调用：只取 ETag。"""
    return _readManifestCacheMeta()["etag"]


# L1466-1523：_writeManifestEtag — 把本次成功拉取拿到的文档验证器写进 mani.json version 段。
def _writeManifestEtag(etag: str, last_modified: str = "") -> None:
    r"""把本次成功拉取拿到的文档验证器写进 mani.json version 段。"""




    if not etag and not last_modified:
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
        if etag:
            ver["etag"] = etag
        if last_modified:
            ver["last_modified"] = last_modified
        data["version"] = ver
        _MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        new_text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)


        try:
            if _MANIFEST_PATH.read_text(encoding="utf-8") == new_text:
                logger.info("scout._writeManifestEtag: 验证器未变（%s），跳过写入", etag or last_modified)
                return
        except OSError:
            pass
        with open(_MANIFEST_PATH, "w", encoding="utf-8") as f:
            f.write(new_text)
        logger.info("scout._writeManifestEtag: 验证器已写 mani.json (%s)", etag or last_modified)

        try:
            cache_text = json.dumps({"etag": etag, "last_modified": last_modified},
                                    ensure_ascii=False, indent=2, sort_keys=True)
            old_cache = ""
            try:
                if _DOC_CACHE_PATH.exists():
                    old_cache = _DOC_CACHE_PATH.read_text(encoding="utf-8")
            except OSError:
                old_cache = ""
            if old_cache != cache_text:
                _DOC_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
                _tmp = str(_DOC_CACHE_PATH) + ".tmp"
                with open(_tmp, "w", encoding="utf-8") as f:
                    f.write(cache_text)
                os.replace(_tmp, _DOC_CACHE_PATH)
                logger.info("scout._writeManifestEtag: 验证器已写 doc_cache.json")
        except OSError as exc:
            logger.warning("scout._writeManifestEtag: 写 doc_cache 失败（%s）", exc)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("scout._writeManifestEtag: 写验证器失败（%s）", exc)


# L1527-1557：_injectLocalVersion — 把本机版本核验结果写进 ot.json 的 version 段（不破坏 spider 已写的 npm 元数据）。
def _injectLocalVersion(ot_content: str, local: dict, latest: str, etag: str = "",
                        last_modified: str = "") -> str:
    r"""把本机版本核验结果写进 ot.json 的 version 段（不破坏 spider 已写的 npm 元数据）。"""








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
    if last_modified:
        ver["last_modified"] = last_modified
    data["version"] = ver
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)




_TLC_DECL = {
    "id": "tlc",
    "type": "tlc_guard",
    "display_name": "tlc 守护",
    "scope": "shell",
    "watch": ["exit_code", "crash_precursor"],
    "normal_exit": ["gateway_systemd_restart", "self_heal_restart"],
    "policy": "keep_alive",
    "lifecycle": "persistent",
    "exempt": ["glory_keepalive", "tlc"],
    "defer_restart_on_conversation": True,
}











_RELOAD_DECLS = [
    {"id": "shell-lane-sidebar", "type": "reload_lane", "scope": "shell", "coalesce": True,
     "display_name": "侧栏刷新",
     "sources": ["sidebar", "skills"]},
    {"id": "shell-lane-chat", "type": "reload_lane", "scope": "shell", "coalesce": True,
     "display_name": "聊天区刷新",
     "sources": ["chat", "session"]},
]


@_manifestLocked
# L1597-1634：injectReloadDeclarations — 把重载车道声明 upsert 进 mani.json 的 entries.reload（幂等）。
def injectReloadDeclarations() -> bool:
    r"""把重载车道声明 upsert 进 mani.json 的 entries.reload（幂等）。"""
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


@_manifestLocked
# L1639-1675：injectTlcDeclaration — 把 tlc 声明 upsert 进 mani.json 的 entries.tlc。返回是否写入。
def injectTlcDeclaration() -> bool:
    r"""把 tlc 声明 upsert 进 mani.json 的 entries.tlc。返回是否写入。"""
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

        for i, item in enumerate(tlc_list):
            if isinstance(item, dict) and item.get("id") == "tlc":
                tlc_list[i] = _TLC_DECL
                break
        else:
            tlc_list.append(_TLC_DECL)
        new_text = json.dumps(mani, ensure_ascii=False, indent=2)

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


@_manifestLocked
# L1680-1765：injectCustomDemands — 把客制化的需求条目 upsert 进 mani.json 的 entries.custom_demands。
def injectCustomDemands() -> bool:
    r"""把客制化的需求条目 upsert 进 mani.json 的 entries.custom_demands。"""














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



            did = str(rec.get("id") or "")
            fp = declaration_fp(rec)
            rec["fp"] = fp
            old = prev.get(did) if did else None
            if old and old.get("fp") == fp:


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
            return True






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


@_manifestLocked
# L1770-1838：settleManifest — Compare ot.json with mani.json, settle the difference.
def settleManifest() -> bool:
    r"""Compare ot.json with mani.json, settle the difference."""




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
    manifestData = json.loads(otContent)
    conCount = _count_manifest_entries(manifestData)
    gwCount = _count_gateway_entries(manifestData)
    _new_count = conCount + gwCount


    if _new_count == 0:
        logger.error("scout.settleManifest: 新清单 0 条，拒绝覆盖 mani.json（保留上一版）")
        try:
            _OT_PATH.unlink()
        except OSError:
            pass
        return False

    _old_count = 0
    if _MANIFEST_PATH.exists():
        try:
            _old_data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
            _old_count = _count_manifest_entries(_old_data)
        except Exception:
            pass
        _MANIFEST_PATH.unlink()
    _OT_PATH.rename(_MANIFEST_PATH)
    now = datetime.now(timezone.utc).isoformat()
    signal = {"updated_at": now, "consumer": conCount, "gateway": gwCount}
    if "version" in manifestData:
        vInfo = manifestData["version"]
        if isinstance(vInfo, dict) and vInfo.get("npm_latest"):
            signal["version"] = vInfo["npm_latest"]
    _delta = _new_count - _old_count
    logger.info("[STAKE-CNT] manifest update: old=%d new=%d delta=%d", _old_count, _new_count, _delta)
    if abs(_delta) > 50 and _old_count > 0:
        logger.warning("[STAKE-CNT] large manifest delta: %d entries changed", _delta)
    logEvent("settle.signal", signal)
    _MANIFEST_SIGNAL.write_text(json.dumps(signal, ensure_ascii=False), encoding="utf-8")
    logger.info("scout.settleManifest: promoted ot.json -> mani.json, signal raised")

    try:
        injectTlcDeclaration()
    except Exception:
        pass

    try:
        injectReloadDeclarations()
    except Exception:
        pass

    try:
        injectCustomDemands()
    except Exception:
        pass
    return True





_NON_CONSUMER_ENTRY_KEYS = {"reload", "tlc", "custom_demands", "mechanisms"}


# L1848-1854：_count_manifest_entries — 数 grouped 结构里的消费端总数（entries 下各类 list 的长度和，排除声明车道）。
def _count_manifest_entries(data: dict) -> int:
    r"""数 grouped 结构里的消费端总数（entries 下各类 list 的长度和，排除声明车道）。"""
    entries = data.get("entries", {})
    if isinstance(entries, dict):
        return sum(len(v) for k, v in entries.items()
                   if k not in _NON_CONSUMER_ENTRY_KEYS and isinstance(v, list))
    return 0


# L1858-1863：_count_gateway_entries — 数 grouped 结构里的 gateway 消费端数。
def _count_gateway_entries(data: dict) -> int:
    r"""数 grouped 结构里的 gateway 消费端数。"""
    entries = data.get("entries", {})
    if isinstance(entries, dict):
        return len(entries.get("gateways", []) or [])
    return 0


# L1867-1885：health_check — Report current probe status: manifest, db, consumer/gateway counts.
def health_check() -> dict:
    r"""Report current probe status: manifest, db, consumer/gateway counts."""
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

                _gw = _count_gateway_entries(data)
                result["gateway_count"] = _gw
                result["consumer_count"] = max(0, _count_manifest_entries(data) - _gw)
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
    return result




_WSL_DISTRO = os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu"





# L1897-1936：_runWslCheck — 跑一条 WSL 命令，返回 (returncode, stdout_stripped)。
def _runWslCheck(cmd: str) -> tuple[int, str]:
    r"""跑一条 WSL 命令，返回 (returncode, stdout_stripped)。"""





    import subprocess as _sp
    si = _sp.STARTUPINFO()
    si.dwFlags |= _sp.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    try:


        _mark = 'bash -c "'
        if _mark in cmd and "LC_ALL" not in cmd:
            cmd = cmd.replace(_mark, _mark + "export LC_ALL=C.UTF-8; ", 1)
        r = _sp.run(
            cmd, shell=True, capture_output=True,
            creationflags=_sp.CREATE_NO_WINDOW,
            startupinfo=si,
        )
        raw = r.stdout
        if not raw:

            raw = r.stderr or b""

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

        logger.warning("scout._runWslCheck: 命令失败或超时（%s）: %s", exc, cmd[:120])
        return -1, ""


@_manifestLocked
# L1941-2024：_self_heal — 自愈：全量对齐 OpenClaw，只在「尚未对齐」或「版本迭代」时触发。
def _self_heal() -> bool:
    r"""自愈：全量对齐 OpenClaw，只在「尚未对齐」或「版本迭代」时触发。"""
















    current = readLocalVersion()
    if not current:
        return False

    anchored = ""
    try:
        if _ALIGNMENT_PATH.exists():
            anchored = (json.loads(_ALIGNMENT_PATH.read_text(encoding="utf-8"))
                        .get("aligned_version", ""))
    except (OSError, json.JSONDecodeError):
        anchored = ""


    if anchored and anchored == current:
        return False


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


    try:
        clis = (mani.get("entries") or {}).get("clis") or []
        fixed.extend(_correct_cli_usages(clis, ground_truth))
    except Exception as exc:
        logger.warning("scout._self_heal: CLI 命令面纠正异常（%s）", exc)


    if fixed:
        try:
            with open(_MANIFEST_PATH, "w", encoding="utf-8") as f:
                json.dump(mani, f, ensure_ascii=False, indent=2, sort_keys=True)
            logger.info("scout._self_heal: 自愈 %d 项: %s", len(fixed), fixed[:20])
        except OSError as exc:
            logger.warning("scout._self_heal: 写 mani 失败（%s）", exc)
            return False


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


# L2028-2045：_sample_deployed_truth — 统一采样：一次采全已部署 OpenClaw 的真值源。
def _sample_deployed_truth(distro: str | None = None) -> dict:
    r"""统一采样：一次采全已部署 OpenClaw 的真值源。"""




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
        "cli": _readDeployedCommandSurface(d),
    }


# L2049-2076：_correct_plugin_kind — 纠正器：plugin.kind（用部署 origin 真值）+ 连带 outSider。
def _correct_plugin_kind(providers: list[dict], truth: dict) -> list[str]:
    r"""纠正器：plugin.kind（用部署 origin 真值）+ 连带 outSider。"""
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


# L2080-2107：_correct_auth_choice — 纠正器：auth_choice 缺失补全（用 onboard --help 真值）。
def _correct_auth_choice(providers: list[dict], truth: dict) -> list[str]:
    r"""纠正器：auth_choice 缺失补全（用 onboard --help 真值）。"""






    choices = truth.get("auth_choices") or {}
    if not choices:
        return []
    fixed: list[str] = []
    for p in providers:
        pid = p.get("id", "")
        if p.get("auth_choice"):
            continue

        if not (p.get("plugin") or {}).get("package"):
            continue

        candidates = [c for c in choices
                      if c.startswith(pid) and c.endswith("-api-key")]
        if not candidates:
            continue
        best = min(candidates, key=len)
        p["auth_choice"] = best
        fixed.append(f"auth_choice:{pid}:{best}")
    return fixed


# L2111-2157：_correct_capabilities — 纠正器：capabilities 用部署真值（各 ProviderIds 维度）覆盖。
def _correct_capabilities(providers: list[dict], truth: dict) -> list[str]:
    r"""纠正器：capabilities 用部署真值（各 ProviderIds 维度）覆盖。"""













    plugins = truth.get("plugins") or {}

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


# L2161-2219：_readCustomDemands — 读客制化区里的【需求条目】（客户/人格模块经客制化提的需求）。
def _readCustomDemands() -> list[dict]:
    r"""读客制化区里的【需求条目】（客户/人格模块经客制化提的需求）。"""









    try:
        out = []

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


        sc = getPref("slot_consumer")
        if sc:
            try:
                d = json.loads(sc)
                if isinstance(d, dict) and d.get("id"):
                    out.append(d)
            except Exception:
                pass


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


# L2223-2284：_readDeployedPlugins — 采已部署 OpenClaw 的真实插件状态（装通后才可采）。
def _readDeployedPlugins(distro: str | None = None) -> dict[str, dict]:
    r"""采已部署 OpenClaw 的真实插件状态（装通后才可采）。"""
















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


# L2288-2302：_parseCliHelp — 从 openclaw <family> --help 里读命令面（Commands 段）。
def _parseCliHelp(text: str) -> dict:
    r"""从 openclaw <family> --help 里读命令面（Commands 段）。"""
    cmds: list[str] = []
    in_cmd = False
    for line in (text or "").splitlines():
        if re.match(r"^\s*Commands:\s*$", line):
            in_cmd = True
            continue
        if in_cmd:
            if not line.strip() or re.match(r"^\s*(Options|Docs|Arguments):", line):
                break
            m = re.match(r"^\s{2}([a-zA-Z0-9_-]+)\b", line)
            if m:
                cmds.append(m.group(1))
    return {"commands": sorted(set(cmds))}


# L2306-2325：_readDeployedCommandSurface — 采已部署 OpenClaw 的命令面真值（CLI 自己说的，不是文档猜的）。
def _readDeployedCommandSurface(distro: str | None = None) -> dict[str, dict]:
    r"""采已部署 OpenClaw 的命令面真值（CLI 自己说的，不是文档猜的）。"""




    d = distro or findOpenClawDistro()
    if not d:
        return {}
    families = ("agents", "gateway", "plugins", "config", "models", "channels", "cron")
    out: dict[str, dict] = {}
    for fam in families:
        rc, text = _runWslCheck(
            f'wsl -d {d} -u root -- bash -c '
            f'"export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; '
            f'openclaw {fam} --help 2>/dev/null"'
        )
        if rc == 0 and (text or "").strip():
            out[fam] = _parseCliHelp(text)
    return out


# L2329-2347：_correct_cli_usages — 纠正器：用 CLI 自己的命令面补齐 mani 里缺的命令（只增不删）。
def _correct_cli_usages(clis: list[dict], truth: dict) -> list[str]:
    r"""纠正器：用 CLI 自己的命令面补齐 mani 里缺的命令（只增不删）。"""
    surface = truth.get("cli") or {}
    fixed: list[str] = []
    for c in clis:
        if not isinstance(c, dict):
            continue
        fam = str(c.get("id") or "")
        cmds = set((surface.get(fam) or {}).get("commands") or [])
        if not cmds:
            continue
        have = set()
        for u in (c.get("usages") or []):
            if isinstance(u, dict):
                have.add(str(u.get("action") or "").split()[0])
        for cmd in sorted(cmds - have):
            c.setdefault("usages", []).append({"action": cmd, "options": []})
            fixed.append(f"cli:{fam}:+{cmd}")
    return fixed


# L2351-2376：_readDeployedAuthChoices — 采已部署 OpenClaw 的 onboard --auth-choice 真值（装通后才可采）。
def _readDeployedAuthChoices(distro: str | None = None) -> dict[str, str]:
    r"""采已部署 OpenClaw 的 onboard --auth-choice 真值（装通后才可采）。"""





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


# L2380-2410：findOpenClawDistro — 探针：遍历 wsl --list --verbose 所有发行版，找到有 OpenClaw 的那个。
def findOpenClawDistro() -> str:
    r"""探针：遍历 wsl --list --verbose 所有发行版，找到有 OpenClaw 的那个。"""



    rc, out = _runWslCheck("wsl --list --verbose")
    if rc != 0 or not out.strip():
        return ""




    candidates = []
    for line in out.splitlines():
        line = line.replace("\x00", "").strip()
        if not line:
            continue

        name = line.lstrip("*").split()[0]

        if name and name.upper() != "NAME":
            candidates.append(name)

    for d in candidates:
        rc2, o2 = _runWslCheck(
            f'wsl -d {d} -- bash -c "command -v openclaw >/dev/null 2>&1 && echo OK"',
        )
        if rc2 == 0 and "OK" in o2:
            return d

    return ""


# L2414-2450：readGatewayAuth — 探针：读指定发行版 ~/.openclaw/openclaw.json 的 gateway.auth 段，反推 mode。
def readGatewayAuth(distro: str) -> dict:
    r"""探针：读指定发行版 ~/.openclaw/openclaw.json 的 gateway.auth 段，反推 mode。"""



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


    if result["mode"]:
        pass
    elif result["token"] and not result["password"]:
        result["mode"] = "token"
    elif result["password"] and not result["token"]:
        result["mode"] = "password"
    elif result["token"] and result["password"]:
        result["mode"] = "token"
    else:
        result["mode"] = "none"

    result["ok"] = True
    return result


# L2454-2488：checkGatewayConfig — 检查 Gateway 配置是否就绪。
def checkGatewayConfig(distro: str = "") -> tuple[bool, str]:
    r"""检查 Gateway 配置是否就绪。"""









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










_VERSION_RE = re.compile(r"(\d{4})\.(\d{1,3})\.(\d{1,3})")



_FULL_VERSION_RE = re.compile(r"(\d{4}\.\d{1,3}\.\d{1,3}(?:[.-]\d+)*)")


# L2507-2521：_parse_version — 解析 OpenClaw 版本号 → (年, 主, 次) 元组；解析失败返回 (0,0,0)。
def _parse_version(ver: str) -> tuple[int, int, int]:
    r"""解析 OpenClaw 版本号 → (年, 主, 次) 元组；解析失败返回 (0,0,0)。"""




    if not ver:
        return (0, 0, 0)
    m = _VERSION_RE.search(str(ver))
    if not m:
        return (0, 0, 0)
    try:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return (0, 0, 0)


# L2525-2528：_extract_full_version — 从 openclaw --version 输出提取完整版本号（含第四段/后缀）。
def _extract_full_version(out: str) -> str:
    r"""从 openclaw --version 输出提取完整版本号（含第四段/后缀）。"""
    m = _FULL_VERSION_RE.search(out)
    return m.group(1) if m else ""


# L2532-2534：_version_older — 本机版本是否落后于远端版本（语义化比较，非字符串比较）。
def _version_older(local: str, remote: str) -> bool:
    r"""本机版本是否落后于远端版本（语义化比较，非字符串比较）。"""
    return _parse_version(local) < _parse_version(remote)


# L2538-2560：readLocalVersion — 只读本机 WSL 内 OpenClaw 版本，不访问 npm。
def readLocalVersion(distro: str = "") -> str:
    r"""只读本机 WSL 内 OpenClaw 版本，不访问 npm。"""
    d = distro or findOpenClawDistro()
    if not d:
        return ""
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
    return _extract_full_version(out) if out else ""


# L2538-2598：checkLocalVersion — 核验本机 OpenClaw 版本 vs npm 官方 latest。
def checkLocalVersion(distro: str = "") -> dict:
    r"""核验本机 OpenClaw 版本 vs npm 官方 latest。"""








    result = {
        "local_version": "",
        "latest_version": "",
        "local_distro": "",
        "behind": False,
        "ok": False,
    }


    d = distro or findOpenClawDistro()
    result["local_distro"] = d
    if not d:
        return result
    result["local_version"] = readLocalVersion(d)


    latest, _date = _fetchNpmLatestVersion()
    if latest:
        result["latest_version"] = latest


    if result["local_version"] and result["latest_version"]:
        result["behind"] = _version_older(result["local_version"], result["latest_version"])
        result["ok"] = True
    elif result["latest_version"]:

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
