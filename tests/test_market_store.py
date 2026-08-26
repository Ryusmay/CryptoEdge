import unittest

from market_store import MarketStore
from blofin_feed import _publish_ohlcv


class TestMarketStoreBuffer(unittest.TestCase):
    def test_snapshot_and_ticker_snapshot_both_exist(self):
        store = MarketStore()
        store.set_tickers({"BTC": {"price": 1.0}}, from_ws=True)
        snap = store.snapshot()
        self.assertTrue(snap["ws_alive"])
        self.assertEqual(1, snap["tickers"])
        self.assertIsNotNone(snap["ticker_age_s"])
        prices, _ = store.ticker_snapshot()
        self.assertEqual(1.0, prices["BTC"])

    def test_ticker_snapshot_splits_price_and_24h(self):
        store = MarketStore()
        store.set_tickers({
            "BTC": {"symbol": "BTC", "price": 64000.0, "change_24h": 1.5},
            "ETH": {"last": 3500.0, "blofin_change_24h": -0.2},
        })
        prices, changes = store.ticker_snapshot()
        self.assertEqual(64000.0, prices["BTC"])
        self.assertEqual(1.5, changes["BTC"])
        self.assertEqual(3500.0, prices["ETH"])
        self.assertEqual(-0.2, changes["ETH"])

    def test_publish_ohlcv_mirrors_feed_into_store(self):
        from market_store import STORE
        STORE.ohlcv.pop("SOL", None)
        frame = {"closes": [1.0, 2.0], "timestamps": [1, 2]}
        out = _publish_ohlcv("sol", "4H", frame)
        self.assertIs(out, frame)
        self.assertEqual(2, STORE.candle_count("SOL", "4H"))
        STORE.ohlcv.pop("SOL", None)


if __name__ == "__main__":
    unittest.main()
