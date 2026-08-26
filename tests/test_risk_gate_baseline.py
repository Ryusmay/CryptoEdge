"""Bramka charakterystyki decyzji ryzyka chodzi w zestawie, nie tylko recznie.

Bramka parytetu pokrywa replay, a replay wola pipeline z risk=None i ma
wlasne limity slotow. Okolo 700 linii risk_manager.py - can_open_position()
i calculate_position_size() z pomocnikami - nie bylo objete niczym poza
testami jednostkowymi wybranych galezi.

Ten test nie zastepuje tamtych. Pilnuje czegos innego: ze przeniesienie
logiki do cryptoedge/risk/ nie zmienilo werdyktu dla 120 kontrolowanych
przypadkow. Jesli zmienilo - diff pokazuje ktore i jak.

Poza werdyktem i rozmiarem baseline trzyma teraz `size_mult` - mnoznik,
ktory can_open_position() wpisuje do sygnalu w galeziach, ktore NIE
odrzucaja (0.6 przy STRAT_FAIL + MTF, 0.5 przy konflikcie kierunku).
Bez tego bramka przepuscilaby zmiane mnoznika bez slowa.

Aktualizacja baseline jest swiadoma decyzja:
    python tools/risk_gate.py --write-baseline
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import risk_gate  # noqa: E402

BASELINE = ROOT / "tests" / "baselines" / "risk_gate.json"


class TestRiskGateMatchesBaseline(unittest.TestCase):
    def test_baseline_exists(self):
        self.assertTrue(
            BASELINE.is_file(),
            "Brak baseline ryzyka. Utworz: python tools/risk_gate.py --write-baseline",
        )

    def test_decisions_did_not_change(self):
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        current = json.loads(json.dumps(risk_gate.run_gate()))
        problems = risk_gate.compare(baseline, current)
        self.assertEqual(
            [], problems,
            "Decyzje ryzyka sie zmienily:\n" + "\n".join(problems) +
            "\n\nJesli zmiana jest zamierzona: python tools/risk_gate.py --write-baseline",
        )

    def test_gate_is_deterministic_within_one_process(self):
        first = json.loads(json.dumps(risk_gate.run_gate()))
        second = json.loads(json.dumps(risk_gate.run_gate()))
        self.assertEqual([], risk_gate.compare(first, second))


class TestRiskGateStaysMeaningful(unittest.TestCase):
    """Bramka bez pokrycia to falszywy spokoj."""

    def setUp(self):
        self.baseline = json.loads(BASELINE.read_text(encoding="utf-8"))

    def test_corpus_covers_many_distinct_reasons(self):
        self.assertGreaterEqual(len(self.baseline["reasons"]), 27)

    def test_corpus_has_both_approvals_and_rejections(self):
        meta = self.baseline["meta"]
        self.assertGreater(meta["approved"], 10)
        self.assertGreater(meta["rejected"], 10)

    def test_no_case_blows_up(self):
        """Wyjatek w bramce ryzyka jest bledem, nie decyzja."""
        raised = [r for r in self.baseline["results"]
                  if str(r.get("reason") or "").startswith("RAISED:")]
        self.assertEqual([], raised, f"Bramka rzuca wyjatkiem: {raised}")

    def test_key_protections_are_present_in_baseline(self):
        reasons = " | ".join(self.baseline["reasons"])
        for guard in ("INVALID_DIRECTION", "INVALID_PRICE", "NON_POSITIVE_NET_R",
                      "DAILY_PROJECTED_LOSS", "RISK_REDUCE_ONLY", "HEAT_LONG",
                      "STRAT_PRIMARY_FAIL", "STRAT_PRIMARY_CONFLICT",
                      "STRAT_NA_NO_MTF", "STRAT_NA_RANGE_WEAK", "STRAT_NA_WEAK",
                      "DAY_SETUP_NOT_CONFIRMED", "DAY_NON_NATIVE_SOURCE"):
            self.assertIn(guard, reasons, f"Baseline nie pokrywa juz {guard}")

    def test_size_multipliers_are_pinned(self):
        """Mnozniki z galezi, ktore przepuszczaja z mniejszym rozmiarem.

        To jedyny widoczny skutek tych galezi - werdykt brzmi OK w obu
        przypadkach. Gdyby 0.6 zamienilo sie w 0.5, bramka bez tego pola
        nie pisnelaby slowa.
        """
        by_case = {r["case"]: r for r in self.baseline["results"]}
        self.assertEqual(0.6, by_case["strat_fail_mtf_majority"]["size_mult"])
        self.assertEqual(0.5, by_case["strat_pass_conflict_with_mtf"]["size_mult"])
        self.assertIsNone(by_case["strat_pass_same_direction"]["size_mult"])

    def test_cooldowns_are_excluded_on_purpose(self):
        """Powod cooldownu niesie pozostale minuty z datetime.now() - w
        baseline dryfowalby. Ma go tam nie byc."""
        reasons = " | ".join(self.baseline["reasons"])
        self.assertNotIn("LOSS_STREAK_PAUSE", reasons)
        self.assertNotIn("COOLDOWN", reasons)


if __name__ == "__main__":
    unittest.main()
