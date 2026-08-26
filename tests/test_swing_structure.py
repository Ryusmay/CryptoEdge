import unittest

from swing_structure import (
    find_last_confirmed_swing, swing_fib_retracement, swing_fib_extension,
)


def _flat_atr_series(n, value=1.0):
    return [value] * n


def _make_up_swing_series(low_price=100.0, high_price=120.0, pad=8):
    """low@index=pad, high@index=pad+move_bars, plaskie swiece dookola (zeby
    fraktalny pivot byl jednoznaczny)."""
    n_bars = 40
    highs = [105.0] * n_bars
    lows = [105.0] * n_bars
    lows[pad] = low_price
    highs[pad + 15] = high_price
    closes = [105.0] * n_bars
    return highs, lows, closes


class TestFindLastConfirmedSwing(unittest.TestCase):
    def test_detects_clean_up_swing_above_move_and_bar_thresholds(self):
        highs, lows, closes = _make_up_swing_series()
        atr = _flat_atr_series(len(closes), value=1.0)  # move=20, ratio=20 >> min_move_atr
        swing = find_last_confirmed_swing(highs, lows, closes, atr, min_move_atr=1.5, min_bars=3, right_confirm=2)
        self.assertIsNotNone(swing)
        self.assertEqual("UP", swing["direction"])
        self.assertEqual(100.0, swing["start"]["price"])
        self.assertEqual(120.0, swing["end"]["price"])
        self.assertEqual(15, swing["bars"])
        self.assertAlmostEqual(20.0, swing["move"])

    def test_detects_down_swing(self):
        n_bars = 40
        highs = [105.0] * n_bars
        lows = [105.0] * n_bars
        highs[8] = 130.0
        lows[8 + 12] = 100.0
        closes = [105.0] * n_bars
        atr = _flat_atr_series(n_bars, value=1.0)
        swing = find_last_confirmed_swing(highs, lows, closes, atr, min_move_atr=1.5, min_bars=3, right_confirm=2)
        self.assertIsNotNone(swing)
        self.assertEqual("DOWN", swing["direction"])
        self.assertEqual(130.0, swing["start"]["price"])
        self.assertEqual(100.0, swing["end"]["price"])

    def test_move_below_min_move_atr_is_rejected(self):
        highs, lows, closes = _make_up_swing_series(low_price=100.0, high_price=101.0)  # move=1
        atr = _flat_atr_series(len(closes), value=1.0)  # ratio=1 < min_move_atr=1.5
        swing = find_last_confirmed_swing(highs, lows, closes, atr, min_move_atr=1.5, min_bars=3, right_confirm=2)
        self.assertIsNone(swing)

    def test_bars_below_min_bars_is_rejected(self):
        n_bars = 40
        highs = [105.0] * n_bars
        lows = [105.0] * n_bars
        lows[10] = 100.0
        highs[11] = 120.0  # tylko 1 swieca miedzy pivotami
        closes = [105.0] * n_bars
        atr = _flat_atr_series(n_bars, value=1.0)
        swing = find_last_confirmed_swing(highs, lows, closes, atr, min_move_atr=1.5, min_bars=3, right_confirm=2)
        self.assertIsNone(swing)

    def test_no_lookahead_pivot_not_confirmed_until_right_confirm_bars_after(self):
        # Ostatnia swieca serii jest "swiezym" ekstremum, ale nie ma jeszcze
        # right_confirm swiec PO niej - nie powinna byc uznana za pivot.
        n_bars = 20
        highs = [105.0] * n_bars
        lows = [105.0] * n_bars
        lows[5] = 100.0
        highs[n_bars - 1] = 200.0  # ostatnia swieca - brak potwierdzenia z prawej
        closes = [105.0] * n_bars
        atr = _flat_atr_series(n_bars, value=1.0)
        swing = find_last_confirmed_swing(highs, lows, closes, atr, min_move_atr=1.5, min_bars=3, right_confirm=2)
        # Nie powinien wykryc swingu do niepotwierdzonego "200.0" na koncu.
        if swing is not None:
            self.assertNotEqual(200.0, swing["end"]["price"])

    def test_too_short_series_returns_none_not_raise(self):
        self.assertIsNone(find_last_confirmed_swing([1, 2], [1, 2], [1, 2], [1, 1], right_confirm=2))

    def test_missing_atr_at_pivot_index_skips_that_candidate(self):
        highs, lows, closes = _make_up_swing_series()
        atr = _flat_atr_series(len(closes), value=1.0)
        atr[8 + 15] = None  # brak ATR dokladnie na koncu swingu
        swing = find_last_confirmed_swing(highs, lows, closes, atr, min_move_atr=1.5, min_bars=3, right_confirm=2)
        self.assertIsNone(swing)

    def test_picks_most_recent_valid_swing_not_an_older_larger_one(self):
        n_bars = 60
        highs = [105.0] * n_bars
        lows = [105.0] * n_bars
        closes = [105.0] * n_bars
        # starszy, wiekszy swing (index 5 -> 20)
        lows[5] = 50.0
        highs[20] = 200.0
        # nowszy, mniejszy (ale wciaz wazny) swing (index 30 -> 40)
        lows[30] = 100.0
        highs[40] = 115.0
        atr = _flat_atr_series(n_bars, value=1.0)
        swing = find_last_confirmed_swing(highs, lows, closes, atr, min_move_atr=1.5, min_bars=3, right_confirm=2)
        self.assertIsNotNone(swing)
        self.assertEqual(40, swing["end"]["index"])

    def test_prefer_direction_skips_newer_opposite_pullback(self):
        n_bars = 55
        highs = [105.0] * n_bars
        lows = [105.0] * n_bars
        closes = [105.0] * n_bars
        lows[8] = 100.0
        highs[23] = 120.0          # impuls UP
        lows[35] = 102.0           # korekta DOWN (ostatni swing)
        atr = _flat_atr_series(n_bars, value=1.0)
        last = find_last_confirmed_swing(highs, lows, closes, atr, min_move_atr=1.5, min_bars=3, right_confirm=2)
        self.assertEqual("DOWN", last["direction"])
        impulse = find_last_confirmed_swing(
            highs, lows, closes, atr, min_move_atr=1.5, min_bars=3, right_confirm=2,
            prefer_direction="UP",
        )
        self.assertIsNotNone(impulse)
        self.assertEqual("UP", impulse["direction"])
        self.assertEqual(100.0, impulse["start"]["price"])
        self.assertEqual(120.0, impulse["end"]["price"])

    def test_prefer_direction_none_when_no_matching_impulse(self):
        highs, lows, closes = _make_up_swing_series()
        atr = _flat_atr_series(len(closes), value=1.0)
        self.assertIsNone(find_last_confirmed_swing(
            highs, lows, closes, atr, min_move_atr=1.5, min_bars=3, right_confirm=2,
            prefer_direction="DOWN",
        ))


class TestSwingFibLevels(unittest.TestCase):
    def _up_swing(self):
        return {"direction": "UP", "start": {"index": 0, "price": 100.0},
                "end": {"index": 10, "price": 200.0}, "move": 100.0, "bars": 10, "move_atr_ratio": 5.0}

    def _down_swing(self):
        return {"direction": "DOWN", "start": {"index": 0, "price": 200.0},
                "end": {"index": 10, "price": 100.0}, "move": 100.0, "bars": 10, "move_atr_ratio": 5.0}

    def test_up_swing_retracement_measures_down_from_high(self):
        levels = swing_fib_retracement(self._up_swing())
        self.assertAlmostEqual(200.0 - 100.0 * 0.5, levels["0.5"])
        self.assertAlmostEqual(200.0 - 100.0 * 0.618, levels["0.618"])

    def test_down_swing_retracement_measures_up_from_low(self):
        levels = swing_fib_retracement(self._down_swing())
        self.assertAlmostEqual(100.0 + 100.0 * 0.5, levels["0.5"])

    def test_up_swing_extension_projects_above_high(self):
        levels = swing_fib_extension(self._up_swing())
        self.assertAlmostEqual(200.0 + 100.0 * 0.272, levels["1.272"])
        self.assertAlmostEqual(200.0 + 100.0 * 0.618, levels["1.618"])

    def test_down_swing_extension_projects_below_low(self):
        levels = swing_fib_extension(self._down_swing())
        self.assertAlmostEqual(100.0 - 100.0 * 0.272, levels["1.272"])
        self.assertAlmostEqual(100.0 - 100.0 * 0.618, levels["1.618"])

    def test_zero_span_swing_returns_empty_levels(self):
        flat = {"direction": "UP", "start": {"index": 0, "price": 100.0},
                "end": {"index": 5, "price": 100.0}, "move": 0.0, "bars": 5, "move_atr_ratio": 0.0}
        self.assertEqual({}, swing_fib_retracement(flat))
        self.assertEqual({}, swing_fib_extension(flat))


if __name__ == "__main__":
    unittest.main()
