# ============================================================
# 34. Testy restartu – protection state + recovery
# ============================================================

import json
import tempfile
import unittest
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from protection import ProtectionManager, ProtectionAttach
from restart_recovery import RestartRecovery


class TestRestartProtection(unittest.TestCase):
    def test_save_load_attachments(self):
        with tempfile.TemporaryDirectory() as td:
            pm = ProtectionManager()
            pm._state_path = Path(td) / "protection_state.json"
            pm.attach_protection("BTC", "LONG", sl_price=50000.0, entry_price=52000.0)
            pm.attach_protection("ETH", "SHORT", sl_price=4000.0, entry_price=3800.0)
            self.assertEqual(len(pm.by_key), 2)

            pm2 = ProtectionManager()
            pm2._state_path = pm._state_path
            pm2.load_state()
            self.assertEqual(len(pm2.by_key), 2)
            att = pm2.by_key["BTC:LONG"]
            self.assertEqual(att.sl_price, 50000.0)
            self.assertTrue(att.local_sl_armed)

    def test_kill_switch_persists(self):
        with tempfile.TemporaryDirectory() as td:
            pm = ProtectionManager()
            pm._state_path = Path(td) / "protection_state.json"
            # kill file w katalogu protection – używamy CWD bot root; tu tylko state flag
            pm.activate_kill_switch("test_restart")
            pm.save_state()
            pm2 = ProtectionManager()
            pm2._state_path = pm._state_path
            pm2.load_state()
            self.assertTrue(pm2.kill_switch_active)
            self.assertEqual(pm2.kill_reason, "test_restart")
            pm2.clear_kill_switch()

    def test_recovery_rearms_missing_sl(self):
        risk = MagicMock()
        risk.is_halted = False
        risk.halt_reason = None
        risk.paused = False

        pos = MagicMock()
        pos.symbol = "SOL"
        pos.direction = "LONG"
        pos.sl_price = 100.0
        pos.tp_price = 150.0
        pos.entry_price = 120.0

        trader = MagicMock()
        trader.positions = [pos]

        pm = ProtectionManager()
        with tempfile.TemporaryDirectory() as td:
            pm._state_path = Path(td) / "protection_state.json"
            recovery = RestartRecovery(
                risk=risk, trader=trader, protection=pm,
                reconciler=None, executor=None, account_sync=None,
            )
            report = recovery.run()
            self.assertGreaterEqual(report["protection_rearmed"], 1)
            self.assertIn("SOL:LONG", pm.by_key)
            self.assertEqual(pm.by_key["SOL:LONG"].sl_price, 100.0)


class TestPaperLeftoverCloseAllKill(unittest.TestCase):
    def _kill_path(self):
        return ROOT / "KILL_SWITCH"

    def setUp(self):
        p = self._kill_path()
        self._kill_backup = p.read_text(encoding="utf-8") if p.exists() else None
        if p.exists():
            p.unlink()

    def tearDown(self):
        p = self._kill_path()
        if p.exists():
            p.unlink()
        if self._kill_backup is not None:
            p.write_text(self._kill_backup, encoding="utf-8")

    def _pm(self, td, reason, active=True):
        pm = ProtectionManager()
        pm._state_path = Path(td) / "protection_state.json"
        pm.kill_switch_active = active
        pm.kill_reason = reason
        pm.save_state()
        return pm

    def _risk(self):
        return SimpleNamespace(is_halted=False, halt_reason=None, paused=False)

    def test_paper_clears_manual_close_all(self):
        risk = self._risk()
        with tempfile.TemporaryDirectory() as td:
            pm = self._pm(td, "manual_close_all")
            with patch.object(config, "PAPER_TRADING", True):
                report = RestartRecovery(risk=risk, trader=None, protection=pm).run()
            self.assertTrue(report["paper_ui_stop_cleared"])
            self.assertFalse(report["kill_switch"])
            self.assertFalse(pm.kill_switch_active)
            self.assertFalse(pm.is_killed())
            self.assertFalse(risk.is_halted)
            self.assertIsNone(risk.halt_reason)

    def test_paper_keeps_operator_kill(self):
        risk = self._risk()
        with tempfile.TemporaryDirectory() as td:
            pm = self._pm(td, "operator")
            with patch.object(config, "PAPER_TRADING", True):
                report = RestartRecovery(risk=risk, trader=None, protection=pm).run()
            self.assertFalse(report["paper_ui_stop_cleared"])
            self.assertTrue(report["kill_switch"])
            self.assertTrue(pm.kill_switch_active)
            self.assertTrue(risk.is_halted)
            self.assertIn("operator", str(risk.halt_reason))

    def test_live_keeps_manual_close_all(self):
        risk = self._risk()
        with tempfile.TemporaryDirectory() as td:
            pm = self._pm(td, "manual_close_all")
            with patch.object(config, "PAPER_TRADING", False):
                report = RestartRecovery(risk=risk, trader=None, protection=pm).run()
            self.assertFalse(report["paper_ui_stop_cleared"])
            self.assertTrue(report["kill_switch"])
            self.assertTrue(pm.kill_switch_active)
            self.assertTrue(risk.is_halted)
            self.assertIn("manual_close_all", str(risk.halt_reason))


if __name__ == "__main__":
    unittest.main(verbosity=2)
