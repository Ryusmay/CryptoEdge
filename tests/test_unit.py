# ============================================================
# 32. Testy jednostkowe – accounting, portfolio, orders, market_data
# ============================================================

import unittest
import time
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from order_models import Order, OrderState, new_client_order_id
from accounting import (
    fee_usd, entry_exit_costs, funding_payment, realized_pnl, unrealized_pnl,
    notional_to_contracts_dec, EquityLedger, D,
)
from portfolio_risk import (
    compute_exposure, aggregate_leverage, cluster_of, check_portfolio_limits,
)
from market_data import (
    drop_unclosed_candle, aggregate_to_4h, binance_blofin_divergence,
    normalize_symbol, STALE,
)
from protection import ProtectionManager


class TestOrderStateMachine(unittest.TestCase):
    def test_client_order_id_length(self):
        cid = new_client_order_id()
        self.assertLessEqual(len(cid), 32)
        self.assertTrue(cid.isalnum())

    def test_happy_path_fill(self):
        o = Order(
            client_order_id="CE1", symbol="BTC", inst_id="BTC-USDT",
            side="buy", direction="LONG", size=1.0,
        )
        self.assertEqual(o.state, OrderState.CREATED)
        self.assertTrue(o.transition(OrderState.SUBMITTING))
        self.assertTrue(o.transition(OrderState.SUBMITTED))
        self.assertTrue(o.transition(OrderState.FILLED))
        self.assertTrue(o.is_terminal)
        self.assertFalse(o.timeout)

    def test_timeout_is_not_rejected(self):
        o = Order(
            client_order_id="CE2", symbol="ETH", inst_id="ETH-USDT",
            side="sell", direction="SHORT", size=2.0,
        )
        o.transition(OrderState.SUBMITTING)
        o.transition(OrderState.TIMEOUT, "network")
        self.assertEqual(o.state, OrderState.TIMEOUT)
        self.assertTrue(o.timeout)
        self.assertFalse(o.is_terminal)  # TIMEOUT nie terminal – da się reconciliować
        # po timeout można dojść do FILLED
        self.assertTrue(o.transition(OrderState.FILLED, "found on refresh"))
        self.assertTrue(o.is_terminal)

    def test_illegal_transition_goes_unknown(self):
        o = Order(
            client_order_id="CE3", symbol="SOL", inst_id="SOL-USDT",
            side="buy", direction="LONG", size=1.0,
        )
        # CREATED → FILLED niedozwolone
        ok = o.transition(OrderState.FILLED)
        self.assertFalse(ok)
        self.assertEqual(o.state, OrderState.UNKNOWN)


class TestAccounting(unittest.TestCase):
    def test_fees_round_trip(self):
        c = entry_exit_costs(1000)
        self.assertAlmostEqual(float(c["fee_entry"]), 0.6, places=6)
        self.assertAlmostEqual(float(c["total_usd"]), 2.0, places=5)

    def test_funding_sign(self):
        long_cost = float(funding_payment(1000, 0.0001, "LONG", hours_held=8, period_hours=8))
        short_gain = float(funding_payment(1000, 0.0001, "SHORT", hours_held=8, period_hours=8))
        self.assertGreater(long_cost, 0)
        self.assertLess(short_gain, 0)

    def test_realized_net_less_than_gross(self):
        r = realized_pnl(1000, 100, 110, "LONG", leverage=10)
        self.assertEqual(r["gross_usd"], 100.0)
        self.assertLess(r["net_usd"], r["gross_usd"])

    def test_contracts_decimal(self):
        d = notional_to_contracts_dec(50, 60000, 0.001, 0.1)
        self.assertTrue(d["ok"])
        self.assertEqual(float(d["contracts"]), 0.8)

    def test_equity_ledger(self):
        led = EquityLedger(100)
        led.apply_realized(5, fees=1, funding=0.2)
        self.assertEqual(led.snapshot()["cash"], 105.0)
        led.sync_exchange(250, 200)
        self.assertEqual(led.snapshot()["equity"], 250.0)


class TestPortfolioRisk(unittest.TestCase):
    def test_gross_net(self):
        pos = [
            {"symbol": "BTC", "direction": "LONG", "size_usd": 100, "margin": 10, "leverage": 10},
            {"symbol": "ETH", "direction": "SHORT", "size_usd": 40, "margin": 4, "leverage": 10},
        ]
        exp = compute_exposure(pos, 50)
        self.assertEqual(exp["gross_usd"], 140)
        self.assertEqual(exp["net_usd"], 60)

    def test_cluster_eth_l2(self):
        self.assertEqual(cluster_of("ARB"), "ETH_L2")
        self.assertEqual(cluster_of("ETH"), "ETH_L2")

    def test_limits_block_gross(self):
        pos = [{"symbol": "BTC", "direction": "LONG", "size_usd": 400, "margin": 40, "leverage": 10}]
        ok, reason = check_portfolio_limits(
            pos, equity=100,
            new_signal={"symbol": "SOL", "direction": "LONG"},
            new_notional=200,
        )
        self.assertFalse(ok)
        self.assertTrue("GROSS" in reason or "CLUSTER" in reason, reason)


class TestMarketData(unittest.TestCase):
    def test_drop_unclosed(self):
        now = int(time.time() * 1000)
        ohlcv = {
            "closes": [1, 2, 3], "highs": [1, 2, 3], "lows": [1, 2, 3],
            "volumes": [1, 1, 1],
            "timestamps": [now - 7_200_000, now - 3_600_000, now],
        }
        d = drop_unclosed_candle(ohlcv, "1h")
        self.assertEqual(len(d["closes"]), 2)

    def test_aggregate_4h(self):
        now = int(time.time() * 1000)
        base = (now // 14_400_000) * 14_400_000 - 40 * 3_600_000
        ts, c, h, l, v = [], [], [], [], []
        for i in range(40):
            ts.append(base + i * 3_600_000)
            c.append(100 + i); h.append(101 + i); l.append(99 + i); v.append(10)
        agg = aggregate_to_4h({"closes": c, "highs": h, "lows": l, "volumes": v, "timestamps": ts})
        self.assertGreaterEqual(len(agg.get("closes") or []), 5)

    def test_divergence_hard(self):
        d = binance_blofin_divergence({"binance_price": 100, "blofin_price": 105})
        self.assertTrue(d["hard"])

    def test_normalize_symbol(self):
        self.assertEqual(normalize_symbol("btc-usdt"), "BTC")

    def test_stale_monotonic(self):
        STALE.touch("test_key")
        ok, _ = STALE.check_trade_allowed(["test_key"], max_age=60)
        self.assertTrue(ok)
        self.assertLess(STALE.age("test_key"), 1.0)


class TestProtectionLocal(unittest.TestCase):
    def test_local_sl_long(self):
        pm = ProtectionManager()
        with tempfile.TemporaryDirectory() as td:
            pm._state_path = Path(td) / "protection_state.json"
            pm.attach_protection("BTC", "LONG", sl_price=50000, entry_price=52000)
            self.assertEqual(pm.check_local_protection("BTC", "LONG", 49800), "local_emergency_sl")
            self.assertIsNone(pm.check_local_protection("BTC", "LONG", 51000))

    def test_kill_switch(self):
        pm = ProtectionManager()
        with tempfile.TemporaryDirectory() as td:
            pm._state_path = Path(td) / "protection_state.json"
            pm.activate_kill_switch("unit")
            self.assertTrue(pm.is_killed())
            pm.clear_kill_switch()
            self.assertFalse(pm.is_killed())


if __name__ == "__main__":
    unittest.main(verbosity=2)
