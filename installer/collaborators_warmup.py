# L1-20：协作者字典后台预热入口 — 只读本地文档缓存，不参与核心预热。
r"""协作者字典后台预热入口 — 只读本地文档缓存，不参与核心预热。"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def main() -> int:
    from kernel.scout import warmCollaboratorsFromCache
    warmCollaboratorsFromCache()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
