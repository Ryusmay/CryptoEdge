"""Dowod, ze wejscie przez PaperExecutionAdapter jest TYM SAMYM wejsciem.

Zanim petla glowna zacznie wolac port zamiast `trader.open_position`, trzeba
pokazac, ze podmiana miejsca wywolania niczego nie przesuwa. Ten test bierze
caly korpus `entry_gate` i przepuszcza go dwiema drogami:

    A. `trader.open_position(signal)`            - tak jak dzis w app.py:699
    B. `PaperExecutionAdapter.submit(...)`       - tak jak po migracji

i porownuje PELNE wiersze bramki: otwarcie, wiersz pozycji, odrzucenia,
mutacje sygnalu (razem z ich kolejnoscia), stan kolejki limitow, liczbe
pozycji i kapital. Rownosc tych wierszy jest warunkiem, ktory musi byc
spelniony PRZED przepieciem, a nie odkryty po nim.
"""

from __future__ import annotations

import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import entry_gate  # noqa: E402
import paper_trader as pt  # noqa: E402

from cryptoedge.execution import PaperExecutionAdapter, SubmitOrder  # noqa: E402


def _side_of(signal) -> str:
    return "buy" if str(signal.get("direction") or "").upper() == "LONG" else "sell"


def _make_routed_open(real_open, seen=None):
    """open_position, ktory idzie przez port zamiast prosto do ksiegi.

    Adapter dostaje maly obiekt venue, ktorego `open_position` wola JUZ
    oryginalna metode - inaczej podmiana na klasie zapetlilaby sie sama
    na siebie. `seen` zbiera zwrocone stany portu, zeby dalo sie sprawdzic
    nie tylko rownowaznosc, ale i to, ktore galezie w ogole padly.
    """

    def routed_open(self, signal):
        venue = SimpleNamespace(
            open_position=lambda sig: real_open(self, sig),
            has_pending_limit=self.has_pending_limit,
        )
        result = PaperExecutionAdapter(venue).submit(SubmitOrder(
            client_order_id="EQ-1",
            symbol=str(signal.get("symbol") or ""),
            side=_side_of(signal),
            quantity=Decimal("1"),
            metadata={"signal": signal},
        ))
        if seen is not None:
            seen.append((result.state, result.reason))
        # Kontrakt portu: pozycja tylko przy FILLED. ACCEPTED to zaparkowany
        # limit, czyli dokladnie ten przypadek, w ktorym stara sciezka tez
        # zwracala None.
        return result.raw if result.state == "FILLED" else None

    return routed_open


class PaperPortEntryEquivalenceTests(unittest.TestCase):

    def test_entry_through_the_port_matches_the_direct_call_case_by_case(self):
        cases = entry_gate.build_corpus()
        self.assertGreaterEqual(len(cases), 20, "korpus wejscia sie skurczyl")

        seen: list = []
        saved_stubs = entry_gate._install_stubs()
        real_open = pt.PaperTrader.open_position
        try:
            direct = [entry_gate.evaluate(case) for case in cases]
            pt.PaperTrader.open_position = _make_routed_open(real_open, seen)
            try:
                routed = [entry_gate.evaluate(case) for case in cases]
            finally:
                pt.PaperTrader.open_position = real_open
        finally:
            entry_gate._restore_stubs(saved_stubs)

        # Rownowaznosc jest bezwartosciowa, jesli obie drogi po prostu
        # odmawiaja. Kazda z trzech galezi portu musi tu naprawde paść.
        states = {state for state, _ in seen}
        self.assertIn("FILLED", states, "port nie wypelnil ani jednego wejscia")
        self.assertIn("REJECTED", states, "port nie odrzucil ani jednego wejscia")
        self.assertIn(
            ("ACCEPTED", "PAPER_LIMIT_PARKED"), set(seen),
            "port nie zaparkowal ani jednego limitu - galaz working order "
            "nie jest sprawdzona wobec prawdziwego PaperTradera",
        )

        differences = []
        for before, after in zip(direct, routed):
            if json.dumps(before, sort_keys=True) != json.dumps(after, sort_keys=True):
                for key in sorted(set(before) | set(after)):
                    if before.get(key) != after.get(key):
                        differences.append(
                            f"{before.get('case')}.{key}: "
                            f"{before.get(key)!r} -> {after.get(key)!r}"
                        )
        self.assertEqual(
            [], differences,
            "Wejscie przez port zmienilo zachowanie:\n" + "\n".join(differences[:40]),
        )

    def test_the_corpus_actually_exercises_both_outcomes(self):
        """Bez tego test powyzej moglby porownywac same odrzucenia.

        Przypadek, ktory nic nie testuje, wyglada identycznie jak przypadek,
        ktory testuje wszystko - stad ta asercja.
        """
        saved_stubs = entry_gate._install_stubs()
        try:
            rows = [entry_gate.evaluate(case) for case in entry_gate.build_corpus()]
        finally:
            entry_gate._restore_stubs(saved_stubs)
        opened = [r for r in rows if r.get("opened")]
        refused = [r for r in rows if not r.get("opened") and not r.get("raised")]
        self.assertTrue(opened, "korpus nie otwiera ani jednej pozycji")
        self.assertTrue(refused, "korpus nie odrzuca ani jednego wejscia")


if __name__ == "__main__":
    unittest.main()
