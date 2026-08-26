import unittest
from pathlib import Path

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.py"
RUNTIME_PATH = Path(__file__).resolve().parents[1] / "runtime.py"


class TestFastTickVsFullScan(unittest.TestCase):
    """Regresja na realny problem z 19-20.08: bot generowal wiecej zapytan
    REST do Blofin niz limit pozwalal (500/min -> 5min ban, 1500/5min -> 1h
    ban), bo pelny skan (fetch_top_coins + generate_signals na calym
    uniwersum - dziesiatki zapytan po swiece 4 interwalow per kandydat) leciat
    w tym samym rytmie co petla (~1s). Sprawdzamy zrodlo (nie importujemy
    app.py - ma efekty uboczne przy imporcie/uruchomieniu petli bota, a w tym
    srodowisku brakuje tkinter wymaganego przez native_ui.py)."""

    @classmethod
    def setUpClass(cls):
        cls.app_source = APP_PATH.read_text(encoding="utf-8")
        cls.config_source = CONFIG_PATH.read_text(encoding="utf-8")
        cls.runtime_source = RUNTIME_PATH.read_text(encoding="utf-8")

    def test_full_scan_interval_config_exists(self):
        self.assertIn("FULL_SCAN_INTERVAL_SECONDS = 30", self.config_source)

    def test_runtime_tracks_full_scan_timestamp_and_last_results(self):
        self.assertIn("self.last_full_scan_ts: float = 0.0", self.runtime_source)
        self.assertIn("self.last_coins: list = []", self.runtime_source)
        self.assertIn("self.last_signals: list = []", self.runtime_source)
        # Restart analizy musi wymusic pelny skan, nie szybki tick na pustym cache.
        self.assertIn("self.last_full_scan_ts = 0.0", self.runtime_source)

    def test_bot_loop_throttles_full_scan_and_has_fast_tick_branch(self):
        self.assertIn("from scan_scheduling import is_full_scan_due", self.app_source)
        self.assertIn("due_for_full_scan = is_full_scan_due(last_full_scan, full_scan_interval)", self.app_source)
        self.assertIn("if not due_for_full_scan:", self.app_source)
        self.assertIn("rt.last_full_scan_ts = time.time()", self.app_source)
        # Fast-tick uzywa ostatnich znanych sygnalow zamiast generowac nowe.
        self.assertIn('trader.check_exits(list(getattr(rt, "last_signals", None) or []), price_map)', self.app_source)

    def test_full_scan_caches_coins_and_signals_for_fast_tick_reuse(self):
        self.assertIn("rt.last_coins = coins", self.app_source)
        self.assertIn("rt.last_signals = signals", self.app_source)

    def test_stop_branch_no_longer_fetches_full_universe_just_for_position_prices(self):
        # feeder.fetch_top_coins() nie powinien juz byc wolany w galezi STOP -
        # refresh_open_position_prices() sam pobiera tylko trzymane symbole
        # (1 zbiorcze zapytanie), bez potrzeby calego uniwersum.
        stop_branch_start = self.app_source.index("# STOP: bez nowych sygnalow")
        stop_branch_end = self.app_source.index("continue", stop_branch_start)
        stop_branch = self.app_source[stop_branch_start:stop_branch_end]
        self.assertNotIn("feeder.fetch_top_coins()", stop_branch)
        self.assertIn("refresh_open_position_prices(feeder, trader, dict(rt.last_price_map or {}))", stop_branch)

    def test_fast_tick_periodically_syncs_live_balance_and_positions(self):
        # Realna luka znaleziona przy weryfikacji punktow 1-9: account_sync.sync()
        # byl wolany tylko raz przy starcie bota (force=True) - w LIVE saldo i
        # pozycje z gieldy nigdy sie nie odswiezaly w trakcie sesji. sync()
        # ma juz wlasny cache (LIVE_BALANCE_CACHE_SECONDS, domyslnie 15s),
        # wiec wystarczy wolac go co fast-tick (~1s) - realnie hita siec tylko
        # raz na okno cache.
        fast_tick_start = self.app_source.index("if not due_for_full_scan:")
        fast_tick_end = self.app_source.index("wait_for_next_cycle(config.LOOP_INTERVAL_SECONDS)", fast_tick_start)
        fast_tick_branch = self.app_source[fast_tick_start:fast_tick_end]
        self.assertIn('getattr(rt, "account_sync", None)', fast_tick_branch)
        self.assertIn("sync.sync(force=False)", fast_tick_branch)

    def test_event_bus_wired_into_startup_and_persist_cycle(self):
        # Punkt 9 planu: zdarzenia cyklu/odrzucen do "laboratorium" (Redis
        # Streams). Domyslnie wylaczone (EVENT_BUS_ENABLED=False w config.py),
        # ale watek publikacji musi istniec, zeby wlaczenie w configu
        # wystarczylo bez zadnej dodatkowej zmiany kodu.
        self.assertIn("from event_bus import build_event_bus", self.app_source)
        self.assertIn("rt.event_bus = build_event_bus(config)", self.app_source)
        self.assertIn('bus = getattr(rt, "event_bus", None)', self.app_source)
        self.assertIn("bus.publish_cycle(", self.app_source)
        self.assertIn("bus.publish_reject(", self.app_source)

    def test_grpc_service_wired_into_startup_as_optional_second_interface(self):
        # Punkt 9 planu: gRPC jako drugi interfejs obok HTTP. Domyslnie
        # wylaczony (GRPC_SERVICE_ENABLED=False w config.py) - musi byc czystym
        # dodatkiem, zero wplywu na normalne dzialanie bota gdy wylaczony.
        self.assertIn("from grpc_service import GrpcServer", self.app_source)
        self.assertIn('bool(getattr(config, "GRPC_SERVICE_ENABLED", False))', self.app_source)
        self.assertIn("rt.grpc_server = GrpcServer(snapshot_provider=rt.snapshot", self.app_source)
        self.assertIn("rt.grpc_server.start()", self.app_source)

    def test_full_scan_duration_is_measured_and_warned_when_over_budget(self):
        # Siatka bezpieczenstwa dla adaptacyjnego DAYTRADING_MAX_CANDIDATES_WS_CONNECTED
        # (brak limitu, gdy WS polaczony) - jesli czas obliczen wskaznikow
        # przekroczy FULL_SCAN_INTERVAL_SECONDS, musi byc widoczne w logu.
        self.assertIn("_scan_t0 = time.time()", self.app_source)
        self.assertIn("_scan_duration = time.time() - _scan_t0", self.app_source)
        self.assertIn("if _scan_duration > _scan_budget:", self.app_source)
        self.assertIn("UWAGA: pełny skan trwał", self.app_source)


if __name__ == "__main__":
    unittest.main()
