# L1-45：壳层兼容入口 —— 真正的卡带 UI 在 Slot 包（slot/persona_page.py）。
r"""壳层兼容入口 —— 真正的卡带 UI 在 Slot 包（slot/persona_page.py）。"""




from __future__ import annotations

import os
import sys

_app = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# L16-26：_loadSlotModule — _loadSlotModule
def _loadSlotModule():
    for _cand in (
        os.path.join(_app, "personal-slot"),
        os.path.join(_app, "persona", "scripts"),
    ):
        if os.path.isfile(os.path.join(_cand, "slot", "persona_page.py")):
            if _cand not in sys.path:
                sys.path.insert(0, _cand)
            break
    import slot.persona_page as _m
    return _m


# L30-39：PersonaPage — 懒代理：只加载真正 Slot 页面；未安装 Slot 直接抛错。
class PersonaPage:
    r"""懒代理：只加载真正 Slot 页面；未安装 Slot 直接抛错，不提供壳内兜底。"""

    def __new__(cls, *args, **kwargs):
        try:
            _m = _loadSlotModule()
            return _m.PersonaPage(*args, **kwargs)
        except Exception as _e:
            raise RuntimeError("Personal Slot 未安装或不可用: %s" % _e) from _e


# L43-45：__getattr__ — 保留 indinid / ensureSlotPackage 等旧导出名，但不在导入期强依赖 slot。
def __getattr__(name: str):
    r"""保留 indinid / ensureSlotPackage 等旧导出名，但不在导入期强依赖 slot。"""
    return getattr(_loadSlotModule(), name)
