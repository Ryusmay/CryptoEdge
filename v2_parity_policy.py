"""Shared, causal Daytrading V2 policy used by runtime/PAPER and replay.

Only rules whose inputs exist at the decision timestamp belong here.  This
prevents the two execution paths from silently drifting as UI settings change.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional

import config


def causal_change_pct(closes: Iterable[float], index: int, bars: int = 288) -> Optional[float]:
    values = list(closes or [])
    if index < bars or index >= len(values):
        return None
    try:
        old, new = float(values[index - bars]), float(values[index])
    except (TypeError, ValueError):
        return None
    if old <= 0:
        return None
    return (new / old - 1.0) * 100.0


def apply_market_gates(signals: List[Dict], coins: List[Dict], regime: str) -> None:
    """Apply the same configurable V2 market gates in runtime and replay."""
    by = {str(c.get("symbol") or "").upper(): c for c in (coins or []) if c.get("symbol")}
    try:
        raw = getattr(config, "BLOCK_PUMP_CHASE_PCT", 22.0)
        pump_limit = 22.0 if raw is None else float(raw)
    except (TypeError, ValueError):
        pump_limit = 22.0
    block_thin = bool(getattr(config, "BLOCK_OB_THIN", True))
    min_depth = float(getattr(config, "OB_MIN_DEPTH_USD", 3500))
    block_range = bool(getattr(config, "BLOCK_RANGE_REGIME", False))
    regime_u = str(regime or "UNKNOWN").upper()

    for signal in signals or []:
        if str(signal.get("engine") or "").lower() != "daytrading_v2":
            continue
        if signal.get("direction") not in ("LONG", "SHORT") or signal.get("reject_reason"):
            continue
        coin = by.get(str(signal.get("symbol") or "").upper()) or {}
        if not signal.get("order_book"):
            signal["order_book"] = coin.get("order_book")
        raw_change = signal.get("change_24h")
        if raw_change is None:
            raw_change = coin.get("change_24h")
        if raw_change is None:
            raw_change = coin.get("blofin_change_24h")
        try:
            change = float(raw_change) if raw_change is not None else None
        except (TypeError, ValueError):
            change = None
        signal["change_24h"] = change
        reasons = list(signal.get("reasons") or [])

        if block_range and regime_u == "RANGE":
            signal.update(reject_reason="REGIME_RANGE_BLOCK", direction="NEUTRAL")
            reasons.append("V2_UI_RANGE")
        elif change is not None and pump_limit > 0 and change >= pump_limit and signal["direction"] == "LONG":
            signal.update(reject_reason="TREND_BLOCK_PUMP_CHASE", direction="NEUTRAL",
                          reversal_watch="SHORT", pump_chase=True)
            reasons.append(f"PUMP_CHASE(+{change:.0f}%)")
        elif change is not None and pump_limit > 0 and change <= -pump_limit and signal["direction"] == "SHORT":
            signal.update(reject_reason="TREND_BLOCK_DUMP_CHASE", direction="NEUTRAL",
                          reversal_watch="LONG", dump_chase=True)
            reasons.append(f"DUMP_CHASE({change:.0f}%)")
        else:
            book = signal.get("order_book") or {}
            depth = book.get("ob_depth_usd")
            thin = bool(book.get("ob_thin"))
            try:
                thin = thin or (depth is not None and float(depth) < min_depth)
            except (TypeError, ValueError):
                pass
            if thin and block_thin:
                signal.update(reject_reason=f"OB_THIN({depth})", direction="NEUTRAL")
                reasons.append(f"OB_THIN({depth})")
        signal["reasons"] = reasons


def limit_timeout_seconds() -> float:
    bars = max(1, int(getattr(config, "DAYTRADING_V2_LIMIT_TIMEOUT_15M_BARS", 1) or 1))
    return float(bars * 15 * 60)


def limit_timeout_5m_bars() -> int:
    return int(limit_timeout_seconds() // (5 * 60))


def limit_touched(direction: str, limit: float, *, price: float = None,
                  open_price: float = None, low: float = None, high: float = None) -> Optional[float]:
    """Causal fill price for a quote (runtime) or one OHLC bar (replay)."""
    direction = str(direction or "").upper()
    limit = float(limit)
    if price is not None:
        px = float(price)
        return limit if ((direction == "LONG" and px <= limit) or
                         (direction == "SHORT" and px >= limit)) else None
    if open_price is None or low is None or high is None:
        return None
    op, lo, hi = float(open_price), float(low), float(high)
    if direction == "LONG":
        return op if op <= limit else (limit if lo <= limit else None)
    if direction == "SHORT":
        return op if op >= limit else (limit if hi >= limit else None)
    return None
