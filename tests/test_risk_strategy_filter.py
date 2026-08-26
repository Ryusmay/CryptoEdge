# -*- coding: utf-8 -*-
"""Filtr strategii primary (4h) i fallback MTF - cryptoedge.risk.

Brzmienie powodow jest kontraktem: trafia do reject_log i na ekran.
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cryptoedge.risk import strategy_filter as sf


def cfg(**kw):
    base = {
        "MTF_MIN_VOTES_FALLBACK": 2,
        "MIN_SIGNAL_STRENGTH": 0.48,
        "STRAT_NA_RANGE_MIN_STRENGTH": 0.68,
        "BLOCK_STRAT_NA_IN_RANGE": True,
        "REQUIRE_PRIMARY_STRATEGY": True,
        "AGGRESSIVE_MODE": False,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def sig(**kw):
    base = {"direction": "LONG", "engine": "trend", "strategy_mode": "SWING"}
    base.update(kw)
    return base


class TestMtfMajority(unittest.TestCase):

    def test_enough_votes_in_the_signal_direction(self):
        self.assertTrue(sf.mtf_majority("LONG", sig(mtf={"long_votes": 2}), cfg()))
        self.assertTrue(sf.mtf_majority("SHORT", sig(mtf={"short_votes": 3}), cfg()))

    def test_votes_below_minimum(self):
        self.assertFalse(sf.mtf_majority("LONG", sig(mtf={"long_votes": 1}), cfg()))

    def test_votes_for_the_other_direction_do_not_count(self):
        self.assertFalse(sf.mtf_majority("LONG", sig(mtf={"short_votes": 5}), cfg()))

    def test_missing_mtf_section_is_zero_votes(self):
        self.assertFalse(sf.mtf_majority("LONG", sig(), cfg()))
        self.assertFalse(sf.mtf_majority("LONG", sig(mtf=None), cfg()))

    def test_lowercase_direction_gets_no_majority(self):
        # Zachowane swiadomie - kierunek czytany surowo, jak od zawsze.
        self.assertFalse(sf.mtf_majority("long", sig(mtf={"long_votes": 5}), cfg()))

    def test_threshold_comes_from_config(self):
        s = sig(mtf={"long_votes": 3})
        self.assertFalse(sf.mtf_majority("LONG", s, cfg(MTF_MIN_VOTES_FALLBACK=4)))
        self.assertTrue(sf.mtf_majority("LONG", s, cfg(MTF_MIN_VOTES_FALLBACK=3)))

    def test_zero_threshold_falls_back_to_two(self):
        self.assertFalse(sf.mtf_majority("LONG", sig(mtf={"long_votes": 1}),
                                         cfg(MTF_MIN_VOTES_FALLBACK=0)))

    def test_votes_of(self):
        self.assertEqual((2, 3), sf.votes_of(sig(mtf={"long_votes": 2, "short_votes": 3})))
        self.assertEqual((0, 0), sf.votes_of(sig()))
        self.assertEqual((0, 0), sf.votes_of(None))


class TestSoftAlign(unittest.TestCase):

    def test_each_marker_counts(self):
        for marker in sf.SOFT_ALIGN_MARKERS:
            self.assertTrue(sf.has_soft_align([marker]), marker)

    def test_unknown_marker_does_not(self):
        self.assertFalse(sf.has_soft_align(["SOMETHING_ELSE"]))

    def test_empty_and_none(self):
        self.assertFalse(sf.has_soft_align([]))
        self.assertFalse(sf.has_soft_align(None))

    def test_marker_among_others(self):
        self.assertTrue(sf.has_soft_align(["A", "PRIMARY_SOFT_PASS", "B"]))


class TestEngineRecognition(unittest.TestCase):

    def test_v2_by_engine_or_mode(self):
        self.assertTrue(sf.is_v2(sig(engine="daytrading_v2")))
        self.assertTrue(sf.is_v2(sig(engine="daytradingv2")))
        self.assertTrue(sf.is_v2(sig(engine="trend", strategy_mode="DAYTRADING_V2")))
        self.assertFalse(sf.is_v2(sig()))

    def test_daytrading_is_not_v2(self):
        self.assertTrue(sf.is_daytrading(sig(engine="daytrading", strategy_mode="DAYTRADING")))
        self.assertFalse(sf.is_daytrading(sig(engine="daytrading_v2",
                                              strategy_mode="DAYTRADING_V2")))

    def test_primary_filter_skips_v2_and_day(self):
        self.assertTrue(sf.primary_filter_applies(sig(), cfg()))
        self.assertFalse(sf.primary_filter_applies(sig(strategy_mode="DAYTRADING_V2"), cfg()))
        self.assertFalse(sf.primary_filter_applies(sig(engine="daytrading",
                                                       strategy_mode="DAYTRADING"), cfg()))

    def test_aggressive_mode_disables_the_filter(self):
        self.assertFalse(sf.primary_filter_applies(sig(), cfg(AGGRESSIVE_MODE=True)))

    def test_requirement_switch_disables_the_filter(self):
        self.assertFalse(sf.primary_filter_applies(sig(), cfg(REQUIRE_PRIMARY_STRATEGY=False)))


class TestDaySetup(unittest.TestCase):

    def test_confirmed_and_native_passes(self):
        for setup in sf.CONFIRMED_DAY_SETUPS:
            ok, why = sf.day_setup_ok(sig(setup=setup, signal_source="BLOFIN_NATIVE"))
            self.assertEqual((True, "OK"), (ok, why), setup)

    def test_missing_or_unknown_setup(self):
        self.assertEqual((False, "DAY_SETUP_NOT_CONFIRMED"), sf.day_setup_ok(sig()))
        self.assertEqual((False, "DAY_SETUP_NOT_CONFIRMED"),
                         sf.day_setup_ok(sig(setup="cos_innego")))

    def test_confirmed_but_foreign_source(self):
        self.assertEqual((False, "DAY_NON_NATIVE_SOURCE"),
                         sf.day_setup_ok(sig(setup="intraday_confirmed")))

    def test_setup_is_checked_before_source(self):
        self.assertEqual((False, "DAY_SETUP_NOT_CONFIRMED"),
                         sf.day_setup_ok(sig(signal_source="OBCE")))


class TestStratNaVerdict(unittest.TestCase):

    def verdict(self, strength, regime="TREND_UP", mtf_ok=False, lv=0, sv=0, **kw):
        return sf.strat_na_verdict(strength, regime, mtf_ok, lv, sv, cfg(**kw))

    def test_mtf_majority_lets_a_strong_signal_through(self):
        self.assertEqual((True, "OK"), self.verdict(0.60, mtf_ok=True))

    def test_no_mtf_needs_min_plus_margin(self):
        ok, why = self.verdict(0.52)
        self.assertFalse(ok)
        self.assertEqual("STRAT_NA_NO_MTF(L0/S0<2)", why)

    def test_no_mtf_above_margin_passes(self):
        self.assertEqual((True, "OK"), self.verdict(0.56))

    def test_vote_counts_appear_in_the_reason(self):
        ok, why = self.verdict(0.50, lv=1, sv=3)
        self.assertEqual("STRAT_NA_NO_MTF(L1/S3<2)", why)

    def test_range_uses_its_own_threshold(self):
        ok, why = self.verdict(0.60, regime="RANGE")
        self.assertFalse(ok)
        self.assertEqual("STRAT_NA_RANGE_WEAK(0.60<0.68)", why)

    def test_range_at_threshold_passes(self):
        self.assertEqual((True, "OK"), self.verdict(0.68, regime="RANGE"))

    def test_range_block_can_be_switched_off(self):
        # Bez blokady RANGE wpada w zwykla galaz sily.
        ok, why = self.verdict(0.60, regime="RANGE", BLOCK_STRAT_NA_IN_RANGE=False)
        self.assertEqual((True, "OK"), (ok, why))

    def test_final_strength_check_runs_even_after_mtf(self):
        """Kontrola `strength < min_str` jest bezwarunkowa.

        To ona lapie sygnaly reversal, ktore przeszly wlasny nizszy prog
        sily. Gdyby byla czescia lancucha elif, STRAT_NA_WEAK nigdy by nie
        padlo przy wiekszosci MTF.
        """
        ok, why = self.verdict(0.40, mtf_ok=True)
        self.assertFalse(ok)
        self.assertEqual("STRAT_NA_WEAK", why)

    def test_range_without_block_falls_into_the_ordinary_branch(self):
        # Wylaczona blokada RANGE nie przepuszcza slabego sygnalu - zdejmuje
        # tylko prog 0.68 i oddaje sprawe zwyklej galezi MIN+0.08.
        ok, why = self.verdict(0.40, regime="RANGE", BLOCK_STRAT_NA_IN_RANGE=False)
        self.assertEqual((False, "STRAT_NA_NO_MTF(L0/S0<2)"), (ok, why))

    def test_weak_is_reachable_only_through_mtf_majority(self):
        """STRAT_NA_WEAK ma dokladnie jedna droge dojscia.

        Bez MTF slaby sygnal wpada wczesniej w STRAT_NA_NO_MTF (prog
        MIN+0.08) albo w STRAT_NA_RANGE_WEAK (prog 0.68) - oba wyzsze niz
        MIN_SIGNAL_STRENGTH. Dopiero wiekszosc MTF przeskakuje te progi
        i oddaje sygnal bezwarunkowej kontroli na koncu.
        """
        self.assertEqual((False, "STRAT_NA_WEAK"), self.verdict(0.40, mtf_ok=True))
        self.assertEqual((False, "STRAT_NA_NO_MTF(L0/S0<2)"), self.verdict(0.40))
        self.assertEqual((False, "STRAT_NA_RANGE_WEAK(0.40<0.68)"),
                         self.verdict(0.40, regime="RANGE"))

    def test_none_strength_is_zero(self):
        ok, why = self.verdict(None, mtf_ok=True)
        self.assertEqual((False, "STRAT_NA_WEAK"), (ok, why))


class TestNoDuplicateImplementations(unittest.TestCase):
    """Kopie regul nie moga wrocic do risk_manager."""

    def setUp(self):
        self.source = (ROOT / "risk_manager.py").read_text(encoding="utf-8")

    def test_reason_strings_live_only_in_the_module(self):
        for literal in ('"DAY_SETUP_NOT_CONFIRMED"', '"DAY_NON_NATIVE_SOURCE"',
                        '"STRAT_NA_WEAK"', 'f"STRAT_NA_RANGE_WEAK(',
                        'f"STRAT_NA_NO_MTF('):
            self.assertNotIn(literal, self.source,
                             f"risk_manager odtworzyl wlasna kopie: {literal}")

    def test_markers_and_setups_are_not_duplicated(self):
        for literal in ('"PRIMARY_MTF_FALLBACK"', '"intraday_5m_confirmed"',
                        '"BLOFIN_NATIVE"', '"MTF_MIN_VOTES_FALLBACK"'):
            self.assertNotIn(literal, self.source,
                             f"risk_manager odtworzyl wlasna kopie: {literal}")

    def test_manager_delegates(self):
        self.assertIn("from cryptoedge.risk import strategy_filter as strat_filter",
                      self.source)
        for call in ("strat_filter.votes_of", "strat_filter.mtf_majority",
                     "strat_filter.has_soft_align", "strat_filter.is_daytrading",
                     "strat_filter.day_setup_ok", "strat_filter.primary_filter_applies",
                     "strat_filter.strat_na_verdict"):
            self.assertIn(call, self.source)

    def test_size_mult_branches_stay_in_the_manager_for_now(self):
        """Mutacja sygnalu jeszcze nie przeniesiona - swiadomie."""
        self.assertIn('signal["_size_mult"] = min(float(signal.get("_size_mult") or 1.0), 0.6)',
                      self.source)
        self.assertIn('signal["_size_mult"] = min(float(signal.get("_size_mult") or 1.0), 0.5)',
                      self.source)


if __name__ == "__main__":
    unittest.main()
