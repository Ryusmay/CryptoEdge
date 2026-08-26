# ============================================================
# ETAP 5 — Portfolio risk
# 25. gross exposure
# 26. net exposure
# 27. correlation clusters
# 28. aggregate leverage
# ============================================================

from __future__ import annotations

from typing import List, Dict, Any, Optional, Iterable, Tuple
import config


# Klaster korelacji – symbole w tej samej „rodzinie” ruchów
# (uproszczone, bez macierzy korelacji historycznej)
DEFAULT_CLUSTERS: Dict[str, tuple] = {
    "BTC_BETA": ("BTC", "WBTC", "TBTC"),
    "ETH_L2": ("ETH", "STETH", "WETH", "ARB", "OP", "STRK", "MANTA", "METIS", "BLAST", "SCROLL", "ZK"),
    "SOL_ECO": ("SOL", "JUP", "JTO", "WIF", "BONK", "PYTH", "RAY", "ORCA", "W"),
    "MEME": ("DOGE", "SHIB", "PEPE", "FLOKI", "BONK", "WIF", "MEME", "NEIRO", "PNUT", "GOAT", "TRUMP", "MELANIA"),
    "AI": ("FET", "RNDR", "RENDER", "TAO", "WLD", "AI", "AIXBT", "VIRTUAL", "GRASS", "ARC"),
    "DEFI": ("UNI", "AAVE", "MKR", "CRV", "LDO", "PENDLE", "ENA", "ETHFI", "REZ", "EIGEN"),
    "LAYER1": ("AVAX", "DOT", "ATOM", "NEAR", "APT", "SUI", "SEI", "TIA", "INJ", "FTM", "S"),
    "PRIVACY": ("XMR", "ZEC", "DASH", "SCRT", "ROSE"),
    "RWA": ("ONDO", "MKR", "TRU", "CFG", "POLYX"),
}


def _pos_fields(p: Any) -> dict:
    """Normalizacja Position / dict – preferuj actual_notional (po lot size)."""
    if isinstance(p, dict):
        notional = p.get("actual_notional")
        if notional is None:
            notional = p.get("size_usd") or p.get("notional") or 0
        return {
            "symbol": str(p.get("symbol") or "").upper(),
            "direction": str(p.get("direction") or "").upper(),
            "size_usd": float(notional or 0),
            "actual_notional": float(notional or 0),
            "size_contracts": float(p.get("size_contracts") or 0) or None,
            "margin": float(p.get("margin") or 0),
            "leverage": float(p.get("leverage") or getattr(config, "LEVERAGE", 10)),
            "pnl": float(p.get("pnl") or 0),
        }
    notional = getattr(p, "actual_notional", None)
    if notional is None:
        notional = getattr(p, "size_usd", 0) or 0
    return {
        "symbol": str(getattr(p, "symbol", "") or "").upper(),
        "direction": str(getattr(p, "direction", "") or "").upper(),
        "size_usd": float(notional or 0),
        "actual_notional": float(notional or 0),
        "size_contracts": float(getattr(p, "size_contracts", 0) or 0) or None,
        "margin": float(getattr(p, "margin", 0) or 0),
        "leverage": float(getattr(p, "leverage", None) or getattr(config, "LEVERAGE", 10)),
        "pnl": float(getattr(p, "pnl", 0) or 0),
    }


def build_symbol_cluster_map(extra: Dict[str, Iterable[str]] = None) -> Dict[str, str]:
    """symbol → cluster_id (pierwszy match wygrywa)."""
    clusters = dict(DEFAULT_CLUSTERS)
    if extra:
        clusters.update({k: tuple(v) for k, v in extra.items()})
    # config override
    cfg_c = getattr(config, "CORRELATION_CLUSTERS", None)
    if isinstance(cfg_c, dict):
        clusters.update({k: tuple(v) for k, v in cfg_c.items()})
    out: Dict[str, str] = {}
    for cid, syms in clusters.items():
        for s in syms:
            su = str(s).upper()
            if su not in out:
                out[su] = cid
    return out


def cluster_of(symbol: str, cmap: Dict[str, str] = None) -> str:
    cmap = cmap or build_symbol_cluster_map()
    s = (symbol or "").upper()
    return cmap.get(s) or f"SOLO:{s}"


# ------------------------------------------------------------------
# 25 + 26  Gross / Net exposure
# ------------------------------------------------------------------
def compute_exposure(positions: List[Any], equity: float = None) -> dict:
    """
    gross = sum(|notional|)
    net   = long_notional - short_notional
    """
    long_n = 0.0
    short_n = 0.0
    long_m = 0.0
    short_m = 0.0
    n_long = n_short = 0
    for raw in positions or []:
        p = _pos_fields(raw)
        n = abs(p["size_usd"])
        m = abs(p["margin"]) or (n / max(p["leverage"], 1))
        if p["direction"] == "LONG":
            long_n += n
            long_m += m
            n_long += 1
        elif p["direction"] == "SHORT":
            short_n += n
            short_m += m
            n_short += 1
    gross = long_n + short_n
    net = long_n - short_n
    equity = float(equity or 0) or 0.0
    return {
        "gross_usd": round(gross, 4),
        "net_usd": round(net, 4),
        "long_usd": round(long_n, 4),
        "short_usd": round(short_n, 4),
        "long_margin": round(long_m, 4),
        "short_margin": round(short_m, 4),
        "used_margin": round(long_m + short_m, 4),
        "n_long": n_long,
        "n_short": n_short,
        "n_total": n_long + n_short,
        "gross_to_equity": round(gross / equity, 4) if equity > 0 else None,
        "net_to_equity": round(net / equity, 4) if equity > 0 else None,
        "net_frac_of_gross": round(abs(net) / gross, 4) if gross > 0 else 0.0,
    }


# ------------------------------------------------------------------
# 28. Aggregate leverage
# ------------------------------------------------------------------
def aggregate_leverage(positions: List[Any], equity: float) -> dict:
    """
    effective leverage = gross_notional / equity
    margin_usage = used_margin / equity
    """
    exp = compute_exposure(positions, equity)
    eq = float(equity or 0)
    gross = exp["gross_usd"]
    used = exp["used_margin"]
    return {
        "equity": round(eq, 4),
        "gross_notional": gross,
        "effective_leverage": round(gross / eq, 4) if eq > 0 else 0.0,
        "margin_usage": round(used / eq, 4) if eq > 0 else 0.0,
        "used_margin": used,
        "free_margin": round(max(0.0, eq - used), 4) if eq > 0 else 0.0,
        "avg_pos_leverage": round(
            sum(_pos_fields(p)["leverage"] for p in (positions or [])) / max(len(positions or []), 1),
            2,
        ),
    }


# ------------------------------------------------------------------
# 27. Correlation clusters
# ------------------------------------------------------------------
def cluster_exposure(positions: List[Any], equity: float = None) -> dict:
    """
    Ekspozycja per klaster + kierunek.
    """
    cmap = build_symbol_cluster_map()
    buckets: Dict[str, dict] = {}
    for raw in positions or []:
        p = _pos_fields(raw)
        cid = cluster_of(p["symbol"], cmap)
        b = buckets.setdefault(cid, {
            "cluster": cid, "long_usd": 0.0, "short_usd": 0.0,
            "gross_usd": 0.0, "net_usd": 0.0, "symbols": [], "count": 0,
        })
        n = abs(p["size_usd"])
        if p["direction"] == "LONG":
            b["long_usd"] += n
        else:
            b["short_usd"] += n
        b["gross_usd"] += n
        b["net_usd"] = b["long_usd"] - b["short_usd"]
        b["count"] += 1
        if p["symbol"] not in b["symbols"]:
            b["symbols"].append(p["symbol"])

    eq = float(equity or 0)
    rows = []
    for b in buckets.values():
        b["gross_to_equity"] = round(b["gross_usd"] / eq, 4) if eq > 0 else None
        b["long_usd"] = round(b["long_usd"], 4)
        b["short_usd"] = round(b["short_usd"], 4)
        b["gross_usd"] = round(b["gross_usd"], 4)
        b["net_usd"] = round(b["net_usd"], 4)
        rows.append(b)
    rows.sort(key=lambda x: x["gross_usd"], reverse=True)
    return {
        "clusters": rows,
        "n_clusters": len(rows),
        "max_cluster_gross": rows[0]["gross_usd"] if rows else 0.0,
        "max_cluster_id": rows[0]["cluster"] if rows else None,
    }


def portfolio_snapshot(positions: List[Any], equity: float) -> dict:
    exp = compute_exposure(positions, equity)
    lev = aggregate_leverage(positions, equity)
    cl = cluster_exposure(positions, equity)
    risk = compute_open_risk(positions, equity)
    return {
        "exposure": exp,
        "leverage": lev,
        "clusters": cl,
        "open_risk": risk,
    }


def _position_risk_usd(raw: Any) -> float:
    p = raw if isinstance(raw, dict) else getattr(raw, "__dict__", {})
    explicit = p.get("actual_risk_usd")
    if explicit is None:
        explicit = p.get("risk_usd") or p.get("open_risk_usd")
    if explicit is not None:
        return max(0.0, float(explicit or 0))
    entry = float(p.get("entry") or p.get("entry_price") or 0)
    stop = float(p.get("sl") or p.get("sl_price") or p.get("stop_loss") or 0)
    notional = abs(float(p.get("actual_notional") or p.get("size_usd") or p.get("notional") or 0))
    if entry <= 0 or stop <= 0 or notional <= 0:
        return 0.0
    return notional * abs(entry - stop) / entry


def compute_open_risk(positions: List[Any], equity: float = None) -> dict:
    """Loss at active stops, total and per correlation cluster."""
    eq = max(0.0, float(equity or 0))
    cmap = build_symbol_cluster_map()
    clusters: Dict[str, float] = {}
    total = 0.0
    unknown = 0
    for raw in positions or []:
        fields = _pos_fields(raw)
        risk = _position_risk_usd(raw)
        if risk <= 0:
            unknown += 1
            continue
        total += risk
        cid = cluster_of(fields["symbol"], cmap)
        clusters[cid] = clusters.get(cid, 0.0) + risk
    return {
        "total_usd": round(total, 4),
        "total_pct": round(total / eq, 6) if eq else None,
        "cluster_usd": {k: round(v, 4) for k, v in clusters.items()},
        "cluster_pct": {k: round(v / eq, 6) for k, v in clusters.items()} if eq else {},
        "positions_without_stop_risk": unknown,
    }


# ------------------------------------------------------------------
# Risk gates (przed otwarciem)
# ------------------------------------------------------------------
def check_portfolio_limits(
    positions: List[Any],
    equity: float,
    new_signal: dict = None,
    new_notional: float = None,
) -> Tuple[bool, str]:
    """
    Twarde limity portfela.
    Zwraca (ok, reason).
    """
    eq = float(equity or 0)
    if eq <= 0:
        return False, "NO_EQUITY"

    # symuluj dodanie nowej pozycji
    sim = list(positions or [])
    if new_signal is not None:
        sim.append({
            "symbol": new_signal.get("symbol"),
            "direction": new_signal.get("direction"),
            "size_usd": float(new_notional or new_signal.get("size_usd") or 0),
            "margin": float(new_notional or 0) / max(float(getattr(config, "LEVERAGE", 10)), 1),
            "leverage": float(getattr(config, "LEVERAGE", 10)),
            "entry": float(new_signal.get("fill_price") or new_signal.get("decision_price") or new_signal.get("price") or 0),
            "sl": float(new_signal.get("sl") or new_signal.get("sl_price") or new_signal.get("stop_loss") or 0),
        })

    exp = compute_exposure(sim, eq)
    lev = aggregate_leverage(sim, eq)
    cl = cluster_exposure(sim, eq)
    open_risk = compute_open_risk(sim, eq)

    max_gross_mult = float(getattr(config, "MAX_GROSS_EXPOSURE_MULT", 5.0))  # gross ≤ 5× equity
    max_net_mult = float(getattr(config, "MAX_NET_EXPOSURE_MULT", 3.5))
    max_eff_lev = float(getattr(config, "MAX_EFFECTIVE_LEVERAGE", 8.0))
    max_cluster_mult = float(getattr(config, "MAX_CLUSTER_EXPOSURE_MULT", 2.5))
    max_cluster_count = int(getattr(config, "MAX_CLUSTER_POSITIONS", 4))
    max_open_risk = float(getattr(config, "MAX_PORTFOLIO_OPEN_RISK_PCT", 0.025))
    max_cluster_risk = float(getattr(config, "MAX_CLUSTER_OPEN_RISK_PCT", 0.0125))

    if (open_risk.get("total_pct") or 0) > max_open_risk:
        return False, f"OPEN_RISK({open_risk['total_pct']:.2%}>{max_open_risk:.2%})"

    if exp["gross_to_equity"] is not None and exp["gross_to_equity"] > max_gross_mult:
        return False, f"GROSS_EXPOSURE({exp['gross_to_equity']:.2f}x>{max_gross_mult}x)"

    if exp["net_to_equity"] is not None and abs(exp["net_to_equity"]) > max_net_mult:
        return False, f"NET_EXPOSURE({exp['net_to_equity']:.2f}x>{max_net_mult}x)"

    if lev["effective_leverage"] > max_eff_lev:
        return False, f"AGG_LEVERAGE({lev['effective_leverage']:.2f}x>{max_eff_lev}x)"

    # klaster nowego sygnału
    if new_signal is not None:
        cid = cluster_of(str(new_signal.get("symbol") or ""))
        for row in cl.get("clusters") or []:
            if row["cluster"] != cid:
                continue
            if row.get("gross_to_equity") is not None and row["gross_to_equity"] > max_cluster_mult:
                return False, f"CLUSTER_EXPOSURE({cid}:{row['gross_to_equity']:.2f}x)"
            if row["count"] > max_cluster_count:
                return False, f"CLUSTER_COUNT({cid}:{row['count']}>{max_cluster_count})"
            cluster_risk = float((open_risk.get("cluster_pct") or {}).get(cid) or 0)
            if cluster_risk > max_cluster_risk:
                return False, f"CLUSTER_RISK({cid}:{cluster_risk:.2%}>{max_cluster_risk:.2%})"
            # ten sam kierunek w klastrze – limit
            direction = str(new_signal.get("direction") or "").upper()
            same_dir = row["long_usd"] if direction == "LONG" else row["short_usd"]
            if eq > 0 and (same_dir / eq) > max_cluster_mult:
                return False, f"CLUSTER_DIR({cid}:{direction})"

    return True, "ok"
