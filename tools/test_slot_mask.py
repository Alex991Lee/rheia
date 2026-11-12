# L1-347：test_slot_mask — 人格插槽 .bak 改名回滚框架离线验证
r"""test_slot_mask — 人格插槽 .bak 改名回滚框架离线验证"""







from __future__ import annotations

import os
import shutil
import sys
import tempfile



for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.dirname(_HERE)
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)


# L32-342：run_all — run_all
def run_all():
    from persona.indivi import (
        slot_files,
        persona_files_from_card,
        write_indm,
        FIELD_MAP,
        backup_all,
        backup_originals,
        clear_slot,
        preload_manifest,
        recover_stale_bak,
        restore_originals,
    )

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


    check("默认 generates = ['soul']", preload_manifest({}) == ["soul"])
    check("无 generates 字段 → 默认", preload_manifest({"name": "x"}) == ["soul"])
    check("显式 [soul, memory]", preload_manifest({"generates": ["soul", "memory"]}) == ["soul", "memory"])
    check("逗号字符串解析", preload_manifest({"generates": "soul, memory"}) == ["soul", "memory"])
    check("非法字段被过滤", preload_manifest({"generates": ["soul", "nonsense"]}) == ["soul"])
    check("全非法回退默认", preload_manifest({"generates": ["foo"]}) == ["soul"])
    check("去重保序", preload_manifest({"generates": ["memory", "soul", "memory"]}) == ["memory", "soul"])


    check("FIELD_MAP soul→SOUL.md", FIELD_MAP["soul"] == "SOUL.md")
    check("FIELD_MAP memory→MEMORY.md", FIELD_MAP["memory"] == "MEMORY.md")
    check("FIELD_MAP identity→IDENTITY.md", FIELD_MAP["identity"] == "IDENTITY.md")
    check("FIELD_MAP user→USER.md", FIELD_MAP["user"] == "USER.md")


    ws = tempfile.mkdtemp(prefix="slot_mask_")
    try:
        mem_orig = "Linda 的长期记忆内容"
        id_orig = "Linda 的身份内容"
        with open(os.path.join(ws, "MEMORY.md"), "w", encoding="utf-8") as f:
            f.write(mem_orig)
        with open(os.path.join(ws, "IDENTITY.md"), "w", encoding="utf-8") as f:
            f.write(id_orig)

        r = backup_originals(ws, ["soul", "memory", "identity"])
        check("backup 只返回需 .bak 字段", set(r.keys()) == {"memory", "identity"})
        check("backup memory 成功", r["memory"] and r["identity"])
        check("backup 后 MEMORY.md 消失", not os.path.exists(os.path.join(ws, "MEMORY.md")))
        check("backup 后 MEMORY.md.bak 存在", os.path.exists(os.path.join(ws, "MEMORY.md.bak")))
        check("backup 后 SOUL.md 不受影响（soul 跳过）", not os.path.exists(os.path.join(ws, "SOUL.md.bak")))


        with open(os.path.join(ws, "MEMORY.md"), "w", encoding="utf-8") as f:
            f.write("卡带生成的记忆")
        with open(os.path.join(ws, "IDENTITY.md"), "w", encoding="utf-8") as f:
            f.write("卡带生成的身份")

        r2 = restore_originals(ws, ["memory", "identity"])
        check("restore 成功", r2["memory"] and r2["identity"])
        with open(os.path.join(ws, "MEMORY.md"), "r", encoding="utf-8") as f:
            check("restore 后 MEMORY.md 字节还原", f.read() == mem_orig)
        with open(os.path.join(ws, "IDENTITY.md"), "r", encoding="utf-8") as f:
            check("restore 后 IDENTITY.md 字节还原", f.read() == id_orig)
        check("restore 后 .bak 消失", not os.path.exists(os.path.join(ws, "MEMORY.md.bak")))
    finally:
        shutil.rmtree(ws, ignore_errors=True)


    ws2 = tempfile.mkdtemp(prefix="slot_mask_")
    try:
        with open(os.path.join(ws2, "MEMORY.md"), "w", encoding="utf-8") as f:
            f.write("原文件 v1")
        backup_originals(ws2, ["memory"])

        with open(os.path.join(ws2, "MEMORY.md"), "w", encoding="utf-8") as f:
            f.write("卡带文件")

        r = backup_originals(ws2, ["memory"])
        check("二次 backup 幂等成功", r["memory"] is True)
        with open(os.path.join(ws2, "MEMORY.md.bak"), "r", encoding="utf-8") as f:
            check(".bak 仍是最初原文件 v1", f.read() == "原文件 v1")
    finally:
        shutil.rmtree(ws2, ignore_errors=True)


    ws3 = tempfile.mkdtemp(prefix="slot_mask_")
    try:
        with open(os.path.join(ws3, "MEMORY.md"), "w", encoding="utf-8") as f:
            f.write("崩溃前的原文件")
        backup_originals(ws3, ["memory"])

        recovered = recover_stale_bak(ws3)
        check("自愈还原了残留 .bak", recovered == ["memory"])
        check("自愈后 MEMORY.md 恢复", os.path.exists(os.path.join(ws3, "MEMORY.md")))
        with open(os.path.join(ws3, "MEMORY.md"), "r", encoding="utf-8") as f:
            check("自愈后内容还原", f.read() == "崩溃前的原文件")
        check("自愈后 .bak 消失", not os.path.exists(os.path.join(ws3, "MEMORY.md.bak")))
    finally:
        shutil.rmtree(ws3, ignore_errors=True)


    ws4 = tempfile.mkdtemp(prefix="slot_mask_")
    try:
        with open(os.path.join(ws4, "MEMORY.md"), "w", encoding="utf-8") as f:
            f.write("当前有效文件")
        with open(os.path.join(ws4, "MEMORY.md.bak"), "w", encoding="utf-8") as f:
            f.write("多余残留 .bak")
        recovered = recover_stale_bak(ws4)
        check("原文件在时自愈不覆盖", recovered == [])
        with open(os.path.join(ws4, "MEMORY.md"), "r", encoding="utf-8") as f:
            check("原文件未被触碰", f.read() == "当前有效文件")
    finally:
        shutil.rmtree(ws4, ignore_errors=True)











    ws5_root = tempfile.mkdtemp(prefix="slot_mask_")
    ws5 = os.path.join(ws5_root, "workspace")
    os.makedirs(ws5, exist_ok=True)
    try:

        with open(os.path.join(ws5, "SOUL.md"), "w", encoding="utf-8") as f:
            f.write("宿主原 SOUL")
        backup_all(ws5)

        with open(os.path.join(ws5, "SOUL.md"), "w", encoding="utf-8") as f:
            f.write("卡带 SOUL")
        _card5 = os.path.join(ws5_root, "card5.indm")
        write_indm(("---\nagents:\n  version: 1\n  md: |\n    卡带落位表\n---\n\n正文\n").encode("utf-8"),
                   b"", b"", b"", b"", b'{"version":1, "algo": {"llm": false}}', _card5)
        from persona.indivi import _read_card_agents_md as _rcam
        _agents_txt = _rcam({"filepath": _card5}) or ""
        with open(os.path.join(ws5, "AGENTS.md"), "w", encoding="utf-8") as f:
            f.write(_agents_txt)


        with open(os.path.join(ws5, "MEMORY.md"), "w", encoding="utf-8") as f:
            f.write("宿主插卡期间写的记忆")

        rep = clear_slot(ws5, card_path=_card5)
        check("拔卡：还原宿主 SOUL（有 .bak）", "SOUL.md" in rep["restored"])
        check("拔卡：删掉卡带顶上来的 AGENTS.md（内容对得上卡上的 agents 块）",
              "AGENTS.md" in rep["removed"])
        check("拔卡后 AGENTS.md 真没了",
              not os.path.exists(os.path.join(ws5, "AGENTS.md")))
        with open(os.path.join(ws5, "SOUL.md"), "r", encoding="utf-8") as f:
            check("拔卡后 SOUL.md 字节还原", f.read() == "宿主原 SOUL")
        check("拔卡后 .bak 清干净",
              not os.path.exists(os.path.join(ws5, "SOUL.md.bak")))
        check("内容对不上卡投影的那份**留着**（宁可不删，也不动宿主的东西）",
              "MEMORY.md" in (rep.get("kept") or [])
              and os.path.exists(os.path.join(ws5, "MEMORY.md")))
        check("返回的 removed / restored / kept 都是文件名清单",
              all(isinstance(rep.get(k), list) for k in ("removed", "restored", "kept")))
    finally:
        shutil.rmtree(ws5_root, ignore_errors=True)





    check("卡上没带落位表（空路径）⇒ 空表（壳不替卡编）",
          persona_files_from_card("") == [])
    check("卡上没带落位表（不存在的卡）⇒ 空表",
          persona_files_from_card(
              os.path.join(tempfile.gettempdir(), "no_such_card.indm")) == [])
    _fb = slot_files("")
    check("卡上没带 ⇒ slot_files 退兜底名单（并已告警）",
          len(_fb) >= 5 and "SOUL.md" in _fb)






    _card6 = os.path.join(tempfile.mkdtemp(prefix="rheia_v6_"), "c.indm")
    try:
        _soul = ("---\nusage: 专家\n---\n\n# 正文\n\n"
                 "- 我是谁 → IDENTITY.md\n- 我说话的样子 → SOUL.md\n")
        write_indm(_soul.encode("utf-8"), b"", b"", b"", b"",
                   b'{"version": 1, "algo": {"llm": false}}', _card6)
        check("v6 卡的落位表读得出来（不是空表）",
              persona_files_from_card(_card6) == ["IDENTITY.md", "SOUL.md"])
        _sf = slot_files(_card6)
        check("卡上落位表说了啥就用啥（不掺兜底）",
              "IDENTITY.md" in _sf and "SOUL.md" in _sf and "MEMORY.md" not in _sf)
        check("AGENTS.md 一律并进名单（烙印那份也要遮罩/还原）",
              "AGENTS.md" in _sf)
    finally:
        shutil.rmtree(os.path.dirname(_card6), ignore_errors=True)







    import json as _json
    from persona import indivi as _I
    _ws11 = tempfile.mkdtemp(prefix="rheia_ws11_")
    _card11 = os.path.join(_ws11, "c.indm")
    write_indm(b"soul", b"", b"", b"", b"", b'{"version":1, "algo": {"llm": false}}', _card11)
    with open(os.path.join(_ws11, "IDENTITY.md"), "w", encoding="utf-8") as _f:
        _f.write("宿主原件：我是大鹅\n")
    _old_pol = _I._SLOT_POLICY_FILE
    try:
        _pol = os.path.join(_ws11, "policy.json")
        _I._SLOT_POLICY_FILE = _pol
        with open(_pol, "w", encoding="utf-8") as _f:
            _f.write('{"card": ""}')
        _goose = "大鹅".encode("utf-8")
        _r1 = _I.collect(_card11, _ws11, delete=False)
        check("卡不在槽上 ⇒ 工作区那份不许进卡（宿主原件一个字节都收不进去）",
              _goose not in (_I._read_zones(_card11).get("id") or b"")
              and any("不在槽上" in x for x in _r1))
        with open(_pol, "w", encoding="utf-8") as _f:
            _f.write(_json.dumps({"card": _I._to_wsl_path(_card11)}))
        _r2 = _I.collect(_card11, _ws11, delete=False)
        check("指针指着卡**也**不许把宿主原件收进卡（判据是归属，不是指针）",
              _goose not in (_I._read_zones(_card11).get("id") or b"")
              and any("不进卡" in x for x in _r2))
        check("被拒的那份宿主文件**不许删**（宁可不收，也不动宿主的东西）",
              os.path.exists(os.path.join(_ws11, "IDENTITY.md")))


        _I.backup_all(_ws11, card_path=_card11)
        with open(os.path.join(_ws11, "IDENTITY.md"), "w", encoding="utf-8") as _f:
            _f.write("我是林清清（她在槽上写的）\n")
        _r3 = _I.collect(_card11, _ws11, delete=False)
        check("有 `.bak` ⇒ 卡的地盘上她写的内容照收回 id 区（回程通着）",
              "林清清".encode("utf-8") in (_I._read_zones(_card11).get("id") or b"")
              and any("回收 IDENTITY.md" in x for x in _r3))


        _ws11b = tempfile.mkdtemp(prefix="rheia_ws11b_")
        _card11b = os.path.join(_ws11b, "c11b.indm")
        _mem_zone11b = ("<\x21-- file:MEMORY.md -->\n她写的记忆\n"
                        "<\x21-- end:MEMORY.md -->").encode("utf-8")
        write_indm(b"soul", b"", _mem_zone11b, b"", b"",
                   b'{"version":1, "algo": {"llm": false}}', _card11b)
        with open(os.path.join(_ws11b, "MEMORY.md"), "w", encoding="utf-8") as _f:
            _f.write(_I._project_text("MEMORY.md", "共同经历", _mem_zone11b))
        with open(_pol, "w", encoding="utf-8") as _f:
            _f.write(_json.dumps({"card": _I._to_wsl_path(_card11b)}))
        _r4 = _I.collect(_card11b, _ws11b, delete=False)
        check("没有 `.bak` 但内容正是卡投影的那份 ⇒ 认它（宿主原本没这份，不误判）",
              any("回收 MEMORY.md" in x for x in _r4))
        _z4 = _I._read_zones(_card11b).get("mem") or b""
        check("重收不套娃（区里仍只有一对 file: 标记）",
              _z4.count(b"<!-- file:MEMORY.md -->") == 1
              and _z4.count(b"<!-- end:MEMORY.md -->") == 1)
        shutil.rmtree(_ws11b, ignore_errors=True)
    finally:
        _I._SLOT_POLICY_FILE = _old_pol
        shutil.rmtree(_ws11, ignore_errors=True)




    _ws12 = tempfile.mkdtemp(prefix="rheia_ws12_")
    _old_pol12 = _I._SLOT_POLICY_FILE
    try:
        _card12 = os.path.join(_ws12, "c12.indm")
        _mem_zone = ("<\x21-- file:MEMORY.md -->\n卡写的记忆\n<\x21-- end:MEMORY.md -->").encode("utf-8")
        write_indm(b"soul", b"", _mem_zone,
                   b"", b"", b'{"version":1, "algo": {"llm": false}}', _card12)

        with open(os.path.join(_ws12, "MEMORY.md"), "w", encoding="utf-8") as _f:
            _f.write("宿主原件的记忆\n")
        _I.backup_all(_ws12, card_path=_card12)
        check("插卡：宿主原件改名 .bak（.bak 里是宿主的）",
              os.path.exists(os.path.join(_ws12, "MEMORY.md.bak"))
              and open(os.path.join(_ws12, "MEMORY.md.bak"), encoding="utf-8").read() == "宿主原件的记忆\n")

        with open(os.path.join(_ws12, "MEMORY.md"), "w", encoding="utf-8") as _f:
            _f.write("卡写的记忆\n")

        _I.backup_all(_ws12, card_path=_card12)
        check("重复插卡：`.bak` 仍是宿主原件（卡那份永远不进 .bak）",
              open(os.path.join(_ws12, "MEMORY.md.bak"), encoding="utf-8").read() == "宿主原件的记忆\n")

        _pol12 = os.path.join(_ws12, "policy.json")
        with open(_pol12, "w", encoding="utf-8") as _f:
            _f.write('{"card": ""}')
        _I._SLOT_POLICY_FILE = _pol12
        _rep12 = _I.clear_slot(_ws12, card_path=_card12, owned={"MEMORY.md"})
        check("拔卡：卡那份被顶掉、宿主原件回来、.bak 清干净",
              _rep12["restored"] == ["MEMORY.md"]
              and open(os.path.join(_ws12, "MEMORY.md"), encoding="utf-8").read() == "宿主原件的记忆\n"
              and not os.path.exists(os.path.join(_ws12, "MEMORY.md.bak")))
    finally:
        _I._SLOT_POLICY_FILE = _old_pol12
        shutil.rmtree(_ws12, ignore_errors=True)

    print(f"\n=== {passed} passed, {failed} failed ===")
    return passed, failed


if __name__ == "__main__":
    p, f = run_all()
    sys.exit(1 if f else 0)
