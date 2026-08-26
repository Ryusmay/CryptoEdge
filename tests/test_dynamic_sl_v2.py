import unittest
from unittest.mock import patch

import config
from paper_trader import Position


def v2_pos(sl=8.0, price=10.0, atr=0.4):
    sig = {
        "symbol": "AAA",
        "direction": "LONG",
        "strength": 0.75,
        "price": price,
        "sl_price": sl,
        "tp1_price": price + (price - sl),
        "tp2_price": price + 2 * (price - sl),
        "engine": "daytrading_v2",
        "strategy_mode": "DAYTRADING_V2",
        "atr": atr,
    }
    return Position(sig, 75.0)


def hl_frame(pivot=8.5):
    # fractal 2: pivot at i=5, 10 bars
    lows = [10.0, 9.5, 9.2, 9.0, 8.8, pivot, 8.9, 9.1, 9.3, 9.4]
    highs = [x + 0.3 for x in lows]
    return {"lows": lows, "highs": highs, "closes": lows[:]}


class TestDynamicSlV2(unittest.TestCase):
    def setUp(self):
        self._p = [
            patch.object(config, "DAYTRADING_V2_SL_RATCHET_AFTER", "tp1"),
            patch.object(config, "DAYTRADING_V2_SL_RATCHET_ATR_MULT", 0.5),
            patch.object(config, "DAYTRADING_V2_SL_RATCHET_FRACTAL", 2),
            patch.object(config, "DAYTRADING_V2_CHANDELIER_ATR_MULT", 1.5),
            patch.object(config, "TRAILING_MIN_UPDATE_INTERVAL_SEC", 0.0),
            patch.object(config, "LIVE_ATR_TRAILING_ENABLED", True),
        ]
        for p in self._p:
            p.start()
            self.addCleanup(p.stop)

    def test_no_ratchet_before_tp1(self):
        pos = v2_pos()
        sl0 = pos.sl_price
        self.assertFalse(pos.ratchet_structure_sl(hl_frame()))
        self.assertEqual(sl0, pos.sl_price)

    def test_ratchet_after_tp1_raises_sl(self):
        pos = v2_pos(sl=8.0, atr=0.2)
        pos.partial_tp1_done = True
        self.assertTrue(pos.ratchet_structure_sl(hl_frame(8.5)))
        self.assertGreater(pos.sl_price, 8.0)
        self.assertAlmostEqual(pos.sl_price, 8.5 - 0.5 * 0.2, places=6)

    def test_ratchet_never_loosens(self):
        pos = v2_pos(sl=8.0, atr=0.2)
        pos.partial_tp1_done = True
        pos.ratchet_structure_sl(hl_frame(8.5))
        raised = pos.sl_price
        self.assertFalse(pos.ratchet_structure_sl(hl_frame(7.0)))
        self.assertEqual(raised, pos.sl_price)

    def test_chandelier_after_tp2(self):
        pos = v2_pos(sl=8.0, price=10.0, atr=0.4)
        pos.partial_tp1_done = True
        pos.partial_tp2_done = True
        pos.highest_price = 12.0
        pos.update_pnl(12.0)
        want = 12.0 - 1.5 * 0.4
        self.assertGreater(pos.sl_price, 10.0)
        self.assertAlmostEqual(pos.sl_price, want, places=5)
        self.assertTrue(pos.trailing_active)

    def test_chandelier_not_below_be_or_initial(self):
        pos = v2_pos(sl=8.0, price=10.0, atr=5.0)
        pos.partial_tp2_done = True
        pos.highest_price = 10.2
        pos.update_pnl(10.2)
        self.assertGreaterEqual(pos.sl_price, pos.initial_sl_price - 1e-9)
        self.assertGreaterEqual(pos.sl_price, pos.entry_price - 0.05)


if __name__ == "__main__":
    unittest.main()
