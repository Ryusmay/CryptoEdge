# ============================================================
# 34. Testy restartu – protection state + recovery
# ============================================================

import json
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
