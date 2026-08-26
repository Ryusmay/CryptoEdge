"""Wspolny, czysty lifecycle pozycji DayTrading V2.

Ten modul nie zna PAPER, LIVE ani replay. Dostaje wyłącznie stan pozycji oraz
obserwację rynku "as-of" i zwraca jedną decyzję. Adapter wykonania rozlicza
zlecenie/PnL, ale nie podejmuje ponownie decyzji strategicznej.
"""

from dataclasses import dataclass
from typing import Optional

import config


@dataclass(frozen=True)
class V2TradeView:
    direction: str
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp1_done: bool = False
    tp2_done: bool = False
    mfe_r: float = 0.0


@dataclass(frozen=True)
class V2Observation:
    high: float
    low: float
    close: float
    age_seconds: float
    htf_bias: Optional[str] = None
    trail_anchor: Optional[float] = None


@dataclass(frozen=True)
class V2LifecycleDecision:
    action: Optional[str] = None
    price: Optional[float] = None
    new_sl: Optional[float] = None


def _hit(direction: str, low: float, high: float, level: float, kind: str) -> bool:
    if kind == "sl":
        return low <= level if direction == "LONG" else high >= level
    return high >= level if direction == "LONG" else low <= level


def _mark_r(v: V2TradeView, price: float) -> float:
    risk = abs(v.entry - v.sl) if not v.tp1_done else abs(v.entry - _initial_sl(v))
    if risk <= 0:
        return 0.0
    move = price - v.entry if v.direction == "LONG" else v.entry - price
    return move / risk


def _initial_sl(v: V2TradeView) -> float:
    """Odtwarza ryzyko początkowe z TP1 tylko po przesunięciu SL na BE.

    Sygnał V2 definiuje TP1 ze struktury, nie gwarantuje dokładnie 1R, dlatego
    adapter powinien przekazywać initial_risk. Pole dodane poniżej zachowuje
    zgodność dla starszych wywołań.
    """
    return v.sl


def decide_v2_lifecycle(
    v: V2TradeView,
    obs: V2Observation,
    *,
    initial_risk: Optional[float] = None,
    hard_stop_seconds: Optional[float] = None,
) -> V2LifecycleDecision:
    """Jedna, deterministyczna decyzja dla PAPER/LIVE i replay.

    Priorytet zdarzeń jest konserwatywny: SL -> HTF -> TP1 -> TP2 -> unclog ->
    hard stop -> aktualizacja trailingu. Dzięki temu świeca dotykająca SL i TP
    nie dostaje optymistycznie przypisanego TP w replay.
    """
    risk = float(initial_risk or abs(v.entry - v.sl))
    if risk <= 0:
        return V2LifecycleDecision()

    tp1_done = bool(v.tp1_done or v.tp2_done)
    sl_armed = tp1_done or bool(getattr(config, "DAYTRADING_V2_ENTRY_SL", False))
    if sl_armed and _hit(v.direction, obs.low, obs.high, v.sl, "sl"):
        return V2LifecycleDecision("sl", v.sl)

    if bool(getattr(config, "DAYTRADING_V2_EXIT_ON_HTF_REVERSAL", False)):
        bias = obs.htf_bias
        if bias in ("LONG", "SHORT") and bias != v.direction:
            return V2LifecycleDecision("htf_reversal", obs.close)

    if not tp1_done and _hit(v.direction, obs.low, obs.high, v.tp1, "tp"):
        new_sl = v.entry if bool(getattr(config, "DAYTRADING_V2_BE_AFTER_TP1", True)) else None
        return V2LifecycleDecision("tp1", v.tp1, new_sl)

    if tp1_done and not v.tp2_done and _hit(v.direction, obs.low, obs.high, v.tp2, "tp"):
        new_sl = v.entry if bool(getattr(config, "DAYTRADING_V2_BE_AFTER_TP2", False)) else None
        return V2LifecycleDecision("tp2", v.tp2, new_sl)

    soft_s = max(3600.0, float(getattr(config, "DAYTRADING_V2_TIME_STOP_HOURS", 24.0) or 24.0) * 3600.0)
    skip_mfe = float(getattr(config, "DAYTRADING_V2_UNCLOG_SKIP_MFE_R", 0.5) or 0.0)
    min_r = float(getattr(config, "DAYTRADING_V2_TIME_STOP_MIN_R", 0.35) or 0.0)
    mark_r = ((obs.close - v.entry) if v.direction == "LONG" else (v.entry - obs.close)) / risk
    if not tp1_done and obs.age_seconds >= soft_s and (skip_mfe <= 0 or v.mfe_r < skip_mfe) and mark_r < min_r:
        return V2LifecycleDecision("time_stop", obs.close)

    hard_s = float(hard_stop_seconds) if hard_stop_seconds is not None else max(
        3600.0,
        float(getattr(config, "DAYTRADING_V2_HARD_TIME_STOP_HOURS", 96.0) or 96.0) * 3600.0,
    )
    if obs.age_seconds >= hard_s:
        return V2LifecycleDecision("hard_time_stop", obs.close)

    if v.tp2_done and obs.trail_anchor is not None:
        anchor = float(obs.trail_anchor)
        if bool(getattr(config, "DAYTRADING_V2_BE_AFTER_TP1", True)):
            anchor = max(anchor, v.entry) if v.direction == "LONG" else min(anchor, v.entry)
        tighter = anchor > v.sl if v.direction == "LONG" else anchor < v.sl
        if tighter:
            return V2LifecycleDecision(new_sl=anchor)
    return V2LifecycleDecision()
