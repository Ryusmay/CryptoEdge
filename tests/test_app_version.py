import unittest
from pathlib import Path

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


class TestAppRecordsBotVersion(unittest.TestCase):
    """Sprawdzamy zrodlo (nie importujemy app.py - ma efekty uboczne przy
    imporcie/uruchomieniu petli bota). bot_version w bot_state.json pozwala
    jednoznacznie stwierdzic, ktory build wygenerowal dany zestaw logow -
    bez tego trzeba to zgadywac po zachowaniu/timestampach."""

    @classmethod
    def setUpClass(cls):
        cls.source = APP_PATH.read_text(encoding="utf-8")

    def test_bot_version_helper_uses_version_module_with_safe_fallback(self):
        self.assertIn("def _bot_version() -> str:", self.source)
        self.assertIn("from version import tag", self.source)
        self.assertIn('return "unknown"', self.source)

    def test_persisted_cycle_state_includes_bot_version(self):
        self.assertIn('"bot_version": _bot_version()', self.source)

    def test_startup_recommends_read_only_blofin_key_when_paper_trading(self):
        # Bot nigdy nie sklada realnych zlecen w PAPER - klucz z pelnymi
        # uprawnieniami (TRADE/TRANSFER) to niepotrzebne ryzyko.
        self.assertIn("secrets_store.has_blofin_keys()", self.source)
        self.assertIn('getattr(config, "PAPER_TRADING", True)', self.source)
        self.assertIn("READ", self.source)

    def test_startup_attempts_best_effort_permission_check_after_generic_reminder(self):
        # Dodatkowy, konkretniejszy check (obok ogolnej wskazowki) - patrz
        # obszerny komentarz w BlofinFeed.fetch_api_key_permissions() o
        # niepewnosci co do dokladnego endpointu. Musi byc czystym dodatkiem:
        # kazdy blad/None cicho ignorowany, nigdy nie blokuje bota.
        self.assertIn("feeder.blofin.fetch_api_key_permissions()", self.source)
        self.assertIn('any(p in ("TRADE", "TRANSFER") for p in perms)', self.source)

    def test_paper_start_never_restores_old_capital_or_positions(self):
        start = self.source.index("def load_previous_state")
        end = self.source.index("def _bot_version", start)
        helper = self.source[start:end]
        guard = helper.index('getattr(config, "PAPER_TRADING", True)')
        disk_read = helper.index("state_path.read_text")
        self.assertLess(guard, disk_read)
        self.assertIn("return False", helper[:disk_read])
        self.assertIn("trader.positions = []", self.source)
        self.assertIn("risk.daily_pnl = 0.0", self.source)


if __name__ == "__main__":
    unittest.main()
