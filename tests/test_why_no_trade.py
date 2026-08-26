import unittest

from why_taxonomy import why_bucket


class TestWhyBucket(unittest.TestCase):
    def test_v2_funnel_is_setup(self):
        self.assertEqual("setup", why_bucket("V2_NO_15M_TRIGGER"))
        self.assertEqual("setup", why_bucket("V2_NO_IMPULSE_SWING"))
        self.assertEqual("setup", why_bucket("V2_1H_NO_BIAS"))
        self.assertEqual("setup", why_bucket("V2_4H_NO_BIAS"))
        self.assertEqual("setup", why_bucket("V2_RANGE_SKIP"))
        self.assertEqual("setup", why_bucket("V2_4H_CTX_OPPOSE"))
        self.assertEqual("setup", why_bucket("TREND_BLOCK_PUMP_CHASE"))

    def test_v2_data_not_liquidity(self):
        self.assertEqual("data", why_bucket("V2_STALE_KLINES_1H(90s)"))
        self.assertEqual("data", why_bucket("V2_NOT_IN_LIQUID_TOP"))
        self.assertEqual("data", why_bucket("OB_THIN"))

    def test_pause_is_timing(self):
        self.assertEqual("timing", why_bucket("V2_LOSS_STREAK_PAUSE"))
        self.assertEqual("timing", why_bucket("V2_SWING_ALREADY_TRADED"))

    def test_cluster_is_risk(self):
        self.assertEqual("risk", why_bucket("CLUSTER_EXPOSURE"))
        self.assertEqual("risk", why_bucket("V2_FUNDING_EXTREME"))
