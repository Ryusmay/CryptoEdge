"""Porownanie ramion eksperymentu na zbiorze wynikow (outcome_dataset.jsonl).

DLACZEGO PAROWANIE. Ramiona roznia sie tylko udzialami partiali, a partiale
nie zmieniaja SYGNALU - te same wejscia wystepuja w obu plikach. Porownywanie
dwoch srednich niezaleznie wyrzucaloby te informacje do kosza i dawalo
przedzial kilka razy szerszy niz trzeba. Parujemy po (symbol, entry_i)
i liczymy rozklad ROZNIC.

DLACZEGO BOOTSTRAP PO SYMBOLACH. 49 monet w tym samym okresie to nie 1224
niezalezne obserwacje - BTC ciagnie caly rynek. Naiwny przedzial ufnosci
zaklada niezaleznosc i jest przez to za waski. Liczymy oba: naiwny (zeby dalo
sie porownac z wczesniejszymi raportami) i klastrowany po symbolu (zeby bylo
widac, ile z tego przedzialu bylo zludzeniem).

DENYLISTA W KOMPARATORZE NAGLOWKOW. Porownujemy WSZYSTKIE klucze naglowka
poza jawnie wymienionymi jako "maja sie roznic". Odwrotnie - allowlista
kluczy do porownania - milczaco przepuszczalaby kazde nowe pole, ktore ktos
kiedys doda: dwa pliki policzone inna wersja kodu wygladalyby na porownywalne.
"""
import argparse
import json
import math
import random
import sys
from pathlib import Path

# Klucze, ktore MAJA sie roznic miedzy ramionami. Wszystko poza ta lista
# musi byc identyczne, lacznie z polami dodanymi w przyszlosci.
ROZNICE_DOZWOLONE = {"arm", "tp1_frac", "tp2_frac"}

# Cechy ZNANE W BARZE SYGNALU. Tylko na nich wolno budowac sitko wejscia.
# Tu allowlista jest wlasciwa: to nie jest porownanie, tylko zabezpieczenie
# przed zajrzeniem w przyszlosc, a lista rzeczy z przyszlosci jest otwarta.
CECHY_WEJSCIOWE = {
    "symbol", "direction", "profile", "regime", "entry", "entry_i",
    "entry_utc", "sl", "initial_risk", "sl_dist",
    "tp1_price", "tp2_price", "tp1_r", "tp2_r", "limit_price",
}


def wczytaj(sciezka: Path) -> tuple[dict, list]:
    naglowek, wiersze = None, []
    with sciezka.open(encoding="utf-8") as fh:
        for linia in fh:
            linia = linia.strip()
            if not linia:
                continue
            rek = json.loads(linia)
            typ = rek.get("_type")
            if typ == "header":
                naglowek = rek
            elif typ is None:
                wiersze.append(rek)
            # symbol_done pomijamy swiadomie - to znacznik wznawiania
    if naglowek is None:
        raise SystemExit(f"BLAD: {sciezka.name} nie ma naglowka")
    return naglowek, wiersze


def porownaj_naglowki(a: dict, b: dict) -> list:
    """Zwraca liste roznic, ktorych byc nie powinno."""
    problemy = []
    for klucz in sorted(set(a) | set(b)):
        if klucz in ROZNICE_DOZWOLONE or klucz == "_type":
            continue
        if a.get(klucz) != b.get(klucz):
            problemy.append(f"{klucz}: {a.get(klucz)!r} vs {b.get(klucz)!r}")
    # Odwrotnie: to, co ma sie roznic, MUSI sie roznic. Dwa ramiona
    # o tych samych partialach to nie eksperyment, tylko ta sama rzecz
    # policzona dwa razy.
    if (a.get("tp1_frac"), a.get("tp2_frac")) == (b.get("tp1_frac"),
                                                  b.get("tp2_frac")):
        problemy.append(
            f"partiale IDENTYCZNE w obu ramionach: "
            f"tp1={a.get('tp1_frac')} tp2={a.get('tp2_frac')} - nie ma czego porownywac")
    return problemy


def srednia(xs):
    return sum(xs) / len(xs) if xs else None


def mediana(xs):
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def blad_std(xs):
    n = len(xs)
    if n < 2:
        return None
    m = srednia(xs)
    war = sum((x - m) ** 2 for x in xs) / (n - 1)
    return math.sqrt(war / n)


def bootstrap_po_symbolach(pary, prob=5000, ziarno=12345):
    """Klastrowany bootstrap: losujemy SYMBOLE ze zwracaniem, nie transakcje.

    Zwraca (dolna, gorna) granice 95% przedzialu dla sredniej roznicy.
    """
    wg_symbolu = {}
    for symbol, roznica in pary:
        wg_symbolu.setdefault(symbol, []).append(roznica)
    symbole = list(wg_symbolu)
    if len(symbole) < 2:
        return None, None
    rng = random.Random(ziarno)
    srednie = []
    for _ in range(prob):
        probka = []
        for _ in range(len(symbole)):
            probka.extend(wg_symbolu[symbole[rng.randrange(len(symbole))]])
        if probka:
            srednie.append(sum(probka) / len(probka))
    srednie.sort()
    if not srednie:
        return None, None
    return (srednie[int(0.025 * len(srednie))],
            srednie[int(0.975 * len(srednie)) - 1])


def opis_ramienia(naglowek, wiersze, etykieta):
    r = [w["realised_r"] for w in wiersze]
    n = len(wiersze)
    tp1 = sum(1 for w in wiersze if w.get("tp1"))
    tp2 = sum(1 for w in wiersze if w.get("tp2"))
    print(f"--- {etykieta}: {naglowek.get('arm')} "
          f"(partiale {naglowek.get('tp1_frac')}/{naglowek.get('tp2_frac')})")
    print(f"    transakcji {n}  srednia R {srednia(r):+.4f}  "
          f"mediana {mediana(r):+.4f}  suma {sum(r):+.2f}R")
    if n:
        print(f"    TP1 {tp1} ({100.0*tp1/n:.1f}%)   TP2 {tp2} ({100.0*tp2/n:.1f}%)")
    powody = {}
    for w in wiersze:
        powody[w.get("exit_reason", "?")] = powody.get(w.get("exit_reason", "?"), 0) + 1
    for powod, ile in sorted(powody.items(), key=lambda kv: -kv[1]):
        print(f"      {powod:<20} {ile:>5}  {100.0*ile/max(n,1):5.1f}%")


def sprawdz_sitko(wyrazenie: str) -> list:
    """Rozbija sitko na warunki i pilnuje, by dotykaly tylko cech wejsciowych."""
    warunki = []
    for czlon in wyrazenie.split(","):
        czlon = czlon.strip()
        if not czlon:
            continue
        for op in ("<=", ">=", "<", ">", "=="):
            if op in czlon:
                pole, wart = czlon.split(op, 1)
                pole = pole.strip()
                if pole not in CECHY_WEJSCIOWE:
                    raise SystemExit(
                        f"ODMAWIAM: '{pole}' nie jest cecha znana w barze sygnalu. "
                        f"Sitko na tym polu filtrowaloby WYNIK, nie sygnal. "
                        f"Dozwolone: {', '.join(sorted(CECHY_WEJSCIOWE))}")
                warunki.append((pole, op, float(wart.strip())))
                break
        else:
            raise SystemExit(f"ODMAWIAM: nie rozumiem warunku {czlon!r}")
    return warunki


def przechodzi(wiersz, warunki) -> bool:
    for pole, op, wart in warunki:
        v = wiersz.get(pole)
        if v is None:
            return False        # brak cechy = nie wiem = nie przepuszczam
        v = float(v)
        if op == "<=" and not v <= wart:
            return False
        if op == ">=" and not v >= wart:
            return False
        if op == "<" and not v < wart:
            return False
        if op == ">" and not v > wart:
            return False
        if op == "==" and not abs(v - wart) < 1e-12:
            return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pliki", nargs=2, type=Path)
    ap.add_argument("--sitko", default=None,
                    help='np. "tp1_r<=0.5, sl_dist<=0.05" - tylko cechy wejsciowe')
    ap.add_argument("--prob-bootstrap", type=int, default=5000)
    args = ap.parse_args()

    naglowek_a, wiersze_a = wczytaj(args.pliki[0])
    naglowek_b, wiersze_b = wczytaj(args.pliki[1])

    problemy = porownaj_naglowki(naglowek_a, naglowek_b)
    if problemy:
        print("ODMAWIAM POROWNANIA - ramiona roznia sie czyms wiecej"
              " niz badana zmienna:")
        for p in problemy:
            print("  -", p)
        return 3

    warunki = sprawdz_sitko(args.sitko) if args.sitko else []
    if warunki:
        print(f"[sitko] {args.sitko}")
        wiersze_a = [w for w in wiersze_a if przechodzi(w, warunki)]
        wiersze_b = [w for w in wiersze_b if przechodzi(w, warunki)]

    print(f"[config] {naglowek_a.get('config_hash')}  "
          f"dni {naglowek_a.get('days')}  wersja {naglowek_a.get('bot_version')}")
    print(f"[overrides] {naglowek_a.get('overrides')}")
    print()
    opis_ramienia(naglowek_a, wiersze_a, "RAMIE 1")
    print()
    opis_ramienia(naglowek_b, wiersze_b, "RAMIE 2")
    print()

    klucz_a = {(w["symbol"], w["entry_i"]): w for w in wiersze_a}
    klucz_b = {(w["symbol"], w["entry_i"]): w for w in wiersze_b}
    wspolne = sorted(set(klucz_a) & set(klucz_b))
    tylko_a = len(klucz_a) - len(wspolne)
    tylko_b = len(klucz_b) - len(wspolne)

    print(f"--- PAROWANIE po (symbol, entry_i)")
    print(f"    sparowanych {len(wspolne)}   tylko w ramieniu 1: {tylko_a}"
          f"   tylko w ramieniu 2: {tylko_b}")
    if tylko_a or tylko_b:
        print("    UWAGA: partiale nie zmieniaja sygnalu, wiec niesparowane"
              " wejscia oznaczaja, ze cos jeszcze sie roznilo.")
    if not wspolne:
        return 4

    pary = [(s, klucz_b[(s, i)]["realised_r"] - klucz_a[(s, i)]["realised_r"])
            for (s, i) in wspolne]
    roznice = [d for _, d in pary]
    m = srednia(roznice)
    se = blad_std(roznice)
    lepsze_b = sum(1 for d in roznice if d > 1e-12)
    lepsze_a = sum(1 for d in roznice if d < -1e-12)
    remis = len(roznice) - lepsze_b - lepsze_a

    print()
    print(f"--- ROZNICA (ramie 2 minus ramie 1), na sparowanych")
    print(f"    srednia roznica {m:+.4f}R   mediana {mediana(roznice):+.4f}R")
    if se:
        print(f"    naiwny przedzial 95%  [{m-1.96*se:+.4f}, {m+1.96*se:+.4f}]"
              f"   (zaklada niezaleznosc transakcji - NIE jest spelniona)")
    dol, gora = bootstrap_po_symbolach(pary, args.prob_bootstrap)
    if dol is not None:
        print(f"    bootstrap po symbolach [{dol:+.4f}, {gora:+.4f}]"
              f"   ({args.prob_bootstrap} prob, klaster = symbol)")
    print(f"    ramie 2 lepsze w {lepsze_b}, ramie 1 lepsze w {lepsze_a},"
          f" bez roznicy {remis}")
    if dol is not None and dol <= 0.0 <= gora:
        print("    WNIOSEK: zero miesci sie w przedziale klastrowanym -"
              " roznicy NIE widac w danych.")
    elif dol is not None:
        print("    WNIOSEK: zero poza przedzialem klastrowanym -"
              " roznica jest widoczna w tym oknie.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
