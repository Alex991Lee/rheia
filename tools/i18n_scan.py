# L1-112：i18n_scan.py — 硬编码中文分类扫描脚本（只读）
r"""i18n_scan.py — 硬编码中文分类扫描脚本（只读）"""










import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN_DIRS = ("ui", "main", "installer", "kernel", "persona")
OUT_DIR = os.path.join(ROOT, "design")

_ZH = re.compile(r"[一-鿿]")


_UI_SIGNALS = (
    "setText", "addItem", "setWindowTitle", "setPlaceholderText", "setToolTip",
    "setTitle", "QLabel", "QPushButton", "QMessageBox", "QInputDialog",
    "setHeaderLabel", "addWidget", "setStatusTip", "setWhatsThis",
    "_writeSignal", "addTab", "setSuffix", "setPrefix", "setWindowIconText",
    "setCurrentText", "addItems", "setTextFormat",
)


_LOG_SIGNALS = (
    "logger.", "stk(", "_stake(", "print(", "log(", "logging.",
)


_DATA_SIGNALS = (
    "stk(\"", "stk('", "openclaw ", "config set", "config get",
)

# L43-72：classify — 分类一行含中文的代码。返回 ui/log/data。
def classify(line: str) -> str:
    r"""分类一行含中文的代码。返回 ui/log/data。"""
    s = line.strip()


    if s.startswith("#") or s.startswith('"""') or s.startswith("'''"):
        return "log"
    if s.startswith(('"', "'")) and _ZH.search(s):

        return "log"


    if re.search(r'stk\s*\(\s*["\'][^"\']*["\']', s):

        return "data"


    if any(sig in s for sig in _LOG_SIGNALS):
        return "log"


    if any(sig in s for sig in _UI_SIGNALS):
        return "ui"


    if any(sig in s for sig in _DATA_SIGNALS):
        return "data"


    return "log"


# L76-108：main — main
def main():
    rows = {"ui": [], "log": [], "data": []}

    for d in SCAN_DIRS:
        base = os.path.join(ROOT, d)
        for root, dirs, files in os.walk(base):
            if "__pycache__" in root:
                continue
            for fn in sorted(files):
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(root, fn)
                try:
                    lines = open(p, encoding="utf-8").read().splitlines()
                except Exception:
                    continue
                rel = os.path.relpath(p, ROOT).replace("\\", "/")
                for i, line in enumerate(lines, 1):
                    if not _ZH.search(line):
                        continue
                    kind = classify(line)
                    rows[kind].append(f"{rel}:{i}: {line.strip()}")

    os.makedirs(OUT_DIR, exist_ok=True)
    total = 0
    for kind, label in (("ui", "UI 文案"), ("log", "日志"), ("data", "数据")):
        out = os.path.join(OUT_DIR, f"i18n-{kind}-清单.txt")
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(rows[kind]))
        total += len(rows[kind])
        print(f"{label}: {len(rows[kind])} 行 -> {out}")

    print(f"\n合计: {total} 行（报告基准 3650）")


if __name__ == "__main__":
    main()
