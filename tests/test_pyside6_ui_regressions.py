import ast
import re
import unittest
from pathlib import Path


UI_PATH = Path(__file__).resolve().parents[1] / "pyside6_ui.py"


class TestNativeUiParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = UI_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.methods = {
            node.name
            for item in cls.tree.body
            if isinstance(item, ast.ClassDef) and item.name == "MainWindow"
            for node in item.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def test_restored_pages_exist(self):
        self.assertTrue({"execution_page", "health_page", "system_page"} <= self.methods)

    def test_separate_stop_trading_control_exists(self):
        self.assertIn("stop_trading", self.methods)
        self.assertIn('("STOP TRADING",self.stop_trading', self.source)

    def test_old_ui_data_sections_are_present(self):
        for label in (
            "EXECUTION QUEUE", "CLOSED TRADES", "TRADE REPLAY",
            "STRATEGY HEALTH", "DATA SOURCES", "SYSTEM EVENTS",
            "MARKET CONTEXT", "DECISION FUNNEL",
        ):
            self.assertIn(label, self.source)

    def test_scanner_has_sorting_and_mtf_detail(self):
        self.assertIn('["SCORE","24H","7D","PRICE"]', self.source)
        self.assertIn('("15m","1h","4h","1d")', self.source)

    def test_blofin_credentials_and_read_only_preview_exist(self):
        for key in ("BLOFIN_API_KEY", "BLOFIN_API_SECRET", "BLOFIN_API_PASSPHRASE"):
            self.assertIn(key, self.source)
        self.assertTrue({
            "save_api_credentials", "clear_api_credentials", "test_blofin_connection"
        } <= self.methods)
        self.assertIn("fetch_futures_balance()", self.source)
        self.assertIn("fetch_open_positions()", self.source)
        self.assertIn("READ ONLY", self.source)

    def test_connection_preview_does_not_call_execution_methods(self):
        main_window = next(
            item for item in self.tree.body
            if isinstance(item, ast.ClassDef) and item.name == "MainWindow"
        )
        method = next(
            item for item in main_window.body
            if isinstance(item, ast.FunctionDef) and item.name == "test_blofin_connection"
        )
        source = ast.unparse(method).lower()
        for forbidden in ("place_order", "cancel_order", "close_position", "start_trading"):
            self.assertNotIn(forbidden, source)

    def test_control_center_is_visible_in_pyside6(self):
        for label in (
            "System Readiness / Watchdog", "Why No Trade?", "Position Protection",
            "Signal Lifecycle", "Expected vs Actual", "Entry Reservations / Session",
            "Engine Router", "Expected Net R", "Decision Telemetry", "EXPORT PAPER SESSION",
        ):
            self.assertIn(label, self.source)
        self.assertIn("control_center.enrich", self.source)
        self.assertIn("control_center.export_paper_session", self.source)
        self.assertIn("entry_reservations", self.source)

    def test_operational_diagnostics_are_not_duplicated_across_pages(self):
        for title in (
            'Card("Signal Lifecycle")', 'Card("Expected vs Actual")',
            'Card("System Readiness / Watchdog")', 'Card("Why No Trade?")',
            'Card("Position Protection")',
        ):
            self.assertEqual(self.source.count(title), 1, title)
        for removed_duplicate in (
            "self.lifecycle_table", "self.execution_compare_table", "self.reservations_table",
            "self.readiness_table", "self.no_trade_table", "self.protection_table",
        ):
            self.assertNotIn(removed_duplicate, self.source)

    def test_control_center_has_dedicated_navigation_and_mode_switch(self):
        self.assertIn('("◉", "Lab")', self.source)
        self.assertIn("self.control_center_page", self.source)
        self.assertTrue({"control_center_page", "refresh_control_center", "apply_account_mode"} <= self.methods)
        self.assertIn('"DEMO (PAPER)", "LIVE (BLOFIN)"', self.source)
        self.assertIn("LIVE_EXECUTION_ENABLED", self.source)

    def test_dashboard_has_prominent_guarded_demo_live_switch(self):
        self.assertIn('QPushButton("●  DEMO / PAPER", objectName="ModeDemo")', self.source)
        self.assertIn('QPushButton("●  LIVE / BLOFIN", objectName="ModeLive")', self.source)
        self.assertIn("self.dashboard_mode_status", self.source)
        self.assertIn("self.apply_account_mode()", self.source)
        self.assertIn("request_dashboard_mode", self.methods)

    def test_overview_shows_live_btc_eth_ticker_refreshed_every_second(self):
        # BTC/ETH co 1s z Binance/Bybit/CoinGecko (nie Blofin - osobne,
        # szybkie zrodlo niezalezne od wolnego cyklu bota).
        self.assertIn("class PriceTickerTask(QRunnable)", self.source)
        self.assertIn("self.btc_ticker_label", self.source)
        self.assertIn("self.eth_ticker_label", self.source)
        self.assertIn("binance_price", self.source)
        self.assertIn("bybit_price", self.source)
        self.assertTrue({"_dispatch_price_ticker", "_on_price_ticker_updated"} <= self.methods)
        # dispatch wpiety w refresh(), ktore i tak leci co 1000ms (self.timer)
        refresh_src = self.source[self.source.index("def refresh(self):"):self.source.index("def _refresh_impl")]
        self.assertIn("self._dispatch_price_ticker()", refresh_src)

    def test_uptime_counts_from_trading_start_not_analysis_start(self):
        # Licznik UPTIME ma liczyc dopiero od uruchomienia HANDLU
        # (trading_started_at), nie od startu analizy (started_at) -
        # dopoki handel nie wystartowal, licznik nie powinien sie ruszac.
        self.assertIn('trading_started = getattr(self.rt, "trading_started_at", None)', self.source)
        self.assertIn('self.uptime.setText("UPTIME —', self.source)
        self.assertNotIn('getattr(self.rt, "started_at", None) or self.started_ui', self.source)
        self.assertNotIn("self.started_ui", self.source)

    def test_analysis_workspace_autoselects_and_merges_full_analysis_data(self):
        self.assertIn("self.analysis_symbol_select", self.source)
        self.assertIn("self.select_analysis_symbol", self.source)
        self.assertIn("if not self.selected_symbol or self.selected_symbol not in symbols", self.source)
        self.assertIn('st.get("scanner_assets") or [], st.get("analysis_board") or [], st.get("signals") or []', self.source)
        self.assertIn("select_analysis_symbol", self.methods)

    def test_analysis_workspace_has_native_blofin_chart(self):
        self.assertIn("class MarketChart", self.source)
        self.assertIn("class ChartLoadTask", self.source)
        self.assertIn('Card("BLOFIN MARKET CHART")', self.source)
        self.assertIn('self.chart_interval.addItems(["5m", "15m", "1h", "4h", "1d"])', self.source)
        self.assertIn("fetch_klines_ohlcv", self.source)
        self.assertIn('self.overlays = {"ema": True, "trade_plan": True, "levels": False, "viper": False}', self.source)
        self.assertIn('data["_viper"]', self.source)
        self.assertIn('viper.get("levels")', self.source)
        self.assertIn('QCheckBox("EMA")', self.source)
        self.assertIn('QCheckBox("ENTRY / SL / TP")', self.source)
        self.assertIn('QCheckBox("FIB + S/R + PIVOT")', self.source)
        self.assertIn('QCheckBox("VIPER")', self.source)
        self.assertIn('"levels": False, "viper": False', self.source)
        self.assertIn("format_fibonacci(fib)", self.source)
        self.assertNotIn("json.dumps(fib", self.source)
        # 21.08.2026: LAB przebudowany pod referencje UI_DESK_V2 - stara grupa
        # naglowkow sekcji siatki ("TRADE SETUP"/"MARKET CONFIRMATION"/
        # "THESIS & CONFLUENCE"/"MODEL & AUDIT") zamieniona na pojedyncze,
        # skompresowane karty w prawej kolumnie (patrz analysis_page()) -
        # to byl wlasnie ten "stary wyglad", ktory user wprost poprosil
        # usunac ("nie zostawiaj starego wygladu zakladek jak np ... lab").
        # Test sprawdza teraz nowe karty zamiast starych naglowkow grup.
        for card_title in ("WHY", "WHY NOT", "MTF MATRIX", "Engine Router", "Decision Telemetry"):
            self.assertIn(card_title, self.source)
        self.assertIn("OPEN IN TRADINGVIEW", self.source)
        self.assertTrue({"load_analysis_chart", "on_chart_loaded", "open_selected_tradingview", "update_chart_overlays"} <= self.methods)

    def test_mode_switch_requires_stopped_engine_no_positions_and_api(self):
        main_window = next(
            item for item in self.tree.body
            if isinstance(item, ast.ClassDef) and item.name == "MainWindow"
        )
        method = next(
            item for item in main_window.body
            if isinstance(item, ast.FunctionDef) and item.name == "apply_account_mode"
        )
        source = ast.unparse(method)
        for guard in ("engine_enabled", "trading_enabled", "local_positions", "exchange_positions", "ready_for_live"):
            self.assertIn(guard, source)
        self.assertIn("sync(force=True)", source)
        self.assertIn("blocking_local = local_positions if requested_live else []", source)
        self.assertIn("managed_exchange", source)
        self.assertIn("external_exchange", source)
        self.assertIn("Pozycje NIE zostaną zamknięte ani zmodyfikowane", source)
        self.assertIn("Nie udało się pobrać świeżego stanu pozycji", source)

    def test_live_to_demo_with_unconfirmed_exchange_state_has_an_escape_hatch(self):
        # 21.08.2026: realny przypadek - uzytkownik przeszedl na LIVE, saldo
        # z Blofin sie nie zaladowalo (fetch_futures_balance/fetch_open_positions
        # failuja), a kazda kolejna proba powrotu na DEMO wpadala w ta sama
        # twarda blokade w kolko - bez zadnego wyjscia, bo "sync(force=True)"
        # wywolane ponownie failuje tak samo (przyczyna trwala, np. zle
        # uprawnienia klucza API). DEMO->LIVE (requested_live=True) MA zostac
        # twardo zablokowane bez wyjatku - ale LIVE->DEMO (requested_live=False)
        # z tym samym bledem MUSI miec jawne potwierdzenie zamiast pulapki bez
        # wyjscia, bo przejscie na DEMO nie wysyla zadnych zlecen/nie zamyka
        # pozycji, tylko przestaje je wyswietlac.
        main_window = next(
            item for item in self.tree.body
            if isinstance(item, ast.ClassDef) and item.name == "MainWindow"
        )
        method = next(
            item for item in main_window.body
            if isinstance(item, ast.FunctionDef) and item.name == "apply_account_mode"
        )
        source = ast.unparse(method)
        self.assertIn("exchange_error and requested_live", source)
        self.assertIn("exchange_error and (not requested_live)", source)
        self.assertIn("Nie udało się pobrać świeżego stanu konta/pozycji z BloFin", source)
        self.assertIn("self.confirm(", source)

    def test_navigation_combines_overlapping_workspaces(self):
        self.assertIn("self.markets_workspace", self.source)
        self.assertIn("self.trading_workspace", self.source)
        self.assertIn("self.safety_workspace", self.source)

    def test_every_nav_index_lookup_targets_an_entry_that_actually_exists(self):
        # Regresja ogolna: kazde self.go(self.NAV.index((icon, "Etykieta")))
        # musi wskazywac na krotke, ktora naprawde jest w self.NAV - inaczej
        # ValueError przy nawigacji w runtime (dokladnie to zlapalem recznie:
        # etykieta "Control Center" przetlumaczona na "Lab" w NAV, ale
        # open_analysis() dalej szukal starej angielskiej nazwy).
        nav_match = re.search(r"NAV\s*=\s*(\[[^\]]+\])", self.source, re.S)
        self.assertIsNotNone(nav_match, "nie znaleziono definicji self.NAV")
        nav_entries = set(ast.literal_eval(nav_match.group(1)))
        lookups = re.findall(r'self\.NAV\.index\(\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)\)', self.source)
        self.assertTrue(lookups, "nie znaleziono zadnego self.NAV.index(...) - test nieaktualny?")
        for icon, label in lookups:
            self.assertIn((icon, label), nav_entries,
                          f'self.NAV.index(("{icon}", "{label}")) nie odpowiada zadnej pozycji w NAV')

    def test_performance_is_nested_inside_markets_workspace(self):
        # Performance przeniesione z osobnej pozycji w NAV ("Sesja") do trzeciej
        # zakladki wewnatrz Markets, obok Market Scanner i Opportunities/Signals.
        self.assertNotIn('("⌁", "Sesja")', self.source)
        self.assertIn('("Performance", self.performance_page())', self.source)
        self.assertIn("markets_workspace", self.methods)
        # performance_page() nie jest juz osobnym top-level buildeem w build().
        builders_match = re.search(r"builders\s*=\s*(\[[^\]]+\])", self.source, re.S)
        self.assertIsNotNone(builders_match, "nie znaleziono listy builders w build()")
        self.assertNotIn("self.performance_page,", builders_match.group(1))
        self.assertNotIn("self.performance_page]", builders_match.group(1))

    def test_historical_replay_runs_off_ui_thread_and_shows_oos(self):
        self.assertIn('("▶", "Replay")', self.source)
        self.assertIn("class ReplayTask(QRunnable)", self.source)
        self.assertIn("self.chart_pool.start(task)", self.source)
        self.assertIn("OUT-OF-SAMPLE", self.source)
        self.assertIn("run_portfolio_replay_v2", self.source)

    def test_replay_uses_v2_engine_not_the_unaudited_v1_one(self):
        # 21.08.2026: regresja na realny mixup z uploadu - REPLAY w UI byl
        # zadrutowany na wprost do run_historical_replay() (silnik V1,
        # daytrading_engine.py), calkowicie inny niz to, co faktycznie
        # handluje bot (DayTradingEngineV2 / STRATEGY_MODE=DAYTRADING_V2 w
        # settings.json) - raport z takiego replayu nie mowil nic o
        # realnej strategii. ReplayTask.run() ma teraz wolac silnik V2.
        run_method = re.search(
            r"class ReplayTask\(QRunnable\):.*?def run\(self\):(.*?)\n\n\nclass",
            self.source, re.S,
        )
        self.assertIsNotNone(run_method, "nie znaleziono ReplayTask.run()")
        self.assertIn("run_portfolio_replay_v2(", run_method.group(1))
        self.assertNotIn("run_historical_replay(", run_method.group(1))
        # panel wynikow ma jawnie pokazywac silnik, zeby ten mixup byl od
        # razu widoczny w kolejnych raportach
        self.assertIn("SILNIK DAYTRADING_V2", self.source)

    def test_swing_engine_is_not_selectable_from_ui(self):
        self.assertNotIn('["SWING", "DAYTRADING"]', self.source)
        self.assertNotIn('self._settings_fields.get("STRATEGY_MODE")', self.source)
        self.assertNotIn("apply_strategy_mode_immediately", self.methods)
        self.assertNotIn("field.currentTextChanged.connect(self.apply_strategy_mode_immediately)", self.source)
        self.assertIn("self.ops_strategy", self.source)
        self.assertIn('Strategy: {strategy_mode}', self.source)
        self.assertTrue({"markets_workspace", "trading_workspace", "safety_workspace"} <= self.methods)
        self.assertIn('(\"⌕\", \"Markets\")', self.source)
        self.assertIn('(\"☷\", \"Trading\")', self.source)

    def test_start_trading_warning_matches_actual_mode(self):
        main_window = next(
            item for item in self.tree.body
            if isinstance(item, ast.ClassDef) and item.name == "MainWindow"
        )
        method = next(
            item for item in main_window.body
            if isinstance(item, ast.FunctionDef) and item.name == "start_trading"
        )
        source = ast.unparse(method)
        self.assertIn("PAPER_TRADING", source)
        self.assertIn("LIVE_EXECUTION_ENABLED", source)
        self.assertIn("Start DEMO trading", source)
        self.assertIn("Start LIVE trading", source)

    def test_ui_prefers_runtime_state_and_supports_alternate_snapshot(self):
        self.assertIn("logger", self.source)
        self.assertIn("last_state", self.source)
        self.assertIn("STATE_ALT_FILE", self.source)
        self.assertIn("max(candidates", self.source)

    def test_refresh_errors_become_visible(self):
        self.assertIn("def _refresh_impl", self.source)
        self.assertIn("UI refresh failed", self.source)
        self.assertIn("last_ui_error", self.source)

    def test_paper_export_is_blocked_in_live_mode(self):
        main_window = next(
            item for item in self.tree.body
            if isinstance(item, ast.ClassDef) and item.name == "MainWindow"
        )
        method = next(
            item for item in main_window.body
            if isinstance(item, ast.FunctionDef) and item.name == "export_paper_session"
        )
        source = ast.unparse(method)
        self.assertIn("PAPER_TRADING", source)
        self.assertIn("return", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
