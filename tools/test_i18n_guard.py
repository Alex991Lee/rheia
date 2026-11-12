#!/usr/bin/env python3
# L1-181：i18n 三态判定单测（in0001 验收）
r"""i18n 三态判定单测（in0001 验收）"""















import sys
import unittest
from pathlib import Path
from unittest import mock

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from main import main as guard


# L32-55：_patch — 伪造检测结果：hits 是命中的语言类检测源名，tz 是佐证是否命中。
def _patch(hits=(), tz=False):
    r"""伪造检测结果：hits 是命中的语言类检测源名，tz 是佐证是否命中。"""
    names = {"lang_id", "wsl_locale", "env"}
    patchers = [
        mock.patch.object(
            guard,
            "_detect_windows_lang",
            return_value=("lang_id" in hits),
        ),
        mock.patch.object(
            guard,
            "_detect_wsl_locale",
            return_value=("wsl_locale" in hits),
        ),
        mock.patch.object(
            guard,
            "_detect_env_override",
            return_value=("env" in hits),
        ),
        mock.patch.object(guard, "_detect_tz", return_value=tz),
    ]
    for p in patchers:
        p.start()
    return patchers


# L59-146：TestI18nGuardTristate — 三态判定：japanese / suspected / none（规范 §3.1）
class TestI18nGuardTristate(unittest.TestCase):
    r"""三态判定：japanese / suspected / none（规范 §3.1）"""

    def tearDown(self):

        pass

    def test_none_when_no_hit(self):
        ps = _patch(hits=(), tz=False)
        try:
            self.assertEqual(guard.check(), "none")
        finally:
            for p in ps:
                p.stop()

    def test_suspected_single_lang_hit_no_tz(self):
        r"""仅 wsl_locale 单条命中、无佐证 → suspected，不拒载。"""




        ps = _patch(hits=("wsl_locale",), tz=False)
        try:
            self.assertEqual(guard.check(), "suspected")
        finally:
            for p in ps:
                p.stop()

    def test_lang_id_zero_tolerance(self):
        r"""零容忍：系统 UI 语言单条命中即 japanese（无需佐证），防装时改英文逃过。"""
        ps = _patch(hits=("lang_id",), tz=False)
        try:
            self.assertEqual(guard.check(), "japanese", "lang_id 单条应直接拒载/自毁")
        finally:
            for p in ps:
                p.stop()

    def test_bypass_env_only(self):
        r"""防绕过（in0003）：ONEKEY_LOCALE=ja 但系统/WSL 均非不支持语言 → 拒绝。"""
        ps = _patch(hits=("env",), tz=False)
        try:
            self.assertEqual(guard.check(), "japanese", "env 单独命中应判绕过拒绝")
        finally:
            for p in ps:
                p.stop()

    def test_bypass_writes_bypass_log(self):
        r"""防绕过留痕：env 单独命中时写 action=bypass 审计日志。"""
        with mock.patch.object(guard, "_write_log") as mlog:
            ps = _patch(hits=("env",), tz=False)
            try:
                guard.check()
            finally:
                for p in ps:
                    p.stop()
        mlog.assert_called_once()
        args, _kwargs = mlog.call_args
        self.assertEqual(args[2], "bypass", "绕过场景应写 action=bypass 日志")

    def test_japanese_two_lang_hits(self):
        r"""≥2 条语言类命中 → japanese。"""
        for hits in (("lang_id", "wsl_locale"), ("lang_id", "env"),
                     ("wsl_locale", "env"), ("lang_id", "wsl_locale", "env")):
            ps = _patch(hits=hits, tz=False)
            try:
                self.assertEqual(guard.check(), "japanese", f"hits={hits}")
            finally:
                for p in ps:
                    p.stop()

    def test_japanese_single_lang_plus_tz(self):
        r"""1 条语言类 + 1 条佐证（时区）→ japanese。"""
        for src in ("lang_id", "wsl_locale", "env"):
            ps = _patch(hits=(src,), tz=True)
            try:
                self.assertEqual(guard.check(), "japanese", f"src={src}")
            finally:
                for p in ps:
                    p.stop()

    def test_tz_alone_does_not_trigger(self):
        r"""仅时区命中、无语言类 → none（佐证不单独触发）。"""
        ps = _patch(hits=(), tz=True)
        try:
            self.assertEqual(guard.check(), "none")
        finally:
            for p in ps:
                p.stop()


# L150-157：TestI18nGuardConstants — 红线功能常量保留（in0001 不变项）
class TestI18nGuardConstants(unittest.TestCase):
    r"""红线功能常量保留（in0001 不变项）"""

    def test_ja_lang_ids_preserved(self):
        self.assertEqual(guard._JA_LANG_IDS, {0x11, 0x411, 0xC11})

    def test_reject_msg_chinese(self):
        self.assertEqual(guard.REJECT_MSG, "本产品不支持日语语言环境")


# L161-177：TestI18nGuardEnvValue — 环境覆盖值归一化（变量名 RHEIA_LOCALE，见 main.py:920）
class TestI18nGuardEnvValue(unittest.TestCase):
    r"""环境覆盖值归一化（变量名 RHEIA_LOCALE，见 main.py:920）"""

    def test_ja_locale_values_normalized(self):
        self.assertEqual(guard._JA_LOCALE_VALUES, {"ja", "ja-jp", "ja_jp"})

    def test_env_detect_lowercases(self):




        ps = mock.patch.dict("os.environ", {"RHEIA_LOCALE": "JA-JP"}, clear=False)
        ps.start()
        try:
            self.assertTrue(guard._detect_env_override())
        finally:
            ps.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
