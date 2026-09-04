# -*- coding: utf-8 -*-
"""Bramka pomiarowa musi wiedziec, czy jej wejscie jest spojne.

tools/parity.py chodzi po WSPOLNYM indeksie baru. Dopoki nie sprawdzal, czy
zamrozone bundle opisuja ten sam kawalek czasu, potrafil podac liczbe z
wejscia, w ktorym "bar 5000" to dla BTC 19 sierpnia, a dla XRP 8 sierpnia -
i nie powiedziec o tym ani slowa. Cicha liczba z niespojnego wejscia jest
gorsza niz brak liczby, bo wyglada tak samo jak dobra.

Te testy pilnuja trzech rzeczy:
  1. pomiar rozjazdu okien jest poprawny,
  2. compare() czerwieni sie, gdy okna sie zmienia - i gdy zmienia sie
     JAKIEKOLWIEK pole meta (denylist, nie allowlist),
  3. znany rozjazd XRP/ZEC (263 h) jest zapisany w baseline jako swiadomie
     przyjeta wada, a nie jako cos, co przeoczono.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import parity  # noqa: E402

BASELINE = ROOT / "tests" / "baselines" / "parity_v2.json"

BAR_MS = 300_000
H_MS = 3_600_000
BTC_START = 1_785_618_900_000
# Rozjazd zmierzony wprost na zamrozonych bundlach: XRP i ZEC startuja
# 263 h (10 d 23 h) wczesniej niz BTC/ETH/SOL.
KNOWN_OFFSET_MIN = 263 * 60


def _bundle(start_ms, bars):
    return {"5m": {"timestamps": [start_ms + i * BAR_MS for i in range(bars)]}}


def _skeleton(windows):
    return {
        "meta": {"symbols": ["BTC"], "days": 30, "bars": 10},
        "config": {"hash": "abc", "watched": {}},
        "windows": windows,
        "totals": {"trades": 0, "net_r": 0.0},
        "funnel": {"rejected_for_slots": 0, "rejected_for_direction": 0, "reasons": {}},
        "trades": [],
    }


class TestWindowAlignmentMeasurement(unittest.TestCase):
    """1. Sam pomiar."""

    def test_aligned_bundles_report_zero_offset(self):
        out = parity.window_alignment({
            "BTC": _bundle(BTC_START, 100), "ETH": _bundle(BTC_START, 100),
        })
        self.assertTrue(out["aligned"])
        self.assertEqual(out["offset_min"], 0)
        self.assertEqual(out["symbols"]["BTC"]["bars"], 100)
        self.assertEqual(out["symbols"]["BTC"]["start"], out["symbols"]["ETH"]["start"])

    def test_misaligned_bundles_report_offset_and_overlap(self):
        """Ten sam ksztalt co realne wejscie: dwa okna przesuniete o 263 h."""
        bars = 8859
        out = parity.window_alignment({
            "BTC": _bundle(BTC_START, bars),
            "XRP": _bundle(BTC_START - 263 * H_MS, bars),
        })
        self.assertFalse(out["aligned"])
        self.assertEqual(out["offset_min"], KNOWN_OFFSET_MIN)
        # Wspolny kalendarzowo zakres to dlugosc okna minus rozjazd.
        span_h = (bars - 1) * BAR_MS / H_MS
        self.assertEqual(out["overlap_h"], round(span_h - 263, 1))

    def test_offset_is_measured_against_the_earliest_start(self):
        """Trzy okna: liczy sie najwiekszy rozjazd, nie sasiedni."""
        out = parity.window_alignment({
            "A": _bundle(BTC_START, 50),
            "B": _bundle(BTC_START - 1 * H_MS, 50),
            "C": _bundle(BTC_START - 5 * H_MS, 50),
        })
        self.assertEqual(out["offset_min"], 5 * 60)

    def test_empty_bundle_is_described_not_crashed(self):
        out = parity.window_alignment({
            "BTC": _bundle(BTC_START, 10), "PUSTY": {"5m": {}},
        })
        self.assertEqual(out["symbols"]["PUSTY"], {"bars": 0, "start": None, "end": None})
        self.assertEqual(out["symbols"]["BTC"]["bars"], 10)


class TestCompareSeesTheWindows(unittest.TestCase):
    """2. Pomiar bez porownania to tylko notatka."""

    def setUp(self):
        self.good = parity.window_alignment({
            "BTC": _bundle(BTC_START, 100), "XRP": _bundle(BTC_START, 100),
        })

    def test_compare_flags_a_window_that_moved(self):
        bad = parity.window_alignment({
            "BTC": _bundle(BTC_START, 100),
            "XRP": _bundle(BTC_START - 263 * H_MS, 100),
        })
        joined = "\n".join(parity.compare(_skeleton(self.good), _skeleton(bad)))
        self.assertIn("OKNA DANYCH", joined)
        self.assertIn("aligned", joined)
        self.assertIn("offset_min", joined)

    def test_compare_notices_every_field_of_the_window_record(self):
        """Sabotaz per pole: zadne nie moze przejsc niezauwazone."""
        for field in ("aligned", "offset_min", "overlap_h"):
            broken = json.loads(json.dumps(self.good))
            broken[field] = "SABOTAZ"
            problems = parity.compare(_skeleton(self.good), _skeleton(broken))
            self.assertTrue(any(field in line for line in problems),
                            f"pole {field} przeszlo bez alarmu")
        broken = json.loads(json.dumps(self.good))
        broken["symbols"]["XRP"]["start"] = "1999-01-01 00:00"
        problems = parity.compare(_skeleton(self.good), _skeleton(broken))
        self.assertTrue(any("XRP" in line for line in problems))

    def test_compare_flags_a_result_that_lost_its_window_section(self):
        """Usuniecie opisu okien z wyniku to tez roznica, a nie cisza."""
        before, after = _skeleton(self.good), _skeleton(self.good)
        after.pop("windows")
        problems = parity.compare(before, after)
        self.assertTrue(any("OKNA DANYCH" in line for line in problems))

    def test_compare_meta_is_a_denylist(self):
        """Nowy wymiar pomiaru w meta ma byc pilnowany od pierwszej chwili.

        Przy allowliscie mozna dodac pole, ktorego nikt nie wpisal na liste,
        a bramka nadal powie "IDENTYCZNIE" - dokladnie ten blad byl juz raz
        w exec_gate.compare().
        """
        win = parity.window_alignment({"BTC": _bundle(BTC_START, 10)})
        before, after = _skeleton(win), _skeleton(win)
        after["meta"]["zupelnie_nowe_pole"] = 7
        problems = parity.compare(before, after)
        self.assertTrue(any("zupelnie_nowe_pole" in line for line in problems))

    def test_compare_still_ignores_elapsed_s(self):
        """Czas wykonania to nie wlasciwosc wyniku."""
        win = parity.window_alignment({"BTC": _bundle(BTC_START, 10)})
        before, after = _skeleton(win), _skeleton(win)
        before["meta"]["elapsed_s"] = 61.0
        after["meta"]["elapsed_s"] = 74.3
        self.assertEqual(parity.compare(before, after), [])


class TestBaselineRecordsTheKnownDefect(unittest.TestCase):
    """3. Stan okien ma byc zapisany, a nie przeoczony.

    HISTORIA. Do v20.69.0 ten test asertowal, ze okna sa ROZJECHANE:
    XRP i ZEC obejmowaly inny miesiac niz BTC/ETH/SOL, rozjazd startow
    wynosil 15780 minut. Test kodowal znana wade jako stan oczekiwany -
    slusznie, bo dopoki wada istnieje, jej ciche zniknieciu tez trzeba
    sie przyjrzec.

    W v20.70.0 wada zostala usunieta: bundle przepobrano w jednym momencie
    i przyciete do wspolnego bara przez tools/align_bundles.py. Test
    odwrocil sie razem z rzeczywistoscia i pilnuje teraz przeciwnego
    stanu - bo teraz to ROZJAZD bylby regresja.
    """

    def test_baseline_records_aligned_windows(self):
        """Gdy ktos odswiezy bundle 30d bez przyciecia, ten test padnie.

        To jest zamierzone: przesuniecie baseline ma byc decyzja, a nie
        skutkiem ubocznym pobrania nowych danych. Naprawa to
        `python tools/align_bundles.py --days 30 --symbols BTC ETH SOL XRP ZEC`,
        a nie poluzowanie tej asercji.
        """
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        win = baseline.get("windows")
        self.assertTrue(win, "baseline bez opisu okien - python tools/parity.py --write-baseline")
        self.assertEqual(set(win["symbols"]), {"BTC", "ETH", "SOL", "XRP", "ZEC"})
        self.assertTrue(win["aligned"],
                        "okna sie rozjechaly - uruchom tools/align_bundles.py, "
                        "nie zmieniaj tego testu")
        self.assertEqual(win["offset_min"], 0)
        starty = {s: d["start"] for s, d in win["symbols"].items()}
        self.assertEqual(len(set(starty.values())), 1,
                         f"kazdy symbol ma zaczynac sie w tym samym barze: {starty}")


class TestExperimentFlagsRefuseToLookLikeMeasurements(unittest.TestCase):
    """4. Eksperyment ma sie nie przebrac za bramke.

    Obie sciezki ponizej koncza sie przed uruchomieniem replayu, wiec test jest
    tani i nie rusza globalnego configu.
    """

    def test_experiment_cannot_be_written_as_baseline(self):
        self.assertEqual(parity.main(["--final-gate", "--write-baseline"]), 2)
        self.assertEqual(
            parity.main(["--final-gate", "--prior-floor", "0.05", "--write-baseline"]), 2)

    def test_prior_floor_without_final_gate_is_refused(self):
        """Bez final_gate net_r_ok nie jest wolane - podmiana progu nic nie
        mierzy, a wynik wygladalby na pomiar."""
        self.assertEqual(parity.main(["--prior-floor", "0.05"]), 2)


if __name__ == "__main__":
    unittest.main()
