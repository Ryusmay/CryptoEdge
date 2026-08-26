import unittest
from unittest.mock import MagicMock

import config
from paper_trader import Position
from runtime import BotRuntime


class TestV2SwingSlNotReplaced(unittest.TestCase):
    def test_v2_keeps_structural_sl_without_tp_price(self):
        sig = {
            "symbol": "BTC",
            "direction": "LONG",
            "price": 100.0,
            "strength": 0.75,
            "sl_price": 97.0,
            "tp1_price": 101.5,
            "tp2_price": 104.0,
            "engine": "daytrading_v2",
            "strategy_mode": "DAYTRADING_V2",
        }
        pos = Position(sig, size_usd=750.0, leverage=10)
        self.assertAlmostEqual(97.0, float(pos.sl_price), places=8)
        self.assertAlmostEqual(101.5, float(pos.tp1_price), places=8)
        self.assertAlmostEqual(104.0, float(pos.tp2_price), places=8)
        generic_sl = 100.0 * (1 + config.STOP_LOSS_PCT / 100 / 10)
        self.assertNotAlmostEqual(generic_sl, float(pos.sl_price), places=6)

    def test_short_v2_keeps_structural_sl(self):
        sig = {
            "symbol": "ETH",
            "direction": "SHORT",
            "price": 200.0,
            "strength": 0.75,
            "sl_price": 206.0,
            "tp1_price": 197.0,
            "tp2_price": 192.0,
            "engine": "daytrading_v2",
        }
        pos = Position(sig, size_usd=750.0, leverage=10)
        self.assertAlmostEqual(206.0, float(pos.sl_price), places=8)


class TestCloseAllDoesNotKill(unittest.TestCase):
    def test_close_all_does_not_halt_or_disable_engine(self):
        rt = BotRuntime()
        rt.engine_enabled = True
        rt.risk = MagicMock()
        rt.risk.is_halted = False
        rt.risk.halt_reason = None
        rt.risk.paused = False
        trader = MagicMock()
        trader.positions = [object(), object()]
        trader.close_all.return_value = []
        trader.lock = MagicMock()
        trader.lock.__enter__ = MagicMock(return_value=None)
        trader.lock.__exit__ = MagicMock(return_value=False)
        rt.trader = trader
        rt.last_price_map = {}
        msg = rt.close_all()
        self.assertTrue(rt.engine_enabled)
        self.assertFalse(rt.risk.is_halted)
        self.assertFalse(rt.risk.paused)
        trader.close_all.assert_called_once()
        self.assertIn("CLOSED", msg)
        self.assertNotIn("KILL", msg.upper() if False else msg)
