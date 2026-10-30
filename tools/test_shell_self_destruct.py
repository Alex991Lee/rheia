#!/usr/bin/env python3
"""壳层自毁单测（in0002 验收）

⚠ 落点已变（2026-09-20 修）：原来 import 的是 `runtime/shell_self_destruct.py`，那个包早
就不存在 —— 这块**方法化并入 `main/main.py`** 了（`main.py:1036` 起「═══ main.shell_self_destruct ═══」
那一节，`_SHELL_FILES` / `_progdata` / `_delete_progdata_shell_files` / `self_destruct` 都在）。
所以改成 `from main import main as sd`。

⚠ 隔离那两条也跟着改了量法（**断言没放宽，只是换了量尺**）：原来扫的是那个独立模块的
全文，现在自毁只是 main.py 里的一节 —— 而 main.py 全文当然会 import license_anchor、
当然会叫 wsl（那是它别处的活）。所以改成**只量自毁那一节**的源码：
"自毁实现自己不许碰 license_anchor、不许以 wsl 为命令" 这条红线照旧焊着。

重点验证红线边界，不实际执行删除：用 monkeypatch + temp 目录伪造
ProgramData 壳层文件，验证：
  - 白名单文件逐个删除，wsl-distro/ 及其下 ext4.vhdx 绝不被删
  - shell_destroyed.flag 幂等
  - _delete_progdata_shell_files 不触碰白名单外的文件
  - self_destruct 那一节不 import license_anchor、不以 wsl 为命令

用法:
    python tools/test_shell_self_destruct.py
"""

import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from main import main as sd


def _self_destruct_section() -> str:
    """main.py 里自毁那一节的源码（从分节标记到下一个分节标记）。"""
    txt = Path(sd.__file__).read_text(encoding="utf-8")
    m = re.search(r"^# ═══ main\.shell_self_destruct.*$", txt, re.M)
    if not m:
        return txt
    rest = txt[m.end():]
    nxt = re.search(r"^# ═══ main\.", rest, re.M)
    return txt[m.start(): m.end() + (nxt.start() if nxt else len(rest))]


class TestShellSelfDestructWhitelist(unittest.TestCase):
    """文件级白名单删除 + wsl-distro 红线保护"""

    def setUp(self):
        # 用临时目录伪造 ProgramData\OneKeyOpenClaw
        self.tmp = tempfile.mkdtemp(prefix="okoc_sd_test_")
        self.progdata = os.path.join(self.tmp, "OneKeyOpenClaw")
        os.makedirs(self.progdata, exist_ok=True)
        # patch _progdata 指向临时目录
        self.p = mock.patch.object(sd, "_progdata", return_value=self.progdata)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mk(self, name, content="x"):
        path = os.path.join(self.progdata, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_whitelist_files_deleted(self):
        for name in sd._SHELL_FILES:
            self._mk(name)
        sd._delete_progdata_shell_files()
        for name in sd._SHELL_FILES:
            self.assertFalse(
                os.path.exists(os.path.join(self.progdata, name)),
                f"{name} 未被删除",
            )

    def test_wsl_distro_protected(self):
        # 伪造 wsl-distro + ext4.vhdx（红线资产，绝不能删）
        vhdx_dir = os.path.join(self.progdata, "wsl-distro")
        os.makedirs(vhdx_dir, exist_ok=True)
        vhdx = os.path.join(vhdx_dir, "ext4.vhdx")
        with open(vhdx, "wb") as f:
            f.write(b"\x00" * 64)
        # 也放一个白名单文件，确保删除逻辑跑过
        self._mk("baseline_pool.db")

        sd._delete_progdata_shell_files()

        self.assertTrue(os.path.exists(vhdx), "wsl-distro\\ext4.vhdx 被删了，红线违规！")
        self.assertTrue(os.path.isdir(vhdx_dir), "wsl-distro 目录被删了，红线违规！")

    def test_non_whitelist_file_untouched(self):
        # 白名单外的文件（如用户自建）不动
        stray = self._mk("user_notes.txt")
        sd._delete_progdata_shell_files()
        self.assertTrue(os.path.exists(stray), "白名单外文件被误删")


class TestShellSelfDestructIsolation(unittest.TestCase):
    """红线隔离：自毁这一节不碰 license_anchor，不以 wsl 为命令"""

    def test_section_does_not_import_license_anchor(self):
        # 运行时验证：模块对象里不得有 license_anchor 的函数引用（说明没 import 它）。
        # 注释里提到的边界说明、白名单里那个「license_anchor.bin」文件名都**不是** import。
        self.assertFalse(hasattr(sd, "lock_wsl_instance"), "不得导入锁 vhdx 函数")
        self.assertFalse(hasattr(sd, "self_destruct_anchor"), "不得导入授权自毁")
        src = _self_destruct_section()
        self.assertNotIn("import license_anchor", src, "自毁那节不得 import license_anchor")
        self.assertNotIn("from main.license_anchor", src, "自毁那节不得 from import license_anchor")

    def test_section_does_not_invoke_wsl(self):
        # 运行时验证：自毁那节里不得有调 wsl 的痕迹（「绝对不能删
        # wsl 侧的任何东西」—— 这条钉子就是那句话的机器版）。
        src = _self_destruct_section()
        self.assertNotIn('subprocess.run(["wsl', src, "不得 subprocess 调 wsl")
        self.assertNotIn('["wsl"', src, "不得以 wsl 为命令首参")
        self.assertNotIn("--unregister", src, "不得注销发行版")
        self.assertNotIn("--terminate", src, "不得终止发行版")


if __name__ == "__main__":
    unittest.main(verbosity=2)
