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

    def test_compare_checks_every_field_not_an_allowlist(self):
        """Bramka nie moze przestac patrzec na pole przez przeoczenie.

        `compare` mialo kiedys krotke dozwolonych nazw. Sabotaz pokazal, ze
        skreslenie z niej `port_result` wylaczalo kontrole calej granicy portu,
        a bramka dalej mowila "IDENTYCZNIE" - i zaden inny test tego nie widzial,
        bo psuty byl sam komparator. Stad ten test: pilnuje, ze pomijane jest
        WYLACZNIE to, co ma byc pomijane.
        """
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        first = dict(baseline["results"][0])
        for field in sorted(set(first) - {"case", "kind"}):
            mutated = json.loads(json.dumps(baseline))
            mutated["results"][0][field] = "SABOTAZ"
            self.assertNotEqual(
                [], exec_gate.compare(baseline, mutated),
                f"compare() nie zauwaza zmiany pola {field!r}",
            )

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

    def _port(self, case):
        return self.by_case[case]["port_result"]

    def test_partial_fill_is_distinguishable_from_a_full_one(self):
        """`accepted` mowi, czy venue przyjal komende - NIE czy mam pozycje.

        Przed v20.43.0 zlecenie wypelnione w 0.4 z 1.0 i zlecenie wypelnione
        w calosci dawaly przez port identyczny wynik `accepted=True` bez sladu
        po ilosci. Bot zaksiegowalby caly zamowiony rozmiar.
        """
        full, partial = self._port("port_submit_full_fill"), self._port("port_submit_partial_fill")
        self.assertTrue(full["accepted"])
        self.assertTrue(partial["accepted"])
        self.assertEqual(1.0, full["filled_quantity"])
        self.assertEqual(0.4, partial["filled_quantity"])
        self.assertEqual(1.0, partial["requested"], "korpus przestal badac partial")

    def test_accepted_but_nothing_filled_yet_is_visible(self):
        row = self._port("port_submit_nothing_filled_yet")
        self.assertTrue(row["accepted"], "venue przyjal zlecenie")
        self.assertEqual(0.0, row["filled_quantity"], "ale nic sie nie wypelnilo")

    def test_uncertain_states_report_unknown_not_zero(self):
        """TIMEOUT i UNKNOWN znacza 'nie wiem', nie 'nic nie kupilem'.

        `Order.filled_size` stoi wtedy na domyslnym zerze - przepuszczenie go
        przez port bylo by zaproszeniem do wejscia drugi raz, czyli dokladnie
        tym, przed czym broni kontrakt TIMEOUT != REJECTED warstwe nizej.
        """
        for case in ("port_submit_timeout", "port_submit_row_not_matched"):
            row = self._port(case)
            self.assertIsNone(row["filled_quantity"], case)
            self.assertIsNone(row["average_price"], case)
            self.assertEqual(0.0, row["raw_knows_filled"],
                             f"{case}: surowy obiekt nadal ma mylace zero")

    def test_both_port_implementations_report_the_filled_quantity(self):
        """Kontrakt portu ma znaczyc to samo po obu stronach - inaczej
        replay i PAPER rozjada sie z gielda w tym, co znaczy 'przyjete'."""
        self.assertEqual(0.4, self._port("port_paper_partial_fill")["filled_quantity"])
        self.assertEqual(1.0, self._port("port_paper_full_fill")["filled_quantity"])

    def test_no_accepted_result_hides_the_fill_quantity(self):
        self.assertEqual(0, self.baseline["meta"]["accepted_without_fill_quantity"])

    def test_no_case_ever_submits_an_order_twice(self):
        """Kontrakt etapu 5. Gdy gielda nie odpowie, zlecenie MOGLO dojsc -
        wiec drugi POST bylby druga pozycja. Wolno tylko zapytac."""
        self.assertEqual(0, self.baseline["meta"]["retried_submits"])

    def test_timeout_is_followed_by_a_query_on_the_same_id(self):
        row = self.by_case["idem_timeout_never_reposts"]
        self.assertEqual(1, row["submits"], "zlecenie poszlo wiecej niz raz")
        posts = [t for t in row["cid_trace"]
                 if t["method"] == "POST" and t["endpoint"].endswith("/trade/order")]
        queries = [t for t in row["cid_trace"] if t["method"] == "GET"]
        self.assertEqual(1, len(posts))
        self.assertTrue(queries, "po timeoucie nikt nie zapytal o zlecenie")
        self.assertEqual(posts[0]["cid"], queries[0]["cid"],
                         "zapytanie poszlo po INNYM identyfikatorze")

    def test_unmatched_row_after_timeout_is_not_adopted(self):
        """Po timeoucie przychodzi wiersz cudzego zlecenia. Przyjecie go
        znaczyloby, ze bot uzna obca pozycje za swoja."""
        self.assertEqual(["UNKNOWN"], self.by_case["idem_timeout_never_reposts"]["states"])

    def test_caller_supplied_id_is_sent_verbatim(self):
        """Idempotencje trzyma wolajacy - wiec jego identyfikator musi
        dojsc na gielde niezmieniony, inaczej dwa wywolania to dwa zlecenia."""
        self.assertEqual(1, self.by_case["idem_caller_supplied_id_is_reused"]["distinct_cids"])

    def test_generated_id_is_new_on_every_call(self):
        """Zapisane, bo to NIE jest blad, tylko podzial odpowiedzialnosci:
        executor nie deduplikuje, wiec wolajacy musi podac swoj identyfikator,
        jesli chce, zeby ponowienie bylo tym samym zleceniem."""
        self.assertEqual(2, self.by_case["idem_generated_id_is_new_each_call"]["distinct_cids"])

    def test_cancel_targets_the_order_that_was_placed(self):
        row = self.by_case["idem_cancel_targets_the_same_order"]
        self.assertEqual(1, row["distinct_cids"], "anulowanie poszlo w inne zlecenie")
        self.assertIn("CANCELED", row["states"])

    def test_orphan_orders_are_cancelled_but_active_ones_are_not(self):
        cancelled = self.by_case["cancel_orphans_one"]
        kept = self.by_case["cancel_orphans_keeps_active"]
        self.assertTrue(any("cancel-order" in r["path"] for r in cancelled["sent"]))
        self.assertFalse(any("cancel-order" in r["path"] for r in kept["sent"]))


if __name__ == "__main__":
    unittest.main()
