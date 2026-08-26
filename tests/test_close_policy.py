"""Reguly zamykania maja jednego wlasciciela.

26.08.2026: ta sama decyzja zyla w trzech kopiach - stop_engine(),
kill_switch() i _close_all_unlocked(). Guard na nieswieze ceny trafil tylko
do jednej z nich, bo nie mialo jak sie zsynchronizowac. Te testy pilnuja
zarowno samej reguly, jak i tego, ze kopie nie wrocily.
"""
import unittest
from pathlib import Path
from types import SimpleNamespace

from cryptoedge.portfolio import (
    ClosePrice, age_of, format_age, max_price_age_s, may_realize_profit,
    prices_are_stale, resolve_close_price,
)

ROOT = Path(__file__).resolve().parents[1]
POS = SimpleNamespace(symbol="BTC", entry_price=100.0)


class TestAge(unittest.TestCase):
    def test_never_written_is_infinite(self):
        self.assertEqual(float("inf"), age_of(0))
        self.assertEqual(float("inf"), age_of(None))

    def test_age_is_seconds_since_timestamp(self):
        self.assertAlmostEqual(30.0, age_of(1000.0, now=1030.0), places=3)

    def test_age_never_negative(self):
        self.assertEqual(0.0, age_of(2000.0, now=1000.0))

    def test_format_never_overflows_on_infinity(self):
        self.assertEqual("NIGDY", format_age(float("inf")))
        self.assertEqual("43788s", format_age(43788.0))
        self.assertEqual("NIEZNANY", format_age("nie-liczba"))


class TestStaleness(unittest.TestCase):
    def test_unknown_age_keeps_old_behaviour(self):
        """None = wolajacy nie podal wieku; nie zgadujemy, ze jest zle."""
        self.assertFalse(prices_are_stale(None))
        self.assertTrue(may_realize_profit(None))

    def test_fresh_prices_allow_realizing_profit(self):
        self.assertFalse(prices_are_stale(5.0))
        self.assertTrue(may_realize_profit(5.0))

    def test_real_incident_age_is_stale(self):
        self.assertTrue(prices_are_stale(43788.0))
        self.assertFalse(may_realize_profit(43788.0))

    def test_threshold_comes_from_config(self):
        cfg = SimpleNamespace(STOP_ENGINE_MAX_PRICE_AGE_S=600.0)
        self.assertEqual(600.0, max_price_age_s(cfg))
        self.assertFalse(prices_are_stale(300.0, cfg))
        self.assertTrue(prices_are_stale(900.0, cfg))

    def test_broken_config_falls_back_to_default(self):
        self.assertEqual(60.0, max_price_age_s(SimpleNamespace(STOP_ENGINE_MAX_PRICE_AGE_S="x")))
        self.assertEqual(60.0, max_price_age_s(SimpleNamespace(STOP_ENGINE_MAX_PRICE_AGE_S=0)))


class TestResolveClosePrice(unittest.TestCase):
    def test_fresh_price_is_used_as_is(self):
        out = resolve_close_price(POS, {"BTC": 101.0}, 5.0, "close_all")
        self.assertEqual(ClosePrice(101.0, "close_all", "map", False, 5.0), out)
        self.assertFalse(out.is_fallback)

    def test_stale_price_is_used_but_tagged(self):
        out = resolve_close_price(POS, {"BTC": 101.0}, 43788.0, "kill_switch")
        self.assertEqual(101.0, out.price)
        self.assertEqual("kill_switch:STALE_PRICE_43788s", out.reason)
        self.assertTrue(out.stale)

    def test_missing_price_falls_back_to_entry_and_is_tagged(self):
        out = resolve_close_price(POS, {}, 5.0, "kill_switch")
        self.assertEqual(100.0, out.price)
        self.assertEqual("kill_switch:NO_PRICE", out.reason)
        self.assertTrue(out.is_fallback)

    def test_zero_price_is_not_a_valid_close(self):
        out = resolve_close_price(POS, {"BTC": 0.0}, 5.0, "close_all")
        self.assertEqual(100.0, out.price)
        self.assertIn("NO_PRICE", out.reason)

    def test_garbage_price_is_not_a_valid_close(self):
        out = resolve_close_price(POS, {"BTC": "brak"}, 5.0, "close_all")
        self.assertEqual(100.0, out.price)
        self.assertIn("NO_PRICE", out.reason)

    def test_missing_price_wins_over_stale_tag(self):
        """Brak ceny jest wazniejszy: cena nie pochodzi z mapy w ogole."""
        out = resolve_close_price(POS, {}, 43788.0, "kill_switch")
        self.assertEqual("kill_switch:NO_PRICE", out.reason)

    def test_never_written_map_does_not_crash_formatting(self):
        out = resolve_close_price(POS, {"BTC": 101.0}, float("inf"), "kill_switch")
        self.assertEqual("kill_switch:STALE_PRICE_NIGDY", out.reason)


class TestNoDuplicateImplementations(unittest.TestCase):
    """Kopie reguly nie moga wrocic do wolajacych."""

    def _src(self, name):
        return (ROOT / name).read_text(encoding="utf-8")

    def test_callers_do_not_read_the_threshold_themselves(self):
        for name in ("runtime.py", "paper_trader.py"):
            self.assertNotIn(
                'getattr(config, "STOP_ENGINE_MAX_PRICE_AGE_S"', self._src(name),
                f"{name} czyta prog samodzielnie zamiast przez close_policy",
            )
            self.assertNotIn(
                'getattr(_cfg, "STOP_ENGINE_MAX_PRICE_AGE_S"', self._src(name),
                f"{name} czyta prog samodzielnie zamiast przez close_policy",
            )

    def test_callers_do_not_compute_age_themselves(self):
        source = self._src("runtime.py")
        self.assertNotIn("time.time() - float(self.last_price_map_ts", source)

    def test_callers_go_through_close_policy(self):
        for name in ("runtime.py", "paper_trader.py"):
            self.assertIn("cryptoedge.portfolio", self._src(name))


if __name__ == "__main__":
    unittest.main()
