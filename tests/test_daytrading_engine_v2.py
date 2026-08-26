import unittest
from unittest.mock import MagicMock, patch

import config
import daytrading_engine_v2
from daytrading_engine_v2 import DayTradingEngineV2, _bias_from_indicators


def indicator(direction="up", atr=1.0, extra=None):
    up = direction == "up"
    ind = {
        "price": 100.0, "atr": atr,
        "ema_fast_above_slow": up, "price_above_ema_slow": up,
        "supertrend": {"is_up": up, "direction": "up" if up else "down"},
        "support_resistance": {"supports": [], "resistances": []},
        "pivot_points": {},
    }
    if extra:
        ind.update(extra)
    return ind


class FakeBlofin:
    """Zwraca rozne OHLCV per (symbol,bar) - wypelnione przez testy przed
    wywolaniem evaluate()."""
    def __init__(self):
        self.frames = {}  # (symbol, bar) -> dict

    def fetch_klines_ohlcv(self, symbol, bar="1H", limit=120, interval=None):
        return self.frames.get((symbol, interval or bar), {})

    def fetch_funding_rate(self, symbol):
        return getattr(self, "funding", {}).get(symbol, {})


class FakeFeeder:
    def __init__(self):
        self.blofin = FakeBlofin()


def _flat_ohlcv(n, price=100.0):
    return {"opens": [price] * n, "highs": [price] * n, "lows": [price] * n, "closes": [price] * n}


def _up_swing_1h(low=100.0, high=120.0, pad=8, gap_bars=15, total=40):
    highs = [105.0] * total
    lows = [105.0] * total
    lows[pad] = low
    highs[pad + gap_bars] = high
    closes = [105.0] * total
    opens = [105.0] * total
    return {"opens": opens, "highs": highs, "lows": lows, "closes": closes}


def _15m_trigger_series(zone_near, direction, n=30):
    """Cena dotyka strefy (ponizej/powyzej zone_near), ostatnia swieca
    reclaim z powrotem po wlasciwej stronie."""
    base = zone_near + (5.0 if direction == "LONG" else -5.0)
    closes = [base] * n
    opens = [base] * n
    highs = [base + 1] * n
    lows = [base - 1] * n
    if direction == "LONG":
        lows[n - 4] = zone_near - 2.0  # dotkniecie strefy
        closes[n - 1] = zone_near + 1.0  # reclaim ponad zone_near
    else:
        highs[n - 4] = zone_near + 2.0
        closes[n - 1] = zone_near - 1.0
    return {"opens": opens, "highs": highs, "lows": lows, "closes": closes}


def _up_impulse_then_pullback_1h(low=100.0, high=120.0, pullback_low=102.0,
                                 pad=8, gap_bars=15, pull_gap=12, total=55):
    """Impuls UP, potem korekta DOWN jako ostatni swing — typowy retest."""
    highs = [105.0] * total
    lows = [105.0] * total
    lows[pad] = low
    highs[pad + gap_bars] = high
    lows[pad + gap_bars + pull_gap] = pullback_low
    closes = [105.0] * total
    opens = [105.0] * total
    return {"opens": opens, "highs": highs, "lows": lows, "closes": closes}


def _down_swing_1h(high=120.0, low=100.0, pad=8, gap_bars=15, total=40):
    """Lustrzane odbicie _up_swing_1h: szczyt najpierw, dolek pozniej ->
    swing DOWN (potrzebne do testow sciezki SHORT/kotwica 4h)."""
    highs = [105.0] * total
    lows = [105.0] * total
    highs[pad] = high
    lows[pad + gap_bars] = low
    closes = [105.0] * total
    opens = [105.0] * total
    return {"opens": opens, "highs": highs, "lows": lows, "closes": closes}


class TestBiasFromIndicators(unittest.TestCase):
    def test_long_bias_when_all_three_aligned(self):
        self.assertEqual("LONG", _bias_from_indicators(indicator("up")))

    def test_short_bias_when_all_three_aligned(self):
        self.assertEqual("SHORT", _bias_from_indicators(indicator("down")))

    def test_two_of_three_aligned_gives_majority_bias_by_default(self):
        # 21.08.2026: domyslnie (DAYTRADING_V2_BIAS_MIN_AGREE=2) wiekszosc 2
        # z 3 wystarcza - pojedynczy spozniony wskaznik (tu: SuperTrend) juz
        # NIE blokuje calego sygnalu, jak w starym, w pelni jednomyslnym
        # zachowaniu (patrz test_two_of_three_needs_unanimous_when_min_agree_is_3).
        ind = indicator("up")
        ind["supertrend"] = {"is_up": False}
        with patch.object(config, "DAYTRADING_V2_BIAS_MIN_AGREE", 2):
            self.assertEqual("LONG", _bias_from_indicators(ind))

    def test_true_tie_still_gives_neutral(self):
        # SuperTrend niedostepny (is_up=None) wstrzymuje sie od glosu; przy
        # price_up != ema_up zostaje remis 1-1, ktory nie osiaga progu 2 w
        # zadna strone.
        ind = indicator("up")
        ind["ema_fast_above_slow"] = False
        ind["supertrend"] = {"is_up": None}
        with patch.object(config, "DAYTRADING_V2_BIAS_MIN_AGREE", 2):
            self.assertEqual("NEUTRAL", _bias_from_indicators(ind))

    def test_two_of_three_needs_unanimous_when_min_agree_is_3(self):
        # Rollback do starego zachowania (sprzed 21.08.2026): ustawienie
        # DAYTRADING_V2_BIAS_MIN_AGREE=3 przywraca wymog jednomyslnosci
        # wszystkich trzech sygnalow.
        ind = indicator("up")
        ind["supertrend"] = {"is_up": False}
        with patch.object(config, "DAYTRADING_V2_BIAS_MIN_AGREE", 3):
            self.assertEqual("NEUTRAL", _bias_from_indicators(ind))

    def test_none_indicator_gives_neutral(self):
        self.assertEqual("NEUTRAL", _bias_from_indicators(None))


class TestDayTradingEngineV2Cascade(unittest.TestCase):
    def setUp(self):
        self.feeder = FakeFeeder()
        self.engine = DayTradingEngineV2(self.feeder)

    def _set_frames(self, symbol, d1=None, h4=None, h1=None, m15=None, m5=None):
        b = self.feeder.blofin
        if d1 is not None: b.frames[(symbol, "1D")] = d1
        if h4 is not None: b.frames[(symbol, "4H")] = h4
        if h1 is not None: b.frames[(symbol, "1H")] = h1
        if m15 is not None: b.frames[(symbol, "15m")] = m15
        if m5 is not None: b.frames[(symbol, "5m")] = m5

    def test_excluded_symbol_is_rejected_before_any_fetch(self):
        with patch.object(config, "DAYTRADING_V2_EXCLUDED_SYMBOLS", ["TRX"]):
            result = self.engine.evaluate({"symbol": "TRX", "price": 1.0})
        self.assertEqual("V2_SYMBOL_EXCLUDED", result["reject_reason"])

    def test_missing_1d_data_does_not_crash_and_falls_back_to_4h_anchor_by_default(self):
        # 21.08.2026: nowsze pary bez 200+ dziennych swiec juz NIE sa
        # twardo odrzucane (V2_1D_DATA_NA) - 4h staje sie kotwica kierunku,
        # o ile 1h (swing/15m/5m) sie z nim zgadza. compute_indicators
        # NIGDY nie powinien byc wolany dla tf="1d", gdy 1D nie ma danych.
        swing = _down_swing_1h()
        m15 = _15m_trigger_series(zone_near=110.0, direction="SHORT")
        self._set_frames("BTC", d1={}, h4=_flat_ohlcv(300), h1=swing, m15=m15)

        def fake_compute_indicators(ohlcv, tf="1h"):
            self.assertNotEqual("1d", tf, "1D nie powinien byc liczony, gdy brak danych 1D")
            return indicator("down", atr=1.0)

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators):
            result = self.engine.evaluate({"symbol": "BTC", "price": 108.0})
        self.assertEqual("SHORT", result["direction"])
        self.assertIsNone(result["reject_reason"])
        self.assertIsNone(result["bias_1d"])
        self.assertEqual("SHORT", result["bias_4h"])
        self.assertIn("V2_1D_CTX_NA", result["reasons"])

    def test_short_1d_history_with_insufficient_indicators_falls_back_to_4h_anchor(self):
        # Regresja 21.08.2026: dla 1D z JAKIMIS swiecami, ale za malo na
        # wskazniki (np. 46 < 200 wymagane pod EMA200 - typowe dla nowszych
        # par/akcji jak AAPL/AMD/ASML), compute_indicators zwraca PUSTY dict
        # {} (nie None). `{} is not None` == True, wiec kod mylnie wchodzil
        # w galaz "mam dzialajace 1D", liczyl bias z pustego slownika
        # (=NEUTRAL) i twardo odrzucal przez V2_1D_NO_BIAS - fallback na
        # kotwice 4h (napisany dokladnie po ten przypadek) nigdy sie nie
        # uruchamial. To odroznia ten test od
        # test_missing_1d_data_does_not_crash_and_falls_back_to_4h_anchor_by_default
        # powyzej, ktory testuje ZUPELNY brak swiec 1D (d1={}), nie "za malo".
        swing = _down_swing_1h()
        m15 = _15m_trigger_series(zone_near=110.0, direction="SHORT")
        self._set_frames("BTC", d1=_flat_ohlcv(46), h4=_flat_ohlcv(300), h1=swing, m15=m15)

        def fake_compute_indicators(ohlcv, tf="1h"):
            if tf == "1d":
                return {}  # za malo swiec na wskazniki, ale d1_closes byl niepusty
            return indicator("down", atr=1.0)

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators):
            result = self.engine.evaluate({"symbol": "BTC", "price": 108.0})
        self.assertEqual("SHORT", result["direction"])
        self.assertNotEqual("V2_1D_NO_BIAS", result.get("reject_reason"))
        self.assertIsNone(result["bias_1d"])
        self.assertEqual("SHORT", result["bias_4h"])
        self.assertIn("V2_1D_CTX_NA", result["reasons"])

    def test_missing_1d_data_with_neutral_4h_still_follows_1h(self):
        swing = _down_swing_1h()
        m15 = _15m_trigger_series(zone_near=110.0, direction="SHORT")
        self._set_frames("BTC", d1={}, h4=_flat_ohlcv(300), h1=swing, m15=m15)

        def fake_compute_indicators(ohlcv, tf="1h"):
            if tf == "4h":
                return indicator("up", extra={"supertrend": {"is_up": None}, "ema_fast_above_slow": False})
            return indicator("down", atr=1.0)

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators):
            result = self.engine.evaluate({"symbol": "BTC", "price": 108.0})
        self.assertIsNone(result.get("reject_reason"))
        self.assertEqual("SHORT", result["direction"])
        self.assertIn("V2_4H_CTX_NA", result["reasons"])

    def test_missing_1d_never_hard_rejects(self):
        """1D nie jest bramką — nawet gdy stary flag kotwicy jest wyłączony."""
        swing = _down_swing_1h()
        m15 = _15m_trigger_series(zone_near=110.0, direction="SHORT")
        self._set_frames("BTC", d1={}, h4=_flat_ohlcv(300), h1=swing, m15=m15)

        def fake_compute_indicators(ohlcv, tf="1h"):
            return indicator("down", atr=1.0)

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators), \
                patch.object(config, "DAYTRADING_V2_ALLOW_4H_ANCHOR_WITHOUT_1D", False):
            result = self.engine.evaluate({"symbol": "BTC", "price": 108.0})
        self.assertNotEqual("V2_1D_DATA_NA", result.get("reject_reason"))
        self.assertEqual("SHORT", result["direction"])

    def test_missing_5m_data_does_not_block_signal(self):
        # Punkt 12: brak 5m NIE jest twardym blokiem - pelny happy path bez
        # danych 5m powinien nadal dac sygnal (jesli reszta kaskady zgadza sie).
        self._run_full_happy_path(with_5m=False)

    def test_full_happy_path_produces_long_signal(self):
        self._run_full_happy_path(with_5m=True)

    def _run_full_happy_path(self, with_5m: bool):
        swing = _up_swing_1h(low=100.0, high=120.0, pad=8, gap_bars=15, total=40)
        # strefa retracement 0.5/0.618 dla swingu 100->120: 0.5 -> 110.0, 0.618 -> 107.64
        m15 = _15m_trigger_series(zone_near=110.0, direction="LONG")
        self._set_frames(
            "BTC", d1=_flat_ohlcv(260), h4=_flat_ohlcv(260), h1=swing, m15=m15,
            m5=_flat_ohlcv(60) if with_5m else {},
        )

        def fake_compute_indicators(ohlcv, tf="1h"):
            if tf in ("1d", "4h"):
                return indicator("up")
            if tf == "1h":
                return indicator("up", atr=1.0)
            return {}

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators):
            result = self.engine.evaluate({"symbol": "BTC", "price": 112.0}, now_ts=10_000_000.0)
        self.assertEqual("LONG", result["direction"])
        self.assertIsNone(result["reject_reason"])
        self.assertLess(result["sl_price"], result["price"])
        self.assertGreater(result["tp1_price"], result["price"])
        self.assertGreater(result["tp2_price"], result["tp1_price"])
        return result

    def test_4h_disagreeing_with_1d_follows_4h(self):
        swing = _down_swing_1h()
        m15 = _15m_trigger_series(zone_near=110.0, direction="SHORT")
        self._set_frames("BTC", d1=_flat_ohlcv(260), h4=_flat_ohlcv(260), h1=swing, m15=m15)

        def fake_compute_indicators(ohlcv, tf="1h"):
            if tf == "1d":
                return indicator("up")
            if tf == "4h":
                return indicator("down")
            return indicator("down", atr=1.0)

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators):
            result = self.engine.evaluate({"symbol": "BTC", "price": 108.0})
        self.assertIsNone(result.get("reject_reason"))
        self.assertEqual("SHORT", result["direction"])
        self.assertEqual("LONG", result["bias_1d"])
        self.assertEqual("SHORT", result["bias_4h"])
        self.assertIn("V2_1D_CTX_OPPOSE(LONG)", result["reasons"])
        self.assertIn("V2_4H_CTX_ALIGN", result["reasons"])

    def test_4h_oppose_does_not_block_1h_long(self):
        swing = _up_swing_1h()
        m15 = _15m_trigger_series(zone_near=110.0, direction="LONG")
        self._set_frames("BTC", d1=_flat_ohlcv(260), h4=_flat_ohlcv(260), h1=swing, m15=m15)

        def fake_compute_indicators(ohlcv, tf="1h"):
            if tf == "4h":
                return indicator("down")
            return indicator("up", atr=1.0)

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators):
            result = self.engine.evaluate({"symbol": "BTC", "price": 112.0}, now_ts=10_000_000.0)
        self.assertIsNone(result.get("reject_reason"), result)
        self.assertEqual("LONG", result["direction"])
        self.assertEqual("SHORT", result["bias_4h"])
        self.assertIn("V2_4H_CTX_OPPOSE(SHORT)", result["reasons"])
        self.assertLess(result["_htf_size_mult"], 1.0)

    def test_1h_neutral_rejects(self):
        swing = _up_swing_1h()
        m15 = _15m_trigger_series(zone_near=110.0, direction="LONG")
        self._set_frames("BTC", d1=_flat_ohlcv(260), h4=_flat_ohlcv(260), h1=swing, m15=m15)

        def fake_compute_indicators(ohlcv, tf="1h"):
            if tf == "1h":
                return indicator("up", extra={"supertrend": {"is_up": None}, "ema_fast_above_slow": False})
            return indicator("up")

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators):
            result = self.engine.evaluate({"symbol": "BTC", "price": 112.0})
        self.assertEqual("V2_1H_NO_BIAS", result["reject_reason"])

    def test_stale_4h_does_not_block_when_1h_and_15m_fresh(self):
        from daytrading_engine_v2 import klines_stale_reason
        now = 1_000_000.0
        frames = {
            "4H": {"timestamps": [(now - 20 * 3600) * 1000]},
            "1H": {"timestamps": [(now - 30 * 60) * 1000]},
            "15m": {"timestamps": [(now - 5 * 60) * 1000]},
        }
        self.assertIsNone(klines_stale_reason(frames, now_ts=now))

    def test_no_1h_swing_rejects(self):
        # 1h plaski - brak swingu spelniajacego filtr ruch x ATR
        self._set_frames("BTC", d1=_flat_ohlcv(260), h4=_flat_ohlcv(260), h1=_flat_ohlcv(260), m15=_flat_ohlcv(30))

        def fake_compute_indicators(ohlcv, tf="1h"):
            return indicator("up")

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators):
            result = self.engine.evaluate({"symbol": "BTC", "price": 105.0})
        self.assertEqual("V2_NO_1H_SWING", result["reject_reason"])

    def test_no_matching_impulse_rejects_without_wrong_side_sl(self):
        # bias 1h = SHORT, na 1h tylko swing UP → brak impulsu DOWN
        swing = _up_swing_1h()
        self._set_frames("BTC", d1=_flat_ohlcv(260), h4=_flat_ohlcv(260), h1=swing, m15=_flat_ohlcv(30))

        def fake_compute_indicators(ohlcv, tf="1h"):
            if tf == "1h":
                return indicator("down", atr=1.0)
            return indicator("up")

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators):
            result = self.engine.evaluate({"symbol": "BTC", "price": 105.0})
        self.assertEqual("V2_NO_IMPULSE_SWING", result["reject_reason"])
        self.assertNotEqual("SHORT", result["direction"])

    def test_pullback_swing_uses_older_impulse_not_mismatch(self):
        h1 = _up_impulse_then_pullback_1h()
        m15 = _15m_trigger_series(zone_near=110.0, direction="LONG")
        self._set_frames("BTC", d1=_flat_ohlcv(260), h4=_flat_ohlcv(260), h1=h1, m15=m15)

        def fake_compute_indicators(ohlcv, tf="1h"):
            if tf in ("1d", "4h"):
                return indicator("up")
            return indicator("up", atr=1.0)

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators):
            result = self.engine.evaluate({"symbol": "BTC", "price": 112.0}, now_ts=10_000_000.0)
        self.assertIsNone(result.get("reject_reason"))
        self.assertEqual("LONG", result["direction"])
        self.assertLess(result["sl_price"], 100.0)  # SL ze startu IMPULSU (100), nie korekty (108)
        self.assertGreater(result["tp2_price"], result["tp1_price"])

    def test_impulse_older_than_max_age_rejects(self):
        h1 = _up_impulse_then_pullback_1h(total=55)
        m15 = _15m_trigger_series(zone_near=110.0, direction="LONG")
        self._set_frames("BTC", d1=_flat_ohlcv(260), h4=_flat_ohlcv(260), h1=h1, m15=m15)

        def fake_compute_indicators(ohlcv, tf="1h"):
            return indicator("up", atr=1.0)

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators), \
             patch.object(config, "DAYTRADING_V2_IMPULSE_MAX_AGE_BARS", 10):
            result = self.engine.evaluate({"symbol": "BTC", "price": 112.0}, now_ts=10_000_000.0)
        self.assertEqual("V2_IMPULSE_TOO_OLD", result["reject_reason"])

    def test_price_beyond_impulse_start_is_broken(self):
        swing = _up_swing_1h(low=100.0, high=120.0)
        m15 = _15m_trigger_series(zone_near=110.0, direction="LONG")
        self._set_frames("BTC", d1=_flat_ohlcv(260), h4=_flat_ohlcv(260), h1=swing, m15=m15)

        def fake_compute_indicators(ohlcv, tf="1h"):
            return indicator("up", atr=1.0)

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators):
            result = self.engine.evaluate({"symbol": "BTC", "price": 99.0}, now_ts=10_000_000.0)
        self.assertEqual("V2_IMPULSE_BROKEN", result["reject_reason"])

    def test_no_15m_trigger_without_retest_reclaim_rejects(self):
        swing = _up_swing_1h(low=100.0, high=120.0)
        # 116 jest POWYZEJ 0.382 (112.36) - cena nigdy nie wchodzi w pasmo
        m15 = _flat_ohlcv(30, price=116.0)
        self._set_frames("BTC", d1=_flat_ohlcv(260), h4=_flat_ohlcv(260), h1=swing, m15=m15)

        def fake_compute_indicators(ohlcv, tf="1h"):
            return indicator("up")

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators):
            result = self.engine.evaluate({"symbol": "BTC", "price": 116.0})
        self.assertEqual("V2_NO_15M_TRIGGER", result["reject_reason"])

    def test_5m_clear_opposite_vetoes_otherwise_valid_signal(self):
        swing = _up_swing_1h(low=100.0, high=120.0)
        m15 = _15m_trigger_series(zone_near=110.0, direction="LONG")
        # 5m: 2 ostatnie swiece wyraznie spadkowe -> weto LONG
        m5 = {"opens": [112.0, 112.0], "closes": [111.0, 110.0]}
        self._set_frames("BTC", d1=_flat_ohlcv(260), h4=_flat_ohlcv(260), h1=swing, m15=m15, m5=m5)

        def fake_compute_indicators(ohlcv, tf="1h"):
            return indicator("up")

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators):
            result = self.engine.evaluate({"symbol": "BTC", "price": 112.0})
        self.assertEqual("V2_5M_VETO", result["reject_reason"])

    def test_signal_without_fill_does_not_consume_swing(self):
        result1 = self._run_full_happy_path(with_5m=False)
        self.assertIsNone(result1["reject_reason"])
        def fake_compute_indicators(ohlcv, tf="1h"):
            if tf in ("1d", "4h"):
                return indicator("up")
            if tf == "1h":
                return indicator("up", atr=1.0)
            return {}
        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators):
            result2 = self.engine.evaluate({"symbol": "BTC", "price": 112.0}, now_ts=10_000_000.0)
        self.assertIsNone(result2.get("reject_reason"), result2)

    def test_actual_fill_consumes_swing(self):
        result1 = self._run_full_happy_path(with_5m=False)
        self.engine.notify_entry_fill("BTC", result1["swing"]["end"]["index"])
        with patch("daytrading_engine_v2.compute_indicators", return_value=indicator("up", atr=1.0)):
            result2 = self.engine.evaluate({"symbol": "BTC", "price": 112.0}, now_ts=10_000_000.0)
        self.assertEqual("V2_SWING_ALREADY_TRADED", result2["reject_reason"])

    def test_after_tp_same_filled_swing_remains_consumed(self):
        first = self._run_full_happy_path(with_5m=False)
        self.assertIsNone(first["reject_reason"])
        self.engine.notify_entry_fill("BTC", first["swing"]["end"]["index"])
        self.engine.notify_exit("BTC", "LONG", "tp", ts=10_000_000.0, pnl=12.0)

        def fake_compute_indicators(ohlcv, tf="1h"):
            return indicator("up", atr=1.0)

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators):
            later = self.engine.evaluate({"symbol": "BTC", "price": 112.0}, now_ts=10_000_001.0)
        self.assertEqual("V2_SWING_ALREADY_TRADED", later.get("reject_reason"))

    def test_after_single_sl_same_filled_swing_remains_consumed(self):
        first = self._run_full_happy_path(with_5m=False)
        self.assertIsNone(first["reject_reason"])
        self.engine.notify_entry_fill("BTC", first["swing"]["end"]["index"])
        self.engine.notify_exit("BTC", "LONG", "sl", ts=10_000_000.0, pnl=-8.0)

        def fake_compute_indicators(ohlcv, tf="1h"):
            return indicator("up", atr=1.0)

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators):
            row = self.engine.evaluate({"symbol": "BTC", "price": 112.0}, now_ts=10_000_001.0)
        self.assertEqual("V2_SWING_ALREADY_TRADED", row.get("reject_reason"))

    def test_five_losses_pause_15_min(self):
        t0 = 10_000_000.0
        for i in range(5):
            self.engine.notify_exit("BTC", "LONG", "sl", ts=t0 + i, pnl=-1.0)
        swing = _up_swing_1h(low=100.0, high=120.0)
        m15 = _15m_trigger_series(zone_near=110.0, direction="LONG")
        self._set_frames("BTC", d1=_flat_ohlcv(260), h4=_flat_ohlcv(260), h1=swing, m15=m15)

        def fake_compute_indicators(ohlcv, tf="1h"):
            return indicator("up", atr=1.0)

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators), \
             patch.object(config, "DAYTRADING_V2_LOSS_STREAK_PAUSE_N", 5), \
             patch.object(config, "DAYTRADING_V2_LOSS_STREAK_PAUSE_MIN", 15):
            blocked = self.engine.evaluate({"symbol": "BTC", "price": 112.0}, now_ts=t0 + 10 * 60)
            self.assertEqual("V2_LOSS_STREAK_PAUSE", blocked["reject_reason"])
            after = self.engine.evaluate({"symbol": "BTC", "price": 112.0}, now_ts=t0 + 16 * 60)
        self.assertIsNone(after.get("reject_reason"), after)
        self.assertEqual("LONG", after["direction"])

    def test_win_resets_loss_streak(self):
        t0 = 10_000_000.0
        for _ in range(4):
            self.engine.notify_exit("BTC", "LONG", "sl", ts=t0, pnl=-1.0)
        self.engine.notify_exit("BTC", "LONG", "tp", ts=t0, pnl=2.0)
        swing = _up_swing_1h(low=100.0, high=120.0)
        m15 = _15m_trigger_series(zone_near=110.0, direction="LONG")
        self._set_frames("BTC", d1=_flat_ohlcv(260), h4=_flat_ohlcv(260), h1=swing, m15=m15)

        def fake_compute_indicators(ohlcv, tf="1h"):
            return indicator("up", atr=1.0)

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators):
            row = self.engine.evaluate({"symbol": "BTC", "price": 112.0}, now_ts=t0 + 1)
        self.assertIsNone(row.get("reject_reason"), row)

    def test_loss_streak_is_per_symbol(self):
        t0 = 10_000_000.0
        for _ in range(5):
            self.engine.notify_exit("ETH", "LONG", "sl", ts=t0, pnl=-1.0)
        swing = _up_swing_1h(low=100.0, high=120.0)
        m15 = _15m_trigger_series(zone_near=110.0, direction="LONG")
        self._set_frames("BTC", d1=_flat_ohlcv(260), h4=_flat_ohlcv(260), h1=swing, m15=m15)

        def fake_compute_indicators(ohlcv, tf="1h"):
            return indicator("up", atr=1.0)

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators):
            row = self.engine.evaluate({"symbol": "BTC", "price": 112.0}, now_ts=t0 + 1)
        self.assertIsNone(row.get("reject_reason"), row)

    def test_nearby_sr_below_min_r_is_ignored_tp1_stays_1r(self):
        swing = _up_swing_1h(low=100.0, high=120.0)
        m15 = _15m_trigger_series(zone_near=110.0, direction="LONG")
        self._set_frames("BTC", d1=_flat_ohlcv(260), h4=_flat_ohlcv(260), h1=swing, m15=m15)

        def fake_compute_indicators(ohlcv, tf="1h"):
            if tf == "1h":
                return indicator("up", extra={"support_resistance": {
                    "supports": [], "resistances": [{"price": 112.5}]}})
            return indicator("up")

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators), \
             patch.object(config, "DAYTRADING_V2_MIN_TP1_R_RATIO", 0.6):
            result = self.engine.evaluate({"symbol": "BTC", "price": 112.0}, now_ts=10_000_000.0)
        self.assertIsNone(result.get("reject_reason"))
        self.assertEqual("LONG", result["direction"])
        self.assertGreaterEqual(result["tp1_r"], 0.6)
        self.assertGreater(result["tp2_price"], result["tp1_price"])

    def test_15m_trigger_requires_overlap_with_050_0618_band(self):
        from daytrading_engine_v2 import DayTradingEngineV2 as E
        # LONG: 0.5=110, 0.618=107.64. Wick tylko do 110.4 = poza pasmem.
        n = 20
        frame = {
            "closes": [112.0] * n, "highs": [113.0] * n, "lows": [110.4] * n, "opens": [112.0] * n,
        }
        self.assertFalse(E._check_15m_trigger(frame, 110.0, 107.64, "LONG"))
        frame["lows"] = [108.0] * n
        frame["closes"] = [111.0] * (n - 1) + [110.5]
        self.assertTrue(E._check_15m_trigger(frame, 110.0, 107.64, "LONG"))

    def test_shallow_0382_touch_reclaim_05_produces_long(self):
        """Plytszy retracement (0.382) + reclaim 0.5 = setup dnia, nie tylko 0.5-0.618."""
        swing = _up_swing_1h(low=100.0, high=120.0)
        n = 30
        base = 114.0
        m15 = {
            "opens": [base] * n, "closes": [base] * n,
            "highs": [base + 1] * n, "lows": [base - 1] * n,
        }
        m15["lows"][n - 4] = 112.0   # 0.382 = 112.36; nie dochodzi do 0.5=110
        m15["closes"][n - 1] = 113.0  # reclaim ponad 0.5
        self._set_frames("BTC", d1=_flat_ohlcv(260), h4=_flat_ohlcv(260), h1=swing, m15=m15)

        def fake_compute_indicators(ohlcv, tf="1h"):
            return indicator("up", atr=1.0)

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators):
            result = self.engine.evaluate({"symbol": "BTC", "price": 113.0}, now_ts=10_000_000.0)
        self.assertIsNone(result.get("reject_reason"), result)
        self.assertEqual("LONG", result["direction"])

    def test_check_15m_uses_reclaim_level_independent_of_band_edge(self):
        from daytrading_engine_v2 import DayTradingEngineV2 as E
        n = 20
        frame = {
            "closes": [113.0] * n, "highs": [115.0] * n, "lows": [112.0] * n, "opens": [114.0] * n,
        }
        # touch 0.382-0.618, close 113 >= reclaim 110
        self.assertTrue(E._check_15m_trigger(
            frame, 112.36, 107.64, "LONG", lookback=8, reclaim_level=110.0,
        ))
        # ten sam wick, ale reclaim wymagany na 114 → za wysoko
        self.assertFalse(E._check_15m_trigger(
            frame, 112.36, 107.64, "LONG", lookback=8, reclaim_level=114.0,
        ))

    def test_nearest_1h_level_skips_levels_inside_min_distance(self):
        from daytrading_engine_v2 import DayTradingEngineV2 as E
        ind = {"support_resistance": {"resistances": [{"price": 101.0}, {"price": 108.0}],
                                      "supports": []}, "pivot_points": {}}
        self.assertAlmostEqual(108.0, E._nearest_1h_level(ind, 100.0, "LONG", min_distance=5.0))
        self.assertIsNone(E._nearest_1h_level(ind, 100.0, "LONG", min_distance=20.0))

    def test_sl_too_tight_vs_round_trip_cost_rejects(self):
        # Maly swing (span=0.5) + mikroskopijny ATR - i cena wejscia, i SL
        # (swing start +/- bufor) siedza bardzo blisko siebie w wartosciach
        # bezwzglednych, wiec koszt round-trip zjada zbyt duzy procent ryzyka.
        total = 40
        highs = [100.4] * total
        lows = [100.4] * total
        lows[8] = 100.0
        highs[8 + 15] = 100.5
        swing = {"opens": [100.4] * total, "highs": highs, "lows": lows, "closes": [100.4] * total}
        # strefa retracement dla swingu 100.0->100.5: 0.5 -> 100.25, 0.618 -> 100.191
        m15 = _15m_trigger_series(zone_near=100.25, direction="LONG")
        self._set_frames("BTC", d1=_flat_ohlcv(260), h4=_flat_ohlcv(260), h1=swing, m15=m15)

        def fake_compute_indicators(ohlcv, tf="1h"):
            if tf == "1h":
                return indicator("up", atr=0.0001)  # bufor SL prawie zerowy
            return indicator("up")

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators), \
             patch.object(config, "DAYTRADING_V2_MIN_SL_VS_COST_MULT", 3.5), \
             patch.object(config, "COMMISSION_RATE", 0.0006), \
             patch.object(config, "SLIPPAGE", 0.0008):
            result = self.engine.evaluate({"symbol": "BTC", "price": 100.3})
        self.assertEqual("V2_SL_TOO_TIGHT_VS_COST", result["reject_reason"])


class TestDayTradingEngineV2Generate(unittest.TestCase):
    """generate() - filtr uniwersum PRZED kosztowna kaskada evaluate().
    Bez tego live V2 odpalalby pelna 5-timeframe analize na CALYM
    uniwersum (setki symboli) co skan, zamiast tylko top-N po wolumenie -
    dokladnie problem, ktory caly epik rate-limitingu mial rozwiazac."""

    def setUp(self):
        self.feeder = FakeFeeder()
        self.engine = DayTradingEngineV2(self.feeder)

    def test_only_top_n_by_volume_get_full_evaluate_rest_get_cheap_path(self):
        # batch_size >= cap: caly target sie rozgrzewa w JEDNYM cyklu, wiec
        # ten test nadal wprost pokazuje "top-N po wolumenie" bez wchodzenia
        # w wieloetapowy pacing (ten jest osobno w
        # TestDayTradingEngineV2GenerateColdStartPacing nizej).
        evaluated = []
        self.engine.evaluate = lambda coin, now_ts=None: evaluated.append(coin["symbol"]) or {
            "symbol": coin["symbol"], "direction": "NEUTRAL", "reject_reason": None,
        }
        coins = [{"symbol": f"C{i:02d}", "price": 1.0, "blofin_quote_volume_24h": 1_000_000 - i}
                 for i in range(50)]
        with patch.object(config, "DAYTRADING_V2_MAX_CANDIDATES", 30), \
             patch.object(config, "DAYTRADING_V2_COLD_START_BATCH_SIZE", 30), \
             patch.object(config, "MIN_VOLUME_24H_USD", 0):
            rows = self.engine.generate(coins)
        self.assertEqual(30, len(evaluated))
        self.assertEqual([f"C{i:02d}" for i in range(30)], evaluated)
        self.assertEqual(50, len(rows))
        self.assertEqual(20, sum(r.get("reject_reason") == "V2_NOT_IN_LIQUID_TOP" for r in rows))

    def test_non_top_candidates_get_spread_not_total_silence(self):
        self.engine.evaluate = lambda coin, now_ts=None: {"symbol": coin["symbol"], "direction": "NEUTRAL"}
        coins = [{"symbol": "TOP", "price": 100.0, "blofin_quote_volume_24h": 10_000_000}] + [
            {"symbol": f"REST{i}", "price": 1.0, "blofin_quote_volume_24h": 1.0,
             "blofin_bid": 0.999, "blofin_ask": 1.001} for i in range(3)
        ]
        with patch.object(config, "DAYTRADING_V2_MAX_CANDIDATES", 1), \
             patch.object(config, "MIN_VOLUME_24H_USD", 0):
            rows = self.engine.generate(coins)
        rest_rows = [r for r in rows if r["symbol"].startswith("REST")]
        self.assertEqual(3, len(rest_rows))
        for row in rest_rows:
            self.assertEqual("V2_NOT_IN_LIQUID_TOP", row["reject_reason"])
            self.assertTrue(row["details"]["spread_only"])
            self.assertAlmostEqual(0.2, row["details"]["spread_pct"], places=2)

    def test_excluded_symbols_never_reach_evaluate_via_generate(self):
        evaluated = []
        self.engine.evaluate = lambda coin, now_ts=None: evaluated.append(coin["symbol"]) or {}
        coins = [{"symbol": "TRX", "price": 1.0, "blofin_quote_volume_24h": 999_000_000},
                 {"symbol": "BTC", "price": 100.0, "blofin_quote_volume_24h": 1.0}]
        with patch.object(config, "DAYTRADING_V2_EXCLUDED_SYMBOLS", ["TRX"]), \
             patch.object(config, "MIN_VOLUME_24H_USD", 0):
            rows = self.engine.generate(coins)
        self.assertNotIn("TRX", evaluated)
        self.assertEqual(1, len(rows))  # TRX odfiltrowany calkowicie, nawet jako "poza topem"

    def test_low_volume_pairs_stay_in_full_universe(self):
        evaluated = []
        self.engine.evaluate = lambda coin, now_ts=None: evaluated.append(coin["symbol"]) or {
            "symbol": coin["symbol"], "direction": "NEUTRAL", "reject_reason": None,
        }
        coins = [
            {"symbol": "BTC", "price": 1.0, "blofin_quote_volume_24h": 50_000_000},
            {"symbol": "DUST", "price": 1.0, "blofin_quote_volume_24h": 12.0},
        ]
        with patch.object(config, "DAYTRADING_V2_COLD_START_BATCH_SIZE", 8):
            rows = self.engine.generate(coins)
        self.assertEqual(["BTC", "DUST"], evaluated)
        self.assertEqual(0, sum(r.get("reject_reason") == "V2_NOT_IN_LIQUID_TOP" for r in rows))
        self.assertEqual([], self.engine.generate([]))
        self.assertEqual([], self.engine.generate(None))

    def test_runtime_decides_only_once_per_closed_15m_candle(self):
        evaluated = []
        bar_ts = [1_787_600_000_000]

        def fake_fetch(symbol, bar, limit):
            if bar == "15m":
                return {"timestamps": [bar_ts[0]], "closes": [1.0]}
            return {}

        self.engine._fetch = fake_fetch
        self.engine.evaluate = lambda coin, now_ts=None: evaluated.append(coin["symbol"]) or {
            "symbol": coin["symbol"], "direction": "LONG", "price": coin["price"],
            "reject_reason": None,
        }
        coin = {"symbol": "BTC", "price": 100.0, "blofin_quote_volume_24h": 1_000_000}
        first = self.engine.generate([coin])[0]
        second = self.engine.generate([{**coin, "price": 101.0}])[0]
        self.assertEqual(["BTC"], evaluated)
        self.assertTrue(first["decision_fresh"])
        self.assertFalse(second["decision_fresh"])
        self.assertEqual(101.0, second["price"])

        bar_ts[0] += 900_000
        third = self.engine.generate([{**coin, "price": 102.0}])[0]
        self.assertEqual(["BTC", "BTC"], evaluated)
        self.assertTrue(third["decision_fresh"])

    def test_ws_disconnected_still_applies_safe_candidate_ceiling(self):
        # Opcjonalny sufit (MAX_CANDIDATES>0) nadal tnie. Domyslnie jest 0.
        evaluated = []
        self.engine.evaluate = lambda coin, now_ts=None: evaluated.append(coin["symbol"]) or {}
        coins = [{"symbol": f"C{i:02d}", "price": 1.0, "blofin_quote_volume_24h": 1_000_000 - i}
                 for i in range(20)]
        with patch.object(config, "DAYTRADING_V2_MAX_CANDIDATES", 5), \
             patch.object(config, "MIN_VOLUME_24H_USD", 0):
            self.engine.generate(coins)
        self.assertEqual(5, len(evaluated))

    def test_default_no_liquid_top_even_when_ws_is_down(self):
        self.assertEqual(0, int(config.DAYTRADING_V2_MAX_CANDIDATES))
        evaluated = []
        self.engine.evaluate = lambda coin, now_ts=None: evaluated.append(coin["symbol"]) or {
            "symbol": coin["symbol"], "direction": "NEUTRAL", "reject_reason": None,
        }
        coins = [{"symbol": f"C{i:02d}", "price": 1.0, "blofin_quote_volume_24h": 1_000_000 - i}
                 for i in range(50)]
        fake_ws = MagicMock()
        fake_ws.is_connected.return_value = False
        with patch.object(daytrading_engine_v2, "PUBLIC_WS", fake_ws), \
             patch.object(config, "DAYTRADING_V2_COLD_START_BATCH_SIZE", 50), \
             patch.object(config, "MIN_VOLUME_24H_USD", 0):
            rows = self.engine.generate(coins)
        self.assertEqual(50, len(evaluated))
        self.assertEqual(0, sum(r.get("reject_reason") == "V2_NOT_IN_LIQUID_TOP" for r in rows))

    def test_ws_connected_target_is_entire_universe_no_hard_cap(self):
        # 21.08.2026, druga iteracja: WS-connected NIE ma juz zadnego
        # plaskiego sufitu liczby kandydatow (config.
        # DAYTRADING_V2_MAX_CANDIDATES_WS_CONNECTED zostal usuniety) - target
        # to zawsze cale przefiltrowane wolumenem uniwersum. Bezpieczenstwo
        # pilnuje juz nie limit liczby kandydatow, tylko pacing partiami -
        # patrz TestDayTradingEngineV2GenerateColdStartPacing.
        self.assertFalse(hasattr(config, "DAYTRADING_V2_MAX_CANDIDATES_WS_CONNECTED"))
        evaluated = []
        self.engine.evaluate = lambda coin, now_ts=None: evaluated.append(coin["symbol"]) or {}
        coins = [{"symbol": f"C{i:02d}", "price": 1.0, "blofin_quote_volume_24h": 1_000_000 - i}
                 for i in range(200)]
        fake_ws = MagicMock()
        fake_ws.is_connected.return_value = True
        with patch.object(daytrading_engine_v2, "PUBLIC_WS", fake_ws), \
             patch.object(config, "DAYTRADING_V2_COLD_START_BATCH_SIZE", 200), \
             patch.object(config, "MIN_VOLUME_24H_USD", 0):
            self.engine.generate(coins)
        # batch_size >= caly target -> caly target sie rozgrzewa juz w 1 cyklu
        self.assertEqual(200, len(evaluated))


class TestDayTradingEngineV2GenerateColdStartPacing(unittest.TestCase):
    """21.08.2026, druga iteracja rate-limitingu (na wyrazna prosbe
    uzytkownika): REST nie pobiera calego target-setu (45 kandydatow przy
    WS-down, CALE uniwersum przy WS-connected) naraz, tylko partiami po
    DAYTRADING_V2_COLD_START_BATCH_SIZE nowych (nigdy niepobieranych w tej
    instancji silnika) symboli na cykl generate(). Juz "cieple" symbole sa
    oceniane co cykl (tanio, dzieki TTL+WS-merge w blofin_feed.py)."""

    def setUp(self):
        self.feeder = FakeFeeder()
        self.engine = DayTradingEngineV2(self.feeder)
        self.engine.evaluate = lambda coin, now_ts=None: {
            "symbol": coin["symbol"], "direction": "NEUTRAL", "reject_reason": None,
        }

    def test_store_warmup_skips_cold_start_batch(self):
        coins = [{"symbol": f"C{i:02d}", "price": 1.0, "blofin_quote_volume_24h": 1_000_000 - i}
                 for i in range(20)]
        fake_store = MagicMock()
        fake_store.candle_count.side_effect = lambda s, tf: 80 if tf in ("4H", "1H") else 0
        with patch.object(config, "DAYTRADING_V2_COLD_START_BATCH_SIZE", 8), \
             patch.object(config, "MIN_VOLUME_24H_USD", 0), \
             patch("market_store.STORE", fake_store):
            rows = self.engine.generate(coins)
        warmed = [r for r in rows if r.get("reject_reason") is None]
        self.assertEqual(20, len(warmed))

    def test_single_cycle_only_warms_a_bounded_batch_ws_down(self):
        coins = [{"symbol": f"C{i:02d}", "price": 1.0, "blofin_quote_volume_24h": 1_000_000 - i}
                 for i in range(45)]
        with patch.object(config, "DAYTRADING_V2_MAX_CANDIDATES", 45), \
             patch.object(config, "DAYTRADING_V2_COLD_START_BATCH_SIZE", 8), \
             patch.object(config, "MIN_VOLUME_24H_USD", 0):
            rows = self.engine.generate(coins)
        warmed = [r for r in rows if r.get("reject_reason") is None]
        warming = [r for r in rows if r.get("reject_reason") == "V2_COLD_START_WARMING_UP"]
        self.assertEqual(8, len(warmed))
        self.assertEqual([f"C{i:02d}" for i in range(8)], sorted(r["symbol"] for r in warmed))
        self.assertEqual(37, len(warming))  # 45 w targecie - 8 rozgrzanych w tym cyklu

    def test_single_cycle_only_warms_a_bounded_batch_ws_connected(self):
        # Cale uniwersum (200) jest w targecie (WS connected, brak sufitu),
        # ale tylko batch_size (8) nowych symboli dostaje kaskade w 1 cyklu -
        # to jest wlasnie mechanizm, ktory zastapil plaski sufit 60.
        coins = [{"symbol": f"C{i:02d}", "price": 1.0, "blofin_quote_volume_24h": 1_000_000 - i}
                 for i in range(200)]
        fake_ws = MagicMock()
        fake_ws.is_connected.return_value = True
        with patch.object(daytrading_engine_v2, "PUBLIC_WS", fake_ws), \
             patch.object(config, "DAYTRADING_V2_COLD_START_BATCH_SIZE", 8), \
             patch.object(config, "MIN_VOLUME_24H_USD", 0):
            rows = self.engine.generate(coins)
        warmed = [r for r in rows if r.get("reject_reason") is None]
        warming = [r for r in rows if r.get("reject_reason") == "V2_COLD_START_WARMING_UP"]
        self.assertEqual(8, len(warmed))
        self.assertEqual(192, len(warming))

    def test_ramps_up_to_full_universe_across_multiple_cycles(self):
        coins = [{"symbol": f"C{i:02d}", "price": 1.0, "blofin_quote_volume_24h": 1_000_000 - i}
                 for i in range(80)]
        fake_ws = MagicMock()
        fake_ws.is_connected.return_value = True
        ever_evaluated = set()
        with patch.object(daytrading_engine_v2, "PUBLIC_WS", fake_ws), \
             patch.object(config, "DAYTRADING_V2_COLD_START_BATCH_SIZE", 8), \
             patch.object(config, "MIN_VOLUME_24H_USD", 0):
            for _ in range(10):  # 80 symboli / batch 8 = 10 cykli do pelnego rozgrzania
                rows = self.engine.generate(coins)
                ever_evaluated.update(r["symbol"] for r in rows if r.get("reject_reason") is None)
        self.assertEqual(80, len(ever_evaluated))

    def test_already_warmed_symbols_are_evaluated_every_cycle_not_just_once(self):
        coins = [{"symbol": f"C{i:02d}", "price": 1.0, "blofin_quote_volume_24h": 1_000_000 - i}
                 for i in range(8)]
        with patch.object(config, "DAYTRADING_V2_MAX_CANDIDATES", 8), \
             patch.object(config, "DAYTRADING_V2_COLD_START_BATCH_SIZE", 8), \
             patch.object(config, "MIN_VOLUME_24H_USD", 0):
            rows_cycle1 = self.engine.generate(coins)
            rows_cycle2 = self.engine.generate(coins)
        self.assertEqual(8, sum(r.get("reject_reason") is None for r in rows_cycle1))
        self.assertEqual(8, sum(r.get("reject_reason") is None for r in rows_cycle2))

    def test_cold_start_batch_pacing_stays_safe_regardless_of_universe_size(self):
        # Sedno bezpieczenstwa nowego mechanizmu: burst REST na cykl zalezy
        # TYLKO od DAYTRADING_V2_COLD_START_BATCH_SIZE, nie od tego, jak duzy
        # jest target (45 czy 500 - bez znaczenia dla 1 cyklu).
        from rate_limiter import PUBLIC_BUCKET as _real_public_bucket
        batch = int(getattr(config, "DAYTRADING_V2_COLD_START_BATCH_SIZE", 8))
        cascade_intervals = 5  # 1D, 4H, 1H, 15m, 5m - patrz _fetch_frames
        worst_case_burst_s = (batch * cascade_intervals) / _real_public_bucket.refill_per_sec
        self.assertLess(
            worst_case_burst_s, 60.0,
            f"cold-start burst dla partii {batch} nowych symboli ({worst_case_burst_s:.1f}s) "
            "jest za dlugi - grozi ta sama kaskada 'Rate limit - czekam Ns' co przy incydencie 21.08.2026",
        )
        for universe_size in (45, 200, 1000):
            evaluated = []
            self.engine.evaluate = lambda coin, now_ts=None: evaluated.append(coin["symbol"]) or {
                "symbol": coin["symbol"], "direction": "NEUTRAL", "reject_reason": None,
            }
            coins = [{"symbol": f"U{i:04d}", "price": 1.0, "blofin_quote_volume_24h": 1_000_000 - i}
                     for i in range(universe_size)]
            fake_ws = MagicMock()
            fake_ws.is_connected.return_value = True
            fresh_engine = DayTradingEngineV2(self.feeder)
            fresh_engine.evaluate = self.engine.evaluate
            with patch.object(daytrading_engine_v2, "PUBLIC_WS", fake_ws), \
                 patch.object(config, "MIN_VOLUME_24H_USD", 0):
                fresh_engine.generate(coins)
            self.assertEqual(
                batch, len(evaluated),
                f"jeden cykl generate() z uniwersum {universe_size} rozgrzal {len(evaluated)} "
                f"symboli, oczekiwano dokladnie {batch} (pacing musi byc niezalezny od "
                "rozmiaru calego uniwersum)",
            )


class TestV2BinanceKlineFallback(unittest.TestCase):
    def test_explicit_test_feeder_does_not_read_runtime_market_store(self):
        from market_store import STORE

        feeder = FakeFeeder()
        feeder.blofin.frames[("BTC", "4H")] = _flat_ohlcv(10, 111.0)
        STORE.put_ohlcv("BTC", "4H", _flat_ohlcv(10, 999.0))
        try:
            engine = DayTradingEngineV2(feeder)
            out = engine._fetch("BTC", "4H", 260)
        finally:
            STORE.ohlcv.pop("BTC", None)
            STORE.ohlcv_ts.pop("BTC", None)
        self.assertEqual(111.0, out["closes"][-1])

    def test_uses_binance_when_blofin_4h_is_stale(self):
        import time as _t
        feeder = FakeFeeder()
        old_ms = int((_t.time() - 30_000) * 1000)
        feeder.blofin.frames[("BTC", "4H")] = {
            "timestamps": [old_ms], "opens": [1], "highs": [1], "lows": [1], "closes": [1],
        }
        fresh_ms = int(_t.time() * 1000)
        feeder.binance = FakeBlofin()
        feeder.binance.frames[("BTC", "4h")] = {
            "timestamps": [fresh_ms], "opens": [2], "highs": [2], "lows": [2], "closes": [2],
        }
        engine = DayTradingEngineV2(feeder)
        out = engine._fetch("BTC", "4H", 260)
        self.assertEqual([2], out.get("closes"))

    def test_keeps_blofin_when_frames_have_no_timestamps(self):
        feeder = FakeFeeder()
        feeder.blofin.frames[("BTC", "4H")] = _flat_ohlcv(10, 100.0)
        engine = DayTradingEngineV2(feeder)
        out = engine._fetch("BTC", "4H", 260)
        self.assertEqual(100.0, out["closes"][-1])


if __name__ == "__main__":
    unittest.main()
