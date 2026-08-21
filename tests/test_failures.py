# ============================================================
# 33. Testy awarii
# 35. API timeout po zaakceptowaniu orderu
# ============================================================

import unittest
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from order_models import Order, OrderState, new_client_order_id
from blofin_executor import BloFinExecutor
from instrument_registry import InstrumentSpec, InstrumentRegistry


class FakeRegistry:
    def get(self, symbol):
        return InstrumentSpec(
            inst_id=f"{symbol.upper()}-USDT",
            symbol=symbol.upper(),
            contract_value=0.001,
            lot_size=0.1,
            min_size=0.1,
            tick_size=0.1,
            max_leverage=20,
            state="live",
        )

    def ensure_loaded(self):
        return True

    def notional_to_contracts(self, symbol, notional_usd, price, leverage=None):
        return {
            "ok": True, "contracts": 1.0, "contracts_raw": 1.0,
            "contract_usd": price * 0.001, "notional_usd": notional_usd,
            "price": price, "contract_value": 0.001, "lot_size": 0.1,
            "min_size": 0.1, "max_market_size": 1000, "inst_id": f"{symbol}-USDT",
            "leverage": leverage or 10,
        }

    def quantize_price(self, symbol, price):
        return float(price)

    def validate_order_size(self, symbol, size, order_type="market"):
        return {"ok": True, "size": float(size), "errors": [], "warnings": []}

    def validate_order(self, symbol, size, price=None, order_type="market"):
        return self.validate_order_size(symbol, size, order_type=order_type)


class TestApiTimeoutAfterAccept(unittest.TestCase):
    """
    35. Scenariusz: POST place zwrócił timeout sieci, ale order mógł
    zostać przyjęty na giełdzie → stan TIMEOUT, nie REJECTED;
    refresh może znaleźć FILLED.
    """

    def test_place_timeout_marks_timeout_not_rejected(self):
        ex = BloFinExecutor(registry=FakeRegistry())
        # podmień _request: pierwsze wywołanie (place) = timeout
        def fake_request(method, path, params=None, body=None, timeout=None):
            if path and "set-leverage" in path:
                return {"ok": True, "timeout": False, "http_status": 200, "code": "0",
                        "msg": "", "data": {}, "raw": {}, "error": None}
            return {
                "ok": False, "timeout": True, "http_status": None,
                "code": None, "msg": None, "data": None, "raw": None,
                "error": "TIMEOUT",
            }
        ex._request = fake_request
        order = ex.place_order(
            symbol="BTC", side="buy", size_contracts=1.0,
            order_type="market", direction="LONG", wait_fill=False,
        )
        self.assertEqual(order.state, OrderState.TIMEOUT)
        self.assertTrue(order.timeout)
        self.assertNotEqual(order.state, OrderState.REJECTED)
        self.assertFalse(order.is_terminal)

    def test_timeout_then_refresh_finds_fill(self):
        ex = BloFinExecutor(registry=FakeRegistry())
        calls = {"n": 0}

        def fake_request(method, path, params=None, body=None, timeout=None):
            calls["n"] += 1
            if path and "set-leverage" in path:
                return {"ok": True, "timeout": False, "http_status": 200, "code": "0",
                        "msg": "", "data": {}, "raw": {}, "error": None}
            if "order" in (path or "") and method == "POST" and "tpsl" not in (path or ""):
                return {"ok": False, "timeout": True, "error": "TIMEOUT",
                        "http_status": None, "code": None, "msg": None, "data": None, "raw": None}
            # refresh GET – match by clientOrderId
            cid = list(ex.orders.keys())[0] if ex.orders else ""
            return {
                "ok": True, "timeout": False, "http_status": 200, "code": "0", "msg": "",
                "data": [{
                    "orderId": "999", "state": "filled",
                    "filledSize": "1", "averagePrice": "60000",
                    "clientOrderId": cid,
                }],
                "raw": {}, "error": None,
            }

        ex._request = fake_request
        order = ex.place_order(
            symbol="BTC", side="buy", size_contracts=1.0,
            order_type="market", direction="LONG", wait_fill=False,
        )
        # place przy TIMEOUT od razu woła refresh_order – fake GET zwraca filled
        self.assertIn(order.state, (OrderState.TIMEOUT, OrderState.FILLED))
        if order.state == OrderState.TIMEOUT:
            refreshed = ex.refresh_order(order)
            self.assertEqual(refreshed.state, OrderState.FILLED)
            order = refreshed
        self.assertEqual(order.state, OrderState.FILLED)
        self.assertAlmostEqual(order.avg_fill_price or 0, 60000)


class TestFailures(unittest.TestCase):
    def test_reject_on_bad_api_response(self):
        ex = BloFinExecutor(registry=FakeRegistry())

        def fake_request(method, path, params=None, body=None, timeout=None):
            if path and "set-leverage" in path:
                return {"ok": True, "timeout": False, "http_status": 200, "code": "0",
                        "msg": "", "data": {}, "raw": {}, "error": None}
            return {
                "ok": False, "timeout": False, "http_status": 400,
                "code": "1", "msg": "Insufficient margin", "data": None,
                "raw": {}, "error": "Insufficient margin",
            }

        ex._request = fake_request
        order = ex.place_order(
            symbol="BTC", side="buy", size_contracts=1.0,
            order_type="market", direction="LONG", wait_fill=False,
        )
        self.assertEqual(order.state, OrderState.REJECTED)
        self.assertFalse(order.timeout)
        self.assertTrue(order.is_terminal)

    def test_size_validation_reject(self):
        reg = FakeRegistry()
        def bad_validate(symbol, size, price=None, order_type="market"):
            return {"ok": False, "size": 0, "errors": ["below min"], "warnings": []}
        reg.validate_order_size = bad_validate
        reg.validate_order = bad_validate
        ex = BloFinExecutor(registry=reg)
        order = ex.place_order(
            symbol="BTC", side="buy", size_contracts=0.01,
            order_type="market", direction="LONG", wait_fill=False,
        )
        self.assertEqual(order.state, OrderState.REJECTED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
