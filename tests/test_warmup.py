import time
import unittest
from unittest.mock import MagicMock, patch

import config
import warmup
from market_store import STORE
from warmup import WarmupController


class TestWarmupFullDurationByDefault(unittest.TestCase):
    """Rozruch trwa pelne WARMUP_SECONDS (90s). Early-ready tylko za flaga."""

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

    def test_stays_not_ready_at_45s_even_when_all_conditions_met(self):
        with patch.object(config, "WARMUP_ALLOW_EARLY_READY", False):
            self._run_tick_at(45.0, ready_n=25, feed_ok=True, bucket_level=0.9)
        self.assertFalse(self.wc.ready)

    def test_becomes_ready_after_full_90s_with_enough_pairs(self):
        with patch.object(config, "WARMUP_ALLOW_EARLY_READY", False), \
             patch.object(config, "WARMUP_SECONDS", 90):
            self._run_tick_at(90.0, ready_n=25, feed_ok=True, bucket_level=0.9)
        self.assertTrue(self.wc.ready)
        self.assertFalse(self.wc.active)

    def test_becomes_ready_after_full_300s_when_duration_patched(self):
        with patch.object(config, "WARMUP_ALLOW_EARLY_READY", False), \
             patch.object(config, "WARMUP_SECONDS", 300):
            self._run_tick_at(300.0, ready_n=25, feed_ok=True, bucket_level=0.9)
        self.assertTrue(self.wc.ready)
        self.assertFalse(self.wc.active)

    def test_stays_in_gate_phase_past_duration_if_still_not_enough_pairs(self):
        with patch.object(config, "WARMUP_ALLOW_EARLY_READY", False), \
             patch.object(config, "WARMUP_SECONDS", 90), \
             patch.object(config, "WARMUP_MIN_PAIRS_READY", 20):
            self._run_tick_at(90.0, ready_n=1, feed_ok=True, bucket_level=0.9)
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

    def test_tick_survives_missing_store_snapshot(self):
        fake_store = MagicMock(spec=["ready_count", "candle_count"])
        fake_store.ready_count.return_value = 20
        with patch.object(warmup, "STORE", fake_store), \
             patch.object(self.wc, "elapsed", return_value=30.0), \
             patch.object(self.wc, "drain", return_value=0):
            self.wc.candidates = ["BTC"] * 25
            st = self.wc.tick(feeder=MagicMock(), coins=[])
        self.assertFalse(self.wc.ready)
        self.assertIn("phase", st)

    def test_default_early_ready_fires_at_60s(self):
        self.assertTrue(config.WARMUP_ALLOW_EARLY_READY)
        self._run_tick_at(60.0, ready_n=25, feed_ok=True, bucket_level=0.9)
        self.assertTrue(self.wc.ready)

    def test_seed_from_disk_fills_store_without_rest(self):
        import json
        import tempfile
        from pathlib import Path
        import disk_cache
        now_ms = int(time.time() * 1000)
        frame = {
            # The newest 4H bar must already be closed.
            "timestamps": [now_ms - 3600_000 * i for i in range(83, 3, -1)],
            "opens": [1.0] * 80, "highs": [1.0] * 80, "lows": [1.0] * 80,
            "closes": [1.0] * 80, "volumes": [1.0] * 80,
        }
        wc = WarmupController()
        # Isolate this test from candles populated by earlier runtime tests.
        STORE.ohlcv.pop("BTC", None)
        with tempfile.TemporaryDirectory() as td:
            cache_dir = Path(td)
            key = "ohlcv_BTC-USDT_4H_260"
            (cache_dir / f"{key}.json").write_text(
                json.dumps({"ts": time.time(), "data": frame}), encoding="utf-8"
            )
            with patch.object(disk_cache, "CACHE_DIR", cache_dir):
                n = wc._seed_from_disk(["BTC"])
        self.assertEqual(1, n)
        self.assertGreaterEqual(STORE.candle_count("BTC", "4H"), 80)
        STORE.ohlcv.pop("BTC", None)

    def test_seed_from_disk_also_fills_blofin_memory_cache(self):
        import json
        import tempfile
        from pathlib import Path
        import disk_cache
        now_ms = int(time.time() * 1000)
        frame = {
            "timestamps": [now_ms - 3600_000 * i for i in range(80, 0, -1)],
            "opens": [1.0] * 80, "highs": [1.0] * 80, "lows": [1.0] * 80,
            "closes": [1.0] * 80, "volumes": [1.0] * 80,
        }
        wc = WarmupController()
        blofin = MagicMock()
        blofin.ohlc_cache = {}
        feeder = MagicMock()
        feeder.blofin = blofin
        with tempfile.TemporaryDirectory() as td:
            cache_dir = Path(td)
            key = "ohlcv_ETH-USDT_1H_260"
            (cache_dir / f"{key}.json").write_text(
                json.dumps({"ts": time.time(), "data": frame}), encoding="utf-8"
            )
            with patch.object(disk_cache, "CACHE_DIR", cache_dir):
                n = wc._seed_from_disk(["ETH"], feeder=feeder)
        self.assertEqual(1, n)
        self.assertIn("ohlcv_ETH-USDT_1H_260", blofin.ohlc_cache)
        STORE.ohlcv.pop("ETH", None)

    def test_enqueue_backfill_puts_4h_before_1h_per_symbol(self):
        from backfill_queue import BackfillQueue
        q = BackfillQueue()
        wc = WarmupController()
        fake_store = MagicMock()
        fake_store.candle_count.return_value = 0
        with patch.object(warmup, "STORE", fake_store), patch.object(warmup, "BACKFILL", q):
            wc.enqueue_backfill(["BTC", "ETH"])
        jobs = list(q.q)
        self.assertEqual(jobs[0][0], "BTC")
        self.assertEqual(jobs[0][1], "4H")
        self.assertEqual(jobs[1][1], "1H")
        self.assertEqual(jobs[2][1], "15m")
        self.assertEqual(jobs[3][0], "ETH")
        self.assertEqual(jobs[3][1], "4H")
        self.assertEqual(6, len(jobs))


class TestWarmupGateMatchesV2Switch(unittest.TestCase):
    """Warmup i generate_signals musza uzywac tej samej bramki V2.
    MODE=DAYTRADING_V2 bez flagi = V2 bez 5 min warmup (bug do 19.25.11)."""

    def test_mode_v2_without_flag_still_warms_up(self):
        with patch.object(config, "WARMUP_ENABLED", True), \
             patch.object(config, "DAYTRADING_V2_ENABLED", False), \
             patch.object(config, "STRATEGY_MODE", "DAYTRADING_V2"):
            self.assertTrue(config.daytrading_v2_active())
            self.assertTrue(warmup.warmup_applies())

    def test_flag_alone_warms_up_even_if_mode_is_v1(self):
        with patch.object(config, "WARMUP_ENABLED", True), \
             patch.object(config, "DAYTRADING_V2_ENABLED", True), \
             patch.object(config, "STRATEGY_MODE", "DAYTRADING"):
            self.assertTrue(config.daytrading_v2_active())
            self.assertTrue(warmup.warmup_applies())

    def test_v1_only_skips_warmup(self):
        with patch.object(config, "WARMUP_ENABLED", True), \
             patch.object(config, "DAYTRADING_V2_ENABLED", False), \
             patch.object(config, "STRATEGY_MODE", "DAYTRADING"):
            self.assertFalse(config.daytrading_v2_active())
            self.assertFalse(warmup.warmup_applies())

    def test_warmup_disabled_overrides_v2(self):
        with patch.object(config, "WARMUP_ENABLED", False), \
             patch.object(config, "DAYTRADING_V2_ENABLED", True), \
             patch.object(config, "STRATEGY_MODE", "DAYTRADING_V2"):
            self.assertTrue(config.daytrading_v2_active())
            self.assertFalse(warmup.warmup_applies())

    def test_app_loop_uses_shared_warmup_applies(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
        self.assertIn("from warmup import WARMUP, warmup_applies", src)
        self.assertIn("if warmup_applies():", src)
        self.assertIn("persist_market_preview", src)
        self.assertIn("[Warmup] tick:", src)
        self.assertIn("[App] persist po bledzie:", src)


if __name__ == "__main__":
    unittest.main()
