"""Adaptacja notional: jedna warstwa, jawny breakdown.

Nie otwiera i nie zamyka pozycji. Mnozy size po bazowym risk/SL,
przed capem orderbooka.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Tuple

import config


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _strength_frac(signal: dict) -> float:
    if not bool(getattr(config, "SIZE_BY_STRENGTH", True)):
        return 1.0
    st = float(signal.get("reversal_score") or signal.get("strength") or signal.get("score") or 0)
    if st > 1.5:
        st = st / 100.0  # score 0-100
    lo = float(getattr(config, "SIZE_STRENGTH_FLOOR",
                        getattr(config, "MIN_SIGNAL_STRENGTH", 0.48)))
    hi = float(getattr(config, "SIZE_STRENGTH_CAP", 1.0))
    fmin = float(getattr(config, "SIZE_MIN_FRACTION", 0.45))
    fmax = float(getattr(config, "SIZE_MAX_FRACTION", 1.25))
    if hi <= lo:
        t = 1.0
    else:
        t = _clip((st - lo) / (hi - lo), 0.0, 1.0)
    return fmin + t * (fmax - fmin)


def _vol_frac(signal: dict) -> float:
    if not bool(getattr(config, "VOLATILITY_SIZE_SCALE", True)):
        return 1.0
    atr_pct = signal.get("atr_pct")
    if atr_pct is None:
        ind = signal.get("indicators") or {}
        atr_pct = ind.get("atr_pct") or ind.get("atr_percent")
    try:
        atr_pct = float(atr_pct)
    except (TypeError, ValueError):
        atr_pct = None
    if atr_pct is None or atr_pct <= 0:
        pctile = signal.get("atr_percentile")
        try:
            pctile = float(pctile) if pctile is not None else None
        except (TypeError, ValueError):
            pctile = None
        if pctile is None:
            return 1.0
        if pctile >= 90:
            return float(getattr(config, "ADAPT_VOL_HIGH_MULT", 0.50))
        if pctile >= 75:
            return float(getattr(config, "ADAPT_VOL_ELEV_MULT", 0.75))
        return 1.0
    # atr as % of price, e.g. 1.5 = 1.5%
    if atr_pct > 1.5:  # already percent-like or huge
        if atr_pct > 8:
            return float(getattr(config, "ADAPT_VOL_HIGH_MULT", 0.50))
        if atr_pct > 4:
            return float(getattr(config, "ADAPT_VOL_ELEV_MULT", 0.75))
        return 1.0
    # fraction 0.04 = 4%
    if atr_pct >= 0.08:
        return float(getattr(config, "ADAPT_VOL_HIGH_MULT", 0.50))
    if atr_pct >= 0.04:
        return float(getattr(config, "ADAPT_VOL_ELEV_MULT", 0.75))
    return 1.0


def _streak_frac(risk) -> float:
    n = int(getattr(risk, "consecutive_losses", 0) or 0)
    if n <= 0:
        return 1.0
    table = getattr(config, "ADAPT_LOSS_STREAK_MULTS", None) or (1.0, 0.85, 0.65, 0.45)
    if n >= len(table):
        return float(table[-1])
    return float(table[n])


def _drawdown_frac(risk) -> float:
    dd = float(getattr(risk, "max_drawdown_pct", 0) or 0)
    if dd >= float(getattr(config, "ADAPT_DD_HARD_PCT", 12.0)):
        return float(getattr(config, "ADAPT_DD_HARD_MULT", 0.40))
    if dd >= float(getattr(config, "ADAPT_DD_SOFT_PCT", 6.0)):
        return float(getattr(config, "ADAPT_DD_SOFT_MULT", 0.70))
    return 1.0


def _daily_frac(risk) -> float:
    start = float(getattr(risk, "daily_start_capital", 0) or 0)
    pnl = float(getattr(risk, "daily_pnl", 0) or 0)
    limit = float(getattr(config, "DAILY_LOSS_LIMIT", 0.04) or 0.04)
    if start <= 0 or pnl >= 0 or limit <= 0:
        return 1.0
    used = (-pnl / start) / limit  # 0..1+ of daily budget
    if used >= 0.85:
        return float(getattr(config, "ADAPT_DAILY_NEAR_LIMIT_MULT", 0.35))
    if used >= 0.50:
        return float(getattr(config, "ADAPT_DAILY_HALFWAY_MULT", 0.70))
    return 1.0


def compute(signal: dict, risk=None) -> Tuple[float, Dict[str, Any]]:
    parts = {
        "strength": _strength_frac(signal),
        "volatility": _vol_frac(signal),
        "streak": _streak_frac(risk) if risk is not None else 1.0,
        "drawdown": _drawdown_frac(risk) if risk is not None else 1.0,
        "daily": _daily_frac(risk) if risk is not None else 1.0,
        "perp": 1.0,
    }
    try:
        sm = float(signal.get("_size_mult") or 1.0)
        if math.isfinite(sm) and sm > 0:
            parts["perp"] = _clip(sm, 0.20, 1.25)
    except (TypeError, ValueError):
        pass
    floor = float(getattr(config, "ADAPT_MULT_FLOOR", 0.20))
    ceil = float(getattr(config, "ADAPT_MULT_CEIL", 1.25))
    mult = 1.0
    for v in parts.values():
        mult *= float(v)
    mult = _clip(mult, floor, ceil)
    breakdown = {k: round(float(v), 3) for k, v in parts.items()}
    breakdown["final"] = round(mult, 3)
    return mult, breakdown


def apply_to_notional(notional: float, signal: dict, risk=None) -> float:
    if not bool(getattr(config, "ADAPTIVE_SIZE_ENABLED", True)):
        try:
            sm = float(signal.get("_size_mult") or 1.0)
            if 0 < sm < 1.0:
                notional *= sm
        except (TypeError, ValueError):
            pass
        return notional
    mult, br = compute(signal, risk)
    signal["size_adapt"] = br
    reasons = list(signal.get("reasons") or [])
    if br["final"] < 0.99:
        reasons.append(f"SIZE_ADAPT({br['final']:.2f})")
        signal["reasons"] = reasons
    return notional * mult
