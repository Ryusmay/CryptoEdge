import dataclasses
import math
import unittest

from cryptoedge.domain import (
    DecisionStatus, Direction, DomainEvent, EntryCandidate, EventType, Fill,
    MarketSnapshot, OrderIntent, OrderSide, OrderType, PositionSnapshot,
    RiskDecision, RiskStatus, StrategyDecision,
)
from v2_market_snapshot import V2MarketSnapshot


class DomainContractTests(unittest.TestCase):
    def test_snapshot_accepts_v2_and_is_deeply_immutable(self):
        old = V2MarketSnapshot("btc", 1000, 1250, frames={"15m": {"closes": [1, 2]}})
        snap = MarketSnapshot.from_legacy(old)
        self.assertEqual(snap.symbol, "BTC")
        self.assertEqual(snap.frames["15m"]["closes"], (1, 2))
        with self.assertRaises(TypeError):
            snap.frames["15m"]["closes"] = (3,)
        legacy = snap.to_legacy()
        self.assertEqual(legacy["frames"]["15m"]["closes"], [1, 2])

    def test_decision_candidate_requires_direction(self):
        with self.assertRaises(ValueError):
            StrategyDecision("BTC", DecisionStatus.CANDIDATE, 1)

    def test_signal_round_trip_preserves_legacy_extension_fields(self):
        signal = {"symbol": "eth", "direction": "LONG", "price": 2500.0,
                  "strength": 0.71, "engine": "daytrading_v2", "sl_price": 2475.0,
                  "tp1_price": 2525.0, "tp2_price": 2550.0, "market_regime": "TREND",
                  "expected_net_r": 0.42, "custom_indicator": {"ok": True}}
        candidate = EntryCandidate.from_legacy(signal, decision_ts_ms=100)
        rebuilt = candidate.to_legacy()
        self.assertEqual(candidate.direction, Direction.LONG)
        self.assertEqual(rebuilt["custom_indicator"], {"ok": True})
        self.assertEqual(rebuilt["tp2_price"], 2550.0)
        self.assertEqual(rebuilt["market_regime"], "TREND")

    def test_risk_decision_is_explicit_and_immutable(self):
        decision = RiskDecision.from_legacy(
            {"approved": True, "size_usd": 100, "risk_usd": 1, "margin_usd": 10},
            candidate_id="cand_1",
        )
        self.assertTrue(decision.approved)
        self.assertEqual(decision.status, RiskStatus.APPROVED)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            decision.size_usd = 500

    def test_order_adapter_matches_existing_order_shape(self):
        legacy = {"client_order_id": "CE1", "symbol": "BTC", "inst_id": "BTC-USDT",
                  "side": "buy", "direction": "LONG", "order_type": "limit", "size": 0.01,
                  "price": 100.0, "reduce_only": False, "leverage": 3, "margin_mode": "isolated"}
        intent = OrderIntent.from_legacy(legacy)
        self.assertEqual(intent.side, OrderSide.BUY)
        self.assertEqual(intent.order_type, OrderType.LIMIT)
        self.assertEqual(intent.to_legacy()["size"], 0.01)
        self.assertEqual(intent.to_legacy()["side"], "buy")

    def test_order_rejects_missing_direction_instead_of_late_serialization_error(self):
        with self.assertRaises(ValueError):
            OrderIntent.from_legacy({"symbol": "BTC", "side": "buy", "size": 1})

    def test_fill_accepts_replay_fill_event(self):
        fill = Fill.from_legacy({"ts_ms": 10, "quantity": 0.5, "price": 99,
                                 "liquidity_role": "maker", "evidence": "bar_cross"},
                                order_id="o1", client_order_id="ce1", symbol="sol")
        self.assertEqual(fill.symbol, "SOL")
        self.assertEqual(fill.to_legacy()["evidence"], "bar_cross")

    def test_position_adapter_supports_paper_shape(self):
        legacy = {"id": "p1", "symbol": "BTC", "direction": "SHORT", "status": "OPEN",
                  "entry_price": 100.0, "mark_price": 98.0, "size_usd": 200.0,
                  "margin": 20.0, "pnl": 4.0, "funding_paid": 0.1, "sl_price": 102.0}
        pos = PositionSnapshot.from_legacy(legacy)
        self.assertAlmostEqual(pos.quantity, 2.0)
        self.assertEqual(pos.to_legacy()["funding_paid"], 0.1)
        marked = pos.marked(97.0, 6.0)
        self.assertEqual(marked.mark_price, 97.0)
        self.assertEqual(pos.mark_price, 98.0)

    def test_event_carries_end_to_end_correlation_ids(self):
        event = DomainEvent(EventType.FILL, 123, "replay", {"price": 42},
                            session_id="s1", decision_id="d1", order_id="o1", position_id="p1")
        restored = DomainEvent.from_legacy(event.to_legacy())
        self.assertEqual(restored.order_id, "o1")
        self.assertEqual(restored.payload["price"], 42)

    def test_event_rejects_unknown_type(self):
        with self.assertRaises(ValueError):
            DomainEvent("BOGUS", 1, "test")

    def test_snapshot_closed_bar_validation_is_causal(self):
        open_bar = MarketSnapshot("BTC", 900_000, 1_000_000,
                                  frames={"15m": {"timestamps": [900_000]}})
        self.assertEqual(open_bar.validate_closed_bars(), (False, "LOOKAHEAD_15m"))
        closed_bar = MarketSnapshot("BTC", 900_000, 1_800_000,
                                    frames={"15m": {"timestamps": [900_000]}})
        self.assertEqual(closed_bar.validate_closed_bars(), (True, "OK"))

    def test_snapshot_explicit_close_timestamp_takes_precedence(self):
        snap = MarketSnapshot("BTC", 100, 200,
                              frames={"5m": {"timestamps": [0], "close_ts": [201]}})
        self.assertEqual(snap.validate_closed_bars(), (False, "LOOKAHEAD_5m"))

    def test_metadata_cannot_override_canonical_fields(self):
        decision = StrategyDecision("BTC", DecisionStatus.CANDIDATE, 10,
                                    Direction.LONG, 100.0,
                                    metadata={"direction": "SHORT", "price": 1.0})
        self.assertEqual(decision.to_legacy()["direction"], "LONG")
        self.assertEqual(decision.to_legacy()["price"], 100.0)
        risk = RiskDecision(RiskStatus.APPROVED, "c1", "OK", size_usd=10,
                            risk_usd=1, margin_usd=2,
                            metadata={"approved": False, "size_usd": 999})
        self.assertTrue(risk.to_legacy()["approved"])
        self.assertEqual(risk.to_legacy()["size_usd"], 10)

    def test_non_finite_financial_values_are_rejected(self):
        decision = StrategyDecision("BTC", DecisionStatus.CANDIDATE, 1,
                                    Direction.LONG, 100.0)
        for bad in (math.nan, math.inf, -math.inf):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    EntryCandidate(decision, bad, 90.0, (110.0,))
                with self.assertRaises(ValueError):
                    RiskDecision(RiskStatus.APPROVED, "c1", "OK", size_usd=bad)
                with self.assertRaises(ValueError):
                    Fill("o1", "c1", "BTC", 1.0, bad, 1)


if __name__ == "__main__":
    unittest.main()
