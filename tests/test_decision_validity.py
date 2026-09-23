"""Stale/late decision protection: czysta funkcja, zegar podaje wolajacy."""
import unittest

from cryptoedge.domain import (
    DecisionStatus, DecisionTiming, DecisionValidityStatus as S, Direction,
    StrategyDecision, assess_validity, valid_until_after_bars,
)

BAR_15M = 900_000


def timing(**kw):
    base = dict(created_ts_ms=1_000_000, valid_until_ms=1_000_000 + BAR_15M,
                market_version="15m:1000000", reference_price=100.0,
                max_price_drift_frac=0.003, invalidation_price=98.0,
                direction=Direction.LONG)
    base.update(kw)
    return DecisionTiming(**base)


class DecisionValidityTests(unittest.TestCase):
    def test_valid_when_all_checks_pass(self):
        v = assess_validity(timing(), now_ms=1_030_000, current_market_version="15m:1000000",
                            current_price=100.1)
        self.assertEqual(v.status, S.VALID)
        self.assertTrue(v.executable)
        self.assertEqual(v.age_ms, 30_000)
        self.assertEqual(v.checks_skipped, ())

    def test_late_after_valid_until(self):
        v = assess_validity(timing(), now_ms=1_000_000 + BAR_15M + 1,
                            current_market_version="15m:1000000", current_price=100.0)
        self.assertEqual(v.status, S.LATE)
        self.assertFalse(v.executable)

    def test_valid_until_boundary_is_inclusive(self):
        v = assess_validity(timing(), now_ms=1_000_000 + BAR_15M,
                            current_market_version="15m:1000000", current_price=100.0)
        self.assertEqual(v.status, S.VALID)

    def test_stale_on_newer_market_version(self):
        v = assess_validity(timing(), now_ms=1_010_000,
                            current_market_version="15m:1900000", current_price=100.0)
        self.assertEqual(v.status, S.STALE)
        self.assertIn("MARKET_VERSION", v.reason)

    def test_stale_on_price_drift(self):
        v = assess_validity(timing(), now_ms=1_010_000,
                            current_market_version="15m:1000000", current_price=100.5)
        self.assertEqual(v.status, S.STALE)
        self.assertAlmostEqual(v.price_drift_frac, 0.005)

    def test_invalidated_beats_everything(self):
        long_ = assess_validity(timing(), now_ms=9_999_999_999, current_price=97.9)
        self.assertEqual(long_.status, S.INVALIDATED)
        short = assess_validity(timing(direction=Direction.SHORT, invalidation_price=102.0),
                                now_ms=1_010_000, current_price=102.0)
        self.assertEqual(short.status, S.INVALIDATED)

    def test_unknown_inputs_skip_checks_instead_of_passing_silently(self):
        v = assess_validity(DecisionTiming(created_ts_ms=1_000), now_ms=5_000)
        self.assertEqual(v.status, S.VALID)
        self.assertEqual(set(v.checks_skipped),
                         {"invalidation", "valid_until", "market_version", "price_drift"})

    def test_clock_before_decision_is_not_fresh(self):
        v = assess_validity(timing(), now_ms=999_999)
        self.assertEqual(v.status, S.STALE)
        self.assertEqual(v.reason, "CLOCK_BEFORE_DECISION")

    def test_timing_rejects_inconsistent_window(self):
        with self.assertRaises(ValueError):
            DecisionTiming(created_ts_ms=10, valid_until_ms=5)
        with self.assertRaises(ValueError):
            DecisionTiming(created_ts_ms=10, max_price_drift_frac=-0.1)

    def test_valid_until_after_bars(self):
        self.assertEqual(valid_until_after_bars(1_000, BAR_15M, 1), 1_000 + BAR_15M)
        with self.assertRaises(ValueError):
            valid_until_after_bars(1_000, 0)

    def test_strategy_decision_exposes_timing_for_router(self):
        d = StrategyDecision("BTC", DecisionStatus.CANDIDATE, 1_000_000, Direction.LONG, 100.0,
                             valid_until_ms=1_000_000 + BAR_15M, market_version="15m:1000000")
        t = d.timing(max_price_drift_frac=0.003, invalidation_price=98.0)
        self.assertEqual(t.reference_price, 100.0)
        self.assertEqual(t.direction, Direction.LONG)
        v = assess_validity(t, now_ms=1_000_000 + BAR_15M + 5_000, current_price=100.0)
        self.assertEqual(v.status, S.LATE)
        self.assertEqual(v.to_legacy()["validity_status"], "LATE")


if __name__ == "__main__":
    unittest.main()
