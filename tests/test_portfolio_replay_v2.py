import unittest

from daytrading_backtester import portfolio_replay_v2


def _flat_bars(n, price=100.0):
    return {"opens": [price] * n, "highs": [price + 0.1] * n, "lows": [price - 0.1] * n,
            "closes": [price] * n, "timestamps": [1_700_000_000_000 + i * 300_000 for i in range(n)]}


def _single_signal_at_bar0(symbol, entry=100.0, sl=98.0, tp1=101.5, tp2=104.0):
    fired = {"done": False}

    def signal_at(i):
        if i == 0 and not fired["done"]:
            fired["done"] = True
            return {"symbol": symbol, "direction": "LONG", "price": entry,
                    "sl_price": sl, "tp1_price": tp1, "tp2_price": tp2}
        return {"direction": "NEUTRAL", "reject_reason": "TEST_NEUTRAL"}
    return signal_at


class TestPortfolioReplayV2(unittest.TestCase):
    def test_empty_symbols_data_returns_empty_result(self):
        result = portfolio_replay_v2({}, max_positions=10)
        self.assertEqual(0, result["count"])
        self.assertEqual({}, result["by_symbol"])

    def test_two_symbols_both_fit_within_max_positions(self):
        n = 10
        symbols_data = {
            "BTC": {"ohlcv_5m": _flat_bars(n), "signal_at": _single_signal_at_bar0("BTC")},
            "ETH": {"ohlcv_5m": _flat_bars(n), "signal_at": _single_signal_at_bar0("ETH")},
        }
        result = portfolio_replay_v2(symbols_data, max_positions=10, max_bars=3)
        self.assertEqual(2, result["count"])
        self.assertEqual(0, result["rejected_for_slots"])
        self.assertIn("BTC", result["by_symbol"])
        self.assertIn("ETH", result["by_symbol"])

    def test_signal_rejected_when_no_free_slot(self):
        n = 10
        symbols_data = {
            "BTC": {"ohlcv_5m": _flat_bars(n), "signal_at": _single_signal_at_bar0("BTC")},
            "ETH": {"ohlcv_5m": _flat_bars(n), "signal_at": _single_signal_at_bar0("ETH")},
        }
        result = portfolio_replay_v2(symbols_data, max_positions=1, max_bars=3)
        # Tylko jeden symbol (alfabetycznie pierwszy: BTC) dostaje slot,
        # drugi jest odrzucony - nie kolejkuje sie na pozniej.
        self.assertEqual(1, result["count"])
        self.assertEqual(1, result["rejected_for_slots"])
        self.assertIn("BTC", result["by_symbol"])
        self.assertNotIn("ETH", result["by_symbol"])

    def test_slot_frees_up_after_exit_and_new_signal_can_enter(self):
        n = 20
        bars_btc = _flat_bars(n)
        bars_btc["lows"][2] = 97.0  # BTC trafia SL szybko - zwalnia slot

        def eth_signal_at(i):
            # ETH probuje wejsc na barze 3 (po tym jak BTC juz zamknieto na barze 2)
            if i == 3:
                return {"symbol": "ETH", "direction": "LONG", "price": 100.0,
                        "sl_price": 98.0, "tp1_price": 101.5, "tp2_price": 104.0}
            return {"direction": "NEUTRAL", "reject_reason": "TEST_NEUTRAL"}

        symbols_data = {
            "BTC": {"ohlcv_5m": bars_btc, "signal_at": _single_signal_at_bar0("BTC")},
            "ETH": {"ohlcv_5m": _flat_bars(n), "signal_at": eth_signal_at},
        }
        result = portfolio_replay_v2(symbols_data, max_positions=1, max_bars=15)
        self.assertEqual(0, result["rejected_for_slots"])
        self.assertIn("BTC", result["by_symbol"])
        self.assertIn("ETH", result["by_symbol"])

    def test_deterministic_slot_assignment_order_is_alphabetical(self):
        n = 10
        symbols_data = {
            "ZEC": {"ohlcv_5m": _flat_bars(n), "signal_at": _single_signal_at_bar0("ZEC")},
            "AAA": {"ohlcv_5m": _flat_bars(n), "signal_at": _single_signal_at_bar0("AAA")},
        }
        result = portfolio_replay_v2(symbols_data, max_positions=1, max_bars=3)
        self.assertIn("AAA", result["by_symbol"])
        self.assertNotIn("ZEC", result["by_symbol"])

    def test_notify_exit_per_symbol_is_called_with_correct_symbol(self):
        n = 10
        calls = []

        def make_notify(name):
            def notify_exit(symbol, side, reason, ts):
                calls.append((name, symbol, side, reason))
            return notify_exit

        symbols_data = {
            "BTC": {"ohlcv_5m": _flat_bars(n), "signal_at": _single_signal_at_bar0("BTC"),
                    "notify_exit": make_notify("BTC")},
            "ETH": {"ohlcv_5m": _flat_bars(n), "signal_at": _single_signal_at_bar0("ETH"),
                    "notify_exit": make_notify("ETH")},
        }
        portfolio_replay_v2(symbols_data, max_positions=10, max_bars=3)
        self.assertEqual(2, len(calls))
        for owner, symbol, _side, _reason in calls:
            self.assertEqual(owner, symbol)  # notify_exit BTC-a nigdy nie dostaje ETH i odwrotnie

    def test_htf_bias_at_is_scoped_per_symbol_not_shared(self):
        n = 25
        bars = _flat_bars(n)

        def btc_htf_bias_at(i):
            return "SHORT" if i >= 5 else "LONG"  # BTC odwraca sie na barze 5

        symbols_data = {
            "BTC": {"ohlcv_5m": bars, "signal_at": _single_signal_at_bar0("BTC"), "htf_bias_at": btc_htf_bias_at},
            "ETH": {"ohlcv_5m": _flat_bars(n), "signal_at": _single_signal_at_bar0("ETH")},  # brak htf_bias_at
        }
        result = portfolio_replay_v2(symbols_data, max_positions=10, max_bars=15)
        btc_trade = next(t for s, t in result["trades_with_symbol"] if s == "BTC")
        eth_trade = next(t for s, t in result["trades_with_symbol"] if s == "ETH")
        self.assertEqual("htf_reversal", btc_trade.exit_reason)
        self.assertNotEqual("htf_reversal", eth_trade.exit_reason)

    def test_by_symbol_aggregates_net_r_correctly(self):
        n = 10
        symbols_data = {
            "BTC": {"ohlcv_5m": _flat_bars(n), "signal_at": _single_signal_at_bar0("BTC")},
        }
        result = portfolio_replay_v2(symbols_data, max_positions=10, max_bars=3)
        self.assertEqual(1, result["by_symbol"]["BTC"]["trades"])
        self.assertAlmostEqual(result["net_r"], result["by_symbol"]["BTC"]["net_r"])

    def test_shorter_symbol_series_truncates_to_shared_range(self):
        symbols_data = {
            "BTC": {"ohlcv_5m": _flat_bars(20), "signal_at": _single_signal_at_bar0("BTC")},
            "ETH": {"ohlcv_5m": _flat_bars(5), "signal_at": _single_signal_at_bar0("ETH")},
        }
        # Nie powinno rzucic wyjatku mimo roznych dlugosci - uzywa wspolnego
        # (najkrotszego) zakresu.
        result = portfolio_replay_v2(symbols_data, max_positions=10, max_bars=3)
        self.assertGreaterEqual(result["count"], 0)


if __name__ == "__main__":
    unittest.main()
