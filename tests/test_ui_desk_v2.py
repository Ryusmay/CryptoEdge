import ast
import unittest
from pathlib import Path


UI_PATH = Path(__file__).resolve().parents[1] / "pyside6_ui.py"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.py"
THEME_PATH = Path(__file__).resolve().parents[1] / "theme.py"


class TestUiDeskV2(unittest.TestCase):
    """Testy statyczne (bez importu - PySide6 niedostepny w tym srodowisku,
    ten sam wzorzec co reszta test_pyside6_ui_regressions.py) dla przebudowy
    UI na DESK/SCAN/LAB za flaga config.UI_DESK_V2."""

    @classmethod
    def setUpClass(cls):
        cls.source = UI_PATH.read_text(encoding="utf-8")
        cls.config_source = CONFIG_PATH.read_text(encoding="utf-8")
        cls.theme_source = THEME_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.top_level_classes = {
            node.name for node in cls.tree.body if isinstance(node, ast.ClassDef)
        }
        cls.mainwindow_methods = {
            node.name
            for item in cls.tree.body
            if isinstance(item, ast.ClassDef) and item.name == "MainWindow"
            for node in item.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def test_flag_exists_and_defaults_true(self):
        # Od 19.9.0 DESK/SCAN/LAB jest glownym interfejsem (SCAN i LAB nie
        # sa juz placeholderami) - flaga wciaz istnieje, zeby dalo sie
        # wrocic do starego 7-zakladkowego shellu recznym UI_DESK_V2 = False.
        self.assertIn("UI_DESK_V2 = True", self.config_source)

    def test_theme_module_imported(self):
        self.assertIn("import theme", self.source)

    def test_new_widget_classes_exist(self):
        self.assertTrue({"DeskPage", "GateBadge", "MiniBar", "WhyNoTradeChip"} <= self.top_level_classes)

    def test_desk_page_has_required_methods(self):
        desk_page_methods = {
            node.name
            for item in self.tree.body
            if isinstance(item, ast.ClassDef) and item.name == "DeskPage"
            for node in item.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        required = {"apply_state", "apply_tick", "select_symbol", "_fill_positions", "_fill_candidates"}
        self.assertTrue(required <= desk_page_methods, f"brakuje: {required - desk_page_methods}")

    def test_build_branches_on_flag_with_early_return(self):
        build_src = self.source[self.source.index("    def build(self):"):self.source.index("    def build_v2(self):")]
        self.assertIn('getattr(config, "UI_DESK_V2", False)', build_src)
        self.assertIn("self.build_v2()", build_src)
        self.assertIn("return", build_src)

    def test_old_build_path_is_untouched_after_the_flag_branch(self):
        # Regresja: stary shell (7 stron) MUSI pozostac nienaruszony - to
        # jedyna gwarancja, ze reczne ustawienie UI_DESK_V2=False (rollback
        # do starego layoutu) nadal dziala dokladnie jak wczesniej.
        build_src = self.source[self.source.index("    def build(self):"):self.source.index("    def build_v2(self):")]
        self.assertIn("self.build_top()", build_src)
        self.assertIn("self.build_ops()", build_src)
        self.assertIn("self.build_sidebar()", build_src)
        self.assertIn("self.overview, self.markets_workspace,", build_src)

    def test_build_v2_wires_four_pages_into_stack(self):
        self.assertIn("build_v2", self.mainwindow_methods)
        build_v2_src = self.source[self.source.index("    def build_v2(self):"):self.source.index("    def build_top_v2(self) -> QWidget:")]
        self.assertIn("self.desk_page = DeskPage(self)", build_v2_src)
        self.assertIn("self.scan_page = ScanPage(self)", build_v2_src)
        self.assertIn("self.lab_page = self.analysis_page()", build_v2_src)
        self.assertIn("self.set_page = self.settings_page()", build_v2_src)

    def test_nav_buttons_are_checkable_and_auto_exclusive(self):
        top_v2_src = self.source[self.source.index("    def build_top_v2(self) -> QWidget:"):self.source.index("    def _go_v2(self, name: str):")]
        self.assertIn("QToolButton()", top_v2_src)
        self.assertIn("btn.setCheckable(True)", top_v2_src)
        self.assertIn("btn.setAutoExclusive(True)", top_v2_src)
        for name in ("DESK", "SCAN", "LAB", "REPLAY", "SET"):
            self.assertIn(f'"{name}"', top_v2_src)

    def test_replay_tab_reaches_historical_replay_page(self):
        # Regresja: Historical Replay istnieje jako pelna strona
        # (historical_replay_page) od lat, ale przed tym poprawka byla
        # osiagalna wylacznie w starym 7-zakladkowym shellu - w UI_DESK_V2
        # (teraz domyslnym) nie dalo sie do niej w ogole dotrzec.
        build_v2_src = self.source[self.source.index("    def build_v2(self):"):self.source.index("    def build_top_v2(self) -> QWidget:")]
        self.assertIn("self.replay_page_v2 = self.historical_replay_page()", build_v2_src)
        go_v2_src = self.source[self.source.index("    def _go_v2(self, name: str):"):self.source.index("    def _on_v2_symbol_selected(self, symbol: str):")]
        self.assertIn('"REPLAY": self.replay_page_v2', go_v2_src)

    def test_account_mode_switch_lives_in_settings_shared_with_set_tab(self):
        # Regresja: DEMO/LIVE switch (account_mode_select) zyl wylacznie w
        # control_center_page() (stary shell "Safety" tab), niedostepnej z
        # UI_DESK_V2 - w nowym shellu nie dalo sie w ogole przelaczyc trybu.
        # Przeniesione do settings_page(), 1:1 wspoldzielonej z set_page.
        # settings_page() jest zdefiniowana po control_center_page() w pliku
        # i jest ostatnia metoda buildujaca strone przed pomocniczymi metodami
        # akcji (save_settings itd.) - szukamy od jej naglowka do konca pliku.
        settings_start = self.source.index("    def settings_page(self):")
        settings_src = self.source[settings_start:]
        self.assertIn("self.account_mode_select = QComboBox()", settings_src)
        self.assertIn('"DEMO (PAPER)", "LIVE (BLOFIN)"', settings_src)
        cc_start = self.source.index("    def control_center_page(self):")
        cc_src = self.source[cc_start:settings_start]
        self.assertNotIn("self.account_mode_select = QComboBox()", cc_src)

    def test_desk_page_has_quick_demo_live_buttons_wired_to_guarded_path(self):
        # Uwaga uzytkownika: przelaczanie DEMO/LIVE bylo dostepne tylko w
        # SET, chcial szybkich przyciskow na glownej stronie (DESK). Musza
        # wolac dokladnie ta sama, w pelni zabezpieczona sciezke co SET/stary
        # Control Center (request_dashboard_mode -> apply_account_mode), nie
        # duplikowac logiki blokujacej zmiane trybu.
        desk_start = self.source.index("class DeskPage(QWidget):")
        desk_end = self.source.index("class MainWindow(QMainWindow):")
        desk_src = self.source[desk_start:desk_end]
        self.assertIn('self.desk_demo_button = QPushButton("●  DEMO"', desk_src)
        self.assertIn('self.desk_live_button = QPushButton("●  LIVE"', desk_src)
        self.assertIn("self.window_.request_dashboard_mode(False)", desk_src)
        self.assertIn("self.window_.request_dashboard_mode(True)", desk_src)
        # apply_state() musi trzymac przyciski w synchronizacji z realnym
        # trybem (checked-state), tak jak stary dashboard_demo_button.
        sync_start = desk_src.index("    def sync_mode_buttons(self, demo: bool):")
        sync_src = desk_src[sync_start:]
        self.assertIn("self.desk_demo_button.setChecked(demo)", sync_src)
        self.assertIn("self.desk_live_button.setChecked(not demo)", sync_src)
        self.assertIn("self.sync_mode_buttons(account[\"mode\"] == \"DEMO\")", desk_src)

    def test_mode_buttons_resync_every_tick_not_only_on_full_scan(self):
        # Regresja: Qt przelacza checked-state checkable+autoExclusive
        # przycisku na sam klik, niezaleznie od tego czy apply_account_mode()
        # faktycznie zmienil tryb (np. odrzucone bo brak testowanych kluczy
        # LIVE) - jesli resync siedzialby tylko w desk_page.apply_state()
        # (gated przez "changed"/pelny skan co 15-30s), przycisk zostawalby
        # wizualnie "checked" na zla strone przez caly ten czas. Musi byc
        # wolany bezwarunkowo co tick, tak jak mode_pill_v2.
        impl_start = self.source.index("    def _refresh_impl_v2(self):")
        impl_end = self.source.index("    def _dispatch_price_ticker(self):")
        impl_src = self.source[impl_start:impl_end]
        gated_start = impl_src.index("if changed:")
        unconditional_src = impl_src[:gated_start]
        self.assertIn("self.desk_page.sync_mode_buttons(current_mode == \"DEMO\")", unconditional_src)

    def test_engine_buttons_visible_in_top_bar_have_start_and_lifecycle_actions(self):
        # 22.08.2026: Pigulki ANALIZA/HANDEL w gornym pasku V2 sa tylko
        # wskaznikami stanu (StatePill = QLabel, bez obslugi klikniecia) -
        # engine_specs w build_top_v2() to teraz jedyne miejsce, z ktorego
        # mozna faktycznie uruchomic analize/handel w layoucie DESK/SCAN/LAB.
        # Zastapilo dawne ukryte menu "..." (_show_v2_menu, usuniete) -
        # user: "przyciski start, pauza itp moga byc widoczne zeby szybciej
        # nimi operowac". Start* musza byc obecne, nie tylko stop/pauza.
        top_v2_src = self.source[self.source.index("    def build_top_v2(self) -> QWidget:"):self.source.index("    def _go_v2(self, name: str):")]
        self.assertIn("engine_specs = [", top_v2_src)
        for label, callback in (
            ("Start analysis", "self.start_analysis"), ("Start trading", "self.start_trading"),
            ("Pause", "self.pause"), ("Resume", "self.resume"),
            ("Stop trading", "self.stop_trading"), ("Stop bot", "self.stop_engine"),
            ("Close all", "self.close_all"),
        ):
            self.assertIn(f'"{label}"', top_v2_src)
            self.assertIn(callback, top_v2_src)
        self.assertIn("btn.clicked.connect(slot)", top_v2_src)
        self.assertNotIn("_show_v2_menu", top_v2_src)

    def test_show_v2_menu_and_stray_menu_button_are_gone(self):
        # Regresja: dawne QMenu ("...") i jego przycisk musza byc naprawde
        # usuniete, nie zostawione jako martwy kod obok nowych, widocznych
        # przyciskow silnika - inaczej dwa rozne UI do tej samej akcji.
        self.assertNotIn("def _show_v2_menu(self):", self.source)
        self.assertNotIn("_v2_menu_button", self.source)

    def test_start_analysis_does_not_hard_depend_on_old_shell_only_pills(self):
        # start_analysis() jest teraz wywolywane rowniez z menu V2, gdzie
        # engine_pill/trade_pill/ops_data (stary shell) nie istnieja -
        # musi uzywac hasattr-guardow, inaczej menu V2 "Start analysis"
        # wywali AttributeError przy pierwszym kliknieciu.
        start_src = self.source[self.source.index("    def start_analysis(self):"):self.source.index("    def start_trading(self):")]
        for widget in ("engine_pill", "trade_pill", "ops_data"):
            self.assertIn(f'hasattr(self, "{widget}")', start_src)

    def test_refresh_branches_on_flag_before_calling_impl(self):
        refresh_src = self.source[self.source.index("    def refresh(self):"):self.source.index("    def _refresh_impl_v2(self):")]
        self.assertIn('getattr(config, "UI_DESK_V2", False)', refresh_src)
        self.assertIn("self._refresh_impl_v2()", refresh_src)
        self.assertIn("self._refresh_impl()", refresh_src)

    def test_refresh_impl_v2_never_touches_old_shell_only_widgets(self):
        # Kluczowa gwarancja poprawnosci: _refresh_impl_v2 NIE MOZE
        # odwolywac sie do widgetow, ktore istnieja tylko w starym shellu
        # (self.clock, self.uptime, self.side_status) - te w V2 nie istnieja
        # w ogole (build_v2 nie tworzy build_top()/build_ops()), wiec
        # odwolanie do nich wywalilo by AttributeError na kazdym tyknieciu.
        # Uwaga: metoda ma docstring WYJASNIAJACY dlaczego tych widgetow nie
        # uzywa (wspomina je z nazwy) - sprawdzamy tylko KOD (po docstringu),
        # nie caly tekst metody, zeby nie zlapac falszywego trafienia.
        impl_v2_full = self.source[self.source.index("    def _refresh_impl_v2(self):"):self.source.index("    def _dispatch_price_ticker(self):")]
        docstring_end = impl_v2_full.index('ogole nie ma."""') + len('ogole nie ma."""')
        impl_v2_code = impl_v2_full[docstring_end:]
        for forbidden in ("self.clock", "self.uptime.", "self.side_status", "self.ops_data"):
            self.assertNotIn(forbidden, impl_v2_code, f"_refresh_impl_v2 odwoluje sie do {forbidden} - nie istnieje w V2")

    def test_refresh_impl_v2_uses_v2_only_widgets(self):
        impl_v2_src = self.source[self.source.index("    def _refresh_impl_v2(self):"):self.source.index("    def _dispatch_price_ticker(self):")]
        for expected in ("self.uptime_v2", "self.mode_pill_v2", "self.analiza_pill_v2",
                         "self.handel_pill_v2", "self.regime_pill_v2", "self.desk_page.apply_state"):
            self.assertIn(expected, impl_v2_src)

    def test_top_bar_state_pills_get_the_objectname_the_qss_targets(self):
        # Regresja: mode_pill_v2/analiza_pill_v2/handel_pill_v2 byly tworzone
        # jako bare StatePill() bez objectName - QSS #V2StatePill[tone=...]
        # w theme.py (obwodka/tlo/kolor per stan) w ogole sie nie stosowal,
        # wiec ANALIZA/HANDEL/DEMO-LIVE wygladaly identycznie w kazdym
        # stanie ("brak jasnego sygnalu co sie dzieje" - zgloszenie uzytkownika).
        top_src = self.source[self.source.index("    def build_top_v2(self) -> QWidget:"):self.source.index("    def _go_v2(self, name: str):")]
        for pill in ("self.mode_pill_v2", "self.analiza_pill_v2", "self.handel_pill_v2"):
            self.assertIn(f'{pill} = StatePill(objectName="V2StatePill")', top_src)

    def test_analiza_handel_pills_distinguish_loading_and_paused_not_just_on_off(self):
        # ANALIZA musi rozroznic "trwa skanowanie" (loading) od zwyklego ON,
        # a HANDEL musi rozroznic PAUZA (risk.paused) od zwyklego OFF -
        # inaczej "wlaczone, ale nic sie nie dzieje" i "wylaczone" wygladaja
        # tak samo, co byl dokladnie ten sam problem co przy starym,
        # binarnym on/off.
        impl_v2_src = self.source[self.source.index("    def _refresh_impl_v2(self):"):self.source.index("    def _dispatch_price_ticker(self):")]
        self.assertIn('self.analiza_pill_v2.set_state("ANALIZA: SKANOWANIE…", "loading")', impl_v2_src)
        self.assertIn('self.analiza_pill_v2.set_state("ANALIZA: ON", "on")', impl_v2_src)
        self.assertIn('self.analiza_pill_v2.set_state("ANALIZA: OFF", "off")', impl_v2_src)
        self.assertIn('self.handel_pill_v2.set_state("HANDEL: ON", "on")', impl_v2_src)
        self.assertIn('self.handel_pill_v2.set_state("HANDEL: PAUZA", "paused")', impl_v2_src)
        self.assertIn('self.handel_pill_v2.set_state("HANDEL: OFF", "off")', impl_v2_src)

    def test_theme_defines_loading_paused_demo_live_tones(self):
        for tone in ("loading", "paused", "demo", "live"):
            self.assertIn(f"[tone='{tone}']", self.theme_source)

    def test_full_apply_state_is_gated_by_state_file_mtime_change(self):
        # Spec: "Odswiezanie: tick 1 s tylko ceny i PnL wierszy. Pelny
        # apply_state przy pelnym skanie (15-30 s)." - nie co 1s tyk timera.
        impl_v2_src = self.source[self.source.index("    def _refresh_impl_v2(self):"):self.source.index("    def _dispatch_price_ticker(self):")]
        self.assertIn("changed = self._last_state_mtime is None or mtime > self._last_state_mtime + 0.5", impl_v2_src)
        self.assertIn("if changed:", impl_v2_src)

    def test_price_ticker_updates_both_old_and_v2_labels_safely(self):
        # 22.08.2026: sygnatura rozszerzona o drugi dict "changes" (24h %)
        # dla WatchlistPanel - patrz test_watchlist_panel_wired_into_desk_page.
        ticker_src = self.source[self.source.index("    def _on_price_ticker_updated(self, prices: dict, changes: dict):"):]
        ticker_src = ticker_src[:ticker_src.index("\n\n    def ", 50)]
        self.assertIn('hasattr(self, "btc_ticker_v2")', ticker_src)
        self.assertIn('hasattr(self, "desk_page")', ticker_src)
        self.assertIn("self.desk_page.apply_tick(prices)", ticker_src)
        self.assertIn("self.desk_page.apply_watchlist_tick(prices, changes)", ticker_src)

    def test_price_ticker_task_covers_four_majors_with_24h_change(self):
        # 22.08.2026: user chcial watchlist z realnymi cenami - rozszerzone
        # z BTC/ETH o SOL/XRP + 24h change (drugi dict "changes"), ten sam
        # pojedynczy fetch_all_tickers() request co wczesniej (zero nowych
        # wywolan sieciowych), patrz WatchlistPanel.SYMBOLS.
        task_src = self.source[self.source.index("class PriceTickerTask(QRunnable):"):self.source.index("class ChartLoadSignals(QObject):")]
        self.assertIn('SYMBOLS = ("BTC", "ETH", "SOL", "XRP")', task_src)
        self.assertIn("binance_change_24h", task_src)
        self.assertIn("bybit_change_24h", task_src)
        self.assertIn("self.signals.updated.emit(prices, changes)", task_src)
        self.assertIn("updated = Signal(dict, dict)", self.source)

    def test_watchlist_classes_exist(self):
        self.assertTrue({"Sparkline", "WatchlistTile", "WatchlistPanel"} <= self.top_level_classes)

    def test_risk_ring_class_exists_and_wired_into_stats_card(self):
        # 22.08.2026: odpowiednik pierscienia "Ryzyko i konto" z mockupu -
        # liczony z tych samych account()['margin']/['equity'], ktore
        # stats_card i tak juz pokazuje jako FREE/USED liczby - zero
        # nowego zrodla danych.
        self.assertIn("RiskRing", self.top_level_classes)
        desk_start = self.source.index("class DeskPage(QWidget):")
        desk_end = self.source.index("class MainWindow(QMainWindow):")
        desk_src = self.source[desk_start:desk_end]
        self.assertIn("self.risk_ring = RiskRing()", desk_src)
        apply_state_start = desk_src.index("    def apply_state(self, data: \"DataAdapter\"):")
        apply_state_src = desk_src[apply_state_start:desk_src.index("    def apply_tick(self, prices: dict):")]
        self.assertIn("self.risk_ring.set_percent((margin / equity * 100.0) if equity > 0 else 0.0)", apply_state_src)

    def test_watchlist_panel_wired_into_desk_page(self):
        # Watchlist musi zyc NAD glownym 3-kolumnowym layoutem (outer =
        # QVBoxLayout), nie wewnatrz jednej z kolumn - patrz user: "watchlist
        # zmiejszyc do 4 par" + finalna wersja mockupu "Krypto Terminal
        # Control Room" (watchlist tiles ponad grid2/grid3).
        desk_start = self.source.index("class DeskPage(QWidget):")
        desk_end = self.source.index("class MainWindow(QMainWindow):")
        desk_src = self.source[desk_start:desk_end]
        self.assertIn("outer = QVBoxLayout(self)", desk_src)
        self.assertIn("self.watchlist_panel = WatchlistPanel()", desk_src)
        self.assertIn("outer.addWidget(self.watchlist_panel)", desk_src)
        self.assertIn("outer.addLayout(root, 1)", desk_src)
        # apply_watchlist_tick() musi byc osobna sciezka od apply_tick()
        # (ten drugi ma wlasny, dawno ustalony kontrakt: ceny/PnL w tabeli
        # OTWARTYCH POZYCJI, nie watchlista).
        self.assertIn("def apply_watchlist_tick(self, prices: dict, changes: dict):", desk_src)
        self.assertIn("self.watchlist_panel.apply_tick(prices, changes)", desk_src)

    def test_data_adapter_has_v2_methods(self):
        adapter_methods = {
            node.name
            for item in self.tree.body
            if isinstance(item, ast.ClassDef) and item.name == "DataAdapter"
            for node in item.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        required = {"candidates", "why_no_trade", "regime", "feed_status"}
        self.assertTrue(required <= adapter_methods, f"brakuje: {required - adapter_methods}")

    def test_candidates_gate_uses_real_can_open_position_not_string_guessing(self):
        method_src = self.source[self.source.index("    def candidates(self, limit: int = 8) -> list[dict]:"):self.source.index("    def why_no_trade(self) -> dict:")]
        self.assertIn("risk.can_open_position(dict(row))", method_src)

    def test_desk_page_reuses_existing_market_chart_and_chart_load_task(self):
        # Spec: "reuzywa istniejace EquityChart/MarketChart/ChartLoadTask,
        # nie duplikuje ich" - test przeciwko przypadkowej duplikacji logiki
        # wykresu w nowej klasie.
        desk_page_src = self.source[self.source.index("class DeskPage(QWidget):"):self.source.index("class MainWindow(QMainWindow):")]
        self.assertIn("self.chart = MarketChart()", desk_page_src)
        self.assertIn("self.equity_chart = EquityChart()", desk_page_src)
        self.assertIn("task = ChartLoadTask(feeder, symbol, self._selected_timeframe)", desk_page_src)
        self.assertNotIn("class MarketChart", desk_page_src)  # nie zdefiniowano drugi raz lokalnie

    def test_close_all_button_delegates_to_existing_close_all_method(self):
        desk_page_src = self.source[self.source.index("class DeskPage(QWidget):"):self.source.index("class MainWindow(QMainWindow):")]
        self.assertIn("self.window_.close_all()", desk_page_src)

    def test_sl_colored_green_when_position_profitable(self):
        # Spec: "SL zielony/cyjan gdy juz na plusie".
        fill_positions_src = self.source[self.source.index("    def _fill_positions(self, positions: list[dict]):"):self.source.index("    def _fill_candidates(self, candidates: list[dict]):")]
        self.assertIn("profitable = (pnl or 0) > 0", fill_positions_src)
        self.assertIn("theme.LONG if profitable else theme.MUTED", fill_positions_src)


class TestUiScanV2(unittest.TestCase):
    """Testy statyczne dla SCAN - krok 4 kolejnosci wdrozenia."""

    @classmethod
    def setUpClass(cls):
        cls.source = UI_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.top_level_classes = {n.name for n in cls.tree.body if isinstance(n, ast.ClassDef)}

    def test_scan_classes_exist(self):
        required = {"ScanTableModel", "ScanFilterProxy", "ScanItemDelegate", "ScanPage"}
        self.assertTrue(required <= self.top_level_classes, f"brakuje: {required - self.top_level_classes}")

    def test_scan_table_model_is_qabstracttablemodel_not_qtablewidget(self):
        # Spec: "QTableView + QAbstractTableModel (nie QTableWidget - 177
        # wierszy x cykl zabije UI)".
        self.assertIn("class ScanTableModel(QAbstractTableModel):", self.source)
        self.assertIn("self.table = QTableView()", self.source)

    def test_scan_model_columns_match_spec(self):
        model_src = self.source[self.source.index("class ScanTableModel(QAbstractTableModel):"):self.source.index("class ScanFilterProxy(QSortFilterProxyModel):")]
        self.assertIn('COLUMNS = ["#", "SYM", "PRICE", "15M", "24H", "TREND (15M)", "SCORE", "PATH", "GATE"]', model_src)

    def test_delegate_paints_gate_score_spark_not_cell_widgets(self):
        # Kluczowa gwarancja wydajnosci - NIE setCellWidget/setIndexWidget
        # per wiersz (to zabiloby wydajnosc przy duzym uniwersum). Sprawdzamy
        # tylko KOD (po docstringu), nie caly tekst klasy - docstring
        # SWIADOMIE wspomina te nazwy, zeby wytlumaczyc dlaczego ich unika.
        delegate_full = self.source[self.source.index("class ScanItemDelegate(QStyledItemDelegate):"):self.source.index("class ScanPage(QWidget):")]
        docstring_end = delegate_full.index('unikac).\"\"\"') + len('unikac).\"\"\"')
        delegate_code = delegate_full[docstring_end:]
        self.assertIn("def _paint_gate(", delegate_code)
        self.assertIn("def _paint_score(", delegate_code)
        self.assertIn("def _paint_spark(", delegate_code)
        self.assertNotIn("setCellWidget", delegate_code)
        self.assertNotIn("setIndexWidget", delegate_code)

    def test_filtering_happens_in_model_not_table_rebuild(self):
        # Spec: "Filtrowanie w modelu (filterAcceptsRow), nie przez
        # przebudowe tabeli."
        proxy_src = self.source[self.source.index("class ScanFilterProxy(QSortFilterProxyModel):"):self.source.index("class ScanItemDelegate(QStyledItemDelegate):")]
        self.assertIn("def filterAcceptsRow(self, source_row: int, source_parent) -> bool:", proxy_src)

    def test_scan_page_has_universe_and_side_filters(self):
        scan_page_src = self.source[self.source.index("class ScanPage(QWidget):"):self.source.index("class MainWindow(QMainWindow):")]
        for name in ("LIQUID", "MAJORS", "ALL", "LONG", "SHORT", "BOTH"):
            self.assertIn(f'"{name}"', scan_page_src)

    def test_scan_page_view_full_analysis_signal_switches_to_lab(self):
        # Spec: "przycisk 'pelna analiza' -> stack na LAB i select_symbol()".
        # LAB = analysis_page() reuzywany 1:1, wiec faktyczna metoda to juz
        # istniejaca select_analysis_symbol() na MainWindow (patrz
        # TestUiLabV2 dla pelnego testu tej sciezki).
        self.assertIn("view_full_analysis = Signal(str)", self.source)
        handler_src = self.source[self.source.index("    def _on_v2_view_full_analysis(self, symbol: str):"):]
        handler_src = handler_src[:handler_src.index("\n\n    def ")]
        self.assertIn('self._go_v2("LAB")', handler_src)
        self.assertIn("self.select_analysis_symbol(symbol)", handler_src)

    def test_scan_page_wired_into_build_v2_and_refresh(self):
        build_v2_src = self.source[self.source.index("    def build_v2(self):"):self.source.index("    def build_top_v2(self) -> QWidget:")]
        self.assertIn("self.scan_page = ScanPage(self)", build_v2_src)
        self.assertIn("self.scan_page.view_full_analysis.connect(self._on_v2_view_full_analysis)", build_v2_src)
        impl_v2_src = self.source[self.source.index("    def _refresh_impl_v2(self):"):self.source.index("    def _dispatch_price_ticker(self):")]
        self.assertIn("self.scan_page.apply_state(self.data)", impl_v2_src)

    def test_data_adapter_has_scan_rows_method(self):
        adapter_methods = {
            node.name
            for item in self.tree.body
            if isinstance(item, ast.ClassDef) and item.name == "DataAdapter"
            for node in item.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertIn("scan_rows", adapter_methods)

    def test_scan_rows_uses_real_can_open_position_for_gate(self):
        method_src = self.source[self.source.index("    def scan_rows(self, universe_filter: str = \"LIQUID\") -> list[dict]:"):]
        method_src = method_src[:method_src.index("\n    def ", 50)]
        self.assertIn("risk.can_open_position(dict(row))", method_src)


class TestUiLabV2(unittest.TestCase):
    """Testy statyczne dla LAB - krok 5 kolejnosci wdrozenia. LAB reuzywa
    istniejacy analysis_page() 1:1, zamiast budowac drugi raz od zera
    (spec: "LAB = przeniesienie istniejacego Analysis Workspace")."""

    @classmethod
    def setUpClass(cls):
        cls.source = UI_PATH.read_text(encoding="utf-8")

    def test_lab_page_reuses_existing_analysis_page_not_rebuilt(self):
        build_v2_src = self.source[self.source.index("    def build_v2(self):"):self.source.index("    def build_top_v2(self) -> QWidget:")]
        self.assertIn("self.lab_page = self.analysis_page()", build_v2_src)

    def test_view_full_analysis_uses_existing_select_analysis_symbol(self):
        # Metoda napedzajaca LAB juz istnieje na MainWindow
        # (select_analysis_symbol) - nie duplikujemy jej na widgecie strony.
        handler_src = self.source[self.source.index("    def _on_v2_view_full_analysis(self, symbol: str):"):]
        handler_src = handler_src[:handler_src.index("\n\n    def ")]
        self.assertIn('self._go_v2("LAB")', handler_src)
        self.assertIn("self.select_analysis_symbol(symbol)", handler_src)

    def test_refresh_impl_v2_calls_refresh_analysis_when_state_changed(self):
        impl_v2_src = self.source[self.source.index("    def _refresh_impl_v2(self):"):self.source.index("    def _dispatch_price_ticker(self):")]
        self.assertIn("self.refresh_analysis()", impl_v2_src)

    def test_mtf_missing_timeframe_shows_explicit_na_not_omitted(self):
        # Spec: "jesli brak swiec, pokaz NA, nie cztery myslniki w jednej
        # linii" - kazdy z 4 interwalow MUSI pojawic sie w tekscie (z NA,
        # jesli brak danych), nie byc po cichu pominiety.
        mtf_src = self.source[self.source.index('        mtf = row.get("mtf_summary")'):self.source.index('        liquidity = row.get("liquidity")')]
        self.assertIn("else 'NA'", mtf_src)
        self.assertIn('"NA (brak danych multi-timeframe dla tego cyklu)"', mtf_src)
        # Kluczowe: kazdy tf jest w mtf_pairs bezwarunkowo (brak filtra "if
        # value not in (...)" przy budowie listy par - filtr byl usuniety).
        self.assertIn('mtf_pairs = [(tf.upper(), mtf.get(tf)) for tf in ("15m", "1h", "4h", "1d")]', mtf_src)

    def test_mtf_string_fallback_preserved_for_non_dict_case(self):
        # Nie zgubione: jesli mtf jest niepustym stringiem (nie dict),
        # oryginalny string ma byc wciaz pokazany, nie zastapiony NA.
        mtf_src = self.source[self.source.index('        mtf = row.get("mtf_summary")'):self.source.index('        liquidity = row.get("liquidity")')]
        self.assertIn('elif isinstance(mtf, str) and mtf.strip():', mtf_src)
        self.assertIn("mtf_text = mtf", mtf_src)


if __name__ == "__main__":
    unittest.main()
