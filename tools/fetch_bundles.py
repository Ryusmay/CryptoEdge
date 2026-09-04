"""Pobiera zamrozone bundle BloFin dla wielu symboli naraz.

DLACZEGO OSOBNE NARZEDZIE. Bundle powstawaly dotad przy okazji uruchamiania
`historical_replay`, czyli jako produkt uboczny backtestu. Przez to zbior
danych zalezal od tego, jaki backtest ktos akurat odpalil, a nie od tego, co
jest potrzebne do badania. To narzedzie robi TYLKO pobieranie: wybiera
uniwersum, sciaga swiece i funding, zapisuje do data/replay i nic nie liczy.

ZRODLO. Wylacznie BloFin, ta sama gielda, na ktorej bot handluje. Swiece
z indeksu (CoinGecko, CoinPaprika) albo ze spotu to INNY instrument: inne
knoty, inny spread, brak fundingu. Backtest na nich mierzylby strategie na
rynku, ktory nie istnieje - ten sam blad co staly spread 4 bps, tylko
trudniejszy do zauwazenia, bo dane wygladalyby wiarygodnie.

ZMIERZONA GLEBOKOSC (2026-09-04, BTC, zadanie 180 dni):
    5m   52059 swiec   180,8 dni
    15m  17699 swiec   184,4 dni
    1h    4539 swiec   189,1 dni
    4h    1339 swiec   223,0 dni
    1d     399 swiec   398,0 dni
    funding 560 rekordow, 186 dni
Czyli 180 dni na 5m jest osiagalne. Wiecej niz ~180 dni na 5m NIE jest -
nie zakladaj tego bez ponownego pomiaru.

    python tools/fetch_bundles.py --days 180 --top 50
    python tools/fetch_bundles.py --days 180 --symbols BTC ETH SOL
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def uniwersum(feed, ile: int, min_obrot: float) -> list[str]:
    """Najplynniejsze perpetuale BloFin, ta sama polityka co replay.

    Uwaga na przezywalnosc: to jest SNAPSHOT dzisiejszych notowan. Monety,
    ktore zostaly zdelistowane w badanym okresie, nie pojawia sie tu wcale,
    wiec kazdy wynik liczony na tym zbiorze ma wbudowany bias przezywalnosci.
    To samo ostrzezenie zwraca discover_replay_symbols i nie da sie go
    usunac bez archiwum kontraktow historycznych.
    """
    from universe_policy import crypto_perpetual_allowed
    from historical_replay import STABLES

    tickers = feed.fetch_all_tickers() or {}
    if not tickers:
        raise RuntimeError(getattr(feed, "last_error", None)
                           or "uniwersum BloFin niedostepne")
    kandydaci = []
    for symbol, row in tickers.items():
        symbol = str(symbol).upper()
        if not crypto_perpetual_allowed(symbol, row):
            continue
        obrot = float((row or {}).get("blofin_quote_volume") or 0.0)
        bid = float((row or {}).get("blofin_bid") or 0.0)
        ask = float((row or {}).get("blofin_ask") or 0.0)
        if symbol in STABLES or obrot < min_obrot or bid <= 0 or ask <= 0 or ask < bid:
            continue
        kandydaci.append((symbol, obrot))
    kandydaci.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in kandydaci[:ile]]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--top", type=int, default=50,
                    help="ile najplynniejszych symboli, gdy nie podano --symbols")
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--min-obrot", type=float, default=5_000_000.0,
                    help="minimalny 24h obrot w USD")
    ap.add_argument("--force", action="store_true",
                    help="pobierz od nowa nawet jesli cache jest swiezy")
    args = ap.parse_args(argv)

    from blofin_feed import BlofinFeed
    from historical_replay import download_bundle, _cache_path

    feed = BlofinFeed()
    symbole = ([s.upper() for s in args.symbols] if args.symbols
               else uniwersum(feed, args.top, args.min_obrot))
    print(f"[bundle] {len(symbole)} symboli x {args.days} dni", flush=True)
    print(f"[bundle] {', '.join(symbole)}", flush=True)

    ok, pominiete, bledy = 0, 0, []
    for i, symbol in enumerate(symbole, 1):
        sciezka = _cache_path(symbol, args.days)
        start = time.time()
        try:
            payload = download_bundle(feed, symbol, args.days, force=args.force)
        except Exception as exc:
            # Jedna moneta bez danych nie moze przerwac calego poboru - inaczej
            # kilkugodzinny przebieg umiera na 47. symbolu i traci 46 poprzednich.
            bledy.append((symbol, repr(exc)))
            print(f"[bundle] {i:>3}/{len(symbole)} {symbol:<10} BLAD: {exc}", flush=True)
            continue
        b = payload.get("bundle") or {}
        ts5 = (b.get("5m") or {}).get("timestamps") or []
        dni = (int(ts5[-1]) - int(ts5[0])) / 86_400_000.0 if len(ts5) > 1 else 0.0
        mb = sciezka.stat().st_size / 1048576.0 if sciezka.exists() else 0.0
        krotki = dni < args.days * 0.95
        if krotki:
            pominiete += 1
        ok += 1
        print(f"[bundle] {i:>3}/{len(symbole)} {symbol:<10} 5m {len(ts5):>6} swiec"
              f" | {dni:6.1f} dni{' KROTKI' if krotki else ''}"
              f" | funding {len(payload.get('funding') or []):>4}"
              f" | {mb:5.1f} MB | {time.time() - start:5.1f}s", flush=True)

    print(f"\n[bundle] pobrane {ok}, w tym krotszych niz zadano {pominiete},"
          f" bledow {len(bledy)}", flush=True)
    for symbol, err in bledy:
        print(f"[bundle]   BLAD {symbol}: {err}", flush=True)
    # Blad choc jednego symbolu ma byc widoczny w kodzie wyjscia, zeby
    # przebieg w tle nie wygladal na udany, gdy polowa monet nie doszla.
    return 1 if bledy else 0


if __name__ == "__main__":
    raise SystemExit(main())
