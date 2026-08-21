"""Kontekst perp: funding + zmiana OI + F&G.

Nie jest triggerem 15m. Mnozy size (_size_mult) i dokleja powody.
Extreme Greed + chase LONG -> mniejszy size, nie NO TRADE.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

import config

_oi_hist: Dict[str, List[Tuple[float, float]]] = {}  # symbol -> [(ts, oi)]


def _fng_value(feeder) -> Optional[float]:
    try:
        ctx = feeder.get_market_context() if feeder else {}
        fng = (ctx or {}).get("fear_greed") or {}
        v = fng.get("value")
        return float(v) if v is not None else None
    except Exception:
        return None


def _record_oi(symbol: str, oi: float) -> Optional[float]:
    """Zwraca zmiane OI w % vs próbka sprzed >= 45 min (jesli jest)."""
    now = time.time()
    hist = _oi_hist.setdefault(symbol, [])
    hist.append((now, oi))
    # trzymaj 6h
    _oi_hist[symbol] = [(t, v) for t, v in hist if now - t < 6 * 3600]
    hist = _oi_hist[symbol]
    old = [v for t, v in hist if now - t >= 45 * 60]
    if not old or oi <= 0:
        return None
    base = old[0]
    if base <= 0:
        return None
    return (oi - base) / base * 100.0


def fetch_oi_cached(feeder, symbol: str) -> dict:
    blofin = getattr(feeder, "blofin", None)
    if blofin is None or not hasattr(blofin, "fetch_open_interest"):
        return {}
    cache = getattr(blofin, "_oi_cache", None)
    if cache is None:
        blofin._oi_cache = {}
        cache = blofin._oi_cache
    hit = cache.get(symbol)
    if hit and time.time() - hit[0] < 90:
        return hit[1]
    try:
        data = blofin.fetch_open_interest(symbol) or {}
    except Exception:
        data = {}
    cache[symbol] = (time.time(), data)
    return data


def apply(signal: dict, feeder=None, fng: Optional[float] = None) -> dict:
    if not bool(getattr(config, "PERP_CONTEXT_ENABLED", True)):
        return signal
    direction = str(signal.get("direction") or "").upper()
    if direction not in ("LONG", "SHORT"):
        return signal

    reasons = list(signal.get("reasons") or [])
    sm = float(signal.get("_size_mult") or 1.0)

    if fng is None:
        fng = _fng_value(feeder)
    # F&G: size only
    if fng is not None:
        signal["fear_greed"] = fng
        if fng >= float(getattr(config, "FNG_EXTREME_GREED", 75)):
            if direction == "LONG":
                sm *= float(getattr(config, "FNG_GREED_LONG_SIZE", 0.70))
                reasons.append(f"FNG_GREED({fng:.0f})_SIZE")
            # SHORT w greed: bez kary
        elif fng <= float(getattr(config, "FNG_EXTREME_FEAR", 25)):
            if direction == "SHORT":
                sm *= float(getattr(config, "FNG_FEAR_SHORT_SIZE", 0.70))
                reasons.append(f"FNG_FEAR({fng:.0f})_SIZE")

    # Funding crowding
    fr = signal.get("funding") or {}
    if not fr and feeder is not None:
        try:
            fr = feeder.blofin.fetch_funding_rate(signal.get("symbol") or "") or {}
            signal["funding"] = fr
        except Exception:
            fr = {}
    rate = float(fr.get("funding_rate") or 0)
    extreme = float(getattr(config, "FUNDING_EXTREME", 0.001))
    if abs(rate) >= extreme:
        if direction == "LONG" and rate > 0:
            sm *= float(getattr(config, "FUNDING_CROWD_SIZE", 0.75))
            reasons.append(f"FUNDING_CROWD_LONG({rate*100:.3f}%)")
        elif direction == "SHORT" and rate < 0:
            sm *= float(getattr(config, "FUNDING_CROWD_SIZE", 0.75))
            reasons.append(f"FUNDING_CROWD_SHORT({rate*100:.3f}%)")
        elif direction == "SHORT" and rate > 0:
            reasons.append(f"FUNDING_PAYS_SHORT({rate*100:.3f}%)")
        elif direction == "LONG" and rate < 0:
            reasons.append(f"FUNDING_PAYS_LONG({rate*100:.3f}%)")

    # OI change
    oi = fetch_oi_cached(feeder, signal.get("symbol") or "") if feeder else {}
    if oi:
        signal["open_interest"] = oi.get("open_interest")
        signal["open_interest_usd"] = oi.get("open_interest_usd")
        chg = _record_oi(signal.get("symbol") or "", float(oi.get("open_interest") or 0))
        if chg is not None:
            signal["oi_change_pct"] = round(chg, 2)
            thr = float(getattr(config, "OI_SPIKE_PCT", 12.0))
            if chg >= thr and direction == "LONG" and rate > 0:
                sm *= float(getattr(config, "OI_SPIKE_SIZE", 0.80))
                reasons.append(f"OI_LONG_BUILD({chg:+.1f}%)")
            elif chg >= thr and direction == "SHORT" and rate < 0:
                sm *= float(getattr(config, "OI_SPIKE_SIZE", 0.80))
                reasons.append(f"OI_SHORT_BUILD({chg:+.1f}%)")
            elif chg <= -thr:
                reasons.append(f"OI_UNWIND({chg:+.1f}%)")

    signal["_size_mult"] = max(0.25, min(1.0, sm))
    signal["reasons"] = reasons
    signal["perp_context"] = {
        "fng": fng,
        "funding": rate,
        "oi_change_pct": signal.get("oi_change_pct"),
        "size_mult": signal["_size_mult"],
    }
    return signal


def apply_all(signals: List[dict], feeder=None) -> None:
    fng = _fng_value(feeder)
    for s in signals or []:
        try:
            apply(s, feeder=feeder, fng=fng)
        except Exception as e:
            print(f"[PerpContext] {s.get('symbol')}: {e}")
