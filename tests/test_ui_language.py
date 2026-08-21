import unittest
from pyside6_ui import friendly_reason, friendly_status


class TestFriendlyUiLanguage(unittest.TestCase):
    def test_status_codes_are_presented_as_trader_language(self):
        self.assertEqual("Brak wejścia", friendly_status("NO_TRADE"))
        self.assertEqual("Obserwacja", friendly_status("NEUTRAL"))
        self.assertEqual("Brak wejścia", friendly_status("PATH=NO_TRADE|ENGINE=daytrading"))

    def test_reject_reason_keeps_numeric_context(self):
        self.assertEqual("Trend jest zbyt słaby (12.94)", friendly_reason("DAY_ADX_WEAK(12.94)"))
        self.assertEqual("Czeka na potwierdzenie wejścia na 5m",
                         friendly_reason("PATH=NO_TRADE|REJECT=DAY_5M_TIMING_WAIT|MODE=DAYTRADING"))

    def test_positive_setup_codes_are_readable(self):
        self.assertEqual("Trend 1h i 4h jest zgodny", friendly_reason("DAY_HTF_ALIGN"))


if __name__ == "__main__":
    unittest.main()
