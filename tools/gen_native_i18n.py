#!/usr/bin/env python3
"""生成 card.c / uninstall.c 的原生 28 语言翻译 C 头。

纯 C 的安装卡/卸载卡（card.c / uninstall.c）无法 import Python 的 tr()，
这里把它们的用户可见文案按 28 语言生成 C 头（UTF-8 字面量），
C 侧用 GetUserDefaultUILanguage 检测语言码后查表取文案。

用法：
    python tools/gen_native_i18n.py

产出：
    extract/card_i18n.h
    extract/uninstall_i18n.h

文案来源：本脚本内嵌 zh/en 主文案 + ui/translations/*.json 的 28 语言。
其余 26 语言若缺 key，回落英文（i18n 已有 _EN_FALLBACK 同款语义）。
"""
from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── 语言码 → 翻译 JSON 文件 ──
# 对齐 ui/i18n.py 的 _LANG_ID_MAP：primary lang id → 语言码
LANG_ID_MAP = {
    0x01: "ar", 0x02: "bg", 0x03: "ca", 0x05: "cs", 0x06: "da", 0x07: "de",
    0x09: "en", 0x0A: "es", 0x0B: "fi", 0x0C: "fr", 0x0D: "he", 0x0E: "hu",
    0x10: "it", 0x12: "ko", 0x13: "nl", 0x14: "nn", 0x15: "pl", 0x16: "pt_BR",
    0x19: "ru", 0x1A: "hr", 0x1B: "sk", 0x1D: "sv", 0x1F: "tr", 0x22: "uk",
    0x26: "lv", 0x29: "fa", 0x37: "ka", 0x1E: "el",
}
# 0x11 日语不在内（红线），0x04 中文单独归 zh

def _load(lang: str) -> dict:
    p = os.path.join(ROOT, "ui", "translations", f"{lang}.json")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

# ── 两个 C 文件的文案 key 与 zh/en 主文案 ──
# key 用 ui.card.* / ui.uninstall.*
CARD_KEYS = {
    "ui.card.choose_dir":        ("选择安装位置", "Select Install Location"),
    "ui.card.browse":            ("浏览...", "Browse..."),
    "ui.card.next":              ("下一步", "Next"),
    "ui.card.cancel":            ("取消", "Cancel"),
    "ui.card.shell_only":        ("已有 OpenClaw，仅安装 OneKey 作为管理器",
                                  "OpenClaw already installed, install OneKey as manager only"),
    "ui.card.preparing":         ("准备安装...", "Preparing..."),
    "ui.card.confirm_cancel_title": ("确认取消", "Confirm Cancel"),
    "ui.card.confirm_cancel_text":  ("确定要取消安装吗？", "Are you sure you want to cancel the installation?"),
    "ui.card.rollback_text":     ("已安装的部分将被回滚。", "Installed parts will be rolled back."),
    "ui.card.confirm_cancel_yes": ("确认取消", "Confirm Cancel"),
    "ui.card.continue_install":  ("继续安装", "Continue Install"),
    "ui.card.unpacking":         ("解压中...", "Unpacking..."),
    "ui.card.reject_cn":         ("本软件不提供日文支持，安装程序将自毁。",
                                  "This software does not support Japanese. The installer will self-destruct."),
    # 日文劝退文案是红线唯一例外，固定日语不随语言切换
}

UNINSTALL_KEYS = {
    "ui.uninstall.title":            ("卸载向导", "Uninstall Wizard"),
    "ui.uninstall.to_remove":        ("将被移除：", "Will be removed:"),
    "ui.uninstall.item_program":     ("OneKeyOpenClaw 程序文件", "OneKeyOpenClaw program files"),
    "ui.uninstall.item_program_desc": ("安装目录下的所有程序和库", "All programs and libraries in the install directory"),
    "ui.uninstall.item_wsl":         ("WSL Ubuntu 子系统", "WSL Ubuntu subsystem"),
    "ui.uninstall.item_wsl_desc":    ("GNU/Linux 运行环境和 Gateway 进程", "GNU/Linux runtime and Gateway process"),
    "ui.uninstall.item_userdata":    ("用户运行时数据", "User runtime data"),
    "ui.uninstall.item_userdata_desc": ("~/.openclaw 下的模型密钥与设置", "Model keys and settings under ~/.openclaw"),
    "ui.uninstall.item_shared":      ("共享应用数据", "Shared app data"),
    "ui.uninstall.item_shared_desc": ("ProgramData 中的运行时缓存和日志", "Runtime cache and logs in ProgramData"),
    "ui.uninstall.keep_checkbox":    ("仅卸载 OneKey", "Uninstall OneKey only"),
    "ui.uninstall.confirm_title":    ("确定要卸载 OneKeyOpenClaw 吗？", "Are you sure you want to uninstall OneKeyOpenClaw?"),
    "ui.uninstall.confirm_irreversible": ("该操作不可撤销，请确认。", "This action is irreversible. Please confirm."),
    "ui.uninstall.confirm_uninstall": ("确认卸载", "Confirm Uninstall"),
    "ui.uninstall.cancel":           ("取消", "Cancel"),
    "ui.uninstall.uninstalling":     ("正在卸载...", "Uninstalling..."),
    "ui.uninstall.checking":         ("检查程序占用", "Checking program usage"),
    "ui.uninstall.removing":         ("移除程序文件", "Removing program files"),
    "ui.uninstall.done":             ("卸载完成", "Uninstall complete"),
    # 弹配置包是**业务所需**（卡还插着就卸 ⇒ 客户回来会发现人格还是卡的、宿主原件还压在 .bak 里），
    # 所以它是卸载流程里**光明正大**的一步：窗口上显示进度、且前置在清任何东西之前；
    # 没载入就不走这一步（不白起 WSL、不占卸载进程）。
    "ui.uninstall.ejecting_card":    ("正在弹出配置包…", "Ejecting the card…"),
    # 弹不出来就**不许往下清**：往下走一步（清 WSL / 删 {app}），这一趟要"收"回配置包的
    # 东西就永远收不回去了。所以这里如实告诉客户，且一个字节都不删。
    # 文案必须短到一行画得下（进度页那一行是单行、不裁剪区域 400px）。
    "ui.uninstall.eject_failed":     ("配置包没能弹出，卸载已中止", "Card eject failed — uninstall aborted"),
    # 卸载壳时要把**我们自己的插件**从 WSL 侧摘干净（`persona/indivi.py --uninstall-plugin`）：
    # 配置里 plugins.entries + plugins.load.paths 两处漏摘一处，下次安装会被半残配置绊住
    # （persona/indivi.py 里 --uninstall-plugin 那段原话）。只在**保留 WSL**那条路上做（全量卸载 WSL 整个没了）。
    "ui.uninstall.removing_plugin":  ("正在移除 Rheia 插件…", "Removing the Rheia plugin…"),
}


def _load_zh_en():
    zh = _load("zh"); en = _load("en")
    return zh, en


def _build_header(keys: dict, prefix: str, zh: dict, en: dict) -> str:
    """生成 C 头。返回 (头内容, 每语言条目数)。"""
    langs = list(LANG_ID_MAP.items())  # [(langid, code), ...]
    # 中文单独：primary 0x04 → zh
    lines = []
    lines.append(f"/* Auto-generated by tools/gen_native_i18n.py — do not edit. */")
    lines.append("#ifndef %s_I18N_H" % prefix.upper())
    lines.append("#define %s_I18N_H" % prefix.upper())
    lines.append("")
    lines.append("/* 语言码索引：0=zh, 1..28 = LANG_ID_MAP 顺序。 */")
    lines.append("static const WCHAR *%s_i18n[29][%d] = {" % (prefix, len(keys)))
    # 顺序：先 zh（index 0），再按 LANG_ID_MAP 顺序
    ordered_langs = [(0x04, "zh")] + list(langs)
    for langid, code in ordered_langs:
        entries = []
        for key, (zh_val, en_val) in keys.items():
            val = None
            if code == "zh":
                val = zh.get(key) or zh_val
            else:
                data = _load(code)
                val = data.get(key) or en.get(key) or en_val
            # C 宽字符串字面量，转义 \ 和 "
            esc = val.replace("\\", "\\\\").replace('"', '\\"')
            entries.append('L"%s"' % esc)
        lines.append("    { " + ", ".join(entries) + " },")
    lines.append("};")
    lines.append("")
    lines.append("/* 语言码 → 行索引。c 侧用 GetUserDefaultUILanguage 得 primary，查此表。 */")
    lines.append("static const struct { int langid; int idx; } %s_langmap[] = {" % prefix)
    lines.append("    {0x04, 0},  /* zh */")
    for i, (langid, code) in enumerate(langs, start=1):
        lines.append("    {0x%02X, %d},  /* %s */" % (langid, i, code))
    lines.append("};")
    lines.append("")
    lines.append("/* 行数 = 文案条数（%d） */" % len(keys))
    lines.append("#define %s_KEYS %d" % (prefix.upper(), len(keys)))
    lines.append("")
    lines.append("#endif")
    return "\n".join(lines) + "\n"


def main():
    zh, en = _load_zh_en()
    # 先把 CARD/UNINSTALL key 写进 zh/en JSON（补全主语言）
    for keys, prefix in ((CARD_KEYS, "ui.card"), (UNINSTALL_KEYS, "ui.uninstall")):
        for key, (zh_val, en_val) in keys.items():
            zh.setdefault(key, zh_val)
            en.setdefault(key, en_val)
    with open(os.path.join(ROOT, "ui", "translations", "zh.json"), "w", encoding="utf-8") as f:
        json.dump(zh, f, ensure_ascii=False, indent=2)
    with open(os.path.join(ROOT, "ui", "translations", "en.json"), "w", encoding="utf-8") as f:
        json.dump(en, f, ensure_ascii=False, indent=2)

    for keys, prefix, out in ((CARD_KEYS, "card", "extract/card_i18n.h"),
                              (UNINSTALL_KEYS, "uninstall", "extract/uninstall_i18n.h")):
        header = _build_header(keys, prefix, zh, en)
        path = os.path.join(ROOT, out)
        with open(path, "w", encoding="utf-8") as f:
            f.write(header)
        print("generated", out, "(%d keys)" % len(keys))


if __name__ == "__main__":
    main()
