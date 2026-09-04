"""Replay musi nagrywac cene limitu i cene wyjscia.

Po co. Zbior wynikow (tools/outcome_dataset.py) jest wejsciem do
przeprojektowania strony brutto modelu. Bez tych dwoch pol nie da sie
z niego odczytac:

  exit_price   GDZIE lada wyjscia. 45% transakcji konczy sie w przedziale
               +/-0.25R, czego model dwustanowy (TP1 albo -1R) nie widzi.
               Bez ceny wyjscia zostaje samo realised_r, czyli wynik bez
               umiejscowienia.
  limit_price  Jakosc fillu. limit_touched() zwraca CENE OTWARCIA, gdy bar
               otworzyl sie juz za limitem - czyli dostajemy LEPIEJ niz
               planowano. Bez zapisanego limitu ta poprawa jest niewidoczna
               i wyglada jak zwykle wejscie.

Przebieg 90d bez tych pol bylby zmarnowany - stad ten plik powstal PRZED
przebiegiem, nie po nim.
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from daytrading_backtester import (  # noqa: E402
    ReplayTradeV2, _open_trade_v2, _process_trade_bar_v2, resolve_v2_fill,
)
from tools.outcome_dataset import _row  # noqa: E402


def _sig(**over):
    s = {"direction": "LONG", "symbol": "BTC", "sl_price": 96.0,
         "tp1_price": 103.0, "tp2_price": 106.0}
    s.update(over)
    return s


def _trade(**over):
    t = ReplayTradeV2(
        direction="LONG", entry_i=0, entry=100.0, sl=96.0, tp1=103.0,
        tp2=106.0, tp1_frac=0.5, initial_risk=4.0, highest=100.0, lowest=100.0,
    )
    for k, v in over.items():
        setattr(t, k, v)
    return t


class TestLimitPriceRecorded(unittest.TestCase):

    def test_limit_price_lands_on_trade(self):
        t = _open_trade_v2(0, 99.0, _sig(limit_price=99.0), tp1_frac=0.5)
        self.assertIsNotNone(t)
        self.assertAlmostEqual(t.limit_price, 99.0, places=10)

    def test_no_limit_price_stays_none(self):
        t = _open_trade_v2(0, 100.0, _sig(), tp1_frac=0.5)
        self.assertIsNotNone(t)
        self.assertIsNone(t.limit_price)

    def test_fill_better_than_limit_is_recoverable(self):
        """Bar otwiera sie PONIZEJ limitu -> fill po cenie otwarcia.

        To jest ten przypadek, dla ktorego to pole powstalo: dostajemy
        lepiej niz planowano, a bez zapisanego limitu nie da sie tego
        odroznic od zwyklego wejscia.
        """
        limit = 99.0
        open_ = 98.2                      # rynek przeskoczyl limit w dol
        fill, kind = resolve_v2_fill(_sig(limit_price=limit), i=1, signal_i=0,
                                     open_=open_, high=99.5, low=98.0)
        self.assertEqual(kind, "limit")
        self.assertAlmostEqual(fill, open_, places=10)
        self.assertLess(fill, limit, "fill musi byc LEPSZY niz limit")

        t = _open_trade_v2(1, fill, _sig(limit_price=limit), tp1_frac=0.5)
        self.assertAlmostEqual(t.entry, open_, places=10)
        self.assertAlmostEqual(t.limit_price, limit, places=10)
        # Poprawa jest odzyskiwalna ze zbioru:
        self.assertAlmostEqual(t.limit_price - t.entry, 0.8, places=10)

    def test_fill_exactly_at_limit(self):
        fill, kind = resolve_v2_fill(_sig(limit_price=99.0), i=1, signal_i=0,
                                     open_=100.0, high=100.5, low=98.5)
        self.assertEqual(kind, "limit")
        self.assertAlmostEqual(fill, 99.0, places=10)


class TestExitPriceRecorded(unittest.TestCase):
    """exit_price musi byc ustawione na KAZDEJ sciezce zamkniecia."""

    def _run(self, trade, high, low, close, max_bars=100):
        return _process_trade_bar_v2(
            trade, i=5, high=high, low=low, close=close,
            htf_bias=None, htf_trail_anchor=None,
            fee_frac_round_trip=0.0008, slippage_frac_round_trip=0.0,
            max_bars=max_bars, tp2_frac=0.5,
        )

    def test_stop_loss_records_exit_price(self):
        t = _trade()
        closed = self._run(t, high=100.5, low=95.0, close=95.5)
        self.assertTrue(closed)
        self.assertEqual(t.exit_reason, "sl")
        self.assertIsNotNone(t.exit_price)
        self.assertAlmostEqual(t.exit_price, t.sl, places=6)

    def test_hard_time_stop_records_exit_price(self):
        t = _trade()
        closed = self._run(t, high=100.2, low=99.8, close=100.1, max_bars=1)
        self.assertTrue(closed)
        self.assertIn(t.exit_reason, ("hard_time_stop", "time_stop"))
        self.assertIsNotNone(t.exit_price)

    def test_open_trade_has_no_exit_price(self):
        t = _trade()
        closed = self._run(t, high=100.2, low=99.8, close=100.1)
        self.assertFalse(closed)
        self.assertIsNone(t.exit_price)


class TestDatasetCarriesBothFields(unittest.TestCase):
    """Pola musza dojsc do zbioru, nie tylko do obiektu w pamieci."""

    def test_row_contains_prices(self):
        t = _trade(limit_price=99.0, exit_price=103.0, exit_i=7,
                   fee_rt=0.0008, realised_r=0.6)
        row = _row(t, "BTC", ts5=[0] * 20)
        self.assertIn("limit_price", row)
        self.assertIn("exit_price", row)
        self.assertIn("fee_rt", row)
        self.assertAlmostEqual(row["limit_price"], 99.0, places=10)
        self.assertAlmostEqual(row["exit_price"], 103.0, places=10)
        self.assertAlmostEqual(row["fee_rt"], 0.0008, places=10)

    def test_row_tolerates_missing_prices(self):
        row = _row(_trade(exit_i=7), "BTC", ts5=[0] * 20)
        self.assertIsNone(row["limit_price"])
        self.assertIsNone(row["exit_price"])


if __name__ == "__main__":
    unittest.main()
