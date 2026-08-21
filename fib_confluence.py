# ============================================================
# FIBONACCI CONFLUENCE ENGINE  (nie „Fibonacci Strategy”)
# ============================================================
#
#                MARKET
#                   ↓
#             SWING DETECTOR   (obiektywny pivot + ATR/% + min bars)
#                   ↓
#             FIBONACCI MAP    (0.236 … 1.0 + extensions)
#                   ↓
#       ┌───────────┴───────────┐
#       ↓                       ↓
# TREND CONTINUATION        REVERSAL
# pullback 0.5–0.618        extreme + divergence
# confirmation              structure reversal
#       └───────────┬───────────┘
#                   ↓
#             EXPECTED NET R
#                   ↓
#                ENTRY
#
# Fibo = mapa + confluence + asymetria SL/TP
# NIGDY = samodzielny sygnał „0.618 → BUY”
# NIGDY = look-ahead / cherry-pick swing pod ładne 0.618
# ============================================================

from __future__ import annotations

from typing import Any, Dict, Optional

# Re-export kanonicznych funkcji z reversal_engine (single source of truth)
from reversal_engine import (
    FIB_LEVELS,
    detect_valid_swing,
    fibonacci_map,
)


def build_fib_context(
    coin: Dict[str, Any],
    *,
    direction: Optional[str] = None,
    engine: str = "trend",
) -> Dict[str, Any]:
    """
    Wspólny kontekst Fibo dla Trend i Reversal.

    engine:
      "trend"    → extreme_side = UP for LONG / DOWN for SHORT (impulse leg)
      "reversal" → extreme_side = DOWN for LONG / UP for SHORT (przeciw leg)
    """
    direction = (direction or "").upper()
    eng = (engine or "trend").lower()

    if eng == "reversal":
        extreme_side = "DOWN" if direction == "LONG" else "UP"
    else:
        extreme_side = "UP" if direction == "LONG" else "DOWN"

    swing = detect_valid_swing(coin, extreme_side=extreme_side)
    fmap = fibonacci_map(coin, extreme_side=extreme_side)

    conf = (fmap.get("confluence") or {}) if fmap.get("ok") else {}
    return {
        "engine_role": "confluence",  # nie strategy
        "path": eng,
        "direction": direction or None,
        "extreme_side": extreme_side,
        "swing": swing,
        "map": fmap,
        "in_zone": bool(fmap.get("in_zone")),
        "in_primary": bool(fmap.get("in_primary")),
        "weight": float(fmap.get("weight") or 0),
        "confluence_score": float(conf.get("score") or 0),
        "confluence_tags": list(conf.get("tags") or []),
        "degraded": bool((fmap.get("swing") or {}).get("degraded") or not swing.get("ok")),
        "look_ahead_safe": True,  # wymaga OHLCV tylko do T (caller responsibility)
    }


def asymmetry_ok(
    entry: float,
    sl: float,
    tp1: float = None,
    tp2: float = None,
    *,
    min_tp1_r: float = 1.0,
    min_reward_r: float = 1.5,
) -> Dict[str, Any]:
    """
    Weryfikacja czy setup ma sens (Expected path przed ENTRY).

    Entry 100 / SL 96 / TP 103 → 0.75R → NO TRADE
    """
    try:
        entry, sl = float(entry), float(sl)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "BAD_PRICES", "tp1_r": 0.0, "tp2_r": 0.0, "reward_r": 0.0}
    risk = abs(entry - sl)
    if risk <= 1e-12 or entry <= 0:
        return {"ok": False, "reason": "ZERO_RISK", "tp1_r": 0.0, "tp2_r": 0.0, "reward_r": 0.0}

    tp1_r = abs(float(tp1) - entry) / risk if tp1 is not None else 0.0
    tp2_r = abs(float(tp2) - entry) / risk if tp2 is not None else 0.0
    reward_r = max(tp1_r, tp2_r)

    if tp1 is not None and tp1_r < min_tp1_r * 0.85:
        return {
            "ok": False,
            "reason": f"TP1_R_LOW({tp1_r:.2f}<{min_tp1_r})",
            "tp1_r": round(tp1_r, 3),
            "tp2_r": round(tp2_r, 3),
            "reward_r": round(reward_r, 3),
        }
    if reward_r < min_reward_r:
        return {
            "ok": False,
            "reason": f"REWARD_R_LOW({reward_r:.2f}<{min_reward_r})",
            "tp1_r": round(tp1_r, 3),
            "tp2_r": round(tp2_r, 3),
            "reward_r": round(reward_r, 3),
        }
    return {
        "ok": True,
        "reason": "OK",
        "tp1_r": round(tp1_r, 3),
        "tp2_r": round(tp2_r, 3),
        "reward_r": round(reward_r, 3),
    }


__all__ = [
    "FIB_LEVELS",
    "detect_valid_swing",
    "fibonacci_map",
    "build_fib_context",
    "asymmetry_ok",
]
