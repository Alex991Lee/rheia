#!/usr/bin/env python3
"""单实例门 + 自愈重启交棒（2026-09-21「自愈后没有自动拉起壳体」的机器版钉子）

事故原样（实测，stake.log / runtime_stake.log）：

    [22:33:09] [self_heal]  OK restarting self...
    [22:33:16] --- main() entered ---
    [22:33:16] !!! 已有壳实例（互斥体）：本实例退出，不碰网关 !!!

老进程说"重启自己"→ 拉起新进程 → 新进程被自己的门挡在门外、**默默退出** ⇒ 壳没了。
两个原因，各钉一颗钉子：

  ① 交棒没有"等"：老进程退出前要跑 `keepalive().stop_nowait()`（里面两条 wsl
     调用各带 30s 超时），门还握在自己手里；新进程导入完 PySide6 就来敲门。
     → 所以新进程要**等老进程退干净**（`RHEIA_HANDOVER_PID` + `_handover_wait`）。
  ② 老代码里那句 `from main.main import _release_single_instance` 是**另一份模块**：
     壳以脚本身份跑（`pythonw main.py`），`main.main` 是再 import 一次的副本，
     它的模块全局里两个句柄都是 None ⇒ 那一下**一把钥匙都没放**。
     → 所以交棒要操作**活着的那一份**（`sys.modules['__main__']`），见 browser.py 的
       `_live_main()`；有源码级断言钉着，防止有人把 `from main.main import …` 贴回来。

用法:
    python tools/test_shell_singleton_handover.py
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

# 壳的 main.py 会 import PySide6（WSL 里跑测试时装不到）⇒ 拿不到就只跑源码级断言。
try:
    from main import main as sd
    _HAVE_SHELL = True
except Exception:                                                 # noqa: BLE001
    sd = None
    _HAVE_SHELL = False

_BROWSER_PY = _BASE / "ui" / "web" / "browser.py"


def _count_def(src: str, name: str) -> int:
    return len([ln for ln in src.splitlines() if ln.strip().startswith(f"def {name}(")])


class TestHandoverWait(unittest.TestCase):
    """新进程必须等老进程退干净（不是干等：老进程是 os._exit(0)，正常几秒内必走）"""

    def setUp(self):
        if not _HAVE_SHELL:
            self.skipTest("拿不到 main.main（多半没有 PySide6）")
        self.tmp = tempfile.mkdtemp(prefix="okoc_handover_")
        # _stake 落盘：别写进真的 runtime_stake.log
        self.p = mock.patch.object(sd, "_STAKE_PATH", os.path.join(self.tmp, "stake.log"))
        self.p.start()

    def tearDown(self):
        if _HAVE_SHELL:
            self.p.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _wait(self, pid, timeout=15.0):
        with mock.patch.dict(os.environ, {sd._HANDOVER_ENV: str(pid)}):
            t0 = time.monotonic()
            sd._handover_wait(timeout=timeout)
            return time.monotonic() - t0

    def test_waits_until_old_process_exits(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])
        try:
            elapsed = self._wait(child.pid)
        finally:
            child.wait(timeout=10)
        self.assertGreaterEqual(elapsed, 1.2, "没等老进程：交棒还是会撞门")
        self.assertLess(elapsed, 10.0, "等太久（上限机制坏了）")

    def test_env_cleared_after_handover(self):
        # 这变量只管一次交棒：留着会被自己的子进程继承，它们会白等 60s
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.4)"])
        try:
            self._wait(child.pid)
            self.assertNotIn(sd._HANDOVER_ENV, os.environ)
        finally:
            child.wait(timeout=10)

    def test_no_env_is_noop(self):
        os.environ.pop(sd._HANDOVER_ENV, None)
        t0 = time.monotonic()
        sd._handover_wait(timeout=15.0)
        self.assertLess(time.monotonic() - t0, 0.5, "没有交棒标记也该立刻走")

    def test_dead_pid_is_noop(self):
        # 999999 这种不存在的 pid：_pid_alive 认"打不开 = 已经没了"
        self.assertLess(self._wait(999999, timeout=15.0), 0.5)

    def test_pid_alive_self(self):
        self.assertTrue(sd._pid_alive(os.getpid()))
        self.assertFalse(sd._pid_alive(999999))


class TestSingleInstanceDoor(unittest.TestCase):
    """门本身：第二实例被挡 + **被挡之后自己还能放掉再重进**"""

    def setUp(self):
        if not _HAVE_SHELL:
            self.skipTest("拿不到 main.main（多半没有 PySide6）")
        self.tmp = tempfile.mkdtemp(prefix="okoc_door_")
        self.p1 = mock.patch.dict(os.environ, {"PROGRAMDATA": self.tmp})
        self.p1.start()
        # 换成唯一名字：用真名字的话，正在跑的壳会把测试自己挡住
        self.p2 = mock.patch.object(sd, "_SINGLETON_MUTEX_NAME",
                                    "Global\\Rheia.Shell.Singleton.test.%d" % os.getpid())
        self.p3 = mock.patch.object(sd, "_SINGLETON_LOCK_NAME",
                                    "shell.lock.test.%d" % os.getpid())
        self.p4 = mock.patch.object(sd, "_STAKE_PATH", os.path.join(self.tmp, "stake.log"))
        for p in (self.p2, self.p3, self.p4):
            p.start()
        sd._release_single_instance()

    def tearDown(self):
        if _HAVE_SHELL:
            sd._release_single_instance()
            for p in (self.p4, self.p3, self.p2, self.p1):
                p.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_second_acquire_is_refused(self):
        self.assertTrue(sd._acquire_single_instance(), "第一道门没关上")
        self.assertFalse(sd._acquire_single_instance(), "第二个实例没被挡住（会双实例抢网关）")

    def test_refusal_does_not_poison_the_door(self):
        """被拒那一下不许把门焊死：放掉之后还得能重新进来。

        旧写法先 `_SINGLETON_HANDLE = _h` 再判 183 ⇒ 被拒时把新句柄顶上去，
        原来那个句柄再没人关 ⇒ 门放不掉（"让出再重进"永远失败）。
        """
        self.assertTrue(sd._acquire_single_instance())
        self.assertFalse(sd._acquire_single_instance())
        sd._release_single_instance()
        self.assertTrue(sd._acquire_single_instance(), "放掉之后重进失败：门被焊死了")

    def test_lockfile_second_door(self):
        # 锁文件那道（独立于 ctypes）：同进程重入会被自己的锁挡住
        self.assertTrue(sd._acquire_single_instance())
        _p = os.path.join(self.tmp, "Rheia", sd._SINGLETON_LOCK_NAME)
        self.assertTrue(os.path.exists(_p), "锁文件没建：第二道门形同没装")


class TestKeepaliveSpawn(unittest.TestCase):
    """保活真的会起（重复定义那版是**无限相互递归**，一个都没起过）

    实测原样：`keepalive guardian start FAILED: maximum recursion depth exceeded`。
    这里把 `Popen` 换掉（**不碰真机 WSL**），只验证：
      `_ensure_keepalive_unit()` → `_spawn_sleep_infinity()` → 真发出那条常驻命令。
    """

    class _FakeProc:
        # 2026-10-05：`is_alive` 改成**按 pid 问内核**（不再读 Popen.poll()），
        # 所以假进程得挂一个**真活着**的 pid 才验得出"起了也算活着"——
        # 用本测试进程自己的 pid（_pid_alive 认它活着，见 test_pid_alive_self）。
        pid = os.getpid()

        def poll(self):
            return None

    def setUp(self):
        if not _HAVE_SHELL:
            self.skipTest("拿不到 main.main（多半没有 PySide6）")
        self.tmp = tempfile.mkdtemp(prefix="okoc_kp_")
        self.p = mock.patch.object(sd, "_STAKE_PATH", os.path.join(self.tmp, "stake.log"))
        self.p.start()

    def tearDown(self):
        if _HAVE_SHELL:
            self.p.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ensure_keepalive_unit_spawns(self):
        kp = sd.WslKeepalive()
        with mock.patch.object(sd.subprocess, "Popen",
                               return_value=self._FakeProc()) as m, \
             mock.patch.object(sd, "_bind_child_to_parent", lambda pid: True):
            try:
                ok = kp._ensure_keepalive_unit()
            except RecursionError:
                self.fail("_spawn_sleep_infinity 又和 _ensure_keepalive_unit 互相递归了")
        self.assertTrue(ok, "保活没起起来")
        argv = m.call_args[0][0]
        self.assertEqual(argv, ["wsl", "-d", "Ubuntu", "--", "sleep", "infinity"],
                         "保活命令变了（照原样保留常驻 sleep infinity 客户端）")

    def test_guardian_sees_it_alive(self):
        kp = sd.WslKeepalive()
        with mock.patch.object(sd.subprocess, "Popen",
                               return_value=self._FakeProc()) as m, \
             mock.patch.object(sd, "_bind_child_to_parent", lambda pid: True):
            kp._spawn_sleep_infinity()
        self.assertTrue(kp.is_alive, "起了也不算活着 ⇒ guardian 每 15s 会再拉一个")
        self.assertTrue(m.called)

    def test_spawn_is_ptb_form(self):
        """起法照 PTB 20260826-001 那份原样：裸 Popen（只 CREATE_NO_WINDOW）+ 隐藏窗口
        + 三个 DEVNULL，**不给 cwd、不带别的 flag、不做别的**
        （keepalive 按原型机一比一复原回 Rheia）。
        """
        kp = sd.WslKeepalive()
        with mock.patch.object(sd.subprocess, "Popen",
                               return_value=self._FakeProc()) as m:
            kp._spawn_sleep_infinity()
        kw = m.call_args[1]
        self.assertEqual(m.call_args[0][0],
                         ["wsl", "-d", "Ubuntu", "--", "sleep", "infinity"])
        self.assertNotIn("cwd", kw, "PTB 那版不给 cwd")
        self.assertEqual(kw.get("creationflags"), 0x08000000,
                         "PTB 那版只带 CREATE_NO_WINDOW")
        self.assertEqual(kw.get("stdin"), sd.subprocess.DEVNULL)
        self.assertEqual(kw.get("stdout"), sd.subprocess.DEVNULL)
        self.assertEqual(kw.get("stderr"), sd.subprocess.DEVNULL)


class TestSourceInvariants(unittest.TestCase):
    """源码级钉子：环境跑不起壳（没 PySide6）也要能拦住回归"""

    @classmethod
    def setUpClass(cls):
        cls.main_src = (_BASE / "main" / "main.py").read_text(encoding="utf-8")
        cls.browser_src = _BROWSER_PY.read_text(encoding="utf-8")

    def test_acquisition_waits_for_handover(self):
        self.assertIn("def _handover_wait(", self.main_src)
        i = self.main_src.index("def _acquire_single_instance(")
        body = self.main_src[i:i + 1200]
        self.assertIn("_handover_wait()", body, "进门之前没等交棒进程")

    def test_keepalive_spawn_defined_once(self):
        """保活起没起来全看这个：重复定义 ⇒ 相互递归 ⇒ 一个都没起过。

        实测：`keepalive guardian start FAILED: maximum recursion depth exceeded`，
        而 guardian 每 15s 重试、每 15s 再炸一次。
        """
        n = _count_def(self.main_src, "_spawn_sleep_infinity")
        self.assertEqual(n, 1, f"_spawn_sleep_infinity 定义了 {n} 次（重复定义会互相递归）")
        self.assertIn("def _ensure_keepalive_unit(", self.main_src)
        self.assertIn("def _guardian_loop(", self.main_src)

    def test_restart_uses_live_module(self):
        i = self.browser_src.index("def _on_self_heal_restart(")
        body = self.browser_src[i:i + 4200]
        self.assertIn("_live_main()", body, "交棒要操作活着的那一份 main.py")
        self.assertNotIn("from main.main import keepalive", body,
                         "那份是 import 副本：停它等于没停（真保活的 guardian 还在跑）")
        self.assertNotIn("from main.main import _release_single_instance", body,
                         "那份副本的句柄是 None：一把钥匙都放不掉")

    def test_restart_passes_handover_pid(self):
        i = self.browser_src.index("def _on_self_heal_restart(")
        body = self.browser_src[i:i + 4200]
        self.assertIn('"RHEIA_HANDOVER_PID"', body, "新进程不知道要等谁")
        self.assertIn("_os._exit(0)", body)
        # 拉不起来就不许退：退了就真没壳了
        self.assertIn("拉新进程失败", body)

    def test_bonus_install_honors_both_gates(self):
        """预装两个前置（模型已配置 / 自愈完成）谁先到都得能被消费。"""
        i = self.browser_src.index("def _on_self_heal_done(")
        body = self.browser_src[i:i + 1800]
        self.assertIn("_bonus_skills_pending", body,
                      "自愈完成时没看'模型已配置'那个挂起 ⇒ 两个前置都满足也永远不装")


if __name__ == "__main__":
    unittest.main(verbosity=2)
