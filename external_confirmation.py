# ============================================================
# External confirmation — Binance (Tier 2) + CoinGecko (Tier 3)
# BloFin = execution truth; BN/CG NIE generują entry, tylko score.
# ============================================================

from __future__ import annotations
from typing import Dict, Any, Optional


def _f(x, default=None):
    try:
        return float(x) if x is not None else default
    except (TypeError, ValueError):
        return default


def cross_market_confirmation(signal: dict, coin: dict = None) -> Dict[str, Any]:
    """
    Porównuje ruch BloFin vs Binance (i opcjonalnie Bybit/CG).
    Zwraca:
      aligned / neutral / diverge
      score_delta (− / 0 / +)
      reasons
    """
    coin = coin or signal
    bf_ch = _f(coin.get("blofin_change_24h"), _f(coin.get("change_24h"), 0))
    bn_ch = _f(coin.get("binance_change_24h"))
    by_ch = _f(coin.get("bybit_change_24h"))
    cg_ch = _f(coin.get("coingecko_change_24h"), _f(coin.get("cg_change_24h")))

    bf_1h = _f(coin.get("blofin_change_1h"))
    bn_1h = _f(coin.get("binance_change_1h"))

    direction = (signal.get("direction") or "").upper()
    reasons = []
    score_delta = 0.0
    status = "NEUTRAL"
    bf_px = _f(coin.get("blofin_price"))
    bn_px = _f(coin.get("binance_price"))
    bf_ts = _f(coin.get("blofin_ts_ms"))
    bn_ts = _f(coin.get("binance_ts_ms"))
    import config
    max_skew_ms = float(getattr(config, "CROSS_MARKET_MAX_SKEW_SECONDS", 20.0)) * 1000.0
    timestamp_aligned = not (bf_ts is not None and bn_ts is not None and abs(bf_ts - bn_ts) > max_skew_ms)
    basis_pct = None
    basis_hard = False
    if bf_px and bn_px and bn_px > 0 and timestamp_aligned:
        basis_pct = (bf_px - bn_px) / bn_px * 100.0
        soft = float(getattr(config, "BN_BF_DIVERGENCE_SOFT_PCT", 1.0))
        hard = float(getattr(config, "BN_BF_DIVERGENCE_HARD_PCT", 3.0))
        if abs(basis_pct) >= hard:
            basis_hard = True
            status, score_delta = "DIVERGE", score_delta - 0.15
            reasons.append(f"BN_BF_BASIS_HARD({basis_pct:+.2f}%)")
        elif abs(basis_pct) >= soft:
            status, score_delta = "DIVERGE", score_delta - 0.08
            reasons.append(f"BN_BF_BASIS_SOFT({basis_pct:+.2f}%)")
    elif not timestamp_aligned:
        status = "UNKNOWN"
        reasons.append("CROSS_MARKET_TS_SKEW")

    # --- 24h alignment ---
    if bn_ch is not None and bf_ch is not None:
        diff = abs(bf_ch - bn_ch)
        same_sign = (bf_ch >= 0 and bn_ch >= 0) or (bf_ch < 0 and bn_ch < 0)
        # hard anomaly: BF moves a lot, BN almost flat
        if abs(bf_ch) >= 2.0 and abs(bn_ch) < 0.5 and diff >= 2.0:
            status = "DIVERGE"
            score_delta -= 0.12
            reasons.append(f"BN_DIVERGE_24H(BF{bf_ch:+.1f}/BN{bn_ch:+.1f})")
        elif same_sign and diff <= 1.5:
            if status != "DIVERGE":
                status = "ALIGNED"
            score_delta += 0.05
            reasons.append(f"BN_ALIGN_24H(BF{bf_ch:+.1f}/BN{bn_ch:+.1f})")
        elif not same_sign and abs(bf_ch) >= 1.5:
            status = "DIVERGE"
            score_delta -= 0.08
            reasons.append(f"BN_CONFLICT_24H(BF{bf_ch:+.1f}/BN{bn_ch:+.1f})")
        else:
            reasons.append(f"BN_NEUTRAL_24H(BF{bf_ch:+.1f}/BN{bn_ch:+.1f})")
    elif bn_ch is None:
        if status != "DIVERGE":
            status = "UNKNOWN"
        reasons.append("BN_CHANGE_NA")
        # Brak Binance nie jest ani zgodnością, ani sprzecznością.
        # Nie przyznajemy żadnego bonusu confirmation.

    # --- 1h micro anomaly ---
    if bf_1h is not None and bn_1h is not None:
        if abs(bf_1h) >= 2.5 and abs(bn_1h) < 0.6:
            status = "DIVERGE"
            score_delta -= 0.10
            reasons.append(f"BN_DIVERGE_1H(BF{bf_1h:+.1f}/BN{bn_1h:+.1f})")

    # --- direction vs BN trend ---
    if bn_ch is not None and direction in ("LONG", "SHORT"):
        if direction == "LONG" and bn_ch <= -2.0:
            score_delta -= 0.06
            reasons.append(f"BN_VS_LONG({bn_ch:+.1f})")
        elif direction == "SHORT" and bn_ch >= 2.0:
            score_delta -= 0.06
            reasons.append(f"BN_VS_SHORT({bn_ch:+.1f})")
        elif direction == "LONG" and bn_ch >= 2.0:
            score_delta += 0.03
            reasons.append(f"BN_SUPPORT_LONG({bn_ch:+.1f})")
        elif direction == "SHORT" and bn_ch <= -2.0:
            score_delta += 0.03
            reasons.append(f"BN_SUPPORT_SHORT({bn_ch:+.1f})")

    # --- CoinGecko context (macro) ---
    cg_delta = 0.0
    if cg_ch is not None:
        if direction == "LONG" and cg_ch >= 3.0:
            cg_delta = 0.03
            reasons.append(f"CG_SUPPORT({cg_ch:+.1f})")
        elif direction == "SHORT" and cg_ch <= -3.0:
            cg_delta = 0.03
            reasons.append(f"CG_SUPPORT({cg_ch:+.1f})")
        elif direction == "LONG" and cg_ch <= -3.0:
            cg_delta = -0.04
            reasons.append(f"CG_HEADWIND({cg_ch:+.1f})")
        elif direction == "SHORT" and cg_ch >= 3.0:
            cg_delta = -0.04
            reasons.append(f"CG_HEADWIND({cg_ch:+.1f})")
        else:
            reasons.append(f"CG_NEUTRAL({cg_ch:+.1f})")
    score_delta += cg_delta

    # Bybit third check (optional soft)
    if by_ch is not None and bn_ch is not None and bf_ch is not None:
        if abs(bf_ch) >= 3 and abs(bn_ch) < 0.5 and abs(by_ch) < 0.5:
            score_delta -= 0.05
            reasons.append("MULTI_EX_DIVERGE")
            status = "DIVERGE"

    # UNKNOWN jest stanem jawnym: brak danych Binance != potwierdzenie.
    if bn_ch is None and status == "ALIGNED":
        status = "UNKNOWN"

    hard_reject = basis_hard
    if status == "DIVERGE" and bool(getattr(config, "REJECT_ON_CROSS_DIVERGE", False)):
        hard_reject = True
    # always hard reject extreme 1h alone-pump
    if any("BN_DIVERGE_1H" in r for r in reasons) and bool(getattr(config, "REJECT_ON_1H_DIVERGE", True)):
        hard_reject = True

    return {
        "status": status,
        "score_delta": round(score_delta, 4),
        "reasons": reasons,
        "hard_reject": hard_reject,
        "binance_change_24h": bn_ch,
        "blofin_change_24h": bf_ch,
        "coingecko_change_24h": cg_ch,
        "basis_pct": basis_pct,
        "timestamp_aligned": timestamp_aligned,
    }


def apply_confirmation(signal: dict, coin: dict = None) -> dict:
    import config
    conf = cross_market_confirmation(signal, coin)
    signal["external_confirmation"] = conf
    signal["cross_market_status"] = conf["status"]
    if not bool(getattr(config, "SOURCE_DIVERGENCE_GATE", False)):
        return signal
    delta = float(conf.get("score_delta") or 0)
    if delta:
        signal["strength"] = max(0.0, min(1.0, float(signal.get("strength") or 0) + delta))
    signal["reasons"] = list(signal.get("reasons") or []) + list(conf.get("reasons") or [])
    if conf.get("hard_reject"):
        signal["reject_reason"] = signal.get("reject_reason") or f"CROSS_MARKET_{conf['status']}"
    # risk mult for soft diverge
    if conf["status"] == "DIVERGE" and not conf.get("hard_reject"):
        signal["cross_market_risk_mult"] = float(
            getattr(__import__("config"), "CROSS_DIVERGE_RISK_MULT", 0.5)
        )
    return signal
