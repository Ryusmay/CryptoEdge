"""Bramka determinizmu dla rownoleglego zbierania zbioru wynikow.

DLACZEGO TEN TEST ISTNIEJE. `tools/outcome_dataset.py --workers N` odpala
pule procesow. Na Windows to `spawn`: dziecko startuje nowy interpreter
i dziedziczy `os.environ` rodzica. Dwie rzeczy z tego srodowiska decyduja
o tym, czy wynik jest bit-w-bit taki sam jak sekwencyjny:

1. `OMP/MKL/OPENBLAS/NUMEXPR_NUM_THREADS=1` - wielowatkowy BLAS dzieli
   redukcje na fragmenty zaleznie od stanu puli watkow. Inna liczba watkow
   to inna KOLEJNOSC SUMOWANIA, a wiec inne ostatnie bity wyniku. Cztery
   procesy bez tego pinowania odpalilyby po wlasnej puli - 16 watkow na
   4 rdzenie, wolniej I z innym wynikiem.
2. `PYTHONHASHSEED=0` - w jednym procesie jest jeden losowy seed na caly
   przebieg. Przy spawn kazde dziecko dostaje wlasny. Gdziekolwiek kod
   iteruje po zbiorze stringow, kolejnosc bylaby inna w kazdym workerze.

Jesli ktos kiedys usunie `_pin_determinism` albo skroci te liste, ten test
padnie ZANIM padnie bramka bajt-w-bajt, ktora kosztuje kilka minut przebiegu.

Testu na sam `ProcessPoolExecutor` tu nie ma swiadomie: udawana pula nie
sprawdzalaby tego, co realnie moze sie zepsuc (prawdziwy spawn). Dowodem
na rownolegly zapis jest bramka bajt-w-bajt opisana w
docs/analysis/PARALLEL_DATASET_GATE_20260904.md, wraz z sabotazem.
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

from outcome_dataset import _pin_determinism, _iter_results  # noqa: E402


WYMAGANE = {
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "PYTHONHASHSEED": "0",
}


class TestPinowanieDeterminizmu(unittest.TestCase):

    def setUp(self):
        self._kopia = {k: os.environ.get(k) for k in WYMAGANE}

    def tearDown(self):
        for k, v in self._kopia.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_pinuje_wszystkie_piec_zmiennych(self):
        for k in WYMAGANE:
            os.environ.pop(k, None)
        _pin_determinism()
        for k, oczekiwana in WYMAGANE.items():
            self.assertEqual(oczekiwana, os.environ.get(k),
                             f"{k} nie zostal przypiety przed spawnem")

    def test_nadpisuje_wartosc_odziedziczona(self):
        # Rodzic moze miec OMP_NUM_THREADS=8 z profilu uzytkownika. Pinowanie
        # ma je NADPISAC, a nie uszanowac - inaczej wynik zalezy od maszyny.
        os.environ["OMP_NUM_THREADS"] = "8"
        _pin_determinism()
        self.assertEqual("1", os.environ["OMP_NUM_THREADS"])


class TestSciezkaSekwencyjna(unittest.TestCase):
    """workers<=1 ma isc stara sciezka, bez tworzenia jakiejkolwiek puli."""

    def test_zero_symboli_nie_odpala_puli(self):
        # Gdyby wpadlo w galaz rownolegla, ProcessPoolExecutor powstalby
        # mimo braku pracy. Pusty generator to dowod, ze nie powstal.
        self.assertEqual([], list(_iter_results([], 30, 4)))

    def test_jeden_symbol_nie_odpala_puli(self):
        # Jeden symbol na czterech procesach to sam narzut spawnu.
        # Nie wolam run_symbol - sprawdzam tylko, ze wybrana jest galaz
        # sekwencyjna, przez podmiane funkcji w module.
        import outcome_dataset as od
        oryginal = od.run_symbol
        wolania = []
        # Podpis MUSI odpowiadac prawdziwemu run_symbol. Gdy v20.71.0 dolozylo
        # tp1_frac/tp2_frac, ten stub zostal z dwoma argumentami i test padal
        # na TypeError - czyli bramka przestala pilnowac galezi sekwencyjnej,
        # a zaczela pilnowac wlasnej nieaktualnosci.
        od.run_symbol = lambda s, d, t1=None, t2=None: (
            wolania.append((s, d, t1, t2)), {"error": "stub"})[1]
        try:
            wynik = list(_iter_results(["BTC"], 30, 4))
        finally:
            od.run_symbol = oryginal
        self.assertEqual([("BTC", 30, None, None)], wolania)
        self.assertEqual(1, len(wynik))
        self.assertEqual("BTC", wynik[0][0])


if __name__ == "__main__":
    unittest.main()
