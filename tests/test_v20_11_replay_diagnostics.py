import unittest
from types import SimpleNamespace

from historical_replay import _v2_diagnostics


class ReplayDiagnosticsTests(unittest.TestCase):
    def test_lifecycle_excursions_and_segments_are_reported(self):
        trade = SimpleNamespace(
            direction="LONG", realised_r=1.2, mae_r=0.4, mfe_r=2.1,
            tp1_done=True, tp2_done=True, remaining=0.4, exit_reason="sl",
            fill_kind="limit", entry_i=10, exit_i=22,
            v2_profile="major", market_regime="TREND",
        )
        result = _v2_diagnostics([("BTC", trade)])
        self.assertEqual(1, result["lifecycle"]["tp1_hits"])
        self.assertEqual(1, result["lifecycle"]["tp2_hits"])
        self.assertEqual(1, result["lifecycle"]["trailing_exits_after_tp2"])
        self.assertEqual(2.1, result["excursions"]["mfe_p50_r"])
        self.assertEqual(1, result["by_profile"]["major"]["trades"])
        self.assertEqual(1, result["by_regime"]["TREND"]["trades"])
        self.assertEqual(1, result["by_direction"]["LONG"]["trades"])


if __name__ == "__main__":
    unittest.main()
