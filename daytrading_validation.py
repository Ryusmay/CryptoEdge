"""Leakage-resistant validation helpers for CryptoEdge daytrading."""

from __future__ import annotations

from typing import List, Sequence, Tuple


def purged_walk_forward_splits(
    n: int, train: int, test: int, purge: int = 12, embargo: int = 12
) -> List[Tuple[range, range]]:
    """Expanding walk-forward splits with purge before and embargo after test."""
    out, train_end = [], int(train)
    while train_end + purge + test <= n:
        test_start = train_end + purge
        test_end = test_start + test
        out.append((range(0, train_end), range(test_start, test_end)))
        train_end = test_end + embargo
    return out


def rolling_purged_folds(
    n: int,
    train: int,
    test: int,
    purge: int = 48,
    embargo: int = 48,
    step: int | None = None,
) -> List[Tuple[range, range]]:
    """Rolling walk-forward: stałe okno train, test po purge, embargo przed następnym train.

    Zwraca (train_range, test_range). Testy nie nachodzą. Train może nachodzić
    na poprzedni test — OK przy frozen config (brak fitu).
    """
    step = int(test if step is None else step)
    train, test, purge, embargo = int(train), int(test), max(0, int(purge)), max(0, int(embargo))
    out: List[Tuple[range, range]] = []
    start = 0
    while start + train + purge + test <= n:
        tr_b = start + train
        te_a = tr_b + purge
        te_b = te_a + test
        out.append((range(start, tr_b), range(te_a, te_b)))
        start += step + embargo
        if len(out) > 40:
            break
    return out


def assert_prefix_invariant(full: Sequence, evaluator, cut_points: Sequence[int]) -> None:
    """Fail if evaluating a prefix differs after unseen future data is appended.

    ``evaluator(data, asof)`` must enforce its as-of boundary. The comparison
    detects accidental use of the final/full array inside feature generation.
    """
    for cut in cut_points:
        left = evaluator(full[:cut], cut - 1)
        right = evaluator(full, cut - 1)
        if left != right:
            raise AssertionError(f"look-ahead detected at cut={cut}: {left!r} != {right!r}")


def recursive_stability(values: Sequence[float], evaluator, windows=(140, 280, 560), tolerance=1e-8) -> dict:
    """Compare final recursive indicator values across longer warm-up windows."""
    outputs = []
    for window in windows:
        if len(values) >= window:
            outputs.append((window, float(evaluator(values[-window:]))))
    if len(outputs) < 2:
        return {"stable": None, "outputs": outputs, "reason": "insufficient_history"}
    spread = max(v for _, v in outputs) - min(v for _, v in outputs)
    return {"stable": abs(spread) <= tolerance, "spread": spread, "outputs": outputs}


def run_production_prefix_audit(symbol: str, bundle: dict, cut_points: Sequence[int]) -> dict:
    """Execute the real DayTradingEngine twice at every as-of cut."""
    from daytrading_backtester import production_signal_provider
    full_provider = production_signal_provider(symbol, bundle)
    checked = 0
    for cut in cut_points:
        prefix = {}
        for tf, data in bundle.items():
            drive_ts = (bundle.get("5m") or {}).get("timestamps") or (bundle.get("5m") or {}).get("ts") or []
            if cut >= len(drive_ts):
                continue
            boundary = int(drive_ts[cut])
            ts = list(data.get("timestamps") or data.get("ts") or [])
            ids = [i for i, value in enumerate(ts) if int(value) <= boundary]
            prefix[tf] = {key: [val[i] for i in ids if i < len(val)]
                          for key, val in data.items() if isinstance(val, list)}
        prefix_provider = production_signal_provider(symbol, prefix)
        right = full_provider(cut)
        prefix_n = len((prefix.get("5m") or {}).get("closes") or [])
        left = prefix_provider(prefix_n - 1)
        keys = ("direction", "setup", "reject_reason", "sl_price", "tp1_price", "tp2_price")
        if any(left.get(k) != right.get(k) for k in keys):
            raise AssertionError(f"production look-ahead mismatch at 5m index {cut}")
        checked += 1
    return {"passed": True, "checked_cut_points": checked}


def run_production_prefix_audit_v2(symbol: str, bundle: dict, cut_points: Sequence[int]) -> dict:
    """As-of audit silnika V2: prefix vs pełna historia w tym samym cut."""
    from daytrading_backtester import production_signal_provider_v2
    drive_ts = list((bundle.get("5m") or {}).get("timestamps") or (bundle.get("5m") or {}).get("ts") or [])
    full_at, _ = production_signal_provider_v2(symbol, bundle)
    checked = 0
    keys = ("direction", "reject_reason", "sl_price", "tp1_price", "v2_profile")
    for cut in cut_points:
        if cut < 0 or cut >= len(drive_ts):
            continue
        boundary = int(drive_ts[cut])
        prefix = {}
        for tf, data in bundle.items():
            if tf == "funding" and isinstance(data, list):
                prefix[tf] = [row for row in data if int((row or {}).get("ts_ms") or 0) <= boundary]
                continue
            if not isinstance(data, dict):
                prefix[tf] = data
                continue
            ts = list(data.get("timestamps") or data.get("ts") or [])
            ids = [i for i, value in enumerate(ts) if int(value) <= boundary]
            prefix[tf] = {
                key: ([val[i] for i in ids if i < len(val)] if isinstance(val, list) else val)
                for key, val in data.items()
            }
        pref_at, _ = production_signal_provider_v2(symbol, prefix)
        prefix_n = len((prefix.get("5m") or {}).get("closes") or [])
        if prefix_n <= 0:
            continue
        left = pref_at(prefix_n - 1)
        right = full_at(cut)
        if any(left.get(k) != right.get(k) for k in keys):
            raise AssertionError(
                f"V2 look-ahead at 5m index {cut}: prefix={ {k: left.get(k) for k in keys} } "
                f"full={ {k: right.get(k) for k in keys} }"
            )
        checked += 1
    return {"passed": True, "checked_cut_points": checked}


def run_prefix_v2_cli(symbol: str = "BTC", days: int = 30, cuts: int = 5, force: bool = False) -> dict:
    """Freqtrade-style lookahead: prefix vs full na prawdziwym bundlu BloFin."""
    from blofin_feed import BlofinFeed
    from historical_replay import download_bundle

    symbol = str(symbol or "BTC").upper()
    days = max(7, int(days))
    print(f"[prefix-v2] download {symbol} {days}D...", flush=True)
    payload = download_bundle(BlofinFeed(), symbol, days, force=force)
    bundle = dict(payload.get("bundle") or {})
    bundle["funding"] = payload.get("funding") or []
    n = len((bundle.get("5m") or {}).get("timestamps") or [])
    if n < 80:
        raise RuntimeError(f"za mało 5m ({n}) — pobierz dłuższe okno")
    start = max(40, n // 5)
    step = max(12, (n - start) // max(1, int(cuts) + 1))
    points = list(range(start, n - 8, step))[: max(1, int(cuts))]
    print(f"[prefix-v2] {symbol} 5m={n} cuts={points}", flush=True)
    out = run_production_prefix_audit_v2(symbol, bundle, points)
    print(f"[prefix-v2] OK checked={out['checked_cut_points']}", flush=True)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    import argparse
    import sys
    argv = list(argv if argv is not None else sys.argv[1:])
    ap = argparse.ArgumentParser(prog="daytrading_validation")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prefix-v2", help="lookahead audit V2 (prefix vs full)")
    p.add_argument("symbol", nargs="?", default="BTC")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--cuts", type=int, default=5)
    p.add_argument("--force-download", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "prefix-v2":
        run_prefix_v2_cli(args.symbol, args.days, args.cuts, force=args.force_download)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

