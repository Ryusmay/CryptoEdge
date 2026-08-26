"""Obowiazkowa bramka odpornosci dla zmian strategii."""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Sequence

from statistical_validation import deflated_sharpe_ratio


def evaluate_research_gate(trades: Sequence[dict], *, trial_count: int = 1,
                           neighbor_results: Iterable[float] = (),
                           paper_expectancy: float | None = None,
                           replay_expectancy: float | None = None,
                           parity_budget_r: float = 0.20) -> dict:
    rows = list(trades or [])
    pnl = [float(r.get("r") if r.get("r") is not None else r.get("pnl_r") or 0) for r in rows]
    total = sum(pnl)
    by_symbol, by_window = defaultdict(float), defaultdict(float)
    for row, value in zip(rows, pnl):
        by_symbol[str(row.get("symbol") or "UNKNOWN")] += value
        by_window[str(row.get("window") or row.get("sample") or "ALL")] += value
    top_n = max(1, int(len(pnl) * 0.10)) if pnl else 0
    concentration = (sum(sorted(pnl, reverse=True)[:top_n]) / total) if total > 0 and top_n else 1.0
    costs = [float(r.get("cost_r") or 0) for r in rows]
    stress_total = sum(v - 0.5 * c for v, c in zip(pnl, costs))
    neighbors = list(float(x) for x in neighbor_results)
    dsr = deflated_sharpe_ratio(pnl, trials=max(1, int(trial_count))) if len(pnl) >= 3 else {"dsr": 0.0}
    parity_gap = None if paper_expectancy is None or replay_expectancy is None else abs(paper_expectancy - replay_expectancy)
    checks = {
        "multi_symbol": sum(1 for v in by_symbol.values() if v > 0) >= 2,
        "multi_window": len(by_window) <= 1 or sum(1 for v in by_window.values() if v > 0) >= 2,
        "profit_concentration": concentration <= 0.50,
        "cost_stress_1_5x": stress_total > 0,
        "parameter_neighborhood": not neighbors or sum(1 for x in neighbors if x > 0) >= max(1, len(neighbors) // 2),
        "dsr": float(dsr.get("dsr") or 0) >= 0.95,
        "paper_replay_parity": parity_gap is None or parity_gap <= parity_budget_r,
    }
    return {"pass": all(checks.values()), "checks": checks, "n": len(rows),
            "total_r": total, "profit_concentration": concentration,
            "cost_stress_total_r": stress_total, "dsr": dsr,
            "paper_replay_gap_r": parity_gap}
