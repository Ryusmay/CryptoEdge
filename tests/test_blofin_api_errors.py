import unittest
from io import StringIO
from unittest.mock import MagicMock, patch

import requests

import blofin_feed
from blofin_feed import BlofinFeed, _BROWSER_UA, _BlofinAdapter, _connect_family
from instrument_registry import InstrumentRegistry
from rate_limiter import TokenBucket


def _fast_bucket():
    return TokenBucket(capacity=10.0, refill_per_sec=1000.0)


class TestBlofinGetErrorsSurface(unittest.TestCase):
    def test_timeout_is_logged(self):
        feed = BlofinFeed()
        buf = StringIO()
        with patch.object(blofin_feed, "PUBLIC_BUCKET", _fast_bucket()), \
             patch.object(feed.session, "get", side_effect=requests.Timeout("x")), \
             patch("sys.stdout", buf):
            out = feed._get("market/instruments")
        self.assertIsNone(out)
        self.assertIn("timeout", feed.last_error)
        self.assertIn("GET market/instruments FAIL", buf.getvalue())

    def test_ssl_is_logged(self):
        feed = BlofinFeed()
        buf = StringIO()
        with patch.object(blofin_feed, "PUBLIC_BUCKET", _fast_bucket()), \
             patch.object(feed.session, "get", side_effect=requests.exceptions.SSLError("cert verify")), \
             patch("sys.stdout", buf):
            out = feed._get("market/instruments")
        self.assertIsNone(out)
        self.assertIn("SSL", feed.last_error)
        self.assertIn("FAIL", buf.getvalue())

    def test_ssl_retries_once_before_giving_up(self):
        feed = BlofinFeed()
        with patch.object(blofin_feed, "PUBLIC_BUCKET", _fast_bucket()), \
             patch.object(feed.session, "get", side_effect=requests.exceptions.SSLError("cert verify")) as mock_get, \
             patch("sys.stdout", StringIO()):
            out = feed._get("market/instruments")
        self.assertIsNone(out)
        self.assertEqual(2, mock_get.call_count)

    def test_timeout_retries_once_before_giving_up(self):
        feed = BlofinFeed()
        with patch.object(blofin_feed, "PUBLIC_BUCKET", _fast_bucket()), \
             patch.object(feed.session, "get", side_effect=requests.Timeout("x")) as mock_get, \
             patch("sys.stdout", StringIO()):
            out = feed._get("market/instruments")
        self.assertIsNone(out)
        self.assertEqual(2, mock_get.call_count)

    def test_http_403_includes_body(self):
        feed = BlofinFeed()
        resp = MagicMock()
        resp.status_code = 403
        resp.text = "cloudflare blocked"
        resp.json.return_value = {}
        buf = StringIO()
        with patch.object(blofin_feed, "PUBLIC_BUCKET", _fast_bucket()), \
             patch.object(feed.session, "get", return_value=resp) as mock_get, \
             patch("sys.stdout", buf):
            out = feed._get("market/instruments")
        self.assertIsNone(out)
        self.assertIn("403", feed.last_error)
        self.assertIn("cloudflare", feed.last_error)
        self.assertEqual(2, mock_get.call_count)
        self.assertIn("WAF headers", buf.getvalue())

    def test_probe_prints_fail_when_get_returns_none(self):
        feed = BlofinFeed()
        feed.last_error = "timeout 4/20s GET market/instruments"
        buf = StringIO()
        with patch.object(feed, "_get", return_value=None), \
             patch.object(feed, "_tcp_probe", return_value=["1.2.3.4"]), \
             patch.object(feed, "_tls_probe", return_value="OK TLSv1.3"), \
             patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("1.2.3.4", 443))]), \
             patch("sys.stdout", buf):
            report = feed.probe_public()
        self.assertFalse(report["ok"])
        self.assertIn("timeout", report["error"])
        self.assertIn("probe FAIL", buf.getvalue())


class TestTickersUniverseFallback(unittest.TestCase):
    def test_synth_from_tickers_keeps_usdt_bases(self):
        from blofin_feed import _synth_instruments_from_tickers
        rows = _synth_instruments_from_tickers([
            {"instId": "BTC-USDT"},
            {"instId": "ETH-USDT-SWAP"},
            {"instId": "BTC-USDT"},
            {"instId": "DOGE-USD"},
            {"instId": ""},
        ])
        ids = [r["instId"] for r in rows]
        self.assertEqual(ids, ["BTC-USDT", "ETH-USDT"])
        self.assertEqual(rows[0]["baseCurrency"], "BTC")
        self.assertEqual(rows[0]["state"], "live")

    def test_fetch_instruments_falls_back_to_tickers(self):
        feed = BlofinFeed()
        tickers = {"code": "0", "data": [
            {"instId": "SOL-USDT", "last": "100"},
            {"instId": "XRP-USDT", "last": "2"},
        ]}

        def fake_get(path, params=None, timeout=None, _attempt=0):
            if "instruments" in path:
                feed.last_error = "timeout 4/20s GET market/instruments"
                return None
            if "tickers" in path:
                return tickers
            return None

        buf = StringIO()
        with patch.object(blofin_feed, "PUBLIC_BUCKET", _fast_bucket()), \
             patch.object(feed, "_get", side_effect=fake_get), \
             patch("sys.stdout", buf):
            out = feed.fetch_instruments()
        self.assertEqual(out.get("msg"), "tickers_fallback")
        self.assertEqual(len(out["data"]), 2)
        self.assertIsNone(feed.last_error)
        self.assertTrue(feed.available)
        self.assertIn("universe z tickers", buf.getvalue())

    def test_probe_recovers_via_tickers(self):
        feed = BlofinFeed()
        tickers = {"code": "0", "data": [{"instId": "BTC-USDT", "last": "1"}]}

        def fake_get(path, params=None, timeout=None, _attempt=0):
            if "instruments" in path:
                feed.last_error = "SSL cert verify"
                return None
            return tickers

        buf = StringIO()
        with patch.object(feed, "_get", side_effect=fake_get), \
             patch.object(feed, "_tcp_probe", return_value=["1.2.3.4"]), \
             patch.object(feed, "_tls_probe", return_value="OK TLSv1.3"), \
             patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("1.2.3.4", 443))]), \
             patch("sys.stdout", buf):
            report = feed.probe_public()
        self.assertTrue(report["ok"])
        self.assertEqual(report["source"], "tickers")
        self.assertEqual(report["n"], 1)
        self.assertIn("probe recovered via tickers", buf.getvalue())
        self.assertIn("probe FAIL", buf.getvalue())

    def test_feeder_fills_universe_from_tickers_fallback(self):
        import tempfile
        from pathlib import Path
        import disk_cache
        from data_feeder import DataFeeder

        feed = BlofinFeed()

        def fake_get(path, params=None, timeout=None, _attempt=0):
            if "instruments" in path:
                feed.last_error = "HTTP 403 cloudflare"
                return None
            return {"code": "0", "data": [
                {"instId": "BTC-USDT", "last": "60000"},
                {"instId": "ETH-USDT", "last": "3000"},
            ]}

        with tempfile.TemporaryDirectory() as td:
            with patch.object(disk_cache, "CACHE_DIR", Path(td)), \
                 patch.object(feed, "_get", side_effect=fake_get):
                feeder = DataFeeder()
                feeder.blofin = feed
                feeder.instruments_cache = []
                feeder.instruments_ts = 0
                rows = feeder.fetch_blofin_usdt_instruments()
        self.assertEqual({r["symbol"] for r in rows}, {"BTC", "ETH"})
        self.assertEqual(feeder.instruments_fail_streak, 0)

    def test_registry_loads_synth_rows(self):
        feed = BlofinFeed()
        feed.fetch_instruments = lambda: {
            "code": "0", "msg": "tickers_fallback",
            "data": [{
                "instId": "BTC-USDT", "baseCurrency": "BTC",
                "quoteCurrency": "USDT", "state": "live",
                "instType": "SWAP", "contractType": "linear",
            }],
        }
        feeder = MagicMock()
        feeder.blofin = feed
        reg = InstrumentRegistry(feeder=feeder)
        self.assertTrue(reg.reload())
        self.assertEqual(reg.count(), 1)
        self.assertIsNotNone(reg.get("BTC"))


class TestBlofinTransport(unittest.TestCase):
    def test_session_uses_browser_ua(self):
        feed = BlofinFeed()
        self.assertEqual(feed.session.headers.get("User-Agent"), _BROWSER_UA)

    def test_waf_headers_include_origin(self):
        feed = BlofinFeed()
        self.assertEqual(feed.session.headers.get("Origin"), "https://blofin.com")
        self.assertTrue(str(feed.session.headers.get("Referer") or "").startswith("https://blofin.com"))

    def test_waf_flag_off_skips_origin(self):
        import config
        with patch.object(config, "BLOFIN_WAF_BROWSER_HEADERS", False):
            feed = BlofinFeed()
        self.assertFalse(feed._waf)
        self.assertIsNone(feed.session.headers.get("Origin"))
        self.assertEqual(feed.session.headers.get("User-Agent"), _BROWSER_UA)

    def test_https_adapter_is_ipv4(self):
        feed = BlofinFeed()
        adapter = feed.session.get_adapter("https://openapi.blofin.com")
        self.assertIsInstance(adapter, _BlofinAdapter)
        self.assertTrue(adapter._ipv4_only)
        self.assertIsNotNone(adapter._ssl_context)
        pool_cls = adapter.poolmanager.pool_classes_by_scheme["https"]
        self.assertTrue(pool_cls.ConnectionCls.ipv4_only)

    def test_ipv4_flag_off_uses_dual_stack(self):
        import config
        with patch.object(config, "BLOFIN_IPV4_ONLY", False):
            feed = BlofinFeed()
        adapter = feed.session.get_adapter("https://openapi.blofin.com")
        self.assertFalse(feed._ipv4_only)
        self.assertFalse(adapter._ipv4_only)
        pool_cls = adapter.poolmanager.pool_classes_by_scheme["https"]
        self.assertFalse(pool_cls.ConnectionCls.ipv4_only)

    def test_connect_family_requests_af_inet(self):
        import socket
        seen = []

        def fake_gai(host, port, family, *a, **k):
            seen.append(family)
            raise OSError("nope")

        with patch("socket.getaddrinfo", side_effect=fake_gai):
            with self.assertRaises(OSError):
                _connect_family(("openapi.blofin.com", 443), 1.0, None, None, socket.AF_INET)
        self.assertEqual(seen, [socket.AF_INET])

    def test_connection_error_retries_on_dual_stack(self):
        feed = BlofinFeed()
        self.assertTrue(feed._ipv4_only)
        with patch.object(blofin_feed, "PUBLIC_BUCKET", _fast_bucket()), \
             patch.object(feed.session, "get", side_effect=requests.exceptions.ConnectionError("refused")) as mock_get, \
             patch("sys.stdout", StringIO()):
            out = feed._get("market/instruments")
        self.assertIsNone(out)
        self.assertEqual(2, mock_get.call_count)
        self.assertFalse(feed._ipv4_only)

    def test_executor_gets_same_transport(self):
        from blofin_executor import BloFinExecutor
        from unittest.mock import MagicMock
        ex = BloFinExecutor(registry=MagicMock())
        adapter = ex.session.get_adapter("https://openapi.blofin.com")
        self.assertIsInstance(adapter, _BlofinAdapter)
        self.assertEqual(ex.session.headers.get("User-Agent"), _BROWSER_UA)
        self.assertEqual(ex.session.headers.get("Origin"), "https://blofin.com")


class TestRegistryUsesFeeder(unittest.TestCase):
    def test_registry_reads_feeder_last_error(self):
        feed = BlofinFeed()
        feed.last_error = "SSL boom"
        feeder = MagicMock()
        feeder.blofin = feed
        with patch.object(feed, "_get", return_value=None):
            reg = InstrumentRegistry(feeder=feeder)
            ok = reg.reload()
        self.assertFalse(ok)
        self.assertEqual(reg.last_error, "SSL boom")
        self.assertEqual(reg.count(), 0)


class TestAppProbesBlofinOnStart(unittest.TestCase):
    def test_app_source_calls_probe(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
        self.assertIn("feeder.blofin.probe_public()", src)
        self.assertIn("InstrumentRegistry: 0 par", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
