# ============================================================
# Performance Metrics — po testach / paper / shadow
# ============================================================
#
# Nie tylko „ile % zarobił”.
#
# Strategia (Trend + Reversal + Combined):
#   Expectancy/R, PF, WR, Avg Win/Loss, Median R,
#   Max DD, Sharpe, Sortino, Calmar/MAR
#
# Reversal dodatkowo:
#   MFE, MAE, time-to-TP/SL, distance-from-extreme,
#   wynik vs RSI / impulse size / liquidity
# ============================================================

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

LOGS = Path("logs")


def _f(x, default=0.0) -> float:
    try:
        if x is None or x == "":
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def _median(xs: List[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return 0.5 * (s[mid - 1] + s[mid])


def _stdev(xs: List[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def strategy_metrics(
    trades: Sequence[dict],
    equity_curve: Sequence[float] = None,
    starting_equity: float = None,
    periods_per_year: float = 365.0,
) -> Dict[str, Any]:
    """
    Pełny zestaw metryk strategii z listy trade'ów.
    Trade dict: r, net, (opcjonalnie) engine, bars, ...
    """
    trades = list(trades or [])
    empty = {
        "n": 0,
        "trades": 0,
        "expectancy_r": 0.0,
        "profit_factor": 0.0,
        "win_rate": 0.0,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "avg_win_r": 0.0,
        "avg_loss_r": 0.0,
        "average_r": 0.0,
        "median_r": 0.0,
        "max_drawdown": 0.0,
        "max_drawdown_r": 0.0,
        "max_dd": 0.0,
        "mfe_avg": 0.0,
        "mae_avg": 0.0,
        "mfe_median": 0.0,
        "mae_median": 0.0,
        "sharpe": 0.0,
        "sortino": 0.0,
        "calmar": 0.0,
        "mar": 0.0,
        "net_pnl": 0.0,
        "total_return_pct": 0.0,
    }
    if not trades:
        return empty

    rs = [_f(t.get("r")) for t in trades]
    nets = [_f(t.get("net")) for t in trades]
    # MFE / MAE (pct or R) — jeśli brak w trade, 0
    mfes = [
        _f(t.get("mfe") if t.get("mfe") is not None else t.get("mfe_pct"))
        for t in trades
    ]
    maes = [
        _f(t.get("mae") if t.get("mae") is not None else t.get("mae_pct"))
        for t in trades
    ]
    wins = [t for t in trades if _f(t.get("r")) > 0]
    losses = [t for t in trades if _f(t.get("r")) <= 0]
    win_nets = [_f(t.get("net")) for t in wins]
    loss_nets = [_f(t.get("net")) for t in losses]
    win_rs = [_f(t.get("r")) for t in wins]
    loss_rs = [_f(t.get("r")) for t in losses]

    gp = sum(win_nets) if win_nets else 0.0
    gl = abs(sum(loss_nets)) if loss_nets else 0.0
    pf = (gp / gl) if gl > 1e-12 else (999.0 if gp > 0 else 0.0)

    avg_r = sum(rs) / len(rs)
    # Expectancy in R = avg R (per trade)
    expectancy_r = avg_r

    # Max DD on R-curve
    eq_r = peak_r = max_dd_r = 0.0
    for r in rs:
        eq_r += r
        peak_r = max(peak_r, eq_r)
        max_dd_r = max(max_dd_r, peak_r - eq_r)

    # Equity curve DD
    max_dd = 0.0
    total_return = 0.0
    if equity_curve and len(equity_curve) >= 2:
        peak = equity_curve[0]
        for e in equity_curve:
            peak = max(peak, e)
            if peak > 0:
                max_dd = max(max_dd, (peak - e) / peak)
        if starting_equity and starting_equity > 0:
            total_return = (equity_curve[-1] / starting_equity) - 1.0
        elif equity_curve[0] > 0:
            total_return = (equity_curve[-1] / equity_curve[0]) - 1.0
            starting_equity = equity_curve[0]
    else:
        net_pnl = sum(nets)
        if starting_equity and starting_equity > 0:
            total_return = net_pnl / starting_equity
            # approximate DD from R
            max_dd = max_dd_r * 0.01  # rough if risk ~1% — better use curve
        else:
            total_return = 0.0
        max_dd = max(max_dd, max_dd_r / max(len(rs), 1) * 0.05)

    # Sharpe / Sortino on per-trade R (annualized soft)
    std = _stdev(rs)
    downside = [r for r in rs if r < 0]
    dstd = _stdev(downside) if downside else 0.0
    n = len(rs)
    # trades-based annualization is rough
    scale = math.sqrt(max(n, 1))
    sharpe = (avg_r / std * scale) if std > 1e-12 else 0.0
    sortino = (avg_r / dstd * scale) if dstd > 1e-12 else 0.0

    # Calmar / MAR = annualized return / max DD
    calmar = (total_return / max_dd) if max_dd > 1e-12 else 0.0
    mar = calmar

    return {
        "n": n,
        "trades": n,
        "expectancy_r": round(expectancy_r, 4),
        "expectancy": round(expectancy_r, 4),
        "profit_factor": round(pf, 3),
        "win_rate": round(len(wins) / n, 4),
        "avg_win": round(sum(win_nets) / len(win_nets), 4) if win_nets else 0.0,
        "avg_loss": round(sum(loss_nets) / len(loss_nets), 4) if loss_nets else 0.0,
        "avg_win_r": round(sum(win_rs) / len(win_rs), 4) if win_rs else 0.0,
        "avg_loss_r": round(sum(loss_rs) / len(loss_rs), 4) if loss_rs else 0.0,
        "average_r": round(avg_r, 4),
        "median_r": round(_median(rs), 4),
        "max_drawdown": round(max_dd, 4),
        "max_drawdown_r": round(max_dd_r, 4),
        "max_dd": round(max_dd_r, 4),
        "mfe_avg": round(sum(mfes) / n, 4) if n else 0.0,
        "mae_avg": round(sum(maes) / n, 4) if n else 0.0,
        "mfe_median": round(_median(mfes), 4),
        "mae_median": round(_median(maes), 4),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "calmar": round(calmar, 3),
        "mar": round(mar, 3),
        "net_pnl": round(sum(nets), 4),
        "total_return_pct": round(total_return * 100.0, 3),
    }



REGIME_LABELS = (
    "TREND_UP", "TREND_DOWN", "RANGE",
    "HIGH_VOLATILITY", "PANIC", "RECOVERY", "UNKNOWN",
)


def _norm_regime(r) -> str:
    s = str(r or "UNKNOWN").upper().strip()
    aliases = {
        "TREND": "TREND_UP",
        "BULL": "TREND_UP",
        "BEAR": "TREND_DOWN",
        "VOLATILE": "HIGH_VOLATILITY",
        "HIGH_VOL": "HIGH_VOLATILITY",
        "EXTREME": "PANIC",
        "CRASH": "PANIC",
        "SIDEWAYS": "RANGE",
        "CHOP": "RANGE",
    }
    s = aliases.get(s, s)
    if s not in REGIME_LABELS:
        # partial match
        for lab in REGIME_LABELS:
            if lab in s or s in lab:
                return lab
        return "UNKNOWN"
    return s


def regime_breakdown(trades: Sequence[dict]) -> Dict[str, Any]:
    """
    Wyniki per reżim × silnik.

    Struktura:
      TREND_UP: { trend: metrics, reversal: metrics, combined: metrics }
      ...
    Dzięki temu widać np.:
      RANGE → Trend -0.12R, Reversal +0.31R
    """
    trades = list(trades or [])
    by: Dict[str, Dict[str, list]] = {}
    for t in trades:
        reg = _norm_regime(
            t.get("market_regime")
            or t.get("regime")
            or (t.get("signal") or {}).get("market_regime")
        )
        eng = t.get("engine") or "trend"
        if eng not in ("trend", "reversal"):
            eng = "trend"
        by.setdefault(reg, {"trend": [], "reversal": [], "combined": []})
        by[reg][eng].append(t)
        by[reg]["combined"].append(t)

    out: Dict[str, Any] = {}
    for reg in list(REGIME_LABELS) + [k for k in by if k not in REGIME_LABELS]:
        if reg not in by:
            continue
        buckets = by[reg]
        out[reg] = {
            "trend": strategy_metrics(buckets["trend"]),
            "reversal": strategy_metrics(buckets["reversal"]),
            "combined": strategy_metrics(buckets["combined"]),
            "n": len(buckets["combined"]),
        }
    return out


def regime_comparison_table(breakdown: Dict[str, Any]) -> List[dict]:
    """Płaska tabela: regime, engine, expectancy_r, n, wr, pf."""
    rows = []
    for reg, block in (breakdown or {}).items():
        for eng in ("trend", "reversal", "combined"):
            m = block.get(eng) or {}
            if not m or m.get("n", 0) == 0:
                continue
            rows.append({
                "regime": reg,
                "engine": eng,
                "n": m["n"],
                "expectancy_r": m.get("expectancy_r"),
                "win_rate": m.get("win_rate"),
                "profit_factor": m.get("profit_factor"),
                "median_r": m.get("median_r"),
                "max_drawdown_r": m.get("max_drawdown_r"),
                "net_pnl": m.get("net_pnl"),
            })
    return rows


def print_regime_report(breakdown: Dict[str, Any]):
    print("\n========== REGIME TEST (Trend vs Reversal) ==========")
    rows = regime_comparison_table(breakdown)
    if not rows:
        print("  (brak trade'ów z market_regime)")
        return
    # header
    print(f"{'Regime':<16} {'Engine':<10} {'n':>4} {'ExpR':>8} {'WR':>7} {'PF':>7} {'MedR':>8} {'Net$':>9}")
    print("-" * 72)
    for r in rows:
        print(
            f"{r['regime']:<16} {r['engine']:<10} {r['n']:>4} "
            f"{r['expectancy_r']:>+8.3f} {r['win_rate']*100:>6.1f}% "
            f"{r['profit_factor']:>7.2f} {r['median_r']:>+8.3f} {r['net_pnl']:>+9.2f}"
        )
    # insights: where reversal beats trend
    print("\n--- Insights ---")
    for reg, block in breakdown.items():
        tr = (block.get("trend") or {}).get("expectancy_r")
        rv = (block.get("reversal") or {}).get("expectancy_r")
        nt = (block.get("trend") or {}).get("n", 0)
        nr = (block.get("reversal") or {}).get("n", 0)
        if nt == 0 and nr == 0:
            continue
        if nt and nr:
            if rv is not None and tr is not None and rv > tr + 0.05:
                print(f"  {reg}: Reversal lepszy ({rv:+.2f}R vs Trend {tr:+.2f}R)")
            elif tr is not None and rv is not None and tr > rv + 0.05:
                print(f"  {reg}: Trend lepszy ({tr:+.2f}R vs Reversal {rv:+.2f}R)")
        elif nr and not nt:
            print(f"  {reg}: tylko Reversal n={nr} exp={rv:+.2f}R")
        elif nt and not nr:
            print(f"  {reg}: tylko Trend n={nt} exp={tr:+.2f}R")


def reversal_extra_metrics(shadow_rows: Sequence[dict] = None, trades: Sequence[dict] = None) -> Dict[str, Any]:
    """
    Reversal-specific:
      MFE, MAE, time to TP/SL, distance from extreme,
      performance vs RSI / impulse / liquidity
    """
    rows = list(shadow_rows or [])
    # If no shadow rows, try load CSV
    if not rows:
        path = LOGS / "reversal_shadow.csv"
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    rows = list(csv.DictReader(f))
                rows = [r for r in rows if (r.get("event") or "") == "REVERSAL_SHADOW_CLOSE"]
            except Exception:
                rows = []

    out: Dict[str, Any] = {
        "n_shadow_closed": len(rows),
        "avg_mfe_pct": 0.0,
        "avg_mae_pct": 0.0,
        "median_mfe_pct": 0.0,
        "median_mae_pct": 0.0,
        "avg_mfe_r": 0.0,
        "avg_mae_r": 0.0,
        "tp1_hit_rate": 0.0,
        "tp2_hit_rate": 0.0,
        "sl_hit_rate": 0.0,
        "avg_time_to_tp1_s": None,
        "avg_time_to_tp2_s": None,
        "avg_time_to_sl_s": None,
        "by_impulse": {},
        "by_rsi_zone": {},
        "by_liquidity": {},
    }
    if not rows:
        return out

    mfes = [_f(r.get("mfe")) for r in rows]
    maes = [_f(r.get("mae")) for r in rows]
    mfe_rs = [_f(r.get("mfe_r")) for r in rows]
    mae_rs = [_f(r.get("mae_r")) for r in rows]
    n = len(rows)

    def _bool(v):
        return str(v).lower() in ("1", "true", "yes")

    tp1 = sum(1 for r in rows if _bool(r.get("hit_tp1")))
    tp2 = sum(1 for r in rows if _bool(r.get("hit_tp2")))
    sl = sum(1 for r in rows if _bool(r.get("hit_sl")))

    def _avg_time(key):
        xs = [_f(r.get(key)) for r in rows if r.get(key) not in (None, "", "None")]
        xs = [x for x in xs if x > 0]
        return round(sum(xs) / len(xs), 1) if xs else None

    out.update({
        "avg_mfe_pct": round(sum(mfes) / n, 3),
        "avg_mae_pct": round(sum(maes) / n, 3),
        "median_mfe_pct": round(_median(mfes), 3),
        "median_mae_pct": round(_median(maes), 3),
        "avg_mfe_r": round(sum(mfe_rs) / n, 3),
        "avg_mae_r": round(sum(mae_rs) / n, 3),
        "tp1_hit_rate": round(tp1 / n, 3),
        "tp2_hit_rate": round(tp2 / n, 3),
        "sl_hit_rate": round(sl / n, 3),
        "avg_time_to_tp1_s": _avg_time("time_to_tp1_s"),
        "avg_time_to_tp2_s": _avg_time("time_to_tp2_s"),
        "avg_time_to_sl_s": _avg_time("time_to_sl_s"),
    })

    # Bucket helpers from reasons / confidence fields if present
    # impulse: parse EXT_PUMP/DUMP from reasons
    def impulse_bucket(r):
        reasons = str(r.get("reasons") or "")
        # try change from entry path — not stored; use confidence as proxy or parse
        for part in reasons.split("|"):
            if "EXT_PUMP" in part or "EXT_DUMP" in part or "24h=" in part:
                try:
                    # 24h=+22.0% or similar
                    import re
                    m = re.search(r"24h=([+-]?\d+(?:\.\d+)?)", part)
                    if m:
                        v = abs(float(m.group(1)))
                        if v >= 30:
                            return "impulse_30+"
                        if v >= 22:
                            return "impulse_22_30"
                        return "impulse_18_22"
                except Exception:
                    pass
        return "impulse_unknown"

    def rsi_bucket(r):
        reasons = str(r.get("reasons") or "")
        import re
        m = re.search(r"RSI[^\d]*(\d+(?:\.\d+)?)", reasons)
        if not m:
            return "rsi_unknown"
        v = float(m.group(1))
        if v <= 30:
            return "rsi_<=30"
        if v <= 40:
            return "rsi_30_40"
        if v >= 70:
            return "rsi_>=70"
        if v >= 60:
            return "rsi_60_70"
        return "rsi_mid"

    def liq_bucket(r):
        reasons = str(r.get("reasons") or "")
        if "THIN" in reasons or "thin" in reasons:
            return "liq_thin"
        if "MULTI_SRC" in reasons or "OB_BID" in reasons or "OB_ASK" in reasons:
            return "liq_ok"
        return "liq_unknown"

    def bucket_stats(key_fn):
        groups: Dict[str, List[dict]] = {}
        for r in rows:
            k = key_fn(r)
            groups.setdefault(k, []).append(r)
        res = {}
        for k, g in groups.items():
            hits = sum(1 for x in g if _bool(x.get("hit_tp1")) or _bool(x.get("hit_tp2")))
            sls = sum(1 for x in g if _bool(x.get("hit_sl")))
            res[k] = {
                "n": len(g),
                "tp_hit_rate": round(hits / len(g), 3),
                "sl_rate": round(sls / len(g), 3),
                "avg_mfe": round(sum(_f(x.get("mfe")) for x in g) / len(g), 3),
                "avg_mae": round(sum(_f(x.get("mae")) for x in g) / len(g), 3),
            }
        return res

    out["by_impulse"] = bucket_stats(impulse_bucket)
    out["by_rsi_zone"] = bucket_stats(rsi_bucket)
    out["by_liquidity"] = bucket_stats(liq_bucket)

    # Distance from extreme: approx |entry move needed| vs impulse — use MAE/MFE ratio
    ratios = []
    for r in rows:
        mfe, mae = _f(r.get("mfe")), _f(r.get("mae"))
        if mfe + mae > 0:
            ratios.append(mfe / (mfe + mae))
    out["avg_mfe_share"] = round(sum(ratios) / len(ratios), 3) if ratios else 0.0

    return out


def full_report(
    trades: Sequence[dict] = None,
    equity_curve: Sequence[float] = None,
    starting_equity: float = None,
    shadow_rows: Sequence[dict] = None,
) -> Dict[str, Any]:
    """Trend / Reversal / Combined + reversal extras."""
    trades = list(trades or [])
    trend = [t for t in trades if (t.get("engine") or "trend") == "trend"]
    rev = [t for t in trades if t.get("engine") == "reversal"]
    report = {
        "trend": strategy_metrics(trend, equity_curve=None, starting_equity=starting_equity),
        "reversal_executed": strategy_metrics(rev, equity_curve=None, starting_equity=starting_equity),
        "combined": strategy_metrics(trades, equity_curve=equity_curve, starting_equity=starting_equity),
        "reversal_shadow": reversal_extra_metrics(shadow_rows=shadow_rows, trades=rev),
        "by_regime": regime_breakdown(trades),
    }
    return report


def print_report(report: Dict[str, Any]):
    def block(title, m):
        print(f"\n=== {title} ===")
        if not m or m.get("n", 0) == 0 and "n_shadow_closed" not in m:
            print("  (brak danych)")
            return
        if "n_shadow_closed" in m:
            print(f"  Shadow closed:   {m.get('n_shadow_closed')}")
            print(f"  Avg MFE / MAE:   {m.get('avg_mfe_pct')}% / {m.get('avg_mae_pct')}%")
            print(f"  Median MFE/MAE:  {m.get('median_mfe_pct')}% / {m.get('median_mae_pct')}%")
            print(f"  MFE/MAE (R):     {m.get('avg_mfe_r')} / {m.get('avg_mae_r')}")
            print(f"  TP1 / TP2 / SL:  {m.get('tp1_hit_rate')} / {m.get('tp2_hit_rate')} / {m.get('sl_hit_rate')}")
            print(f"  Time TP1/TP2/SL: {m.get('avg_time_to_tp1_s')}s / {m.get('avg_time_to_tp2_s')}s / {m.get('avg_time_to_sl_s')}s")
            for label, key in (("Impulse", "by_impulse"), ("RSI", "by_rsi_zone"), ("Liquidity", "by_liquidity")):
                b = m.get(key) or {}
                if b:
                    print(f"  vs {label}:")
                    for k, v in b.items():
                        print(f"    {k}: n={v['n']} tp={v['tp_hit_rate']} sl={v['sl_rate']} mfe={v['avg_mfe']}")
            return
        # PRIORYTET 17 — pełny zestaw per silnik
        print(f"  Trades:          {m.get('trades') or m.get('n')}")
        print(f"  Win Rate:        {m.get('win_rate')}")
        print(f"  Average R:       {m.get('average_r') or m.get('median_r')}")
        print(f"  Expectancy:      {m.get('expectancy_r')}")
        print(f"  Profit Factor:   {m.get('profit_factor')}")
        print(f"  Max DD (R):      {m.get('max_drawdown_r') or m.get('max_dd')}")
        print(f"  MFE avg/med:     {m.get('mfe_avg')} / {m.get('mfe_median')}")
        print(f"  MAE avg/med:     {m.get('mae_avg')} / {m.get('mae_median')}")
        print(f"  Avg Win / Loss:  {m.get('avg_win')} / {m.get('avg_loss')}")
        print(f"  Avg WinR/LossR:  {m.get('avg_win_r')} / {m.get('avg_loss_r')}")
        print(f"  Median R:        {m.get('median_r')}")
        print(f"  Max DD %:        {m.get('max_drawdown')}")
        print(f"  Sharpe/Sortino:  {m.get('sharpe')} / {m.get('sortino')}")
        print(f"  Calmar/MAR:      {m.get('calmar')} / {m.get('mar')}")
        print(f"  Net PnL:         {m.get('net_pnl')}")
        print(f"  Return %:        {m.get('total_return_pct')}")

    print("\n========== PRIORYTET 17 — TREND vs REVERSAL vs COMBINED ==========")
    block("TREND", report.get("trend"))
    block("REVERSAL", report.get("reversal_executed") or report.get("reversal"))
    block("COMBINED (cały portfel)", report.get("combined"))
    block("REVERSAL SHADOW (extra)", report.get("reversal_shadow"))

    # Czy reversal poprawia strategię, czy tylko dokłada volume?
    tr = report.get("trend") or {}
    rv = report.get("reversal_executed") or report.get("reversal") or {}
    cb = report.get("combined") or {}
    print("\n--- Pytanie decyzyjne ---")
    nt, nr = tr.get("n", 0) or 0, rv.get("n", 0) or 0
    if nr == 0:
        print("  Brak executed reversal — porównaj shadow / zbierz próbkę.")
    elif nt == 0:
        print("  Tylko reversal w próbce — brak bazy trend do porównania.")
    else:
        et, er = tr.get("expectancy_r") or 0, rv.get("expectancy_r") or 0
        ec = cb.get("expectancy_r") or 0
        if er > et + 0.05 and ec >= et:
            print(f"  ✓ Reversal poprawia expectancy ({er:+.3f}R vs Trend {et:+.3f}R; Combined {ec:+.3f}R)")
        elif er > 0 and ec > et:
            print(f"  ~ Reversal lekko pomaga Combined ({ec:+.3f}R > Trend {et:+.3f}R)")
        elif er <= 0 and nr > 0:
            print(f"  ✗ Reversal psuje (exp {er:+.3f}R) — nie zwiększaj share / zostań w shadow")
        else:
            print(
                f"  ? Reversal głównie volume: n={nr} exp={er:+.3f}R vs Trend n={nt} exp={et:+.3f}R "
                f"| Combined exp={ec:+.3f}R"
            )
    if report.get("by_regime"):
        print_regime_report(report["by_regime"])


def save_report(report: Dict[str, Any], path: str = None):
    path = path or str(LOGS / "performance_last.json")
    LOGS.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    return path


def report_from_backtest_json(path: str = None) -> Dict[str, Any]:
    path = path or str(LOGS / "backtest_last.json")
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    trades = data.get("trades") or []
    eq = data.get("equity_curve")
    start = data.get("starting_capital") or data.get("start_equity")
    rep = full_report(trades, equity_curve=eq, starting_equity=start)
    print_report(rep)
    save_report(rep)
    return rep
