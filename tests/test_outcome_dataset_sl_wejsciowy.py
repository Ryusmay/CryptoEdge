"""Bramka: zbior wynikow musi zapisywac SL Z WEJSCIA, nie zmutowany.

DLACZEGO TEN TEST ISTNIEJE. `Trade.sl` jest mutowane w trakcie zycia
transakcji: BE po TP1 przesuwa stop na wejscie, trailing przesuwa go dalej.
`tools/outcome_dataset.py` zapisywal to pole jako "sl" i dzielil przez nie
`tp1_r`. Efekt zmierzony na zbiorze 180d / 49 monet:

  - wszystkie 111 transakcji z tp1=True mialy przesuniety stop
    (78 dokladnie na wejsciu, 33 dalej przez trailing, 0 z pierwotnym),
  - 78 wierszy mialo entry == sl, czyli ZEROWE ryzyko, przy sredniej +1.86R,
  - sitko "tp1_r <= 0.50" wybieralo wylacznie te transakcje, ktorym trailing
    zdazyl odsunac stop - czyli zwyciezcow. Wygladalo na 100% trafien
    i bylo czysta cyrkularnoscia: filtrowalo wynik, nie sygnal.

Jedyna wielkosc znana W BARZE SYGNALU jest `initial_risk` - ustawiane przy
otwarciu i nietykane pozniej. Z niego odtwarzamy SL wejsciowy i wzgledem
niego liczymy tp1_r/tp2_r/sl_dist. Zmutowany stop zostaje, ale pod nazwa
`sl_koncowy`, zeby nikt nie wzial go za ceche wejsciowa.

SABOTAZ. Test `test_sabotaz_...` liczy te same pola STARYM wzorem
(mianownik abs(entry - sl_zmutowany)) i zada, zeby wynik byl INNY niz
zapisany. Gdyby ktos cofnal poprawke, ten test padnie - a nie przejdzie
"przypadkiem", bo dane sa tak dobrane, ze oba wzory daja rozne liczby.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

from outcome_dataset import _row  # noqa: E402


class FalszywaTransakcja:
    """Minimalny obiekt o interfejsie Trade, jakiego uzywa `_row`."""

    def __init__(self, **pola):
        # Wartosci domyslne odpowiadaja transakcji, ktora doszla do TP1
        # i ma stop przesuniety trailingiem PONAD wejscie.
        self.direction = "LONG"
        self.entry = 100.0
        self.initial_risk = 4.0        # SL z wejscia = 96.0
        self.sl = 102.0                # po BE + trailingu, ponad wejsciem
        self.tp1 = 108.0               # 2.0R od wejscia
        self.tp2 = 116.0               # 4.0R od wejscia
        self.tp1_done = True
        self.tp2_done = False
        self.realised_r = 1.5
        self.mfe_r = 2.4
        self.mae_r = -0.3
        self.remaining = 0.5
        self.exit_reason = "trailing"
        self.fill_kind = "limit"
        self.entry_i = 0
        self.exit_i = 1
        self.funding_r = 0.0
        for k, v in pola.items():
            setattr(self, k, v)


TS5 = [1_700_000_000_000, 1_700_000_300_000]


class TestSlWejsciowy(unittest.TestCase):

    def test_sl_to_stop_z_wejscia_odtworzony_z_initial_risk(self):
        w = _row(FalszywaTransakcja(), "BTC", TS5)
        self.assertAlmostEqual(w["sl"], 96.0, places=9)
        self.assertAlmostEqual(w["initial_risk"], 4.0, places=9)

    def test_short_odbija_stop_w_druga_strone(self):
        w = _row(FalszywaTransakcja(direction="SHORT", sl=98.0, tp1=92.0,
                                    tp2=84.0), "BTC", TS5)
        self.assertAlmostEqual(w["sl"], 104.0, places=9)

    def test_zmutowany_stop_zostaje_ale_pod_inna_nazwa(self):
        w = _row(FalszywaTransakcja(), "BTC", TS5)
        self.assertAlmostEqual(w["sl_koncowy"], 102.0, places=9)
        self.assertNotAlmostEqual(w["sl"], w["sl_koncowy"], places=6)

    def test_tp_w_r_liczone_wzgledem_ryzyka_wejsciowego(self):
        w = _row(FalszywaTransakcja(), "BTC", TS5)
        self.assertAlmostEqual(w["tp1_r"], 2.0, places=6)
        self.assertAlmostEqual(w["tp2_r"], 4.0, places=6)

    def test_sl_dist_liczony_z_ryzyka_wejsciowego(self):
        w = _row(FalszywaTransakcja(), "BTC", TS5)
        self.assertAlmostEqual(w["sl_dist"], 0.04, places=6)

    def test_brak_initial_risk_daje_none_a_nie_zero(self):
        """"Nie wiem" bije "wiem zle": bez initial_risk nie zgadujemy stopa."""
        w = _row(FalszywaTransakcja(initial_risk=0.0), "BTC", TS5)
        self.assertIsNone(w["sl"])
        self.assertIsNone(w["initial_risk"])
        self.assertIsNone(w["sl_dist"])
        self.assertIsNone(w["tp1_r"])
        self.assertIsNone(w["tp2_r"])

    def test_stop_na_wejsciu_nie_daje_zerowego_ryzyka(self):
        """Przypadek 78 wierszy ze zbioru 180d: BE po TP1, sl == entry."""
        t = FalszywaTransakcja(sl=100.0)     # BE dokladnie na wejsciu
        w = _row(t, "BTC", TS5)
        self.assertNotAlmostEqual(w["entry"], w["sl"], places=6)
        self.assertGreater(w["sl_dist"], 0.0)
        self.assertIsNotNone(w["tp1_r"])

    def test_sabotaz_stary_wzor_dawal_inne_liczby(self):
        """Dowod, ze bramka rozroznia stary wzor od nowego.

        Gdyby `_row` wrocilo do dzielenia przez abs(entry - sl), tp1_r
        wynioslby 4.0 zamiast 2.0, a sl_dist 0.02 zamiast 0.04.
        """
        t = FalszywaTransakcja()
        w = _row(t, "BTC", TS5)
        stary_mianownik = abs(t.entry - t.sl)          # 2.0, nie 4.0
        stary_tp1_r = abs(t.tp1 - t.entry) / stary_mianownik
        stary_sl_dist = stary_mianownik / t.entry
        self.assertAlmostEqual(stary_tp1_r, 4.0, places=6)
        self.assertNotAlmostEqual(w["tp1_r"], stary_tp1_r, places=6)
        self.assertNotAlmostEqual(w["sl_dist"], stary_sl_dist, places=6)

    def test_sabotaz_be_na_wejsciu_wysadzalby_stary_wzor(self):
        """Przy BE na wejsciu stary wzor dzielil przez zero albo gubil wiersz."""
        t = FalszywaTransakcja(sl=100.0)
        self.assertAlmostEqual(abs(t.entry - t.sl), 0.0, places=9)
        w = _row(t, "BTC", TS5)
        self.assertAlmostEqual(w["tp1_r"], 2.0, places=6)


if __name__ == "__main__":
    unittest.main()
