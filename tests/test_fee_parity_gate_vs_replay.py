"""Bramka: czy bramka wejscia i replay wyceniaja te sama transakcje tak samo.

Kontekst. Trzy miejsca w systemie wiedza o roznicy maker/taker:

  daytrading_backtester.py:849
      trade.fee_rt = (mf + tf) if kind == "limit" else (2.0 * tf)
  replay_execution.py:133,145
      etykietuje fill jako "maker" albo "taker"
  expected_net_r.py:220
      fee_rt = TAKER_FEE * 2.0            <-- zawsze, bez wyjatku

Przy DAYTRADING_V2_LIMIT_IN_ZONE = True silnik wstawia limit_price do
sygnalu (daytrading_engine_v2.py:686), a resolve_v2_fill() ma tylko dwa
wyjscia: "limit" albo "expired" - setup, ktory nie doszedl do strefy, jest
ANULOWANY, nigdy nie zamieniany w zlecenie rynkowe. Czyli kazda transakcja,
ktora faktycznie powstaje, jest wejsciem limitem i placi maker+taker.

Bramka wejscia placi wtedy 0.0012 zamiast 0.0008 - przeplaca o 50%.
Po naprawie spreadu i poslizgu prowizje sa CALA pozostala masa kosztowa,
wiec to nie jest zaokraglenie.

Ten plik nie rozstrzyga, ktora strona ma racje. Rozstrzyga, ze obie musza
mowic to samo.
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from daytrading_backtester import resolve_v2_fill
from expected_net_r import expected_net_r


def _signal(**over):
    """Sygnal o sl_dist dokladnie 0.04, zeby arytmetyka byla scisla.

    price 100.0, sl 96.0 -> sl_dist = 0.04
    fee_r = fee_rt / 0.04, wiec fee_rt odzyskujemy bez bledu zaokraglenia.
    """
    sig = {
        "symbol": "BTC", "direction": "LONG", "price": 100.0, "sl_price": 96.0,
        "strength": 0.75, "trend_score": 0.75, "engine": "daytrading_v2",
        "strategy_mode": "DAYTRADING_V2", "expected_r_status": "OK",
        "market_regime": "TREND_UP", "atr_pct": 1.2,
    }
    sig.update(over)
    return sig


def _implied_fee_rt(signal):
    """Ile bramka wejscia faktycznie policzyla za obrot."""
    br = expected_net_r(dict(signal))
    return round(br["fee_r"] * br["sl_dist"], 10), br


class TestFillKindIsRealNotAssumed(unittest.TestCase):
    """Najpierw dowod, ze przeslanka jest prawdziwa, a nie moja teza."""

    def test_signal_with_limit_price_fills_as_limit(self):
        sig = _signal(limit_price=99.0)
        fill, kind = resolve_v2_fill(sig, i=1, signal_i=0,
                                     open_=100.0, high=100.5, low=98.5)
        self.assertEqual(kind, "limit")
        self.assertAlmostEqual(fill, 99.0, places=10)

    def test_signal_without_limit_price_fills_as_market(self):
        sig = _signal()
        fill, kind = resolve_v2_fill(sig, i=1, signal_i=0,
                                     open_=100.0, high=100.5, low=98.5)
        self.assertEqual(kind, "market")
        self.assertAlmostEqual(fill, 100.0, places=10)

    def test_unfilled_limit_expires_and_never_becomes_market(self):
        """Kluczowa przeslanka: nie ma sciezki limit -> market."""
        sig = _signal(limit_price=90.0)          # cena nigdy tam nie dochodzi
        fill, kind = resolve_v2_fill(sig, i=99, signal_i=0,
                                     open_=100.0, high=100.5, low=98.5)
        self.assertIsNone(fill)
        self.assertEqual(kind, "expired")


class TestFeeParity(unittest.TestCase):

    def test_market_entry_is_charged_taker_twice(self):
        """Ten przypadek jest ZGODNY dzisiaj - dowod, ze bramka nie jest
        po prostu zawsze czerwona."""
        sig = _signal()
        got, _ = _implied_fee_rt(sig)
        want = round(2.0 * config.TAKER_FEE, 10)
        self.assertEqual(
            got, want,
            "wejscie rynkowe: bramka powinna liczyc 2x taker")

    def test_limit_entry_is_charged_maker_plus_taker(self):
        """ROZJAZD. Replay liczy maker+taker, bramka liczy taker+taker."""
        sig = _signal(limit_price=99.0)

        _, kind = resolve_v2_fill(sig, i=1, signal_i=0,
                                  open_=100.0, high=100.5, low=98.5)
        self.assertEqual(kind, "limit", "przeslanka: to jest fill limitem")

        got, br = _implied_fee_rt(sig)
        want = round(config.MAKER_FEE + config.TAKER_FEE, 10)
        self.assertEqual(
            got, want,
            "\n  Wejscie wypelnia sie jako LIMIT (kind='limit')."
            "\n  daytrading_backtester.py:849 liczy wtedy (maker + taker) = {:.6f}"
            "\n  expected_net_r.py:220     liczy zawsze (2 x taker)  = {:.6f}"
            "\n  Przeplata: {:.1f}%.  fee_r w tym sygnale = {:.6f} zamiast {:.6f}."
            "\n  Prowizje sa dzis CALA pozostala masa kosztowa modelu."
            .format(want, got, 100.0 * (got / want - 1.0),
                    br["fee_r"], round(want / br["sl_dist"], 6)))


if __name__ == "__main__":
    unittest.main()
