# ============================================================
# v19.25.2 — A1–A7: flagi UI na V2, stały margin 7.5%,
# brak dummy strength, reversal bez RSI, skip SL$ portfolio.
# ============================================================

import unittest
from unittest.mock import patch

import config
from risk_manager import RiskManager
from reversal_engine import (
    detect_confirmation,
    detect_exhaustion,
    detect_extreme,
    _hydrate_1h_from_feeder,
    generate_reversal_signals,
    score_reversal_candidate,
)
from signal_engine import apply_v2_ui_gates


def v2_signal(**overrides):
    sig = {
        "symbol": "AAA",
        "direction": "LONG",
        "engine": "daytrading_v2",
        "strategy_mode": "DAYTRADING_V2",
        "strength": 0.75,
        "hide_strength": True,
        "price": 100.0,
        "sl_price": 97.0,
        "market_regime": "TREND",
        "risk_pct_of_capital": 0.5,
        "setup": "htf_swing_retest",
    }
    sig.update(overrides)
    return sig


class TestApplyV2UiGates(unittest.TestCase):
    def test_pump_chase_blocks_long(self):
        s = v2_signal(change_24h=30.0)
        with patch.object(config, "BLOCK_PUMP_CHASE_PCT", 22.0):
            apply_v2_ui_gates([s], [{"symbol": "AAA", "change_24h": 30.0}], "TREND")
        self.assertEqual(s["direction"], "NEUTRAL")
        self.assertEqual(s["reject_reason"], "TREND_BLOCK_PUMP_CHASE")

    def test_dump_chase_blocks_short(self):
        s = v2_signal(direction="SHORT", change_24h=-30.0)
        with patch.object(config, "BLOCK_PUMP_CHASE_PCT", 22.0):
            apply_v2_ui_gates([s], [{"symbol": "AAA", "change_24h": -30.0}], "TREND")
        self.assertEqual(s["direction"], "NEUTRAL")
        self.assertEqual(s["reject_reason"], "TREND_BLOCK_DUMP_CHASE")

    def test_zero_pump_limit_disables_chase_gate(self):
        s = v2_signal(change_24h=40.0)
        with patch.object(config, "BLOCK_PUMP_CHASE_PCT", 0.0):
            apply_v2_ui_gates([s], [{"symbol": "AAA", "change_24h": 40.0}], "TREND")
        self.assertEqual(s["direction"], "LONG")
        self.assertFalse(s.get("reject_reason"))

    def test_range_block_when_flag_on(self):
        s = v2_signal()
        with patch.object(config, "BLOCK_RANGE_REGIME", True):
            apply_v2_ui_gates([s], [{"symbol": "AAA"}], "RANGE")
        self.assertEqual(s["direction"], "NEUTRAL")
        self.assertEqual(s["reject_reason"], "REGIME_RANGE_BLOCK")

    def test_range_pass_when_flag_off(self):
        s = v2_signal()
        with patch.object(config, "BLOCK_RANGE_REGIME", False):
            apply_v2_ui_gates([s], [{"symbol": "AAA"}], "RANGE")
        self.assertEqual(s["direction"], "LONG")
        self.assertFalse(s.get("reject_reason"))

    def test_thin_order_book_blocks(self):
        s = v2_signal()
        coin = {"symbol": "AAA", "order_book": {"ob_thin": True, "ob_depth_usd": 100}}
        with patch.object(config, "BLOCK_OB_THIN", True), \
             patch.object(config, "OB_MIN_DEPTH_USD", 3500):
            apply_v2_ui_gates([s], [coin], "TREND")
        self.assertEqual(s["direction"], "NEUTRAL")
        self.assertTrue(str(s.get("reject_reason") or "").startswith("OB_THIN"))

    def test_source_divergence_does_not_block(self):
        s = v2_signal()
        with patch("signal_engine.analyze_source_divergence", return_value={"max_diff_pct": 4.2, "warning": True}):
            apply_v2_ui_gates([s], [{"symbol": "AAA"}], "TREND")
        self.assertEqual(s["direction"], "LONG")
        self.assertFalse(s.get("reject_reason"))

    def test_leaves_non_v2_untouched(self):
        s = {"symbol": "AAA", "engine": "trend", "direction": "LONG", "change_24h": 40.0}
        apply_v2_ui_gates([s], [{"symbol": "AAA", "change_24h": 40.0}], "TREND")
        self.assertEqual(s["direction"], "LONG")
        self.assertFalse(s.get("reject_reason"))


class TestV2RiskGates(unittest.TestCase):
    def setUp(self):
        self.rm = RiskManager(starting_capital=1000.0)
        self.rm._positions_ref = []

    def _open_ok_patches(self):
        return (
            patch.object(config, "MAX_CORRELATED_RISK", 1.0),
            patch.object(config, "REQUIRE_LIQ_BEYOND_SL", False),
            patch.object(config, "OB_IMPACT_FILTER", False),
            patch.object(config, "PORTFOLIO_RISK_ENABLED", False),
            patch.object(config, "DYN_CORR_FILTER", False),
            patch.object(config, "BTC_CORRELATION_HARD", False),
        )

    def test_v2_skips_min_signal_strength(self):
        sig = v2_signal(strength=0.10)
        patches = self._open_ok_patches()
        for p in patches:
            p.start()
        try:
            ok, why = self.rm.can_open_position(sig)
        finally:
            for p in patches:
                p.stop()
        self.assertNotIn("Za slaby", why or "")
        self.assertTrue(ok, why)

    def test_v2_skips_require_primary_trend_filter(self):
        sig = v2_signal()
        sig.pop("strategy", None)
        patches = self._open_ok_patches() + (patch.object(config, "REQUIRE_PRIMARY_STRATEGY", True),)
        for p in patches:
            p.start()
        try:
            ok, why = self.rm.can_open_position(sig)
        finally:
            for p in patches:
                p.stop()
        self.assertFalse(str(why or "").startswith("STRAT_"))
        self.assertTrue(ok, why)

    def test_v2_obeys_sl_dollar_portfolio_risk(self):
        self.rm._portfolio_open_risk_ok = lambda s: (False, "PORTFOLIO_RISK(3.1%>2.5%)")
        patches = self._open_ok_patches()
        for p in patches:
            p.start()
        try:
            ok, why = self.rm.can_open_position(v2_signal(sl_price=96.0))
        finally:
            for p in patches:
                p.stop()
        self.assertFalse(ok)
        self.assertEqual(why, "PORTFOLIO_RISK(3.1%>2.5%)")

    def test_trend_still_blocked_by_sl_dollar_portfolio_risk(self):
        self.rm._portfolio_open_risk_ok = lambda s: (False, "PORTFOLIO_RISK(3.1%>2.5%)")
        sig = {
            "symbol": "BTC", "direction": "LONG", "strength": 0.80,
            "price": 100.0, "sl_price": 98.0, "market_regime": "TREND",
            "engine": "trend",
            "strategy": {"pass": True, "direction": "LONG"},
        }
        patches = self._open_ok_patches()
        for p in patches:
            p.start()
        try:
            ok, why = self.rm.can_open_position(sig)
        finally:
            for p in patches:
                p.stop()
        self.assertFalse(ok)
        self.assertEqual(why, "PORTFOLIO_RISK(3.1%>2.5%)")

    def test_default_risk_sl_mode_is_not_fixed_margin(self):
        self.assertFalse(bool(config.DAYTRADING_V2_MARGIN_STRENGTH_SCALED))
        self.assertAlmostEqual(float(config.DAYTRADING_V2_MARGIN_PCT_FIXED), 7.5)
        size = self.rm.calculate_position_size(v2_signal(sl_price=99.5))
        self.assertEqual(config.DAYTRADING_V2_SIZE_MODE, "risk_sl")
        self.assertAlmostEqual(size, 800.0, places=2)

    def test_v2_panic_does_not_use_dummy_strength_gate(self):
        sig = v2_signal(market_regime="PANIC", strength=0.10)
        patches = self._open_ok_patches()
        for p in patches:
            p.start()
        try:
            ok, why = self.rm.can_open_position(sig)
        finally:
            for p in patches:
                p.stop()
        self.assertNotIn("REGIME_PANIC_TREND", why or "")
        self.assertNotIn("REGIME_PANIC_DAY", why or "")
        self.assertTrue(ok, why)
        self.assertGreaterEqual(float(sig.get("_size_mult") or 1.0), 1.0)

    def test_trend_can_open_in_panic_without_extra_strength(self):
        sig = {
            "symbol": "BTC", "direction": "LONG", "strength": 0.50,
            "price": 100.0, "sl_price": 98.0, "market_regime": "PANIC",
            "engine": "trend",
            "strategy": {"pass": True, "direction": "LONG"},
        }
        patches = self._open_ok_patches() + (
            patch.object(config, "USE_EXPECTED_NET_R_FILTER", False),
        )
        for p in patches:
            p.start()
        try:
            ok, why = self.rm.can_open_position(sig)
        finally:
            for p in patches:
                p.stop()
        self.assertTrue(ok, why)
        self.assertNotIn("REGIME_PANIC_TREND", why or "")


class TestReversalWithoutTickerRsi(unittest.TestCase):
    def test_extreme_at_12_pct_not_18(self):
        coin = {"symbol": "ZZZ", "price": 5.0, "change_24h": -13.0, "change_1h": 0.2}
        ext = detect_extreme(coin)
        self.assertIsNotNone(ext)
        self.assertEqual(ext["side"], "DOWN")

    def test_exhaustion_proxy_without_rsi(self):
        coin = {"symbol": "ZZZ", "price": 5.0, "change_24h": -15.0, "change_1h": 0.4}
        ext = detect_extreme(coin)
        exh = detect_exhaustion(coin, ext)
        self.assertIsNotNone(exh)
        self.assertIn("EXH_NO_RSI_USE_1H", exh["tags"])

    def test_single_1h_turn_is_not_enough_confirmation(self):
        coin = {"symbol": "ZZZ", "price": 5.0, "change_24h": -15.0, "change_1h": 0.5}
        ext = detect_extreme(coin)
        exh = detect_exhaustion(coin, ext)
        conf = detect_confirmation(coin, ext, exh, regime="RANGE")
        self.assertIsNone(conf)

    def test_hydrate_attaches_rsi_and_structure(self):
        n = 120
        closes = [100.0 - i * 0.5 for i in range(n)]
        highs = [c + 0.4 for c in closes]
        lows = [c - 0.4 for c in closes]
        ohlcv = {"opens": closes, "highs": highs, "lows": lows, "closes": closes}

        class Feed:
            def fetch_klines_ohlcv(self, symbol, bar="1H", limit=120):
                return ohlcv

        class Feeder:
            blofin = Feed()

        coin = {"symbol": "ZZZ", "price": closes[-1], "change_24h": -16.0}
        self.assertTrue(_hydrate_1h_from_feeder(coin, Feeder()))
        self.assertIsNotNone(coin.get("rsi"))
        self.assertTrue(coin.get("highs"))
        self.assertTrue(coin.get("lows"))


    def test_single_stall_is_not_enough_confirmation(self):
        coin = {"symbol": "ZZZ", "price": 5.0, "change_24h": -15.0, "change_1h": 0.0}
        ext = detect_extreme(coin)
        exh = detect_exhaustion(coin, ext)
        conf = detect_confirmation(coin, ext, exh, regime="RANGE")
        self.assertIsNone(conf)

    def test_generate_rejects_candidate_without_two_confirmations(self):
        coins = [{"symbol": "ZZZ", "price": 5.0, "change_24h": -16.0, "change_1h": 0.3}]
        out = generate_reversal_signals(coins, regime="RANGE")
        self.assertEqual(out, [])

    def test_score_candidate_rejects_single_factor_reversal(self):
        coin = {"symbol": "ZZZ", "price": 5.0, "change_24h": -16.0, "change_1h": 0.3}
        sig = score_reversal_candidate(coin, regime="RANGE")
        self.assertIsNone(sig)


class TestTenV2PositionsFitGrossCap(unittest.TestCase):
    def test_ten_v2_slots_under_gross_cap(self):
        from portfolio_risk import check_portfolio_limits
        equity = 100.0
        positions = []
        for i in range(10):
            ok, why = check_portfolio_limits(
                positions, equity,
                new_signal={"symbol": f"S{i}", "direction": "LONG", "engine": "daytrading_v2"},
                new_notional=75.0,  # 7.5% margin * x10 on $100 = $75 wait that's margin
            )
            # notional = margin * lev = 7.5 * 10 = 75? On $100 equity 7.5% margin = $7.5, *10 = $75 notional
            self.assertTrue(ok, f"pos {i}: {why}")
            positions.append({"symbol": f"S{i}", "direction": "LONG", "size_usd": 75.0, "leverage": 10})
        ok11, why11 = check_portfolio_limits(
            positions, equity,
            new_signal={"symbol": "S10", "direction": "LONG"},
            new_notional=75.0,
        )
        self.assertFalse(ok11)
        self.assertTrue("GROSS" in why11 or "LEVERAGE" in why11 or "NET" in why11, why11)


class TestScoreLabelSource(unittest.TestCase):
    def test_pyside_hides_v2_dummy(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "pyside6_ui.py").read_text(encoding="utf-8")
        self.assertIn("def score_label(row: dict) -> str:", src)
        self.assertIn('return "PASS"', src)
        self.assertIn("score = score_label(row)", src)
        self.assertIn("V2: PASS/— zamiast dummy paska", src)


if __name__ == "__main__":
    unittest.main()
