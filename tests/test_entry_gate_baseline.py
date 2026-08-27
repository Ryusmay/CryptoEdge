# -*- coding: utf-8 -*-
"""Bramka wykonania wejscia chodzi w zestawie.

risk_gate pilnuje DECYZJI (czy wolno wejsc), ta bramka pilnuje WYKONANIA:
co robi PaperTrader.open_position() od sygnalu do powstalej pozycji.

Kluczowe pole baseline to `signal_mutations` - lista kluczy dopisanych do
sygnalu wraz z wartosciami. Cztery bledy naprawione w v20.23.0-v20.26.0
wynikaly z kolejnosci tych mutacji; tutaj kolejnosc jest widoczna jako dane.

Aktualizacja baseline jest swiadoma decyzja:
    python tools/entry_gate.py --write-baseline
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import entry_gate  # noqa: E402

BASELINE = ROOT / "tests" / "baselines" / "entry_gate.json"


class TestEntryGateMatchesBaseline(unittest.TestCase):

    def test_baseline_exists(self):
        self.assertTrue(BASELINE.is_file(),
                        "Brak baseline wejscia: python tools/entry_gate.py --write-baseline")

    def test_entry_execution_did_not_change(self):
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        current = json.loads(json.dumps(entry_gate.run_gate()))
        problems = entry_gate.compare(baseline, current)
        self.assertEqual(
            [], problems,
            "Wykonanie wejscia sie zmienilo:\n" + "\n".join(problems[:40]) +
            "\n\nJesli zmiana jest zamierzona: python tools/entry_gate.py --write-baseline",
        )

    def test_gate_is_deterministic_within_one_process(self):
        first = json.loads(json.dumps(entry_gate.run_gate()))
        second = json.loads(json.dumps(entry_gate.run_gate()))
        self.assertEqual([], entry_gate.compare(first, second))


class TestEntryGateStaysMeaningful(unittest.TestCase):

    def setUp(self):
        self.baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        self.by_case = {r["case"]: r for r in self.baseline["results"]}

    def test_no_case_blows_up(self):
        raised = [r["case"] for r in self.baseline["results"] if r.get("raised")]
        self.assertEqual([], raised, f"Bramka rzuca wyjatkiem: {raised}")

    def test_corpus_opens_and_rejects(self):
        meta = self.baseline["meta"]
        self.assertGreater(meta["opened"], 3)
        self.assertGreater(meta["rejected"], 5)

    def test_half_spread_moves_entry_price(self):
        """Polowa spreadu jest doliczana PRZED sizingiem - to wlasnie ta
        kolejnosc byla kiedys zrodlem RISK_INVARIANT_FAIL tuz po wejsciu."""
        base = self.by_case["spread_baseline_no_book"]["position"]["entry_price"]
        long_px = self.by_case["spread_widens_entry_long"]["position"]["entry_price"]
        short_px = self.by_case["spread_widens_entry_short"]["position"]["entry_price"]
        self.assertGreater(long_px, base, "LONG wchodzi drozej o pol spreadu")
        self.assertLess(short_px, base, "SHORT wchodzi taniej o pol spreadu")

    def test_wider_entry_shrinks_size(self):
        """Szersze wejscie to wiekszy dystans do SL, wiec mniejszy rozmiar."""
        base = self.by_case["spread_baseline_no_book"]["position"]["size_usd"]
        widened = self.by_case["spread_widens_entry_long"]["position"]["size_usd"]
        self.assertLess(widened, base)

    def test_wide_spread_is_stopped_by_three_independent_gates(self):
        """Warte zapamietania: szeroki spread nie zmienia ceny, bo trade
        odpada wczesniej - i to na trzech roznych bramkach."""
        for case in ("wide_spread_hits_dynamic_limit", "wide_spread_hits_net_r",
                     "wide_spread_hits_ob_levels"):
            row = self.by_case[case]
            self.assertFalse(row["opened"], case)
            self.assertTrue(row["rejects"], f"{case} musi zapisac powod")

    def test_signal_mutations_are_recorded(self):
        """Bez tego pola bramka nie widzialaby zmiany kolejnosci mutacji."""
        row = self.by_case["open_v2_clean"]
        after_sizing = row["mutations_after_sizing"]
        after_gate = row["signal_mutations"]
        self.assertIn("_risk_pct", after_sizing,
                      "sizing musi zostawic slad swojego ryzyka")
        self.assertTrue(after_gate, "bramka tez cos dopisuje")

    def test_planned_notional_appears_only_after_the_gate(self):
        """Kolejnosc, ktora byla przyczyna czterech bledow: rozmiar jest
        policzony PRZED bramka, a `_planned_notional` dopisany dopiero
        pozniej. Zmiana tej kolejnosci zmieni baseline."""
        row = self.by_case["open_v2_clean"]
        self.assertNotIn("_planned_notional", row["mutations_after_sizing"])
        self.assertIn("_planned_notional", row["signal_mutations"])

    def test_open_position_does_not_touch_the_caller_signal(self):
        """open_position() pracuje na wlasnej kopii - slownik wolajacego
        zostaje czysty. Warto to przypiac, bo to jedyne miejsce w tej
        sciezce, ktore zachowuje sie higienicznie."""
        for row in self.baseline["results"]:
            if row.get("raised"):
                continue
            self.assertTrue(row["caller_signal_untouched"], row["case"])


if __name__ == "__main__":
    unittest.main()
