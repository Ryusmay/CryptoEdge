import unittest
from unittest.mock import patch
from datetime import datetime, timedelta

from v2_parity_policy import (
    apply_market_gates, causal_change_pct, limit_timeout_5m_bars,
    limit_timeout_seconds, limit_touched,
)


def sig(direction="LONG"):
    return {"symbol": "BTC", "direction": direction, "engine": "daytrading_v2", "reasons": []}


class V2RuntimeReplayParityTests(unittest.TestCase):
    def test_event_trade_messages_are_compact(self):
        from pyside6_ui import compact_trade_event
        opened = compact_trade_event({
            "event": "OPEN", "price": "101.25", "reasons": "A|B|C",
        })
        won = compact_trade_event({"event": "CLOSE", "price": "105", "pnl": "4.25"})
        lost = compact_trade_event({"event": "CLOSE", "price": "98", "pnl": "-2.5"})
        self.assertEqual(opened, "Otwarto po cenie 101.25")
        self.assertEqual(won, "Zamknięto po cenie 105 · Zysk 4.25 USD")
        self.assertEqual(lost, "Zamknięto po cenie 98 · Strata 2.50 USD")
        self.assertNotIn("A", opened)

    def test_shared_lifecycle_gives_same_actions_for_quote_and_flat_bar(self):
        from v2_trade_lifecycle import (
            V2Observation, V2TradeView, decide_v2_lifecycle,
        )
        import config
        view = V2TradeView("LONG", 100.0, 98.0, 104.0, 106.0)
        # Runtime widzi POJEDYNCZY TICK (high=low=close), replay caly BAR 5m.
        # Wczesniej oba wywolania mialy identyczne argumenty, wiec
        # assertEqual(quote, bar) porownywalo wywolanie samo ze soba i nie
        # moglo paść. Bar dostaje teraz realny zakres wokol tej samej ceny.
        quote = decide_v2_lifecycle(
            view, V2Observation(104.0, 104.0, 104.0, 300.0), initial_risk=2.0,
        )
        bar = decide_v2_lifecycle(
            view, V2Observation(104.5, 103.5, 104.0, 300.0), initial_risk=2.0,
        )
        self.assertEqual(quote, bar)
        self.assertEqual(quote.action, "tp1")
        # BE po TP1 stoi na wejsciu POWIEKSZONYM o bufor - taki stop stawia
        # bot na zywo (paper_trader.py:479-486), a od v20.72.0 to samo robi
        # replay, bo bufor przeniesiony zostal do wspolnego reducera.
        buf = float(config.DAYTRADING_BREAK_EVEN_BUFFER_PCT) / 100.0
        self.assertAlmostEqual(quote.new_sl, 100.0 * (1 + buf), places=9)
        self.assertNotAlmostEqual(quote.new_sl, 100.0, places=6)

    def test_paper_adapter_uses_shared_lifecycle_contract(self):
        from paper_trader import Position
        position = Position({
            "symbol": "BTC", "direction": "LONG", "price": 100.0,
            "strength": 0.75, "sl_price": 98.0, "tp1_price": 104.0,
            "tp2_price": 106.0, "engine": "daytrading_v2",
        }, 100.0, leverage=3)
        position.entry_time = datetime.now() - timedelta(minutes=5)
        decision = position.decide_v2_exit(104.0)
        self.assertEqual(decision.action, "tp1")
        self.assertEqual(decision.price, 104.0)

    def test_stop_has_same_conservative_priority_over_tp(self):
        from v2_trade_lifecycle import (
            V2Observation, V2TradeView, decide_v2_lifecycle,
        )
        view = V2TradeView("LONG", 100.0, 100.0, 104.0, 106.0, tp1_done=True)
        decision = decide_v2_lifecycle(
            view, V2Observation(105.0, 99.0, 103.0, 600.0), initial_risk=2.0,
        )
        self.assertEqual(decision.action, "sl")

    def test_timeout_uses_one_shared_unit_conversion(self):
        with patch("config.DAYTRADING_V2_LIMIT_TIMEOUT_15M_BARS", 2):
            self.assertEqual(limit_timeout_seconds(), 1800.0)
            self.assertEqual(limit_timeout_5m_bars(), 6)

    def test_quote_and_bar_touch_agree_at_limit(self):
        self.assertEqual(limit_touched("LONG", 99, price=98.5), 99)
        self.assertEqual(limit_touched("LONG", 99, open_price=100, low=98.5, high=101), 99)
        self.assertIsNone(limit_touched("LONG", 99, price=100))
        self.assertIsNone(limit_touched("LONG", 99, open_price=100, low=99.5, high=101))

    def test_pump_gate_is_identical_for_runtime_and_replay_payload(self):
        live, replay = sig(), sig()
        coin = {"symbol": "BTC", "change_24h": 30.0}
        with patch("config.BLOCK_PUMP_CHASE_PCT", 28.0):
            apply_market_gates([live], [coin], "TREND")
            replay["change_24h"] = 30.0
            apply_market_gates([replay], [{"symbol": "BTC"}], "UNKNOWN")
        self.assertEqual(live["reject_reason"], replay["reject_reason"])
        self.assertEqual(live["direction"], replay["direction"])

    def test_causal_change_never_reads_future(self):
        closes = [100.0] * 289 + [1000.0]
        self.assertEqual(causal_change_pct(closes, 288), 0.0)
        self.assertAlmostEqual(causal_change_pct(closes, 289), 900.0)

    def test_portfolio_replay_enforces_runtime_direction_cap(self):
        from daytrading_backtester import portfolio_replay_v2

        def data(symbol):
            frame = {"opens": [100.0] * 4, "highs": [101.0] * 4,
                     "lows": [99.0] * 4, "closes": [100.0] * 4}
            def signal_at(i):
                if i != 0:
                    return None
                return {"symbol": symbol, "direction": "LONG", "engine": "daytrading_v2",
                        "price": 100.0, "sl_price": 99.0, "tp1_price": 101.0,
                        "tp2_price": 102.0, "limit_price": None}
            return {"ohlcv_5m": frame, "signal_at": signal_at}

        result = portfolio_replay_v2(
            {"AAA": data("AAA"), "BBB": data("BBB")}, max_positions=2,
            max_same_direction=1,
        )
        self.assertEqual(result["rejected_for_direction"], 1)
        self.assertEqual(result["max_same_direction"], 1)


if __name__ == "__main__":
    unittest.main()
