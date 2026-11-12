# L1-109：网关信号 + 「等账本被写」的离线用例（不碰 WSL、不碰装好的产品）。
r"""网关信号 + 「等账本被写」的离线用例（不碰 WSL、不碰装好的产品）。"""










import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from main import main as m


# L30-105：TestGatewaySignal — TestGatewaySignal
class TestGatewaySignal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rheia_signals_test_")
        self.path = os.path.join(self.tmp, "signals.json")
        self._old_sig = m._SIGNALS_SINGLETON
        self._old_probe = m._GATEWAY_PROBE
        self.sig = m.Signals(self.path)
        m._SIGNALS_SINGLETON = self.sig
        m._GATEWAY_PROBE = m.GatewayProbe()
        self.sig.open_install("test-install")

    def tearDown(self):
        m._SIGNALS_SINGLETON = self._old_sig
        m._GATEWAY_PROBE = self._old_probe
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_wait_change_wakes_on_write(self):
        r"""写者一笔下去，等的人被叫醒；醒来时账本已经是新的。"""
        box = {}

        def _writer():
            time.sleep(0.3)
            box["written_at"] = time.perf_counter()
            m.Signals(self.path).mark_gateway(True, probe="test")

        t = threading.Thread(target=_writer)
        t.start()
        woke = self.sig.wait_change()
        woke_at = time.perf_counter()
        t.join()
        self.assertTrue(woke, "wait_change 说没看成（拿不到通知对象）")
        self.assertGreaterEqual(woke_at, box["written_at"], "比写还先醒 ⇒ 不是真等")
        self.assertLess(woke_at - box["written_at"], 1.0, "醒来比写晚了一秒以上 ⇒ 不是被写叫醒的")
        self.assertTrue(m.gateway_probe().ready(), "醒来后账本里还不是 ready")

    def test_ready_reads_the_ledger(self):
        r"""探针的 ready 就是账本 Gateway 段那一栏，读不到就是没 ready。"""
        self.assertFalse(m.gateway_probe().ready())
        self.sig.mark_gateway(True, probe="test")
        self.assertTrue(m.gateway_probe().ready())
        self.sig.mark_gateway(False, probe="test")
        self.assertFalse(m.gateway_probe().ready())

    def test_install_chain_gates(self):
        r"""读链按 wsl→node→cli→plugin→done 找上一步；缺一步就是 False。"""
        ok, prev = self.sig.prev_ready("node")
        self.assertFalse(ok)
        self.assertEqual(prev, "wsl")

        self.sig.mark_install("wsl")
        ok, prev = self.sig.prev_ready("node")
        self.assertTrue(ok)
        self.assertEqual(prev, "wsl")

        ok, prev = self.sig.prev_ready("plugin")
        self.assertFalse(ok)
        self.assertEqual(prev, "cli")

        self.sig.mark_install("node")
        self.sig.mark_install("cli")
        ok, prev = self.sig.prev_ready("plugin")
        self.assertTrue(ok)
        self.assertEqual(prev, "cli")

        ok, prev = self.sig.prev_ready("done")
        self.assertFalse(ok)
        self.assertEqual(prev, "plugin")

    def test_install_segment_is_frozen_within_one_install(self):
        r"""同一次装机里，已签的 ready 不许被改回；换了 install_id 才整段重写。"""
        self.sig.mark_install("wsl")
        self.sig.mark_install("wsl", ready_=False)
        self.assertTrue(self.sig.ready("wsl"), "同一次装机里 ready 被改回去了")

        self.sig.open_install("another-install")
        self.assertFalse(self.sig.ready("wsl"), "换了 install_id 旧 ready 还留着")


if __name__ == "__main__":
    unittest.main(verbosity=2)
