# -*- coding: utf-8 -*-
"""Ksztalt sygnalu i budzet dziennej straty - cryptoedge.risk.

Brzmienie powodow jest kontraktem: trafia do reject_log, telemetrii i na
ekran. Testy pilnuja liter, nie tylko faktu odrzucenia.
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cryptoedge.risk.validation import normalized_direction, validate_signal_shape
from cryptoedge.risk.limits import daily_loss_budget_remaining, projected_loss_ok


def sig(**kw):
    base = {"direction": "LONG", "price": 100.0, "strength": 0.7}
    base.update(kw)
    return base


class TestValidateSignalShape(unittest.TestCase):

    def test_good_signal_passes(self):
        self.assertEqual((True, "OK"), validate_signal_shape(sig()))

    def test_direction_is_case_insensitive(self):
        self.assertTrue(validate_signal_shape(sig(direction="long"))[0])
        self.assertTrue(validate_signal_shape(sig(direction="Short"))[0])

    def test_missing_or_odd_direction(self):
        for bad in (None, "", "FLAT", "BUY", 0):
            self.assertEqual((False, "INVALID_DIRECTION"),
                             validate_signal_shape(sig(direction=bad)), bad)

    def test_price_must_be_positive_and_finite(self):
        for bad in (0, -1, "abc", None, float("nan"), float("inf")):
            self.assertEqual((False, "INVALID_PRICE"),
                             validate_signal_shape(sig(price=bad)), bad)

    def test_strength_may_be_zero_but_must_be_a_number(self):
        self.assertTrue(validate_signal_shape(sig(strength=0))[0])
        for bad in ("mocny", None, float("nan")):
            self.assertEqual((False, "INVALID_STRENGTH"),
                             validate_signal_shape(sig(strength=bad)), bad)

    def test_numeric_strings_are_accepted(self):
        self.assertTrue(validate_signal_shape(sig(price="100", strength="0.7"))[0])

    def test_direction_wins_when_everything_is_broken(self):
        # Kolejnosc powodow to kontrakt telemetrii - nie wolno jej przestawic.
        self.assertEqual((False, "INVALID_DIRECTION"),
                         validate_signal_shape({"direction": "X", "price": -1, "strength": None}))

    def test_price_is_checked_before_strength(self):
        self.assertEqual((False, "INVALID_PRICE"),
                         validate_signal_shape(sig(price=0, strength=None)))

    def test_empty_signal_does_not_explode(self):
        self.assertEqual((False, "INVALID_DIRECTION"), validate_signal_shape({}))
        self.assertEqual((False, "INVALID_DIRECTION"), validate_signal_shape(None))

    def test_validation_does_not_mutate(self):
        s = sig()
        before = dict(s)
        validate_signal_shape(s)
        self.assertEqual(before, s)

    def test_normalized_direction(self):
        self.assertEqual("LONG", normalized_direction({"direction": "long"}))
        self.assertEqual("", normalized_direction({}))
        self.assertEqual("", normalized_direction(None))


class TestDailyLossBudget(unittest.TestCase):

    def cfg(self, limit=0.04):
        return SimpleNamespace(DAILY_LOSS_LIMIT=limit)

    def test_fresh_day_has_full_budget(self):
        self.assertAlmostEqual(40.0, daily_loss_budget_remaining(1000.0, 0.0, self.cfg()))

    def test_losses_eat_the_budget(self):
        self.assertAlmostEqual(15.0, daily_loss_budget_remaining(1000.0, -25.0, self.cfg()))

    def test_profit_enlarges_the_budget(self):
        self.assertAlmostEqual(70.0, daily_loss_budget_remaining(1000.0, 30.0, self.cfg()))

    def test_budget_may_go_negative(self):
        # Ujemny budzet niesie informacje o skali przekroczenia; obcinamy
        # dopiero przy porownaniu.
        self.assertAlmostEqual(-60.0, daily_loss_budget_remaining(1000.0, -100.0, self.cfg()))

    def test_projected_loss_within_budget(self):
        self.assertEqual((True, "OK"), projected_loss_ok(1000.0, 0.02, 40.0))

    def test_projected_loss_exceeds_budget(self):
        ok, why = projected_loss_ok(10000.0, 0.02, 40.0)
        self.assertFalse(ok)
        self.assertEqual("DAILY_PROJECTED_LOSS(200.0000>40.0000)", why)

    def test_negative_budget_is_reported_as_zero(self):
        ok, why = projected_loss_ok(1000.0, 0.02, -60.0)
        self.assertFalse(ok)
        self.assertEqual("DAILY_PROJECTED_LOSS(20.0000>0.0000)", why)

    def test_exactly_at_the_limit_passes(self):
        self.assertEqual((True, "OK"), projected_loss_ok(2000.0, 0.02, 40.0))


class TestNoDuplicateImplementations(unittest.TestCase):
    """Kopie regul nie moga wrocic do risk_manager."""

    def setUp(self):
        self.source = (ROOT / "risk_manager.py").read_text(encoding="utf-8")

    def test_reason_strings_live_only_in_the_modules(self):
        for literal in ('"INVALID_DIRECTION"', 'f"INVALID_{field.upper()}"',
                        'f"DAILY_PROJECTED_LOSS('):
            self.assertNotIn(literal, self.source,
                             f"risk_manager odtworzyl wlasna kopie: {literal}")

    def test_manager_delegates(self):
        self.assertIn("from cryptoedge.risk import validation as risk_validation", self.source)
        for call in ("risk_validation.validate_signal_shape",
                     "risk_limits.daily_loss_budget_remaining",
                     "risk_limits.projected_loss_ok"):
            self.assertIn(call, self.source)

    def test_unavailable_fallback_stays_in_the_manager(self):
        # Ten powod dotyczy brakujacego atrybutu na self/config, nie reguly -
        # celowo nie wedruje do modulu.
        self.assertIn('"DAILY_PROJECTED_LOSS_UNAVAILABLE"', self.source)


if __name__ == "__main__":
    unittest.main()
