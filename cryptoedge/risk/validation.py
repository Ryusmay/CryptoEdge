"""Czy sygnal jest w ogole dobrze uformowany.

Pierwsza bramka w can_open_position(): zanim zapytamy o kapital, sloty czy
rezim, sygnal musi miec sensowny kierunek, cene i sile. To pytanie o ksztalt
danych, nie o polityke ryzyka - dlatego mieszka osobno od limits.py.

Modul jest czysty: nie mutuje sygnalu, nie czyta configu, nie ma stanu.
Brzmienie powodow jest kontraktem - trafiaja do reject_log i telemetrii.
"""
from __future__ import annotations

import math

VALID_DIRECTIONS = ("LONG", "SHORT")
# Kolejnosc ma znaczenie: przy sygnale z dwoma bledami naraz zglaszamy ten
# sam, co wczesniej, zeby powody w telemetrii nie zmienily sie po refaktorze.
NUMERIC_FIELDS = ("price", "strength")


def normalized_direction(signal) -> str:
    return str((signal or {}).get("direction") or "").upper()


def validate_signal_shape(signal) -> tuple:
    """(ok, powod). "OK" gdy sygnal nadaje sie do dalszej oceny."""
    signal = signal or {}
    if normalized_direction(signal) not in VALID_DIRECTIONS:
        return False, "INVALID_DIRECTION"
    for field in NUMERIC_FIELDS:
        try:
            value = float(signal.get(field))
        except (TypeError, ValueError):
            return False, f"INVALID_{field.upper()}"
        if not math.isfinite(value) or (field == "price" and value <= 0):
            return False, f"INVALID_{field.upper()}"
    return True, "OK"
