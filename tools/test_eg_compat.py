# L1-95：test_eg_compat — 旧版 .eg 卡带兼容解析离线验证
r"""test_eg_compat — 旧版 .eg 卡带兼容解析离线验证"""







from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.dirname(_HERE)
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

_DAISY_DIR = r"E:\Daisy\DaisyUI\individual"


# L24-90：run_all — run_all
def run_all():
    from persona.indivi import load_legacy_eg

    passed = 0
    failed = 0

    def check(name, cond):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"[PASS] {name}")
        else:
            failed += 1
            print(f"[FAIL] {name}")


    p = os.path.join(_DAISY_DIR, "DaisySecretary.eg")
    if os.path.exists(p):
        manifest, content, err = load_legacy_eg(p)
        check("变体1 DaisySecretary 无错误", err == "")
        check("变体1 name 提取", manifest and manifest.get("name") == "编剧小姐Daisy")
        check("变体1 prompt 非空", bool(content and "SYSTEM_PROMPT" not in content[:5]))
        check("变体1 persona_content 含 frontmatter", content is not None and "---" in content)
        check("变体1 prompt 含人设", content and "Daisy" in content)
    else:
        print(f"[SKIP] 变体1 文件不存在 {p}")


    p = os.path.join(_DAISY_DIR, "7Layer.eg")
    if os.path.exists(p):
        manifest, content, err = load_legacy_eg(p)
        check("变体2 7Layer 无错误", err == "")
        check("变体2 name 提取（空白模板）", manifest and manifest.get("name") == "空白模板")
        check("变体2 prompt 为空但不报错", content is not None and "author: legacy" in content)
    else:
        print(f"[SKIP] 变体2 文件不存在 {p}")


    p = os.path.join(_DAISY_DIR, "7layer-engine_blank.eg")
    if os.path.exists(p):
        manifest, content, err = load_legacy_eg(p)
        check("变体3 INI 无错误", err == "")
        check("变体3 有 manifest", manifest is not None)
        check("变体3 persona_content 非空", bool(content))
    else:
        print(f"[SKIP] 变体3 文件不存在 {p}")


    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".eg", delete=False, encoding="utf-8") as f:
        f.write("this is not a card")
        bad_path = f.name
    manifest, content, err = load_legacy_eg(bad_path)
    check("非卡带文件报错", err != "")
    os.remove(bad_path)



    with tempfile.NamedTemporaryFile("w", suffix=".eg", delete=False, encoding="utf-8") as f:
        f.write("import os\nos.makedirs(r'C:/tmp_slot_eg_pwned', exist_ok=True)\nSYSTEM_PROMPT = 'x'\n")
        evil_path = f.name
    load_legacy_eg(evil_path)
    check("不执行卡带代码（无副作用文件）", not os.path.exists(r"C:/tmp_slot_eg_pwned"))
    os.remove(evil_path)

    print(f"\n=== {passed} passed, {failed} failed ===")
    return passed, failed


if __name__ == "__main__":
    p, f = run_all()
    sys.exit(1 if f else 0)
