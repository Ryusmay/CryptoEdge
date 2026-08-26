# ============================================================
# REVERSAL ENGINE — niezależny silnik (nie zastępuje Trend Engine)
# ============================================================
#
# NIE robimy:  +30% → SHORT / -20% → LONG
# ANTY-OVERFIT: H/COW = przykłady problemu, NIE progi do strojenia.
# Hipoteza → reguły → shadow → dane → walidacja → dopiero strojenie.
# Robimy:
#   EXTREME → EXHAUSTION → REVERSAL CONFIRMATION → ENTRY
#
# Architektura:
#                    MARKET
#                       │
#                REGIME ENGINE
#                       │
#         ┌─────────────┴─────────────┐
#         │                           │
#    NORMAL / TREND              EXTREME MOVE
#         │                           │
#         ▼                           ▼
#   TREND ENGINE               REVERSAL ENGINE
#    continuation                 exhaustion
#         │                           │
#         └─────────────┬─────────────┘
#                       │
#                  TRADE SCORE → RISK → ENTRY
# ============================================================

from __future__ import annotations

from typing import Dict, Any, List, Optional
import config


def _f(x, default=None):
    try:
        if x is None:
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


# Fibonacci jako MAPA stref, nie sygnał wejścia.
# NIGDY: "dotknęło 0.618 → BUY"
# TAK: impuls → fib zone → + dywergencja + exhaustion + confirmation → setup
# NIGDY: wybór spośród 17 swingów tego, który „ładnie” daje 0.618 (overfit)
FIB_LEVELS = (0.236, 0.382, 0.5, 0.618, 0.786, 1.0)


def detect_valid_swing(coin: Dict[str, Any], extreme_side: str = None) -> Dict[str, Any]:
    """
    Obiektywny swing high/low — bez cherry-pickingu i bez look-ahead.

    valid_swing =
        price_move >= X × ATR   (lub min % gdy brak ATR)
        AND duration >= N candles
        AND pivot confirmation (L bars left + R bars right)

    NO LOOK-AHEAD:
      - Pivot jest ważny dopiero po zamknięciu R świec PO prawej.
      - Ostatnie R barów NIE może być swing high/low (jeszcze niepotwierdzone).
      - Backtest musi podać OHLCV tylko do czasu T (window_until) — nigdy przyszłych HIGH.

    Bierzemy OSTATNI istotny leg w kierunku extreme_side
    (nie „najładniejszy 0.618” spośród wielu).
    """
    out = {
        "ok": False,
        "swing_high": None,
        "swing_low": None,
        "leg": None,
        "bars": 0,
        "method": None,
        "reason": "NONE",
    }
    highs_raw = coin.get("highs") or coin.get("ohlcv_highs") or []
    lows_raw = coin.get("lows") or coin.get("ohlcv_lows") or []
    closes_raw = coin.get("closes") or coin.get("ohlcv_closes") or []
    try:
        highs = [float(x) for x in highs_raw if x is not None]
        lows = [float(x) for x in lows_raw if x is not None]
        closes = [float(x) for x in closes_raw if x is not None]
    except (TypeError, ValueError):
        return {**out, "reason": "BAD_OHLCV"}

    n = min(len(highs), len(lows))
    if n < 8:
        return {**out, "reason": "FEW_BARS"}

    # Ogranicz okno (nie cała historia — ostatnie N barów)
    max_look = int(getattr(config, "FIB_SWING_MAX_LOOKBACK", 80) or 80)
    if n > max_look:
        highs, lows = highs[-max_look:], lows[-max_look:]
        if closes:
            closes = closes[-max_look:]
        n = len(highs)

    pivot_L = int(getattr(config, "FIB_PIVOT_LEFT", 2) or 2)
    pivot_R = int(getattr(config, "FIB_PIVOT_RIGHT", 2) or 2)
    min_bars = int(getattr(config, "FIB_SWING_MIN_BARS", 4) or 4)
    atr_mult = float(getattr(config, "FIB_SWING_MIN_ATR_MULT", 1.5) or 1.5)
    min_pct = float(getattr(config, "FIB_SWING_MIN_PCT", 2.5) or 2.5) / 100.0

    # ATR proxy: średni true-range z okna
    atr_abs = _f(coin.get("atr"))
    if atr_abs is None or atr_abs <= 0:
        atr_pct = _f(coin.get("atr_pct") or coin.get("atr_percent"))
        px = _f(coin.get("price") or coin.get("blofin_price"))
        if atr_pct and px:
            atr_abs = px * (atr_pct / 100.0)
    if atr_abs is None or atr_abs <= 0:
        # median range of last bars
        ranges = [highs[i] - lows[i] for i in range(n) if highs[i] > lows[i]]
        if ranges:
            ranges_s = sorted(ranges)
            atr_abs = ranges_s[len(ranges_s) // 2]
        else:
            atr_abs = 0.0

    min_move = max(atr_abs * atr_mult, (_f(coin.get("price"), 1.0) or 1.0) * min_pct)

    # Fractal pivots
    pivot_highs = []  # (idx, price)
    pivot_lows = []
    for i in range(pivot_L, n - pivot_R):
        h = highs[i]
        l = lows[i]
        is_ph = all(h >= highs[i - j] for j in range(1, pivot_L + 1)) and all(
            h > highs[i + j] for j in range(1, pivot_R + 1)
        )
        is_pl = all(l <= lows[i - j] for j in range(1, pivot_L + 1)) and all(
            l < lows[i + j] for j in range(1, pivot_R + 1)
        )
        if is_ph:
            pivot_highs.append((i, h))
        if is_pl:
            pivot_lows.append((i, l))

    side = (extreme_side or "").upper()
    if side not in ("UP", "DOWN"):
        ch = _f(coin.get("change_24h"), 0) or 0
        side = "UP" if ch > 0 else "DOWN"

    # Ostatni istotny leg:
    # UP impulse: pivot_low → later pivot_high (move >= min, bars >= min)
    # DOWN impulse: pivot_high → later pivot_low
    candidates = []
    if side == "UP":
        for i_lo, p_lo in pivot_lows:
            for i_hi, p_hi in pivot_highs:
                if i_hi <= i_lo:
                    continue
                bars = i_hi - i_lo
                move = p_hi - p_lo
                if bars >= min_bars and move >= min_move:
                    candidates.append((i_lo, i_hi, p_lo, p_hi, move, bars))
    else:
        for i_hi, p_hi in pivot_highs:
            for i_lo, p_lo in pivot_lows:
                if i_lo <= i_hi:
                    continue
                bars = i_lo - i_hi
                move = p_hi - p_lo
                if bars >= min_bars and move >= min_move:
                    candidates.append((i_hi, i_lo, p_lo, p_hi, move, bars))

    if not candidates:
        # Fallback: min/max w oknie TYLKO gdy ruch spełnia min_move
        hi = max(highs)
        lo = min(lows)
        move = hi - lo
        if move >= min_move:
            i_hi = highs.index(hi)
            i_lo = lows.index(lo)
            bars = abs(i_hi - i_lo)
            if bars >= max(2, min_bars // 2):
                out.update({
                    "ok": True,
                    "swing_high": hi,
                    "swing_low": lo,
                    "leg": move,
                    "bars": bars,
                    "method": "window_minmax",
                    "reason": "FALLBACK_MINMAX",
                    "side": side,
                    "min_move": min_move,
                })
                return out
        return {**out, "reason": "NO_VALID_PIVOT", "min_move": min_move, "n_ph": len(pivot_highs), "n_pl": len(pivot_lows)}

    # Bierzemy OSTATNI leg (najbliższy prawej krawędzi) — nie „najładniejszy”
    # sort by end index descending
    candidates.sort(key=lambda c: c[1], reverse=True)
    best = candidates[0]
    # wśród legów kończących się w tym samym miejscu — największy move (istotność)
    end_i = best[1]
    same_end = [c for c in candidates if c[1] == end_i]
    same_end.sort(key=lambda c: c[4], reverse=True)
    best = same_end[0]
    _, _, p_lo, p_hi, move, bars = best

    out.update({
        "ok": True,
        "swing_high": float(p_hi),
        "swing_low": float(p_lo),
        "leg": float(move),
        "bars": int(bars),
        "method": "pivot_atr",
        "reason": "OK",
        "side": side,
        "min_move": float(min_move),
        "n_candidates": len(candidates),
    })
    return out


def fibonacci_map(coin: Dict[str, Any], extreme_side: str = None) -> Dict[str, Any]:
    """
    Po ISTOTNYM swing high/low (detect_valid_swing) wyznacza retracement:
      0.236, 0.382, 0.5, 0.618, 0.786, 1.0

    Swing wybierany obiektywnie (pivot + ATR/% + min bars) — nie pod ładne 0.618.
    """
    px = _f(coin.get("price") or coin.get("blofin_price"))
    if px is None or px <= 0:
        return {"ok": False, "in_zone": False, "weight": 0.0, "reason": "NO_PRICE"}

    side = (extreme_side or "").upper()
    if side not in ("UP", "DOWN"):
        ch = _f(coin.get("change_24h"), 0) or 0
        side = "UP" if ch > 0 else "DOWN"

    swing = detect_valid_swing(coin, extreme_side=side)
    swing_low = swing.get("swing_low")
    swing_high = swing.get("swing_high")

    # Fallback 24h tylko gdy brak valid pivot — oznaczony jako degraded
    ch24 = abs(_f(coin.get("change_24h"), 0) or 0)
    degraded = False
    if not swing.get("ok") or swing_low is None or swing_high is None:
        degraded = True
        if side == "UP":
            swing_high = px
            swing_low = px / (1.0 + max(0.05, ch24 / 100.0))
        else:
            swing_low = px
            swing_high = px * (1.0 + max(0.05, ch24 / 100.0))

    try:
        hi, lo = float(swing_high), float(swing_low)
    except (TypeError, ValueError):
        return {"ok": False, "in_zone": False, "weight": 0.0, "reason": "BAD_SWING"}
    if hi <= lo or hi <= 0:
        return {"ok": False, "in_zone": False, "weight": 0.0, "reason": "FLAT_SWING"}

    rng = hi - lo
    levels = {}
    for r in FIB_LEVELS:
        # Retracement od ekstremum impulsu:
        # po UP (pump) retrace w dół: level = hi - r * range
        # po DOWN (dump) retrace w górę: level = lo + r * range
        if side == "UP":
            levels[r] = hi - r * rng
        else:
            levels[r] = lo + r * rng

    # Gdzie jest cena względem retracement (0 = swing extreme impulsu, 1 = full retrace)
    if side == "UP":
        # 0 at high, 1 at low
        retr = (hi - px) / rng
    else:
        retr = (px - lo) / rng
    retr = max(0.0, min(1.2, retr))

    # Strefy
    in_primary = 0.48 <= retr <= 0.65   # 0.5–0.618
    in_deep = 0.65 < retr <= 0.82       # ~0.786
    in_shallow = 0.20 <= retr < 0.48    # 0.236–0.382
    in_zone = in_primary or in_deep

    weight = 0.0
    zone = "none"
    if in_primary:
        weight = 1.0
        zone = "primary_0.5_0.618"
    elif in_deep:
        weight = 0.75
        zone = "deep_0.786"
    elif in_shallow:
        weight = 0.25
        zone = "shallow"
    elif 0.82 < retr <= 1.05:
        weight = 0.35
        zone = "full_retrace"

    # --- Confluence (Reversal): reclaim / retest konkretnych poziomów ---
    # LONG po DUMP: reclaim >0.382, retest 0.382/0.5
    # SHORT po PUMP: utrata high → retrace, retest 0.5/0.618
    tol = 0.04  # tolerancja wokół poziomu (w retracement units)
    near = {}
    for r in (0.382, 0.5, 0.618, 0.786):
        near[r] = abs(retr - r) <= tol

    ch1 = _f(coin.get("change_1h"), 0) or 0
    confluence = {
        "reclaim_0382": False,
        "retest_0382": False,
        "retest_05": False,
        "retest_0618": False,
        "tags": [],
        "score": 0.0,
    }
    if side == "DOWN":
        # LONG path: cena wraca nad 0.382, retest 0.382/0.5
        if retr >= 0.35:
            confluence["reclaim_0382"] = True
            confluence["tags"].append("FIB_RECLAIM_0.382")
            confluence["score"] += 0.35
        if near.get(0.382) and ch1 >= -0.5:
            confluence["retest_0382"] = True
            confluence["tags"].append("FIB_RETEST_0.382")
            confluence["score"] += 0.40
        if near.get(0.5):
            confluence["retest_05"] = True
            confluence["tags"].append("FIB_RETEST_0.5")
            confluence["score"] += 0.45
        if near.get(0.618):
            confluence["retest_0618"] = True
            confluence["tags"].append("FIB_RETEST_0.618")
            confluence["score"] += 0.30
    else:
        # SHORT path: retrace w dół, retest 0.5 / 0.618
        if near.get(0.5):
            confluence["retest_05"] = True
            confluence["tags"].append("FIB_RETEST_0.5")
            confluence["score"] += 0.45
        if near.get(0.618):
            confluence["retest_0618"] = True
            confluence["tags"].append("FIB_RETEST_0.618")
            confluence["score"] += 0.50
        if near.get(0.382) and ch1 <= 0.5:
            confluence["retest_0382"] = True
            confluence["tags"].append("FIB_RETEST_0.382_SHORT")
            confluence["score"] += 0.25
        if retr >= 0.45:
            confluence["tags"].append("FIB_RETRACE_ACTIVE")
            confluence["score"] += 0.15

    confluence["score"] = min(1.0, confluence["score"])
    # podnieś weight gdy confluence + strefa
    if confluence["score"] >= 0.35 and (in_zone or near.get(0.382) or near.get(0.5)):
        weight = max(weight, 0.55 + 0.45 * confluence["score"])
        if zone == "none":
            zone = "confluence_retest"
    # degraded swing (brak pivotów) → mniejsza wiarygodność Fibo
    if degraded:
        weight *= 0.5

    return {
        "ok": True,
        "side": side,
        "swing_high": hi,
        "swing_low": lo,
        "range": rng,
        "levels": {str(k): round(v, 10) for k, v in levels.items()},
        "retracement": round(retr, 4),
        "zone": zone,
        "in_zone": in_zone or confluence["score"] >= 0.4,
        "in_primary": in_primary,
        "in_deep": in_deep,
        "weight": min(1.0, weight),
        "confluence": confluence,
        "swing": {
            "method": swing.get("method"),
            "bars": swing.get("bars"),
            "leg": swing.get("leg"),
            "min_move": swing.get("min_move"),
            "degraded": degraded,
            "reason": swing.get("reason"),
            "n_candidates": swing.get("n_candidates"),
        },
        "reason": zone if (in_zone or confluence["score"] > 0) else f"OUT({retr:.2f})",
    }


def detect_extreme(coin: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A. Extreme move — duży 24h / z-score / ATR expansion / odchylenie od EMA|VWAP."""
    ch24 = _f(coin.get("change_24h"), 0) or 0
    ch1 = _f(coin.get("change_1h"), 0) or 0
    min_pct = float(getattr(config, "REVERSAL_MIN_24H_PCT", 12.0) or 12.0)
    atr_pct = _f(coin.get("atr_pct") or coin.get("atr_percent"))
    z = _f(coin.get("zscore_24h"))
    px = _f(coin.get("price") or coin.get("blofin_price"))
    side = None
    score = 0.0
    tags: List[str] = []

    if ch24 >= min_pct:
        side = "UP"
        score += min(1.0, (ch24 - min_pct) / 25.0 + 0.4)
        tags.append(f"EXT_PUMP_24H({ch24:+.1f}%)")
    elif ch24 <= -min_pct:
        side = "DOWN"
        score += min(1.0, (abs(ch24) - min_pct) / 25.0 + 0.4)
        tags.append(f"EXT_DUMP_24H({ch24:+.1f}%)")
    else:
        z_min = float(getattr(config, "REVERSAL_ZSCORE_EXTREME", 2.2) or 2.2)
        if z is not None and abs(z) >= z_min:
            side = "UP" if z > 0 else "DOWN"
            score += min(0.7, abs(z) / 4.0)
            tags.append(f"EXT_ZSCORE({z:+.1f})")
        else:
            return None

    if atr_pct is not None and atr_pct > 0:
        if abs(ch24) >= atr_pct * float(getattr(config, "REVERSAL_ATR_MULT", 2.5) or 2.5):
            score += 0.15
            tags.append(f"EXT_ATR_EXP({atr_pct:.1f}%)")

    # Duże odchylenie od EMA / VWAP (nie sam % 24h)
    ema_ref = _f(coin.get("ema_slow") or coin.get("ema_55") or coin.get("ema_50") or coin.get("ema_200"))
    vwap = _f(coin.get("vwap") or coin.get("vwap_24h"))
    dev_thr = float(getattr(config, "REVERSAL_EMA_DEV_PCT", 6.0) or 6.0)
    if px and ema_ref and ema_ref > 0:
        dev = (px - ema_ref) / ema_ref * 100.0
        if side == "UP" and dev >= dev_thr:
            score += min(0.12, 0.04 + (dev - dev_thr) / 40.0)
            tags.append(f"EXT_EMA_STRETCH({dev:+.1f}%)")
        if side == "DOWN" and dev <= -dev_thr:
            score += min(0.12, 0.04 + (abs(dev) - dev_thr) / 40.0)
            tags.append(f"EXT_EMA_STRETCH({dev:+.1f}%)")
    if px and vwap and vwap > 0:
        dev_v = (px - vwap) / vwap * 100.0
        if side == "UP" and dev_v >= dev_thr:
            score += 0.06
            tags.append(f"EXT_VWAP_STRETCH({dev_v:+.1f}%)")
        if side == "DOWN" and dev_v <= -dev_thr:
            score += 0.06
            tags.append(f"EXT_VWAP_STRETCH({dev_v:+.1f}%)")

    return {"side": side, "score": round(min(1.0, score), 3), "tags": tags, "ch24": ch24, "ch1": ch1}


def detect_exhaustion(coin: Dict[str, Any], extreme: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """B. Exhaustion — RSI extreme, momentum slow, MACD/cipher, vol climax."""
    side = extreme["side"]
    ch1 = _f(coin.get("change_1h"), 0) or 0
    rsi = _f(coin.get("rsi"))
    macd_sig = str(coin.get("macd_signal") or "").lower()
    cipher = coin.get("cipher_b") if isinstance(coin.get("cipher_b"), dict) else {}
    vol_flag = str(coin.get("vol_flag") or "")
    score = 0.0
    tags: List[str] = []
    need = float(getattr(config, "REVERSAL_EXHAUST_MIN_SCORE", 0.20) or 0.20)

    if rsi is None:
        score += 0.12
        tags.append("EXH_NO_RSI_USE_1H")

    if side == "DOWN":
        if rsi is not None:
            if rsi <= float(getattr(config, "REVERSAL_RSI_LONG_MAX", 38.0)):
                score += 0.25
                tags.append(f"EXH_RSI_OS({rsi:.1f})")
            elif rsi <= 45:
                score += 0.10
                tags.append(f"EXH_RSI_SOFT({rsi:.1f})")
    else:
        if rsi is not None:
            if rsi >= float(getattr(config, "REVERSAL_RSI_SHORT_MIN", 62.0)):
                score += 0.25
                tags.append(f"EXH_RSI_OB({rsi:.1f})")
            elif rsi >= 55:
                score += 0.10
                tags.append(f"EXH_RSI_SOFT({rsi:.1f})")

    stall = float(getattr(config, "REVERSAL_1H_STALL_PCT", 2.0) or 2.0)
    if side == "DOWN":
        if ch1 >= -stall:
            score += 0.18
            tags.append(f"EXH_MOM_SLOW({ch1:+.1f}%)")
        if ch1 > 0:
            score += 0.10
            tags.append(f"EXH_1H_TURN({ch1:+.1f}%)")
        if ch1 < -stall * 1.8:
            score -= 0.15
            tags.append(f"EXH_STILL_DUMPING({ch1:+.1f}%)")
    else:
        # SHORT setup: pump exhaustion
        if ch1 <= stall:
            score += 0.18
            tags.append(f"EXH_MOM_SLOW({ch1:+.1f}%)")
        if ch1 < 0:
            score += 0.12
            tags.append(f"EXH_LOWER_HIGH_HINT({ch1:+.1f}%)")  # failure to extend / turn down
        if 0 < ch1 <= stall * 0.6:
            score += 0.06
            tags.append("EXH_FAIL_NEW_HIGH")  # stall near top
        if ch1 > stall * 1.8:
            score -= 0.15
            tags.append(f"EXH_STILL_PUMPING({ch1:+.1f}%)")

    if side == "DOWN" and ("bull" in macd_sig or macd_sig in ("bullish", "bullish_cross")):
        score += 0.10
        tags.append("EXH_MACD_IMPROVING")
    if side == "UP" and ("bear" in macd_sig or macd_sig in ("bearish", "bearish_cross")):
        score += 0.10
        tags.append("EXH_MACD_IMPROVING")

    if side == "DOWN" and (cipher.get("bull_div") or cipher.get("oversold") or cipher.get("cross_up")):
        score += 0.12
        tags.append("EXH_CIPHER_BULL")
    if side == "UP" and (cipher.get("bear_div") or cipher.get("overbought") or cipher.get("cross_down")):
        score += 0.12
        tags.append("EXH_CIPHER_BEAR")

    if "SPIKE" in vol_flag:
        score += 0.08
        tags.append("EXH_VOL_CLIMAX")
    elif "WEAK" in vol_flag or "THIN" in vol_flag:
        score -= 0.05
        tags.append("EXH_VOL_THIN")

    if score < need:
        return None
    return {"score": round(min(1.0, score), 3), "tags": tags}


def _swing_structure_confirm(coin: Dict[str, Any], direction: str) -> Dict[str, Any]:
    """
    Struktura z lows/highs:
      LONG  — failure to make new low + higher low / reclaim
      SHORT — failure to make new high + lower high / utrata poziomu
    """
    out = {"score": 0.0, "tags": [], "confirms": 0}
    lows = coin.get("lows") or coin.get("ohlcv_lows") or []
    highs = coin.get("highs") or coin.get("ohlcv_highs") or []
    px = _f(coin.get("price") or coin.get("blofin_price"))
    if px is None or px <= 0:
        return out
    try:
        lows_f = [float(x) for x in list(lows)[-16:] if x is not None]
        highs_f = [float(x) for x in list(highs)[-16:] if x is not None]
    except (TypeError, ValueError):
        return out

    if direction == "LONG" and len(lows_f) >= 4:
        # najniższy low w oknie vs ostatnie 2–3 bary
        abs_low = min(lows_f[:-2]) if len(lows_f) > 3 else min(lows_f)
        recent_low = min(lows_f[-3:])
        # failure to make new low: recent low > abs low (nie zrobił nowego dołka)
        if recent_low > abs_low * 1.001:
            out["score"] += 0.10
            out["confirms"] += 1
            out["tags"].append("CONF_FAIL_NEW_LOW")
        # higher low: ostatni low > poprzedni lokalny low
        if len(lows_f) >= 5 and lows_f[-1] > lows_f[-3]:
            out["score"] += 0.08
            out["confirms"] += 1
            out["tags"].append("CONF_HIGHER_LOW_STRUCT")
        # reclaim: cena powyżej recent low + buffer
        if px > recent_low * 1.005:
            out["score"] += 0.05
            out["tags"].append("CONF_RECLAIM_LOW")

    if direction == "SHORT" and len(highs_f) >= 4:
        abs_high = max(highs_f[:-2]) if len(highs_f) > 3 else max(highs_f)
        recent_high = max(highs_f[-3:])
        if recent_high < abs_high * 0.999:
            out["score"] += 0.10
            out["confirms"] += 1
            out["tags"].append("CONF_FAIL_NEW_HIGH_STRUCT")
        if len(highs_f) >= 5 and highs_f[-1] < highs_f[-3]:
            out["score"] += 0.08
            out["confirms"] += 1
            out["tags"].append("CONF_LOWER_HIGH_STRUCT")
        # utrata poziomu: cena poniżej recent high
        if px < recent_high * 0.995:
            out["score"] += 0.05
            out["tags"].append("CONF_LOST_HIGH")
    return out


def detect_confirmation(
    coin: Dict[str, Any],
    extreme: Dict[str, Any],
    exhaustion: Dict[str, Any],
    regime: str = "UNKNOWN",
) -> Optional[Dict[str, Any]]:
    """C. Confirmation — failure to continue, structure, OB, multi-src, BTC."""
    side = extreme["side"]
    direction = "LONG" if side == "DOWN" else "SHORT"
    ch1 = _f(coin.get("change_1h"), 0) or 0
    ob = coin.get("order_book") if isinstance(coin.get("order_book"), dict) else {}
    src = coin.get("source_div") if isinstance(coin.get("source_div"), dict) else {}
    bn_ch = _f(coin.get("binance_change_24h"))
    bf_ch = _f(coin.get("blofin_change_24h"), _f(coin.get("change_24h"), 0))
    btc_ch = _f(coin.get("btc_change_24h") or coin.get("_btc_24h"), 0) or 0
    rsi = _f(coin.get("rsi"))
    score = 0.0
    tags: List[str] = []
    confirms = 0
    min_conf = int(getattr(config, "REVERSAL_MIN_CONFIRMATIONS", 1) or 1)
    stall = float(getattr(config, "REVERSAL_1H_STALL_PCT", 2.0) or 2.0)

    # Struktura swing (failure new low/high, higher/lower)
    struct = _swing_structure_confirm(coin, direction)
    score += float(struct.get("score") or 0)
    confirms += int(struct.get("confirms") or 0)
    tags.extend(list(struct.get("tags") or []))

    # Fibonacci = confluence (reclaim/retest), nie sam sygnał
    # LONG: dump → reclaim 0.382 → retest 0.382/0.5
    # SHORT: pump → retrace → retest 0.5/0.618
    extreme_side = "DOWN" if direction == "LONG" else "UP"
    fib = fibonacci_map(coin, extreme_side=extreme_side)
    coin["_fib_map"] = fib
    conf_fib = (fib.get("confluence") or {}) if fib.get("ok") else {}
    if fib.get("ok") and (fib.get("in_zone") or float(conf_fib.get("score") or 0) >= 0.35):
        w = float(fib.get("weight") or 0)
        cscore = float(conf_fib.get("score") or 0)
        score += 0.10 * w + 0.14 * cscore
        if cscore >= 0.35:
            confirms += 1
        for t in (conf_fib.get("tags") or [])[:4]:
            tags.append(str(t))
        if fib.get("in_primary"):
            tags.append(f"FIB_PRIMARY({fib.get('retracement'):.2f})")
        elif fib.get("in_deep"):
            tags.append(f"FIB_DEEP_0.786({fib.get('retracement'):.2f})")
    elif fib.get("ok"):
        tags.append(f"FIB_OUT({fib.get('retracement', 0):.2f})")

    if direction == "LONG":
        if ch1 > 0:
            score += 0.20
            confirms += 1
            tags.append(f"CONF_HIGHER_LOW({ch1:+.1f}%)")
        elif ch1 >= -stall * 0.5:
            score += 0.10
            confirms += 1
            tags.append(f"CONF_FAIL_CONTINUE({ch1:+.1f}%)")
        macd_sig = str(coin.get("macd_signal") or "").lower()
        if "bull" in macd_sig:
            score += 0.08
            confirms += 1
            tags.append("CONF_RECLAIM_MOMENTUM_MACD")
        ema_f = _f(coin.get("ema_fast") or coin.get("ema_21"))
        ema_s = _f(coin.get("ema_slow") or coin.get("ema_55") or coin.get("ema_50"))
        px = _f(coin.get("price") or coin.get("blofin_price"))
        vwap = _f(coin.get("vwap") or coin.get("vwap_24h"))
        if ema_f is not None and ema_s is not None and ema_f > ema_s:
            score += 0.10
            confirms += 1
            tags.append("CONF_RECLAIM_EMA")
        elif px is not None and ema_s is not None and px > ema_s:
            score += 0.08
            confirms += 1
            tags.append("CONF_ABOVE_EMA")
        if px is not None and vwap is not None and px > vwap:
            score += 0.06
            confirms += 1
            tags.append("CONF_RECLAIM_VWAP")
    else:
        # SHORT: lower high → loss of support
        if ch1 < 0:
            score += 0.20
            confirms += 1
            tags.append(f"CONF_LOWER_HIGH({ch1:+.1f}%)")
        elif ch1 <= stall * 0.5:
            score += 0.10
            confirms += 1
            tags.append(f"CONF_FAIL_NEW_HIGH({ch1:+.1f}%)")

        macd_sig = str(coin.get("macd_signal") or "").lower()
        if "bear" in macd_sig:
            score += 0.08
            confirms += 1
            tags.append("CONF_LOSS_MOMENTUM_MACD")
        ema_f = _f(coin.get("ema_fast") or coin.get("ema_21"))
        ema_s = _f(coin.get("ema_slow") or coin.get("ema_55") or coin.get("ema_50"))
        px = _f(coin.get("price") or coin.get("blofin_price"))
        vwap = _f(coin.get("vwap") or coin.get("vwap_24h"))
        if ema_f is not None and ema_s is not None and ema_f < ema_s:
            score += 0.10
            confirms += 1
            tags.append("CONF_LOSS_EMA_SUPPORT")
        elif px is not None and ema_s is not None and px < ema_s:
            score += 0.08
            confirms += 1
            tags.append("CONF_BELOW_EMA")
        if px is not None and vwap is not None and px < vwap:
            score += 0.06
            confirms += 1
            tags.append("CONF_LOST_VWAP")

    imb = _f(ob.get("ob_imbalance"))
    bias = str(ob.get("ob_bias") or "").lower()
    if direction == "LONG":
        if (imb is not None and imb >= 0.12) or bias in ("buy", "bid"):
            score += 0.15
            confirms += 1
            tags.append(f"CONF_OB_BID({imb if imb is not None else bias})")
        elif imb is not None and imb <= -0.15:
            score -= 0.08
            tags.append("CONF_OB_STILL_SELL")
    else:
        if (imb is not None and imb <= -0.12) or bias in ("sell", "ask"):
            score += 0.15
            confirms += 1
            tags.append(f"CONF_OB_ASK({imb if imb is not None else bias})")
        elif imb is not None and imb >= 0.15:
            score -= 0.08
            tags.append("CONF_OB_STILL_BUY")

    n_src = int(src.get("sources_available") or 0)
    max_diff = _f(src.get("max_diff_pct"), 0) or 0
    if bool(getattr(config, "SOURCE_DIVERGENCE_GATE", False)) and bn_ch is not None:
        if bf_ch is not None:
            bn_diff = abs(float(bf_ch) - float(bn_ch))
            same_sign = (float(bf_ch) >= 0 and float(bn_ch) >= 0) or (float(bf_ch) < 0 and float(bn_ch) < 0)
            if same_sign and bn_diff <= 1.5:
                score += 0.10
                confirms += 1
                tags.append(f"CONF_BINANCE_ALIGN(BF{bf_ch:+.1f}/BN{bn_ch:+.1f})")
            elif bn_diff >= float(getattr(config, "BN_BF_DIVERGENCE_HARD_PCT", 3.0)) or not same_sign and abs(float(bf_ch)) >= 1.5:
                score -= 0.12
                tags.append(f"CONF_BINANCE_DIVERGE(BF{bf_ch:+.1f}/BN{bn_ch:+.1f})")
            else:
                tags.append(f"CONF_BINANCE_NEUTRAL(BF{bf_ch:+.1f}/BN{bn_ch:+.1f})")
        elif n_src >= 2 and max_diff < float(getattr(config, "REVERSAL_MAX_SRC_DIFF", 1.5) or 1.5):
            score += 0.05
            tags.append(f"CONF_MULTI_SRC({n_src})")
    else:
        tags.append("CONF_BINANCE_UNKNOWN")
        if bool(getattr(config, "BN_CONFIRMATION_REQUIRED", True)):
            # Nie blokuj shadow discovery, ale nie licz UNKNOWN jako potwierdzenia.
            pass

    if bool(getattr(config, "SOURCE_DIVERGENCE_GATE", False)) and n_src >= 2 and max_diff >= 3.0:
        score -= 0.12
        tags.append(f"CONF_SRC_DIVERGE({max_diff:.1f}%)")

    if direction == "LONG" and btc_ch > -1.0:
        score += 0.08
        tags.append(f"CONF_BTC_STABLE({btc_ch:+.1f}%)")
        if btc_ch > 0.5:
            confirms += 1
            tags.append("CONF_BTC_RECOVERY")
    if direction == "SHORT" and btc_ch < 1.0:
        score += 0.08
        tags.append(f"CONF_BTC_STABLE({btc_ch:+.1f}%)")
        if btc_ch < -0.5:
            confirms += 1
            tags.append("CONF_BTC_WEAK")

    if direction == "LONG" and rsi is not None and 28 <= rsi <= 42:
        score += 0.08
        tags.append(f"CONF_RSI_ZONE({rsi:.0f})")
    if direction == "SHORT" and rsi is not None and 58 <= rsi <= 72:
        score += 0.08
        tags.append(f"CONF_RSI_ZONE({rsi:.0f})")

    regime_u = (regime or "UNKNOWN").upper()
    if regime_u in ("PANIC", "RANGE", "EXTREME"):
        score += 0.08
        tags.append(f"CONF_REGIME_{regime_u}")
    elif regime_u in ("TREND_UP", "TREND_DOWN"):
        score -= 0.06
        tags.append(f"CONF_VS_{regime_u}")

    score += 0.15 * float(exhaustion.get("score") or 0)

    bypass = float(getattr(config, "REVERSAL_CONF_SCORE_BYPASS", 0.0) or 0.0)
    confirmed = confirms >= min_conf
    if not confirmed and (bypass <= 0.0 or score < bypass):
        return None

    return {
        "direction": direction,
        "score": round(min(1.0, max(0.0, score)), 3),
        "confirms": confirms,
        "min_confirms": min_conf,
        "confirmed": bool(confirmed),
        "status": "CONFIRMED" if confirmed else "BYPASS",
        "tags": tags,
    }




def _structural_sl_tp(
    coin: Dict[str, Any],
    direction: str,
    price: float,
) -> Dict[str, float]:
    """
    SL = structural level ± ATR buffer  (NIE dokładnie na Fibo)

    LONG:  swing_low  - ATR * buffer
    SHORT: swing_high + ATR * buffer

    Fib tylko do sanity: entry przy 0.618 → SL za swingiem ~0.786 − ATR.
    Jeżeli SL absurdalnie szeroki → sl_ok=False → NO TRADE.
    """
    atr_pct = _f(coin.get("atr_pct") or coin.get("atr_percent"))
    atr_abs = _f(coin.get("atr"))
    if atr_abs is None and atr_pct is not None and price > 0:
        atr_abs = price * (atr_pct / 100.0)
    if atr_abs is None or atr_abs <= 0:
        # fallback ~1.2% ceny gdy brak ATR
        atr_abs = price * float(getattr(config, "REVERSAL_ATR_FALLBACK_PCT", 0.012) or 0.012)

    buf = float(getattr(config, "REVERSAL_ATR_SL_BUFFER", 0.6) or 0.6)
    # swing z OHLCV jeśli jest
    lows = coin.get("lows") or coin.get("ohlcv_lows") or []
    highs = coin.get("highs") or coin.get("ohlcv_highs") or []
    lookback = int(getattr(config, "REVERSAL_SWING_LOOKBACK", 12) or 12)

    # Preferuj swing z fib_map (ta sama noga impulsu)
    fib = coin.get("_fib_map") if isinstance(coin.get("_fib_map"), dict) else None
    swing_low = None
    swing_high = None
    if fib and fib.get("ok"):
        try:
            if fib.get("swing_low") is not None:
                swing_low = float(fib["swing_low"])
            if fib.get("swing_high") is not None:
                swing_high = float(fib["swing_high"])
        except (TypeError, ValueError):
            pass
    # OHLCV tylko gdy brak swing z Fib
    try:
        if swing_low is None and lows and len(lows) >= 3:
            window = [float(x) for x in list(lows)[-lookback:] if x is not None]
            if window:
                swing_low = min(window)
        if swing_high is None and highs and len(highs) >= 3:
            window = [float(x) for x in list(highs)[-lookback:] if x is not None]
            if window:
                swing_high = max(window)
    except (TypeError, ValueError):
        pass

    # fallback strukturalny z impulsu 24h (przybliżony swing)
    ch24 = abs(_f(coin.get("change_24h"), 0) or 0)
    if swing_low is None and direction == "LONG":
        swing_low = price * (1.0 - min(0.12, max(0.02, ch24 / 100.0 * 0.25)))
    if swing_high is None and direction == "SHORT":
        swing_high = price * (1.0 + min(0.12, max(0.02, ch24 / 100.0 * 0.25)))

    # Opcjonalnie: structural bliżej Fib 0.786 (głębszy poziom unieważnienia)
    # NIE stawiamy SL dokładnie na 0.786 — tylko używamy jako referencji swing
    fib_ref = None
    if fib and fib.get("ok") and isinstance(fib.get("levels"), dict):
        try:
            fib_ref = float(fib["levels"].get("0.786") or fib["levels"].get(0.786) or 0) or None
        except (TypeError, ValueError):
            fib_ref = None

    if direction == "LONG":
        structural = float(swing_low if swing_low is not None else price * 0.97)
        # jeśli fib 0.786 jest poniżej entry i blisko swing — trzymaj structural = min(swing, fib*soft)
        if fib_ref and fib_ref < price:
            structural = min(structural, fib_ref)
        # SL = structural − ATR buffer (NIE na czystym Fibo)
        sl = structural - atr_abs * buf
        sl = min(sl, price - atr_abs * 0.4)
        method = "swing_low+ATR" if (swing_low is not None) else "impulse_low+ATR"
        if fib_ref:
            method = "fib_swing+ATR"
    else:
        structural = float(swing_high if swing_high is not None else price * 1.03)
        if fib_ref and fib_ref > price:
            structural = max(structural, fib_ref)
        sl = structural + atr_abs * buf
        sl = max(sl, price + atr_abs * 0.4)
        method = "swing_high+ATR" if (swing_high is not None) else "impulse_high+ATR"
        if fib_ref:
            method = "fib_swing+ATR"

    # Szerokość SL — bez clampowania w dół „na siłę”; zamiast tego flaga sl_ok
    max_sl_pct = float(getattr(config, "REVERSAL_MAX_SL_PCT", 0.08) or 0.08)
    raw_dist = abs(price - sl) / price if price else 0.0
    sl_ok = raw_dist <= max_sl_pct and raw_dist >= 0.003
    # clamp tylko dla ekstremów (ochrona), ale zaznacz że było za szeroko
    if direction == "LONG":
        if sl < price * (1.0 - max_sl_pct):
            sl = price * (1.0 - max_sl_pct)
            sl_ok = False  # wymagany strukturalny SL był absurdalnie szeroki
        risk = max(price - sl, atr_abs * 0.5)
    else:
        if sl > price * (1.0 + max_sl_pct):
            sl = price * (1.0 + max_sl_pct)
            sl_ok = False
        risk = max(sl - price, atr_abs * 0.5)

    # --- TP: Fib extensions jako mapa, nie „zawsze 1.618” ---
    # TP1 = 1R (logiczny najbliższy cel / risk multiple)
    # TP2 = extension (domyślnie 1.618 leg) jeśli sensowny względem 1R
    # TP3 = trailing
    # Extensions: 1.0, 1.272, 1.618, 2.0, 2.618 od swing leg
    ext_mult = float(getattr(config, "REVERSAL_FIB_TP_EXT", 1.618) or 1.618)
    leg = None
    if swing_high is not None and swing_low is not None:
        try:
            leg = abs(float(swing_high) - float(swing_low))
        except (TypeError, ValueError):
            leg = None
    if not leg or leg <= 0:
        leg = risk * 2.0  # fallback

    # Extension levels measured from entry in trade direction
    # (klasycznie extension od swing — tu projekcja od entry w stronę trade)
    def _ext_price(mult: float) -> float:
        if direction == "LONG":
            return price + leg * mult
        return price - leg * mult

    fib_ext_levels = {
        "1.0": _ext_price(1.0),
        "1.272": _ext_price(1.272),
        "1.618": _ext_price(1.618),
        "2.0": _ext_price(2.0),
        "2.618": _ext_price(2.618),
    }

    if direction == "LONG":
        tp1 = price + risk * 1.0  # 1R — najbliższy logiczny
        # TP2 = extension, ale nie bliższy niż ~1.3R i nie absurdalnie daleki
        tp2_fib = _ext_price(ext_mult)
        min_tp2 = price + risk * 1.3
        max_tp2 = price + risk * 3.5
        tp2 = max(min_tp2, min(tp2_fib, max_tp2))
        # jeśli fib extension jest blisko 1R — użyj 2R jako fallback
        if abs(tp2 - tp1) / price < 0.003:
            tp2 = price + risk * float(getattr(config, "REVERSAL_TP_R_MULT", 2.0) or 2.0)
        tp = tp2
        tp_method = f"1R+fib_ext_{ext_mult}"
    else:
        tp1 = price - risk * 1.0
        tp2_fib = _ext_price(ext_mult)
        min_tp2 = price - risk * 1.3
        max_tp2 = price - risk * 3.5
        # SHORT: tp2 dalej w dół → mniejsza cena
        tp2 = min(min_tp2, max(tp2_fib, max_tp2))
        if abs(tp2 - tp1) / price < 0.003:
            tp2 = price - risk * float(getattr(config, "REVERSAL_TP_R_MULT", 2.0) or 2.0)
        tp = tp2
        tp_method = f"1R+fib_ext_{ext_mult}"

    return {
        "sl_price": float(sl),
        "tp_price": float(tp),
        "tp1_price": float(tp1),
        "tp2_price": float(tp2),
        "risk_abs": float(risk),
        "structural_level": float(structural),
        "atr_abs": float(atr_abs),
        "atr_buffer": buf,
        "sl_method": method,
        "tp_method": tp_method,
        "sl_dist_pct": abs(price - sl) / price if price else 0,
        "sl_raw_dist_pct": raw_dist,
        "sl_ok": sl_ok,
        "fib_ref_0786": fib_ref,
        "fib_extensions": {k: round(v, 10) for k, v in fib_ext_levels.items()},
        "tp_plan": {
            "tp1": "1R",
            "tp2": f"fib_ext_{ext_mult}",
            "tp3": "trailing",
            "tp1_r": 1.0,
            "tp2_r": round(abs(tp2 - price) / risk, 3) if risk else 2.0,
            "frac_tp1": float(getattr(config, "REVERSAL_TP1_FRAC", 0.25)),
            "frac_tp2": float(getattr(config, "REVERSAL_TP2_FRAC", 0.35)),
            "frac_trail": float(getattr(config, "REVERSAL_TP3_FRAC", 0.40)),
        },
    }


def compute_reversal_score(
    coin: Dict[str, Any],
    extreme: Dict[str, Any],
    exhaustion: Dict[str, Any],
    conf: Dict[str, Any],
    regime: str = "UNKNOWN",
) -> Dict[str, Any]:
    """
    Osobny scoring REVERSAL — nie współdzielony z Trend Engine strength.

    Framework (wagi START — do testów, nie święte):
      Exhaustion              ~0.20
      Divergence              ~0.20   (w exhaustion / cipher)
      Structure reversal      ~0.20
      Momentum reversal       ~0.15
      Fibonacci confluence    ~0.10   ← jeden element, nie dominanta
      Orderbook absorption    ~0.10
      Cross-market            ~0.05
      − adverse
    """
    direction = conf["direction"]
    ch24 = abs(float(extreme.get("ch24") or 0))
    ch1 = float(extreme.get("ch1") or _f(coin.get("change_1h"), 0) or 0)
    rsi = _f(coin.get("rsi"))
    macd_sig = str(coin.get("macd_signal") or "").lower()
    cipher = coin.get("cipher_b") if isinstance(coin.get("cipher_b"), dict) else {}
    vol_flag = str(coin.get("vol_flag") or "")
    ob = coin.get("order_book") if isinstance(coin.get("order_book"), dict) else {}
    src = coin.get("source_div") if isinstance(coin.get("source_div"), dict) else {}
    btc_ch = _f(coin.get("_btc_24h") or coin.get("btc_change_24h"), 0) or 0
    spread = _f(ob.get("ob_spread_pct"))
    depth = _f(ob.get("ob_depth_usd"))
    imb = _f(ob.get("ob_imbalance"))

    components: Dict[str, float] = {}
    notes: List[str] = []

    # --- Extreme move (0–0.20) ---
    min_pct = float(getattr(config, "REVERSAL_MIN_24H_PCT", 12.0) or 12.0)
    ext = min(0.20, 0.08 + (ch24 - min_pct) / 80.0)
    if float(extreme.get("score") or 0) >= 0.7:
        ext = min(0.20, ext + 0.04)
    components["extreme_move"] = round(max(0.0, ext), 3)
    notes.append(f"ext={components['extreme_move']:.2f}")

    # --- Exhaustion ~0.20 (RSI extreme + volume climax) ---
    exh = 0.55 * float(exhaustion.get("score") or 0)
    if direction == "LONG" and rsi is not None:
        if rsi <= 30:
            exh += 0.08
        elif rsi <= 38:
            exh += 0.05
    if direction == "SHORT" and rsi is not None:
        if rsi >= 70:
            exh += 0.08
        elif rsi >= 62:
            exh += 0.05
    if "SPIKE" in vol_flag:
        exh += 0.04
        notes.append("vol_climax")
    components["exhaustion"] = round(min(0.20, exh), 3)

    # --- Divergence ~0.20 (osobno — ważniejsze niż samo RSI) ---
    div = 0.0
    if direction == "LONG" and (cipher.get("bull_div") or cipher.get("oversold")):
        div += 0.12 if cipher.get("bull_div") else 0.06
        notes.append("rsi_div_bull")
    if direction == "SHORT" and (cipher.get("bear_div") or cipher.get("overbought")):
        div += 0.12 if cipher.get("bear_div") else 0.06
        notes.append("rsi_div_bear")
    # soft RSI extreme jako część dywergencji strukturalnej
    if direction == "LONG" and rsi is not None and rsi <= 28:
        div += 0.05
    if direction == "SHORT" and rsi is not None and rsi >= 72:
        div += 0.05
    components["divergence"] = round(min(0.20, div), 3)

    # --- Momentum reversal MACD/1h (0–0.18) ---
    mom = 0.0
    if direction == "LONG":
        if ch1 > 0:
            mom += 0.10
        elif ch1 > -1.0:
            mom += 0.04
        if "bull" in macd_sig:
            mom += 0.08
    else:
        if ch1 < 0:
            mom += 0.10
        elif ch1 < 1.0:
            mom += 0.04
        if "bear" in macd_sig:
            mom += 0.08
    components["momentum_reversal"] = round(min(0.18, mom), 3)

    # --- Structure confirmation (0–0.20) ---
    # z conf tags + EMA
    struct = 0.0
    conf_tags = conf.get("tags") or []
    for key, pts in (
        ("CONF_HIGHER_LOW", 0.08),
        ("CONF_LOWER_HIGH", 0.08),
        ("CONF_HIGHER_LOW_STRUCT", 0.08),
        ("CONF_LOWER_HIGH_STRUCT", 0.08),
        ("CONF_FAIL_NEW_LOW", 0.07),
        ("CONF_FAIL_NEW_HIGH_STRUCT", 0.07),
        ("CONF_FAIL_CONTINUE", 0.08),
        ("CONF_FAIL_NEW_HIGH", 0.05),
        ("CONF_RECLAIM_LOW", 0.04),
        ("CONF_LOST_HIGH", 0.04),
        ("CONF_RECLAIM_EMA", 0.07),
        ("CONF_LOSS_EMA_SUPPORT", 0.07),
        ("CONF_ABOVE_EMA", 0.05),
        ("CONF_BELOW_EMA", 0.05),
        ("CONF_RECLAIM_VWAP", 0.05),
        ("CONF_LOST_VWAP", 0.05),
        ("CONF_RECLAIM_MOMENTUM_MACD", 0.05),
        ("CONF_LOSS_MOMENTUM_MACD", 0.05),
    ):
        if any(str(t).startswith(key) for t in conf_tags):
            struct += pts
    struct += 0.03 * min(3, int(conf.get("confirms") or 0))
    components["structure_confirmation"] = round(min(0.20, struct), 3)

    # --- Fibonacci confluence ~0.10 (jeden element scoringu, NIE dominanta) ---
    fib = coin.get("_fib_map") or fibonacci_map(
        coin, extreme_side="DOWN" if direction == "LONG" else "UP"
    )
    fib_w = 0.0
    if fib.get("ok"):
        conf_f = fib.get("confluence") or {}
        zone_w = float(fib.get("weight") or 0) if fib.get("in_zone") else 0.0
        conf_s = float(conf_f.get("score") or 0)
        fib_w = 0.06 * zone_w + 0.08 * conf_s
        if conf_s > 0 or zone_w > 0:
            notes.append(f"fib:{fib.get('zone')}|conf={conf_s:.2f}")
            for t in (conf_f.get("tags") or [])[:3]:
                notes.append(str(t).lower())
    components["fibonacci_confluence"] = round(min(0.10, fib_w), 3)
    components["fibonacci_zone"] = components["fibonacci_confluence"]  # alias

    # --- Orderbook absorption ~0.10 ---
    liq = 0.0
    if depth is not None:
        if depth >= 10000:
            liq += 0.05
        elif depth >= 3500:
            liq += 0.03
        elif depth < 1500:
            liq -= 0.05
            notes.append("thin_book")
    if spread is not None:
        if spread <= 0.08:
            liq += 0.03
        elif spread > 0.35:
            liq -= 0.06
            notes.append("wide_spread")
    if direction == "LONG" and imb is not None and imb >= 0.12:
        liq += 0.04
    if direction == "SHORT" and imb is not None and imb <= -0.12:
        liq += 0.04
    components["orderbook_absorption"] = round(max(-0.08, min(0.10, liq)), 3)
    components["liquidity_confirmation"] = components["orderbook_absorption"]

    # --- Cross-market ~0.05 ---
    x = 0.0
    n_src = int(src.get("sources_available") or 0)
    max_diff = _f(src.get("max_diff_pct"), 0) or 0
    if bool(getattr(config, "SOURCE_DIVERGENCE_GATE", False)):
        if n_src >= 2 and max_diff < 1.5:
            x += 0.03
        if n_src >= 3:
            x += 0.01
        if max_diff >= 3.0:
            x -= 0.05
            notes.append("src_diverge")
    regime_u = (regime or "").upper()
    if direction == "LONG" and btc_ch > 0.3:
        x += 0.02
    if direction == "SHORT" and btc_ch < -0.3:
        x += 0.02
    if regime_u in ("PANIC", "RANGE", "EXTREME"):
        x += 0.01
    components["cross_market_confirmation"] = round(max(-0.05, min(0.05, x)), 3)

    # --- Adverse conditions (penalty) ---
    adverse = 0.0
    if direction == "LONG" and btc_ch <= -2.5:
        adverse += 0.08
        notes.append("btc_dump")
    if direction == "SHORT" and btc_ch >= 2.5:
        adverse += 0.08
        notes.append("btc_pump")
    if regime_u == "TREND_UP" and direction == "SHORT":
        adverse += 0.05
    if regime_u == "TREND_DOWN" and direction == "LONG":
        adverse += 0.05
    # still extending hard
    if direction == "LONG" and ch1 < -3.0:
        adverse += 0.10
        notes.append("still_dumping")
    if direction == "SHORT" and ch1 > 3.0:
        adverse += 0.10
        notes.append("still_pumping")
    # RSI alone is NOT enough — if only RSI and no structure, already low structure component
    components["adverse"] = round(min(0.25, adverse), 3)

    # Momentum cap ~0.15
    if "momentum_reversal" in components:
        components["momentum_reversal"] = round(
            min(0.15, float(components["momentum_reversal"])), 3
        )

    total = (
        components.get("exhaustion", 0.0)
        + components.get("divergence", 0.0)
        + components.get("structure_confirmation", 0.0)
        + components.get("momentum_reversal", 0.0)
        + components.get("fibonacci_confluence", components.get("fibonacci_zone", 0.0))
        + components.get("orderbook_absorption", components.get("liquidity_confirmation", 0.0))
        + components.get("cross_market_confirmation", 0.0)
        + 0.5 * components.get("extreme_move", 0.0)  # extreme = kontekst, nie dominanta
        - components.get("adverse", 0.0)
    )
    total = max(0.0, min(0.95, total))

    return {
        "reversal_score": round(total, 4),
        "components": components,
        "score_framework": "rev_exh20_div20_struct20_mom15_fib10_ob10_x5",
        "fib_map": fib if isinstance(fib, dict) else None,
        "notes": notes,
    }


def score_reversal_candidate(
    coin: Dict[str, Any],
    regime: str = "UNKNOWN",
    btc_change_24h: float = 0.0,
) -> Optional[Dict[str, Any]]:
    if not bool(getattr(config, "REVERSAL_ENGINE_ENABLED", True)):
        return None
    sym = coin.get("symbol") or ""
    if not sym:
        return None
    price = _f(coin.get("price") or coin.get("blofin_price"), 0) or 0
    if price <= 0:
        return None

    coin = dict(coin)
    coin["_btc_24h"] = btc_change_24h

    extreme = detect_extreme(coin)
    if not extreme:
        return None
    exhaustion = detect_exhaustion(coin, extreme)
    if not exhaustion:
        return None
    conf = detect_confirmation(coin, extreme, exhaustion, regime=regime)
    if not conf:
        return None

    direction = conf["direction"]
    scored = compute_reversal_score(coin, extreme, exhaustion, conf, regime=regime)
    strength = float(scored["reversal_score"])
    min_str = float(getattr(config, "REVERSAL_MIN_STRENGTH", 0.48) or 0.48)
    comps = scored["components"]

    # --- Jakość: Divergence + Fibonacci + Market Structure ---
    # To jest docelowy zestaw; RSI<30 → BUY jest ZA SŁABE.
    div_v = float(comps.get("divergence", 0) or 0)
    fib_v = float(
        comps.get("fibonacci_confluence")
        or comps.get("fibonacci_zone")
        or 0
    )
    struct_v = float(comps.get("structure_confirmation", 0) or 0)
    mom_v = float(comps.get("momentum_reversal", 0) or 0)

    # Guard: nie otwieramy tylko bo RSI=25
    if (struct_v + mom_v) < 0.08:
        return None

    # Triad quality (preferowane)
    has_div = div_v >= 0.06
    has_fib = fib_v >= 0.04
    has_struct = struct_v >= 0.08
    triad_count = int(has_div) + int(has_fib) + int(has_struct)
    quality_setup = triad_count >= 2  # min 2 z 3; ideał = 3

    # Twardy tryb jakości (opcjonalny): wymagaj triad albo silną strukturę+div
    if bool(getattr(config, "REVERSAL_REQUIRE_QUALITY_TRIAD", False)):
        if triad_count < 2:
            return None
    if str(conf.get("status") or "").upper() != "CONFIRMED":
        return None
    if int(conf.get("confirms") or 0) < int(getattr(config, "REVERSAL_MIN_CONFIRMATIONS", 2) or 2):
        return None
    # A6: extreme+exhaust+1h stall już przeszły detect_* — nie zabijaj
    # kandydata tylko dlatego, że brak fib/div na tickerze bez OHLC.

    # Bonus gdy pełna triada (Divergence + Fib + Structure)
    if triad_count == 3:
        strength = min(0.95, strength + 0.06)
        scored["reversal_score"] = round(strength, 4)
    elif triad_count == 2:
        strength = min(0.95, strength + 0.03)
        scored["reversal_score"] = round(strength, 4)

    if strength < min_str:
        return None

    reasons = (
        ["ENGINE_REVERSAL"]
        + ([f"QUALITY_TRIAD({triad_count}/3)"] if triad_count else [])
        + (["QUALITY_SETUP"] if quality_setup else [])
        + list(extreme.get("tags") or [])
        + list(exhaustion.get("tags") or [])
        + list(conf.get("tags") or [])
        + [f"REV_CONFIRMS({conf.get('confirms', 0)})"]
        + [f"REV_SCORE({scored['reversal_score']:.2f})"]
        + [f"REV_COMP({k}={v:.2f})" for k, v in scored["components"].items() if abs(v) >= 0.04]
    )

    levels = _structural_sl_tp(coin, direction, price)
    # SL absurdalnie szeroki względem entry/Fib → NO TRADE
    if levels.get("sl_ok") is False:
        return None
    sl_price = levels["sl_price"]
    tp_price = levels["tp_price"]
    tp1_price = levels.get("tp1_price")
    tp2_price = levels.get("tp2_price")

    # --- Asymetria setupu (Fib/struktura TP vs SL) ---
    # Entry 100, SL 96, TP 103 → 0.75R → NO TRADE mimo ładnych wskaźników
    risk_abs = abs(price - float(sl_price)) if price and sl_price else 0.0
    min_tp1_r = float(getattr(config, "REVERSAL_MIN_TP1_R", 1.0) or 1.0)
    min_tp2_r = float(getattr(config, "REVERSAL_MIN_TP2_R", 1.5) or 1.5)
    min_reward_r = float(getattr(config, "REVERSAL_MIN_REWARD_R", 1.5) or 1.5)
    tp1_r = tp2_r = 0.0
    if risk_abs > 1e-12:
        if tp1_price is not None:
            tp1_r = abs(float(tp1_price) - price) / risk_abs
        if tp2_price is not None:
            tp2_r = abs(float(tp2_price) - price) / risk_abs
        # najbliższy sensowny TP (TP1) i główny cel (TP2)
        if tp1_r < min_tp1_r * 0.85:
            # 0.75R vs 1R wymagane → no trade
            return None
        best_r = max(tp1_r, tp2_r)
        if best_r < min_reward_r:
            return None
    else:
        return None

    _rev = round(float(scored["reversal_score"]), 4)
    try:
        from v2_profiles import profile_for
        instrument_profile = profile_for(sym, coin)
    except Exception:
        instrument_profile = "major" if sym in ("BTC", "ETH", "SOL") else "alt"
    return {
        "symbol": sym,
        "direction": direction,
        # PRIORYTET 5: osobny score — NIE trend_score
        "engine": "reversal",
        "score_type": "reversal",
        "reversal_score": _rev,
        "trend_score": None,
        "strength": _rev,  # alias do risk/UI (źródło = reversal_score)
        "v2_profile": instrument_profile,
        "reversal_profile": f"{direction.lower()}_{instrument_profile}",
        # Divergence + Fib + Structure (jakość > RSI alone)
        "quality_triad": triad_count,
        "quality_setup": quality_setup,
        "quality_flags": {
            "divergence": has_div,
            "fibonacci": has_fib,
            "structure": has_struct,
        },
        "price": price,
        "change_24h": round(_f(coin.get("change_24h"), 0) or 0, 2),
        "change_1h": round(_f(coin.get("change_1h"), 0) or 0, 2),
        "rs": round(_f(coin.get("residual_momentum_24h"), _f(coin.get("change_24h"), 0)) or 0, 2),
        "residual_momentum_24h": coin.get("residual_momentum_24h"),
        "benchmark_return_24h": coin.get("benchmark_return_24h"),
        "market_median_return_24h": coin.get("market_median_return_24h"),
        "rsi": _f(coin.get("rsi")),
        "rsi_signal": "oversold" if direction == "LONG" else "overbought",
        "macd_signal": coin.get("macd_signal") or "neutral",
        "reasons": reasons,
        "tp_price": tp_price,
        "tp1_price": tp1_price,
        "tp2_price": tp2_price,
        "tp_plan": levels.get("tp_plan"),
        "sl_price": sl_price,
        "rr_tp1": round(tp1_r, 3),
        "rr_tp2": round(tp2_r, 3),
        "reward_r": round(max(tp1_r, tp2_r), 3),
        "setup": "reversal_confirmed",
        "confirmation_status": str(conf.get("status") or "CONFIRMED"),
        "confirmation_count": int(conf.get("confirms") or 0),
        "confirmation_required": int(conf.get("min_confirms") or getattr(config, "REVERSAL_MIN_CONFIRMATIONS", 2)),
        "decision_path": "REVERSAL",
        "score_components": scored["components"],
        "fib_map": scored.get("fib_map") or coin.get("_fib_map"),
        # extreme | exhaustion | momentum | structure | fibonacci_zone | liquidity | cross | adverse
        "sl_method": levels.get("sl_method"),
        "structural_level": levels.get("structural_level"),
        "atr_buffer": levels.get("atr_buffer"),
        "sl_ok": levels.get("sl_ok", True),
        "sl_raw_dist_pct": levels.get("sl_raw_dist_pct"),
        "fib_ref_0786": levels.get("fib_ref_0786"),
        "reversal_stages": {
            "extreme": extreme,
            "exhaustion": exhaustion,
            "confirmation": conf,
        },
        "trend": coin.get("trend") or "SIDEWAYS",
        "trend_label_score": coin.get("trend_score"),
        "categories": coin.get("categories") or [],
        "sectors": coin.get("sectors") or [],
        "volume_24h": coin.get("volume_24h"),
        "vol_flag": coin.get("vol_flag"),
        "cipher_b": coin.get("cipher_b") if isinstance(coin.get("cipher_b"), dict) else None,
        "market_regime": regime,
        "order_book": coin.get("order_book"),
        "source_div": coin.get("source_div"),
        "reject_reason": None,
    }


LAST_REVERSAL_GEN_DIAG: Dict[str, Any] = {}


def _hydrate_1h_from_feeder(coin: Dict[str, Any], feeder) -> bool:
    """A6: ticker nie ma RSI/swing. Najpierw MarketStore (warmup), REST tylko fallback."""
    if coin is None:
        return False
    has_rsi = coin.get("rsi") is not None
    has_struct = bool(coin.get("highs") or coin.get("ohlcv_highs")) and bool(
        coin.get("lows") or coin.get("ohlcv_lows")
    )
    if has_rsi and has_struct:
        return False
    symbol = str(coin.get("symbol") or "")
    if not symbol:
        return False
    ohlcv = None
    try:
        from market_store import STORE
        ohlcv = STORE.get_ohlcv(symbol, "1H") or STORE.get_ohlcv(symbol, "1h")
    except Exception:
        ohlcv = None
    if not ohlcv and feeder is not None:
        feed = getattr(feeder, "blofin", None)
        if feed is not None and hasattr(feed, "fetch_klines_ohlcv"):
            try:
                ohlcv = feed.fetch_klines_ohlcv(symbol, bar="1H", limit=120) or {}
            except Exception:
                ohlcv = None
    if not ohlcv:
        return False
    highs = list(ohlcv.get("highs") or [])
    lows = list(ohlcv.get("lows") or [])
    changed = False
    if highs and not coin.get("highs"):
        coin["highs"] = highs
        changed = True
    if lows and not coin.get("lows"):
        coin["lows"] = lows
        changed = True
    if coin.get("rsi") is None or coin.get("ema_slow") is None:
        try:
            from indicators_full import compute_indicators
            ind = compute_indicators(ohlcv, tf="1h") or {}
        except Exception:
            ind = {}
        if coin.get("rsi") is None and ind.get("rsi") is not None:
            coin["rsi"] = ind.get("rsi")
            changed = True
        if coin.get("atr") is None and ind.get("atr") is not None:
            coin["atr"] = ind.get("atr")
        if coin.get("atr_pct") is None:
            coin["atr_pct"] = ind.get("atr_pct") or ind.get("atr_percent")
        if coin.get("ema_fast") is None and ind.get("ema_fast") is not None:
            coin["ema_fast"] = ind.get("ema_fast")
        if coin.get("ema_slow") is None and ind.get("ema_slow") is not None:
            coin["ema_slow"] = ind.get("ema_slow")
        macd = ind.get("macd") or {}
        if not coin.get("macd_signal") and isinstance(macd, dict):
            hist = macd.get("histogram") or macd.get("hist")
            if hist is not None:
                try:
                    coin["macd_signal"] = "bullish" if float(hist) > 0 else "bearish"
                    changed = True
                except (TypeError, ValueError):
                    pass
    return changed


def generate_reversal_signals(
    coins: List[Dict[str, Any]],
    regime: str = "UNKNOWN",
    btc_change_24h: float = 0.0,
    max_candidates: int = 15,
    feeder=None,
) -> List[Dict[str, Any]]:
    global LAST_REVERSAL_GEN_DIAG
    if not bool(getattr(config, "REVERSAL_ENGINE_ENABLED", True)):
        LAST_REVERSAL_GEN_DIAG = {
            "ok": False, "error": "REVERSAL_ENGINE_DISABLED",
            "coins_scanned": len(coins or []), "reversal_signals_generated": 0,
        }
        return []
    out: List[Dict[str, Any]] = []
    drops: Dict[str, int] = {
        "scanned": 0, "no_symbol": 0, "no_price": 0, "no_extreme": 0,
        "no_exhaustion": 0, "no_confirmation": 0, "weak_structure": 0,
        "exception": 0, "scored": 0, "hydrated_1h": 0,
    }
    max_hydrate = max(1, int(getattr(config, "REVERSAL_MAX_CANDIDATES", 12) or 12))
    for coin in coins:
        drops["scanned"] += 1
        try:
            if not (coin.get("symbol") or ""):
                drops["no_symbol"] += 1
                continue
            price = _f(coin.get("price") or coin.get("blofin_price"), 0) or 0
            if price <= 0:
                drops["no_price"] += 1
                continue
            extreme = detect_extreme(coin)
            if not extreme:
                drops["no_extreme"] += 1
                continue
            if feeder is not None and drops["hydrated_1h"] < max_hydrate:
                if _hydrate_1h_from_feeder(coin, feeder):
                    drops["hydrated_1h"] += 1
            exhaustion = detect_exhaustion(coin, extreme)
            if not exhaustion:
                drops["no_exhaustion"] += 1
                continue
            conf = detect_confirmation(coin, extreme, exhaustion, regime=regime)
            if not conf:
                drops["no_confirmation"] += 1
                continue
            sig = score_reversal_candidate(coin, regime=regime, btc_change_24h=btc_change_24h)
            if sig:
                try:
                    from strength_calibration import get_calibrator
                    get_calibrator().annotate(sig)
                except Exception:
                    sig["expected_r_status"] = "UNAVAILABLE"
                out.append(sig)
                drops["scored"] += 1
            else:
                drops["weak_structure"] += 1
        except Exception:
            drops["exception"] += 1
            continue
    out.sort(key=lambda x: float(x.get("strength") or 0), reverse=True)
    limit = int(getattr(config, "REVERSAL_MAX_CANDIDATES", max_candidates) or max_candidates)
    extreme_thr = float(getattr(config, "REVERSAL_MIN_24H_PCT", 12.0) or 12.0)
    LAST_REVERSAL_GEN_DIAG = {
        "ok": True, "error": None,
        "coins_scanned": drops["scanned"],
        "drops": drops,
        "extreme_threshold_pct": extreme_thr,
        "reversal_signals_generated": len(out),
        "regime": regime,
    }
    if not out:
        print(
            f"[Reversal] 0 sygnałów | scanned={drops['scanned']} "
            f"no_extreme={drops['no_extreme']} no_exh={drops['no_exhaustion']} "
            f"no_conf={drops['no_confirmation']} weak={drops['weak_structure']} "
            f"hydrated_1h={drops['hydrated_1h']} thr24h={extreme_thr}% regime={regime}"
        )
    return out[:limit]


def merge_trend_and_reversal(
    trend_signals: List[Dict[str, Any]],
    reversal_signals: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    by_sym: Dict[str, Dict[str, Any]] = {}

    def _put(s: Dict[str, Any]):
        sym = s.get("symbol")
        if not sym:
            return
        eng = s.get("engine") or "trend"
        s = dict(s)
        s["engine"] = eng
        try:
            from engine_router import route_signal
            s = route_signal(s)
        except Exception:
            pass
        if sym not in by_sym:
            by_sym[sym] = s
            return
        cur = by_sym[sym]
        regime = (s.get("market_regime") or cur.get("market_regime") or "").upper()
        if cur.get("direction") == s.get("direction"):
            pair = {str(cur.get("engine") or "trend"), str(eng)}
            if pair == {"trend", "reversal"}:
                rev = s if eng == "reversal" else cur
                trend = cur if eng == "reversal" else s
                rev_confirmed = rev.get("setup") == "reversal_confirmed" and rev.get("confirmation_status") == "CONFIRMED"
                if rev_confirmed and (trend.get("reject_reason") or regime in ("PANIC", "EXTREME", "RANGE")):
                    rev = dict(rev)
                    rev["engines"] = ["trend", "reversal"]
                    rev["conflict_engine"] = "trend"
                    rev["conflict_direction"] = trend.get("direction")
                    rev["reasons"] = list(rev.get("reasons") or []) + ["CONFIRMED_REVERSAL_OVERRIDES_TREND"]
                    by_sym[sym] = rev
                    return
            if float(s.get("strength") or 0) > float(cur.get("strength") or 0):
                s["engines"] = list({cur.get("engine"), eng})
                by_sym[sym] = s
            else:
                cur["engines"] = list({cur.get("engine"), eng})
            return
        prefer_rev = regime in ("PANIC", "EXTREME", "RANGE")
        # PUMP/DUMP chase: trend zablokowany → NIE kończymy analizy, preferujemy reversal
        if cur.get("trend_blocked") or s.get("trend_blocked"):
            prefer_rev = True
        if cur.get("pump_chase") or s.get("pump_chase") or cur.get("dump_chase") or s.get("dump_chase"):
            prefer_rev = True
        # reversal_watch musi zgadzać się z kierunkiem kandydata reversal
        watch = cur.get("reversal_watch") or s.get("reversal_watch")
        if eng == "reversal" and watch and s.get("direction") == watch:
            prefer_rev = True
        if cur.get("engine") == "reversal" and watch and cur.get("direction") == watch:
            prefer_rev = True

        s_str = float(s.get("strength") or 0) + float(s.get("engine_preference_score") or 0)
        c_str = float(cur.get("strength") or 0) + float(cur.get("engine_preference_score") or 0)
        # przy prefer_rev: reversal wygrywa nawet przy nieco niższej sile (do 0.12)
        if prefer_rev:
            if eng == "reversal" and s_str + 0.12 >= c_str:
                winner, loser = s, cur
            elif cur.get("engine") == "reversal" and c_str + 0.12 >= s_str:
                winner, loser = cur, s
            elif abs(s_str - c_str) < 0.05:
                winner = s if eng == "reversal" else cur
                loser = cur if winner is s else s
            else:
                winner = s if s_str > c_str else cur
                loser = cur if winner is s else s
        elif abs(s_str - c_str) < 0.05:
            winner = s if eng == "trend" else cur
            loser = cur if winner is s else s
        else:
            winner = s if s_str > c_str else cur
            loser = cur if winner is s else s

        winner = dict(winner)
        winner["conflict_engine"] = loser.get("engine")
        winner["conflict_direction"] = loser.get("direction")
        # przenieś flagi watch z trendu na zwycięzcę
        if loser.get("reversal_watch") and not winner.get("reversal_watch"):
            winner["reversal_watch"] = loser.get("reversal_watch")
        if loser.get("trend_blocked"):
            winner["trend_blocked"] = True
        # reversal NIE dziedziczy rejectu trendu (PUMP_CHASE ≠ NO TRADE na instrumencie)
        if winner.get("engine") == "reversal":
            rr = str(winner.get("reject_reason") or "")
            if rr.startswith("TREND_BLOCK_") or rr in ("TREND_BLOCK_PUMP_CHASE", "TREND_BLOCK_DUMP_CHASE"):
                winner["reject_reason"] = None
            winner["reasons"] = [
                r for r in list(winner.get("reasons") or [])
                if not str(r).startswith("TREND_") or "WATCH" in str(r)
            ]
            winner["reasons"] = list(winner.get("reasons") or []) + [
                f"ENGINE_PREFER_REVERSAL(vs {loser.get('engine')}:{loser.get('direction')})"
            ]
        else:
            winner["reasons"] = list(winner.get("reasons") or []) + [
                f"ENGINE_CONFLICT({loser.get('engine')}:{loser.get('direction')})"
            ]
        by_sym[sym] = winner

    for s in trend_signals:
        s = dict(s)
        if not s.get("engine"):
            s["engine"] = "trend"
        _put(s)
    for s in reversal_signals:
        _put(s)

    merged = list(by_sym.values())
    merged.sort(
        key=lambda x: (0 if x.get("direction") in ("LONG", "SHORT") else 1, float(x.get("strength") or 0)),
        reverse=True,
    )
    return merged
