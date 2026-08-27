# -*- coding: utf-8 -*-
"""Bramka egzekucji chodzi w zestawie.

Ostatnia nieobjeta powierzchnia: skladanie zlecen na gieldzie i uzgadnianie
stanu. Bez sieci - `BloFinExecutor` dostaje atrape sesji, rejestr i zegar.

Aktualizacja baseline jest swiadoma decyzja:
    python tools/exec_gate.py --write-baseline
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import exec_gate  # noqa: E402

BASELINE = ROOT / "tests" / "baselines" / "exec_gate.json"


class TestExecGateMatchesBaseline(unittest.TestCase):

    def test_baseline_exists(self):
        self.assertTrue(BASELINE.is_file(),
                        "Brak baseline: python tools/exec_gate.py --write-baseline")

    def test_execution_did_not_change(self):
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        current = json.loads(json.dumps(exec_gate.run_gate()))
        problems = exec_gate.compare(baseline, current)
        self.assertEqual(
            [], problems,
            "Egzekucja sie zmienila:\n" + "\n".join(problems[:40]) +
            "\n\nJesli zmiana jest zamierzona: python tools/exec_gate.py --write-baseline",
        )

    def test_gate_is_deterministic_within_one_process(self):
        first = json.loads(json.dumps(exec_gate.run_gate()))
        second = json.loads(json.dumps(exec_gate.run_gate()))
        self.assertEqual([], exec_gate.compare(first, second))

    def test_gate_never_touches_the_network(self):
        """Atrapa sesji jest jedynym wyjsciem na zewnatrz. Gdyby ktos wpial
        prawdziwe `requests.Session`, ten test tego nie wykryje - ale wykryje
        zmiane, ktora omija wstrzykniety `session`."""
        for case in exec_gate.build_executor_corpus():
            result = exec_gate.evaluate(case)
            self.assertIsNotNone(result.get("sent"), case["case"])


class TestExecGateStaysMeaningful(unittest.TestCase):

    def setUp(self):
        self.baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        self.by_case = {r["case"]: r for r in self.baseline["results"]}

    def test_no_case_blows_up(self):
        raised = [r["case"] for r in self.baseline["results"] if r.get("raised")]
        self.assertEqual([], raised, f"Bramka rzuca wyjatkiem: {raised}")

    def test_all_order_states_are_covered(self):
        for state in ("SUBMITTED", "FILLED", "PARTIAL", "CANCELED",
                      "REJECTED", "TIMEOUT"):
            self.assertIn(state, self.baseline["order_states"], state)

    def test_timeout_is_not_a_rejection(self):
        """Kontrakt tej warstwy. Gdy gielda nie odpowie, zlecenie MOGLO
        zostac zlozone. Zamiana TIMEOUT na REJECTED sprawi, ze bot uzna, iz
        pozycji nie ma - i wejdzie drugi raz."""
        row = self.by_case["place_market_timeout"]
        self.assertEqual("TIMEOUT", row["order"]["state"])
        self.assertNotEqual("REJECTED", row["order"]["state"])

    def test_api_error_is_a_rejection(self):
        self.assertEqual("REJECTED", self.by_case["place_market_api_error"]["order"]["state"])
        self.assertEqual("REJECTED", self.by_case["place_market_http_error"]["order"]["state"])

    def test_unknown_instrument_never_reaches_the_venue(self):
        row = self.by_case["place_unknown_instrument"]
        self.assertEqual("REJECTED", row["order"]["state"])
        self.assertEqual([], row["sent"], "nie wolno wyslac zlecenia na nieznany instrument")

    def test_too_small_order_never_reaches_the_venue(self):
        row = self.by_case["place_zero_size"]
        self.assertEqual("REJECTED", row["order"]["state"])
        self.assertEqual([], row["sent"])

    def test_drift_blocks_entries_only_in_live(self):
        """PAPER trzyma pozycje lokalnie, a konto BloFin jest puste -
        only_local to stan oczekiwany, nie rozjazd."""
        for name, row in self.by_case.items():
            if row.get("kind") != "reconciler":
                continue
            if name.endswith("_paper"):
                self.assertFalse(row["drift_blocks_entries"],
                                 f"{name} nie moze blokowac wejsc w PAPER")

    def test_drift_states_block_in_live(self):
        for name in ("only_local_live", "only_exchange_live",
                     "size_mismatch_live", "two_sided_drift_live"):
            self.assertTrue(self.by_case[name]["drift_blocks_entries"], name)

    def test_venue_silence_is_not_proof_of_flat_account(self):
        """Brak odpowiedzi z gieldy nie jest dowodem, ze nie ma pozycji.
        W LIVE fail closed."""
        row = self.by_case["venue_error_live"]
        self.assertFalse(row["in_sync"])
        self.assertTrue(row["drift_blocks_entries"])

    def test_orphan_orders_are_cancelled_but_active_ones_are_not(self):
        cancelled = self.by_case["cancel_orphans_one"]
        kept = self.by_case["cancel_orphans_keeps_active"]
        self.assertTrue(any("cancel-order" in r["path"] for r in cancelled["sent"]))
        self.assertFalse(any("cancel-order" in r["path"] for r in kept["sent"]))


if __name__ == "__main__":
    unittest.main()
