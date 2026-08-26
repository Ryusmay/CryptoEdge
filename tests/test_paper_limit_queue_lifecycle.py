import unittest
from unittest.mock import patch

import config
from paper_trader import PaperTrader
from risk_manager import RiskManager


def signal(symbol="BTC", direction="LONG", price=100.0, limit=99.0):
    return {
        "symbol": symbol,
        "direction": direction,
        "price": price,
        "limit_price": limit,
        "strength": 0.75,
        "engine": "daytrading_v2",
        "strategy_mode": "DAYTRADING_V2",
        "sl_price": 99.1 if direction == "LONG" else 100.9,
        "tp1_price": 105.0 if direction == "LONG" else 95.0,
        "tp2_price": 110.0 if direction == "LONG" else 90.0,
        "tp_price": 110.0 if direction == "LONG" else 90.0,
        "expected_net_r": 0.8,
        "reasons": ["TEST"],
    }


class PaperLimitQueueLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.trader = PaperTrader(RiskManager(1000.0))

    def test_repeated_signal_does_not_extend_original_deadline(self):
        with patch("time.time", return_value=1000.0), patch.object(
            self.trader.risk, "can_open_position", return_value=(True, "OK")
        ):
            self.assertIsNone(self.trader.open_position(signal()))
        first = self.trader._limit_queue["BTC"].copy()
        with patch("time.time", return_value=1300.0), patch.object(
            self.trader.risk, "can_open_position", return_value=(True, "OK")
        ):
            self.assertIsNone(self.trader.open_position(signal(limit=98.0)))
        current = self.trader._limit_queue["BTC"]
        self.assertEqual(current["deadline"], first["deadline"])
        self.assertEqual(current["limit"], first["limit"])

    def test_limit_timeout_cancels_setup_without_chasing_market(self):
        with patch("time.time", return_value=1000.0), patch.object(
            self.trader.risk, "can_open_position", return_value=(True, "OK")
        ):
            self.trader.open_position(signal())
        deadline = self.trader._limit_queue["BTC"]["deadline"]
        with patch.object(self.trader.risk, "can_open_position", return_value=(True, "OK")):
            opened = self.trader.process_limit_queue({"BTC": 100.0}, now=deadline)
        self.assertEqual(opened, [])
        self.assertFalse(self.trader.has_position("BTC"))
        self.assertFalse(self.trader.has_pending_limit("BTC"))

    def test_pending_snapshot_is_serializable_and_visible(self):
        with patch("time.time", return_value=1000.0), patch.object(
            self.trader.risk, "can_open_position", return_value=(True, "OK")
        ):
            self.trader.open_position(signal())
            rows = self.trader.pending_limit_orders(now=1100.0)
        self.assertEqual(rows[0]["symbol"], "BTC")
        self.assertGreater(rows[0]["seconds_remaining"], 0)

    def test_risk_gate_runs_before_limit_is_parked(self):
        bad = signal()
        bad["expected_net_r"] = -0.1
        with patch.object(
            self.trader.risk, "can_open_position",
            return_value=(False, "NON_POSITIVE_NET_R(-0.10<=0)"),
        ) as gate:
            self.assertIsNone(self.trader.open_position(bad))
        gate.assert_called_once()
        self.assertFalse(self.trader.has_pending_limit("BTC"))


if __name__ == "__main__":
    unittest.main()
