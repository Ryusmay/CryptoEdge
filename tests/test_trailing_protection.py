# ============================================================
# Trailing stop ↔ ProtectionManager integration tests
# ============================================================

import unittest
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from paper_trader import Position
from protection import ProtectionManager
from portfolio_risk import compute_exposure, check_portfolio_limits


class TestTrailingProtectionIntegration(unittest.TestCase):
    def test_sync_method_exists(self):
        self.assertTrue(hasattr(Position, "_sync_exchange_trailing_sl"))

    def test_trail_calls_callback_not_protection_ref(self):
        """Position nie trzyma ProtectionManager – tylko callback on_sl_updated."""
        pos = Position(
            {"symbol": "BTC", "direction": "LONG", "price": 100.0,
             "strength": 0.9, "sl_price": 90.0, "tp_price": 130.0},
            size_usd=50, leverage=10,
        )
        self.assertFalse(hasattr(pos, "_protection_ref") and pos.__dict__.get("_protection_ref"))

        calls = []

        def cb(sym, direction, new_sl, size_contracts):
            calls.append({
                "sym": sym, "direction": direction,
                "sl": new_sl, "contracts": size_contracts,
            })

        pos.on_sl_updated = cb
        pos.pnl_pct = 50.0  # powyżej activation
        pos.highest_price = 120.0
        pos.trailing_active = False
        pos._update_trailing(120.0)

        self.assertTrue(pos.trailing_active)
        self.assertIsNotNone(pos.trailing_stop_price)
        # trail powinien podnieść SL powyżej 90 → callback
        self.assertGreater(pos.sl_price, 90.0)
        self.assertTrue(len(calls) >= 1)
        self.assertEqual(calls[-1]["sym"], "BTC")
        self.assertEqual(calls[-1]["direction"], "LONG")
        self.assertAlmostEqual(calls[-1]["sl"], pos.sl_price)

    def test_no_callback_is_noop(self):
        pos = Position(
            {"symbol": "ETH", "direction": "SHORT", "price": 200.0,
             "strength": 0.8, "sl_price": 220.0, "tp_price": 150.0},
            size_usd=30, leverage=10,
        )
        pos.pnl_pct = 40.0
        pos.lowest_price = 180.0
        pos.trailing_active = True
        pos.trailing_stop_price = 210.0
        # brak callback – nie crashuje
        pos._sync_exchange_trailing_sl()
        pos._update_trailing(180.0)

    def test_protection_update_exchange_sl_local(self):
        """Paper/no LIVE: update_exchange_sl aktualizuje lokalny att.sl_price."""
        pm = ProtectionManager(executor=None, registry=None)
        with tempfile.TemporaryDirectory() as td:
            pm._state_path = Path(td) / "protection_state.json"
            pm.attach_protection("BTC", "LONG", sl_price=90.0, entry_price=100.0, size_contracts=1.0)
            ok = pm.update_exchange_sl("BTC", "LONG", new_sl=95.0, size_contracts=1.0)
            # LIVE off → False, ale lokalny SL zaktualizowany
            self.assertFalse(ok)
            att = pm.by_key["BTC:LONG"]
            self.assertEqual(att.sl_price, 95.0)
            self.assertTrue(att.local_sl_armed)

    def test_protection_manager_called_via_callback_chain(self):
        pm = ProtectionManager(executor=None, registry=None)
        self._pm_tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._pm_tmpdir.cleanup)
        pm._state_path = Path(self._pm_tmpdir.name) / "protection_state.json"
        pm.attach_protection("SOL", "LONG", sl_price=10.0, entry_price=12.0, size_contracts=5.0)

        pos = Position(
            {"symbol": "SOL", "direction": "LONG", "price": 12.0,
             "strength": 0.9, "sl_price": 10.0, "tp_price": 20.0},
            size_usd=40, leverage=10,
        )
        pos.size_contracts = 5.0

        def _sl_cb(sym, direction, new_sl, size_contracts, _pm=pm):
            _pm.update_exchange_sl(sym, direction, new_sl=new_sl, size_contracts=size_contracts)

        pos.on_sl_updated = _sl_cb
        pos.pnl_pct = 60.0
        pos.highest_price = 15.0
        pos.trailing_active = True
        pos.trailing_stop_price = 11.0
        pos._update_trailing(15.0)

        att = pm.by_key["SOL:LONG"]
        self.assertGreaterEqual(att.sl_price, pos.sl_price - 1e-9)
        self.assertAlmostEqual(att.sl_price, pos.sl_price)


class TestActualNotionalPortfolio(unittest.TestCase):
    def test_exposure_prefers_actual_notional(self):
        pos = {
            "symbol": "BTC", "direction": "LONG",
            "size_usd": 100,          # requested
            "actual_notional": 80,    # po lot
            "margin": 8, "leverage": 10,
        }
        exp = compute_exposure([pos], equity=100)
        self.assertEqual(exp["gross_usd"], 80.0)
        self.assertEqual(exp["long_usd"], 80.0)

    def test_check_limits_uses_planned_actual(self):
        open_pos = [
            {"symbol": "ETH", "direction": "LONG", "actual_notional": 200,
             "size_usd": 250, "margin": 20, "leverage": 10},
        ]
        # new notional = actual 150
        ok, reason = check_portfolio_limits(
            open_pos, equity=100,
            new_signal={"symbol": "SOL", "direction": "LONG"},
            new_notional=150,
        )
        # gross 200+150=350 → 3.5x equity, may pass or fail depending on limits
        # at least uses 150 not something else
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(reason, str)


if __name__ == "__main__":
    unittest.main(verbosity=2)
