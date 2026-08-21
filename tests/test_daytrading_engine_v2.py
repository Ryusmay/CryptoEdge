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

    def fetch_klines_ohlcv(self, symbol, bar="1H", limit=120):
        return self.frames.get((symbol, bar), {})


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
        self.assertIn("V2_4H_ANCHOR_NO_1D", result["reasons"])

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
        self.assertIn("V2_4H_ANCHOR_NO_1D", result["reasons"])

    def test_missing_1d_data_with_neutral_4h_gives_no_bias_reason(self):
        swing = _down_swing_1h()
        m15 = _15m_trigger_series(zone_near=110.0, direction="SHORT")
        self._set_frames("BTC", d1={}, h4=_flat_ohlcv(300), h1=swing, m15=m15)

        def fake_compute_indicators(ohlcv, tf="1h"):
            if tf == "4h":
                return indicator("up", extra={"supertrend": {"is_up": None}, "ema_fast_above_slow": False})
            return indicator("down", atr=1.0)

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators):
            result = self.engine.evaluate({"symbol": "BTC", "price": 108.0})
        self.assertEqual("V2_4H_NO_BIAS_NO_1D", result["reject_reason"])

    def test_missing_1d_data_hard_rejects_when_anchor_disabled(self):
        self._set_frames("BTC", d1={}, h4=_flat_ohlcv(300), h1=_up_swing_1h(), m15=_flat_ohlcv(30))

        def fake_compute_indicators(ohlcv, tf="1h"):
            return indicator("down", atr=1.0)

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators), \
                patch.object(config, "DAYTRADING_V2_ALLOW_4H_ANCHOR_WITHOUT_1D", False):
            result = self.engine.evaluate({"symbol": "BTC", "price": 100.0})
        self.assertEqual("V2_1D_DATA_NA", result["reject_reason"])

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

    def test_4h_disagreeing_with_1d_rejects(self):
        swing = _up_swing_1h()
        self._set_frames("BTC", d1=_flat_ohlcv(260), h4=_flat_ohlcv(260), h1=swing, m15=_flat_ohlcv(30))

        def fake_compute_indicators(ohlcv, tf="1h"):
            if tf == "1d":
                return indicator("up")
            if tf == "4h":
                return indicator("down")  # niezgodny z 1D
            return indicator("up")

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators):
            result = self.engine.evaluate({"symbol": "BTC", "price": 112.0})
        self.assertEqual("V2_4H_NOT_CONFIRMED", result["reject_reason"])

    def test_no_1h_swing_rejects(self):
        # 1h plaski - brak swingu spelniajacego filtr ruch x ATR
        self._set_frames("BTC", d1=_flat_ohlcv(260), h4=_flat_ohlcv(260), h1=_flat_ohlcv(260), m15=_flat_ohlcv(30))

        def fake_compute_indicators(ohlcv, tf="1h"):
            return indicator("up")

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators):
            result = self.engine.evaluate({"symbol": "BTC", "price": 105.0})
        self.assertEqual("V2_NO_1H_SWING", result["reject_reason"])

    def test_swing_direction_mismatched_with_bias_rejects(self):
        # bias 1D/4h = SHORT, ale 1h ma swing UP -> niespojnosc
        swing = _up_swing_1h()
        self._set_frames("BTC", d1=_flat_ohlcv(260), h4=_flat_ohlcv(260), h1=swing, m15=_flat_ohlcv(30))

        def fake_compute_indicators(ohlcv, tf="1h"):
            if tf in ("1d", "4h"):
                return indicator("down")
            return indicator("up")

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators):
            result = self.engine.evaluate({"symbol": "BTC", "price": 105.0})
        self.assertEqual("V2_SWING_DIRECTION_MISMATCH", result["reject_reason"])

    def test_no_15m_trigger_without_retest_reclaim_rejects(self):
        swing = _up_swing_1h(low=100.0, high=120.0)
        m15 = _flat_ohlcv(30, price=112.0)  # nigdy nie dotyka strefy, brak reclaim
        self._set_frames("BTC", d1=_flat_ohlcv(260), h4=_flat_ohlcv(260), h1=swing, m15=m15)

        def fake_compute_indicators(ohlcv, tf="1h"):
            return indicator("up")

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators):
            result = self.engine.evaluate({"symbol": "BTC", "price": 112.0})
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

    def test_one_entry_per_swing_second_call_on_same_swing_rejects(self):
        result1 = self._run_full_happy_path(with_5m=False)
        self.assertIsNone(result1["reject_reason"])
        # drugie wywolanie na TYM SAMYM swingu (te same dane, ten sam mock
        # compute_indicators musi byc aktywny) - powinno odrzucic.
        def fake_compute_indicators(ohlcv, tf="1h"):
            if tf in ("1d", "4h"):
                return indicator("up")
            if tf == "1h":
                return indicator("up", atr=1.0)
            return {}
        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators):
            result2 = self.engine.evaluate({"symbol": "BTC", "price": 112.0}, now_ts=10_000_000.0)
        self.assertEqual("V2_SWING_ALREADY_TRADED", result2["reject_reason"])

    def test_cooldown_after_exit_blocks_immediate_reentry(self):
        self.engine.notify_exit("BTC", "LONG", "tp", ts=9_999_970.0)  # 30s temu wzgledem now_ts ponizej
        swing = _up_swing_1h(low=100.0, high=120.0)
        m15 = _15m_trigger_series(zone_near=110.0, direction="LONG")
        self._set_frames("BTC", d1=_flat_ohlcv(260), h4=_flat_ohlcv(260), h1=swing, m15=m15)

        def fake_compute_indicators(ohlcv, tf="1h"):
            return indicator("up")

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators), \
             patch.object(config, "DAYTRADING_V2_COOLDOWN_AFTER_EXIT_MIN", 60):
            result = self.engine.evaluate({"symbol": "BTC", "price": 112.0}, now_ts=10_000_000.0)
        self.assertEqual("V2_COOLDOWN_AFTER_EXIT", result["reject_reason"])

    def test_cooldown_after_sl_same_side_is_longer_than_generic(self):
        self.engine.notify_exit("BTC", "LONG", "sl", ts=10_000_000.0 - 100 * 60)  # 100 min temu
        swing = _up_swing_1h(low=100.0, high=120.0)
        m15 = _15m_trigger_series(zone_near=110.0, direction="LONG")
        self._set_frames("BTC", d1=_flat_ohlcv(260), h4=_flat_ohlcv(260), h1=swing, m15=m15)

        def fake_compute_indicators(ohlcv, tf="1h"):
            return indicator("up")

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators), \
             patch.object(config, "DAYTRADING_V2_COOLDOWN_AFTER_EXIT_MIN", 60), \
             patch.object(config, "DAYTRADING_V2_COOLDOWN_AFTER_SL_SAME_SIDE_MIN", 240):
            result = self.engine.evaluate({"symbol": "BTC", "price": 112.0}, now_ts=10_000_000.0)
        # 100 min > generic cooldown (60) ale < SL-same-side cooldown (240)
        self.assertEqual("V2_COOLDOWN_AFTER_SL_SAME_SIDE", result["reject_reason"])

    def test_min_tp1_r_ratio_filters_too_tight_reward(self):
        swing = _up_swing_1h(low=100.0, high=120.0)
        m15 = _15m_trigger_series(zone_near=110.0, direction="LONG")
        self._set_frames("BTC", d1=_flat_ohlcv(260), h4=_flat_ohlcv(260), h1=swing, m15=m15)

        def fake_compute_indicators(ohlcv, tf="1h"):
            if tf == "1h":
                # resistance BARDZO blisko ceny wejscia -> TP1 << 1R
                return indicator("up", extra={"support_resistance": {
                    "supports": [], "resistances": [{"price": 112.5}]}})
            return indicator("up")

        with patch("daytrading_engine_v2.compute_indicators", side_effect=fake_compute_indicators), \
             patch.object(config, "DAYTRADING_V2_MIN_TP1_R_RATIO", 0.6):
            result = self.engine.evaluate({"symbol": "BTC", "price": 112.0})
        self.assertEqual("V2_TP1_TOO_SMALL_VS_RISK", result["reject_reason"])

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

    def test_generate_never_crashes_on_empty_universe(self):
        self.assertEqual([], self.engine.generate([]))
        self.assertEqual([], self.engine.generate(None))

    def test_ws_disconnected_still_applies_safe_candidate_ceiling(self):
        # PUBLIC_WS realny singleton bez polaczenia w testach - domyslna
        # sciezka (WS padl) MUSI dalej uzywac bezpiecznego sufitu.
        evaluated = []
        self.engine.evaluate = lambda coin, now_ts=None: evaluated.append(coin["symbol"]) or {}
        coins = [{"symbol": f"C{i:02d}", "price": 1.0, "blofin_quote_volume_24h": 1_000_000 - i}
                 for i in range(20)]
        with patch.object(config, "DAYTRADING_V2_MAX_CANDIDATES", 5), \
             patch.object(config, "MIN_VOLUME_24H_USD", 0):
            self.engine.generate(coins)
        self.assertEqual(5, len(evaluated))

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


if __name__ == "__main__":
    unittest.main()
