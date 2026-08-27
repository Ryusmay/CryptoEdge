"""ExecutionPort nad lokalna ksiega PAPER.

Zaden request nie opuszcza procesu - "gielda" to PaperTrader. Adapter nie
zmienia PaperTradera i nie podejmuje za niego zadnej decyzji handlowej;
tlumaczy wylacznie komendy portu na jego publiczne API.

Sa trzy miejsca, w ktorych kontrakt portu i ksiega PAPER nie pasuja 1:1.
Kazde jest tu opisane jawnie, bo cicha proteza w adapterze byla by gorsza
od udokumentowanej luki:

1. PAPER nie ma ksiegi zlecen po identyfikatorze. Kolejka limitow jest
   kluczowana SYMBOLEM, wiec `client_order_id` z kontraktu nie ma tam
   swojego miejsca. Adapter trzyma wlasna mape client_order_id -> symbol
   i tylko dzieki niej potrafi anulowac wczesniej zlozone zlecenie.

2. Wejscie PAPER ma ksztalt sygnalu, nie zlecenia: `open_position(signal)`.
   Dopoki krok "rozdzielic strategy/decision/submitted/fill/mark price"
   z etapu 5 nie istnieje, zlecenie musi niesc decyzje, ktora je zrodzila,
   czyli `command.metadata["signal"]`. Brak sygnalu konczy sie bledem,
   nigdy zgadywaniem ksztaltu wejscia.

3. `ReducePosition` nie niesie ceny, bo prawdziwa gielda odkrywa ja sama.
   PAPER musi dostac mark price z zewnatrz - stad `mark_price`. Brak ceny
   to odmowa, nie domysl.

ZNANA LUKA (swiadoma, nie do naprawy w tym kroku): `reduce` zamyka cala
pozycje, bo ksiega PAPER liczy pozycje w notional USD, a `quantity`
w kontrakcie jest w kontraktach venue. Adapter nie wymysla przelicznika -
zamiast tego kazdy taki wynik niesie reason `PAPER_FULL_CLOSE_QUANTITY_IGNORED`,
zeby pominiecie bylo widoczne w telemetrii, a nie ciche.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

from .legacy import ExecutionDisabled
from .ports import (
    CancelOrder, ExecutionResult, ReconciliationResult, ReducePosition, SubmitOrder,
)


class PaperOrderNeedsSignal(ValueError):
    """Zlecenie PAPER przyszlo bez decyzji, ktora je zrodzila."""


class PaperMarkPriceUnavailable(RuntimeError):
    """Redukcja PAPER bez ceny mark - odmowa zamiast zgadywania."""


def _symbol_of(value: Any) -> str:
    return str(getattr(value, "symbol", "") or "").upper()


class PaperExecutionAdapter:
    """Ten sam ExecutionPort co BloFin i replay, ale venue jest lokalne."""

    def __init__(self, trader: Any, *,
                 mark_price: Optional[Callable[[str], Any]] = None,
                 enabled: bool = True) -> None:
        self.trader = trader
        self.mark_price = mark_price
        self.enabled = bool(enabled)
        self.live = False
        self._symbol_by_order: dict[str, str] = {}

    # ---- ExecutionPort ---------------------------------------------------

    def submit(self, command: SubmitOrder) -> ExecutionResult:
        if not self.enabled:
            raise ExecutionDisabled("paper execution submit is disabled")
        signal = (command.metadata or {}).get("signal")
        if not isinstance(signal, dict):
            raise PaperOrderNeedsSignal(
                "PAPER submit wymaga command.metadata['signal']: wejscie PAPER "
                "ma ksztalt sygnalu, nie zlecenia"
            )
        symbol = str(command.symbol or signal.get("symbol") or "").upper()
        self._symbol_by_order[command.client_order_id] = symbol

        # Sygnal idzie dalej BEZ dodatkowej kopii - dokladnie tak, jak wola
        # go dzis petla glowna (`app.py:699`). Zmierzone, nie zalozone:
        # `open_position` i tak rebinduje na wlasna kopie
        # (`paper_trader.py:1051`), a baseline entry_gate ma
        # `caller_signal_untouched: True` we wszystkich 21 przypadkach.
        # Kopia w adapterze byla wiec nieszkodliwa, ale i zbedna: dodawala
        # druga tozsamosc obiektu w miejscu, ktore ma byc czysta podmiana
        # miejsca wywolania.
        position = self.trader.open_position(signal)
        if position is not None:
            return ExecutionResult(
                accepted=True, state="FILLED",
                client_order_id=command.client_order_id, raw=position,
            )
        # Brak pozycji nie znaczy odrzucenia: PaperTrader mogl zaparkowac
        # limit i wtedy zlecenie zyje dalej jako working order.
        if self.trader.has_pending_limit(symbol):
            return ExecutionResult(
                accepted=True, state="ACCEPTED",
                client_order_id=command.client_order_id,
                reason="PAPER_LIMIT_PARKED",
            )
        self._symbol_by_order.pop(command.client_order_id, None)
        return ExecutionResult(
            accepted=False, state="REJECTED",
            client_order_id=command.client_order_id,
            reason="PAPER_OPEN_REJECTED",
        )

    def cancel(self, command: CancelOrder) -> ExecutionResult:
        symbol = str(
            command.symbol or self._symbol_by_order.get(command.client_order_id) or ""
        ).upper()
        if not symbol:
            raise KeyError(f"unknown client_order_id: {command.client_order_id}")
        canceled = self.trader.cancel_pending_limit(symbol)
        self._symbol_by_order.pop(command.client_order_id, None)
        if not canceled:
            return ExecutionResult(
                accepted=False, state="UNKNOWN",
                client_order_id=command.client_order_id,
                reason="PAPER_NO_PENDING_LIMIT",
            )
        return ExecutionResult(
            accepted=True, state="CANCELED",
            client_order_id=command.client_order_id, raw=canceled,
        )

    def reduce(self, command: ReducePosition) -> ExecutionResult:
        symbol = str(command.symbol or "").upper()
        client_order_id = command.client_order_id or f"PAPER-REDUCE-{symbol}"
        price = self._resolve_mark(symbol)
        if price is None:
            raise PaperMarkPriceUnavailable(
                f"brak mark price dla {symbol}: PAPER nie odkrywa ceny sam"
            )
        pnl = self.trader.close_by_symbol(symbol, {symbol: float(price)}, "port_reduce")
        if pnl is None:
            return ExecutionResult(
                accepted=False, state="REJECTED",
                client_order_id=client_order_id, reason="PAPER_NO_SUCH_POSITION",
            )
        return ExecutionResult(
            accepted=True, state="FILLED", client_order_id=client_order_id,
            raw={"pnl": float(pnl), "price": float(price)},
            reason="PAPER_FULL_CLOSE_QUANTITY_IGNORED",
        )

    def reconcile(self, local_positions: Sequence[Any] = ()) -> ReconciliationResult:
        book = list(getattr(self.trader, "positions", ()) or ())
        try:
            orders = list(self.trader.pending_limit_orders())
        except Exception as exc:  # ksiega zlecen jest czescia raportu, nie dodatkiem
            return ReconciliationResult(
                in_sync=False, positions=tuple(book), orders=(),
                discrepancies=({"reason": "PAPER_ORDER_BOOK_UNREADABLE",
                                "error": str(exc)[:160]},),
                raw={"in_sync": False, "source": "PAPER_LOCAL_BOOK"},
            )
        provided = list(local_positions or ())
        discrepancies: list[dict] = []
        if provided:
            in_book = {_symbol_of(p) for p in book}
            given = {_symbol_of(p) for p in provided}
            for symbol in sorted(given - in_book):
                discrepancies.append({"symbol": symbol, "reason": "ONLY_CALLER"})
            for symbol in sorted(in_book - given):
                discrepancies.append({"symbol": symbol, "reason": "ONLY_PAPER_BOOK"})
        in_sync = not discrepancies
        return ReconciliationResult(
            in_sync=in_sync, positions=tuple(book), orders=tuple(orders),
            discrepancies=tuple(discrepancies),
            raw={"in_sync": in_sync, "source": "PAPER_LOCAL_BOOK"},
        )

    # ---- wewnetrzne ------------------------------------------------------

    def _resolve_mark(self, symbol: str) -> Optional[float]:
        if not symbol or self.mark_price is None:
            return None
        try:
            value = self.mark_price(symbol)
        except Exception:
            return None
        if value is None:
            return None
        try:
            price = float(value)
        except (TypeError, ValueError):
            return None
        return price if price > 0 else None
