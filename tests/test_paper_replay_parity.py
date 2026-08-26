import unittest

from accounting import realized_pnl, taker_rate
from paper_trader import Position
from v2_profiles import replay_slip_round_trip, paper_slip_round_trip
from daytrading_validation import run_production_prefix_audit_v2


class TestPaperReplaySlipParity(unittest.TestCase):
    def _sig(self, symbol, engine="daytrading_v2"):
        return {
            "symbol": symbol, "direction": "LONG", "price": 100.0, "strength": 0.75,
            "sl_price": 98.0, "tp1_price": 101.5, "tp2_price": 104.0,
            "engine": engine, "strategy_mode": "DAYTRADING_V2",
        }

    def test_v2_paper_uses_replay_slip_not_config_0008(self):
        btc = Position(self._sig("BTC"), 75.0, leverage=10)
        pepe = Position(self._sig("PEPE"), 50.0, leverage=10)
        self.assertAlmostEqual(btc.slip_rt, replay_slip_round_trip("BTC"), places=6)
        self.assertGreaterEqual(pepe.slip_rt, 0.003)
        self.assertAlmostEqual(btc.slip_rt, paper_slip_round_trip("BTC", self._sig("BTC")), places=6)

    def test_close_alt_costs_more_than_major(self):
        btc = Position(self._sig("BTC"), 75.0, leverage=10)
        pepe = Position(self._sig("PEPE"), 75.0, leverage=10)
        btc.close(100.0, "flat")
        pepe.close(100.0, "flat")
        self.assertLess(pepe.pnl, btc.pnl)

    def test_realized_pnl_slip_frac_matches_replay_formula(self):
        slip = replay_slip_round_trip("BTC")
        r = realized_pnl(100.0, 100.0, 100.0, "LONG", leverage=10, slip_frac=slip)
        fee = float(taker_rate()) * 2
        self.assertAlmostEqual(r["cost_frac"], fee + slip, places=8)
        self.assertAlmostEqual(r["net_usd"], -(fee + slip) * 100.0, places=6)

    def test_non_v2_keeps_legacy_slip(self):
        sig = self._sig("BTC", engine="reversal")
        sig["strategy_mode"] = "REVERSAL"
        pos = Position(sig, 75.0, leverage=10)
        self.assertIsNone(pos.slip_rt)


class TestPrefixAuditV2(unittest.TestCase):
    def _bundle(self, n5=80):
        t0 = 1_700_000_000_000
        step5 = 300_000
        ts5 = [t0 + i * step5 for i in range(n5)]
        px = [100.0 + (i % 7) * 0.01 for i in range(n5)]
        def ohlc(ts, prices):
            return {
                "timestamps": ts, "opens": prices, "highs": [p + 0.2 for p in prices],
                "lows": [p - 0.2 for p in prices], "closes": prices, "volumes": [1000.0] * len(ts),
            }
        ts1 = ts5[::12]
        p1 = px[::12]
        return {
            "5m": ohlc(ts5, px),
            "15m": ohlc(ts5[::3], px[::3]),
            "1h": ohlc(ts1, p1),
            "4h": ohlc(ts5[::48], px[::48] or px[:1]),
            "1d": ohlc(ts5[::288] or ts5[:1], px[::288] or px[:1]),
            "funding": [{"ts_ms": t0, "rate": 0.0001}],
        }

    def test_v2_asof_prefix_matches_full(self):
        bundle = self._bundle()
        n = len(bundle["5m"]["closes"])
        out = run_production_prefix_audit_v2("BTC", bundle, [n // 2, n - 5])
        self.assertGreaterEqual(out["checked_cut_points"], 1)


if __name__ == "__main__":
    unittest.main()
