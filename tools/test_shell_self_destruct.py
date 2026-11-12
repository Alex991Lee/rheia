#!/usr/bin/env python3
# L1-133：壳层自毁单测（in0002 验收）
r"""壳层自毁单测（in0002 验收）"""






















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


# L43-51：_self_destruct_section — main.py 里自毁那一节的源码（从分节标记到下一个分节标记）。
def _self_destruct_section() -> str:
    r"""main.py 里自毁那一节的源码（从分节标记到下一个分节标记）。"""
    txt = Path(sd.__file__).read_text(encoding="utf-8")
    m = re.search(r"^# ═══ main\.shell_self_destruct.*$", txt, re.M)
    if not m:
        return txt
    rest = txt[m.end():]
    nxt = re.search(r"^# ═══ main\.", rest, re.M)
    return txt[m.start(): m.end() + (nxt.start() if nxt else len(rest))]


# L55-106：TestShellSelfDestructWhitelist — 文件级白名单删除 + wsl-distro 红线保护
class TestShellSelfDestructWhitelist(unittest.TestCase):
    r"""文件级白名单删除 + wsl-distro 红线保护"""

    def setUp(self):

        self.tmp = tempfile.mkdtemp(prefix="okoc_sd_test_")
        self.progdata = os.path.join(self.tmp, "OneKeyOpenClaw")
        os.makedirs(self.progdata, exist_ok=True)

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

        vhdx_dir = os.path.join(self.progdata, "wsl-distro")
        os.makedirs(vhdx_dir, exist_ok=True)
        vhdx = os.path.join(vhdx_dir, "ext4.vhdx")
        with open(vhdx, "wb") as f:
            f.write(b"\x00" * 64)

        self._mk("baseline_pool.db")

        sd._delete_progdata_shell_files()

        self.assertTrue(os.path.exists(vhdx), "wsl-distro\\ext4.vhdx 被删了，红线违规！")
        self.assertTrue(os.path.isdir(vhdx_dir), "wsl-distro 目录被删了，红线违规！")

    def test_non_whitelist_file_untouched(self):

        stray = self._mk("user_notes.txt")
        sd._delete_progdata_shell_files()
        self.assertTrue(os.path.exists(stray), "白名单外文件被误删")


# L110-129：TestShellSelfDestructIsolation — 红线隔离：自毁这一节不碰 license_anchor，不以 wsl 为命令
class TestShellSelfDestructIsolation(unittest.TestCase):
    r"""红线隔离：自毁这一节不碰 license_anchor，不以 wsl 为命令"""

    def test_section_does_not_import_license_anchor(self):


        self.assertFalse(hasattr(sd, "lock_wsl_instance"), "不得导入锁 vhdx 函数")
        self.assertFalse(hasattr(sd, "self_destruct_anchor"), "不得导入授权自毁")
        src = _self_destruct_section()
        self.assertNotIn("import license_anchor", src, "自毁那节不得 import license_anchor")
        self.assertNotIn("from main.license_anchor", src, "自毁那节不得 from import license_anchor")

    def test_section_does_not_invoke_wsl(self):


        src = _self_destruct_section()
        self.assertNotIn('subprocess.run(["wsl', src, "不得 subprocess 调 wsl")
        self.assertNotIn('["wsl"', src, "不得以 wsl 为命令首参")
        self.assertNotIn("--unregister", src, "不得注销发行版")
        self.assertNotIn("--terminate", src, "不得终止发行版")


if __name__ == "__main__":
    unittest.main(verbosity=2)
