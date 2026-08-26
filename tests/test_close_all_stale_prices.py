"""Awaryjne zamkniecie na nieswiezych cenach.

25.08.2026: stop_engine() liczyl wiek mapy cen i odmawial zamkniecia
(zadzialalo poprawnie przy 43788 s), ale kill_switch() i close_all() wolaly
trader.close_all(last_price_map) bez zadnego sprawdzenia. Do tego brak
symbolu w mapie ksiegowal PnL = 0 po entry_price - po cichu.

Kontrakt: awaryjne zamkniecie NIE odmawia (to jest wyjscie awaryjne), ale
kazda pozycja zamknieta po starej lub brakujacej cenie ma to zapisane
w powodzie zamkniecia.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from runtime import BotRuntime


class _Pos(SimpleNamespace):
    pass


class _Trader:
    """Minimalny dubler PaperTradera - liczy sie tylko powod zamkniecia."""

    def __init__(self, positions):
        self.positions = list(positions)
        self.closed = []
        self.lock = MagicMock()
        self.lock.__enter__ = MagicMock(return_value=None)
        self.lock.__exit__ = MagicMock(return_value=False)

    def close_all(self, price_map, reason="close_all", price_map_age_s=None):
        for pos in list(self.positions):
            self.closed.append((pos.symbol, reason, price_map_age_s))
        self.positions = []
        return len(self.closed)


class TestRuntimePassesPriceAge(unittest.TestCase):
    def _rt(self, age_s, price_map):
        rt = BotRuntime()
        rt.trader = _Trader([_Pos(symbol="BTC", entry_price=100.0)])
        rt.last_price_map = price_map
        rt.last_price_map_ts = 0.0 if age_s is None else __import__("time").time() - age_s
        return rt

    def test_price_map_age_is_inf_when_never_written(self):
        rt = self._rt(None, {})
        self.assertEqual(float("inf"), rt.price_map_age_s())

    def test_close_all_forwards_age_to_trader(self):
        rt = self._rt(43788.0, {"BTC": 101.0})
        rt.close_all()
        symbol, reason, age = rt.trader.closed[0]
        self.assertEqual("BTC", symbol)
        self.assertEqual("close_all", reason)
        self.assertIsNotNone(age)
        self.assertGreater(age, 40000)

    def test_kill_switch_forwards_age_to_trader(self):
        rt = self._rt(43788.0, {"BTC": 101.0})
        rt.kill_switch("test")
        symbol, reason, age = rt.trader.closed[0]
        self.assertEqual("kill_switch", reason)
        self.assertGreater(age, 40000)


class TestPaperTraderTagsStaleCloses(unittest.TestCase):
    def _trader(self):
        import paper_trader

        trader = paper_trader.PaperTrader.__new__(paper_trader.PaperTrader)
        trader.positions = [_Pos(symbol="BTC", entry_price=100.0)]
        trader.close_position = MagicMock(return_value=0.0)
        return trader

    def test_fresh_price_keeps_plain_reason(self):
        trader = self._trader()
        trader._close_all_unlocked({"BTC": 101.0}, "close_all", price_map_age_s=5.0)
        self.assertEqual("close_all", trader.close_position.call_args[0][2])

    def test_stale_price_is_tagged(self):
        trader = self._trader()
        trader._close_all_unlocked({"BTC": 101.0}, "kill_switch", price_map_age_s=43788.0)
        reason = trader.close_position.call_args[0][2]
        self.assertIn("STALE_PRICE", reason)
        self.assertIn("43788", reason)

    def test_missing_price_is_tagged_not_silent(self):
        trader = self._trader()
        trader._close_all_unlocked({}, "kill_switch", price_map_age_s=5.0)
        args = trader.close_position.call_args[0]
        self.assertEqual(100.0, args[1])
        self.assertIn("NO_PRICE", args[2])

    def test_zero_price_does_not_book_a_close_at_zero(self):
        trader = self._trader()
        trader._close_all_unlocked({"BTC": 0.0}, "close_all", price_map_age_s=5.0)
        args = trader.close_position.call_args[0]
        self.assertEqual(100.0, args[1])
        self.assertIn("NO_PRICE", args[2])

    def test_infinite_age_does_not_crash(self):
        trader = self._trader()
        trader._close_all_unlocked({"BTC": 101.0}, "kill_switch", price_map_age_s=float("inf"))
        self.assertIn("STALE_PRICE", trader.close_position.call_args[0][2])

    def test_missing_age_stays_backward_compatible(self):
        trader = self._trader()
        trader._close_all_unlocked({"BTC": 101.0}, "close_all")
        self.assertEqual("close_all", trader.close_position.call_args[0][2])


if __name__ == "__main__":
    unittest.main()
