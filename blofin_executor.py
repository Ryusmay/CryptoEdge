# ============================================================
# BloFinExecutor – składanie / odpytywanie / anulowanie zleceń
# timeout ≠ failed; fill price/size z giełdy
# ============================================================
# UWAGA: domyślnie LIVE_EXECUTION_ENABLED=False.
# Ten moduł NIE jest wywoływany z paper loop, dopóki nie włączysz flagi.
# ============================================================

from __future__ import annotations

import json
import time
import uuid
import hmac
import hashlib
import base64
from typing import Optional, Dict, Any, List

import requests

import config
from instrument_registry import InstrumentRegistry
from order_models import (
    Order, OrderState, new_client_order_id, map_blofin_state,
)


class BloFinExecutor:
    """
    Warstwa egzekucji REST Blofin.
    - place market/limit
    - get order by orderId / clientOrderId
    - cancel
    - set leverage
    """

    BASE = "https://openapi.blofin.com"

    def __init__(self, registry: InstrumentRegistry = None, session: requests.Session = None):
        self.registry = registry or InstrumentRegistry()
        self.session = session or requests.Session()
        try:
            from blofin_feed import configure_blofin_session
            configure_blofin_session(self.session)
        except Exception as e:
            print(f"[Executor] transport: {e}")
            self.session.headers.update({"Accept": "application/json"})
        self.last_error: Optional[str] = None
        self.orders: Dict[str, Order] = {}  # client_order_id → Order
        self.request_timeout = float(getattr(config, "ORDER_REQUEST_TIMEOUT", 12) or 12)
        self.poll_timeout = float(getattr(config, "ORDER_POLL_TIMEOUT", 8) or 8)

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    def ready(self) -> bool:
        return bool(
            getattr(config, "BLOFIN_API_KEY", "")
            and getattr(config, "BLOFIN_API_SECRET", "")
            and getattr(config, "BLOFIN_API_PASSPHRASE", "")
        )

    def _sign_headers(self, method: str, path_with_query: str, body: str = "") -> dict:
        ts = str(int(time.time() * 1000))
        nonce = str(uuid.uuid4())
        prehash = f"{path_with_query}{method.upper()}{ts}{nonce}{body}"
        secret = config.BLOFIN_API_SECRET
        hex_sig = hmac.new(secret.encode(), prehash.encode(), hashlib.sha256).hexdigest()
        sign = base64.b64encode(hex_sig.encode()).decode()
        return {
            "ACCESS-KEY": config.BLOFIN_API_KEY,
            "ACCESS-SIGN": sign,
            "ACCESS-TIMESTAMP": ts,
            "ACCESS-NONCE": nonce,
            "ACCESS-PASSPHRASE": config.BLOFIN_API_PASSPHRASE,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        params: dict = None,
        body: dict = None,
        timeout: float = None,
    ) -> dict:
        """
        Zwraca ujednolicony wynik:
          {ok, timeout, http_status, code, msg, data, raw, error}
        timeout=True → NIE traktować jako REJECTED.
        """
        timeout = timeout if timeout is not None else self.request_timeout
        params = params or {}
        q = ""
        if params:
            q = "?" + "&".join(f"{k}={v}" for k, v in params.items())
        full_path = path + q
        body_str = ""
        if body is not None:
            body_str = json.dumps(body, separators=(",", ":"))
        url = self.BASE + full_path
        out = {
            "ok": False,
            "timeout": False,
            "http_status": None,
            "code": None,
            "msg": None,
            "data": None,
            "raw": None,
            "error": None,
        }
        if not self.ready():
            out["error"] = "NO_API_KEYS"
            return out
        try:
            headers = self._sign_headers(method.upper(), full_path, body_str)
            if method.upper() == "GET":
                r = self.session.get(url, headers=headers, timeout=timeout)
            elif method.upper() == "POST":
                r = self.session.post(url, headers=headers, data=body_str, timeout=timeout)
            elif method.upper() == "DELETE":
                r = self.session.delete(url, headers=headers, data=body_str or None, timeout=timeout)
            else:
                out["error"] = f"BAD_METHOD:{method}"
                return out
            out["http_status"] = r.status_code
            try:
                payload = r.json()
            except Exception:
                payload = {"raw_text": r.text[:500]}
            out["raw"] = payload
            out["code"] = payload.get("code")
            out["msg"] = payload.get("msg") or payload.get("message")
            out["data"] = payload.get("data")
            code_ok = str(out["code"]) in ("0", "success") or out["code"] == 0
            if r.status_code == 200 and code_ok:
                out["ok"] = True
            else:
                out["error"] = f"API code={out['code']} msg={out['msg']} http={r.status_code}"
            return out
        except requests.Timeout as e:
            out["timeout"] = True
            out["error"] = f"TIMEOUT: {e}"
            self.last_error = out["error"]
            return out
        except requests.RequestException as e:
            # błędy sieci – też nie = reject
            out["timeout"] = True  # traktuj jak niepewność (reconcile)
            out["error"] = f"NETWORK: {e}"
            self.last_error = out["error"]
            return out
        except Exception as e:
            out["error"] = str(e)
            self.last_error = out["error"]
            return out

    # ------------------------------------------------------------------
    # Helpers: size from USD
    # ------------------------------------------------------------------
    def prepare_size(
        self,
        symbol: str,
        notional_usd: float,
        price: float,
        order_type: str = "market",
        leverage: float = None,
    ) -> dict:
        """USD notional → validated contracts (actual_notional po lot size)."""
        self.registry.ensure_loaded()
        lev = float(leverage or getattr(config, "LEVERAGE", 10))
        spec = self.registry.get(symbol)
        if spec and float(getattr(spec, "max_leverage", 0) or 0) > 0 and lev > float(spec.max_leverage):
            return {
                "ok": False,
                "error": f"LEVERAGE_EXCEEDS_MAX({lev}>{spec.max_leverage})",
                "contracts": 0.0,
                "max_leverage": float(spec.max_leverage),
            }
        conv = self.registry.notional_to_contracts(symbol, notional_usd, price, leverage=lev)
        if not conv.get("ok"):
            return conv
        val = self.registry.validate_order(
            symbol, conv["contracts"], price=price if order_type != "market" else None,
            order_type=order_type,
        )
        actual = conv.get("actual_notional_usd")
        conv.update(val)
        if actual is not None:
            conv["actual_notional_usd"] = actual
            conv["notional_usd_requested"] = notional_usd
            conv["notional_usd"] = actual
        conv["ok"] = bool(val.get("ok")) and float(conv.get("contracts") or 0) > 0
        return conv

    # ------------------------------------------------------------------
    # Leverage
    # ------------------------------------------------------------------
    def set_leverage(self, inst_id: str, leverage: int, margin_mode: str = "cross") -> dict:
        body = {
            "instId": inst_id,
            "leverage": str(int(leverage)),
            "marginMode": margin_mode,
        }
        return self._request("POST", "/api/v1/account/set-leverage", body=body)

    # ------------------------------------------------------------------
    # Place order
    # ------------------------------------------------------------------
    def place_order(
        self,
        symbol: str,
        side: str,                      # buy | sell
        size_contracts: float,
        order_type: str = "market",
        price: float = None,
        reduce_only: bool = False,
        leverage: int = None,
        direction: str = None,          # LONG | SHORT
        client_order_id: str = None,
        margin_mode: str = None,
        position_side: str = None,
        wait_fill: bool = True,
        poll_seconds: float = None,
    ) -> Order:
        """
        Składa zlecenie. Przy timeout → state=TIMEOUT (nie REJECTED).
        Przy wait_fill odpytuje status i ustawia avg_fill_price / filled_size.
        """
        self.registry.ensure_loaded()
        spec = self.registry.get(symbol)
        if not spec:
            o = Order(
                client_order_id=client_order_id or new_client_order_id(),
                symbol=symbol, inst_id=f"{symbol}-USDT",
                side=side, direction=direction or ("LONG" if side == "buy" else "SHORT"),
                order_type=order_type, size=float(size_contracts or 0), price=price,
            )
            o.transition(OrderState.REJECTED, "UNKNOWN_INSTRUMENT")
            o.reject_reason = "UNKNOWN_INSTRUMENT"
            return o

        lev = int(leverage or getattr(config, "LEVERAGE", 10))
        mm = margin_mode or getattr(config, "BLOFIN_MARGIN_MODE", "cross")
        ps = position_side or getattr(config, "BLOFIN_POSITION_SIDE", "net")
        cid = client_order_id or new_client_order_id()

        # walidacja
        val = self.registry.validate_order(symbol, size_contracts, price=price, order_type=order_type)
        order = Order(
            client_order_id=cid,
            symbol=spec.symbol,
            inst_id=spec.inst_id,
            side=side.lower(),
            direction=(direction or ("LONG" if side.lower() == "buy" else "SHORT")).upper(),
            order_type=order_type,
            size=float(val.get("size") or 0),
            price=val.get("price"),
            reduce_only=reduce_only,
            leverage=lev,
            margin_mode=mm,
            position_side=ps,
            decision_ts_ms=int(time.time() * 1000),
        )
        self.orders[cid] = order

        if not val.get("ok"):
            order.transition(OrderState.REJECTED, ";".join(val.get("errors") or []))
            order.reject_reason = ";".join(val.get("errors") or [])
            return order

        # maxLeverage instrumentu – twarde ograniczenie
        max_lev = float(getattr(spec, "max_leverage", 0) or 0)
        if max_lev > 0 and lev > max_lev:
            order.transition(OrderState.REJECTED, f"LEVERAGE>{max_lev}")
            order.reject_reason = f"LEVERAGE_EXCEEDS_MAX({lev}>{max_lev})"
            order.last_error = order.reject_reason
            return order

        # positionSide: one-way = net; hedge = long/short zgodne z direction
        ps = self._resolve_position_side(ps, order.direction)

        # leverage – w LIVE nie wolno ignorować błędu
        require_lev = bool(getattr(config, "REQUIRE_LEVERAGE_SET", True))
        try:
            lev_resp = self.set_leverage(spec.inst_id, lev, mm)
            if require_lev and (lev_resp.get("timeout") or not lev_resp.get("ok")):
                order.transition(
                    OrderState.REJECTED,
                    lev_resp.get("error") or "LEVERAGE_SET_FAILED",
                )
                order.reject_reason = lev_resp.get("msg") or lev_resp.get("error") or "LEVERAGE_SET_FAILED"
                order.last_error = order.reject_reason
                order.timeout = bool(lev_resp.get("timeout"))
                return order
        except Exception as e:
            if require_lev:
                order.transition(OrderState.REJECTED, f"LEVERAGE_EXC:{e}")
                order.reject_reason = str(e)
                order.last_error = str(e)
                return order

        body = {
            "instId": spec.inst_id,
            "marginMode": mm,
            "positionSide": ps,
            "side": order.side,
            "orderType": order_type,
            "size": str(order.size),
            "clientOrderId": cid,
        }
        order.position_side = ps
        if order_type != "market" and order.price is not None:
            body["price"] = str(order.price)
        if reduce_only:
            body["reduceOnly"] = "true"

        delay = float(getattr(config, "ENTRY_DELAY_SECONDS", 0) or 0)
        if delay > 0 and not reduce_only:
            time.sleep(min(delay, 30.0))

        order.transition(OrderState.SUBMITTING, "POST /trade/order")
        order.submitted_ts_ms = int(time.time() * 1000)
        resp = self._request("POST", "/api/v1/trade/order", body=body)
        order.raw_submit = resp.get("raw")

        if resp.get("timeout"):
            # NIE failed – niepewność; trzeba reconciliować po clientOrderId
            order.transition(OrderState.TIMEOUT, resp.get("error") or "timeout")
            order.last_error = resp.get("error")
            # spróbuj od razu odpytać po clientOrderId
            self.refresh_order(order)
            return order

        if not resp.get("ok"):
            order.transition(OrderState.REJECTED, resp.get("error") or "api_reject")
            order.reject_reason = resp.get("msg") or resp.get("error")
            order.last_error = order.reject_reason
            return order

        data = resp.get("data")
        # data bywa listą lub dictem
        row = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else {})
        order.order_id = str(row.get("orderId") or row.get("ordId") or "") or None
        order.accepted_ts_ms = int(time.time() * 1000)
        order.transition(OrderState.SUBMITTED, f"orderId={order.order_id}")

        if wait_fill:
            wait_s = poll_seconds
            if wait_s is None:
                wait_s = float(getattr(config, "ORDER_WAIT_FILL_SECONDS", 3.0) or 3.0)
            self._wait_fill(order, poll_seconds=wait_s)
        return order

    def _wait_fill(self, order: Order, poll_seconds: float = 3.0):
        # monotonic – odporne na NTP step
        deadline = time.monotonic() + poll_seconds
        while time.monotonic() < deadline and not order.is_terminal:
            time.sleep(0.35)
            self.refresh_order(order)
            if order.state in (OrderState.FILLED, OrderState.PARTIAL) and order.filled_size > 0:
                if order.state == OrderState.FILLED:
                    break
        return order

    # ------------------------------------------------------------------
    # Query / cancel
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_position_side(configured: str, direction: str) -> str:
        """
        One-way mode → 'net'
        Hedge mode → 'long' / 'short' zgodne z kierunkiem pozycji
        """
        mode = (getattr(config, "BLOFIN_POSITION_MODE", None) or "").lower()
        cfg = (configured or getattr(config, "BLOFIN_POSITION_SIDE", "net") or "net").lower()
        if mode in ("hedge", "long_short", "long-short") or cfg in ("long", "short", "hedge"):
            d = (direction or "").upper()
            if d == "LONG":
                return "long"
            if d == "SHORT":
                return "short"
            return cfg if cfg in ("long", "short") else "net"
        return "net"

    @staticmethod
    def _match_order_row(order: Order, rows: list) -> Optional[dict]:
        """
        Znajdź wiersz odpowiadający dokładnie temu zleceniu.
        NIE wolno brać rows[0] w ciemno.
        """
        if not rows:
            return None
        oid = str(order.order_id or "")
        cid = str(order.client_order_id or "")
        exact = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            rid = str(row.get("orderId") or row.get("ordId") or "")
            rcid = str(row.get("clientOrderId") or row.get("clOrdId") or "")
            if oid and rid and rid == oid:
                exact.append(row)
            elif cid and rcid and rcid == cid:
                exact.append(row)
        if exact:
            return exact[0]
        # jeden wiersz + filtr instId – ostrożnie akceptuj tylko gdy ID się zgadza częściowo
        if len(rows) == 1 and isinstance(rows[0], dict):
            row = rows[0]
            rid = str(row.get("orderId") or row.get("ordId") or "")
            rcid = str(row.get("clientOrderId") or row.get("clOrdId") or "")
            if (oid and rid == oid) or (cid and rcid == cid):
                return row
            # bez ID – nie zgaduj
            return None
        return None

    def refresh_order(self, order: Order) -> Order:
        """
        Odśwież stan z giełdy.
        1) order-detail (konkretne zlecenie) – preferowane
        2) orders-history / orders – tylko z twardym match po ID
        """
        if not order.order_id and not order.client_order_id:
            return order

        resp = None
        # 1) order-detail – podstawowy refresh
        detail_params = {"instId": order.inst_id}
        if order.order_id:
            detail_params["orderId"] = order.order_id
        if order.client_order_id:
            detail_params["clientOrderId"] = order.client_order_id
        resp = self._request(
            "GET", "/api/v1/trade/order-detail",
            params=detail_params,
            timeout=self.poll_timeout,
        )
        # 2) fallback: historia (nadal z ID)
        if not resp.get("ok") or not resp.get("data"):
            hist_params = dict(detail_params)
            resp2 = self._request(
                "GET", "/api/v1/trade/orders-history",
                params=hist_params,
                timeout=self.poll_timeout,
            )
            if resp2.get("ok") and resp2.get("data"):
                resp = resp2
        # 3) ostatni fallback: pending orders + match
        if not resp.get("ok") or not resp.get("data"):
            pend = self._request(
                "GET", "/api/v1/trade/orders",
                params=detail_params,
                timeout=self.poll_timeout,
            )
            if pend.get("ok") and pend.get("data"):
                resp = pend

        if resp.get("timeout"):
            if order.state not in (OrderState.FILLED, OrderState.CANCELED, OrderState.REJECTED):
                order.transition(OrderState.TIMEOUT, "refresh timeout")
            return order

        if not resp.get("ok"):
            order.last_error = resp.get("error")
            return order

        data = resp.get("data")
        # order-detail bywa dict; lista w pending/history
        if isinstance(data, dict):
            rows = [data]
        elif isinstance(data, list):
            rows = data
        else:
            rows = []
        row = self._match_order_row(order, rows)
        # detail zwrócił pojedynczy obiekt bez ID w body – zaakceptuj gdy query było po ID
        if not row and len(rows) == 1 and isinstance(rows[0], dict) and order.order_id:
            rid = str(rows[0].get("orderId") or rows[0].get("ordId") or "")
            if not rid or rid == str(order.order_id):
                row = rows[0]
        if not row:
            order.last_error = "ORDER_ROW_NOT_MATCHED"
            if order.state not in (OrderState.FILLED, OrderState.CANCELED, OrderState.REJECTED):
                order.transition(OrderState.UNKNOWN, "no matching order row")
            return order

        order.raw_status = row
        if not order.order_id:
            order.order_id = str(row.get("orderId") or row.get("ordId") or "") or None

        st = map_blofin_state(str(row.get("state") or row.get("status") or ""))
        try:
            filled = float(row.get("filledSize") or row.get("accFillSz") or row.get("fillSz") or 0)
        except (TypeError, ValueError):
            filled = order.filled_size
        try:
            avg = row.get("averagePrice") or row.get("avgPx") or row.get("fillPx")
            avg = float(avg) if avg not in (None, "", "0") else None
        except (TypeError, ValueError):
            avg = order.avg_fill_price

        delta = max(0.0, filled - float(order.filled_size or 0))
        if delta > 0 and avg:
            role = str(row.get("liquidityRole") or row.get("execType") or row.get("fillRole") or "").lower()
            if role not in ("maker", "taker"):
                # Market orders remove liquidity; limit fills require the venue
                # role to avoid inventing maker rebates.
                role = "taker" if str(order.order_type).lower() == "market" else None
            raw_fee = float(row.get("fee") or row.get("fillFee") or 0)
            fee = raw_fee - float(order.fee or 0) if abs(raw_fee) >= abs(float(order.fee or 0)) else raw_fee
            fill_ts = int(row.get("fillTime") or row.get("updateTime") or row.get("uTime") or time.time() * 1000)
            order.record_fill(delta, avg, fee=fee, liquidity_role=role, ts_ms=fill_ts)
            # `averagePrice` z giełdy to średnia CAŁEGO zlecenia i jest
            # autorytatywna. Nasza rekonstrukcja z przyrostów nie jest: wiersz
            # zlecenia nie niesie ceny pojedynczego fillu, więc przy fillach po
            # ruchomej cenie VWAP liczony przyrostowo wychodzi zaniżony.
            # Zdarzenia zostają do audytu, średnia idzie z giełdy.
            order.avg_fill_price = avg
            if order.size and filled > float(order.size):
                order.last_error = "OVERFILL"
        elif delta > 0:
            # Ilość bez ceny. NIE wolno jej przyjąć: kolejny odczyt policzyłby
            # VWAP tak, jakby ta część poszła po cenie zero. Zostawiamy stan
            # bez zmian — przyrost wróci przy następnym odpytaniu, już z ceną.
            order.last_error = "FILL_WITHOUT_PRICE"
        elif filled < float(order.filled_size or 0):
            # Giełda cofnęła ilość. To glitch albo nie ten wiersz; przyjęcie
            # rozjechałoby `filled_size` z sumą `fill_events`.
            order.last_error = "FILLED_SIZE_WENT_BACKWARDS"
        elif avg:
            order.avg_fill_price = avg
        order.transition(st, f"ex_state={row.get('state')} filled={filled}")
        return order

    def cancel_order(self, order: Order) -> Order:
        if order.is_terminal:
            return order
        order.transition(OrderState.CANCELING, "cancel requested")
        body = {"instId": order.inst_id}
        if order.order_id:
            body["orderId"] = order.order_id
        if order.client_order_id:
            body["clientOrderId"] = order.client_order_id
        resp = self._request("POST", "/api/v1/trade/cancel-order", body=body)
        if resp.get("timeout"):
            order.transition(OrderState.TIMEOUT, "cancel timeout")
            return order
        if not resp.get("ok"):
            order.last_error = resp.get("error")
            # może już filled
            self.refresh_order(order)
            return order
        self.refresh_order(order)
        if not order.is_terminal:
            order.transition(OrderState.CANCELED, "cancel accepted")
        if order.state == OrderState.CANCELED:
            order.canceled_ts_ms = int(time.time() * 1000)
        return order

    def fetch_open_orders(self) -> List[dict]:
        resp = self._request("GET", "/api/v1/trade/orders", params={"instType": "SWAP"}, timeout=self.poll_timeout)
        data = resp.get("data") if resp.get("ok") else []
        return list(data if isinstance(data, list) else ([data] if isinstance(data, dict) else []))

    def cancel_orphan_orders(self, active_symbols=None, client_prefix: str = "CE") -> List[dict]:
        """Anuluje tylko zlecenia nalezace do bota, bez odpowiadajacej pozycji."""
        active = {str(x).upper().replace("-USDT", "") for x in (active_symbols or [])}
        results = []
        for row in self.fetch_open_orders():
            client_id = str(row.get("clientOrderId") or row.get("clOrdId") or "")
            inst = str(row.get("instId") or "").upper()
            symbol = inst.replace("-USDT", "")
            if not client_id.startswith(client_prefix) or symbol in active:
                continue
            shadow = Order(client_order_id=client_id, symbol=symbol, inst_id=inst,
                           side="sell", direction="LONG", order_type="market", size=0)
            shadow.order_id = str(row.get("orderId") or row.get("ordId") or "") or None
            self.cancel_order(shadow)
            results.append({"symbol": symbol, "client_order_id": client_id, "state": str(shadow.state)})
        return results

    # ------------------------------------------------------------------
    # Convenience: open / close by notional
    # ------------------------------------------------------------------
    def open_market(
        self,
        symbol: str,
        direction: str,          # LONG | SHORT
        notional_usd: float,
        price: float,
        leverage: int = None,
        wait_fill: bool = True,
    ) -> Order:
        side = "buy" if direction.upper() == "LONG" else "sell"
        prep = self.prepare_size(symbol, notional_usd, price, order_type="market", leverage=leverage)
        if not prep.get("ok"):
            o = Order(
                client_order_id=new_client_order_id(),
                symbol=symbol, inst_id=prep.get("inst_id") or f"{symbol}-USDT",
                side=side, direction=direction.upper(),
                order_type="market", size=0,
            )
            o.transition(OrderState.REJECTED, prep.get("error") or str(prep.get("errors")))
            o.reject_reason = prep.get("error") or ";".join(prep.get("errors") or [])
            return o
        return self.place_order(
            symbol=symbol,
            side=side,
            size_contracts=prep["size"],
            order_type="market",
            direction=direction.upper(),
            leverage=leverage,
            wait_fill=wait_fill,
        )

    def close_market(
        self,
        symbol: str,
        direction: str,          # kierunek ISTNIEJĄCEJ pozycji
        size_contracts: float,
        wait_fill: bool = True,
    ) -> Order:
        # zamknięcie LONG = sell, SHORT = buy
        side = "sell" if direction.upper() == "LONG" else "buy"
        return self.place_order(
            symbol=symbol,
            side=side,
            size_contracts=size_contracts,
            order_type="market",
            direction=direction.upper(),
            reduce_only=True,
            wait_fill=wait_fill,
        )

    def close_position_endpoint(self, symbol: str, margin_mode: str = None) -> dict:
        """POST /api/v1/trade/close-position – awaryjne zamknięcie całego instrumentu."""
        spec = self.registry.get(symbol) if self.registry else None
        inst_id = spec.inst_id if spec else f"{symbol.upper()}-USDT"
        body = {
            "instId": inst_id,
            "marginMode": margin_mode or getattr(config, "BLOFIN_MARGIN_MODE", "cross"),
        }
        return self._request("POST", "/api/v1/trade/close-position", body=body)

    def emergency_close_all(self, symbols: List[str] = None, reconciler=None) -> List[dict]:
        """
        Kill-switch helper: close-position per symbol.
        Po request – potwierdzenie reconciliation (confirm_flat), nie samo przyjęcie HTTP.
        """
        results = []
        for sym in symbols or []:
            entry = {
                "symbol": sym,
                "request_ok": False,
                "confirmed_flat": False,
                "error": None,
                "timeout": False,
            }
            try:
                r = self.close_position_endpoint(sym)
                entry["request_ok"] = bool(r.get("ok"))
                entry["timeout"] = bool(r.get("timeout"))
                entry["error"] = r.get("error")
            except Exception as e:
                entry["error"] = str(e)
            results.append(entry)

        if reconciler is not None and hasattr(reconciler, "confirm_flat"):
            try:
                time.sleep(float(getattr(config, "EMERGENCY_CLOSE_CONFIRM_WAIT", 0.8)))
                conf = reconciler.confirm_flat(symbols or [])
                flat = bool(conf.get("flat"))
                remaining = {x["symbol"]: x for x in (conf.get("remaining") or [])}
                for entry in results:
                    if entry["symbol"] in remaining:
                        entry["confirmed_flat"] = False
                        entry["remaining_size"] = remaining[entry["symbol"]].get("size")
                    else:
                        entry["confirmed_flat"] = flat or entry["symbol"] not in remaining
                if not flat:
                    for entry in results:
                        if entry["symbol"] in remaining:
                            entry["error"] = entry.get("error") or "NOT_FLAT_AFTER_CLOSE"
            except Exception as e:
                for entry in results:
                    entry["error"] = entry.get("error") or f"CONFIRM_ERR:{e}"
        return results
