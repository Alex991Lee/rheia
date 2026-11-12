# L1-257：glory — Gateway connection lifecycle management
r"""glory — Gateway connection lifecycle management"""



















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


_state = {
    "url": "",
    "token": "",
    "port": 18789,
    "connected": False,
    "verified_at": 0.0,
    "last_keepalive": 0.0,
}


# L48-50：_ensureGloryDir — 确保 glory 目录存在
def _ensureGloryDir():
    r"""确保 glory 目录存在"""
    os.makedirs(_GLORY_DIR, exist_ok=True)


# L54-62：_load — 从磁盘加载持久化状态
def _load():
    r"""从磁盘加载持久化状态"""
    global _state
    if os.path.exists(_GATEWAY_PATH):
        try:
            with open(_GATEWAY_PATH, "r", encoding="utf-8") as f:
                _state.update(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass


# L66-72：_save — 持久化当前状态到磁盘
def _save():
    r"""持久化当前状态到磁盘"""
    _ensureGloryDir()
    with json_owner("kernel.glory"):
        json_write_guard("kernel/glory/glory.json", _state)
    with open(_GATEWAY_PATH, "w", encoding="utf-8") as f:
        json.dump(_state, f, ensure_ascii=False, indent=2)


# L76-82：status — 返回当前 Gateway 连接状态
def status():
    r"""返回当前 Gateway 连接状态"""


    if not _state["url"]:
        _load()
    return dict(_state)


# L86-126：adopt — 写入新的 Gateway 连接参数 + 持久化 gateway_config.json（installer 消费）
def adopt(url, token):
    r"""写入新的 Gateway 连接参数 + 持久化 gateway_config.json（installer 消费）"""
    _state["url"] = url
    _state["token"] = token

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


    try:
        localdata = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia")
        os.makedirs(localdata, exist_ok=True)
        cfgPath = os.path.join(localdata, "gateway_config.json")

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


# L130-179：verify — 验证 Gateway 服务是否在线。
def verify():
    r"""验证 Gateway 服务是否在线。"""






    if not _state["url"]:
        logger.warning("glory.verify: missing url")
        return False

    host = _state["url"].replace("http://", "").replace("https://", "").split(":")[0]
    port = _state.get("port", 18789)

    import urllib.request
    import urllib.error


    endpoints = ["/api/health", "/"]
    for ep in endpoints:
        try:
            probe_url = f"http://{host}:{port}{ep}"
            req = urllib.request.Request(probe_url, method="GET")
            resp = urllib.request.urlopen(req)
            if resp.status == 200:
                _state["connected"] = True
                _state["verified_at"] = time.time()
                _save()
                logger.info("glory.verify: healthy at %s:%d (endpoint=%s)", host, port, ep)
                return True
            logger.debug("glory.verify: %s returned %d", ep, resp.status)
        except urllib.error.HTTPError as http_err:

            if http_err.code == 404 and ep == "/api/health":
                logger.debug("glory.verify: /api/health 404, degrading to /")
                continue
            logger.debug("glory.verify: %s HTTP %d", ep, http_err.code)
        except Exception as exc:
            logger.debug("glory.verify: %s %s", ep, exc)

            if ep == "/api/health":
                continue
            break


    _state["verified_at"] = time.time()
    _state["connected"] = False
    logger.warning("glory.verify: all endpoints failed at %s:%d", host, port)
    return False


# L183-204：keepalive — 轻量心跳，防止 WSL 回收 Gateway 进程
def keepalive():
    r"""轻量心跳，防止 WSL 回收 Gateway 进程"""





    if not _state["connected"]:
        if _state["url"] and _state["token"]:
            return verify()
        return False

    try:
        host = _state["url"].replace("http://", "").replace("https://", "").split(":")[0]
        port = _state.get("port", 18789)
        sock = socket.create_connection((host, port))
        sock.close()
        _state["last_keepalive"] = time.time()
        return True
    except (ValueError, OSError, socket.timeout):
        _state["connected"] = False
        return False


# L208-225：dwell — 住户进场 — 浏览器等消费端确认连接存活。
def dwell(url="", token=""):
    r"""住户进场 — 浏览器等消费端确认连接存活。"""





    if url and (url != _state["url"] or token != _state["token"]):

        adopt(url, token)
    if not _state["url"] or not _state["token"]:
        logger.warning("glory.dwell: url/token empty, cannot mark connected")
        return False
    _state["connected"] = True
    _state["verified_at"] = time.time()
    _save()
    logger.info("glory.dwell: connected=true at %s", _state["url"])
    return True


# L229-236：leave — 住户离场 — 浏览器等消费端主动断开或连接失败。
def leave():
    r"""住户离场 — 浏览器等消费端主动断开或连接失败。"""



    _state["connected"] = False
    _save()
    logger.info("glory.leave: connected=false")


# L240-253：reset — 清空连接状态
def reset():
    r"""清空连接状态"""



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
