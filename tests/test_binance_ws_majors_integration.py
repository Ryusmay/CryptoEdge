import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import disk_cache
import data_feeder
from data_feeder import DataFeeder


class TestBinanceWsMajorsIntegration(unittest.TestCase):
    """Punkt 6 z listy: WebSocket Binance jako potwierdzenie ceny dla BTC/
    ETH/majors, uzywane w fetch_top_coins() zamiast wolniejszego bulk REST
    dla tej konkretnej, malej listy symboli."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        patcher = patch.object(disk_cache, "CACHE_DIR", Path(self._tmpdir.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _instrument(self, symbol):
        return {"symbol": symbol, "instId": f"{symbol}-USDT", "max_leverage": "10",
                "min_size": "0.001", "tick_size": "0.1", "contract_value": "1"}

    def test_major_symbol_price_prefers_fresh_ws_over_bulk_rest(self):
        feeder = DataFeeder()
        fake_ws = MagicMock()
        fake_ws.get_price.side_effect = lambda sym, max_age_s=5.0: 71000.0 if sym == "BTC" else None
        with patch.object(feeder, "fetch_blofin_usdt_instruments", return_value=[self._instrument("BTC")]), \
             patch.object(feeder.blofin, "fetch_all_tickers", return_value={"BTC": {"blofin_price": 70000.0}}), \
             patch.object(feeder.binance, "fetch_all_tickers", return_value={"BTC": {"binance_price": 69999.0}}), \
             patch.object(feeder.bybit, "fetch_all_tickers", return_value={}), \
             patch.object(feeder, "_refresh_coingecko_top", return_value={}), \
             patch.object(data_feeder, "binance_ws", type("_m", (), {"PUBLIC_WS": fake_ws})):
            coins = feeder.fetch_top_coins()
        # Cena finalna nadal pochodzi z Blofin (priorytet 1) - WS Binance
        # wplywa tylko na WEWNETRZNE pole binance_price uzywane jako
        # potwierdzenie/fallback, nie nadpisuje samego Blofin.
        btc = next(c for c in coins if c["symbol"] == "BTC")
        self.assertEqual(70000.0, btc["price"])  # Blofin ma priorytet
        self.assertEqual(71000.0, btc.get("binance_price"))  # ale binance_price = swieze WS, nie stary bulk REST
        fake_ws.start.assert_called_once()

    def test_non_major_symbol_unaffected_by_ws(self):
        feeder = DataFeeder()
        fake_ws = MagicMock()
        with patch.object(feeder, "fetch_blofin_usdt_instruments", return_value=[self._instrument("SOMECOIN")]), \
             patch.object(feeder.blofin, "fetch_all_tickers", return_value={"SOMECOIN": {"blofin_price": 1.0}}), \
             patch.object(feeder.binance, "fetch_all_tickers", return_value={}), \
             patch.object(feeder.bybit, "fetch_all_tickers", return_value={}), \
             patch.object(feeder, "_refresh_coingecko_top", return_value={}), \
             patch.object(data_feeder, "binance_ws", type("_m", (), {"PUBLIC_WS": fake_ws})):
            feeder.fetch_top_coins()
        fake_ws.get_price.assert_not_called()


if __name__ == "__main__":
    unittest.main()
