"""Testy projekcji UI (test_ui_trade_projection.py).

Testy byly tu golymi funkcjami w stylu pytest. run_tests.py uzywa loadera
unittest, ktory takich funkcji NIE zbiera - modul raportowal "Ran 0 tests",
a caly przebieg konczyl sie kodem 1. Pytest nie jest zainstalowany, wiec te
asercje nie wykonaly sie nigdy, w zadnym runnerze.

Opakowane w TestCase 2026-09-04. Cialka i asercje bez zmian - naprawa
uruchamiania i zmiana tresci testu to dwie rozne rzeczy.
"""
import unittest

from datetime import datetime, timezone
from types import SimpleNamespace
from ui_trade_projection import normalize_symbol, project_ui_trades


class TestUiTradeProjection(unittest.TestCase):

    def test_projects_levels_and_object_markers_with_stable_ids(self):
        candidate = {"candidate_id": "c1", "symbol": "btc-usdt", "price": 100, "sl_price": 95, "tp1_price": 110}
        opened = datetime(2026, 8, 31, 10, tzinfo=timezone.utc)
        position = SimpleNamespace(id="p 1", symbol="BTC/USDT", direction="LONG", entry_price=100, entry_time=opened)
        closed = {"id": "p 1", "symbol": "BTCUSDT", "direction": "LONG", "entry_price": 100,
                  "entry_time": opened, "exit_price": 108, "exit_time": "2026-08-31T11:00:00Z"}
        result = project_ui_trades(candidates=[candidate], positions=[position], closed=[closed])
        assert [(x["kind"], x["price"]) for x in result["BTC"]["levels"]] == [
            ("entry", 100.0), ("stop", 95.0), ("target", 110.0)]
        assert result == project_ui_trades(candidates=[candidate], positions=[position], closed=[closed])
        assert result["BTC"]["markers"][0]["kind"] == "entry"
        assert result["BTC"]["markers"][-1]["kind"] == "exit"
        assert " " not in result["BTC"]["markers"][0]["id"]


    def test_dict_and_object_events_and_millisecond_time(self):
        events = [
            {"event_id": "f1", "symbol": "SOLUSDT", "tag": "ORDER_FILLED", "ts_ms": 1_700_000_000_000, "fill_price": 50},
            SimpleNamespace(id="e1", symbol="SOL-USDT", type="POSITION_CLOSED", timestamp=1_700_000_100, price=55),
        ]
        markers = project_ui_trades(events=events)["SOL"]["markers"]
        assert [(x["kind"], x["time"], x["price"]) for x in markers] == [
            ("fill", 1_700_000_000, 50.0), ("exit", 1_700_000_100, 55.0)]


    def test_malformed_values_are_ignored_fail_closed(self):
        result = project_ui_trades(
            candidates=[{"symbol": "ETHUSDT", "price": float("nan"), "sl_price": -1, "tp_price": "bad"}],
            positions=[{"symbol": "ETH", "entry_time": "bad", "entry_price": float("nan")}],
            events=[{"symbol": "ETH", "tag": "FILL", "time": 10, "price": float("inf")}],
        )
        assert result == {"ETH": {"levels": [], "markers": []}}
        assert normalize_symbol(" eth/usdc ") == "ETH"
        assert normalize_symbol(None) is None

if __name__ == "__main__":
    unittest.main()
