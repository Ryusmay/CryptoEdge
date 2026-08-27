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

    def test_real_paper_wiring_actually_routes_the_entry_through_the_port(self):
        """Zabezpieczenie przed cicho martwym przepieciem.

        Sam fakt, ze `open_entry` istnieje i ze port jest zbudowany, nie
        dowodzi, ze wejscie przez niego idzie - fallback jest na tyle
        cichy, ze wszystko byloby zielone takze wtedy, gdyby trasowanie
        nigdy sie nie wlaczylo. Mapa client_order_id -> symbol zapelnia sie
        WYLACZNIE na sciezce portu, wiec sluzy tu za dowod.
        """
        from cryptoedge.apps.runtime import open_entry

        seen = []
        trader = SimpleNamespace(
            positions=[],
            open_position=lambda sig: seen.append(sig) or "POS",
            has_pending_limit=lambda symbol: False,
        )
        rt = SimpleNamespace(
            feeder=SimpleNamespace(), risk=SimpleNamespace(is_halted=False, paused=False),
            trader=trader, executor=object(),
        )
        attach_runtime_modules(rt)

        self.assertEqual("POS", open_entry(rt, trader, {"symbol": "BTC",
                                                        "direction": "LONG"}))
        self.assertEqual({"PAPER-BTC": "BTC"}, rt.execution_port._symbol_by_order)
        self.assertEqual(1, len(seen))

    def test_entry_never_goes_through_a_live_venue_port(self):
        """Najwazniejszy test tego przepiecia.

        W trybie LIVE `rt.execution_port` to adapter gieldy, ktorego
        `submit` sklada PRAWDZIWE zlecenie. Wejscie papierowe nie moze tam
        trafic nigdy - warunkiem jest konkretny typ portu, nie samo
        "port istnieje".
        """
        from cryptoedge.apps.runtime import open_entry
        from cryptoedge.execution import LegacyExecutionAdapter

        submitted = []

        class _Venue:
            def place_order(self, **kwargs):
                submitted.append(kwargs)
                raise AssertionError("wejscie PAPER dotknelo adaptera gieldy")

        opened = []
        trader = SimpleNamespace(open_position=lambda sig: opened.append(sig) or "POS")
        rt = SimpleNamespace(execution_port=LegacyExecutionAdapter(
            _Venue(), enabled=True, live=True))

        self.assertEqual("POS", open_entry(rt, trader, {"symbol": "BTC"}))
        self.assertEqual([], submitted)
        self.assertEqual(1, len(opened))

    def test_entry_falls_back_when_the_module_attach_degraded(self):
        from cryptoedge.apps.runtime import open_entry
        opened = []
        trader = SimpleNamespace(open_position=lambda sig: opened.append(sig) or "POS")
        for port in (None, object()):
            opened.clear()
            self.assertEqual("POS", open_entry(SimpleNamespace(execution_port=port),
                                               trader, {"symbol": "BTC"}))
            self.assertEqual(1, len(opened))

    def test_entry_uses_the_paper_port_and_maps_its_states(self):
        from cryptoedge.apps.runtime import open_entry
        from cryptoedge.execution import PaperExecutionAdapter

        class _Book:
            def __init__(self, position, pending=False):
                self.position = position
                self.pending = pending
                self.seen = []

            def open_position(self, signal):
                self.seen.append(signal)
                return self.position

            def has_pending_limit(self, symbol):
                return self.pending

        filled = _Book("POS")
        rt = SimpleNamespace(execution_port=PaperExecutionAdapter(filled))
        self.assertEqual("POS", open_entry(rt, filled, {"symbol": "BTC",
                                                        "direction": "LONG"}))

        # Zaparkowany limit: port mowi ACCEPTED, petla ma dostac None -
        # dokladnie tak, jak zwracala stara sciezka.
        parked = _Book(None, pending=True)
        rt = SimpleNamespace(execution_port=PaperExecutionAdapter(parked))
        self.assertIsNone(open_entry(rt, parked, {"symbol": "BTC",
                                                  "direction": "LONG"}))

    def test_entry_does_not_retry_after_a_failed_submit(self):
        """Ponowienie po czesciowym submit zrobiloby DWA wejscia.

        Gdy `submit` wywali sie juz po wykonaniu `open_position`, fallback
        nie ma prawa zawolac ksiegi drugi raz.
        """
        from cryptoedge.apps.runtime import open_entry
        from cryptoedge.execution import PaperExecutionAdapter

        calls = []

        class _ExplodesAfterOpening:
            def open_position(self, signal):
                calls.append(signal)
                return None

            def has_pending_limit(self, symbol):
                raise RuntimeError("ksiega zlecen padla po otwarciu")

        book = _ExplodesAfterOpening()
        rt = SimpleNamespace(execution_port=PaperExecutionAdapter(book))
        with self.assertRaises(RuntimeError):
            open_entry(rt, book, {"symbol": "BTC", "direction": "LONG"})
        self.assertEqual(1, len(calls), "ksiega zostala zawolana dwa razy")

    def test_paper_port_has_no_price_source_of_its_own(self):
        """Regula ceny zamkniecia ma jednego wlasciciela: close_policy.

        Adapter mial przez chwile wlasny prog swiezosci - inny niz
        STOP_ENGINE_MAX_PRICE_AGE_S i z odwrotna semantyka (odmawial
        zamkniecia zamiast zamknac ze sladem w powodzie). To dokladnie ten
        rozjazd, ktory close_policy powstala, zeby usunac.
        """
        from cryptoedge.execution import PaperExecutionAdapter
        adapter = PaperExecutionAdapter(SimpleNamespace(positions=[]))
        self.assertFalse(hasattr(adapter, "mark_price"))
        import cryptoedge.apps.runtime as runtime_module
        self.assertFalse(hasattr(runtime_module, "_paper_mark_price"))


if __name__ == "__main__":
    unittest.main()
