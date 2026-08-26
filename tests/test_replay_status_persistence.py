import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import engine_api


class ReplayStatusPersistenceTests(unittest.TestCase):
    def test_running_job_becomes_interrupted_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_module = root / "engine_api.py"
            state_path = root / "logs" / "replay_job_state.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(json.dumps({
                "ok": True,
                "running": True,
                "phase": "out_of_sample",
                "message": "Analiza OOS",
                "progress": 75,
                "started_at": 100.0,
            }), encoding="utf-8")

            with patch.object(engine_api, "__file__", str(fake_module)):
                job = engine_api.ReplayJob(SimpleNamespace())

            status = job.snapshot()
            self.assertFalse(status["running"])
            self.assertEqual(status["phase"], "interrupted")
            self.assertEqual(status["error"], "REPLAY_INTERRUPTED_BY_RESTART")
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["phase"], "interrupted")

    def test_latest_report_is_recovered_when_state_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_module = root / "engine_api.py"
            report_dir = root / "reports" / "replay"
            report_dir.mkdir(parents=True)
            report_path = report_dir / "daytrading_v2_portfolio_replay_90d_test.json"
            report_path.write_text(json.dumps({
                "symbols_downloaded": ["BTC", "ETH"],
                "portfolio": {
                    "in_sample": {"trades": 3, "win_rate": 0.5, "net_r": 0.4},
                    "out_of_sample": {
                        "trades": 2, "win_rate": 0.5, "net_r": 0.2,
                        "by_symbol": {"BTC": {"trades": 2, "net_r": 0.2}},
                    },
                },
            }), encoding="utf-8")

            with patch.object(engine_api, "__file__", str(fake_module)):
                job = engine_api.ReplayJob(SimpleNamespace())

            status = job.snapshot()
            self.assertEqual(status["phase"], "complete")
            self.assertEqual(status["progress"], 100)
            self.assertEqual(status["result"]["trades_oos"], 2)
            self.assertEqual(status["result"]["report_path"], str(report_path))

    def test_interrupted_job_keeps_latest_completed_report_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_module = root / "engine_api.py"
            state_path = root / "logs" / "replay_job_state.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(json.dumps({"running": True, "started_at": 100.0}), encoding="utf-8")
            report_dir = root / "reports" / "replay"
            report_dir.mkdir(parents=True)
            report_path = report_dir / "daytrading_v2_portfolio_replay_90d_test.json"
            report_path.write_text(json.dumps({
                "symbols_downloaded": ["BTC"],
                "portfolio": {
                    "in_sample": {"trades": 0},
                    "out_of_sample": {"trades": 2, "net_r": -0.4, "by_symbol": {}},
                },
            }), encoding="utf-8")

            with patch.object(engine_api, "__file__", str(fake_module)):
                status = engine_api.ReplayJob(SimpleNamespace()).snapshot()

            self.assertEqual(status["phase"], "interrupted")
            self.assertIn("ostatni raport zachowany", status["message"])
            self.assertEqual(status["result"]["trades_oos"], 2)


if __name__ == "__main__":
    unittest.main()
