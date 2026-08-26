import unittest

from daytrading_validation import rolling_purged_folds
from walk_forward_v2 import fold_metrics, run_walk_forward_v2, MS_HOUR, MS_15M


class T:
    def __init__(self, r, direction="LONG"):
        self.direction = direction
        self.realised_r = r
        self.exit_reason = "tp"
        self.tp1_done = True
        self.tp2_done = False
        self.entry_i = 1
        self.exit_i = 3


class TestRollingPurgedFolds(unittest.TestCase):
    def test_tests_do_not_overlap_and_purge_gap_holds(self):
        splits = rolling_purged_folds(2000, train=504, test=168, purge=48, embargo=48)
        self.assertGreaterEqual(len(splits), 5)
        for train, test in splits:
            self.assertEqual(test.start - train.stop, 48)
            self.assertEqual(len(train), 504)
            self.assertEqual(len(test), 168)
        for (_, a), (_, b) in zip(splits, splits[1:]):
            self.assertGreaterEqual(b.start - a.stop, 48)

    def test_too_short_history_is_empty(self):
        self.assertEqual([], rolling_purged_folds(100, 504, 168, 48, 48))


class TestFoldMetrics(unittest.TestCase):
    def test_negative_flag_and_drawdown(self):
        m = fold_metrics([1.0, -2.0, 0.5])
        self.assertTrue(m["negative"])
        self.assertEqual(3, m["n"])
        self.assertAlmostEqual(m["max_dd_r"], 2.0)
        self.assertAlmostEqual(m["profit_factor"], 1.5 / 2.0)


class TestRunWalkForwardV2(unittest.TestCase):
    def _bundle(self, n_1h=2160):
        t0 = 1_700_000_000_000
        t0 -= t0 % MS_15M
        ts1 = [t0 + i * MS_HOUR for i in range(n_1h)]
        n5 = n_1h * 12
        ts5 = [t0 + i * 300_000 for i in range(n5)]
        px = [100.0] * n5
        h = [100.0] * n_1h
        return {
            "5m": {"timestamps": ts5, "opens": px, "highs": px, "lows": px, "closes": px},
            "1h": {"timestamps": ts1, "opens": h, "highs": h, "lows": h, "closes": h},
            "15m": {"timestamps": ts5[::3], "opens": [100.0] * (n5 // 3),
                    "highs": [100.0] * (n5 // 3), "lows": [100.0] * (n5 // 3),
                    "closes": [100.0] * (n5 // 3)},
        }

    def test_oos_folds_on_frozen_fake_replay(self):
        seen = []

        def provider(symbol, bundle):
            def signal_at(i):
                return {"direction": "LONG", "reject_reason": None, "symbol": symbol}
            return signal_at, type("E", (), {"notify_exit": lambda *a, **k: None})()

        def replay(ohlcv, gated, **kw):
            ts = ohlcv.get("timestamps") or []
            for i, t in enumerate(ts):
                sig = gated(i)
                if sig and sig.get("direction") == "LONG":
                    seen.append(t)
                    return {"trades": [T(0.4)]}
            return {"trades": []}

        def none_bias(symbol, bundle):
            return lambda i: None

        def none_trail(symbol, bundle):
            return lambda i, d: None

        report = run_walk_forward_v2(
            {"BTC": self._bundle()},
            replay_fn=replay,
            provider_fn=provider,
            bias_fn=none_bias,
            trail_fn=none_trail,
        )
        self.assertGreaterEqual(len(report.folds), 5)
        self.assertTrue(report.config["frozen_config"])
        self.assertGreater(report.oos_metrics["n"], 0)
        self.assertEqual(0, report.oos_metrics["folds_negative"])
        for a, b in zip(seen, seen[1:]):
            self.assertGreater(b, a)

    def test_counts_negative_folds(self):
        n_calls = {"i": 0}

        def provider(symbol, bundle):
            return (
                lambda i: {"direction": "LONG", "reject_reason": None, "symbol": symbol},
                type("E", (), {"notify_exit": lambda *a, **k: None})(),
            )

        def replay(ohlcv, gated, **kw):
            for i in range(len(ohlcv.get("timestamps") or [])):
                if gated(i):
                    n_calls["i"] += 1
                    r = -0.8 if n_calls["i"] % 2 == 0 else 0.5
                    return {"trades": [T(r)]}
            return {"trades": []}

        report = run_walk_forward_v2(
            {"BTC": self._bundle()},
            replay_fn=replay,
            provider_fn=provider,
            bias_fn=lambda s, b: (lambda i: None),
            trail_fn=lambda s, b: (lambda i, d: None),
        )
        self.assertGreaterEqual(report.oos_metrics["folds_negative"], 1)
        self.assertGreaterEqual(report.oos_metrics["folds"], 5)

    def test_by_profile_splits_major_and_alt(self):
        def provider(symbol, bundle):
            return (
                lambda i: {"direction": "LONG", "reject_reason": None, "symbol": symbol},
                type("E", (), {"notify_exit": lambda *a, **k: None})(),
            )

        def replay(ohlcv, gated, **kw):
            for i in range(len(ohlcv.get("timestamps") or [])):
                if gated(i):
                    return {"trades": [T(0.4)]}
            return {"trades": []}

        report = run_walk_forward_v2(
            {"BTC": self._bundle(), "PEPE": self._bundle()},
            replay_fn=replay,
            provider_fn=provider,
            bias_fn=lambda s, b: (lambda i: None),
            trail_fn=lambda s, b: (lambda i, d: None),
        )
        bp = report.oos_metrics.get("by_profile") or {}
        self.assertIn("major", bp)
        self.assertIn("alt", bp)
        self.assertGreater(bp["major"]["n"], 0)
        self.assertGreater(bp["alt"]["n"], 0)


if __name__ == "__main__":
    unittest.main()
