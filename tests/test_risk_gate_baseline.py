"""Bramka charakterystyki decyzji ryzyka chodzi w zestawie, nie tylko recznie.

Bramka parytetu pokrywa replay, a replay wola pipeline z risk=None i ma
wlasne limity slotow. Okolo 700 linii risk_manager.py - can_open_position()
i calculate_position_size() z pomocnikami - nie bylo objete niczym poza
testami jednostkowymi wybranych galezi.

Ten test nie zastepuje tamtych. Pilnuje czegos innego: ze przeniesienie
logiki do cryptoedge/risk/ nie zmienilo werdyktu dla 88 kontrolowanych
przypadkow. Jesli zmienilo - diff pokazuje ktore i jak.

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
        self.assertGreaterEqual(len(self.baseline["reasons"]), 18)

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
                      "DAILY_PROJECTED_LOSS", "RISK_REDUCE_ONLY", "HEAT_LONG"):
            self.assertIn(guard, reasons, f"Baseline nie pokrywa juz {guard}")

    def test_cooldowns_are_excluded_on_purpose(self):
        """Powod cooldownu niesie pozostale minuty z datetime.now() - w
        baseline dryfowalby. Ma go tam nie byc."""
        reasons = " | ".join(self.baseline["reasons"])
        self.assertNotIn("LOSS_STREAK_PAUSE", reasons)
        self.assertNotIn("COOLDOWN", reasons)


if __name__ == "__main__":
    unittest.main()
