# ============================================================
# 10–11. Regime model + hysteresis
# ADX, ATR percentile, EMA slope, realized vol, breadth,
# BTC trend, cross-sectional dispersion, funding
# ============================================================

from __future__ import annotations
import math
import time
from typing import Dict, List, Optional, Any, Deque
from collections import deque


def _ema(series: List[float], period: int) -> Optional[float]:
    if not series or len(series) < period:
        return None
    k = 2.0 / (period + 1)
    e = series[0]
    for x in series[1:]:
        e = x * k + e * (1 - k)
    return e


def _atr_list(highs, lows, closes, period=14) -> List[float]:
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    if len(trs) < period:
        return []
    out = []
    atr = sum(trs[:period]) / period
    out.append(atr)
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
        out.append(atr)
    return out


def _adx(highs, lows, closes, period=14) -> Optional[float]:
    n = len(closes)
    if n < period + 2:
        return None
    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    if len(trs) < period:
        return None

    def _wild(vals):
        s = sum(vals[:period])
        out = [s]
        for v in vals[period:]:
            s = s - s / period + v
            out.append(s)
        return out

    atr_s = _wild(trs)
    p_s = _wild(plus_dm)
    m_s = _wild(minus_dm)
    dx = []
    for a, p, m in zip(atr_s, p_s, m_s):
        if a <= 0:
            dx.append(0.0)
            continue
        pdi = 100.0 * p / a
        mdi = 100.0 * m / a
        denom = pdi + mdi
        dx.append(0.0 if denom <= 0 else abs(pdi - mdi) / denom * 100.0)
    if len(dx) < period:
        return None
    adx = sum(dx[:period]) / period
    for v in dx[period:]:
        adx = (adx * (period - 1) + v) / period
    return adx


def _realized_vol(closes: List[float], window: int = 24) -> Optional[float]:
    """Annualized-ish daily vol from log returns of last `window` bars (1h → ~sqrt(24))."""
    if len(closes) < window + 1:
        return None
    rets = []
    for i in range(-window, 0):
        a, b = closes[i - 1], closes[i]
        if a > 0 and b > 0:
            rets.append(math.log(b / a))
    if len(rets) < 5:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return math.sqrt(var) * math.sqrt(24) * 100.0  # % per day-ish


def _ema_slope(closes: List[float], period: int = 50, lookback: int = 6) -> Optional[float]:
    """Relative slope of EMA over lookback bars (% of price)."""
    if len(closes) < period + lookback:
        return None
    e_now = _ema(closes, period)
    e_prev = _ema(closes[:-lookback], period)
    if e_now is None or e_prev is None or e_prev == 0:
        return None
    return (e_now - e_prev) / e_prev * 100.0


def _percentile(value: float, history: List[float]) -> Optional[float]:
    if not history:
        return None
    below = sum(1 for x in history if x <= value)
    return below / len(history) * 100.0


def cross_sectional_dispersion(changes_24h: List[float]) -> Optional[float]:
    """Std of cross-section 24h changes (%)."""
    vals = [float(x) for x in changes_24h if x is not None]
    if len(vals) < 10:
        return None
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    return math.sqrt(var)


def market_breadth(changes_24h: List[float], thr: float = 0.5) -> Dict[str, float]:
    vals = [float(x) for x in changes_24h if x is not None]
    if not vals:
        return {"up_pct": 50.0, "down_pct": 50.0, "ratio": 1.0, "n": 0}
    up = sum(1 for v in vals if v > thr)
    down = sum(1 for v in vals if v < -thr)
    n = len(vals)
    return {
        "up_pct": up / n * 100.0,
        "down_pct": down / n * 100.0,
        "ratio": (up / down) if down > 0 else float(up),
        "n": n,
    }


class RegimeEngine:
    """
    Buduje bogaty opis reżimu + hysteresis (N consecutive confirmations).
    """

    def __init__(self, confirmations: int = 3):
        self.confirmations = max(1, int(confirmations))
        self._raw_hist: Deque[str] = deque(maxlen=20)
        self._stable: str = "UNKNOWN"
        self._pending: Optional[str] = None
        self._pending_count: int = 0
        self._atr_hist: Deque[float] = deque(maxlen=200)
        self.last_detail: Dict[str, Any] = {}

    def compute_from_ohlcv(
        self,
        ohlcv: dict,
        btc_change_24h: float = 0.0,
        alt_changes_24h: Optional[List[float]] = None,
        avg_funding: Optional[float] = None,
    ) -> Dict[str, Any]:
        closes = list(ohlcv.get("closes") or [])
        highs = list(ohlcv.get("highs") or [])
        lows = list(ohlcv.get("lows") or [])
        detail: Dict[str, Any] = {}
        btc = float(btc_change_24h or 0)

        import config
        atr_period = max(2, int(getattr(config, "REGIME_ATR_PERIOD", 14) or 14))
        adx = _adx(highs, lows, closes, atr_period) if len(closes) >= atr_period * 2 else None
        atrs = _atr_list(highs, lows, closes, atr_period) if len(closes) >= atr_period * 2 else []
        atr = atrs[-1] if atrs else None
        atr_ratio = None
        atr_pctile = None
        if atr is not None and atrs:
            ma_n = min(max(2, int(getattr(config, "REGIME_ATR_MA", 50) or 50)), len(atrs))
            atr_ma = sum(atrs[-ma_n:]) / ma_n
            atr_ratio = atr / atr_ma if atr_ma > 0 else 1.0
            # Percentyl liczony z historycznych ATR świec, nie z wartości
            # dopisywanej co kilka sekund dla tej samej niezakończonej świecy.
            # Poprzednia wersja szybko przyklejała wynik do 100.
            reference = list(atrs[:-1])
            min_samples = int(getattr(__import__("config"), "REGIME_ATR_PERCENTILE_MIN_SAMPLES", 30) or 30)
            if len(reference) >= min_samples:
                atr_pctile = _percentile(atr, reference)
            self._atr_hist.append(atr)  # zachowane wyłącznie dla diagnostyki zgodności

        ema_slope = _ema_slope(closes, 50, 6)
        rvol = _realized_vol(closes, 24)

        dispersion = cross_sectional_dispersion(alt_changes_24h or [])
        breadth = market_breadth(alt_changes_24h or [])

        # BTC trend label
        if btc >= 2.0:
            btc_trend = "UP"
        elif btc <= -2.0:
            btc_trend = "DOWN"
        else:
            btc_trend = "FLAT"

        # Funding regime hint
        fund_label = "NEUTRAL"
        if avg_funding is not None:
            if avg_funding > 0.0003:
                fund_label = "LONG_CROWDED"
            elif avg_funding < -0.0003:
                fund_label = "SHORT_CROWDED"

        detail.update({
            "adx": round(adx, 2) if adx is not None else None,
            "atr": atr,
            "atr_ratio": round(atr_ratio, 3) if atr_ratio is not None else None,
            "atr_percentile": round(atr_pctile, 1) if atr_pctile is not None else None,
            "ema_slope": round(ema_slope, 3) if ema_slope is not None else None,
            "realized_vol": round(rvol, 3) if rvol is not None else None,
            "dispersion": round(dispersion, 3) if dispersion is not None else None,
            "breadth": breadth,
            "btc_24h": btc,
            "btc_trend": btc_trend,
            "avg_funding": avg_funding,
            "funding_label": fund_label,
        })

        raw = self._classify(detail)
        stable = self._apply_hysteresis(raw)
        detail["raw_regime"] = raw
        detail["regime"] = stable
        detail["hysteresis"] = {
            "confirmations_needed": self.confirmations,
            "pending": self._pending,
            "pending_count": self._pending_count,
            "stable": self._stable,
        }
        self.last_detail = detail
        return {"regime": stable, "raw_regime": raw, **detail}

    def _classify(self, d: Dict[str, Any]) -> str:
        """
        Priorytet: PANIC > TREND > RANGE.
        """
        import config
        atr_ratio = d.get("atr_ratio") or 1.0
        atr_pctile = d.get("atr_percentile")
        adx = d.get("adx")
        btc = float(d.get("btc_24h") or 0)
        slope = d.get("ema_slope")
        rvol = d.get("realized_vol")
        dispersion = d.get("dispersion")
        breadth = d.get("breadth") or {}

        panic_mult = float(getattr(config, "REGIME_PANIC_ATR_MULT", 1.8))
        range_max = float(getattr(config, "REGIME_RANGE_BTC_MAX", 1.2))
        adx_trend = float(getattr(config, "REGIME_ADX_TREND", 22.0))
        adx_range = float(getattr(config, "REGIME_ADX_RANGE", 18.0))

        # PANIC: extreme vol. Zachowaj dokladna przyczyne dla UI/telemetrii.
        panic_rvol = float(getattr(config, "REGIME_PANIC_RVOL", 8.0))
        panic_triggers = []
        if atr_ratio >= panic_mult:
            panic_triggers.append(f"ATR_RATIO({atr_ratio:.3f}>={panic_mult:.3f})")
        percentile_ratio_min = float(getattr(config, "REGIME_PANIC_PERCENTILE_MIN_ATR_RATIO", 1.50))
        percentile_rvol_min = float(getattr(config, "REGIME_PANIC_PERCENTILE_MIN_RVOL", 2.00))
        percentile_confirmed = (
            atr_pctile is not None and atr_pctile >= 95
            and atr_ratio >= percentile_ratio_min
            and rvol is not None and rvol >= percentile_rvol_min
        )
        if percentile_confirmed:
            panic_triggers.append(
                f"ATR_PERCENTILE_CONFIRMED({atr_pctile:.1f}>=95;"
                f"ratio={atr_ratio:.3f}>={percentile_ratio_min:.3f};"
                f"rvol={rvol:.3f}>={percentile_rvol_min:.3f})"
            )
        elif atr_pctile is not None and atr_pctile >= 95:
            d["panic_percentile_unconfirmed"] = (
                f"ATR_PERCENTILE_UNCONFIRMED({atr_pctile:.1f};"
                f"ratio={atr_ratio:.3f};rvol={rvol})"
            )
        if rvol is not None and rvol >= panic_rvol:
            panic_triggers.append(f"REALIZED_VOL({rvol:.3f}>={panic_rvol:.3f})")
        d["panic_triggers"] = panic_triggers
        d["panic_trigger"] = "|".join(panic_triggers)
        if panic_triggers:
            return "PANIC"

        trend_votes = 0
        # BTC move
        if btc >= range_max:
            trend_votes += 1
        elif btc <= -range_max:
            trend_votes -= 1
        # EMA slope
        if slope is not None:
            if slope >= 0.4:
                trend_votes += 1
            elif slope <= -0.4:
                trend_votes -= 1
        # ADX strength
        if adx is not None and adx >= adx_trend:
            if trend_votes > 0:
                trend_votes += 1
            elif trend_votes < 0:
                trend_votes -= 1
            elif btc > 0:
                trend_votes += 1
            elif btc < 0:
                trend_votes -= 1
        # Breadth confirmation
        up = breadth.get("up_pct") or 50
        down = breadth.get("down_pct") or 50
        if up >= 60 and trend_votes > 0:
            trend_votes += 1
        if down >= 60 and trend_votes < 0:
            trend_votes -= 1
        # High dispersion often = trend/selective
        if dispersion is not None and dispersion >= float(getattr(config, "REGIME_DISP_TREND", 6.0)):
            if trend_votes != 0:
                trend_votes += 1 if trend_votes > 0 else -1

        if trend_votes >= 2:
            return "TREND_UP"
        if trend_votes <= -2:
            return "TREND_DOWN"

        # RANGE: low ADX, flat BTC, moderate ATR
        if (adx is not None and adx < adx_range) or (abs(btc) < range_max and atr_ratio < 1.15):
            return "RANGE"
        if abs(btc) < range_max:
            return "RANGE"
        return "TREND_UP" if btc > 0 else "TREND_DOWN"

    def _apply_hysteresis(self, raw: str) -> str:
        """Wymaga N kolejnych takich samych raw zanim zmieni stable."""
        self._raw_hist.append(raw)
        if self._stable == "UNKNOWN":
            self._stable = raw
            self._pending = None
            self._pending_count = 0
            return self._stable
        if raw == self._stable:
            self._pending = None
            self._pending_count = 0
            return self._stable
        if raw == self._pending:
            self._pending_count += 1
        else:
            self._pending = raw
            self._pending_count = 1
        if self._pending_count >= self.confirmations:
            self._stable = raw
            self._pending = None
            self._pending_count = 0
        return self._stable


# singleton for bot
_ENGINE: Optional[RegimeEngine] = None


def get_regime_engine() -> RegimeEngine:
    global _ENGINE
    if _ENGINE is None:
        try:
            import config
            n = int(getattr(config, "REGIME_HYSTERESIS", 3))
        except Exception:
            n = 3
        _ENGINE = RegimeEngine(confirmations=n)
    return _ENGINE
