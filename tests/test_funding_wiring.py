"""Bramka regresji: stawki fundingu musza dojsc z pliku do replaya.

DLACZEGO TEN TEST ISTNIEJE. Zamrozony bundle ma taki ksztalt:

    {"source": ..., "symbol": ..., "bundle": {"5m": ..., "1h": ...},
     "funding": [{"ts_ms": ..., "rate": ...}, ...]}

Lista `funding` lezy OBOK klucza `bundle`, nie w srodku. Przez to zapis

    bundle = json.loads(...)["bundle"]

wyglada poprawnie i po cichu gubi caly funding. Skutek byl niemy: `funding_r`
= 0.0 w 560 transakcjach na 560, co czytalo sie jak "funding nic nie kosztuje",
a znaczylo "nie bylo czego liczyc". Ta sama wada siedziala w DWOCH miejscach
naraz - `tools/outcome_dataset.py` i `tools/parity.py` - bo obie funkcje
powstaly z tego samego wzorca.

Nic w tescie nie dotyka `data/replay`: bundle 90d nie sa w repozytorium, wiec
test oparty na nich przechodzilby u mnie i pekal wszedzie indziej. Zamiast
tego budujemy minimalny plik w katalogu tymczasowym.

Sabotaz jako dowod wiarygodnosci: `test_bramka_lapie_stary_ksztalt` odtwarza
stary, wadliwy odczyt i sprawdza, ze asercja z pierwszego testu faktycznie by
na nim padla. Bramka, ktora przepuszcza blad, ktorego pilnuje, nie jest bramka.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import parity  # noqa: E402
from daytrading_backtester import AsOfBlofinFeed, apply_observed_funding  # noqa: E402


FUNDING = [{"ts_ms": 1_000_000, "rate": 0.0001},
           {"ts_ms": 2_000_000, "rate": -0.00005},
           {"ts_ms": 3_000_000, "rate": 0.00012}]


def _plik_bundla(katalog: Path, symbol: str, dni: int) -> Path:
    sciezka = katalog / f"{symbol}_{dni}d.json"
    sciezka.write_text(json.dumps({
        "source": "test", "symbol": symbol, "requested_days": dni,
        "bundle": {"5m": {"timestamps": [1_000_000, 2_000_000, 3_000_000],
                          "opens": [1.0, 1.0, 1.0], "highs": [1.0, 1.0, 1.0],
                          "lows": [1.0, 1.0, 1.0], "closes": [1.0, 1.0, 1.0],
                          "volumes": [1.0, 1.0, 1.0]}},
        "funding": FUNDING,
    }), encoding="utf-8")
    return sciezka


class TestParityNieGubiFundingu(unittest.TestCase):

    def test_load_bundles_przenosi_funding_do_bundla(self):
        with tempfile.TemporaryDirectory() as tmp:
            katalog = Path(tmp)
            _plik_bundla(katalog, "TESTCOIN", 7)
            oryginal = parity.CACHE
            parity.CACHE = katalog
            try:
                bundles = parity.load_bundles(["TESTCOIN"], 7)
            finally:
                parity.CACHE = oryginal
        self.assertEqual(FUNDING, bundles["TESTCOIN"].get("funding"),
                         "load_bundles zgubilo stawki fundingu z poziomu pliku")

    def test_bramka_lapie_stary_ksztalt(self):
        # SABOTAZ: stary, wadliwy odczyt. Asercja z testu wyzej MUSI na nim
        # pasc. Gdyby przechodzila, tamten test nie pilnowalby niczego.
        with tempfile.TemporaryDirectory() as tmp:
            sciezka = _plik_bundla(Path(tmp), "TESTCOIN", 7)
            stary = json.loads(sciezka.read_text(encoding="utf-8"))["bundle"]
        self.assertIsNone(stary.get("funding"),
                          "stary odczyt mial gubic funding - jesli go ma, "
                          "ksztalt pliku sie zmienil i ten test nic nie znaczy")


class TestFundingDochodziDoObuSciezek(unittest.TestCase):
    """Dwie niezalezne sciezki czytaja te sama liste w innych miejscach."""

    def test_sciezka_sygnalu_czyta_bundle_funding(self):
        # AsOfBlofinFeed -> fetch_funding_rate -> signal["funding"]
        # -> expected_net_r._funding_cost_frac
        feed = AsOfBlofinFeed({"5m": {"timestamps": [1], "opens": [1.0]},
                               "funding": FUNDING})
        feed.asof_ts = 2_500_000
        got = feed.fetch_funding_rate("TESTCOIN")
        self.assertTrue(got, "feed bez fundingu zwraca pusty slownik")
        self.assertAlmostEqual(-0.00005, got["funding_rate"])

    def test_pusty_funding_daje_pusty_slownik_a_nie_zero(self):
        # "Nie wiem" bije "wiem zle": brak danych ma byc pustka, nie zerem
        # udajacym zmierzona stawke.
        feed = AsOfBlofinFeed({"5m": {"timestamps": [1], "opens": [1.0]}})
        feed.asof_ts = 2_500_000
        self.assertEqual({}, feed.fetch_funding_rate("TESTCOIN"))

    def test_sciezka_replaya_ksieguje_settlementy(self):
        class Trade:
            entry_i, exit_i = 0, 2
            direction = "LONG"
            entry, initial_risk = 100.0, 5.0
            realised_r, funding_r = 1.0, 0.0

        t = Trade()
        zwrot = apply_observed_funding(t, [1_000_000, 2_000_000, 3_000_000],
                                       FUNDING)
        # Okno jest (start, end], wiec bierze rate z ts 2e6 i 3e6, nie z 1e6.
        oczekiwane = -(-0.00005 + 0.00012) / (5.0 / 100.0)
        self.assertAlmostEqual(oczekiwane, zwrot)
        self.assertAlmostEqual(oczekiwane, t.funding_r)
        self.assertAlmostEqual(1.0 + oczekiwane, t.realised_r)

    def test_brak_fundingu_nie_rusza_realised_r(self):
        class Trade:
            entry_i, exit_i = 0, 2
            direction = "LONG"
            entry, initial_risk = 100.0, 5.0
            realised_r, funding_r = 1.0, 0.0

        t = Trade()
        self.assertEqual(0.0, apply_observed_funding(t, [1, 2, 3], []))
        self.assertEqual(1.0, t.realised_r)


class TestSoftStopJestWidocznyWDiffie(unittest.TestCase):
    """Prog, ktory zmienia horyzont transakcji, nie moze byc niemy."""

    def test_oba_progi_czasowe_v2_sa_w_watched_config(self):
        for nazwa in ("DAYTRADING_V2_TIME_STOP_HOURS",
                      "DAYTRADING_V2_HARD_TIME_STOP_HOURS"):
            self.assertIn(nazwa, parity.WATCHED_CONFIG,
                          f"{nazwa} bylby widoczny tylko jako zmiana hasha, "
                          "bez nazwy i bez starej wartosci")


if __name__ == "__main__":
    unittest.main()
