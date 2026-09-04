"""Porownanie dwoch zbiorow wynikow: horyzont 10/10h kontra 24/48h.

DLACZEGO NIE WYSTARCZY ODJAC SREDNICH. Stary zbior powstal, gdy naliczanie
fundingu bylo zepsute (funding_r = 0 w 560/560). Nowy ma funding policzony.
Roznica srednich mieszalaby wiec DWIE zmiany naraz.

Rozdzielenie jest tu dokladne, nie szacunkowe: `apply_observed_funding`
dodaje funding do realised_r addytywnie (daytrading_backtester.py:463), wiec
`realised_r - funding_r` w nowym zbiorze to dokladnie ten sam wynik, jaki
dalby przebieg z zepsutym fundingiem. To pozwala porownac sam horyzont.

Przedzial ufnosci sredniej: t-Studenta przy nieznanej wariancji, ale przy
n > 300 kwantyl praktycznie rowna sie normalnemu, wiec uzywam 1,96 i mowie
o tym wprost zamiast udawac dokladnosc, ktorej tu nie ma.

    python tools/horizon_compare.py --stary docs/analysis/outcomes.jsonl \
                                    --nowy docs/analysis/outcomes_v20.66.0_h2448.jsonl
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path


def wczytaj(sciezka: Path) -> tuple[list[dict], dict]:
    naglowek, wiersze = {}, []
    with sciezka.open(encoding="utf-8") as fh:
        for linia in fh:
            linia = linia.strip()
            if not linia:
                continue
            rec = json.loads(linia)
            typ = rec.get("_type")
            if typ == "header":
                naglowek = rec
            elif typ is None:
                wiersze.append(rec)
    return wiersze, naglowek


def statystyki(wartosci: list[float]) -> dict:
    n = len(wartosci)
    if n == 0:
        # "Nie wiem" bije "wiem zle": brak probki to None, nie zero.
        return {"n": 0, "srednia": None, "polprzedzial": None, "mediana": None}
    srednia = sum(wartosci) / n
    if n < 2:
        return {"n": n, "srednia": srednia, "polprzedzial": None,
                "mediana": wartosci[0]}
    war = sum((x - srednia) ** 2 for x in wartosci) / (n - 1)
    posortowane = sorted(wartosci)
    srodek = n // 2
    mediana = (posortowane[srodek] if n % 2
               else (posortowane[srodek - 1] + posortowane[srodek]) / 2.0)
    return {"n": n, "srednia": srednia,
            "polprzedzial": 1.96 * math.sqrt(war / n), "mediana": mediana}


def _f(rec: dict, klucz: str):
    wartosc = rec.get(klucz)
    return None if wartosc is None else float(wartosc)


def opis(etykieta: str, s: dict) -> str:
    if s["n"] == 0:
        return f"{etykieta:<34} brak probki"
    if s["polprzedzial"] is None:
        return f"{etykieta:<34} n={s['n']:<5} srednia {s['srednia']:+.4f}"
    return (f"{etykieta:<34} n={s['n']:<5} srednia {s['srednia']:+.4f}"
            f" +/- {s['polprzedzial']:.4f}   mediana {s['mediana']:+.4f}")


def rozklad(wiersze: list[dict], klucz: str, ile: int = 8) -> str:
    licznik = Counter(str(r.get(klucz)) for r in wiersze)
    razem = sum(licznik.values()) or 1
    return "  ".join(f"{k}={v} ({100.0 * v / razem:.1f}%)"
                     for k, v in licznik.most_common(ile))


def raport(etykieta: str, wiersze: list[dict], naglowek: dict,
           odejmij_funding: bool = False) -> None:
    print(f"\n===== {etykieta} =====")
    print(f"config {naglowek.get('config_hash')}  wersja {naglowek.get('bot_version')}"
          f"  dni {naglowek.get('days')}  transakcji {len(wiersze)}")

    r = [_f(x, "realised_r") for x in wiersze]
    r = [x for x in r if x is not None]
    if odejmij_funding:
        pary = [(_f(x, "realised_r"), _f(x, "funding_r") or 0.0) for x in wiersze]
        r_bez = [a - b for a, b in pary if a is not None]
        print(opis("realised_r (z fundingiem)", statystyki(r)))
        print(opis("realised_r BEZ fundingu", statystyki(r_bez)))
    else:
        print(opis("realised_r", statystyki(r)))

    fund = [_f(x, "funding_r") for x in wiersze]
    fund = [x for x in fund if x is not None]
    niezerowe = [x for x in fund if x]
    print(opis("funding_r", statystyki(fund)))
    print(f"{'funding_r niezerowych':<34} {len(niezerowe)} z {len(fund)}")

    mfe = [_f(x, "mfe_r") for x in wiersze]
    mfe = [x for x in mfe if x is not None]
    print(opis("mfe_r", statystyki(mfe)))
    if mfe:
        for prog in (0.5, 1.0, 1.5, 2.0):
            ile = sum(1 for x in mfe if x >= prog)
            print(f"{'  MFE >= ' + str(prog) + 'R':<34} {ile} ({100.0 * ile / len(mfe):.1f}%)")

    # UWAGA NA NAZWY. Zbior zapisuje te pola jako "tp1"/"tp2"
    # (tools/outcome_dataset.py:96-97), a NIE "tp1_done"/"tp2_done" - tak
    # nazywaja sie atrybuty obiektu transakcji, z ktorych powstaja.
    # Pierwsza wersja tej funkcji czytala nazwy atrybutow i dostawala None
    # dla kazdego wiersza, co wygladalo jak "TP1 nie odpala sie nigdy,
    # 0 na 560" i zostalo przeze mnie zaraportowane jako wada strategii.
    # To byl blad narzedzia pomiarowego, nie strategii. Czytam OBIE nazwy,
    # zeby stare zbiory tez dalo sie policzyc, ale brak obu daje None -
    # "nie wiem" zamiast falszywego zera, ktore wlasnie mnie wpuscilo w maliny.
    def _flaga(rec, *nazwy):
        for nazwa in nazwy:
            if nazwa in rec:
                return bool(rec[nazwa])
        return None

    tp1_flagi = [_flaga(x, "tp1", "tp1_done") for x in wiersze]
    tp2_flagi = [_flaga(x, "tp2", "tp2_done") for x in wiersze]
    if any(f is None for f in tp1_flagi):
        print(f"{'TP1 / TP2':<34} BRAK POLA w zbiorze - nie licze")
        tp1 = tp2 = None
    else:
        tp1 = sum(1 for f in tp1_flagi if f)
        tp2 = sum(1 for f in tp2_flagi if f)
    n = len(wiersze) or 1
    if tp1 is not None:
        print(f"{'TP1 osiagniete':<34} {tp1} ({100.0 * tp1 / n:.1f}%)")
        print(f"{'TP2 osiagniete':<34} {tp2} ({100.0 * tp2 / n:.1f}%)")
    print(f"{'powody wyjscia':<34} {rozklad(wiersze, 'exit_reason')}")

    czas = [x for x in ((_f(a, "exit_i") or 0) - (_f(a, "entry_i") or 0)
                        for a in wiersze) if x > 0]
    if czas:
        s = statystyki(czas)
        print(f"{'dlugosc w barach 5m':<34} srednia {s['srednia']:.1f}"
              f"  mediana {s['mediana']:.1f}  max {max(czas):.0f}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stary", type=Path, required=True)
    ap.add_argument("--nowy", type=Path, required=True)
    args = ap.parse_args(argv)

    stare, nag_s = wczytaj(args.stary)
    nowe, nag_n = wczytaj(args.nowy)
    raport(f"STARY  {args.stary.name}", stare, nag_s)
    raport(f"NOWY   {args.nowy.name}", nowe, nag_n, odejmij_funding=True)

    r_st = [_f(x, "realised_r") for x in stare]
    r_st = [x for x in r_st if x is not None]
    r_no_bez = [(_f(x, "realised_r") or 0.0) - (_f(x, "funding_r") or 0.0)
                for x in nowe if _f(x, "realised_r") is not None]
    r_no = [_f(x, "realised_r") for x in nowe]
    r_no = [x for x in r_no if x is not None]

    a, b, c = statystyki(r_st), statystyki(r_no_bez), statystyki(r_no)
    print("\n===== ROZDZIELENIE =====")
    print(opis("stary (funding zepsuty)", a))
    print(opis("nowy bez fundingu", b))
    print(opis("nowy z fundingiem", c))
    if a["srednia"] is not None and b["srednia"] is not None:
        print(f"\nsam horyzont     {b['srednia'] - a['srednia']:+.4f}R na transakcje")
    if b["srednia"] is not None and c["srednia"] is not None:
        print(f"sam funding      {c['srednia'] - b['srednia']:+.4f}R na transakcje")
    if a["polprzedzial"] and c["polprzedzial"]:
        print("\nUWAGA: polprzedzialy obu srednich nachodza na siebie, jesli"
              f" {a['srednia']:+.4f}+/-{a['polprzedzial']:.4f} i"
              f" {c['srednia']:+.4f}+/-{c['polprzedzial']:.4f} maja czesc wspolna."
              "\nTo nie jest test istotnosci - to surowe liczby do przeczytania.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
