"""Causal setup-quality features shared by runtime and historical replay.

These features describe a setup; they do not create an entry by themselves.
Every calculation uses only candles already present in the supplied frame.
"""
from __future__ import annotations

from statistics import median
from typing import Any, Dict, Iterable


def _f(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
        return value if value == value and abs(value) != float("inf") else default
    except (TypeError, ValueError):
        return default


def _bounded(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return min(high, max(low, value))


def candle_rejection_features(frame: dict, direction: str, zone_a: float,
                              zone_b: float, atr: float = 0.0,
                              touch_lookback: int = 6) -> Dict[str, Any]:
    """Measure rejection/acceptance around a setup zone without pattern names.

    The latest closed candle supplies confirmation.  The most recent zone touch
    is searched only inside the supplied, already-closed prefix.
    """
    opens = list((frame or {}).get("opens") or [])
    highs = list((frame or {}).get("highs") or [])
    lows = list((frame or {}).get("lows") or [])
    closes = list((frame or {}).get("closes") or [])
    volumes = list((frame or {}).get("volumes") or [])
    n = min(len(opens), len(highs), len(lows), len(closes))
    neutral = {
        "ready": False, "score": 0.5, "touch_age": None,
        "body_fraction": None, "directional_wick_fraction": None,
        "close_location": None, "range_atr": None, "volume_ratio": None,
    }
    if n < 2:
        return neutral
    direction = str(direction or "").upper()
    lo, hi = sorted((_f(zone_a), _f(zone_b)))
    touch_age = None
    start = max(0, n - max(1, int(touch_lookback)))
    for i in range(n - 1, start - 1, -1):
        if _f(lows[i]) <= hi and _f(highs[i]) >= lo:
            touch_age = n - 1 - i
            break

    o, h, l, c = map(_f, (opens[n - 1], highs[n - 1], lows[n - 1], closes[n - 1]))
    span = max(h - l, 1e-12)
    body = abs(c - o)
    upper = max(0.0, h - max(o, c))
    lower = max(0.0, min(o, c) - l)
    close_location = _bounded((c - l) / span)
    body_fraction = _bounded(body / span)
    directional_wick = lower / span if direction == "LONG" else upper / span
    directional_close = close_location if direction == "LONG" else 1.0 - close_location
    directional_body = 1.0 if ((c >= o) if direction == "LONG" else (c <= o)) else 0.0
    atr_value = _f(atr)
    if atr_value <= 0 and n >= 15:
        true_ranges = []
        for i in range(max(1, n - 14), n):
            true_ranges.append(max(
                _f(highs[i]) - _f(lows[i]),
                abs(_f(highs[i]) - _f(closes[i - 1])),
                abs(_f(lows[i]) - _f(closes[i - 1])),
            ))
        atr_value = sum(true_ranges) / len(true_ranges) if true_ranges else 0.0
    range_atr = span / max(atr_value, 1e-12) if atr_value > 0 else None

    volume_ratio = None
    if len(volumes) >= n and n >= 6:
        baseline = [_f(v) for v in volumes[max(0, n - 21):n - 1] if _f(v) > 0]
        base = median(baseline) if baseline else 0.0
        if base > 0:
            volume_ratio = _f(volumes[n - 1]) / base
    volume_quality = _bounded((_f(volume_ratio, 1.0) - 0.5) / 1.5)
    touch_quality = 1.0 if touch_age is not None and touch_age <= 2 else (0.5 if touch_age is not None else 0.0)
    range_quality = 0.5 if range_atr is None else _bounded(1.0 - abs(range_atr - 0.8) / 1.6)
    score = (
        0.25 * directional_close
        + 0.25 * _bounded(directional_wick * 2.0)
        + 0.15 * directional_body
        + 0.15 * touch_quality
        + 0.10 * range_quality
        + 0.10 * volume_quality
    )
    return {
        "ready": True, "score": round(_bounded(score), 4), "touch_age": touch_age,
        "body_fraction": round(body_fraction, 4),
        "directional_wick_fraction": round(_bounded(directional_wick), 4),
        "close_location": round(close_location, 4),
        "range_atr": round(range_atr, 4) if range_atr is not None else None,
        "volume_ratio": round(volume_ratio, 4) if volume_ratio is not None else None,
    }


def _confirmed_pivots(values: Iterable[float], kind: str, left: int, right: int) -> list[int]:
    values = list(values)
    points: list[int] = []
    for i in range(left, len(values) - right):
        value = values[i]
        if value is None:
            continue
        neighbours = values[i - left:i] + values[i + 1:i + right + 1]
        if any(v is None for v in neighbours):
            continue
        if kind == "LOW" and all(value <= v for v in neighbours):
            points.append(i)
        if kind == "HIGH" and all(value >= v for v in neighbours):
            points.append(i)
    return points


def rsi_structure_features(frame: dict, direction: str, period: int = 14,
                           left: int = 2, right: int = 2) -> Dict[str, Any]:
    """Confirmed price/RSI divergence and a conservative RSI failure swing."""
    from indicators_full import _rsi_series

    closes = [_f(v) for v in ((frame or {}).get("closes") or [])]
    highs = [_f(v) for v in ((frame or {}).get("highs") or closes)]
    lows = [_f(v) for v in ((frame or {}).get("lows") or closes)]
    n = min(len(closes), len(highs), len(lows))
    result = {"ready": False, "divergence": False, "failure_swing": False,
              "rsi": None, "rsi_slope": None, "pivot_indices": []}
    if n < period + left + right + 8:
        return result
    closes, highs, lows = closes[-n:], highs[-n:], lows[-n:]
    rsi = _rsi_series(closes, period)
    if not rsi or rsi[-1] is None:
        return result
    direction = str(direction or "").upper()
    kind = "LOW" if direction == "LONG" else "HIGH"
    price_values = lows if direction == "LONG" else highs
    pivots = _confirmed_pivots(price_values, kind, left, right)
    valid = [i for i in pivots if i < len(rsi) and rsi[i] is not None]
    divergence = failure = False
    pair: list[int] = []
    if len(valid) >= 2:
        a, b = valid[-2], valid[-1]
        pair = [a, b]
        if direction == "LONG":
            divergence = price_values[b] < price_values[a] and rsi[b] > rsi[a]
            interim = [v for v in rsi[a:b + 1] if v is not None]
            failure = rsi[a] <= 35 and rsi[b] > rsi[a] and bool(interim) and rsi[-1] > max(interim)
        else:
            divergence = price_values[b] > price_values[a] and rsi[b] < rsi[a]
            interim = [v for v in rsi[a:b + 1] if v is not None]
            failure = rsi[a] >= 65 and rsi[b] < rsi[a] and bool(interim) and rsi[-1] < min(interim)
    slope = _f(rsi[-1]) - _f(rsi[-2]) if rsi[-2] is not None else None
    return {"ready": True, "divergence": bool(divergence),
            "failure_swing": bool(failure), "rsi": round(_f(rsi[-1]), 4),
            "rsi_slope": round(slope, 4) if slope is not None else None,
            "pivot_indices": pair}


def probability_quality_multiplier(candle: dict, rsi: dict,
                                   target_clearance: float) -> float:
    """Bounded, deliberately small modifier for an empirical TP prior."""
    candle_score = _bounded(_f((candle or {}).get("score"), 0.5))
    candle_mult = 0.90 + 0.20 * candle_score
    rsi_mult = 1.0
    if (rsi or {}).get("divergence"):
        rsi_mult += 0.04
    if (rsi or {}).get("failure_swing"):
        rsi_mult += 0.04
    clearance_mult = 0.70 + 0.30 * _bounded(_f(target_clearance, 1.0))
    return round(_bounded(candle_mult * rsi_mult * clearance_mult, 0.65, 1.10), 4)


def structure_aware_target(price: float, raw_target: float, risk: float,
                           direction: str, obstacle: float = None,
                           atr: float = 0.0, buffer_atr: float = 0.15,
                           min_r: float = 0.6) -> tuple[float, Dict[str, Any]]:
    """Cap TP before a confirmed obstacle when enough executable R remains."""
    price, raw_target, risk = _f(price), _f(raw_target), abs(_f(risk))
    raw_r = abs(raw_target - price) / max(risk, 1e-12)
    obstacle_value = _f(obstacle) if obstacle is not None else None
    in_path = False
    obstacle_r = None
    target = raw_target
    if obstacle_value is not None:
        in_path = (
            price < obstacle_value < raw_target if str(direction).upper() == "LONG"
            else raw_target < obstacle_value < price
        )
        if in_path:
            obstacle_r = abs(obstacle_value - price) / max(risk, 1e-12)
            buffer_value = max(0.0, _f(atr) * max(0.0, _f(buffer_atr)))
            executable = obstacle_value - buffer_value if str(direction).upper() == "LONG" else obstacle_value + buffer_value
            executable_r = abs(executable - price) / max(risk, 1e-12)
            if executable_r >= max(0.0, _f(min_r)):
                target = executable
    clearance = min(1.0, max(0.0, _f(obstacle_r, raw_r) / max(raw_r, 1e-12))) if in_path else 1.0
    return target, {
        "raw_tp1_price": round(raw_target, 10), "raw_tp1_r": round(raw_r, 4),
        "nearest_obstacle": round(obstacle_value, 10) if obstacle_value is not None else None,
        "obstacle_r": round(obstacle_r, 4) if obstacle_r is not None else None,
        "obstacle_in_path": in_path, "clearance": round(clearance, 4),
        "tp1_capped": abs(target - raw_target) > 1e-12,
    }
