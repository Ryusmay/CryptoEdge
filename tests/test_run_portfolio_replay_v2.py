import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import historical_replay
import daytrading_backtester
from historical_replay import run_portfolio_replay_v2, ReplayRequest


class FakeFeedV2:
    _COUNTS = {"5m": 500, "15m": 500, "1h": 250, "4h": 260, "1D": 250}

    def __init__(self):
        self.last_error = None

    def fetch_klines_ohlcv(self, symbol, bar="5m", limit=120):
        n = min(limit, self._COUNTS.get(bar, limit))
        ts = 1_700_000_000
        step = {"5m": 300, "15m": 900, "1H": 3600, "4H": 14400, "1D": 86400}.get(bar, 300)
        return {
            "opens": [100.0] * n, "highs": [100.5] * n, "lows": [99.5] * n, "closes": [100.0] * n,
            "volumes": [1000.0] * n, "timestamps": [ts + i * step for i in range(n)],
        }

    def fetch_funding_rate_history(self, symbol, limit=50):
        return []


def _fake_signal_provider(symbol, bundle):
    engine = type("E", (), {"notify_exit": lambda *a, **k: None})()
    return (lambda i: {"direction": "NEUTRAL", "reject_reason": "TEST_NEUTRAL"}), engine


def _fake_htf_bias(symbol, bundle):
    return lambda i: None


def _fake_htf_anchor(symbol, bundle):
    return lambda i, direction: None


def _fake_portfolio_replay(symbols_data, max_positions, **kwargs):
    return {"trades": [], "count": 0, "win_rate": 0.0, "net_r": 0.0, "avg_r": 0.0,
            "rejected_for_slots": 0, "by_symbol": {}}


class TestRunPortfolioReplayV2Orchestration(unittest.TestCase):
    def _run_isolated(self, request):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with patch.object(historical_replay, "CACHE_DIR", base / "data" / "replay"), \
                 patch.object(historical_replay, "REPORT_DIR", base / "reports" / "replay"), \
                 patch.object(daytrading_backtester, "production_signal_provider_v2", _fake_signal_provider), \
                 patch.object(daytrading_backtester, "htf_bias_provider_v2", _fake_htf_bias), \
                 patch.object(daytrading_backtester, "htf_trail_anchor_provider_v2", _fake_htf_anchor), \
                 patch.object(daytrading_backtester, "portfolio_replay_v2", _fake_portfolio_replay):
                return run_portfolio_replay_v2(FakeFeedV2(), request)

    def test_report_has_expected_top_level_shape(self):
        request = ReplayRequest(symbols=("BTC", "ETH"), universe_mode="MANUAL",
                                days=1, oos_fraction=0.3, counterfactual_audit=False)
        report = self._run_isolated(request)
        self.assertEqual("DAYTRADING_V2", report["strategy"])
        self.assertIn("portfolio", report)
        self.assertIn("in_sample", report["portfolio"])
        self.assertIn("out_of_sample", report["portfolio"])
        self.assertIn("split", report)
        self.assertIn("report_path", report)
        self.assertTrue(str(report["report_path"]).endswith(".json"))

    def test_excluded_symbols_are_filtered_from_universe(self):
        request = ReplayRequest(symbols=("BTC", "TRX", "XAU"), universe_mode="MANUAL",
                                days=1, oos_fraction=0.3, counterfactual_audit=False)
        with patch("config.DAYTRADING_V2_EXCLUDED_SYMBOLS", ["TRX", "XAU"]):
            report = self._run_isolated(request)
        self.assertIn("BTC", report["symbols_downloaded"])
        self.assertNotIn("TRX", report["symbols_downloaded"])
        self.assertNotIn("XAU", report["symbols_downloaded"])
        self.assertEqual({"TRX", "XAU"}, set(report["universe"]["excluded_by_v2_profile"]))

    def test_split_matches_same_formula_as_v1_purge_12(self):
        request = ReplayRequest(symbols=("BTC",), universe_mode="MANUAL",
                                days=1, oos_fraction=0.3, counterfactual_audit=False)
        report = self._run_isolated(request)
        split = report["split"]
        self.assertEqual(12, split["purge_bars"])
        self.assertEqual(220, split["test_start"])  # floor 220 z shortest_len=500, requested_bars=288
        self.assertLess(split["is_end"], split["oos_start"])
        self.assertGreaterEqual(split["oos_start"] - split["is_end"], 2 * 12)

    def test_no_bundles_downloaded_gives_clean_error_not_crash(self):
        class AlwaysFailFeed(FakeFeedV2):
            def fetch_klines_ohlcv(self, symbol, bar="5m", limit=120):
                return {}
        request = ReplayRequest(symbols=("BTC",), universe_mode="MANUAL",
                                days=1, oos_fraction=0.3, counterfactual_audit=False)
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with patch.object(historical_replay, "CACHE_DIR", base / "data" / "replay"), \
                 patch.object(historical_replay, "REPORT_DIR", base / "reports" / "replay"):
                report = run_portfolio_replay_v2(AlwaysFailFeed(), request)
        self.assertIn("error", report)
        self.assertIn("BTC", report["skipped"])


if __name__ == "__main__":
    unittest.main()
