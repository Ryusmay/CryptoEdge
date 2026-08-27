import unittest
from types import SimpleNamespace

from cryptoedge.apps.api import health_payload
from cryptoedge.apps.replay import create_replay_pipeline
from cryptoedge.apps.runtime import create_runtime_pipeline
from cryptoedge.apps.runtime import attach_runtime_modules, refresh_runtime_health
from cryptoedge.portfolio import PortfolioManager
from cryptoedge.telemetry import HealthRegistry
from cryptoedge.services import AnalysisService, TradingService


class FakeMarket:
    def snapshot(self, symbol, decision_ts_ms=None):
        return SimpleNamespace(symbol=symbol, decision_ts_ms=decision_ts_ms)


class FakeStrategy:
    def evaluate(self, snapshot):
        return {"symbol": snapshot.symbol, "direction": "LONG", "price": 100.0}


class FakeRisk:
    def assess(self, candidate, positions=()):
        return {"approved": True, "size_usd": 10.0, "candidate": candidate}


class FakeExecution:
    def submit(self, risk):
        return {"order_id": "one", "size_usd": risk["size_usd"]}


class ModularServicesTests(unittest.TestCase):
    def _components(self):
        return dict(market_data=FakeMarket(), strategy=FakeStrategy(), risk=FakeRisk(),
                    execution=FakeExecution(), portfolio=PortfolioManager(),
                    order_factory=lambda decision, risk: risk)

    def test_execution_without_contract_order_factory_fails_safe_as_approved_only(self):
        components = self._components()
        components.pop("order_factory")
        result = create_runtime_pipeline(**components).process(
            "BTC", decision_ts_ms=123, trading_enabled=True)
        self.assertEqual("APPROVED", result.status)
        self.assertIsNone(result.order)

    def test_analysis_never_submits_order(self):
        pipeline = create_runtime_pipeline(**self._components())
        result = pipeline.process("BTC", decision_ts_ms=123, trading_enabled=False)
        self.assertEqual("ANALYZED", result.status)
        self.assertIsNone(result.order)

    def test_runtime_and_replay_share_the_same_decision_pipeline(self):
        runtime = create_runtime_pipeline(**self._components())
        replay = create_replay_pipeline(**self._components())
        self.assertIsInstance(replay.pipeline, type(runtime))
        # decision_id jest bity per ocena, wiec dwa niezalezne przebiegi maja
        # go rozny z definicji - to nie jest roznica decyzji, tylko jej slad.
        # Porownujemy tresc decyzji, identyfikatory sprawdza test_decision_lineage.
        def content(decision):
            return {k: v for k, v in decision.items()
                    if k not in ("decision_id", "snapshot_id", "decision_ts_ms")}

        self.assertEqual(
            content(runtime.process("BTC", decision_ts_ms=123, trading_enabled=True).decision),
            content(replay.step("BTC", 123).decision),
        )

    def test_analysis_and_trading_services_only_differ_by_execution_permission(self):
        pipeline = create_runtime_pipeline(**self._components())
        analyzed = AnalysisService(pipeline).scan(["BTC"], decision_ts_ms=123)[0]
        traded = TradingService(pipeline).process(["BTC"], decision_ts_ms=123)[0]
        self.assertEqual("ANALYZED", analyzed.status)
        self.assertEqual("SUBMITTED", traded.status)

    def test_health_api_is_module_oriented(self):
        health = HealthRegistry()
        health.report("market_data", "warming", "12/30")
        health.report("risk", "healthy")
        payload = health_payload(health)
        self.assertEqual("degraded", payload["status"])
        self.assertIn("market_data", payload["modules"])

    def test_portfolio_rejects_raw_fill_instead_of_pretending_it_is_position(self):
        with self.assertRaises(TypeError):
            PortfolioManager().apply_fill(SimpleNamespace(order_id="o1"))

    def test_legacy_runtime_gets_module_facades_without_copying_state(self):
        legacy_positions = [SimpleNamespace(pnl=2.0)]
        rt = SimpleNamespace(
            feeder=SimpleNamespace(), risk=SimpleNamespace(is_halted=False, paused=False),
            trader=SimpleNamespace(positions=legacy_positions), executor=object(),
        )
        attach_runtime_modules(rt)
        self.assertEqual(tuple(legacy_positions), rt.portfolio_manager.positions())
        self.assertIn("risk", rt.module_health.snapshot()["modules"])
        refreshed = refresh_runtime_health(rt)
        self.assertIn("portfolio", refreshed["modules"])

    def test_paper_runtime_also_gets_an_execution_port(self):
        from cryptoedge.execution import PaperExecutionAdapter
        rt = SimpleNamespace(
            feeder=SimpleNamespace(), risk=SimpleNamespace(is_halted=False, paused=False),
            trader=SimpleNamespace(positions=[]), executor=object(),
        )
        attach_runtime_modules(rt)
        # PAPER ma wlasne venue, wiec "brak portu" przestaje znaczyc dwie rzeczy.
        self.assertEqual("PAPER", rt.execution_mode)
        self.assertIsInstance(rt.execution_port, PaperExecutionAdapter)
        self.assertFalse(rt.execution_port.live)

    def test_paper_mark_price_refuses_a_stale_price_map(self):
        from cryptoedge.apps.runtime import _paper_mark_price
        fresh = SimpleNamespace(last_price_map={"BTC": 101.0},
                                price_map_age_s=lambda: 5.0)
        self.assertEqual(101.0, _paper_mark_price(fresh)("BTC"))
        # Zamkniecie po cenie sprzed kilku minut to ten sam blad, przed
        # ktorym runtime broni sie juz przy close_all.
        stale = SimpleNamespace(last_price_map={"BTC": 101.0},
                                price_map_age_s=lambda: 9_999.0)
        self.assertIsNone(_paper_mark_price(stale)("BTC"))
        broken = SimpleNamespace(last_price_map={"BTC": 101.0})
        self.assertIsNone(_paper_mark_price(broken)("BTC"))


if __name__ == "__main__":
    unittest.main()
