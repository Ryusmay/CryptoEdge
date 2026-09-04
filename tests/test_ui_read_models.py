"""Testy projekcji UI (test_ui_read_models.py).

Testy byly tu golymi funkcjami w stylu pytest. run_tests.py uzywa loadera
unittest, ktory takich funkcji NIE zbiera - modul raportowal "Ran 0 tests",
a caly przebieg konczyl sie kodem 1. Pytest nie jest zainstalowany, wiec te
asercje nie wykonaly sie nigdy, w zadnym runnerze.

Opakowane w TestCase 2026-09-04. Cialka i asercje bez zmian - naprawa
uruchamiania i zmiana tresci testu to dwie rozne rzeczy.
"""
import unittest

from types import SimpleNamespace
from ui_read_models import (
    build_ui_read_models, equity_drawdown_projection, exposure_projection,
    history_projection, reconciliation_projection, signal_telemetry_projection,
)


class TestUiReadModels(unittest.TestCase):

    def test_partial_runtime_is_safe_and_empty(self):
        result = build_ui_read_models(SimpleNamespace())
        assert result["history"] == []
        assert result["exposure"]["gross"] == 0
        assert result["reconciliation"]["status"] == "unknown"
        assert result["signals"]["rows"] == []


    def test_history_accepts_dict_and_object_without_mutation(self):
        original = {"symbol": "btc", "side": "long", "entry": "100", "exit": 110, "pnl": "10"}
        other = SimpleNamespace(symbol="eth", direction="short", entry_price=200, exit_price=190, pnl=5)
        runtime = SimpleNamespace(trader=SimpleNamespace(closed_positions=[original, other]))
        rows = history_projection(runtime)
        assert [row["symbol"] for row in rows] == ["ETH", "BTC"]
        assert rows[1]["pnl"] == 10
        assert original == {"symbol": "btc", "side": "long", "entry": "100", "exit": 110, "pnl": "10"}


    def test_equity_and_drawdown_follow_chronological_pnl(self):
        closed = [{"pnl": 10, "time": "a"}, {"pnl": -30, "time": "b"}, {"pnl": 5, "time": "c"}]
        runtime = SimpleNamespace(risk=SimpleNamespace(starting_capital=100), trader=SimpleNamespace(closed_positions=closed))
        result = equity_drawdown_projection(runtime)
        assert result["current_equity"] == 85
        assert result["peak_equity"] == 110
        assert result["max_drawdown"] == 30
        assert [point["equity"] for point in result["points"]] == [110, 80, 85]


    def test_exposure_uses_notional_or_size_times_mark(self):
        positions = [
            {"symbol": "btc", "side": "LONG", "notional": 1000},
            SimpleNamespace(symbol="eth", direction="SHORT", size=2, mark_price=200),
        ]
        result = exposure_projection(SimpleNamespace(trader=SimpleNamespace(positions=positions)))
        assert result == {"gross": 1400, "net": 600, "long": 1000, "short": 400,
                          "positions": 2, "by_symbol": {"BTC": 1000, "ETH": -400}}


    def test_reconciliation_surfaces_mismatch_without_trading_calls(self):
        runtime = SimpleNamespace(reconciliation_state={"status": "ok", "issues": [
            {"symbol": "sol", "type": "size", "message": "local 1 exchange 0"}
        ]})
        result = reconciliation_projection(runtime)
        assert result["status"] == "mismatch"
        assert result["mismatch_count"] == 1
        assert result["mismatches"][0]["symbol"] == "SOL"


    def test_signal_telemetry_counts_gates_and_engines(self):
        state = {"signals": [
            {"sym": "btc", "gate": "cost", "strategy": "v2", "score": 1},
            SimpleNamespace(symbol="eth", reject_reason="cost", engine="v2", score="bad"),
            {"symbol": "sol", "gate": "ready", "engine": "v1"},
        ]}
        runtime = SimpleNamespace(logger=SimpleNamespace(last_state=state))
        result = signal_telemetry_projection(runtime)
        assert result["by_gate"] == {"cost": 2, "ready": 1}
        assert result["by_engine"] == {"v2": 2, "v1": 1}
        assert result["rows"][0]["symbol"] == "SOL"

if __name__ == "__main__":
    unittest.main()
