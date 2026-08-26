# ============================================================
# ETAP 3 — Market-data correctness
# 15. closed candles only
# 16. poprawna agregacja 4H (alignment do granic UTC)
# 17. stale-data detection (ticker + klines)
# 18. Binance/BloFin divergence gate
# 19. spójność symboli / instrumentów
# ============================================================

from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple, Any

import config

# Interwały w ms
_INTERVAL_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "1H": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "4H": 14_400_000,
    "6h": 21_600_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "1D": 86_400_000,
}


def interval_ms(interval: str) -> int:
    return int(_INTERVAL_MS.get(interval, 3_600_000))


def drop_unclosed_candle(
    ohlcv: dict,
    interval: str,
    now_ms: int = None,
    safety_lag_ms: int = 2000,
) -> dict:
    """
    Usuwa ostatnią świecę jeśli jeszcze się nie zamknęła.
    Binance kline open time + interval <= now → zamknięta.
    Bez timestamps: usuń ostatnią (konserwatywnie), chyba że CLOSED_CANDLES_STRICT=False.
    """
    if not ohlcv:
        return {}
    closes = list(ohlcv.get("closes") or [])
    if not closes:
        return dict(ohlcv)

    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    ts_list = list(ohlcv.get("timestamps") or [])
    ims = interval_ms(interval)

    def _trim(n_drop: int):
        if n_drop <= 0:
            return dict(ohlcv)
        out = {}
        for k, v in ohlcv.items():
            if isinstance(v, list) and len(v) == len(closes):
                out[k] = v[:-n_drop] if n_drop < len(v) else []
            else:
                out[k] = v
        out["closed_only"] = True
        out["dropped_unclosed"] = n_drop
        return out

    if ts_list and len(ts_list) == len(closes):
        # ostatnia świeca zamknięta gdy open_time + interval <= now - lag
        last_open = int(ts_list[-1])
        # timestamps mogą być w sekundach
        if last_open < 1e12:
            last_open *= 1000
        close_at = last_open + ims
        if close_at > (now_ms - safety_lag_ms):
            return _trim(1)
        out = dict(ohlcv)
        out["closed_only"] = True
        out["dropped_unclosed"] = 0
        return out

    # brak timestampów – konserwatywnie odetnij ostatnią
    if bool(getattr(config, "CLOSED_CANDLES_STRICT", True)):
        return _trim(1)
    out = dict(ohlcv)
    out["closed_only"] = False
    out["dropped_unclosed"] = 0
    return out


def aggregate_to_4h(
    ohlcv_1h: dict,
    drop_incomplete_bucket: bool = True,
) -> dict:
    """
    Poprawna agregacja 1h → 4h:
    - grupowanie po granicy UTC 4h (0,4,8,12,16,20)
    - wymaga timestamps
    - niepełny bieżący bucket 4h jest odrzucany
    """
    closes = list(ohlcv_1h.get("closes") or [])
    opens = list(ohlcv_1h.get("opens") or [])
    highs = list(ohlcv_1h.get("highs") or [])
    lows = list(ohlcv_1h.get("lows") or [])
    volumes = list(ohlcv_1h.get("volumes") or [])
    ts_list = list(ohlcv_1h.get("timestamps") or [])
    n = len(closes)
    if n < 20:
        return {}

    m = min(n, len(highs), len(lows))
    if volumes:
        m = min(m, len(volumes))
    else:
        volumes = [0.0] * m
    if opens:
        m = min(m, len(opens))
    else:
        opens = [closes[max(0, i - 1)] for i in range(m)]
    closes, opens, highs, lows, volumes = closes[:m], opens[:m], highs[:m], lows[:m], volumes[:m]

    # timestamps
    if len(ts_list) >= m:
        ts_ms = []
        for t in ts_list[:m]:
            t = int(t)
            if t < 1e12:
                t *= 1000
            ts_ms.append(t)
    else:
        # BRAK timestampów → nie generuj sztucznej 4H (fałszywy alignment UTC)
        return {}

    four_h = 14_400_000
    buckets: Dict[int, list] = {}
    order: List[int] = []
    for i in range(m):
        bucket = (ts_ms[i] // four_h) * four_h
        if bucket not in buckets:
            buckets[bucket] = []
            order.append(bucket)
        buckets[bucket].append(i)

    out_o, out_c, out_h, out_l, out_v, out_ts = [], [], [], [], [], []
    now_ms = int(time.time() * 1000)
    current_bucket = (now_ms // four_h) * four_h

    for b in order:
        idxs = buckets[b]
        if len(idxs) < 1:
            continue
        # niepełny bucket bieżący (jeszcze trwa 4h)
        if drop_incomplete_bucket and b >= current_bucket:
            continue
        # historyczny bucket powinien mieć 4 świece 1h; tolerancja ≥2
        if len(idxs) < 2 and drop_incomplete_bucket:
            continue
        out_o.append(opens[idxs[0]])
        out_c.append(closes[idxs[-1]])
        out_h.append(max(highs[i] for i in idxs))
        out_l.append(min(lows[i] for i in idxs))
        out_v.append(sum(volumes[i] for i in idxs))
        out_ts.append(b)

    if len(out_c) < 5:
        return {}
    return {
        "opens": out_o, "closes": out_c,
        "highs": out_h,
        "lows": out_l,
        "volumes": out_v,
        "timestamps": out_ts,
        "closed_only": True,
        "aggregated_from": "1h",
        "factor": 4,
    }


# ------------------------------------------------------------------
# 17. Stale data
# ------------------------------------------------------------------
class StaleTracker:
    """Per-source / per-symbol freshness."""

    def __init__(self):
        self._ts: Dict[str, float] = {}
        self.last_reasons: List[str] = []

    def touch(self, key: str):
        self._ts[key] = time.monotonic()

    def age(self, key: str) -> float:
        t = self._ts.get(key)
        if not t:
            return 99999.0
        return time.monotonic() - t

    def is_stale(self, key: str, max_age: float = None) -> bool:
        max_age = max_age if max_age is not None else float(
            getattr(config, "STALE_DATA_SECONDS", 45)
        )
        return self.age(key) > max_age

    def check_trade_allowed(
        self,
        require_keys: List[str] = None,
        max_age: float = None,
    ) -> Tuple[bool, str]:
        """
        Zwraca (ok, reason).
        require_keys np. ['ticker:universe', 'ticker:binance']
        """
        max_age = max_age if max_age is not None else float(
            getattr(config, "STALE_DATA_SECONDS", 45)
        )
        require_keys = require_keys or ["ticker:universe"]
        self.last_reasons = []
        for k in require_keys:
            if self.is_stale(k, max_age):
                msg = f"STALE:{k} age={self.age(k):.0f}s>{max_age:.0f}s"
                self.last_reasons.append(msg)
                return False, msg
        return True, "ok"


# globalny tracker (współdzielony)
STALE = StaleTracker()


# ------------------------------------------------------------------
# 18. Divergence gate (Binance vs Blofin priorytet)
# ------------------------------------------------------------------
def binance_blofin_divergence(coin: dict) -> dict:
    """
    Skupienie na BN vs BF – to są źródła egzekucji / uniwersum.
    Kara / hard block — nie tylko log (PRIORYTET 12).
    """
    bn = coin.get("binance_price")
    bf = coin.get("blofin_price")
    # source_div z korelacji źródeł (CG/BN/BY/BF)
    src = coin.get("source_div") if isinstance(coin.get("source_div"), dict) else {}
    if bn is None and src.get("binance") is not None:
        bn = src.get("binance")
    if bf is None and src.get("blofin") is not None:
        bf = src.get("blofin")
    # fallback: price z uniwersum Blofin
    if bf is None and coin.get("price") is not None:
        bf = coin.get("price")
    # legacy max_diff z multi-source — użyj jako proxy gdy brak BN tickera
    if bn is None and bf is not None and src.get("max_diff_pct") is not None:
        try:
            md = float(src["max_diff_pct"])
            # syntetyczny "bn" tylko do policzenia diff (kara i tak działa)
            if md > 0 and float(bf) > 0:
                bn = float(bf) * (1.0 + md / 100.0)
        except (TypeError, ValueError):
            pass
    if bn is None or bf is None:
        require = bool(getattr(config, "REQUIRE_BN_BF_DIVERGENCE", False))
        return {
            "ok": not require,  # gdy wymagane – blokuj wejście
            "skipped": not require,
            "status": "UNKNOWN",
            "diff_pct": None,
            "binance": bn,
            "blofin": bf,
            "reason": "MISSING_SOURCE",
            "hard": require,
        }
    try:
        bn_f, bf_f = float(bn), float(bf)
        if bn_f <= 0 or bf_f <= 0:
            return {"ok": False, "diff_pct": None, "reason": "BAD_PRICE", "binance": bn, "blofin": bf}
        diff = abs(bn_f - bf_f) / ((bn_f + bf_f) / 2.0) * 100.0
    except (TypeError, ValueError):
        return {"ok": False, "diff_pct": None, "reason": "PARSE", "binance": bn, "blofin": bf}

    soft = float(getattr(config, "BN_BF_DIVERGENCE_SOFT_PCT", 1.0))
    hard = float(getattr(config, "BN_BF_DIVERGENCE_HARD_PCT", 3.0))
    ok = diff < hard
    return {
        "ok": ok,
        "skipped": False,
        "status": "DIVERGED" if diff >= soft else "ALIGNED",
        "diff_pct": round(diff, 4),
        "soft": diff >= soft,
        "hard": diff >= hard,
        "binance": bn_f,
        "blofin": bf_f,
        "reason": "OK" if ok else f"BN_BF_DIV:{diff:.2f}%",
    }


def apply_divergence_gate(signal: dict, coin: dict = None) -> dict:
    """Rozjazd źródeł wyłączony — nic nie karze i nic nie blokuje."""
    import config
    if not bool(getattr(config, "SOURCE_DIVERGENCE_GATE", False)):
        d = binance_blofin_divergence(coin or signal)
        signal["bn_bf_div"] = d
        return signal
    """
    PRIORYTET 11 — BloFin↔Binance divergence jako filtr ryzyka.

    Hierarchia:
      BloFin  = PRIMARY (cena, OB, spread, exec, funding)
      Binance = CONFIRMATION / REFERENCE (trend, anomaly)
      CG      = CONTEXT (sanity)

    soft  → kara strength + mniejszy size
    hard  → NO TRADE (reject)
    """
    c = coin or signal
    d = binance_blofin_divergence(c)
    signal["bn_bf_div"] = d
    # Preferuj cenę BloFin do egzekucji gdy jest
    bf = d.get("blofin")
    if bf is not None and float(bf) > 0:
        signal["exec_price_source"] = "blofin"
        if signal.get("price") is None:
            signal["price"] = float(bf)
    elif d.get("binance") is not None:
        signal["exec_price_source"] = "binance_fallback"

    hard_div = bool(d.get("hard")) and not d.get("skipped")
    missing_block = (
        d.get("reason") == "MISSING_SOURCE"
        and not d.get("ok")
        and bool(getattr(config, "REQUIRE_BN_BF_DIVERGENCE", False))
    )
    diff = d.get("diff_pct")
    try:
        diff_f = float(diff) if diff is not None else None
    except (TypeError, ValueError):
        diff_f = None

    if hard_div or missing_block:
        # HARD BLOCK — artefakt rynku, nie setup
        reason = d.get("reason") or (
            f"BN_BF_DIV_HARD({diff_f:.2f}%)" if diff_f is not None else "BN_BF_DIV_HARD"
        )
        signal["reject_reason"] = signal.get("reject_reason") or reason
        # siła poniżej min → risk_manager też nie wpuści
        min_s = float(getattr(config, "MIN_SIGNAL_STRENGTH", 0.55) or 0.55)
        signal["strength"] = min(float(signal.get("strength") or 0), min_s - 0.01)
        if signal.get("trend_score") is not None:
            signal["trend_score"] = min(float(signal["trend_score"]), min_s - 0.01)
        if signal.get("reversal_score") is not None:
            signal["reversal_score"] = min(float(signal["reversal_score"]), min_s - 0.01)
        signal["reasons"] = list(signal.get("reasons") or []) + [reason, "HARD_BLOCK_CROSS_DIV"]
        signal["_size_mult"] = 0.0
        signal["decision_path"] = "NO_TRADE"
    elif d.get("soft") and diff_f is not None:
        # Kara proporcjonalna do rozjazdu (nie tylko flaga w logu)
        soft_thr = float(getattr(config, "BN_BF_DIVERGENCE_SOFT_PCT", 1.0) or 1.0)
        hard_thr = float(getattr(config, "BN_BF_DIVERGENCE_HARD_PCT", 3.0) or 3.0)
        # 0 at soft → 1 at hard
        span = max(0.01, hard_thr - soft_thr)
        severity = min(1.0, max(0.0, (diff_f - soft_thr) / span))
        pen = 0.05 + 0.15 * severity  # 0.05 … 0.20
        mult_base = float(getattr(config, "CROSS_DIVERGE_RISK_MULT", 0.50) or 0.50)
        # size: soft thr → mult_base; blisko hard → ~0.25
        size_mult = mult_base * (1.0 - 0.5 * severity)
        signal["strength"] = max(0.0, float(signal.get("strength") or 0) - pen)
        if signal.get("trend_score") is not None:
            signal["trend_score"] = max(0.0, float(signal["trend_score"]) - pen)
        if signal.get("reversal_score") is not None:
            signal["reversal_score"] = max(0.0, float(signal["reversal_score"]) - pen)
        signal["reasons"] = list(signal.get("reasons") or []) + [
            f"BN_BF_SOFT({diff_f:.2f}%|pen={pen:.2f}|size×{size_mult:.2f})"
        ]
        signal["_size_mult"] = min(float(signal.get("_size_mult") or 1.0), size_mult)
        signal["_div_penalty"] = {
            "diff_pct": diff_f,
            "severity": round(severity, 3),
            "strength_pen": round(pen, 3),
            "size_mult": round(size_mult, 3),
        }
    return signal


# ------------------------------------------------------------------
# 19. Symbol / instrument consistency
# ------------------------------------------------------------------
def normalize_symbol(raw: str) -> str:
    s = (raw or "").upper().strip()
    if not s:
        return ""
    for sep in ("-", "/", "_"):
        if sep in s:
            s = s.split(sep)[0]
            break
    if s.endswith("USDT"):
        s = s[:-4]
    if s.endswith("USD") and len(s) > 3:
        s = s[:-3]
    # typowe prefiksy kontraktów
    for prefix in ("1000", "1000000"):
        if s.startswith(prefix) and len(s) > len(prefix):
            # zostaw 1000PEPE jako 1000PEPE (osobny instrument)
            break
    return s


def resolve_instrument(symbol: str, registry=None) -> dict:
    """
    Mapuj symbol bota → inst_id Blofin + flagi spójności.
    """
    sym = normalize_symbol(symbol)
    out = {
        "symbol": sym,
        "inst_id": f"{sym}-USDT",
        "in_registry": False,
        "tradable": False,
        "contract_value": None,
        "lot_size": None,
        "tick_size": None,
    }
    if registry is not None:
        try:
            registry.ensure_loaded()
            spec = registry.get(sym)
            if spec:
                out.update({
                    "inst_id": spec.inst_id,
                    "in_registry": True,
                    "tradable": bool(spec.is_tradable),
                    "contract_value": spec.contract_value,
                    "lot_size": spec.lot_size,
                    "tick_size": spec.tick_size,
                    "max_leverage": spec.max_leverage,
                })
        except Exception as e:
            out["error"] = str(e)
    return out


def filter_universe_by_registry(coins: List[dict], registry) -> List[dict]:
    """
    Zostaw tylko symbole obecne i tradable w InstrumentRegistry (Blofin).
    """
    if not registry:
        return coins
    try:
        registry.ensure_loaded()
    except Exception:
        return coins
    out = []
    for c in coins:
        sym = normalize_symbol(c.get("symbol") or "")
        spec = registry.get(sym)
        if not spec or not spec.is_tradable:
            continue
        c = dict(c)
        c["symbol"] = sym
        c["inst_id"] = spec.inst_id
        c["in_registry"] = True
        out.append(c)
    return out
