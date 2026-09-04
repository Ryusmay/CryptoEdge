"""Przycina serie 5m kilku bundli do WSPOLNEGO okna barow.

PO CO. `portfolio_replay_v2` chodzi po wspolnym INDEKSIE bara, nie po zegarze.
Gdy bundle startuja o roznych godzinach, "bar 5000" to inna chwila dla kazdego
symbolu, a wtedy limit slotow i limit tego samego kierunku porownuja pozycje,
ktore nigdy nie istnialy jednoczesnie. Dlatego `parity.py --require-aligned`
odmawia wyniku przy jakimkolwiek rozjezdzie - i ma racje.

Samo pobranie w jednym momencie NIE wystarcza: BloFin zwraca okna zaokraglone
do wlasnej siatki, wiec piec symboli pobranych w tej samej sekundzie potrafi
zaczynac sie o 16:55 albo 17:00 i miec 8860 albo 8859 barow. Roznica jednego
bara wystarczy, zeby bramka odmowila.

CZEGO TO NIE ROBI. Nie tyka wyzszych interwalow (15m, 1h, 4h, 1d). One sa
w replayu ciete po ZNACZNIKU CZASU (`AsOfBlofinFeed.fetch_klines_ohlcv`
uzywa `asof_ts`), a nie po indeksie, wiec przyciecie 5m im wystarcza.
Nie tyka tez fundingu - `apply_observed_funding` filtruje po `ts_ms`.

BRAMKA. Po przycieciu wszystkie serie musza miec IDENTYCZNY pierwszy
znacznik czasu i IDENTYCZNA dlugosc. Jesli nie maja, narzedzie konczy
bledem zamiast zapisac cokolwiek - inaczej "wyrownane" znaczyloby tylko
"probowalem wyrownac".

    python tools/align_bundles.py --days 30 --symbols BTC ETH SOL XRP ZEC
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SERIE = ("timestamps", "opens", "highs", "lows", "closes", "volumes")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--symbols", nargs="+", required=True)
    ap.add_argument("--dry-run", action="store_true",
                    help="pokaz, co by zrobil, i nie zapisuj")
    args = ap.parse_args(argv)

    from parity import CACHE

    plik, ladunek = {}, {}
    for symbol in args.symbols:
        sciezka = CACHE / f"{symbol}_{args.days}d.json"
        if not sciezka.exists():
            print(f"[align] BRAK {sciezka.name}", flush=True)
            return 2
        plik[symbol] = sciezka
        ladunek[symbol] = json.loads(sciezka.read_text(encoding="utf-8"))

    ts = {s: list(ladunek[s]["bundle"]["5m"]["timestamps"]) for s in args.symbols}
    for s, t in ts.items():
        if len(t) < 2:
            print(f"[align] {s}: seria 5m za krotka")
            return 2

    start = max(int(t[0]) for t in ts.values())
    koniec = min(int(t[-1]) for t in ts.values())
    if start > koniec:
        print("[align] okna nie maja czesci wspolnej - nie ma czego przycinac")
        return 2

    print(f"[align] wspolne okno: {start} -> {koniec}", flush=True)
    for s in args.symbols:
        t = ts[s]
        print(f"[align]   {s:<9} przed {len(t):>6} barow  {t[0]} -> {t[-1]}", flush=True)

    wyniki = {}
    for s in args.symbols:
        t = ts[s]
        # Znaczniki sa rosnace, wiec wystarczy znalezc pierwszy >= start
        # i ostatni <= koniec. Bez bisect, bo listy sa krotkie, a jawna
        # petla latwiej sie czyta przy dowodzeniu poprawnosci.
        i0 = next(i for i, x in enumerate(t) if int(x) >= start)
        i1 = len(t) - 1
        while i1 > i0 and int(t[i1]) > koniec:
            i1 -= 1
        wyniki[s] = (i0, i1 + 1)

    dlugosci = {s: b - a for s, (a, b) in wyniki.items()}
    pierwsze = {s: int(ts[s][a]) for s, (a, _) in wyniki.items()}
    if len(set(dlugosci.values())) != 1 or len(set(pierwsze.values())) != 1:
        print("[align] BRAMKA PADLA: po przycieciu serie nadal sie roznia")
        for s in args.symbols:
            print(f"[align]   {s:<9} dlugosc {dlugosci[s]}  pierwszy {pierwsze[s]}")
        print("[align] nic nie zapisano")
        return 1

    n = next(iter(dlugosci.values()))
    print(f"[align] po przycieciu: {n} barow, wspolny start {next(iter(pierwsze.values()))}",
          flush=True)
    if args.dry_run:
        print("[align] --dry-run: nie zapisuje")
        return 0

    for s in args.symbols:
        a, b = wyniki[s]
        piatka = ladunek[s]["bundle"]["5m"]
        for klucz in SERIE:
            if klucz in piatka and piatka[klucz]:
                piatka[klucz] = list(piatka[klucz])[a:b]
        ladunek[s]["aligned_to"] = {"start_ms": start, "end_ms": koniec,
                                    "bars": n, "symbols": list(args.symbols)}
        plik[s].write_text(json.dumps(ladunek[s], ensure_ascii=False),
                           encoding="utf-8")
        print(f"[align]   {s:<9} zapisany, {b - a} barow", flush=True)

    print("[align] gotowe. Teraz parity --require-aligned ma przejsc.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
