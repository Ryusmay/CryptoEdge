# ============================================================
# Signal Engine - Multi-TF + RSI/MACD + Entry/Exit + Anomalie
# ============================================================

from typing import List, Dict
import config
from indicators import analyze_rsi_macd
from correlation import analyze_source_divergence
from indicators_full import compute_indicators, evaluate_entry


def _se_log(where: str, exc: BaseException):
    """Log wyjątku SignalEngine bez połykania typu."""
    try:
        print(f"[SignalEngine] {where}: {type(exc).__name__}: {exc}")
    except Exception as e:
        _se_log("handler", e)
class SignalEngine:
    def __init__(self, data_feeder=None):
        self.last_analysis_board = []
        self.last_regime = {"regime": "UNKNOWN"}
        self.last_reversal_diag = {"ok": None, "error": None}
        self.previous_prices = {}
        self.price_history = {}
        self.feeder = data_feeder  # do pobierania OHLC
        self.indicator_cache = {}  # symbol -> wynik RSI/MACD

    @staticmethod
    def _apply_trend_fib_pullback(s: Dict) -> None:
        """
        Trend Engine + Fibonacci mapa (nie sygnał).

          LOW ──impuls──→ HIGH
                            │
                     0.382  │
                     0.500  │
                     0.618 ⭐  ← preferowana strefa LONG pullback
                     0.786  │
                           LOW

        LONG:  trend UP + cena w retrace 0.5–0.618 (nie na szczycie) → bonus
        SHORT: trend DOWN + pullback w górę do 0.5–0.618 → bonus
        Na ekstremum impulsu (płytki retrace) → kara (chase)
        """
        direction = s.get("direction")
        if direction not in ("LONG", "SHORT"):
            return
        try:
            from reversal_engine import fibonacci_map
        except Exception:
            return

        # Impuls w kierunku trendu: LONG po UP-leg, SHORT po DOWN-leg
        extreme_side = "UP" if direction == "LONG" else "DOWN"
        # Do mapy potrzebne highs/lows — z s lub strategy/ohlcv
        coin = dict(s)
        if not coin.get("lows") and not coin.get("highs"):
            # spróbuj z price_history nie — zostaw fallback z change_24h w fibonacci_map
            pass
        fib = fibonacci_map(coin, extreme_side=extreme_side)
        s["trend_fib"] = fib
        if not fib.get("ok"):
            return

        retr = float(fib.get("retracement") or 0)
        strength = float(s.get("strength") or 0)
        reasons = list(s.get("reasons") or [])

        # Trend framework (START): Trend20 MTF15 Mom10 Vol10 Pullback15 Fib10 OB10 X10
        fib_pts = 0.0
        if fib.get("in_primary"):
            fib_pts = 0.10
            reasons.append(f"TREND_FIB_PULLBACK_0.5_0.618({retr:.2f})")
            s["ride_hint"] = True
        elif fib.get("in_deep"):
            fib_pts = 0.05
            reasons.append(f"TREND_FIB_DEEP_0.786({retr:.2f})")
        elif retr < 0.25:
            fib_pts = -0.10
            reasons.append(f"TREND_FIB_CHASE_TOP({retr:.2f})")
            s["_size_mult"] = min(float(s.get("_size_mult") or 1.0), 0.55)
        elif retr < 0.48:
            fib_pts = -0.03
            reasons.append(f"TREND_FIB_SHALLOW({retr:.2f})")
        else:
            reasons.append(f"TREND_FIB_OUT({retr:.2f})")

        strength = max(0.0, min(1.0, strength + fib_pts))
        s["strength"] = strength
        s["score_components"] = dict(s.get("score_components") or {})
        s["score_components"]["fibonacci_confluence"] = round(fib_pts, 3)
        if s.get("trend_score") is not None:
            try:
                s["trend_score"] = float(s["strength"])
            except (TypeError, ValueError):
                pass
        s["reasons"] = reasons

    @staticmethod
    def _aggregate_ohlcv_to_higher(ohlcv: dict, factor: int = 4) -> dict:
        """Z 1h OHLCV zbuduj syntetyczne 4h – granice UTC, tylko zamknięte buckety."""
        if factor == 4:
            try:
                from market_data import aggregate_to_4h
                return aggregate_to_4h(ohlcv, drop_incomplete_bucket=True) or {}
            except Exception as e:
                _se_log("handler", e)
        # fallback legacy (bez timestamps)
        closes = list(ohlcv.get("closes") or [])
        highs = list(ohlcv.get("highs") or [])
        lows = list(ohlcv.get("lows") or [])
        volumes = list(ohlcv.get("volumes") or [])
        n = len(closes)
        if n < factor * 10:
            return {}
        m = min(n, len(highs), len(lows), len(volumes) if volumes else n)
        closes, highs, lows = closes[:m], highs[:m], lows[:m]
        volumes = volumes[:m] if volumes else [0.0] * m
        # wyrównaj do granicy factor od końca (nie od początku – mniej przesunięcia)
        start = m % factor
        out_c, out_h, out_l, out_v = [], [], [], []
        for i in range(start, m - factor + 1, factor):
            chunk_c = closes[i:i + factor]
            if len(chunk_c) < factor:
                break
            out_c.append(chunk_c[-1])
            out_h.append(max(highs[i:i + factor]))
            out_l.append(min(lows[i:i + factor]))
            out_v.append(sum(volumes[i:i + factor]))
        if len(out_c) < 20:
            return {}
        return {"closes": out_c, "highs": out_h, "lows": out_l, "volumes": out_v}

    def evaluate_strategy_tf(self, symbol: str, tf: str = "1h") -> dict:
        """
        Strategia TYLKO na BloFin OHLCV (execution venue = signal venue).
        Kolejność:
          1) BloFin native
          2) BloFin proxy 1H→4H (tylko tf=4h)
          3) NO DATA → {}  (NIE używamy Binance jako fallbacku strategii)
        Binance/CG = external_confirmation (osobny moduł).
        """
        if tf in ("4h", "1d"):
            min_bars = int(getattr(config, "MIN_BARS_4H_1D", 225))
            fetch_limit = max(380, int(getattr(config, "FETCH_LIMIT_4H_1D", 250)))
        else:
            min_bars = 50
            fetch_limit = 380
        proxy_1h_limit = int(getattr(config, "PROXY_1H_LIMIT_FOR_4H", 1000))

        bar = {"15m": "15m", "1h": "1H", "4h": "4H", "1d": "1D"}.get(tf, "1H")
        proxy_used = False
        ohlcv = {}
        signal_source = "NONE"

        # 1) BloFin native — PRIMARY
        try:
            if self.feeder and hasattr(self.feeder, "blofin"):
                ohlcv = self.feeder.blofin.fetch_klines_ohlcv(
                    symbol, bar=bar, limit=fetch_limit
                ) or {}
                if len((ohlcv or {}).get("closes") or []) >= min_bars:
                    signal_source = "BLOFIN_NATIVE"
        except (OSError, ValueError, TypeError, KeyError, TimeoutError) as e:
            _se_log(f"klines BF {symbol} {tf}", e)
            ohlcv = {}
        except Exception as e:
            _se_log(f"klines BF unexpected {symbol} {tf}", e)
            ohlcv = {}

        # 2) BloFin proxy 4H z 1H (NIE Binance)
        if len((ohlcv or {}).get("closes") or []) < min_bars and tf == "4h":
            o1h = {}
            try:
                if self.feeder and hasattr(self.feeder, "blofin"):
                    o1h = self.feeder.blofin.fetch_klines_ohlcv(
                        symbol, bar="1H", limit=proxy_1h_limit
                    ) or {}
            except Exception as e:
                _se_log(f"proxy1h BF {symbol}", e)
                o1h = {}
            if len((o1h or {}).get("closes") or []) >= int(proxy_1h_limit * 0.8):
                agg = self._aggregate_ohlcv_to_higher(o1h, factor=4)
                if len((agg or {}).get("closes") or []) >= min_bars:
                    ohlcv = agg
                    proxy_used = True
                    signal_source = "BLOFIN_PROXY_4H"

        n = len((ohlcv or {}).get("closes") or [])
        if n < min_bars:
            # Jawny brak danych BloFin — NIE spadamy na Binance do strategii
            return {
                "pass": False,
                "signal_source": "NONE",
                "ohlcv_source": "none",
                "error": "BLOFIN_OHLCV_UNAVAILABLE",
                "bars": n,
            }

        ind = compute_indicators(ohlcv, tf=tf)
        if not ind:
            return {"pass": False, "signal_source": signal_source, "error": "INDICATORS_FAIL"}
        entry = evaluate_entry(ind)
        if not entry:
            return {"pass": False, "signal_source": signal_source, "error": "ENTRY_EVAL_FAIL"}

        entry["indicators"] = {
            "rsi": ind.get("rsi"),
            "adx": ind.get("adx"),
            "atr": ind.get("atr"),
            "supertrend": (ind.get("supertrend") or {}).get("direction"),
            "ema_fast": ind.get("ema_fast"),
            "ema_slow": ind.get("ema_slow"),
            "cipher_b": ind.get("cipher_b"),
            "pivot_points": ind.get("pivot_points"),
            "support_resistance": ind.get("support_resistance"),
            "viper": ind.get("viper"),
            "choppiness": ind.get("choppiness"),
            "choppiness_state": ind.get("choppiness_state"),
        }
        entry["adx"] = ind.get("adx")
        entry["rsi"] = ind.get("rsi")
        entry["supertrend"] = (ind.get("supertrend") or {}).get("direction")
        entry["cipher_b"] = ind.get("cipher_b")
        entry["signal_source"] = signal_source
        if proxy_used:
            entry["proxy"] = "blofin_1h_agg_4h"
            entry["ohlcv_source"] = "proxy_4h"
        else:
            entry["ohlcv_source"] = (
                "native_4h" if tf == "4h" else ("native_1d" if tf == "1d" else "native_blofin")
            )
        return entry


    def evaluate_mtf(self, symbol: str) -> dict:
        """
        Multi-timeframe: 15m, 1h, 4h, 1d.
        Zwraca alignment z kierunkiem i sugestie hold.
        """
        tfs = getattr(config, "MTF_TIMEFRAMES", ["15m", "1h", "4h", "1d"])
        if not getattr(config, "MTF_ENABLED", True):
            return {}
        results = {}
        for tf in tfs:
            try:
                results[tf] = self.evaluate_strategy_tf(symbol, tf=tf) or {}
            except Exception:
                results[tf] = {}
        long_votes = sum(1 for r in results.values() if r.get("direction") == "LONG" and r.get("pass"))
        short_votes = sum(1 for r in results.values() if r.get("direction") == "SHORT" and r.get("pass"))
        # trend continuation: wyzsze TF
        higher = []
        for tf in ("1h", "4h", "1d"):
            r = results.get(tf) or {}
            if r.get("pass") and r.get("direction") in ("LONG", "SHORT"):
                higher.append(r["direction"])
        hold_long = higher.count("LONG") >= 2
        hold_short = higher.count("SHORT") >= 2
        return {
            "by_tf": {k: {"direction": v.get("direction"), "pass": v.get("pass"),
                          "adx": v.get("adx"), "rsi": v.get("rsi")} for k, v in results.items()},
            "long_votes": long_votes,
            "short_votes": short_votes,
            "hold_long": hold_long,
            "hold_short": hold_short,
            "early_long": (results.get("15m") or {}).get("direction") == "LONG" and (results.get("15m") or {}).get("pass"),
            "early_short": (results.get("15m") or {}).get("direction") == "SHORT" and (results.get("15m") or {}).get("pass"),
            "required_align": int(getattr(config, "MTF_REQUIRE_ALIGN", 2) or 2),
        }


    def compute_liquidity_score(self, s: dict) -> dict:
        """
        Liquidity score 0–100 dla altów:
        volume 24h + OB depth + spread + multi-source.
        """
        parts = {}
        score = 0.0
        notes = []

        # Volume 24h (0–35)
        vol = float(s.get("volume_24h") or 0)
        if vol >= 50_000_000:
            parts["volume"] = 35
            notes.append("vol bardzo wysoki")
        elif vol >= 10_000_000:
            parts["volume"] = 28
            notes.append("vol wysoki")
        elif vol >= 2_000_000:
            parts["volume"] = 20
            notes.append("vol OK")
        elif vol >= 500_000:
            parts["volume"] = 12
            notes.append("vol niski")
        elif vol > 0:
            parts["volume"] = 5
            notes.append("vol bardzo niski")
        else:
            parts["volume"] = 0
            notes.append("vol brak")
        score += parts["volume"]

        # Order book depth (0–35)
        ob = s.get("order_book") or {}
        depth = ob.get("ob_depth_usd")
        min_d = float(getattr(config, "OB_MIN_DEPTH_USD", 5000) or 5000)
        if depth is None:
            parts["depth"] = 8
            notes.append("depth brak danych")
        else:
            depth = float(depth)
            if depth >= min_d * 4:
                parts["depth"] = 35
                notes.append(f"depth solidny ${depth:.0f}")
            elif depth >= min_d * 2:
                parts["depth"] = 28
                notes.append(f"depth dobry ${depth:.0f}")
            elif depth >= min_d:
                parts["depth"] = 20
                notes.append(f"depth OK ${depth:.0f}")
            elif depth >= min_d * 0.4:
                parts["depth"] = 10
                notes.append(f"depth słaby ${depth:.0f}")
            else:
                parts["depth"] = 2
                notes.append(f"depth thin ${depth:.0f}")
        score += parts["depth"]

        # Spread (0–15) – niższy = lepiej
        sp = ob.get("ob_spread_pct")
        if sp is None:
            parts["spread"] = 6
            notes.append("spread n/a")
        else:
            sp = float(sp)
            if sp <= 0.05:
                parts["spread"] = 15
                notes.append(f"spread {sp:.3f}% tight")
            elif sp <= 0.12:
                parts["spread"] = 12
                notes.append(f"spread {sp:.3f}% OK")
            elif sp <= 0.25:
                parts["spread"] = 7
                notes.append(f"spread {sp:.3f}% szeroki")
            else:
                parts["spread"] = 2
                notes.append(f"spread {sp:.3f}% zły")
        score += parts["spread"]

        # Multi-source (0–15)
        sd = s.get("source_div") or {}
        if isinstance(sd, (int, float)):
            sd = {"max_diff_pct": sd}
        n_src = int(sd.get("sources_available") or 0)
        diff = float(sd.get("max_diff_pct") or 0)
        if s.get("blofin_only"):
            parts["sources"] = 3
            notes.append("tylko Blofin")
        elif n_src >= 3 and diff < 0.5:
            parts["sources"] = 15
            notes.append(f"{n_src} źródeł, rozjazd {diff:.2f}%")
        elif n_src >= 2 and diff < 1.0:
            parts["sources"] = 12
            notes.append(f"{n_src} źródeł, rozjazd {diff:.2f}%")
        elif n_src >= 2:
            parts["sources"] = 8
            notes.append(f"{n_src} źródeł, rozjazd {diff:.2f}%")
        elif n_src == 1:
            parts["sources"] = 5
            notes.append("1 źródło")
        else:
            parts["sources"] = 6
            notes.append("źródła nieznane")
        score += parts["sources"]

        score = max(0, min(100, round(score, 1)))
        if score >= 75:
            grade = "A"
            label = "wysoka"
        elif score >= 55:
            grade = "B"
            label = "średnia+"
        elif score >= 40:
            grade = "C"
            label = "średnia"
        elif score >= 25:
            grade = "D"
            label = "niska"
        else:
            grade = "F"
            label = "bardzo niska"

        return {
            "score": score,
            "grade": grade,
            "label": label,
            "parts": parts,
            "notes": notes,
            "depth_usd": depth if depth is not None else None,
            "spread_pct": float(sp) if sp is not None else None,
            "volume_24h": vol,
        }

    def liquidity_reject(self, signal: dict, coin: dict = None) -> str:
        """Zwraca powod odrzucenia lub pusty string."""
        # Liquidity gate TYLKO na BloFin volume (nie BN/CG fallback)
        vol = float(signal.get("blofin_volume_24h") or signal.get("blofin_volume") or 0)
        vol_unknown = vol <= 0
        min_vol = float(getattr(config, "MIN_VOLUME_24H_USD", 0) or 0)
        if vol_unknown and bool(getattr(config, "REQUIRE_BLOFIN_VOLUME", True)):
            return "VOL_UNKNOWN_BLOFIN"
        if min_vol > 0 and vol > 0 and vol < min_vol:
            return f"LOW_VOLUME({vol:.0f}<{min_vol:.0f})"
        mode = str(getattr(config, "MULTI_SOURCE_MODE", "off") or "off").lower()
        if getattr(config, "REQUIRE_MULTI_SOURCE", False):
            mode = "all"
        if mode in ("all", "majors") and signal.get("blofin_only"):
            if mode == "all":
                return "BLOFIN_ONLY"
            # majors: tylko przy wysokim volume (top plynnosc – unikaj false knotow)
            vol = float(signal.get("volume_24h") or 0)
            thr = float(getattr(config, "MULTI_SOURCE_MAJOR_VOLUME", 5_000_000) or 5_000_000)
            if vol >= thr:
                return f"BLOFIN_ONLY_MAJOR(vol={vol:.0f})"
        sd = signal.get("source_div") or {}
        max_div = float(getattr(config, "MAX_SOURCE_DIVERGENCE_PCT", 1.5) or 1.5)
        if sd.get("warning") and not sd.get("symbol_mismatch"):
            if float(sd.get("max_diff_pct") or 0) >= max_div:
                return f"SRC_DIVERGENCE({sd.get('max_diff_pct')}%)"
        return ""


    def detect_market_regime(self, btc_change_24h: float, alt_changes_24h=None, avg_funding=None) -> dict:
        """
        10–11. Reżim: ADX, ATR%, EMA slope, realized vol, breadth,
        BTC trend, dispersion, funding + hysteresis (N confirmations).
        """
        try:
            from regime_model import get_regime_engine
            eng = get_regime_engine()
            ohlcv = {}
            if self.feeder and hasattr(self.feeder, "binance"):
                ohlcv = self.feeder.binance.fetch_klines_ohlcv("BTC", interval="1h", limit=120) or {}
            if len((ohlcv or {}).get("closes") or []) < 40 and self.feeder and hasattr(self.feeder, "blofin"):
                ohlcv = self.feeder.blofin.fetch_klines_ohlcv("BTC", bar="1H", limit=120) or {}
            # alt changes z last board / param
            if alt_changes_24h is None:
                alt_changes_24h = []
                for row in (getattr(self, "last_analysis_board", None) or []):
                    ch = row.get("change_24h")
                    if ch is not None:
                        alt_changes_24h.append(ch)
            result = eng.compute_from_ohlcv(
                ohlcv or {},
                btc_change_24h=btc_change_24h,
                alt_changes_24h=alt_changes_24h,
                avg_funding=avg_funding,
            )
            self.last_regime = result
            return result
        except Exception as e:
            btc = float(btc_change_24h or 0)
            if abs(btc) < 1.2:
                regime = "RANGE"
            elif btc >= 1.2:
                regime = "TREND_UP"
            else:
                regime = "TREND_DOWN"
            out = {"regime": regime, "btc_24h": btc, "error": str(e)}
            self.last_regime = out
            return out


    def volume_breakout_confirm(self, symbol: str) -> dict:
        """
        Vol ostatniej swiecy vs MA(VOLUME_MA_PERIOD) na TF VOLUME_BREAKOUT_TF.
        """
        out = {"ok": None, "ratio": None, "flag": None}
        period = int(getattr(config, "VOLUME_MA_PERIOD", 20))
        mult = float(getattr(config, "VOLUME_BREAKOUT_MULT", 1.75))
        tf = str(getattr(config, "VOLUME_BREAKOUT_TF", "15m"))
        try:
            ohlcv = {}
            if self.feeder and hasattr(self.feeder, "binance"):
                ohlcv = self.feeder.binance.fetch_klines_ohlcv(symbol, interval=tf, limit=period + 5) or {}
            if len((ohlcv or {}).get("volumes") or []) < period and self.feeder and hasattr(self.feeder, "blofin"):
                bar = {"15m": "15m", "1h": "1H", "5m": "5m", "1H": "1H"}.get(tf, "15m")
                ohlcv = self.feeder.blofin.fetch_klines_ohlcv(symbol, bar=bar, limit=period + 5) or {}
            vols = ohlcv.get("volumes") or []
            if len(vols) < period:
                out["flag"] = "VOL_MA_NA"
                return out
            last = float(vols[-1] or 0)
            ma = sum(float(v or 0) for v in vols[-period - 1:-1]) / period
            if ma <= 0:
                out["flag"] = "VOL_MA_NA"
                return out
            ratio = last / ma
            # fallback 1h gdy 15m wyglada na smieci
            if ratio < 0.2 and tf in ("15m", "5m"):
                try:
                    o2 = {}
                    if self.feeder and hasattr(self.feeder, "binance"):
                        o2 = self.feeder.binance.fetch_klines_ohlcv(symbol, interval="1h", limit=period + 5) or {}
                    vols2 = (o2 or {}).get("volumes") or []
                    if len(vols2) >= period:
                        last = float(vols2[-1] or 0)
                        ma = sum(float(v or 0) for v in vols2[-period - 1:-1]) / period
                        if ma > 0:
                            ratio = last / ma
                            out["tf_used"] = "1h"
                except Exception as e:
                    _se_log("handler", e)
            out["ratio"] = round(ratio, 2)
            out["ok"] = ratio >= mult
            out["flag"] = f"VOL_BREAKOUT({ratio:.2f}x)" if out["ok"] else f"VOL_WEAK({ratio:.2f}x)"
        except Exception as e:
            out["flag"] = f"VOL_MA_ERR"
            out["error"] = str(e)
        return out

    def analyze_volume_ob(self, coin: dict, symbol: str, fetch_ob: bool = False) -> dict:
        """Analiza wolumenu + opcjonalnie order book."""
        out = {
            "vol_score": 0.0,
            "vol_flag": None,
            "ob": None,
        }
        vol = float(coin.get("blofin_volume_24h") or coin.get("blofin_volume") or 0)
        # progi orientacyjne (USD notional)
        if vol >= 50_000_000:
            out["vol_score"] = 0.12
            out["vol_flag"] = "VOL_HIGH"
        elif vol >= 5_000_000:
            out["vol_score"] = 0.06
            out["vol_flag"] = "VOL_OK"
        elif vol > 0 and vol < 200_000:
            out["vol_score"] = -0.08
            out["vol_flag"] = "VOL_THIN"
        elif vol == 0:
            out["vol_flag"] = "VOL_UNKNOWN"

        # volume / price spike – progi z configu
        # VOLUME_SPIKE_MULTIPLIER: wyzszy = latwiej o flage SPIKE (wrazliwe)
        # bazowo 2.5 → próg |change_24h| ≈ 8%; przy 5.0 → ≈ 4%
        try:
            mult = float(getattr(config, "VOLUME_SPIKE_MULTIPLIER", 2.5) or 2.5)
        except Exception:
            mult = 2.5
        spike_change = max(3.0, 20.0 / max(mult, 0.5))  # 2.5 → 8.0
        jump = float(getattr(config, "PRICE_JUMP_THRESHOLD", 0.035) or 0.035) * 100  # 3.5%
        ch24 = abs(float(coin.get("change_24h") or 0))
        ch1h = abs(float(coin.get("change_1h") or 0))
        if ch24 >= spike_change and vol >= 1_000_000:
            bonus = min(0.12, 0.04 * (mult / 2.5))
            out["vol_score"] += bonus
            out["vol_flag"] = (out["vol_flag"] or "VOL_OK") + "+SPIKE"
        elif ch1h >= jump and vol >= 500_000:
            out["vol_score"] += 0.04
            out["vol_flag"] = (out["vol_flag"] or "VOL_OK") + "+JUMP"

        if fetch_ob and self.feeder and hasattr(self.feeder, "blofin"):
            try:
                ob = self.feeder.blofin.fetch_order_book(symbol, size=12)
            except Exception:
                ob = {}
            if ob:
                out["ob"] = ob
                imb = ob.get("ob_imbalance") or 0
                if imb > 0.2:
                    out["vol_score"] += 0.08
                elif imb < -0.2:
                    out["vol_score"] -= 0.08  # dla long bedzie kara, short bonus w scoringu
                if (ob.get("ob_spread_pct") or 0) > 0.15:
                    out["vol_score"] -= 0.05  # szeroki spread = plycej
        return out

    def detect_anomaly(self, coin: Dict) -> Dict:
        change_1h = abs(coin.get("change_1h") or 0)
        change_24h = abs(coin.get("change_24h") or 0)

        anomaly = {
            "is_price_jump": False,
            "is_volume_spike": False,
            "jump_strength": 0.0,
            "direction": None,
        }

        if change_1h >= config.PRICE_JUMP_THRESHOLD * 100:
            anomaly["is_price_jump"] = True
            anomaly["jump_strength"] = min(change_1h / 8, 1.0)
            anomaly["direction"] = "up" if coin["change_1h"] > 0 else "down"
        elif change_24h >= config.PRICE_JUMP_THRESHOLD * 100 * 2.0:
            anomaly["is_price_jump"] = True
            anomaly["jump_strength"] = min(change_24h / 14, 1.0)
            anomaly["direction"] = "up" if coin["change_24h"] > 0 else "down"

        if coin.get("market_cap") and coin["market_cap"] > 0:
            ratio = coin["volume_24h"] / coin["market_cap"]
            if ratio > 0.20:
                anomaly["is_volume_spike"] = True

        return anomaly

    def multi_timeframe_bias(self, coin: Dict) -> Dict:
        c1h = coin.get("change_1h") or 0
        c24h = coin.get("change_24h") or 0
        c7d = coin.get("change_7d") or 0
        c30d = coin.get("change_30d") or 0

        scores = {"1h": 0, "24h": 0, "7d": 0, "30d": 0}
        if c1h > 1.2: scores["1h"] = 1
        elif c1h < -1.2: scores["1h"] = -1
        if c24h > 2.5: scores["24h"] = 1
        elif c24h < -2.5: scores["24h"] = -1
        if c7d > 5: scores["7d"] = 1
        elif c7d < -5: scores["7d"] = -1
        if c30d > 12: scores["30d"] = 1
        elif c30d < -12: scores["30d"] = -1

        total = sum(scores.values())
        if total >= 2:
            bias = "BULLISH"
        elif total <= -2:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

        return {"bias": bias, "score": total, "details": scores}

    def support_resistance(self, coin: Dict) -> Dict:
        price = coin["price"]
        high = coin.get("high_24h") or price
        low = coin.get("low_24h") or price
        dist_to_high = ((high - price) / price * 100) if price > 0 else 0
        dist_to_low = ((price - low) / price * 100) if price > 0 else 0
        return {
            "high_24h": high,
            "low_24h": low,
            "dist_to_high": round(dist_to_high, 2),
            "dist_to_low": round(dist_to_low, 2),
            "near_resistance": dist_to_high < 1.8,
            "near_support": dist_to_low < 1.8
        }

    def get_rsi_macd(self, coin: Dict) -> Dict:
        """
        RSI/MACD na BloFin (spójne z venue wykonania).
        Binance tylko jako opcjonalne porównanie w cache meta — NIE primary.
        """
        import time
        symbol = coin["symbol"]
        if not self.feeder:
            return {"rsi": None, "macd": None, "combined": "neutral", "rsi_signal": "neutral", "macd_signal": "neutral"}

        if symbol in self.indicator_cache:
            ts, data = self.indicator_cache[symbol]
            if time.time() - ts < float(getattr(config, "INDICATOR_CACHE_SECONDS", 12) or 12):
                return data

        closes = []
        source = "NONE"
        # 1) BloFin 1H — PRIMARY
        try:
            if hasattr(self.feeder, "blofin"):
                closes = self.feeder.blofin.fetch_klines_closes(symbol, bar="1H", limit=50) or []
                if len(closes) >= 30:
                    source = "BLOFIN"
        except Exception as e:
            _se_log(f"rsi_macd BF {symbol}", e)
            closes = []

        # 2) Bez cichego Binance/Bybit/CG do scoringu — brak danych = neutral
        # (strategia i tak idzie z evaluate_strategy_tf na BloFin)
        if len(closes) < 30:
            result = {
                "rsi": None, "macd": None, "combined": "neutral",
                "rsi_signal": "neutral", "macd_signal": "neutral",
                "source": "NONE",
            }
        else:
            result = analyze_rsi_macd(closes)
            result["source"] = source

        self.indicator_cache[symbol] = (time.time(), result)
        return result

    def calculate_score(self, coin: Dict, btc_change_24h: float, use_indicators: bool = False) -> Dict:
        rs = coin.get("residual_momentum_24h")
        if rs is None:
            rs = (coin.get("change_24h") or 0) - btc_change_24h
        mtf = self.multi_timeframe_bias(coin)
        sr = self.support_resistance(coin)
        anomaly = self.detect_anomaly(coin)
        source_div = analyze_source_divergence(coin)
        blofin_only = bool(coin.get("blofin_only"))
        vol_info = self.analyze_volume_ob(coin, coin["symbol"], fetch_ob=False)


        # RSI/MACD tylko dla coinów które już mają jakiś momentum (oszczędność API)
        ind = {"rsi": None, "macd": None, "combined": "neutral", "rsi_signal": "neutral", "macd_signal": "neutral"}
        if use_indicators and abs(coin.get("change_24h") or 0) > 1.5:
            ind = self.get_rsi_macd(coin)

        score_long = 0.0
        score_short = 0.0
        reasons = []
        entry_hint = None
        exit_hint = None

        # Relative Strength
        if rs > 1.8:
            score_long += 0.18
            reasons.append(f"RS+{rs:.1f}%")
        elif rs < -1.8:
            score_short += 0.18
            reasons.append(f"RS{rs:.1f}%")

        # Multi-timeframe – główny głos klastra trendu (13: nie dublować z TREND/ST/EMA/BTC)
        if mtf["bias"] == "BULLISH":
            score_long += 0.14 + (mtf["score"] * 0.02)
            reasons.append(f"MTF_BULL({mtf['score']})")
        elif mtf["bias"] == "BEARISH":
            score_short += 0.14 + (abs(mtf["score"]) * 0.02)
            reasons.append(f"MTF_BEAR({mtf['score']})")

        # Momentum
        c1h = coin.get("change_1h") or 0
        c24h = coin.get("change_24h") or 0
        # 1h tylko lekki timing (trend mode: nie glowny driver)
        if c1h > 1.5:
            score_long += 0.04
            reasons.append(f"1h+{c1h:.1f}%")
        elif c1h < -1.5:
            score_short += 0.04
            reasons.append(f"1h{c1h:.1f}%")
        if c24h > 3.0:
            score_long += 0.10
            reasons.append(f"24h+{c24h:.1f}%")
        elif c24h < -3.0:
            score_short += 0.10
            reasons.append(f"24h{c24h:.1f}%")

        # Support / Resistance
        if sr["near_support"] and mtf["bias"] != "BEARISH":
            score_long += 0.10
            reasons.append("NEAR_SUPPORT")
            entry_hint = f"Long blisko wsparcia ${sr['low_24h']:.4f}"
        if sr["near_resistance"] and mtf["bias"] != "BULLISH":
            score_short += 0.10
            reasons.append("NEAR_RESISTANCE")
            entry_hint = f"Short blisko oporu ${sr['high_24h']:.4f}"

        # === RSI / MACD ===
        if ind["rsi"] is not None:
            if ind["rsi_signal"] == "oversold":
                score_long += 0.15
                reasons.append(f"RSI_OVERSOLD({ind['rsi']})")
            elif ind["rsi_signal"] == "overbought":
                score_short += 0.15
                reasons.append(f"RSI_OVERBOUGHT({ind['rsi']})")
            elif ind["rsi_signal"] == "bullish":
                score_long += 0.08
                reasons.append(f"RSI({ind['rsi']})")
            elif ind["rsi_signal"] == "bearish":
                score_short += 0.08
                reasons.append(f"RSI({ind['rsi']})")

        if ind.get("macd"):
            if ind["macd_signal"] == "bullish_cross":
                score_long += 0.14
                reasons.append("MACD_BULL_CROSS")
            elif ind["macd_signal"] == "bearish_cross":
                score_short += 0.14
                reasons.append("MACD_BEAR_CROSS")
            elif ind["macd_signal"] == "bullish":
                score_long += 0.06
                reasons.append("MACD_BULL")
            elif ind["macd_signal"] == "bearish":
                score_short += 0.06
                reasons.append("MACD_BEAR")

        # Anomalie
        if anomaly["is_price_jump"]:
            if anomaly["direction"] == "up":
                score_long += 0.14 * anomaly["jump_strength"]
                reasons.append("JUMP_UP")
            else:
                score_short += 0.14 * anomaly["jump_strength"]
                reasons.append("JUMP_DOWN")
        if anomaly["is_volume_spike"]:
            if score_long > score_short:
                score_long += 0.07
                reasons.append("VOL_SPIKE")
            elif score_short > score_long:
                score_short += 0.07
                reasons.append("VOL_SPIKE")

        score_long = min(score_long, 1.0)
        score_short = min(score_short, 1.0)

        # === Filtry RSI ===
        rsi_val = ind.get("rsi")
        rsi_sig = ind.get("rsi_signal") or "neutral"
        if rsi_val is not None:
            # Ekstrema: mocny blok
            if rsi_val < 25 and score_short > score_long:
                score_short *= 0.35
                reasons.append("RSI_BLOCK_SHORT")
            if rsi_val > 75 and score_long > score_short:
                score_long *= 0.35
                reasons.append("RSI_BLOCK_LONG")
            # Strefy: nie longuj gdy RSI bearish/overbought, nie shortuj gdy bullish/oversold
            if rsi_sig in ("overbought", "bearish") and score_long > score_short:
                score_long *= 0.55
                reasons.append("RSI_AGAINST_LONG")
            if rsi_sig in ("oversold", "bullish") and score_short > score_long:
                score_short *= 0.55
                reasons.append("RSI_AGAINST_SHORT")

        # === Filtr MACD - nie idz przeciwko silnemu MACD ===
        macd_sig = ind.get("macd_signal") or "neutral"
        if macd_sig in ("bearish_cross", "bearish") and score_long > score_short:
            score_long *= 0.45
            reasons.append("MACD_BLOCK_LONG")
        if macd_sig in ("bullish_cross", "bullish") and score_short > score_long:
            score_short *= 0.45
            reasons.append("MACD_BLOCK_SHORT")

        # Rozjazd zrodel - ostroznosc przy wysokiej dywergencji
        if source_div.get("warning") and 1.5 <= source_div.get("max_diff_pct", 0) <= 15 and not source_div.get("symbol_mismatch"):
            div_m = float(getattr(config, "SRC_DIVERGENCE_SCORE_MULT", 0.95))
            score_long *= div_m
            score_short *= div_m
            reasons.append(f"SRC_DIVERGENCE({source_div['max_diff_pct']:.1f}%)")

        # Wolumen
        vs = vol_info.get("vol_score") or 0
        if vs:
            score_long += vs
            score_short += vs  # thin volume kara dla obu; high vol bonus
            if vol_info.get("vol_flag"):
                reasons.append(vol_info["vol_flag"])

        # Trend monety – część klastra trendu (nie osobny „głos” obok MTF/ST/EMA)
        # 13. bez podwójnego liczenia: mniejsza waga, flaga cluster
        trend = coin.get("trend") or "SIDEWAYS"
        trend_cluster_long = 0.0
        trend_cluster_short = 0.0
        if trend in ("STRONG_UP", "UP"):
            trend_cluster_long = 0.04 if trend == "UP" else 0.06
            reasons.append(f"TREND_{trend}")
        elif trend in ("STRONG_DOWN", "DOWN"):
            trend_cluster_short = 0.04 if trend == "DOWN" else 0.06
            reasons.append(f"TREND_{trend}")
        # MTF już dodał 0.16 – nie sumujemy full TREND niezależnie
        score_long += trend_cluster_long
        score_short += trend_cluster_short

        # Konkretne poziomy TP / SL w cenie
        price = coin["price"]
        if score_long >= config.MIN_SIGNAL_STRENGTH and score_long > score_short:
            direction = "LONG"
            strength = score_long
            # TP/SL w cenie (uwzględniając dźwignię w % ruchu ceny)
            # Przy x3: +9% na pozycji = +3% ruchu ceny
            tp_price = price * (1 + config.TAKE_PROFIT_PCT / 100 / config.LEVERAGE)
            sl_price = price * (1 + config.STOP_LOSS_PCT / 100 / config.LEVERAGE)
            if not entry_hint:
                entry_hint = f"Long @ ${price:.6f}"
            exit_hint = f"TP: ${tp_price:.6f} (+{config.TAKE_PROFIT_PCT}%) | SL: ${sl_price:.6f} ({config.STOP_LOSS_PCT}%)"
        elif score_short >= config.MIN_SIGNAL_STRENGTH and score_short > score_long:
            direction = "SHORT"
            strength = score_short
            tp_price = price * (1 - config.TAKE_PROFIT_PCT / 100 / config.LEVERAGE)
            sl_price = price * (1 - config.STOP_LOSS_PCT / 100 / config.LEVERAGE)
            if not entry_hint:
                entry_hint = f"Short @ ${price:.6f}"
            exit_hint = f"TP: ${tp_price:.6f} (+{config.TAKE_PROFIT_PCT}%) | SL: ${sl_price:.6f} ({config.STOP_LOSS_PCT}%)"
        else:
            direction = "NEUTRAL"
            strength = max(score_long, score_short)
            tp_price = None
            sl_price = None
            entry_hint = None
            exit_hint = None

        # 12. kalibracja strength → expected R
        try:
            from strength_calibration import get_calibrator
            _cal = get_calibrator()
            _er = _cal.expected_r(strength)
            _lab = _cal.label(strength)
            _cal_meta = _cal.calibration_meta(strength)
        except Exception:
            _er, _lab, _cal_meta = None, None, {"status": "UNAVAILABLE", "n": 0}

        # Trend score ≠ Reversal score (PRIORYTET 5)
        # strength = uniwersalne pole do risk/UI, ale źródło to trend_score
        _trend_score = round(min(float(strength), 1.0), 3)
        return {
            "symbol": coin["symbol"],
            "id": coin.get("id"),
            "name": coin.get("name", ""),
            "direction": direction,
            "engine": "trend",
            "score_type": "trend",
            "trend_score": _trend_score,
            "reversal_score": None,  # tylko silnik reversal wypełnia
            "strength": _trend_score,  # alias → risk_manager / UI
            "expected_r": _er,
            "strength_label": _lab,
            "expected_r_calibration": _cal_meta,
            "expected_r_status": _cal_meta.get("status"),
            "score_components": {
                "mtf": mtf,
                "trend": coin.get("trend", "SIDEWAYS"),
                "rsi": ind.get("rsi"),
                "macd": ind.get("macd_signal"),
                "volume": vol_info.get("vol_flag"),
                "rs": round(rs, 2),
                "liquidity_src_div": source_div.get("max_diff_pct") if isinstance(source_div, dict) else None,
            },
            "reasons": reasons,
            "price": price,
            "rs": round(rs, 2),
            "residual_momentum_24h": round(float(rs or 0), 2),
            "benchmark_return_24h": coin.get("benchmark_return_24h"),
            "market_median_return_24h": coin.get("market_median_return_24h"),
            "mtf": mtf,
            "sr": sr,
            "anomaly": anomaly,
            "rsi": ind.get("rsi"),
            "macd": ind.get("macd"),
            "rsi_signal": ind.get("rsi_signal"),
            "macd_signal": ind.get("macd_signal"),
            "entry_hint": entry_hint,
            "exit_hint": exit_hint,
            "tp_price": tp_price,
            "sl_price": sl_price,
            "change_1h": round(c1h, 2),
            "change_24h": round(c24h, 2),
            "change_7d": round(coin.get("change_7d") or 0, 2),
            "volume_24h": coin.get("volume_24h") or 0,
            "source_div": source_div,
            "trend": coin.get("trend", "SIDEWAYS"),
            "trend_label_score": coin.get("trend_score", 0),  # etykieta UP/DOWN, nie engine score
            "categories": coin.get("categories") or [],
            "sectors": coin.get("sectors") or [],
            "blofin_only": bool(coin.get("blofin_only")),
            "vol_flag": vol_info.get("vol_flag"),
        }


    def decision_breakdown(self, s: Dict) -> Dict:
        """
        Rozbija sygnal na: ZA / PRZECIW / WYNIK decyzji.
        Do panelu Analiza w UI.
        """
        import config as cfg
        fors, against = [], []
        direction = s.get("direction") or "NEUTRAL"
        strength = float(s.get("strength") or 0)
        reasons = list(s.get("reasons") or [])

        # Siła
        if strength >= cfg.MIN_SIGNAL_STRENGTH:
            fors.append(f"Siła {strength:.2f} ≥ min {cfg.MIN_SIGNAL_STRENGTH}")
        else:
            against.append(f"Siła {strength:.2f} < min {cfg.MIN_SIGNAL_STRENGTH}")

        # RSI / MACD z powodów
        for r in reasons:
            ru = str(r).upper()
            if "RSI" in ru or "MACD" in ru or "MTF_" in ru or "TREND_" in ru or "STRAT_PRIMARY_OK" in ru or "CIPHER_" in ru:
                fors.append(str(r))
            if "STRAT_PRIMARY_FAIL" in ru or "STRAT_PRIMARY_CONFLICT" in ru:
                against.append(str(r))
            if "CIPHER_VS_" in ru:
                against.append(str(r))
            if "LOW_VOLUME" in ru or "WIDE_SPREAD" in ru or "SRC_DIVERGENCE" in ru or "BLOFIN_ONLY" in ru:
                against.append(str(r))
            if "FUNDING" in ru or "OB_" in ru or "VOL_" in ru:
                fors.append(str(r))

        strat = s.get("strategy") or {}
        if strat:
            if strat.get("pass") and strat.get("direction") == direction:
                fors.append(f"Strategia 1H: PASS {strat.get('direction')} ADX={strat.get('adx')} ST={strat.get('supertrend')} RSI={strat.get('rsi')}")
            elif strat.get("pass") and strat.get("direction") != direction:
                against.append(f"Strategia 1H: konflikt ({strat.get('direction')} vs {direction})")
            else:
                against.append(f"Strategia 1H: FAIL (pass=False)")
            for sr in (strat.get("reasons") or [])[:5]:
                if str(sr).startswith("OK") or "pass" in str(sr).lower():
                    fors.append(f"1H: {sr}")
                else:
                    against.append(f"1H: {sr}")

        mtf = s.get("mtf") or {}
        by_tf = mtf.get("by_tf") or {}
        for tf, info in by_tf.items():
            d = info.get("direction")
            p = info.get("pass")
            if not d:
                continue
            line = f"{tf}: {d} {'PASS' if p else 'FAIL'} RSI={info.get('rsi')} ADX={info.get('adx')}"
            if p and d == direction:
                fors.append(line)
            elif p and d != direction:
                against.append(line)
            else:
                against.append(line)

        # Liquidity / OB numbers
        vol = s.get("volume_24h")
        if vol is not None:
            min_vol = float(getattr(cfg, "MIN_VOLUME_24H_USD", 0) or 0)
            if min_vol and vol < min_vol:
                against.append(f"Volume 24h ${vol:,.0f} < min ${min_vol:,.0f}")
            elif vol:
                fors.append(f"Volume 24h ${vol:,.0f}")

        ob = s.get("order_book") or {}
        if ob.get("ob_spread_pct") is not None:
            sp = float(ob["ob_spread_pct"])
            max_sp = float(getattr(cfg, "MAX_SPREAD_PCT", 0.12) or 0.12)
            if sp > max_sp:
                against.append(f"Spread {sp:.3f}% > max {max_sp}%")
            else:
                fors.append(f"Spread {sp:.3f}% OK")
        if ob.get("ob_imbalance") is not None:
            fors.append(f"OB imbalance {ob.get('ob_imbalance')} bias={ob.get('ob_bias')}")

        sd = s.get("source_div") or {}
        if isinstance(sd, dict) and sd.get("max_diff_pct") is not None:
            md = float(sd.get("max_diff_pct") or 0)
            max_div = float(getattr(cfg, "MAX_SOURCE_DIVERGENCE_PCT", 1.5) or 1.5)
            if md >= max_div and sd.get("warning"):
                against.append(f"Rozjazd źródeł {md:.2f}% ≥ {max_div}%")
            else:
                fors.append(f"Rozjazd źródeł {md:.2f}%")

        # Final decision hint
        reject = s.get("reject_reason")
        decision = "CANDIDATE"
        decision_why = "Spełnia warunki scoringu – do weryfikacji risk managera"
        if direction == "NEUTRAL":
            decision = "SKIP"
            decision_why = "Kierunek NEUTRAL – za słaby / mieszany scoring"
        elif reject:
            decision = "REJECT"
            decision_why = str(reject)
        elif strength < cfg.MIN_SIGNAL_STRENGTH:
            decision = "REJECT"
            decision_why = f"Siła poniżej progu ({strength:.2f})"
        elif getattr(cfg, "REQUIRE_STRATEGY_1H", True) and not getattr(cfg, "AGGRESSIVE_MODE", False):
            if not (strat and strat.get("pass") and strat.get("direction") == direction):
                decision = "REJECT"
                decision_why = "Brak potwierdzenia strategii 1H"
            else:
                decision = "OPEN_OK"
                decision_why = "Sygnał + strategia 1H OK – kwalifikuje się do otwarcia"
        else:
            decision = "OPEN_OK"
            decision_why = "Sygnał kwalifikuje się (tryb bez twardego 1H / aggressive)"

        # dedupe preserving order
        def dedupe(xs):
            seen = set()
            out = []
            for x in xs:
                if x not in seen:
                    seen.add(x)
                    out.append(x)
            return out

        # Mapowanie decyzji na etykiety UI
        ui_decision = {
            "OPEN_OK": "KANDYDAT",
            "CANDIDATE": "KANDYDAT",
            "REJECT": "ODRZUCONY",
            "SKIP": "POMINIĘTY",
        }.get(decision, decision)

        return {
            "symbol": s.get("symbol"),
            "direction": direction,
            "strength": round(strength, 4),
            "price": s.get("price"),
            "change_1h": s.get("change_1h"),
            "change_24h": s.get("change_24h"),
            "change_7d": s.get("change_7d"),
            "rsi": s.get("rsi"),
            "macd": s.get("macd_signal") or s.get("macd"),
            "trend": s.get("trend"),
            "atr_pct": s.get("atr_pct"),
            "volume_24h": s.get("volume_24h"),
            "vol_flag": s.get("vol_flag"),
            "strategy_pass": strat.get("pass") if strat else None,
            "strategy_direction": strat.get("direction") if strat else None,
            "strategy_adx": strat.get("adx") if strat else None,
            "tp_price": s.get("tp_price"),
            "sl_price": s.get("sl_price"),
            "reject_reason": reject,
            "mtf_summary": {
                tf: f"{(v or {}).get('direction') or '—'}/{'✓' if (v or {}).get('pass') else '✗'}"
                for tf, v in by_tf.items()
            } if by_tf else {},
            "pros": dedupe(fors)[:12],
            "cons": dedupe(against)[:12],
            "for": dedupe(fors)[:12],
            "against": dedupe(against)[:12],
            "decision": ui_decision,
            "decision_raw": decision,
            "decision_why": decision_why,
            "liquidity": s.get("liquidity") or self.compute_liquidity_score(s),
            "reasons": reasons[:10],
            "indicators": {
                "rsi": s.get("rsi"),
                "macd": s.get("macd_signal"),
                "trend": s.get("trend"),
                "atr": s.get("atr"),
                "atr_pct": s.get("atr_pct"),
                "price": s.get("price"),
                "change_1h": s.get("change_1h"),
                "change_24h": s.get("change_24h"),
                "rs": s.get("rs"),
                "volume_24h": s.get("volume_24h"),
                "strategy_adx": strat.get("adx") if strat else None,
                "strategy_st": strat.get("supertrend") if strat else None,
                "mtf_long_votes": mtf.get("long_votes"),
                "mtf_short_votes": mtf.get("short_votes"),
                "cipher_b": s.get("cipher_b") or (strat.get("cipher_b") if strat else None),
            },
            "cipher_b": s.get("cipher_b") or (strat.get("cipher_b") if strat else None),
        }


    def build_decision_card(self, s: Dict) -> Dict:
        """
        Karta analizy: za / przeciw / decyzja.
        Uzywane w UI 'Analiza'.
        """
        pros, cons = [], []
        direction = s.get("direction") or "NEUTRAL"
        reasons = list(s.get("reasons") or [])

        # Strategy 1H
        strat = s.get("strategy") or {}
        if strat.get("pass") and strat.get("direction") == direction:
            pros.append(f"Strategia 1H OK ({strat.get('direction')})")
        elif strat.get("pass") and strat.get("direction") and strat.get("direction") != direction:
            cons.append(f"Strategia 1H konflikt: {strat.get('direction')} vs {direction}")
        elif strat and not strat.get("pass"):
            cons.append("Strategia 1H FAIL")
        for r in (strat.get("reasons") or [])[:4]:
            if strat.get("pass"):
                pros.append(f"1H: {r}")
            else:
                cons.append(f"1H: {r}")

        # MTF
        mtf = s.get("mtf") or {}
        by_tf = mtf.get("by_tf") or {}
        for tf, info in by_tf.items():
            d = info.get("direction")
            ok = info.get("pass")
            if not d:
                continue
            line = f"{tf}: {d}" + (" ✓" if ok else " ✗")
            if d == direction and ok:
                pros.append(line)
            elif d and d != direction:
                cons.append(line)
            elif not ok:
                cons.append(line)

        if mtf.get("hold_long") and direction == "LONG":
            pros.append("MTF HOLD (wyższe TF long)")
        if mtf.get("hold_short") and direction == "SHORT":
            pros.append("MTF HOLD (wyższe TF short)")

        # Indicators from signal
        rsi = s.get("rsi")
        if rsi is not None:
            try:
                rsi = float(rsi)
                if direction == "LONG":
                    if 40 <= rsi <= 65:
                        pros.append(f"RSI {rsi:.1f} (strefa long)")
                    elif rsi < 35:
                        pros.append(f"RSI {rsi:.1f} (wyjście z wyprzedania)")
                    elif rsi > 75:
                        cons.append(f"RSI {rsi:.1f} (przegrzanie)")
                    else:
                        cons.append(f"RSI {rsi:.1f}")
                elif direction == "SHORT":
                    if 35 <= rsi <= 60:
                        pros.append(f"RSI {rsi:.1f} (strefa short)")
                    elif rsi > 65:
                        pros.append(f"RSI {rsi:.1f} (wysoki – short bias)")
                    elif rsi < 25:
                        cons.append(f"RSI {rsi:.1f} (wyprzedanie)")
                    else:
                        cons.append(f"RSI {rsi:.1f}")
            except (TypeError, ValueError):
                pass

        macd = s.get("macd_signal") or s.get("macd")
        if macd:
            m = str(macd).lower()
            if direction == "LONG" and ("bull" in m or "up" in m):
                pros.append(f"MACD {macd}")
            elif direction == "SHORT" and ("bear" in m or "down" in m):
                pros.append(f"MACD {macd}")
            elif "bull" in m or "bear" in m:
                cons.append(f"MACD {macd}")

        # Volume / OB
        vf = s.get("vol_flag")
        if vf:
            if "HIGH" in str(vf) or "SPIKE" in str(vf):
                pros.append(f"Wolumen: {vf}")
            elif "THIN" in str(vf) or "LOW" in str(vf):
                cons.append(f"Wolumen: {vf}")
            else:
                pros.append(f"Wolumen: {vf}")

        ob = s.get("order_book") or {}
        if ob.get("ob_bias"):
            bias = str(ob.get("ob_bias")).upper()
            if direction == "LONG" and "BID" in bias:
                pros.append(f"OB bias {bias} ({ob.get('ob_imbalance')})")
            elif direction == "SHORT" and "ASK" in bias:
                pros.append(f"OB bias {bias} ({ob.get('ob_imbalance')})")
            elif bias:
                cons.append(f"OB bias {bias}")

        if ob.get("ob_spread_pct") is not None:
            try:
                sp = float(ob["ob_spread_pct"])
                max_sp = float(getattr(config, "MAX_SPREAD_PCT", 0.12) or 0.12)
                if sp <= max_sp:
                    pros.append(f"Spread {sp:.3f}% OK")
                else:
                    cons.append(f"Spread {sp:.3f}% za szeroki")
            except (TypeError, ValueError):
                pass

        # Source divergence
        sd = s.get("source_div") or {}
        if isinstance(sd, dict) and sd.get("max_diff_pct") is not None:
            try:
                md = float(sd.get("max_diff_pct") or 0)
                if md >= float(getattr(config, "MAX_SOURCE_DIVERGENCE_PCT", 1.5)):
                    cons.append(f"Rozjazd źródeł {md:.2f}%")
                elif md > 0.5:
                    cons.append(f"Rozjazd źródeł {md:.2f}% (ostrożnie)")
                else:
                    pros.append(f"Źródła zgodne ({md:.2f}%)")
            except (TypeError, ValueError):
                pass

        # Momentum / RS from reasons
        for r in reasons:
            ru = str(r).upper()
            if any(x in ru for x in ("RS+", "MTF_BULL", "MTF_LONG", "24H+", "1H+", "MACD_BULL", "BULL", "TREND_UP", "TREND_STRONG_UP", "FUNDING", "STRAT_PRIMARY_OK", "ATR_SL", "MTF_HOLD")):
                if r not in pros and not any(r in p for p in pros):
                    if "FAIL" in ru or "CONFLICT" in ru or "DIVERGENCE" in ru or "LOW_VOLUME" in ru or "WIDE_SPREAD" in ru:
                        cons.append(r)
                    else:
                        pros.append(r)
            elif any(x in ru for x in ("RS-", "MTF_BEAR", "MTF_SHORT", "24H-", "FAIL", "CONFLICT", "DIVERGENCE", "LOW_VOLUME", "WIDE_SPREAD", "THIN", "OVERSOLD", "OVERBOUGHT", "REJECT")):
                if r not in cons:
                    cons.append(r)

        reject = s.get("reject_reason")
        if reject:
            cons.append(f"FILTR: {reject}")

        strength = float(s.get("strength") or 0)
        min_s = float(getattr(config, "MIN_SIGNAL_STRENGTH", 0.62))
        if strength < min_s:
            cons.append(f"Siła {strength:.2f} < min {min_s:.2f}")
        else:
            pros.append(f"Siła {strength:.2f} ≥ min {min_s:.2f}")

        # Decision summary
        if direction == "NEUTRAL":
            decision = "POMINIĘTY"
            decision_why = "Brak kierunku (NEUTRAL)"
        elif reject:
            decision = "ODRZUCONY"
            decision_why = str(reject)
        elif strength < min_s:
            decision = "ODRZUCONY"
            decision_why = f"Za słaby sygnał ({strength:.2f})"
        elif strat and not strat.get("pass") and getattr(config, "REQUIRE_STRATEGY_1H", True) and not getattr(config, "AGGRESSIVE_MODE", False):
            decision = "ODRZUCONY"
            decision_why = "Brak potwierdzenia strategii 1H"
        elif len(cons) > len(pros) + 1:
            decision = "SŁABY"
            decision_why = "Więcej argumentów przeciw niż za"
        else:
            decision = "KANDYDAT"
            decision_why = "Spełnia warunki scoringu – może wejść jeśli slot/risk OK"

        return {
            "symbol": s.get("symbol"),
            "direction": direction,
            "strength": round(strength, 4),
            "price": s.get("price"),
            "change_1h": s.get("change_1h"),
            "change_24h": s.get("change_24h"),
            "change_7d": s.get("change_7d"),
            "rsi": s.get("rsi"),
            "macd": s.get("macd_signal") or s.get("macd"),
            "trend": s.get("trend"),
            "volume_24h": s.get("volume_24h"),
            "vol_flag": s.get("vol_flag"),
            "atr_pct": s.get("atr_pct"),
            "strategy_pass": strat.get("pass") if strat else None,
            "strategy_direction": strat.get("direction") if strat else None,
            "strategy_adx": strat.get("adx") if strat else None,
            "mtf_summary": {tf: f"{(v or {}).get('direction') or '—'}/{'✓' if (v or {}).get('pass') else '✗'}"
                            for tf, v in by_tf.items()} if by_tf else {},
            "pros": pros[:12],
            "cons": cons[:12],
            "decision": decision,
            "decision_why": decision_why,
            "reject_reason": reject,
            "reasons": reasons[:10],
            "tp_price": s.get("tp_price"),
            "sl_price": s.get("sl_price"),
        }

    def _cross_sectional_zscores(self, coins: List[Dict]) -> Dict[str, float]:
        """Z-score change_24h względem uniwersum (cross-sectional)."""
        vals = []
        for c in coins:
            try:
                v = float(c.get("change_24h") or 0)
                vals.append(v)
            except (TypeError, ValueError):
                pass
        if len(vals) < 10:
            return {}
        mean = sum(vals) / len(vals)
        var = sum((x - mean) ** 2 for x in vals) / max(len(vals) - 1, 1)
        std = var ** 0.5
        if std < 1e-9:
            return {}
        out = {}
        for c in coins:
            try:
                v = float(c.get("change_24h") or 0)
                out[c["symbol"]] = (v - mean) / std
            except (TypeError, ValueError, KeyError):
                continue
        return out

    def generate_signals(self, coins: List[Dict], btc_change_24h: float) -> List[Dict]:
        strategy_mode = str(getattr(config, "STRATEGY_MODE", "DAYTRADING") or "DAYTRADING").upper()
        # 21.08.2026: bramka poszerzona o "DAYTRADING_V2" - config zaczal
        # ustawiac STRATEGY_MODE na ta wartosc wprost (nie tylko przez
        # DAYTRADING_V2_ENABLED), a bramka porownywala tylko do literalnego
        # "DAYTRADING" - w efekcie cala galaz V1/V2 nigdy sie nie wykonywala,
        # bot spadal do silnika "trend" bez wzgledu na DAYTRADING_V2_ENABLED.
        if strategy_mode in ("DAYTRADING", "DAYTRADING_V2"):
            try:
                regime_info = self.detect_market_regime(btc_change_24h) if getattr(config, "REGIME_ENABLED", True) else {"regime": "UNKNOWN"}
            except Exception:
                regime_info = {"regime": "UNKNOWN"}
            self.last_regime = regime_info

            # Przelacznik silnika V1/V2 (plan hierarchii timeframe, 20.08.2026).
            # V1 (DayTradingEngine) pozostaje domyslny i w pelni nietkniety -
            # V2 wlacza sie przez DAYTRADING_V2_ENABLED LUB przez
            # STRATEGY_MODE="DAYTRADING_V2" wprost (dowolne z nich wystarczy).
            v2_enabled = bool(getattr(config, "DAYTRADING_V2_ENABLED", False)) or strategy_mode == "DAYTRADING_V2"
            if v2_enabled:
                from daytrading_engine_v2 import DayTradingEngineV2
                engine = getattr(self, "daytrading_engine_v2", None)
                if engine is None or engine.feeder is not self.feeder:
                    engine = DayTradingEngineV2(self.feeder)
                    self.daytrading_engine_v2 = engine
                final = engine.generate(coins)
            else:
                from daytrading_engine import DayTradingEngine
                engine = getattr(self, "daytrading_engine", None)
                if engine is None or engine.feeder is not self.feeder:
                    engine = DayTradingEngine(self.feeder)
                    self.daytrading_engine = engine
                final = engine.generate(coins)
            for signal in final:
                signal["market_regime"] = (regime_info or {}).get("regime") or "UNKNOWN"
                signal["market_regime_detail"] = regime_info
                signal["panic_trigger"] = (regime_info or {}).get("panic_trigger") or ""
                try:
                    signal["liquidity"] = self.compute_liquidity_score(signal)
                except Exception:
                    signal["liquidity"] = {"score": 0, "grade": "?", "label": "err", "notes": []}
                try:
                    from engine_router import route_signal
                    route_signal(signal)
                except Exception:
                    pass
                signal["decision_path"] = self._assign_decision_path(signal)
            try:
                from perp_context import apply_all as _apply_perp
                _apply_perp(final, feeder=self.feeder)
            except Exception as e:
                print(f"[PerpContext] apply_all: {e}")
            self.last_analysis_board = [dict(signal.get("analysis") or {}, **{
                "symbol": signal.get("symbol"), "direction": signal.get("direction"),
                "strength": signal.get("strength"), "price": signal.get("price"),
                "score": signal.get("score"), "score_components": signal.get("score_components") or {},
                "engine": signal.get("engine") or "daytrading",
                "strategy_mode": signal.get("strategy_mode") or "DAYTRADING",
                "setup": signal.get("setup"), "reject_reason": signal.get("reject_reason"),
                "expected_net_r": signal.get("expected_net_r"), "indicators": signal.get("indicators") or {},
                "trend_fib": signal.get("trend_fib") or {},
                "decision_path": signal.get("decision_path"),
            }) for signal in final]

            # Reversal engine dziala ROWNOLEGLE do daytradingu, niezaleznie od
            # STRATEGY_MODE - shadow_mode.process_reversal_signals() w app.py
            # potrzebuje sygnalow z engine=="reversal" w zwracanej licie, zeby
            # w ogole miec co sledzic. Bez tego reversal_shadow.csv jest zawsze
            # pusty w trybie DAYTRADING, niezaleznie od progow konfirmacji.
            # Doklejamy (nie mergujemy per-symbol) - merge_trend_and_reversal
            # ma logike arbitrazu specyficzna dla starcia trend-vs-reversal
            # (pump_chase/trend_blocked), nieadekwatna dla daytradingu.
            # Bezpieczne: shadow_mode.should_execute() w paper_trader.py i tak
            # blokuje realne otwarcie reversal bez pelnej konfirmacji, a
            # otwarcie pozycji na juz zajetym symbolu blokuje osobny guard.
            try:
                from reversal_engine import generate_reversal_signals
                regime_name = (regime_info or {}).get("regime") or "UNKNOWN"
                reversal_signals = generate_reversal_signals(coins, regime=regime_name, btc_change_24h=btc_change_24h)
                for rsig in reversal_signals:
                    rsig.setdefault("engine", "reversal")
                    rsig["market_regime"] = regime_name
                    rsig["strategy_mode"] = "DAYTRADING"
                self.last_analysis_board += [{
                    "symbol": s.get("symbol"), "direction": s.get("direction"),
                    "strength": s.get("strength"), "price": s.get("price"),
                    "score": round(float(s.get("strength") or 0) * 100, 1),
                    "score_components": s.get("score_components") or {},
                    "engine": "reversal", "strategy_mode": "DAYTRADING",
                    "setup": s.get("setup"), "reject_reason": s.get("reject_reason"),
                    "expected_net_r": s.get("expected_net_r"), "indicators": {},
                    "trend_fib": {}, "decision_path": s.get("decision_path"),
                } for s in reversal_signals]
                final = final + reversal_signals
                # Diagnostyka widoczna w bot_state.json (nie tylko w konsoli) -
                # zeby przy analizie logow bylo od razu widac, czy generator w
                # ogole probowal cokolwiek policzyc, czy mial wyjatek, i ile
                # coinow przeszlo przez brame "extreme move" (18%+ 24h).
                extreme_thr = float(getattr(config, "REVERSAL_MIN_24H_PCT", 18.0) or 18.0)
                extreme_count = sum(1 for c in coins if abs(float(c.get("change_24h") or 0)) >= extreme_thr)
                self.last_reversal_diag = {
                    "ok": True, "error": None, "coins_scanned": len(coins),
                    "extreme_candidates_24h": extreme_count, "extreme_threshold_pct": extreme_thr,
                    "reversal_signals_generated": len(reversal_signals),
                }
            except Exception as e:
                import traceback
                print(f"[SignalEngine] reversal_engine (daytrading mode): {e}")
                self.last_reversal_diag = {
                    "ok": False, "error": f"{type(e).__name__}: {e}",
                    "traceback": traceback.format_exc(limit=6),
                }
            return final
        signals = []
        try:
            from engine_router import universe_market_return, annotate_residual_momentum
            market_ret = universe_market_return(coins)
            for coin in coins:
                annotate_residual_momentum(coin, btc_change_24h, market_ret)
        except Exception:
            pass
        # Cross-sectional z-score 24h (działa zawsze, bez 4h)
        zmap = {}
        if getattr(config, "ZSCORE_ENABLED", True):
            try:
                zmap = self._cross_sectional_zscores(coins)
            except Exception:
                zmap = {}

        # Najpierw szybki scoring bez wskaźników
        pre_scored = []
        for coin in coins:
            if coin["symbol"] in [
                "USDT", "USDC", "DAI", "USDE", "USD1", "BFUSD", "TUSD", "FDUSD",
                "BUSD", "USDD", "GUSD", "USDP", "FRAX", "LUSD", "PYUSD",
                "EURC", "EUROC", "XAUT", "PAXG", "USTC"
            ]:
                continue
            sig = self.calculate_score(coin, btc_change_24h, use_indicators=False)
            # Z-score boost: odstający od rynku
            z = zmap.get(coin["symbol"])
            if z is not None:
                sig["zscore_24h"] = round(z, 2)
                z_min = float(getattr(config, "ZSCORE_MIN_ABS", 1.5))
                bonus_max = float(getattr(config, "ZSCORE_STRENGTH_BONUS", 0.08))
                if abs(z) >= z_min and sig.get("direction") in ("LONG", "SHORT"):
                    # LONG lubi +z, SHORT lubi -z
                    aligned = (sig["direction"] == "LONG" and z > 0) or (sig["direction"] == "SHORT" and z < 0)
                    if aligned:
                        t = min(1.0, (abs(z) - z_min) / 2.0)
                        bonus = bonus_max * t
                        sig["strength"] = min(1.0, float(sig.get("strength") or 0) + bonus)
                        sig["reasons"] = list(sig.get("reasons") or []) + [f"ZSCORE({z:+.1f})"]
            pre_scored.append((coin, sig))

        # Dla top kandydatów dociągamy RSI/MACD
        pre_scored.sort(key=lambda x: x[1]["strength"], reverse=True)
        final = []
        for i, (coin, sig) in enumerate(pre_scored):
            if i < 12 and sig["strength"] >= 0.35:  # tylko top kandydaci
                sig = self.calculate_score(coin, btc_change_24h, use_indicators=True)
            final.append(sig)

        final.sort(key=lambda x: (0 if x["direction"] == "NEUTRAL" else 1, x["strength"]), reverse=True)

        # Pelne reguly strategii (primary 4h + filtr 1d) – trend mode
        primary_tf = str(getattr(config, "STRATEGY_PRIMARY_TF", "4h") or "4h")
        filter_tf = str(getattr(config, "STRATEGY_FILTER_TF", "1d") or "1d")
        checked = 0
        for s in final:
            if s["direction"] == "NEUTRAL" and s["strength"] < 0.45:
                continue
            if checked >= 10:
                break
            try:
                strat = self.evaluate_strategy_tf(s["symbol"], tf=primary_tf)
            except Exception:
                strat = {}
            try:
                strat_d = self.evaluate_strategy_tf(s["symbol"], tf=filter_tf) if getattr(config, "REQUIRE_DAILY_ALIGN", True) else {}
            except Exception:
                strat_d = {}
            checked += 1
            # Normalizuj: error/NONE = brak strategii (nie cichy Binance)
            def _strat_ok(st):
                if not st or not isinstance(st, dict):
                    return False
                if st.get("signal_source") == "NONE" or st.get("error") == "BLOFIN_OHLCV_UNAVAILABLE":
                    return False
                if st.get("pass") is False and not st.get("direction"):
                    return False
                return True

            if not _strat_ok(strat) and primary_tf == "4h" and getattr(config, "STRATEGY_1H_PROXY", True):
                # Degraded: BloFin 1h only (nadal execution venue)
                try:
                    strat_1h = self.evaluate_strategy_tf(s["symbol"], tf="1h") or {}
                except Exception:
                    strat_1h = {}
                if _strat_ok(strat_1h):
                    strat = dict(strat_1h)
                    strat["proxy"] = "blofin_1h"
                    strat["tf_proxy"] = True
                    strat["ohlcv_source"] = strat.get("ohlcv_source") or "native_blofin_1h"
            if not _strat_ok(strat):
                s["strategy"] = None
                s["signal_source"] = (strat or {}).get("signal_source") or "NONE"
                s["reasons"] = list(s.get("reasons") or []) + ["STRAT_PRIMARY_NA", "BLOFIN_OHLCV_UNAVAILABLE"]
                s["strength"] = max(0.0, float(s.get("strength") or 0) - 0.08)
                if getattr(config, "BLOCK_ON_BLOFIN_OHLCV_FAIL", True):
                    s["reject_reason"] = s.get("reject_reason") or "BLOFIN_OHLCV_UNAVAILABLE"
                # nie continue — dalsze filtry/conf mogą dodać info; risk zablokuje
            else:
                s["strategy"] = strat
                s["signal_source"] = strat.get("signal_source") or "BLOFIN_NATIVE"
                s["strategy_daily"] = strat_d or None
                if strat.get("proxy"):
                    s["reasons"] = list(s.get("reasons") or []) + [f"STRAT_PROXY({strat.get('proxy')})"]
                s["supertrend"] = (strat.get("indicators") or {}).get("supertrend") or strat.get("supertrend")
                # VuManChu Cipher B (z primary TF)
                cipher = strat.get("cipher_b") or (strat.get("indicators") or {}).get("cipher_b")
                if cipher:
                    s["cipher_b"] = cipher
                    s["reasons"] = list(s.get("reasons") or [])
                    if cipher.get("bull_div"):
                        s["reasons"].append("CIPHER_BULL_DIV")
                        if s.get("direction") == "LONG":
                            s["strength"] = min(1.0, float(s.get("strength") or 0) + 0.05)
                    if cipher.get("bear_div"):
                        s["reasons"].append("CIPHER_BEAR_DIV")
                        if s.get("direction") == "SHORT":
                            s["strength"] = min(1.0, float(s.get("strength") or 0) + 0.05)
                    if cipher.get("cross_up"):
                        s["reasons"].append(f"CIPHER_CROSS_UP(wt1={cipher.get('wt1')})")
                    if cipher.get("cross_down"):
                        s["reasons"].append(f"CIPHER_CROSS_DOWN(wt1={cipher.get('wt1')})")
                    if cipher.get("oversold"):
                        s["reasons"].append("CIPHER_OS")
                    if cipher.get("overbought"):
                        s["reasons"].append("CIPHER_OB")
                    # konflikt dywergencji z kierunkiem
                    if s.get("direction") == "LONG" and cipher.get("bear_div"):
                        s["strength"] = max(0.0, float(s.get("strength") or 0) - 0.06)
                        s["reasons"].append("CIPHER_VS_LONG")
                    if s.get("direction") == "SHORT" and cipher.get("bull_div"):
                        s["strength"] = max(0.0, float(s.get("strength") or 0) - 0.06)
                        s["reasons"].append("CIPHER_VS_SHORT")
                # ATR z primary TF → poziomy
                lv = strat.get("levels") or {}
                ind = strat.get("indicators") or {}
                atr = lv.get("atr") or ind.get("atr")
                # native vs proxy 4H
                if strat.get("ohlcv_source"):
                    s["ohlcv_source"] = strat.get("ohlcv_source")
                if strat.get("signal_source"):
                    s["signal_source"] = strat.get("signal_source")
                if strat.get("proxy") or strat.get("ohlcv_source") == "proxy_4h":
                    s["proxy_4h"] = True
                    s["reasons"] = list(s.get("reasons") or []) + ["PROXY_4H"]
                    s["strength"] = max(0.0, float(s.get("strength") or 0) - float(getattr(config, "PROXY_4H_STRENGTH_PENALTY", 0.04)))
                if atr and s.get("price"):
                    try:
                        price = float(s["price"])
                        atr_v = float(atr)
                        atr_pct = (atr_v / price * 100.0) if price > 0 else 0.0
                        # Guard: ATR >15% ceny = prawdopodobnie zła skala / śmieciowe OHLC
                        max_atr_pct = float(getattr(config, "ATR_SL_MAX_ATR_PCT", 12.0) or 12.0)
                        if atr_pct > max_atr_pct and price > 0:
                            # zclampuj ATR do rozsądnego % ceny
                            atr_v = price * (max_atr_pct / 100.0)
                            atr_pct = max_atr_pct
                            s["reasons"] = list(s.get("reasons") or []) + [f"ATR_CLAMP({max_atr_pct}%)"]
                        s["atr"] = atr_v
                        s["atr_pct"] = atr_pct
                        if getattr(config, "USE_ATR_STOPS", True) and lv.get("sl"):
                            sl = float(lv["sl"])
                            # max dystans SL od entry (nie VELVET-style -70%)
                            max_sl_pct = float(getattr(config, "ATR_SL_MAX_DIST_PCT", 10.0) or 10.0) / 100.0
                            if s.get("direction") == "LONG":
                                floor = price * (1.0 - max_sl_pct)
                                if sl < floor:
                                    sl = floor
                                    s["reasons"] = list(s.get("reasons") or []) + ["ATR_SL_CAP"]
                            elif s.get("direction") == "SHORT":
                                ceil = price * (1.0 + max_sl_pct)
                                if sl > ceil:
                                    sl = ceil
                                    s["reasons"] = list(s.get("reasons") or []) + ["ATR_SL_CAP"]
                            s["sl_price"] = sl
                            if lv.get("tp1"):
                                s["tp1_price"] = lv["tp1"]
                            if lv.get("tp2"):
                                s["tp_price"] = lv["tp2"]
                            s["reasons"] = list(s.get("reasons") or []) + [f"ATR_SL_{primary_tf}(x{lv.get('r_multiple_sl', 2)})"]
                    except Exception as e:
                        _se_log("handler", e)
                # Filtr 1d: kierunek musi sie zgadzac; brak 1D = degraded mode (jawny)
                if getattr(config, "REQUIRE_DAILY_ALIGN", True):
                    if not strat_d:
                        s["degraded_1d"] = True
                        s["reasons"] = list(s.get("reasons") or []) + ["DEGRADED_1D"]
                        s["strength"] = max(0.0, float(s.get("strength") or 0) - float(getattr(config, "DEGRADED_1D_STRENGTH_PENALTY", 0.08)))
                        if getattr(config, "BLOCK_ON_DEGRADED_1D", False):
                            s["reject_reason"] = s.get("reject_reason") or "DEGRADED_1D"
                    else:
                        s["degraded_1d"] = False
                        d_dir = strat_d.get("direction")
                        if d_dir in ("LONG", "SHORT") and s.get("direction") in ("LONG", "SHORT"):
                            if d_dir != s["direction"]:
                                s["strength"] = max(0.0, float(s.get("strength") or 0) - 0.15)
                                s["reasons"] = list(s.get("reasons") or []) + [f"DAILY_CONFLICT({d_dir})"]
                                if getattr(config, "REQUIRE_PRIMARY_STRATEGY", True):
                                    s["reject_reason"] = s.get("reject_reason") or f"DAILY_VS_{s['direction']}"
                            else:
                                s["strength"] = min(1.0, float(s.get("strength") or 0) + 0.03)
                                s["reasons"] = list(s.get("reasons") or []) + [f"DAILY_ALIGN({d_dir})"]
            # External confirmation (Binance Tier2 + CoinGecko Tier3) — nie generuje entry
            try:
                from external_confirmation import apply_confirmation
                apply_confirmation(s, s)
            except Exception as e:
                _se_log(f"ext_confirm {s.get('symbol')}", e)

            # MTF 15m/1h/4h/1d
            try:
                mtf = self.evaluate_mtf(s["symbol"]) if getattr(config, "MTF_ENABLED", True) else {}
            except Exception:
                mtf = {}
            s["mtf"] = mtf
            if mtf:
                need = int(getattr(config, "MTF_REQUIRE_ALIGN", 2) or 2)
                lv = int(mtf.get("long_votes") or 0)
                sv = int(mtf.get("short_votes") or 0)
                if s.get("direction") == "LONG":
                    if lv >= need:
                        # 13: MTF już w score bazowym – tu tylko lekka konfirmacja
                        s["strength"] = min(1.0, s["strength"] + 0.02 + 0.01 * min(lv, 4))
                        s["reasons"] = list(s.get("reasons") or []) + [f"MTF_LONG({lv}/{need})"]
                    elif lv > 0:
                        # czesciowe dopasowanie – lekka kara gdy poniżej progu
                        s["strength"] = max(0.0, s["strength"] - 0.04)
                        s["reasons"] = list(s.get("reasons") or []) + [f"MTF_WEAK_LONG({lv}<{need})"]
                elif s.get("direction") == "SHORT":
                    if sv >= need:
                        s["strength"] = min(1.0, s["strength"] + 0.02 + 0.01 * min(sv, 4))
                        s["reasons"] = list(s.get("reasons") or []) + [f"MTF_SHORT({sv}/{need})"]
                    elif sv > 0:
                        s["strength"] = max(0.0, s["strength"] - 0.04)
                        s["reasons"] = list(s.get("reasons") or []) + [f"MTF_WEAK_SHORT({sv}<{need})"]
                if s.get("direction") == "LONG" and mtf.get("hold_long"):
                    s["reasons"] = list(s.get("reasons") or []) + ["MTF_HOLD_HTF"]
                    s["ride_hint"] = True
                if s.get("direction") == "SHORT" and mtf.get("hold_short"):
                    s["reasons"] = list(s.get("reasons") or []) + ["MTF_HOLD_HTF"]
                    s["ride_hint"] = True
            # Order book dla top kandydatow
            try:
                vob = self.analyze_volume_ob(
                    {"volume_24h": s.get("volume_24h"), "change_24h": s.get("change_24h"),
                     "blofin_volume": s.get("volume_24h")},
                    s["symbol"],
                    fetch_ob=True,
                )
                s["order_book"] = vob.get("ob")
                # Funding rate (futures)
                if getattr(__import__('config'), 'FUNDING_ENABLED', True):
                    try:
                        fr = self.feeder.blofin.fetch_funding_rate(s["symbol"]) if self.feeder else {}
                    except Exception:
                        fr = {}
                    s["funding"] = fr
                    rate = fr.get("funding_rate") or 0
                    extreme = getattr(__import__('config'), 'FUNDING_EXTREME', 0.001)
                    if abs(rate) >= extreme:
                        # dodatni funding = lonhy placa shortom → lekki bonus short
                        if s.get("direction") == "SHORT" and rate > 0:
                            s["strength"] = min(1.0, s["strength"] + 0.04)
                            s["reasons"] = list(s.get("reasons") or []) + [f"FUNDING+{rate*100:.3f}%"]
                        elif s.get("direction") == "LONG" and rate < 0:
                            s["strength"] = min(1.0, s["strength"] + 0.04)
                            s["reasons"] = list(s.get("reasons") or []) + [f"FUNDING{rate*100:.3f}%"]

                s["vol_flag"] = vob.get("vol_flag") or s.get("vol_flag")
                ob = vob.get("ob") or {}
                imb = ob.get("ob_imbalance") or 0
                if s.get("direction") == "LONG" and imb > 0.15:
                    s["strength"] = min(1.0, s["strength"] + 0.06)
                    s["reasons"] = list(s.get("reasons") or []) + [f"OB_BUY({imb:+.2f})"]
                elif s.get("direction") == "SHORT" and imb < -0.15:
                    s["strength"] = min(1.0, s["strength"] + 0.06)
                    s["reasons"] = list(s.get("reasons") or []) + [f"OB_SELL({imb:+.2f})"]
                elif s.get("direction") == "LONG" and imb < -0.25:
                    s["strength"] *= 0.85
                    s["reasons"] = list(s.get("reasons") or []) + [f"OB_AGAINST({imb:+.2f})"]
                elif s.get("direction") == "SHORT" and imb > 0.25:
                    s["strength"] *= 0.85
                    s["reasons"] = list(s.get("reasons") or []) + [f"OB_AGAINST({imb:+.2f})"]
                if (ob.get("ob_spread_pct") or 0) > 0.2:
                    s["strength"] *= 0.9
                    s["reasons"] = list(s.get("reasons") or []) + ["OB_WIDE_SPREAD"]
            except Exception:
                s["order_book"] = None
            require_pri = getattr(config, "REQUIRE_PRIMARY_STRATEGY", getattr(config, "REQUIRE_STRATEGY_1H", True))
            if not strat:
                # Brak OHLCV 4h – oznacz NA; twardy filtr w risk_manager (RANGE / MTF)
                s["reasons"] = list(s.get("reasons") or []) + ["STRAT_PRIMARY_NA"]
                s["strategy"] = None
                # lekka kara strength – bez twardego reject tutaj (decyduje risk + MTF)
                s["strength"] = max(0.0, float(s.get("strength") or 0) - 0.05)
                continue
            if strat.get("pass"):
                if strat.get("direction") == s.get("direction"):
                    # 13: primary = gate jakości; nie dubluj trendu jako +0.12
                    s["strength"] = min(1.0, float(s.get("strength") or 0) + 0.05)
                    s["reasons"] = list(s.get("reasons") or []) + ["STRAT_PRIMARY_OK"] + (strat.get("reasons") or [])[:4]
                    lv = strat.get("levels") or {}
                    if lv.get("tp2") and lv.get("sl"):
                        s["tp_price"] = lv["tp2"]
                        s["sl_price"] = lv["sl"]
                        s["tp1_price"] = lv.get("tp1")
                        s["exit_hint"] = (
                            f"TP1: ${lv.get('tp1', 0):.6f} | TP2: ${lv['tp2']:.6f} | "
                            f"SL: ${lv['sl']:.6f} (ATR x {lv.get('r_multiple_sl', 2)})"
                        )
                elif strat.get("direction") in ("LONG", "SHORT") and s.get("direction") in ("LONG", "SHORT"):
                    s["strength"] = max(0.0, float(s.get("strength") or 0) * 0.7)
                    s["reasons"] = list(s.get("reasons") or []) + ["STRAT_PRIMARY_CONFLICT"]
                    if require_pri:
                        s["reject_reason"] = s.get("reject_reason") or "STRAT_PRIMARY_CONFLICT"
            else:
                # Primary FAIL – kara, ale BEZ hard-cap strength (to zabijało 100% wejść).
                # Twardy reject tylko gdy REQUIRE_PRIMARY i brak MTF fallback.
                s["reasons"] = list(s.get("reasons") or []) + ["STRAT_PRIMARY_FAIL"]
                s["strength"] = max(0.0, float(s.get("strength") or 0) - 0.08)
                mtf = s.get("mtf") or {}
                dir_ = s.get("direction")
                mtf_ok = False
                min_votes = int(getattr(config, "MTF_MIN_VOTES_FALLBACK", 3) or 3)
                if dir_ == "LONG":
                    mtf_ok = int(mtf.get("long_votes") or 0) >= min_votes or bool(mtf.get("hold_long"))
                elif dir_ == "SHORT":
                    mtf_ok = int(mtf.get("short_votes") or 0) >= min_votes or bool(mtf.get("hold_short"))
                # soft-pass: ST+ADX z primary zgodne z kierunkiem mimo pełnego FAIL (np. RSI/VOL)
                st = str(strat.get("supertrend") or strat.get("st") or "").lower()
                adx = strat.get("adx")
                soft = False
                try:
                    adx_ok = adx is not None and float(adx) >= float(getattr(config, "ADX_SOFT_THRESHOLD", 22))
                except (TypeError, ValueError):
                    adx_ok = False
                if dir_ == "LONG" and adx_ok and st in ("up", "long", "1", "true"):
                    soft = True
                if dir_ == "SHORT" and adx_ok and st in ("down", "short", "0", "false"):
                    soft = True
                if soft:
                    s["reasons"] = list(s.get("reasons") or []) + ["STRAT_SOFT_ALIGN"]
                    s["strength"] = min(1.0, float(s.get("strength") or 0) + 0.06)
                if require_pri and not getattr(config, "AGGRESSIVE_MODE", False):
                    if mtf_ok or soft:
                        s["reasons"] = list(s.get("reasons") or []) + ["PRIMARY_MTF_FALLBACK" if mtf_ok else "PRIMARY_SOFT_PASS"]
                        # nie ustawiaj reject_reason
                    else:
                        s["reject_reason"] = s.get("reject_reason") or "STRAT_PRIMARY_FAIL"

            # --- Trend Fib: impulse → pullback 0.5–0.618 → continuation ---
            # NIE: kupuj na HIGH. TAK: czekaj retrace + potwierdzenie trendu.
            if bool(getattr(config, "TREND_FIB_PULLBACK", True)):
                try:
                    self._apply_trend_fib_pullback(s)
                except Exception as e:
                    _se_log(f"trend_fib {s.get('symbol')}", e)

        # re-sort po korekcie strength
        final.sort(key=lambda x: (0 if x["direction"] == "NEUTRAL" else 1, x["strength"]), reverse=True)


        # === Rezim rynku (BTC) ===
        regime_info = {}
        try:
            if getattr(config, "REGIME_ENABLED", True):
                regime_info = self.detect_market_regime(btc_change_24h)
        except Exception:
            regime_info = {"regime": "UNKNOWN"}
        regime = (regime_info or {}).get("regime") or "UNKNOWN"
        self.last_regime = regime_info

        for s in final:
            s["market_regime"] = regime
            s["panic_trigger"] = (regime_info or {}).get("panic_trigger") or ""
            s["regime_atr_ratio"] = (regime_info or {}).get("atr_ratio")
            s["regime_atr_percentile"] = (regime_info or {}).get("atr_percentile")
            s["regime_realized_vol"] = (regime_info or {}).get("realized_vol")
            if regime == "PANIC":
                eng = (s.get("engine") or "trend").lower()
                s["reasons"] = list(s.get("reasons") or []) + ["REGIME_PANIC"]
                if eng == "reversal":
                    # PANIC = dobre środowisko dla reversal — nie odrzucaj, lekki boost
                    s["strength"] = min(1.0, float(s.get("strength") or 0) + 0.04)
                    s["reasons"] = list(s.get("reasons") or []) + ["PANIC_REVERSAL_ACTIVE"]
                    # nie ustawiaj reject_reason
                else:
                    # Trend Engine mocno ograniczony (nie cały system)
                    s["strength"] = max(0.0, float(s.get("strength") or 0) * 0.55)
                    s["reasons"] = list(s.get("reasons") or []) + ["PANIC_TREND_LIMITED"]
                    s["_size_mult"] = min(float(s.get("_size_mult") or 1.0), 0.35)
                    # twarde odrzucenie trendu w PANIC (chyba że ekstremalnie silny)
                    min_trend_panic = float(
                        getattr(config, "REGIME_PANIC_TREND_MIN_STRENGTH", 0.72) or 0.72
                    )
                    if float(s.get("strength") or 0) < min_trend_panic:
                        s["reject_reason"] = s.get("reject_reason") or "REGIME_PANIC_TREND"
            elif regime == "RANGE":
                pen = float(getattr(config, "RANGE_STRENGTH_PENALTY", 0.08))
                s["strength"] = max(0.0, float(s.get("strength") or 0) - pen)
                s["reasons"] = list(s.get("reasons") or []) + ["REGIME_RANGE"]
                # domyślnie tylko kara; twardy blok tylko gdy BLOCK_RANGE_REGIME=True
                if getattr(config, "BLOCK_RANGE_REGIME", False):
                    s["reject_reason"] = s.get("reject_reason") or "REGIME_RANGE_BLOCK"
            elif regime == "TREND_UP" and s.get("direction") == "LONG":
                s["strength"] = min(1.0, float(s.get("strength") or 0) + 0.03)  # 13: mniejszy boost (klaster trendu)
                s["reasons"] = list(s.get("reasons") or []) + ["REGIME_TREND_UP"]
            elif regime == "TREND_DOWN" and s.get("direction") == "SHORT":
                s["strength"] = min(1.0, float(s.get("strength") or 0) + 0.03)
                s["reasons"] = list(s.get("reasons") or []) + ["REGIME_TREND_DOWN"]
            elif regime == "TREND_UP" and s.get("direction") == "SHORT":
                # Miękki counter-trend: SHORT przy BTC up tylko przy silnej slabości względnej
                rs = float(s.get("rs") or 0)
                min_rs = float(getattr(config, "COUNTER_TREND_MIN_RS", 5.0))  # RS <= -5%
                if rs <= -min_rs:
                    s["strength"] = max(0.0, float(s.get("strength") or 0) - 0.06)
                    s["reasons"] = list(s.get("reasons") or []) + [f"REGIME_VS_SHORT_SOFT(RS{rs:+.1f})"]
                    # nie ustawiaj reject_reason
                else:
                    s["strength"] = max(0.0, float(s.get("strength") or 0) - 0.12)
                    s["reasons"] = list(s.get("reasons") or []) + ["REGIME_VS_SHORT"]
                    s["reject_reason"] = s.get("reject_reason") or "REGIME_VS_SHORT"
            elif regime == "TREND_DOWN" and s.get("direction") == "LONG":
                # Miękki counter-trend: LONG przy BTC down tylko przy silnym RS
                rs = float(s.get("rs") or 0)
                min_rs = float(getattr(config, "COUNTER_TREND_MIN_RS", 5.0))  # RS >= +5%
                if rs >= min_rs:
                    s["strength"] = max(0.0, float(s.get("strength") or 0) - 0.06)
                    s["reasons"] = list(s.get("reasons") or []) + [f"REGIME_VS_LONG_SOFT(RS{rs:+.1f})"]
                else:
                    s["strength"] = max(0.0, float(s.get("strength") or 0) - 0.12)
                    s["reasons"] = list(s.get("reasons") or []) + ["REGIME_VS_LONG"]
                    s["reject_reason"] = s.get("reject_reason") or "REGIME_VS_LONG"

        # === Volume breakout vs MA (top kandydaci) ===
        checked_vol = 0
        for s in final:
            if s.get("direction") == "NEUTRAL":
                continue
            if checked_vol >= 12:
                break
            if float(s.get("strength") or 0) < 0.4:
                continue
            checked_vol += 1
            try:
                vb = self.volume_breakout_confirm(s["symbol"])
            except Exception:
                vb = {}
            s["volume_breakout"] = vb
            if vb.get("ok") is True:
                s["strength"] = min(1.0, float(s.get("strength") or 0) + 0.07)
                s["reasons"] = list(s.get("reasons") or []) + [vb.get("flag") or "VOL_BREAKOUT"]
            elif vb.get("ok") is False:
                ratio = float(vb.get("ratio") or 0)
                # bardzo niskie ratio (np. 0.04x) czesto = zly TF/jednostki – tylko info, bez ciezkiej kary
                if ratio > 0 and ratio < 0.25:
                    s["reasons"] = list(s.get("reasons") or []) + [vb.get("flag") or "VOL_WEAK"]
                else:
                    s["strength"] = max(0.0, float(s.get("strength") or 0) - 0.06)
                    s["reasons"] = list(s.get("reasons") or []) + [vb.get("flag") or "VOL_WEAK"]
                    if regime == "RANGE":
                        s["strength"] = max(0.0, float(s.get("strength") or 0) - 0.05)

        # === OB depth – TWARDE odrzucenie cienkiego booka (anti-VELVET) ===
        pen = float(getattr(config, "OB_THIN_STRENGTH_PENALTY", 0.12))
        min_depth = float(getattr(config, "OB_MIN_DEPTH_USD", 3500))
        block_thin = getattr(config, "BLOCK_OB_THIN", True)
        for s in final:
            ob = s.get("order_book") or {}
            if not ob:
                continue
            depth = ob.get("ob_depth_usd")
            thin = bool(ob.get("ob_thin"))
            if depth is not None and float(depth) < min_depth:
                thin = True
            if thin:
                s["reasons"] = list(s.get("reasons") or []) + [f"OB_THIN({depth})"]
                s["strength"] = max(0.0, float(s.get("strength") or 0) - pen)
                if block_thin:
                    s["reject_reason"] = s.get("reject_reason") or f"OB_THIN({depth})"
                    s["strength"] = min(float(s.get("strength") or 0), float(config.MIN_SIGNAL_STRENGTH) - 0.01)
            elif depth is not None:
                s["reasons"] = list(s.get("reasons") or []) + [f"OB_DEPTH({float(depth):.0f})"]

        # === Anti pump/dump chase — NIE zabija instrumentu ===
        # +X% → BLOCK TREND LONG  + CHECK SHORT REVERSAL
        # -X% → BLOCK TREND SHORT + CHECK LONG REVERSAL
        # Ekstremum zmienia rodzaj strategii, a nie kończy analizę.
        pump_lim = float(getattr(config, "BLOCK_PUMP_CHASE_PCT", 22.0) or 22.0)
        for s in final:
            if s.get("engine") == "reversal":
                continue  # reversal ma własny pipeline — nie tykamy
            ch = float(s.get("change_24h") or s.get("rs") or 0)
            reasons = list(s.get("reasons") or [])
            direction = s.get("direction")

            # ekstremalny PUMP: blokuj tylko LONG trend; włącz watch SHORT reversal
            if ch >= pump_lim:
                s["trend_blocked"] = True
                s["reversal_watch"] = "SHORT"
                s["pump_chase"] = True
                if direction == "LONG":
                    reasons.append(f"PUMP_CHASE(+{ch:.0f}%)")
                    reasons.append("TREND_LONG_BLOCKED")
                    reasons.append("REVERSAL_WATCH_SHORT")
                    # reject TYLKO dla trend LONG — nie dla całego symbolu
                    s["reject_reason"] = "TREND_BLOCK_PUMP_CHASE"
                    s["strength"] = min(
                        float(s.get("strength") or 0),
                        float(config.MIN_SIGNAL_STRENGTH) - 0.01,
                    )
                else:
                    # SHORT trend przy pumpie: nie blokuj automatycznie; reversal i tak sprawdzi
                    reasons.append(f"PUMP_EXT(+{ch:.0f}%)")
                    reasons.append("REVERSAL_WATCH_SHORT")
                s["reasons"] = reasons

            # ekstremalny DUMP: blokuj tylko SHORT trend; włącz watch LONG reversal
            elif ch <= -pump_lim:
                s["trend_blocked"] = True
                s["reversal_watch"] = "LONG"
                s["dump_chase"] = True
                if direction == "SHORT":
                    reasons.append(f"DUMP_CHASE({ch:.0f}%)")
                    reasons.append("TREND_SHORT_BLOCKED")
                    reasons.append("REVERSAL_WATCH_LONG")
                    s["reject_reason"] = "TREND_BLOCK_DUMP_CHASE"
                    s["strength"] = min(
                        float(s.get("strength") or 0),
                        float(config.MIN_SIGNAL_STRENGTH) - 0.01,
                    )
                else:
                    reasons.append(f"DUMP_EXT({ch:.0f}%)")
                    reasons.append("REVERSAL_WATCH_LONG")
                s["reasons"] = reasons


        # === Trend continuation: IMPULSE → PULLBACK → RETEST → CONTINUATION ===
        try:
            from trend_continuation import apply_to_signals as _apply_cont
            final = _apply_cont(final)
        except Exception as e:
            print(f"[SignalEngine] trend_continuation: {e}")

        # === Exhaustion Lens na sygnałach TREND (continuation gate) ===
        try:
            from exhaustion_lens import apply_to_signals as _apply_exhaustion
            final = _apply_exhaustion(final)
        except Exception as e:
            print(f"[SignalEngine] exhaustion_lens: {e}")

        # === REVERSAL ENGINE — niezależne sygnały (równolegle do Trend) ===
        try:
            from reversal_engine import generate_reversal_signals, merge_trend_and_reversal
            regime_name = "UNKNOWN"
            try:
                regime_name = (getattr(self, "last_regime", None) or {}).get("regime") or "UNKNOWN"
            except Exception:
                pass
            # wzbogacenie coinów danymi z final (rsi/cipher) pod reversal
            coin_by = {c.get("symbol"): c for c in coins if c.get("symbol")}
            for s in final:
                c = coin_by.get(s.get("symbol"))
                if not c:
                    continue
                if s.get("rsi") is not None:
                    c["rsi"] = s.get("rsi")
                if s.get("cipher_b"):
                    c["cipher_b"] = s.get("cipher_b")
                if s.get("macd_signal"):
                    c["macd_signal"] = s.get("macd_signal")
                if s.get("vol_flag"):
                    c["vol_flag"] = s.get("vol_flag")
                if s.get("order_book"):
                    c["order_book"] = s.get("order_book")
            coin_list = list(coin_by.values())
            # dopisz atr/rsi z final jeśli brak na coin
            for s in final:
                c = coin_by.get(s.get("symbol"))
                if not c:
                    continue
                if c.get("atr_pct") is None and s.get("atr_pct") is not None:
                    c["atr_pct"] = s.get("atr_pct")
                if c.get("atr") is None and s.get("atr") is not None:
                    c["atr"] = s.get("atr")
                if not c.get("lows") and s.get("lows"):
                    c["lows"] = s.get("lows")
                if not c.get("highs") and s.get("highs"):
                    c["highs"] = s.get("highs")
            rev = generate_reversal_signals(coin_list, regime=regime_name, btc_change_24h=float(btc_change_24h or 0))
            if rev:
                try:
                    from expected_net_r import expected_net_r
                    for candidate in rev:
                        expected_net_r(candidate)
                except Exception as _net_err:
                    _se_log("reversal expected_net_r", _net_err)
                for s in final:
                    if not s.get("engine"):
                        s["engine"] = "trend"
                final = merge_trend_and_reversal(final, rev)
                # Pelny reversal nie moze zniknac wskutek konfliktu z
                # odrzuconym lub neutralnym rekordem Trend Engine.
                merged_by_symbol = {str(s.get("symbol") or "").upper(): i for i, s in enumerate(final)}
                for candidate in rev:
                    sym = str(candidate.get("symbol") or "").upper()
                    idx = merged_by_symbol.get(sym)
                    if idx is None:
                        continue
                    current = final[idx]
                    confirmed = (
                        candidate.get("setup") == "reversal_confirmed"
                        and candidate.get("confirmation_status") == "CONFIRMED"
                    )
                    current_untradeable = (
                        current.get("direction") not in ("LONG", "SHORT")
                        or bool(current.get("reject_reason"))
                    )
                    if confirmed and current.get("engine") != "reversal" and current_untradeable:
                        replacement = dict(candidate)
                        replacement["market_regime"] = regime_name
                        replacement["reasons"] = list(replacement.get("reasons") or []) + ["REVERSAL_MERGE_INVARIANT"]
                        final[idx] = replacement
                n_rev = sum(1 for s in final if s.get("engine") == "reversal")
                if n_rev:
                    print(f"[SignalEngine] Reversal Engine: {len(rev)} kandydatów → {n_rev} w merge")
            # PANIC post-merge: reversal NIE dziedziczy rejectu trendu
            for s in final:
                if (s.get("engine") or "") != "reversal":
                    continue
                s["market_regime"] = regime_name
                s["panic_trigger"] = (self.last_regime or {}).get("panic_trigger") or ""
                s["regime_atr_ratio"] = (self.last_regime or {}).get("atr_ratio")
                s["regime_atr_percentile"] = (self.last_regime or {}).get("atr_percentile")
                s["regime_realized_vol"] = (self.last_regime or {}).get("realized_vol")
                rr = str(s.get("reject_reason") or "")
                if rr in ("REGIME_PANIC", "REGIME_PANIC_TREND") or rr.startswith("REGIME_PANIC"):
                    s["reject_reason"] = None
                if regime_name == "PANIC" and "PANIC_REVERSAL_ACTIVE" not in (s.get("reasons") or []):
                    s["reasons"] = list(s.get("reasons") or []) + ["PANIC_REVERSAL_ACTIVE"]
            # Shadow: loguj też ekstremum bez pełnego confirm (WATCH), plus pełne CANDIDATE
            try:
                from shadow_mode import process_reversal_signals, register_reversal_watches
                process_reversal_signals(rev or [], price_map={c.get("symbol"): c.get("price") for c in coin_list if c.get("symbol")})
                register_reversal_watches(coin_list, regime=regime_name)
            except Exception as _sh:
                print(f"[SignalEngine] shadow: {_sh}")
        except Exception as e:
            print(f"[SignalEngine] reversal_engine: {e}")

        # === Counter-trend soft: wymagaj strategii 4h gdy wlaczone ===
        if getattr(config, "REQUIRE_STRAT_FOR_COUNTER", True) or getattr(config, "COUNTER_TREND_BLOCK_IF_NA", True):
            for s in final:
                reasons = s.get("reasons") or []
                is_counter = any(
                    str(r).startswith("REGIME_VS_LONG_SOFT") or str(r).startswith("REGIME_VS_SHORT_SOFT")
                    for r in reasons
                )
                if not is_counter:
                    continue
                strat = s.get("strategy") or {}
                has_primary = bool(strat.get("pass") and strat.get("direction") == s.get("direction"))
                na = "STRAT_PRIMARY_NA" in reasons or not strat
                if na or not has_primary:
                    s["reasons"] = list(reasons) + ["COUNTER_NO_STRAT"]
                    s["reject_reason"] = s.get("reject_reason") or "COUNTER_NO_STRAT"
                    s["strength"] = min(float(s.get("strength") or 0), float(config.MIN_SIGNAL_STRENGTH) - 0.01)


        # Soft BTC correlation: kara do strength zamiast twardego bloku
        if getattr(config, "BTC_CORRELATION_FILTER", True) and not getattr(config, "BTC_CORRELATION_HARD", False):
            dump = float(getattr(config, "BTC_DUMP_THRESHOLD", -2.0))
            pump = float(getattr(config, "BTC_PUMP_THRESHOLD", 2.0))
            pen = float(getattr(config, "BTC_STRENGTH_PENALTY", 0.18))
            for s in final:
                sym = s.get("symbol")
                if sym in ("BTC", "TBTC"):
                    continue
                d = s.get("direction")
                if d == "LONG" and btc_change_24h <= dump:
                    s["strength"] = max(0.0, float(s.get("strength") or 0) - pen)
                    s["reasons"] = list(s.get("reasons") or []) + [f"BTC_DUMP_PENALTY({btc_change_24h:+.1f}%)"]
                elif d == "SHORT" and btc_change_24h >= pump:
                    s["strength"] = max(0.0, float(s.get("strength") or 0) - pen)
                    s["reasons"] = list(s.get("reasons") or []) + [f"BTC_PUMP_PENALTY({btc_change_24h:+.1f}%)"]

        # Filtr BN/BF divergence + płynność + clamp strength
        try:
            from market_data import apply_divergence_gate
        except Exception:
            apply_divergence_gate = None
        for s in final:
            if apply_divergence_gate:
                try:
                    apply_divergence_gate(s, s)
                except (TypeError, ValueError, KeyError) as e:
                    _se_log(f"divergence {s.get('symbol')}", e)
                except Exception as e:
                    _se_log(f"divergence unexpected {s.get('symbol')}", e)
            reason = self.liquidity_reject(s)
            if reason:
                s["reasons"] = list(s.get("reasons") or []) + [reason]
                if reason.startswith("LOW_VOLUME"):
                    # miekki filtr volume – kara, nie twardy blok
                    s["strength"] = max(0.0, s["strength"] - 0.12)
                else:
                    s["reject_reason"] = reason
                    s["strength"] = min(s["strength"], config.MIN_SIGNAL_STRENGTH - 0.01)
            s["strength"] = round(min(1.0, max(0.0, float(s.get("strength") or 0))), 4)
            # 12. strength → expected R (po wszystkich bonusach)
            try:
                from strength_calibration import get_calibrator
                get_calibrator().annotate(s)
            except Exception:
                pass
            # OB spread – twardy tylko przy bardzo szerokim
            ob = s.get("order_book") or {}
            max_spread = float(getattr(config, "MAX_SPREAD_PCT", 0.25) or 0.25)
            if ob.get("ob_spread_pct") is not None and float(ob["ob_spread_pct"]) > max_spread:
                s["reject_reason"] = f"WIDE_SPREAD({ob['ob_spread_pct']}%)"
                s["strength"] = min(s["strength"], config.MIN_SIGNAL_STRENGTH - 0.01)

        final.sort(key=lambda x: (0 if x["direction"] == "NEUTRAL" else 1, x["strength"]), reverse=True)


        # Liquidity score 0-100 (UI Analiza)
        for s in final:
            try:
                s["liquidity"] = self.compute_liquidity_score(s)
            except (TypeError, ValueError, KeyError, ZeroDivisionError) as e:
                _se_log(f"liquidity {s.get('symbol')}", e)
                s["liquidity"] = {"score": 0, "grade": "?", "label": "err", "notes": [f"{type(e).__name__}: {e}"]}
            except Exception as e:
                _se_log(f"liquidity unexpected {s.get('symbol')}", e)
                s["liquidity"] = {"score": 0, "grade": "?", "label": "err", "notes": [f"{type(e).__name__}: {e}"]}
            try:
                from engine_router import route_signal
                route_signal(s)
            except Exception as e:
                _se_log(f"engine_router {s.get('symbol')}", e)

        # Breakdown decyzji dla top kandydatow (UI Analiza)
        for s in final[:20]:
            try:
                s["analysis"] = self.decision_breakdown(s)
            except (TypeError, ValueError, KeyError) as e:
                _se_log(f"analysis {s.get('symbol')}", e)
                s["analysis"] = {"for": [], "against": [f"{type(e).__name__}: {e}"], "decision": "ERR", "decision_why": str(e), "indicators": {}}
            except Exception as e:
                _se_log(f"analysis unexpected {s.get('symbol')}", e)
                s["analysis"] = {"for": [], "against": [f"{type(e).__name__}: {e}"], "decision": "ERR", "decision_why": str(e), "indicators": {}}

        # Kategorie tylko dla aktywnych top sygnalow (oszczednosc API)
        if self.feeder and hasattr(self.feeder, "market_ctx"):
            for s in final:
                if s["direction"] == "NEUTRAL":
                    continue
                if s.get("categories"):
                    continue
                try:
                    cid = s.get("id")
                    if cid and cid != (s.get("symbol") or "").lower():
                        meta = self.feeder.market_ctx.get_coin_meta(cid)
                        s["categories"] = (meta.get("categories") or [])[:6]
                        s["sectors"] = meta.get("sectors") or []
                except Exception:
                    s["categories"] = []
                # limit calls
                if sum(1 for x in final if x.get("categories")) >= 8:
                    break

        # Board dla UI (zakładka Analiza) – WSZYSTKIE monety, alfabetycznie
        board = []
        for s in final:
            try:
                card = self.decision_breakdown(s)
            except Exception:
                try:
                    card = self.build_decision_card(s)
                except Exception as e:
                    card = {
                        "symbol": s.get("symbol"),
                        "direction": s.get("direction"),
                        "strength": s.get("strength"),
                        "decision": "ERR",
                        "decision_why": str(e),
                        "pros": [],
                        "cons": [str(e)],
                    }
            for k in ("symbol", "direction", "strength", "price", "change_1h", "change_24h",
                      "change_7d", "rsi", "trend", "atr_pct", "volume_24h", "vol_flag",
                      "tp_price", "sl_price", "reject_reason", "rs", "reasons", "engine",
                      "setup", "confirmation_status", "confirmation_count", "expected_net_r",
                      "expected_gross_r", "expected_r_breakdown", "market_regime"):
                if card.get(k) is None and s.get(k) is not None:
                    card[k] = s.get(k)
            if card.get("macd") is None:
                card["macd"] = s.get("macd_signal") or s.get("macd")
            if "pros" not in card or not card.get("pros"):
                card["pros"] = card.get("for") or []
            if "cons" not in card or not card.get("cons"):
                card["cons"] = card.get("against") or []
            strat = s.get("strategy") or {}
            if card.get("strategy_pass") is None:
                card["strategy_pass"] = strat.get("pass")
                card["strategy_direction"] = strat.get("direction")
                card["strategy_adx"] = strat.get("adx")
            if not card.get("liquidity"):
                card["liquidity"] = s.get("liquidity") or {}
            # Dane do wizualnego Decision Path w UI — bez zmiany logiki decyzji.
            card["score_components"] = dict(card.get("score_components") or s.get("score_components") or {})
            card["trend_fib"] = card.get("trend_fib") or s.get("trend_fib")
            card["divergence"] = card.get("divergence") or s.get("divergence") or s.get("source_div")
            card["source_div"] = card.get("source_div") or s.get("source_div")
            card["decision_path"] = s.get("decision_path") or card.get("decision_path")
            # status efektywności sygnału
            dec = (card.get("decision") or "").upper()
            direction = (card.get("direction") or "NEUTRAL").upper()
            strength = float(card.get("strength") or 0)
            reject = card.get("reject_reason")
            is_ok = dec in ("OPEN_OK", "KANDYDAT", "CANDIDATE") and direction in ("LONG", "SHORT") and not reject
            if is_ok:
                card["signal_status"] = "OK"
                card["signal_summary"] = "Sygnał efektywny – kwalifikuje się do otwarcia"
            elif direction in ("LONG", "SHORT") and (reject or strength < float(getattr(__import__("config"), "MIN_SIGNAL_STRENGTH", 0.55))):
                card["signal_status"] = "INEFFECTIVE"
                why = reject or card.get("decision_why") or f"siła {strength:.2f}"
                card["signal_summary"] = f"Obecnie nieefektywna jako sygnał: {why}"
            else:
                card["signal_status"] = "NEUTRAL"
                card["signal_summary"] = "Brak wyraźnego sygnału LONG/SHORT – moneta obserwowana"
            board.append(card)
        board.sort(key=lambda x: str(x.get("symbol") or "").upper())
        self.last_analysis_board = board

        # PRIORYTET 6 — decision_path na każdym sygnale
        for s in final:
            s["decision_path"] = self._assign_decision_path(s)
        for card in board:
            if card.get("decision_path") is None:
                card["decision_path"] = self._assign_decision_path(card)

        return final

    @staticmethod
    def _assign_decision_path(s: Dict) -> str:
        """
        Jednoznaczna ścieżka decyzji:
          TREND_PRIMARY | TREND_SOFT | REVERSAL | NO_TRADE
        """
        direction = s.get("direction") or "NEUTRAL"
        reasons = [str(r) for r in (s.get("reasons") or [])]
        eng = (s.get("engine") or s.get("score_type") or "").lower()
        reject = s.get("reject_reason")

        if eng == "daytrading":
            return "DAYTRADING" if direction in ("LONG", "SHORT") else "NO_TRADE"

        # Reversal wygrywa ścieżkę gdy engine=reversal
        if eng == "reversal":
            if direction in ("LONG", "SHORT") and not reject:
                return "REVERSAL"
            if direction in ("LONG", "SHORT"):
                # shadow / odrzucony kandydat — nadal REVERSAL path (nie trend)
                return "REVERSAL"
            return "NO_TRADE"

        if direction not in ("LONG", "SHORT"):
            return "NO_TRADE"
        if reject:
            return "NO_TRADE"

        soft_markers = (
            "PRIMARY_SOFT_PASS",
            "STRAT_SOFT_ALIGN",
            "PRIMARY_MTF_FALLBACK",
        )
        primary_ok = any(r == "STRAT_PRIMARY_OK" or r.startswith("STRAT_PRIMARY_OK") for r in reasons)
        soft = any(any(m in r for m in soft_markers) for r in reasons)

        if primary_ok and not soft:
            return "TREND_PRIMARY"
        if soft or ("STRAT_PRIMARY_FAIL" in reasons and not reject):
            return "TREND_SOFT"
        if primary_ok:
            return "TREND_PRIMARY"
        # trend bez primary — soft fallback jeśli w ogóle kwalifikuje się kierunkiem
        if any("CONT_SETUP_OK" in r for r in reasons):
            return "TREND_SOFT"
        return "TREND_SOFT" if not reject else "NO_TRADE"

    def print_analysis(self, signals: List[Dict], limit: int = 7):
        active = [s for s in signals if s["direction"] != "NEUTRAL"]
        reg = getattr(self, "last_regime", {}) or {}
        if reg:
            print(f"Rezim rynku: {reg.get('regime')} | BTC24h={reg.get('btc_24h')} | ATR_ratio={reg.get('atr_ratio')}")
        print(f"\n=== ANALIZA TOP SYGNAŁÓW ({len(active)} aktywnych) ===")
        for s in active[:limit]:
            path = s.get("decision_path") or "?"
            sc = s.get("reversal_score") if path == "REVERSAL" else s.get("trend_score", s.get("strength"))
            print(f"\n{s['direction']:5} {s['symbol']:8} | path={path} | score {sc}")
            rs = s.get("rs")
            c1 = s.get("change_1h")
            c24 = s.get("change_24h")
            c7 = s.get("change_7d")
            def _pct(v):
                try:
                    return f"{float(v):+.1f}%"
                except (TypeError, ValueError):
                    return "—"
            print(f"     Cena: ${float(s.get('price') or 0):.6f} | RS: {_pct(rs)} | "
                  f"1h:{_pct(c1)} 24h:{_pct(c24)} 7d:{_pct(c7)}")
            mtf = s.get("mtf") or {}
            if mtf.get("by_tf"):
                parts = []
                for tf in ("15m", "1h", "4h", "1d"):
                    info = (mtf.get("by_tf") or {}).get(tf) or {}
                    if info.get("direction"):
                        parts.append(f"{tf}:{info.get('direction')}{'✓' if info.get('pass') else '✗'}")
                    else:
                        parts.append(f"{tf}:—")
                votes = f"L{mtf.get('long_votes', 0)}/S{mtf.get('short_votes', 0)}"
                print(f"     MTF: {' | '.join(parts)}  votes {votes}")
            elif "bias" in mtf:
                print(f"     MTF(score): {mtf.get('bias')} ({mtf.get('score')})")
            else:
                print(f"     MTF: —")
            strat = s.get("strategy") or {}
            if strat:
                print(f"     STRAT 4h: pass={strat.get('pass')} dir={strat.get('direction')} ADX={strat.get('adx')}")
            if s.get("reject_reason"):
                print(f"     REJECT: {s.get('reject_reason')}")
            if s.get("rsi") is not None:
                print(f"     RSI: {s['rsi']} ({s['rsi_signal']}) | MACD: {s.get('macd_signal')}")
            print(f"     Trend: {s.get('trend', '—')} (score {s.get('trend_score', 0)})")
            sectors = s.get("sectors") or []
            cats = s.get("categories") or []
            if sectors:
                print(f"     Sektor: {', '.join(sectors)}")
            elif cats:
                print(f"     Kategorie: {', '.join(cats[:4])}")
            if s["reasons"]:
                print(f"     Powody: {', '.join(s['reasons'])}")
            if s.get("source_div") and s["source_div"].get("sources_available", 0) >= 2:
                sd = s["source_div"]
                print(f"     Zrodla: max rozjazd {sd['max_diff_pct']:.2f}% ({sd['level']}) | "
                      f"CG={sd['prices'].get('coingecko')} BN={sd['prices'].get('binance')} BY={sd['prices'].get('bybit')} BF={sd['prices'].get('blofin')}")
            strat = s.get("strategy") or {}
            if strat:
                print(f"     Strategia 1h: {strat.get('direction')} pass={strat.get('pass')} | "
                      f"ADX={strat.get('adx')} ST={strat.get('supertrend')} RSI={strat.get('rsi')}")
            ob = s.get("order_book") or {}
            if ob:
                print(f"     OrderBook: imbalance {ob.get('ob_imbalance'):+.2f} ({ob.get('ob_bias')}) | "
                      f"spread {ob.get('ob_spread_pct')}% | bid/ask vol {ob.get('ob_bid_vol'):.0f}/{ob.get('ob_ask_vol'):.0f}")
            if s.get("vol_flag"):
                print(f"     Wolumen: {s.get('vol_flag')} | 24h=${(s.get('volume_24h') or 0):,.0f}")
            fr = s.get("funding") or {}
            if fr.get("funding_rate") is not None:
                print(f"     Funding: {fr.get('funding_rate_pct')}% / {fr.get('funding_interval')}h")
            if s["entry_hint"]:
                print(f"     ENTRY → {s['entry_hint']}")
            if s["exit_hint"]:
                print(f"     EXIT  → {s['exit_hint']}")
