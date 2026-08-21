import time
import unittest
from unittest.mock import MagicMock, patch

import blofin_feed
import blofin_ws
from blofin_feed import BlofinFeed, _merge_ws_closed_candle
from rate_limiter import TokenBucket


def _ok_response(payload):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.text = ""
    return resp


class TestBlofinFeedRateLimiting(unittest.TestCase):
    """Regresja na realny problem 19-20.08: bot generowal wiecej zapytan niz
    limit Blofin pozwalal. Zamiast tylko reagowac na HTTP 429 (co robilismy
    wczesniej), _get() teraz proaktywnie czeka na token PRZED wyslaniem
    zapytania - sprawdzamy to bez sieci, na izolowanych wiadrach zamiast
    dzielonych modulowych singletonow (zeby testy nie byly zalezne od
    kolejnosci/siebie nawzajem)."""

    def _isolated_bucket(self, capacity=2.0, refill_per_sec=1000.0):
        # refill bardzo szybki domyslnie, zeby testy "normalnej sciezki" nie
        # byly przypadkowo zablokowane przez powolny refill - testy
        # wyczerpania wiadra jawnie ustawiaja wolniejszy refill.
        return TokenBucket(capacity=capacity, refill_per_sec=refill_per_sec)

    def test_get_consumes_one_token_per_request(self):
        bucket = self._isolated_bucket(capacity=3.0)
        feed = BlofinFeed()
        with patch.object(blofin_feed, "PUBLIC_BUCKET", bucket), \
             patch.object(feed.session, "get", return_value=_ok_response({"code": "0", "data": []})) as mock_get:
            feed._get("market/instruments")
        self.assertEqual(1, mock_get.call_count)
        self.assertAlmostEqual(2.0, bucket.tokens, places=1)

    def test_get_waits_instead_of_firing_when_bucket_is_empty_then_succeeds_after_refill(self):
        bucket = self._isolated_bucket(capacity=1.0, refill_per_sec=20.0)  # 1 token/0.05s
        bucket.try_acquire()  # oproznij od razu
        feed = BlofinFeed()
        with patch.object(blofin_feed, "PUBLIC_BUCKET", bucket), \
             patch.object(feed.session, "get", return_value=_ok_response({"code": "0", "data": []})) as mock_get:
            result = feed._get("market/instruments")
        # acquire() poczekalo na odnowe (szybki refill), wiec zapytanie finalnie poszlo.
        self.assertEqual(1, mock_get.call_count)
        self.assertIsNotNone(result)

    def test_get_gives_up_without_network_call_when_bucket_denies(self):
        bucket = MagicMock()
        bucket.acquire.return_value = False  # symulacja odmowy bez realnego czekania w tescie
        feed = BlofinFeed()
        with patch.object(blofin_feed, "PUBLIC_BUCKET", bucket), \
             patch.object(feed.session, "get") as mock_get:
            result = feed._get("market/instruments")
        # Kluczowe: zero wywolan sieciowych, bo lokalny limiter odmowil zanim
        # cokolwiek poszlo do Blofin (proaktywnie, nie reaktywnie na 429).
        self.assertEqual(0, mock_get.call_count)
        self.assertIsNone(result)
        self.assertIn("rate limit", feed.last_error)

class TestApiKeyPermissionsBestEffort(unittest.TestCase):
    """fetch_api_key_permissions() - endpoint/schemat NIE zostal potwierdzony
    w dokumentacji (patrz komentarz w kodzie), wiec funkcja musi byc
    maksymalnie defensywna: kazdy niepasujacy ksztalt zwraca None, nigdy nie
    rzuca wyjatku, nigdy nie zglasza falszywej pewnosci."""

    def test_returns_none_without_auth_configured(self):
        feed = BlofinFeed()
        with patch.object(feed, "_has_auth", return_value=False):
            self.assertIsNone(feed.fetch_api_key_permissions())

    def test_returns_none_when_bucket_denies(self):
        feed = BlofinFeed()
        bucket = MagicMock()
        bucket.acquire.return_value = False
        with patch.object(feed, "_has_auth", return_value=True), \
             patch.object(blofin_feed, "TRADING_BUCKET", bucket):
            self.assertIsNone(feed.fetch_api_key_permissions())

    def test_parses_permissions_list_from_recognized_field(self):
        feed = BlofinFeed()
        resp = _ok_response({"code": "0", "data": [{"permissions": ["read", "trade"]}]})
        with patch.object(feed, "_has_auth", return_value=True), \
             patch.object(feed.session, "get", return_value=resp):
            self.assertEqual(["READ", "TRADE"], feed.fetch_api_key_permissions())

    def test_parses_comma_separated_string_permissions(self):
        feed = BlofinFeed()
        resp = _ok_response({"code": "0", "data": {"perm": "read, trade"}})
        with patch.object(feed, "_has_auth", return_value=True), \
             patch.object(feed.session, "get", return_value=resp):
            self.assertEqual(["READ", "TRADE"], feed.fetch_api_key_permissions())

    def test_returns_none_on_non_200_status(self):
        feed = BlofinFeed()
        resp = MagicMock()
        resp.status_code = 404
        with patch.object(feed, "_has_auth", return_value=True), \
             patch.object(feed.session, "get", return_value=resp):
            self.assertIsNone(feed.fetch_api_key_permissions())

    def test_returns_none_on_unrecognized_schema_instead_of_guessing(self):
        feed = BlofinFeed()
        resp = _ok_response({"code": "0", "data": {"somethingElse": 123}})
        with patch.object(feed, "_has_auth", return_value=True), \
             patch.object(feed.session, "get", return_value=resp):
            self.assertIsNone(feed.fetch_api_key_permissions())

    def test_returns_none_on_network_exception_not_raise(self):
        feed = BlofinFeed()
        with patch.object(feed, "_has_auth", return_value=True), \
             patch.object(feed.session, "get", side_effect=ConnectionError("boom")):
            self.assertIsNone(feed.fetch_api_key_permissions())


class TestTradingBucketGatesPrivateGet(unittest.TestCase):
    def test_trading_bucket_gates_private_get_separately_from_public(self):
        bucket = MagicMock()
        bucket.acquire.return_value = False
        feed = BlofinFeed()
        with patch.object(blofin_feed, "TRADING_BUCKET", bucket), \
             patch.object(feed, "_has_auth", return_value=True), \
             patch.object(feed.session, "get") as mock_get:
            result = feed._private_get("/api/v1/account/positions")
        self.assertEqual(0, mock_get.call_count)
        self.assertIsNone(result)
        self.assertIn("rate limit", feed.last_error)

    def test_klines_return_stale_cache_without_network_call_when_bucket_below_20pct(self):
        # Swiece to najbardziej dyskrecjonalne zapytanie (najwieksza objetosc:
        # 4 interwaly x N kandydatow co skan) - przy niskim wiadrze oddajemy
        # stare dane zamiast zuzywac budzet potrzebny na ceny ochronne pozycji.
        import time as time_mod
        feed = BlofinFeed()
        stale_data = {"opens": [1.0], "closes": [1.0]}
        feed.ohlc_cache["ohlcv_BTC-USDT_15m_120"] = (time_mod.time() - 999, stale_data)
        bucket = MagicMock()
        bucket.level.return_value = 0.1  # ponizej progu 20%
        with patch.object(blofin_feed, "PUBLIC_BUCKET", bucket), \
             patch.object(feed.session, "get") as mock_get:
            result = feed.fetch_klines_ohlcv("BTC", bar="15m", limit=120)
        self.assertEqual(0, mock_get.call_count)
        self.assertEqual(stale_data, result)

    def test_klines_fetch_fresh_when_bucket_above_20pct(self):
        import time as time_mod
        feed = BlofinFeed()
        stale_data = {"opens": [1.0], "closes": [1.0]}
        feed.ohlc_cache["ohlcv_BTC-USDT_15m_120"] = (time_mod.time() - 999, stale_data)
        bucket = MagicMock()
        bucket.level.return_value = 0.9  # wysoko - normalna sciezka
        bucket.acquire.return_value = True
        with patch.object(blofin_feed, "PUBLIC_BUCKET", bucket), \
             patch.object(feed.session, "get", return_value=_ok_response({"code": "0", "data": []})) as mock_get:
            feed.fetch_klines_ohlcv("BTC", bar="15m", limit=120)
        self.assertGreaterEqual(mock_get.call_count, 1)

    def test_kline_cache_ttl_matches_bar_duration_not_flat_60_120s(self):
        # Realny problem: 1h/4h swiece odswiezaly sie niemal tak czesto jak
        # 5m (plaskie 60/120s), mimo ze ich bar zmienia sie dziesiatki razy
        # rzadziej - jedno z glownych zrodel nadmiarowych zapytan.
        self.assertEqual(30, blofin_feed._KLINE_CACHE_TTL_S["5m"])
        self.assertEqual(90, blofin_feed._KLINE_CACHE_TTL_S["15m"])
        self.assertEqual(180, blofin_feed._KLINE_CACHE_TTL_S["1H"])
        self.assertEqual(600, blofin_feed._KLINE_CACHE_TTL_S["4H"])
        self.assertGreater(blofin_feed._KLINE_CACHE_TTL_S["1H"], blofin_feed._KLINE_CACHE_TTL_S["15m"])
        self.assertGreater(blofin_feed._KLINE_CACHE_TTL_S["4H"], blofin_feed._KLINE_CACHE_TTL_S["1H"])

    def test_kline_cache_ttl_budget_utilization_stays_comfortably_under_public_bucket_capacity(self):
        # 20.08.2026: TTL dobrane wg realnego przeliczenia wzgledem calej
        # zbudowanej od tamtej pory infrastruktury (token bucket 5 req/s),
        # nie zgadywane "z ostroznosci" - przy 30 kandydatach (V1/V2
        # MAX_CANDIDATES) sumaryczne zuzycie budzetu ma zostac wyraznie
        # ponizej 5 req/s, zostawiajac margines na fast-tick i inne zapytania.
        candidates = 30
        bars = {"5m": 300, "15m": 900, "1H": 3600, "4H": 14400, "1D": 86400, "1W": 604800}
        total_req_per_s = sum(candidates / ttl for tf, ttl in blofin_feed._KLINE_CACHE_TTL_S.items())
        self.assertLess(total_req_per_s, 2.5)  # < 50% budzetu PUBLIC_BUCKET (5 req/s)
        self.assertGreater(total_req_per_s, 1.0)  # ale nie z powrotem do skrajnej ostroznosci (<20%)
        for tf, ttl in blofin_feed._KLINE_CACHE_TTL_S.items():
            self.assertGreaterEqual(bars[tf] / ttl, 8.0, f"{tf}: mniej niz 8 odswiezen na bar")

    def test_ws_connected_ttl_budget_is_meaningfully_lower_than_disconnected(self):
        # 20.08.2026: skoro WS realnie dowozi swiezosc (~1-2s od zamkniecia
        # bara przez _merge_ws_closed_candle) I bezpiecznie wykrywa luki
        # (patrz TestMergeWsClosedCandle), REST moze poluzowac TTL az do 1x
        # czasu zycia bara - naturalny sufit bezpieczenstwa (REST wciaz
        # okresowo resynchronizuje, ale WS robi cala robote w miedzyczasie).
        candidates = 30
        budget_disconnected = sum(candidates / ttl for ttl in blofin_feed._KLINE_CACHE_TTL_S.values())
        budget_connected = sum(candidates / ttl for ttl in blofin_feed._KLINE_CACHE_TTL_S_WS_CONNECTED.values())
        self.assertLess(budget_connected, budget_disconnected * 0.25)
        self.assertLess(budget_connected / 5.0, 0.05)  # < 5% budzetu gdy WS zyje (cel: ~2.9%)

    def test_ws_connected_ttls_are_never_shorter_than_disconnected_ones(self):
        # Poluzowanie ma isc TYLKO w jedna strone (dluzszy TTL gdy WS zyje) -
        # nigdy nie powinno byc ciasniejsze niz baseline bez WS.
        for bar in blofin_feed._KLINE_CACHE_TTL_S:
            self.assertGreaterEqual(
                blofin_feed._KLINE_CACHE_TTL_S_WS_CONNECTED.get(bar, 0),
                blofin_feed._KLINE_CACHE_TTL_S[bar],
            )

    def test_budget_stays_safe_at_configured_max_candidates_even_if_ws_is_down(self):
        # 20.08.2026: test-strazak - jesli ktos kiedys podniesie MAX_CANDIDATES
        # LUB obnizy PUBLIC_BUCKET bez przeliczenia razem, to sie wywali.
        # Kluczowe: liczy wzgledem REALNEJ PUBLIC_BUCKET.refill_per_sec, nie
        # zaszytej na sztywno liczby - dokladnie brak tego pozwolil
        # niezauwazenie przeoczyc obnizke budzetu z 5 do 3 req/s (patrz
        # rate_limiter.py) wzgledem MAX_CANDIDATES=60 dobranego pod stary,
        # wiekszy budzet - realny 429 na Cyklu #1 to ujawnil.
        import config
        from rate_limiter import PUBLIC_BUCKET as _real_public_bucket
        candidates = max(int(config.DAYTRADING_MAX_CANDIDATES), int(config.DAYTRADING_V2_MAX_CANDIDATES))
        budget_ws_down = sum(candidates / ttl for ttl in blofin_feed._KLINE_CACHE_TTL_S.values())
        self.assertLess(
            budget_ws_down / _real_public_bucket.refill_per_sec, 0.70,
            "budzet REST w scenariuszu 'WS padl' przekracza 70% REALNEGO budzetu PUBLIC_BUCKET",
        )

    def test_ws_connected_steady_state_budget_stays_safe_even_at_incident_scale_universe(self):
        # 21.08.2026, druga iteracja: DAYTRADING_V2_MAX_CANDIDATES_WS_CONNECTED
        # (plaski sufit, byl kolejno None potem 60) zostal CALKOWICIE
        # usuniety - WS-connected target to teraz cale przefiltrowane
        # wolumenem uniwersum (patrz generate() w daytrading_engine_v2.py).
        # Bezpieczenstwo cold-startu pilnuje juz nie limit liczby
        # kandydatow, tylko pacing partiami (test ponizej). Ten test pilnuje
        # DRUGIEJ polowy rownania - ze nawet raz w pelni rozgrzany (steady-
        # state, poluzowany TTL) cache na uniwersum WIEKSZYM niz realnie
        # widziany w incydencie 21.08.2026 (181 kandydatow) wciaz miesci sie
        # bezpiecznie w budzecie PUBLIC_BUCKET - inaczej "brak limitu" bylby
        # bezpieczny tylko dopoki gielda nie doda wiecej par.
        import config
        from rate_limiter import PUBLIC_BUCKET as _real_public_bucket
        self.assertFalse(
            hasattr(config, "DAYTRADING_V2_MAX_CANDIDATES_WS_CONNECTED"),
            "plaski sufit WS-connected mial zostac zastapiony ramp-upem/pacingiem, nie odtworzony",
        )
        incident_scale_universe = 300  # > 181 z realnego incydentu, margines na wzrost gieldy
        budget_ws_connected = sum(
            incident_scale_universe / ttl for ttl in blofin_feed._KLINE_CACHE_TTL_S_WS_CONNECTED.values()
        )
        self.assertLess(
            budget_ws_connected / _real_public_bucket.refill_per_sec, 0.70,
            f"budzet REST w steady-state (WS polaczony) dla {incident_scale_universe} symboli "
            "przekracza 70% REALNEGO budzetu PUBLIC_BUCKET - 'brak limitu' przestaje byc bezpieczny",
        )

    def test_cold_start_batch_pacing_burst_stays_safe_independent_of_universe_size(self):
        # 21.08.2026, druga iteracja, na wyrazna prosbe uzytkownika: REST nie
        # pobiera calego target-setu naraz (ani 45 przy WS-down, ani calego
        # uniwersum przy WS-connected), tylko partiami po
        # DAYTRADING_V2_COLD_START_BATCH_SIZE nowych symboli na cykl
        # generate() (patrz daytrading_engine_v2.py). Kluczowa wlasciwosc: to
        # ograniczenie NIE zalezy od rozmiaru calego uniwersum - stad ten
        # test liczy tylko wzgledem batch_size, nie wzgledem
        # MAX_CANDIDATES/liczby symboli na gieldzie.
        import config
        from rate_limiter import PUBLIC_BUCKET as _real_public_bucket
        batch_size = int(getattr(config, "DAYTRADING_V2_COLD_START_BATCH_SIZE", 8))
        cold_start_intervals = 5  # 1D,4H,1H,15m,5m - patrz _fetch_frames w daytrading_engine_v2.py
        cold_start_burst_s = (batch_size * cold_start_intervals) / _real_public_bucket.refill_per_sec
        self.assertLess(
            cold_start_burst_s, 60.0,
            f"cold-start burst dla partii {batch_size} nowych symboli ({cold_start_burst_s:.1f}s) "
            "jest za dlugi - grozi ta sama kaskada 'Rate limit - czekam Ns' co przy incydencie 21.08.2026",
        )

    def test_fetch_klines_uses_looser_ttl_when_ws_connected_no_network_call(self):
        # Cache ma dane sprzed 100s - z rozlaczonym WS (TTL=30s dla 5m) to
        # juz jest przestarzale (potrzebny nowy fetch); z polaczonym WS
        # (TTL=90s) to wciaz swieze (cache hit, zero zapytan REST).
        feed = BlofinFeed()
        stale_data = {"opens": [1.0], "closes": [1.0], "timestamps": [1]}
        feed.ohlc_cache["ohlcv_BTC-USDT_5m_1"] = (time.time() - 40, stale_data)
        fake_ws = MagicMock()
        fake_ws.is_connected.return_value = True
        fake_ws.get_last_closed_candle.return_value = None
        with patch.object(blofin_feed, "PUBLIC_WS", fake_ws), \
             patch.object(feed.session, "get") as mock_get:
            feed.fetch_klines_ohlcv("BTC", bar="5m", limit=1)
        mock_get.assert_not_called()

    def test_fetch_klines_falls_back_to_tighter_ttl_when_ws_disconnected(self):
        # Te same dane sprzed 40s, ale WS rozlaczony -> TTL=30s (bez WS) juz
        # wygasl, wiec powinno probowac odswiezyc (nie cichy cache hit).
        feed = BlofinFeed()
        stale_data = {"opens": [1.0], "closes": [1.0], "timestamps": [1]}
        feed.ohlc_cache["ohlcv_BTC-USDT_5m_1"] = (time.time() - 40, stale_data)
        fake_ws = MagicMock()
        fake_ws.is_connected.return_value = False
        fake_ws.available = False
        bucket = MagicMock()
        bucket.level.return_value = 0.9  # powyzej progu 20% - normalna sciezka odswiezenia
        bucket.acquire.return_value = True
        payload = {"code": "0", "data": [[str(1_700_000_000_000), "1", "1", "1", "1", "1"]]}
        with patch.object(blofin_feed, "PUBLIC_WS", fake_ws), \
             patch.object(blofin_feed, "PUBLIC_BUCKET", bucket), \
             patch.object(feed.session, "get", return_value=_ok_response(payload)) as mock_get:
            feed.fetch_klines_ohlcv("BTC", bar="5m", limit=1)
        self.assertGreaterEqual(mock_get.call_count, 1)

    def test_gap_in_cache_hit_forces_real_rest_fetch_not_silent_append(self):
        # WS polaczony, cache fresh (wewnatrz TTL) - ale WS zglasza swiece
        # z luka (>1 bar skoku) - nie powinno bylo cichego merge'u, tylko
        # wymuszenie prawdziwego zapytania REST, zeby wypelnic dziure.
        feed = BlofinFeed()
        fresh_data = {"opens": [1.0], "closes": [1.0], "highs": [1.0], "lows": [1.0], "timestamps": [0]}
        feed.ohlc_cache["ohlcv_BTC-USDT_1H_1"] = (time.time() - 1, fresh_data)  # bardzo swieze
        fake_ws = MagicMock()
        fake_ws.is_connected.return_value = True
        fake_ws.available = True
        fake_ws.get_last_closed_candle.return_value = {
            "ts": 3 * 3_600_000, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1,
        }  # 3 bary do przodu - luka
        bucket = MagicMock()
        bucket.level.return_value = 0.9
        bucket.acquire.return_value = True
        payload = {"code": "0", "data": [[str(1_700_000_000_000), "1", "1", "1", "1", "1"]]}
        with patch.object(blofin_feed, "PUBLIC_WS", fake_ws), \
             patch.object(blofin_feed, "PUBLIC_BUCKET", bucket), \
             patch.object(feed.session, "get", return_value=_ok_response(payload)) as mock_get:
            feed.fetch_klines_ohlcv("BTC", bar="1H", limit=1)
        self.assertGreaterEqual(mock_get.call_count, 1)

    def test_klines_for_long_bars_are_seeded_from_disk_cache_when_memory_empty(self):
        # Symulacja restartu: nic w pamieci (ohlc_cache pusty), ale dysk ma
        # przestarzale (lecz nie puste) dane dla 4H - powinny zostac uzyte
        # jako pierwszy seed zamiast startowac od zera.
        import tempfile
        from pathlib import Path
        import disk_cache
        with tempfile.TemporaryDirectory() as td, patch.object(disk_cache, "CACHE_DIR", Path(td)):
            disk_cache.save("ohlcv_BTC-USDT_4H_120", {"opens": [42.0], "closes": [42.0]})
            feed = BlofinFeed()
            bucket = MagicMock()
            bucket.level.return_value = 0.1  # ponizej 20% - powinno oddac to, co przed chwila zaladowane z dysku
            with patch.object(blofin_feed, "PUBLIC_BUCKET", bucket), \
                 patch.object(feed.session, "get") as mock_get:
                result = feed.fetch_klines_ohlcv("BTC", bar="4H", limit=120)
        self.assertEqual(0, mock_get.call_count)
        self.assertEqual([42.0], result["closes"])

    def test_klines_for_short_bars_are_not_persisted_to_disk(self):
        import tempfile
        from pathlib import Path
        import disk_cache
        with tempfile.TemporaryDirectory() as td, patch.object(disk_cache, "CACHE_DIR", Path(td)):
            feed = BlofinFeed()
            bucket = MagicMock()
            bucket.acquire.return_value = True
            payload = {"code": "0", "data": [[str(1_700_000_000_000), "1", "1", "1", "1", "1"]]}
            with patch.object(blofin_feed, "PUBLIC_BUCKET", bucket), \
                 patch.object(feed.session, "get", return_value=_ok_response(payload)):
                feed.fetch_klines_ohlcv("BTC", bar="5m", limit=1)
            self.assertIsNone(disk_cache.load("ohlcv_BTC-USDT_5m_1"))

    def test_fetch_klines_ohlcv_subscribes_ws_candles_when_available(self):
        feed = BlofinFeed()
        fake_ws = MagicMock()
        fake_ws.available = True
        fake_ws.get_last_closed_candle.return_value = None
        bucket = MagicMock()
        bucket.acquire.return_value = True
        payload = {"code": "0", "data": [[str(1_700_000_000_000), "1", "1", "1", "1", "1"]]}
        with patch.object(blofin_feed, "PUBLIC_WS", fake_ws), \
             patch.object(blofin_feed, "PUBLIC_BUCKET", bucket), \
             patch.object(feed.session, "get", return_value=_ok_response(payload)):
            feed.fetch_klines_ohlcv("BTC", bar="1H", limit=1)
        fake_ws.start.assert_called_with(["BTC"])
        fake_ws.subscribe_candles.assert_called_with("BTC", ["1H"])

    def test_fetch_klines_ohlcv_skips_ws_subscribe_when_unavailable(self):
        feed = BlofinFeed()
        fake_ws = MagicMock()
        fake_ws.available = False
        bucket = MagicMock()
        bucket.acquire.return_value = True
        payload = {"code": "0", "data": [[str(1_700_000_000_000), "1", "1", "1", "1", "1"]]}
        with patch.object(blofin_feed, "PUBLIC_WS", fake_ws), \
             patch.object(blofin_feed, "PUBLIC_BUCKET", bucket), \
             patch.object(feed.session, "get", return_value=_ok_response(payload)):
            feed.fetch_klines_ohlcv("BTC", bar="1H", limit=1)
        fake_ws.start.assert_not_called()
        fake_ws.subscribe_candles.assert_not_called()


class TestMergeWsClosedCandle(unittest.TestCase):
    """_merge_ws_closed_candle - nakleja ostatnia FAKTYCZNIE zamknieta
    swiece z WS na koniec serii REST/cache, skracajac opoznienie wykrycia
    zamkniecia bara z rzedu TTL cache do ~1-2s (push WS). Nigdy nie dolacza
    wciaz-formujacej sie swiecy (ta zasada jest juz wymuszona w
    BlofinPublicWebSocket.get_last_closed_candle - tutaj testujemy sama
    logike naklejania). Zwraca (data, gap_detected) - druga wartosc mowi
    wolajacemu, czy WS przeskoczyl >1 bar i trzeba wymusic prawdziwy fetch
    REST zamiast ufac merge'owi (ktory zrobilby cicha dziure w serii)."""

    def _data(self, ts_list, closes):
        n = len(ts_list)
        return {"timestamps": list(ts_list), "opens": list(closes), "highs": list(closes),
                "lows": list(closes), "closes": list(closes), "volumes": [1.0] * n}

    def test_appends_newer_ws_candle_to_series(self):
        # 1H bar = 3 600 000 ms - odstep 1000->2000->3000ms tutaj to tylko
        # testowe znaczniki, w praktyce dużo mniejsze niz bar_ms, wiec NIE
        # traktowane jako luka.
        data = self._data([1000, 2000], [100.0, 101.0])
        fake_ws = MagicMock()
        fake_ws.get_last_closed_candle.return_value = {
            "ts": 3000, "open": 101.0, "high": 103.0, "low": 100.5, "close": 102.5, "volume": 5.0,
        }
        with patch.object(blofin_feed, "PUBLIC_WS", fake_ws):
            out, gap = _merge_ws_closed_candle("BTC", "1H", data)
        self.assertFalse(gap)
        self.assertEqual([1000, 2000, 3000], out["timestamps"])
        self.assertEqual(102.5, out["closes"][-1])
        self.assertEqual(103.0, out["highs"][-1])
        # oryginalny obiekt data NIE zostal zmutowany
        self.assertEqual([1000, 2000], data["timestamps"])

    def test_does_not_append_when_ws_candle_not_newer(self):
        data = self._data([1000, 2000], [100.0, 101.0])
        fake_ws = MagicMock()
        fake_ws.get_last_closed_candle.return_value = {
            "ts": 2000, "open": 100.5, "high": 101.5, "low": 100.0, "close": 101.0, "volume": 1.0,
        }
        with patch.object(blofin_feed, "PUBLIC_WS", fake_ws):
            out, gap = _merge_ws_closed_candle("BTC", "1H", data)
        self.assertFalse(gap)
        self.assertEqual([1000, 2000], out["timestamps"])

    def test_returns_data_unchanged_when_ws_has_nothing(self):
        data = self._data([1000], [100.0])
        fake_ws = MagicMock()
        fake_ws.get_last_closed_candle.return_value = None
        with patch.object(blofin_feed, "PUBLIC_WS", fake_ws):
            out, gap = _merge_ws_closed_candle("BTC", "1H", data)
        self.assertFalse(gap)
        self.assertEqual(data, out)

    def test_returns_data_unchanged_on_empty_timestamps(self):
        fake_ws = MagicMock()
        with patch.object(blofin_feed, "PUBLIC_WS", fake_ws):
            out, gap = _merge_ws_closed_candle("BTC", "1H", {"timestamps": [], "closes": []})
        fake_ws.get_last_closed_candle.assert_not_called()
        self.assertFalse(gap)

    def test_never_raises_when_ws_lookup_throws(self):
        data = self._data([1000], [100.0])
        fake_ws = MagicMock()
        fake_ws.get_last_closed_candle.side_effect = RuntimeError("boom")
        with patch.object(blofin_feed, "PUBLIC_WS", fake_ws):
            out, gap = _merge_ws_closed_candle("BTC", "1H", data)
        self.assertFalse(gap)
        self.assertEqual(data, out)

    def test_gap_larger_than_one_bar_is_detected_not_silently_appended(self):
        # 1H bar = 3 600 000 ms. Ostatni znany bar @ t=0. WS wraca z barem
        # @ t=3 bar_ms (przeskoczyl 2 bary, np. po krotkim rozlaczeniu WS).
        bar_ms = 3_600_000
        data = self._data([0], [100.0])
        fake_ws = MagicMock()
        fake_ws.get_last_closed_candle.return_value = {
            "ts": bar_ms * 3, "open": 100.0, "high": 105.0, "low": 99.0, "close": 104.0, "volume": 1.0,
        }
        with patch.object(blofin_feed, "PUBLIC_WS", fake_ws):
            out, gap = _merge_ws_closed_candle("BTC", "1H", data)
        self.assertTrue(gap)
        # NIE doklejone - seria pozostaje taka, jaka byla (bez cichej dziury).
        self.assertEqual([0], out["timestamps"])

    def test_exactly_one_bar_gap_is_not_flagged(self):
        bar_ms = 3_600_000
        data = self._data([0], [100.0])
        fake_ws = MagicMock()
        fake_ws.get_last_closed_candle.return_value = {
            "ts": bar_ms, "open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5, "volume": 1.0,
        }
        with patch.object(blofin_feed, "PUBLIC_WS", fake_ws):
            out, gap = _merge_ws_closed_candle("BTC", "1H", data)
        self.assertFalse(gap)
        self.assertEqual([0, bar_ms], out["timestamps"])

    def test_gap_detection_scales_with_bar_duration(self):
        # Ten sam bezwzgledny skok czasu (2h) - dla 15m to ogromna luka
        # (8 barow), dla 4h to wciaz w normie (mniej niz 1 bar).
        jump_ms = 2 * 3_600_000
        data_15m = self._data([0], [1.0])
        fake_ws_15m = MagicMock()
        fake_ws_15m.get_last_closed_candle.return_value = {"ts": jump_ms, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
        with patch.object(blofin_feed, "PUBLIC_WS", fake_ws_15m):
            _out, gap_15m = _merge_ws_closed_candle("BTC", "15m", data_15m)
        self.assertTrue(gap_15m)

        data_4h = self._data([0], [1.0])
        fake_ws_4h = MagicMock()
        fake_ws_4h.get_last_closed_candle.return_value = {"ts": jump_ms, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
        with patch.object(blofin_feed, "PUBLIC_WS", fake_ws_4h):
            _out, gap_4h = _merge_ws_closed_candle("BTC", "4H", data_4h)
        self.assertFalse(gap_4h)

    def test_fetch_last_prices_uses_websocket_when_fresh_no_network_call(self):
        feed = BlofinFeed()
        fake_ws = MagicMock()
        fake_ws.available = True
        fake_ws.get_price.side_effect = lambda sym, max_age_s=5.0: {"BTC": 65000.0, "ETH": 3200.0}.get(sym)
        with patch.object(blofin_feed, "PUBLIC_WS", fake_ws), \
             patch.object(feed.session, "get") as mock_get:
            result = feed.fetch_last_prices(["BTC", "ETH"])
        self.assertEqual(0, mock_get.call_count)
        self.assertEqual({"BTC": 65000.0, "ETH": 3200.0}, result)
        fake_ws.start.assert_called_once()
        fake_ws.subscribe.assert_called_once()

    def test_fetch_last_prices_falls_back_to_rest_for_symbols_missing_from_ws(self):
        feed = BlofinFeed()
        fake_ws = MagicMock()
        fake_ws.available = True
        fake_ws.get_price.side_effect = lambda sym, max_age_s=5.0: {"BTC": 65000.0}.get(sym)  # ETH brak w WS
        rest_payload = {"code": "0", "data": [
            {"instId": "ETH-USDT", "last": "3200.0"},
        ]}
        with patch.object(blofin_ws, "PUBLIC_WS", fake_ws), \
             patch.object(blofin_feed, "PUBLIC_WS", fake_ws), \
             patch.object(feed.session, "get", return_value=_ok_response(rest_payload)) as mock_get:
            result = feed.fetch_last_prices(["BTC", "ETH"])
        self.assertEqual(1, mock_get.call_count)  # tylko dla brakujacego ETH
        self.assertEqual({"BTC": 65000.0, "ETH": 3200.0}, result)

    def test_fetch_last_prices_uses_pure_rest_when_ws_unavailable(self):
        feed = BlofinFeed()
        fake_ws = MagicMock()
        fake_ws.available = False
        rest_payload = {"code": "0", "data": [{"instId": "BTC-USDT", "last": "65000.0"}]}
        with patch.object(blofin_feed, "PUBLIC_WS", fake_ws), \
             patch.object(feed.session, "get", return_value=_ok_response(rest_payload)) as mock_get:
            result = feed.fetch_last_prices(["BTC"])
        fake_ws.start.assert_not_called()
        self.assertEqual(1, mock_get.call_count)
        self.assertEqual({"BTC": 65000.0}, result)


def _rate_limited_response(retry_after=None):
    resp = MagicMock()
    resp.status_code = 429
    resp.headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}
    resp.json.return_value = {}
    resp.text = ""
    return resp


class TestRetryAfterSecondsHelper(unittest.TestCase):
    def test_returns_default_and_false_when_header_missing(self):
        val, from_header = blofin_feed._retry_after_seconds({}, default=12.0)
        self.assertEqual(12.0, val)
        self.assertFalse(from_header)

    def test_returns_parsed_seconds_and_true_when_present(self):
        val, from_header = blofin_feed._retry_after_seconds({"Retry-After": "5"}, default=12.0)
        self.assertEqual(5.0, val)
        self.assertTrue(from_header)


class TestRetryAfterHeader(unittest.TestCase):
    def test_uses_retry_after_seconds_value_from_header(self):
        feed = BlofinFeed()
        bucket = MagicMock()
        bucket.acquire.return_value = True
        responses = [_rate_limited_response(retry_after=3), _ok_response({"code": "0", "data": []})]
        with patch.object(blofin_feed, "PUBLIC_BUCKET", bucket), \
             patch.object(feed.session, "get", side_effect=responses), \
             patch("blofin_feed.time.sleep") as mock_sleep:
            feed._get("market/instruments")
        mock_sleep.assert_called_once_with(3.0)

    def test_falls_back_to_default_when_header_missing(self):
        feed = BlofinFeed()
        bucket = MagicMock()
        bucket.acquire.return_value = True
        responses = [_rate_limited_response(retry_after=None), _ok_response({"code": "0", "data": []})]
        with patch.object(blofin_feed, "PUBLIC_BUCKET", bucket), \
             patch.object(feed.session, "get", side_effect=responses), \
             patch("blofin_feed.time.sleep") as mock_sleep:
            feed._get("market/instruments")
        mock_sleep.assert_called_once_with(12.0)

    def test_parses_http_date_retry_after(self):
        from email.utils import format_datetime
        from datetime import datetime, timezone, timedelta
        future = datetime.now(timezone.utc) + timedelta(seconds=7)
        feed = BlofinFeed()
        bucket = MagicMock()
        bucket.acquire.return_value = True
        responses = [_rate_limited_response(retry_after=format_datetime(future)), _ok_response({"code": "0", "data": []})]
        with patch.object(blofin_feed, "PUBLIC_BUCKET", bucket), \
             patch.object(feed.session, "get", side_effect=responses), \
             patch("blofin_feed.time.sleep") as mock_sleep:
            feed._get("market/instruments")
        waited = mock_sleep.call_args[0][0]
        self.assertGreater(waited, 4.0)
        self.assertLess(waited, 9.0)

    def test_malformed_retry_after_falls_back_to_default(self):
        feed = BlofinFeed()
        bucket = MagicMock()
        bucket.acquire.return_value = True
        responses = [_rate_limited_response(retry_after="not-a-number-or-date"), _ok_response({"code": "0", "data": []})]
        with patch.object(blofin_feed, "PUBLIC_BUCKET", bucket), \
             patch.object(feed.session, "get", side_effect=responses), \
             patch("blofin_feed.time.sleep") as mock_sleep:
            feed._get("market/instruments")
        mock_sleep.assert_called_once_with(12.0)

    def test_extreme_retry_after_is_capped_not_obeyed_blindly(self):
        # 21.08.2026: realny incydent - Blofin zwrocil Retry-After: 3600
        # (godzina) na publicznym endpoincie, a watek skanujacy (osobny od
        # UI) zasnal na cala godzine z pojedynczego naglowka, bez sufitu i
        # bez widocznosci dla uzytkownika. Retry-After to sugestia serwera,
        # nie kontrakt - patrz BLOFIN_MAX_RATE_LIMIT_SLEEP_S w config.py.
        import config
        feed = BlofinFeed()
        bucket = MagicMock()
        bucket.acquire.return_value = True
        responses = [_rate_limited_response(retry_after=3600), _ok_response({"code": "0", "data": []})]
        with patch.object(blofin_feed, "PUBLIC_BUCKET", bucket), \
             patch.object(feed.session, "get", side_effect=responses), \
             patch("blofin_feed.time.sleep") as mock_sleep:
            feed._get("market/instruments")
        mock_sleep.assert_called_once_with(config.BLOFIN_MAX_RATE_LIMIT_SLEEP_S)
        self.assertLess(
            config.BLOFIN_MAX_RATE_LIMIT_SLEEP_S, 3600.0,
            "sufit musi byc realnie mniejszy niz to, co serwer zazadal w tym incydencie",
        )

    def test_retry_after_below_cap_still_uses_the_real_server_value(self):
        # Sufit nie ma obcinac normalnych, rozsadnych odpowiedzi serwera -
        # tylko ekstremalne przypadki jak w tescie powyzej.
        import config
        feed = BlofinFeed()
        bucket = MagicMock()
        bucket.acquire.return_value = True
        below_cap = config.BLOFIN_MAX_RATE_LIMIT_SLEEP_S - 5.0
        responses = [_rate_limited_response(retry_after=below_cap), _ok_response({"code": "0", "data": []})]
        with patch.object(blofin_feed, "PUBLIC_BUCKET", bucket), \
             patch.object(feed.session, "get", side_effect=responses), \
             patch("blofin_feed.time.sleep") as mock_sleep:
            feed._get("market/instruments")
        mock_sleep.assert_called_once_with(below_cap)


if __name__ == "__main__":
    unittest.main()
