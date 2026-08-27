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


def run_gate() -> dict:
    cases = build_executor_corpus() + build_reconciler_corpus()
    results = [evaluate(case) for case in cases]
    raised = [r["case"] for r in results if r.get("raised")]
    states = sorted({(r.get("order") or {}).get("state")
                     for r in results if r.get("order")} - {None})
    blocking = sum(1 for r in results if r.get("drift_blocks_entries"))
    return {
        "meta": {"cases": len(results), "raised": len(raised),
                 "order_states": len(states), "drift_blocking": blocking},
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
    fields = ("raised", "order", "sent", "last_error", "result", "result_count",
              "in_sync", "drift_blocks_entries", "blocks_new_entries",
              "local_count", "exchange_count", "only_local", "only_exchange",
              "size_mismatch", "has_error")
    for name in sorted(set(old_map) | set(new_map)):
        before, after = old_map.get(name), new_map.get(name)
        if before is None:
            problems.append(f"  + NOWY PRZYPADEK {name}")
            continue
        if after is None:
            problems.append(f"  - USUNIETY PRZYPADEK {name}")
            continue
        for field in fields:
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
