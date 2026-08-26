"""Order lifecycle driven by accepted commands and immutable fills."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from .ledger import Fill, FillAggregate, FillLedger


class OrderStatus(str, Enum):
    CREATED = "CREATED"
    SUBMITTING = "SUBMITTING"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class InvalidTransition(ValueError):
    pass


_ALLOWED: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.CREATED: frozenset({OrderStatus.SUBMITTING, OrderStatus.REJECTED}),
    OrderStatus.SUBMITTING: frozenset({OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.UNKNOWN}),
    OrderStatus.ACCEPTED: frozenset({OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCEL_PENDING, OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.UNKNOWN}),
    OrderStatus.PARTIALLY_FILLED: frozenset({OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCEL_PENDING, OrderStatus.CANCELED, OrderStatus.UNKNOWN}),
    OrderStatus.CANCEL_PENDING: frozenset({OrderStatus.CANCELED, OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.UNKNOWN}),
    OrderStatus.UNKNOWN: frozenset({OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED}),
    OrderStatus.FILLED: frozenset(),
    OrderStatus.CANCELED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
}


@dataclass(slots=True)
class OrderLifecycle:
    client_order_id: str
    requested_quantity: Decimal
    ledger: FillLedger = field(default_factory=FillLedger)
    status: OrderStatus = OrderStatus.CREATED
    exchange_order_id: str | None = None
    history: list[tuple[OrderStatus, str | None]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.requested_quantity = Decimal(str(self.requested_quantity))
        if not self.client_order_id or self.requested_quantity <= 0:
            raise ValueError("client_order_id and positive requested_quantity are required")

    def transition(self, status: OrderStatus, reason: str | None = None) -> None:
        status = OrderStatus(status)
        if status == self.status:
            return
        if status not in _ALLOWED[self.status]:
            raise InvalidTransition(f"{self.status.value} -> {status.value}")
        self.status = status
        self.history.append((status, reason))

    def apply_fill(self, fill: Fill) -> bool:
        if fill.client_order_id != self.client_order_id:
            raise ValueError("fill belongs to another order")
        previous = self.ledger.fills_for(self.client_order_id)
        if any(item.trade_id == fill.trade_id for item in previous):
            return self.ledger.record(fill)
        if fill.quantity > self.remaining_quantity:
            raise ValueError("fill quantity exceeds remaining order quantity")
        inserted = self.ledger.record(fill)
        if not inserted:
            return False
        aggregate = self.fill_summary
        target = (OrderStatus.FILLED if aggregate.quantity >= self.requested_quantity
                  else OrderStatus.PARTIALLY_FILLED)
        self.transition(target, f"trade_id={fill.trade_id}")
        return True

    @property
    def fill_summary(self) -> FillAggregate:
        return self.ledger.aggregate(self.client_order_id)

    @property
    def remaining_quantity(self) -> Decimal:
        return max(Decimal("0"), self.requested_quantity - self.fill_summary.quantity)

    @property
    def is_terminal(self) -> bool:
        return self.status in {OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED}
