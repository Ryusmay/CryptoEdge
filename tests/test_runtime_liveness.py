"""Wykrywanie zamrozonej petli glownej.

25.08.2026: ostatni cykl o 11:30:57, proces zyl i odpowiadal na /api/health
az do 19:10. Kazdy modul raportowal "healthy", bo raportowal ostatni znany
stan i nikt nie pytal, ile ten stan ma lat. Te testy pilnuja, zeby wiek
cyklu byl czescia kontraktu, a nie detalem implementacji.
"""
import time
import unittest

from runtime import BotRuntime


def _rt(*, engine=True, cycle_age=1.0, price_age=1.0, loading=False):
    rt = BotRuntime()
    rt.engine_enabled = engine
    rt.analysis_loading = loading
    rt.cycle = 42
    now = time.time()
    rt.last_heartbeat = 0.0 if cycle_age is None else now - cycle_age
    rt.last_price_map_ts = 0.0 if price_age is None else now - price_age
    return rt


class TestRuntimeLiveness(unittest.TestCase):
    def test_fresh_cycle_is_ok(self):
        self.assertEqual("ok", _rt().liveness()["state"])

    def test_old_cycle_with_engine_on_is_frozen(self):
        alive = _rt(cycle_age=BotRuntime.LOOP_STALE_AFTER_S + 60).liveness()
        self.assertEqual("frozen", alive["state"])
        self.assertTrue(alive["frozen"])

    def test_engine_off_is_never_frozen(self):
        alive = _rt(engine=False, cycle_age=40000).liveness()
        self.assertEqual("off", alive["state"])
        self.assertFalse(alive["frozen"])

    def test_warmup_is_not_frozen(self):
        alive = _rt(cycle_age=BotRuntime.LOOP_STALE_AFTER_S + 60, loading=True).liveness()
        self.assertNotEqual("frozen", alive["state"])

    def test_stale_price_map_is_degraded(self):
        alive = _rt(price_age=BotRuntime.PRICE_MAP_STALE_AFTER_S + 60).liveness()
        self.assertEqual("degraded", alive["state"])
        self.assertTrue(alive["prices_stale"])

    def test_never_started_is_starting(self):
        self.assertEqual("starting", _rt(cycle_age=None).liveness()["state"])

    def test_ages_are_reported_in_seconds(self):
        alive = _rt(cycle_age=125.0, price_age=7.0).liveness()
        self.assertAlmostEqual(125.0, alive["cycle_age_s"], delta=5.0)
        self.assertAlmostEqual(7.0, alive["price_map_age_s"], delta=5.0)

    def test_real_incident_shape(self):
        """43788 s nieswiezych cen i stojaca petla - stan z 25.08.2026."""
        alive = _rt(cycle_age=27540.0, price_age=43788.0).liveness()
        self.assertEqual("frozen", alive["state"])
        self.assertTrue(alive["prices_stale"])


class TestLivenessIsExposed(unittest.TestCase):
    def test_status_payload_contains_liveness(self):
        from pathlib import Path

        source = Path(__file__).resolve().parents[1].joinpath("engine_api.py").read_text(encoding="utf-8")
        self.assertIn('"liveness": _liveness(rt)', source)

    def test_health_registry_reports_frozen_loop_as_failed(self):
        from cryptoedge.apps.runtime import refresh_runtime_health
        from cryptoedge.telemetry import HealthRegistry

        rt = _rt(cycle_age=BotRuntime.LOOP_STALE_AFTER_S + 60)
        rt.module_health = HealthRegistry()
        snapshot = refresh_runtime_health(rt)
        self.assertEqual("failed", snapshot["modules"]["runtime_loop"]["status"])
        self.assertEqual("failed", snapshot["status"])

    def test_health_registry_healthy_when_loop_runs(self):
        from cryptoedge.apps.runtime import refresh_runtime_health
        from cryptoedge.telemetry import HealthRegistry

        rt = _rt()
        rt.module_health = HealthRegistry()
        snapshot = refresh_runtime_health(rt)
        self.assertEqual("healthy", snapshot["modules"]["runtime_loop"]["status"])


if __name__ == "__main__":
    unittest.main()
