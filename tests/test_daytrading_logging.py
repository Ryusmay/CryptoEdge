import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import logger as logger_module


class TestDaytradingLogging(unittest.TestCase):
    def test_history_keeps_eligible_neutral_readiness_rows(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(logger_module, "LOGS_DIR", Path(tmp)), patch.object(config, "STRATEGY_MODE", "DAYTRADING"):
            log = logger_module.BotLogger()
            log.log_signals([
                {"symbol": "BTC", "direction": "NEUTRAL", "strength": 0.49, "price": 100.0,
                 "strategy_mode": "DAYTRADING", "reject_reason": "DAY_5M_TIMING_WAIT"},
                {"symbol": "ALT", "direction": "NEUTRAL", "strength": 0.05, "price": 1.0,
                 "strategy_mode": "DAYTRADING", "reject_reason": "DAY_NOT_IN_LIQUID_TOP"},
            ])
            with log.signals_file.open(encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "BTC")
        self.assertIn("MODE=DAYTRADING", rows[0]["reasons"])
        self.assertIn("REJECT=DAY_5M_TIMING_WAIT", rows[0]["reasons"])
        self.assertIn("+00:00", rows[0]["timestamp"])


if __name__ == "__main__":
    unittest.main()
