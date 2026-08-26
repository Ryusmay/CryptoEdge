"""Explicit conversion between domain fill facts and execution accounting."""

from __future__ import annotations

from typing import Any, Mapping

from .ledger import Fill


def domain_fill_to_ledger(value: Any) -> Fill:
    metadata = getattr(value, "metadata", None) or {}
    if not isinstance(metadata, Mapping):
        metadata = {}
    trade_id = (metadata.get("trade_id") or metadata.get("exchange_trade_id")
                or metadata.get("exec_id") or getattr(value, "fill_id", None))
    if not trade_id:
        raise ValueError("domain fill has no stable trade identity")
    role = getattr(value, "liquidity_role", None)
    role = getattr(role, "value", role)
    return Fill(
        trade_id=str(trade_id), client_order_id=str(getattr(value, "client_order_id", "")),
        exchange_order_id=str(getattr(value, "order_id", "") or "") or None,
        quantity=getattr(value, "quantity"), price=getattr(value, "price"),
        fee=getattr(value, "fee", 0), liquidity_role=str(role) if role else None,
        timestamp_ms=getattr(value, "ts_ms", None),
    )
