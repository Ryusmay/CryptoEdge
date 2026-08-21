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
