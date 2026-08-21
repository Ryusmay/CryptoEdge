import unittest

from indicators import calculate_macd, calculate_rsi
from indicators_full import (
    _adx, _atr, _atr_series, _choppiness_index, _classic_pivot_points, _confirmed_structure_levels,
    _macd, _rsi, _supertrend, _viper, compute_indicators,
)


class TestIndicatorConsistency(unittest.TestCase):
    def test_rsi_uses_wilder_reference_value(self):
        closes = [44.00, 44.15, 43.90, 44.35, 44.70, 45.10, 45.35, 45.20,
                  45.55, 45.75, 46.00, 45.85, 46.10, 46.35, 46.20]
        gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
        losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
        expected = 100 - 100 / (1 + (sum(gains) / 14) / (sum(losses) / 14))
        self.assertAlmostEqual(_rsi(closes, 14), expected, places=2)
        self.assertEqual(calculate_rsi(closes, 14), _rsi(closes, 14))

    def test_atr_uses_recursive_wilder_smoothing(self):
        closes = [100, 101, 100, 103, 102, 106]
        highs = [c + 1 for c in closes]
        lows = [c - 1 for c in closes]
        series = _atr_series(highs, lows, closes, 3)
        true_ranges = [2, 2, 4, 2, 5]
        seed = sum(true_ranges[:3]) / 3
        expected = ((seed * 2 + true_ranges[3]) / 3 * 2 + true_ranges[4]) / 3
        self.assertAlmostEqual(_atr(highs, lows, closes, 3), expected, places=8)
        self.assertEqual(len(series), len(closes))

    def test_simple_and_full_macd_share_one_engine(self):
        closes = [100 + i * 0.2 + ((i % 7) - 3) * 0.05 for i in range(80)]
        simple = calculate_macd(closes)
        full = _macd(closes)
        self.assertAlmostEqual(simple["macd"], full["macd"], places=6)
        self.assertAlmostEqual(simple["signal"], full["signal"], places=6)
        self.assertEqual(simple["cross"], full["cross"])

    def test_supertrend_returns_band_and_direction(self):
        closes = [100 + i * 0.5 for i in range(40)]
        highs = [c + 0.4 for c in closes]
        lows = [c - 0.4 for c in closes]
        result = _supertrend(highs, lows, closes, 10, 3.0)
        self.assertTrue(result["is_up"])
        self.assertLess(result["value"], closes[-1])

    def test_classic_pivots_use_previous_closed_bar(self):
        result = _classic_pivot_points([9, 12, 99], [7, 6, 1], [8, 9, 50])
        self.assertEqual(result["P"], 9.0)
        self.assertEqual(result["R1"], 12.0)
        self.assertEqual(result["S1"], 6.0)

    def test_structure_requires_right_side_confirmation(self):
        highs = [10.0] * 45
        lows = [9.0] * 45
        closes = [9.5] * 45
        highs[20] = 15.0
        early = _confirmed_structure_levels(highs[:30], lows[:30], closes[:30], right=14)
        confirmed = _confirmed_structure_levels(highs, lows, closes, right=14)
        self.assertEqual(early["confirmed_pivots"], 0)
        self.assertGreaterEqual(confirmed["confirmed_pivots"], 1)

    def test_viper_exposes_requested_configuration_and_exact_ohlc4(self):
        closes = [100 + i * 0.1 for i in range(365)]
        opens = [c - 0.05 for c in closes]
        highs = [c + 0.2 for c in closes]
        lows = [c - 0.2 for c in closes]
        result = _viper(opens, highs, lows, closes, [1] * 365)
        self.assertTrue(result["ready"])
        self.assertTrue(result["source_exact"])
        self.assertEqual(result["kind"], "volume_profile")
        self.assertEqual((result["candle_range"], result["bar_size"], result["placement"]), (365, 14, "right"))
        self.assertEqual(result["chart_distance"], 90)
        self.assertEqual(result["noise"], 0.05)
        self.assertEqual(result["colors"], {"price": "white", "sell": "maroon", "buy": "lime"})
        self.assertTrue(result["levels"])
        self.assertIsNotNone(result["poc"])

    def test_choppiness_is_bounded_and_distinguishes_trend_from_range(self):
        trend = [100 + i for i in range(40)]
        trend_chop = _choppiness_index([x + 0.2 for x in trend], [x - 0.2 for x in trend], trend, 14)
        sideways = [100 + (1 if i % 2 else -1) for i in range(40)]
        sideways_chop = _choppiness_index([x + 0.2 for x in sideways], [x - 0.2 for x in sideways], sideways, 14)
        self.assertTrue(0 <= trend_chop <= 100)
        self.assertTrue(0 <= sideways_chop <= 100)
        self.assertLess(trend_chop, sideways_chop)

    def test_adx_uses_recursive_wilder_smoothing(self):
        closes = [100 + i * 0.35 + ((i % 9) - 4) * 0.18 for i in range(80)]
        highs = [c + 0.6 + (i % 3) * 0.05 for i, c in enumerate(closes)]
        lows = [c - 0.5 - (i % 4) * 0.04 for i, c in enumerate(closes)]
        period = 14
        plus_dm, minus_dm, trs = [], [], []
        for i in range(1, len(closes)):
            up, down = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
            plus_dm.append(up if up > down and up > 0 else 0.0)
            minus_dm.append(down if down > up and down > 0 else 0.0)
            trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
        def smooth(values):
            current, output = sum(values[:period]), []
            output.append(current)
            for value in values[period:]:
                current = current - current / period + value
                output.append(current)
            return output
        tr_s, plus_s, minus_s = smooth(trs), smooth(plus_dm), smooth(minus_dm)
        dx = []
        for tr, plus, minus in zip(tr_s, plus_s, minus_s):
            pdi, mdi = 100 * plus / tr, 100 * minus / tr
            dx.append(100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi else 0.0)
        expected = sum(dx[:period]) / period
        for value in dx[period:]:
            expected = (expected * (period - 1) + value) / period
        self.assertAlmostEqual(_adx(highs, lows, closes, period), expected, places=2)

    def test_complete_indicator_bundle_has_finite_bounded_outputs_on_every_tf(self):
        import math
        closes = [100 + i * 0.025 + math.sin(i / 7.0) * 1.4 for i in range(400)]
        opens = [closes[max(0, i - 1)] for i in range(400)]
        highs = [max(opens[i], closes[i]) + 0.45 for i in range(400)]
        lows = [min(opens[i], closes[i]) - 0.45 for i in range(400)]
        data = {"opens": opens, "highs": highs, "lows": lows, "closes": closes,
                "volumes": [1000 + (i % 17) * 25 for i in range(400)]}
        for timeframe in ("15m", "1h", "4h", "1d"):
            result = compute_indicators(data, timeframe)
            self.assertIsNotNone(result, timeframe)
            self.assertTrue(0 <= result["rsi"] <= 100)
            self.assertTrue(0 <= result["adx"] <= 100)
            self.assertTrue(0 <= result["choppiness"] <= 100)
            self.assertGreater(result["atr"], 0)
            self.assertIn(result["supertrend"]["direction"], ("up", "down"))
            # Viper (volume profile) zostal swiadomie usuniety z pakietu wskaznikow
            # uzywanego do decyzji (patrz komentarz przy `viper = {}` w compute_indicators) -
            # zostaje tylko jako osobna nakladka na wykresie, inna sciezka danych.
            self.assertEqual(result["viper"], {})
            self.assertEqual(set(result["pivot_points"]), {"P", "R1", "R2", "R3", "S1", "S2", "S3", "source_bar"})


if __name__ == "__main__":
    unittest.main()
