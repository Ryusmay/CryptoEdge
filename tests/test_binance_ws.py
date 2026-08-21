import json
import time
import unittest
from unittest.mock import patch

import binance_ws
from binance_ws import BinancePublicWebSocket


class FakeWebSocketApp:
    def __init__(self, url, on_open=None, on_message=None, on_error=None, on_close=None):
        self.url = url
        self.on_open = on_open
        self.on_message = on_message
        self.on_error = on_error
        self.on_close = on_close
        self.closed = False

    def send(self, msg):
        if self.closed:
            raise RuntimeError("połączenie zamknięte")

    def close(self):
        self.closed = True

    def run_forever(self, **kwargs):
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


TICKER_24H = {
    "e": "24hrTicker", "E": 1700000000000, "s": "BTCUSDT",
    "p": "100.0", "P": "0.15", "w": "65100.0", "c": "65123.45",
    "Q": "0.5", "o": "65023.45", "h": "65500.0", "l": "64900.0",
    "v": "1000.0", "q": "65000000.0", "O": 1699999000000, "C": 1700000000000,
    "F": 1, "L": 100, "n": 100,
}


class TestBinancePublicWebSocketAvailability(unittest.TestCase):
    def test_start_returns_false_when_library_not_installed(self):
        with patch.object(binance_ws, "_WS_AVAILABLE", False):
            ws = BinancePublicWebSocket()
            self.assertFalse(ws.available)
            self.assertFalse(ws.start(["BTC"]))

    def test_start_returns_false_with_empty_symbol_list(self):
        with patch.object(binance_ws, "_WS_AVAILABLE", True), \
             patch.object(binance_ws, "websocket", type("_m", (), {"WebSocketApp": FakeWebSocketApp})):
            ws = BinancePublicWebSocket()
            self.assertFalse(ws.start([]))
            ws.stop()


class TestBinancePublicWebSocketWithFakeConnection(unittest.TestCase):
    def setUp(self):
        self._patchers = [
            patch.object(binance_ws, "_WS_AVAILABLE", True),
            patch.object(binance_ws, "websocket", type("_m", (), {"WebSocketApp": FakeWebSocketApp})),
        ]
        for p in self._patchers:
            p.start()
            self.addCleanup(p.stop)
        self.ws = BinancePublicWebSocket()
        self.addCleanup(self.ws.stop)

    def _fake(self) -> FakeWebSocketApp:
        self.assertTrue(_wait_until(lambda: self.ws._ws is not None and self.ws.is_connected()))
        return self.ws._ws

    def test_stream_url_uses_new_routed_market_endpoint_not_legacy(self):
        # Krytyczne (sprawdzone 20.08.2026): stare /ws bez routingu jest
        # wycofane dla @ticker (kategoria "market") - termin migracji
        # 2026-04-23 juz minal. Musi byc /market/stream?streams=...
        self.ws.start(["BTC", "ETH"])
        fake = self._fake()
        self.assertIn("fstream.binance.com/market/stream?streams=", fake.url)
        self.assertIn("btcusdt@ticker", fake.url)
        self.assertIn("ethusdt@ticker", fake.url)
        self.assertNotIn("/ws/btcusdt", fake.url)  # legacy, wycofane

    def test_combined_stream_ticker_message_updates_price_cache(self):
        self.ws.start(["BTC"])
        fake = self._fake()
        wrapped = {"stream": "btcusdt@ticker", "data": TICKER_24H}
        fake.on_message(fake, json.dumps(wrapped))
        self.assertEqual(65123.45, self.ws.get_price("BTC"))

    def test_raw_unwrapped_ticker_message_also_handled(self):
        # Na wszelki wypadek: pojedynczy (nie-combined) stream dawalby surowy
        # payload bez opakowania "stream"/"data".
        self.ws.start(["BTC"])
        fake = self._fake()
        fake.on_message(fake, json.dumps(TICKER_24H))
        self.assertEqual(65123.45, self.ws.get_price("BTC"))

    def test_strips_usdt_suffix_from_symbol(self):
        self.ws.start(["ETH"])
        fake = self._fake()
        eth_ticker = dict(TICKER_24H, s="ETHUSDT", c="3200.5")
        fake.on_message(fake, json.dumps({"stream": "ethusdt@ticker", "data": eth_ticker}))
        self.assertEqual(3200.5, self.ws.get_price("ETH"))
        self.assertIsNone(self.ws.get_price("ETHUSDT"))

    def test_non_ticker_event_is_ignored(self):
        self.ws.start(["BTC"])
        fake = self._fake()
        fake.on_message(fake, json.dumps({"stream": "btcusdt@depth", "data": {"e": "depthUpdate"}}))
        self.assertIsNone(self.ws.get_price("BTC"))

    def test_malformed_json_does_not_raise(self):
        self.ws.start(["BTC"])
        fake = self._fake()
        fake.on_message(fake, "{zly json")

    def test_get_price_returns_none_when_stale(self):
        self.ws.start(["BTC"])
        fake = self._fake()
        fake.on_message(fake, json.dumps({"stream": "btcusdt@ticker", "data": TICKER_24H}))
        self.assertEqual(65123.45, self.ws.get_price("BTC", max_age_s=5.0))
        self.assertIsNone(self.ws.get_price("BTC", max_age_s=0.0))


if __name__ == "__main__":
    unittest.main()
