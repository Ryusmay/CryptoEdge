# ============================================================
# 6. Funding settlement model (per instrument)
# ============================================================

from __future__ import annotations
import time
from typing import Dict, Optional, Any


def normalize_interval_hours(raw) -> float:
    """Blofin/Binance: ms, s, '8h', '480m', hours number."""
    import config
    fallback = float(getattr(config, "FUNDING_PERIOD_HOURS", 8.0) or 8.0)
    if fallback <= 0:
        fallback = 8.0
    if raw is None:
        return fallback
    if isinstance(raw, str):
        s = raw.strip().lower().replace(" ", "")
        if s.endswith("h"):
            try:
                return max(float(s[:-1]) or fallback, 0.25)
            except ValueError:
                return fallback
        if s.endswith("m"):
            try:
                return max(float(s[:-1]) / 60.0, 1.0 / 60.0)
            except ValueError:
                return fallback
        if s.endswith("s"):
            try:
                return max(float(s[:-1]) / 3600.0, 1.0 / 3600.0)
            except ValueError:
                return fallback
        try:
            v = float(s)
        except ValueError:
            return fallback
    else:
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return fallback
    # ms timestamps mistaken as interval
    if v > 1e10:
        return fallback
    if v > 1000:  # ms duration
        return max(v / 3600000.0, 1.0 / 60.0)
    if v > 48:  # seconds
        return max(v / 3600.0, 1.0 / 60.0)
    return max(v, 0.25)


def parse_next_funding_ts(raw) -> Optional[float]:
    """Unix seconds."""
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v > 1e12:  # ms
        v /= 1000.0
    if v < 1e9:  # relative hours? ignore
        return None
    return v


def enrich_funding(info: dict, now: float = None) -> Dict[str, Any]:
    """
    Per instrument:
      funding_rate, funding_interval_h, next_funding_ts,
      seconds_to_funding, periods_until_settlement
    """
    now = now if now is not None else time.time()
    info = dict(info or {})
    rate = info.get("funding_rate")
    try:
        rate = float(rate) if rate is not None else 0.0
    except (TypeError, ValueError):
        rate = 0.0
    interval_h = normalize_interval_hours(
        info.get("funding_interval") or info.get("fundingInterval") or info.get("interval")
    )
    next_ts = parse_next_funding_ts(
        info.get("next_funding_time")
        or info.get("nextFundingTime")
        or info.get("funding_time")
        or info.get("fundingTime")
    )
    # if only last funding time – estimate next = last + interval
    if next_ts is None:
        last = parse_next_funding_ts(info.get("funding_time") or info.get("fundingTime"))
        if last is not None:
            # if last is in the past, roll forward
            next_ts = last
            while next_ts <= now:
                next_ts += interval_h * 3600.0
        else:
            # align to UTC epoch buckets
            period = interval_h * 3600.0
            next_ts = (int(now // period) + 1) * period

    # jeśli next w przeszłości – przewiń o interval
    if next_ts is not None:
        period = interval_h * 3600.0
        while next_ts <= now and period > 0:
            next_ts += period
            if next_ts > now + period * 10:  # safety
                break

    sec_left = max(0.0, float(next_ts) - now)
    return {
        "funding_rate": rate,
        "funding_rate_pct": round(rate * 100, 6),
        "funding_interval_h": interval_h,
        "next_funding_ts": next_ts,
        "seconds_to_funding": sec_left,
        "hours_to_funding": round(sec_left / 3600.0, 4),
        "settlements_per_day": round(24.0 / interval_h, 2) if interval_h else 3.0,
    }


def settlement_cost_estimate(
    notional: float,
    funding_rate: float,
    direction: str,
    hours_held: float,
    interval_h: float = 8.0,
) -> float:
    """Oczekiwany koszt funding za hours_held (dodatni = koszt)."""
    if not notional or not funding_rate or not interval_h:
        return 0.0
    periods = hours_held / interval_h
    raw = float(notional) * float(funding_rate) * periods
    return raw if (direction or "").upper() == "LONG" else -raw
