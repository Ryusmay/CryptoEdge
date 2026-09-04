# -*- coding: utf-8 -*-
"""Zmierzony spread ma zastapic stala - ale nigdy po cichu.

Stala DEFAULT_SPREAD_FRAC = 0.0004 opisywala wielkosc, ktora na uniwersum
zmienia sie 337-krotnie (BTC 0.0133 bps, TRUMP 4.48 bps). Zastapienie jej
pomiarem jest poprawa tylko wtedy, gdy widac, KTORE liczby sa zmierzone,
a ktore nadal pochodza ze stalej.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import venue_microstructure as vm  # noqa: E402
from expected_net_r import expected_net_r  # noqa: E402


def _signal(symbol="BTC", **kw):
    sig = {
        "symbol": symbol, "direction": "LONG", "engine": "daytrading_v2",
        "price": 100.0, "sl_price": 98.0,
        "tp_plan": {"frac_tp1": 0.5, "tp1_r": 1.5, "tp2_r": 2.2},
    }
    sig.update(kw)
    return sig


class TestLoader(unittest.TestCase):

    def setUp(self):
        vm.reset()

    def test_the_real_file_loads_and_covers_the_universe(self):
        syms = vm.measured_symbols()
        self.assertGreaterEqual(len(syms), 19)
        for must in ("BTC", "ETH", "SOL", "XRP", "ZEC"):
            self.assertIn(must, syms)

    def test_measured_spreads_span_orders_of_magnitude(self):
        """Sedno sprawy: jedna stala nie moze obsluzyc takiej rozpietosci."""
        btc = vm.spread_frac("BTC")
        trump = vm.spread_frac("TRUMP")
        self.assertIsNotNone(btc)
        self.assertIsNotNone(trump)
        self.assertGreater(trump / btc, 100.0)
        # Stala lezy PRZY GORNYM koncu rozkladu, nie posrodku.
        const = float(getattr(config, "DEFAULT_SPREAD_FRAC", 0.0004))
        self.assertLess(btc, const)
        self.assertGreater(trump, const)

    def test_unknown_symbol_gives_none_not_a_guess(self):
        self.assertIsNone(vm.spread_frac("NIE_MA_TAKIEGO"))
        self.assertIsNone(vm.top1_depth_usd("NIE_MA_TAKIEGO"))

    def test_symbol_suffixes_are_normalised(self):
        direct = vm.spread_frac("BTC")
        for variant in ("btc", "BTCUSDT", "BTC-USDT", "BTC/USDT"):
            self.assertEqual(vm.spread_frac(variant), direct, variant)

    def test_a_missing_file_degrades_instead_of_raising(self):
        """Produkcja ma dzialac dalej na stalej, a nie wywalic sie na braku pliku."""
        missing = Path(tempfile.mkdtemp()) / "nie_ma.json"
        try:
            vm.load(missing, force=True)
            self.assertIsNone(vm.spread_frac("BTC"))
            self.assertEqual(vm.measured_symbols(), set())
        finally:
            vm.reset()

    def test_a_corrupt_file_degrades_instead_of_raising(self):
        bad = Path(tempfile.mkdtemp()) / "zle.json"
        bad.write_text("to nie jest json", encoding="utf-8")
        try:
            vm.load(bad, force=True)
            self.assertIsNone(vm.spread_frac("BTC"))
        finally:
            vm.reset()

    def test_pointing_at_another_file_STAYS_pointed(self):
        """Regresja: wskazanie gubilo sie przy kolejnym load() bez argumentu,
        wiec plik uszkodzony i tak dawal poprawne odczyty z domyslnego."""
        bad = Path(tempfile.mkdtemp()) / "zle.json"
        bad.write_text("{}", encoding="utf-8")
        try:
            vm.load(bad, force=True)
            vm.load()          # bez argumentu - nie moze wrocic do domyslnego
            self.assertIsNone(vm.spread_frac("BTC"))
        finally:
            vm.reset()
        self.assertIsNotNone(vm.spread_frac("BTC"))

    def test_provenance_travels_with_the_numbers(self):
        prov = vm.provenance()
        self.assertTrue(prov.get("as_of"))
        self.assertTrue(prov.get("source"))
        # Venue musi byc jawne, bo to NIE jest BloFin, tylko proxy.
        self.assertEqual(prov.get("venue"), "binance_swap")
        self.assertIn("BloFin", str(prov.get("venue_note")))

    def test_top_of_book_depth_is_exposed_for_the_impact_denominator(self):
        """Wlasciwy mianownik dla malego zlecenia - nie obrot calej swiecy."""
        self.assertGreater(vm.top1_depth_usd("BTC", "ask"), 100000.0)
        # XMR ma najcienszy szczyt ksiegi w zestawie: zlecenie 50 USD to 16%.
        self.assertLess(vm.top1_depth_usd("XMR", "ask"), 1000.0)


class TestWiredIntoExpectedNetR(unittest.TestCase):

    def setUp(self):
        vm.reset()

    def test_a_measured_symbol_uses_the_measurement_and_says_so(self):
        br = expected_net_r(_signal("BTC"))
        self.assertEqual(br["spread_source"], "measured")
        # sl_dist = 2/100 = 0.02; spread BTC = 0.0133 bps = 1.331e-6
        self.assertAlmostEqual(br["spread_r"], 1.331e-6 / 0.02, places=6)

    def test_an_unmeasured_symbol_falls_back_and_says_so(self):
        br = expected_net_r(_signal("NIE_MA_TAKIEGO"))
        self.assertEqual(br["spread_source"], "default_const")
        const = float(getattr(config, "DEFAULT_SPREAD_FRAC", 0.0004))
        self.assertAlmostEqual(br["spread_r"], const / 0.02, places=6)

    def test_a_live_order_book_still_wins_over_the_snapshot(self):
        """Zywa ksiazka dotyczy TEJ chwili, snapshot jest sprzed godzin."""
        sig = _signal("BTC", order_book={"ob_spread_pct": 0.05})
        br = expected_net_r(sig)
        self.assertEqual(br["spread_source"], "order_book")
        self.assertAlmostEqual(br["spread_r"], 0.0005 / 0.02, places=6)

    def test_the_measured_spread_lowers_cost_for_btc(self):
        """Kierunek zmiany dla BTC: 4 bps -> 0.0133 bps to 300x mniej."""
        measured = expected_net_r(_signal("BTC"))["spread_r"]
        fallback = expected_net_r(_signal("NIE_MA_TAKIEGO"))["spread_r"]
        self.assertLess(measured, fallback / 100.0)

    def test_the_measured_spread_raises_cost_for_trump(self):
        """I kierunek odwrotny, bo stala nie jest po prostu 'za duza'."""
        trump = expected_net_r(_signal("TRUMP"))["spread_r"]
        fallback = expected_net_r(_signal("NIE_MA_TAKIEGO"))["spread_r"]
        self.assertGreater(trump, fallback)


if __name__ == "__main__":
    unittest.main()
