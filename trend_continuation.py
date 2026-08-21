# ============================================================
# Trend Engine — CONTINUATION structure
# IMPULSE → PULLBACK → RETEST → CONTINUATION
# ============================================================
#
# Nie zmienia Trend Engine (EMA/ST/ADX/RSI/MACD).
# Nakłada preferencję setupu kontynuacji po cofnięciu.
#
# LONG:
#   1D UP + 4H UP + 1H setup + 15m timing
#   NIE wchodź na samym ekstremum impulsu
#   Preferuj: impulse → pullback → retest → continuation
# SHORT: lustrzanie
# ============================================================

from __future__ import annotations

from typing import Dict, Any, Optional, List
import config


def _f(x, default=None):
    try:
        if x is None:
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def _tf_dir(mtf: dict, tf: str) -> Optional[str]:
    info = (mtf or {}).get(tf) or {}
    if isinstance(info, dict):
        d = info.get("direction")
        if d in ("LONG", "SHORT"):
            return d
        if info.get("pass") and d:
            return d
    return None


def _tf_pass(mtf: dict, tf: str) -> bool:
    info = (mtf or {}).get(tf) or {}
    if isinstance(info, dict):
        return bool(info.get("pass"))
    return False


def assess_continuation(signal: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ocena struktury kontynuacji dla sygnału trendowego.
    Zwraca dict z score_delta, quality, reasons, structure tags.
    """
    direction = signal.get("direction")
    if direction not in ("LONG", "SHORT"):
        return {"active": False}

    ch24 = _f(signal.get("change_24h"), 0) or 0
    ch1 = _f(signal.get("change_1h"), 0) or 0
    rsi = _f(signal.get("rsi"))
    mtf = signal.get("mtf") or {}
    strat = signal.get("strategy") or {}
    reasons: List[str] = []
    quality = 0.0  # -1..+1 influence

    # --- Higher TF alignment (1D + 4H) ---
    d1 = _tf_dir(mtf, "1d")
    d4 = _tf_dir(mtf, "4h")
    d1h = _tf_dir(mtf, "1h")
    d15 = _tf_dir(mtf, "15m")

    # fallback ze strategy 4h
    if d4 is None and strat.get("direction") in ("LONG", "SHORT"):
        d4 = strat.get("direction") if strat.get("pass") else None

    ht_ok = 0
    if direction == "LONG":
        if d1 == "LONG":
            ht_ok += 1
            reasons.append("CONT_1D_UP")
        elif d1 == "SHORT":
            quality -= 0.15
            reasons.append("CONT_1D_VS")
        if d4 == "LONG":
            ht_ok += 1
            reasons.append("CONT_4H_UP")
        elif d4 == "SHORT":
            quality -= 0.12
            reasons.append("CONT_4H_VS")
        if d1h == "LONG":
            quality += 0.06
            reasons.append("CONT_1H_UP")
        if d15 == "LONG":
            quality += 0.04
            reasons.append("CONT_15M_TIMING")
    else:
        if d1 == "SHORT":
            ht_ok += 1
            reasons.append("CONT_1D_DOWN")
        elif d1 == "LONG":
            quality -= 0.15
            reasons.append("CONT_1D_VS")
        if d4 == "SHORT":
            ht_ok += 1
            reasons.append("CONT_4H_DOWN")
        elif d4 == "LONG":
            quality -= 0.12
            reasons.append("CONT_4H_VS")
        if d1h == "SHORT":
            quality += 0.06
            reasons.append("CONT_1H_DOWN")
        if d15 == "SHORT":
            quality += 0.04
            reasons.append("CONT_15M_TIMING")

    if ht_ok >= 2:
        quality += 0.12
        reasons.append("CONT_HTF_ALIGN")
    elif ht_ok == 1:
        quality += 0.04
        reasons.append("CONT_HTF_PARTIAL")
    else:
        quality -= 0.08
        reasons.append("CONT_HTF_WEAK")

    # --- Structure: impulse → pullback → continuation ---
    # Impulse already happened (moderate 24h), 1h cooled = pullback zone
    impulse_min = float(getattr(config, "CONT_IMPULSE_MIN_24H", 6.0) or 6.0)
    impulse_max = float(getattr(config, "CONT_IMPULSE_MAX_24H", 18.0) or 18.0)
    pullback_1h = float(getattr(config, "CONT_PULLBACK_1H_PCT", 1.2) or 1.2)

    structure = "unknown"
    if direction == "LONG":
        if ch24 >= impulse_min and ch24 < impulse_max:
            # był impuls w górę
            if ch1 <= pullback_1h and ch1 >= -abs(pullback_1h) * 2.5:
                # 1h wyhamował / lekki cofnięcie = pullback/retest zone
                structure = "pullback_continuation"
                quality += 0.14
                reasons.append(f"CONT_PULLBACK(24h={ch24:+.1f}%,1h={ch1:+.1f}%)")
            elif ch1 > pullback_1h * 1.5:
                # dalej goni impuls
                structure = "impulse_extension"
                quality -= 0.16
                reasons.append(f"CONT_CHASE_EXT(1h={ch1:+.1f}%)")
            else:
                structure = "impulse_hot"
                quality -= 0.06
                reasons.append("CONT_IMPULSE_HOT")
        elif ch24 >= impulse_max:
            structure = "late_impulse"
            quality -= 0.10
            reasons.append(f"CONT_LATE_IMPULSE({ch24:+.1f}%)")
        elif 0 < ch24 < impulse_min and ch1 > 0:
            structure = "early_trend"
            quality += 0.05
            reasons.append("CONT_EARLY_TREND")
    else:  # SHORT
        if ch24 <= -impulse_min and ch24 > -impulse_max:
            if ch1 >= -pullback_1h and ch1 <= abs(pullback_1h) * 2.5:
                structure = "pullback_continuation"
                quality += 0.14
                reasons.append(f"CONT_PULLBACK(24h={ch24:+.1f}%,1h={ch1:+.1f}%)")
            elif ch1 < -pullback_1h * 1.5:
                structure = "impulse_extension"
                quality -= 0.16
                reasons.append(f"CONT_CHASE_EXT(1h={ch1:+.1f}%)")
            else:
                structure = "impulse_hot"
                quality -= 0.06
                reasons.append("CONT_IMPULSE_HOT")
        elif ch24 <= -impulse_max:
            structure = "late_impulse"
            quality -= 0.10
            reasons.append(f"CONT_LATE_IMPULSE({ch24:+.1f}%)")
        elif 0 > ch24 > -impulse_min and ch1 < 0:
            structure = "early_trend"
            quality += 0.05
            reasons.append("CONT_EARLY_TREND")

    # RSI: continuation lubi mid-zone, nie ekstremum
    if rsi is not None:
        if direction == "LONG":
            if 42 <= rsi <= 62:
                quality += 0.06
                reasons.append(f"CONT_RSI_MID({rsi:.0f})")
            elif rsi >= 70:
                quality -= 0.10
                reasons.append(f"CONT_RSI_EXT({rsi:.0f})")
        else:
            if 38 <= rsi <= 58:
                quality += 0.06
                reasons.append(f"CONT_RSI_MID({rsi:.0f})")
            elif rsi <= 30:
                quality -= 0.10
                reasons.append(f"CONT_RSI_EXT({rsi:.0f})")

    # SuperTrend / strat soft
    st = str(strat.get("supertrend") or strat.get("st") or signal.get("strategy_st") or "").lower()
    if direction == "LONG" and st in ("up", "long"):
        quality += 0.04
        reasons.append("CONT_ST_UP")
    if direction == "SHORT" and st in ("down", "short"):
        quality += 0.04
        reasons.append("CONT_ST_DOWN")

    # Volume confirmation
    vf = str(signal.get("vol_flag") or "")
    if "OK" in vf or "SPIKE" in vf:
        quality += 0.03
        reasons.append("CONT_VOL_OK")

    return {
        "active": True,
        "structure": structure,
        "quality": round(quality, 3),
        "htf_aligned": ht_ok >= 2,
        "reasons": reasons,
        "prefer": structure == "pullback_continuation",
        "avoid": structure in ("impulse_extension", "late_impulse"),
    }


def apply_continuation_rules(signal: Dict[str, Any]) -> Dict[str, Any]:
    """
    Aplikuj na sygnał Trend Engine (engine=trend).
    Reversal engine — pomijamy.
    """
    if signal.get("engine") == "reversal":
        return signal
    if signal.get("direction") not in ("LONG", "SHORT"):
        return signal
    if not bool(getattr(config, "TREND_CONTINUATION_FILTER", True)):
        return signal

    assessment = assess_continuation(signal)
    if not assessment.get("active"):
        return signal

    signal["continuation"] = assessment
    reasons = list(signal.get("reasons") or []) + list(assessment.get("reasons") or [])
    strength = float(signal.get("strength") or 0)
    q = float(assessment.get("quality") or 0)

    # quality → strength delta (clamp)
    delta = max(-0.20, min(0.15, q))
    strength = max(0.0, min(1.0, strength + delta))

    # twarde unikanie pure chase extension (Trend continuation only)
    if assessment.get("avoid") and bool(getattr(config, "BLOCK_CONT_CHASE_EXT", True)):
        if assessment.get("structure") == "impulse_extension":
            signal["reject_reason"] = signal.get("reject_reason") or "CONT_CHASE_EXT"
            strength = min(strength, float(config.MIN_SIGNAL_STRENGTH) - 0.01)
            reasons.append("CONT_BLOCK_EXTENSION")
        elif assessment.get("structure") == "late_impulse":
            # late = Exhaustion Lens zwykle ogarnie; tu dodatkowa kara
            strength *= 0.75
            reasons.append("CONT_LATE_PENALTY")

    if assessment.get("prefer"):
        reasons.append("CONT_SETUP_OK")
        signal["setup"] = "pullback_continuation"

    signal["strength"] = round(strength, 4)
    signal["reasons"] = reasons
    if not signal.get("engine"):
        signal["engine"] = "trend"
    return signal


def apply_to_signals(signals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for s in signals:
        try:
            out.append(apply_continuation_rules(s))
        except Exception as e:
            s = dict(s)
            s["continuation"] = {"error": str(e)[:80]}
            out.append(s)
    return out
