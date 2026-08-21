import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from event_backtester import BTPosition, EventBacktester, window_until
from expected_net_r import expected_net_r
from walk_forward import _slice_bundle_with_warmup


class TestClosedBarCausality(unittest.TestCase):
    def test_higher_timeframe_bar_is_hidden_until_close(self):
        hour = 3_600_000
        ohlcv = {
            "ts": [0, 4 * hour, 8 * hour],
            "opens": [10, 20, 30], "highs": [11, 21, 31],
            "lows": [9, 19, 29], "closes": [10, 20, 30],
            "volumes": [1, 1, 1],
        }
        # At 05:00 the bar opened at 04:00 is not a known 4h candle yet.
        win = window_until(ohlcv, 5 * hour, tf="4h")
        self.assertEqual(win, {})
        # At 08:00 both bars opened at 00:00 and 04:00 are now closed.
        win = window_until(ohlcv, 8 * hour, tf="4h")
        self.assertEqual(win["closes"], [10, 20])


class TestExecutionCostAccounting(unittest.TestCase):
    def test_measured_impact_is_not_counted_as_slippage_too(self):
        signal = {
            "engine": "trend", "direction": "LONG", "strength": 0.8,
            "expected_r": 1.0, "price": 100.0, "sl_price": 98.0,
            "_ob_impact": {"impact_pct": 0.10},
            "order_book": {"ob_spread_pct": 0.04},
        }
        br = expected_net_r(signal)
        self.assertAlmostEqual(br["impact_r"], 0.05, places=4)
        self.assertAlmostEqual(br["slip_r"], 0.03, places=4)

    def test_backtest_position_preserves_engine(self):
        pos = BTPosition("BTC", "LONG", 100, 1000, 98, 104, 0.8, 1,
                         engine="reversal")
        self.assertEqual(pos.engine, "reversal")


class TestWalkForwardWarmup(unittest.TestCase):
    def test_oos_slice_keeps_prior_bars_without_future_bars(self):
        ohlcv = {k: list(range(300)) for k in
                 ("ts", "opens", "highs", "lows", "closes", "volumes")}
        out = _slice_bundle_with_warmup({"1d": ohlcv}, 250, 270)["1d"]
        self.assertEqual(out["ts"][0], 30)   # 220 prior daily bars
        self.assertEqual(out["ts"][-1], 270)
        self.assertNotIn(271, out["ts"])


if __name__ == "__main__":
    unittest.main()
