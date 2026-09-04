"""Read-only, dependency-free projections of CryptoEdge runtime state for UI.

The functions in this module intentionally never call trading/risk/exchange
methods. They tolerate partial dicts, dataclasses and legacy object models.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Iterable


def _get(value: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(value, dict) and name in value:
            return value[name]
        if value is not None and hasattr(value, name):
            return getattr(value, name)
    return default


def _items(value: Any) -> list[Any]:
    if value is None or isinstance(value, (str, bytes, dict)):
        return []
    try:
        return list(value)
    except TypeError:
        return []


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if number == number and abs(number) != float("inf") else default
    except (TypeError, ValueError, OverflowError):
        return default


def _text(value: Any, default: str = "") -> str:
    return str(value).strip() if value not in (None, "") else default


def _timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return _text(value)


def _trader(runtime: Any) -> Any:
    return _get(runtime, "trader")


def history_projection(runtime: Any, limit: int = 200) -> list[dict[str, Any]]:
    rows = _items(_get(_trader(runtime), "closed_positions", default=[]))[-max(0, int(limit)):]
    return [{
        "time": _timestamp(_get(row, "exit_time", "time")),
        "symbol": _text(_get(row, "symbol")).upper(),
        "side": _text(_get(row, "direction", "side")).upper(),
        "entry": _number(_get(row, "entry_price", "entry")),
        "exit": _number(_get(row, "exit_price", "exit")),
        "pnl": _number(_get(row, "pnl")),
        "pnl_pct": _number(_get(row, "pnl_pct")),
        "engine": _text(_get(row, "engine"), "unknown"),
        "reason": _text(_get(row, "exit_reason", "reason"), "unknown"),
    } for row in reversed(rows)]


def equity_drawdown_projection(runtime: Any, limit: int = 500) -> dict[str, Any]:
    rows = list(reversed(history_projection(runtime, limit)))
    risk = _get(runtime, "risk")
    starting = _number(_get(risk, "starting_capital", "initial_capital", "current_capital"))
    equity, peak, max_drawdown = starting, starting, 0.0
    points = []
    for index, row in enumerate(rows):
        equity += row["pnl"]
        peak = max(peak, equity)
        drawdown = max(0.0, peak - equity)
        max_drawdown = max(max_drawdown, drawdown)
        points.append({"index": index, "time": row["time"], "equity": equity, "drawdown": drawdown})
    return {
        "starting_equity": starting,
        "current_equity": equity,
        "peak_equity": peak,
        "max_drawdown": max_drawdown,
        "max_drawdown_pct": (max_drawdown / peak * 100.0) if peak > 0 else 0.0,
        "points": points,
    }


def exposure_projection(runtime: Any) -> dict[str, Any]:
    positions = _items(_get(_trader(runtime), "positions", default=[]))
    by_symbol: dict[str, float] = {}
    long_notional = short_notional = 0.0
    for row in positions:
        symbol = _text(_get(row, "symbol"), "UNKNOWN").upper()
        side = _text(_get(row, "direction", "side")).upper()
        notional = abs(_number(_get(row, "notional", "position_value")))
        if notional == 0:
            notional = abs(_number(_get(row, "size", "quantity")) * _number(_get(row, "mark_price", "mark", "entry_price", "entry")))
        signed = -notional if side in {"SHORT", "SELL"} else notional
        by_symbol[symbol] = by_symbol.get(symbol, 0.0) + signed
        if signed < 0:
            short_notional += notional
        else:
            long_notional += notional
    gross = long_notional + short_notional
    return {"gross": gross, "net": long_notional - short_notional, "long": long_notional,
            "short": short_notional, "positions": len(positions), "by_symbol": by_symbol}


def reconciliation_projection(runtime: Any) -> dict[str, Any]:
    candidates = (
        _get(runtime, "reconciliation_state", "last_reconciliation")
        or _get(_trader(runtime), "reconciliation_state", "last_reconciliation")
        or {}
    )
    mismatches = _items(_get(candidates, "mismatches", "issues", default=[]))
    status = _text(_get(candidates, "status", "state"), "unknown").lower()
    if mismatches and status in {"", "unknown", "ok", "healthy"}:
        status = "mismatch"
    return {
        "status": status,
        "checked_at": _timestamp(_get(candidates, "checked_at", "timestamp", "ts")),
        "mismatch_count": len(mismatches),
        "mismatches": [{"symbol": _text(_get(row, "symbol"), "UNKNOWN").upper(),
                         "kind": _text(_get(row, "kind", "type"), "unknown"),
                         "detail": _text(_get(row, "detail", "message"))} for row in mismatches],
    }


def signal_telemetry_projection(runtime: Any, limit: int = 200) -> dict[str, Any]:
    logger = _get(runtime, "logger")
    state = _get(logger, "last_state", default={}) or _get(runtime, "last_state_snapshot", default={}) or {}
    signals = _items(_get(state, "signals", "analysis_board", "scanner_assets", default=[]))[-max(0, int(limit)):]
    gates = Counter(_text(_get(row, "gate", "reject_reason", "reason"), "unknown") for row in signals)
    engines = Counter(_text(_get(row, "engine", "strategy"), "unknown") for row in signals)
    rows = [{"symbol": _text(_get(row, "symbol", "sym"), "UNKNOWN").upper(),
             "side": _text(_get(row, "side", "direction")).upper(),
             "score": _number(_get(row, "score")),
             "gate": _text(_get(row, "gate", "reject_reason", "reason"), "unknown"),
             "engine": _text(_get(row, "engine", "strategy"), "unknown"),
             "time": _timestamp(_get(row, "time", "timestamp", "ts"))} for row in reversed(signals)]
    return {"total": len(rows), "by_gate": dict(gates), "by_engine": dict(engines), "rows": rows}


def build_ui_read_models(runtime: Any) -> dict[str, Any]:
    """Return all non-trading projections as one immutable-by-convention snapshot."""
    return {"history": history_projection(runtime), "equity": equity_drawdown_projection(runtime),
            "exposure": exposure_projection(runtime), "reconciliation": reconciliation_projection(runtime),
            "signals": signal_telemetry_projection(runtime)}

