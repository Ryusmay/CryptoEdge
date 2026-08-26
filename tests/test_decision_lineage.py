"""Slad decyzji: od snapshotu, przez odrzucenie, do wyniku.

26.08.2026: decision_id bylo bite dopiero przy zapisie telemetrii, wiec
odrzucenie nie mialo jak wskazac stanu rynku, ktory je wywolal. Do tego
trzy powody odrzucenia nie trafialy do telemetrii w ogole, bo wolajacy nie
przekazywal `signal=`. Efekt: na pytanie "czemu nie weszlismy w ETH o 14:32"
nie dalo sie odpowiedziec inaczej niz czytaniem konsoli.
"""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import config
from cryptoedge.services import DecisionPipeline

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


class _Snapshot(SimpleNamespace):
    pass


def _pipeline(decision):
    snapshot = _Snapshot(symbol="ETH", snapshot_id="snap_abc", decision_ts_ms=1_700_000_000_000)
    market = SimpleNamespace(snapshot=lambda symbol, decision_ts_ms=None: snapshot)
    strategy = SimpleNamespace(evaluate=lambda snap: decision)
    return DecisionPipeline(market, strategy, risk=None)


class TestLineageStamp(unittest.TestCase):
    def test_decision_gets_id_at_the_snapshot(self):
        decision = {"symbol": "ETH", "direction": "NEUTRAL"}
        _pipeline(decision).analyze("ETH")
        self.assertTrue(decision["decision_id"])
        self.assertEqual("snap_abc", decision["snapshot_id"])
        self.assertEqual(1_700_000_000_000, decision["decision_ts_ms"])

    def test_rejection_and_acceptance_share_one_id(self):
        """Ta sama ocena musi miec jeden identyfikator na calej sciezce."""
        decision = {"symbol": "ETH", "direction": "LONG"}
        _pipeline(decision).analyze("ETH")
        first = decision["decision_id"]
        _pipeline(decision).analyze("ETH")
        self.assertEqual(first, decision["decision_id"])

    def test_existing_id_is_never_overwritten(self):
        decision = {"symbol": "ETH", "decision_id": "z-gory-nadany"}
        _pipeline(decision).analyze("ETH")
        self.assertEqual("z-gory-nadany", decision["decision_id"])

    def test_non_dict_decision_does_not_crash(self):
        result = _pipeline(SimpleNamespace(direction="LONG")).analyze("ETH")
        self.assertIsNotNone(result.decision)

    def test_two_decisions_get_different_ids(self):
        a, b = {"symbol": "ETH"}, {"symbol": "BTC"}
        _pipeline(a).analyze("ETH")
        _pipeline(b).analyze("BTC")
        self.assertNotEqual(a["decision_id"], b["decision_id"])


class TestTelemetryCarriesLineage(unittest.TestCase):
    def test_row_points_back_at_market_state(self):
        from decision_telemetry import decision_snapshot

        path = Path(config.__file__).parent / "logs" / "_test_lineage.jsonl"
        signal = {
            "symbol": "ETH", "direction": "LONG", "engine": "daytrading_v2",
            "decision_id": "abc123", "snapshot_id": "snap_xyz",
            "decision_ts_ms": 1_700_000_000_000,
        }
        with patch.object(config, "DECISION_TELEMETRY_PATH", str(path)):
            decision_snapshot(signal, "REJECT", "V2_RANGE_SKIP")
        row = json.loads(path.read_text(encoding="utf-8").strip().splitlines()[-1])
        path.unlink(missing_ok=True)
        self.assertEqual("abc123", row["decision_id"])
        self.assertEqual("snap_xyz", row["snapshot_id"])
        self.assertEqual(1_700_000_000_000, row["decision_ts_ms"])
        self.assertEqual("V2_RANGE_SKIP", row["reason"])


class TestEveryRejectionIsVisible(unittest.TestCase):
    """log_reject zapisuje telemetrie tylko gdy dostanie signal=."""

    def test_no_caller_forgets_the_signal(self):
        source = (ROOT / "paper_trader.py").read_text(encoding="utf-8")
        blocks = source.split("log_reject(")[1:]
        missing = []
        for block in blocks:
            call = block[:block.index(")\n") + 1] if ")\n" in block else block[:400]
            if "signal=" not in call:
                missing.append(" ".join(call.split())[:90])
        self.assertEqual([], missing, "Odrzucenia bez telemetrii:\n" + "\n".join(missing))

    def test_unreachable_live_branch_is_gone(self):
        """Blok LIVE_NOT_WIRED stal za bezwarunkowym return None."""
        source = (ROOT / "paper_trader.py").read_text(encoding="utf-8")
        self.assertNotIn("LIVE_NOT_WIRED", source)


class TestWhyTool(unittest.TestCase):
    def _rows(self):
        return [
            {"event": "DECISION", "ts": 1000.0, "decision_id": "aaa1", "decision": "REJECT",
             "symbol": "ETH", "direction": "LONG", "reason": "V2_RANGE_SKIP"},
            {"event": "DECISION", "ts": 2000.0, "decision_id": "bbb2", "decision": "ACCEPT",
             "symbol": "BTC", "direction": "LONG", "reason": ""},
            {"event": "OUTCOME", "ts": 3000.0, "decision_id": "bbb2", "pnl_usd": 1.5},
        ]

    def test_summary_counts_accepts_and_rejects(self):
        import why

        with patch("builtins.print") as printed:
            code = why.show_summary(self._rows(), None, 10)
        text = " ".join(str(c.args[0]) for c in printed.call_args_list if c.args)
        self.assertEqual(0, code)
        self.assertIn("wejsc 1", text)
        self.assertIn("odrzucen 1", text)
        self.assertIn("V2_RANGE_SKIP", text)

    def test_lineage_joins_decision_with_outcome(self):
        import why

        with patch("builtins.print") as printed:
            code = why.show_lineage(self._rows(), "bbb2")
        text = " ".join(str(c.args[0]) for c in printed.call_args_list if c.args)
        self.assertEqual(0, code)
        self.assertIn("DECISION", text)
        self.assertIn("OUTCOME", text)

    def test_missing_symbol_explains_itself(self):
        import why

        with patch("builtins.print") as printed:
            code = why.show_symbol(self._rows(), "DOGE", None, 5)
        text = " ".join(str(c.args[0]) for c in printed.call_args_list if c.args)
        self.assertEqual(1, code)
        self.assertIn("Brak jakiejkolwiek decyzji", text)

    def test_at_reports_row_age_so_dedupe_is_not_mistaken_for_silence(self):
        import why

        with patch("builtins.print") as printed:
            why.show_symbol(self._rows(), "ETH", 1600.0, 5)
        text = " ".join(str(c.args[0]) for c in printed.call_args_list if c.args)
        self.assertIn("600 s", text)
        self.assertIn("dedupe", text)

    def test_parse_at_accepts_bare_clock(self):
        import why

        from datetime import datetime
        parsed = datetime.fromtimestamp(why.parse_at("14:32"))
        self.assertEqual((14, 32), (parsed.hour, parsed.minute))

    def test_parse_at_rejects_garbage(self):
        import why

        with self.assertRaises(SystemExit):
            why.parse_at("kiedys")


if __name__ == "__main__":
    unittest.main()
