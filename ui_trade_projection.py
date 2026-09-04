"""Read-only projection of legacy/domain trade data for the chart UI.

This module deliberately has no dependency on execution, risk, or runtime state.
Malformed and incomplete values are omitted instead of being inferred.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import math
import re
from typing import Any, Iterable, Mapping


_QUOTES = ("USDT", "USDC", "USD")


def normalize_symbol(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    text = re.sub(r"[^A-Z0-9]", "", text)
    for quote in _QUOTES:
        if text.endswith(quote) and len(text) > len(quote):
            text = text[: -len(quote)]
            break
    return text or None


def _get(item: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(item, Mapping) and name in item:
            return item[name]
        if hasattr(item, name):
            return getattr(item, name)
    return default


def _price(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def _timestamp(value: Any) -> int | None:
    if isinstance(value, datetime):
        current = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        result = current.timestamp()
    elif isinstance(value, str):
        try:
            current = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            current = current if current.tzinfo else current.replace(tzinfo=timezone.utc)
            result = current.timestamp()
        except (TypeError, ValueError, OverflowError):
            return None
    else:
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if result > 10_000_000_000:  # domain snapshots use milliseconds
            result /= 1000.0
    return int(result) if math.isfinite(result) and result > 0 else None


def _stable_id(prefix: str, symbol: str, source_id: Any, *identity: Any) -> str:
    source = str(source_id or "").strip()
    if not source:
        raw = "|".join(str(value) for value in (symbol, *identity))
        source = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    source = re.sub(r"[^A-Za-z0-9_.:-]", "-", source)[:64]
    return f"{prefix}:{symbol}:{source}"


def _source_id(item: Any) -> Any:
    return _get(item, "position_id", "candidate_id", "event_id", "fill_id", "order_id", "id")


def _targets(item: Any) -> list[float]:
    raw = _get(item, "target_prices")
    values: list[Any] = list(raw) if isinstance(raw, (list, tuple)) else []
    values.extend(_get(item, name) for name in ("tp1_price", "tp2_price", "tp_price", "tp"))
    result: list[float] = []
    for value in values:
        valid = _price(value)
        if valid is not None and valid not in result:
            result.append(valid)
    return result


def _add_levels(output: dict[str, dict[str, list[dict[str, Any]]]], item: Any) -> None:
    symbol = normalize_symbol(_get(item, "symbol", "sym", "inst_id", "instrument"))
    if not symbol:
        return
    source = _source_id(item)
    levels = output.setdefault(symbol, {"levels": [], "markers": []})["levels"]
    candidates = [
        ("entry", "ENTRY", _price(_get(item, "entry_price", "fill_price", "price", "entry"))),
        ("stop", "SL", _price(_get(item, "stop_price", "sl_price", "sl"))),
    ]
    candidates.extend(("target", f"TP{index}", price) for index, price in enumerate(_targets(item), 1))
    for kind, label, price in candidates:
        if price is None:
            continue
        level_id = _stable_id(f"level-{kind}", symbol, source, kind, label, price)
        levels.append({"id": level_id, "kind": kind, "price": price, "label": label})


def _add_marker(
    output: dict[str, dict[str, list[dict[str, Any]]]], item: Any, kind: str,
    time_names: tuple[str, ...], price_names: tuple[str, ...], *, label: str,
) -> None:
    symbol = normalize_symbol(_get(item, "symbol", "sym", "inst_id", "instrument"))
    when = _timestamp(_get(item, *time_names))
    price = _price(_get(item, *price_names))
    if not symbol or when is None or price is None:
        return
    side_value = str(_get(item, "direction", "side", default="") or "").lower()
    side = "long" if side_value in ("long", "buy") else "short" if side_value in ("short", "sell") else None
    marker = {
        "id": _stable_id(f"marker-{kind}", symbol, _source_id(item), kind, when, price),
        "time": when, "kind": kind, "price": price, "label": label,
    }
    if side:
        marker["side"] = side
    output.setdefault(symbol, {"levels": [], "markers": []})["markers"].append(marker)


def _event_kind(item: Any) -> str | None:
    value = str(_get(item, "kind", "tag", "type", "event_type", "action", default="") or "").upper()
    if "FILL" in value:
        return "fill"
    if any(token in value for token in ("CLOSE", "CLOSED", "EXIT")):
        return "exit"
    if any(token in value for token in ("OPEN", "ENTRY", "ENTER")):
        return "entry"
    return None


def project_ui_trades(
    *, candidates: Iterable[Any] = (), positions: Iterable[Any] = (),
    closed: Iterable[Any] = (), events: Iterable[Any] = (),
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Return ``{SYMBOL: {levels, markers}}`` suitable for the frontend market model."""
    output: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for item in (*tuple(candidates or ()), *tuple(positions or ()), *tuple(closed or ())):
        _add_levels(output, item)
    for item in positions or ():
        _add_marker(output, item, "entry", ("entry_time", "opened_at", "opened_ts_ms", "entry_ts_ms"),
                    ("entry_price", "fill_price", "price"), label="ENTRY")
        _add_marker(output, item, "fill", ("fill_time", "filled_at", "filled_ts_ms"),
                    ("fill_price", "entry_price", "price"), label="FILL")
    for item in closed or ():
        _add_marker(output, item, "entry", ("entry_time", "opened_at", "opened_ts_ms", "entry_ts_ms"),
                    ("entry_price", "fill_price", "price"), label="ENTRY")
        _add_marker(output, item, "exit", ("exit_time", "closed_at", "closed_ts_ms", "exit_ts_ms"),
                    ("exit_price", "close_price", "exit"), label="EXIT")
    for item in events or ():
        kind = _event_kind(item)
        if kind:
            _add_marker(output, item, kind, ("time", "timestamp", "ts", "ts_ms", "created_at"),
                        ("price", "fill_price", "entry_price", "exit_price"), label=kind.upper())

    for bucket in output.values():
        # Several read models can describe the same live trade.  Collapse
        # identical visual levels so the chart does not draw them twice.
        unique_levels = {(row["kind"], row["price"]): row for row in bucket["levels"]}
        order = {"entry": 0, "stop": 1, "target": 2}
        bucket["levels"] = sorted(
            unique_levels.values(), key=lambda row: (order.get(row["kind"], 9), row["price"])
        )
        bucket["markers"] = sorted({row["id"]: row for row in bucket["markers"]}.values(), key=lambda row: (row["time"], row["id"]))
    return dict(sorted(output.items()))


__all__ = ["normalize_symbol", "project_ui_trades"]
