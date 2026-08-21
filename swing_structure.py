"""Obiektywna detekcja ostatniego potwierdzonego swingu (impulsu) na danym
interwale - domyslnie projektowana pod 1h, ale dziala na dowolnych OHLCV.

Zasady (z planu hierarchii timeframe):
- Swing = ruch >= min_move_atr * ATR (filtr rozmiaru) ORAZ trwajacy
  >= min_bars swiec (filtr czasu) - "ruch x ATR + czas", nie dowolne dwa
  ekstrema w oknie.
- Bez look-ahead: pivot (swing high/low) jest potwierdzony dopiero po
  `right_confirm` swiecach PO nim - dokladnie ten sam mechanizm co juz
  istniejacy _confirmed_structure_levels() w indicators_full.py, tylko
  celowo wydzielony jako osobny, mniejszy, latwiejszy do przetestowania
  prymityw ukierunkowany na POJEDYNCZY, NAJSWIEZSZY impuls (nie liste
  poziomow S/R).
- Z tego swingu licza sie poziomy Fibonacci: retracement (0.382/0.5/0.618/
  0.786) do SL/TP1 i extension (1.272/1.618) do TP2 - patrz funkcje ponizej.
"""

from __future__ import annotations

from typing import List, Optional, TypedDict


class SwingPivot(TypedDict):
    index: int
    price: float


class Swing(TypedDict):
    direction: str  # "UP" (low->high, kontekst LONG) albo "DOWN" (high->low, kontekst SHORT)
    start: SwingPivot
    end: SwingPivot
    move: float
    bars: int
    move_atr_ratio: float


def _find_confirmed_pivots(highs: List[float], lows: List[float], right_confirm: int) -> list:
    """Fraktalne pivoty (wysoki/niski), potwierdzone dopiero po `right_confirm`
    swiecach po nich - bez look-ahead. Zwraca liste (index, price, kind) w
    kolejnosci chronologicznej, kind in ("H","L")."""
    n = min(len(highs), len(lows))
    pivots = []
    if n < (2 * right_confirm + 1):
        return pivots
    for i in range(right_confirm, n - right_confirm):
        window_h = highs[i - right_confirm:i + right_confirm + 1]
        window_l = lows[i - right_confirm:i + right_confirm + 1]
        if highs[i] == max(window_h) and window_h.count(highs[i]) == 1:
            pivots.append((i, float(highs[i]), "H"))
        if lows[i] == min(window_l) and window_l.count(lows[i]) == 1:
            pivots.append((i, float(lows[i]), "L"))
    pivots.sort(key=lambda p: p[0])
    return pivots


def find_last_confirmed_swing(
    highs: List[float], lows: List[float], closes: List[float],
    atr_series: List[Optional[float]],
    min_move_atr: float = 1.5, min_bars: int = 3, right_confirm: int = 2,
) -> Optional[Swing]:
    """Ostatni potwierdzony swing spelniajacy filtr ruch>=min_move_atr*ATR
    ORAZ czas>=min_bars. Zwraca None, jesli brak danych/zaden swing nie
    spelnia filtrow (NIE zwraca "najlepszego z gorszych" - brak swingu to
    legalny wynik, ktory wolajacy ma potraktowac jako brak setupu)."""
    n = min(len(highs), len(lows), len(closes), len(atr_series))
    if n < (2 * right_confirm + 1) or n < min_bars + 1:
        return None
    pivots = _find_confirmed_pivots(highs[:n], lows[:n], right_confirm)
    if len(pivots) < 2:
        return None

    best: Optional[Swing] = None
    for j in range(len(pivots) - 1, 0, -1):
        end_i, end_p, end_kind = pivots[j]
        # szukamy najblizszego WCZESNIEJSZEGO pivotu przeciwnego rodzaju
        for k in range(j - 1, -1, -1):
            start_i, start_p, start_kind = pivots[k]
            if start_kind == end_kind:
                continue
            bars = end_i - start_i
            if bars < min_bars:
                continue
            move = abs(end_p - start_p)
            atr_at_end = atr_series[end_i] if end_i < len(atr_series) else None
            if not atr_at_end or atr_at_end <= 0:
                continue
            ratio = move / atr_at_end
            if ratio < min_move_atr:
                continue
            direction = "UP" if end_kind == "H" else "DOWN"
            best = {
                "direction": direction,
                "start": {"index": start_i, "price": start_p},
                "end": {"index": end_i, "price": end_p},
                "move": move,
                "bars": bars,
                "move_atr_ratio": round(ratio, 3),
            }
            break
        if best is not None:
            break
    return best


def swing_fib_retracement(swing: Swing, ratios=(0.382, 0.5, 0.618, 0.786)) -> dict:
    """Poziomy retracement MIERZONE OD KONCA impulsu w strone jego poczatku -
    to jest strefa, w ktorej szukamy setupu wejscia (retest/reclaim), nie
    kierunek sygnalu samej w sobie."""
    lo = min(swing["start"]["price"], swing["end"]["price"])
    hi = max(swing["start"]["price"], swing["end"]["price"])
    span = hi - lo
    if span <= 0:
        return {}
    if swing["direction"] == "UP":
        # cofniecie od szczytu (end) w dol
        return {str(r): hi - span * r for r in ratios}
    return {str(r): lo + span * r for r in ratios}


def swing_fib_extension(swing: Swing, ratios=(1.272, 1.618)) -> dict:
    """Poziomy extension - projekcja POZA koniec impulsu, w jego kierunku.
    Uzywane do TP2 (patrz punkt 16 planu)."""
    lo = min(swing["start"]["price"], swing["end"]["price"])
    hi = max(swing["start"]["price"], swing["end"]["price"])
    span = hi - lo
    if span <= 0:
        return {}
    if swing["direction"] == "UP":
        return {str(r): hi - span + span * r for r in ratios}
    return {str(r): lo + span - span * r for r in ratios}
