"""Compatibility adapter for current BloFin/replay executors and reconciler."""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any, Sequence

from .ports import (
    CancelOrder, ExecutionResult, ReconciliationResult, ReducePosition, SubmitOrder,
)


class ExecutionDisabled(RuntimeError):
    """Raised when a caller attempts to increase exposure through a closed gate."""


def _value(obj: Any, name: str, default: Any = None) -> Any:
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def _decimal(value: Any) -> Decimal | None:
    """Liczba z legacy obiektu na Decimal. Brak danych zostaje brakiem."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return None


# Stany, w ktorych gielda NIE POWIEDZIALA, co sie stalo. `Order.filled_size`
# stoi wtedy na domyslnym 0.0 - i wlasnie dlatego nie wolno go przepuszczac:
# zero znaczy "nic sie nie wypelnilo", a tu prawda brzmi "nie wiem". Wolajacy,
# ktory przeczyta zero po TIMEOUCIE, uzna, ze pozycji nie ma, i wejdzie drugi
# raz. To ten sam kontrakt, ktory `blofin_executor` trzyma na poziomie stanu
# (TIMEOUT nigdy nie staje sie REJECTED) - tu trzyma go dla ilosci.
_UNKNOWN_FILL_STATES = frozenset({"TIMEOUT", "UNKNOWN"})


def _result(raw: Any, client_order_id: str) -> ExecutionResult:
    state_obj = _value(raw, "state", "UNKNOWN")
    state = str(getattr(state_obj, "value", state_obj))
    accepted = state.upper() in {
        "SUBMITTED", "ACCEPTED", "PARTIAL", "PARTIALLY_FILLED", "FILLED",
    }
    known = state.upper() not in _UNKNOWN_FILL_STATES
    return ExecutionResult(
        accepted=accepted,
        state=state,
        client_order_id=str(_value(raw, "client_order_id", client_order_id)),
        exchange_order_id=_value(raw, "order_id", _value(raw, "exchange_order_id")),
        raw=raw,
        reason=_value(raw, "reject_reason", _value(raw, "last_error")),
        # Legacy `Order` zna wypelnienie - dotad nie przechodzilo przez
        # granice portu i wolajacy nie mial jak odroznic partiala od pelnego.
        filled_quantity=_decimal(_value(raw, "filled_size")) if known else None,
        average_price=_decimal(_value(raw, "avg_fill_price")) if known else None,
    )


class LegacyExecutionAdapter:
    """Expose the new port without changing legacy production classes."""

    def __init__(self, executor: Any, reconciler: Any | None = None, *,
                 enabled: bool = False, live: bool = False) -> None:
        self.executor = executor
        self.reconciler = reconciler
        self.enabled = bool(enabled)
        self.live = bool(live)
        self._orders: dict[str, Any] = {}

    def submit(self, command: SubmitOrder) -> ExecutionResult:
        if not self.enabled:
            raise ExecutionDisabled("execution submit is disabled")
        if hasattr(self.executor, "place_order") and not self.live:
            raise ExecutionDisabled("live venue submit is disabled")
        if hasattr(self.executor, "place_order"):
            raw = self.executor.place_order(
                symbol=command.symbol, side=command.side,
                size_contracts=float(command.quantity), order_type=command.order_type,
                price=(float(command.limit_price) if command.limit_price is not None else None),
                reduce_only=command.reduce_only, leverage=command.leverage,
                direction=command.direction, client_order_id=command.client_order_id,
            )
        elif hasattr(self.executor, "submit"):
            raw = self.executor.submit(
                order_id=command.client_order_id, symbol=command.symbol,
                side=command.side, quantity=float(command.quantity),
                decision_ts_ms=int(command.metadata.get("decision_ts_ms", 0)),
                limit_price=(float(command.limit_price) if command.limit_price is not None else None),
                timeout_ms=command.metadata.get("timeout_ms"),
            )
        else:
            raise TypeError("legacy executor supports neither place_order nor submit")
        self._orders[command.client_order_id] = raw
        return _result(raw, command.client_order_id)

    def cancel(self, command: CancelOrder) -> ExecutionResult:
        raw_order = self._orders.get(command.client_order_id)
        if hasattr(self.executor, "cancel_order"):
            if raw_order is None:
                raise KeyError(f"unknown client_order_id: {command.client_order_id}")
            raw = self.executor.cancel_order(raw_order)
        elif hasattr(self.executor, "request_cancel"):
            requested_at = command.requested_at_ms
            if requested_at is None:
                requested_at = int(time.time() * 1000)
            self.executor.request_cancel(command.client_order_id, requested_at)
            raw = self.executor.orders[command.client_order_id]
        else:
            raise TypeError("legacy executor does not support cancellation")
        return _result(raw, command.client_order_id)

    def reduce(self, command: ReducePosition) -> ExecutionResult:
        if not hasattr(self.executor, "close_market"):
            raise TypeError("legacy executor does not support reduce-only close")
        raw = self.executor.close_market(
            command.symbol, command.direction, float(command.quantity), wait_fill=True,
        )
        return _result(raw, command.client_order_id or _value(raw, "client_order_id", ""))

    def reconcile(self, local_positions: Sequence[Any] = ()) -> ReconciliationResult:
        target = self.reconciler or self.executor
        if not hasattr(target, "reconcile"):
            raise TypeError("no reconciler configured")
        raw = target.reconcile(list(local_positions), executor=self.executor)
        discrepancies = []
        explicit_sync = isinstance(raw, dict) and "in_sync" in raw
        if isinstance(raw, dict):
            for key in ("only_local", "only_exchange", "size_mismatch", "discrepancies"):
                discrepancies.extend(raw.get(key) or [])
        if not explicit_sync:
            discrepancies.append({"reason": "MALFORMED_RECONCILIATION_REPORT"})
        return ReconciliationResult(
            in_sync=bool(raw.get("in_sync")) if explicit_sync else False,
            positions=tuple(_value(raw, "positions", ())),
            orders=tuple(_value(raw, "orders", ())),
            discrepancies=tuple(discrepancies), raw=raw,
        )
