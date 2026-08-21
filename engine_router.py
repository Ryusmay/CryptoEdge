"""Liquidity-aware routing between Trend and confirmed Reversal candidates."""

from __future__ import annotations

import math
import statistics
from typing import Dict, Iterable, Optional


def _finite(value, default: Optional[float] = None) -> Optional[float]:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def universe_market_return(coins: Iterable[dict]) -> Optional[float]:
    values = []
    for coin in coins or []:
        value = _finite(coin.get("change_24h"))
        if value is not None:
            values.append(value)
    return float(statistics.median(values)) if len(values) >= 5 else None


def annotate_residual_momentum(coin: dict, btc_change_24h: float,
                               market_return_24h: Optional[float]) -> dict:
    own = _finite(coin.get("change_24h"), 0.0) or 0.0
    btc = _finite(btc_change_24h, 0.0) or 0.0
    market = _finite(market_return_24h)
    benchmark = btc if market is None else 0.65 * btc + 0.35 * market
    coin["benchmark_return_24h"] = round(benchmark, 4)
    coin["market_median_return_24h"] = round(market, 4) if market is not None else None
    coin["residual_momentum_24h"] = round(own - benchmark, 4)
    coin["residual_momentum_source"] = "btc+universe_median" if market is not None else "btc_only"
    return coin


def liquidity_bucket(signal: Dict) -> str:
    liq = signal.get("liquidity") or {}
    score = _finite(liq.get("score"))
    if str(liq.get("label") or "").lower() == "err" or str(liq.get("grade") or "") == "?":
        score = None
    volume = _finite(signal.get("blofin_volume_24h") or signal.get("blofin_volume")
                     or signal.get("volume_24h"), 0.0) or 0.0
    ob = signal.get("order_book") or {}
    spread = _finite(ob.get("ob_spread_pct"))
    depth = _finite(ob.get("ob_depth_usd"), 0.0) or 0.0
    if (score is not None and score >= 70) or volume >= 50_000_000:
        bucket = "LIQUID"
    elif (score is not None and score < 40) or (0 < volume < 2_000_000):
        bucket = "ILLIQUID"
    else:
        bucket = "MID"
    if spread is not None and spread > 0.25:
        bucket = "ILLIQUID"
    if depth > 0 and depth < 25_000:
        bucket = "ILLIQUID"
    return bucket


def route_signal(signal: Dict) -> Dict:
    """Annotate only; never creates or auto-confirms an entry."""
    engine = str(signal.get("engine") or "trend").lower()
    regime = str(signal.get("market_regime") or "UNKNOWN").upper()
    bucket = liquidity_bucket(signal)
    residual = _finite(signal.get("residual_momentum_24h"), 0.0) or 0.0
    direction = str(signal.get("direction") or "").upper()
    aligned = (direction == "LONG" and residual > 0) or (direction == "SHORT" and residual < 0)
    extreme = abs(_finite(signal.get("change_24h"), 0.0) or 0.0) >= 12.0
    confirmed_reversal = engine == "reversal" and (
        signal.get("setup") == "reversal_confirmed"
        or bool(signal.get("confirmation"))
        or float(signal.get("reversal_score") or signal.get("strength") or 0) >= 0.60
    )
    preferred, reason = "trend", "DEFAULT_TREND"
    if confirmed_reversal and (bucket == "ILLIQUID" or extreme or regime in ("PANIC", "RANGE")):
        preferred, reason = "reversal", "CONFIRMED_EXTREME_OR_ILLIQUID"
    elif bucket == "LIQUID" and aligned and regime not in ("PANIC", "EXTREME"):
        preferred, reason = "trend", "LIQUID_RESIDUAL_CONTINUATION"
    elif confirmed_reversal and not aligned:
        preferred, reason = "reversal", "CONFIRMED_RESIDUAL_FLIP"
    preference = 0.06 if engine == preferred else (-0.03 if bucket != "MID" else 0.0)
    if preference < 0:
        signal["_size_mult"] = min(float(signal.get("_size_mult") or 1.0), 0.80)
    signal.update({
        "liquidity_bucket": bucket,
        "preferred_engine": preferred,
        "engine_route_reason": reason,
        "engine_preference_score": preference,
        "engine_route_version": "v1",
    })
    return signal
