# -*- coding: utf-8 -*-
"""Wznawianie zbioru wynikow nie moze po cichu dublowac ani mieszac konfiguracji.

Przebieg na kilkunastu symbolach trwa okolo godziny i raz juz padl w polowie
razem z mostem do maszyny. Wznawianie jest wiec potrzebne - ale wznawianie
zrobione zle jest gorsze niz jego brak: zdublowane transakcje albo zbior
sklejony z dwoch konfiguracji wygladaja jak poprawny pomiar.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import outcome_dataset as od  # noqa: E402

HASH = "60c1af1e975f04f6"


def _write(lines):
    tmp = Path(tempfile.mkdtemp()) / "outcomes.jsonl"
    with tmp.open("w", encoding="utf-8") as fh:
        for row in lines:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return tmp


def _header(cfg=HASH, version=None):
    return {"_type": "header", "config_hash": cfg, "days": 90,
            "bot_version": version if version is not None else od._bot_version()}


def _trade(symbol="BTC"):
    return {"symbol": symbol, "realised_r": -0.05, "tp1": False, "tp2": False}


def _done(symbol, trades=1):
    return {"_type": "symbol_done", "symbol": symbol, "trades": trades}


class TestResume(unittest.TestCase):

    def test_missing_file_means_start_fresh(self):
        missing = Path(tempfile.mkdtemp()) / "nie_ma.jsonl"
        self.assertIsNone(od.completed_symbols(missing, HASH))

    def test_completed_symbols_are_read_from_markers(self):
        path = _write([_header(), _trade("BTC"), _done("BTC", 1),
                       _done("AAVE", 0)])
        self.assertEqual(od.completed_symbols(path, HASH), {"BTC", "AAVE"})

    def test_a_symbol_with_zero_trades_still_counts_as_done(self):
        """Inaczej symbol bez transakcji byl by liczony w kazdym przebiegu."""
        path = _write([_header(), _done("AAVE", 0)])
        self.assertIn("AAVE", od.completed_symbols(path, HASH))

    def test_a_different_config_refuses_to_append(self):
        """Zbior sklejony z dwoch konfiguracji wygladalby jak jeden pomiar.
        Dokladnie ten blad mielismy w probce 193 transakcji."""
        path = _write([_header("INNY_HASH"), _done("BTC", 5)])
        self.assertIsNone(od.completed_symbols(path, HASH))

    def test_a_different_code_version_refuses_to_append(self):
        """config_hash pilnuje USTAWIEN, nie KODU. Zmiana modelu kosztu
        (np. poslizgu) nie rusza config.py, a zmienia realised_r kazdej
        transakcji - wiec zbior sklejony z dwoch wersji kodu wygladalby jak
        jeden pomiar, i to trudniej zauwazalnie niz przy zmianie configu."""
        path = _write([_header(version="20.1.0"), _done("BTC", 5)])
        self.assertIsNone(od.completed_symbols(path, HASH))

    def test_old_format_with_rows_but_no_markers_refuses_to_append(self):
        """Plik z przerwanego przebiegu w starym formacie: sa transakcje, nie ma
        znacznikow. Nie da sie powiedziec, ktore symbole sa KOMPLETNE, wiec
        dopisanie zdublowaloby dane."""
        path = _write([_header(), _trade("AAVE"), _trade("BTC")])
        self.assertIsNone(od.completed_symbols(path, HASH))

    def test_header_only_file_resumes_with_nothing_done(self):
        """Swiezo zalozony plik to nie to samo co plik starego formatu."""
        path = _write([_header()])
        self.assertEqual(od.completed_symbols(path, HASH), set())

    def test_file_without_header_refuses_to_append(self):
        path = _write([_trade("BTC"), _done("BTC", 1)])
        self.assertIsNone(od.completed_symbols(path, HASH))

    def test_a_corrupt_line_does_not_abort_the_scan(self):
        tmp = Path(tempfile.mkdtemp()) / "outcomes.jsonl"
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(_header()) + "\n")
            fh.write("to nie jest json\n")
            fh.write(json.dumps(_done("BTC", 2)) + "\n")
        self.assertEqual(od.completed_symbols(tmp, HASH), {"BTC"})


class TestSymbolDiscovery(unittest.TestCase):

    def test_available_symbols_are_parsed_from_bundle_names(self):
        got = od.available_symbols(90)
        self.assertTrue(got, "brak bundli 90d - sprawdz data/replay")
        for name in got:
            self.assertNotIn("_", name.replace("1000BONK", "BONK"))
            self.assertNotIn(".json", name)
        # Zestaw wyrownany jest PODZBIOREM dostepnych, a nie calym zbiorem -
        # to jest sedno poprawki: do tego pomiaru wyrownanie okien nie ma
        # znaczenia, bo kazdy symbol jest odtwarzany niezaleznie.
        self.assertTrue(set(od.ALIGNED_90D).issubset(set(got)))
        self.assertGreater(len(got), len(od.ALIGNED_90D))

    def test_unknown_horizon_gives_nothing_rather_than_guessing(self):
        self.assertEqual(od.available_symbols(4242), [])


if __name__ == "__main__":
    unittest.main()
