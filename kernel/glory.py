"""
glory — Gateway connection lifecycle management

职责:
  adopt()   写入连接参数 (URL + Token)，来自 TerminalView 提取
  status()  返回当前连接状态 (connected/url/token_ok)
  verify()  验证当前连接是否可用 (socket 探活)
  keepalive()  轻量心跳，防止 WSL 回收 Gateway 进程
  dwell()   住户（浏览器等消费端）确认连接存活，写入 connected=true
  leave()   住户主动断开/失败，写入 connected=false
  reset()   清空连接状态

原则:
  薄模块，只管通路
  不碰渲染不碰配置
  向 large.usm() 暴露 status() 作为判断依据
  状态持久化在 glory/glory.json，每次启动从 WSL 重取
  外部模块绝不可直接触碰 _state 字典——所有写操作走公开方法
"""

import json
import logging
import os
import socket
import time

from kernel.filter import json_owner, json_write_guard

logger = logging.getLogger(__name__)

_BASE = os.path.dirname(os.path.abspath(__file__))
_GLORY_DIR = os.path.join(_BASE, "glory")
_GATEWAY_PATH = os.path.join(_GLORY_DIR, "glory.json")

# Runtime cache — 避免每次 status() 都读盘
_state = {
    "url": "",
    "token": "",
    "port": 18789,
    "connected": False,
    "verified_at": 0.0,
    "last_keepalive": 0.0,
}


def _ensureGloryDir():
    """确保 glory 目录存在"""
    os.makedirs(_GLORY_DIR, exist_ok=True)


def _load():
    """从磁盘加载持久化状态"""
    global _state
    if os.path.exists(_GATEWAY_PATH):
        try:
            with open(_GATEWAY_PATH, "r", encoding="utf-8") as f:
                _state.update(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass


def _save():
    """持久化当前状态到磁盘"""
    _ensureGloryDir()
    with json_owner("kernel.glory"):
        json_write_guard("kernel/glory/glory.json", _state)
    with open(_GATEWAY_PATH, "w", encoding="utf-8") as f:
        json.dump(_state, f, ensure_ascii=False, indent=2)


def status():
    """返回当前 Gateway 连接状态
    large.usm() 通过此接口判断消费端是否可放行。
    """
    if not _state["url"]:
        _load()
    return dict(_state)


def adopt(url, token):
    """写入新的 Gateway 连接参数 + 持久化 gateway_config.json（installer 消费）"""
    _state["url"] = url
    _state["token"] = token
    # 从 URL 提取端口
    try:
        rest = url.replace("http://", "").replace("https://", "").split(":", 1)
        if len(rest) > 1:
            port_str = rest[1].rstrip("/")
            _state["port"] = int(port_str) if port_str else 18789
        else:
            _state["port"] = 18789
    except (IndexError, ValueError):
        _state["port"] = 18789
    _state["connected"] = False
    _state["verified_at"] = 0.0
    _save()

    # 同时写入 PROGRAMDATA\gateway_config.json（installer/runtime 消费）
    try:
        localdata = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia")
        os.makedirs(localdata, exist_ok=True)
        cfgPath = os.path.join(localdata, "gateway_config.json")
        # ⚠ 2026-10-19 修：读-改-写，别把 gpu_render 等键抹掉。
        _cfg = {}
        try:
            with open(cfgPath, "r", encoding="utf-8") as f:
                _cfg = json.load(f) or {}
            if not isinstance(_cfg, dict):
                _cfg = {}
        except (OSError, json.JSONDecodeError):
            _cfg = {}
        _cfg["url"] = url
        _cfg["token"] = token
        with open(cfgPath, "w", encoding="utf-8") as f:
            json.dump(_cfg, f, ensure_ascii=False, indent=2)
        logger.info("glory.adopt: wrote gateway_config.json url=%s", url)
    except OSError:
        pass

    logger.info("glory.adopt: url=%s token=%s…", url, token[:8] if token else "(none)")


def verify():
    """验证 Gateway 服务是否在线。

    不要求 token——auth:none 环境下 Gateway 无需鉴权但仍需确认服务在跑。
    探活策略: 先探 /api/health（标准端点），404 则降级探 /（根路径）。
    只要 HTTP 200 即认为 Gateway 在线。
    返回 bool，同时更新 _state["connected"]。
    """
    if not _state["url"]:
        logger.warning("glory.verify: missing url")
        return False

    host = _state["url"].replace("http://", "").replace("https://", "").split(":")[0]
    port = _state.get("port", 18789)

    import urllib.request
    import urllib.error

    # 两级探活: /api/health → fallback /
    endpoints = ["/api/health", "/"]
    for ep in endpoints:
        try:
            probe_url = f"http://{host}:{port}{ep}"
            req = urllib.request.Request(probe_url, method="GET")
            resp = urllib.request.urlopen(req, timeout=5)   # 探活给短超时：网络黑洞不许挂死线程
            if resp.status == 200:
                _state["connected"] = True
                _state["verified_at"] = time.time()
                _save()
                logger.info("glory.verify: healthy at %s:%d (endpoint=%s)", host, port, ep)
                return True
            logger.debug("glory.verify: %s returned %d", ep, resp.status)
        except urllib.error.HTTPError as http_err:
            # 404 on /api/health → 降级，不是失败
            if http_err.code == 404 and ep == "/api/health":
                logger.debug("glory.verify: /api/health 404, degrading to /")
                continue
            logger.debug("glory.verify: %s HTTP %d", ep, http_err.code)
        except Exception as exc:
            logger.debug("glory.verify: %s %s", ep, exc)
            # /api/health 抛异常（如连接拒绝）→ 也试降级
            if ep == "/api/health":
                continue
            break

    # 两级都失败
    _state["verified_at"] = time.time()
    _state["connected"] = False
    logger.warning("glory.verify: all endpoints failed at %s:%d", host, port)
    return False


def keepalive():
    """轻量心跳，防止 WSL 回收 Gateway 进程

    调用方建议每 30s 轮询一次。
    如果已断开且配置存在，自动尝试一次 verify() 恢复。
    不会修改 adopt 写入的 url/token。
    """
    if not _state["connected"]:
        if _state["url"] and _state["token"]:
            return verify()
        return False

    try:
        host = _state["url"].replace("http://", "").replace("https://", "").split(":")[0]
        port = _state.get("port", 18789)
        sock = socket.create_connection((host, port))   # 随到随走：不设超时（timeout 是禁忌）
        sock.close()
        _state["last_keepalive"] = time.time()
        return True
    except (ValueError, OSError, socket.timeout):
        _state["connected"] = False
        return False


def dwell(url="", token=""):
    """住户进场 — 浏览器等消费端确认连接存活。

    glory 作为场主判断是否写入，消费端不碰 _state。
    如果传入 url/token 与当前一致或为空，仅设 connected=True。
    如果传入不一致，委托 adopt() 更新后再设 connected。
    """
    if url and (url != _state["url"] or token != _state["token"]):
        # 住户带来了新连接参数
        adopt(url, token)
    if not _state["url"] or not _state["token"]:
        logger.warning("glory.dwell: url/token empty, cannot mark connected")
        return False
    _state["connected"] = True
    _state["verified_at"] = time.time()
    _save()
    logger.info("glory.dwell: connected=true at %s", _state["url"])
    return True


def leave():
    """住户离场 — 浏览器等消费端主动断开或连接失败。

    glory 作为场主记录断开，消费端不碰 _state。
    """
    _state["connected"] = False
    _save()
    logger.info("glory.leave: connected=false")


def reset():
    """清空连接状态

    large.usm() 或在客户主动切换配置时调用。
    """
    _state["url"] = ""
    _state["token"] = ""
    _state["port"] = 18789
    _state["connected"] = False
    _state["verified_at"] = 0.0
    _state["last_keepalive"] = 0.0
    if os.path.exists(_GATEWAY_PATH):
        os.remove(_GATEWAY_PATH)
    logger.info("glory.reset: connection state cleared")


if __name__ == "__main__":
    print("glory: module loaded, status:", status())
