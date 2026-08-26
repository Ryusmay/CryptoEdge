import unittest

from daytrading_validation import main
from feed_log import note, recent


class TestFeedLog(unittest.TestCase):
    def test_note_keeps_ring(self):
        note("Blofin", "429 cooldown 12s GET market/tickers")
        rows = recent(3)
        self.assertTrue(rows)
        self.assertIn("429", rows[0]["msg"])


class TestPrefixCliParse(unittest.TestCase):
    def test_help_exits_zero(self):
        with self.assertRaises(SystemExit) as ctx:
            main(["prefix-v2", "-h"])
        self.assertEqual(0, ctx.exception.code)

    def test_missing_cmd_exits_nonzero(self):
        with self.assertRaises(SystemExit):
            main([])


if __name__ == "__main__":
    unittest.main()
