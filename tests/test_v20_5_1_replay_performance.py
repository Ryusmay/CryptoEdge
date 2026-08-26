import unittest
from unittest.mock import patch

from daytrading_backtester import AsOfBlofinFeed
from daytrading_engine_v2 import DayTradingEngineV2
from historical_replay import v2_decision_due


def frame(timestamps):
    n = len(timestamps)
    return {"timestamps": timestamps, "opens": list(range(n)),
            "highs": list(range(n)), "lows": list(range(n)),
            "closes": list(range(n)), "volumes": [1] * n}


class AsOfWindowCacheTests(unittest.TestCase):
    def test_open_candle_is_excluded_and_same_window_is_reused(self):
        feed = AsOfBlofinFeed({"5m": frame([0, 300_000, 600_000])})
        feed.asof_ts = 600_000
        first = feed.fetch_klines_ohlcv("BTC", "5m", 60)
        second = feed.fetch_klines_ohlcv("BTC", "5m", 60)
        self.assertEqual(tuple(first["timestamps"]), (0, 300_000))
        self.assertIs(first, second)
        self.assertIsInstance(first["closes"], tuple)

    def test_decision_cadence_is_15m_not_every_5m(self):
        timestamps = [0, 300_000, 600_000, 900_000, 1_200_000, 1_500_000]
        self.assertEqual([v2_decision_due(ts) for ts in timestamps],
                         [True, False, False, True, False, False])


class IndicatorCacheTests(unittest.TestCase):
    def test_same_closed_frame_is_computed_once(self):
        engine = DayTradingEngineV2()
        sample = frame([0, 3_600_000, 7_200_000])
        with patch("daytrading_engine_v2.compute_indicators", return_value={"atr": 1}) as calc:
            one = engine._cached_indicators("BTC", sample, "1h")
            two = engine._cached_indicators("BTC", sample, "1h")
        self.assertEqual(one, two)
        self.assertEqual(calc.call_count, 1)


class HistoricalFundingTests(unittest.TestCase):
    def test_funding_cache_follows_historical_asof_not_wall_clock(self):
        bundle = {"funding": [{"ts_ms": 1_000, "rate": 0.001},
                              {"ts_ms": 2_000, "rate": 0.002}]}
        feed = AsOfBlofinFeed(bundle)
        engine = DayTradingEngineV2(type("Feeder", (), {"blofin": feed})())
        feed.asof_ts = 1_500
        first = engine._funding_for("BTC", {})
        feed.asof_ts = 2_500
        second = engine._funding_for("BTC", {})
        self.assertEqual(first["funding_rate"], 0.001)
        self.assertEqual(second["funding_rate"], 0.002)


if __name__ == "__main__":
    unittest.main()
