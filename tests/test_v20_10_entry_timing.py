import unittest

from daytrading_engine_v2 import DayTradingEngineV2


class EntryTimingContractTests(unittest.TestCase):
    def test_stale_zone_touch_is_not_a_current_trigger(self):
        n = 20
        frame = {
            "closes": [111.0] * n,
            "highs": [113.0] * n,
            "lows": [110.5] * n,
            "opens": [111.0] * n,
        }
        frame["lows"][n - 8] = 108.0
        self.assertFalse(DayTradingEngineV2._check_15m_trigger(
            frame, 110.0, 107.64, "LONG", lookback=12,
            reclaim_level=110.0, reclaim_bars=2, max_touch_age_bars=3,
        ))

    def test_recent_zone_touch_remains_valid(self):
        n = 20
        frame = {
            "closes": [111.0] * n,
            "highs": [113.0] * n,
            "lows": [110.5] * n,
            "opens": [111.0] * n,
        }
        frame["lows"][n - 2] = 108.0
        self.assertTrue(DayTradingEngineV2._check_15m_trigger(
            frame, 110.0, 107.64, "LONG", lookback=12,
            reclaim_level=110.0, reclaim_bars=2, max_touch_age_bars=3,
        ))


if __name__ == "__main__":
    unittest.main()
