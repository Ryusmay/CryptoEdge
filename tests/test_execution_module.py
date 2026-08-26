import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from cryptoedge.execution import (
    CancelOrder, ExecutionDisabled, Fill, FillLedger, InvalidTransition,
    LegacyExecutionAdapter, OrderLifecycle, OrderStatus, ReducePosition, SubmitOrder,
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


if __name__ == "__main__":
    unittest.main()
