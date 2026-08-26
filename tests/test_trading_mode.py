# -*- coding: utf-8 -*-
"""Jedno zrodlo prawdy o tym, czy w grze sa prawdziwe pieniadze.

Przed v20.20.0 na to pytanie odpowiadalo szesc kopii tej samej logiki plus
kilka miejsc robiacych `bool(config.PAPER_TRADING)`. Te dwie odpowiedzi
roznia sie dla stringa: bool("false") to True.
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cryptoedge.domain.trading_mode import (
    coerce_paper_flag, is_live, is_paper, live_execution_armed, mode_label,
)

PRODUCTION_FILES = (
    "risk_manager.py", "position_reconciler.py", "paper_trader.py",
    "restart_recovery.py", "runtime.py", "shadow_mode.py", "account_sync.py",
    "cryptoedge/apps/runtime.py",
)


class TestCoercePaperFlag(unittest.TestCase):

    def test_real_booleans_pass_through(self):
        self.assertTrue(coerce_paper_flag(True))
        self.assertFalse(coerce_paper_flag(False))

    def test_live_strings_mean_live(self):
        # To jest sedno bledu: bool("false") == True.
        for token in ("false", "False", " FALSE ", "0", "no", "off", "live", "real"):
            self.assertFalse(coerce_paper_flag(token), token)

    def test_paper_strings_mean_paper(self):
        for token in ("true", "True", "1", "yes", "on", "demo", "paper"):
            self.assertTrue(coerce_paper_flag(token), token)

    def test_unknown_string_falls_back_to_paper(self):
        # Przy niejasnym ustawieniu system ma udawac, nie ryzykowac.
        for token in ("maybe", "", "   ", "zzz"):
            self.assertTrue(coerce_paper_flag(token), token)

    def test_none_uses_the_default(self):
        self.assertTrue(coerce_paper_flag(None))
        self.assertFalse(coerce_paper_flag(None, default=False))

    def test_numbers_behave_like_bool(self):
        self.assertFalse(coerce_paper_flag(0))
        self.assertTrue(coerce_paper_flag(1))


class TestModeQueries(unittest.TestCase):

    def test_is_paper_and_is_live_are_opposites(self):
        for value in (True, False, "true", "false", "demo", "live", None, "zzz"):
            cfg = SimpleNamespace(PAPER_TRADING=value)
            self.assertNotEqual(is_paper(cfg), is_live(cfg), value)

    def test_missing_attribute_means_paper(self):
        self.assertTrue(is_paper(SimpleNamespace()))

    def test_string_false_is_live(self):
        cfg = SimpleNamespace(PAPER_TRADING="false")
        self.assertTrue(is_live(cfg))
        self.assertEqual("LIVE", mode_label(cfg))

    def test_live_execution_needs_both_switches(self):
        self.assertFalse(live_execution_armed(
            SimpleNamespace(PAPER_TRADING=True, LIVE_EXECUTION_ENABLED=True)))
        self.assertFalse(live_execution_armed(
            SimpleNamespace(PAPER_TRADING=False, LIVE_EXECUTION_ENABLED=False)))
        self.assertTrue(live_execution_armed(
            SimpleNamespace(PAPER_TRADING=False, LIVE_EXECUTION_ENABLED=True)))

    def test_missing_live_switch_is_not_armed(self):
        self.assertFalse(live_execution_armed(SimpleNamespace(PAPER_TRADING=False)))


class TestSettingsBoundary(unittest.TestCase):
    """Wartosc z JSON-a musi byc skonwertowana zanim wejdzie do configu."""

    def test_apply_settings_coerces_the_string(self):
        import config
        import settings_store
        original = getattr(config, "PAPER_TRADING", True)
        try:
            settings_store.apply_settings({"PAPER_TRADING": "false"})
            self.assertIs(False, config.PAPER_TRADING)
            settings_store.apply_settings({"PAPER_TRADING": "true"})
            self.assertIs(True, config.PAPER_TRADING)
        finally:
            config.PAPER_TRADING = original


class TestNoDuplicateImplementations(unittest.TestCase):
    """Kopie parsowania nie moga wrocic."""

    def test_the_token_tuple_lives_in_one_file_only(self):
        for name in PRODUCTION_FILES:
            source = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn('"demo", "paper"', source,
                             f"{name} odtworzyl wlasna kopie parsowania")

    def test_no_raw_bool_on_the_flag(self):
        # bool(config.PAPER_TRADING) to wlasnie ten blad - nie wolno go
        # przywrocic w plikach decydujacych o egzekucji.
        for name in PRODUCTION_FILES:
            source = (ROOT / name).read_text(encoding="utf-8")
            for bad in ('bool(getattr(config, "PAPER_TRADING"',
                        'bool(getattr(_cfg, "PAPER_TRADING"',
                        'bool(getattr(cfg, "PAPER_TRADING"'):
                self.assertNotIn(bad, source, f"{name}: {bad}")

    def test_every_touched_file_asks_the_module(self):
        for name in PRODUCTION_FILES:
            source = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("trading_mode", source, name)


if __name__ == "__main__":
    unittest.main()
