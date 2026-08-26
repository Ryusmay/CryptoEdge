"""Purged walk-forward for DayTrading V2. Frozen config — no fit on train.

Default: 21D train / 7D test / 48h purge / 48h embargo, drive=1H.
Decyzja tylko z TEST (OOS). Train w raporcie jako okno, nie jako fit.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import config
from daytrading_validation import rolling_purged_folds


LOGS = Path("logs")
MS_HOUR = 3_600_000
MS_15M = 900_000


@dataclass
class V2Fold:
    fold: int
    train_start_ts: int
    train_end_ts: int
    test_start_ts: int
    test_end_ts: int
    metrics: Dict[str, Any] = field(default_factory=dict)
    by_symbol: Dict[str, Any] = field(default_factory=dict)
    n_trades: int = 0


@dataclass
class V2WFReport:
    folds: List[V2Fold] = field(default_factory=list)
    oos_trades: List[dict] = field(default_factory=list)
    oos_metrics: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    config: Dict[str, Any] = field(default_factory=dict)


def _ts(ohlcv: dict) -> List[int]:
    return [int(x) for x in (ohlcv.get("timestamps") or ohlcv.get("ts") or [])]


def _drive_1h_ts(bundle: dict) -> List[int]:
    for key in ("1h", "1H"):
        ts = _ts(bundle.get(key) or {})
        if len(ts) >= 20:
            return ts
    ts5 = _ts(bundle.get("5m") or {})
    return ts5[::12] if len(ts5) >= 24 else ts5


def _slice_ohlcv(ohlcv: dict, t0: int, t1: int) -> dict:
    ts = _ts(ohlcv)
    if not ts:
        return {}
    idx = [i for i, t in enumerate(ts) if t0 <= t <= t1]
    if len(idx) < 5:
        return {}
    a, b = idx[0], idx[-1] + 1
    out = {}
    for k, arr in ohlcv.items():
        if isinstance(arr, list):
            out[k] = list(arr[a:b])
        else:
            out[k] = arr
    if "timestamps" not in out and out.get("ts"):
        out["timestamps"] = list(out["ts"])
    return out


def fold_metrics(rs: List[float]) -> dict:
    n = len(rs)
    if n == 0:
        return {
            "n": 0, "win_rate": 0.0, "avg_r": 0.0, "net_r": 0.0,
            "profit_factor": 0.0, "max_dd_r": 0.0, "negative": False,
        }
    wins = sum(1 for r in rs if r > 0)
    gains = sum(r for r in rs if r > 0)
    losses = sum(-r for r in rs if r < 0)
    eq = peak = max_dd = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
    pf = (gains / losses) if losses > 0 else (float("inf") if gains > 0 else 0.0)
    net = sum(rs)
    return {
        "n": n,
        "win_rate": wins / n,
        "avg_r": net / n,
        "net_r": net,
        "profit_factor": pf if pf != float("inf") else 99.0,
        "max_dd_r": max_dd,
        "negative": net < 0,
    }


def _trade_row(symbol: str, trade) -> dict:
    try:
        from v2_profiles import profile_for
        prof = profile_for(symbol)
    except Exception:
        prof = "alt"
    return {
        "symbol": symbol,
        "v2_profile": prof,
        "direction": getattr(trade, "direction", None),
        "realised_r": float(getattr(trade, "realised_r", 0) or 0),
        "exit_reason": getattr(trade, "exit_reason", None),
        "tp1_done": bool(getattr(trade, "tp1_done", False)),
        "tp2_done": bool(getattr(trade, "tp2_done", False)),
        "entry_i": getattr(trade, "entry_i", None),
        "exit_i": getattr(trade, "exit_i", None),
        "slip_rt": getattr(trade, "slip_rt", None),
        "funding_r": getattr(trade, "funding_r", None),
        "mae_r": float(getattr(trade, "mae_r", 0) or 0),
        "mfe_r": float(getattr(trade, "mfe_r", 0) or 0),
        "fill_kind": getattr(trade, "fill_kind", None),
        "market_regime": getattr(trade, "market_regime", "unknown"),
    }


def _run_symbol_fold(
    symbol: str,
    bundle: dict,
    te0: int,
    te1: int,
    replay_fn=None,
    provider_fn=None,
    bias_fn=None,
    trail_fn=None,
) -> Tuple[List[dict], dict]:
    from daytrading_backtester import (
        production_signal_provider_v2,
        htf_bias_provider_v2,
        htf_trail_anchor_provider_v2,
        replay_daytrading_v2,
        v2_max_bars_5m,
    )
    replay_fn = replay_fn or replay_daytrading_v2
    provider_fn = provider_fn or production_signal_provider_v2
    bias_fn = bias_fn or htf_bias_provider_v2
    trail_fn = trail_fn or htf_trail_anchor_provider_v2

    warmup_ms = 12 * 24 * MS_HOUR  # ~12D 1H historii przed testem
    stop_h = float(getattr(config, "DAYTRADING_V2_HARD_TIME_STOP_HOURS", 48.0) or 48.0)
    exit_ms = int((stop_h + 2) * MS_HOUR)
    ohlcv_5m_full = bundle.get("5m") or {}
    ohlcv = _slice_ohlcv(ohlcv_5m_full, te0 - warmup_ms, te1 + exit_ms)
    if len(ohlcv.get("opens") or []) < 50:
        return [], fold_metrics([])

    win_bundle = dict(bundle)
    win_bundle["5m"] = ohlcv
    if bundle.get("funding"):
        win_bundle["funding"] = bundle.get("funding")
    ts5 = _ts(ohlcv)
    n = len(ohlcv.get("opens") or [])
    signal_at_raw, engine = provider_fn(symbol, win_bundle)

    def gated(index: int):
        if index < 0 or index >= n:
            return None
        ts = ts5[index] if index < len(ts5) else 0
        if ts < te0 or ts > te1:
            return None
        if ts % MS_15M != 0:
            return None
        return signal_at_raw(index)

    result = replay_fn(
        ohlcv, gated,
        htf_bias_at=bias_fn(symbol, win_bundle),
        htf_trail_anchor_at=trail_fn(symbol, win_bundle),
        notify_exit=getattr(engine, "notify_exit", None),
        fee_frac_round_trip=2.0 * float(getattr(config, "TAKER_FEE", 0.0006) or 0.0006),
        slippage_frac_round_trip=0.0006,
        funding=win_bundle.get("funding") or [],
        max_bars=v2_max_bars_5m(),
    )
    rows = [_trade_row(symbol, t) for t in (result.get("trades") or [])]
    rs = [r["realised_r"] for r in rows]
    return rows, fold_metrics(rs)


def run_walk_forward_v2(
    bundles: Dict[str, dict],
    train_days: Optional[int] = None,
    test_days: Optional[int] = None,
    purge_hours: Optional[int] = None,
    embargo_hours: Optional[int] = None,
    replay_fn=None,
    provider_fn=None,
    bias_fn=None,
    trail_fn=None,
) -> V2WFReport:
    train_days = int(train_days if train_days is not None else getattr(config, "DAYTRADING_V2_WF_TRAIN_DAYS", 21))
    test_days = int(test_days if test_days is not None else getattr(config, "DAYTRADING_V2_WF_TEST_DAYS", 7))
    purge_hours = int(purge_hours if purge_hours is not None else getattr(config, "DAYTRADING_V2_WF_PURGE_HOURS", 48))
    embargo_hours = int(embargo_hours if embargo_hours is not None else getattr(config, "DAYTRADING_V2_WF_EMBARGO_HOURS", 48))
    train_bars = train_days * 24
    test_bars = test_days * 24
    purge_bars = max(1, purge_hours)
    embargo_bars = max(0, embargo_hours)

    report = V2WFReport(config={
        "engine": "daytrading_v2",
        "frozen_config": True,
        "train_days": train_days,
        "test_days": test_days,
        "purge_hours": purge_hours,
        "embargo_hours": embargo_hours,
        "drive_tf": "1h",
        "fee_round_trip": 0.0012,
        "slip_round_trip": 0.0006,
        "symbols": sorted(bundles),
    })
    report.notes.append("NO_FIT: parametry V2 nie są strojone na TRAIN")

    drive = []
    for b in bundles.values():
        ts = _drive_1h_ts(b)
        if len(ts) > len(drive):
            drive = ts
    if not drive:
        report.notes.append("brak drive 1H")
        return report

    splits = rolling_purged_folds(len(drive), train_bars, test_bars, purge_bars, embargo_bars)
    if len(splits) < 1:
        report.notes.append(
            f"0 foldów — 1H bars={len(drive)} need>={train_bars + purge_bars + test_bars} "
            f"(~{train_days}+{purge_hours/24:.0f}+{test_days} dni)"
        )
        return report
    if len(splits) < 3:
        report.notes.append(f"tylko {len(splits)} fold — 90D+ daje sensowny WF")

    for i, (train, test) in enumerate(splits, 1):
        te0, te1 = int(drive[test.start]), int(drive[test.stop - 1])
        tr0, tr1 = int(drive[train.start]), int(drive[train.stop - 1])
        fold = V2Fold(i, tr0, tr1, te0, te1)
        rows_all: List[dict] = []
        for symbol, bundle in bundles.items():
            rows, _ = _run_symbol_fold(
                symbol, bundle, te0, te1,
                replay_fn=replay_fn, provider_fn=provider_fn,
                bias_fn=bias_fn, trail_fn=trail_fn,
            )
            fold.by_symbol[symbol] = fold_metrics([r["realised_r"] for r in rows])
            rows_all.extend(rows)
        fold.metrics = fold_metrics([r["realised_r"] for r in rows_all])
        fold.n_trades = int(fold.metrics["n"])
        report.oos_trades.extend(rows_all)
        report.folds.append(fold)
        m = fold.metrics
        print(
            f"[WF-V2] fold {i}/{len(splits)} n={m['n']} "
            f"avgR={m['avg_r']:+.3f} WR={100*m['win_rate']:.0f}% "
            f"PF={m['profit_factor']:.2f} DD={m['max_dd_r']:.2f}R "
            f"{'NEG' if m['negative'] else 'pos'}",
            flush=True,
        )

    oos = fold_metrics([t["realised_r"] for t in report.oos_trades])
    neg = sum(1 for f in report.folds if f.metrics.get("negative"))
    oos["folds"] = len(report.folds)
    oos["folds_negative"] = neg
    oos["folds_negative_pct"] = (neg / len(report.folds)) if report.folds else 0.0
    by_p: Dict[str, List[float]] = {}
    for t in report.oos_trades:
        p = str(t.get("v2_profile") or "alt")
        by_p.setdefault(p, []).append(float(t["realised_r"]))
    oos["by_profile"] = {k: fold_metrics(v) for k, v in by_p.items()}
    for field, target in (("direction", "by_direction"), ("market_regime", "by_regime")):
        buckets: Dict[str, List[float]] = {}
        for trade in report.oos_trades:
            buckets.setdefault(str(trade.get(field) or "unknown"), []).append(float(trade["realised_r"]))
        oos[target] = {key: fold_metrics(values) for key, values in buckets.items()}
    report.oos_metrics = oos
    return report


def print_v2_wf(report: V2WFReport) -> None:
    print("\n========== V2 WALK-FORWARD (OOS / frozen config) ==========")
    print(f"Config: {report.config}")
    for n in report.notes:
        print(f"  note: {n}")
    print("--- per fold TEST ---")
    print(f"{'fold':<6}{'n':>5}{'avgR':>8}{'WR':>7}{'PF':>7}{'DD_R':>8}  sign")
    for f in report.folds:
        m = f.metrics
        print(
            f"{f.fold:<6}{m['n']:>5}{m['avg_r']:>+8.3f}"
            f"{100*m['win_rate']:>6.0f}%{m['profit_factor']:>7.2f}"
            f"{m['max_dd_r']:>8.2f}  {'NEG' if m['negative'] else 'ok'}"
        )
    m = report.oos_metrics
    print("--- OOS aggregate ---")
    print(
        f"  folds={m.get('folds')} negative={m.get('folds_negative')} "
        f"({100*(m.get('folds_negative_pct') or 0):.0f}%)"
    )
    print(
        f"  n={m.get('n')} avgR={m.get('avg_r'):+.3f} WR={100*(m.get('win_rate') or 0):.0f}% "
        f"PF={m.get('profit_factor'):.2f} DD={m.get('max_dd_r'):.2f}R net={m.get('net_r'):+.2f}R"
    )
    bp = m.get("by_profile") or {}
    if bp:
        print("--- per profil ---")
        print(f"{'profil':<8}{'n':>5}{'avgR':>8}{'WR':>7}{'PF':>7}{'netR':>8}")
        for name in ("major", "alt", "metal"):
            if name not in bp:
                continue
            pm = bp[name]
            print(
                f"{name:<8}{pm['n']:>5}{pm['avg_r']:>+8.3f}"
                f"{100*pm['win_rate']:>6.0f}%{pm['profit_factor']:>7.2f}"
                f"{pm['net_r']:>+8.2f}"
            )
    if (m.get("folds") or 0) >= 3 and (m.get("folds_negative") or 0) >= max(2, int((m.get("folds") or 1) * 0.35)):
        print("  werdykt: 30D suma NIE jest dowodem — za dużo foldów na minusie")
    elif (m.get("n") or 0) < 20:
        print("  werdykt: za mało trade'ów OOS — więcej par albo dłuższe okno")
    else:
        print("  werdykt: OOS trzyma znak; nadal nie gridsearch")
    _print_mae_mfe(report.oos_trades)


def _print_mae_mfe(trades: List[dict]) -> None:
    if not trades:
        return
    print("--- MAE / MFE (wejście, nie filtr) ---")
    n = len(trades)
    unclog = [t for t in trades if t.get("exit_reason") == "time_stop"]
    def _avg(xs, k):
        vals = [float(t.get(k) or 0) for t in xs]
        return sum(vals) / len(vals) if vals else 0.0
    print(
        f"  all n={n} avgMAE={_avg(trades,'mae_r'):.2f}R avgMFE={_avg(trades,'mfe_r'):.2f}R "
        f"limit={sum(1 for t in trades if t.get('fill_kind')=='limit')}/{n}"
    )
    if unclog:
        dead = sum(1 for t in unclog if float(t.get("mfe_r") or 0) < 0.05)
        late = sum(1 for t in unclog if float(t.get("mfe_r") or 0) >= 0.5)
        heat = sum(1 for t in unclog if float(t.get("mae_r") or 0) >= 0.8)
        print(
            f"  unclog n={len(unclog)} avgMAE={_avg(unclog,'mae_r'):.2f}R avgMFE={_avg(unclog,'mfe_r'):.2f}R "
            f"MFE≈0 {100*dead/len(unclog):.0f}%  MFE≥0.5R {100*late/len(unclog):.0f}%  MAE≥0.8R {100*heat/len(unclog):.0f}%"
        )
        print("  MFE≈0 = martwy setup (limit nie pomoże); MFE≥0.5 = fill/exit; MAE≥0.8 = od razu przeciw")


def save_v2_wf(report: V2WFReport, path: str | None = None) -> str:
    LOGS.mkdir(parents=True, exist_ok=True)
    path = path or str(LOGS / "walk_forward_v2_last.json")
    payload = {
        "config": report.config,
        "notes": report.notes,
        "oos_metrics": report.oos_metrics,
        "folds": [
            {
                "fold": f.fold,
                "train_start_ts": f.train_start_ts,
                "train_end_ts": f.train_end_ts,
                "test_start_ts": f.test_start_ts,
                "test_end_ts": f.test_end_ts,
                "n_trades": f.n_trades,
                "metrics": f.metrics,
                "by_symbol": f.by_symbol,
            }
            for f in report.folds
        ],
        "oos_trades": report.oos_trades,
    }
    Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def run_cli(argv: Optional[List[str]] = None) -> V2WFReport:
    ap = argparse.ArgumentParser(description="CryptoEdge V2 purged walk-forward")
    ap.add_argument("--symbols", default="BTC,ETH,SOL,DOGE,PEPE,XAU")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--train-days", type=int, default=None)
    ap.add_argument("--test-days", type=int, default=None)
    ap.add_argument("--force-download", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    from blofin_feed import BlofinFeed
    from historical_replay import download_bundle

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    feed = BlofinFeed()
    bundles: Dict[str, dict] = {}
    for sym in symbols:
        print(f"[WF-V2] bundle {sym} {args.days}D...", flush=True)
        payload = download_bundle(feed, sym, args.days, force=args.force_download)
        b = dict(payload.get("bundle") or {})
        b["funding"] = payload.get("funding") or []
        bundles[sym] = b

    t0 = time.time()
    report = run_walk_forward_v2(
        bundles,
        train_days=args.train_days,
        test_days=args.test_days,
    )
    print_v2_wf(report)
    out = save_v2_wf(report, args.out)
    print(f"[WF-V2] {time.time()-t0:.0f}s → {out}")
    return report


if __name__ == "__main__":
    run_cli()
