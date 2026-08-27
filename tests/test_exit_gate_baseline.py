# -*- coding: utf-8 -*-
"""Bramka wyjsc chodzi w zestawie, nie tylko recznie.

Zamykanie pozycji nie mialo dotad zadnego pokrycia charakterystyki. Ten test
pilnuje, ze refaktor `check_exits()` - a to nastepny duzy kawalek migracji -
nie zmieni ani powodu, ani etykiety, ani stanu pozycji po ticku.

Aktualizacja baseline jest swiadoma decyzja:
    python tools/exit_gate.py --write-baseline
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import exit_gate  # noqa: E402

BASELINE = ROOT / "tests" / "baselines" / "exit_gate.json"


class TestExitGateMatchesBaseline(unittest.TestCase):

    def test_baseline_exists(self):
        self.assertTrue(
            BASELINE.is_file(),
            "Brak baseline wyjsc. Utworz: python tools/exit_gate.py --write-baseline",
        )

    def test_exits_did_not_change(self):
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        current = json.loads(json.dumps(exit_gate.run_gate()))
        problems = exit_gate.compare(baseline, current)
        self.assertEqual(
            [], problems,
            "Zamykanie pozycji sie zmienilo:\n" + "\n".join(problems[:40]) +
            "\n\nJesli zmiana jest zamierzona: python tools/exit_gate.py --write-baseline",
        )

    def test_gate_is_deterministic_within_one_process(self):
        """Zegar jest wstrzykiwany, kalibratory zaslepione - dwa przebiegi
        musza dac to samo. Gdyby nie, baseline bylby bezwartosciowy."""
        first = json.loads(json.dumps(exit_gate.run_gate()))
        second = json.loads(json.dumps(exit_gate.run_gate()))
        self.assertEqual([], exit_gate.compare(first, second))

    def test_gate_does_not_leave_its_stubs_in_sys_modules(self):
        """Bramka dziala w tym samym interpreterze co reszta testow.

        Atrapa zostawiona w sys.modules truje kazdy test uruchomiony pozniej:
        `from day_expectancy_calibration import DayExpectancyCalibrator`
        konczyl sie wtedy ImportError "unknown location" i wywracal 3 testy,
        ktore osobno przechodzily.
        """
        before = {name: sys.modules.get(name) for name in exit_gate._STUBBED_MODULES}
        exit_gate.run_gate()
        after = {name: sys.modules.get(name) for name in exit_gate._STUBBED_MODULES}
        self.assertEqual(before, after)
        from day_expectancy_calibration import DayExpectancyCalibrator  # noqa: F401


class TestExitGateStaysMeaningful(unittest.TestCase):

    def setUp(self):
        self.baseline = json.loads(BASELINE.read_text(encoding="utf-8"))

    def test_corpus_covers_many_distinct_reasons(self):
        self.assertGreaterEqual(len(self.baseline["reasons"]), 16)

    def test_no_case_blows_up(self):
        raised = [r["case"] for r in self.baseline["results"] if r.get("raised")]
        self.assertEqual([], raised, f"Bramka rzuca wyjatkiem: {raised}")

    def test_key_exit_paths_are_present(self):
        reasons = " | ".join(self.baseline["reasons"])
        for guard in ("stop_loss", "margin_call", "trailing_stop", "take_profit",
                      "partial_tp1", "partial_tp2", "early_loss_cut",
                      "opposite_signal", "supertrend_flip", "htf_opposite",
                      "day_time_stop", "local_emergency_sl", "local_emergency_tp"):
            self.assertIn(guard, reasons, f"Baseline nie pokrywa juz {guard}")

    def test_v2_lifecycle_reasons_are_present(self):
        """V2 decyduje przez czysty reduktor v2_trade_lifecycle - jego slownik
        akcji jest osobnym kontraktem."""
        reasons = " | ".join(self.baseline["reasons"])
        for guard in ("sl", "time_stop", "htf_reversal"):
            self.assertIn(guard, reasons)

    def test_exit_events_actually_happen(self):
        """Korpus, w ktorym nic sie nie zamyka, niczego nie pilnuje."""
        self.assertGreater(self.baseline["meta"]["exit_events"], 30)


if __name__ == "__main__":
    unittest.main()
