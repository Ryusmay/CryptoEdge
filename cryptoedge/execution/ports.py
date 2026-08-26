"""Typed boundaries between trading services and execution venues."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True, slots=True)
class SubmitOrder:
    client_order_id: str
    symbol: str
    side: str
    quantity: Decimal
    order_type: str = "market"
    limit_price: Decimal | None = None
    reduce_only: bool = False
    direction: str | None = None
    leverage: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CancelOrder:
    client_order_id: str
    symbol: str
    exchange_order_id: str | None = None
    requested_at_ms: int | None = None


@dataclass(frozen=True, slots=True)
class ReducePosition:
    symbol: str
    direction: str
    quantity: Decimal
    client_order_id: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    accepted: bool
    state: str
    client_order_id: str
    exchange_order_id: str | None = None
    raw: Any = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    in_sync: bool
    positions: Sequence[Any] = ()
    orders: Sequence[Any] = ()
    discrepancies: Sequence[Any] = ()
    raw: Any = None


@runtime_checkable
class ExecutionPort(Protocol):
    """Small, venue-independent command surface used by runtime and replay."""

    def submit(self, command: SubmitOrder) -> ExecutionResult: ...

    def cancel(self, command: CancelOrder) -> ExecutionResult: ...

    def reduce(self, command: ReducePosition) -> ExecutionResult: ...

    def reconcile(self, local_positions: Sequence[Any] = ()) -> ReconciliationResult: ...
