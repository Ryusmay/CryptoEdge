import unittest

import config
from daytrading_backtester import v2_max_bars_5m, v2_unclog_bars_5m
from daytrading_engine_v2 import DayTradingEngineV2
from v2_profiles import params_for, profile_for


class TestHardTimeStopBars(unittest.TestCase):
    def test_10h_is_120_five_minute_bars(self):
        self.assertEqual(120, v2_max_bars_5m())

    def test_v2_time_horizon_10h_is_120_five_minute_bars(self):
        self.assertEqual(120, v2_unclog_bars_5m())
        self.assertEqual(10.0, config.DAYTRADING_V2_TIME_STOP_HOURS)
        self.assertEqual(0.35, config.DAYTRADING_V2_TIME_STOP_MIN_R)
        self.assertEqual(0.5, config.DAYTRADING_V2_UNCLOG_SKIP_MFE_R)


class Test15mReclaimBars(unittest.TestCase):
    def _frame(self, last_two):
        n = 20
        closes = [100.0] * n
        highs = [101.0] * n
        lows = [99.0] * n
        lows[-5] = 98.0
        highs[-5] = 100.5
        closes[-2], closes[-1] = last_two
        return {"closes": closes, "highs": highs, "lows": lows}

    def test_one_bar_reclaim_not_enough(self):
        ok = DayTradingEngineV2._check_15m_trigger(
            self._frame((99.0, 100.6)), 99.5, 101.0, "LONG",
            lookback=12, reclaim_level=100.0, reclaim_bars=2,
        )
        self.assertFalse(ok)

    def test_two_bar_reclaim_passes(self):
        ok = DayTradingEngineV2._check_15m_trigger(
            self._frame((100.2, 100.6)), 99.5, 101.0, "LONG",
            lookback=12, reclaim_level=100.0, reclaim_bars=2,
        )
        self.assertTrue(ok)


class TestMetalOff(unittest.TestCase):
    def test_xau_is_metal_and_not_traded(self):
        self.assertEqual("metal", profile_for("XAU"))
        self.assertFalse(params_for("metal")["trade"])
        self.assertFalse(config.DAYTRADING_V2_METAL_TRADE)

    def test_tp_ladder_and_protective_entry_sl(self):
        self.assertEqual(2.0, config.DAYTRADING_V2_TP1_R)
        self.assertEqual(3.0, config.DAYTRADING_V2_TP2_R_FALLBACK)
        self.assertTrue(config.DAYTRADING_V2_ENTRY_SL)
        self.assertTrue(config.DAYTRADING_V2_BE_AFTER_TP1)
        self.assertEqual(10.0, config.DAYTRADING_V2_HARD_TIME_STOP_HOURS)


if __name__ == "__main__":
    unittest.main()
