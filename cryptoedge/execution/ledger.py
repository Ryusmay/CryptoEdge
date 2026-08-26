"""Immutable fill facts with idempotent ingestion and order-level aggregation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
import os
from pathlib import Path
from threading import RLock
from typing import Iterable
import uuid


def _decimal(value: Decimal | int | float | str) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


@dataclass(frozen=True, slots=True)
class Fill:
    trade_id: str
    client_order_id: str
    quantity: Decimal
    price: Decimal
    fee: Decimal = Decimal("0")
    liquidity_role: str | None = None
    timestamp_ms: int | None = None
    exchange_order_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "trade_id", str(self.trade_id).strip())
        object.__setattr__(self, "client_order_id", str(self.client_order_id).strip())
        object.__setattr__(self, "quantity", _decimal(self.quantity))
        object.__setattr__(self, "price", _decimal(self.price))
        object.__setattr__(self, "fee", _decimal(self.fee))
        if not self.trade_id or not self.client_order_id:
            raise ValueError("trade_id and client_order_id are required")
        if self.quantity <= 0 or self.price <= 0:
            raise ValueError("fill quantity and price must be positive")


@dataclass(frozen=True, slots=True)
class FillAggregate:
    client_order_id: str
    quantity: Decimal
    notional: Decimal
    vwap: Decimal | None
    fee: Decimal
    fill_count: int
    liquidity_role: str | None


class FillLedger:
    """Thread-safe fill ledger; re-delivered venue events are harmless."""

    def __init__(self) -> None:
        self._by_trade_id: dict[str, Fill] = {}
        self._trade_ids_by_order: dict[str, list[str]] = {}
        self._lock = RLock()

    def record(self, fill: Fill) -> bool:
        """Return True only when a new trade fact was inserted.

        A repeated trade_id with different economics is rejected: accepting it
        would silently corrupt position accounting.
        """
        with self._lock:
            previous = self._by_trade_id.get(fill.trade_id)
            if previous is not None:
                if previous != fill:
                    raise ValueError(f"conflicting duplicate trade_id: {fill.trade_id}")
                return False
            self._by_trade_id[fill.trade_id] = fill
            self._trade_ids_by_order.setdefault(fill.client_order_id, []).append(fill.trade_id)
            return True

    def extend(self, fills: Iterable[Fill]) -> int:
        return sum(1 for fill in fills if self.record(fill))

    def fills_for(self, client_order_id: str) -> tuple[Fill, ...]:
        with self._lock:
            ids = tuple(self._trade_ids_by_order.get(str(client_order_id), ()))
            return tuple(self._by_trade_id[trade_id] for trade_id in ids)

    def aggregate(self, client_order_id: str) -> FillAggregate:
        fills = self.fills_for(client_order_id)
        quantity = sum((fill.quantity for fill in fills), Decimal("0"))
        notional = sum((fill.quantity * fill.price for fill in fills), Decimal("0"))
        fee = sum((fill.fee for fill in fills), Decimal("0"))
        roles = {fill.liquidity_role for fill in fills if fill.liquidity_role}
        role = next(iter(roles)) if len(roles) == 1 else ("mixed" if roles else None)
        return FillAggregate(
            client_order_id=str(client_order_id), quantity=quantity,
            notional=notional, vwap=(notional / quantity if quantity else None),
            fee=fee, fill_count=len(fills), liquidity_role=role,
        )

    def dump(self) -> dict:
        """Return a JSON-safe snapshot without losing decimal precision."""
        with self._lock:
            fills = tuple(self._by_trade_id.values())
        return {"schema_version": 1, "fills": [{
            "trade_id": fill.trade_id,
            "client_order_id": fill.client_order_id,
            "quantity": str(fill.quantity), "price": str(fill.price),
            "fee": str(fill.fee), "liquidity_role": fill.liquidity_role,
            "timestamp_ms": fill.timestamp_ms,
            "exchange_order_id": fill.exchange_order_id,
        } for fill in fills]}

    @classmethod
    def load(cls, payload: dict) -> "FillLedger":
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("unsupported fill ledger payload")
        rows = payload.get("fills")
        if not isinstance(rows, list):
            raise ValueError("fill ledger rows must be a list")
        ledger = cls()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("fill ledger row must be an object")
            ledger.record(Fill(**row))
        return ledger

    def save_json(self, path: str | Path) -> None:
        """Atomically persist the ledger in the destination directory."""
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(
            f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(self.dump(), handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    @classmethod
    def load_json(cls, path: str | Path) -> "FillLedger":
        with Path(path).open("r", encoding="utf-8") as handle:
            return cls.load(json.load(handle))

    def __len__(self) -> int:
        with self._lock:
            return len(self._by_trade_id)
