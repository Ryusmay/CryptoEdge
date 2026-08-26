import unittest
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from position_reconciler import PositionReconciler
from blofin_executor import BloFinExecutor
from order_models import Order, OrderState
from market_data import aggregate_to_4h, binance_blofin_divergence
import config
from unittest.mock import patch


class TestReconcileSize(unittest.TestCase):
    def test_size_mismatch_detected(self):
        rec = PositionReconciler()
        rec.fetch_exchange_positions = lambda: [
            {"symbol": "BTC", "direction": "LONG", "size": 2.0}
        ]
        local = [{"symbol": "BTC", "direction": "LONG", "size_contracts": 1.0}]
        with patch.object(config, "PAPER_TRADING", False):
            report = rec.reconcile(local)
        self.assertFalse(report["in_sync"])
        self.assertTrue(len(report["size_mismatch"]) >= 1)
        self.assertTrue(report["drift_blocks_entries"])

    def test_size_ok_within_tolerance(self):
        rec = PositionReconciler()
        rec.fetch_exchange_positions = lambda: [
            {"symbol": "BTC", "direction": "LONG", "size": 1.0}
        ]
        local = [{"symbol": "BTC", "direction": "LONG", "size_contracts": 1.0}]
        report = rec.reconcile(local)
        self.assertTrue(report["in_sync"])
        self.assertFalse(report["drift_blocks_entries"])


class TestMatchOrderRow(unittest.TestCase):
    def test_no_blind_rows0(self):
        o = Order(
            client_order_id="CEABC", symbol="BTC", inst_id="BTC-USDT",
            side="buy", direction="LONG", size=1,
        )
        o.order_id = "111"
        rows = [
            {"orderId": "999", "clientOrderId": "OTHER", "state": "filled"},
            {"orderId": "111", "clientOrderId": "CEABC", "state": "live"},
        ]
        row = BloFinExecutor._match_order_row(o, rows)
        self.assertEqual(row["orderId"], "111")

    def test_empty_when_no_match(self):
        o = Order(
            client_order_id="CEABC", symbol="BTC", inst_id="BTC-USDT",
            side="buy", direction="LONG", size=1,
        )
        o.order_id = "111"
        rows = [{"orderId": "999", "clientOrderId": "X", "state": "filled"}]
        self.assertIsNone(BloFinExecutor._match_order_row(o, rows))


class TestNoSynthetic4h(unittest.TestCase):
    def test_no_timestamps_returns_empty(self):
        ohlcv = {
            "closes": list(range(50)),
            "highs": list(range(50)),
            "lows": list(range(50)),
            "volumes": [1]*50,
        }
        self.assertEqual(aggregate_to_4h(ohlcv), {})


class TestDivergenceMissing(unittest.TestCase):
    def test_missing_ok_when_not_required(self):
        config.REQUIRE_BN_BF_DIVERGENCE = False
        d = binance_blofin_divergence({"binance_price": 100})
        self.assertTrue(d["ok"])

    def test_missing_blocks_when_required(self):
        config.REQUIRE_BN_BF_DIVERGENCE = True
        d = binance_blofin_divergence({"binance_price": 100})
        self.assertFalse(d["ok"])
        self.assertTrue(d.get("hard"))
        config.REQUIRE_BN_BF_DIVERGENCE = False


class TestPositionSide(unittest.TestCase):
    def test_one_way_net(self):
        config.BLOFIN_POSITION_MODE = "one_way"
        self.assertEqual(BloFinExecutor._resolve_position_side("net", "LONG"), "net")

    def test_hedge_long_short(self):
        config.BLOFIN_POSITION_MODE = "hedge"
        self.assertEqual(BloFinExecutor._resolve_position_side("net", "LONG"), "long")
        self.assertEqual(BloFinExecutor._resolve_position_side("net", "SHORT"), "short")
        config.BLOFIN_POSITION_MODE = "one_way"


if __name__ == "__main__":
    unittest.main(verbosity=2)
