# -*- coding: utf-8 -*-
"""Mnoznik filtra primary MUSI realnie zmniejszyc pozycje.

Do v20.23.0 nie zmniejszal. `can_open_position()` wpisywal
`signal["_size_mult"] = 0.6` juz PO tym, jak paper_trader policzyl rozmiar:

    open_position():
        size = risk.calculate_position_size(signal)   # rozmiar gotowy
        signal["_planned_notional"] = actual
        can_open, _ = risk.can_open_position(signal)  # tu dopiero 0.6
        pos = Position(signal, size)                  # uzyty size z gory

Ani `Position`, ani `paper_trader` nie czytaja `_size_mult` - jedynym jego
konsumentem jest `calculate_position_size()` (przez `adaptive_size`), a ta
juz sie wykonala. "Wpusc z mniejszym size" wpuszczalo z pelnym.

Ten test nie patrzy na ksztalt kodu, tylko na liczbe: ile realnie wyjdzie
z sizingu. Testy strukturalne mozna obejsc refaktorem, tego nie.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from cryptoedge.risk import strategy_filter as sf
from risk_manager import RiskManager


def signal(**kw):
    base = {
        "symbol": "BTC", "direction": "LONG", "price": 100.0, "strength": 0.75,
        "trend_score": 0.75, "engine": "trend", "strategy_mode": "SWING",
        "sl_price": 96.0, "expected_net_r": 0.9, "expected_r_status": "OK",
        "market_regime": "TREND_UP", "atr_pct": 1.2, "leverage": 10,
    }
    base.update(kw)
    return base


class TestPrimaryMultiplierReachesTheSize(unittest.TestCase):

    def setUp(self):
        self.risk = RiskManager(starting_capital=1000.0)
        self.risk.current_capital = 1000.0
        self.risk.paper_capital = 1000.0
        self.risk.daily_start_capital = 1000.0
        self.risk.peak_equity = 1000.0
        self.risk.paper_peak_equity = 1000.0
        self.risk.last_regime = "TREND_UP"
        self.risk._positions_ref = []
        self.risk._reconciler_ref = None
        self.baseline = self.risk.calculate_position_size(signal())
        self.assertGreater(self.baseline, 0, "sygnal bazowy musi cos wypelnic")

    def size_for(self, **kw):
        return self.risk.calculate_position_size(signal(**kw))

    def test_strat_fail_with_mtf_is_reduced(self):
        got = self.size_for(strategy={"pass": False}, mtf={"long_votes": 2})
        self.assertAlmostEqual(self.baseline * sf.STRAT_FAIL_SIZE_MULT, got, places=2)
        self.assertLess(got, self.baseline)

    def test_direction_conflict_with_mtf_is_reduced_more(self):
        got = self.size_for(strategy={"pass": True, "direction": "SHORT"},
                            mtf={"long_votes": 2})
        self.assertAlmostEqual(self.baseline * sf.STRAT_CONFLICT_SIZE_MULT, got, places=2)
        self.assertLess(got, self.size_for(strategy={"pass": False},
                                           mtf={"long_votes": 2}))

    def test_soft_align_is_reduced_like_mtf(self):
        got = self.size_for(strategy={"pass": False}, reasons=["STRAT_SOFT_ALIGN"])
        self.assertAlmostEqual(self.baseline * sf.STRAT_FAIL_SIZE_MULT, got, places=2)

    def test_clean_signal_is_not_reduced(self):
        got = self.size_for(strategy={"pass": True, "direction": "LONG"})
        self.assertAlmostEqual(self.baseline, got, places=2)

    def rev(self, **kw):
        base = {"engine": "reversal", "strategy_mode": "SWING",
                "strength": 0.60, "reversal_score": 0.60}
        base.update(kw)
        return self.size_for(**base)

    def test_reversal_reduction_reaches_the_size(self):
        """v20.24.0 - reversal omija adaptive_size, wiec przed ta wersja
        mnoznik przepadal. Trend dzialal, reversal nie."""
        clean = self.rev(strategy={"pass": True, "direction": "LONG"})
        cut = self.rev(strategy={"pass": False}, mtf={"long_votes": 2})
        self.assertLess(cut, clean, "redukcja nie dotarla do rozmiaru reversal")

    def test_reversal_conflict_is_reduced_more_than_fail(self):
        fail = self.rev(strategy={"pass": False}, mtf={"long_votes": 2})
        conflict = self.rev(strategy={"pass": True, "direction": "SHORT"},
                            mtf={"long_votes": 2})
        self.assertLess(conflict, fail)

    def test_reversal_clean_is_not_reduced(self):
        a = self.rev(strategy={"pass": True, "direction": "LONG"})
        b = self.rev()
        self.assertAlmostEqual(a, b, places=2)

    def test_apply_size_mult_helper(self):
        """Jeden wlasciciel nakladania mnoznika - takze dla zlych danych."""
        f = self.risk._apply_size_mult
        self.assertAlmostEqual(60.0, f(100.0, {"_size_mult": 0.6}))
        self.assertEqual(100.0, f(100.0, {}), "brak mnoznika = bez zmian")
        self.assertEqual(100.0, f(100.0, {"_size_mult": 1.0}))
        self.assertEqual(100.0, f(100.0, {"_size_mult": 1.5}),
                         "mnoznik > 1 nie moze podniesc rozmiaru")
        self.assertEqual(100.0, f(100.0, {"_size_mult": 0}))
        self.assertEqual(100.0, f(100.0, {"_size_mult": "duzo"}))
        self.assertEqual(100.0, f(100.0, {"_size_mult": None}))

    def test_v2_is_untouched(self):
        """V2 omija ten filtr - poprawka nie moze go dotknac."""
        v2 = {"engine": "daytrading_v2", "strategy_mode": "DAYTRADING_V2"}
        clean = self.size_for(**v2)
        with_strat = self.size_for(strategy={"pass": False},
                                   mtf={"long_votes": 2}, **v2)
        self.assertAlmostEqual(clean, with_strat, places=2)

    def test_gate_does_not_shrink_the_size_afterwards(self):
        """Sedno bledu: po can_open_position() rozmiar ma byc juz taki sam.

        Gdyby bramka nadal dokladala mnoznik po fakcie, sizing przed nia
        i po niej dalby rozne liczby - i ta przed nia (uzywana przez
        paper_trader) bylaby ta wieksza.
        """
        s = signal(strategy={"pass": False}, mtf={"long_votes": 2})
        before = self.risk.calculate_position_size(dict(s))
        approved, reason = self.risk.can_open_position(dict(s), open_directions=[])
        self.assertTrue(approved, reason)
        after = self.risk.calculate_position_size(dict(s))
        self.assertAlmostEqual(before, after, places=6)

    def test_multiplier_is_idempotent(self):
        """prepare_signal_for_sizing() bierze minimum, wiec dwa przebiegi
        nie moga zejsc do 0.36."""
        s = signal(strategy={"pass": False}, mtf={"long_votes": 2})
        self.risk.prepare_signal_for_sizing(s)
        self.risk.prepare_signal_for_sizing(s)
        self.assertAlmostEqual(sf.STRAT_FAIL_SIZE_MULT, float(s["_size_mult"]), places=6)


if __name__ == "__main__":
    unittest.main()
