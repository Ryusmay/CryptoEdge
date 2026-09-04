"""Przelacznik eksperymentu nie moze wlaczac sie sam przez domyslke.

01.09.2026: `dynamic_time_stop` w v2_trade_lifecycle czytal
DAYTRADING_V2_EARLY_CUT_HOURS z domyslka 12.0, a klucza NIE bylo w configu.
Regula dzialala wiec na produkcji, mimo ze config deklarowal wprost
"Controlled optimization experiments; all disabled in the production
baseline". Zaden test tego nie lapal, bo bramki nie mialy przypadku
starszego niz 12h z MFE < 0.3R i ujemnym markiem.

Ten plik pilnuje dwoch rzeczy naraz:
  1. przelaczniki sa w configu i stoja na wylaczonych (co deklaruje config);
  2. kod czyta je z domyslka, ktora TEZ jest wylaczona - wiec usuniecie
     klucza z configu nie wlacza eksperymentu po cichu.

Punkt 2 jest wazniejszy: to on zawiodl.
"""

from __future__ import annotations

import inspect
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import v2_trade_lifecycle  # noqa: E402
import daytrading_engine_v2  # noqa: E402

WYLACZONE = {
    "DAYTRADING_V2_EARLY_CUT_HOURS": 0.0,
    "DAYTRADING_V2_SOFT_4H_EXPERIMENT": False,
    "DAYTRADING_V2_15M_SCORING_EXPERIMENT": False,
    "DAYTRADING_V2_5M_TIMING_EXPERIMENT": False,
    "DAYTRADING_V2_MAX_SL_PCT": 0.0,
}


class TestExperimentSwitchesAreOff(unittest.TestCase):

    def test_config_declares_every_switch_and_keeps_it_off(self):
        for key, expected in WYLACZONE.items():
            self.assertTrue(hasattr(config, key),
                            f"{key} nie istnieje w config.py - kod spadnie na domyslke")
            self.assertEqual(expected, getattr(config, key),
                             f"{key} nie jest wylaczony w production baseline")

    def test_code_defaults_do_not_enable_anything(self):
        """Domyslka w getattr musi byc wylaczona, nie tylko wartosc w configu.

        Czytamy zrodla, bo to jedyny sposob, zeby sprawdzic wartosc uzywana
        PRZY BRAKU klucza - a to wlasnie ta sciezka zawiodla.
        """
        wzorce = {
            "DAYTRADING_V2_EARLY_CUT_HOURS": ("0.0", "0"),
            "DAYTRADING_V2_SOFT_4H_EXPERIMENT": ("False",),
            "DAYTRADING_V2_15M_SCORING_EXPERIMENT": ("False",),
            "DAYTRADING_V2_5M_TIMING_EXPERIMENT": ("False",),
            "DAYTRADING_V2_MAX_SL_PCT": ("0.0", "0"),
        }
        zrodla = {
            "v2_trade_lifecycle": inspect.getsource(v2_trade_lifecycle),
            "daytrading_engine_v2": inspect.getsource(daytrading_engine_v2),
        }
        znalezione = 0
        for nazwa, tekst in zrodla.items():
            for key, dozwolone in wzorce.items():
                for m in re.finditer(
                    r'getattr\(\s*config\s*,\s*"%s"\s*,\s*([^)]+?)\s*\)' % key, tekst
                ):
                    znalezione += 1
                    domyslna = m.group(1).strip()
                    self.assertIn(
                        domyslna, dozwolone,
                        f"{nazwa}: getattr({key}, {domyslna}) - domyslka WLACZA "
                        f"eksperyment przy braku klucza w configu",
                    )
        self.assertGreater(znalezione, 0, "nie znaleziono ani jednego getattr - wzorzec sie rozjechal")


class TestEarlyCutFiresOnlyWhenEnabled(unittest.TestCase):
    """Sama regula dziala - po prostu domyslnie jest wylaczona."""

    def test_rule_exists_in_source_and_is_gated(self):
        src = inspect.getsource(v2_trade_lifecycle)
        self.assertIn("dynamic_time_stop", src)
        # Regula musi byc za bramka `early_cut_s > 0`, inaczej 0.0 jej nie gasi.
        self.assertRegex(src, r"early_cut_s\s*>\s*0")


if __name__ == "__main__":
    unittest.main()
