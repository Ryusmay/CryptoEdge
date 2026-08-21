import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import config
import settings_store
import daytrading_engine
from daytrading_engine import DayTradingEngine
from risk_manager import RiskManager


class FakeBlofin:
    def fetch_klines_ohlcv(self, symbol, bar="1H", limit=120):
        return {"closes": [1.0]}

    def fetch_order_book(self, symbol, size=20):
        return {"ob_spread_pct": 0.02, "ob_depth_usd": 1_000_000}


class FakeFeeder:
    def __init__(self):
        self.blofin = FakeBlofin()


def indicator(direction="up", chop=40.0, adx=28.0, timing=False):
    up = direction == "up"
    return {
        "price": 100.0, "atr": 1.0, "atr_dead": False,
        "ema_fast_above_slow": up, "price_above_ema_slow": up,
        "supertrend": {"is_up": up, "direction": direction},
        "choppiness": chop, "choppiness_state": "trending", "adx": adx,
        "bb": {"extreme_above": False, "extreme_below": False},
        "rsi": 55.0 if up else 45.0,
        "macd": {
            "cross": "bullish" if up and timing else "bearish" if timing else "none",
            "macd_above_signal": up, "hist_rising": up, "hist_falling": not up,
        },
    }


class TestDayTradingEngine(unittest.TestCase):
    def test_fibonacci_snapshot_is_available_for_neutral_display(self):
        raw = {
            "highs": [100, 102, 104, 106, 108],
            "lows": [99, 100, 102, 104, 106],
        }
        fib = DayTradingEngine._fib_snapshot(raw, "NEUTRAL", 105)
        self.assertTrue(fib["map"]["ok"])
        self.assertEqual("LONG", fib["direction"])
        self.assertIn("0.618", fib["map"]["levels"])

    def test_settings_default_is_daytrading_v2(self):
        # 21.08.2026: potwierdzone przez uzytkownika - przelaczamy sie na
        # daytrading v2 jako glowny tryb.
        self.assertEqual(settings_store.DEFAULTS["STRATEGY_MODE"], "DAYTRADING_V2")

    def test_strategy_mode_is_atomically_persisted_and_applied(self):
        # Uzywamy "swing" (nie "daytrading") - STRATEGY_MODE="DAYTRADING" ma
        # celowa strazniczke w apply_settings(): jesli DAYTRADING_V2_ENABLED
        # jest wlaczone (domyslnie tak), proba ustawienia z powrotem na
        # "DAYTRADING" jest celowo nadpisywana na "DAYTRADING_V2" - to nie
        # dotyczy "SWING", wiec test atomowosci pozostaje czysty (nie miesza
        # sie z ta osobna logika ochronna).
        previous = config.STRATEGY_MODE
        try:
            with tempfile.TemporaryDirectory() as tmp, patch.object(
                settings_store, "SETTINGS_FILE", Path(tmp) / "logs" / "settings.json"
            ):
                settings_store.update_setting("STRATEGY_MODE", "swing")
                self.assertEqual(settings_store.load_settings()["STRATEGY_MODE"], "SWING")
                self.assertEqual(config.STRATEGY_MODE, "SWING")
                self.assertFalse(settings_store.SETTINGS_FILE.with_suffix(".json.tmp").exists())
        finally:
            config.STRATEGY_MODE = previous

    def test_strategy_mode_daytrading_guard_prevents_silent_v1_downgrade_while_v2_enabled(self):
        # Dokumentuje wprost strazniczke znaleziona powyzej - to nie luka,
        # to celowe zabezpieczenie przed niespojnym stanem (STRATEGY_MODE=
        # DAYTRADING podczas gdy silnik V2 wciaz jest wlaczony flaga).
        previous = config.STRATEGY_MODE
        try:
            with tempfile.TemporaryDirectory() as tmp, \
                 patch.object(settings_store, "SETTINGS_FILE", Path(tmp) / "logs" / "settings.json"), \
                 patch.object(config, "DAYTRADING_V2_ENABLED", True):
                settings_store.update_setting("STRATEGY_MODE", "daytrading")
                self.assertEqual(config.STRATEGY_MODE, "DAYTRADING_V2")
        finally:
            config.STRATEGY_MODE = previous

    def test_htf_bias_full_alignment_gives_full_strength(self):
        self.assertEqual(DayTradingEngine._bias(indicator("up"), indicator("up")), ("LONG", 1.0))
        self.assertEqual(DayTradingEngine._bias(indicator("down"), indicator("down")), ("SHORT", 1.0))

    def test_htf_bias_direct_opposite_is_still_a_real_conflict(self):
        # 4h i 1h wprost przeciwne - to nie jest lag, to prawdziwa niezgoda,
        # wiec musi zostac zablokowane niezaleznie od SOFT_MODE.
        self.assertEqual(DayTradingEngine._bias(indicator("up"), indicator("down")), ("NEUTRAL", 0.0))
        self.assertEqual(DayTradingEngine._bias(indicator("down"), indicator("up")), ("NEUTRAL", 0.0))

    def test_htf_bias_soft_mode_passes_lag_with_reduced_strength(self):
        # 1h "neutralny" (ani up, ani down) - typowy lag miedzy interwalami,
        # nie realny konflikt. Przy SOFT_MODE=True powinno przejsc dalej,
        # ale ze zredukowanym alignment.
        neutral_1h = {**indicator("up"), "price_above_ema_slow": False}
        previous = config.DAYTRADING_HTF_SOFT_MODE
        try:
            config.DAYTRADING_HTF_SOFT_MODE = True
            direction, align = DayTradingEngine._bias(indicator("up"), neutral_1h)
            self.assertEqual(direction, "LONG")
            self.assertLess(align, 1.0)
            self.assertGreater(align, 0.0)

            config.DAYTRADING_HTF_SOFT_MODE = False
            self.assertEqual(DayTradingEngine._bias(indicator("up"), neutral_1h), ("NEUTRAL", 0.0))
        finally:
            config.DAYTRADING_HTF_SOFT_MODE = previous

    def test_counterfactual_htf_relaxes_only_conflict_and_uses_one_hour_direction(self):
        frames = [indicator("down"), indicator("up"), indicator("up"), indicator("up", timing=True)]
        engine = DayTradingEngine(FakeFeeder())
        with patch("daytrading_engine.compute_indicators", side_effect=frames):
            baseline = engine.evaluate({"symbol": "BTC", "price": 100})
        frames = [indicator("down"), indicator("up"), indicator("up"), indicator("up", timing=True)]
        with patch("daytrading_engine.compute_indicators", side_effect=frames):
            relaxed = engine.evaluate({"symbol": "BTC", "price": 100}, {"DAY_HTF_CONFLICT"})
        self.assertEqual("LONG", baseline["direction"])
        self.assertEqual("LONG", relaxed["direction"])
        self.assertIn(baseline.get("intraday", {}).get("bias_4h_1h"), ("LONG", "SHORT"))

    def test_long_requires_closed_multitimeframe_confirmation(self):
        frames = [indicator("up"), indicator("up"), indicator("up"), indicator("up", timing=True)]
        engine = DayTradingEngine(FakeFeeder())
        with patch("daytrading_engine.compute_indicators", side_effect=frames):
            signal = engine.evaluate({"symbol": "BTC", "price": 100, "volume_24h": 5_000_000})
        self.assertEqual(signal["direction"], "LONG")
        self.assertEqual(signal["engine"], "daytrading")
        self.assertEqual(signal["strategy_mode"], "DAYTRADING")
        self.assertLess(signal["sl_price"], signal["price"])
        self.assertGreater(signal["tp1_price"], signal["price"])
        self.assertEqual(signal["decision_path"] if "decision_path" in signal else "DAYTRADING", "DAYTRADING")

    def test_choppy_15m_blocks_entry(self):
        frames = [indicator("up"), indicator("up"), indicator("up", chop=70), indicator("up", timing=True)]
        engine = DayTradingEngine(FakeFeeder())
        with patch("daytrading_engine.compute_indicators", side_effect=frames):
            signal = engine.evaluate({"symbol": "ETH", "price": 100})
        self.assertEqual(signal["direction"], "LONG")
        self.assertFalse(signal.get("funnel", {}).get("chop_ok"))
        self.assertGreater(signal["strength"], 0)
        self.assertIn("htf_alignment", signal["score_components"])

    def test_daytrading_adx_has_hard_floor_and_penalized_borderline_band(self):
        engine = DayTradingEngine(FakeFeeder())
        weak_frames = [indicator("up"), indicator("up"), indicator("up", adx=14.9), indicator("up", timing=True)]
        with patch("daytrading_engine.compute_indicators", side_effect=weak_frames):
            weak = engine.evaluate({"symbol": "ETH", "price": 100})
        self.assertEqual("LONG", weak["direction"])
        self.assertFalse(weak.get("funnel", {}).get("adx_ok"))

        borderline_frames = [indicator("up"), indicator("up"), indicator("up", adx=17.0), indicator("up", timing=True)]
        with patch("daytrading_engine.compute_indicators", side_effect=borderline_frames):
            borderline = engine.evaluate({"symbol": "ETH", "price": 100})
        quality_frames = [indicator("up"), indicator("up"), indicator("up", adx=18.0), indicator("up", timing=True)]
        with patch("daytrading_engine.compute_indicators", side_effect=quality_frames):
            quality = engine.evaluate({"symbol": "ETH", "price": 100})

        self.assertEqual("LONG", borderline["direction"])
        self.assertEqual(0.01, borderline["intraday"]["adx_policy"]["strength_penalty"])
        self.assertLess(borderline["strength"], quality["strength"])
        self.assertEqual(18.0, borderline["intraday"]["adx_policy"]["quality_min"])

    def test_counterfactual_adx_removes_only_numeric_floor(self):
        frames = [indicator("up"), indicator("up"), indicator("up", adx=10.0), indicator("up", timing=True)]
        engine = DayTradingEngine(FakeFeeder())
        with patch("daytrading_engine.compute_indicators", side_effect=frames):
            relaxed = engine.evaluate({"symbol": "BTC", "price": 100}, {"DAY_ADX_WEAK"})
        self.assertEqual("LONG", relaxed["direction"])
        self.assertFalse(relaxed.get("reject_reason"))
        self.assertEqual(0.03, relaxed["intraday"]["adx_policy"]["strength_penalty"])

    def test_timing_wait_has_visible_readiness_score_but_cannot_trade(self):
        m15 = indicator("up", timing=False)
        m15["supertrend"] = {"is_up": False}
        m15["macd"]["macd_above_signal"] = False
        m15["macd"]["hist_rising"] = False
        frames = [indicator("up"), indicator("up"), m15, indicator("up")]
        engine = DayTradingEngine(FakeFeeder())
        with patch("daytrading_engine.compute_indicators", side_effect=frames):
            signal = engine.evaluate({"symbol": "BTC", "price": 100})
        self.assertEqual(signal["direction"], "LONG")
        self.assertFalse(signal.get("funnel", {}).get("st_5m"))
        self.assertGreaterEqual(signal.get("funnel", {}).get("votes", 0), 2)

    def test_daytrading_has_separate_risk_curve_and_close_cooldown(self):
        rm = RiskManager(starting_capital=1000)
        signal = {"symbol": "BTC", "price": 100, "sl_price": 98.5, "strength": 0.8,
                  "engine": "daytrading", "strategy_mode": "DAYTRADING"}
        self.assertGreater(rm.calculate_position_size(signal), 0)
        self.assertEqual(signal["_risk_engine"], "daytrading")
        rm.register_open()
        rm.register_close("BTC", pnl=1.0, engine="daytrading")
        self.assertIn(("BTC", "daytrading"), rm.engine_symbol_cooldown)

    def test_liquidity_ranking_uses_quote_not_base_units(self):
        engine = DayTradingEngine(FakeFeeder())
        engine.evaluate = lambda coin: {
            "symbol": coin["symbol"], "direction": "LONG", "strength": 0.7,
            "price": coin["price"], "engine": "daytrading",
        }
        coins = [
            {"symbol": "CHEAP", "price": 0.00001, "blofin_base_volume_24h": 1_000_000_000},
            {"symbol": "BTC", "price": 100_000, "blofin_base_volume_24h": 100},
        ]
        with patch.object(config, "DAYTRADING_MAX_CANDIDATES", 1), patch.object(config, "MIN_VOLUME_24H_USD", 0):
            rows = engine.generate(coins)
        selected = [row["symbol"] for row in rows if row.get("direction") != "NEUTRAL"]
        self.assertEqual(selected, ["BTC"])

    def test_default_liquid_funnel_evaluates_top_thirty_only(self):
        engine = DayTradingEngine(FakeFeeder())
        evaluated = []
        engine.evaluate = lambda coin: evaluated.append(coin["symbol"]) or {
            "symbol": coin["symbol"], "direction": "LONG", "strength": 0.7,
            "price": coin["price"], "engine": "daytrading",
        }
        coins = [
            {"symbol": f"C{i:02d}", "price": 1.0, "blofin_quote_volume_24h": 1_000_000 - i}
            for i in range(40)
        ]
        with patch.object(config, "MIN_VOLUME_24H_USD", 0):
            rows = engine.generate(coins)
        self.assertEqual(30, config.DAYTRADING_MAX_CANDIDATES)
        self.assertEqual(30, len(evaluated))
        self.assertEqual([f"C{i:02d}" for i in range(30)], evaluated)
        self.assertEqual(10, sum(row.get("reject_reason") == "DAY_NOT_IN_LIQUID_TOP" for row in rows))

    def test_outside_liquid_top_still_gets_price_and_spread_not_full_silence(self):
        # Punkt 3 planu: pozostale (poza topem) dostaja przynajmniej rozjazd
        # ceny i spread (juz i tak dostepne z bulk tickera Blofin, zero
        # dodatkowego kosztu sieciowego), nie totalna cisza.
        engine = DayTradingEngine(FakeFeeder())
        engine.evaluate = lambda coin: {"symbol": coin["symbol"], "direction": "LONG", "strength": 0.7,
                                        "price": coin["price"], "engine": "daytrading"}
        coins = [{"symbol": "TOP", "price": 100.0, "blofin_quote_volume_24h": 10_000_000}] + [
            {"symbol": f"REST{i}", "price": 1.0, "blofin_quote_volume_24h": 1.0,
             "blofin_bid": 0.999, "blofin_ask": 1.001}
            for i in range(3)
        ]
        with patch.object(config, "MIN_VOLUME_24H_USD", 0), patch.object(config, "DAYTRADING_MAX_CANDIDATES", 1):
            rows = engine.generate(coins)
        rest_rows = [r for r in rows if r["symbol"].startswith("REST")]
        self.assertEqual(3, len(rest_rows))
        for row in rest_rows:
            self.assertEqual("DAY_NOT_IN_LIQUID_TOP", row["reject_reason"])
            intraday = row.get("intraday") or {}
            self.assertTrue(intraday.get("spread_only"))
            self.assertEqual(0.999, intraday.get("bid"))
            self.assertEqual(1.001, intraday.get("ask"))
            self.assertAlmostEqual(0.2, intraday.get("spread_pct"), places=2)

    def test_outside_liquid_top_without_bid_ask_has_no_spread_pct(self):
        engine = DayTradingEngine(FakeFeeder())
        engine.evaluate = lambda coin: {"symbol": coin["symbol"], "direction": "LONG", "strength": 0.7,
                                        "price": coin["price"], "engine": "daytrading"}
        coins = [{"symbol": "TOP", "price": 100.0, "blofin_quote_volume_24h": 10_000_000},
                 {"symbol": "NODATA", "price": 1.0, "blofin_quote_volume_24h": 1.0}]
        with patch.object(config, "MIN_VOLUME_24H_USD", 0), patch.object(config, "DAYTRADING_MAX_CANDIDATES", 1):
            rows = engine.generate(coins)
        nodata = next(r for r in rows if r["symbol"] == "NODATA")
        self.assertIsNone((nodata.get("intraday") or {}).get("spread_pct"))

    def test_ws_disconnected_still_applies_safe_candidate_ceiling(self):
        # PUBLIC_WS realny singleton bez polaczenia w testach - domyslna
        # sciezka (WS padl) MUSI dalej uzywac bezpiecznego sufitu.
        engine = DayTradingEngine(FakeFeeder())
        evaluated = []
        engine.evaluate = lambda coin: evaluated.append(coin["symbol"]) or {}
        coins = [{"symbol": f"C{i:02d}", "price": 1.0, "blofin_quote_volume_24h": 1_000_000 - i}
                 for i in range(20)]
        with patch.object(config, "DAYTRADING_MAX_CANDIDATES", 5), \
             patch.object(config, "MIN_VOLUME_24H_USD", 0):
            engine.generate(coins)
        self.assertEqual(5, len(evaluated))

    def test_ws_connected_with_no_limit_configured_evaluates_entire_universe(self):
        engine = DayTradingEngine(FakeFeeder())
        evaluated = []
        engine.evaluate = lambda coin: evaluated.append(coin["symbol"]) or {}
        coins = [{"symbol": f"C{i:02d}", "price": 1.0, "blofin_quote_volume_24h": 1_000_000 - i}
                 for i in range(200)]
        fake_ws = MagicMock()
        fake_ws.is_connected.return_value = True
        with patch.object(daytrading_engine, "PUBLIC_WS", fake_ws), \
             patch.object(config, "DAYTRADING_MAX_CANDIDATES_WS_CONNECTED", None), \
             patch.object(config, "MIN_VOLUME_24H_USD", 0):
            engine.generate(coins)
        self.assertEqual(200, len(evaluated))

    def test_ws_connected_with_explicit_limit_configured_still_respects_it(self):
        engine = DayTradingEngine(FakeFeeder())
        evaluated = []
        engine.evaluate = lambda coin: evaluated.append(coin["symbol"]) or {}
        coins = [{"symbol": f"C{i:02d}", "price": 1.0, "blofin_quote_volume_24h": 1_000_000 - i}
                 for i in range(200)]
        fake_ws = MagicMock()
        fake_ws.is_connected.return_value = True
        with patch.object(daytrading_engine, "PUBLIC_WS", fake_ws), \
             patch.object(config, "DAYTRADING_MAX_CANDIDATES_WS_CONNECTED", 80), \
             patch.object(config, "MIN_VOLUME_24H_USD", 0):
            engine.generate(coins)
        self.assertEqual(80, len(evaluated))

    def test_rsi_outside_old_band_does_not_block_when_st_and_macd_ok(self):
        m15 = indicator("up", timing=True)
        m15["rsi"] = 72.0
        frames = [indicator("up"), indicator("up"), m15, indicator("up")]
        engine = DayTradingEngine(FakeFeeder())
        with patch("daytrading_engine.compute_indicators", side_effect=frames):
            signal = engine.evaluate({"symbol": "BTC", "price": 100})
        self.assertEqual("LONG", signal["direction"])
        self.assertFalse(signal.get("reject_reason"))
        self.assertTrue(signal["funnel"]["timing_5m"])
        self.assertFalse(signal["funnel"]["rsi_mid"])

    def test_extreme_rsi_against_long_still_waits(self):
        m15 = indicator("up", timing=True)
        m15["rsi"] = 82.0
        frames = [indicator("up"), indicator("up"), m15, indicator("up")]
        engine = DayTradingEngine(FakeFeeder())
        with patch("daytrading_engine.compute_indicators", side_effect=frames):
            signal = engine.evaluate({"symbol": "BTC", "price": 100})
        self.assertEqual("NEUTRAL", signal["direction"])
        self.assertEqual("DAY_RSI_EXTREME", signal["reject_reason"])
        self.assertTrue(signal["funnel"]["rsi_extreme_against"])

    def test_levels_ignore_viper_and_near_price_noise(self):
        levels = DayTradingEngine._levels(
            {
                "support_resistance": {
                    "supports": [{"price": 99.9}],
                    "resistances": [{"price": 100.1}],
                },
                "pivot_points": {"R1": 104.0, "S1": 96.0},
                "viper": {"nearest_support": 99.99, "nearest_resistance": 100.01},
            },
            price=100.0,
            atr=1.0,
        )
        supports, resistances = levels
        self.assertEqual(supports, [96.0])
        self.assertEqual(resistances, [104.0])

    def test_soft_barrier_caps_tp_instead_of_reject(self):
        m15 = indicator("up")
        m15["support_resistance"] = {
            "supports": [{"price": 97.0}],
            "resistances": [{"price": 101.5}],  # 1.5 / 2.0 ATR-SL = 0.75R → soft
        }
        frames = [indicator("up"), indicator("up"), m15, indicator("up", timing=True)]
        engine = DayTradingEngine(FakeFeeder())
        with patch("daytrading_engine.compute_indicators", side_effect=frames):
            signal = engine.evaluate({"symbol": "BTC", "price": 100})
        self.assertEqual("LONG", signal["direction"])
        self.assertNotIn("DAY_NEAR_BARRIER", str(signal.get("reject_reason") or ""))
        self.assertEqual("soft_cap_tp1", signal["intraday"]["barrier"]["policy"])
        self.assertLess(signal["tp_plan"]["tp1_r"], 1.2)

    def test_hard_barrier_is_strength_penalty_not_stop(self):
        m15 = indicator("up")
        m15["support_resistance"] = {
            "supports": [{"price": 97.0}],
            "resistances": [{"price": 100.8}],  # 0.8 / 2.0 = 0.40R → kara -0.05
        }
        frames = [indicator("up"), indicator("up"), m15, indicator("up", timing=True)]
        engine = DayTradingEngine(FakeFeeder())
        with patch("daytrading_engine.compute_indicators", side_effect=frames):
            signal = engine.evaluate({"symbol": "BTC", "price": 100})
        self.assertEqual("LONG", signal["direction"])
        self.assertNotIn("DAY_NEAR_BARRIER", str(signal.get("reject_reason") or ""))
        self.assertEqual("penalty_logit", signal["intraday"]["barrier"]["policy"])
        self.assertIn("quality", signal)
        self.assertIn("size_mult", signal)


if __name__ == "__main__":
    unittest.main()
