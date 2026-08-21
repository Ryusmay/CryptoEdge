# ============================================================
# CryptoEdge Backtest Engine v2.2 — lustro LIVE
# A. MTF replay: 15m / 1H / 4H / 1D (primary 4h, filter 1d)
# B. Binance confirmation (BF signal → BN confirm historycznie)
# C. Historyczny funding (BloFin / Binance fapi)
# D. Expected Net R — ta sama logika co LIVE (expected_net_r.py)
# E. Realistyczny slippage = f(notional, volume, ATR, liquidity)
# next-bar entry · SL-before-trail · portfolio risk
# ============================================================

from __future__ import annotations
import argparse
import json
import time
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any, Tuple

try:
    import requests
except ImportError:
    requests = None

from indicators_full import compute_indicators, evaluate_entry

try:
    import config as _cfg
except Exception:
    _cfg = None


def _cfg_get(name, default):
    if _cfg is None:
        return default
    return getattr(_cfg, name, default)


@dataclass
class BTPosition:
    symbol: str
    direction: str
    entry: float
    size_usd: float
    sl: float
    tp: Optional[float]
    strength: float
    entry_bar: int
    risk_usd: float = 0.0
    highest: float = 0.0
    lowest: float = 0.0
    funding_paid: float = 0.0
    reasons: List[str] = field(default_factory=list)
    trail_active: bool = False
    mtf_votes: int = 0
    entry_ts_ms: int = 0
    last_funding_ts: int = 0
    engine: str = "trend"
    liquidity_bucket: str = "UNKNOWN"
    preferred_engine: str = ""
    residual_momentum_24h: float = 0.0
    expected_r_status: str = "UNKNOWN"


@dataclass
class PendingEntry:
    symbol: str
    direction: str
    signal_bar: int
    strength: float
    sl_dist: float
    tp_dist: Optional[float]
    reasons: List[str]
    signal_price: float
    mtf_votes: int = 0
    risk_mult: float = 1.0
    risk_pct: float = 0.0  # precomputed LIVE-identical risk %
    expected_r: float = 0.0
    expected_net_r: float = 0.0
    engine: str = "trend"
    market_regime: str = "UNKNOWN"
    liquidity_bucket: str = "UNKNOWN"
    preferred_engine: str = ""
    residual_momentum_24h: float = 0.0
    expected_r_status: str = "UNKNOWN"


@dataclass
class BTResult:
    equity_curve: List[float] = field(default_factory=list)
    trades: List[dict] = field(default_factory=list)
    final_equity: float = 0.0
    max_dd: float = 0.0
    n_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_r: float = 0.0
    expectancy_r: float = 0.0
    notes: List[str] = field(default_factory=list)
    by_symbol: Dict[str, dict] = field(default_factory=dict)
    by_engine: Dict[str, dict] = field(default_factory=dict)


# ------------------------------------------------------------------
# Data fetch
# ------------------------------------------------------------------
def _interval_to_blofin_bar(interval: str) -> str:
    m = {
        "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m",
        "1h": "1H", "60": "1H", "4h": "4H", "1d": "1D", "1D": "1D",
    }
    return m.get(interval, "1H")


def fetch_klines_binance(symbol: str, interval: str = "1h", limit: int = 500) -> dict:
    if requests is None:
        return {}
    pair = symbol.upper().replace("-", "").replace("/", "")
    if not pair.endswith("USDT"):
        pair += "USDT"
    urls = [
        "https://fapi.binance.com/fapi/v1/klines",
        "https://data-api.binance.vision/api/v3/klines",
    ]
    params = {"symbol": pair, "interval": interval, "limit": min(int(limit), 1500)}
    for url in urls:
        try:
            r = requests.get(url, params=params, timeout=20)
            if r.status_code != 200:
                continue
            rows = r.json()
            if not isinstance(rows, list) or len(rows) < 10:
                continue
            rows = rows[:-1]
            return {
                "ts": [int(k[0]) for k in rows],
                "opens": [float(k[1]) for k in rows],
                "highs": [float(k[2]) for k in rows],
                "lows": [float(k[3]) for k in rows],
                "closes": [float(k[4]) for k in rows],
                "volumes": [float(k[5]) for k in rows],
                "source": "binance",
            }
        except Exception as e:
            print(f"[BT] BN {pair} {interval}: {e}")
    return {}


def fetch_klines_blofin(symbol: str, interval: str = "1h", limit: int = 500) -> dict:
    try:
        from blofin_feed import BlofinFeed
        feed = BlofinFeed()
        bar = _interval_to_blofin_bar(interval)
        data = feed.fetch_klines_ohlcv(symbol, bar=bar, limit=int(limit) + 2) or {}
        closes = list(data.get("closes") or [])
        if len(closes) < 10:
            return {}
        highs = list(data.get("highs") or closes)
        lows = list(data.get("lows") or closes)
        vols = list(data.get("volumes") or [0] * len(closes))
        ts = list(data.get("timestamps") or data.get("ts") or [])
        opens = list(data.get("opens") or [])
        if len(opens) != len(closes):
            opens = [closes[0]] + closes[:-1]
        # BlofinFeed already filters the explicit confirm=0/forming candle.
        # Dropping once more would introduce a one-bar backtest delay.
        return {
            "ts": ts, "opens": opens, "highs": highs, "lows": lows,
            "closes": closes, "volumes": vols, "source": "blofin",
        }
    except Exception as e:
        print(f"[BT] BF {symbol} {interval}: {e}")
        return {}


def fetch_klines(symbol: str, interval: str = "1h", limit: int = 500, source: str = "binance") -> dict:
    source = (source or "binance").lower().strip()
    if source in ("blofin", "bf", "auto"):
        data = fetch_klines_blofin(symbol, interval, limit)
        if data.get("closes"):
            return data
        if source != "auto":
            return {}
        print(f"[BT] BloFin empty {symbol} {interval} → Binance")
    return fetch_klines_binance(symbol, interval, limit)



def clip_bundle_overlap(bundle: Dict[str, dict]) -> Dict[str, dict]:
    """
    1d NIE jest przycinany – potrzebuje długiej historii pod EMA200.
    1h i 4h są przycinane do wspólnego okna.
    15m opcjonalne w obrębie okna 1h/4h.
    """
    starts, ends = [], []
    for tf in ("1h", "4h"):
        o = bundle.get(tf) or {}
        ts = o.get("ts") or []
        if len(ts) >= 2:
            starts.append(int(ts[0]))
            ends.append(int(ts[-1]))
    out = dict(bundle)  # 1d stays full
    if not starts:
        return out
    t0, t1 = max(starts), min(ends)
    for tf in ("1h", "4h", "15m"):
        o = bundle.get(tf) or {}
        ts = o.get("ts") or []
        if not ts:
            if tf in out:
                pass
            continue
        idxs = [i for i, tt in enumerate(ts) if t0 <= int(tt) <= t1]
        if len(idxs) < (30 if tf == "15m" else 50):
            if tf == "15m":
                out.pop("15m", None)
            continue
        lo, hi = idxs[0], idxs[-1] + 1
        out[tf] = {
            k: (v[lo:hi] if isinstance(v, list) else v)
            for k, v in o.items()
        }
    return out


def fetch_mtf_bundle(symbol: str, source: str = "binance", limits: dict = None) -> Dict[str, dict]:
    """
    Pobiera 15m / 1h / 4h / 1d dla jednego symbolu.
    limits: ile barów per TF (domyślnie skalowane do ~tego samego horyzontu).
    """
    limits = limits or {
        # ~60–80 dni wspólnego horyzontu (Binance max 1500)
        "15m": 1500,  # ~15 dni (ograniczenie API)
        "1h": 1500,   # ~62 dni
        "4h": 500,    # ~83 dni, EMA200 OK
        "1d": 400,    # ~13 mies., EMA200 OK
    }
    out = {}
    for tf, lim in limits.items():
        d = fetch_klines(symbol, interval=tf, limit=lim, source=source)
        n = len(d.get("closes") or [])
        print(f"  {symbol} {tf}: {n} bars [{d.get('source','?')}]")
        if n >= 40:
            out[tf] = d
        time.sleep(0.08)
    return clip_bundle_overlap(out)


# ------------------------------------------------------------------
# Window helpers (closed bars up to timestamp)
# ------------------------------------------------------------------
def _tf_ms(tf: str) -> int:
    unit = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}
    s = str(tf or "").strip().lower()
    try:
        return int(s[:-1]) * unit[s[-1]]
    except (KeyError, TypeError, ValueError):
        return 0


def window_until(ohlcv: dict, ts_ms: int, tf: str = None) -> dict:
    """Return only bars known at ``ts_ms``.

    Exchange kline timestamps are bar open times. With a timeframe, a bar is
    visible only when ``open_ts + timeframe <= decision_ts``. Omitting ``tf``
    preserves the legacy behavior for timestamped point series.
    """
    ts_list = ohlcv.get("ts") or []
    if not ts_list:
        # no timestamps – return full (degraded)
        return ohlcv
    idx = 0
    duration = _tf_ms(tf) if tf else 0
    for i, t in enumerate(ts_list):
        if int(t) + duration <= int(ts_ms):
            idx = i + 1
        else:
            break
    if idx < 2:
        return {}
    return {
        "ts": ts_list[:idx],
        "opens": (ohlcv.get("opens") or [])[:idx],
        "highs": (ohlcv.get("highs") or [])[:idx],
        "lows": (ohlcv.get("lows") or [])[:idx],
        "closes": (ohlcv.get("closes") or [])[:idx],
        "volumes": (ohlcv.get("volumes") or [])[:idx],
    }


def eval_tf(ohlcv: dict, tf: str, min_bars: int = 50) -> dict:
    """compute_indicators + evaluate_entry na oknie."""
    closes = ohlcv.get("closes") or []
    if len(closes) < min_bars:
        return {"pass": False, "direction": None, "error": "few_bars"}
    ind = compute_indicators(ohlcv, tf=tf)
    if not ind:
        return {"pass": False, "direction": None, "error": "ind_fail"}
    entry = evaluate_entry(ind)
    if not entry:
        return {"pass": False, "direction": None, "error": "entry_fail"}
    entry["adx"] = ind.get("adx")
    entry["rsi"] = ind.get("rsi")
    entry["atr"] = ind.get("atr")
    entry["indicators"] = {
        "rsi": ind.get("rsi"), "adx": ind.get("adx"), "atr": ind.get("atr"),
        "supertrend": (ind.get("supertrend") or {}).get("direction"),
    }
    return entry


def evaluate_mtf_at(
    bundle: Dict[str, dict],
    ts_ms: int,
    min_bars: int = 50,
) -> dict:
    """
    LIVE-equivalent evaluate_mtf at timestamp ts_ms.
    Uses only closed candles with ts <= ts_ms for each TF.
    """
    # Core MTF dla swing = 1h/4h/1d; 15m bonus gdy jest w bundle
    tfs = list(_cfg_get("MTF_TIMEFRAMES", ["15m", "1h", "4h", "1d"]))
    # upewnij się że core jest obecne
    for c in ("1h", "4h", "1d"):
        if c not in tfs:
            tfs.append(c)
    results = {}
    for tf in tfs:
        ohlcv_full = bundle.get(tf) or {}
        win = window_until(ohlcv_full, ts_ms, tf=tf)
        n = len(win.get("closes") or [])
        if n < 10:
            results[tf] = {}
            continue
        # 4h/1d need EMA200 → 210 bars; 15m/1h → 60
        mb = 210 if tf in ("4h", "1d") else max(60, min_bars)
        if n < mb:
            results[tf] = {"pass": False, "direction": None, "error": f"few_bars({n}<{mb})"}
            continue
        try:
            results[tf] = eval_tf(win, tf=tf, min_bars=mb)
        except Exception:
            results[tf] = {}

    # Głosy tylko z TF które mają dane (nie few_bars / empty)
    valid = {k: v for k, v in results.items()
             if v and not str(v.get("error") or "").startswith("few_bars") and "direction" in v}
    long_votes = sum(1 for r in valid.values() if r.get("direction") == "LONG" and r.get("pass"))
    short_votes = sum(1 for r in valid.values() if r.get("direction") == "SHORT" and r.get("pass"))
    higher = []
    for tf in ("1h", "4h", "1d"):
        r = valid.get(tf) or {}
        if r.get("pass") and r.get("direction") in ("LONG", "SHORT"):
            higher.append(r["direction"])
    hold_long = higher.count("LONG") >= 2
    hold_short = higher.count("SHORT") >= 2
    # Dynamiczny próg: min(config, liczba dostępnych TF)
    base_need = int(_cfg_get("MTF_REQUIRE_ALIGN", 2) or 2)
    n_valid = max(1, len(valid))
    need = min(base_need, n_valid)
    return {
        "by_tf": {
            k: {"direction": v.get("direction"), "pass": v.get("pass"),
                "adx": v.get("adx"), "rsi": v.get("rsi"), "error": v.get("error")}
            for k, v in results.items()
        },
        "results": results,
        "long_votes": long_votes,
        "short_votes": short_votes,
        "hold_long": hold_long,
        "hold_short": hold_short,
        "required_align": need,
        "n_valid_tf": n_valid,
    }




def fetch_funding_schedule(symbol: str, limit: int = 300, source: str = "blofin") -> list:
    """
    Historyczne settlementy funding.
    Prefer BloFin; fallback Binance public (gdy dostępne).
    Zwraca [{ts_ms, rate}, ...] rosnąco.
    """
    rows = []
    if source in ("blofin", "bf", "auto", "binance"):
        try:
            from blofin_feed import BlofinFeed
            rows = BlofinFeed().fetch_funding_rate_history(symbol, limit=limit) or []
        except Exception as e:
            print(f"[BT] BF funding history {symbol}: {e}")
            rows = []
    if not rows and source in ("binance", "auto"):
        try:
            import requests as _rq
            pair = symbol.upper().replace("-", "")
            if not pair.endswith("USDT"):
                pair += "USDT"
            r = _rq.get(
                "https://fapi.binance.com/fapi/v1/fundingRate",
                params={"symbol": pair, "limit": min(limit, 1000)},
                timeout=15,
            )
            if r.status_code == 200:
                for x in r.json() or []:
                    try:
                        rows.append({
                            "ts_ms": int(x["fundingTime"]),
                            "rate": float(x["fundingRate"]),
                        })
                    except (KeyError, TypeError, ValueError):
                        continue
                rows.sort(key=lambda z: z["ts_ms"])
        except Exception as e:
            print(f"[BT] BN funding history {symbol}: {e}")
    return rows


def funding_paid_between(
    schedule: list,
    entry_ts_ms: int,
    now_ts_ms: int,
    direction: str,
    notional: float,
    last_settled_ts: int = 0,
) -> tuple:
    """
    Suma funding P/L dla settlementów w (last_settled_ts, now_ts_ms]
    przy otwartej pozycji od entry_ts_ms.
    LONG płaci gdy rate>0; SHORT płaci gdy rate<0.
    Zwraca (paid_usd, new_last_settled_ts).
    """
    if not schedule or notional <= 0:
        return 0.0, last_settled_ts
    direction = (direction or "").upper()
    # Funding cost: LONG pays +notional*rate, SHORT pays -notional*rate
    # (positive "paid" reduces equity; when rate>0 longs pay shorts, so SHORT cost is negative = income)
    paid = 0.0
    new_last = last_settled_ts
    for row in schedule:
        ts = int(row["ts_ms"])
        if ts <= last_settled_ts or ts <= entry_ts_ms or ts > now_ts_ms:
            if ts <= last_settled_ts:
                pass
            continue
        rate = float(row["rate"])
        if direction == "LONG":
            cost = notional * rate
        else:
            cost = -notional * rate
        paid += cost
        new_last = ts
    return paid, new_last


def _pct_change(ohlcv: dict, bars_back: int) -> Optional[float]:
    closes = ohlcv.get("closes") or []
    if len(closes) < bars_back + 1:
        return None
    a, b = float(closes[-(bars_back + 1)]), float(closes[-1])
    if a == 0:
        return None
    return (b - a) / a * 100.0


def apply_bt_confirmation(
    signal: dict,
    primary_bundle: Dict[str, dict],
    confirm_bundle: Dict[str, dict] = None,
    primary_source: str = "blofin",
) -> Optional[dict]:
    """
    Historyczny odpowiednik external_confirmation.
    primary_bundle = venue strategii (BF lub BN)
    confirm_bundle = niezależny rynek (zwykle Binance gdy primary=blofin)
    """
    if signal is None:
        return None
    try:
        from external_confirmation import apply_confirmation
    except Exception:
        apply_confirmation = None

    p1h = primary_bundle.get("1h") or {}
    c1h = (confirm_bundle or {}).get("1h") or {}
    # 24h ≈ 24 bars 1h; 1h = 1 bar
    prim_24 = _pct_change(p1h, 24)
    prim_1h = _pct_change(p1h, 1)
    conf_24 = _pct_change(c1h, 24) if c1h.get("closes") else None
    conf_1h = _pct_change(c1h, 1) if c1h.get("closes") else None

    # map to coin fields expected by external_confirmation
    if primary_source in ("blofin", "bf", "auto"):
        coin = {
            "blofin_change_24h": prim_24,
            "blofin_change_1h": prim_1h,
            "binance_change_24h": conf_24 if conf_24 is not None else prim_24,
            "binance_change_1h": conf_1h if conf_1h is not None else prim_1h,
            # CG: brak historycznego → None (tylko BN confirmation w BT)
            "coingecko_change_24h": None,
        }
    else:
        # primary=binance → confirmation is self-aligned (no second venue)
        coin = {
            "blofin_change_24h": prim_24,
            "blofin_change_1h": prim_1h,
            "binance_change_24h": prim_24,
            "binance_change_1h": prim_1h,
            "coingecko_change_24h": None,
        }

    if apply_confirmation:
        apply_confirmation(signal, coin)
    else:
        # minimal inline
        signal["external_confirmation"] = {"status": "NEUTRAL", "score_delta": 0}
        signal["cross_market_status"] = "NEUTRAL"

    if signal.get("reject_reason"):
        return None
    # risk mult already set by apply_confirmation on DIVERGE
    return signal


def live_mtf_signal(
    symbol: str,
    bundle: Dict[str, dict],
    ts_ms: int,
    confirm_bundle: Dict[str, dict] = None,
    primary_source: str = "binance",
    funding_schedule: list = None,
) -> Optional[dict]:
    """
    Primary = STRATEGY_PRIMARY_TF (4h)
    Filter  = STRATEGY_FILTER_TF (1d)
    + MTF votes (15m/1h/4h/1d)
    Identyczna logika jak LIVE generate_signals MTF block.
    """
    primary_tf = str(_cfg_get("STRATEGY_PRIMARY_TF", "4h") or "4h")
    filter_tf = str(_cfg_get("STRATEGY_FILTER_TF", "1d") or "1d")
    require_daily = bool(_cfg_get("REQUIRE_DAILY_ALIGN", True))
    mtf_enabled = bool(_cfg_get("MTF_ENABLED", True))

    mtf = evaluate_mtf_at(bundle, ts_ms)
    results = mtf.get("results") or {}
    need = int(mtf.get("required_align") or _cfg_get("MTF_REQUIRE_ALIGN", 2) or 2)
    primary = results.get(primary_tf) or {}

    # Primary must pass
    if not primary.get("pass") or primary.get("direction") not in ("LONG", "SHORT"):
        # degraded: allow 1h proxy if primary 4h fails (like LIVE STRATEGY_1H_PROXY)
        if primary_tf == "4h" and bool(_cfg_get("STRATEGY_1H_PROXY", True)):
            proxy = results.get("1h") or {}
            if proxy.get("pass") and proxy.get("direction") in ("LONG", "SHORT"):
                primary = dict(proxy)
                primary["proxy"] = "1h"
            else:
                return None
        else:
            return None

    direction = primary["direction"]

    # Daily filter: block only when 1d actively opposes (pass + opposite dir)
    if require_daily:
        daily = results.get(filter_tf) or {}
        ddir = daily.get("direction")
        if daily.get("pass") and ddir in ("LONG", "SHORT") and ddir != direction:
            return None  # DAILY_VS_DIRECTION
        # soft: if 1d has data but NEUTRAL, slight strength penalty later

    # MTF alignment
    if mtf_enabled:
        lv = int(mtf.get("long_votes") or 0)
        sv = int(mtf.get("short_votes") or 0)
        if direction == "LONG" and lv < need:
            return None
        if direction == "SHORT" and sv < need:
            return None
    else:
        lv = sv = 0

    # Price / levels from primary window
    price_tf = primary_tf if bundle.get(primary_tf) else "1h"
    p_ohlcv = window_until(bundle.get(primary_tf) or bundle.get("1h") or {}, ts_ms, tf=price_tf)
    closes = p_ohlcv.get("closes") or []
    if not closes:
        return None
    price = float(closes[-1])
    levels = primary.get("levels") or {}
    atr = primary.get("atr") or (price * 0.02)
    sl = levels.get("sl")
    tp = levels.get("tp2") or levels.get("tp1")
    if sl is None:
        sl = price - 2.0 * atr if direction == "LONG" else price + 2.0 * atr
    if tp is None:
        tp = price + 3.5 * atr if direction == "LONG" else price - 3.5 * atr

    # --- Strength = mirror LIVE (strategy pass + ADX + MTF + cipher + proxy) ---
    # Base: signal that passed evaluate_entry starts near MIN_SIGNAL_STRENGTH
    min_str = float(_cfg_get("MIN_SIGNAL_STRENGTH", 0.55) or 0.55)
    strength = min_str + 0.05  # 0.60 typical pass baseline
    adx = float(primary.get("adx") or 0)
    adx_th = float((primary.get("adx_threshold") if primary.get("adx_threshold") is not None
                    else (25.0 if primary_tf in ("4h", "1d") else 22.5)))
    if adx >= adx_th + 8:
        strength += 0.12
    elif adx >= adx_th + 3:
        strength += 0.08
    elif adx >= adx_th:
        strength += 0.04

    votes = lv if direction == "LONG" else sv
    # LIVE: +0.02 + 0.01 * min(votes, 4) when aligned
    if votes >= need:
        strength = min(1.0, strength + 0.02 + 0.01 * min(votes, 4))
    elif votes > 0:
        strength = max(0.0, strength - 0.04)

    if primary.get("proxy"):
        strength = max(0.0, strength - 0.05)
        # LIVE proxy 4h risk mult handled in sizing

    # Cipher B soft (from primary indicators if present)
    cipher = primary.get("cipher_b") or (primary.get("indicators") or {}).get("cipher_b") or {}
    if isinstance(cipher, dict):
        if direction == "LONG" and cipher.get("bull_div"):
            strength = min(1.0, strength + 0.05)
        if direction == "SHORT" and cipher.get("bear_div"):
            strength = min(1.0, strength + 0.05)
        if direction == "LONG" and cipher.get("bear_div"):
            strength = max(0.0, strength - 0.06)
        if direction == "SHORT" and cipher.get("bull_div"):
            strength = max(0.0, strength - 0.06)

    if direction == "LONG" and mtf.get("hold_long"):
        strength = min(1.0, strength + 0.02)
    if direction == "SHORT" and mtf.get("hold_short"):
        strength = min(1.0, strength + 0.02)

    strength = max(0.0, min(1.0, strength))

    reasons = list(primary.get("reasons") or [])[:6]
    reasons.append(f"MTF_{direction[0]}({votes}/{need})")
    if direction == "LONG" and mtf.get("hold_long"):
        reasons.append("MTF_HOLD_HTF")
    if direction == "SHORT" and mtf.get("hold_short"):
        reasons.append("MTF_HOLD_HTF")
    by_tf = mtf.get("by_tf") or {}
    for tf, info in by_tf.items():
        if info.get("pass"):
            reasons.append(f"{tf}:{info.get('direction')}")

    out = {
        "symbol": symbol.upper(),
        "direction": direction,
        "price": price,
        "sl_price": float(sl),
        "tp_price": float(tp) if tp is not None else None,
        "strength": round(strength, 3),
        "atr": atr,
        "adx": adx,
        "reasons": reasons[:10],
        "mtf_votes": votes,
        "mtf": by_tf,
        "pass": True,
        "primary_tf": primary_tf if not primary.get("proxy") else "1h_proxy",
    }
    # LIVE calibrator: strength → expected_r label
    try:
        from strength_calibration import get_calibrator
        get_calibrator().annotate(out)
    except Exception:
        out["expected_r"] = None

    # Historical cross-market confirmation (Binance vs primary)
    # Windows already truncated by caller via evaluate_mtf_at; use full bundles + ts
    prim_win = {
        "1h": window_until(bundle.get("1h") or {}, ts_ms, tf="1h"),
        "4h": window_until(bundle.get("4h") or {}, ts_ms, tf="4h"),
    }
    conf_win = None
    if confirm_bundle:
        conf_win = {
            "1h": window_until(confirm_bundle.get("1h") or {}, ts_ms, tf="1h"),
            "4h": window_until(confirm_bundle.get("4h") or {}, ts_ms, tf="4h"),
        }
    out = apply_bt_confirmation(out, prim_win, conf_win, primary_source=primary_source)
    if out is None:
        return None

    # Attach nearest funding rate for Expected Net R (historical)
    if funding_schedule:
        # last rate at or before ts_ms
        fr = None
        for row in reversed(funding_schedule):
            if int(row.get("ts_ms") or 0) <= int(ts_ms):
                fr = float(row.get("rate") or 0)
                break
        if fr is not None:
            out["funding"] = {
                "funding_rate": fr,
                "funding_interval_h": 8.0,
            }

    # Volume/ATR slip model for Expected Net R (not real OB)
    try:
        from orderbook_impact import estimate_bar_slippage
        o1h = prim_win.get("1h") or {}
        closes = o1h.get("closes") or []
        vols = o1h.get("volumes") or []
        px = float(closes[-1]) if closes else float(out.get("price") or 0)
        vol = float(vols[-1]) if vols else 0.0
        atr = float(out.get("atr") or 0)
        atr_pct = (atr / px) if px else 0
        # assume mid-size notional ~ equity * risk / sl_dist — use 5k placeholder scale
        est = estimate_bar_slippage(
            notional_usd=5000.0, bar_volume_base=vol, price=px, atr_pct=atr_pct,
        )
        out["_ob_impact"] = {
            "impact_pct": est["slip"] * 100.0,
            "model": "volume_atr",
            "participation": est["participation"],
        }
        # also half-spread assumption from atr
        out.setdefault("order_book", {})
        out["order_book"]["ob_spread_pct"] = max(0.02, atr_pct * 100 * 0.05)
    except Exception:
        pass

    # LIVE gate: MIN_EXPECTED_NET_R
    if bool(_cfg_get("USE_EXPECTED_NET_R_FILTER", True)):
        try:
            from expected_net_r import net_r_ok, expected_net_r
            # ensure expected_r present
            if out.get("expected_r") is None:
                try:
                    from strength_calibration import get_calibrator
                    get_calibrator().annotate(out)
                except Exception:
                    pass
            ok_n, why_n = net_r_ok(out, risk_manager=None)
            br = out.get("expected_r_breakdown") or {}
            if not br:
                try:
                    br = expected_net_r(out, None)
                except Exception:
                    br = {}
            out["expected_net_r"] = br.get("net_r") if br else out.get("expected_net_r")
            out["expected_r_breakdown"] = br
            if not ok_n:
                out["reject_reason"] = why_n
                return None
        except Exception as e:
            # don't block on filter errors
            out.setdefault("reasons", []).append(f"NET_R_ERR")

    return out


# ------------------------------------------------------------------
# Backtester
# ------------------------------------------------------------------

def _bt_stats(trades: list) -> dict:
    if not trades:
        return {"n": 0, "win_rate": 0.0, "profit_factor": 0.0, "avg_r": 0.0,
                "expectancy_r": 0.0, "net": 0.0, "max_dd_r": 0.0}
    wins = [x for x in trades if float(x.get("r") or 0) > 0]
    losses = [x for x in trades if float(x.get("r") or 0) <= 0]
    gp = sum(float(x.get("net") or 0) for x in wins) or 0.0
    gl = abs(sum(float(x.get("net") or 0) for x in losses)) or 1e-9
    rs = [float(x.get("r") or 0) for x in trades]
    avg_r = sum(rs) / len(rs)
    eq = peak = max_dd = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
    return {
        "n": len(trades),
        "win_rate": len(wins) / len(trades),
        "profit_factor": gp / gl,
        "avg_r": avg_r,
        "expectancy_r": avg_r,
        "net": sum(float(x.get("net") or 0) for x in trades),
        "max_dd_r": max_dd,
    }


def bt_reversal_signal_from_window(symbol: str, bundle: dict, ts_ms: int, regime: str = "UNKNOWN"):
    """
    Reversal signal at ts_ms — STRICT no look-ahead:
      window_until(...) keeps only candles with ts <= ts_ms.
      Fib swing pivots need right-side confirmation from those bars only.
    """
    if not bool(_cfg_get("REVERSAL_ENGINE_ENABLED", True)):
        return None
    # TYLKO zamknięte świece do T (nigdy przyszły HIGH)
    o1h = window_until(bundle.get("1h") or {}, ts_ms, tf="1h")
    closes = o1h.get("closes") or []
    if len(closes) < 30:
        return None
    price = float(closes[-1])
    if price <= 0:
        return None
    def ch(n):
        if len(closes) <= n:
            return 0.0
        p0 = float(closes[-1 - n])
        return (price - p0) / p0 * 100.0 if p0 else 0.0
    # Pełniejsze okno pod pivot+ATR (max lookback Fibo), nadal <= ts_ms
    max_lb = int(_cfg_get("FIB_SWING_MAX_LOOKBACK", 80) or 80)
    coin = {
        "symbol": symbol, "price": price,
        "change_24h": ch(24), "change_1h": ch(1),
        "lows": (o1h.get("lows") or [])[-max_lb:],
        "highs": (o1h.get("highs") or [])[-max_lb:],
        "closes": closes[-max_lb:],
        "volume_24h": sum(float(v or 0) for v in (o1h.get("volumes") or [])[-24:]) * price,
        # jawny znacznik dla audytu look-ahead
        "_as_of_ts_ms": int(ts_ms),
        "_ohlcv_closed_only": True,
    }
    try:
        ind = compute_indicators(o1h, tf="1h")
        if ind:
            coin["rsi"] = ind.get("rsi")
            if ind.get("atr") and price:
                coin["atr_pct"] = float(ind["atr"]) / price * 100.0
            coin["macd_signal"] = ind.get("macd_signal") or "neutral"
            if ind.get("ema_fast") is not None:
                coin["ema_fast"] = ind.get("ema_fast")
            if ind.get("ema_slow") is not None:
                coin["ema_slow"] = ind.get("ema_slow")
    except Exception:
        pass
    try:
        from reversal_engine import score_reversal_candidate
        sig = score_reversal_candidate(coin, regime=regime, btc_change_24h=0.0)
        if not sig:
            return None
        try:
            from expected_net_r import net_r_ok
            ok, _ = net_r_ok(sig, None)
            if not ok:
                return None
        except Exception:
            pass
        return sig
    except Exception:
        return None


class EventBacktester:
    def __init__(
        self,
        starting_capital: float = 100.0,
        leverage: float = 10.0,
        fee: float = 0.0006,
        slip: float = 0.0003,
        max_positions: int = 5,
        risk_pct: float = 0.005,
        risk_pct_max: float = 0.0075,
        max_portfolio_risk: float = 0.025,
        trail_activate: float = 0.04,
        trail_dist: float = 0.03,
        daily_loss_limit: float = 0.04,
        max_dd_halt: float = 0.15,
        funding_rate: float = 0.00005,
        funding_every: int = 8,
    ):
        self.starting_capital = starting_capital
        self.leverage = leverage
        self.fee = fee
        self.slip = slip
        self.max_positions = max_positions
        self.risk_pct = risk_pct
        self.risk_pct_max = risk_pct_max
        self.max_portfolio_risk = max_portfolio_risk
        self.trail_activate = trail_activate
        self.trail_dist = trail_dist
        self.daily_loss_limit = daily_loss_limit
        self.max_dd_halt = max_dd_halt
        self.funding_rate = funding_rate
        self.funding_every = funding_every

    def _bar_slip(self, notional_usd: float, ohlcv: dict, i: int, price: float, atr: float = None) -> float:
        """
        E. Realistyczny slippage:
          slip = f(notional, volume, ATR, liquidity)
        Model: orderbook_impact.estimate_bar_slippage
          participation = notional / bar_volume_usd
          impact ≈ k * participation^0.6
          vol_buffer ≈ 0.15 * atr_pct
          liquidity: niski wolumen → wyższy slip (wbudowane w participation)
        """
        try:
            from orderbook_impact import estimate_bar_slippage
            vols = (ohlcv or {}).get("volumes") or []
            vol = float(vols[i]) if 0 <= i < len(vols) else 0.0
            # liquidity lookback: średni vol 20 barów
            if i >= 5 and vols:
                window = [float(v or 0) for v in vols[max(0, i - 20):i + 1]]
                avg_vol = sum(window) / max(len(window), 1)
                # jeśli bieżący bar << średniej → traktuj jako cieńszą płynność
                if avg_vol > 0 and vol < avg_vol * 0.4:
                    vol = max(vol, avg_vol * 0.25)  # nie zero, ale kara przez participation
            px = float(price or 0)
            atr_pct = (float(atr) / px * 100.0) if (atr and px) else None
            est = estimate_bar_slippage(
                notional_usd=float(notional_usd or 0),
                bar_volume_base=vol,
                price=px,
                atr_pct=atr_pct,
                base_slip=self.slip,
            )
            return float(est.get("slip", self.slip))
        except Exception:
            return float(self.slip)

    def _risk_pct(self, strength: float, signal: dict = None) -> float:
        """Identyczna mapa co risk_manager.calculate_position_size (trend vs reversal)."""
        sig = signal or {}
        is_rev = sig.get("engine") == "reversal" or sig.get("setup") == "reversal_confirmed"
        if is_rev:
            risk_lo = float(_cfg_get("REVERSAL_RISK_PCT_MIN", 0.0025))
            risk_hi = float(_cfg_get("REVERSAL_RISK_PCT_MAX", 0.0050))
        else:
            risk_lo = float(_cfg_get("RISK_PCT_MIN", self.risk_pct if self.risk_pct else 0.0050))
            risk_hi = float(_cfg_get("RISK_PCT_MAX", self.risk_pct_max if self.risk_pct_max else 0.0075))
        if bool(_cfg_get("SIZE_BY_STRENGTH", True)):
            st = float(strength or 0)
            lo = float(_cfg_get("SIZE_STRENGTH_FLOOR", _cfg_get("MIN_SIGNAL_STRENGTH", 0.55)))
            hi = float(_cfg_get("SIZE_STRENGTH_CAP", 1.0))
            if hi > lo:
                t = max(0.0, min(1.0, (st - lo) / (hi - lo)))
            else:
                t = 1.0
            risk_pct = risk_lo + t * (risk_hi - risk_lo)
        else:
            risk_pct = float(_cfg_get("RISK_PCT_DEFAULT", 0.005))
        sig = signal or {}
        if sig.get("ohlcv_source") == "proxy_4h" or sig.get("proxy") or sig.get("proxy_4h"):
            risk_pct *= float(_cfg_get("PROXY_4H_RISK_MULT", 0.70))
        if sig.get("cross_market_risk_mult"):
            risk_pct *= float(sig["cross_market_risk_mult"])
        if sig.get("degraded_1d"):
            risk_pct *= float(_cfg_get("DEGRADED_1D_RISK_MULT", 0.75))
        return risk_pct

    def _size(self, cash, equity, open_risk, entry, sl, strength):
        sl_dist = abs(entry - sl) / entry if entry else 0
        if sl_dist <= 1e-8:
            return 0.0, 0.0
        rp = self._risk_pct(strength)
        risk_usd = equity * rp
        room = equity * self.max_portfolio_risk - open_risk
        if room <= 0:
            return 0.0, 0.0
        risk_usd = min(risk_usd, room)
        notional = risk_usd / sl_dist
        margin = notional / self.leverage
        if margin > cash * 0.9:
            notional = cash * 0.9 * self.leverage
            risk_usd = notional * sl_dist
        return max(0.0, notional), max(0.0, risk_usd)

    def run_mtf(
        self,
        bundles: Dict[str, Dict[str, dict]],
        drive_tf: str = "1h",
        warmup: int = 60,
        confirm_bundles: Dict[str, Dict[str, dict]] = None,
        primary_source: str = "binance",
        funding_schedules: Dict[str, list] = None,
        evaluation_start_ts: int = None,
        evaluation_end_ts: int = None,
    ) -> BTResult:
        """
        bundles: {symbol: {"15m": ohlcv, "1h": ohlcv, "4h": ohlcv, "1d": ohlcv}}
        Drive loop on drive_tf (default 1h) for exit resolution;
        signals evaluated with full MTF at each drive bar close.
        """
        if not bundles:
            return BTResult(notes=["no_bundles"])

        # Build drive timeline from first symbol that has drive_tf
        drive_sym = None
        drive_ohlcv = None
        for sym, b in bundles.items():
            if drive_tf in b and len((b[drive_tf].get("closes") or [])) > warmup + 10:
                drive_sym = sym
                drive_ohlcv = b[drive_tf]
                break
        if drive_ohlcv is None:
            # fallback: use any 1h
            for sym, b in bundles.items():
                for tf in ("1h", "4h", "15m"):
                    if tf in b and len((b[tf].get("closes") or [])) > warmup + 10:
                        drive_tf = tf
                        drive_sym = sym
                        drive_ohlcv = b[tf]
                        break
                if drive_ohlcv:
                    break
        if drive_ohlcv is None:
            return BTResult(notes=["no_drive_tf"])

        drive_ts = drive_ohlcv.get("ts") or list(range(len(drive_ohlcv["closes"])))
        n = len(drive_ohlcv["closes"])
        symbols = list(bundles.keys())

        cash = float(self.starting_capital)
        positions: List[BTPosition] = []
        pending: List[PendingEntry] = []
        trades: List[dict] = []
        equity_curve: List[float] = []
        peak = cash
        max_dd = 0.0
        day_start_eq = cash
        bars_in_day = 0
        halted = False
        notes: List[str] = []
        funnel = {"checked": 0, "mtf_ok": 0}

        def price_at(sym: str, i: int, field: str = "closes") -> Optional[float]:
            """Map drive index → symbol price via timestamp or fallback last."""
            b = bundles.get(sym) or {}
            # prefer same drive_tf
            o = b.get(drive_tf) or b.get("1h") or b.get("4h") or {}
            arr = o.get(field) or o.get("closes") or []
            ts_arr = o.get("ts") or []
            if ts_arr and i < len(drive_ts):
                target = drive_ts[i]
                # find last bar with ts <= target
                idx = 0
                for j, t in enumerate(ts_arr):
                    if int(t) <= int(target):
                        idx = j
                    else:
                        break
                if idx < len(arr):
                    return float(arr[idx])
            if i < len(arr):
                return float(arr[i])
            return float(arr[-1]) if arr else None

        def mark_equity(i: int) -> float:
            upl = locked = 0.0
            for pos in positions:
                px = price_at(pos.symbol, i, "closes")
                if px is None:
                    continue
                if pos.direction == "LONG":
                    upl += pos.size_usd * (px - pos.entry) / pos.entry
                else:
                    upl += pos.size_usd * (pos.entry - px) / pos.entry
                upl -= pos.funding_paid
                locked += pos.size_usd / self.leverage
            return cash + locked + upl

        def close_pos(pos, exit_px, reason, bar, slip_one_way=None):
            nonlocal cash
            s = float(slip_one_way if slip_one_way is not None else self.slip)
            # Entry is already adversely repriced. Apply exactly one adverse
            # exit reprice and keep fees separate.
            executed_exit = float(exit_px) * (1 - s if pos.direction == "LONG" else 1 + s)
            ch = ((executed_exit - pos.entry) / pos.entry) if pos.direction == "LONG" else ((pos.entry - executed_exit) / pos.entry)
            gross = pos.size_usd * ch
            cost = pos.size_usd * self.fee * 2
            net = gross - cost - pos.funding_paid
            cash += pos.size_usd / self.leverage + net
            r = net / pos.risk_usd if pos.risk_usd > 0 else 0.0
            trades.append({
                "symbol": pos.symbol, "dir": pos.direction,
                "entry": pos.entry, "exit": executed_exit, "reason": reason,
                "net": round(net, 4), "r": round(r, 4),
                "bars": bar - pos.entry_bar, "strength": pos.strength,
                "mtf_votes": pos.mtf_votes,
                "engine": pos.engine,
                "liquidity_bucket": pos.liquidity_bucket,
                "preferred_engine": pos.preferred_engine,
                "residual_momentum_24h": pos.residual_momentum_24h,
                "expected_r_status": pos.expected_r_status,
            })

        # earliest usable index: all key TFs must have data at this ts
        def _ready(ts_ms: int) -> bool:
            # Core TF only (15m optional)
            checks = [("1h", 60), ("4h", 210)]
            # 1d if present
            if (bundles.get(drive_sym) or {}).get("1d"):
                checks.append(("1d", 210))
            for tf, need in checks:
                o = (bundles.get(drive_sym) or {}).get(tf) or {}
                win = window_until(o, ts_ms, tf=tf)
                if len(win.get("closes") or []) < need:
                    return False
            return True
        start_i = warmup
        for j in range(warmup, n):
            if _ready(int(drive_ts[j]) + _tf_ms(drive_tf)):
                start_i = j
                break
        notes.append(f"drive_start={start_i}/{n} tf={drive_tf}")

        for i in range(start_i, n):
            ts_ms = int(drive_ts[i]) if i < len(drive_ts) else i
            # Exchange timestamps identify bar opens. Signals are formed only
            # after the drive bar closes, then executed at the next bar open.
            decision_ts_ms = ts_ms + _tf_ms(drive_tf)

            # --- 1) execute pending at open[i] ---
            open_syms = {p.symbol for p in positions}
            open_risk = sum(p.risk_usd for p in positions)
            equity = mark_equity(i - 1) if i > warmup else cash
            new_pending = []
            for pe in pending:
                if pe.symbol in open_syms or len(positions) >= self.max_positions or halted:
                    continue
                raw_open = price_at(pe.symbol, i, "opens")
                if raw_open is None:
                    continue
                # provisional entry at base slip → size → reprice with volume/ATR model
                if pe.direction == "LONG":
                    entry0 = raw_open * (1 + self.slip)
                else:
                    entry0 = raw_open * (1 - self.slip)
                sl0 = entry0 * (1 - pe.sl_dist) if pe.direction == "LONG" else entry0 * (1 + pe.sl_dist)
                if pe.risk_pct and pe.risk_pct > 0:
                    sl_dist = abs(entry0 - sl0) / entry0 if entry0 else pe.sl_dist
                    if sl_dist <= 1e-8:
                        continue
                    risk_usd = equity * float(pe.risk_pct)
                    room = equity * self.max_portfolio_risk - open_risk
                    if room <= 0:
                        continue
                    risk_usd = min(risk_usd, room)
                    notional = risk_usd / sl_dist
                    margin = notional / self.leverage
                    if margin > cash * 0.9:
                        notional = cash * 0.9 * self.leverage
                        risk_usd = notional * sl_dist
                else:
                    notional, risk_usd = self._size(cash, equity, open_risk, entry0, sl0, pe.strength)
                if notional <= 0:
                    continue
                # Dynamic slip from bar volume + ATR (model, not real OB)
                drive_o = (bundles.get(pe.symbol) or {}).get(drive_tf) or {}
                atr_est = float(pe.sl_dist) * entry0 / 2.0  # approx from SL distance
                slip_e = self._bar_slip(notional, drive_o, i, raw_open, atr=atr_est)
                if pe.direction == "LONG":
                    entry = raw_open * (1 + slip_e)
                    sl = entry * (1 - pe.sl_dist)
                    tp = entry * (1 + pe.tp_dist) if pe.tp_dist else None
                else:
                    entry = raw_open * (1 - slip_e)
                    sl = entry * (1 + pe.sl_dist)
                    tp = entry * (1 - pe.tp_dist) if pe.tp_dist else None
                margin = notional / self.leverage
                if margin > cash:
                    continue
                cash -= margin
                positions.append(BTPosition(
                    symbol=pe.symbol, direction=pe.direction, entry=entry,
                    size_usd=notional, sl=sl, tp=tp, strength=pe.strength,
                    entry_bar=i, risk_usd=risk_usd, highest=entry, lowest=entry,
                    reasons=pe.reasons, mtf_votes=pe.mtf_votes,
                    entry_ts_ms=ts_ms, last_funding_ts=ts_ms,
                    engine=pe.engine,
                    liquidity_bucket=pe.liquidity_bucket,
                    preferred_engine=pe.preferred_engine,
                    residual_momentum_24h=pe.residual_momentum_24h,
                    expected_r_status=pe.expected_r_status,
                ))
                open_syms.add(pe.symbol)
                open_risk += risk_usd
            pending = new_pending

            # --- 2) manage positions: SL first, then trail ---
            still = []
            for pos in positions:
                hi = price_at(pos.symbol, i, "highs")
                lo = price_at(pos.symbol, i, "lows")
                px = price_at(pos.symbol, i, "closes")
                if hi is None or lo is None or px is None:
                    still.append(pos)
                    continue
                hit = False
                exit_px = reason = None
                if pos.direction == "LONG":
                    if lo <= pos.sl:
                        hit, exit_px, reason = True, pos.sl, "SL"
                    elif pos.tp is not None and hi >= pos.tp:
                        hit, exit_px, reason = True, pos.tp, "TP"
                else:
                    if hi >= pos.sl:
                        hit, exit_px, reason = True, pos.sl, "SL"
                    elif pos.tp is not None and lo <= pos.tp:
                        hit, exit_px, reason = True, pos.tp, "TP"
                if hit:
                    close_pos(pos, exit_px, reason, i)
                    continue
                # Real historical funding settlements
                sched = (funding_schedules or {}).get(pos.symbol) or []
                if sched and pos.entry_ts_ms:
                    extra, new_last = funding_paid_between(
                        sched, pos.entry_ts_ms, ts_ms, pos.direction,
                        pos.size_usd, pos.last_funding_ts,
                    )
                    if extra:
                        pos.funding_paid += extra
                    pos.last_funding_ts = new_last
                elif self.funding_rate and self.funding_every and i % self.funding_every == 0:
                    # fallback synthetic only when no schedule
                    pos.funding_paid += pos.size_usd * self.funding_rate * (
                        1 if pos.direction == "LONG" else -1
                    )
                if pos.direction == "LONG":
                    pos.highest = max(pos.highest or pos.entry, hi)
                    if (px - pos.entry) / pos.entry >= self.trail_activate:
                        pos.trail_active = True
                        pos.sl = max(pos.sl, pos.highest * (1 - self.trail_dist))
                else:
                    pos.lowest = min(pos.lowest or pos.entry, lo)
                    if (pos.entry - px) / pos.entry >= self.trail_activate:
                        pos.trail_active = True
                        pos.sl = min(pos.sl, pos.lowest * (1 + self.trail_dist))
                still.append(pos)
            positions = still

            # --- 3) equity / halts ---
            equity = mark_equity(i)
            equity_curve.append(equity)
            peak = max(peak, equity)
            dd = (peak - equity) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)
            bars_in_day += 1
            if bars_in_day >= 24:
                day_start_eq = equity
                bars_in_day = 0
            daily_loss = (day_start_eq - equity) / day_start_eq if day_start_eq > 0 else 0
            if daily_loss >= self.daily_loss_limit or dd >= self.max_dd_halt:
                if not halted:
                    notes.append(f"HALT@{i} daily={daily_loss:.2%} dd={dd:.2%}")
                halted = True

            # --- 4) MTF signals on close[i] → pending open[i+1] ---
            if halted or len(positions) >= self.max_positions:
                continue
            if evaluation_start_ts is not None and decision_ts_ms < int(evaluation_start_ts):
                continue
            if evaluation_end_ts is not None and decision_ts_ms > int(evaluation_end_ts):
                continue
            open_syms = {p.symbol for p in positions}
            pending_syms = {p.symbol for p in pending}
            change24_map = {}
            for rsym in symbols:
                rwin = window_until((bundles.get(rsym) or {}).get("1h") or {}, decision_ts_ms, tf="1h")
                rv = _pct_change(rwin, 24)
                if rv is not None:
                    change24_map[rsym] = rv
            try:
                from engine_router import universe_market_return
                market_ret_24h = universe_market_return(
                    [{"change_24h": v} for v in change24_map.values()]
                )
            except Exception:
                market_ret_24h = None
            btc_ret_24h = float(change24_map.get("BTC") or 0.0)
            for sym in symbols:
                if sym in open_syms or sym in pending_syms:
                    continue
                bundle = bundles.get(sym) or {}
                if not bundle:
                    continue
                funnel["checked"] += 1
                try:
                    sig = live_mtf_signal(
                        sym, bundle, decision_ts_ms,
                        confirm_bundle=(confirm_bundles or {}).get(sym),
                        primary_source=primary_source,
                        funding_schedule=(funding_schedules or {}).get(sym),
                    )
                except Exception as e:
                    notes.append(f"sig_err {sym}: {e}")
                    continue
                # Dual engine: TREND + REVERSAL
                candidates = []
                if sig:
                    sig.setdefault("engine", "trend")
                    candidates.append(sig)
                try:
                    rev = bt_reversal_signal_from_window(sym, bundle, decision_ts_ms)
                    if rev:
                        candidates.append(rev)
                except Exception:
                    pass
                if not candidates:
                    continue
                for candidate in candidates:
                    candidate["change_24h"] = change24_map.get(sym)
                    try:
                        from engine_router import annotate_residual_momentum, route_signal
                        annotate_residual_momentum(candidate, btc_ret_24h, market_ret_24h)
                        route_signal(candidate)
                    except Exception:
                        pass
                candidates.sort(
                    key=lambda s: float(s.get("strength") or 0)
                    + float(s.get("engine_preference_score") or 0),
                    reverse=True,
                )
                sig = candidates[0]
                funnel["mtf_ok"] += 1
                sp = float(sig["price"])
                slp = float(sig["sl_price"])
                tpp = sig.get("tp_price")
                sl_dist = abs(sp - slp) / sp
                tp_dist = abs(float(tpp) - sp) / sp if tpp is not None else None
                rp = self._risk_pct(float(sig.get("strength") or 0), sig)
                pending.append(PendingEntry(
                    symbol=sym, direction=sig["direction"], signal_bar=i,
                    strength=float(sig["strength"]), sl_dist=sl_dist, tp_dist=tp_dist,
                    reasons=list(sig.get("reasons") or []), signal_price=sp,
                    mtf_votes=int(sig.get("mtf_votes") or 0),
                    risk_mult=float(sig.get("cross_market_risk_mult") or 1.0),
                    risk_pct=rp,
                    expected_r=float(sig.get("expected_r") or 0),
                    expected_net_r=float(sig.get("expected_net_r") or 0),
                    engine=str(sig.get("engine") or "trend"),
                    market_regime=str(sig.get("market_regime") or "UNKNOWN"),
                    liquidity_bucket=str(sig.get("liquidity_bucket") or "UNKNOWN"),
                    preferred_engine=str(sig.get("preferred_engine") or ""),
                    residual_momentum_24h=float(sig.get("residual_momentum_24h") or 0),
                    expected_r_status=str(sig.get("expected_r_status") or "UNKNOWN"),
                ))
                # diagnostics already in funnel mtf_ok
                pending_syms.add(sym)

        # EOD
        for pos in list(positions):
            px = price_at(pos.symbol, n - 1, "closes") or pos.entry
            close_pos(pos, px, "EOD", n - 1)
        positions = []
        final_equity = cash
        if equity_curve:
            equity_curve.append(final_equity)

        wins = [t for t in trades if t["net"] > 0]
        losses = [t for t in trades if t["net"] <= 0]
        gp = sum(t["net"] for t in wins) or 0
        gl = abs(sum(t["net"] for t in losses)) or 1e-9
        avg_r = sum(t["r"] for t in trades) / len(trades) if trades else 0
        by_sym: Dict[str, dict] = {}
        for t in trades:
            s = t["symbol"]
            by_sym.setdefault(s, {"n": 0, "net": 0.0, "wins": 0})
            by_sym[s]["n"] += 1
            by_sym[s]["net"] += t["net"]
            if t["net"] > 0:
                by_sym[s]["wins"] += 1

        notes.append('funnel: checked=%s mtf_ok=%s trades=%s' % (funnel['checked'], funnel['mtf_ok'], len(trades)))
        return BTResult(
            equity_curve=equity_curve, trades=trades, final_equity=final_equity,
            max_dd=max_dd, n_trades=len(trades),
            win_rate=len(wins) / len(trades) if trades else 0,
            profit_factor=gp / gl, avg_r=avg_r, expectancy_r=avg_r,
            notes=notes, by_symbol=by_sym,
            by_engine={
                "trend": _bt_stats([x for x in trades if (x.get("engine") or "trend") == "trend"]),
                "reversal": _bt_stats([x for x in trades if x.get("engine") == "reversal"]),
                "combined": _bt_stats(trades),
            },
        )

    # backward compat
    def run_multi(self, series: Dict[str, dict], tf: str = "1h", warmup: int = 60) -> BTResult:
        bundles = {sym: {tf: ohlcv} for sym, ohlcv in series.items()}
        return self.run_mtf(bundles, drive_tf=tf, warmup=warmup)

    def run(self, ohlcv: dict, signal_fn=None, funding_rate: float = 0.0, funding_every_bars: int = 8) -> BTResult:
        self.funding_rate = funding_rate or self.funding_rate
        self.funding_every = funding_every_bars or self.funding_every
        return self.run_multi({"SYM": ohlcv})


def run_cli():
    # PRIORYTET 14 — assert shared LIVE modules before any run
    try:
        from bt_parity import print_parity_banner
        print_parity_banner()
    except Exception as e:
        print(f"[BT parity] skip banner: {e}")

    ap = argparse.ArgumentParser(description="CryptoEdge Backtest Engine v2.2 LIVE-parity")
    ap.add_argument("--symbols", default="BTC,ETH")
    ap.add_argument("--drive-tf", default="1h", help="bar resolution for exits (1h recommended)")
    ap.add_argument("--capital", type=float, default=100.0)
    ap.add_argument("--leverage", type=float, default=10.0)
    ap.add_argument("--max-pos", type=int, default=5)
    # BloFin PRIMARY (jak LIVE); Binance = confirmation gdy auto/blofin
    ap.add_argument("--data-source", default="auto", choices=["binance", "blofin", "auto"],
                    help="PRIMARY klines: blofin|binance|auto (prefer blofin)")
    ap.add_argument("--limit-1h", type=int, default=1500)
    ap.add_argument("--limit-4h", type=int, default=500)
    ap.add_argument("--limit-1d", type=int, default=400)
    ap.add_argument("--limit-15m", type=int, default=1500)
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    limits = {
        "15m": args.limit_15m,
        "1h": args.limit_1h,
        "4h": args.limit_4h,
        "1d": args.limit_1d,
    }

    print(f"[BT] MTF backtest | source={args.data_source} | primary={_cfg_get('STRATEGY_PRIMARY_TF','4h')} | filter={_cfg_get('STRATEGY_FILTER_TF','1d')} | align>={_cfg_get('MTF_REQUIRE_ALIGN',2)}")
    bundles = {}
    for sym in symbols:
        print(f"[BT] fetching MTF bundle for {sym}...")
        b = fetch_mtf_bundle(sym, source=args.data_source, limits=limits)
        if "1h" in b or "4h" in b:
            bundles[sym] = b
        else:
            print(f"  SKIP {sym} (insufficient TF data)")

    if not bundles:
        print("[BT] no data")
        return

    # Confirmation venue: if primary=blofin → fetch Binance as independent check
    confirm_bundles = {}
    primary_source = args.data_source
    if args.data_source in ("blofin", "bf", "auto"):
        print("[BT] fetching Binance confirmation bundles...")
        for sym in list(bundles.keys()):
            cb = fetch_mtf_bundle(sym, source="binance", limits=limits)
            if cb:
                confirm_bundles[sym] = cb
        print(f"[BT] confirmation symbols: {list(confirm_bundles.keys())}")
    else:
        # primary=binance → confirmation = same data (aligned by construction)
        confirm_bundles = bundles

    bt = EventBacktester(
        starting_capital=args.capital, leverage=args.leverage,
        max_positions=args.max_pos, fee=0.0006, slip=0.0003,
        risk_pct=0.005, risk_pct_max=0.0075, max_portfolio_risk=0.025,
    )
    funding_schedules = {}
    print("[BT] fetching funding rate history (BloFin)...")
    for sym in list(bundles.keys()):
        sched = fetch_funding_schedule(sym, limit=300, source="blofin")
        funding_schedules[sym] = sched
        print(f"  {sym} funding events: {len(sched)}")
        time.sleep(0.08)

    res = bt.run_mtf(
        bundles, drive_tf=args.drive_tf,
        confirm_bundles=confirm_bundles,
        primary_source=primary_source,
        funding_schedules=funding_schedules,
    )

    print("\n========== BACKTEST v2.2 DUAL ENGINE ==========")
    print("Mode: TREND + REVERSAL | MTF 15m/1H/4H/1D | Net R | next-bar entry")
    print(f"Data source: {args.data_source} | confirmation: {'binance' if args.data_source!='binance' else 'self(binance)'}")
    print(f"Symbols: {list(bundles.keys())}")
    print(f"Final equity: ${res.final_equity:.2f} (start ${args.capital:.2f})")
    print(f"Return: {(res.final_equity/args.capital - 1)*100:+.2f}%")
    print(f"Portfolio Max DD: {res.max_dd*100:.2f}%")

    def _print_engine(name, st):
        if not st or st.get("n", 0) == 0:
            print(f"\n{name}:\n  (brak transakcji)")
            return
        print(f"\n{name}:")
        print(f"  Trades:       {st['n']}")
        print(f"  Expectancy:   {st['expectancy_r']:+.3f} R")
        print(f"  Win rate:     {st['win_rate']*100:.1f}%")
        print(f"  Profit factor:{st['profit_factor']:.2f}")
        print(f"  Average R:    {st['avg_r']:+.3f}")
        print(f"  Net PnL:      ${st['net']:+.2f}")
        print(f"  Max DD (R):   {st['max_dd_r']:.2f}")

    be = res.by_engine or {}
    _print_engine("Trend", be.get("trend"))
    _print_engine("Reversal", be.get("reversal"))
    _print_engine("Combined", be.get("combined") or {
        "n": res.n_trades, "expectancy_r": res.avg_r, "win_rate": res.win_rate,
        "profit_factor": res.profit_factor, "avg_r": res.avg_r, "net": res.final_equity - args.capital,
        "max_dd_r": 0.0,
    })
    if res.notes:
        print(f"Notes: {res.notes[:5]}")
    if res.by_symbol:
        print("By symbol:")
        for s, st in sorted(res.by_symbol.items(), key=lambda x: -x[1]["net"]):
            wr = st["wins"] / st["n"] * 100 if st["n"] else 0
            print(f"  {s:8} n={st['n']:3} net=${st['net']:+.2f} WR={wr:.0f}%")
    if res.trades:
        print("\nTrades:")
        for t in res.trades:
            print(f"  {t['dir']:5} {t['symbol']:6} R={t['r']:+.2f} net=${t['net']:+.2f} mtf={t.get('mtf_votes','?')} {t['reason']}")

    try:
        import os
        os.makedirs("logs", exist_ok=True)
        with open("logs/backtest_last.json", "w", encoding="utf-8") as f:
            json.dump({
                "version": "2.2-dual-engine",
                "data_source": args.data_source,
                "final_equity": res.final_equity, "max_dd": res.max_dd,
                "n_trades": res.n_trades, "win_rate": res.win_rate,
                "profit_factor": res.profit_factor, "avg_r": res.avg_r,
                "trades": res.trades, "by_symbol": res.by_symbol,
                "by_engine": res.by_engine, "notes": res.notes,
            }, f, indent=2)
        print("\nSaved logs/backtest_last.json")
        try:
            from performance_metrics import full_report, print_report, save_report
            rep = full_report(
                res.trades,
                equity_curve=getattr(res, "equity_curve", None),
                starting_equity=args.capital,
            )
            # merge by_engine if present
            if getattr(res, "by_engine", None):
                rep["by_engine_legacy"] = res.by_engine
            print_report(rep)
            save_report(rep)
            print("Saved logs/performance_last.json")
        except Exception as e:
            print(f"metrics: {e}")
    except Exception as e:
        print(f"save: {e}")


if __name__ == "__main__":
    run_cli()
