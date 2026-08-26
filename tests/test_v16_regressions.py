import unittest
from types import SimpleNamespace
from unittest.mock import patch

import config
from paper_trader import PaperTrader, Position
from runtime import BotRuntime
from restart_recovery import RestartRecovery


class RiskStub:
    def __init__(self):
        self.paused = False
        self.is_halted = False
        self.halt_reason = None
        self.current_capital = 1000.0
        self.rejections = []

    def log_reject(self, symbol, direction, strength, reason, signal=None):
        # Sygnatura musi odpowiadac RiskManager.log_reject, ktory od dawna
        # przyjmuje signal=. Bez tego wolajacy dostaje TypeError, ktory ginie
        # w `except Exception: pass` wokol telemetrii i odrzucenie po cichu
        # nie zostaje nigdzie zapisane.
        self.rejections.append(reason)

    def update_capital(self, capital, pnl=0):
        self.current_capital = capital


class TestLiveBoundary(unittest.TestCase):
    def test_paper_trader_never_creates_live_fill(self):
        risk = RiskStub()
        trader = PaperTrader(risk)
        signal = {"symbol": "BTC", "direction": "LONG", "strength": 1.0, "price": 100.0}
        with patch.object(config, "PAPER_TRADING", False), patch.object(
            config, "LIVE_EXECUTION_ENABLED", True
        ):
            self.assertIsNone(trader.open_position(signal))
        self.assertEqual(trader.positions, [])
        self.assertIn("PAPER_TRADER_FORBIDDEN_IN_LIVE", risk.rejections)


class TestPersistentHalts(unittest.TestCase):
    def _runtime(self, reason):
        rt = BotRuntime()
        rt.risk = RiskStub()
        rt.risk.is_halted = True
        rt.risk.halt_reason = reason
        rt.risk.sync_open_count = lambda count: None
        rt.trader = SimpleNamespace(positions=[])
        return rt

    def test_start_does_not_clear_kill_switch(self):
        rt = self._runtime("KILL_SWITCH: operator")
        rt.start_trading()
        self.assertTrue(rt.risk.is_halted)
        self.assertEqual(rt.risk.halt_reason, "KILL_SWITCH: operator")

    def test_resume_does_not_clear_reconciliation_halt(self):
        rt = self._runtime("RECOVERY_ORPHAN_EXCHANGE")
        message = rt.resume()
        self.assertTrue(rt.risk.is_halted)
        self.assertIn("hard halt", message)


class TestRecoverySafety(unittest.TestCase):
    def test_exchange_orphan_halts_new_entries(self):
        risk = RiskStub()
        trader = SimpleNamespace(positions=[])
        reconciler = SimpleNamespace(
            fetch_exchange_positions=lambda: [],
            reconcile=lambda positions, **kwargs: {
                "in_sync": False, "only_local": [],
                "only_exchange": [{"symbol": "BTC", "direction": "LONG", "size": 1}],
            },
            summary_text=lambda report: "drift",
        )
        with patch.object(config, "PAPER_TRADING", False):
            RestartRecovery(risk=risk, trader=trader, reconciler=reconciler).run()
        self.assertTrue(risk.is_halted)
        self.assertTrue(risk.paused)
        self.assertEqual(risk.halt_reason, "RECOVERY_ORPHAN_EXCHANGE")


class TestPartialContractSizing(unittest.TestCase):
    def test_partial_close_quantizes_to_lot_size(self):
        class Registry:
            @staticmethod
            def quantize_size(symbol, size, mode="down"):
                return int(size)

        risk = RiskStub()
        trader = PaperTrader(risk)
        pos = SimpleNamespace(
            symbol="BTC", direction="LONG", engine="reversal",
            tp_plan={"frac_tp1": 0.5}, original_size=1300.0,
            size_usd=1300.0, size_contracts=13.0,
            actual_notional=1300.0, funding_paid=0.0,
            leverage=10, entry_price=100.0, partial_stage=0,
            partial_taken=False, trailing_active=False,
        )
        previous = getattr(Position, "_shared_registry", None)
        Position._shared_registry = Registry()
        try:
            trader.partial_close(pos, 110.0, reason="partial_tp1")
        finally:
            Position._shared_registry = previous
        self.assertEqual(pos.size_contracts, 7.0)
        self.assertAlmostEqual(pos.size_usd, 700.0)

    def test_partial_then_final_close_reports_one_cumulative_position_result(self):
        risk = RiskStub()
        risk.register_close = lambda *args, **kwargs: None
        trader = PaperTrader(risk)
        pos = Position({
            "symbol": "BTC", "direction": "LONG", "price": 100.0,
            "strength": 1.0, "sl_price": 90.0, "tp_price": 120.0,
        }, size_usd=1000.0, leverage=1)
        trader.positions.append(pos)
        with patch.object(config, "ALERTS_ENABLED", False):
            partial_pnl = trader.partial_close(pos, 110.0, reason="partial_tp")
            capital_after_partial = risk.current_capital
            total_pnl = trader.close_position(pos, 105.0, reason="manual")

        self.assertAlmostEqual(total_pnl, partial_pnl + pos.final_leg_pnl)
        self.assertAlmostEqual(risk.current_capital, capital_after_partial + pos.final_leg_pnl)
        self.assertEqual(1, len(trader.closed_positions))
        self.assertGreater(pos.partial_realized_pnl, 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
