import unittest
from unittest.mock import patch

from daytrading_engine_v2 import DayTradingEngineV2
from daytrading_backtester import (
    AsOfBlofinFeed, apply_observed_funding, replay_daytrading_v2, ReplayTradeV2,
)
from v2_profiles import replay_slip_round_trip
from tests.test_daytrading_engine_v2 import (
    FakeFeeder, indicator, _up_swing_1h, _15m_trigger_series, _flat_ohlcv,
)
from why_taxonomy import why_bucket


class TestV2FundingSkip(unittest.TestCase):
    def setUp(self):
        self.feeder = FakeFeeder()
        self.feeder.blofin.funding = {}
        self.engine = DayTradingEngineV2(self.feeder)

    def _long_ready(self, symbol="BTC"):
        swing = _up_swing_1h()
        m15 = _15m_trigger_series(110.0, "LONG")
        b = self.feeder.blofin
        b.frames[(symbol, "1D")] = _flat_ohlcv(260)
        b.frames[(symbol, "4H")] = _flat_ohlcv(260)
        b.frames[(symbol, "1H")] = swing
        b.frames[(symbol, "15m")] = m15

    def test_long_pays_extreme_is_rejected(self):
        self._long_ready()
        self.feeder.blofin.funding["BTC"] = {"funding_rate": 0.002}

        def ind(ohlcv, tf="1h"):
            return indicator("up", atr=1.0)

        with patch("daytrading_engine_v2.compute_indicators", side_effect=ind):
            result = self.engine.evaluate({"symbol": "BTC", "price": 112.0}, now_ts=10_000_000.0)
        self.assertEqual("V2_FUNDING_EXTREME", result["reject_reason"])
        self.assertEqual("risk", why_bucket(result["reject_reason"]))

    def test_short_receives_positive_funding(self):
        from tests.test_daytrading_engine_v2 import _down_swing_1h
        swing = _down_swing_1h()
        m15 = _15m_trigger_series(110.0, "SHORT")
        b = self.feeder.blofin
        b.frames[("BTC", "1D")] = _flat_ohlcv(260)
        b.frames[("BTC", "4H")] = _flat_ohlcv(260)
        b.frames[("BTC", "1H")] = swing
        b.frames[("BTC", "15m")] = m15
        b.funding["BTC"] = {"funding_rate": 0.002}

        def ind(ohlcv, tf="1h"):
            return indicator("down", atr=1.0)

        with patch("daytrading_engine_v2.compute_indicators", side_effect=ind):
            result = self.engine.evaluate({"symbol": "BTC", "price": 108.0}, now_ts=10_000_000.0)
        self.assertIsNone(result.get("reject_reason"), result)
        self.assertEqual("SHORT", result["direction"])
        self.assertEqual(0.002, result["funding"]["funding_rate"])

    def test_long_attaches_mild_funding(self):
        self._long_ready()
        self.feeder.blofin.funding["BTC"] = {"funding_rate": 0.0001}

        def ind(ohlcv, tf="1h"):
            return indicator("up", atr=1.0)

        with patch("daytrading_engine_v2.compute_indicators", side_effect=ind):
            result = self.engine.evaluate({"symbol": "BTC", "price": 112.0}, now_ts=10_000_000.0)
        self.assertIsNone(result.get("reject_reason"), result)
        self.assertEqual(0.0001, result["funding"]["funding_rate"])


class TestObservedFundingReplay(unittest.TestCase):
    def test_long_pays_positive_settlement(self):
        trade = ReplayTradeV2("LONG", 1, 100.0, 98.0, 101.5, 104.0, 0.5, 2.0)
        trade.exit_i = 10
        trade.realised_r = 1.0
        ts = [1_700_000_000_000 + i * 300_000 for i in range(12)]
        funding = [{"ts_ms": ts[5], "rate": 0.001}]
        r = apply_observed_funding(trade, ts, funding)
        self.assertAlmostEqual(r, -0.05, places=6)
        self.assertAlmostEqual(trade.realised_r, 0.95, places=6)

    def test_short_receives_positive_settlement(self):
        trade = ReplayTradeV2("SHORT", 1, 100.0, 102.0, 98.5, 96.0, 0.5, 2.0)
        trade.exit_i = 10
        trade.realised_r = 0.5
        ts = [1_700_000_000_000 + i * 300_000 for i in range(12)]
        apply_observed_funding(trade, ts, [{"ts_ms": ts[5], "rate": 0.001}])
        self.assertAlmostEqual(trade.funding_r, 0.05, places=6)

    def test_replay_books_funding_on_closed_trade(self):
        n = 12
        bars = {
            "opens": [100.0] * n, "highs": [100.1] * n, "lows": [99.9] * n,
            "closes": [100.0] * n,
            "timestamps": [1_700_000_000_000 + i * 300_000 for i in range(n)],
        }
        bars["lows"][3] = 97.0
        fired = {"done": False}

        def signal_at(i):
            if i == 1 and not fired["done"]:
                fired["done"] = True
                return {"symbol": "BTC", "direction": "LONG", "price": 100.0,
                        "sl_price": 98.0, "tp1_price": 101.5, "tp2_price": 104.0}
            return {"direction": "NEUTRAL", "reject_reason": "TEST"}

        # Sygnał na barze 1, fill na open baru 2, SL na barze 3. Funding
        # musi wypaść po fillu, inaczej poprawnie nie należy do pozycji.
        funding = [{"ts_ms": bars["timestamps"][2] + 1, "rate": 0.001}]
        with patch("config.DAYTRADING_V2_ENTRY_SL", True):
            result = replay_daytrading_v2(bars, signal_at, funding=funding)
        t = result["trades"][0]
        self.assertEqual("sl", t.exit_reason)
        self.assertAlmostEqual(t.funding_r, -0.05, places=5)

    def test_asof_funding_is_causal(self):
        t0 = 1_700_000_000_000
        bundle = {
            "5m": {"timestamps": [t0, t0 + 300_000], "closes": [1, 1]},
            "funding": [
                {"ts_ms": t0 - 8 * 3600_000, "rate": 0.0002},
                {"ts_ms": t0 + 8 * 3600_000, "rate": 0.009},
            ],
        }
        feed = AsOfBlofinFeed(bundle)
        feed.asof_ts = t0
        fr = feed.fetch_funding_rate("BTC")
        self.assertAlmostEqual(fr["funding_rate"], 0.0002)


class TestAltReplaySlip(unittest.TestCase):
    def test_alt_floor_is_30bps_rt_major_is_6(self):
        self.assertAlmostEqual(replay_slip_round_trip("BTC"), 0.0006, places=5)
        self.assertGreaterEqual(replay_slip_round_trip("PEPE"), 0.003)
        self.assertAlmostEqual(replay_slip_round_trip("XAU"), 0.001, places=4)

    def test_thin_alt_bar_adds_impact(self):
        ohlcv = {"volumes": [1.0], "closes": [0.001]}
        fat = replay_slip_round_trip("PEPE", ohlcv, 0, 0.001)
        self.assertGreater(fat, 0.003)

    def test_alt_sl_costs_more_r_than_major(self):
        def bars(n=8):
            return {
                "opens": [100.0] * n, "highs": [100.1] * n, "lows": [99.9] * n,
                "closes": [100.0] * n,
                "timestamps": [1_700_000_000_000 + i * 300_000 for i in range(n)],
            }

        def sig(sym):
            fired = {"d": False}
            def signal_at(i):
                if i == 1 and not fired["d"]:
                    fired["d"] = True
                    return {"symbol": sym, "direction": "LONG", "price": 100.0,
                            "sl_price": 98.0, "tp1_price": 101.5, "tp2_price": 104.0}
                return {"direction": "NEUTRAL", "reject_reason": "TEST"}
            return signal_at

        btc_b, alt_b = bars(), bars()
        btc_b["lows"][3] = 97.0
        alt_b["lows"][3] = 97.0
        with patch("config.DAYTRADING_V2_ENTRY_SL", True):
            btc = replay_daytrading_v2(btc_b, sig("BTC"))["trades"][0]
            alt = replay_daytrading_v2(alt_b, sig("PEPE"))["trades"][0]
        self.assertLess(alt.realised_r, btc.realised_r)
        self.assertGreater(alt.slip_rt, btc.slip_rt)


if __name__ == "__main__":
    unittest.main()
