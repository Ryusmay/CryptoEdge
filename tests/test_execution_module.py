import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from cryptoedge.execution import (
    CancelOrder, ExecutionDisabled, ExecutionPort, Fill, FillLedger, InvalidTransition,
    LegacyExecutionAdapter, OrderLifecycle, OrderStatus, PaperExecutionAdapter,
    PaperMarkPriceUnavailable, PaperOrderNeedsSignal, ReducePosition, SubmitOrder,
    domain_fill_to_ledger,
)


class FillLedgerTests(unittest.TestCase):
    def test_duplicate_trade_is_idempotent_and_partial_fills_use_vwap(self):
        ledger = FillLedger()
        first = Fill("T1", "C1", Decimal("2"), Decimal("100"), Decimal("0.2"), "maker")
        second = Fill("T2", "C1", Decimal("1"), Decimal("106"), Decimal("0.1"), "taker")
        self.assertTrue(ledger.record(first))
        self.assertFalse(ledger.record(first))
        self.assertTrue(ledger.record(second))
        total = ledger.aggregate("C1")
        self.assertEqual(total.quantity, Decimal("3"))
        self.assertEqual(total.vwap, Decimal("102"))
        self.assertEqual(total.fee, Decimal("0.3"))
        self.assertEqual(total.liquidity_role, "mixed")

    def test_conflicting_duplicate_trade_is_rejected(self):
        ledger = FillLedger()
        ledger.record(Fill("T1", "C1", 1, 100))
        with self.assertRaises(ValueError):
            ledger.record(Fill("T1", "C1", 2, 100))

    def test_restart_preserves_trade_id_idempotency(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "fills.json"
            ledger = FillLedger()
            fill = Fill("VENUE-1", "C1", Decimal("0.25"), Decimal("101.125"))
            ledger.record(fill)
            ledger.save_json(path)
            restored = FillLedger.load_json(path)
            self.assertFalse(restored.record(fill))
            self.assertEqual(restored.aggregate("C1").quantity, Decimal("0.25"))


class LifecycleTests(unittest.TestCase):
    def test_partial_then_full_and_duplicate_does_not_overfill(self):
        order = OrderLifecycle("C1", Decimal("3"))
        order.transition(OrderStatus.SUBMITTING)
        order.transition(OrderStatus.ACCEPTED)
        first = Fill("T1", "C1", 1, 100)
        self.assertTrue(order.apply_fill(first))
        self.assertEqual(order.status, OrderStatus.PARTIALLY_FILLED)
        self.assertFalse(order.apply_fill(first))
        order.apply_fill(Fill("T2", "C1", 2, 103))
        self.assertEqual(order.status, OrderStatus.FILLED)
        self.assertEqual(order.remaining_quantity, Decimal("0"))

    def test_terminal_transition_is_rejected(self):
        order = OrderLifecycle("C1", 1)
        order.transition(OrderStatus.REJECTED)
        with self.assertRaises(InvalidTransition):
            order.transition(OrderStatus.ACCEPTED)

    def test_fill_cannot_silently_exceed_requested_quantity(self):
        order = OrderLifecycle("C1", 1)
        order.transition(OrderStatus.SUBMITTING)
        with self.assertRaises(ValueError):
            order.apply_fill(Fill("T1", "C1", 2, 100))
        self.assertEqual(len(order.ledger), 0)


class _Order:
    def __init__(self, cid, state="SUBMITTED"):
        self.client_order_id = cid
        self.order_id = "E1"
        self.state = state
        self.reject_reason = None


class _Executor:
    def __init__(self):
        self.calls = []

    def place_order(self, **kwargs):
        self.calls.append(("submit", kwargs))
        return _Order(kwargs["client_order_id"])

    def cancel_order(self, order):
        self.calls.append(("cancel", order))
        order.state = "CANCELED"
        return order

    def close_market(self, symbol, direction, quantity, wait_fill=True):
        self.calls.append(("reduce", symbol, direction, quantity, wait_fill))
        return _Order("REDUCE1", "FILLED")


class _Reconciler:
    def reconcile(self, positions, executor=None):
        return {"in_sync": False, "only_exchange": [{"symbol": "ETH"}]}


class LegacyAdapterTests(unittest.TestCase):
    def test_adapter_delegates_commands_and_normalizes_results(self):
        executor = _Executor()
        adapter = LegacyExecutionAdapter(executor, _Reconciler(), enabled=True, live=True)
        submit = adapter.submit(SubmitOrder("C1", "BTC", "buy", Decimal("2")))
        self.assertTrue(submit.accepted)
        self.assertEqual(submit.exchange_order_id, "E1")
        canceled = adapter.cancel(CancelOrder("C1", "BTC", "E1"))
        self.assertEqual(canceled.state, "CANCELED")
        reduced = adapter.reduce(ReducePosition("BTC", "LONG", Decimal("0.5")))
        self.assertEqual(reduced.state, "FILLED")
        reconciliation = adapter.reconcile([])
        self.assertFalse(reconciliation.in_sync)
        self.assertEqual(len(reconciliation.discrepancies), 1)

    def test_submit_is_disabled_by_default_and_paper_cannot_use_live_executor(self):
        command = SubmitOrder("C1", "BTC", "buy", Decimal("1"))
        with self.assertRaises(ExecutionDisabled):
            LegacyExecutionAdapter(_Executor()).submit(command)
        with self.assertRaises(ExecutionDisabled):
            LegacyExecutionAdapter(_Executor(), enabled=True, live=False).submit(command)

    def test_unknown_error_state_is_not_accepted(self):
        executor = _Executor()
        executor.place_order = lambda **kwargs: _Order(kwargs["client_order_id"], "ERROR")
        result = LegacyExecutionAdapter(executor, enabled=True, live=True).submit(
            SubmitOrder("C1", "BTC", "buy", Decimal("1"))
        )
        self.assertFalse(result.accepted)

    def test_none_and_malformed_reconciliation_fail_closed(self):
        class NoneReconciler:
            def reconcile(self, positions, executor=None): return None
        class MalformedReconciler:
            def reconcile(self, positions, executor=None): return {"orders": []}
        for reconciler in (NoneReconciler(), MalformedReconciler()):
            result = LegacyExecutionAdapter(_Executor(), reconciler).reconcile([])
            self.assertFalse(result.in_sync)
            self.assertTrue(result.discrepancies)

    def test_domain_fill_bridge_uses_stable_venue_trade_id(self):
        from cryptoedge.domain import Fill as DomainFill
        value = DomainFill(order_id="E1", client_order_id="C1", symbol="BTC",
                           quantity=1, price=100, ts_ms=123,
                           metadata={"trade_id": "T-VENUE"})
        bridged = domain_fill_to_ledger(value)
        self.assertEqual(bridged.trade_id, "T-VENUE")
        self.assertEqual(bridged.exchange_order_id, "E1")


class _ReplayLikeExecutor:
    """Minimalna atrapa o ksztalcie ReplayExecutionEngine (submit/request_cancel)."""

    def __init__(self):
        self.orders = {}
        self.calls = []

    def submit(self, *, order_id, symbol, side, quantity, decision_ts_ms,
               limit_price=None, timeout_ms=None):
        self.calls.append(("submit", order_id, symbol, side, quantity,
                           decision_ts_ms, limit_price, timeout_ms))
        order = _Order(order_id, "ACCEPTED")
        order.order_id = order_id
        self.orders[order_id] = order
        return order

    def request_cancel(self, order_id, ts_ms):
        self.calls.append(("cancel", order_id, ts_ms))
        self.orders[order_id].state = "CANCELING"


class LegacyAdapterReplayBranchTests(unittest.TestCase):
    """Gałąź replay nie miała dotąd żadnego testu — atrapa ma kształt silnika."""

    def test_replay_submit_passes_the_signature_replay_engine_declares(self):
        executor = _ReplayLikeExecutor()
        adapter = LegacyExecutionAdapter(executor, enabled=True, live=False)
        result = adapter.submit(SubmitOrder(
            "C1", "BTC", "buy", Decimal("2"), limit_price=Decimal("100"),
            metadata={"decision_ts_ms": 1_000, "timeout_ms": 900_000},
        ))
        self.assertTrue(result.accepted)
        self.assertEqual(result.state, "ACCEPTED")
        kind, order_id, symbol, side, qty, ts, limit, timeout = executor.calls[0]
        self.assertEqual((kind, order_id, symbol, side), ("submit", "C1", "BTC", "buy"))
        self.assertEqual((qty, ts, limit, timeout), (2.0, 1_000, 100.0, 900_000))

    def test_replay_executor_does_not_need_the_live_flag(self):
        # place_order (venue) wymaga live=True; submit (replay) nie dotyka gieldy.
        adapter = LegacyExecutionAdapter(_ReplayLikeExecutor(), enabled=True, live=False)
        self.assertTrue(adapter.submit(SubmitOrder("C1", "BTC", "buy", Decimal("1"))).accepted)

    def test_replay_cancel_reads_state_back_from_the_engine_book(self):
        executor = _ReplayLikeExecutor()
        adapter = LegacyExecutionAdapter(executor, enabled=True, live=False)
        adapter.submit(SubmitOrder("C1", "BTC", "buy", Decimal("1")))
        result = adapter.cancel(CancelOrder("C1", "BTC", requested_at_ms=2_000))
        self.assertEqual(result.state, "CANCELING")
        self.assertEqual(executor.calls[-1], ("cancel", "C1", 2_000))


class _PaperPosition:
    def __init__(self, symbol):
        self.symbol = symbol


class _FakeTrader:
    """Atrapa ksiegi PAPER: tylko to, czego adapter naprawde dotyka."""

    def __init__(self, *, opens=None, parks=False):
        self.positions = []
        self._pending = {}
        self._opens = opens
        self._parks = parks
        self.opened_signals = []
        self.closed = []

    def open_position(self, signal):
        self.opened_signals.append(signal)
        if self._parks:
            self._pending[str(signal.get("symbol")).upper()] = {"limit": 100.0}
            return None
        if self._opens is None:
            return None
        position = _PaperPosition(self._opens)
        self.positions.append(position)
        return position

    def has_pending_limit(self, symbol):
        return str(symbol or "").upper() in self._pending

    def cancel_pending_limit(self, symbol):
        return self._pending.pop(str(symbol or "").upper(), None)

    def pending_limit_orders(self, now=None):
        return [{"symbol": sym} for sym in sorted(self._pending)]

    def close_by_symbol(self, symbol, price_map=None, reason="manual"):
        symbol = str(symbol or "").upper()
        position = next((p for p in self.positions if p.symbol == symbol), None)
        if position is None:
            return None
        self.positions.remove(position)
        self.closed.append((symbol, price_map, reason))
        return 12.5


class PaperExecutionAdapterTests(unittest.TestCase):
    def test_adapter_satisfies_the_same_port_as_the_venue_adapter(self):
        self.assertIsInstance(PaperExecutionAdapter(_FakeTrader()), ExecutionPort)

    def test_submit_without_the_decision_that_produced_it_fails_closed(self):
        # PAPER wchodzi sygnalem; adapter nie zgaduje ksztaltu wejscia.
        with self.assertRaises(PaperOrderNeedsSignal):
            PaperExecutionAdapter(_FakeTrader()).submit(
                SubmitOrder("C1", "BTC", "buy", Decimal("1"))
            )

    def test_opened_position_is_reported_as_filled(self):
        trader = _FakeTrader(opens="BTC")
        result = PaperExecutionAdapter(trader).submit(SubmitOrder(
            "C1", "BTC", "buy", Decimal("1"),
            metadata={"signal": {"symbol": "BTC", "direction": "LONG"}},
        ))
        self.assertTrue(result.accepted)
        self.assertEqual(result.state, "FILLED")
        self.assertEqual(len(trader.opened_signals), 1)

    def test_parked_limit_is_accepted_not_rejected(self):
        # Brak pozycji nie znaczy odrzucenia - zlecenie zyje jako working order.
        result = PaperExecutionAdapter(_FakeTrader(parks=True)).submit(SubmitOrder(
            "C1", "BTC", "buy", Decimal("1"),
            metadata={"signal": {"symbol": "BTC"}},
        ))
        self.assertTrue(result.accepted)
        self.assertEqual((result.state, result.reason), ("ACCEPTED", "PAPER_LIMIT_PARKED"))

    def test_refused_entry_is_rejected(self):
        result = PaperExecutionAdapter(_FakeTrader()).submit(SubmitOrder(
            "C1", "BTC", "buy", Decimal("1"), metadata={"signal": {"symbol": "BTC"}},
        ))
        self.assertFalse(result.accepted)
        self.assertEqual(result.state, "REJECTED")

    def test_submit_is_disabled_when_the_gate_is_closed(self):
        with self.assertRaises(ExecutionDisabled):
            PaperExecutionAdapter(_FakeTrader(), enabled=False).submit(SubmitOrder(
                "C1", "BTC", "buy", Decimal("1"), metadata={"signal": {"symbol": "BTC"}},
            ))

    def test_cancel_removes_the_parked_limit_once_and_then_reports_no_op(self):
        trader = _FakeTrader(parks=True)
        adapter = PaperExecutionAdapter(trader)
        adapter.submit(SubmitOrder("C1", "BTC", "buy", Decimal("1"),
                                   metadata={"signal": {"symbol": "BTC"}}))
        first = adapter.cancel(CancelOrder("C1", "BTC"))
        self.assertEqual((first.accepted, first.state), (True, "CANCELED"))
        second = adapter.cancel(CancelOrder("C1", "BTC"))
        self.assertFalse(second.accepted)
        self.assertEqual(second.reason, "PAPER_NO_PENDING_LIMIT")

    def test_cancel_of_an_unknown_order_without_a_symbol_is_an_error(self):
        with self.assertRaises(KeyError):
            PaperExecutionAdapter(_FakeTrader()).cancel(CancelOrder("NOPE", ""))

    def test_reduce_without_a_price_refuses_instead_of_inventing_one(self):
        # Regula "po jakiej cenie zamykamy" ma jednego wlasciciela
        # (close_policy). Adapter nie moze byc drugim.
        trader = _FakeTrader(opens="BTC")
        trader.positions.append(_PaperPosition("BTC"))
        for bad in (None, 0, -1, "abc"):
            with self.assertRaises(PaperMarkPriceUnavailable):
                PaperExecutionAdapter(trader).reduce(
                    ReducePosition("BTC", "LONG", Decimal("1"), price=bad)
                )

    def test_reduce_closes_at_the_price_and_reason_the_caller_resolved(self):
        trader = _FakeTrader()
        trader.positions.append(_PaperPosition("BTC"))
        result = PaperExecutionAdapter(trader).reduce(ReducePosition(
            "BTC", "LONG", Decimal("0.5"), "R1",
            price=Decimal("250"), reason="manual:STALE_PRICE_900s",
        ))
        self.assertTrue(result.accepted)
        self.assertEqual(result.state, "FILLED")
        # Pominiecie quantity musi byc widoczne, a nie ciche.
        self.assertEqual(result.reason, "PAPER_FULL_CLOSE_QUANTITY_IGNORED")
        self.assertEqual(trader.closed[0][0], "BTC")
        self.assertEqual(trader.closed[0][1], {"BTC": 250.0})
        # Slad po nieswiezej cenie musi dojsc do ksiegi nietkniety.
        self.assertEqual(trader.closed[0][2], "manual:STALE_PRICE_900s")

    def test_reduce_of_a_missing_position_is_rejected_not_raised(self):
        result = PaperExecutionAdapter(_FakeTrader()).reduce(
            ReducePosition("ETH", "LONG", Decimal("1"), price=Decimal("10"))
        )
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "PAPER_NO_SUCH_POSITION")

    def test_reconcile_reports_the_local_book_and_its_working_orders(self):
        trader = _FakeTrader(parks=True)
        trader.positions.append(_PaperPosition("BTC"))
        trader._pending["ETH"] = {"limit": 1.0}
        report = PaperExecutionAdapter(trader).reconcile()
        self.assertTrue(report.in_sync)
        self.assertEqual(len(report.positions), 1)
        self.assertEqual([row["symbol"] for row in report.orders], ["ETH"])

    def test_reconcile_names_both_directions_of_drift(self):
        trader = _FakeTrader()
        trader.positions.append(_PaperPosition("BTC"))
        report = PaperExecutionAdapter(trader).reconcile([_PaperPosition("SOL")])
        self.assertFalse(report.in_sync)
        reasons = {(row["symbol"], row["reason"]) for row in report.discrepancies}
        self.assertEqual(reasons, {("SOL", "ONLY_CALLER"), ("BTC", "ONLY_PAPER_BOOK")})

    def test_unreadable_order_book_fails_closed(self):
        trader = _FakeTrader()

        def boom(now=None):
            raise RuntimeError("book unavailable")

        trader.pending_limit_orders = boom
        report = PaperExecutionAdapter(trader).reconcile()
        self.assertFalse(report.in_sync)
        self.assertEqual(report.discrepancies[0]["reason"], "PAPER_ORDER_BOOK_UNREADABLE")


if __name__ == "__main__":
    unittest.main()
