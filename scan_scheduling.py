"""Decyzja "pelny skan czy szybki tick" dla petli bota (app.py::bot_loop).

Pelny skan (fetch_top_coins + generate_signals na calym uniwersum) kosztuje
dziesiatki zapytan REST po swiece 4 interwalow per kandydat. Nie moze isc w
rytmie petli (~1s) - stad odrebny, wolniejszy interwal (FULL_SCAN_INTERVAL_SECONDS).
Wydzielone do osobnego, malego modulu bez zaleznosci (zero importu Qt/tkinter/
sieci), zeby dalo sie to realnie przetestowac w izolacji.
"""

from __future__ import annotations

import time


def is_full_scan_due(last_full_scan_ts: float, interval_seconds: float, now: float | None = None) -> bool:
    """Czy minelo dosc czasu od ostatniego pelnego skanu, zeby zrobic kolejny."""
    now = time.time() if now is None else now
    interval = max(1.0, float(interval_seconds))
    last = float(last_full_scan_ts or 0.0)
    return (now - last) >= interval
