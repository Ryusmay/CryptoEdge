import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import disk_cache
from data_feeder import DataFeeder


class TestCoinGeckoCooldown(unittest.TestCase):
    """Ten sam wzorzec co test_blofin_instruments_cooldown.py - CoinGecko jest
    historycznie najbardziej rate-limitowanym zrodlem w tym projekcie
    (darmowy, publiczny tier), wiec zasluguje na ta sama ochrone: eskalujacy
    backoff po porazce zamiast wiecznego probowania na stalym rytmie."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        patcher = patch.object(disk_cache, "CACHE_DIR", Path(self._tmpdir.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.feeder = DataFeeder()

    def test_repeated_calls_within_cooldown_hit_the_network_only_once(self):
        with patch.object(self.feeder, "_get_coingecko", return_value=None) as mock_get:
            self.feeder._refresh_coingecko_top()
            self.feeder._refresh_coingecko_top()
        self.assertEqual(1, mock_get.call_count)

    def test_cooldown_escalates_on_consecutive_failures(self):
        cases = [(1, 120.0), (2, 240.0), (3, 480.0), (4, 960.0), (5, 1800.0), (10, 1800.0)]
        for streak_before, expected in cases:
            self.feeder.cg_map_fail_streak = streak_before
            self.feeder.cg_map_fail_ts = time.time() - (expected - 5)
            with patch.object(self.feeder, "_get_coingecko", return_value=None) as too_early:
                self.feeder._refresh_coingecko_top()
            self.assertEqual(0, too_early.call_count, f"streak={streak_before}: nie powinno probowac przed {expected}s")
            self.feeder.cg_map_fail_streak = streak_before
            self.feeder.cg_map_fail_ts = time.time() - (expected + 1)
            with patch.object(self.feeder, "_get_coingecko", return_value=None) as on_time:
                self.feeder._refresh_coingecko_top()
            self.assertEqual(1, on_time.call_count, f"streak={streak_before}: powinno probowac po {expected}s")

    def test_success_clears_the_failure_streak(self):
        self.feeder.cg_map_fail_streak = 3
        self.feeder.cg_map_fail_ts = time.time() - 1000
        ok_payload = [{"id": "bitcoin", "symbol": "btc", "name": "Bitcoin", "current_price": 65000.0,
                       "market_cap": 1, "total_volume": 1}]
        with patch.object(self.feeder, "_get_coingecko", return_value=ok_payload):
            self.feeder._refresh_coingecko_top()
        self.assertEqual(0, self.feeder.cg_map_fail_streak)
        self.assertEqual(0.0, self.feeder.cg_map_fail_ts)
        self.assertIn("BTC", self.feeder.cg_map_cache)


if __name__ == "__main__":
    unittest.main()
