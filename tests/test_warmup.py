import unittest
from unittest.mock import MagicMock, patch

import config
import warmup
from warmup import WarmupController


class TestWarmupFullDurationByDefault(unittest.TestCase):
    """21.08.2026: rozruch ma trwac PELNE WARMUP_SECONDS (300s) domyslnie -
    wczesniejsze wyjscie (bylo: gotowe juz po t>=60s) jest teraz za flaga
    WARMUP_ALLOW_EARLY_READY, wylaczona domyslnie. Powod: Blofin dostawal
    nawal zapytan zaraz po cold-starcie, bo realny czas rozruchu wynosil
    ~60-90s zamiast nazwanych/logowanych 300s."""

    def setUp(self):
        self.wc = WarmupController()
        self.wc.active = True
        self.wc.started_at = 0.0

    def _run_tick_at(self, elapsed_s: float, ready_n: int = 20, feed_ok: bool = True, bucket_level: float = 0.9):
        fake_store = MagicMock()
        fake_store.ready_count.return_value = ready_n
        fake_store.snapshot.return_value = {"ws_alive": feed_ok, "ticker_age_s": 1.0}
        fake_store.candle_count.return_value = 999
        fake_bucket = MagicMock()
        fake_bucket.level.return_value = bucket_level
        fake_backfill = MagicMock()
        fake_backfill.pending.return_value = 0
        fake_backfill.done = 0
        with patch.object(warmup, "STORE", fake_store), \
             patch.object(warmup, "PUBLIC_BUCKET", fake_bucket), \
             patch.object(warmup, "BACKFILL", fake_backfill), \
             patch.object(self.wc, "elapsed", return_value=elapsed_s), \
             patch.object(self.wc, "drain", return_value=0):
            self.wc.candidates = ["BTC"] * 25
            return self.wc.tick(feeder=MagicMock(), coins=[])

    def test_stays_not_ready_at_60s_even_when_all_conditions_met(self):
        with patch.object(config, "WARMUP_ALLOW_EARLY_READY", False):
            self._run_tick_at(60.0, ready_n=25, feed_ok=True, bucket_level=0.9)
        self.assertFalse(self.wc.ready)
        self.assertTrue(self.wc.active)

    def test_stays_not_ready_at_150s_even_when_all_conditions_met(self):
        with patch.object(config, "WARMUP_ALLOW_EARLY_READY", False):
            self._run_tick_at(150.0, ready_n=25, feed_ok=True, bucket_level=0.9)
        self.assertFalse(self.wc.ready)

    def test_becomes_ready_after_full_300s_with_enough_pairs(self):
        with patch.object(config, "WARMUP_ALLOW_EARLY_READY", False), \
             patch.object(config, "WARMUP_SECONDS", 300):
            self._run_tick_at(300.0, ready_n=25, feed_ok=True, bucket_level=0.9)
        self.assertTrue(self.wc.ready)
        self.assertFalse(self.wc.active)

    def test_stays_in_gate_phase_past_300s_if_still_not_enough_pairs(self):
        with patch.object(config, "WARMUP_ALLOW_EARLY_READY", False), \
             patch.object(config, "WARMUP_SECONDS", 300), \
             patch.object(config, "WARMUP_MIN_PAIRS_READY", 20):
            self._run_tick_at(300.0, ready_n=1, feed_ok=True, bucket_level=0.9)
        self.assertFalse(self.wc.ready)
        self.assertEqual("gate", self.wc.phase)

    def test_early_ready_flag_restores_old_behavior_when_explicitly_enabled(self):
        with patch.object(config, "WARMUP_ALLOW_EARLY_READY", True), \
             patch.object(config, "WARMUP_MIN_PAIRS_READY", 20):
            self._run_tick_at(60.0, ready_n=25, feed_ok=True, bucket_level=0.9)
        self.assertTrue(self.wc.ready)

    def test_early_ready_flag_still_respects_bucket_and_feed_gates(self):
        with patch.object(config, "WARMUP_ALLOW_EARLY_READY", True), \
             patch.object(config, "WARMUP_MIN_PAIRS_READY", 20):
            self._run_tick_at(60.0, ready_n=25, feed_ok=True, bucket_level=0.10)
        self.assertFalse(self.wc.ready)


if __name__ == "__main__":
    unittest.main()
