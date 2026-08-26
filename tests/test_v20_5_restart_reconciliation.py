import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import config
from protection import ProtectionManager
from position_reconciler import PositionReconciler
from restart_recovery import RestartRecovery


class ReconcilerStub:
    def fetch_exchange_positions(self):
        return [{"symbol": "BTC-USDT", "direction": "LONG", "size": 1,
                 "avg_price": 100.0}]

    def reconcile(self, positions, executor=None, protection=None):
        return {"in_sync": True, "only_local": [], "only_exchange": []}

    def summary_text(self, report):
        return "sync ok"

    def _norm_symbol(self, row):
        return str(row.get("symbol", "")).split("-")[0]


class ExecutorStub:
    def __init__(self):
        self.active_symbols = None
        self.orders = {}

    def cancel_orphan_orders(self, active_symbols):
        self.active_symbols = list(active_symbols)
        return [{"order_id": "orphan-1", "state": "CANCELED"}]


class RestartReconciliationScenarioTests(unittest.TestCase):
    def test_live_position_query_failure_cannot_be_interpreted_as_flat(self):
        class BrokenAccount:
            _last_positions = []

            @staticmethod
            def sync(force=False):
                raise TimeoutError("exchange position query timed out")

        reconciler = PositionReconciler(account_sync=BrokenAccount())
        with patch.object(config, "PAPER_TRADING", False):
            report = reconciler.reconcile([])
        self.assertFalse(report["in_sync"])
        self.assertTrue(report["drift_blocks_entries"])
        self.assertIn("timed out", report["error"])

    def test_live_order_query_failure_is_unknown_and_blocks_entries(self):
        class EmptyAccount:
            _last_positions = []

        class BrokenExecutor:
            @staticmethod
            def fetch_open_orders():
                raise TimeoutError("exchange order query timed out")

        reconciler = PositionReconciler(account_sync=EmptyAccount())
        with patch.object(config, "PAPER_TRADING", False):
            report = reconciler.reconcile([], executor=BrokenExecutor())
        self.assertFalse(report["in_sync"])
        self.assertTrue(report["drift_blocks_entries"])
        self.assertIn("timed out", report["orders_error"])
        self.assertTrue(reconciler.blocks_new_entries())

    def test_position_orphan_order_and_missing_sl_are_reconciled_together(self):
        reconciler, executor = ReconcilerStub(), ExecutorStub()
        trader = SimpleNamespace(positions=[])
        risk = SimpleNamespace(is_halted=False, paused=False, halt_reason=None)
        with tempfile.TemporaryDirectory() as td:
            protection = ProtectionManager()
            protection._state_path = Path(td) / "protection.json"
            with patch.object(config, "PAPER_TRADING", False), \
                 patch.object(config, "LIVE_EXECUTION_ENABLED", False), \
                 patch.object(config, "AUTO_CANCEL_ORPHAN_ORDERS", True), \
                 patch.object(config, "RECOVERY_REATTACH_EXCHANGE_SL", False):
                report = RestartRecovery(risk=risk, trader=trader,
                                         protection=protection,
                                         reconciler=reconciler,
                                         executor=executor).run()
        self.assertEqual(report["exchange_positions"], 1)
        self.assertEqual(report["protection_rearmed"], 1)
        self.assertIn("BTC:LONG", protection.by_key)
        self.assertEqual(executor.active_symbols, ["BTC"])
        self.assertEqual(report["orphan_orders_canceled"][0]["order_id"], "orphan-1")


if __name__ == "__main__":
    unittest.main()
