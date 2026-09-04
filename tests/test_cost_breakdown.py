# -*- coding: utf-8 -*-
"""Rozklad kosztu ma sie zgadzac do zera i nie gubic nowych skladnikow.

tools/cost_breakdown.py odpowiada na pytanie "setup czy koszt?". Zeby ta
odpowiedz cos znaczyla, sam rozklad musi byc szczelny: suma skladnikow plus
reszta musi sie rownac roznicy brutto-netto, a koszt, ktorego nikt nie dopisal
do COST_FIELDS, ma wyjsc w reszcie, a nie zniknac.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import cost_breakdown as cb  # noqa: E402


def _br(gross, net, **costs):
    out = {"gross_r": gross, "net_r": net, "sl_dist": 0.019}
    out.update(costs)
    return out


class TestBreakdownRowIsAirtight(unittest.TestCase):

    def test_named_components_plus_residual_equal_the_gap(self):
        row = cb.breakdown_row("BTC", _br(
            0.05, -0.10, fee_r=0.06, spread_r=0.02, slip_r=0.07,
            impact_r=0.0, funding_r=0.0))
        self.assertAlmostEqual(row["total_cost_r"], 0.15, places=4)
        named = sum(row[f] for f in cb.COST_FIELDS)
        self.assertAlmostEqual(named + row["other_r"], row["total_cost_r"], places=4)
        self.assertAlmostEqual(row["other_r"], 0.0, places=4)

    def test_an_unnamed_cost_shows_up_in_the_residual(self):
        """Kara za skrajna niepynnosc nie ma wlasnego pola w expected_net_r.

        Gdyby other_r bylo liczone jako suma nazwanych pol zamiast jako
        reszta, ten koszt zniknalby bez sladu.
        """
        row = cb.breakdown_row("BTC", _br(
            0.05, -0.25, fee_r=0.06, spread_r=0.02, slip_r=0.07,
            impact_r=0.0, funding_r=0.0))
        self.assertAlmostEqual(row["other_r"], 0.15, places=4)

    def test_a_funding_rebate_is_negative_cost_not_a_gain_in_gross(self):
        row = cb.breakdown_row("BTC", _br(
            0.05, 0.02, fee_r=0.06, spread_r=0.02, slip_r=0.0,
            impact_r=0.0, funding_r=-0.05))
        self.assertAlmostEqual(row["total_cost_r"], 0.03, places=4)
        self.assertAlmostEqual(row["other_r"], 0.0, places=4)

    def test_missing_fields_do_not_crash_the_row(self):
        row = cb.breakdown_row("BTC", {"gross_r": 0.05, "net_r": -0.10})
        self.assertEqual(row["fee_r"], 0.0)
        self.assertAlmostEqual(row["other_r"], 0.15, places=4)


class TestCounterfactual(unittest.TestCase):

    def test_zeroing_one_cost_lifts_exactly_those_it_can_lift(self):
        rows = [
            # net -0.01, fee 0.06 -> bez fee na plusie
            cb.breakdown_row("A", _br(0.05, -0.01, fee_r=0.06, spread_r=0.0,
                                      slip_r=0.0, impact_r=0.0, funding_r=0.0)),
            # net -0.30, fee 0.06 -> bez fee nadal na minusie
            cb.breakdown_row("B", _br(0.05, -0.30, fee_r=0.06, spread_r=0.0,
                                      slip_r=0.0, impact_r=0.0, funding_r=0.0)),
            # juz dodatni - liczy sie w kazdym wariancie
            cb.breakdown_row("C", _br(0.20, 0.05, fee_r=0.06, spread_r=0.0,
                                      slip_r=0.0, impact_r=0.0, funding_r=0.0)),
        ]
        out = cb.counterfactual_net_positive(rows)
        self.assertEqual(out["fee_r"], 2)
        self.assertEqual(out["spread_r"], 1)

    def test_empty_input_gives_zeros_not_a_crash(self):
        out = cb.counterfactual_net_positive([])
        self.assertEqual(set(out), set(cb.COST_FIELDS) | {"other_r"})
        self.assertEqual(sum(out.values()), 0)


class TestMajorFloorCounterfactual(unittest.TestCase):
    """Replay klasyfikuje wszystko poza BTC/ETH/SOL jako 'alt' (30 bps RT),
    bo `_volume_rank` nigdy nie jest wypelniane poza `generate()`. Ten
    kontrfaktyczny podmienia TYLKO czlon poslizgu na floor major (6 bps)."""

    def test_only_the_slippage_term_moves(self):
        rows = [cb.breakdown_row("XRP", _br(
            0.05, -0.10, sl_dist=0.02, fee_r=0.03, spread_r=0.02,
            slip_r=0.15, impact_r=0.0, funding_r=0.0))]
        fixed = cb.major_floor_rows(rows)[0]
        # 2 * DAYTRADING_V2_SLIP_MAJOR = 0.0006; 0.0006 / 0.02 = 0.03
        self.assertAlmostEqual(fixed["slip_r"], 0.03, places=4)
        # net rosnie dokladnie o roznice w poslizgu, nic wiecej
        self.assertAlmostEqual(fixed["net_r"], -0.10 + 0.15 - 0.03, places=4)
        for field in ("gross_r", "fee_r", "spread_r", "sl_dist"):
            self.assertEqual(fixed[field], rows[0][field])

    def test_input_rows_are_not_mutated(self):
        rows = [cb.breakdown_row("XRP", _br(
            0.05, -0.10, sl_dist=0.02, fee_r=0.03, spread_r=0.02,
            slip_r=0.15, impact_r=0.0, funding_r=0.0))]
        before = dict(rows[0])
        cb.major_floor_rows(rows)
        self.assertEqual(rows[0], before)

    def test_zero_sl_dist_is_passed_through_not_divided_by(self):
        rows = [cb.breakdown_row("X", _br(0.05, -0.10, sl_dist=0.0))]
        fixed = cb.major_floor_rows(rows)[0]
        self.assertEqual(fixed["net_r"], rows[0]["net_r"])


class TestDistribution(unittest.TestCase):

    def test_percentiles_and_counts(self):
        dist = cb._distribution([-0.3, -0.1, 0.0, 0.1, 0.2])
        self.assertEqual(dist["n"], 5)
        self.assertEqual(dist["min"], -0.3)
        self.assertEqual(dist["max"], 0.2)
        self.assertEqual(dist["median"], 0.0)
        # Zero to nie jest "dodatni" - inaczej NON_POSITIVE_NET_R liczyloby sie
        # inaczej tu niz w bramce.
        self.assertEqual(dist["above_zero"], 2)

    def test_empty_distribution_is_empty_not_zeroed(self):
        self.assertEqual(cb._distribution([]), {})


if __name__ == "__main__":
    unittest.main()
