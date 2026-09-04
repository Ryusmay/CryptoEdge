"""Bramka komparatora ramion: ma ODMAWIAC, kiedy porownanie jest nieuczciwe.

Trzy rzeczy, ktore ten komparator ma lapac, i kazda z nich raz juz w tym
projekcie wystapila albo o wlos ominela:

1. Ramiona policzone INNA konfiguracja albo inna wersja bota. Porownanie
   wygladaloby normalnie, a mierzyloby dwie zmienne naraz. Dlatego naglowki
   porownywane sa DENYLISTA: rozni sie moze tylko to, co jawnie wypisane,
   a kazde nowe pole naglowka jest porownywane domyslnie.
2. Ramiona z IDENTYCZNYMI partialami - czyli ta sama rzecz policzona dwa
   razy, podana jako eksperyment.
3. Sitko na cesze, ktorej w barze sygnalu nie ma. Tak powstalo sitko
   "tp1_r <= 0.50" liczone na zmutowanym stopie (v20.71.0): filtrowalo
   wynik, nie sygnal, i dawalo 100% trafien.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NARZEDZIE = ROOT / "tools" / "compare_arms.py"

NAGLOWEK = {
    "_type": "header", "config_hash": "abc123", "bot_version": "20.71.0",
    "days": 180, "skip_bars": 100, "fee_rt": 0.0011, "slippage_rt": 0.0006,
    "overrides": {"DAYTRADING_V2_TP1_R": 1.0},
}


def wiersz(symbol, entry_i, r, **extra):
    w = {"symbol": symbol, "entry_i": entry_i, "realised_r": r,
         "direction": "LONG", "entry": 100.0, "sl": 96.0,
         "initial_risk": 4.0, "sl_dist": 0.04, "tp1_r": 2.0,
         "tp1": False, "tp2": False, "exit_reason": "time_stop"}
    w.update(extra)
    return w


def zapisz(katalog, nazwa, naglowek_extra, wiersze):
    sciezka = Path(katalog) / nazwa
    naglowek = dict(NAGLOWEK)
    naglowek.update(naglowek_extra)
    with sciezka.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(naglowek) + "\n")
        for w in wiersze:
            fh.write(json.dumps(w) + "\n")
    return sciezka


def uruchom(*argumenty):
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(NARZEDZIE), *map(str, argumenty)],
        capture_output=True, text=True, cwd=str(ROOT))
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


class TestKomparatorRamion(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.kat = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _para(self, a_extra=None, b_extra=None, a_wiersze=None, b_wiersze=None):
        a = zapisz(self.kat, "a.jsonl",
                   {"arm": "A", "tp1_frac": 0.30, "tp2_frac": 0.50,
                    **(a_extra or {})},
                   a_wiersze if a_wiersze is not None else
                   [wiersz("BTC", 1, 0.5), wiersz("ETH", 2, -1.0)])
        b = zapisz(self.kat, "b.jsonl",
                   {"arm": "B", "tp1_frac": 0.50, "tp2_frac": 0.30,
                    **(b_extra or {})},
                   b_wiersze if b_wiersze is not None else
                   [wiersz("BTC", 1, 0.9), wiersz("ETH", 2, -1.0)])
        return a, b

    def test_zgodne_ramiona_przechodza(self):
        a, b = self._para()
        rc, out = uruchom(a, b)
        self.assertEqual(rc, 0, out)
        self.assertIn("sparowanych 2", out)

    def test_inny_config_hash_odmawia(self):
        a, b = self._para(b_extra={"config_hash": "INNY"})
        rc, out = uruchom(a, b)
        self.assertEqual(rc, 3, out)
        self.assertIn("config_hash", out)

    def test_inna_wersja_bota_odmawia(self):
        a, b = self._para(b_extra={"bot_version": "20.70.0"})
        rc, out = uruchom(a, b)
        self.assertEqual(rc, 3, out)

    def test_inne_overrides_odmawiaja(self):
        a, b = self._para(b_extra={"overrides": {"DAYTRADING_V2_TP1_R": 2.0}})
        rc, out = uruchom(a, b)
        self.assertEqual(rc, 3, out)

    def test_nowe_pole_naglowka_jest_porownywane_domyslnie(self):
        """Denylista, nie allowlista: pole dodane w przyszlosci ma byc sprawdzone."""
        a, b = self._para(b_extra={"pole_ktorego_jeszcze_nie_ma": 7})
        rc, out = uruchom(a, b)
        self.assertEqual(rc, 3, out)
        self.assertIn("pole_ktorego_jeszcze_nie_ma", out)

    def test_identyczne_partiale_to_nie_eksperyment(self):
        a, b = self._para(b_extra={"tp1_frac": 0.30, "tp2_frac": 0.50})
        rc, out = uruchom(a, b)
        self.assertEqual(rc, 3, out)
        self.assertIn("IDENTYCZNE", out)

    def test_sitko_na_cesze_z_przyszlosci_odmawia(self):
        a, b = self._para()
        for pole in ("realised_r", "mfe_r", "exit_reason", "sl_koncowy", "tp1"):
            rc, out = uruchom(a, b, "--sitko", f"{pole}<=1")
            self.assertNotEqual(rc, 0, f"{pole}: komparator przepuscil ceche wyniku")
            self.assertIn("ODMAWIAM", out)

    def test_sitko_na_cesze_wejsciowej_przechodzi(self):
        a, b = self._para()
        rc, out = uruchom(a, b, "--sitko", "tp1_r<=2.5")
        self.assertEqual(rc, 0, out)

    def test_roznica_liczona_jako_ramie2_minus_ramie1(self):
        a, b = self._para(
            a_wiersze=[wiersz("BTC", 1, 0.0)],
            b_wiersze=[wiersz("BTC", 1, 0.4)])
        rc, out = uruchom(a, b, "--prob-bootstrap", "50")
        self.assertEqual(rc, 0, out)
        self.assertIn("srednia roznica +0.4000R", out)

    def test_niesparowane_wejscia_sa_zglaszane(self):
        a, b = self._para(
            a_wiersze=[wiersz("BTC", 1, 0.0), wiersz("BTC", 9, 0.0)],
            b_wiersze=[wiersz("BTC", 1, 0.4)])
        rc, out = uruchom(a, b, "--prob-bootstrap", "50")
        self.assertEqual(rc, 0, out)
        self.assertIn("tylko w ramieniu 1: 1", out)
        self.assertIn("UWAGA", out)


if __name__ == "__main__":
    unittest.main()
