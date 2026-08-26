"""Jedyny wlasciciel regul zamykania pozycji.

26.08.2026: te same reguly zyly w trzech kopiach. stop_engine() liczyl wiek
mapy cen inline, kill_switch() liczyl go inaczej, on_engine_stop() nie
sprawdzal ceny zerowej, a _close_all_unlocked() sprawdzal. Skutek byl taki,
ze guard na nieswieze ceny dostala tylko jedna sciezka, a pozostale zamykaly
po cenach sprzed godzin. Nikt tego nie zepsul - poprawka trafila w jedno
miejsce z trzech i nie mialo jak sie to zsynchronizowac.

Modul jest czysty: nie zamyka pozycji, nie rusza ksiegi, nie loguje. Zwraca
decyzje, wykonanie zostaje po stronie wolajacego. Dzieki temu da sie go
przetestowac bez PaperTradera i bez runtime.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

MAX_PRICE_AGE_DEFAULT_S = 60.0
NEVER = float("inf")


def _config(cfg=None):
    if cfg is not None:
        return cfg
    import config as _cfg
    return _cfg


def max_price_age_s(cfg=None) -> float:
    """Prog swiezosci mapy cen. Jedno zrodlo dla wszystkich sciezek zamkniecia."""
    raw = getattr(_config(cfg), "STOP_ENGINE_MAX_PRICE_AGE_S", MAX_PRICE_AGE_DEFAULT_S)
    try:
        value = float(raw or MAX_PRICE_AGE_DEFAULT_S)
    except (TypeError, ValueError):
        return MAX_PRICE_AGE_DEFAULT_S
    return value if value > 0 else MAX_PRICE_AGE_DEFAULT_S


def age_of(price_map_ts, now: float | None = None) -> float:
    """Wiek mapy cen w sekundach. NEVER, gdy mapa nie byla ani razu zapisana."""
    try:
        ts = float(price_map_ts or 0.0)
    except (TypeError, ValueError):
        return NEVER
    if ts <= 0:
        return NEVER
    return max(0.0, (time.time() if now is None else now) - ts)


def format_age(age_s) -> str:
    """int(inf) rzuca OverflowError, wiec wiek formatujemy w jednym miejscu."""
    try:
        age = float(age_s)
    except (TypeError, ValueError):
        return "NIEZNANY"
    if age == NEVER:
        return "NIGDY"
    return f"{int(age)}s"


def prices_are_stale(age_s, cfg=None) -> bool:
    """None = brak informacji o wieku; wtedy NIE uznajemy cen za nieswieze,
    zeby stara sciezka bez tego parametru zachowala dotychczasowe zachowanie."""
    if age_s is None:
        return False
    try:
        age = float(age_s)
    except (TypeError, ValueError):
        return False
    return age > max_price_age_s(cfg)


def may_realize_profit(age_s, cfg=None) -> bool:
    """Czy wolno podjac decyzje 'ta pozycja jest na plusie' na tych cenach.

    Oddzielne od prices_are_stale() wylacznie dla czytelnosci wolajacego:
    STOP pyta o to, a nie o swiezosc samą w sobie.
    """
    return not prices_are_stale(age_s, cfg)


@dataclass(frozen=True)
class ClosePrice:
    """Po jakiej cenie zamykamy i jak to ma byc opisane w ksiedze."""
    price: float
    reason: str
    source: str          # "map" | "entry"
    stale: bool
    age_s: float | None

    @property
    def is_fallback(self) -> bool:
        return self.source == "entry"


def resolve_close_price(position, price_map, age_s=None, reason: str = "close",
                        cfg=None) -> ClosePrice:
    """Cena i etykieta dla jednego zamkniecia.

    Awaryjne zamkniecie NIE odmawia - kill switch musi splaszczyc pozycje
    nawet przy zamrozonym feedzie. Ale kazde zamkniecie po cenie brakujacej
    albo sprzed godzin ma zostawic slad w powodzie, zeby historia nie
    udawala normalnej transakcji.
    """
    symbol = getattr(position, "symbol", None)
    entry = getattr(position, "entry_price", 0.0) or 0.0
    stale = prices_are_stale(age_s, cfg)
    raw = (price_map or {}).get(symbol)
    try:
        price = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        price = None
    if price is None or price <= 0:
        return ClosePrice(float(entry), f"{reason}:NO_PRICE", "entry", stale, age_s)
    if stale:
        return ClosePrice(
            price, f"{reason}:STALE_PRICE_{format_age(age_s)}", "map", True, age_s,
        )
    return ClosePrice(price, reason, "map", False, age_s)
