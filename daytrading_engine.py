"""Independent intraday engine: 4H/1H bias, 15m setup, 5m timing.

The engine consumes BloFin closed candles only. Binance remains an external
sanity check elsewhere; it never becomes the strategy-price fallback.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional

import config
from expected_net_r import expected_net_r
from blofin_ws import PUBLIC_WS
try:
    from indicators_full import compute_indicators
except ImportError:  # pragma: no cover - pełny pakiet ma indicators_full
    def compute_indicators(ohlcv, tf="1h"):
        return {}


STABLES = {"USDT", "USDC", "DAI", "USDE", "USD1", "BFUSD", "TUSD", "FDUSD",
           "BUSD", "USDD", "GUSD", "USDP", "FRAX", "LUSD", "PYUSD", "EURC",
           "EUROC", "XAUT", "PAXG", "USTC"}


def _sigmoid(x: float) -> float:
    x = max(-20.0, min(20.0, float(x)))
    return 1.0 / (1.0 + math.exp(-x))


def _quality_and_size(features: dict, expected_r: float = 0.0) -> dict:
    """Trzy liczby: quality (0-1), expected_net_r, size_mult.

    quality = sigmoid(suma wag). 0.50 = brak opinii, nie próg wejścia.
    size_mult skaluje ryzyko od expected R, nie od quality.
    """
    logit = 0.0
    parts = {}
    def add(name, value):
        nonlocal logit
        w = float(value or 0.0)
        parts[name] = round(w, 4)
        logit += w

    add("adx", features.get("adx", 0.0))
    add("st_15m", features.get("st_15m", 0.0))
    add("macd_cross", features.get("macd_cross", 0.0))
    add("chop_clean", features.get("chop_clean", 0.0))
    add("barrier", features.get("barrier", 0.0))
    add("fib", features.get("fib", 0.0))
    add("htf_partial", features.get("htf_partial", 0.0))
    add("bb_extreme", features.get("bb_extreme", 0.0))
    quality = _sigmoid(logit)
    target = _finite(getattr(config, "DAYTRADING_SIZE_R_TARGET", 0.40), 0.40)
    size_mult = 1.0
    if target > 0:
        size_mult = max(0.25, min(1.50, _finite(expected_r) / target))
    return {
        "quality": round(quality, 4),
        "logit": round(logit, 4),
        "parts": parts,
        "size_mult": round(size_mult, 4),
    }


def _finite(value, default=0.0) -> float:
    try:
        out = float(value)
        return out if out == out and abs(out) != float("inf") else default
    except (TypeError, ValueError):
        return default


class DayTradingEngine:
    def __init__(self, feeder=None):
        self.feeder = feeder

    def _fetch(self, symbol: str, tf: str) -> dict:
        feed = getattr(self.feeder, "blofin", None)
        if feed is None:
            return {}
        bar = {"5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H"}[tf]
        # 15m needs extra history for structure/pivots (Viper is not used).
        limit = {"5m": 180, "15m": 400, "1h": 180, "4h": 240}[tf]
        return feed.fetch_klines_ohlcv(symbol, bar=bar, limit=limit) or {}

    @staticmethod
    def _levels(ind: dict, price: float, atr: float = 0.0) -> tuple:
        """Struktura 15m + klasyczne pivoty. Viper nie wchodzi do decyzji."""
        supports, resistances = [], []
        structure = ind.get("support_resistance") or {}
        supports.extend(_finite(x.get("price")) for x in structure.get("supports") or [])
        resistances.extend(_finite(x.get("price")) for x in structure.get("resistances") or [])
        pivots = ind.get("pivot_points") or {}
        supports.extend(_finite(v) for k, v in pivots.items() if k.startswith("S"))
        resistances.extend(_finite(v) for k, v in pivots.items() if k.startswith("R"))
        if bool(getattr(config, "DAYTRADING_USE_VIPER_LEVELS", False)):
            viper = ind.get("viper") or {}
            supports.append(_finite(viper.get("nearest_support")))
            resistances.append(_finite(viper.get("nearest_resistance")))
        ignore = max(
            price * 1e-8,
            _finite(atr) * _finite(getattr(config, "DAYTRADING_BARRIER_IGNORE_ATR", 0.25), 0.25),
        )
        supports = sorted({x for x in supports if 0 < x <= price - ignore}, reverse=True)
        resistances = sorted({x for x in resistances if x >= price + ignore})
        return supports, resistances

    @staticmethod
    def _fib_levels(raw: dict, bias: str) -> List[float]:
        """Recent-range Fibonacci map; confluence only, never a direction signal."""
        highs = list(raw.get("highs") or [])[-96:]
        lows = list(raw.get("lows") or [])[-96:]
        if not highs or not lows:
            return []
        low_values, high_values = list(map(float, lows)), list(map(float, highs))
        lo, hi = min(low_values), max(high_values)
        lo_i, hi_i = low_values.index(lo), high_values.index(hi)
        # A Fibonacci map requires an ordered impulse, not merely two extrema
        # somewhere in an arbitrary rolling range.
        if (bias == "LONG" and lo_i >= hi_i) or (bias == "SHORT" and hi_i >= lo_i):
            return []
        span = hi - lo
        if span <= 0:
            return []
        return [lo + span * ratio for ratio in (0.236, 0.382, 0.5, 0.618, 0.786)]

    @staticmethod
    def _fib_snapshot(raw: dict, bias: str, price: float) -> dict:
        """UI/audit map from an ordered impulse; confluence only, never a signal."""
        highs = list(map(float, (raw.get("highs") or [])[-96:]))
        lows = list(map(float, (raw.get("lows") or [])[-96:]))
        if not highs or not lows:
            return {"map": {"ok": False, "reason": "brak danych impulsu 15m"}}
        lo, hi = min(lows), max(highs)
        lo_i, hi_i = lows.index(lo), highs.index(hi)
        map_bias = bias if bias in ("LONG", "SHORT") else ("LONG" if lo_i < hi_i else "SHORT")
        ordered = (map_bias == "LONG" and lo_i < hi_i) or (map_bias == "SHORT" and hi_i < lo_i)
        span = hi - lo
        if not ordered or span <= 0:
            return {"map": {"ok": False, "reason": "brak uporządkowanego impulsu 15m"}}
        ratios = (0.236, 0.382, 0.5, 0.618, 0.786)
        levels = {str(ratio): lo + span * ratio for ratio in ratios}
        retracement = (hi - price) / span if map_bias == "LONG" else (price - lo) / span
        in_primary = 0.50 <= retracement <= 0.618
        in_zone = 0.236 <= retracement <= 0.786
        zone = ("primary_0.5_0.618" if in_primary else "deep_0.786" if 0.618 < retracement <= 0.786
                else "shallow" if 0.236 <= retracement < 0.50 else "none")
        return {
            "direction": map_bias, "in_primary": in_primary,
            "confluence_score": 1.0 if in_primary else 0.5 if in_zone else 0.0,
            "weight": 1.0,
            "map": {"ok": True, "side": map_bias, "low": lo, "high": hi,
                    "levels": levels, "retracement": retracement, "zone": zone,
                    "in_zone": in_zone, "in_primary": in_primary},
        }

    @staticmethod
    def _tf_bias(ind: dict) -> str:
        up = bool(ind.get("ema_fast_above_slow") and ind.get("price_above_ema_slow"))
        down = not ind.get("ema_fast_above_slow", True) and not ind.get("price_above_ema_slow", True)
        return "LONG" if up else "SHORT" if down else "NEUTRAL"

    @classmethod
    def _bias(cls, ind_4h: dict, ind_1h: dict) -> tuple:
        """Zwraca (kierunek, sila_wyrownania).

        1.0  -> 4h i 1h w pelni zgodne (jak dawniej)
        <1.0 -> 4h ma wyrazny kierunek, 1h jest NEUTRAL (lag miedzy
                interwalami, nie realny konflikt) - przy DAYTRADING_HTF_SOFT_MODE
                sygnal przechodzi dalej, tylko slabszy
        0.0 z "NEUTRAL" -> 4h i 1h WPROST przeciwne (albo 4h sam neutralny) -
                realny konflikt, blokujemy tak jak dawniej
        """
        b4, b1 = cls._tf_bias(ind_4h), cls._tf_bias(ind_1h)
        if b4 == "LONG" and b1 == "LONG":
            return "LONG", 1.0
        if b4 == "SHORT" and b1 == "SHORT":
            return "SHORT", 1.0

        if not getattr(config, "DAYTRADING_HTF_SOFT_MODE", True):
            return "NEUTRAL", 0.0

        partial_mult = _finite(getattr(config, "DAYTRADING_HTF_PARTIAL_STRENGTH_MULT", 0.55), 0.55)
        if b4 == "LONG" and b1 == "NEUTRAL":
            return "LONG", partial_mult
        if b4 == "SHORT" and b1 == "NEUTRAL":
            return "SHORT", partial_mult

        # b4 sam neutralny, albo b4/b1 wprost przeciwne -> realny konflikt.
        return "NEUTRAL", 0.0

    def _neutral(self, coin: dict, reason: str, details: Optional[dict] = None,
                 strength: float = 0.0, score_components: Optional[dict] = None) -> dict:
        components = dict(score_components or {})
        score = max(0.0, min(0.54, _finite(strength)))
        result = {
            "symbol": coin.get("symbol"), "price": _finite(coin.get("price")),
            "direction": "NEUTRAL", "strength": round(score, 4), "score": round(score * 100, 1),
            "quality": _finite((details or {}).get("quality"), score) if isinstance(details, dict) else score,
            "score_components": components, "engine": "daytrading",
            "strategy_mode": "DAYTRADING", "setup": "intraday_wait",
            "reasons": [reason], "reject_reason": reason,
            "change_1h": coin.get("change_1h"), "change_24h": coin.get("change_24h"),
            "volume_24h": coin.get("blofin_quote_volume_24h") or coin.get("blofin_volume_24h") or coin.get("volume_24h"),
            "signal_source": "BLOFIN_NATIVE", "analysis": {
                "decision": "WAIT", "decision_why": reason,
                "for": [name.replace("_", " ").upper() for name, value in components.items() if _finite(value) > 0],
                "against": [reason], "score_components": components,
            },
        }
        if details:
            result["intraday"] = details
            if details.get("trend_fib"):
                result["trend_fib"] = details["trend_fib"]
            if details.get("funnel"):
                result["funnel"] = details["funnel"]
                result["analysis"]["funnel"] = details["funnel"]
        return result

    def evaluate(self, coin: dict, audit_relax: Optional[set[str]] = None) -> dict:
        audit_relax = frozenset(audit_relax or ())
        symbol = str(coin.get("symbol") or "").upper()
        if not symbol:
            return self._neutral(coin, "DAY_NO_SYMBOL")
        frames, raw = {}, {}
        for tf in ("4h", "1h", "15m", "5m"):
            raw[tf] = self._fetch(symbol, tf)
            frames[tf] = compute_indicators(raw[tf], tf) if raw[tf] else None
        missing = [tf for tf, value in frames.items() if not value]
        if missing:
            coverage = (4 - len(missing)) / 4.0
            return self._neutral(
                coin, "DAY_BLOFIN_DATA_NA(" + ",".join(missing) + ")",
                strength=0.10 * coverage, score_components={"data_coverage": round(0.10 * coverage, 3)},
            )
        try:
            from dynamic_correlation import get_correlation_engine
            get_correlation_engine().set_close_series(
                symbol, raw["5m"].get("closes") or [], raw["5m"].get("timestamps") or []
            )
        except Exception:
            pass

        h4, h1, m15, m5 = frames["4h"], frames["1h"], frames["15m"], frames["5m"]
        bias, bias_align = self._bias(h4, h1)
        price = _finite(m5.get("price"))
        details = {
            "bias_4h_1h": bias, "bias_alignment": bias_align, "tf": frames,
            "bar_ts": {tf: ((raw[tf].get("timestamps") or [None])[-1]) for tf in raw},
            "trend_fib": self._fib_snapshot(raw["15m"], bias, price),
        }
        components = {"data_ready": 0.10}
        b4, b1 = self._tf_bias(h4), self._tf_bias(h1)
        if bias == "NEUTRAL":
            # OR: bierzemy jakikolwiek czytelny kierunek zamiast twardego STOP.
            for candidate, align in ((b1, 0.55), (b4, 0.45)):
                if candidate in ("LONG", "SHORT"):
                    bias, bias_align = candidate, align
                    components["htf_or_fallback"] = align
                    break
            if bias == "NEUTRAL":
                b15 = "LONG" if m15.get("ema_fast_above_slow") else (
                    "SHORT" if m15.get("ema_fast_above_slow") is False else "NEUTRAL"
                )
                if b15 in ("LONG", "SHORT"):
                    bias, bias_align = b15, 0.35
                    components["htf_or_fallback"] = 0.35
        if bias == "NEUTRAL":
            return self._neutral(coin, "DAY_NO_DIRECTION", details, 0.12, components)
        details["bias_4h_1h"] = bias
        details["bias_alignment"] = bias_align
        details["trend_fib"] = self._fib_snapshot(raw["15m"], bias, price)
        components["htf_alignment"] = 0.15 if bias_align >= 1.0 else 0.08

        chop_max = _finite(getattr(config, "DAYTRADING_SETUP_CHOP_MAX", 61.8), 61.8)
        adx_min = _finite(getattr(config, "DAYTRADING_ADX_MIN", 15.0), 15.0)
        adx_quality_min = max(
            adx_min,
            _finite(getattr(config, "DAYTRADING_ADX_QUALITY_MIN", 18.0), 18.0),
        )
        chop_ok = m15.get("choppiness") is not None and m15["choppiness"] <= chop_max
        if chop_ok:
            components["chop_quality"] = 0.08
        adx_value = _finite(m15.get("adx"), 0.0)
        adx_ok = m15.get("adx") is not None and (
            adx_value >= adx_min or "DAY_ADX_WEAK" in audit_relax
        )
        adx_borderline_penalty = min(0.03, max(0.0, (adx_quality_min - adx_value) / 100.0))
        components["adx_strength"] = round(0.08 - adx_borderline_penalty, 4)
        details["adx_policy"] = {
            "value_15m": adx_value,
            "hard_min": adx_min,
            "quality_min": adx_quality_min,
            "strength_penalty": round(adx_borderline_penalty, 4),
        }

        timing_tf = str(getattr(config, "DAYTRADING_TIMING_TF", "15m") or "15m")
        timing = m15 if timing_tf != "5m" else m5
        atr = _finite(timing.get("atr")) or _finite(m15.get("atr")) or _finite(m5.get("atr"))
        if price <= 0 or atr <= 0:
            return self._neutral(coin, "DAY_PRICE_ATR_NA", details, 0.41, components)
        macd = timing.get("macd") or {}
        rsi = timing.get("rsi")
        st_up = bool((timing.get("supertrend") or {}).get("is_up"))
        if bias == "LONG":
            ema_ok = bool(m15.get("ema_fast_above_slow"))
            bb_extreme = bool((m15.get("bb") or {}).get("extreme_above"))
            st_ok = st_up
            macd_ok = macd.get("cross") == "bullish" or (
                macd.get("macd_above_signal") and macd.get("hist_rising")
            )
            rsi_extreme_against = rsi is not None and rsi >= _finite(
                getattr(config, "DAYTRADING_RSI_LONG_EXTREME", 78.0), 78.0
            )
            rsi_mid = rsi is not None and 38 <= rsi <= 68
            sl = price - atr * _finite(getattr(config, "DAYTRADING_SL_ATR_MULT", 2.0), 2.0)
        else:
            ema_ok = not m15.get("ema_fast_above_slow", True)
            bb_extreme = bool((m15.get("bb") or {}).get("extreme_below"))
            st_ok = not st_up
            macd_ok = macd.get("cross") == "bearish" or (
                (not macd.get("macd_above_signal", True)) and macd.get("hist_falling")
            )
            rsi_extreme_against = rsi is not None and rsi <= _finite(
                getattr(config, "DAYTRADING_RSI_SHORT_EXTREME", 22.0), 22.0
            )
            rsi_mid = rsi is not None and 32 <= rsi <= 62
            sl = price + atr * _finite(getattr(config, "DAYTRADING_SL_ATR_MULT", 2.0), 2.0)
        setup_soft = bool(getattr(config, "DAYTRADING_SETUP_SOFT_MODE", True))
        setup_partial_mult = _finite(getattr(config, "DAYTRADING_SETUP_PARTIAL_STRENGTH_MULT", 0.6), 0.6)
        if ema_ok and not bb_extreme:
            setup_align, setup_partial = True, False
        elif ema_ok and bb_extreme and setup_soft:
            # Kierunek 15m OK, ale cena przy skraju Bollingera (mozliwe
            # przegrzanie) - przepuszczamy dalej ze zredukowana sila zamiast
            # twardego rejectu.
            setup_align, setup_partial = True, True
        else:
            setup_align, setup_partial = False, False
        timing = (st_ok or macd_ok) and not rsi_extreme_against
        votes = {
            "htf": bias_align >= 1.0,
            "htf_soft": bias_align > 0,
            "chop_ok": bool(chop_ok),
            "adx_ok": bool(adx_ok),
            "setup_15m": bool(setup_align),
            "st_5m": bool(st_ok),
            "macd_5m": bool(macd_ok),
            "rsi_ok": not rsi_extreme_against,
        }
        vote_count = sum(1 for k in ("htf", "chop_ok", "adx_ok", "setup_15m", "st_5m", "macd_5m") if votes[k])
        min_votes = int(getattr(config, "DAYTRADING_MIN_GATE_VOTES", 2) or 2)
        details["funnel"] = {
            "htf": votes["htf"],
            "chop_ok": votes["chop_ok"],
            "adx_ok": votes["adx_ok"],
            "setup_15m": votes["setup_15m"],
            "setup_15m_partial": bool(setup_partial),
            "st_5m": votes["st_5m"],
            "macd_5m": votes["macd_5m"],
            "rsi_mid": bool(rsi_mid),
            "rsi_extreme_against": bool(rsi_extreme_against),
            "timing_5m": bool(timing),
            "votes": vote_count,
            "min_votes": min_votes,
        }
        if setup_align:
            components["setup_15m"] = 0.08 if not setup_partial else round(0.08 * setup_partial_mult, 4)
        if rsi_mid:
            components["rsi_mid"] = 0.02
        if rsi_extreme_against:
            return self._neutral(coin, "DAY_RSI_EXTREME", details, 0.35, components)
        if vote_count < min_votes:
            return self._neutral(coin, f"DAY_VOTES_LOW({vote_count}<{min_votes})", details, 0.40, components)

        supports, resistances = self._levels(m15, price, atr)
        fib = self._fib_levels(raw["15m"], bias)
        buffer_atr = _finite(getattr(config, "DAYTRADING_STRUCTURE_ATR_BUFFER", 0.15), 0.15) * atr
        # Put the stop beyond the nearest confirmed invalidation level when it
        # is reasonably close. A too-distant structure does not widen risk.
        structural = (supports[0] - buffer_atr) if bias == "LONG" and supports else (
            (resistances[0] + buffer_atr) if bias == "SHORT" and resistances else None
        )
        details["sl_source"] = "atr_5m"
        if structural is not None:
            structural_atr = abs(price - structural) / atr
            max_struct = _finite(getattr(config, "DAYTRADING_MAX_STRUCTURAL_SL_ATR", 2.5), 2.5)
            if 0.6 <= structural_atr <= max_struct:
                sl = structural
                components["structure_sl"] = 0.03
                details["sl_source"] = "structure_15m"
        min_sl_pct = _finite(getattr(config, "DAYTRADING_MIN_SL_PCT", 0.40), 0.40) / 100.0
        if abs(price - sl) < price * min_sl_pct:
            sl = price - (price * min_sl_pct if bias == "LONG" else -price * min_sl_pct)
            details["sl_source"] = f"{details.get('sl_source')}+min_pct"
        risk_distance = abs(price - sl)
        atr_sl_mult = _finite(getattr(config, "DAYTRADING_SL_ATR_MULT", 2.0), 2.0)
        atr_risk = max(atr * atr_sl_mult, price * 1e-8)
        tp1_r = _finite(getattr(config, "DAYTRADING_TP1_R", 1.5), 1.5)
        tp2_r = _finite(getattr(config, "DAYTRADING_TP2_R", 2.2), 2.2)
        sign = 1.0 if bias == "LONG" else -1.0
        barrier = (resistances[0] if bias == "LONG" and resistances else
                   supports[0] if bias == "SHORT" and supports else None)
        barrier_r = abs(barrier - price) / atr_risk if barrier is not None else None
        min_barrier_r = _finite(getattr(config, "DAYTRADING_MIN_BARRIER_R", 1.2), 1.2)
        hard_barrier_r = _finite(getattr(config, "DAYTRADING_BARRIER_HARD_R", 0.60), 0.60)
        details["barrier"] = {
            "price": barrier, "r_atr": barrier_r,
            "hard": hard_barrier_r, "soft": min_barrier_r,
            "source": "structure_or_pivot",
        }
        if details.get("funnel") is not None:
            details["funnel"]["barrier_ok"] = True
        if barrier_r is not None and barrier_r < hard_barrier_r:
            components["barrier_penalty"] = -0.25
            details["barrier"]["policy"] = "penalty_logit"
            tp1_r_effective = max(0.35, barrier_r - 0.10)
        elif barrier_r is not None and barrier_r < min_barrier_r:
            tp1_r_effective = max(0.35, barrier_r - 0.10)
            components["barrier_soft"] = round(barrier_r, 3)
            details["barrier"]["policy"] = "soft_cap_tp1"
        elif barrier_r is not None:
            tp1_r_effective = min(tp1_r, max(min_barrier_r, barrier_r - 0.10))
            details["barrier"]["policy"] = "full"
        else:
            tp1_r_effective = tp1_r
            details["barrier"]["policy"] = "no_level"
        fib_near = any(abs(level - price) <= 0.35 * atr for level in fib)
        fib_snapshot = details["trend_fib"]
        if fib_near:
            components["fibonacci_confluence"] = 0.12
            fib_snapshot["confluence_score"] = max(float(fib_snapshot.get("confluence_score") or 0), 1.0)
            fib_snapshot["confluence_tags"] = ["FIB_NEAR_PRICE"]
        qfeat = {
            "adx": min(0.50, max(-0.25, (adx_value - adx_quality_min) / 20.0)) if adx_ok else -0.25,
            "st_15m": 0.35 if st_ok else -0.20,
            "macd_cross": 0.35 if macd_ok else 0.0,
            "chop_clean": 0.25 if chop_ok and m15.get("choppiness", 100) < 50 else (-0.20 if not chop_ok else 0.0),
            "barrier": -0.25 if (barrier_r is not None and barrier_r < hard_barrier_r) else 0.0,
            "fib": 0.12 if fib_near else 0.0,
            "htf_partial": 0.0 if bias_align >= 1.0 else (-0.35 if bias_align >= 0.5 else -0.55),
            "bb_extreme": -0.50 if setup_partial else (-0.25 if not setup_align else 0.0),
        }
        scored = _quality_and_size(qfeat, 0.0)
        quality = scored["quality"]
        if bias_align < 1.0:
            components["htf_partial_confirm"] = -0.60
        if setup_partial:
            components["setup_15m_partial_confirm"] = -0.50
        signal = {
            "symbol": symbol, "price": price, "direction": bias,
            "rs": _finite(coin.get("residual_momentum_24h"), _finite(coin.get("change_24h"))),
            "change_1h": coin.get("change_1h"),
            "change_24h": coin.get("change_24h"),
            "change_7d": coin.get("change_7d"),
            "quality": quality,
            "strength": quality,
            "engine": "daytrading",
            "score": round(quality * 100, 1),
            "score_logit": scored["logit"],
            "score_parts": scored["parts"],
            "score_components": {**components, "timing_5m": 0.06},
            "strategy_mode": "DAYTRADING", "setup": "intraday_5m_confirmed",
            "reasons": [
                "DAY_HTF_ALIGN" if bias_align >= 1.0 else "DAY_HTF_PARTIAL",
                "DAY_15M_SETUP" if not setup_partial else "DAY_15M_PARTIAL",
                "DAY_15M_TIMING" if timing_tf != "5m" else "DAY_5M_CONFIRM",
                "DAY_CLOSED_CANDLES",
            ],
            "sl_price": sl, "tp1_price": price + sign * risk_distance * tp1_r_effective,
            "tp2_price": price + sign * risk_distance * tp2_r,
            "tp_price": price + sign * risk_distance * tp2_r,
            "tp_plan": {
                "tp1_r": tp1_r_effective, "tp2_r": tp2_r,
                "frac_tp1": _finite(getattr(config, "DAYTRADING_TP1_FRAC", 0.50), 0.50),
                "frac_tp2": _finite(getattr(config, "DAYTRADING_TP2_FRAC", 0.25), 0.25),
                "frac_trail": 0.25,
            },
            "funnel": details.get("funnel") or {},
            "sl_source": details.get("sl_source") or "atr_5m",
            "margin_mode": getattr(config, "BLOFIN_MARGIN_MODE", "isolated"),
            "exchange_sl_planned": True,
            "atr": atr, "atr_pct": atr / price * 100.0,
            "rsi": rsi, "adx": m15.get("adx"), "choppiness": m15.get("choppiness"),
            "indicators": m5, "intraday": details, "trend_fib": fib_snapshot,
            "signal_source": "BLOFIN_NATIVE",
            "confluence": {"supports": supports, "resistances": resistances,
                            "fib_levels": fib, "nearest_barrier_r": barrier_r},
            "ohlcv_source": "native_blofin",
            "change_1h": coin.get("change_1h"), "change_24h": coin.get("change_24h"),
            "volume_24h": coin.get("blofin_quote_volume_24h") or coin.get("blofin_volume_24h") or coin.get("volume_24h"),
        }
        signal["order_book"] = {}
        signal["funding"] = {}
        try:
            from external_confirmation import apply_confirmation
            apply_confirmation(signal, coin)
            signal["score"] = round(_finite(signal.get("strength")) * 100, 1)
        except Exception:
            signal["cross_market_status"] = "UNKNOWN"
        try:
            from day_expectancy_calibration import get_day_calibrator
            signal["day_expectancy_calibration"] = get_day_calibrator().snapshot()
        except Exception:
            signal["day_expectancy_calibration"] = {"n": 0}
        expected_net_r(signal)
        net_r = _finite(signal.get("expected_net_r"), 0.0)
        sized = _quality_and_size(qfeat, net_r)
        size_mult = sized["size_mult"]
        calib_n = 0
        try:
            calib_n = int((signal.get("day_expectancy_calibration") or {}).get("n") or 0)
        except (TypeError, ValueError):
            calib_n = 0
        min_sample = int(getattr(config, "DAYTRADING_NET_R_MIN_SAMPLE", 20) or 20)
        uncalibrated = (
            signal.get("expected_r_status") in ("PRIOR_ONLY", "LOW_SAMPLE")
            or calib_n < min_sample
        )
        if uncalibrated:
            size_mult = min(size_mult, _finite(getattr(config, "DAYTRADING_UNCALIBRATED_SIZE_MULT", 0.35), 0.35))
        signal["_size_mult"] = size_mult
        signal["size_mult"] = size_mult
        signal["quality"] = quality
        signal["strength"] = quality
        signal["score"] = round(quality * 100, 1)
        minimum = _finite(getattr(config, "DAYTRADING_MIN_EXPECTED_NET_R", 0.20), 0.20)
        qmin = _finite(getattr(config, "DAYTRADING_QUALITY_MIN", 0.55), 0.55)
        ch24 = _finite(coin.get("change_24h"), 0.0)
        chase_long = _finite(getattr(config, "DAYTRADING_CHASE_LONG_PCT", 22.0), 22.0)
        chase_short = _finite(getattr(config, "DAYTRADING_CHASE_SHORT_PCT", 20.0), 20.0)
        if (not uncalibrated) and net_r < minimum:
            signal["reject_reason"] = f"DAY_NET_R_LOW({signal.get('expected_net_r')})"
        elif quality < qmin:
            signal["reject_reason"] = f"DAY_QUALITY_LOW({quality:.2f}<{qmin:.2f})"
        elif bias == "LONG" and ch24 >= chase_long:
            signal["reject_reason"] = f"DAY_CHASE_LONG(+{ch24:.0f}%)"
            signal["pump_chase"] = True
        elif bias == "SHORT" and ch24 <= -chase_short:
            signal["reject_reason"] = f"DAY_CHASE_SHORT({ch24:.0f}%)"
            signal["dump_chase"] = True
        signal["analysis"] = {
            "decision": "OPEN_OK" if not signal.get("reject_reason") else "REJECTED",
            "decision_why": signal.get("reject_reason") or "quality + expected_net_r + 5m confirm",
            "for": list(signal["reasons"]),
            "against": [signal["reject_reason"]] if signal.get("reject_reason") else [],
            "indicators": {"rsi_5m": rsi, "adx_15m": m15.get("adx"), "chop_15m": m15.get("choppiness")},
            "quality": quality, "expected_net_r": net_r, "size_mult": size_mult,
        }
        return signal

    def generate(self, coins: Iterable[dict]) -> List[dict]:
        valid = [coin for coin in coins or [] if str(coin.get("symbol") or "").upper() not in STABLES]
        def quote_volume(coin):
            explicit = _finite(coin.get("blofin_quote_volume_24h"))
            if explicit > 0:
                return explicit
            base = _finite(coin.get("blofin_base_volume_24h"))
            if base > 0:
                return base * _finite(coin.get("price"))
            return _finite(coin.get("blofin_volume_24h") or coin.get("volume_24h"))

        ranked = sorted(valid, key=quote_volume, reverse=True)
        minimum = max(0.0, _finite(getattr(config, "MIN_VOLUME_24H_USD", 0)))
        ranked = [coin for coin in ranked if quote_volume(coin) >= minimum]
        # Adaptacyjnie: gdy WS jest polaczony, budzet REST na kandydata jest
        # praktycznie nieograniczajacy (>1000 kandydatow zmiescilo by sie w
        # budzecie PUBLIC_BUCKET - patrz komentarz przy configu) - cale
        # wazne uniwersum dostaje pelna kaskade, nie tylko top-N. Gdy WS
        # padnie, wracamy do bezpiecznego sufitu DAYTRADING_MAX_CANDIDATES.
        ws_connected = PUBLIC_WS.is_connected()
        ws_limit = getattr(config, "DAYTRADING_MAX_CANDIDATES_WS_CONNECTED", None)
        if ws_connected and not ws_limit:
            ranked_selected = ranked  # brak limitu
        else:
            limit = int(ws_limit) if (ws_connected and ws_limit) else int(getattr(config, "DAYTRADING_MAX_CANDIDATES", 12))
            ranked_selected = ranked[:max(1, limit)]
        selected = {str(c.get("symbol") or "").upper() for c in ranked_selected}
        out = []
        for coin in valid:
            symbol = str(coin.get("symbol") or "").upper()
            if symbol in selected:
                out.append(self.evaluate(coin))
            else:
                # Trzecia warstwa uniwersum: bez pelnej analizy wielu
                # interwalow (kosztowne swiece), ale spread/rozjazd ceny sa
                # juz i tak w `coin` (z bulk tickera Blofin, zero
                # dodatkowego kosztu sieciowego) - podajemy je zamiast
                # calkowitej ciszy.
                bid = _finite(coin.get("blofin_bid"), None)
                ask = _finite(coin.get("blofin_ask"), None)
                price = _finite(coin.get("price"))
                spread_pct = None
                if bid and ask and price:
                    spread_pct = round((ask - bid) / price * 100.0, 4)
                out.append(self._neutral(
                    coin, "DAY_NOT_IN_LIQUID_TOP", strength=0.05,
                    score_components={"liquid_universe": 0.05},
                    details={"spread_only": True, "bid": bid, "ask": ask, "spread_pct": spread_pct},
                ))
        out.sort(key=lambda item: (item.get("direction") != "NEUTRAL", _finite(item.get("strength"))), reverse=True)
        return out
