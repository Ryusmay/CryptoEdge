import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from paper_trader import Position


def day_position(direction="LONG"):
    return Position({
        "symbol": "BTC", "direction": direction, "price": 100.0,
        "strength": 0.75, "engine": "daytrading", "atr": 1.0,
        "sl_price": 98.5 if direction == "LONG" else 101.5,
        "tp1_price": 102.25 if direction == "LONG" else 97.75,
        "tp2_price": 103.30 if direction == "LONG" else 96.70,
        "tp_price": 103.30 if direction == "LONG" else 96.70,
        "tp_plan": {"frac_tp1": 0.50, "frac_tp2": 0.0, "frac_trail": 0.50},
    }, size_usd=100.0, leverage=3)


class TestDaytradingLifecycle(unittest.TestCase):
    def test_break_even_at_one_r_and_trail_at_tp1(self):
        pos = day_position()
        pos.update_pnl(101.50)
        self.assertGreater(pos.sl_price, pos.entry_price)
        self.assertFalse(pos.trailing_active)
        pos.update_pnl(102.25)
        self.assertTrue(pos.trailing_active)

    def test_tp1_partial_then_tp2_hard_exit(self):
        pos = day_position()
        pos.update_pnl(102.25)
        self.assertEqual(pos.check_tp_sl(102.25), "partial_tp")
        pos.partial_tp1_done = True
        pos.partial_tp2_done = True
        self.assertEqual(pos.check_tp_sl(103.30), "take_profit")

    def test_time_stop_only_for_stale_weak_day_trade(self):
        pos = day_position()
        pos.entry_time = datetime.now() - timedelta(hours=config.DAYTRADING_TIME_STOP_HOURS + 0.1)
        self.assertTrue(pos.daytrading_time_stop_due(100.20))
        self.assertFalse(pos.daytrading_time_stop_due(101.00))

    def test_fill_recalculates_all_targets(self):
        pos = day_position()
        pos.recalculate_after_fill(101.0)
        self.assertAlmostEqual(pos.tp1_price, 103.2725)
        self.assertAlmostEqual(pos.tp2_price, 104.333)
        self.assertAlmostEqual(pos.initial_risk_abs, 1.515)


if __name__ == "__main__":
    unittest.main()
