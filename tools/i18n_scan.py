"""i18n_scan.py — 硬编码中文分类扫描脚本（只读）

把全项目 .py 里的中文行，自动分类成三张清单：
  UI 文案   — 客户可见（按钮/标签/弹窗/进度文案），后续收编进 i18n
  日志      — 开发者可见（logger/stk/print/注释），保持中文，不收编
  数据      — 诊断桩 tag、命令字符串、json label，不收编

输出到 design/ 下三个 txt，每行「文件:行号:内容」。
只读，不修改任何源文件。分类规则宁可"不确定归日志"，也不把日志误归 UI。
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN_DIRS = ("ui", "main", "installer", "kernel", "persona")
OUT_DIR = os.path.join(ROOT, "design")

_ZH = re.compile(r"[一-鿿]")

# UI 文案信号：这些 API 里的字符串是客户可见的
_UI_SIGNALS = (
    "setText", "addItem", "setWindowTitle", "setPlaceholderText", "setToolTip",
    "setTitle", "QLabel", "QPushButton", "QMessageBox", "QInputDialog",
    "setHeaderLabel", "addWidget", "setStatusTip", "setWhatsThis",
    "_writeSignal", "addTab", "setSuffix", "setPrefix", "setWindowIconText",
    "setCurrentText", "addItems", "setTextFormat",
)

# 日志信号：这些里的中文是开发者看的
_LOG_SIGNALS = (
    "logger.", "stk(", "_stake(", "print(", "log(", "logging.",
)

# 数据信号：诊断桩 tag、命令、配置 key 里的中文
_DATA_SIGNALS = (
    "stk(\"", "stk('", "openclaw ", "config set", "config get",
)

def classify(line: str) -> str:
    """分类一行含中文的代码。返回 ui/log/data。"""
    s = line.strip()

    # 注释 / 纯字符串 docstring → 日志（开发者读）
    if s.startswith("#") or s.startswith('"""') or s.startswith("'''"):
        return "log"
    if s.startswith(('"', "'")) and _ZH.search(s):
        # 独立的字符串字面量行（docstring 正文）→ 日志
        return "log"

    # 数据：诊断桩 tag（stk 的第一个参数是 tag）
    if re.search(r'stk\s*\(\s*["\'][^"\']*["\']', s):
        # stk 的 tag 参数是数据，但 stk 的 msg 参数是日志。整行归数据。
        return "data"

    # 日志信号优先
    if any(sig in s for sig in _LOG_SIGNALS):
        return "log"

    # UI 信号
    if any(sig in s for sig in _UI_SIGNALS):
        return "ui"

    # 命令字符串 → 数据
    if any(sig in s for sig in _DATA_SIGNALS):
        return "data"

    # 默认：不确定归日志（漏翻好于误翻）
    return "log"


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
