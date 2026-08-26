import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
from decision_telemetry import decision_snapshot, _append
from risk_manager import RiskManager


class TestDecisionTelemetry(unittest.TestCase):
    def test_default_runtime_path_is_redirected_inside_test_process(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(config, "DECISION_TELEMETRY_PATH", "logs/decision_telemetry.jsonl"), \
             patch("decision_telemetry.tempfile.gettempdir", return_value=tmp):
            _append({"event": "TEST_ISOLATION"})
            isolated = Path(tmp) / "cryptoedge-tests" / "logs" / "decision_telemetry.jsonl"
            self.assertTrue(isolated.exists())
    def test_reject_snapshot_contains_execution_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decisions.jsonl"
            signal = {
                "symbol": "BTC", "direction": "LONG", "engine": "trend",
                "strength": 0.72, "price": 100.0, "expected_net_r": 0.4,
                "liquidity_bucket": "LIQUID",
                "_ob_impact": {"vwap": 100.1, "impact_pct": 0.1, "fill_ratio": 1.0},
            }
            with patch.object(config, "DECISION_TELEMETRY_PATH", str(path)):
                decision_id = decision_snapshot(signal, "REJECT", "TEST")
            row = json.loads(path.read_text(encoding="utf-8").strip())
            self.assertEqual(row["decision_id"], decision_id)
            self.assertEqual(row["reason"], "TEST")
            self.assertEqual(row["impact_pct"], 0.1)
            self.assertEqual(row["strategy_mode"], str(config.STRATEGY_MODE).upper())

    def test_outside_liquid_universe_reject_is_not_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decisions.jsonl"
            signal = {"symbol": "ALT", "direction": "NEUTRAL", "engine": "daytrading"}
            with patch.object(config, "DECISION_TELEMETRY_PATH", str(path)):
                decision_snapshot(signal, "REJECT", "DAY_NOT_IN_LIQUID_TOP")
            self.assertFalse(path.exists())


class TestEngineCooldown(unittest.TestCase):
    def test_trend_loss_does_not_block_reversal_engine(self):
        risk = RiskManager(1000.0)
        risk.register_close("BTC", pnl=-1.0, engine="trend")
        base = {"symbol": "BTC", "direction": "LONG", "strength": 0.9,
                "price": 100.0, "sl_price": 98.0, "market_regime": "TREND"}
        trend_ok, trend_reason = risk.can_open_position({**base, "engine": "trend"})
        reversal_ok, reversal_reason = risk.can_open_position({**base, "engine": "reversal"})
        self.assertFalse(trend_ok)
        self.assertIn("ENGINE_COOLDOWN", trend_reason)
        self.assertNotIn("ENGINE_COOLDOWN", reversal_reason)

    def test_third_loss_escalates_engine_cooldown(self):
        risk = RiskManager(1000.0)
        for _ in range(3):
            risk.register_close("ETH", pnl=-1.0, engine="reversal")
        until = risk.engine_symbol_cooldown[("ETH", "reversal")]
        remaining = (until - __import__("datetime").datetime.now()).total_seconds() / 60
        self.assertGreater(remaining, 70)


if __name__ == "__main__":
    unittest.main()
