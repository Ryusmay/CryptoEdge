import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine_router import annotate_residual_momentum, route_signal, universe_market_return
from reversal_engine import merge_trend_and_reversal
from strength_calibration import StrengthCalibrator


class TestResidualMomentum(unittest.TestCase):
    def test_uses_btc_and_universe_median(self):
        coins = [{"change_24h": x} for x in (-2, 0, 2, 4, 6)]
        market = universe_market_return(coins)
        self.assertEqual(market, 2.0)
        coin = {"change_24h": 10.0}
        annotate_residual_momentum(coin, btc_change_24h=4.0, market_return_24h=market)
        self.assertAlmostEqual(coin["benchmark_return_24h"], 3.3)
        self.assertAlmostEqual(coin["residual_momentum_24h"], 6.7)


class TestLiquidityRouter(unittest.TestCase):
    def test_liquid_aligned_panic_prefers_continuation(self):
        sig = route_signal({
            "engine": "trend", "direction": "LONG", "change_24h": 8,
            "residual_momentum_24h": 3, "volume_24h": 100_000_000,
            "market_regime": "PANIC",
        })
        self.assertEqual(sig["preferred_engine"], "trend")
        self.assertEqual(sig["engine_route_reason"], "LIQUID_RESIDUAL_CONTINUATION")

    def test_reversal_needs_confirmation_to_receive_preference(self):
        base = {
            "engine": "reversal", "direction": "SHORT", "change_24h": 15,
            "residual_momentum_24h": 8, "volume_24h": 1_000_000,
            "market_regime": "PANIC", "strength": 0.55,
        }
        unconfirmed = route_signal(dict(base))
        self.assertEqual(unconfirmed["preferred_engine"], "trend")
        confirmed = route_signal({**base, "setup": "reversal_confirmed", "strength": 0.65})
        self.assertEqual(confirmed["preferred_engine"], "reversal")

    def test_router_breaks_close_conflict_toward_liquid_continuation(self):
        trend = [{
            "symbol": "BTC", "engine": "trend", "direction": "LONG",
            "strength": 0.65, "change_24h": 5, "residual_momentum_24h": 3,
            "volume_24h": 100_000_000, "market_regime": "TREND_UP",
        }]
        reversal = [{
            "symbol": "BTC", "engine": "reversal", "direction": "SHORT",
            "strength": 0.68, "reversal_score": 0.68,
            "change_24h": 5, "residual_momentum_24h": 3,
            "volume_24h": 100_000_000, "market_regime": "TREND_UP",
        }]
        merged = merge_trend_and_reversal(trend, reversal)
        self.assertEqual(merged[0]["engine"], "trend")


class TestCalibrationStatus(unittest.TestCase):
    def test_default_curve_is_explicitly_prior_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            cal = StrengthCalibrator(path=os.path.join(tmp, "missing.json"))
            signal = {"strength": 0.8}
            cal.annotate(signal)
            self.assertEqual(signal["expected_r_status"], "PRIOR_ONLY")
            self.assertEqual(signal["expected_r_calibration"]["n"], 0)


if __name__ == "__main__":
    unittest.main()
