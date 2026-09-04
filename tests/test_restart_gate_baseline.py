# -*- coding: utf-8 -*-
"""Bramka restartu chodzi w zestawie, nie tylko recznie.

Warunek wyjscia etapu 5: "testy restartu z pozycja, orphan orderem, partialem
i brakujacym SL". Wczesniej te scenariusze mialy pokrycie tylko na poziomie
samego rekoncyliatora (3 testy), wiec `RestartRecovery.run()` - czyli to, co
naprawde wykonuje sie przy starcie bota - bylo niepokryte.

Aktualizacja baseline jest swiadoma decyzja:
    python tools/restart_gate.py --write-baseline
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import restart_gate  # noqa: E402

BASELINE = ROOT / "tests" / "baselines" / "restart_gate.json"


class TestRestartGateMatchesBaseline(unittest.TestCase):

    def test_baseline_exists(self):
        self.assertTrue(
            BASELINE.is_file(),
            "Brak baseline restartu. Utworz: python tools/restart_gate.py --write-baseline",
        )

    def test_restart_did_not_change(self):
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        current = json.loads(json.dumps(restart_gate.run_gate()))
        problems = restart_gate.compare(baseline, current)
        self.assertEqual(
            [], problems,
            "Restart sie zmienil:\n" + "\n".join(problems[:40]) +
            "\n\nJesli zmiana jest zamierzona: python tools/restart_gate.py --write-baseline",
        )

    def test_gate_is_deterministic_within_one_process(self):
        """`ts` jest wycinane, atrapy nie maja zegara - dwa przebiegi musza
        dac to samo. Gdyby nie, baseline bylby bezwartosciowy."""
        first = json.loads(json.dumps(restart_gate.run_gate()))
        second = json.loads(json.dumps(restart_gate.run_gate()))
        self.assertEqual([], restart_gate.compare(first, second))


class TestRestartGateLeavesNoTraces(unittest.TestCase):
    """Bramka chodzi w tym samym interpreterze i katalogu co reszta zestawu."""

    def test_gate_never_creates_the_real_kill_switch_file(self):
        """Plik KILL_SWITCH zatrzymuje bota. Scenariusz kill-switcha jest
        pokryty przez podmiane `restart_recovery.Path`, nie przez dysk."""
        kill_file = ROOT / "KILL_SWITCH"
        existed = kill_file.exists()
        restart_gate.run_gate()
        self.assertEqual(existed, kill_file.exists(),
                         "Bramka dotknela prawdziwego pliku KILL_SWITCH")

    def test_gate_restores_patched_path(self):
        import restart_recovery as rr
        before = rr.Path
        restart_gate.run_gate()
        self.assertIs(before, rr.Path)

    def test_gate_restores_config(self):
        """FORCED_CONFIG i nadpisania per-przypadek musza zostac cofniete,
        inaczej bramka truje kazdy test uruchomiony po niej."""
        import config
        watched = sorted(set(restart_gate.FORCED_CONFIG) | {
            "PAPER_TRADING", "LIVE_EXECUTION_ENABLED"})
        before = {key: getattr(config, key, None) for key in watched}
        restart_gate.run_gate()
        after = {key: getattr(config, key, None) for key in watched}
        self.assertEqual(before, after)


class TestRestartGateStaysMeaningful(unittest.TestCase):

    def setUp(self):
        self.baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        self.by_case = {r["case"]: r for r in self.baseline["results"]}

    def test_no_case_blows_up(self):
        raised = [name for name, r in self.by_case.items() if r.get("raised")]
        self.assertEqual([], raised, f"Bramka rzuca wyjatkiem: {raised}")

    def test_all_four_exit_conditions_are_covered(self):
        for guard in ("local_position_only", "position_on_both_sides",
                      "exchange_position_without_local",
                      "orphan_orders_are_cancelled",
                      "partial_fill_exchange_smaller_than_local",
                      "partial_order_state_is_refreshed",
                      "missing_sl_falls_back_to_percent"):
            self.assertIn(guard, self.by_case, f"Baseline nie pokrywa juz {guard}")

    def test_protection_is_actually_rearmed(self):
        """Korpus, w ktorym nic sie nie uzbraja, niczego nie pilnuje."""
        self.assertGreater(self.baseline["meta"]["sl_rearmed"], 5)

    def test_partial_arms_on_exchange_size_not_on_local_book(self):
        """Ksiega lokalna mowi 3.0, gielda 1.0. Chronimy to, co istnieje.

        To rozbieznosc udokumentowana w docstringu bramki: `restart_recovery`
        czyta lokalny `size_contracts` tylko gdy gielda nie podala nic. Ksiega
        PAPER zostaje przy 3.0 i nikt jej nie koryguje - to zadanie dla ksiegi
        opartej na fillach, nie dla restartu. Pinujemy stan faktyczny, zeby
        zmiana byla swiadoma.
        """
        attach = [c for c in self.by_case[
            "partial_fill_exchange_smaller_than_local"]["protection_calls"]
            if c["op"] == "attach"]
        self.assertEqual(1, len(attach))
        self.assertEqual(1.0, attach[0]["size"])

    def test_trailing_stop_survives_restart_without_being_weakened(self):
        attach = [c for c in self.by_case[
            "persisted_trailing_sl_is_never_weakened"]["protection_calls"]
            if c["op"] == "attach"]
        self.assertEqual(99.0, attach[0]["sl"],
                         "Restart cofnal trailing SL do starszego snapshotu")

    def test_missing_sl_is_reconstructed_from_percent(self):
        attach = [c for c in self.by_case[
            "missing_sl_falls_back_to_percent"]["protection_calls"]
            if c["op"] == "attach"]
        self.assertEqual(1, len(attach), "Pozycja z gieldy zostala bez SL")
        self.assertGreater(attach[0]["sl"], 0)

    def test_live_failures_are_recorded_not_read_as_flat(self):
        """Pusta odpowiedz z gieldy nie moze wygladac jak brak pozycji."""
        for guard in ("exchange_position_query_failure", "reconcile_failure_is_recorded"):
            self.assertTrue(self.by_case[guard]["report"]["errors"],
                            f"{guard} przeszedl bez sladu bledu")

    def test_live_failure_forces_reduce_only(self):
        """Druga polowa warunku wyjscia etapu 5.

        Gdy nie potwierdzilismy stanu gieldy, brak pozycji NIE znaczy
        "plasko" - znaczy "nie wiem". `can_open_position` odrzuca wtedy
        sygnal na REDUCE_ONLY zanim sprawdzi cokolwiek innego, a zamykanie,
        anulowanie i ochrona dzialaja dalej.
        """
        for guard in ("exchange_position_query_failure",
                      "reconcile_failure_is_recorded",
                      "orphan_cancel_failure_is_recorded_not_swallowed"):
            risk = self.by_case[guard]["risk"]
            self.assertEqual("REDUCE_ONLY", risk["risk_state"],
                             f"{guard}: LIVE bez potwierdzenia wpuszcza nowe wejscia")
            self.assertTrue(str(risk["reduce_only_reason"] or "").startswith(
                "RECOVERY_UNCONFIRMED:"), f"{guard}: brak powodu blokady")

    def test_paper_failure_does_not_block_entries(self):
        """W PAPER nie ma gieldy, ktorej stanu nie potwierdzilismy."""
        risk = self.by_case["paper_reconcile_failure_does_not_block_entries"]["risk"]
        self.assertEqual("NORMAL", risk["risk_state"])
        self.assertTrue(
            self.by_case["paper_reconcile_failure_does_not_block_entries"][
                "report"]["errors"],
            "Przypadek nie odtwarza juz awarii - test nic nie sprawdza",
        )

    def test_healthy_restart_never_blocks_entries(self):
        """Blokada, ktora wlacza sie zawsze, jest tylko awaria pod inna nazwa."""
        for guard in ("clean_live_no_positions", "position_on_both_sides",
                      "orphan_orders_are_cancelled",
                      "partial_fill_exchange_smaller_than_local"):
            self.assertEqual("NORMAL", self.by_case[guard]["risk"]["risk_state"],
                             f"{guard}: zdrowy restart zablokowal wejscia")

    def test_orphan_on_exchange_halts_the_bot(self):
        risk = self.by_case["exchange_position_without_local"]["risk"]
        self.assertTrue(risk["halted"])
        self.assertEqual("RECOVERY_ORPHAN_EXCHANGE", risk["halt_reason"])

    def test_kill_switch_halts_and_paper_leftover_does_not(self):
        self.assertTrue(self.by_case["kill_switch_from_file_halts"]["risk"]["halted"])
        self.assertTrue(self.by_case["live_kill_switch_stays_halted"]["risk"]["halted"])
        self.assertFalse(
            self.by_case["paper_leftover_close_all_is_cleared"]["risk"]["halted"],
            "Leftover po przycisku Close-All w PAPER zablokowal handel")


if __name__ == "__main__":
    unittest.main()
