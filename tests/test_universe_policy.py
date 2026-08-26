import unittest

from universe_policy import crypto_perpetual_allowed, is_traditional_market_symbol


class TestCryptoUniversePolicy(unittest.TestCase):
    def test_rejects_observed_traditional_symbols(self):
        for symbol in (
            "UVXY", "QCOM", "META", "SMH", "XAU", "SPY", "SOXS", "ADI",
            "SPCX", "CSOPSAMSUNG2L", "CSOPSKHYNIX2L", "BTC3L", "ETH2S",
            "SAMSUNG", "SKHYNIX", "OPENAI", "ANTHROPIC", "WTIOIL", "NG",
            "LRCX", "IBM", "BTCDOM", "ETHBTC",
        ):
            with self.subTest(symbol=symbol):
                self.assertTrue(is_traditional_market_symbol(symbol))
                self.assertFalse(crypto_perpetual_allowed(symbol))

    def test_accepts_crypto_linear_usdt_swap(self):
        row = {"instId": "BTC-USDT", "quoteCurrency": "USDT", "instType": "SWAP",
               "contractType": "linear", "state": "live"}
        self.assertTrue(crypto_perpetual_allowed("BTC", row))

    def test_rejects_metadata_marked_equity(self):
        row = {"instId": "ZZZZ-USDT", "instType": "SWAP", "category": "equity"}
        self.assertFalse(crypto_perpetual_allowed("ZZZZ", row))


if __name__ == "__main__":
    unittest.main()
