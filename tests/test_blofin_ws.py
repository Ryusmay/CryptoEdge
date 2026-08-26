import json
import threading
import time
import unittest
from unittest.mock import patch

import blofin_ws
from blofin_ws import BlofinPublicWebSocket


class FakeWebSocketApp:
    """Podmienia websocket.WebSocketApp - symuluje połączenie bez sieci."""

    def __init__(self, url, on_open=None, on_message=None, on_error=None, on_close=None, **kwargs):
        self.url = url
        self.on_open = on_open
        self.on_message = on_message
        self.on_error = on_error
        self.on_close = on_close
        self.header = kwargs.get("header")
        self.sent = []
        self.closed = False

    def send(self, msg):
        if self.closed:
            raise RuntimeError("połączenie zamknięte")
        self.sent.append(msg)

    def close(self):
        self.closed = True

    def run_forever(self, ping_interval=0, **kwargs):
        self.origin = kwargs.get("origin")
        if self.on_open:
            self.on_open(self)
        while not self.closed:
            time.sleep(0.005)


def _wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


class TestBlofinPublicWebSocketAvailability(unittest.TestCase):
    def test_start_returns_false_when_library_not_installed(self):
        with patch.object(blofin_ws, "_WS_AVAILABLE", False):
            ws = BlofinPublicWebSocket()
            self.assertFalse(ws.available)
            self.assertFalse(ws.start(["BTC"]))
            self.assertFalse(ws.is_connected())


class TestBlofinPublicWebSocketWithFakeConnection(unittest.TestCase):
    def setUp(self):
        self._patchers = [
            patch.object(blofin_ws, "_WS_AVAILABLE", True),
            patch.object(blofin_ws, "websocket", type("_m", (), {"WebSocketApp": FakeWebSocketApp})),
        ]
        for p in self._patchers:
            p.start()
            self.addCleanup(p.stop)
        self.ws = BlofinPublicWebSocket()
        self.addCleanup(self.ws.stop)

    def _fake(self) -> FakeWebSocketApp:
        self.assertTrue(_wait_until(lambda: self.ws._ws is not None and self.ws.is_connected()))
        return self.ws._ws

    def test_on_open_sends_subscribe_for_pending_symbols(self):
        self.ws.start(["BTC", "ETH"])
        fake = self._fake()
        self.assertTrue(_wait_until(lambda: len(fake.sent) >= 1))
        payload = json.loads(fake.sent[0])
        self.assertEqual("subscribe", payload["op"])
        inst_ids = {arg["instId"] for arg in payload["args"]}
        self.assertEqual({"BTC-USDT", "ETH-USDT"}, inst_ids)
        channels = {arg["channel"] for arg in payload["args"]}
        self.assertEqual({"tickers", "mark-price-candle1m", "books5"}, channels)

    def test_ticker_push_message_updates_price_cache(self):
        self.ws.start(["BTC"])
        fake = self._fake()
        push = {
            "arg": {"channel": "tickers", "instId": "BTC-USDT"},
            "data": [{
                "instId": "BTC-USDT", "last": "65432.1", "lastSize": "0.1",
                "askPrice": "65433.0", "askSize": "1", "bidPrice": "65431.0", "bidSize": "1",
                "open24h": "64000", "high24h": "66000", "low24h": "63000",
                "volCurrency24h": "100", "vol24h": "100", "ts": "1700000000000",
            }],
        }
        fake.on_message(fake, json.dumps(push))
        self.assertEqual(65432.1, self.ws.get_price("BTC"))
        ticker = self.ws.get_ticker("BTC")
        self.assertEqual(65433.0, ticker["ask"])
        self.assertEqual(65431.0, ticker["bid"])

    def test_get_price_returns_none_when_stale(self):
        self.ws.start(["BTC"])
        fake = self._fake()
        push = {
            "arg": {"channel": "tickers", "instId": "BTC-USDT"},
            "data": [{"instId": "BTC-USDT", "last": "100.0", "ts": "1700000000000"}],
        }
        fake.on_message(fake, json.dumps(push))
        self.assertEqual(100.0, self.ws.get_price("BTC", max_age_s=5.0))
        self.assertIsNone(self.ws.get_price("BTC", max_age_s=0.0))

    def test_get_price_returns_none_for_unknown_symbol(self):
        self.ws.start(["BTC"])
        self._fake()
        self.assertIsNone(self.ws.get_price("DOGE"))

    def test_mark_price_candle_push_updates_mark_price(self):
        self.ws.start(["BTC"])
        fake = self._fake()
        push = {
            "arg": {"channel": "mark-price-candle1m", "instId": "BTC-USDT"},
            "data": [["1696636800000", "27491.5", "27495", "27483", "27489.5", "0"]],
        }
        fake.on_message(fake, json.dumps(push))
        self.assertEqual(27489.5, self.ws.get_mark_price("BTC"))
        self.assertIsNone(self.ws.get_mark_price("ETH"))

    def test_mark_price_stale_returns_none(self):
        self.ws.start(["BTC"])
        fake = self._fake()
        push = {"arg": {"channel": "mark-price-candle1m", "instId": "BTC-USDT"},
                "data": [["1", "1", "1", "1", "100.0", "0"]]}
        fake.on_message(fake, json.dumps(push))
        self.assertEqual(100.0, self.ws.get_mark_price("BTC", max_age_s=5.0))
        self.assertIsNone(self.ws.get_mark_price("BTC", max_age_s=0.0))

    def test_order_book_top_push_updates_book(self):
        self.ws.start(["BTC"])
        fake = self._fake()
        push = {
            "arg": {"channel": "books5", "instId": "BTC-USDT"},
            "data": {
                "asks": [["65001.0", "10"], ["65002.0", "5"]],
                "bids": [["65000.0", "8"], ["64999.0", "3"]],
                "ts": "1696670727520",
            },
        }
        fake.on_message(fake, json.dumps(push))
        book = self.ws.get_order_book_top("BTC")
        self.assertEqual(65001.0, book["best_ask"])
        self.assertEqual(65000.0, book["best_bid"])
        self.assertEqual(2, len(book["asks_top5"]))
        self.assertIsNone(self.ws.get_order_book_top("ETH"))

    def test_different_channels_merge_into_same_symbol_row_without_clobbering(self):
        # Ticker, mark-price i books5 aktualizuja RAZEM ten sam wpis per
        # symbol - jeden kanal nie moze nadpisac danych z drugiego.
        self.ws.start(["BTC"])
        fake = self._fake()
        fake.on_message(fake, json.dumps({
            "arg": {"channel": "tickers", "instId": "BTC-USDT"},
            "data": [{"instId": "BTC-USDT", "last": "65000.0", "ts": "1"}],
        }))
        fake.on_message(fake, json.dumps({
            "arg": {"channel": "mark-price-candle1m", "instId": "BTC-USDT"},
            "data": [["1", "1", "1", "1", "64999.0", "0"]],
        }))
        self.assertEqual(65000.0, self.ws.get_price("BTC"))
        self.assertEqual(64999.0, self.ws.get_mark_price("BTC"))

    def test_pong_string_message_does_not_raise(self):
        self.ws.start(["BTC"])
        fake = self._fake()
        fake.on_message(fake, "pong")  # nie powinno rzucic wyjatku ani nic zapisac

    def test_subscribe_event_ack_is_ignored_not_stored_as_price(self):
        self.ws.start(["BTC"])
        fake = self._fake()
        ack = {"event": "subscribe", "arg": {"channel": "tickers", "instId": "BTC-USDT"}}
        fake.on_message(fake, json.dumps(ack))
        self.assertIsNone(self.ws.get_price("BTC"))

    def test_error_event_does_not_raise_or_store_price(self):
        self.ws.start(["BTC"])
        fake = self._fake()
        err = {"event": "error", "code": "60012", "msg": "Invalid request"}
        fake.on_message(fake, json.dumps(err))
        self.assertIsNone(self.ws.get_price("BTC"))

    def test_malformed_json_message_does_not_raise(self):
        self.ws.start(["BTC"])
        fake = self._fake()
        fake.on_message(fake, "{niepoprawny json")

    def test_subscribe_batches_over_80_symbols_into_multiple_messages(self):
        symbols = [f"SYM{i}" for i in range(180)]  # 180 symboli x 3 kanaly = 540 argumentow
        self.ws.start(symbols)
        fake = self._fake()
        self.assertTrue(_wait_until(lambda: len(fake.sent) >= 3))
        total_args = sum(len(json.loads(m)["args"]) for m in fake.sent)
        self.assertEqual(540, total_args)  # 180 symboli x (tickers + mark-price-candle1m + books5)
        for msg in fake.sent:
            self.assertLessEqual(len(json.loads(msg)["args"]), 80)

    def test_subscribe_after_connect_sends_only_new_symbols(self):
        self.ws.start(["BTC"])
        fake = self._fake()
        self.assertTrue(_wait_until(lambda: len(fake.sent) >= 1))
        fake.sent.clear()
        self.ws.subscribe(["BTC", "ETH"])  # BTC juz subskrybowany - tylko ETH powinien pojsc
        self.assertTrue(_wait_until(lambda: len(fake.sent) >= 1))
        payload = json.loads(fake.sent[0])
        self.assertEqual({"ETH-USDT"}, {a["instId"] for a in payload["args"]})

    def test_snapshot_returns_copy_not_live_reference(self):
        self.ws.start(["BTC"])
        fake = self._fake()
        push = {"arg": {"channel": "tickers", "instId": "BTC-USDT"},
                "data": [{"instId": "BTC-USDT", "last": "1.0", "ts": "1"}]}
        fake.on_message(fake, json.dumps(push))
        snap = self.ws.snapshot()
        snap["BTC"]["last"] = 999.0
        self.assertNotEqual(999.0, self.ws.get_price("BTC"))


class TestBlofinPublicWebSocketCandles(unittest.TestCase):
    """Zywe swiece (kanal candle{bar}) - punkt: dane jak najbardziej
    aktualne z minimalnym opoznieniem. Kluczowa zasada: wciaz-formujaca sie
    swieca NIGDY nie jest eksponowana jako "zamknieta" (get_last_closed_candle) -
    tylko po wykryciu rollover timestampu, ta sama zasada co REST
    ("Never pass a known-open candle into indicators")."""

    def setUp(self):
        self._patchers = [
            patch.object(blofin_ws, "_WS_AVAILABLE", True),
            patch.object(blofin_ws, "websocket", type("_m", (), {"WebSocketApp": FakeWebSocketApp})),
        ]
        for p in self._patchers:
            p.start()
            self.addCleanup(p.stop)
        self.ws = BlofinPublicWebSocket()
        self.addCleanup(self.ws.stop)

    def _fake(self) -> FakeWebSocketApp:
        self.assertTrue(_wait_until(lambda: self.ws._ws is not None and self.ws.is_connected()))
        return self.ws._ws

    def _candle_push(self, ts, o, h, l, c, vol="1.0"):
        return {"arg": {"channel": "candle1H", "instId": "BTC-USDT"},
                "data": [[str(ts), str(o), str(h), str(l), str(c), vol]]}

    def test_subscribe_candles_sends_correct_channel_and_inst_id(self):
        self.ws.start([])
        self.ws.subscribe_candles("BTC", ["1H", "4H"])
        fake = self._fake()
        self.assertTrue(_wait_until(lambda: len(fake.sent) >= 1))
        payload = json.loads(fake.sent[0])
        channels = {a["channel"] for a in payload["args"]}
        self.assertEqual({"candle1H", "candle4H"}, channels)
        for a in payload["args"]:
            self.assertEqual("BTC-USDT", a["instId"])

    def test_still_forming_candle_never_exposed_as_closed(self):
        self.ws.start([])
        self.ws.subscribe_candles("BTC", ["1H"])
        fake = self._fake()
        fake.on_message(fake, json.dumps(self._candle_push(1000, 100, 101, 99, 100.5)))
        # Ta sama swieca (ten sam ts) aktualizuje sie kilka razy - to wciaz
        # TA SAMA, wciaz otwarta swieca, nie zamknieta.
        fake.on_message(fake, json.dumps(self._candle_push(1000, 100, 102, 99, 101.0)))
        self.assertIsNone(self.ws.get_last_closed_candle("BTC", "1H"))
        live = self.ws.get_live_candle("BTC", "1H")
        self.assertEqual(101.0, live["close"])

    def test_timestamp_rollover_exposes_previous_candle_as_closed(self):
        self.ws.start([])
        self.ws.subscribe_candles("BTC", ["1H"])
        fake = self._fake()
        fake.on_message(fake, json.dumps(self._candle_push(1000, 100, 102, 99, 101.5)))
        # Nowy bar (inny ts) - poprzedni (ts=1000) wlasnie sie zamknal.
        fake.on_message(fake, json.dumps(self._candle_push(2000, 101.5, 103, 101, 102.0)))
        closed = self.ws.get_last_closed_candle("BTC", "1H")
        self.assertIsNotNone(closed)
        self.assertEqual(1000, int(closed["ts"]) if isinstance(closed["ts"], (int, str)) else closed["ts"])
        self.assertEqual(101.5, closed["close"])
        # Aktualna (druga, wciaz formujaca sie) swieca NIE jest "zamknieta".
        live = self.ws.get_live_candle("BTC", "1H")
        self.assertEqual(102.0, live["close"])

    def test_get_last_closed_candle_returns_none_when_stale(self):
        self.ws.start([])
        self.ws.subscribe_candles("BTC", ["1H"])
        fake = self._fake()
        fake.on_message(fake, json.dumps(self._candle_push(1000, 100, 102, 99, 101.5)))
        fake.on_message(fake, json.dumps(self._candle_push(2000, 101.5, 103, 101, 102.0)))
        self.assertIsNotNone(self.ws.get_last_closed_candle("BTC", "1H", max_age_s=999.0))
        self.assertIsNone(self.ws.get_last_closed_candle("BTC", "1H", max_age_s=0.0))

    def test_mark_price_candle_channel_does_not_pollute_regular_candles(self):
        # "mark-price-candle1m" zaczyna sie od "candle" po odcieciu prefiksu
        # kanalowego, ale NIE powinien trafic do zwyklych swiec OHLCV -
        # obsluguje go osobna, juz istniejaca sciezka (_store_mark_price).
        self.ws.start([])
        self.ws.subscribe_candles("BTC", ["1m"])
        fake = self._fake()
        push = {"arg": {"channel": "mark-price-candle1m", "instId": "BTC-USDT"},
                "data": [["1000", "100", "101", "99", "100.5", "0"]]}
        fake.on_message(fake, json.dumps(push))
        self.assertIsNone(self.ws.get_live_candle("BTC", "1m"))

    def test_candles_scoped_per_symbol_and_bar_independently(self):
        self.ws.start([])
        self.ws.subscribe_candles("BTC", ["1H", "4H"])
        fake = self._fake()
        fake.on_message(fake, json.dumps(self._candle_push(1000, 100, 101, 99, 100.5)))
        push_4h = {"arg": {"channel": "candle4H", "instId": "BTC-USDT"},
                   "data": [["1000", "100", "105", "98", "103.0", "1.0"]]}
        fake.on_message(fake, json.dumps(push_4h))
        c1h = self.ws.get_live_candle("BTC", "1H")
        c4h = self.ws.get_live_candle("BTC", "4H")
        self.assertEqual(100.5, c1h["close"])
        self.assertEqual(103.0, c4h["close"])

    def test_subscribe_candles_does_not_resend_already_subscribed_pairs(self):
        self.ws.start([])
        self.ws.subscribe_candles("BTC", ["1H"])
        fake = self._fake()
        self.assertTrue(_wait_until(lambda: len(fake.sent) >= 1))
        time.sleep(0.05)  # ustabilizuj - upewnij sie, ze odroczona wysylka z _on_open juz wyladowala
        fake.sent.clear()
        self.ws.subscribe_candles("BTC", ["1H"])  # juz subskrybowane
        time.sleep(0.05)
        self.assertEqual(0, len(fake.sent))


if __name__ == "__main__":
    unittest.main()
