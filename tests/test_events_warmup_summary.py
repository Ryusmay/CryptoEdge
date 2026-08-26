import unittest

from pyside6_ui import engine_warmup_event


class EventsWarmupSummaryTests(unittest.TestCase):
    def test_warming_message_has_available_and_total_coin_counts(self):
        event = engine_warmup_event({
            "engine_warmup": {"active": True, "ready": False,
                              "available_coins": 128, "total_coins": 460},
        })
        self.assertEqual(event["event"], "Rozgrzewanie silnika · 128 z 460 monet dostępnych do analizy")

    def test_ready_message_reports_full_analysis_universe(self):
        event = engine_warmup_event({
            "engine_warmup": {"active": False, "ready": True,
                              "available_coins": 460, "total_coins": 460},
        })
        self.assertEqual(event["event"], "Silnik rozgrzany · 460 monet dostępnych do analizy")

    def test_bootstrap_warmup_never_claims_engine_ready(self):
        event = engine_warmup_event({
            "warmup": {"active": True, "ready": False, "ready_pairs": 20, "candidates": 460},
        })
        self.assertIn("20 z 460", event["event"])
        self.assertEqual(event["tag"], "WARMUP")

    def test_no_warmup_state_adds_no_synthetic_event(self):
        self.assertIsNone(engine_warmup_event({}))


if __name__ == "__main__":
    unittest.main()
