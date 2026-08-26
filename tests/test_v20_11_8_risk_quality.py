import unittest
from unittest.mock import patch
from pathlib import Path

import config
from expected_net_r import _is_daytrading, net_r_ok
from portfolio_risk import compute_open_risk
from risk_manager import RiskManager


class TestRiskQualityHardening(unittest.TestCase):
    def test_low_sample_never_soft_passes_non_positive_net_r(self):
        signal = {
            "engine": "daytrading_v2", "strategy_mode": "DAYTRADING_V2",
            "direction": "LONG", "expected_r_status": "LOW_SAMPLE",
        }
        breakdown = {
            "net_r": -0.01, "calibration_status": "LOW_SAMPLE",
            "calibration_n": 0,
        }
        with patch("expected_net_r.expected_net_r", return_value=breakdown):
            ok, reason = net_r_ok(signal)
        self.assertFalse(ok)
        self.assertTrue(reason.startswith("NON_POSITIVE_NET_R"))

    def test_prior_only_requires_positive_safety_margin(self):
        signal = {
            "engine": "daytrading_v2", "strategy_mode": "DAYTRADING_V2",
            "direction": "LONG", "expected_r_status": "PRIOR_ONLY",
        }
        breakdown = {
            "net_r": 0.05, "calibration_status": "PRIOR_ONLY",
            "calibration_n": 0,
        }
        with patch("expected_net_r.expected_net_r", return_value=breakdown):
            ok, reason = net_r_ok(signal)
        self.assertFalse(ok)
        self.assertTrue(reason.startswith("DAY_PRIOR_NET_R_LOW"))

    def test_reversal_inside_v2_is_not_daytrading_soft_pass(self):
        signal = {
            "engine": "reversal", "strategy_mode": "DAYTRADING_V2",
            "setup": "reversal_confirmed", "direction": "SHORT",
            "price": 100.0, "sl_price": 100.2,
            "rr_tp1": 1.0, "rr_tp2": 2.0,
            "reversal_score": 0.35, "strength": 0.35,
            "tp_plan": {"frac_tp1": .25, "frac_tp2": .35, "frac_trail": .40},
        }
        self.assertFalse(_is_daytrading(signal))
        ok, reason = net_r_ok(signal)
        self.assertFalse(ok)
        self.assertIn("NET_R", reason)

    def test_reversal_quality_defaults_are_hard(self):
        self.assertTrue(config.REVERSAL_REQUIRE_QUALITY_TRIAD)
        self.assertGreaterEqual(config.REVERSAL_MIN_CONFIRMATIONS, 2)
        self.assertTrue(config.REVERSAL_PAPER_REQUIRE_NET_R)

    def test_projected_daily_loss_blocks_overshoot(self):
        risk = RiskManager(100.0)
        risk.daily_pnl = -3.8
        signal = {
            "symbol": "BTC", "direction": "LONG", "engine": "reversal",
            "price": 100.0, "sl_price": 99.0, "strength": 0.8,
            "_planned_notional": 50.0,
        }
        ok, reason = risk.can_open_position(signal)
        self.assertFalse(ok)
        self.assertTrue(reason.startswith("DAILY_PROJECTED_LOSS"))

    def test_open_risk_reads_position_native_fields(self):
        class Pos:
            def __init__(self):
                self.symbol = "BTC"
                self.direction = "LONG"
                self.entry_price = 100.0
                self.sl_price = 98.0
                self.size_usd = 50.0
                self.actual_risk_usd = 1.0
        out = compute_open_risk([Pos()], 100.0)
        self.assertEqual(out["positions_without_stop_risk"], 0)
        self.assertAlmostEqual(out["total_usd"], 1.0)

    def test_runtime_is_only_open_event_writer(self):
        root = Path(__file__).resolve().parents[1]
        app = (root / "app.py").read_text(encoding="utf-8")
        trader = (root / "paper_trader.py").read_text(encoding="utf-8")
        self.assertNotIn('logger.log_event(\n                            "OPEN"', app)
        self.assertIn('"OPEN", pos.symbol', trader)

    def test_api_exposes_effective_halt_state(self):
        src = (Path(__file__).resolve().parents[1] / "engine_api.py").read_text(encoding="utf-8")
        self.assertIn('effective_trading', src)
        self.assertIn('"HALTED" if risk_halted', src)


if __name__ == "__main__":
    unittest.main()
