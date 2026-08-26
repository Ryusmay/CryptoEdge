import unittest
from unittest.mock import patch

import config
from v2_profiles import profile_for, params_for, refresh_volume_ranks
from daytrading_engine_v2 import DayTradingEngineV2
from tests.test_daytrading_engine_v2 import (
    FakeFeeder, indicator, _up_swing_1h, _15m_trigger_series, _flat_ohlcv,
)


class TestProfileClassification(unittest.TestCase):
    def tearDown(self):
        refresh_volume_ranks([])

    def test_btc_always_major(self):
        self.assertEqual("major", profile_for("BTC"))

    def test_xau_is_metal(self):
        self.assertEqual("metal", profile_for("XAU-USDT"))

    def test_rank_top30_is_major(self):
        coins = [{"symbol": f"A{i}", "blofin_quote_volume_24h": 1_000_000 - i} for i in range(40)]
        coins.append({"symbol": "DOGE", "blofin_quote_volume_24h": 9_000_000})
        refresh_volume_ranks(coins)
        self.assertEqual("major", profile_for("DOGE"))
        self.assertEqual("alt", profile_for("A39"))

    def test_alt_params_tighter_than_major(self):
        alt = params_for("alt")
        maj = params_for("major")
        self.assertGreater(alt["swing_min_move_atr"], maj["swing_min_move_atr"])
        self.assertEqual(5.0, alt["margin_pct"])
        self.assertTrue(alt["skip_range"])
        self.assertTrue(alt["skip_4h_oppose"])
        self.assertFalse(params_for("metal")["use_4h_context"])
        self.assertFalse(params_for("metal")["use_5m_veto"])
        self.assertGreater(alt["slip_one_way"], maj["slip_one_way"])


class TestProfileEngine(unittest.TestCase):
    def setUp(self):
        self.feeder = FakeFeeder()
        self.engine = DayTradingEngineV2(self.feeder)
        refresh_volume_ranks([])

    def _set(self, symbol, **frames):
        b = self.feeder.blofin
        mapping = {"d1": "1D", "h4": "4H", "h1": "1H", "m15": "15m", "m5": "5m"}
        for k, bar in mapping.items():
            if k in frames:
                b.frames[(symbol, bar)] = frames[k]

    def test_btc_happy_is_major_7_5(self):
        swing = _up_swing_1h()
        m15 = _15m_trigger_series(110.0, "LONG")
        self._set("BTC", d1=_flat_ohlcv(260), h4=_flat_ohlcv(260), h1=swing, m15=m15)

        def ind(ohlcv, tf="1h"):
            return indicator("up", atr=1.0)

        with patch("daytrading_engine_v2.compute_indicators", side_effect=ind):
            result = self.engine.evaluate({"symbol": "BTC", "price": 112.0}, now_ts=10_000_000.0)
        self.assertIsNone(result.get("reject_reason"), result)
        self.assertEqual("major", result["v2_profile"])
        self.assertEqual(7.5, result["margin_pct"])
        self.assertIn("V2_PROFILE_MAJOR", result["reasons"])

    def test_alt_range_skips(self):
        swing = _up_swing_1h()
        m15 = _15m_trigger_series(110.0, "LONG")
        self._set("PEPE", d1=_flat_ohlcv(260), h4=_flat_ohlcv(260), h1=swing, m15=m15)

        def ind(ohlcv, tf="1h"):
            extra = {"adx": 12.0} if tf == "1h" else None
            return indicator("up", atr=1.0, extra=extra)

        with patch("daytrading_engine_v2.compute_indicators", side_effect=ind):
            result = self.engine.evaluate({"symbol": "PEPE", "price": 112.0}, now_ts=10_000_000.0)
        self.assertEqual("V2_RANGE_SKIP", result["reject_reason"])
        self.assertEqual("alt", result["v2_profile"])

    def test_alt_4h_oppose_skips(self):
        swing = _up_swing_1h()
        m15 = _15m_trigger_series(110.0, "LONG")
        self._set("PEPE", d1=_flat_ohlcv(260), h4=_flat_ohlcv(260), h1=swing, m15=m15)

        def ind(ohlcv, tf="1h"):
            if tf == "4h":
                return indicator("down", extra={"adx": 30.0})
            return indicator("up", atr=1.0, extra={"adx": 30.0})

        with patch("daytrading_engine_v2.compute_indicators", side_effect=ind):
            result = self.engine.evaluate({"symbol": "PEPE", "price": 112.0}, now_ts=10_000_000.0)
        self.assertEqual("V2_4H_CTX_OPPOSE", result["reject_reason"])

    def test_xau_is_disabled_until_metal_profile_passes_oos(self):
        swing = _up_swing_1h()
        m15 = _15m_trigger_series(110.0, "LONG")
        m5 = {
            "opens": [112.0] * 8, "closes": [110.0] * 8,
            "highs": [112.0] * 8, "lows": [110.0] * 8,
        }
        self._set("XAU", d1=_flat_ohlcv(260), h4=_flat_ohlcv(260), h1=swing, m15=m15, m5=m5)
        self._set("BTC", d1=_flat_ohlcv(260), h4=_flat_ohlcv(260), h1=swing, m15=m15, m5=m5)

        def ind(ohlcv, tf="1h"):
            if tf == "4h":
                return indicator("down", extra={"adx": 30.0})
            return indicator("up", atr=1.0, extra={"adx": 30.0})

        with patch("daytrading_engine_v2.compute_indicators", side_effect=ind):
            xau = self.engine.evaluate({"symbol": "XAU", "price": 112.0}, now_ts=10_000_000.0)
            btc = self.engine.evaluate({"symbol": "BTC", "price": 112.0}, now_ts=10_000_000.0)
        # Traditional/synthetic instruments are rejected before strategy
        # profile selection; they must never enter the crypto funnel.
        self.assertEqual("V2_SYMBOL_EXCLUDED", xau.get("reject_reason"), xau)
        self.assertEqual("metal", xau["v2_profile"])
        self.assertEqual("V2_5M_VETO", btc["reject_reason"])


if __name__ == "__main__":
    unittest.main()
