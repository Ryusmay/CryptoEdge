# -*- coding: utf-8 -*-
"""Czysty reduktor wyjsc sterowanych cena - cryptoedge.portfolio.exit_rules."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cryptoedge.portfolio.exit_rules import PriceExitView, decide_price_exit


def cfg(**kw):
    base = {"NO_HARD_TP": True, "PARTIAL_TP_ENABLED": True,
            "PARTIAL_TP_TRIGGER_PCT": 50.0, "MARGIN_CALL_ENABLED": True,
            "MARGIN_CALL_THRESHOLD": 0.80, "DAYTRADING_V2_ENTRY_SL": False}
    base.update(kw)
    return SimpleNamespace(**base)


def view(**kw):
    base = {"direction": "LONG", "engine": "trend", "sl_price": 96.0,
            "tp_price": 112.0, "tp1_price": None, "tp2_price": None,
            "margin": 100.0, "margin_call_price": 92.0, "ride_trend": False}
    base.update(kw)
    return PriceExitView(**base)


class TestOrdering(unittest.TestCase):
    """Kolejnosc jest kontraktem: partial > margin call > trailing > SL > TP."""

    def test_partial_beats_margin_call(self):
        v = view(tp1_price=104.0, pnl=-200.0)
        self.assertEqual("partial_tp", decide_price_exit(v, 105.0, cfg()).action)

    def test_margin_call_beats_stop_loss(self):
        got = decide_price_exit(view(), 91.0, cfg())
        self.assertEqual("margin_call", got.action)

    def test_trailing_beats_stop_loss(self):
        v = view(trailing_active=True, trailing_stop_price=105.0,
                 margin_call_price=50.0)
        self.assertEqual("trailing_stop", decide_price_exit(v, 95.0, cfg()).action)

    def test_stop_loss_beats_take_profit(self):
        """Nie da sie trafic obu naraz, ale kolejnosc pilnujemy jawnie."""
        v = view(sl_price=96.0, tp_price=112.0)
        self.assertEqual("stop_loss", decide_price_exit(v, 95.0, cfg()).action)


class TestStopLoss(unittest.TestCase):

    def test_long_hit_and_exact_touch(self):
        self.assertEqual("stop_loss", decide_price_exit(view(), 95.0, cfg()).action)
        self.assertEqual("stop_loss", decide_price_exit(view(), 96.0, cfg()).action)

    def test_long_one_tick_inside_holds(self):
        self.assertIsNone(decide_price_exit(view(), 96.01, cfg()).action)

    def test_short(self):
        v = view(direction="SHORT", sl_price=104.0, tp_price=88.0,
                 margin_call_price=108.0)
        self.assertEqual("stop_loss", decide_price_exit(v, 105.0, cfg()).action)
        self.assertIsNone(decide_price_exit(v, 103.9, cfg()).action)

    def test_v2_stop_is_disarmed_before_first_partial(self):
        v = view(engine="daytrading_v2", margin_call_price=50.0)
        self.assertIsNone(decide_price_exit(v, 95.0, cfg()).action)

    def test_v2_stop_arms_after_partial_or_trailing_or_be(self):
        for flag in ("partial_taken", "trailing_active", "breakeven_active"):
            v = view(engine="daytrading_v2", margin_call_price=50.0, **{flag: True})
            self.assertEqual("stop_loss", decide_price_exit(v, 95.0, cfg()).action,
                             flag)

    def test_v2_stop_armed_by_config(self):
        v = view(engine="daytrading_v2", margin_call_price=50.0)
        got = decide_price_exit(v, 95.0, cfg(DAYTRADING_V2_ENTRY_SL=True))
        self.assertEqual("stop_loss", got.action)


class TestPartials(unittest.TestCase):

    def test_tp1_by_price(self):
        got = decide_price_exit(view(tp1_price=104.0), 104.5, cfg())
        self.assertEqual(("partial_tp", 1), (got.action, got.partial_stage))

    def test_tp1_by_legacy_pnl_when_no_plan(self):
        got = decide_price_exit(view(pnl_pct=60.0), 101.0, cfg())
        self.assertEqual(("partial_tp", 1), (got.action, got.partial_stage))

    def test_legacy_pnl_trigger_ignored_when_plan_exists(self):
        v = view(pnl_pct=60.0, tp_plan={"stages": 2})
        self.assertIsNone(decide_price_exit(v, 101.0, cfg()).action)

    def test_tp2_needs_a_plan(self):
        done = {"partial_tp1_done": True, "tp2_price": 108.0}
        self.assertIsNone(decide_price_exit(view(**done), 109.0, cfg()).action)
        got = decide_price_exit(view(tp_plan={"x": 1}, **done), 109.0, cfg())
        self.assertEqual(("partial_tp", 2), (got.action, got.partial_stage))

    def test_partials_disabled(self):
        v = view(tp1_price=104.0)
        self.assertIsNone(decide_price_exit(v, 104.5,
                                            cfg(PARTIAL_TP_ENABLED=False)).action)

    def test_stage_is_returned_not_written(self):
        """Sedno wydzielenia: etap partiala jest wartoscia, nie efektem
        ubocznym w srodku zapytania."""
        v = view(tp1_price=104.0)
        self.assertEqual(1, decide_price_exit(v, 104.5, cfg()).partial_stage)
        self.assertEqual(0, decide_price_exit(v, 100.0, cfg()).partial_stage)


class TestMarginCallAndTakeProfit(unittest.TestCase):

    def test_margin_call_by_pnl_eating_the_deposit(self):
        v = view(pnl=-85.0, margin=100.0, margin_call_price=1.0)
        self.assertEqual("margin_call", decide_price_exit(v, 99.0, cfg()).action)

    def test_margin_call_disabled(self):
        got = decide_price_exit(view(), 91.0, cfg(MARGIN_CALL_ENABLED=False))
        self.assertEqual("stop_loss", got.action)

    def test_take_profit_needs_hard_tp_enabled(self):
        """Domyslnie NO_HARD_TP=True, wiec twardy TP jest WYLACZONY. Zeby go
        zobaczyc, trzeba go wlaczyc jawnie - inaczej przypadek "take profit"
        nie testuje take profita."""
        self.assertIsNone(decide_price_exit(view(), 113.0, cfg()).action)
        self.assertEqual("take_profit",
                         decide_price_exit(view(), 113.0, cfg(NO_HARD_TP=False)).action)

    def test_ride_trend_disables_take_profit(self):
        v = view(ride_trend=True)
        self.assertIsNone(decide_price_exit(v, 113.0, cfg()).action)

    def test_no_hard_tp_config_disables_it(self):
        v = view(ride_trend=False)
        self.assertIsNone(decide_price_exit(v, 113.0, cfg(NO_HARD_TP=True)).action)
        self.assertEqual("take_profit",
                         decide_price_exit(v, 113.0, cfg(NO_HARD_TP=False)).action)

    def test_daytrading_always_has_hard_tp(self):
        """daytrading nadpisuje OBA wylaczniki - i config, i ride_trend."""
        v = view(engine="daytrading", ride_trend=True)
        self.assertEqual("take_profit", decide_price_exit(v, 113.0, cfg()).action)
        self.assertEqual("take_profit",
                         decide_price_exit(v, 113.0, cfg(NO_HARD_TP=True)).action)

    def test_hard_tp_after_stop_reenables_it(self):
        v = view(ride_trend=True, hard_tp_after_stop=True)
        self.assertEqual("take_profit", decide_price_exit(v, 113.0, cfg()).action)


class TestDegenerateInputs(unittest.TestCase):

    def test_bad_prices_decide_nothing(self):
        for price in (None, 0.0, -5.0, float("nan"), float("inf"), "tanio"):
            self.assertIsNone(decide_price_exit(view(), price, cfg()).action, price)

    def test_missing_levels_are_not_fatal(self):
        v = view(sl_price=None, tp_price=None, margin_call_price=None)
        self.assertIsNone(decide_price_exit(v, 50.0, cfg()).action)


class TestNoDuplicateImplementations(unittest.TestCase):

    def test_manager_delegates(self):
        source = (ROOT / "paper_trader.py").read_text(encoding="utf-8")
        self.assertIn("decide_price_exit", source)
        for literal in ('return "margin_call"', 'return "trailing_stop"',
                        'return "partial_tp"'):
            self.assertNotIn(literal, source,
                             f"paper_trader odtworzyl wlasna kopie: {literal}")


if __name__ == "__main__":
    unittest.main()
