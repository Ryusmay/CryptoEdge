# ============================================================
# Pelny zestaw wskaznikow: EMA, RSI, MACD, ATR, BB, ADX, SuperTrend
# ============================================================

from typing import List, Dict, Optional, Tuple
import config


def _ema(values: List[float], period: int) -> List[float]:
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    out = [sum(values[:period]) / period]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _sma(values: List[float], period: int) -> List[float]:
    if len(values) < period:
        return []
    out = []
    s = sum(values[:period])
    out.append(s / period)
    for i in range(period, len(values)):
        s += values[i] - values[i - period]
        out.append(s / period)
    return out


def _rsi(closes: List[float], period: int = 14) -> Optional[float]:
    series = _rsi_series(closes, period)
    return series[-1] if series and series[-1] is not None else None


def _rsi_series(closes: List[float], period: int = 14) -> List[float]:
    """RSI Wildera zgodny z typową implementacją giełdową/TradingView."""
    if len(closes) < period + 1:
        return [None] * len(closes)
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    out = [None] * period
    def value():
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0
        return round(100.0 - 100.0 / (1.0 + avg_gain / avg_loss), 2)
    out.append(value())
    for i in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out.append(value())
    return out


def _wavetrend(hlc3: List[float], channel_len: int = 9, avg_len: int = 12) -> Optional[Dict]:
    """
    WaveTrend (LazyBear) – rdzeń VuManChu Cipher B.
    WT1 = EMA(CI, avg_len), WT2 = SMA(WT1, 4)
    """
    if len(hlc3) < channel_len + avg_len + 5:
        return None
    esa = _ema(hlc3, channel_len)
    if not esa:
        return None
    # align
    offset = len(hlc3) - len(esa)
    h = hlc3[offset:]
    d_raw = [abs(h[i] - esa[i]) for i in range(len(esa))]
    d = _ema(d_raw, channel_len)
    if not d:
        return None
    off2 = len(esa) - len(d)
    esa2 = esa[off2:]
    h2 = h[off2:]
    ci = []
    for i in range(len(d)):
        den = 0.015 * d[i] if d[i] else 1e-10
        ci.append((h2[i] - esa2[i]) / den)
    wt1 = _ema(ci, avg_len)
    if not wt1 or len(wt1) < 5:
        return None
    wt2 = _sma(wt1, 4)
    if not wt2:
        return None
    # align wt1 to wt2
    wt1 = wt1[-len(wt2):]
    return {
        "wt1": wt1,
        "wt2": wt2,
        "wt1_last": wt1[-1],
        "wt2_last": wt2[-1],
    }


def _find_div_peaks(series: List[float], order: int = 3) -> List[int]:
    """Lokalne maksima (indeksy)."""
    peaks = []
    for i in range(order, len(series) - order):
        w = series[i - order: i + order + 1]
        if series[i] == max(w) and series[i] > series[i - 1]:
            peaks.append(i)
    return peaks


def _find_div_troughs(series: List[float], order: int = 3) -> List[int]:
    troughs = []
    for i in range(order, len(series) - order):
        w = series[i - order: i + order + 1]
        if series[i] == min(w) and series[i] < series[i - 1]:
            troughs.append(i)
    return troughs


def vumanchu_cipher_b(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    channel_len: int = 9,
    avg_len: int = 12,
    ob_level: float = 60.0,
    os_level: float = -60.0,
) -> Optional[Dict]:
    """
    Przybliżenie VuManChu Cipher B + dywergencje:
    - WaveTrend WT1/WT2
    - cross up/down
    - overbought/oversold
    - bullish/bearish divergence (cena vs WT1)
    """
    n = min(len(highs), len(lows), len(closes))
    if n < 40:
        return None
    highs, lows, closes = highs[-n:], lows[-n:], closes[-n:]
    hlc3 = [(highs[i] + lows[i] + closes[i]) / 3.0 for i in range(n)]
    wt = _wavetrend(hlc3, channel_len, avg_len)
    if not wt:
        return None
    wt1, wt2 = wt["wt1"], wt["wt2"]
    w1, w2 = wt1[-1], wt2[-1]
    prev1, prev2 = wt1[-2], wt2[-2]

    cross_up = prev1 <= prev2 and w1 > w2
    cross_down = prev1 >= prev2 and w1 < w2
    overbought = w1 >= ob_level
    oversold = w1 <= os_level

    # Dywergencje na ostatnich ~60 barach WT1 vs close (align)
    look = min(80, len(wt1), len(closes))
    wt_s = wt1[-look:]
    # closes aligned to wt length
    cl_s = closes[-len(wt1):][-look:]
    peaks_w = _find_div_peaks(wt_s, 3)
    troughs_w = _find_div_troughs(wt_s, 3)
    bull_div = False
    bear_div = False
    # bearish: price higher high, WT lower high
    if len(peaks_w) >= 2:
        i1, i2 = peaks_w[-2], peaks_w[-1]
        if cl_s[i2] > cl_s[i1] and wt_s[i2] < wt_s[i1] and i2 >= look - 8:
            bear_div = True
    # bullish: price lower low, WT higher low
    if len(troughs_w) >= 2:
        i1, i2 = troughs_w[-2], troughs_w[-1]
        if cl_s[i2] < cl_s[i1] and wt_s[i2] > wt_s[i1] and i2 >= look - 8:
            bull_div = True

    signal = "neutral"
    if bull_div or (cross_up and oversold):
        signal = "bullish"
    elif bear_div or (cross_down and overbought):
        signal = "bearish"
    elif cross_up:
        signal = "bullish_soft"
    elif cross_down:
        signal = "bearish_soft"

    return {
        "wt1": round(w1, 3),
        "wt2": round(w2, 3),
        "cross_up": cross_up,
        "cross_down": cross_down,
        "overbought": overbought,
        "oversold": oversold,
        "bull_div": bull_div,
        "bear_div": bear_div,
        "signal": signal,
        "name": "VuManChu_Cipher_B",
    }


def _macd(closes: List[float], fast=12, slow=26, signal=9) -> Optional[Dict]:
    if len(closes) < slow + signal:
        return None
    ef = _ema(closes, fast)
    es = _ema(closes, slow)
    n = min(len(ef), len(es))
    ef, es = ef[-n:], es[-n:]
    line = [a - b for a, b in zip(ef, es)]
    sig = _ema(line, signal)
    if not sig:
        return None
    line = line[-len(sig):]
    hist = [m - s for m, s in zip(line, sig)]
    hist_rising = len(hist) >= 3 and hist[-1] > hist[-2] > hist[-3]
    hist_falling = len(hist) >= 3 and hist[-1] < hist[-2] < hist[-3]
    cross = "none"
    if len(hist) >= 2:
        if hist[-2] <= 0 < hist[-1]:
            cross = "bullish"
        elif hist[-2] >= 0 > hist[-1]:
            cross = "bearish"
    return {
        "macd": round(line[-1], 8),
        "signal": round(sig[-1], 8),
        "hist": round(hist[-1], 8),
        "macd_above_signal": line[-1] > sig[-1],
        "hist_rising": hist_rising,
        "hist_falling": hist_falling,
        "cross": cross,
    }


def _atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Optional[float]:
    series = _atr_series(highs, lows, closes, period)
    return series[-1] if series and series[-1] is not None else None


def _atr_series(highs, lows, closes, period=14) -> List[float]:
    """ATR Wildera (RMA), współdzielony przez SL i SuperTrend."""
    if not closes:
        return []
    out = [None] * len(closes)
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)
        if len(trs) == period:
            out[i] = sum(trs) / period
        elif len(trs) > period:
            out[i] = (out[i - 1] * (period - 1) + tr) / period
    return out


def _choppiness_index(highs, lows, closes, period=14) -> Optional[float]:
    """CHOP = 100 * log10(sum(TR, n) / (HH(n)-LL(n))) / log10(n)."""
    import math
    n = min(len(highs), len(lows), len(closes))
    period = int(period)
    if period < 2 or n < period + 1:
        return None
    start = n - period
    tr_sum = 0.0
    for i in range(start, n):
        tr_sum += max(
            float(highs[i]) - float(lows[i]),
            abs(float(highs[i]) - float(closes[i - 1])),
            abs(float(lows[i]) - float(closes[i - 1])),
        )
    price_range = max(float(x) for x in highs[-period:]) - min(float(x) for x in lows[-period:])
    if tr_sum <= 0 or price_range <= 0:
        return 100.0
    value = 100.0 * math.log10(tr_sum / price_range) / math.log10(period)
    return round(max(0.0, min(100.0, value)), 2)


def _bollinger(closes: List[float], period: int = 20, mult: float = 2.0) -> Optional[Dict]:
    if len(closes) < period:
        return None
    window = closes[-period:]
    mid = sum(window) / period
    var = sum((x - mid) ** 2 for x in window) / period
    std = var ** 0.5
    upper = mid + mult * std
    lower = mid - mult * std
    price = closes[-1]
    return {
        "mid": mid,
        "upper": upper,
        "lower": lower,
        "above_upper": price > upper,
        "below_lower": price < lower,
        "extreme_above": price > upper + 0.25 * (upper - mid) if upper != mid else False,
        "extreme_below": price < lower - 0.25 * (mid - lower) if mid != lower else False,
        "near_lower": abs(price - lower) / price < 0.01 if price else False,
        "near_mid": abs(price - mid) / price < 0.008 if price else False,
    }


def _adx(highs, lows, closes, period=14) -> Optional[float]:
    n = len(closes)
    if n < period * 2:
        return None
    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0)
        minus_dm.append(down if down > up and down > 0 else 0)
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    if len(trs) < period:
        return None
    # Wilder smoothing dla TR i directional movement.
    def wilder(arr, p):
        if len(arr) < p:
            return []
        s = sum(arr[:p])
        out = [s]
        for i in range(p, len(arr)):
            s = s - s / p + arr[i]
            out.append(s)
        return out

    atr_w = wilder(trs, period)
    pdm_w = wilder(plus_dm, period)
    mdm_w = wilder(minus_dm, period)
    if not atr_w or not pdm_w:
        return None
    dx_list = []
    m = min(len(atr_w), len(pdm_w), len(mdm_w))
    for i in range(m):
        if atr_w[i] == 0:
            continue
        pdi = 100 * pdm_w[i] / atr_w[i]
        mdi = 100 * mdm_w[i] / atr_w[i]
        denom = pdi + mdi
        dx = 100 * abs(pdi - mdi) / denom if denom else 0
        dx_list.append(dx)
    if len(dx_list) < period:
        return None
    # Pierwszy ADX to średnia pierwszych `period` DX, kolejne używają RMA Wildera.
    adx = sum(dx_list[:period]) / period
    for dx in dx_list[period:]:
        adx = (adx * (period - 1) + dx) / period
    return round(adx, 2)


def _supertrend(highs, lows, closes, period=10, mult=3.0) -> Optional[Dict]:
    atr_s = _atr_series(highs, lows, closes, period)
    n = len(closes)
    if n < period + 2:
        return None
    final_upper, final_lower, direction = [None] * n, [None] * n, [None] * n
    first = next((i for i, value in enumerate(atr_s) if value is not None), None)
    if first is None:
        return None
    mid = (highs[first] + lows[first]) / 2.0
    final_upper[first] = mid + mult * atr_s[first]
    final_lower[first] = mid - mult * atr_s[first]
    direction[first] = 1 if closes[first] >= mid else -1
    for i in range(first + 1, n):
        mid = (highs[i] + lows[i]) / 2.0
        basic_upper = mid + mult * atr_s[i]
        basic_lower = mid - mult * atr_s[i]
        prev_upper, prev_lower = final_upper[i - 1], final_lower[i - 1]
        final_upper[i] = basic_upper if basic_upper < prev_upper or closes[i - 1] > prev_upper else prev_upper
        final_lower[i] = basic_lower if basic_lower > prev_lower or closes[i - 1] < prev_lower else prev_lower
        if direction[i - 1] == -1 and closes[i] > final_upper[i]:
            direction[i] = 1
        elif direction[i - 1] == 1 and closes[i] < final_lower[i]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]
    last_direction = direction[-1]
    line = final_lower[-1] if last_direction == 1 else final_upper[-1]
    return {
        "direction": "up" if last_direction == 1 else "down",
        "is_up": last_direction == 1,
        "value": line,
    }


def _classic_pivot_points(highs, lows, closes) -> Optional[Dict]:
    """Klasyczne pivot points z poprzedniej, zamkniętej świecy (bez look-ahead)."""
    if min(len(highs), len(lows), len(closes)) < 2:
        return None
    high, low, close = float(highs[-2]), float(lows[-2]), float(closes[-2])
    pivot = (high + low + close) / 3.0
    span = high - low
    return {
        "P": pivot,
        "R1": 2.0 * pivot - low,
        "R2": pivot + span,
        "R3": high + 2.0 * (pivot - low),
        "S1": 2.0 * pivot - high,
        "S2": pivot - span,
        "S3": low - 2.0 * (high - pivot),
        "source_bar": -2,
    }


def _confirmed_structure_levels(highs, lows, closes, volumes=None, lookback=365, right=14, tolerance_pct=0.05) -> Dict:
    """Potwierdzone swing S/R. Pivot jest dostępny dopiero po `right` świecach."""
    n = min(len(highs), len(lows), len(closes))
    left = right
    start = max(left, n - int(lookback))
    end = n - right
    pivots = []
    for i in range(start, end):
        h_window = highs[i - left:i + right + 1]
        l_window = lows[i - left:i + right + 1]
        vol = float(volumes[i]) if volumes and i < len(volumes) else 0.0
        if highs[i] == max(h_window) and highs[i] > highs[i - 1]:
            pivots.append({"kind": "resistance", "price": float(highs[i]), "index": i, "volume": vol})
        if lows[i] == min(l_window) and lows[i] < lows[i - 1]:
            pivots.append({"kind": "support", "price": float(lows[i]), "index": i, "volume": vol})

    tolerance = max(float(tolerance_pct), 0.0) / 100.0
    clusters = []
    for point in pivots:
        match = next((c for c in clusters if c["kind"] == point["kind"] and
                      abs(point["price"] - c["price"]) / max(c["price"], 1e-12) <= tolerance), None)
        if match:
            match["price"] = (match["price"] * match["touches"] + point["price"]) / (match["touches"] + 1)
            match["touches"] += 1
            match["last_index"] = max(match["last_index"], point["index"])
            match["volume"] += point["volume"]
        else:
            clusters.append({"kind": point["kind"], "price": point["price"], "touches": 1,
                             "last_index": point["index"], "volume": point["volume"]})
    price = float(closes[-1])
    for level in clusters:
        recency = level["last_index"] / max(n - 1, 1)
        level["score"] = round(level["touches"] + recency, 3)
    supports = sorted((x for x in clusters if x["price"] < price), key=lambda x: (-x["score"], price - x["price"]))[:3]
    resistances = sorted((x for x in clusters if x["price"] > price), key=lambda x: (-x["score"], x["price"] - price))[:3]
    return {"supports": supports, "resistances": resistances, "confirmed_pivots": len(pivots),
            "lookback": int(lookback), "right": int(right), "tolerance_pct": float(tolerance_pct)}


def _viper(opens, highs, lows, closes, volumes=None, lookback=365, bar_size=14,
           placement="right", chart_distance=90, source="ohlc4", noise=0.05) -> Dict:
    """Profil wolumenu po cenie. Estymuje koncentrację buy/sell z zamkniętych OHLCV."""
    n = min(len(highs), len(lows), len(closes))
    start = max(0, n - int(lookback))
    if opens and len(opens) >= n:
        source_values = [(float(opens[i]) + float(highs[i]) + float(lows[i]) + float(closes[i])) / 4.0 for i in range(start, n)]
        candle_opens = [float(opens[i]) for i in range(start, n)]
        source_exact = True
    else:
        source_values = [(float(closes[max(0, i - 1)]) + float(highs[i]) + float(lows[i]) + float(closes[i])) / 4.0 for i in range(start, n)]
        candle_opens = [float(closes[max(0, i - 1)]) for i in range(start, n)]
        source_exact = False
    if not source_values:
        return {"name": "Viper", "kind": "volume_profile", "levels": [], "ready": False}
    low_price, high_price = min(source_values), max(source_values)
    price_range = max(high_price - low_price, max(abs(high_price), 1.0) * 1e-8)
    bin_width = max(price_range * max(float(noise), 0.001), price_range / 200.0)
    bin_count = max(1, int(price_range / bin_width) + 1)
    bins = [{"buy_volume": 0.0, "sell_volume": 0.0, "samples": 0} for _ in range(bin_count)]
    window_volumes = list(volumes[start:n]) if volumes else [1.0] * len(source_values)
    window_volumes += [1.0] * max(0, len(source_values) - len(window_volumes))
    window_closes = [float(closes[i]) for i in range(start, n)]
    for value, open_price, close_price, volume in zip(source_values, candle_opens, window_closes, window_volumes):
        index = min(bin_count - 1, max(0, int((value - low_price) / bin_width)))
        volume = max(0.0, float(volume or 0.0))
        key = "buy_volume" if close_price >= open_price else "sell_volume"
        bins[index][key] += volume
        bins[index]["samples"] += 1
    levels = []
    for index, item in enumerate(bins):
        total = item["buy_volume"] + item["sell_volume"]
        if total <= 0:
            continue
        side = "buy" if item["buy_volume"] > item["sell_volume"] else "sell" if item["sell_volume"] > item["buy_volume"] else "balanced"
        levels.append({"price": low_price + (index + 0.5) * bin_width,
                       "buy_volume": item["buy_volume"], "sell_volume": item["sell_volume"],
                       "total_volume": total, "side": side, "samples": item["samples"]})
    max_volume = max((item["total_volume"] for item in levels), default=0.0)
    for item in levels:
        item["strength"] = item["total_volume"] / max_volume if max_volume else 0.0
    levels.sort(key=lambda item: item["price"], reverse=True)
    price = float(closes[-1])
    significant = [item for item in levels if item["strength"] >= 0.20]
    nearest_support = max((x["price"] for x in significant if x["price"] < price and x["side"] == "buy"), default=None)
    nearest_resistance = min((x["price"] for x in significant if x["price"] > price and x["side"] == "sell"), default=None)
    poc = max(levels, key=lambda item: item["total_volume"], default=None)
    return {
        "name": "Viper", "kind": "volume_profile", "candle_range": int(lookback),
        "bar_size": int(bar_size), "placement": placement, "chart_distance": int(chart_distance),
        "source": source, "source_exact": source_exact, "noise": float(noise),
        "interpretation": "historical_volume_profile_not_open_orders",
        "colors": {"price": "white", "sell": "maroon", "buy": "lime"},
        "levels": levels, "poc": poc, "nearest_support": nearest_support,
        "nearest_resistance": nearest_resistance, "ready": n >= int(lookback),
    }


TF_PARAMS = {
    "5m": {
        "ema_fast": 21, "ema_slow": 55,
        "rsi": 9, "macd": (6, 13, 5),
        "atr": 14, "st_atr": 7, "st_mult": 2.2,
        "bb": (20, 2.0), "adx": 14, "adx_th": 18.0,
        "sl_atr": 1.5, "tp1_r": 1.5, "tp2_r": 2.2,
    },
    "15m": {
        "ema_fast": 21, "ema_slow": 55,
        "rsi": 10, "macd": (8, 17, 9),
        "atr": 14, "st_atr": 7, "st_mult": 2.5,
        "bb": (20, 2.0), "adx": 14, "adx_th": 22.5,
        "sl_atr": 1.75, "tp1_r": 1.5, "tp2_r": 2.5,
    },
    "1h": {
        "ema_fast": 34, "ema_slow": 89,
        "rsi": 14, "macd": (12, 26, 9),
        "atr": 14, "st_atr": 10, "st_mult": 3.0,
        "bb": (20, 2.0), "adx": 14, "adx_th": 22.5,
        "sl_atr": 2.0, "tp1_r": 1.5, "tp2_r": 2.75,
    },
    "4h": {
        "ema_fast": 50, "ema_slow": 200,
        "rsi": 14, "macd": (12, 26, 9),
        "atr": 14, "st_atr": 10, "st_mult": 3.0,
        "bb": (20, 2.0), "adx": 14, "adx_th": 25.0,
        "sl_atr": 2.2, "tp1_r": 1.5, "tp2_r": 3.0,
    },
    "1d": {
        "ema_fast": 50, "ema_slow": 200,
        "rsi": 14, "macd": (12, 26, 9),
        "atr": 14, "st_atr": 14, "st_mult": 3.0,
        "bb": (20, 2.1), "adx": 14, "adx_th": 25.0,
        "sl_atr": 2.3, "tp1_r": 1.5, "tp2_r": 3.0,
    },
}


def compute_indicators(ohlcv: Dict, tf: str = "1h") -> Optional[Dict]:
    """
    ohlcv: {closes, highs, lows, volumes}
    """
    p = dict(TF_PARAMS.get(tf, TF_PARAMS["1h"]))
    # ATR_SL_MULTIPLIER z configu skaluje bazowy SL (referencja = 2.0 dla 1h)
    try:
        base = float(getattr(config, "ATR_SL_MULTIPLIER", 2.0) or 2.0)
        # zachowaj proporcje TF wzgledem 1h (sl_atr 2.0)
        rel = float(p.get("sl_atr") or 2.0) / 2.0
        p["sl_atr"] = round(base * rel, 3)
    except Exception:
        pass

    closes = ohlcv.get("closes") or []
    opens = ohlcv.get("opens") or []
    highs = ohlcv.get("highs") or closes
    lows = ohlcv.get("lows") or closes
    volumes = ohlcv.get("volumes") or []

    need = max(p["ema_slow"] + 5, 60)
    if len(closes) < need:
        return None

    ema_f = _ema(closes, p["ema_fast"])
    ema_s = _ema(closes, p["ema_slow"])
    if not ema_f or not ema_s:
        return None

    rsi = _rsi(closes, p["rsi"])
    rsi_prev = _rsi(closes[:-1], p["rsi"]) if len(closes) > p["rsi"] + 2 else None
    macd = _macd(closes, *p["macd"])
    atr = _atr(highs, lows, closes, p["atr"])
    atr_hist = []
    for i in range(max(0, len(closes) - 50), len(closes)):
        a = _atr(highs[: i + 1], lows[: i + 1], closes[: i + 1], p["atr"])
        if a:
            atr_hist.append(a)
    bb = _bollinger(closes, p["bb"][0], p["bb"][1])
    adx = _adx(highs, lows, closes, p["adx"])
    st = _supertrend(highs, lows, closes, p["st_atr"], p["st_mult"])
    chop = _choppiness_index(highs, lows, closes, 14)
    pivot_points = _classic_pivot_points(highs, lows, closes)
    support_resistance = _confirmed_structure_levels(highs, lows, closes, volumes, 365, 14, 0.05)
    viper = {}  # Viper usuniety z decyzji i z liczenia poziomow

    vol_ma = None
    vol_ok = False
    if len(volumes) >= 20:
        vol_ma = sum(volumes[-20:]) / 20
        vol_ok = volumes[-1] > vol_ma

    atr_dead = False
    if atr and len(atr_hist) >= 20:
        sorted_a = sorted(atr_hist)
        p20 = sorted_a[max(0, int(len(sorted_a) * 0.2) - 1)]
        atr_dead = atr <= p20

    rsi_rising = rsi is not None and rsi_prev is not None and rsi > rsi_prev
    rsi_falling = rsi is not None and rsi_prev is not None and rsi < rsi_prev
    rsi_cross_up_35 = rsi is not None and rsi_prev is not None and rsi_prev < 35 <= rsi
    rsi_cross_down_65 = rsi is not None and rsi_prev is not None and rsi_prev > 65 >= rsi

    cipher = vumanchu_cipher_b(highs, lows, closes)

    return {
        "tf": tf,
        "price": closes[-1],
        "ema_fast": ema_f[-1],
        "ema_slow": ema_s[-1],
        "ema_fast_above_slow": ema_f[-1] > ema_s[-1],
        "price_above_ema_slow": closes[-1] > ema_s[-1],
        "rsi": rsi,
        "rsi_rising": rsi_rising,
        "rsi_falling": rsi_falling,
        "rsi_cross_up_35": rsi_cross_up_35,
        "rsi_cross_down_65": rsi_cross_down_65,
        "macd": macd,
        "atr": atr,
        "atr_dead": atr_dead,
        "bb": bb,
        "adx": adx,
        "adx_ok": adx is not None and adx > p["adx_th"],
        "adx_threshold": p["adx_th"],
        "supertrend": st,
        "choppiness": chop,
        "choppiness_state": (
            "trending" if chop is not None and chop < 38.2 else
            "choppy" if chop is not None and chop > 61.8 else
            "transition" if chop is not None else "unknown"
        ),
        "vol_ok": vol_ok,
        "vol_ma": vol_ma,
        "cipher_b": cipher,
        "pivot_points": pivot_points,
        "support_resistance": support_resistance,
        "viper": viper,
        "params": p,
    }


def evaluate_entry(ind: Dict) -> Dict:
    """Reguly Long/Short z checklisty. Zwraca direction + reasons + levels."""
    if not ind:
        return {"direction": "NEUTRAL", "pass": False, "reasons": [], "score": 0}

    reasons = []
    long_ok = True
    short_ok = True

    # Trend filter
    if not (ind["price_above_ema_slow"] and ind["ema_fast_above_slow"]):
        long_ok = False
    else:
        reasons.append("EMA_TREND_UP")
    st = ind.get("supertrend") or {}
    if not st.get("is_up"):
        long_ok = False
    else:
        reasons.append("ST_UP")

    # Short: kierunek dokladnie odwrotny do long (EMA ponizej + ST w dol).
    short_trend = (not ind["price_above_ema_slow"]) and (not ind["ema_fast_above_slow"]) and not st.get("is_up", True)
    if short_trend:
        reasons.append("EMA_TREND_DOWN")
        reasons.append("ST_DOWN")
    else:
        short_ok = False

    # ADX
    if not ind.get("adx_ok"):
        long_ok = False
        short_ok = False
    else:
        reasons.append(f"ADX>{ind['adx_threshold']}({ind.get('adx')})")

    # RSI momentum – szersze okno; rising/falling opcjonalne gdy trend+ADX już OK
    rsi = ind.get("rsi")
    long_rsi = ind.get("rsi_cross_up_35") or (
        rsi is not None and 40 <= rsi <= 65 and ind.get("rsi_rising")
    )
    # soft: RSI w trend-zone bez rising (gdy EMA+ST już long_ok dotychczas)
    if not long_rsi and rsi is not None and 35 <= rsi <= 68 and long_ok:
        long_rsi = True
        reasons.append("RSI_LONG_SOFT")
    short_rsi = ind.get("rsi_cross_down_65") or (
        rsi is not None and 35 <= rsi <= 60 and ind.get("rsi_falling")
    )
    if not short_rsi and rsi is not None and 32 <= rsi <= 65 and short_ok:
        short_rsi = True
        reasons.append("RSI_SHORT_SOFT")
    if not long_rsi:
        long_ok = False
    else:
        if "RSI_LONG_SOFT" not in reasons:
            reasons.append("RSI_LONG_OK")
    if not short_rsi:
        short_ok = False
    else:
        if "RSI_SHORT_SOFT" not in reasons:
            reasons.append("RSI_SHORT_OK")

    # MACD
    macd = ind.get("macd") or {}
    long_macd = macd.get("macd_above_signal") or macd.get("hist_rising")
    short_macd = (not macd.get("macd_above_signal", True)) or macd.get("hist_falling")
    if not long_macd:
        long_ok = False
    else:
        reasons.append("MACD_LONG_OK")
    if not short_macd:
        short_ok = False
    else:
        reasons.append("MACD_SHORT_OK")

    # BB extremes
    bb = ind.get("bb") or {}
    if bb.get("extreme_above"):
        long_ok = False
        reasons.append("BB_EXTREME_HIGH")
    if bb.get("extreme_below"):
        short_ok = False
        reasons.append("BB_EXTREME_LOW")

    # ATR dead market
    if ind.get("atr_dead"):
        long_ok = False
        short_ok = False
        reasons.append("ATR_DEAD")

    # Volume – niski VOL to kara, nie twardy kill (za często blokował 4H)
    if not ind.get("vol_ok"):
        if ind.get("vol_ma") is not None:
            reasons.append("VOL_LOW")
            # nie wyłączaj long_ok/short_ok – tylko soft flag
        else:
            reasons.append("VOL_NA")
    else:
        reasons.append("VOL_OK")

    # Bonus
    if bb.get("near_lower") or bb.get("near_mid"):
        reasons.append("BB_BOUNCE_ZONE")

    price = ind["price"]
    atr = ind.get("atr") or 0
    p = ind["params"]
    levels = {}
    direction = "NEUTRAL"
    score = 0.0

    # VuManChu Cipher B – soft confirm (nie twarde kill)
    cipher = ind.get("cipher_b") or {}
    if cipher:
        if cipher.get("bull_div"):
            reasons.append("CIPHER_BULL_DIV")
        if cipher.get("bear_div"):
            reasons.append("CIPHER_BEAR_DIV")
        if cipher.get("cross_up"):
            reasons.append("CIPHER_CROSS_UP")
        if cipher.get("cross_down"):
            reasons.append("CIPHER_CROSS_DOWN")
        if cipher.get("oversold"):
            reasons.append("CIPHER_OS")
        if cipher.get("overbought"):
            reasons.append("CIPHER_OB")
        # lekka kara za konflikt z Cipher
        if long_ok and cipher.get("signal") in ("bearish", "bearish_soft") and cipher.get("bear_div"):
            reasons.append("CIPHER_VS_LONG")
        if short_ok and cipher.get("signal") in ("bullish", "bullish_soft") and cipher.get("bull_div"):
            reasons.append("CIPHER_VS_SHORT")

    if long_ok and not short_ok:
        direction = "LONG"
        score = 0.85
        if cipher.get("bull_div") or (cipher.get("cross_up") and cipher.get("oversold")):
            score = min(0.95, score + 0.06)
            reasons.append("CIPHER_LONG_CONFIRM")
        elif cipher.get("bear_div"):
            score = max(0.55, score - 0.08)
        # ATR sanity: max ~12% ceny
        if price > 0 and atr / price > 0.12:
            atr = price * 0.12
        sl = price - p["sl_atr"] * atr
        # hard cap dystansu SL 10%
        sl = max(sl, price * 0.90)
        risk = price - sl
        levels = {
            "sl": sl,
            "tp1": price + p["tp1_r"] * risk,
            "tp2": price + p["tp2_r"] * risk,
            "atr": atr,
            "r_multiple_sl": p["sl_atr"],
        }
    elif short_ok and not long_ok:
        direction = "SHORT"
        score = 0.85
        if cipher.get("bear_div") or (cipher.get("cross_down") and cipher.get("overbought")):
            score = min(0.95, score + 0.06)
            reasons.append("CIPHER_SHORT_CONFIRM")
        elif cipher.get("bull_div"):
            score = max(0.55, score - 0.08)
        if price > 0 and atr / price > 0.12:
            atr = price * 0.12
        sl = price + p["sl_atr"] * atr
        sl = min(sl, price * 1.10)
        risk = sl - price
        levels = {
            "sl": sl,
            "tp1": price - p["tp1_r"] * risk,
            "tp2": price - p["tp2_r"] * risk,
            "atr": atr,
            "r_multiple_sl": p["sl_atr"],
        }
    # Uwaga: nie ma tu galezi "long_ok and short_ok -> CONFLICT". Bramka
    # trendu wyzej (EMA_TREND_UP wymaga price>ema_slow i fast>slow; short_trend
    # wymaga obu odwrotnie) jest wzajemnie wykluczajaca, wiec long_ok i
    # short_ok nie moga byc jednoczesnie True - taka galaz byla nieosiagalnym
    # martwym kodem.

    return {
        "direction": direction,
        "pass": direction in ("LONG", "SHORT"),
        "reasons": reasons,
        "score": score,
        "levels": levels,
        "tf": ind.get("tf"),
        "rsi": rsi,
        "adx": ind.get("adx"),
        "supertrend": st.get("direction"),
        "cipher_b": cipher,
    }
