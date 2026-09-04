import json
import threading
import time
import unittest
from unittest.mock import patch
from http.client import HTTPConnection
from pathlib import Path
from types import SimpleNamespace

import engine_api


class FakeRisk:
    def __init__(self):
        self.current_capital = 1000.0
        self.daily_pnl = 12.5
        self.paused = False
        self.is_halted = False

    def can_open_position(self, row):
        return True, None


class FakePos:
    def __init__(self):
        self.symbol = "BTC"
        self.side = "LONG"
        self.entry_price = 100.0
        self.sl_price = 95.0
        self.size_usd = 100.0
        self.margin = 10.0
        self.unrealized_pnl = 5.0
        self.pnl_pct = 5.0
        self.trailing_active = True
        self.breakeven_active = False
        self.engine = "V2"
        self.leverage = 10
        self.entry_time = None

    def pnl_at_stop(self):
        return -5.0


class FakeTrader:
    def __init__(self):
        self.positions = [FakePos()]
        self.closed_positions = []
        self.lock = threading.Lock()


class FakeRuntime:
    def __init__(self):
        self.engine_enabled = True
        self.trading_enabled = False
        self.analysis_loading = False
        self.started_at = time.time() - 3661
        self.risk = FakeRisk()
        self.trader = FakeTrader()
        self.logger = SimpleNamespace(last_state={
            "signals": [{"symbol": "ETH", "direction": "LONG", "strength": 0.71, "expected_net_r": 1.9}],
            "market_regime": {"regime": "TREND"},
        })
        self.last_price_map = {"BTC": 105.0, "ETH": 2500.0}
        self.protection = SimpleNamespace(kill_switch_active=False)
        self.calls = []

    def start_analysis(self):
        self.calls.append("start_analysis")
        return "ANALYSIS_ON"

    def start_trading(self):
        self.calls.append("start_trading")
        return "TRADING_ON"

    def close_all(self):
        self.calls.append("close_all")
        return "CLOSED 1"


class TestEngineApi(unittest.TestCase):
    @patch("secrets_store.load_secrets")
    def test_blofin_credentials_status_never_returns_secret(self, load_secrets):
        load_secrets.return_value = {
            "BLOFIN_API_KEY": "public-key-1234",
            "BLOFIN_API_SECRET": "never-return-this",
            "BLOFIN_API_PASSPHRASE": "nor-this",
        }
        status = engine_api.blofin_credentials_status()
        self.assertTrue(status["configured"])
        self.assertTrue(status["masked_key"].endswith("1234"))
        serialized = json.dumps(status)
        self.assertNotIn("never-return-this", serialized)
        self.assertNotIn("nor-this", serialized)

    @patch("secrets_store.save_secrets")
    def test_incomplete_blofin_credentials_are_not_saved(self, save_secrets):
        result = engine_api.update_blofin_credentials(FakeRuntime(), {
            "action": "save", "api_key": "key", "api_secret": "secret", "passphrase": "",
        })
        self.assertFalse(result["ok"])
        save_secrets.assert_not_called()

    def test_replay_progress_projects_live_symbol_rows(self):
        job = engine_api.ReplayJob(FakeRuntime())
        job.state = {**job._idle(), "running": True, "started_at": time.time(), "symbols": []}
        job._progress("BTC: instrument 1/10")
        job._progress("BTC: dane gotowe · 26139 świec 5m")
        snap = job.snapshot()
        self.assertTrue(snap["running"])
        self.assertEqual(snap["total"], 10)
        self.assertEqual(snap["symbols"][0]["symbol"], "BTC")
        self.assertEqual(snap["symbols"][0]["bars_5m"], 26139)
        self.assertEqual(snap["symbols"][0]["status"], "Dane gotowe")

    def test_replay_rejects_second_concurrent_job(self):
        job = engine_api.ReplayJob(FakeRuntime())
        job.state["running"] = True
        result = job.start({"days": 90, "universe_mode": "LIQUID"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "replay_already_running")

    def test_engine_progress_updates_warmup_count(self):
        rt = FakeRuntime()
        rt.analysis_loading = True
        rt.last_coins = [{"symbol": "BTC"}, {"symbol": "ETH"}, {"symbol": "SOL"}]
        rt.last_state_snapshot = {"warmup": {
            "active": True, "ready": False, "candidates": 3, "ready_pairs": 2,
        }}
        progress = engine_api._engine_progress(rt)
        self.assertEqual(progress["phase"], "warming")
        self.assertEqual(progress["available"], 2)
        self.assertEqual(progress["total"], 3)
        self.assertIn("2/3", progress["message"])

    def test_engine_progress_confirms_analysis_and_trading(self):
        rt = FakeRuntime()
        rt.trading_enabled = True
        rt.last_coins = [{"symbol": "BTC"}, {"symbol": "ETH"}]
        progress = engine_api._engine_progress(rt, {"universe_size": 2})
        self.assertTrue(progress["ready"])
        self.assertIn("analiza gotowa", progress["message"])
        self.assertIn("handel uruchomiony", progress["message"])

    def test_cycle_event_is_not_exposed_to_ui(self):
        self.assertIsNone(engine_api._present_event({
            "timestamp": "2026-08-24T18:00:00+00:00", "event": "CYCLE",
            "reasons": "trade=True|BTC:LONG:DAY_CHOP",
        }))

    def test_open_event_is_short_and_hides_strategy_reasons(self):
        event = engine_api._present_event({
            "timestamp": "2026-08-24T18:00:00+00:00", "event": "OPEN",
            "symbol": "btc", "direction": "LONG", "price": "123.450000",
            "reasons": "very technical|internal detail",
        })
        self.assertEqual(event, {
            "time": "18:00:00", "tag": "POZYCJA",
            "text": "BTC: otwarto LONG po 123.45",
        })
        self.assertNotIn("technical", event["text"])

    def test_close_event_contains_only_price_and_result(self):
        event = engine_api._present_event({
            "timestamp": "2026-08-24T18:05:00+00:00", "event": "CLOSE",
            "symbol": "eth", "price": "2500", "pnl": "-4.25",
            "reasons": "exit=SL|gross=-3.5|costs=0.75",
        })
        self.assertEqual(event["tag"], "POZYCJA")
        self.assertEqual(event["text"], "ETH: zamknięto po 2500 · strata 4.25 USD")
        self.assertNotIn("costs", event["text"])

    def test_build_status_has_v14_position_fields(self):
        payload = engine_api.build_status(FakeRuntime())
        self.assertTrue(payload["ok"])
        pos = payload["positions"][0]
        self.assertEqual(pos["symbol"], "BTC")
        self.assertEqual(pos["sl_mark"], "▲")
        self.assertEqual(pos["pnl_at_stop"], -5.0)
        self.assertEqual(payload["session"]["uptime"], "1h 1m")
        self.assertEqual(payload["candidates"][0]["sym"], "ETH")
        self.assertEqual(payload["engine"]["mode"], "DEMO")

    def test_market_projection_converts_columnar_ohlcv(self):
        frame = {"timestamps": [10, 20], "opens": [1, 2], "highs": [2, 3], "lows": [0.5, 1.5], "closes": [1.5, 2.5], "volumes": [100, 200]}
        with patch.object(engine_api, "_candles", return_value={"candles": frame}):
            market = engine_api._market_projection(FakeRuntime(), limit=1)
        self.assertEqual(market["BTC"]["candles"], [{"time": 20, "open": 2.0, "high": 3.0, "low": 1.5, "close": 2.5, "volume": 200.0}])

    def test_mutating_action_requires_confirm(self):
        rt = FakeRuntime()
        api = engine_api.start_engine_api(rt, host="127.0.0.1", port=47931)
        self.addCleanup(api.stop)
        self.assertTrue(api.url)
        conn = HTTPConnection("127.0.0.1", api.port, timeout=2)
        conn.request("POST", "/api/engine/close_all", body=b"{}", headers={"Content-Type": "application/json"})
        res = conn.getresponse()
        body = json.loads(res.read())
        self.assertEqual(res.status, 400)
        self.assertEqual(body["error"], "confirm_required")
        self.assertEqual(rt.calls, [])
        conn.close()
        conn = HTTPConnection("127.0.0.1", api.port, timeout=2)
        conn.request(
            "POST", "/api/engine/close_all",
            body=json.dumps({"confirm": True}).encode(),
            headers={"Content-Type": "application/json"},
        )
        res = conn.getresponse()
        body = json.loads(res.read())
        conn.close()
        self.assertTrue(body["ok"])
        self.assertIn("close_all", rt.calls)

    def test_status_and_html_served(self):
        api = engine_api.start_engine_api(FakeRuntime(), host="127.0.0.1", port=47941)
        self.addCleanup(api.stop)
        conn = HTTPConnection("127.0.0.1", api.port, timeout=2)
        conn.request("GET", "/health")
        res = conn.getresponse()
        self.assertEqual(res.status, 200)
        self.assertTrue(json.loads(res.read())["ok"])
        conn.close()
        conn = HTTPConnection("127.0.0.1", api.port, timeout=2)
        conn.request("GET", "/")
        res = conn.getresponse()
        html = res.read().decode("utf-8")
        conn.close()
        self.assertEqual(res.status, 200)
        self.assertIn("ZYSK SL", html)
        self.assertIn("/api/status", html)
        self.assertIn("Zamknij wszystkie", html)

    def test_http_api_does_not_own_market_websocket(self):
        api = engine_api.start_engine_api(FakeRuntime(), host="127.0.0.1", port=47961)
        self.addCleanup(api.stop)
        conn = HTTPConnection("127.0.0.1", api.port, timeout=2)
        conn.request("GET", "/api/stream")
        res = conn.getresponse()
        body = json.loads(res.read())
        conn.close()
        self.assertEqual(res.status, 404)
        self.assertEqual(body["error"], "not_found")

    def test_loopback_forced_without_token(self,):
        api = engine_api.EngineApi(FakeRuntime(), host="0.0.0.0", port=47951)
        api.token = ""
        api.start()
        self.addCleanup(api.stop)
        self.assertEqual(api.host, "127.0.0.1")

    def test_html_file_exists(self):
        self.assertTrue((Path(engine_api.WEB_DIR) / "desk.html").exists())

    def test_app_starts_api_alongside_ui(self):
        src = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
        self.assertIn("from engine_api import start_engine_api", src)
        self.assertIn("ENGINE_API_ENABLED", src)
        cfg = (Path(__file__).resolve().parents[1] / "config.py").read_text(encoding="utf-8")
        self.assertIn("ENGINE_API_HOST = \"127.0.0.1\"", cfg)


if __name__ == "__main__":
    unittest.main()
