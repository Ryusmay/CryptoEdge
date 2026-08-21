import unittest

from runtime import BotRuntime


class RiskStub:
    def __init__(self):
        self.paused = False
        self.is_halted = False
        self.halt_reason = None


class TestAnalysisMode(unittest.TestCase):
    def test_analysis_wakes_engine_without_enabling_trading(self):
        runtime = BotRuntime()
        runtime.risk = RiskStub()
        message = runtime.start_analysis()
        self.assertTrue(runtime.engine_enabled)
        self.assertFalse(runtime.trading_enabled)
        self.assertTrue(runtime.risk.paused)
        self.assertTrue(runtime.analysis_wakeup.is_set())
        self.assertTrue(runtime.analysis_loading)
        self.assertIn("ANALYSIS_ON", message)

    def test_trading_is_a_separate_explicit_action(self):
        runtime = BotRuntime()
        runtime.risk = RiskStub()
        runtime.start_analysis()
        runtime.start_trading()
        self.assertTrue(runtime.engine_enabled)
        self.assertTrue(runtime.trading_enabled)
        self.assertFalse(runtime.risk.paused)


if __name__ == "__main__":
    unittest.main()
