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
    # Prawdziwa gielda odkrywa cene sama, ale venue lokalne (PAPER, replay)
    # musi ja dostac. Ustala ja wolajacy przez cryptoedge.portfolio.
    # close_policy - jedyny wlasciciel regul zamkniecia - i przekazuje tu
    # razem z powodem, ktory niesie slad po nieswiezej cenie.
    price: Decimal | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    accepted: bool
    state: str
    client_order_id: str
    exchange_order_id: str | None = None
    raw: Any = None
    reason: str | None = None
    # ILE naprawde sie wypelnilo i po jakiej cenie.
    #
    # `accepted` odpowiada na pytanie "czy venue przyjal komende", a NIE
    # "czy mam pozycje". Zmierzone w exec_gate: zlecenie wypelnione w 0.4
    # z 1.0 i zlecenie, ktore nie wypelnilo sie wcale, dawaly ten sam wynik
    # `accepted=True` bez zadnego sladu po ilosci. Informacja istniala
    # w `raw`, ale nie przechodzila przez granice, wiec wolajacy nie mial
    # jak zaksiegowac tego, co naprawde kupil.
    #
    # `None` znaczy "venue nie podal", a nie "zero" - te dwie rzeczy musza
    # dac sie odroznic, bo z zera wolno ksiegowac brak pozycji, a z braku
    # informacji nie wolno niczego.
    filled_quantity: Decimal | None = None
    average_price: Decimal | None = None


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
