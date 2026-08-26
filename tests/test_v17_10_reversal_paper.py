import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import decision_telemetry
from decision_telemetry import decision_snapshot
from regime_model import RegimeEngine
from reversal_engine import merge_trend_and_reversal
from shadow_mode import should_execute


def confirmed_reversal():
    return {
        "symbol": "BTC", "direction": "SHORT", "engine": "reversal",
        "setup": "reversal_confirmed", "confirmation_status": "CONFIRMED",
        "confirmation_count": 3, "strength": 0.8, "reversal_score": 0.8,
        "price": 100.0, "sl_price": 105.0,
        "rr_tp1": 1.0, "rr_tp2": 2.0,
        "tp_plan": {"frac_tp1": 0.25, "frac_tp2": 0.35, "frac_trail": 0.40},
        "order_book": {"ob_spread_pct": 0.02, "ob_depth_usd": 1_000_000},
        "_planned_notional": 100.0,
    }


class TestControlledReversalPaper(unittest.TestCase):
    def test_confirmed_positive_net_r_executes_in_paper(self):
        signal = confirmed_reversal()
        with patch.object(config, "PAPER_TRADING", True), patch.object(
            config, "REVERSAL_PAPER_EXECUTION_ENABLED", True
        ):
            self.assertTrue(should_execute(signal))
        self.assertGreater(signal["expected_net_r"], config.REVERSAL_MIN_EXPECTED_NET_R)

    def test_reversal_never_uses_paper_trader_in_live(self):
        signal = confirmed_reversal()
        with patch.object(config, "PAPER_TRADING", False):
            self.assertFalse(should_execute(signal))
        self.assertEqual(signal["paper_reversal_block_reason"], "REVERSAL_LIVE_DISABLED")

    def test_reversal_stays_paper_even_if_live_execution_on(self):
        signal = confirmed_reversal()
        with patch.object(config, "PAPER_TRADING", False), patch.object(
            config, "LIVE_EXECUTION_ENABLED", True
        ), patch.object(config, "REVERSAL_PAPER_EXECUTION_ENABLED", True):
            self.assertFalse(should_execute(signal))
        self.assertEqual(signal["paper_reversal_block_reason"], "REVERSAL_LIVE_DISABLED")

    def test_bypass_status_with_sufficient_confirmations_now_executes(self):
        # 21.08.2026: shadow mode ma wejsc do realnych testow (decyzja
        # uzytkownika) - status BYPASS z wystarczajaca liczba potwierdzen
        # (>= REVERSAL_PAPER_MIN_CONFIRMATIONS) jest teraz akceptowany,
        # nie automatycznie odrzucany jak wczesniej.
        signal = confirmed_reversal()
        signal["confirmation_status"] = "BYPASS"  # confirmation_count=3 z fixture, minimum domyslnie 1
        with patch.object(config, "PAPER_TRADING", True), \
             patch.object(config, "REVERSAL_PAPER_EXECUTION_ENABLED", True):
            self.assertTrue(should_execute(signal))

    def test_confirmations_below_minimum_still_blocked_regardless_of_status(self):
        # Bezwarunkowy prog (niezalezny od confirmation_status) - "BYPASS"
        # czy "CONFIRMED" nie pomoze, jesli confirmation_count < minimum.
        signal = confirmed_reversal()
        signal["confirmation_status"] = "BYPASS"
        signal["confirmation_count"] = 0
        with patch.object(config, "PAPER_TRADING", True), \
             patch.object(config, "REVERSAL_PAPER_EXECUTION_ENABLED", True), \
             patch.object(config, "REVERSAL_PAPER_MIN_CONFIRMATIONS", 1):
            self.assertFalse(should_execute(signal))
        self.assertEqual(signal["paper_reversal_block_reason"], "REVERSAL_CONFIRMATIONS_LOW")


class TestPanicAndTelemetry(unittest.TestCase):
    def test_panic_records_exact_trigger(self):
        detail = {"atr_ratio": 2.1, "atr_percentile": 97.0, "realized_vol": 4.0}
        self.assertEqual(RegimeEngine()._classify(detail), "PANIC")
        self.assertIn("ATR_RATIO", detail["panic_trigger"])
        self.assertIn("ATR_PERCENTILE", detail["panic_trigger"])

    def test_atr_percentile_alone_does_not_create_panic(self):
        detail = {"atr_ratio": 1.629, "atr_percentile": 100.0, "realized_vol": 0.969, "btc_24h": 0.0}
        self.assertNotEqual(RegimeEngine()._classify(detail), "PANIC")
        self.assertIn("UNCONFIRMED", detail["panic_percentile_unconfirmed"])

    def test_identical_reject_is_deduplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decisions.jsonl"
            signal = {"symbol": "BTC", "direction": "LONG", "engine": "trend", "market_regime": "PANIC"}
            decision_telemetry._RECENT_REJECTIONS.clear()
            with patch.object(config, "DECISION_TELEMETRY_PATH", str(path)), patch.object(
                config, "DECISION_TELEMETRY_DEDUPE_SECONDS", 300
            ):
                decision_snapshot(signal.copy(), "REJECT", "REGIME_PANIC_TREND")
                decision_snapshot(signal.copy(), "REJECT", "REGIME_PANIC_TREND")
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 1)


class TestReversalMergeInvariant(unittest.TestCase):
    def test_confirmed_reversal_overrides_rejected_same_direction_trend(self):
        trend = {
            "symbol": "H", "direction": "LONG", "engine": "trend", "strength": 0.7,
            "market_regime": "PANIC", "reject_reason": "REGIME_PANIC_TREND", "reasons": [],
        }
        reversal = confirmed_reversal()
        reversal.update({"symbol": "H", "direction": "LONG", "strength": 0.59, "market_regime": "PANIC"})
        merged = merge_trend_and_reversal([trend], [reversal])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["engine"], "reversal")
        self.assertIn("CONFIRMED_REVERSAL_OVERRIDES_TREND", merged[0]["reasons"])


if __name__ == "__main__":
    unittest.main()
