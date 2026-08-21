import unittest
from unittest.mock import MagicMock, patch

import config
from signal_engine import SignalEngine


def _minimal_signal(engine_tag, mode_tag):
    return {"symbol": "BTC", "direction": "NEUTRAL", "reject_reason": "TEST_NEUTRAL",
            "strength": 0.05, "engine": engine_tag, "strategy_mode": mode_tag,
            "price": 100.0}


class TestDaytradingV2Switch(unittest.TestCase):
    def setUp(self):
        self.se = SignalEngine(data_feeder=object())

    def test_v1_engine_used_by_default(self):
        fake_v1 = MagicMock()
        fake_v1.return_value.feeder = self.se.feeder
        fake_v1.return_value.generate.return_value = [_minimal_signal("daytrading", "DAYTRADING")]
        fake_v2 = MagicMock()
        with patch.object(config, "STRATEGY_MODE", "DAYTRADING"), \
             patch.object(config, "DAYTRADING_V2_ENABLED", False), \
             patch.object(config, "REGIME_ENABLED", False), \
             patch("daytrading_engine.DayTradingEngine", fake_v1), \
             patch("daytrading_engine_v2.DayTradingEngineV2", fake_v2):
            result = self.se.generate_signals([], 0.0)
        fake_v1.assert_called_once()
        fake_v2.assert_not_called()
        self.assertEqual(1, len(result))

    def test_v2_engine_used_when_enabled(self):
        fake_v1 = MagicMock()
        fake_v2 = MagicMock()
        fake_v2.return_value.feeder = self.se.feeder
        fake_v2.return_value.generate.return_value = [_minimal_signal("daytrading_v2", "DAYTRADING_V2")]
        with patch.object(config, "DAYTRADING_V2_ENABLED", True), \
             patch.object(config, "REGIME_ENABLED", False), \
             patch("daytrading_engine.DayTradingEngine", fake_v1), \
             patch("daytrading_engine_v2.DayTradingEngineV2", fake_v2):
            result = self.se.generate_signals([{"symbol": "BTC", "price": 100.0}], 0.0)
        fake_v2.assert_called_once()
        fake_v1.assert_not_called()
        self.assertEqual(1, len(result))
        self.assertEqual("daytrading_v2", result[0]["engine"])

    def test_strategy_mode_daytrading_v2_alone_is_sufficient_without_the_flag(self):
        # 21.08.2026: naprawiony bug - bramka porownywala STRATEGY_MODE
        # tylko do literalnego "DAYTRADING", wiec przy config.py ustawiajacym
        # STRATEGY_MODE="DAYTRADING_V2" (bez zmiany DAYTRADING_V2_ENABLED)
        # cala galaz V1/V2 nigdy sie nie wykonywala - bot spadal do "trend".
        fake_v1 = MagicMock()
        fake_v2 = MagicMock()
        fake_v2.return_value.feeder = self.se.feeder
        fake_v2.return_value.generate.return_value = [_minimal_signal("daytrading_v2", "DAYTRADING_V2")]
        with patch.object(config, "STRATEGY_MODE", "DAYTRADING_V2"), \
             patch.object(config, "DAYTRADING_V2_ENABLED", False), \
             patch.object(config, "REGIME_ENABLED", False), \
             patch("daytrading_engine.DayTradingEngine", fake_v1), \
             patch("daytrading_engine_v2.DayTradingEngineV2", fake_v2):
            result = self.se.generate_signals([{"symbol": "BTC", "price": 100.0}], 0.0)
        fake_v2.assert_called_once()
        self.assertEqual("daytrading_v2", result[0]["engine"])

    def test_v2_engine_instance_reused_across_calls_for_same_feeder(self):
        fake_v2 = MagicMock()
        fake_v2.return_value.feeder = self.se.feeder
        fake_v2.return_value.generate.return_value = [_minimal_signal("daytrading_v2", "DAYTRADING_V2")]
        with patch.object(config, "DAYTRADING_V2_ENABLED", True), \
             patch.object(config, "REGIME_ENABLED", False), \
             patch("daytrading_engine_v2.DayTradingEngineV2", fake_v2):
            self.se.generate_signals([{"symbol": "BTC", "price": 100.0}], 0.0)
            self.se.generate_signals([{"symbol": "BTC", "price": 100.0}], 0.0)
        # Konstruktor wolany tylko raz - druga runda uzywa juz istniejacej
        # instancji (wazne dla stanu hamulcow czestotliwosci silnika V2).
        fake_v2.assert_called_once()

    def test_analysis_board_reflects_signals_own_engine_tag_not_hardcoded(self):
        fake_v2 = MagicMock()
        fake_v2.return_value.feeder = self.se.feeder
        fake_v2.return_value.generate.return_value = [_minimal_signal("daytrading_v2", "DAYTRADING_V2")]
        with patch.object(config, "DAYTRADING_V2_ENABLED", True), \
             patch.object(config, "REGIME_ENABLED", False), \
             patch("daytrading_engine_v2.DayTradingEngineV2", fake_v2):
            self.se.generate_signals([{"symbol": "BTC", "price": 100.0}], 0.0)
        board = self.se.last_analysis_board
        self.assertEqual(1, len(board))
        self.assertEqual("daytrading_v2", board[0]["engine"])
        self.assertEqual("DAYTRADING_V2", board[0]["strategy_mode"])


if __name__ == "__main__":
    unittest.main()
