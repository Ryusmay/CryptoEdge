"""ExecutionAssumptions: decyzja i adapter musza liczyc ta sama transakcje."""
import unittest

import config
from cryptoedge.domain import (
    DecisionStatus, Direction, ExecutionAssumptions, LiquidityRole, OrderType,
    StrategyDecision, execution_mismatches,
)
from expected_net_r import expected_net_r


MAKER, TAKER = 0.0002, 0.0006


class ExecutionAssumptionsContractTests(unittest.TestCase):
    def test_limit_signal_assumes_maker_entry_taker_exit(self):
        a = ExecutionAssumptions.from_signal({"limit_price": 99.5, "slip_rt": 0.001},
                                             maker_fee_rate=MAKER, taker_fee_rate=TAKER)
        self.assertEqual(a.order_type, OrderType.LIMIT)
        self.assertEqual(a.entry_role, LiquidityRole.MAKER)
        self.assertEqual(a.exit_role, LiquidityRole.TAKER)
        self.assertAlmostEqual(a.fee_round_trip_frac, MAKER + TAKER)
        self.assertEqual(a.slippage_round_trip_frac, 0.001)

    def test_market_signal_assumes_taker_both_ways(self):
        a = ExecutionAssumptions.from_signal({}, maker_fee_rate=MAKER, taker_fee_rate=TAKER)
        self.assertEqual(a.order_type, OrderType.MARKET)
        self.assertAlmostEqual(a.fee_round_trip_frac, 2 * TAKER)

    def test_unknown_fee_is_none_not_zero(self):
        a = ExecutionAssumptions.from_signal({"limit_price": 1.0})
        self.assertIsNone(a.fee_round_trip_frac)
        self.assertIsNone(a.to_legacy()["fee_round_trip_frac"])

    def test_post_only_rules(self):
        with self.assertRaises(ValueError):
            ExecutionAssumptions(order_type="MARKET", post_only=True)
        with self.assertRaises(ValueError):
            ExecutionAssumptions(order_type="LIMIT", post_only=True, entry_role="taker")
        ok = ExecutionAssumptions(order_type="LIMIT", post_only=True, entry_role="maker")
        self.assertTrue(ok.post_only)

    def test_rejects_invalid_numbers(self):
        for kwargs in ({"taker_fee_rate": -0.1}, {"fill_probability": 1.5},
                       {"latency_ms": float("nan")}, {"spread_frac": "x"}):
            with self.assertRaises(ValueError, msg=kwargs):
                ExecutionAssumptions(**kwargs)

    def test_mismatch_detection_ignores_unknowns(self):
        assumed = ExecutionAssumptions(order_type="LIMIT", entry_role="maker", exit_role="taker",
                                       maker_fee_rate=MAKER, taker_fee_rate=TAKER)
        same = ExecutionAssumptions(order_type="LIMIT", entry_role="maker", exit_role="taker",
                                    maker_fee_rate=MAKER, taker_fee_rate=TAKER)
        self.assertEqual(execution_mismatches(assumed, same), ())
        unknown = ExecutionAssumptions(order_type="LIMIT")
        self.assertEqual(execution_mismatches(assumed, unknown), ())
        taker = ExecutionAssumptions(order_type="LIMIT", entry_role="taker", exit_role="taker",
                                     maker_fee_rate=MAKER, taker_fee_rate=TAKER)
        found = execution_mismatches(assumed, taker)
        self.assertIn("ENTRY_ROLE maker!=taker", found)
        self.assertTrue(any(m.startswith("FEE_ROUND_TRIP") for m in found))

    def test_decision_envelope_round_trip_keeps_legacy_shape_when_unset(self):
        plain = StrategyDecision("BTC", DecisionStatus.CANDIDATE, 1_000, Direction.LONG, 100.0)
        legacy = plain.to_legacy()
        for key in ("strategy_version", "model_id", "confidence", "valid_until_ms",
                    "market_version", "execution_assumptions"):
            self.assertNotIn(key, legacy)

    def test_decision_envelope_round_trip_with_all_fields(self):
        execution = ExecutionAssumptions.from_signal({"limit_price": 99.0},
                                                     maker_fee_rate=MAKER, taker_fee_rate=TAKER)
        d = StrategyDecision("eth", DecisionStatus.CANDIDATE, 1_000, Direction.SHORT, 2500.0,
                             strategy_version="v2.73", model_id="lgbm-7", confidence=0.61,
                             valid_until_ms=901_000, market_version="15m:1000",
                             execution=execution)
        restored = StrategyDecision.from_legacy(d.to_legacy())
        self.assertEqual(restored.strategy_version, "v2.73")
        self.assertEqual(restored.model_id, "lgbm-7")
        self.assertEqual(restored.confidence, 0.61)
        self.assertEqual(restored.valid_until_ms, 901_000)
        self.assertEqual(restored.market_version, "15m:1000")
        self.assertEqual(restored.execution, execution)
        self.assertNotIn("execution_assumptions", restored.metadata)

    def test_decision_envelope_validation(self):
        with self.assertRaises(ValueError):
            StrategyDecision("BTC", DecisionStatus.NO_TRADE, 1_000, confidence=1.2)
        with self.assertRaises(ValueError):
            StrategyDecision("BTC", DecisionStatus.NO_TRADE, 1_000, valid_until_ms=999)


class DecisionVsPaperExecutionParityTests(unittest.TestCase):
    """Czy paper ksieguje wejscie tak, jak je wycenila decyzja?"""

    def _signal(self):
        return {"symbol": "BTC", "direction": "LONG", "price": 100.0, "strength": 0.75,
                "engine": "daytrading_v2", "sl_price": 98.0, "tp1_price": 103.0,
                "tp_price": 104.0, "limit_price": 100.0}

    def test_expected_net_r_prices_limit_entry_as_maker(self):
        # Kotwica: to jest zalozenie decyzji, ktore sprawdzamy nizej.
        signal = self._signal()
        expected_net_r(signal)
        assumed = ExecutionAssumptions.from_signal(
            signal, maker_fee_rate=config.MAKER_FEE, taker_fee_rate=config.TAKER_FEE)
        self.assertEqual(assumed.entry_role, LiquidityRole.MAKER)

    @unittest.expectedFailure
    def test_paper_limit_fill_is_booked_with_decision_fee_assumption(self):
        """ZNANA ROZBIEZNOSC (audyt jev-trader, docs/architecture/JEV_TRADER_AUDIT.md).

        paper_trader.process_limit_queue ustawia fill_kind="limit", a
        Position.entry_side uznaje za maker tylko maker/resting_limit/
        limit_maker - wejscie limitem jest ksiegowane jako taker (0.0012 RT
        wobec 0.0008 w expected_net_r i replay). Poprawka zmienia PnL paper,
        wiec wymaga bramki (AGENTS.md: gate before move) i nie wchodzi tutaj.
        Gdy zostanie naprawiona, ten test zacznie przechodzic i unittest
        zglosi "unexpected success" - wtedy zdjac dekorator.
        """
        from paper_trader import Position
        signal = self._signal()
        signal["fill_kind"] = "limit"  # dokladnie to, co ustawia process_limit_queue
        pos = Position(signal, size_usd=100.0, leverage=3)
        assumed = ExecutionAssumptions.from_signal(
            signal, maker_fee_rate=config.MAKER_FEE, taker_fee_rate=config.TAKER_FEE)
        realized = ExecutionAssumptions(
            order_type="LIMIT", entry_role=pos.entry_side, exit_role=pos.exit_side,
            maker_fee_rate=config.MAKER_FEE, taker_fee_rate=config.TAKER_FEE)
        self.assertEqual(execution_mismatches(assumed, realized), ())


if __name__ == "__main__":
    unittest.main()
