"""Metryki edge-decay emitowane do API/dashboardu."""
from __future__ import annotations

from collections import Counter, defaultdict


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def snapshot(trades, rejects=None, replay_expectancy=None, portfolio_risk=None):
    rows = list(trades or [])
    pnls = [float(getattr(x, "pnl", 0) if not isinstance(x, dict) else x.get("pnl") or 0) for x in rows]
    slips = [float((getattr(x, "pnl_breakdown", {}) if not isinstance(x, dict) else x).get("slippage") or 0) for x in rows]
    maker = sum(1 for x in rows if str((x.get("entry_side") if isinstance(x, dict) else getattr(x, "entry_side", "taker"))).lower() == "maker")
    per_regime, per_profile = defaultdict(list), defaultdict(list)
    for x, pnl in zip(rows, pnls):
        get = x.get if isinstance(x, dict) else lambda k, d=None: getattr(x, k, d)
        per_regime[str(get("market_regime", "UNKNOWN"))].append(pnl)
        per_profile[str(get("v2_profile", "UNKNOWN"))].append(pnl)
    ordered = sorted(slips)
    p95 = ordered[min(len(ordered)-1, int(0.95 * len(ordered)))] if ordered else 0.0
    positive = sorted((x for x in pnls if x > 0), reverse=True)
    concentration = sum(positive[:max(1, len(pnls)//10)]) / sum(positive) if positive else 0.0
    return {
        "paper_expectancy": {str(n): _mean(pnls[-n:]) for n in (20, 50, 100)},
        "replay_expectancy": replay_expectancy,
        "maker_ratio": maker / len(rows) if rows else 0.0,
        "slippage_avg": _mean(slips), "slippage_p95": p95,
        "reject_share": dict(Counter(str(x.get("reason") or "UNKNOWN") for x in (rejects or []))),
        "by_regime": {k: {"n": len(v), "expectancy": _mean(v)} for k, v in per_regime.items()},
        "by_profile": {k: {"n": len(v), "expectancy": _mean(v)} for k, v in per_profile.items()},
        "profit_concentration": concentration,
        "risk_budget": portfolio_risk or {},
    }
