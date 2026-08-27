# -*- coding: utf-8 -*-
"""Pasmo ryzyka, skalowanie sila i mnozniki - cryptoedge.risk.sizing."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cryptoedge.risk import sizing


def cfg(**kw):
    base = {
        "DAYTRADING_RISK_PCT_MIN": 0.010, "DAYTRADING_RISK_PCT_MAX": 0.018,
        "DAYTRADING_RISK_PCT_DEFAULT": 0.014,
        "REVERSAL_RISK_PCT_MIN": 0.0030, "REVERSAL_RISK_PCT_MAX": 0.0055,
        "REVERSAL_RISK_PCT_DEFAULT": 0.0040,
        "RISK_PCT_MIN": 0.0060, "RISK_PCT_MAX": 0.0090,
        "RISK_PCT_DEFAULT": 0.0075,
        "SIZE_STRENGTH_FLOOR": 0.48, "SIZE_STRENGTH_CAP": 1.0,
        "MIN_SIGNAL_STRENGTH": 0.48, "REVERSAL_MIN_STRENGTH": 0.32,
        "REGIME_RANGE_SIZE_MULT": 0.50, "REGIME_PANIC_TREND_SIZE_MULT": 1.0,
        "REGIME_PANIC_SIZE_MULT": 1.0, "PROXY_4H_RISK_MULT": 0.70,
        "DEGRADED_1D_RISK_MULT": 0.75,
        "UNCALIBRATED_EXPECTED_R_SIZE_MULT": 0.65,
        "EXTREME_VOL_ATR_PCTILE": 85.0, "EXTREME_VOL_RISK_MULT": 0.50,
    }
    base.update(kw)
    return SimpleNamespace(**base)


class TestRiskBand(unittest.TestCase):

    def test_band_per_engine(self):
        self.assertEqual((0.010, 0.018, 0.014),
                         sizing.risk_band(True, False, cfg()))
        self.assertEqual((0.0030, 0.0055, 0.0040),
                         sizing.risk_band(False, True, cfg()))
        self.assertEqual((0.0060, 0.0090, 0.0075),
                         sizing.risk_band(False, False, cfg()))

    def test_daytrading_wins_over_reversal(self):
        """Sygnal jednoczesnie daytrading i reversal bierze pasmo dnia."""
        self.assertEqual(sizing.risk_band(True, False, cfg()),
                         sizing.risk_band(True, True, cfg()))

    def test_broken_config_value_falls_back(self):
        band = sizing.risk_band(False, False, cfg(RISK_PCT_MIN="duzo"))
        self.assertEqual(0.0050, band[0])


class TestScaleByStrength(unittest.TestCase):

    def band(self, is_rev=False, c=None):
        return sizing.risk_band(False, is_rev, c or cfg())

    def test_floor_gives_minimum(self):
        self.assertAlmostEqual(0.0060, sizing.scale_by_strength(0.48, False,
                                                                self.band(), cfg()))

    def test_cap_gives_maximum(self):
        self.assertAlmostEqual(0.0090, sizing.scale_by_strength(1.0, False,
                                                                self.band(), cfg()))

    def test_below_floor_is_clamped(self):
        self.assertAlmostEqual(0.0060, sizing.scale_by_strength(0.10, False,
                                                                self.band(), cfg()))

    def test_above_cap_is_clamped(self):
        self.assertAlmostEqual(0.0090, sizing.scale_by_strength(5.0, False,
                                                                self.band(), cfg()))

    def test_midpoint(self):
        got = sizing.scale_by_strength(0.74, False, self.band(), cfg())
        self.assertAlmostEqual(0.0075, got, places=5)

    def test_reversal_uses_its_own_lower_floor(self):
        """Reversal ma prog 0.32, wiec ta sama sila daje mu wieksze ryzyko."""
        c = cfg()
        rev = sizing.scale_by_strength(0.60, True, self.band(True, c), c)
        self.assertGreater(rev, sizing.risk_band(False, True, c)[0])
        self.assertEqual(0.32, sizing.strength_floor(True, c))
        self.assertEqual(0.48, sizing.strength_floor(False, c))

    def test_degenerate_scale_gives_full_risk(self):
        c = cfg(SIZE_STRENGTH_CAP=0.20)
        self.assertAlmostEqual(0.0090, sizing.scale_by_strength(0.50, False,
                                                                self.band(False, c), c))


class TestRiskMultipliers(unittest.TestCase):

    def mult(self, signal=None, regime="TREND_UP", **kw):
        opts = {"is_rev": False, "is_day": False, "is_v2": False}
        opts.update(kw)
        return sizing.risk_multipliers(signal or {}, regime, cfg=cfg(), **opts)

    def test_clean_signal_has_no_reduction(self):
        self.assertEqual((1.0, {}), self.mult())

    def test_range_halves_risk(self):
        self.assertAlmostEqual(0.50, self.mult(regime="RANGE")[0])

    def test_panic_only_for_plain_trend(self):
        """PANIC omija reversal, daytrading i V2 - i przy domyslnym configu
        mnoznik i tak wynosi 1.0, wiec galaz jest bezczynna liczbowo."""
        self.assertAlmostEqual(1.0, self.mult(regime="PANIC")[0])
        self.assertAlmostEqual(1.0, self.mult(regime="PANIC", is_rev=True)[0])

    def test_panic_multiplier_when_configured(self):
        got, _ = sizing.risk_multipliers({}, "PANIC", is_rev=False, is_day=False,
                                         is_v2=False,
                                         cfg=cfg(REGIME_PANIC_TREND_SIZE_MULT=0.4))
        self.assertAlmostEqual(0.4, got)

    def test_panic_falls_back_to_generic_mult(self):
        got, _ = sizing.risk_multipliers({}, "PANIC", is_rev=False, is_day=False,
                                         is_v2=False,
                                         cfg=cfg(REGIME_PANIC_TREND_SIZE_MULT=None,
                                                 REGIME_PANIC_SIZE_MULT=0.3))
        self.assertAlmostEqual(0.3, got)

    def test_proxy_4h_both_spellings(self):
        self.assertAlmostEqual(0.70, self.mult({"ohlcv_source": "proxy_4h"})[0])
        self.assertAlmostEqual(0.70, self.mult({"proxy_4h": True})[0])

    def test_degraded_1d(self):
        self.assertAlmostEqual(0.75, self.mult({"degraded_1d": True})[0])

    def test_cross_market_multiplier_is_unclamped(self):
        """Wartosc idzie wprost z sygnalu, bez klamry - moze ZWIEKSZYC ryzyko."""
        self.assertAlmostEqual(0.5, self.mult({"cross_market_risk_mult": 0.5})[0])
        self.assertAlmostEqual(5.0, self.mult({"cross_market_risk_mult": 5.0})[0])
        self.assertAlmostEqual(1.0, self.mult({"cross_market_risk_mult": 0})[0],
                               msg="zero jest falsy - ignorowane, nie zeruje")

    def test_uncalibrated_expected_r_marks_the_signal(self):
        got, stamps = self.mult({"expected_r_status": "prior_only"})
        self.assertAlmostEqual(0.65, got)
        self.assertEqual({"_uncalibrated_expected_r": True}, stamps)

    def test_uncalibrated_skipped_for_daytrading(self):
        got, stamps = self.mult({"expected_r_status": "LOW_SAMPLE"}, is_day=True)
        self.assertAlmostEqual(1.0, got)
        self.assertEqual({}, stamps)

    def test_extreme_vol_from_signal_and_from_detail(self):
        self.assertAlmostEqual(0.50, self.mult({"atr_percentile": 90})[0])
        self.assertAlmostEqual(0.50, self.mult(
            {"market_regime_detail": {"atr_percentile": 85}})[0])
        self.assertAlmostEqual(1.0, self.mult({"atr_percentile": 84.9})[0])

    def test_extreme_vol_from_last_regime_detail(self):
        got, _ = sizing.risk_multipliers({}, "TREND_UP", is_rev=False,
                                         is_day=False, is_v2=False,
                                         last_regime_detail={"atr_percentile": 99},
                                         cfg=cfg())
        self.assertAlmostEqual(0.50, got)

    def test_bad_percentile_does_not_apply_penalty(self):
        self.assertAlmostEqual(1.0, self.mult({"atr_percentile": "wysoko"})[0])

    def test_multipliers_compose(self):
        got, _ = self.mult({"degraded_1d": True, "atr_percentile": 90},
                           regime="RANGE")
        self.assertAlmostEqual(0.50 * 0.75 * 0.50, got)

    def test_module_does_not_write_to_the_signal(self):
        """Sedno wydzielenia: modul zwraca znaczniki, nie wbija ich sam."""
        s = {"expected_r_status": "PRIOR_ONLY", "degraded_1d": True}
        before = dict(s)
        sizing.risk_multipliers(s, "RANGE", is_rev=False, is_day=False,
                                is_v2=False, cfg=cfg())
        self.assertEqual(before, s)


class TestNoDuplicateImplementations(unittest.TestCase):

    def setUp(self):
        self.source = (ROOT / "risk_manager.py").read_text(encoding="utf-8")

    def test_multiplier_literals_are_gone(self):
        for literal in ('"REGIME_RANGE_SIZE_MULT"', '"PROXY_4H_RISK_MULT"',
                        '"DEGRADED_1D_RISK_MULT"', '"EXTREME_VOL_ATR_PCTILE"',
                        '"UNCALIBRATED_EXPECTED_R_SIZE_MULT"',
                        '"DAYTRADING_RISK_PCT_MIN"', '"REVERSAL_RISK_PCT_MIN"'):
            self.assertNotIn(literal, self.source,
                             f"risk_manager odtworzyl wlasna kopie: {literal}")

    def test_manager_delegates(self):
        for call in ("risk_sizing.risk_band", "risk_sizing.scale_by_strength",
                     "risk_sizing.risk_multipliers"):
            self.assertIn(call, self.source)


if __name__ == "__main__":
    unittest.main()


class TestNotionalCap(unittest.TestCase):

    def caps(self, **kw):
        base = dict(regime="TREND_UP", open_positions_count=0, positions=None,
                    capital_pct_mode=False, cfg=cfg(
                        MAX_POSITIONS=10, REGIME_RANGE_MAX_POSITIONS=5,
                        CAPITAL_RESERVE_PCT=0.20, MAX_NOTIONAL_EQUITY_FRAC=2.0,
                        MAX_POSITION_MARGIN_EQUITY_FRAC=0.12,
                        DAYTRADING_V2_MARGIN_PCT_MAX=10.0))
        base.update(kw)
        return sizing.notional_cap(1000.0, 10.0, **base)

    def test_slot_allocation_is_the_binding_cap_at_defaults(self):
        cap, _ = self.caps()
        self.assertAlmostEqual(800.0, cap)

    def loose(self, **kw):
        """Sufity marginu i globalny rozluznione, zeby widziec sam efekt slotow.
        Przy domyslnych to one wiaza pierwsze i przykrywaja alokacje."""
        base = dict(MAX_POSITIONS=10, REGIME_RANGE_MAX_POSITIONS=5,
                    CAPITAL_RESERVE_PCT=0.20, MAX_NOTIONAL_EQUITY_FRAC=99.0,
                    MAX_POSITION_MARGIN_EQUITY_FRAC=1.0,
                    DAYTRADING_V2_MARGIN_PCT_MAX=10.0)
        return self.caps(cfg=cfg(**base), **kw)

    def test_fewer_free_slots_means_bigger_slot(self):
        wide, _ = self.loose(open_positions_count=0)
        tight, _ = self.loose(open_positions_count=9)
        self.assertAlmostEqual(800.0, wide)
        self.assertAlmostEqual(8000.0, tight)

    def test_used_margin_shrinks_free_equity(self):
        cap, _ = self.loose(positions=[{"margin": 400.0}])
        self.assertAlmostEqual(400.0, cap)

    def test_range_narrows_slots(self):
        wide, _ = self.loose()
        narrow, _ = self.loose(regime="RANGE")
        self.assertAlmostEqual(800.0, wide)
        self.assertAlmostEqual(1600.0, narrow)

    def test_margin_cap_hides_slot_effect_at_defaults(self):
        """Warte zapamietania: przy MAX_POSITION_MARGIN_EQUITY_FRAC=0.12 i x10
        sufit marginu (1.2x equity) wiaze wczesniej niz alokacja na slot, wiec
        zwolnienie slotow nie zwieksza rozmiaru tak, jak by sie wydawalo."""
        self.assertAlmostEqual(1200.0, self.caps(open_positions_count=9)[0])
        self.assertAlmostEqual(1200.0, self.caps(regime="RANGE")[0])

    def test_capital_pct_mode_skips_slot_division(self):
        cap, _ = self.caps(capital_pct_mode=True)
        self.assertAlmostEqual(1000.0, cap)

    def test_margin_frac_cap_binds_when_tight(self):
        cap, _ = self.caps(cfg=cfg(MAX_POSITIONS=1, CAPITAL_RESERVE_PCT=0.0,
                                   MAX_NOTIONAL_EQUITY_FRAC=99.0,
                                   MAX_POSITION_MARGIN_EQUITY_FRAC=0.05,
                                   DAYTRADING_V2_MARGIN_PCT_MAX=10.0))
        self.assertAlmostEqual(500.0, cap)

    def test_stamp_is_returned_not_written(self):
        _, stamps = self.caps()
        self.assertEqual({"_max_position_margin_pct": 12.0}, stamps)

    def test_overfull_book_still_leaves_one_slot(self):
        """max(1, ...) - ksiega ponad limit nie daje zera, tylko jeden slot."""
        cap, _ = self.caps(open_positions_count=99)
        self.assertGreater(cap, 0.0)


class TestUsedMargin(unittest.TestCase):

    def test_objects_and_dicts(self):
        obj = SimpleNamespace(margin=100.0)
        self.assertAlmostEqual(150.0, sizing.used_margin([obj, {"margin": 50.0}]))

    def test_empty_and_none(self):
        self.assertEqual(0.0, sizing.used_margin([]))
        self.assertEqual(0.0, sizing.used_margin(None))

    def test_broken_entry_is_skipped_not_fatal(self):
        """Zachowane zastane: zle dane daja MNIEJSZY zajety margin, czyli
        WIEKSZA pozycje. Awaria w strone ryzyka - udokumentowana."""
        self.assertAlmostEqual(50.0, sizing.used_margin(
            [{"margin": "duzo"}, {"margin": 50.0}]))


class TestSlotsDivergence(unittest.TestCase):
    """PANIC: bramka wejscia zaciska sufit, sizing nie. Kopie tej samej
    reguly, ktore moga sie rozjechac - przypiete swiadomie."""

    def test_range_matches_entry_gate(self):
        from cryptoedge.risk import limits
        c = cfg(MAX_POSITIONS=10, REGIME_RANGE_MAX_POSITIONS=5,
                REGIME_PANIC_MAX_POSITIONS=10)
        self.assertEqual(5, limits.max_positions_for_regime("RANGE", c))
        self.assertEqual(5, sizing.slots_for_sizing("RANGE", 0, c))

    def test_panic_diverges_when_config_differs(self):
        from cryptoedge.risk import limits
        c = cfg(MAX_POSITIONS=10, REGIME_RANGE_MAX_POSITIONS=5,
                REGIME_PANIC_MAX_POSITIONS=3)
        self.assertEqual(3, limits.max_positions_for_regime("PANIC", c),
                         "bramka wejscia zaciska sloty w PANIC")
        self.assertEqual(10, sizing.slots_for_sizing("PANIC", 0, c),
                         "sizing tego nie robi - rozbieznosc jest zastana")

    def test_panic_agrees_at_shipped_defaults(self):
        from cryptoedge.risk import limits
        c = cfg(MAX_POSITIONS=10, REGIME_RANGE_MAX_POSITIONS=10,
                REGIME_PANIC_MAX_POSITIONS=10)
        self.assertEqual(limits.max_positions_for_regime("PANIC", c),
                         sizing.slots_for_sizing("PANIC", 0, c))
