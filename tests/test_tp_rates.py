# -*- coding: utf-8 -*-
"""Pomiar p(TP1)/p(TP2|TP1) ma nie klamac ani w gore, ani w dol.

Trzy rzeczy potrafia zepsuc taki pomiar po cichu:
  1. pulowanie nakladajacych sie raportow (ta sama transakcja liczona kilka razy),
  2. zgadywanie TP z progow R zamiast czytania jawnej flagi,
  3. podanie punktowego odsetka bez przedzialu, gdy probka ma 200 wierszy.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import tp_rates as tr  # noqa: E402


def _row(tp1=False, tp2=False, **kw):
    row = {"symbol": "BTC", "direction": "LONG", "profile": "major",
           "regime": "UNKNOWN", "exit_reason": "time_stop", "fill_kind": "limit",
           "tp1": tp1, "tp2": tp2, "realised_r": -0.05, "split": "in_sample",
           "mfe_r": 0.3, "mae_r": 0.4, "duration_bars_5m": 100}
    row.update(kw)
    return row


class TestWilson(unittest.TestCase):

    def test_interval_brackets_the_point_estimate(self):
        lo, hi = tr.wilson(3, 193)
        self.assertLess(lo, 3 / 193)
        self.assertGreater(hi, 3 / 193)

    def test_zero_hits_still_gives_an_upper_bound(self):
        """0/86 to nie jest 'zero i koniec' - gorna granica musi byc dodatnia."""
        lo, hi = tr.wilson(0, 86)
        self.assertEqual(lo, 0.0)
        self.assertGreater(hi, 0.0)
        self.assertLess(hi, 0.10)

    def test_smaller_sample_gives_a_wider_interval(self):
        _, hi_small = tr.wilson(0, 8)
        _, hi_big = tr.wilson(0, 200)
        self.assertGreater(hi_small, hi_big)

    def test_empty_sample_does_not_divide_by_zero(self):
        self.assertEqual(tr.wilson(0, 0), (0.0, 0.0))


class TestDedupKey(unittest.TestCase):

    def test_identical_trades_collapse(self):
        a, b = _row(), _row()
        self.assertEqual(tr._key(a), tr._key(b))

    def test_a_different_outcome_is_a_different_trade(self):
        self.assertNotEqual(tr._key(_row()), tr._key(_row(realised_r=0.5)))

    def test_float_noise_below_nine_places_does_not_split_a_trade(self):
        self.assertEqual(tr._key(_row(realised_r=0.1)),
                         tr._key(_row(realised_r=0.1 + 1e-12)))


class TestRates(unittest.TestCase):

    def test_tp2_is_conditioned_on_tp1_not_on_everything(self):
        rows = [_row(tp1=True, tp2=True), _row(tp1=True, tp2=False),
                _row(tp1=False, tp2=False), _row(tp1=False, tp2=False)]
        out = tr.rates(rows)
        self.assertEqual(out["p_tp1"], 0.5)
        # 1 z 2 transakcji, ktore dotknely TP1 - nie 1 z 4.
        self.assertEqual(out["p_tp2_given_tp1"], 0.5)

    def test_tp2_without_tp1_is_counted_and_surfaced(self):
        """Lancuch TP1 -> TP2 jest zalozeniem modelu. Jego zlamanie ma byc
        widoczne, a nie po cichu wchlaniane."""
        rows = [_row(tp1=False, tp2=True), _row(tp1=False, tp2=False)]
        out = tr.rates(rows)
        self.assertEqual(out["tp2_without_tp1"], 1)
        self.assertIsNone(out["p_tp2_given_tp1"])

    def test_no_trades_is_none_not_zero(self):
        out = tr.rates([])
        self.assertEqual(out["n"], 0)
        self.assertIsNone(out["p_tp1"])


class TestGrossAndBreakEven(unittest.TestCase):

    def test_gross_matches_the_engine_formula_at_the_prior(self):
        # -(1-0.55) + 0.55*(0.5*1.5 + 0.5*0.45*2.2)
        self.assertAlmostEqual(tr.gross_at(0.55, 0.45), 0.23475, places=5)

    def test_gross_is_minus_one_when_tp1_never_happens(self):
        self.assertAlmostEqual(tr.gross_at(0.0, 0.45), -1.0, places=6)

    def test_break_even_inverts_the_same_formula(self):
        be = tr.break_even_p1(median_gross=0.0476, median_cost=0.1432,
                              p2_given=0.45)
        # p1 odtworzone z brutto musi wrocic do tego samego brutto
        self.assertAlmostEqual(
            tr.gross_at(be["p1_implied_by_median_gross"], 0.45), 0.0476, places=3)
        # a p1 potrzebne do zera musi dac brutto rowne kosztowi
        self.assertAlmostEqual(
            tr.gross_at(be["p1_needed_for_zero_net"], 0.45), 0.1432, places=3)
        self.assertGreater(be["delta"], 0)


if __name__ == "__main__":
    unittest.main()
