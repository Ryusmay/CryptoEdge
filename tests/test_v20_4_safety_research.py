import unittest
from types import SimpleNamespace

from historical_replay import ReplayRequest, validate_execution_dataset
from price_layers import extract_price_layers
from research_gate import evaluate_research_gate
from risk_manager import RiskManager
from runtime import BotRuntime
from v2_market_snapshot import EventClock, V2MarketSnapshot


class TestV204Safety(unittest.TestCase):
    def test_price_layers_are_explicit(self):
        x = extract_price_layers({"strategy_price": 100, "decision_price": 101,
                                  "submitted_price": 102, "fill_price": 103, "mark_price": 104})
        self.assertEqual((100, 101, 102, 103, 104), tuple(x[k] for k in
            ("strategy_price", "decision_price", "submitted_price", "fill_price", "mark_price")))

    def test_reduce_only_blocks_new_risk(self):
        risk = RiskManager(starting_capital=1000)
        rt = BotRuntime(); rt.risk = risk
        rt.set_reduce_only(True, "test")
        ok, why = risk.can_open_position({"symbol": "BTC", "direction": "LONG", "strength": 1.0})
        self.assertFalse(ok); self.assertEqual("RISK_REDUCE_ONLY", why)
        self.assertEqual("RISK_STATE=NORMAL", rt.set_reduce_only(False))

    def test_event_clock_never_fills_before_latency(self):
        clock = EventClock(250)
        self.assertFalse(clock.fill_allowed(1000, 1249))
        self.assertTrue(clock.fill_allowed(1000, 1250))

    def test_snapshot_rejects_future_bar(self):
        s = V2MarketSnapshot("BTC", 1000, 1000, frames={"15m": {"timestamps": [1001]}})
        self.assertEqual((False, "LOOKAHEAD_15m"), s.validate_closed_bars())

    def test_real_1m_and_l2_are_required(self):
        self.assertFalse(validate_execution_dataset({}, ReplayRequest(execution_resolution="1m"))[0])
        self.assertFalse(validate_execution_dataset({}, ReplayRequest(execution_resolution="L2"))[0])
        self.assertTrue(validate_execution_dataset({"1m": {"timestamps": [1]}}, ReplayRequest(execution_resolution="1m"))[0])

    def test_research_gate_rejects_concentrated_single_symbol_edge(self):
        rows = [{"symbol": "BTC", "window": "A", "r": 1.0, "cost_r": .1} for _ in range(20)]
        report = evaluate_research_gate(rows, trial_count=10)
        self.assertFalse(report["pass"])
        self.assertFalse(report["checks"]["multi_symbol"])


if __name__ == "__main__":
    unittest.main()
