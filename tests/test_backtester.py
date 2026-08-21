# ============================================================
# Regresja: brakująca metoda EventBacktester._bar_slip powodowała
# AttributeError przy pierwszej egzekucji sygnału w run_mtf().
# Ten test pilnuje, żeby metoda istniała i zwracała sensowny poślizg.
# ============================================================

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from event_backtester import EventBacktester


class TestBarSlip(unittest.TestCase):
    def setUp(self):
        self.bt = EventBacktester(slip=0.0003)

    def test_bar_slip_exists_and_callable(self):
        ohlcv = {"volumes": [1000.0, 1200.0, 900.0]}
        slip = self.bt._bar_slip(5000.0, ohlcv, 1, 100.0, atr=1.5)
        self.assertIsInstance(slip, float)
        self.assertGreater(slip, 0.0)

    def test_bar_slip_falls_back_on_missing_volume(self):
        # brak volumes / indeks poza zakresem -> nie powinno rzucić wyjątku
        slip = self.bt._bar_slip(5000.0, {}, 0, 100.0, atr=None)
        self.assertIsInstance(slip, float)
        self.assertGreaterEqual(slip, 0.0)

    def test_bar_slip_scales_with_participation(self):
        # duży notional względem wolumenu bara -> większy poślizg niż mały notional
        ohlcv = {"volumes": [10.0] * 5}  # niska płynność
        small = self.bt._bar_slip(10.0, ohlcv, 0, 100.0, atr=1.0)
        large = self.bt._bar_slip(50_000.0, ohlcv, 0, 100.0, atr=1.0)
        self.assertGreaterEqual(large, small)


if __name__ == "__main__":
    unittest.main()
