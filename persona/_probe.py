# -*- coding: utf-8 -*-
# L1-48：_probe — 插槽链路检测桩（诊断用，整文件可整体拔除）。
r"""_probe — 插槽链路检测桩（诊断用，整文件可整体拔除）。"""















from __future__ import annotations

import sys
import time


_PATH = ""


# L29-31：trace_path — 老接口：现在没有落点文件了，返回空串。
def trace_path() -> str:
    r"""老接口：现在没有落点文件了，返回空串。"""
    return _PATH


# L35-48：probe — 打一行 [stage] k=v … 到 stderr。永不抛、不落任何文件。
def probe(stage: str, **kv) -> None:
    r"""打一行 [stage] k=v … 到 stderr。永不抛、不落任何文件。"""
    try:
        parts = []
        for k, v in kv.items():
            s = str(v).replace("\n", "\\n")
            if len(s) > 200:
                s = s[:200] + "…"
            parts.append(f"{k}={s}")
        line = (f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [slot.{stage}] "
                + " ".join(parts))
        print(line, file=sys.stderr, flush=True)
    except Exception:
        pass
