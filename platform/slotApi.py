# -*- coding: utf-8 -*-
"""platform.slotApi — 壳与 Personal Slot 的唯一薄接口。

L1-62：壳只通过本模块请求卡域能力；卡域实现位于 personal-slot/persona。
"""
from __future__ import annotations

import importlib
import os
import sys
from typing import Any, Callable


def _slot_dir() -> str:
    _app = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _cand = os.path.join(_app, "personal-slot")
    return _cand if os.path.isdir(_cand) else ""


def _load(name: str):
    _root = _slot_dir()
    if not _root:
        raise ImportError("Personal Slot 未安装")
    if _root not in sys.path:
        sys.path.insert(0, _root)
    return importlib.import_module(name)


def _call(module: str, name: str, *args: Any, **kwargs: Any) -> Any:
    return getattr(_load(module), name)(*args, **kwargs)


def setStake(fn: Callable) -> bool:
    try:
        _call("persona.indivi", "setStake", fn)
        return True
    except Exception:
        return False


def setInjectPlugins(fn: Callable) -> bool:
    try:
        _call("persona.indivi", "setInjectPlugins", fn)
        return True
    except Exception:
        return False


def _reconcile_md_watch(*args: Any, **kwargs: Any) -> Any:
    return _call("persona.indivi", "_reconcile_md_watch", *args, **kwargs)


def injectPersona(*args: Any, **kwargs: Any) -> Any:
    return _call("persona.indivi", "injectPersona", *args, **kwargs)


def declare_tlc_consumer(*args: Any, **kwargs: Any) -> Any:
    return _call("persona.indivi", "declare_tlc_consumer", *args, **kwargs)


def refresh_slot_declarations(*args: Any, **kwargs: Any) -> Any:
    try:
        return _call("persona.indivi", "refresh_slot_declarations", *args, **kwargs)
    except Exception:
        return False


def read_slot_window(*args: Any, **kwargs: Any) -> dict:
    try:
        return _call("persona.indivi", "read_slot_window", *args, **kwargs)
    except Exception:
        return {}


def agentInRoster(*args: Any, **kwargs: Any) -> bool:
    try:
        return bool(_call("persona.indivi", "agentInRoster", *args, **kwargs))
    except Exception:
        return False


def read_indm(*args: Any, **kwargs: Any) -> Any:
    return _call("persona.cardBridge", "read_indm", *args, **kwargs)


def read_indm_corpus(*args: Any, **kwargs: Any) -> Any:
    return _call("persona.cardBridge", "read_indm_corpus", *args, **kwargs)


def read_indm_expertise(*args: Any, **kwargs: Any) -> Any:
    return _call("persona.cardBridge", "read_indm_expertise", *args, **kwargs)


def memoryPanelClass() -> Any:
    return _load("slot.memory_panel").MemoryPanel
