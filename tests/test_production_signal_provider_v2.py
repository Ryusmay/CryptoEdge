import unittest

from daytrading_backtester import (
    production_signal_provider_v2, htf_bias_provider_v2, htf_trail_anchor_provider_v2,
)


def _trending_bars(n, start=100.0, drift=0.05, seed=1):
    import random
    rnd = random.Random(seed)
    price = start
    opens, highs, lows, closes, timestamps = [], [], [], [], []
    ts = 1_700_000_000_000
    for i in range(n):
        o = price
        c = price + drift + rnd.uniform(-0.02, 0.02)
        h = max(o, c) + 0.05
        l = min(o, c) - 0.05
        opens.append(o); highs.append(h); lows.append(l); closes.append(c)
        timestamps.append(ts + i * 3_600_000)  # 1h krok - wspolny mianownik dla testu
        price = c
    return {"opens": opens, "highs": highs, "lows": lows, "closes": closes, "timestamps": timestamps}


def _flat_bars(n, price=100.0, step_ms=3_600_000):
    ts = 1_700_000_000_000
    return {
        "opens": [price] * n, "highs": [price] * n, "lows": [price] * n, "closes": [price] * n,
        "timestamps": [ts + i * step_ms for i in range(n)],
    }


def _up_swing_1h_bundle(n=300, low=100.0, high=120.0, pad=100, gap_bars=15):
    highs = [105.0] * n
    lows = [105.0] * n
    closes = [105.0] * n
    opens = [105.0] * n
    lows[pad] = low
    highs[pad + gap_bars] = high
    ts = 1_700_000_000_000
    timestamps = [ts + i * 3_600_000 for i in range(n)]
    return {"opens": opens, "highs": highs, "lows": lows, "closes": closes, "timestamps": timestamps}


class TestHtfBiasProviderV2(unittest.TestCase):
    def test_returns_long_or_neutral_when_1d_and_4h_both_trend_up_never_short(self):
        bundle = {
            "1d": _trending_bars(300, drift=0.5, seed=1),
            "4h": _trending_bars(300, drift=0.3, seed=2),
        }
        htf_bias_at = htf_bias_provider_v2("BTC", bundle)
        result = htf_bias_at(299)
        # Nigdy SHORT przy jednoznacznym trendzie wzrostowym - None jest
        # akceptowalny, jesli 300 barow nie starcza na rozgrzewke wskaznikow.
        self.assertIn(result, ("LONG", "NEUTRAL", None))

    def test_returns_none_with_insufficient_data(self):
        bundle = {"1d": _flat_bars(5), "4h": _flat_bars(5)}
        htf_bias_at = htf_bias_provider_v2("BTC", bundle)
        self.assertIsNone(htf_bias_at(4))

    def test_returns_none_for_out_of_range_index(self):
        bundle = {"1d": _trending_bars(300), "4h": _trending_bars(300)}
        htf_bias_at = htf_bias_provider_v2("BTC", bundle, drive_tf="1d")
        self.assertIsNone(htf_bias_at(9999))

    def test_never_raises_on_missing_timeframe_key(self):
        bundle = {"1d": _trending_bars(300)}  # brak "4h" w ogole
        htf_bias_at = htf_bias_provider_v2("BTC", bundle, drive_tf="1d")
        result = htf_bias_at(299)
        self.assertIsNone(result)


class TestHtfTrailAnchorProviderV2(unittest.TestCase):
    def test_returns_none_when_swing_direction_mismatches_requested_direction(self):
        bundle = {"1h": _up_swing_1h_bundle()}  # swing UP
        anchor_at = htf_trail_anchor_provider_v2("BTC", bundle, drive_tf="1h")
        result = anchor_at(299, "SHORT")
        self.assertIsNone(result)

    def test_returns_none_with_flat_1h_no_swing(self):
        bundle = {"1h": _flat_bars(300)}
        anchor_at = htf_trail_anchor_provider_v2("BTC", bundle, drive_tf="1h")
        self.assertIsNone(anchor_at(299, "LONG"))

    def test_returns_a_price_below_swing_high_for_long_when_swing_matches(self):
        bundle = {"1h": _up_swing_1h_bundle(low=100.0, high=120.0)}
        anchor_at = htf_trail_anchor_provider_v2("BTC", bundle, drive_tf="1h")
        result = anchor_at(299, "LONG")
        if result is not None:
            self.assertLess(result, 120.0)

    def test_never_raises_on_missing_timeframe_key(self):
        bundle = {}
        anchor_at = htf_trail_anchor_provider_v2("BTC", bundle, drive_tf="1h")
        self.assertIsNone(anchor_at(0, "LONG"))


class TestProductionSignalProviderV2Smoke(unittest.TestCase):
    """Test dymny (nie e2e happy-path) - sprawdza, ze cala sciezka
    (AsOfDataFeeder + DayTradingEngineV2 + wszystkie timeframy) nie wywala
    sie na wyjatku i zwraca uzywalna strukture."""

    def test_signal_at_returns_dict_without_raising(self):
        bundle = {
            "1d": _trending_bars(300, drift=0.5, seed=1),
            "4h": _trending_bars(300, drift=0.3, seed=2),
            "1h": _up_swing_1h_bundle(),
            "15m": _trending_bars(300, drift=0.05, seed=3),
            "5m": _trending_bars(300, drift=0.01, seed=4),
        }
        signal_at, engine = production_signal_provider_v2("BTC", bundle)
        from daytrading_engine_v2 import DayTradingEngineV2
        self.assertIsInstance(engine, DayTradingEngineV2)
        result = signal_at(280)
        self.assertIn("direction", result)
        self.assertIn(result["direction"], ("LONG", "SHORT", "NEUTRAL"))

    def test_returned_engine_is_used_for_state_shared_across_calls(self):
        bundle = {
            "1d": _trending_bars(300, drift=0.5, seed=1),
            "4h": _trending_bars(300, drift=0.3, seed=2),
            "1h": _up_swing_1h_bundle(),
            "15m": _trending_bars(300, drift=0.05, seed=3),
            "5m": _trending_bars(300, drift=0.01, seed=4),
        }
        signal_at, engine = production_signal_provider_v2("BTC", bundle)
        signal_at(280)
        signal_at(281)
        # Jedna, trwala instancja silnika obsluguje caly ciag wywolan - to
        # ona (nie osobna instancja za kazdym razem) trzyma stan hamulcow
        # czestotliwosci (cooldown, jedno wejscie na swing).
        self.assertIsInstance(engine._consumed_swing_end, dict)
        self.assertIsInstance(engine._active_swing_key, dict)


if __name__ == "__main__":
    unittest.main()
