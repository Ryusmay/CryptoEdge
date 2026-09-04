"""Bramka charakterystyki ksiegowania fillow.

Etap 5 ma oprzec ksiege na fillach. Dzis fill z gieldy dociera WYLACZNIE do
`Order.filled_size` / `Order.fill_events` w pamieci `BloFinExecutor` i tam
umiera - `paper_trader` nigdy go nie widzi. Rownolegle istnieje druga,
kompletniejsza implementacja tej samej reguly - `FillLedger`/`OrderLifecycle`
w `cryptoedge/execution/` - z ZEREM wywolan produkcyjnych.

METODA. Nie porownuje ksiag ze soba, bo wtedy nie wiadomo, ktora klamie.
Kazdy przypadek niesie PRAWDE ZIEMI: co naprawde zostalo wypelnione, po jakiej
cenie, za jaka prowizje. Wiersze z gieldy sa z tej prawdy generowane wedlug
konwencji danego przypadku (fee narastajace vs przyrostowe, brakujaca cena,
cofnieta ilosc). Bramka mierzy, JAK WIERNIE kazda ze stron odtwarza prawde
z tego, co przyszlo z gieldy.

To jest pytanie, na ktore trzeba odpowiedziec PRZED podmiana ksiegi:
`refresh_order` rekonstruuje fill z ROZNICY `filledSize` wzgledem poprzedniego
odczytu, bo wiersz zlecenia nie niesie identyfikatora transakcji. `FillLedger`
zaklada strumien faktow z `trade_id`. To dwa rozne modele danych, nie dwie
implementacje tego samego.

Bramka NIE rusza sieci: `_request` executora jest podmieniany na kanwe
odpowiedzi z korpusu. Zaden przypadek nie wysyla zlecenia.

CENA WEJSCIA. Ceny POJEDYNCZEGO fillu z migawki `order-detail` odtworzyc sie
nie da - wiersz niesie `averagePrice` calego zlecenia, nie ceny przyrostu.
Wniosek nie jest jednak taki, ze trzeba czekac na strumien transakcji: srednia
z gieldy JEST autorytatywna dla calego zlecenia, wiec od v20.42.0
`refresh_order` bierze ja wprost, zamiast rekonstruowac VWAP z przyrostow
(`partial_fills_at_moving_prices`: prawda 100.9, wczesniej 100.3986).

To odwraca kierunek migracji w tym miejscu. `FillLedger` liczy VWAP z faktow
per transakcja, ktorych ta gielda w tej scieżce nie dostarcza, wiec sam
z siebie bylby MNIEJ dokladny niz kod, ktory zastepuje. Ksiega oparta na
fillach musi wiec albo dostac prawdziwy strumien transakcji, albo przenosic
autorytatywna srednia zlecenia obok wlasnego agregatu. Bramka pilnuje tego
wprost: `partial_fills_at_moving_prices` pokazuje `Order` zgodny z prawda
i ksiege rozjechana.

ZNANE ROZJAZDY (zmierzone, swiadomie NIE naprawiane):
  - `fee_reported_incrementally`: heurystyka `raw_fee - order.fee` zaklada, ze
    gielda podaje prowizje NARASTAJACO. Gdy podaje przyrostowo, druga i kazda
    kolejna jest gubiona (0.3 zamiast 0.6). Nie ruszam tego, bo z samego
    wiersza tych dwoch konwencji ROZROZNIC SIE NIE DA - wybor wymaga
    potwierdzenia, jak zachowuje sie konkretna gielda, a nie zgadywania.
  - `quantity_without_average_price`: gdy gielda poda ilosc bez ceny, ksiega
    nie przyjmuje NICZEGO i czeka na nastepny odczyt. Wychodzi wiec "nie wiem"
    zamiast "wiem zle" - swiadomy wybor, bo ilosc bez ceny zafalszowalaby
    VWAP przy kolejnym fillu.
  - `quantity_without_price_then_price`: ilosc i cena koncowa sa poprawne,
    ale zdarzen jest 1 zamiast 2 - gielda scalila dwa fille w jedna migawke
    i tego sie juz nie rozdzieli.
  - `overfill_beyond_requested`: `Order` przyjmuje wiecej, niz zamowiono, i
    tylko ODNOTOWUJE to w `last_error`. Odmowa zapisania fillu, ktory sie
    faktycznie wydarzyl, bylaby gorsza: ksiega nie moze udawac, ze na gieldzie
    nie ma pozycji. `OrderLifecycle` odmawia - i to jest rozjazd kontraktow,
    ktory trzeba rozstrzygnac przy podmianie ksiegi.
  - `role_missing_on_market_order`: `refresh_order` dopisuje role "taker" dla
    zlecen market. To nie zgadywanie - zlecenie market zdejmuje plynnosc.
    `FillLedger` dostaje z wiersza None i tak zostaje, wiec nowa sciezka
    musialaby to wzbogacenie odtworzyc.
  - `apply_fill` w `OrderLifecycle` nie jest atomowe: zapisuje fill do ksiegi,
    a dopiero potem probuje przejscia stanu - niedozwolone przejscie zostawia
    ksiege z faktem, ktorego status nie odzwierciedla.

CZEGO TA BRAMKA NIE POKRYWA (i dlaczego to nie jest przeoczenie):
  - idempotencji `FillLedger` po `trade_id`. Wiersz zlecenia nie niesie
    identyfikatora transakcji, wiec `trade_id` musi tu byc ZMYSLONY z numeru
    kroku. Tej wlasciwosci przez sciezke gieldowa pokryc sie nie da; pokrywa
    ja bezposrednio `tests/test_execution_module.py`.
  - wewnetrznego VWAP w `Order.record_fill`. Odkad `refresh_order` bierze
    srednia z gieldy, ta rekonstrukcja nie wplywa juz na wynik tej sciezki -
    sabotaz jej wzoru przechodzi przez bramke niezauwazony. Pilnuje jej
    `tests/test_v20_5_execution_research.py::test_actual_partial_fills_
    produce_vwap_role_and_latency`, wiec zostaje pokryta, tylko gdzie indziej.

    python tools/fill_gate.py                   # porownaj z baseline
    python tools/fill_gate.py --write-baseline  # zapisz nowy baseline
"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from parity import config_fingerprint  # noqa: E402

DEFAULT_BASELINE = ROOT / "tests" / "baselines" / "fill_gate.json"

# Zlecenie wzorcowe: 10 kontraktow, market, LONG.
REQUESTED = 10.0
CID = "CE-TEST-1"


def _round(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    if number != number or number in (float("inf"), float("-inf")):
        return str(number)
    return round(number, 10)


class _FakeSession:
    """Executor ustawia naglowki i adaptery; sieci tu nie ma."""

    def __init__(self):
        self.headers = {}

    def mount(self, *_a, **_k):
        return None


class _FakeRegistry:
    """Registry nie jest uzywane przez refresh_order - ma tylko istniec."""

    def quantize_size(self, *_a, **_k):
        return None


def _make_order(**overrides):
    from order_models import Order, OrderState
    kw = dict(
        client_order_id=CID, symbol="BTC", inst_id="BTC-USDT",
        side="buy", direction="LONG", order_type="market", size=REQUESTED,
        state=OrderState.SUBMITTED, order_id="EX-1", decision_ts_ms=1_000_000,
    )
    kw.update(overrides)
    return Order(**kw)


def _order_snapshot(order) -> dict:
    """Stan `Order` po kroku. `events_qty` obok `filled` celowo - rozjazd tych
    dwoch liczb znaczy, ze ksiega ma ilosc bez sladu po transakcji."""
    events = list(order.fill_events or [])
    return {
        "state": order.state.value if hasattr(order.state, "value") else str(order.state),
        "filled": _round(order.filled_size),
        "avg_price": _round(order.avg_fill_price),
        "fee": _round(order.fee),
        "role": order.liquidity_role,
        "events": len(events),
        "events_qty": _round(sum(float(e.get("quantity") or 0) for e in events)),
        "timeout": bool(order.timeout),
        "last_error": order.last_error,
    }


def _ledger_snapshot(lifecycle) -> dict:
    aggregate = lifecycle.fill_summary
    return {
        "status": lifecycle.status.value,
        "filled": _round(aggregate.quantity),
        "vwap": _round(aggregate.vwap),
        "fee": _round(aggregate.fee),
        "role": aggregate.liquidity_role,
        "events": aggregate.fill_count,
        "remaining": _round(lifecycle.remaining_quantity),
    }


def _truth_snapshot(fills) -> dict:
    """Prawda ziemi zsumowana do tego kroku wlacznie."""
    quantity = sum(float(f["qty"]) for f in fills)
    notional = sum(float(f["qty"]) * float(f["price"]) for f in fills)
    fee = sum(float(f.get("fee") or 0) for f in fills)
    roles = {f.get("role") for f in fills if f.get("role")}
    return {
        "filled": _round(quantity),
        "avg_price": _round(notional / quantity) if quantity else None,
        "fee": _round(fee),
        "role": (next(iter(roles)) if len(roles) == 1 else ("mixed" if roles else None)),
        "events": len(fills),
    }


def _vs_truth(state: dict, truth: dict, price_key: str) -> list:
    """Czym dana ksiega rozni sie od prawdy. Pusta lista = odtworzyla wiernie."""
    problems = []
    if state["filled"] != truth["filled"]:
        problems.append(f"ilosc {state['filled']} zamiast {truth['filled']}")
    if state[price_key] != truth["avg_price"]:
        problems.append(f"cena {state[price_key]} zamiast {truth['avg_price']}")
    if state["fee"] != truth["fee"]:
        problems.append(f"fee {state['fee']} zamiast {truth['fee']}")
    if state["role"] != truth["role"]:
        problems.append(f"rola {state['role']} zamiast {truth['role']}")
    if state["events"] != truth["events"]:
        problems.append(f"zdarzen {state['events']} zamiast {truth['events']}")
    return problems


def _row(filled, avg=100.0, state="partially_filled", fee=0.0, role=None, **extra):
    row = {"orderId": "EX-1", "clientOrderId": CID, "state": state,
           "filledSize": filled, "averagePrice": avg, "fee": fee}
    if role:
        row["liquidityRole"] = role
    row.update(extra)
    return row


def _ok(row):
    return {"ok": True, "timeout": False, "data": [row]}


TIMEOUT = {"ok": False, "timeout": True, "data": None}
EMPTY = {"ok": True, "timeout": False, "data": []}
FOREIGN = {"ok": True, "timeout": False, "data": [
    {"orderId": "EX-999", "clientOrderId": "CE-INNE", "state": "filled",
     "filledSize": 10, "averagePrice": 100},
]}


def _step(resp, fill=None):
    """Jeden odczyt z gieldy plus PRAWDA o tym, co sie w nim wydarzylo."""
    return {"resp": resp, "fill": fill}


def _fill(qty, price, fee=0.0, role=None):
    return {"qty": qty, "price": price, "fee": fee, "role": role}


def _case(name, steps, **kw):
    case = {"case": name, "steps": steps, "order": {}, "fee_mode": "cumulative"}
    case.update(kw)
    return case


def build_corpus() -> list:
    cases = []
    add = cases.append

    # --- sciezka szczesliwa ---
    add(_case("single_full_fill", [
        _step(_ok(_row(10, 100.0, "filled", fee=0.6, role="taker")),
              _fill(10, 100.0, 0.6, "taker")),
    ]))
    add(_case("two_partials_at_one_price", [
        _step(_ok(_row(4, 100.0, fee=0.24, role="taker")), _fill(4, 100.0, 0.24, "taker")),
        _step(_ok(_row(7, 100.0, fee=0.42, role="taker")), _fill(3, 100.0, 0.18, "taker")),
        _step(_ok(_row(10, 100.0, "filled", fee=0.6, role="taker")), _fill(3, 100.0, 0.18, "taker")),
    ]))
    # Cena rusza sie miedzy fillami. Wiersz zlecenia niesie `averagePrice`
    # calego zlecenia (narastajaco), a `refresh_order` podaje ja do
    # `record_fill` jako cene PRZYROSTU. Prawdziwy VWAP to 100.9; obie ksiegi
    # wychodza na 100.3986, bo z samego wiersza zlecenia ceny pojedynczego
    # fillu ODTWORZYC SIE NIE DA. To nie jest blad implementacji, tylko brak
    # danych: potrzebny jest strumien transakcji, nie migawka zlecenia.
    add(_case("partial_fills_at_moving_prices", [
        _step(_ok(_row(4, 100.0, fee=0.24, role="taker")), _fill(4, 100.0, 0.24, "taker")),
        _step(_ok(_row(7, 100.4285714286, fee=0.42, role="taker")), _fill(3, 101.0, 0.18, "taker")),
        _step(_ok(_row(10, 100.9, "filled", fee=0.6, role="taker")), _fill(3, 102.0, 0.18, "taker")),
    ]))

    # --- partial, ktory nigdy sie nie domyka ---
    add(_case("partial_never_completes", [
        _step(_ok(_row(3, 100.0, fee=0.18, role="taker")), _fill(3, 100.0, 0.18, "taker")),
        _step(_ok(_row(3, 100.0, fee=0.18, role="taker"))),
    ]))

    # --- gielda podaje ilosc BEZ ceny sredniej ---
    add(_case("quantity_without_average_price", [
        _step(_ok(_row(5, None, fee=0.3, role="taker")), _fill(5, 100.0, 0.3, "taker")),
    ]))
    add(_case("quantity_without_price_then_price", [
        _step(_ok(_row(5, None, fee=0.3, role="taker")), _fill(5, 100.0, 0.3, "taker")),
        _step(_ok(_row(10, 100.0, "filled", fee=0.6, role="taker")), _fill(5, 100.0, 0.3, "taker")),
    ]))

    # --- idempotencja: ten sam wiersz podany trzy razy ---
    add(_case("same_row_redelivered", [
        _step(_ok(_row(6, 100.0, fee=0.36, role="taker")), _fill(6, 100.0, 0.36, "taker")),
        _step(_ok(_row(6, 100.0, fee=0.36, role="taker"))),
        _step(_ok(_row(6, 100.0, fee=0.36, role="taker"))),
    ]))

    # --- gielda cofa ilosc ---
    add(_case("filled_size_goes_backwards", [
        _step(_ok(_row(8, 100.0, fee=0.48, role="taker")), _fill(8, 100.0, 0.48, "taker")),
        _step(_ok(_row(3, 100.0, fee=0.48, role="taker"))),
    ]))

    # --- przepelnienie ponad zamowiona ilosc ---
    add(_case("overfill_beyond_requested", [
        _step(_ok(_row(12, 100.0, "filled", fee=0.72, role="taker")),
              _fill(12, 100.0, 0.72, "taker")),
    ]))

    # --- rola plynnosci ---
    # Zlecenie market ZDEJMUJE plynnosc - "taker" to fakt, nie zgadywanie,
    # wiec prawda ziemi ma tu role. `refresh_order` ja dopisuje, `FillLedger`
    # dostaje z wiersza None i tak zostaje: to nie blad ksiegi, tylko brak
    # wzbogacenia, ktory nowa sciezka musialaby odtworzyc.
    add(_case("role_missing_on_market_order", [
        _step(_ok(_row(10, 100.0, "filled", fee=0.6)), _fill(10, 100.0, 0.6, "taker")),
    ]))
    # Zlecenie limit moze byc maker ALBO taker - bez informacji z gieldy nie
    # wolno wybrac. Obie ksiegi zostawiaja None i obie maja racje.
    add(_case("role_missing_on_limit_order", [
        _step(_ok(_row(10, 100.0, "filled", fee=0.6)), _fill(10, 100.0, 0.6)),
    ], order={"order_type": "limit", "price": 100.0}))
    add(_case("role_changes_between_fills", [
        _step(_ok(_row(5, 100.0, fee=0.3, role="maker")), _fill(5, 100.0, 0.3, "maker")),
        _step(_ok(_row(10, 100.0, "filled", fee=0.6, role="taker")), _fill(5, 100.0, 0.3, "taker")),
    ]))

    # --- konwencja prowizji ---
    add(_case("fee_reported_cumulatively", [
        _step(_ok(_row(5, 100.0, fee=0.30, role="taker")), _fill(5, 100.0, 0.30, "taker")),
        _step(_ok(_row(10, 100.0, "filled", fee=0.60, role="taker")), _fill(5, 100.0, 0.30, "taker")),
    ]))
    add(_case("fee_reported_incrementally", [
        _step(_ok(_row(5, 100.0, fee=0.30, role="taker")), _fill(5, 100.0, 0.30, "taker")),
        _step(_ok(_row(10, 100.0, "filled", fee=0.30, role="taker")), _fill(5, 100.0, 0.30, "taker")),
    ], fee_mode="incremental"))

    # --- awarie transportu: nie wolno ich czytac jako "brak fillu" ---
    add(_case("refresh_timeout", [_step(TIMEOUT)]))
    add(_case("partial_then_timeout", [
        _step(_ok(_row(4, 100.0, fee=0.24, role="taker")), _fill(4, 100.0, 0.24, "taker")),
        _step(TIMEOUT),
    ]))
    add(_case("empty_response", [_step(EMPTY)]))
    add(_case("foreign_order_row_is_not_matched", [_step(FOREIGN)]))
    add(_case("partial_then_foreign_row", [
        _step(_ok(_row(4, 100.0, fee=0.24, role="taker")), _fill(4, 100.0, 0.24, "taker")),
        _step(FOREIGN),
    ]))

    # --- alternatywne nazwy pol z gieldy ---
    add(_case("alternative_field_names", [
        _step({"ok": True, "timeout": False, "data": [{
            "ordId": "EX-1", "clOrdId": CID, "state": "filled",
            "accFillSz": 10, "avgPx": 100.0, "fillFee": 0.6, "execType": "taker",
        }]}, _fill(10, 100.0, 0.6, "taker")),
    ]))

    # --- anulowane po czesciowym wypelnieniu ---
    add(_case("canceled_after_partial", [
        _step(_ok(_row(4, 100.0, fee=0.24, role="taker")), _fill(4, 100.0, 0.24, "taker")),
        _step(_ok(_row(4, 100.0, "canceled", fee=0.24, role="taker"))),
    ]))

    # --- kontrakt OrderLifecycle: fill na zleceniu, ktore nie zostalo wyslane ---
    add(_case("fill_before_lifecycle_was_accepted", [
        _step(_ok(_row(4, 100.0, fee=0.24, role="taker")), _fill(4, 100.0, 0.24, "taker")),
    ], accept_lifecycle=False))

    return cases


def _feed_ledger(lifecycle, row, index, fee_mode, seen_fee):
    """Co z tego wiersza wyciagnalby WIERNY adapter piszacy do `FillLedger`.

    Ilosc liczona tak samo jak w `refresh_order` - z roznicy - bo wiersz
    zlecenia nie niesie `trade_id` i innej drogi nie ma. Prowizja wedlug
    konwencji zadeklarowanej w przypadku: adapter, ktory zna swoja gielde,
    wie, czy `fee` jest narastajace, czy przyrostowe. `refresh_order` tego
    nie wie i zawsze zaklada narastajace.
    """
    from cryptoedge.execution.ledger import Fill

    if not row:
        return {"skipped": "brak wiersza"}, seen_fee
    try:
        filled = Decimal(str(row.get("filledSize") or row.get("accFillSz")
                             or row.get("fillSz") or 0))
    except Exception:
        return {"skipped": "zly filledSize"}, seen_fee
    delta = filled - lifecycle.fill_summary.quantity
    if delta <= 0:
        return {"skipped": "brak przyrostu"}, seen_fee
    price = row.get("averagePrice") or row.get("avgPx") or row.get("fillPx")
    if not price:
        # Gielda nie podala ceny. Wierny adapter NIE ZMYSLA jej - odklada fakt
        # do czasu, az cena bedzie znana. `Fill` wymaga dodatniej ceny wlasnie
        # po to, zeby taki brak nie wszedl do ksiegi jako zero.
        return {"skipped": "brak ceny w wierszu"}, seen_fee

    raw_fee = float(row.get("fee") or row.get("fillFee") or 0)
    fee = raw_fee - seen_fee if fee_mode == "cumulative" else raw_fee
    role = str(row.get("liquidityRole") or row.get("execType")
               or row.get("fillRole") or "").lower() or None
    try:
        lifecycle.apply_fill(Fill(
            trade_id=f"T{index}", client_order_id=lifecycle.client_order_id,
            quantity=delta, price=Decimal(str(price)),
            fee=Decimal(str(fee)), liquidity_role=role,
        ))
        return {"applied": True}, raw_fee if fee_mode == "cumulative" else seen_fee + raw_fee
    except Exception as exc:
        return ({"raised": f"{type(exc).__name__}: {str(exc)[:90]}"},
                raw_fee if fee_mode == "cumulative" else seen_fee + raw_fee)


def evaluate(case: dict) -> dict:
    from blofin_executor import BloFinExecutor
    from cryptoedge.execution.lifecycle import OrderLifecycle, OrderStatus

    out = {"case": case["case"], "steps": []}
    try:
        executor = BloFinExecutor(registry=_FakeRegistry(), session=_FakeSession())
        order = _make_order(**(case.get("order") or {}))
        executor.orders[order.client_order_id] = order
        lifecycle = OrderLifecycle(
            client_order_id=order.client_order_id,
            requested_quantity=Decimal(str(REQUESTED)),
        )
        if case.get("accept_lifecycle", True):
            lifecycle.transition(OrderStatus.SUBMITTING, "gate")
            lifecycle.transition(OrderStatus.ACCEPTED, "gate")

        truth_fills, seen_fee = [], 0.0
        for index, step in enumerate(case["steps"]):
            response = step["resp"]
            executor._request = lambda *_a, **_k: dict(response)
            executor.refresh_order(order)

            rows = response.get("data") or []
            row = rows[0] if rows else {}
            # Cudzy wiersz nie jest fillem tego zlecenia - druga ksiega tez go
            # nie moze dostac, inaczej porownanie byloby oszustwem.
            if str(row.get("clientOrderId") or row.get("clOrdId") or "") not in ("", CID):
                row = {}
            ledger_step, seen_fee = _feed_ledger(
                lifecycle, row, index, case["fee_mode"], seen_fee)

            if step.get("fill"):
                truth_fills.append(step["fill"])
            truth = _truth_snapshot(truth_fills)
            order_state = _order_snapshot(order)
            ledger_state = _ledger_snapshot(lifecycle)
            out["steps"].append({
                "n": index,
                "truth": truth,
                "order": order_state,
                "ledger": ledger_state,
                "ledger_step": ledger_step,
                "order_vs_truth": _vs_truth(order_state, truth, "avg_price"),
                "ledger_vs_truth": _vs_truth(ledger_state, truth, "vwap"),
            })

        final = out["steps"][-1] if out["steps"] else {}
        order_state = final.get("order") or {}
        out["summary"] = {
            "order_vs_truth": final.get("order_vs_truth") or [],
            "ledger_vs_truth": final.get("ledger_vs_truth") or [],
            # Ilosc bez sladu po transakcji: ksiega wie ILE, nie wie SKAD.
            "events_desync": bool(
                order_state.get("filled") != order_state.get("events_qty")),
        }
    except Exception as exc:
        out["raised"] = f"RAISED:{type(exc).__name__}: {str(exc)[:160]}"
    return out


def run_gate() -> dict:
    results = [evaluate(case) for case in build_corpus()]

    def _flagged(key):
        return sorted(r["case"] for r in results if (r.get("summary") or {}).get(key))

    order_wrong, ledger_wrong = _flagged("order_vs_truth"), _flagged("ledger_vs_truth")
    desync = _flagged("events_desync")
    raised = [r["case"] for r in results if r.get("raised")]
    return {
        "meta": {"cases": len(results), "order_wrong": len(order_wrong),
                 "ledger_wrong": len(ledger_wrong), "events_desync": len(desync),
                 "raised": len(raised)},
        "config": config_fingerprint(),
        "order_wrong_cases": order_wrong,
        "ledger_wrong_cases": ledger_wrong,
        "events_desync_cases": desync,
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
    for name in sorted(set(old_map) - set(new_map)):
        problems.append(f"ZNIKNAL PRZYPADEK: {name}")
    for name in sorted(set(new_map) - set(old_map)):
        problems.append(f"NOWY PRZYPADEK: {name}")
    for name in sorted(set(old_map) & set(new_map)):
        old, new = old_map[name], new_map[name]
        for key in sorted(set(old) | set(new)):
            if old.get(key) != new.get(key):
                problems.append(
                    f"{name}.{key}: {json.dumps(old.get(key), ensure_ascii=False)} "
                    f"-> {json.dumps(new.get(key), ensure_ascii=False)}"
                )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Bramka charakterystyki fillow")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    args = parser.parse_args()

    current = json.loads(json.dumps(run_gate()))
    meta = current["meta"]
    print(f"[fill] przypadkow {meta['cases']} | Order myli sie w {meta['order_wrong']}"
          f" | ksiega w {meta['ledger_wrong']} | rozjazd zdarzen {meta['events_desync']}"
          f" | wyjatkow {meta['raised']} | config {current['config'].get('hash')}")

    if args.write_baseline:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(
            json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[fill] zapisano baseline: {args.baseline}")
        return 0

    if not args.baseline.is_file():
        print(f"[fill] BRAK baseline ({args.baseline}). "
              f"Utworz: python tools/fill_gate.py --write-baseline")
        return 2

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    problems = compare(baseline, current)
    if not problems:
        print("[fill] IDENTYCZNIE — ksiegowanie fillow bez zmian.")
        return 0
    print(f"[fill] ROZNI SIE ({len(problems)} pozycji):")
    for line in problems[:60]:
        print(line)
    if len(problems) > 60:
        print(f"  ... i {len(problems) - 60} wiecej")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
