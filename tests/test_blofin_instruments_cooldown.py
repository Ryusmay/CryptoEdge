import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import disk_cache
from data_feeder import DataFeeder


class TestBlofinInstrumentsCooldown(unittest.TestCase):
    """Regresja na realny problem z konsoli: [Blofin] Rate limit co cykl,
    bo fetch_blofin_usdt_instruments() nie mial cooldownu po porazce (w
    przeciwienstwie do fetch_all_tickers(), ktore juz go mialo) - kazdy
    kolejny cykl (~13s) od razu bil w API ponownie, nie dajac realnemu
    rate-limitowi czasu na wygasniecie.

    Izolacja disk_cache: DataFeeder.__init__ probuje seedowac instrumenty z
    dysku (przestarzale dane > brak danych po restarcie) - bez izolacji testy
    zanieczyszczalyby sie nawzajem przez prawdziwy, wspoldzielony plik cache."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        patcher = patch.object(disk_cache, "CACHE_DIR", Path(self._tmpdir.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.feeder = DataFeeder()

    def test_repeated_calls_within_cooldown_hit_the_network_only_once(self):
        with patch.object(self.feeder.blofin, "_get", return_value=None) as mock_get:
            first = self.feeder.fetch_blofin_usdt_instruments()
            second = self.feeder.fetch_blofin_usdt_instruments()
            third = self.feeder.fetch_blofin_usdt_instruments()
        self.assertEqual(1, mock_get.call_count)
        self.assertEqual([], first)
        self.assertEqual([], second)
        self.assertEqual([], third)

    def test_retry_happens_again_after_cooldown_elapses(self):
        with patch.object(self.feeder.blofin, "_get", return_value=None) as mock_get:
            self.feeder.fetch_blofin_usdt_instruments()
        self.assertEqual(1, mock_get.call_count)
        self.feeder.instruments_fail_ts = time.time() - 46
        with patch.object(self.feeder.blofin, "_get", return_value=None) as mock_get2:
            self.feeder.fetch_blofin_usdt_instruments()
        self.assertEqual(1, mock_get2.call_count)

    def test_cooldown_escalates_on_consecutive_failures_instead_of_staying_flat(self):
        cases = [(1, 45.0), (2, 90.0), (3, 180.0), (4, 360.0), (5, 600.0), (10, 600.0)]
        for streak_before, expected in cases:
            self.feeder.instruments_fail_streak = streak_before
            self.feeder.instruments_fail_ts = time.time() - (expected - 5)
            with patch.object(self.feeder.blofin, "_get", return_value=None) as too_early:
                self.feeder.fetch_blofin_usdt_instruments()
            self.assertEqual(0, too_early.call_count, f"streak={streak_before}: nie powinno probowac przed {expected}s")
            self.feeder.instruments_fail_streak = streak_before
            self.feeder.instruments_fail_ts = time.time() - (expected + 1)
            with patch.object(self.feeder.blofin, "_get", return_value=None) as on_time:
                self.feeder.fetch_blofin_usdt_instruments()
            self.assertEqual(1, on_time.call_count, f"streak={streak_before}: powinno probowac po {expected}s")

    def test_success_clears_the_failure_streak_not_just_the_timestamp(self):
        self.feeder.instruments_fail_streak = 3
        self.feeder.instruments_fail_ts = time.time() - 400
        ok_payload = {"data": [{
            "state": "live", "quoteCurrency": "USDT", "instType": "SWAP",
            "baseCurrency": "BTC", "instId": "BTC-USDT", "maxLeverage": "10",
            "minSize": "0.001", "tickSize": "0.1", "contractValue": "1",
        }]}
        with patch.object(self.feeder.blofin, "_get", return_value=ok_payload):
            self.feeder.fetch_blofin_usdt_instruments()
        self.assertEqual(0, self.feeder.instruments_fail_streak)
        self.assertEqual(0.0, self.feeder.instruments_fail_ts)

    def test_success_clears_the_failure_cooldown(self):
        self.feeder.instruments_fail_ts = time.time() - 46
        ok_payload = {"data": [{
            "state": "live", "quoteCurrency": "USDT", "instType": "SWAP",
            "baseCurrency": "BTC", "instId": "BTC-USDT", "maxLeverage": "10",
            "minSize": "0.001", "tickSize": "0.1", "contractValue": "1",
        }]}
        with patch.object(self.feeder.blofin, "_get", return_value=ok_payload):
            result = self.feeder.fetch_blofin_usdt_instruments()
        self.assertEqual(1, len(result))
        self.assertEqual(0.0, self.feeder.instruments_fail_ts)

    def test_disk_cache_seeds_instruments_after_simulated_restart(self):
        # "przestarzale dane != brak danych" - druga instancja DataFeeder
        # (symulacja restartu procesu) powinna od razu miec dane z dysku,
        # zamiast startowac pusta i robic natychmiastowy burst zapytan.
        ok_payload = {"data": [{
            "state": "live", "quoteCurrency": "USDT", "instType": "SWAP",
            "baseCurrency": "ETH", "instId": "ETH-USDT", "maxLeverage": "10",
            "minSize": "0.001", "tickSize": "0.1", "contractValue": "1",
        }]}
        with patch.object(self.feeder.blofin, "_get", return_value=ok_payload):
            self.feeder.fetch_blofin_usdt_instruments()
        restarted_feeder = DataFeeder()
        self.assertEqual(1, len(restarted_feeder.instruments_cache))
        self.assertEqual("ETH", restarted_feeder.instruments_cache[0]["symbol"])
        self.assertGreater(restarted_feeder.instruments_ts, 0)


if __name__ == "__main__":
    unittest.main()
