# ============================================================
# PRIORYTET 16 — Monte Carlo stress test
# ============================================================
#
# Losujemy:
#   - kolejność trade'ów
#   - slippage
#   - fee
#   - opóźnienia wejścia / skip
#   - (efekt) serie strat na krzywej equity
#
# Sprawdzamy:
#   - Max DD (R i % kapitału)
#   - Risk of Ruin
#   - P(utrata 20% / 30% / 50% kapitału)
#
# Wejście: trade'y z backtestu / walk-forward OOS (pole r lub net+risk_usd)
# ============================================================

from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

LOGS = Path("logs")


def _f(x, default=0.0) -> float:
    try:
        return float(x) if x is not None else default
    except (TypeError, ValueError):
        return default


def trade_r(t: dict) -> float:
    """R-multiple trade'u."""
    if t.get("r") is not None:
        return _f(t["r"])
    net = _f(t.get("net"))
    risk = _f(t.get("risk_usd"))
    if risk > 1e-12:
        return net / risk
    return 0.0


def max_drawdown_from_r(rs: Sequence[float]) -> float:
    """Max DD na krzywej skumulowanego R (w jednostkach R)."""
    eq = peak = max_dd = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
    return max_dd


def max_drawdown_pct_from_r(rs: Sequence[float], risk_pct: float = 0.006) -> float:
    """
    Przybliżony Max DD % kapitału:
      equity_mult ≈ 1 + cumR * risk_pct
    (każde 1R ≈ risk_pct * equity).
    """
    risk_pct = max(1e-6, float(risk_pct or 0.006))
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in rs:
        equity *= (1.0 + float(r) * risk_pct)
        if equity <= 0:
            return 1.0  # total wipe
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
    return max_dd


def max_loss_streak(rs: Sequence[float]) -> int:
    streak = best = 0
    for r in rs:
        if float(r) < 0:
            streak += 1
            best = max(best, streak)
        else:
            streak = 0
    return best


def total_r(rs: Sequence[float]) -> float:
    return float(sum(rs))


def apply_cost_shock(
    r: float,
    slip_extra: float = 0.0,
    fee_extra: float = 0.0,
    typical_sl_dist: float = 0.025,
) -> float:
    """
    Koszt jako ułamek notionalu → R:
      cost_R ≈ cost_frac / sl_dist
    slip_extra/fee_extra to dodatkowe frakcje (np. 0.0005 = 5 bps).
    """
    if typical_sl_dist <= 1e-9:
        typical_sl_dist = 0.025
    cost_r = (slip_extra + fee_extra) / typical_sl_dist
    return r - cost_r


@dataclass
class MCConfig:
    n_sims: int = 2000
    seed: Optional[int] = 42
    # shuffle
    shuffle_order: bool = True
    # cost shocks (fraction of price, round-trip-ish applied once as extra)
    slip_base: float = 0.0          # już w trade'ach; tu dodatkowy
    slip_std: float = 0.0004        # σ dodatkowego slip
    fee_base: float = 0.0
    fee_std: float = 0.0001
    typical_sl_dist: float = 0.025
    # entry delay: z prawdopodobieństwem p pomijamy trade (nie złapany)
    skip_prob: float = 0.05
    # albo degradujemy R (spóźniony entry)
    delay_r_penalty_mean: float = 0.05
    delay_r_penalty_std: float = 0.05
    delay_prob: float = 0.15
    # progi ryzyka w R
    danger_dd_r: float = 8.0        # DD w R uznany za niebezpieczny
    ruin_dd_r: float = 15.0
    # Risk of Ruin / utrata kapitału (frakcje equity)
    # approx: 1R ≈ risk_pct_per_trade * equity  →  dd_pct ≈ dd_r * risk_pct
    risk_pct_per_trade: float = 0.006   # typowy risk % equity na trade (trend ~0.6%)
    capital_loss_levels: Tuple[float, ...] = (0.20, 0.30, 0.50)  # 20/30/50%
    # bootstrap vs pure permute
    sample_with_replacement: bool = False


@dataclass
class MCResult:
    n_sims: int = 0
    n_trades: int = 0
    config: Dict[str, Any] = field(default_factory=dict)
    # distributions
    final_r: List[float] = field(default_factory=list)
    max_dd_r: List[float] = field(default_factory=list)
    max_dd_pct: List[float] = field(default_factory=list)
    max_loss_streak: List[int] = field(default_factory=list)
    # summary
    median_final_r: float = 0.0
    p05_final_r: float = 0.0
    p95_final_r: float = 0.0
    median_max_dd_r: float = 0.0
    p95_max_dd_r: float = 0.0
    p99_max_dd_r: float = 0.0
    median_max_dd_pct: float = 0.0
    p95_max_dd_pct: float = 0.0
    pct_danger_dd: float = 0.0   # % symulacji z DD >= danger (R)
    pct_ruin_dd: float = 0.0
    pct_negative_final: float = 0.0
    # Risk of Ruin — P(max equity loss ≥ X%)
    pct_loss_20: float = 0.0
    pct_loss_30: float = 0.0
    pct_loss_50: float = 0.0
    risk_of_ruin: Dict[str, float] = field(default_factory=dict)
    median_max_loss_streak: float = 0.0
    p95_max_loss_streak: float = 0.0
    baseline_final_r: float = 0.0
    baseline_max_dd_r: float = 0.0
    notes: List[str] = field(default_factory=list)


def _percentile(xs: List[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    if p <= 0:
        return s[0]
    if p >= 100:
        return s[-1]
    k = (len(s) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return s[int(k)]
    return s[f] * (c - k) + s[c] * (k - f)


def run_monte_carlo(
    trades: Sequence[dict],
    cfg: MCConfig = None,
) -> MCResult:
    cfg = cfg or MCConfig()
    trades = list(trades or [])
    base_rs = [trade_r(t) for t in trades]
    result = MCResult(
        n_trades=len(base_rs),
        config={
            "n_sims": cfg.n_sims,
            "seed": cfg.seed,
            "shuffle_order": cfg.shuffle_order,
            "slip_std": cfg.slip_std,
            "fee_std": cfg.fee_std,
            "skip_prob": cfg.skip_prob,
            "delay_prob": cfg.delay_prob,
            "danger_dd_r": cfg.danger_dd_r,
            "ruin_dd_r": cfg.ruin_dd_r,
            "risk_pct_per_trade": cfg.risk_pct_per_trade,
            "capital_loss_levels": list(cfg.capital_loss_levels),
            "sample_with_replacement": cfg.sample_with_replacement,
        },
    )
    if len(base_rs) < 3:
        result.notes.append("za mało trade'ów do MC (<3) — zbierz więcej z shadow/WF OOS")
        return result

    rng = random.Random(cfg.seed)
    result.baseline_final_r = total_r(base_rs)
    result.baseline_max_dd_r = max_drawdown_from_r(base_rs)

    finals: List[float] = []
    dds: List[float] = []
    dds_pct: List[float] = []
    streaks: List[int] = []

    for _ in range(cfg.n_sims):
        # 1) kolejność / bootstrap
        if cfg.sample_with_replacement:
            rs = [rng.choice(base_rs) for _ in base_rs]
        else:
            rs = list(base_rs)
            if cfg.shuffle_order:
                rng.shuffle(rs)

        shocked: List[float] = []
        for r in rs:
            # 2) skip (opóźnienie / nie wejście)
            if cfg.skip_prob > 0 and rng.random() < cfg.skip_prob:
                continue
            # 3) entry delay → kara do R
            if cfg.delay_prob > 0 and rng.random() < cfg.delay_prob:
                pen = abs(rng.gauss(cfg.delay_r_penalty_mean, cfg.delay_r_penalty_std))
                r = r - pen
            # 4) slip + fee shock
            slip_e = abs(rng.gauss(cfg.slip_base, cfg.slip_std)) if cfg.slip_std > 0 else cfg.slip_base
            fee_e = abs(rng.gauss(cfg.fee_base, cfg.fee_std)) if cfg.fee_std > 0 else cfg.fee_base
            r = apply_cost_shock(
                r,
                slip_extra=slip_e * 2.0,
                fee_extra=fee_e * 2.0,
                typical_sl_dist=cfg.typical_sl_dist,
            )
            shocked.append(r)

        if not shocked:
            finals.append(0.0)
            dds.append(0.0)
            dds_pct.append(0.0)
            streaks.append(0)
            continue
        finals.append(total_r(shocked))
        dds.append(max_drawdown_from_r(shocked))
        dds_pct.append(max_drawdown_pct_from_r(shocked, cfg.risk_pct_per_trade))
        streaks.append(max_loss_streak(shocked))

    n = max(len(finals), 1)
    result.n_sims = len(finals)
    result.final_r = finals
    result.max_dd_r = dds
    result.max_dd_pct = dds_pct
    result.max_loss_streak = streaks
    result.median_final_r = _percentile(finals, 50)
    result.p05_final_r = _percentile(finals, 5)
    result.p95_final_r = _percentile(finals, 95)
    result.median_max_dd_r = _percentile(dds, 50)
    result.p95_max_dd_r = _percentile(dds, 95)
    result.p99_max_dd_r = _percentile(dds, 99)
    result.median_max_dd_pct = _percentile(dds_pct, 50)
    result.p95_max_dd_pct = _percentile(dds_pct, 95)
    result.pct_danger_dd = sum(1 for d in dds if d >= cfg.danger_dd_r) / n
    result.pct_ruin_dd = sum(1 for d in dds if d >= cfg.ruin_dd_r) / n
    result.pct_negative_final = sum(1 for x in finals if x < 0) / n
    result.median_max_loss_streak = _percentile([float(x) for x in streaks], 50)
    result.p95_max_loss_streak = _percentile([float(x) for x in streaks], 95)

    # Risk of Ruin — P(max equity DD ≥ 20/30/50%)
    levels = tuple(cfg.capital_loss_levels) or (0.20, 0.30, 0.50)
    ror = {}
    for lv in levels:
        key = f"loss_{int(lv * 100)}pct"
        ror[key] = sum(1 for d in dds_pct if d >= lv) / n
    result.risk_of_ruin = ror
    result.pct_loss_20 = ror.get("loss_20pct", 0.0)
    result.pct_loss_30 = ror.get("loss_30pct", 0.0)
    result.pct_loss_50 = ror.get("loss_50pct", 0.0)
    return result


def print_mc_report(res: MCResult):
    print("\n========== MONTE CARLO STRESS (PRIORYTET 16) ==========")
    print(f"Trades: {res.n_trades} | Sims: {res.n_sims}")
    print(f"Config: {res.config}")
    if res.notes:
        print(f"Notes: {res.notes}")
    print("\n--- Baseline (oryginalna kolejność) ---")
    print(f"  Final R:   {res.baseline_final_r:+.3f}")
    print(f"  Max DD R:  {res.baseline_max_dd_r:.3f}")
    print("\n--- Final R (shuffle + slip + fee + delay) ---")
    print(f"  median:    {res.median_final_r:+.3f}")
    print(f"  5% / 95%:  {res.p05_final_r:+.3f} / {res.p95_final_r:+.3f}")
    print(f"  P(final<0): {res.pct_negative_final*100:.1f}%")
    print("\n--- Max Drawdown (R) ---")
    print(f"  median:    {res.median_max_dd_r:.3f}")
    print(f"  95% / 99%: {res.p95_max_dd_r:.3f} / {res.p99_max_dd_r:.3f}")
    print(f"  P(DD ≥ danger {res.config.get('danger_dd_r')}R): {res.pct_danger_dd*100:.1f}%")
    print(f"  P(DD ≥ ruin   {res.config.get('ruin_dd_r')}R): {res.pct_ruin_dd*100:.1f}%")
    print("\n--- Max Drawdown (% kapitału) ---")
    print(f"  median:    {res.median_max_dd_pct*100:.1f}%")
    print(f"  95%:       {res.p95_max_dd_pct*100:.1f}%")
    print("\n--- Risk of Ruin / utrata kapitału ---")
    print(f"  P(loss ≥ 20%): {res.pct_loss_20*100:.1f}%")
    print(f"  P(loss ≥ 30%): {res.pct_loss_30*100:.1f}%")
    print(f"  P(loss ≥ 50%): {res.pct_loss_50*100:.1f}%")
    if res.risk_of_ruin:
        for k, v in res.risk_of_ruin.items():
            print(f"  {k}: {v*100:.1f}%")
    print("\n--- Serie strat ---")
    print(f"  median max streak: {res.median_max_loss_streak:.0f}")
    print(f"  95% max streak:    {res.p95_max_loss_streak:.0f}")
    print("\n--- Werdykt (heurystyka) ---")
    if res.n_trades < 30:
        print("  ⚠ mało trade'ów — MC ma niską moc; zbierz ≥30–50 z WF OOS / shadow")
    if res.pct_loss_50 > 0.10:
        print("  ✗ Risk of Ruin 50%: >10% symulacji")
    elif res.pct_loss_30 > 0.20:
        print("  ✗ często DD ≥ 30% kapitału")
    elif res.pct_loss_20 > 0.35:
        print("  ~ podwyższone P(loss≥20%)")
    else:
        print("  ✓ akceptowalne P(utraty 20/30/50%) na tej próbie")
    if res.pct_danger_dd > 0.25:
        print("  ✗ wysokie ryzyko niebezpiecznego DD w R")
    elif res.pct_danger_dd > 0.10:
        print("  ~ umiarkowane ryzyko DD (R)")
    else:
        print("  ✓ rzadki niebezpieczny DD w R")
    if res.pct_negative_final > 0.40:
        print("  ✗ często ujemny final R po szokach kosztów")
    elif res.median_final_r <= 0:
        print("  ✗ median final R ≤ 0 — edge kruchy")
    else:
        print("  ✓ median final R dodatni")


def save_mc_report(res: MCResult, path: str = None) -> str:
    LOGS.mkdir(parents=True, exist_ok=True)
    path = path or str(LOGS / "monte_carlo_last.json")
    # don't dump full arrays if huge — percentiles enough + sample
    payload = {
        "n_sims": res.n_sims,
        "n_trades": res.n_trades,
        "config": res.config,
        "baseline_final_r": res.baseline_final_r,
        "baseline_max_dd_r": res.baseline_max_dd_r,
        "median_final_r": res.median_final_r,
        "p05_final_r": res.p05_final_r,
        "p95_final_r": res.p95_final_r,
        "median_max_dd_r": res.median_max_dd_r,
        "p95_max_dd_r": res.p95_max_dd_r,
        "p99_max_dd_r": res.p99_max_dd_r,
        "pct_danger_dd": res.pct_danger_dd,
        "pct_ruin_dd": res.pct_ruin_dd,
        "pct_negative_final": res.pct_negative_final,
        "notes": res.notes,
        # compact histograms
        "final_r_sample": res.final_r[:: max(1, len(res.final_r)//200)] if res.final_r else [],
        "max_dd_r_sample": res.max_dd_r[:: max(1, len(res.max_dd_r)//200)] if res.max_dd_r else [],
    }
    Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_trades_from_json(path: str) -> List[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    for key in ("oos_trades", "trades", "closed"):
        if key in data and isinstance(data[key], list):
            return data[key]
    return []


def run_cli():
    ap = argparse.ArgumentParser(description="CryptoEdge Monte Carlo (Etap 6)")
    ap.add_argument(
        "--trades",
        default="logs/walk_forward_last.json",
        help="JSON z trade'ami (WF OOS / backtest_last)",
    )
    ap.add_argument("--sims", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--danger-dd", type=float, default=8.0, help="próg niebezpiecznego DD w R")
    ap.add_argument("--ruin-dd", type=float, default=15.0)
    ap.add_argument("--skip-prob", type=float, default=0.05)
    ap.add_argument("--delay-prob", type=float, default=0.15)
    ap.add_argument("--slip-std", type=float, default=0.0004)
    ap.add_argument("--bootstrap", action="store_true", help="sample with replacement")
    ap.add_argument("--fallback-synthetic", action="store_true",
                    help="gdy brak trade'ów — demo na syntetycznych R")
    args = ap.parse_args()

    trades: List[dict] = []
    path = Path(args.trades)
    if path.exists():
        trades = load_trades_from_json(str(path))
        print(f"[MC] loaded {len(trades)} trades from {path}")
    else:
        # try alternatives
        for alt in ("logs/backtest_last.json", "logs/performance_wf_oos.json"):
            if Path(alt).exists():
                trades = load_trades_from_json(alt)
                if trades:
                    print(f"[MC] loaded {len(trades)} trades from {alt}")
                    break

    if len(trades) < 3 and args.fallback_synthetic:
        # demo path — not for decisions
        rng = random.Random(0)
        trades = [{"r": rng.gauss(0.15, 0.9)} for _ in range(80)]
        print("[MC] SYNTHETIC demo trades (nie używaj do decyzji)")
    elif len(trades) < 3:
        print("[MC] za mało trade'ów — uruchom najpierw backtest / walk_forward")
        print("     albo: python monte_carlo.py --fallback-synthetic")
        return

    cfg = MCConfig(
        n_sims=args.sims,
        seed=args.seed,
        danger_dd_r=args.danger_dd,
        ruin_dd_r=args.ruin_dd,
        skip_prob=args.skip_prob,
        delay_prob=args.delay_prob,
        slip_std=args.slip_std,
        sample_with_replacement=args.bootstrap,
    )
    res = run_monte_carlo(trades, cfg)
    print_mc_report(res)
    out = save_mc_report(res)
    print(f"\nSaved {out}")


if __name__ == "__main__":
    run_cli()
