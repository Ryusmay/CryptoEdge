# ============================================================
# PRIORYTET 15 — Walk-Forward Validation
# ============================================================
#
#   TRAIN → VALIDATION → TEST → przesunięcie okna → ...
#
# NIE optymalizujemy parametrów na całej historii.
# Metryka decyzyjna = tylko TEST (OOS).
# TRAIN  = kontekst / diagnostyka IS
# VAL    = sanity (czy fold „wygląda” sensownie) — bez strojenia progów
# TEST   = jedyna agregacja OOS
# ============================================================

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from event_backtester import (
    EventBacktester,
    BTResult,
    fetch_mtf_bundle,
    fetch_funding_schedule,
    clip_bundle_overlap,
    _cfg_get,
)

try:
    from performance_metrics import full_report, print_report, save_report, strategy_metrics
except Exception:
    full_report = print_report = save_report = strategy_metrics = None  # type: ignore


LOGS = Path("logs")


@dataclass
class WFFold:
    fold: int
    train_start_ts: int
    train_end_ts: int
    val_start_ts: int = 0
    val_end_ts: int = 0
    test_start_ts: int = 0
    test_end_ts: int = 0
    train_n_bars: int = 0
    val_n_bars: int = 0
    test_n_bars: int = 0
    train_result: Optional[dict] = None
    val_result: Optional[dict] = None
    test_result: Optional[dict] = None


@dataclass
class WFReport:
    folds: List[WFFold] = field(default_factory=list)
    oos_trades: List[dict] = field(default_factory=list)       # tylko TEST
    val_trades: List[dict] = field(default_factory=list)       # VALIDATION (nie do fit)
    is_trades: List[dict] = field(default_factory=list)        # TRAIN
    oos_metrics: Dict[str, Any] = field(default_factory=dict)
    val_metrics: Dict[str, Any] = field(default_factory=dict)
    is_metrics: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    config: Dict[str, Any] = field(default_factory=dict)


def _ts_list(ohlcv: dict) -> List[int]:
    ts = ohlcv.get("ts") or []
    return [int(x) for x in ts]


def _slice_ohlcv(ohlcv: dict, t0: int, t1: int) -> dict:
    """Bars with t0 <= ts < t1 (or <= t1 if last fold)."""
    ts = _ts_list(ohlcv)
    if not ts:
        return {}
    idx = [i for i, t in enumerate(ts) if t0 <= t <= t1]
    if len(idx) < 5:
        return {}
    a, b = idx[0], idx[-1] + 1
    out = {}
    for k in ("ts", "opens", "highs", "lows", "closes", "volumes"):
        arr = ohlcv.get(k) or []
        out[k] = list(arr[a:b])
    if ohlcv.get("source"):
        out["source"] = ohlcv["source"]
    return out


def _slice_bundle(bundle: Dict[str, dict], t0: int, t1: int) -> Dict[str, dict]:
    out = {}
    for tf, ohlcv in (bundle or {}).items():
        s = _slice_ohlcv(ohlcv, t0, t1)
        if len(s.get("closes") or []) >= 20:
            out[tf] = s
    return out


def _slice_bundle_with_warmup(bundle: Dict[str, dict], t0: int, t1: int) -> Dict[str, dict]:
    """OOS slice plus causal indicator pre-roll, independently per timeframe."""
    out = {}
    warmup = {"15m": 80, "1h": 80, "4h": 220, "1d": 220}
    for tf, ohlcv in (bundle or {}).items():
        ts = _ts_list(ohlcv)
        if not ts:
            continue
        in_window = [i for i, t in enumerate(ts) if t0 <= t <= t1]
        if not in_window:
            continue
        a = max(0, in_window[0] - int(warmup.get(tf, 80)))
        b = in_window[-1] + 1
        sliced = {}
        for key in ("ts", "opens", "highs", "lows", "closes", "volumes"):
            sliced[key] = list((ohlcv.get(key) or [])[a:b])
        if ohlcv.get("source"):
            sliced["source"] = ohlcv["source"]
        out[tf] = sliced
    return out


def _drive_ts(bundles: Dict[str, Dict[str, dict]], drive_tf: str = "1h") -> List[int]:
    for sym, b in bundles.items():
        o = b.get(drive_tf) or b.get("1h") or {}
        ts = _ts_list(o)
        if ts:
            return ts
    # fallback any tf
    for sym, b in bundles.items():
        for o in b.values():
            ts = _ts_list(o)
            if ts:
                return ts
    return []


def make_folds(
    ts: List[int],
    train_bars: int = 500,
    val_bars: int = 100,
    test_bars: int = 150,
    step_bars: int = 150,
    min_folds: int = 2,
    anchored: bool = False,
    purge_bars: int = 0,
    embargo_bars: int = 0,
) -> List[Tuple[int, int, int, int, int, int]]:
    """
    Zwraca listę:
      (train_start_ts, train_end_ts, val_start_ts, val_end_ts, test_start_ts, test_end_ts)

    TRAIN → VALIDATION → TEST → przesunięcie o step_bars

    Rolling: stała długość train; anchored: train od t0 do początku val.
    """
    n = len(ts)
    folds = []
    purge_bars = max(0, int(purge_bars))
    embargo_bars = max(0, int(embargo_bars))
    need = train_bars + val_bars + test_bars + 2 * purge_bars
    if n < need:
        return folds

    start = 0
    while True:
        if anchored:
            tr_a, tr_b = 0, start + train_bars
        else:
            tr_a, tr_b = start, start + train_bars
        va_a, va_b = tr_b + purge_bars, tr_b + purge_bars + val_bars
        te_a, te_b = va_b + purge_bars, va_b + purge_bars + test_bars
        if te_b > n:
            break
        folds.append((
            int(ts[tr_a]),
            int(ts[tr_b - 1]),
            int(ts[va_a]),
            int(ts[va_b - 1]),
            int(ts[te_a]),
            int(ts[min(te_b, n) - 1]),
        ))
        start += step_bars + embargo_bars
        if start + need > n and not anchored:
            break
        if anchored and te_b >= n:
            break
        if len(folds) > 50:
            break
    return folds


def _result_to_dict(res: BTResult) -> dict:
    return {
        "n_trades": res.n_trades,
        "final_equity": res.final_equity,
        "max_dd": res.max_dd,
        "win_rate": res.win_rate,
        "profit_factor": res.profit_factor,
        "avg_r": res.avg_r,
        "expectancy_r": res.expectancy_r,
        "trades": res.trades,
        "by_engine": getattr(res, "by_engine", {}) or {},
        "notes": res.notes[:10] if res.notes else [],
    }


def run_walk_forward(
    bundles: Dict[str, Dict[str, dict]],
    drive_tf: str = "1h",
    train_bars: int = 500,
    val_bars: int = 100,
    test_bars: int = 150,
    step_bars: int = 150,
    anchored: bool = False,
    confirm_bundles: Dict[str, Dict[str, dict]] = None,
    primary_source: str = "auto",
    funding_schedules: Dict[str, list] = None,
    starting_capital: float = 100.0,
    leverage: float = 10.0,
    max_positions: int = 5,
    run_train: bool = True,
    run_val: bool = True,
    purge_bars: int = None,
    embargo_bars: int = None,
) -> WFReport:
    """
    Walk-forward: TRAIN → VALIDATION → TEST → shift.

    Parametry strategii NIE są optymalizowane na żadnym oknie.
    Agregat decyzyjny = wyłącznie TEST (OOS).
    """
    purge_bars = int(_cfg_get("DAYTRADING_WF_PURGE_BARS", 12) if purge_bars is None else purge_bars)
    embargo_bars = int(_cfg_get("DAYTRADING_WF_EMBARGO_BARS", 12) if embargo_bars is None else embargo_bars)
    report = WFReport(config={
        "train_bars": train_bars,
        "val_bars": val_bars,
        "test_bars": test_bars,
        "step_bars": step_bars,
        "anchored": anchored,
        "drive_tf": drive_tf,
        "primary_source": primary_source,
        "starting_capital": starting_capital,
        "no_fit_on_full_history": True,
        "purge_bars": purge_bars,
        "embargo_bars": embargo_bars,
    })

    ts = _drive_ts(bundles, drive_tf)
    need = train_bars + val_bars + test_bars + 2 * purge_bars
    if len(ts) < need:
        report.notes.append(
            f"za mało barów drive_tf={drive_tf}: {len(ts)} < {need}"
        )
        return report

    fold_specs = make_folds(
        ts, train_bars, val_bars, test_bars, step_bars, anchored=anchored,
        purge_bars=purge_bars, embargo_bars=embargo_bars,
    )
    if len(fold_specs) < 1:
        report.notes.append("0 foldów — zwiększ historię lub zmniejsz train/val/test")
        return report

    report.notes.append(
        f"folds={len(fold_specs)} drive_bars={len(ts)} "
        f"schema=TRAIN({train_bars})→VAL({val_bars})→TEST({test_bars})"
    )

    bt = EventBacktester(
        starting_capital=starting_capital,
        leverage=leverage,
        max_positions=max_positions,
        fee=0.0006,
        slip=0.0003,
        risk_pct=0.005,
        risk_pct_max=0.0075,
        max_portfolio_risk=0.025,
    )

    for i, (tr0, tr1, va0, va1, te0, te1) in enumerate(fold_specs):
        fold = WFFold(
            fold=i + 1,
            train_start_ts=tr0,
            train_end_ts=tr1,
            val_start_ts=va0,
            val_end_ts=va1,
            test_start_ts=te0,
            test_end_ts=te1,
        )

        def _run_window(t0, t1, label: str, warmup_hint: int):
            win = {sym: _slice_bundle_with_warmup(b, t0, t1) for sym, b in bundles.items()}
            win = {s: b for s, b in win.items() if b}
            conf = None
            if confirm_bundles:
                conf = {
                    sym: _slice_bundle_with_warmup(b, t0, t1) for sym, b in confirm_bundles.items()
                }
                conf = {s: b for s, b in conf.items() if b}
            n_bars = len(_drive_ts(win, drive_tf))
            if n_bars < 30:
                return n_bars, {"n_trades": 0, "notes": [f"{label} window too short"]}, []
            try:
                res = bt.run_mtf(
                    win,
                    drive_tf=drive_tf,
                    confirm_bundles=conf,
                    primary_source=primary_source,
                    funding_schedules=funding_schedules,
                    warmup=min(warmup_hint, max(10, n_bars // 5)),
                    evaluation_start_ts=t0,
                    evaluation_end_ts=t1,
                )
                return n_bars, _result_to_dict(res), res.trades or []
            except Exception as e:
                report.notes.append(f"fold {i+1} {label} err: {e}")
                return n_bars, {"error": str(e), "n_trades": 0}, []

        # --- TRAIN (IS) — diagnostyka, bez fitowania ---
        if run_train:
            tn, tres, ttrades = _run_window(tr0, tr1, "TRAIN", 60)
            fold.train_n_bars = tn
            fold.train_result = tres
            report.is_trades.extend(ttrades)

        # --- VALIDATION — sanity, też bez strojenia ---
        if run_val and val_bars > 0:
            vn, vres, vtrades = _run_window(va0, va1, "VAL", 40)
            fold.val_n_bars = vn
            fold.val_result = vres
            report.val_trades.extend(vtrades)

        # --- TEST (OOS) — jedyna metryka decyzyjna ---
        ten, teres, tetrades = _run_window(te0, te1, "TEST", 40)
        fold.test_n_bars = ten
        fold.test_result = teres
        report.oos_trades.extend(tetrades)

        report.folds.append(fold)
        tr_n = (fold.test_result or {}).get("n_trades", 0)
        print(
            f"[WF] fold {i+1}/{len(fold_specs)} "
            f"TRAIN={fold.train_n_bars} VAL={fold.val_n_bars} TEST={fold.test_n_bars} "
            f"OOS trades={tr_n} avgR={(fold.test_result or {}).get('avg_r', 0):.3f}"
        )

    # Agregacja — TYLKO TEST (OOS) decyduje o edge
    if strategy_metrics:
        report.oos_metrics = strategy_metrics(report.oos_trades, starting_equity=starting_capital)
        report.val_metrics = strategy_metrics(report.val_trades, starting_equity=starting_capital)
        report.is_metrics = strategy_metrics(report.is_trades, starting_equity=starting_capital)
    else:
        report.oos_metrics = {"n": len(report.oos_trades)}
        report.val_metrics = {"n": len(report.val_trades)}
        report.is_metrics = {"n": len(report.is_trades)}

    if full_report:
        try:
            rep = full_report(report.oos_trades, starting_equity=starting_capital)
            report.oos_metrics["full"] = {
                "trend": rep.get("trend"),
                "reversal": rep.get("reversal_executed"),
                "combined": rep.get("combined"),
                "by_regime": rep.get("by_regime"),
            }
        except Exception as e:
            report.notes.append(f"full_report OOS: {e}")

    report.notes.append(
        "NO_FIT: parametry strategii nie są optymalizowane na TRAIN/VAL/pełnej historii"
    )
    return report


def print_wf_report(report: WFReport):
    print("\n========== WALK-FORWARD (OOS only matters) ==========")
    print(f"Config: {report.config}")
    print(f"Folds: {len(report.folds)}")
    print(f"Notes: {report.notes[:5]}")
    print("\n--- Per fold (TEST / OOS) ---")
    for f in report.folds:
        tr = f.test_result or {}
        print(
            f"  fold {f.fold}: bars={f.test_n_bars} "
            f"n={tr.get('n_trades', 0)} "
            f"avgR={tr.get('avg_r', 0):+.3f} "
            f"WR={100*(tr.get('win_rate') or 0):.0f}% "
            f"PF={tr.get('profit_factor', 0):.2f} "
            f"DD={100*(tr.get('max_dd') or 0):.1f}%"
        )
    print("\n=== OOS AGGREGATE (decyzja) ===")
    m = report.oos_metrics or {}
    if m.get("full"):
        # prefer nested
        comb = m["full"].get("combined") or m
        print(f"  Trades:        {comb.get('n')}")
        print(f"  Expectancy/R:  {comb.get('expectancy_r')}")
        print(f"  Win rate:      {comb.get('win_rate')}")
        print(f"  Profit factor: {comb.get('profit_factor')}")
        print(f"  Median R:      {comb.get('median_r')}")
        print(f"  Max DD (R):    {comb.get('max_drawdown_r')}")
        print(f"  Sharpe:        {comb.get('sharpe')}")
    else:
        print(f"  {m}")
    print("\n=== VAL AGGREGATE (sanity — NIE do strojenia) ===")
    print(f"  n_trades={report.val_metrics.get('n')} expR={report.val_metrics.get('expectancy_r')}")
    print("\n=== IS AGGREGATE (tylko diagnostyka — NIE decyzja) ===")
    print(f"  n_trades={report.is_metrics.get('n')} expR={report.is_metrics.get('expectancy_r')}")


def save_wf_report(report: WFReport, path: str = None) -> str:
    LOGS.mkdir(parents=True, exist_ok=True)
    path = path or str(LOGS / "walk_forward_last.json")
    payload = {
        "config": report.config,
        "notes": report.notes,
        "oos_metrics": report.oos_metrics,
        "val_metrics": report.val_metrics,
        "is_metrics": report.is_metrics,
        "n_oos_trades": len(report.oos_trades),
        "n_val_trades": len(report.val_trades),
        "n_is_trades": len(report.is_trades),
        "folds": [
            {
                "fold": f.fold,
                "train_start_ts": f.train_start_ts,
                "train_end_ts": f.train_end_ts,
                "val_start_ts": f.val_start_ts,
                "val_end_ts": f.val_end_ts,
                "test_start_ts": f.test_start_ts,
                "test_end_ts": f.test_end_ts,
                "train_n_bars": f.train_n_bars,
                "val_n_bars": f.val_n_bars,
                "test_n_bars": f.test_n_bars,
                "train_result": {
                    k: v for k, v in (f.train_result or {}).items() if k != "trades"
                },
                "val_result": {
                    k: v for k, v in (f.val_result or {}).items() if k != "trades"
                },
                "test_result": {
                    k: v for k, v in (f.test_result or {}).items() if k != "trades"
                },
            }
            for f in report.folds
        ],
        "oos_trades": report.oos_trades,
    }
    Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def run_cli():
    ap = argparse.ArgumentParser(description="CryptoEdge Walk-Forward (PRIORYTET 15)")
    ap.add_argument("--symbols", default="BTC,ETH")
    ap.add_argument("--drive-tf", default="1h")
    ap.add_argument("--data-source", default="auto", choices=["binance", "blofin", "auto"])
    ap.add_argument("--train-bars", type=int, default=500, help="~21 dni na 1h")
    ap.add_argument("--val-bars", type=int, default=100, help="validation window")
    ap.add_argument("--test-bars", type=int, default=150, help="~6 dni na 1h OOS")
    ap.add_argument("--step-bars", type=int, default=150, help="przesunięcie okna")
    ap.add_argument("--anchored", action="store_true", help="rosnący TRAIN od początku")
    ap.add_argument("--capital", type=float, default=100.0)
    ap.add_argument("--leverage", type=float, default=10.0)
    ap.add_argument("--max-pos", type=int, default=5)
    ap.add_argument("--skip-train", action="store_true", help="pomiń TRAIN (szybciej)")
    ap.add_argument("--skip-val", action="store_true", help="pomiń VALIDATION")
    ap.add_argument("--limit-1h", type=int, default=1500)
    ap.add_argument("--limit-4h", type=int, default=500)
    ap.add_argument("--limit-1d", type=int, default=400)
    ap.add_argument("--limit-15m", type=int, default=800)
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    limits = {
        "15m": args.limit_15m,
        "1h": args.limit_1h,
        "4h": args.limit_4h,
        "1d": args.limit_1d,
    }

    print(
        f"[WF] source={args.data_source} "
        f"TRAIN={args.train_bars} → VAL={args.val_bars} → TEST={args.test_bars} "
        f"step={args.step_bars} anchored={args.anchored}"
    )
    bundles = {}
    for sym in symbols:
        print(f"[WF] fetch MTF {sym}...")
        b = fetch_mtf_bundle(sym, source=args.data_source, limits=limits)
        if "1h" in b or "4h" in b:
            bundles[sym] = b
        time.sleep(0.05)

    if not bundles:
        print("[WF] no data")
        return

    confirm_bundles = {}
    primary_source = args.data_source
    if args.data_source in ("blofin", "bf", "auto"):
        print("[WF] Binance confirmation bundles...")
        for sym in list(bundles.keys()):
            cb = fetch_mtf_bundle(sym, source="binance", limits=limits)
            if cb:
                confirm_bundles[sym] = cb
            time.sleep(0.05)
    else:
        confirm_bundles = bundles

    funding_schedules = {}
    for sym in list(bundles.keys()):
        funding_schedules[sym] = fetch_funding_schedule(sym, limit=300, source="blofin")
        time.sleep(0.05)

    report = run_walk_forward(
        bundles,
        drive_tf=args.drive_tf,
        train_bars=args.train_bars,
        val_bars=args.val_bars,
        test_bars=args.test_bars,
        step_bars=args.step_bars,
        anchored=args.anchored,
        confirm_bundles=confirm_bundles,
        primary_source=primary_source,
        funding_schedules=funding_schedules,
        starting_capital=args.capital,
        leverage=args.leverage,
        max_positions=args.max_pos,
        run_train=not args.skip_train,
        run_val=not args.skip_val,
    )
    print_wf_report(report)
    path = save_wf_report(report)
    print(f"\nSaved {path}")
    if full_report and report.oos_trades:
        try:
            rep = full_report(report.oos_trades, starting_equity=args.capital)
            print_report(rep)
            save_report(rep, str(LOGS / "performance_wf_oos.json"))
        except Exception as e:
            print(f"metrics: {e}")


if __name__ == "__main__":
    run_cli()
