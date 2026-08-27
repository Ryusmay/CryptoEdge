"""Ktora liczba jest "sila sygnalu" dla danego silnika.

Kazdy silnik liczy sile pod inna nazwa: trend ma `trend_score`, reversal ma
`reversal_score`, a `strength` jest polem wspolnym. Bramka wejscia od zawsze
sprowadzala jedno do drugiego - ale robila to ZA POZNO.

Rozjazd, ktory to powodowal (zmierzony, v20.25.0): sygnal trendowy
`strength=0.50, trend_score=0.85` byl sizowany na 36.75, bo sizing widzial
0.50. Zaraz potem bramka nadpisywala `strength` na 0.85 i ta wartosc trafiala
do `Position`. Pozycja zapisywala sile 0.85 przy rozmiarze policzonym dla
0.50 - a te dwie liczby czyta pozniej kalibracja sily i telemetria.

Normalizacja nalezy do `prepare_signal_for_sizing()`, czyli przed sizingiem.
Ten modul jest czysty i tylko odpowiada na pytanie; zapisu dokonuje manager.

Brzmienie powodu jest kontraktem - trafia do reject_log i na ekran.
"""
from __future__ import annotations

V2_ENGINES = ("daytrading_v2", "daytradingv2")


def _config(cfg=None):
    if cfg is not None:
        return cfg
    import config as _cfg
    return _cfg


def engine_key(signal) -> str:
    """Klucz silnika tak, jak widzi go bramka sily.

    Uwaga: fallback idzie przez `score_type`, inaczej niz w
    strategy_filter.is_v2(). Zachowane celowo - to dwa rozne pytania i dwie
    rozne historie.
    """
    signal = signal or {}
    return str(signal.get("engine") or signal.get("score_type") or "trend").lower()


def is_exempt(signal) -> bool:
    """V2 podaje stala, sztuczna sile - jego checklista jest w silniku."""
    return engine_key(signal) in V2_ENGINES


def effective_score(signal) -> float:
    """Sila wedlug wlasciwego pola dla tego silnika.

    Brak pola per-silnik cofa sie do `strength`. Uwaga na `None` vs 0.0:
    jawne zero jest wartoscia, nie brakiem danych.
    """
    signal = signal or {}
    if engine_key(signal) == "reversal":
        raw = signal.get("reversal_score")
    else:
        raw = signal.get("trend_score")
    if raw is None:
        raw = signal.get("strength") or 0
    return float(raw)


def min_strength(signal, cfg=None) -> float:
    cfg = _config(cfg)
    if engine_key(signal) == "reversal":
        return float(getattr(cfg, "REVERSAL_MIN_STRENGTH", cfg.MIN_SIGNAL_STRENGTH))
    return float(cfg.MIN_SIGNAL_STRENGTH)


def strength_ok(signal, cfg=None) -> tuple:
    """(ok, powod). V2 przechodzi bez sprawdzenia - z zalozenia."""
    if is_exempt(signal):
        return True, "OK"
    score = effective_score(signal)
    minimum = min_strength(signal, cfg)
    if score < minimum:
        return False, f"Za slaby sygnal {engine_key(signal)} ({score:.2f}<{minimum})"
    return True, "OK"


def normalize_strength(signal, cfg=None) -> float | None:
    """Zwraca sile, ktora nalezy zapisac na sygnale, albo None dla V2.

    Wywolujacy zapisuje ja PRZED sizingiem - patrz naglowek modulu.
    """
    if is_exempt(signal):
        return None
    return effective_score(signal)
