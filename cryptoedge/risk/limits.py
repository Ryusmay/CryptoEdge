"""Limity kształtu portfela: sloty, kapitał, przechył kierunkowy.

Pierwsza rodzina reguł realnie przeniesiona z `risk_manager.py` do modułu
risk. Wybrana celowo: te cztery decyzje są czyste (nie mutują sygnału, nie
sięgają do sieci ani do dysku) i w całości pokryte bramką charakterystyki
`tools/risk_gate.py`, więc przeniesienie da się udowodnić, a nie tylko
zadeklarować.

Kontrakt jest dosłowny: powody zwracane przez te funkcje muszą być bajt
w bajt takie same jak wcześniej, bo trafiają do reject_log, telemetrii
i na ekran. Zmiana brzmienia to zmiana zachowania, nie kosmetyka.

Czego tu NIE ma i dlaczego: progi siły w reżimie PANIC oraz mnożnik
`_size_mult` zostały w `can_open_position()`, bo mutują sygnał w miejscu.
Przeniesienie mutacji wymaga najpierw uczynienia jej jawną - to osobny krok.
"""
from __future__ import annotations


def _config(cfg=None):
    if cfg is not None:
        return cfg
    import config as _cfg
    return _cfg


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def max_positions_for_regime(regime: str, cfg=None) -> int:
    """Sufit slotow dla danego rezimu.

    RANGE zaciska sie do REGIME_RANGE_MAX_POSITIONS, PANIC do
    REGIME_PANIC_MAX_POSITIONS. PANIC nie jest haltem - to silny
    jednokierunkowy ruch, wiec sloty zostaja, zmienia sie tylko sufit.
    """
    cfg = _config(cfg)
    regime = str(regime or "UNKNOWN").upper()
    max_pos = _int(getattr(cfg, "MAX_POSITIONS", 10), 10)
    if regime == "RANGE":
        max_pos = min(max_pos, _int(getattr(cfg, "REGIME_RANGE_MAX_POSITIONS", 5) or 5, 5))
    elif regime == "PANIC":
        max_pos = min(max_pos, _int(getattr(cfg, "REGIME_PANIC_MAX_POSITIONS", 10) or 10, 10))
    return max_pos


def slot_available(open_positions_count: int, max_pos: int, regime: str) -> tuple:
    """Czy jest wolny slot. Brzmienie powodu jest czescia kontraktu."""
    if _int(open_positions_count, 0) >= _int(max_pos, 0):
        suffix = " RANGE)" if str(regime or "").upper() == "RANGE" else ")"
        return False, f"Max pozycji ({max_pos}" + suffix
    return True, "OK"


def capital_sufficient(current_capital) -> tuple:
    try:
        capital = float(current_capital or 0)
    except (TypeError, ValueError):
        capital = 0.0
    if capital < 1.0:
        return False, "Kapital zbyt niski"
    return True, "OK"


def max_same_direction(cfg=None) -> int:
    """Ile slotow wolno zajac w jednym kierunku."""
    cfg = _config(cfg)
    max_pos = _int(getattr(cfg, "MAX_POSITIONS", 10), 10)
    try:
        share = float(getattr(cfg, "MAX_SAME_DIRECTION_PCT", 0.65))
    except (TypeError, ValueError):
        share = 0.65
    return max(1, int(max_pos * share))


def heat_limit_ok(direction: str, open_directions, cfg=None) -> tuple:
    """Przechyl kierunkowy: nie wolno wypelnic portfela jedna strona rynku."""
    same = sum(1 for d in (open_directions or []) if d == direction)
    limit = max_same_direction(cfg)
    if same >= limit:
        return False, f"HEAT_{direction}({same}>={limit})"
    return True, "OK"


def daily_loss_budget_remaining(daily_start_capital, daily_pnl, cfg=None) -> float:
    """Ile jeszcze wolno dzis stracic.

    daily_pnl jest ujemne przy stracie, wiec dodanie go zjada budzet. Wynik
    moze wyjsc ujemny - to znaczy, ze limit jest juz przekroczony; obcinamy
    do zera dopiero przy porownaniu, zeby powod pokazywal realny prog.
    """
    cfg = _config(cfg)
    return float(daily_start_capital) * float(getattr(cfg, "DAILY_LOSS_LIMIT", 0.04)) + float(daily_pnl)


def projected_loss_ok(planned_notional, sl_distance_pct, remaining_budget) -> tuple:
    """Czy strata do SL zmiesci sie w dzisiejszym budzecie.

    Pytamy PRZED otwarciem: nie "ile juz stracilem", tylko "ile strace, jesli
    ta pozycja pojdzie prosto na stop-loss". Bez tego dzienny limit lapie
    dopiero po fakcie.
    """
    projected = float(planned_notional) * float(sl_distance_pct)
    ceiling = max(0.0, float(remaining_budget))
    if projected > ceiling:
        return False, f"DAILY_PROJECTED_LOSS({projected:.4f}>{ceiling:.4f})"
    return True, "OK"
