import math
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import config
from accounting import D
from paper_trader import PaperTrader
from restart_recovery import RestartRecovery


class _Risk:
    def __init__(self):
        self.current_capital = 10_000.0
        self.opened = 0

    def register_open(self):
        self.opened += 1

    def calculate_position_size(self, signal):
        return 1000.0

    def can_open_position(self, signal, open_directions=None):
        return True, "OK"

    def log_reject(self, *args, **kwargs):
        return None

    def update_capital(self, capital, pnl=0):
        self.current_capital = capital


class _Protection:
    def __init__(self):
        self.by_key = {}
        self.updates = []

    @staticmethod
    def _key(symbol, direction):
        return f"{symbol}:{direction}"

    def load_state(self):
        return None

    def attach_protection(self, symbol, direction, sl_price, **kwargs):
        self.by_key[self._key(symbol, direction)] = SimpleNamespace(
            sl_price=sl_price, size_contracts=kwargs.get("size_contracts")
        )

    def update_exchange_sl(self, symbol, direction, new_sl, size_contracts=None):
        self.updates.append((symbol, direction, new_sl, size_contracts))


class TestFiniteAccounting(unittest.TestCase):
    def test_decimal_conversion_rejects_non_finite_values(self):
        for value in (float("nan"), float("inf"), float("-inf"),
                      Decimal("NaN"), Decimal("Infinity")):
            self.assertEqual(D(value), Decimal("0"))


class TestPositionLifecyclePersistence(unittest.TestCase):
    def test_restart_preserves_partial_and_contract_state(self):
        risk1 = _Risk()
        source = PaperTrader(risk1)
        with tempfile.TemporaryDirectory() as td, patch.object(
            config, "DECISION_TELEMETRY_PATH", str(Path(td) / "decision_telemetry.jsonl")
        ):
            pos = source.open_position({
                "symbol": "BTC", "direction": "LONG", "price": 100.0,
                "strength": 0.8, "sl_price": 95.0, "tp_price": 120.0,
            })
        self.assertIsNotNone(pos)
        pos.size_contracts = 7.0
        pos.actual_notional = 700.0
        pos.contract_value = 1.0
        pos.funding_paid = 1.25
        pos.partial_taken = True
        pos.partial_tp1_done = True
        pos.partial_stage = 1
        pos.original_size = 1000.0
        pos.actual_risk_usd = 35.0
        pos.tp_plan = {"frac_tp1": 0.3}

        rows = source.export_open_positions()
        risk2 = _Risk()
        protection = _Protection()
        restored = PaperTrader(risk2, protection=protection)
        self.assertEqual(restored.restore_open_positions(rows), 1)
        got = restored.positions[0]
        self.assertEqual(got.size_contracts, 7.0)
        self.assertEqual(got.actual_notional, 700.0)
        self.assertEqual(got.funding_paid, 1.25)
        self.assertTrue(got.partial_tp1_done)
        self.assertEqual(got.partial_stage, 1)
        self.assertEqual(got.original_size, 1000.0)
        self.assertEqual(got.actual_risk_usd, 35.0)
        self.assertEqual(got.tp_plan, {"frac_tp1": 0.3})

    def test_recovery_reconnects_trailing_sl_callback(self):
        risk = _Risk()
        trader = PaperTrader(risk)
        with tempfile.TemporaryDirectory() as td, patch.object(
            config, "DECISION_TELEMETRY_PATH", str(Path(td) / "decision_telemetry.jsonl")
        ):
            pos = trader.open_position({
                "symbol": "ETH", "direction": "LONG", "price": 100.0,
                "strength": 0.8, "sl_price": 95.0,
            })
        protection = _Protection()
        RestartRecovery(risk=risk, trader=trader, protection=protection).run()
        self.assertTrue(callable(pos.on_sl_updated))
        pos.on_sl_updated("ETH", "LONG", 101.0, 2.0)
        self.assertEqual(protection.updates[-1], ("ETH", "LONG", 101.0, 2.0))


class TestInvalidMarketPrices(unittest.TestCase):
    def test_paper_trader_rejects_non_finite_entry_prices(self):
        trader = PaperTrader(_Risk())
        base = {"symbol": "BTC", "direction": "LONG", "strength": 0.8}
        for price in (float("nan"), float("inf"), -1.0, 0.0):
            self.assertIsNone(trader.open_position({**base, "price": price}))
        self.assertEqual(trader.positions, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
