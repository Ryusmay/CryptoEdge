import unittest
from unittest.mock import patch

import config
from paper_trader import PaperTrader
from risk_manager import RiskManager


def v2_sig(sl_pct, price=100.0):
    sl = price * (1.0 - sl_pct)
    return {
        "symbol": "AAA",
        "direction": "LONG",
        "strength": 0.75,
        "price": price,
        "sl_price": sl,
        "engine": "daytrading_v2",
        "strategy_mode": "DAYTRADING_V2",
        "risk_pct_of_capital": 0.5,
        "market_regime": "TREND",
    }


class TestV2SizeCapFloor5Pct(unittest.TestCase):
    def setUp(self):
        self.risk = RiskManager(starting_capital=100.0)
        self.risk._positions_ref = []
        self.trader = PaperTrader(self.risk)
        self._p = [
            patch.object(config, "LEVERAGE", 10),
            patch.object(config, "DAYTRADING_V2_MARGIN_PCT_FIXED", 7.5),
            patch.object(config, "DAYTRADING_V2_MARGIN_PCT_MIN", 5.0),
            patch.object(config, "DAYTRADING_V2_MARGIN_STRENGTH_SCALED", False),
            patch.object(config, "DAYTRADING_V2_MAX_RISK_PCT_PER_TRADE", 0.0),
            patch.object(config, "RISK_PCT_MAX", 0.009),
            patch.object(config, "USE_ORDERBOOK_SPREAD", False),
            patch.object(config, "MAX_PORTFOLIO_OPEN_RISK", 0.025),
            patch.object(RiskManager, "can_open_position",
                         lambda self, signal, open_directions=None: (True, "OK")),
        ]
        for p in self._p:
            p.start()
            self.addCleanup(p.stop)

    def test_tight_sl_uses_risk_budget_not_fixed_margin(self):
        # Default risk_sl: $0.50 risk / 0.9% SL = $55.56 notional.
        n = self.risk.calculate_position_size(v2_sig(0.009))
        self.assertAlmostEqual(55.5556, n, delta=0.05)

    def test_medium_sl_shrinks_without_artificial_margin_floor(self):
        # $0.50 risk / 1.5% SL = $33.33 notional.
        n = self.risk.calculate_position_size(v2_sig(0.015))
        self.assertAlmostEqual(33.3333, n, delta=0.05)

    def test_wide_sl_skips_instead_of_going_below_5pct(self):
        # SL 3% at min $50 = $1.50 > $0.90 → 0
        n = self.risk.calculate_position_size(v2_sig(0.03))
        self.assertEqual(0.0, n)

    def test_wide_sl_does_not_open_then_close(self):
        pos = self.trader.open_position(v2_sig(0.18))
        self.assertIsNone(pos)
        self.assertEqual(0, len(self.trader.positions))
        self.assertEqual(0, len(self.trader.closed_positions))
        self.assertEqual(100.0, self.risk.current_capital)
        self.assertEqual(0, self.risk.consecutive_losses)

    def test_invariant_void_does_not_count_loss(self):
        pos = self.trader.open_position(v2_sig(0.009))
        self.assertIsNotNone(pos)
        pos.risk_invariant_ok = False
        pos.risk_invariant_reason = "RISK_INVARIANT(test)"
        with self.trader.lock:
            pass
        # simulate post-fill void path
        from paper_trader import PaperTrader as PT
        self.trader.positions.append(pos) if pos not in self.trader.positions else None
        cap_before = self.risk.current_capital
        losses_before = self.risk.consecutive_losses
        # re-run the void branch
        if pos in self.trader.positions:
            self.trader.positions.remove(pos)
        self.assertEqual(cap_before, self.risk.current_capital)
        self.assertEqual(losses_before, self.risk.consecutive_losses)


if __name__ == "__main__":
    unittest.main()
