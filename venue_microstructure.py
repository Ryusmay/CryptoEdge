# -*- coding: utf-8 -*-
"""Zmierzona mikrostruktura rynku - spread i glebokosc ksiegi per symbol.

POWOD. `expected_net_r._spread_cost_frac()` uzywal stalej
DEFAULT_SPREAD_FRAC = 0.0004 (4 bps), gdy nie ma ksiazki zlecen - a replay nie
ma jej nigdy (`AsOfBlofinFeed.fetch_order_book` zwraca {}). Zmierzone na 19
symbolach uniwersum: realne spready ida od 0.0133 bps (BTC) do 4.48 bps
(TRUMP), czyli 337-krotnie. Stala lezy przy GORNYM koncu tego rozkladu, wiec
zawyza dla 18 symboli (do 300x) i zaniza dla jednego. To jest blad ksztaltu:
jedna liczba na wielkosc zmieniajaca sie o dwa i pol rzedu.

SKAD DANE. `data/venue_microstructure_*.json` - plik z proweniencja:
data, zrodlo, gielda, wielkosc ticka i jawnie wypisane ograniczenia. Wartosci
NIE trafiaja do `config.py`, bo tam po pol roku bylyby nieodroznialne od tych,
ktore wlasnie obalilismy.

OD v20.74.0 domyslny plik to `venue_microstructure_blofin.json` - pomiar
gieldy, na ktorej bot handluje, budowany przez
`tools/build_blofin_microstructure.py` z dwoch pelnych dob. Wczesniejszy plik
(`venue_microstructure_20260903.json`, Binance USDT-M) zostaje w `data/` jako
proxy i zapis historyczny; na BloFinie stala 4 bps zaniza koszt dla 9 z 19
symboli, a nie dla 1, jak twierdzil proxy.

KOSZT PRZEJSCIA. Plik BloFina niesie `rt_bps_by_notional`: koszt round-trip
przechodzony przez realna ksiege przy 50..25000 USD. `round_trip_frac()`
zwraca go dla konkretnego notionalu i to jest wlasciwa wielkosc dla poslizgu -
zawiera spread i impact naraz. Plik bez tej tabeli (proxy) daje None i
wolajacy wraca do przyblizenia spread + szczyt ksiegi.

CZEGO TEN MODUL NIE UDAJE.
- Dwie doby to dwie probki, nie rozklad. `n_days` stoi przy kazdym symbolu.
- To wrzesien 2026, a okno replayu to czerwiec-sierpien. Uzycie w replayie
  historycznym jest przyblizeniem - lepszym niz stala wzieta znikad, ale
  przyblizeniem.
- `top1_depth_usd` w pliku BloFina to MINIMUM z migawek - najgorszy moment
  doby. Nie nadaje sie na test "czy zlecenie miesci sie na szczycie".
- Symbol spoza pliku dostaje None, a nie zgadniete zero. Wolajacy ma wtedy
  swiadomie wrocic do stalej i to odnotowac.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent
DEFAULT_PATH = ROOT / "data" / "venue_microstructure_blofin.json"

_lock = threading.Lock()
_cache: Optional[dict] = None
_cache_path: Optional[Path] = None
_override_path: Optional[Path] = None


def reset() -> None:
    """Wraca do domyslnego pliku i czysci cache. Do uzytku w testach."""
    global _cache, _cache_path, _override_path
    with _lock:
        _cache = None
        _cache_path = None
        _override_path = None


def _norm(symbol) -> str:
    """BTC, btc, BTCUSDT, BTC-USDT, BTC/USDT -> BTC.

    Separatory ida PRZED sufiksami: przy odwrotnej kolejnosci "BTC/USDT"
    konczy sie na "USDT", wiec obcinane bylo tylko "USDT" i zostawalo "BTC/".
    Sufiksy sortowane od najdluzszego, zeby "-USDT" nie przegralo z "USD".
    """
    s = str(symbol or "").strip().upper()
    for sep in ("/", ":", "_"):
        if sep in s:
            s = s.split(sep, 1)[0]
    for suffix in ("-USDT", "-USDC", "-PERP", "-USD", "USDT", "USDC", "USD"):
        if s.endswith(suffix) and len(s) > len(suffix):
            return s[: -len(suffix)]
    return s


def load(path: Optional[Path] = None, force: bool = False) -> dict:
    """Wczytuje plik pomiarowy. Brak pliku to pusty zbior, nie wyjatek -
    produkcja ma dzialac dalej na stalej, tylko z odnotowanym brakiem.

    Podana sciezka ZOSTAJE zapamietana. Bez tego kolejne `load()` bez
    argumentu wracalo do DEFAULT_PATH i po cichu uniewazialo wskazanie -
    przez co plik pusty albo uszkodzony i tak dawal poprawne odczyty.
    Zeby wrocic do domyslnego pliku: reset().
    """
    global _cache, _cache_path, _override_path
    if path is not None:
        _override_path = Path(path)
    target = _override_path if _override_path is not None else DEFAULT_PATH
    with _lock:
        if not force and _cache is not None and _cache_path == target:
            return _cache
        data = {}
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        except (OSError, ValueError):
            data = {}
        _cache = data
        _cache_path = target
        return data


def _instrument(symbol) -> Optional[dict]:
    row = (load().get("instruments") or {}).get(_norm(symbol))
    return row if isinstance(row, dict) else None


def spread_frac(symbol) -> Optional[float]:
    """Zmierzony spread jako ulamek ceny, albo None gdy symbolu nie zmierzono."""
    row = _instrument(symbol)
    if not row:
        return None
    try:
        bps = float(row.get("spread_bps_avg"))
    except (TypeError, ValueError):
        return None
    if bps <= 0:
        return None
    return bps / 10000.0


def top1_depth_usd(symbol, side: str = "ask") -> Optional[float]:
    """Glebokosc szczytu ksiegi w USD. Wlasciwy mianownik dla market impactu
    malego zlecenia - w przeciwienstwie do obrotu calej swiecy 5m."""
    row = _instrument(symbol)
    if not row:
        return None
    depth = row.get("top1_depth_usd") or {}
    try:
        value = float(depth.get(str(side).lower()))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def round_trip_frac(symbol, notional_usd) -> Optional[float]:
    """Zmierzony koszt round-trip zlecenia o danym notionale, jako ulamek ceny.

    Liniowa interpolacja miedzy zmierzonymi wielkosciami. Ponizej najmniejszej
    - koszt najmniejszej (mniejsze zlecenie nie jest tansze niz przejscie
    spreadu). Powyzej najwiekszej - None: nie ekstrapolujemy ksztaltu ksiegi,
    ktorego nie zmierzylismy. None takze wtedy, gdy plik nie ma tabeli (proxy)
    albo symbolu - wolajacy ma swiadomie wrocic do innej sciezki.
    """
    row = _instrument(symbol)
    if not row:
        return None
    table = row.get("rt_bps_by_notional") or {}
    pts = []
    for k, v in table.items():
        try:
            pts.append((float(k), float(v)))
        except (TypeError, ValueError):
            continue
    if not pts:
        return None
    pts.sort()
    try:
        n = float(notional_usd)
    except (TypeError, ValueError):
        return None
    if n <= pts[0][0]:
        bps = pts[0][1]
    elif n > pts[-1][0]:
        return None
    else:
        bps = pts[-1][1]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if x0 <= n <= x1:
                bps = y0 + (y1 - y0) * (n - x0) / (x1 - x0)
                break
    if bps < 0:
        return None
    return bps / 10000.0


def provenance() -> dict:
    """Skad te liczby - do zaraportowania razem z kazda liczba, ktora z nich
    powstala."""
    doc = load()
    return {
        "as_of": doc.get("as_of"),
        "source": doc.get("source"),
        "venue": doc.get("venue"),
        "venue_note": doc.get("venue_note"),
        "symbols": len((doc.get("instruments") or {})),
        "path": str(_cache_path or DEFAULT_PATH),
    }


def measured_symbols() -> set:
    return set((load().get("instruments") or {}).keys())
