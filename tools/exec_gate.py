# -*- coding: utf-8 -*-
"""Bramka charakterystyki dla EGZEKUCJI na gieldzie.

Ostatnia nieobjeta powierzchnia: `blofin_executor.py` (skladanie i anulowanie
zlecen) oraz `position_reconciler.py` (uzgadnianie stanu z gielda). To kod,
ktory wysyla prawdziwe zlecenia i decyduje, czy bot ma prawo dalej handlowac.

BEZ SIECI. `BloFinExecutor` przyjmuje `session` w konstruktorze i cala
komunikacja idzie przez ten jeden szew - wystarczy podstawic atrape. Rejestr
instrumentow tez jest wstrzykiwany.

CO JEST ZAPISYWANE. Nie tylko wynik, ale przede wszystkim **co bot wyslal na
gielde**: metoda, sciezka i tresc kazdego zadania. To odpowiednik
`signal_mutations` z entry_gate - refaktor, ktory zmieni tresc zlecenia albo
kolejnosc wywolan, zmieni te liste, nawet jesli wynik zostanie ten sam.

    python tools/exec_gate.py                   # porownaj z baseline
    python tools/exec_gate.py --write-baseline  # zapisz punkt odniesienia
    python tools/exec_gate.py --coverage        # jakie stany pokrywa korpus

TIMEOUT NIE JEST ODMOWA. Kluczowy kontrakt tej warstwy: gdy gielda nie
odpowie, zlecenie moze byc zlozone. Stan TIMEOUT nie moze zamienic sie
w REJECTED - inaczej bot uzna, ze pozycji nie ma, i wejdzie drugi raz.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from parity import config_fingerprint  # noqa: E402
from exit_gate import _round, _MISSING  # noqa: E402

DEFAULT_BASELINE = ROOT / "tests" / "baselines" / "exec_gate.json"

# Klucze API sa wymagane przez ready(); w bramce sa jawnie fikcyjne.
FAKE_KEYS = {
    "BLOFIN_API_KEY": "GATE-KEY",
    "BLOFIN_API_SECRET": "GATE-SECRET",
    "BLOFIN_API_PASSPHRASE": "GATE-PASS",
}


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"code": "0", "data": []}
        self.text = text or json.dumps(self._payload)

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeExchange:
    """Atrapa sesji HTTP. Notuje kazde zadanie i oddaje zaplanowana odpowiedz.

    Scenariusz to lista regul: (fragment_sciezki, odpowiedz). Odpowiedz moze
    byc `FakeResponse`, wyjatkiem do podniesienia albo funkcja.
    """

    def __init__(self, script=None, default=None):
        self.script = list(script or [])
        self.default = default or FakeResponse()
        self.requests: list = []
        self.headers = {}

    def mount(self, *args, **kwargs):
        """requests.Session.mount - executor probuje wpiac adapter IPv4/SSL."""
        return None

    def _handle(self, method, url, body=None):
        path = url.replace("https://openapi.blofin.com", "")
        parsed = None
        if isinstance(body, str) and body:
            try:
                parsed = json.loads(body)
            except ValueError:
                parsed = body
        self.requests.append({"method": method, "path": path, "body": parsed})
        for fragment, response in self.script:
            if fragment in path:
                if isinstance(response, BaseException):
                    raise response
                if callable(response):
                    return response(self.requests)
                return response
        return self.default

    def get(self, url, headers=None, timeout=None, **kw):
        return self._handle("GET", url)

    def post(self, url, headers=None, data=None, timeout=None, **kw):
        return self._handle("POST", url, data)

    def delete(self, url, headers=None, data=None, timeout=None, **kw):
        return self._handle("DELETE", url, data)


class FakeSpec:
    """Minimalna specyfikacja instrumentu - tyle, ile czyta executor."""

    def __init__(self, symbol="BTC"):
        self.symbol = symbol
        self.inst_id = f"{symbol}-USDT"
        self.contract_value = 0.001
        self.lot_size = 0.1
        self.min_size = 0.1
        self.tick_size = 0.1
        self.max_leverage = 50


class FakeRegistry:
    """Rejestr bez sieci. ensure_loaded() nie robi nic."""

    def __init__(self, known=("BTC", "ETH")):
        self.known = set(known)
        self.loaded = False

    def ensure_loaded(self):
        self.loaded = True

    def get(self, symbol):
        symbol = str(symbol or "").upper()
        return FakeSpec(symbol) if symbol in self.known else None

    def validate_order(self, symbol, size_contracts, price=None, order_type="market"):
        """Kwantyzacja do lot size i prog minimalnego rozmiaru.

        Odwzorowuje kontrakt prawdziwego rejestru: `ok` decyduje o tym, czy
        zlecenie w ogole poleci, a `size`/`price` sa juz po zaokragleniu.
        """
        spec = self.get(symbol)
        errors = []
        if spec is None:
            return {"ok": False, "size": 0.0, "price": price,
                    "errors": ["UNKNOWN_INSTRUMENT"]}
        size = float(size_contracts or 0)
        size = round(size / spec.lot_size) * spec.lot_size
        size = round(size, 8)
        if size < spec.min_size:
            errors.append(f"SIZE<{spec.min_size}")
        if order_type == "limit":
            if not price:
                errors.append("LIMIT_WITHOUT_PRICE")
            else:
                price = round(round(float(price) / spec.tick_size) * spec.tick_size, 8)
        return {"ok": not errors, "size": size, "price": price, "errors": errors}

    def notional_to_contracts(self, symbol, notional, price, leverage=None):
        spec = self.get(symbol)
        if spec is None or not price:
            return {"ok": False}
        contracts = float(notional) / (float(price) * spec.contract_value)
        contracts = round(contracts / spec.lot_size) * spec.lot_size
        return {"ok": contracts >= spec.min_size, "contracts": contracts,
                "actual_notional_usd": contracts * float(price) * spec.contract_value}


def _ok(data=None, code="0"):
    return FakeResponse(200, {"code": code, "msg": "", "data": data or []})


def _api_error(code="152001", msg="Insufficient balance"):
    return FakeResponse(200, {"code": code, "msg": msg, "data": []})


def _http_error(status=503):
    return FakeResponse(status, {"code": "1", "msg": "unavailable", "data": []})


def _timeout():
    import requests
    return requests.Timeout("gate: brak odpowiedzi")


ORDER_OK = _ok([{"orderId": "EX-1", "clientOrderId": "CE-GATE-1"}])
FILLED = _ok([{"orderId": "EX-1", "clientOrderId": "CE-GATE-1",
               "state": "filled", "filledSize": "1.0",
               "averagePrice": "100.5", "size": "1.0"}])


def build_executor_corpus() -> list:
    """Scenariusze skladania i anulowania zlecen."""
    cases = []

    def add(name, script, action, default=None):
        cases.append({"case": name, "kind": "executor", "script": script,
                      "action": action, "default": default})

    place = {"op": "place_order", "symbol": "BTC", "side": "buy",
             "size_contracts": 1.0, "direction": "LONG", "wait_fill": False}

    add("place_market_accepted", [("/trade/order", ORDER_OK)], place)
    add("place_market_api_error", [("/trade/order", _api_error())], place)
    add("place_market_http_error", [("/trade/order", _http_error())], place)
    # Kontrakt: timeout NIE jest odmowa - zlecenie moglo dojsc.
    add("place_market_timeout", [("/trade/order", _timeout())], place)
    add("place_unknown_instrument", [], dict(place, symbol="NOPE"))
    add("place_zero_size", [("/trade/order", ORDER_OK)],
        dict(place, size_contracts=0.0))
    add("place_reduce_only", [("/trade/order", ORDER_OK)],
        dict(place, reduce_only=True))
    add("place_limit_order", [("/trade/order", ORDER_OK)],
        dict(place, order_type="limit", price=99.5))
    add("place_short", [("/trade/order", ORDER_OK)],
        dict(place, side="sell", direction="SHORT"))
    # Odswiezenie stanu idzie przez order-detail; kolejnosc regul ma
    # znaczenie, bo "/trade/order" jest prefiksem "/trade/order-detail".
    add("place_and_wait_fill",
        [("/trade/order-detail", FILLED), ("/trade/order", ORDER_OK)],
        dict(place, wait_fill=True, poll_seconds=2.0))
    add("place_and_wait_partial_fill",
        [("/trade/order-detail",
          _ok([{"orderId": "EX-1", "state": "partially_filled",
                "filledSize": "0.4", "averagePrice": "100.2", "size": "1.0"}])),
         ("/trade/order", ORDER_OK)],
        dict(place, wait_fill=True, poll_seconds=2.0))
    add("place_and_wait_canceled",
        [("/trade/order-detail",
          _ok([{"orderId": "EX-1", "state": "canceled",
                "filledSize": "0", "size": "1.0"}])),
         ("/trade/order", ORDER_OK)],
        dict(place, wait_fill=True, poll_seconds=2.0))
    add("refresh_falls_back_to_history",
        [("/trade/order-detail", _ok([])),
         ("/trade/orders-history", FILLED),
         ("/trade/order", ORDER_OK)],
        dict(place, wait_fill=True, poll_seconds=2.0))

    add("fetch_open_orders_empty", [("/trade/orders", _ok([]))],
        {"op": "fetch_open_orders"})
    add("fetch_open_orders_some",
        [("/trade/orders", _ok([{"orderId": "EX-9", "instId": "ETH-USDT",
                                 "clientOrderId": "CE-OLD-9"}]))],
        {"op": "fetch_open_orders"})
    add("fetch_open_orders_timeout", [("/trade/orders", _timeout())],
        {"op": "fetch_open_orders"})

    add("cancel_orphans_none",
        [("/trade/orders", _ok([]))],
        {"op": "cancel_orphan_orders", "active_symbols": ["BTC"]})
    add("cancel_orphans_one",
        [("/trade/orders", _ok([{"orderId": "EX-9", "instId": "ETH-USDT",
                                 "clientOrderId": "CE-OLD-9"}])),
         ("/trade/cancel-order", _ok([{"orderId": "EX-9"}]))],
        {"op": "cancel_orphan_orders", "active_symbols": ["BTC"]})
    add("cancel_orphans_keeps_active",
        [("/trade/orders", _ok([{"orderId": "EX-2", "instId": "BTC-USDT",
                                 "clientOrderId": "CE-LIVE-2"}]))],
        {"op": "cancel_orphan_orders", "active_symbols": ["BTC"]})
    return cases


def build_reconciler_corpus() -> list:
    """Uzgadnianie stanu lokalnego z gielda.

    Najwazniejsze pole wyniku to `drift_blocks_entries`: gdy stan sie
    rozjezdza, bot ma przestac otwierac nowe pozycje. W PAPER lokalne pozycje
    przy pustym koncie to stan oczekiwany, nie rozjazd - dlatego kazdy
    przypadek jest sprawdzany w obu trybach.
    """
    cases = []

    def add(name, local, exchange, *, error=None, paper=True, orders=None):
        cases.append({"case": name, "kind": "reconciler", "local": local,
                      "exchange": exchange, "error": error, "paper": paper,
                      "orders": orders})

    btc = {"symbol": "BTC", "direction": "LONG", "size_contracts": 1.0}
    eth = {"symbol": "ETH", "direction": "SHORT", "size_contracts": 2.0}

    for paper in (True, False):
        tag = "paper" if paper else "live"
        add(f"in_sync_{tag}", [btc], [btc], paper=paper)
        add(f"empty_both_{tag}", [], [], paper=paper)
        add(f"only_local_{tag}", [btc], [], paper=paper)
        add(f"only_exchange_{tag}", [], [btc], paper=paper)
        add(f"size_mismatch_{tag}", [btc],
            [dict(btc, size_contracts=0.5)], paper=paper)
        add(f"two_sided_drift_{tag}", [btc], [eth], paper=paper)
        # Brak odpowiedzi z gieldy NIE jest dowodem plaskiego konta.
        add(f"venue_error_{tag}", [btc], [], error="TIMEOUT", paper=paper)
    return cases


class FakeAccountSync:
    """Zrodlo pozycji z gieldy. `error` symuluje brak odpowiedzi."""

    def __init__(self, positions=None, error=None):
        self._last_positions = list(positions or [])
        self.error = error

    def sync(self, force=False):
        if self.error:
            raise RuntimeError(self.error)
        return {"ok": True}


def _order_row(order) -> dict:
    """Stan zlecenia po operacji. `state` jest tu kontraktem."""
    if order is None:
        return None
    state = getattr(order, "state", None)
    return {
        "state": getattr(state, "name", None) or getattr(state, "value", None) or str(state),
        "symbol": getattr(order, "symbol", None),
        "side": getattr(order, "side", None),
        "direction": getattr(order, "direction", None),
        "order_type": getattr(order, "order_type", None),
        "size": _round(getattr(order, "size", None)),
        "price": _round(getattr(order, "price", None)),
        "filled_size": _round(getattr(order, "filled_size", None)),
        "avg_fill_price": _round(getattr(order, "avg_fill_price", None)),
        "reject_reason": getattr(order, "reject_reason", None),
        "exchange_order_id": getattr(order, "exchange_order_id", None),
    }


def _scrub(path: str) -> str:
    """Usuwa clientOrderId z query stringa.

    Identyfikator zawiera znacznik czasu i losowy sufiks - bez tego kazdy
    przebieg dawalby inna sciezke i baseline byl bezuzyteczny. Sam fakt
    wyslania parametru zostaje widoczny jako `clientOrderId=<zmienne>`.
    """
    if "clientOrderId=" not in path:
        return path
    head, _, tail = path.partition("clientOrderId=")
    rest = tail.split("&", 1)
    suffix = "&" + rest[1] if len(rest) > 1 else ""
    return f"{head}clientOrderId=<zmienne>{suffix}"


def _sent(exchange: FakeExchange) -> list:
    """Co bot naprawde wyslal na gielde - metoda, sciezka, tresc.

    To odpowiednik `signal_mutations` z entry_gate: refaktor, ktory zmieni
    tresc zlecenia albo kolejnosc wywolan, zmieni te liste, nawet gdy wynik
    zostanie ten sam."""
    out = []
    for req in exchange.requests:
        body = req.get("body")
        if isinstance(body, dict):
            body = {k: v for k, v in sorted(body.items())
                    if k not in ("clientOrderId", "brokerId")}
        out.append({"method": req["method"], "path": _scrub(req["path"]),
                    "body": body})
    return out


def _eval_executor(case: dict) -> dict:
    import blofin_executor as bex
    from blofin_executor import BloFinExecutor
    from exit_gate import FakeClock

    out = {"case": case["case"], "kind": "executor"}
    exchange = FakeExchange(case.get("script"), case.get("default"))
    # `_wait_fill` spi i patrzy na monotonic - bez atrapy zegara korpus
    # czekalby naprawde, a wynik zalezalby od predkosci maszyny.
    real_time = bex.time
    bex.time = FakeClock()
    try:
        executor = BloFinExecutor(registry=FakeRegistry(), session=exchange)
        action = dict(case["action"])
        op = action.pop("op")
        result = getattr(executor, op)(**action)
    finally:
        bex.time = real_time

    if op in ("place_order",):
        out["order"] = _order_row(result)
    elif isinstance(result, list):
        out["result_count"] = len(result)
        out["result"] = [
            {k: v for k, v in sorted(row.items())
             if k in ("orderId", "instId", "state", "cancelled", "reason")}
            if isinstance(row, dict) else str(row)
            for row in result
        ]
    else:
        out["result"] = str(result)
    out["sent"] = _sent(exchange)
    out["last_error"] = executor.last_error
    return out


def _eval_reconciler(case: dict) -> dict:
    import config
    from position_reconciler import PositionReconciler

    out = {"case": case["case"], "kind": "reconciler"}
    saved = getattr(config, "PAPER_TRADING", _MISSING)
    try:
        config.PAPER_TRADING = bool(case["paper"])
        account = FakeAccountSync(case["exchange"], case.get("error"))
        rec = PositionReconciler(account_sync=account)
        report = rec.reconcile(list(case["local"]))
        out["in_sync"] = report.get("in_sync")
        out["drift_blocks_entries"] = report.get("drift_blocks_entries")
        out["local_count"] = report.get("local_count")
        out["exchange_count"] = report.get("exchange_count")
        out["only_local"] = len(report.get("only_local") or [])
        out["only_exchange"] = len(report.get("only_exchange") or [])
        out["size_mismatch"] = len(report.get("size_mismatch") or [])
        out["has_error"] = bool(report.get("error"))
        out["blocks_new_entries"] = rec.blocks_new_entries()
    finally:
        if saved is _MISSING:
            delattr(config, "PAPER_TRADING")
        else:
            config.PAPER_TRADING = saved
    return out


def evaluate(case: dict) -> dict:
    import config

    saved = {key: getattr(config, key, _MISSING) for key in FAKE_KEYS}
    try:
        for key, value in FAKE_KEYS.items():
            setattr(config, key, value)
        if case["kind"] == "executor":
            return _eval_executor(case)
        if case["kind"] == "port":
            return _eval_port(case)
        if case["kind"] == "idempotency":
            return _eval_idempotency(case)
        return _eval_reconciler(case)
    except Exception as exc:
        return {"case": case["case"], "kind": case["kind"],
                "raised": f"RAISED:{type(exc).__name__}: {str(exc)[:160]}"}
    finally:
        for key, value in saved.items():
            if value is _MISSING:
                delattr(config, key)
            else:
                setattr(config, key, value)


def _cid_trace(exchange: FakeExchange) -> list:
    """Slad identyfikatorow zlecenia na drucie.

    Wartosc `clientOrderId` niesie znacznik czasu i losowy sufiks, wiec sama
    w sobie jest nieporownywalna miedzy przebiegami. Liczy sie natomiast
    TOZSAMOSC: czy kolejne zadanie uzylo tego samego identyfikatora, czy
    wygenerowalo nowy. Mapujemy je wiec na `cid#1`, `cid#2`... w kolejnosci
    pierwszego wystapienia - to jest deterministyczne i dokladnie o to chodzi.
    """
    seen: dict = {}
    trace = []
    for req in exchange.requests:
        cid = None
        body = req.get("body")
        if isinstance(body, dict):
            cid = body.get("clientOrderId") or body.get("clOrdId")
        path = req["path"]
        if cid is None and "clientOrderId=" in path:
            cid = path.partition("clientOrderId=")[2].split("&", 1)[0]
        if cid is not None:
            cid = seen.setdefault(str(cid), f"cid#{len(seen) + 1}")
        trace.append({"method": req["method"],
                      "endpoint": path.split("?", 1)[0], "cid": cid})
    return trace


def build_idempotency_corpus() -> list:
    """Czy ponowienie kiedykolwiek tworzy DRUGIE zlecenie.

    Kontrakt etapu 5. Gdy gielda nie odpowie, zlecenie moglo dojsc - wiec
    jedyne, co wolno zrobic, to zapytac o nie PO TYM SAMYM identyfikatorze.
    Drugi POST bylby druga pozycja.
    """
    cases = []

    def add(name, script, ops):
        cases.append({"case": name, "kind": "idempotency",
                      "script": script, "ops": ops})

    place = {"op": "place_order", "symbol": "BTC", "side": "buy",
             "size_contracts": 1.0, "direction": "LONG", "wait_fill": False}

    # Rdzen kontraktu: POST przepada, executor NIE sklada drugi raz - tylko
    # odpytuje po tym samym cid.
    add("idem_timeout_never_reposts",
        [("/trade/order-detail", FILLED), ("/trade/order", _timeout())],
        [dict(place)])

    # Wolajacy moze narzucic wlasny identyfikator - wtedy dwa wywolania to
    # jedno zlecenie z punktu widzenia gieldy.
    add("idem_caller_supplied_id_is_reused",
        [("/trade/order", ORDER_OK)],
        [dict(place, client_order_id="CE-STALE-1"),
         dict(place, client_order_id="CE-STALE-1")])

    # Bez narzuconego identyfikatora executor generuje nowy przy KAZDYM
    # wywolaniu. To nie jest blad - to znaczy, ze idempotencje trzyma
    # wolajacy, i ta bramka ma to trzymac widocznym.
    add("idem_generated_id_is_new_each_call",
        [("/trade/order", ORDER_OK)],
        [dict(place), dict(place)])

    # Anulowanie musi trafic w to samo zlecenie, nie w nowe.
    add("idem_cancel_targets_the_same_order",
        [("/trade/cancel-order", _ok([{"orderId": "EX-1"}])),
         ("/trade/order", ORDER_OK)],
        [dict(place), {"op": "cancel_order", "use": "last"}])

    # Odswiezenie po timeoucie i drugie odswiezenie z zewnatrz - dalej ten
    # sam identyfikator, zadnego POST-a.
    add("idem_refresh_after_timeout_stays_on_one_id",
        [("/trade/order-detail", FILLED), ("/trade/order", _timeout())],
        [dict(place), {"op": "refresh_order", "use": "last"}])
    return cases


def _eval_idempotency(case: dict) -> dict:
    import blofin_executor as bex
    from blofin_executor import BloFinExecutor
    from exit_gate import FakeClock

    out = {"case": case["case"], "kind": "idempotency"}
    exchange = FakeExchange(case.get("script"), case.get("default"))
    real_time = bex.time
    bex.time = FakeClock()
    try:
        executor = BloFinExecutor(registry=FakeRegistry(), session=exchange)
        last = None
        states = []
        for op in case["ops"]:
            action = dict(op)
            name = action.pop("op")
            if action.pop("use", None) == "last":
                result = getattr(executor, name)(last)
            else:
                result = getattr(executor, name)(**action)
            if getattr(result, "client_order_id", None):
                last = result
            states.append(
                result.state.value if hasattr(getattr(result, "state", None), "value")
                else str(getattr(result, "state", result))
            )
    finally:
        bex.time = real_time

    trace = _cid_trace(exchange)
    out["cid_trace"] = trace
    out["states"] = states
    out["distinct_cids"] = len({row["cid"] for row in trace if row["cid"]})
    out["submits"] = sum(1 for row in trace
                         if row["method"] == "POST"
                         and row["endpoint"].endswith("/trade/order"))
    # Ile razy WOLAJACY poprosil o zlozenie zlecenia. Porownanie z `submits`
    # jest cala pointa: POSTow wiecej niz prosb znaczy, ze executor ponowil
    # sam z siebie - czyli druga pozycja na gieldzie. Samo `submits > 1` nic
    # nie znaczy, bo korpus celowo wola `place_order` dwa razy.
    out["place_calls"] = sum(1 for op in case["ops"] if op["op"] == "place_order")
    out["retried_submit"] = out["submits"] > out["place_calls"]
    return out


class _StubPaperTrader:
    """Minimalny PaperTrader: tyle, ile potrzebuje PaperExecutionAdapter."""

    class _Position:
        def __init__(self, symbol, contracts):
            self.symbol = symbol
            self.size_contracts = contracts

    def __init__(self, fill_contracts=None):
        self.fill_contracts = fill_contracts

    def open_position(self, signal):
        if self.fill_contracts is None:
            return None
        return self._Position(str(signal.get("symbol") or "BTC"),
                              float(self.fill_contracts))

    def has_pending_limit(self, _symbol):
        return False


def build_port_corpus() -> list:
    """Co z wypelnienia widzi WOLAJACY przez `ExecutionPort`.

    `exec_gate` pokrywal dotad executor. Ale produkcja nie wola executora
    wprost - wola port, a port zwraca `ExecutionResult`. Pytanie, ktore
    etap 5 musi rozstrzygnac ("wspolna maszyna decision -> submit -> accepted
    -> partial/full fill -> cancel"), brzmi: czy z tego wyniku da sie odczytac,
    ILE naprawde sie wypelnilo.
    """
    cases = []

    def add(name, script, quantity=1.0, adapter="legacy", **kw):
        cases.append({"case": name, "kind": "port", "script": script,
                      "adapter": adapter, "quantity": quantity, **kw})

    PARTIAL = _ok([{"orderId": "EX-1", "clientOrderId": "CE-GATE-1",
                    "state": "partially_filled", "filledSize": "0.4",
                    "averagePrice": "100.5", "size": "1.0"}])
    WORKING = _ok([{"orderId": "EX-1", "clientOrderId": "CE-GATE-1",
                    "state": "live", "filledSize": "0", "size": "1.0"}])

    add("port_submit_full_fill",
        [("/trade/order-detail", FILLED), ("/trade/order", ORDER_OK)])
    add("port_submit_partial_fill",
        [("/trade/order-detail", PARTIAL), ("/trade/order", ORDER_OK)])
    add("port_submit_nothing_filled_yet",
        [("/trade/order-detail", WORKING), ("/trade/order", ORDER_OK)])
    add("port_submit_rejected_by_venue", [("/trade/order", _api_error())])
    # TIMEOUT i UNKNOWN to jedyne stany, w ktorych ilosc MUSI byc None:
    # `Order.filled_size` stoi wtedy na domyslnym zerze, a zero znaczylo by
    # "nic nie kupilem" - czyli zaproszenie do wejscia drugi raz.
    add("port_submit_timeout", [("/trade/order", _timeout())])
    add("port_submit_row_not_matched",
        [("/trade/order-detail",
          _ok([{"orderId": "EX-INNE", "clientOrderId": "CE-INNE",
                "state": "filled", "filledSize": "1.0", "averagePrice": "99.0"}])),
         ("/trade/order", ORDER_OK)],
        )

    # Ta sama komenda przez druga implementacje portu.
    add("port_paper_full_fill", [], adapter="paper", fill_contracts=1.0)
    add("port_paper_partial_fill", [], adapter="paper", fill_contracts=0.4)
    add("port_paper_rejected", [], adapter="paper", fill_contracts=None)
    return cases


def _eval_port(case: dict) -> dict:
    from decimal import Decimal

    out = {"case": case["case"], "kind": "port"}
    quantity = Decimal(str(case.get("quantity", 1.0)))
    command_kw = dict(
        client_order_id="CE-GATE-1", symbol="BTC", side="buy",
        quantity=quantity, direction="LONG",
    )

    if case["adapter"] == "paper":
        from cryptoedge.execution.paper_port import PaperExecutionAdapter
        from cryptoedge.execution.ports import SubmitOrder
        trader = _StubPaperTrader(case.get("fill_contracts"))
        adapter = PaperExecutionAdapter(trader)
        command = SubmitOrder(metadata={"signal": {"symbol": "BTC"}}, **command_kw)
        result = adapter.submit(command)
        raw_filled = getattr(result.raw, "size_contracts", None)
    else:
        import blofin_executor as bex
        from blofin_executor import BloFinExecutor
        from cryptoedge.execution.legacy import LegacyExecutionAdapter
        from cryptoedge.execution.ports import SubmitOrder
        from exit_gate import FakeClock

        exchange = FakeExchange(case.get("script"), case.get("default"))
        real_time = bex.time
        bex.time = FakeClock()
        try:
            executor = BloFinExecutor(registry=FakeRegistry(), session=exchange)
            adapter = LegacyExecutionAdapter(executor, enabled=True, live=True)
            command = SubmitOrder(**command_kw)
            result = adapter.submit(command)
        finally:
            bex.time = real_time
        raw_filled = getattr(result.raw, "filled_size", None)

    out["port_result"] = {
        "accepted": bool(result.accepted),
        "state": result.state,
        "reason": result.reason,
        "exchange_order_id": result.exchange_order_id,
        # Ile port UMIE powiedziec o wypelnieniu. `None` znaczy: kontrakt
        # tego nie niesie, wiec wolajacy nie ma jak odroznic partiala.
        "filled_quantity": _round(getattr(result, "filled_quantity", None)),
        "average_price": _round(getattr(result, "average_price", None)),
        # Ile WIE obiekt schowany w `raw` - informacja istnieje, tylko nie
        # przechodzi przez granice.
        "raw_knows_filled": _round(raw_filled),
        "requested": _round(quantity),
    }
    return out


def run_gate() -> dict:
    cases = (build_executor_corpus() + build_port_corpus()
             + build_idempotency_corpus() + build_reconciler_corpus())
    results = [evaluate(case) for case in cases]
    raised = [r["case"] for r in results if r.get("raised")]
    states = sorted({(r.get("order") or {}).get("state")
                     for r in results if r.get("order")} - {None})
    blocking = sum(1 for r in results if r.get("drift_blocks_entries"))
    # Ile przypadkow portu konczy sie "przyjete", a wolajacy NIE MA jak
    # sprawdzic, ile sie wypelnilo. Kazdy taki przypadek to miejsce, w ktorym
    # bot moze zaksiegowac wiecej, niz naprawde kupil.
    blind = sum(1 for r in results
                if (r.get("port_result") or {}).get("accepted")
                and (r["port_result"].get("filled_quantity") is None))
    return {
        "meta": {"cases": len(results), "raised": len(raised),
                 "order_states": len(states), "drift_blocking": blocking,
                 "accepted_without_fill_quantity": blind,
                 # Najwazniejsza liczba w sekcji idempotencji: w ilu
                 # przypadkach executor zlozyl zlecenie wiecej razy, niz
                 # poproszono. Musi byc zero - kazde takie ponowienie to
                 # druga pozycja na gieldzie.
                 "retried_submits": sum(1 for r in results
                                        if r.get("retried_submit"))},
        "config": config_fingerprint(),
        "order_states": states,
        "results": results,
    }


def compare(baseline: dict, current: dict) -> list:
    problems: list = []
    old_cfg = (baseline.get("config") or {}).get("hash")
    new_cfg = (current.get("config") or {}).get("hash")
    if old_cfg != new_cfg:
        problems.append(f"KONFIGURACJA: hash {old_cfg} -> {new_cfg}")
    old_map = {r["case"]: r for r in baseline.get("results") or []}
    new_map = {r["case"]: r for r in current.get("results") or []}
    # Porownujemy WSZYSTKIE klucze, nie liste dozwolonych.
    #
    # Wczesniej byla tu krotka `fields` z nazwami do sprawdzenia. Sabotaz
    # pokazal, ze to sie cicho psuje: skreslenie z niej `port_result`
    # wylaczalo kontrole calej granicy portu, a bramka dalej mowila
    # "IDENTYCZNIE". Nowe pole dodane do wyniku bez dopisania do listy bylo
    # tak samo niewidoczne. Denylist zamiast allowlisty - jak w restart_gate
    # i fill_gate - nie da sie zapomniec.
    ignored = {"case", "kind"}
    for name in sorted(set(old_map) | set(new_map)):
        before, after = old_map.get(name), new_map.get(name)
        if before is None:
            problems.append(f"  + NOWY PRZYPADEK {name}")
            continue
        if after is None:
            problems.append(f"  - USUNIETY PRZYPADEK {name}")
            continue
        for field in sorted((set(before) | set(after)) - ignored):
            if before.get(field) != after.get(field):
                problems.append(f"  ~ {name}.{field}: "
                                f"{before.get(field)!r} -> {after.get(field)!r}")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Bramka charakterystyki egzekucji.")
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument("--coverage", action="store_true")
    args = ap.parse_args(argv)

    current = json.loads(json.dumps(run_gate()))
    meta = current["meta"]
    print(f"[exec] przypadkow {meta['cases']} | wyjatkow {meta['raised']}"
          f" | stanow zlecen {meta['order_states']}"
          f" | blokad wejsc {meta['drift_blocking']}"
          f" | config {current['config'].get('hash')}")

    if args.coverage:
        print("\nStany zlecen pokryte przez korpus:")
        for state in current["order_states"]:
            print(f"  {state}")
        print("\nPrzypadki blokujace nowe wejscia (drift):")
        for res in current["results"]:
            if res.get("drift_blocks_entries"):
                print(f"  {res['case']}")
        for res in current["results"]:
            if res.get("raised"):
                print(f"  WYJATEK {res['case']}: {res['raised']}")
        return 0

    if args.write_baseline:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(
            json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[exec] zapisano baseline: {args.baseline}")
        return 0

    if not args.baseline.is_file():
        print(f"[exec] BRAK baseline. Utworz: python tools/exec_gate.py --write-baseline")
        return 2

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    problems = compare(baseline, current)
    if not problems:
        print("[exec] IDENTYCZNIE — egzekucja bez zmian.")
        return 0
    print(f"[exec] ROZNI SIE ({len(problems)} pozycji):")
    for line in problems[:60]:
        print(line)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
