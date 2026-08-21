import unittest

from scan_scheduling import is_full_scan_due


class TestIsFullScanDue(unittest.TestCase):
    def test_never_scanned_before_is_always_due(self):
        self.assertTrue(is_full_scan_due(0.0, 20.0, now=1_000_000.0))
        self.assertTrue(is_full_scan_due(None, 20.0, now=1_000_000.0))

    def test_not_due_before_interval_elapses(self):
        self.assertFalse(is_full_scan_due(1_000_000.0, 20.0, now=1_000_015.0))

    def test_due_exactly_at_interval_boundary(self):
        self.assertTrue(is_full_scan_due(1_000_000.0, 20.0, now=1_000_020.0))

    def test_due_after_interval_elapses(self):
        self.assertTrue(is_full_scan_due(1_000_000.0, 20.0, now=1_000_025.0))

    def test_interval_has_a_floor_of_one_second(self):
        # Nawet przy interval=0 (zla konfiguracja), nie odpala co petle -
        # minimalny odstep to 1s.
        self.assertFalse(is_full_scan_due(1_000_000.0, 0.0, now=1_000_000.5))
        self.assertTrue(is_full_scan_due(1_000_000.0, 0.0, now=1_000_001.0))


if __name__ == "__main__":
    unittest.main()
