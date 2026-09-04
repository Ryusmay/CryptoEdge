import unittest

import config
from daytrading_backtester import v2_max_bars_5m, v2_unclog_bars_5m
from daytrading_engine_v2 import DayTradingEngineV2
from v2_profiles import params_for, profile_for


class TestHardTimeStopBars(unittest.TestCase):
    # 04.09.2026, v20.66.0: soft 24h, hard 48h. Wczesniej OBA mialy 10.0 i to
    # nie byla decyzja, tylko pozostalosc po cofnietym eksperymencie z
    # 01.09.2026 (ustawiono wtedy hard 48.0, zapalilo 37 testow, wrocono do
    # 10.0 - bo rebaseline byl drozszy niz revert, a nie bo 10h wygralo pomiar).
    # Zrownanie softa z hardem sprawialo, ze soft nigdy nie mial szansy
    # zadzialac: obie decyzje zapadaly w tym samym barze.
    #
    # Nazwy i asercje nie niosa przeliczonej liczby swiec: godzina jest
    # asertowana wprost (zmiana ma byc glosna), a liczba barow liczona z niej
    # w tescie.
    BARS_PER_HOUR_5M = 12

    def test_hard_time_stop_hours_match_bar_count(self):
        hours = config.DAYTRADING_V2_HARD_TIME_STOP_HOURS
        self.assertEqual(48.0, hours)
        self.assertEqual(int(hours * self.BARS_PER_HOUR_5M), v2_max_bars_5m())

    def test_soft_time_stop_jest_ostrzejszy_niz_hard(self):
        # Rownosc soft == hard to stan, ktory raz juz przeszedl niezauwazony
        # i wyprodukowal 560 transakcji wychodzacych co do bara w tym samym
        # miejscu. Test pilnuje RELACJI, nie konkretnych liczb, wiec przezyje
        # kazde przyszle strojenie i padnie dokladnie na tym jednym bledzie.
        self.assertLess(config.DAYTRADING_V2_TIME_STOP_HOURS,
                        config.DAYTRADING_V2_HARD_TIME_STOP_HOURS,
                        "soft time-stop musi byc ostrzejszy niz hard, inaczej "
                        "nigdy nie zadziala i kazda transakcja wychodzi na hardzie")

    def test_unclog_horizon_matches_bar_count(self):
        hours = config.DAYTRADING_V2_TIME_STOP_HOURS
        self.assertEqual(24.0, hours)
        self.assertEqual(int(hours * self.BARS_PER_HOUR_5M), v2_unclog_bars_5m())
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
        # Wartosci wrocily po cofnieciu strojenia z 01.09.2026. Sama drabina
        # TP byla w dekompozycji praktycznie neutralna (+0.45R), wiec nie ona
        # przesadzila o cofnieciu calosci.
        self.assertEqual(2.0, config.DAYTRADING_V2_TP1_R)
        self.assertEqual(3.0, config.DAYTRADING_V2_TP2_R_FALLBACK)
        self.assertTrue(config.DAYTRADING_V2_ENTRY_SL)
        self.assertTrue(config.DAYTRADING_V2_BE_AFTER_TP1)
        self.assertFalse(config.DAYTRADING_V2_BE_AFTER_TP2)
        self.assertEqual(48.0, config.DAYTRADING_V2_HARD_TIME_STOP_HOURS)


if __name__ == "__main__":
    unittest.main()
