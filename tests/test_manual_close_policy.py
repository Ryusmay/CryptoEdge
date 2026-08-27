"""Reczne zamkniecie pyta o wiek cen tak samo jak reszta sciezek.

Do v20.36.0 `BotRuntime.close_symbol` bylo JEDYNA sciezka zamkniecia, ktora
brala cene z mapy bez pytania o jej wiek. Kill switch, close_all
i on_engine_stop szly przez close_policy, reczne zamkniecie nie - wiec
zamkniecie przy zamrozonym feedzie zapisywalo sie w historii jak zwykla
transakcja.

Ta sciezka nie miala dotad zadnego testu.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime import BotRuntime  # noqa: E402


class _Position:
    def __init__(self, symbol="BTC", entry_price=100.0):
        self.symbol = symbol
        self.entry_price = entry_price


class _Trader:
    def __init__(self, positions):
        self.positions = list(positions)
        self.calls = []

    def close_by_symbol(self, symbol, price_map=None, reason="manual"):
        self.calls.append({"symbol": symbol, "price_map": dict(price_map or {}),
                           "reason": reason})
        wanted = str(symbol or "").upper()
        pos = next((p for p in self.positions
                    if str(p.symbol).upper() == wanted), None)
        if pos is None:
            return None
        self.positions.remove(pos)
        return 7.5


def _runtime(trader, price_map, age_s):
    rt = BotRuntime.__new__(BotRuntime)
    rt.trader = trader
    rt.last_price_map = dict(price_map)
    rt.price_map_age_s = lambda: age_s
    return rt


class ManualCloseGoesThroughClosePolicyTests(unittest.TestCase):

    def test_fresh_price_closes_exactly_as_before(self):
        trader = _Trader([_Position()])
        out = _runtime(trader, {"BTC": 101.0}, 5.0).close_symbol("BTC")
        call = trader.calls[0]
        self.assertEqual({"BTC": 101.0}, call["price_map"])
        self.assertEqual("manual", call["reason"])
        # Bez ostrzezenia w komunikacie - nic nie bylo nie tak.
        self.assertEqual("CLOSED BTC PnL $+7.5000", out)

    def test_stale_price_still_closes_but_leaves_a_trace(self):
        # Kluczowe: NIE odmawiamy. Odmowa zamkniecia jest gorsza niz
        # zamkniecie po starej cenie - trzeba tylko zostawic slad.
        trader = _Trader([_Position()])
        out = _runtime(trader, {"BTC": 101.0}, 9_999.0).close_symbol("BTC")
        call = trader.calls[0]
        self.assertEqual({"BTC": 101.0}, call["price_map"])
        self.assertTrue(call["reason"].startswith("manual:STALE_PRICE_"),
                        f"powod bez sladu po nieswiezej cenie: {call['reason']}")
        self.assertIn("STALE_PRICE_", out)
        self.assertIn("CLOSED BTC", out)

    def test_missing_price_falls_back_to_entry_and_says_so(self):
        trader = _Trader([_Position(entry_price=100.0)])
        out = _runtime(trader, {}, 5.0).close_symbol("BTC")
        call = trader.calls[0]
        self.assertEqual({"BTC": 100.0}, call["price_map"])
        self.assertEqual("manual:NO_PRICE", call["reason"])
        self.assertIn("NO_PRICE", out)

    def test_second_key_lookup_is_preserved(self):
        """close_by_symbol szukalo ceny pod dwoma kluczami.

        close_policy pyta tylko o pos.symbol, wiec gdyby przeniesienie
        reguly zabralo drugi klucz, zamkniecie po cichu spadloby na cene
        wejscia zamiast uzyc dostepnej ceny rynkowej.
        """
        # Pozycja trzyma symbol malymi literami, mapa cen - duzymi.
        # Pierwszy klucz (pos.symbol = "btc") NIE trafia; ratuje dopiero
        # drugi ("BTC"). Bez doklejenia go decyzja spadlaby na entry_price.
        trader = _Trader([_Position(symbol="btc", entry_price=100.0)])
        out = _runtime(trader, {"BTC": 133.0}, 5.0).close_symbol("btc")
        call = trader.calls[0]
        self.assertEqual({"btc": 133.0}, call["price_map"],
                         "drugi klucz przepadl - zamknieto po cenie wejscia")
        self.assertEqual("manual", call["reason"])
        self.assertIn("CLOSED", out)

    def test_missing_position_is_reported_without_touching_the_book(self):
        trader = _Trader([])
        out = _runtime(trader, {"BTC": 101.0}, 5.0).close_symbol("BTC")
        self.assertEqual("Brak pozycji BTC", out)
        self.assertEqual([], trader.calls)


if __name__ == "__main__":
    unittest.main()
