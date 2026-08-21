# ============================================================
# Exhaustion Lens — DRUGA warstwa patrzenia na rynek
# ============================================================
#
# NIE zastępuje Trend Engine (EMA / SuperTrend / ADX / RSI / MACD /
# MTF / volume / ATR / regime). Trend Engine zostaje bez zmian.
#
# Ten moduł odpowiada na inne pytanie:
#   „Czy impuls jest już wyczerpany i kontynuacja ma zły R:R?”
# oraz opcjonalnie:
#   „Czy widać warunki pod scout reversal (mean-reversion)?”
#
# Wejście: sygnał już oceniony przez Trend Engine
# Wyjście: ten sam sygnał + flagi / kary / reject / reversal_hint
# ============================================================

from __future__ import annotations

from typing import Dict, Any, List
import config


def apply_exhaustion_lens(signal: Dict[str, Any]) -> Dict[str, Any]:
    """
    Aplikuje soczewkę wyczerpania na pojedynczy sygnał trendowy.
    Nie zmienia direction z Trend Engine (chyba że REVERSAL_AUTO_FLIP=True).
    """
    if not bool(getattr(config, "EXHAUSTION_FILTER", True)):
        return signal
    if signal.get("direction") not in ("LONG", "SHORT"):
        return signal

    ch = float(signal.get("change_24h") or 0)
    c1 = float(signal.get("change_1h") or 0)
    try:
        rsi_f = float(signal["rsi"]) if signal.get("rsi") is not None else None
    except (TypeError, ValueError):
        rsi_f = None

    exh_pct = float(getattr(config, "EXHAUSTION_24H_PCT", 18.0) or 18.0)
    exh_1h = float(getattr(config, "EXHAUSTION_1H_EXTENSION_PCT", 3.5) or 3.5)
    rsi_long = float(getattr(config, "EXHAUSTION_RSI_LONG", 68.0) or 68.0)
    rsi_short = float(getattr(config, "EXHAUSTION_RSI_SHORT", 32.0) or 32.0)
    block = bool(getattr(config, "BLOCK_EXHAUSTION_CHASE", True))
    rev_scout = bool(getattr(config, "REVERSAL_SCOUT_ENABLED", True))
    auto_flip = bool(getattr(config, "REVERSAL_AUTO_FLIP", False))

    cipher = signal.get("cipher_b") if isinstance(signal.get("cipher_b"), dict) else {}
    reasons: List[str] = list(signal.get("reasons") or [])
    strength = float(signal.get("strength") or 0)
    lens: Dict[str, Any] = {
        "name": "exhaustion",
        "active": False,
        "chase": False,
        "soft": False,
        "reversal_hint": None,
        "reversal_score": None,
    }

    # ----- LONG continuation into parabolic -----
    if signal.get("direction") == "LONG" and ch >= exh_pct:
        lens["active"] = True
        ext_1h = c1 >= exh_1h
        rsi_hot = rsi_f is not None and rsi_f >= rsi_long
        hard = ext_1h or rsi_hot or ch >= exh_pct * 1.35
        if hard:
            lens["chase"] = True
            reasons.append(f"EXHAUST_LONG_CHASE(24h={ch:+.0f}%,1h={c1:+.1f}%)")
            strength *= 0.55
            if block:
                # blokuj tylko TREND LONG — aktywuj reversal watch
                signal["reject_reason"] = "TREND_BLOCK_EXHAUST_LONG"
                signal["trend_blocked"] = True
                signal["reversal_watch"] = "SHORT"
                strength = min(strength, float(config.MIN_SIGNAL_STRENGTH) - 0.01)
                reasons.append("TREND_LONG_BLOCKED")
                reasons.append("REVERSAL_WATCH_SHORT")
            if rev_scout and (rsi_hot or cipher.get("bear_div") or cipher.get("overbought") or ch >= 25):
                score = round(min(0.85, 0.35 + min(ch, 40) / 80 + (0.15 if rsi_hot else 0)), 3)
                lens["reversal_hint"] = "SHORT"
                lens["reversal_score"] = score
                signal["reversal_hint"] = "SHORT"
                signal["reversal_score"] = score
                signal["reversal_watch"] = "SHORT"
                reasons.append(f"REVERSAL_SCOUT_SHORT(score={score})")
        else:
            lens["soft"] = True
            reasons.append(f"EXHAUST_LONG_SOFT(24h={ch:+.0f}%)")
            strength *= 0.85

    # ----- SHORT continuation into capitulation -----
    if signal.get("direction") == "SHORT" and ch <= -exh_pct:
        lens["active"] = True
        ext_1h = c1 <= -exh_1h
        rsi_cold = rsi_f is not None and rsi_f <= rsi_short
        hard = ext_1h or rsi_cold or ch <= -exh_pct * 1.35
        if hard:
            lens["chase"] = True
            reasons.append(f"EXHAUST_SHORT_CHASE(24h={ch:+.0f}%,1h={c1:+.1f}%)")
            strength *= 0.55
            if block:
                signal["reject_reason"] = "TREND_BLOCK_EXHAUST_SHORT"
                signal["trend_blocked"] = True
                signal["reversal_watch"] = "LONG"
                strength = min(strength, float(config.MIN_SIGNAL_STRENGTH) - 0.01)
                reasons.append("TREND_SHORT_BLOCKED")
                reasons.append("REVERSAL_WATCH_LONG")
            if rev_scout and (rsi_cold or cipher.get("bull_div") or cipher.get("oversold") or ch <= -25):
                score = round(min(0.85, 0.35 + min(abs(ch), 40) / 80 + (0.15 if rsi_cold else 0)), 3)
                lens["reversal_hint"] = "LONG"
                lens["reversal_score"] = score
                signal["reversal_hint"] = "LONG"
                signal["reversal_score"] = score
                signal["reversal_watch"] = "LONG"
                reasons.append(f"REVERSAL_SCOUT_LONG(score={score})")
        else:
            lens["soft"] = True
            reasons.append(f"EXHAUST_SHORT_SOFT(24h={ch:+.0f}%)")
            strength *= 0.85

    # Opcjonalny auto-flip (domyślnie OFF) — nie miesza z Trend Engine bez świadomej zgody
    if auto_flip and lens.get("reversal_hint") and lens.get("chase"):
        score = float(lens.get("reversal_score") or 0)
        min_flip = float(getattr(config, "REVERSAL_AUTO_FLIP_MIN_SCORE", 0.70) or 0.70)
        if score >= min_flip:
            signal["direction_trend"] = signal.get("direction")  # zachowaj oryginał
            signal["direction"] = lens["reversal_hint"]
            signal["engine"] = "reversal_scout"
            reasons.append(f"REVERSAL_AUTO_FLIP→{lens['reversal_hint']}")
            strength = max(strength, score * 0.9)
            signal.pop("reject_reason", None)

    signal["strength"] = max(0.0, float(strength))
    signal["reasons"] = reasons
    signal["lens_exhaustion"] = lens
    if "engine" not in signal:
        signal["engine"] = "trend"
    return signal


def apply_to_signals(signals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Batch: Trend Engine output → Exhaustion Lens."""
    out = []
    for s in signals:
        try:
            out.append(apply_exhaustion_lens(s))
        except Exception as e:
            s = dict(s)
            s["lens_exhaustion"] = {"name": "exhaustion", "error": str(e)[:80]}
            out.append(s)
    return out
