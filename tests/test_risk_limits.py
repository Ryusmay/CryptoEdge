"""Limity ksztaltu portfela jako osobny modul.

Pierwsza rodzina regul realnie przeniesiona z risk_manager.py do
cryptoedge/risk/. Powody musza byc bajt w bajt takie same jak wczesniej -
trafiaja do reject_log, telemetrii i na ekran, wiec zmiana brzmienia jest
zmiana zachowania, nie kosmetyka.
"""
import unittest
from pathlib import Path
from types import SimpleNamespace

from cryptoedge.risk import (
    capital_sufficient, heat_limit_ok, max_positions_for_regime,
    max_same_direction, slot_available,
)

ROOT = Path(__file__).resolve().parents[1]
CFG = SimpleNamespace(MAX_POSITIONS=10, REGIME_RANGE_MAX_POSITIONS=5,
                      REGIME_PANIC_MAX_POSITIONS=8, MAX_SAME_DIRECTION_PCT=0.65)


class TestMaxPositionsForRegime(unittest.TestCase):
    def test_normal_regime_uses_full_budget(self):
        for regime in ("TREND_UP", "TREND_DOWN", "UNKNOWN", ""):
            self.assertEqual(10, max_positions_for_regime(regime, CFG), regime)

    def test_range_tightens_the_ceiling(self):
        self.assertEqual(5, max_positions_for_regime("RANGE", CFG))

    def test_panic_tightens_but_does_not_halt(self):
        """PANIC to silny ruch jednokierunkowy, nie zatrzymanie bota."""
        self.assertEqual(8, max_positions_for_regime("PANIC", CFG))

    def test_regime_is_case_insensitive(self):
        self.assertEqual(5, max_positions_for_regime("range", CFG))

    def test_ceiling_never_exceeds_max_positions(self):
        loose = SimpleNamespace(MAX_POSITIONS=3, REGIME_RANGE_MAX_POSITIONS=99,
                                REGIME_PANIC_MAX_POSITIONS=99)
        self.assertEqual(3, max_positions_for_regime("RANGE", loose))
        self.assertEqual(3, max_positions_for_regime("PANIC", loose))


class TestSlotAvailable(unittest.TestCase):
    def test_free_slot_passes(self):
        self.assertEqual((True, "OK"), slot_available(3, 10, "TREND_UP"))

    def test_full_book_is_rejected(self):
        ok, why = slot_available(10, 10, "TREND_UP")
        self.assertFalse(ok)
        self.assertEqual("Max pozycji (10)", why)

    def test_range_reason_says_range(self):
        """Brzmienie z nawiasem jest dziwne, ale jest kontraktem - nie poprawiac."""
        ok, why = slot_available(5, 5, "RANGE")
        self.assertFalse(ok)
        self.assertEqual("Max pozycji (5 RANGE)", why)

    def test_over_limit_is_rejected(self):
        self.assertFalse(slot_available(11, 10, "TREND_UP")[0])


class TestCapitalSufficient(unittest.TestCase):
    def test_enough_capital(self):
        self.assertEqual((True, "OK"), capital_sufficient(1000.0))

    def test_exactly_one_is_enough(self):
        self.assertTrue(capital_sufficient(1.0)[0])

    def test_below_one_is_rejected(self):
        ok, why = capital_sufficient(0.99)
        self.assertFalse(ok)
        self.assertEqual("Kapital zbyt niski", why)

    def test_none_and_garbage_are_rejected_not_crashing(self):
        self.assertFalse(capital_sufficient(None)[0])
        self.assertFalse(capital_sufficient("duzo")[0])


class TestHeatLimit(unittest.TestCase):
    def test_limit_is_share_of_max_positions(self):
        self.assertEqual(6, max_same_direction(CFG))

    def test_empty_book_passes(self):
        self.assertEqual((True, "OK"), heat_limit_ok("LONG", [], CFG))

    def test_opposite_direction_does_not_count(self):
        self.assertTrue(heat_limit_ok("LONG", ["SHORT"] * 9, CFG)[0])

    def test_same_direction_at_limit_is_rejected(self):
        ok, why = heat_limit_ok("LONG", ["LONG"] * 6, CFG)
        self.assertFalse(ok)
        self.assertEqual("HEAT_LONG(6>=6)", why)

    def test_short_side_has_its_own_reason(self):
        ok, why = heat_limit_ok("SHORT", ["SHORT"] * 7, CFG)
        self.assertFalse(ok)
        self.assertEqual("HEAT_SHORT(7>=6)", why)

    def test_limit_is_never_zero(self):
        strict = SimpleNamespace(MAX_POSITIONS=1, MAX_SAME_DIRECTION_PCT=0.1)
        self.assertEqual(1, max_same_direction(strict))

    def test_broken_share_falls_back_to_default(self):
        broken = SimpleNamespace(MAX_POSITIONS=10, MAX_SAME_DIRECTION_PCT="duzo")
        self.assertEqual(6, max_same_direction(broken))


class TestNoDuplicateImplementations(unittest.TestCase):
    """Kopie regul nie moga wrocic do risk_manager."""

    def setUp(self):
        self.source = (ROOT / "risk_manager.py").read_text(encoding="utf-8")

    def test_reason_strings_live_only_in_the_module(self):
        for literal in ('"Kapital zbyt niski"', 'f"Max pozycji (', 'f"HEAT_{direction}('):
            self.assertNotIn(literal, self.source,
                             f"risk_manager odtworzyl wlasna kopie: {literal}")

    def test_manager_delegates_to_limits(self):
        self.assertIn("from cryptoedge.risk import limits as risk_limits", self.source)
        for call in ("risk_limits.max_positions_for_regime", "risk_limits.slot_available",
                     "risk_limits.capital_sufficient", "risk_limits.heat_limit_ok"):
            self.assertIn(call, self.source)


if __name__ == "__main__":
    unittest.main()
