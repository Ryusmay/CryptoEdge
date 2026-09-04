# ============================================================
# 7. Mark-price risk – rozdział cen
# ============================================================

from __future__ import annotations
from typing import Dict, Optional, Any


def extract_price_layers(signal: dict, execution_price: float = None) -> Dict[str, Optional[float]]:
    """
    strategy_price  – cena użyta do sygnału / scoringu
    execution_price – zakładany lub rzeczywisty fill
    mark_price      – mark (do UPL / likwidacji)
    index_price     – index (basis)
    """
    s = signal or {}
    strategy = s.get("strategy_price")
    if strategy is None:
        strategy = s.get("price")
    mark = s.get("mark_price") or s.get("blofin_mark") or (s.get("ticker") or {}).get("mark")
    index = s.get("index_price") or s.get("blofin_index") or (s.get("ticker") or {}).get("index")
    mid = (s.get("order_book") or {}).get("ob_mid")
    decision = s.get("decision_price") or s.get("price")
    submitted = s.get("submitted_price") or decision
    fill = s.get("fill_price") or execution_price or s.get("execution_price") or submitted

    def _f(x):
        try:
            return float(x) if x is not None else None
        except (TypeError, ValueError):
            return None

    strategy_f = _f(strategy)
    # Mark NIE schodzi na cene strategii.
    #
    # Zmierzone: `blofin_mark` nie ma w calym repo ani jednego pisarza, a
    # kanal mark price z `blofin_ws` nie trafia do sygnalu - wiec ten fallback
    # dzialal ZAWSZE. Skutkiem bylo `mark_price == strategy_price`, a stad
    # `basis_pct` wychodzilo strukturalnie 0.0 i wygladalo jak zmierzone zero,
    # a nie jak brak pomiaru. `dynamic_spread` czyta te wartosc i rozszerza
    # nia limit spreadu (SPREAD_K_BASIS), wiec "zero basis" bylo cicha
    # deklaracja, ze basisu nie ma - zamiast: ze nikt go nie podal.
    #
    # None znaczy teraz "gielda nie podala". Wolajacy, ktory potrzebuje
    # liczby, ma wlasny jawny fallback (`Position.mark_price` bierze wtedy
    # `entry_price`, `dynamic_spread` liczy basis jako 0.0) - tyle ze widac
    # go tam, gdzie jest napisany.
    mark_f = _f(mark)
    index_f = _f(index)
    decision_f = _f(decision) or strategy_f
    submitted_f = _f(submitted) or decision_f
    fill_f = _f(fill) or submitted_f
    mid_f = _f(mid)

    basis_pct = None
    if mark_f and index_f and index_f > 0:
        basis_pct = (mark_f - index_f) / index_f * 100.0
    elif mark_f and strategy_f and strategy_f > 0:
        basis_pct = (mark_f - strategy_f) / strategy_f * 100.0

    return {
        "strategy_price": strategy_f,
        "decision_price": decision_f,
        "submitted_price": submitted_f,
        "fill_price": fill_f,
        "execution_price": fill_f,  # kompatybilnosc
        "mark_price": mark_f,
        "index_price": index_f,
        "mid_price": mid_f,
        "basis_pct": round(basis_pct, 5) if basis_pct is not None else None,
    }


def mark_pnl(size_usd: float, entry: float, mark: float, direction: str) -> float:
    """UPL względem mark price (nie last)."""
    if not entry or not mark or not size_usd:
        return 0.0
    if direction.upper() == "LONG":
        ch = (mark - entry) / entry
    else:
        ch = (entry - mark) / entry
    return float(size_usd) * ch
