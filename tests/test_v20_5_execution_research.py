import unittest
from unittest.mock import patch

import config
from order_models import Order
from portfolio_risk import check_portfolio_limits, compute_open_risk
from replay_execution import ReplayExecutionEngine
from signal_research import calibrate_score_probability, marginal_filter_contribution, rank_candidates
from universe_policy import crypto_perpetual_allowed
from v2_market_snapshot import V2MarketSnapshot, last_bar_closed


class ClosedBarContractTests(unittest.TestCase):
    def test_open_timestamp_does_not_make_current_bar_closed(self):
        frame = {"timestamps": [1_000_000]}
        self.assertFalse(last_bar_closed(frame, "5m", 1_299_999))
        self.assertTrue(last_bar_closed(frame, "5m", 1_300_000))

    def test_snapshot_rejects_open_higher_timeframe(self):
        snap = V2MarketSnapshot("BTC", 2_000_000, 2_000_000,
                                frames={"15m": {"timestamps": [1_500_000]}})
        self.assertEqual(snap.validate_closed_bars(), (False, "LOOKAHEAD_15m"))


class PortfolioStopRiskTests(unittest.TestCase):
    def test_total_and_cluster_stop_risk_are_hard_limits(self):
        positions = [{"symbol": "SOL", "direction": "LONG", "size_usd": 10_000,
                      "entry": 100, "sl": 98, "leverage": 10}]
        risk = compute_open_risk(positions, 10_000)
        self.assertAlmostEqual(risk["total_pct"], 0.02)
        new = {"symbol": "BONK", "direction": "LONG", "price": 1, "sl": 0.99}
        with patch.object(config, "MAX_CLUSTER_OPEN_RISK_PCT", 0.0125):
            ok, reason = check_portfolio_limits(positions, 10_000, new, 5_000)
        self.assertFalse(ok)
        self.assertIn("CLUSTER_RISK", reason)


class InstrumentPolicyTests(unittest.TestCase):
    def test_traditional_and_synthetic_are_excluded(self):
        self.assertFalse(crypto_perpetual_allowed("XAU"))
        self.assertFalse(crypto_perpetual_allowed("NVDA", {"assetClass": "equity"}))
        self.assertTrue(crypto_perpetual_allowed("BTC", {"instId": "BTC-USDT", "instType": "SWAP"}))


class FillAuditTests(unittest.TestCase):
    def test_actual_partial_fills_produce_vwap_role_and_latency(self):
        order = Order("CE1", "BTC", "BTC-USDT", "buy", "LONG", size=2,
                      decision_ts_ms=1_000, submitted_ts_ms=1_100)
        order.record_fill(1, 100, liquidity_role="maker", ts_ms=1_500)
        order.record_fill(1, 102, liquidity_role="taker", ts_ms=1_700)
        self.assertEqual(order.avg_fill_price, 101)
        self.assertEqual(order.liquidity_role, "mixed")
        self.assertEqual(order.fill_latency_ms, 700)
        self.assertEqual(len(order.fill_events), 2)


class ReplayExecutionTests(unittest.TestCase):
    def test_no_fill_before_accept_and_pessimistic_touch(self):
        engine = ReplayExecutionEngine(touch_model="pessimistic", submit_latency_ms=250)
        order = engine.submit(order_id="1", symbol="BTC", side="BUY", quantity=1,
                              decision_ts_ms=1_000, limit_price=100)
        self.assertFalse(engine.on_bar("1", ts_ms=1_100, open_=101, high=102, low=99, close=100))
        self.assertFalse(engine.on_bar("1", ts_ms=1_300, open_=101, high=101, low=100, close=100))
        fill = engine.on_bar("1", ts_ms=1_400, open_=101, high=101, low=99, close=100)
        self.assertEqual(fill["evidence"], "bar_cross")
        self.assertIn(order.state, {"PARTIAL", "FILLED"})

    def test_cancel_has_latency_and_can_be_filled_in_flight(self):
        engine = ReplayExecutionEngine(submit_latency_ms=0, cancel_latency_ms=250)
        order = engine.submit(order_id="1", symbol="ETH", side="BUY", quantity=1,
                              decision_ts_ms=1_000, limit_price=100)
        engine.request_cancel("1", 1_100)
        fill = engine.on_trade("1", ts_ms=1_200, price=99, quantity=1, aggressor="SELLER")
        self.assertTrue(fill)
        self.assertEqual(order.state, "FILLED")


class ResearchLayerTests(unittest.TestCase):
    def test_calibration_is_monotonic_and_profiles_rank_independently(self):
        rows = ([{"score": 0.2, "net_r": 1 if i % 4 == 0 else -1} for i in range(20)] +
                [{"score": 0.8, "net_r": 1 if i % 4 else -1} for i in range(20)])
        curve = calibrate_score_probability(rows, min_bin_n=20)
        self.assertLessEqual(curve[0]["prob_positive"], curve[-1]["prob_positive"])
        ranked = rank_candidates([
            {"symbol": "BONK", "expected_net_r": 1, "fill_probability": .8,
             "adverse_selection_score": .1, "distance_from_invalidation_atr": 2},
            {"symbol": "BTC", "expected_net_r": 1, "fill_probability": .8,
             "adverse_selection_score": .1, "distance_from_invalidation_atr": 2},
        ], open_positions=[{"symbol": "SOL"}])
        self.assertEqual(ranked[0]["symbol"], "BTC")

    def test_filter_attribution_requires_counterfactuals(self):
        report = marginal_filter_contribution([
            {"net_r": 1, "passed_filters": ["ADX"]},
            {"net_r": -1, "counterfactual_filters": ["ADX"]},
        ])
        self.assertEqual(report["ADX"]["marginal_r"], 2)


if __name__ == "__main__":
    unittest.main()
