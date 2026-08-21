import unittest
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import config
from blofin_feed import BlofinFeed
from daytrading_backtester import replay_daytrading
from daytrading_backtester import AsOfBlofinFeed
from daytrading_validation import assert_prefix_invariant, purged_walk_forward_splits
from day_expectancy_calibration import DayExpectancyCalibrator
from dynamic_correlation import DynamicCorrelation
from statistical_validation import deflated_sharpe_ratio, expected_max_sharpe_z
from walk_forward import make_folds
from expected_net_r import expected_net_r
from paper_trader import Position
from signal_engine import SignalEngine


class TestBlofinBookUnits(unittest.TestCase):
    def setUp(self):
        # Regresja 21.08.2026: test_candle_pagination_... uzywa realnego
        # BlofinFeed() z bar="15m" - odkad 15m trafil do
        # _KLINE_DISK_PERSIST_BARS, bez izolacji ten test cicho zapisywal
        # ohlcv_X-USDT_15m_400.json do PRAWDZIWEGO F:\CryptoEdge\data\
        # disk_cache\ (tego samego katalogu, ktorego uzywa zywy bot), co
        # przy kolejnym uruchomieniu testow psulo asercje liczby zapytan
        # (fetch_klines_ohlcv zaczynal doszywac delte z tego zanieczyszczenia
        # zamiast robic pelny fetch). Izolujemy CACHE_DIR dla calej klasy.
        import disk_cache
        self._tmpdir = tempfile.TemporaryDirectory()
        self._disk_cache_patcher = patch.object(disk_cache, "CACHE_DIR", Path(self._tmpdir.name))
        self._disk_cache_patcher.start()

    def tearDown(self):
        self._disk_cache_patcher.stop()
        self._tmpdir.cleanup()

    def test_contract_sizes_are_converted_to_base_before_usd_depth(self):
        feed = BlofinFeed()
        feed._get = lambda *a, **k: {"data": [{"bids": [["100", "10"]], "asks": [["101", "20"]]}]}
        with patch.object(feed, "_contract_value", return_value=0.01):
            book = feed.fetch_order_book("X")
        self.assertEqual(book["bids"], [(100.0, 0.1)])
        self.assertEqual(book["bids_contracts"], [(100.0, 10.0)])
        self.assertAlmostEqual(book["ob_depth_bid_usd"], 10.0)
        self.assertEqual(book["size_unit"], "base_asset")

    def test_candle_pagination_uses_after_and_returns_base_volume(self):
        feed = BlofinFeed()
        base_ts = 1_500_000_000_000
        rows = [[str(base_ts + i * 900_000), "1", "2", "0.5", "1.5",
                 "100", "10", "15", "1"] for i in range(400)]
        calls = []
        def fake_get(endpoint, params):
            calls.append(dict(params))
            eligible = rows
            if params.get("after"):
                eligible = [r for r in rows if int(r[0]) < int(params["after"])]
            return {"data": list(reversed(eligible))[:int(params["limit"])]}
        feed._get = fake_get
        data = feed.fetch_klines_ohlcv("X", "15m", 400)
        self.assertEqual(len(data["closes"]), 400)
        self.assertIn("after", calls[1])
        self.assertNotIn("before", calls[1])
        self.assertEqual(data["volumes"][-1], 10.0)
        self.assertEqual(data["quote_volumes"][-1], 15.0)


class TestDayExpectancy(unittest.TestCase):
    def test_prior_is_probability_weighted_and_uses_six_hour_funding(self):
        sig = {"engine": "daytrading", "strategy_mode": "DAYTRADING", "direction": "LONG",
               "price": 100, "sl_price": 99, "tp_plan": {"tp1_r": 1.5, "tp2_r": 2.2,
               "frac_tp1": .5}, "funding": {"funding_rate": .008, "funding_interval_h": 8},
               "order_book": {"ob_spread_pct": 0}}
        with patch.object(config, "TAKER_FEE", 0), patch.object(config, "DEFAULT_SLIPPAGE", 0), patch.object(config, "DEFAULT_IMPACT_FRAC", 0):
            out = expected_net_r(sig)
        self.assertEqual(out["calibration_status"], "PRIOR_ONLY")
        self.assertAlmostEqual(out["funding_r"], 0.6, places=6)
        self.assertLess(out["gross_r"], 0.30)


class TestDayLifecycle(unittest.TestCase):
    def _position(self):
        return Position({"symbol": "BTC", "direction": "LONG", "price": 100, "strength": .8,
                         "engine": "daytrading", "sl_price": 98, "tp_price": 104,
                         "tp1_price": 103, "tp2_price": 104, "atr": 1}, 100)

    def test_hard_time_stop_is_unconditional(self):
        p = self._position()
        p.entry_time = datetime.now() - timedelta(hours=config.DAYTRADING_HARD_TIME_STOP_HOURS + 1)
        self.assertTrue(p.daytrading_hard_time_stop_due())

    def test_invalidation_counts_distinct_closed_bars(self):
        p = self._position()
        def sig(ts):
            return {"intraday": {"bias_4h_1h": "LONG", "bar_ts": {"5m": ts}, "tf": {
                "5m": {"supertrend": {"is_up": False}}, "15m": {"ema_fast_above_slow": False}}}}
        self.assertIsNone(p.daytrading_invalidation(sig(1)))
        self.assertIsNone(p.daytrading_invalidation(sig(1)))
        self.assertEqual(p.daytrading_invalidation(sig(2)), "day_setup_invalidated")

    def test_funding_is_charged_only_at_instrument_settlement(self):
        p = self._position()
        p.funding_rate = 0.001
        p.funding_interval_h = 4
        p.next_funding_ts = __import__("time").time() + 60
        p.update_pnl(100)
        self.assertEqual(p.funding_paid, 0.0)
        p.next_funding_ts = __import__("time").time() - 1
        p.update_pnl(100)
        self.assertAlmostEqual(p.funding_paid, p.size_usd * 0.001, places=6)


class TestValidationAndReplay(unittest.TestCase):
    def test_asof_feed_uses_correct_bounded_slice(self):
        bundle = {"5m": {"timestamps": list(range(1, 1001)),
                           "opens": list(range(1, 1001)), "highs": list(range(1, 1001)),
                           "lows": list(range(1, 1001)), "closes": list(range(1, 1001)),
                           "volumes": [1] * 1000}}
        feed = AsOfBlofinFeed(bundle)
        feed.asof_ts = 500
        result = feed.fetch_klines_ohlcv("BTC", "5m", 3)
        self.assertEqual([498, 499, 500], result["timestamps"])
        self.assertEqual([498, 499, 500], result["closes"])

    def test_purged_splits_do_not_overlap(self):
        splits = purged_walk_forward_splits(200, 80, 30, purge=5, embargo=5)
        self.assertTrue(splits)
        for train, test in splits:
            self.assertGreaterEqual(test.start - train.stop, 5)

    def test_prefix_audit_detects_future_access(self):
        with self.assertRaises(AssertionError):
            assert_prefix_invariant(list(range(20)), lambda data, asof: data[-1], [10])

    def test_replay_enters_next_open_and_assumes_stop_first(self):
        data = {"opens": [100, 101, 101], "highs": [100, 104, 101], "lows": [100, 98, 101]}
        signal = {"direction": "LONG", "price": 100, "sl_price": 99,
                  "tp_plan": {"tp1_r": 1.5, "tp2_r": 2.2, "frac_tp1": .5}}
        out = replay_daytrading(data, lambda i: signal if i == 0 else None,
                                fee_frac_round_trip=0, slippage_frac_round_trip=0)
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["trades"][0].entry, 101)
        self.assertEqual(out["trades"][0].exit_reason, "sl_first_intrabar")

    def test_hard_time_stop_books_mark_to_market_r(self):
        n = 123
        data = {"opens": [100, 100] + [102] * (n - 2), "highs": [100, 100.1] + [102.1] * (n - 2),
                "lows": [100, 99.9] + [101.9] * (n - 2), "closes": [100, 100] + [102] * (n - 2)}
        signal = {"direction": "LONG", "price": 100, "sl_price": 99, "atr": 1,
                  "tp_plan": {"tp1_r": 3, "tp2_r": 4, "frac_tp1": .5}}
        out = replay_daytrading(data, lambda i: signal if i == 0 else None,
                                fee_frac_round_trip=0, slippage_frac_round_trip=0, max_bars=120)
        self.assertAlmostEqual(out["trades"][0].realised_r, 2.0)

    def test_main_walk_forward_has_real_purge_and_embargo(self):
        ts = list(range(300))
        folds = make_folds(ts, 80, 30, 30, 30, purge_bars=5, embargo_bars=7)
        self.assertGreaterEqual(folds[0][2] - folds[0][1], 5)
        self.assertGreaterEqual(folds[0][4] - folds[0][3], 5)
        self.assertEqual(folds[1][0] - folds[0][0], 37)

    def test_dynamic_correlation_aligns_common_timestamps(self):
        eng = DynamicCorrelation(window=10, min_obs=2)
        eng.set_close_series("A", [100, 101, 103], [1, 2, 3])
        eng.set_close_series("B", [50, 52, 53], [1, 3, 4])
        self.assertIsNone(eng.correlation("A", "B"))

    def test_day_calibration_persists_empirical_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "day.json")
            cal = DayExpectancyCalibrator(path)
            cal.record(True, False)
            cal.record(True, True)
            snap = DayExpectancyCalibrator(path).snapshot()
            self.assertEqual(snap["n"], 2)
            self.assertEqual(snap["p_tp1"], 1.0)
            self.assertEqual(snap["p_tp2_given_tp1"], 0.5)

    def test_deflated_sharpe_penalizes_multiple_trials(self):
        returns = [.2, -.1, .15, .05, -.02] * 20
        one = deflated_sharpe_ratio(returns, trials=1)["dsr"]
        many = deflated_sharpe_ratio(returns, trials=100)["dsr"]
        self.assertLess(many, one)

    def test_expected_max_sharpe_z_matches_bailey_lopez_de_prado_reference(self):
        # Referencyjne wartosci z ich wlasnego kodu (getExpMaxSR, Snippet 1
        # w "The Deflated Sharpe Ratio", 2014), emc=0.5772156649.
        self.assertAlmostEqual(expected_max_sharpe_z(10), 1.5745983013449718, places=6)
        self.assertAlmostEqual(expected_max_sharpe_z(100), 2.5306028932011424, places=6)
        self.assertEqual(expected_max_sharpe_z(1), 0.0)

    def test_deflated_sharpe_uses_real_trial_variance_when_provided(self):
        returns = [.2, -.1, .15, .05, -.02] * 20
        # Rozrzut SR miedzy niezaleznymi probami (np. inne symbole/parametry)
        # jest tu bardzo szeroki - realny DSR powinien byc dużo bardziej
        # konserwatywny niz fallback oparty na dlugosci probki.
        wide = deflated_sharpe_ratio(returns, trials=20, trial_sharpes=[3.0, -2.5, 1.8, -1.2, 0.5])
        narrow = deflated_sharpe_ratio(returns, trials=20, trial_sharpes=[0.01, -0.01, 0.02, 0.0, 0.01])
        fallback = deflated_sharpe_ratio(returns, trials=20)
        self.assertGreater(wide["selection_benchmark_sr"], fallback["selection_benchmark_sr"])
        self.assertLess(narrow["selection_benchmark_sr"], fallback["selection_benchmark_sr"])
        self.assertIn("zarejestrowanych SR", wide["benchmark_note"])
        self.assertIn("przyblizone z dlugosci probki", fallback["benchmark_note"])

    def test_daytrading_mode_still_generates_reversal_signals_for_shadow_tracking(self):
        # Regresja: generate_signals() w trybie DAYTRADING wczesniej robil
        # "return" zanim reversal_engine w ogole sie odpalil, wiec
        # reversal_shadow.csv byl zawsze pusty niezaleznie od progow
        # konfirmacji. Sprawdzamy, ze sygnaly z engine=="reversal" trafiaja
        # do wyniku i do last_analysis_board, nawet gdy STRATEGY_MODE=DAYTRADING.
        eng = SignalEngine(data_feeder=None)
        coins = [{"symbol": "BTC", "price": 100.0}]
        fake_reversal_signal = {
            "symbol": "BTC", "direction": "LONG", "strength": 0.6,
            "engine": "reversal", "setup": "reversal_confirmed",
            "confirmation_status": "CONFIRMED", "confirmation_count": 2,
            "price": 100.0, "sl": 96.0, "tp1": 103.0, "tp2": 106.0,
        }
        previous_mode = config.STRATEGY_MODE
        try:
            config.STRATEGY_MODE = "DAYTRADING"
            with patch("daytrading_engine.DayTradingEngine.generate", return_value=[]), \
                 patch("reversal_engine.generate_reversal_signals", return_value=[fake_reversal_signal]):
                final = eng.generate_signals(coins, btc_change_24h=0.0)
        finally:
            config.STRATEGY_MODE = previous_mode
        reversal_out = [s for s in final if s.get("engine") == "reversal"]
        self.assertEqual(1, len(reversal_out))
        self.assertEqual("BTC", reversal_out[0]["symbol"])
        board_engines = {row.get("engine") for row in eng.last_analysis_board}
        self.assertIn("reversal", board_engines)


if __name__ == "__main__":
    unittest.main()
