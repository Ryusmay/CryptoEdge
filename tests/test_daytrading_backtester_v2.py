import unittest

from daytrading_backtester import replay_daytrading_v2, _is_htf_reversed


def _flat_bars(n, price=100.0):
    return {"opens": [price] * n, "highs": [price + 0.1] * n, "lows": [price - 0.1] * n,
            "closes": [price] * n, "timestamps": [1_700_000_000_000 + i * 300_000 for i in range(n)]}


class TestIsHtfReversed(unittest.TestCase):
    def test_none_bias_never_reverses(self):
        self.assertFalse(_is_htf_reversed(None, "LONG"))

    def test_neutral_bias_never_reverses(self):
        self.assertFalse(_is_htf_reversed("NEUTRAL", "LONG"))

    def test_opposite_bias_reverses(self):
        self.assertTrue(_is_htf_reversed("SHORT", "LONG"))

    def test_same_bias_does_not_reverse(self):
        self.assertFalse(_is_htf_reversed("LONG", "LONG"))


class TestReplayDaytradingV2Mechanics(unittest.TestCase):
    def _single_long_signal(self, entry=100.0, sl=98.0, tp1=101.5, tp2=104.0):
        fired = {"done": False}

        def signal_at(i):
            if i == 0 and not fired["done"]:
                fired["done"] = True
                return {"symbol": "BTC", "direction": "LONG", "price": entry,
                        "sl_price": sl, "tp1_price": tp1, "tp2_price": tp2}
            return {"direction": "NEUTRAL", "reject_reason": "TEST_NEUTRAL"}
        return signal_at

    def test_direct_sl_hit_gives_full_negative_r_minus_costs(self):
        n = 10
        bars = _flat_bars(n, price=100.0)
        bars["opens"][1] = 100.0
        # zaraz po wejsciu cena od razu spada ponizej SL
        bars["lows"][1] = 97.0
        bars["highs"][1] = 100.0
        result = replay_daytrading_v2(bars, self._single_long_signal(entry=100.0, sl=98.0, tp1=101.5, tp2=104.0))
        self.assertEqual(1, result["count"])
        t = result["trades"][0]
        self.assertEqual("sl", t.exit_reason)
        self.assertLess(t.realised_r, -0.9)  # ~-1R minus koszty

    def test_tp1_then_tp2_then_trailing_stop_hit_gives_positive_r(self):
        n = 20
        bars = _flat_bars(n, price=100.0)
        # bar1: wejscie @100 (open[1]=100 z sygnalu na i=0)
        bars["opens"][1] = 100.0
        # bar2: dobija do TP1 (101.5)
        bars["highs"][2] = 102.0
        bars["lows"][2] = 100.0
        # bar3: dobija do TP2 (104.0)
        bars["highs"][3] = 105.0
        bars["lows"][3] = 101.0
        # bary 4-6: cena dalej rosnie (trailing anchor tez rosnie)
        for i in (4, 5, 6):
            bars["highs"][i] = 106.0 + i
            bars["lows"][i] = 105.0 + i
        # bar7: cena spada i trafia w podniesiony (trailingiem) SL
        bars["lows"][7] = 105.0
        bars["highs"][7] = 108.0

        anchors = {4: 103.0, 5: 104.5, 6: 106.0, 7: 106.0}  # rosnaca kotwica 1h

        def htf_trail_anchor_at(i, direction):
            return anchors.get(i)

        result = replay_daytrading_v2(
            bars, self._single_long_signal(entry=100.0, sl=98.0, tp1=101.5, tp2=104.0),
            htf_trail_anchor_at=htf_trail_anchor_at,
        )
        self.assertEqual(1, result["count"])
        t = result["trades"][0]
        self.assertTrue(t.tp1_done)
        self.assertTrue(t.tp2_done)
        self.assertEqual("sl", t.exit_reason)  # ta sama sciezka co surowy stop, ale...
        self.assertGreater(t.realised_r, 0)     # ...z realnym zyskiem, bo SL byl juz podniesiony

    def test_htf_reversal_closes_position_at_current_close(self):
        n = 10
        bars = _flat_bars(n, price=100.0)
        bars["opens"][1] = 100.0
        bars["closes"][3] = 101.0

        def htf_bias_at(i):
            return "SHORT" if i >= 3 else "LONG"  # odwrocenie na barze 3

        result = replay_daytrading_v2(
            bars, self._single_long_signal(entry=100.0, sl=98.0, tp1=101.5, tp2=104.0),
            htf_bias_at=htf_bias_at,
        )
        self.assertEqual(1, result["count"])
        t = result["trades"][0]
        self.assertEqual("htf_reversal", t.exit_reason)
        self.assertEqual(3, t.exit_i)

    def test_htf_bias_none_never_forces_exit(self):
        n = 15
        bars = _flat_bars(n, price=100.0)
        bars["opens"][1] = 100.0
        bars["lows"][5] = 97.0  # dopiero tu realny SL

        def htf_bias_at(i):
            return None  # brak danych - nigdy nie zamyka

        result = replay_daytrading_v2(
            bars, self._single_long_signal(entry=100.0, sl=98.0, tp1=101.5, tp2=104.0),
            htf_bias_at=htf_bias_at,
        )
        t = result["trades"][0]
        self.assertEqual("sl", t.exit_reason)
        self.assertEqual(5, t.exit_i)

    def test_htf_bias_neutral_never_forces_exit(self):
        n = 15
        bars = _flat_bars(n, price=100.0)
        bars["opens"][1] = 100.0
        bars["lows"][5] = 97.0

        def htf_bias_at(i):
            return "NEUTRAL"

        result = replay_daytrading_v2(
            bars, self._single_long_signal(entry=100.0, sl=98.0, tp1=101.5, tp2=104.0),
            htf_bias_at=htf_bias_at,
        )
        t = result["trades"][0]
        self.assertEqual("sl", t.exit_reason)

    def test_notify_exit_called_with_symbol_direction_reason_and_ts(self):
        n = 10
        bars = _flat_bars(n, price=100.0)
        bars["opens"][1] = 100.0
        bars["lows"][2] = 97.0
        calls = []

        def notify_exit(symbol, side, reason, ts):
            calls.append((symbol, side, reason, ts))

        replay_daytrading_v2(bars, self._single_long_signal(), notify_exit=notify_exit)
        self.assertEqual(1, len(calls))
        symbol, side, reason, ts = calls[0]
        self.assertEqual("BTC", symbol)
        self.assertEqual("LONG", side)
        self.assertEqual("sl", reason)
        self.assertIsInstance(ts, float)

    def test_hard_time_stop_still_works_as_last_resort(self):
        n = 30
        bars = _flat_bars(n, price=100.0)
        bars["opens"][1] = 100.0
        # cena stoi w miejscu - nigdy nie trafia SL ani TP
        result = replay_daytrading_v2(bars, self._single_long_signal(), max_bars=5)
        self.assertEqual(1, result["count"])
        self.assertEqual("hard_time_stop", result["trades"][0].exit_reason)

    def test_no_trade_ever_exits_with_setup_invalidated_reason(self):
        # Twarda gwarancja punktu 11 planu: day_setup_invalidated NIE
        # istnieje jako mozliwe wyjscie w V2, niezaleznie od scenariusza.
        n = 40
        bars = _flat_bars(n, price=100.0)
        bars["opens"][1] = 100.0
        for i in range(2, 10):
            bars["highs"][i] = 100.0 + i * 0.3
            bars["lows"][i] = 99.5 + i * 0.2

        def htf_bias_at(i):
            return "LONG" if i % 2 == 0 else "SHORT"  # migotanie, ale i tak nigdy invalidation

        result = replay_daytrading_v2(bars, self._single_long_signal(), htf_bias_at=htf_bias_at)
        reasons = {t.exit_reason for t in result["trades"]}
        self.assertNotIn("day_setup_invalidated", reasons)

    def test_tp1_partial_reduces_remaining_by_exactly_tp1_frac(self):
        n = 10
        bars = _flat_bars(n, price=100.0)
        bars["opens"][1] = 100.0
        bars["highs"][2] = 102.0
        bars["lows"][2] = 100.0
        # po TP1 SL=entry(breakeven) - dobijamy tam natychmiast, konczac trade
        bars["lows"][3] = 99.5
        result = replay_daytrading_v2(bars, self._single_long_signal(entry=100.0, sl=98.0, tp1=101.5, tp2=104.0),
                                       tp1_frac=0.4)
        t = result["trades"][0]
        self.assertTrue(t.tp1_done)
        self.assertAlmostEqual(0.6, t.remaining, places=6)


if __name__ == "__main__":
    unittest.main()
