import csv
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import engine_api


class CurrentSessionEventsTests(unittest.TestCase):
    def test_old_rows_remain_on_disk_but_are_hidden_from_ui(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "logs").mkdir()
            path = root / "logs" / "bot_log.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["timestamp", "event", "symbol", "direction", "price"])
                writer.writeheader()
                writer.writerow({"timestamp": "2026-01-01T00:00:00+00:00", "event": "OPEN", "symbol": "OLD", "price": "1"})
                writer.writerow({"timestamp": "2026-08-25T00:00:01+00:00", "event": "OPEN", "symbol": "NEW", "price": "2"})
            session = datetime(2026, 8, 25, tzinfo=timezone.utc).timestamp()
            with patch.object(engine_api, "__file__", str(root / "engine_api.py")):
                rows = engine_api._events(SimpleNamespace(session_started_at=session), limit=10)
            self.assertEqual(1, len(rows))
            self.assertIn("NEW", rows[0]["text"])
            self.assertNotIn("OLD", rows[0]["text"])


if __name__ == "__main__":
    unittest.main()
