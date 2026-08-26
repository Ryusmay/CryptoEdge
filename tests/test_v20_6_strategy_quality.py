import tempfile
import unittest
from unittest.mock import patch

import config
from day_expectancy_calibration import DayExpectancyCalibrator
from expected_net_r import expected_net_r
from setup_quality import (
    candle_rejection_features, probability_quality_multiplier,
    rsi_structure_features, structure_aware_target,
)


class CandleQualityTests(unittest.TestCase):
    def test_long_rejection_scores_better_than_bearish_acceptance(self):
        base = {"opens": [100.0] * 20, "highs": [101.0] * 20,
                "lows": [99.0] * 20, "closes": [100.0] * 20,
                "volumes": [100.0] * 20}
        rejection = {k: list(v) for k, v in base.items()}
        rejection["opens"][-1], rejection["highs"][-1] = 99.4, 101.0
        rejection["lows"][-1], rejection["closes"][-1] = 97.9, 100.7
        rejection["volumes"][-1] = 160.0
        acceptance = {k: list(v) for k, v in base.items()}
        acceptance["opens"][-1], acceptance["highs"][-1] = 100.5, 100.7
        acceptance["lows"][-1], acceptance["closes"][-1] = 98.0, 98.2
        good = candle_rejection_features(rejection, "LONG", 98.0, 99.0)
        bad = candle_rejection_features(acceptance, "LONG", 98.0, 99.0)
        self.assertGreater(good["score"], bad["score"])
        self.assertEqual(good["touch_age"], 0)

    def test_rsi_divergence_uses_only_confirmed_pivots(self):
        n = 30
        lows = [110.0 + i * 0.01 for i in range(n)]
        lows[8], lows[18] = 100.0, 98.0
        frame = {"closes": [105.0] * n, "highs": [106.0] * n, "lows": lows}
        series = [None] * 14 + [50.0] * (n - 14)
        series[8] = 25.0
        series[18] = 32.0
        with patch("indicators_full._rsi_series", return_value=series):
            out = rsi_structure_features(frame, "LONG", left=2, right=2)
        self.assertTrue(out["divergence"])
        self.assertEqual(out["pivot_indices"], [8, 18])


class StructureAwareTargetTests(unittest.TestCase):
    def test_tp_is_placed_before_reachable_resistance(self):
        target, audit = structure_aware_target(
            100.0, 110.0, 2.0, "LONG", obstacle=106.0,
            atr=1.0, buffer_atr=0.15, min_r=0.6,
        )
        self.assertAlmostEqual(target, 105.85)
        self.assertTrue(audit["tp1_capped"])
        self.assertAlmostEqual(audit["obstacle_r"], 3.0)

    def test_too_close_obstacle_reduces_clearance_without_forcing_bad_tp(self):
        target, audit = structure_aware_target(
            100.0, 104.0, 2.0, "LONG", obstacle=100.5,
            atr=1.0, buffer_atr=0.15, min_r=0.6,
        )
        self.assertEqual(target, 104.0)
        self.assertFalse(audit["tp1_capped"])
        self.assertLess(audit["clearance"], 0.2)


class V2ExpectancyTests(unittest.TestCase):
    def test_v2_uses_probability_weighted_day_model(self):
        signal = {
            "engine": "daytrading_v2", "strategy_mode": "DAYTRADING_V2",
            "direction": "LONG", "price": 100.0, "sl_price": 98.0,
            "tp_plan": {"tp1_r": 1.5, "tp2_r": 3.0, "frac_tp1": 0.5},
            "setup_probability_multiplier": 0.8,
            "day_expectancy_calibration": {"n": 0},
            "order_book": {"ob_spread_pct": 0}, "slip_rt": 0,
        }
        with patch.object(config, "TAKER_FEE", 0), patch.object(config, "DEFAULT_IMPACT_FRAC", 0):
            out = expected_net_r(signal)
        self.assertEqual(out["engine"], "daytrading")
        self.assertEqual(out["calibration_status"], "PRIOR_ONLY")
        self.assertAlmostEqual(out["probabilities"]["p_tp1"], config.DAYTRADING_PRIOR_P_TP1 * 0.8)
        self.assertLess(out["gross_r"], signal["net_reward_potential_r"] if "net_reward_potential_r" in signal else 1.5)

    def test_calibrator_separates_major_and_alt(self):
        with tempfile.TemporaryDirectory() as td:
            calibrator = DayExpectancyCalibrator(f"{td}/cal.json")
            calibrator.record(True, True, profile="major")
            calibrator.record(False, False, profile="alt")
            self.assertEqual(calibrator.snapshot(profile="MAJOR")["p_tp1"], 1.0)
            self.assertEqual(calibrator.snapshot(profile="ALT")["p_tp1"], 0.0)

    def test_probability_modifier_is_bounded(self):
        high = probability_quality_multiplier(
            {"score": 1.0}, {"divergence": True, "failure_swing": True}, 1.0,
        )
        low = probability_quality_multiplier({"score": 0.0}, {}, 0.0)
        self.assertLessEqual(high, 1.10)
        self.assertGreaterEqual(low, 0.65)


if __name__ == "__main__":
    unittest.main()
