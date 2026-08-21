import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import control_center
from entry_reservations import EntryReservationBook


class TestControlCenter(unittest.TestCase):
    def test_safe_config_snapshot_excludes_credentials(self):
        import config
        old = {
            "BLOFIN_API_KEY": getattr(config, "BLOFIN_API_KEY", None),
            "BLOFIN_API_SECRET": getattr(config, "BLOFIN_API_SECRET", None),
            "BLOFIN_API_PASSPHRASE": getattr(config, "BLOFIN_API_PASSPHRASE", None),
        }
        try:
            config.BLOFIN_API_KEY = "must-not-export"
            config.BLOFIN_API_SECRET = "must-not-export"
            config.BLOFIN_API_PASSPHRASE = "must-not-export"
            snapshot = control_center.safe_config_snapshot()
            self.assertFalse(any("KEY" in key or "SECRET" in key or "PASSPHRASE" in key for key in snapshot))
            self.assertNotIn("must-not-export", repr(snapshot))
        finally:
            for key, value in old.items():
                setattr(config, key, value)

    def test_rejection_summary_aggregates_reason_family(self):
        out = control_center.rejection_summary([
            {"reason": "WEAK(0.40)"}, {"reason": "WEAK(0.41)"}, {"reason": "OB_THIN"}
        ])
        self.assertEqual(out["total"], 3)
        self.assertEqual(out["reasons"][0], {"reason": "WEAK", "count": 2})

    def test_watchdog_blocks_stale_running_engine(self):
        rt = SimpleNamespace(engine_enabled=True, last_heartbeat=1.0, reconciler=None, protection=None)
        with patch("control_center.time.time", return_value=1000.0):
            out = control_center.readiness(rt, {"sources": "BloFin Binance CoinGecko", "mode": "DEMO"})
        self.assertEqual(out["overall"], "BLOCKED")

    def test_session_export_contains_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(control_center, "BASE", Path(tmp)):
                target = control_center.export_paper_session({"mode": "DEMO", "risk": {"capital": 100}})
            self.assertTrue(target.exists())
            import zipfile
            with zipfile.ZipFile(target) as archive:
                self.assertIn("session_state.json", archive.namelist())


class TestEntryReservations(unittest.TestCase):
    def test_reservations_prevent_slot_overbooking(self):
        book = EntryReservationBook(ttl_seconds=30)
        self.assertTrue(book.reserve("BTC", "trend", 0, 1)[0])
        self.assertFalse(book.reserve("ETH", "trend", 0, 1)[0])
        book.release("BTC", "trend")
        self.assertTrue(book.reserve("ETH", "trend", 0, 1)[0])


if __name__ == "__main__":
    unittest.main()
