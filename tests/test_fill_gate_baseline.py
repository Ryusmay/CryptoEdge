# -*- coding: utf-8 -*-
"""Bramka fillow chodzi w zestawie, nie tylko recznie.

Etap 5 ma oprzec ksiege na fillach. Zanim to sie stanie, ta bramka pilnuje,
ze ksiegowanie fillow - i to, jak bardzo mija sie z prawda - nie zmieni sie
przypadkiem. Kilka przypadkow pinuje ZNANE DEFEKTY: nie dlatego, ze sa
akceptowane, tylko dlatego, ze ich naprawa ma byc swiadoma zmiana, a nie
efekt uboczny refaktoru.

Aktualizacja baseline jest swiadoma decyzja:
    python tools/fill_gate.py --write-baseline
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

import fill_gate  # noqa: E402

BASELINE = ROOT / "tests" / "baselines" / "fill_gate.json"


class TestFillGateMatchesBaseline(unittest.TestCase):

    def test_baseline_exists(self):
        self.assertTrue(
            BASELINE.is_file(),
            "Brak baseline fillow. Utworz: python tools/fill_gate.py --write-baseline",
        )

    def test_fill_accounting_did_not_change(self):
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        current = json.loads(json.dumps(fill_gate.run_gate()))
        problems = fill_gate.compare(baseline, current)
        self.assertEqual(
            [], problems,
            "Ksiegowanie fillow sie zmienilo:\n" + "\n".join(problems[:40]) +
            "\n\nJesli zmiana jest zamierzona: python tools/fill_gate.py --write-baseline",
        )

    def test_gate_is_deterministic_within_one_process(self):
        first = json.loads(json.dumps(fill_gate.run_gate()))
        second = json.loads(json.dumps(fill_gate.run_gate()))
        self.assertEqual([], fill_gate.compare(first, second))

    def test_gate_never_patches_the_executor_class(self):
        """Bramka podmienia `_request` na INSTANCJI. Podmiana na klasie
        przezylaby przebieg i otrula kazdy test uruchomiony pozniej."""
        from blofin_executor import BloFinExecutor
        before = BloFinExecutor._request
        fill_gate.run_gate()
        self.assertIs(before, BloFinExecutor._request)


class TestFillGateStaysMeaningful(unittest.TestCase):

    def setUp(self):
        self.baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        self.by_case = {r["case"]: r for r in self.baseline["results"]}

    def _final(self, case):
        return self.by_case[case]["steps"][-1]

    def test_no_case_blows_up(self):
        raised = [name for name, r in self.by_case.items() if r.get("raised")]
        self.assertEqual([], raised, f"Bramka rzuca wyjatkiem: {raised}")

    def test_happy_paths_reconstruct_the_truth_exactly(self):
        """Jesli sciezka szczesliwa przestanie sie zgadzac z prawda, to znak,
        ze zepsula sie UPRZEZ, a nie kod - i cala reszta bramki jest wtedy
        bezwartosciowa."""
        for guard in ("single_full_fill", "two_partials_at_one_price",
                      "role_missing_on_limit_order", "fee_reported_cumulatively"):
            final = self._final(guard)
            self.assertEqual([], final["order_vs_truth"], f"{guard}: Order")
            self.assertEqual([], final["ledger_vs_truth"], f"{guard}: ksiega")

    def test_moving_price_partials_use_the_venue_average_not_our_guess(self):
        """Srednia z gieldy jest autorytatywna dla calego zlecenia.

        Ceny POJEDYNCZEGO fillu z migawki `order-detail` odtworzyc sie nie da,
        wiec rekonstrukcja VWAP z przyrostow dawala 100.3986 zamiast 100.9.
        `refresh_order` bierze teraz srednia wprost z wiersza.

        Odwrotna strona tego wyniku jest wazniejsza dla migracji: `FillLedger`
        liczy VWAP z faktow per transakcja, ktorych ta sciezka nie dostarcza,
        wiec sam z siebie jest tu MNIEJ dokladny niz kod, ktory miałby
        zastapic. Ten test pilnuje obu polowek naraz.
        """
        final = self._final("partial_fills_at_moving_prices")
        self.assertEqual(100.9, final["truth"]["avg_price"])
        self.assertEqual(100.9, final["order"]["avg_price"],
                         "Wrocila rekonstrukcja VWAP z przyrostow")
        self.assertEqual(100.3985714286, final["ledger"]["vwap"],
                         "Ksiega zaczela sie zgadzac - sprawdz, skad wziela cene")

    def test_quantity_without_price_is_not_taken_at_face_value(self):
        """Ilosc bez ceny nie wchodzi do ksiegi wcale.

        Wczesniej wchodziła galezia else bez zdarzenia fillu, a kolejny odczyt
        liczyl VWAP tak, jakby ta czesc poszla po cenie ZERO - `avg_fill_price`
        wychodzilo 50.0 przy kazdym fillu po 100.0.
        """
        final = self._final("quantity_without_price_then_price")
        self.assertEqual(100.0, final["truth"]["avg_price"])
        self.assertEqual(100.0, final["order"]["avg_price"])
        self.assertEqual(10.0, final["order"]["filled"])
        # Gielda scalila dwa fille w jedna migawke - tego sie nie rozdzieli.
        self.assertEqual(["zdarzen 1 zamiast 2"], final["order_vs_truth"])

    def test_single_step_quantity_without_price_says_nothing_rather_than_wrong(self):
        """'Nie wiem' jest lepsze niz 'wiem zle'."""
        final = self._final("quantity_without_average_price")
        self.assertEqual(0.0, final["order"]["filled"])
        self.assertIsNone(final["order"]["avg_price"])
        self.assertEqual("FILL_WITHOUT_PRICE", final["order"]["last_error"])

    def test_backwards_filled_size_is_refused(self):
        final = self._final("filled_size_goes_backwards")
        self.assertEqual(8.0, final["order"]["filled"], "Ksiega cofnela ilosc")
        self.assertEqual(8.0, final["order"]["events_qty"])
        self.assertEqual("FILLED_SIZE_WENT_BACKWARDS", final["order"]["last_error"])

    def test_incremental_fees_are_lost_by_the_cumulative_heuristic(self):
        final = self._final("fee_reported_incrementally")
        self.assertEqual(0.6, final["truth"]["fee"])
        self.assertEqual(0.3, final["order"]["fee"])
        self.assertEqual(0.6, final["ledger"]["fee"])

    def test_overfill_is_recorded_with_a_marker_and_refused_by_lifecycle(self):
        """Fill, ktory sie wydarzyl, musi trafic do ksiegi - odmowa zapisu
        kazalaby botowi wierzyc, ze na gieldzie nie ma pozycji. Zostaje wiec
        zapisany i OZNACZONY. `OrderLifecycle` odmawia: rozjazd kontraktow do
        rozstrzygniecia przy podmianie ksiegi."""
        final = self._final("overfill_beyond_requested")
        self.assertEqual(12.0, final["order"]["filled"])
        self.assertEqual("OVERFILL", final["order"]["last_error"])
        self.assertIn("exceeds remaining", final["ledger_step"].get("raised", ""))

    def test_transport_failures_never_look_like_an_empty_order(self):
        """Timeout, pusta odpowiedz i cudzy wiersz musza dawac UNKNOWN,
        a nie ciche zero."""
        for guard in ("refresh_timeout", "empty_response",
                      "foreign_order_row_is_not_matched"):
            self.assertEqual("UNKNOWN", self._final(guard)["order"]["state"], guard)
        for guard in ("partial_then_timeout", "partial_then_foreign_row"):
            final = self._final(guard)
            self.assertEqual("UNKNOWN", final["order"]["state"], guard)
            self.assertEqual(4.0, final["order"]["filled"],
                             f"{guard}: awaria skasowala wypelniona ilosc")

    def test_lifecycle_records_the_fill_before_it_refuses_the_transition(self):
        """`apply_fill` nie jest atomowe: fakt trafia do ksiegi, a dopiero
        potem sprawdzane jest przejscie stanu."""
        final = self._final("fill_before_lifecycle_was_accepted")
        self.assertEqual("CREATED", final["ledger"]["status"])
        self.assertEqual(4.0, final["ledger"]["filled"],
                         "Fill nie zostal juz zapisany mimo bledu - przepisz baseline")
        self.assertIn("InvalidTransition", final["ledger_step"].get("raised", ""))

    def test_corpus_still_covers_both_books_being_wrong(self):
        """Korpus, w ktorym nic sie nie rozjezdza, niczego nie pilnuje."""
        meta = self.baseline["meta"]
        self.assertGreaterEqual(meta["cases"], 20)
        self.assertGreater(meta["order_wrong"], 0)
        self.assertGreater(meta["ledger_wrong"], 0)

    def test_quantity_never_drifts_from_the_event_log(self):
        """`filled_size` musi sie zgadzac z suma zdarzen w KAZDYM przypadku.

        Ksiega, ktora wie ILE, ale nie wie SKAD, nie da sie ani zaudytowac,
        ani odtworzyc po restarcie. Wczesniej rozjezdzaly sie trzy przypadki.
        """
        self.assertEqual([], self.baseline["events_desync_cases"])


if __name__ == "__main__":
    unittest.main()
