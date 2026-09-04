"""Bramka charakterystyki dla RESTARTU.

Warunek wyjscia etapu 5 brzmi: "testy restartu z pozycja, orphan orderem,
partialem i brakujacym SL; LIVE failure wymusza REDUCE_ONLY zamiast pustego
stanu". Te cztery scenariusze mialy dotad pokrycie czesciowe i na poziomie
rekoncyliatora, a nie calego `RestartRecovery.run()`.

Bramka pinuje to, co restart NAPRAWDE robi:
  - pelny raport `run()` (bez `ts`, ktore nie jest deterministyczne),
  - stan ryzyka po restarcie (halt / pause / powod),
  - kazde wywolanie `attach_protection` z argumentami,
  - anulowanie sierot i odswiezanie zlecen przez executor.

Uwaga na `KILL_SWITCH`: `run()` czyta plik z katalogu repo. Bramka NIE tworzy
tego pliku - podmienia `restart_recovery.Path`, zeby scenariusz kill-switcha
dalo sie pokryc bez dotykania dysku. Utworzenie tego pliku zatrzymaloby bota.

ZNANE ROZBIEZNOSCI (zmierzone, nie naprawiane w tej bramce):
  - PARTIAL: przy `partial_fill_exchange_smaller_than_local` ochrona zostaje
    uzbrojona na rozmiar Z GIELDY (1.0), bo `restart_recovery` czyta lokalny
    `size_contracts` tylko gdy gielda nie podala nic. Kierunek jest bezpieczny
    - chronimy to, co istnieje. Ale ksiega PAPER dalej trzyma 3.0 i NIKT jej
    nie koryguje, wiec po restarcie bot uwaza, ze ma wiecej niz ma naprawde.
    Domkniecie tego to praca nad ksiega oparta na fillach (dalsza czesc
    etapu 5), a nie zmiana w samym restarcie.
  - `exchange_position_query_failure` nie anuluje sierot w ogole: blad zapytania
    o pozycje przerywa caly blok, wiec `cancel_orphan_orders` nie jest wolane.

    python tools/restart_gate.py                   # porownaj z baseline
    python tools/restart_gate.py --write-baseline  # zapisz nowy baseline
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

DEFAULT_BASELINE = ROOT / "tests" / "baselines" / "restart_gate.json"

_MISSING = object()

# Konfiguracja wymuszona dla kazdego przypadku - bramka ma badac restart,
# a nie to, jak akurat stoi config uzytkownika.
FORCED_CONFIG = {
    "ALERTS_ENABLED": False,
    "ALERT_PUSH": False,
    "ALERT_SOUND": False,
    "RECOVERY_REATTACH_EXCHANGE_SL": True,
    "RECOVERY_WARN_ORPHANS": True,
    "AUTO_CANCEL_ORPHAN_ORDERS": True,
    "STOP_LOSS_PCT": -22.0,
    "LEVERAGE": 10,
}


def _round(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    if number != number or number in (float("inf"), float("-inf")):
        return str(number)
    return round(number, 8)


class RecordingProtection:
    """Atrapa warstwy ochronnej. Nagrywa kazde uzbrojenie SL."""

    def __init__(self, state=None, kill_active=False, kill_reason=""):
        self.by_key = dict(state or {})
        self.kill_switch_active = bool(kill_active)
        self.kill_reason = kill_reason
        self.calls = []
        self.loaded = 0

    @staticmethod
    def _key(symbol, direction):
        return f"{str(symbol).upper()}|{str(direction).upper()}"

    def load_state(self):
        self.loaded += 1

    def is_killed(self):
        return bool(self.kill_switch_active)

    def clear_kill_switch(self):
        self.kill_switch_active = False
        self.kill_reason = ""
        self.calls.append({"op": "clear_kill_switch"})
        return "cleared"

    def attach_protection(self, symbol, direction, sl_price=None,
                          size_contracts=None, entry_price=None,
                          place_exchange=False, **extra):
        self.calls.append({
            "op": "attach", "symbol": symbol, "direction": direction,
            "sl": _round(sl_price), "size": _round(size_contracts),
            "entry": _round(entry_price), "place_exchange": bool(place_exchange),
        })
        self.by_key[self._key(symbol, direction)] = _Attachment(sl_price, size_contracts)

    def update_exchange_sl(self, symbol, direction, new_sl=None, size_contracts=None):
        self.calls.append({
            "op": "update_sl", "symbol": symbol, "direction": direction,
            "sl": _round(new_sl), "size": _round(size_contracts),
        })


class _Attachment:
    def __init__(self, sl_price=None, size_contracts=None):
        self.sl_price = sl_price
        self.size_contracts = size_contracts


class RecordingRisk:
    def __init__(self, halted=False, paused=False, halt_reason=None):
        self.is_halted = halted
        self.paused = paused
        self.halt_reason = halt_reason
        # Stan, ktory decyduje o wpuszczaniu nowych wejsc. `can_open_position`
        # odrzuca sygnal na REDUCE_ONLY zanim sprawdzi cokolwiek innego.
        self.risk_state = "NORMAL"
        self.reduce_only_reason = None


class FakePosition:
    def __init__(self, symbol, direction, sl_price=None, entry_price=None,
                 size_contracts=None):
        self.symbol = symbol
        self.direction = direction
        self.sl_price = sl_price
        self.entry_price = entry_price
        self.size_contracts = size_contracts
        self.on_sl_updated = None


class FakeTrader:
    def __init__(self, positions=()):
        self.positions = list(positions)


class RecordingExecutor:
    """Executor, ktory tylko zapamietuje - nic nie wysyla."""

    def __init__(self, orders=None, cancel_result=None, cancel_raises=False):
        self.orders = dict(orders or {})
        self.calls = []
        self._cancel_result = cancel_result if cancel_result is not None else []
        self._cancel_raises = cancel_raises

    def cancel_orphan_orders(self, active_symbols=None, client_prefix="CE"):
        self.calls.append({"op": "cancel_orphans", "active": list(active_symbols or [])})
        if self._cancel_raises:
            raise RuntimeError("cancel failed")
        return list(self._cancel_result)

    def refresh_order(self, order):
        self.calls.append({"op": "refresh", "id": getattr(order, "client_order_id", "?"),
                           "state": str(getattr(order, "state", ""))})


class FakeOrder:
    def __init__(self, client_order_id, state="SUBMITTED", timeout=False):
        self.client_order_id = client_order_id
        self.state = state
        self.timeout = timeout


class RecordingReconciler:
    """Rekoncyliator sterowany scenariuszem.

    `exchange_positions` moze byc lista albo wyjatkiem - drugie odwzorowuje
    awarie zapytania o pozycje, ktorej NIE wolno czytac jako "plasko".
    """

    def __init__(self, exchange_positions=(), report=None, raises=None):
        self._positions = exchange_positions
        self._report = report
        self._raises = raises
        self.calls = []

    def fetch_exchange_positions(self):
        self.calls.append({"op": "fetch_positions"})
        if isinstance(self._positions, Exception):
            raise self._positions
        return list(self._positions)

    def _norm_symbol(self, item):
        raw = item.get("symbol") if isinstance(item, dict) else getattr(item, "symbol", "")
        return str(raw or "").upper().split("-")[0]

    def reconcile(self, positions, executor=None, protection=None):
        self.calls.append({"op": "reconcile", "local": len(list(positions or []))})
        if isinstance(self._raises, Exception):
            raise self._raises
        return dict(self._report or {"in_sync": True})

    def summary_text(self, report):
        return f"[recon] in_sync={report.get('in_sync')}"


class _FakeKillPath:
    """Podmiana za `Path` w restart_recovery - bez dotykania dysku."""

    def __init__(self, exists=False, text=""):
        self._exists = exists
        self._text = text

    def __call__(self, *_a, **_k):
        return self

    def resolve(self):
        return self

    @property
    def parent(self):
        return self

    def __truediv__(self, _other):
        return self

    def exists(self):
        return self._exists

    def read_text(self, encoding=None):
        return self._text


def _case(name, **kw):
    base = {
        "case": name,
        "local": [],            # pozycje w ksiedze PAPER
        "exchange": [],         # pozycje widziane na gieldzie
        "reconcile": None,      # raport rekoncyliacji
        "orders": {},           # zlecenia w executorze
        "protection_state": {},
        "kill_active": False,
        "kill_reason": "",
        "kill_file": None,      # (exists, text)
        "cancel_result": [],
        "config": {},
    }
    base.update(kw)
    return base


def build_corpus() -> list:
    cases = []
    add = cases.append
    LIVE = {"PAPER_TRADING": False, "LIVE_EXECUTION_ENABLED": True}
    pos = lambda s, d, sl=96.0, e=100.0, c=1.0: FakePosition(s, d, sl, e, c)  # noqa: E731

    # --- restart bez niczego ---
    add(_case("clean_paper_no_positions"))
    add(_case("clean_live_no_positions", config=LIVE))

    # --- WARUNEK WYJSCIA 1: restart z pozycja ---
    add(_case("local_position_only", local=[pos("BTC", "LONG")]))
    add(_case("position_on_both_sides", local=[pos("BTC", "LONG")],
              exchange=[{"symbol": "BTC-USDT", "direction": "LONG",
                         "contracts": 1.0, "avg_price": 100.0}],
              config=LIVE, reconcile={"in_sync": True}))
    add(_case("exchange_position_without_local", config=LIVE,
              exchange=[{"symbol": "ETH-USDT", "direction": "SHORT",
                         "contracts": 2.0, "avg_price": 200.0}],
              reconcile={"in_sync": False,
                         "only_exchange": [{"symbol": "ETH", "direction": "SHORT", "size": 2.0}]}))

    # --- WARUNEK WYJSCIA 2: orphan order ---
    add(_case("orphan_orders_are_cancelled", config=LIVE,
              local=[pos("BTC", "LONG")],
              exchange=[{"symbol": "BTC-USDT", "direction": "LONG",
                         "contracts": 1.0, "avg_price": 100.0}],
              reconcile={"in_sync": True},
              cancel_result=[{"orderId": "X1"}, {"orderId": "X2"}]))
    add(_case("orphan_cancel_failure_is_recorded_not_swallowed", config=LIVE,
              local=[pos("BTC", "LONG")],
              exchange=[{"symbol": "BTC-USDT", "direction": "LONG", "contracts": 1.0}],
              reconcile={"in_sync": True}, cancel_result=RuntimeError("boom")))

    # --- WARUNEK WYJSCIA 3: partial ---
    # Gielda pokazuje mniej kontraktow niz ksiega lokalna. Restart musi
    # uzbroic SL na ROZMIAR Z GIELDY, nie na lokalny.
    add(_case("partial_fill_exchange_smaller_than_local", config=LIVE,
              local=[pos("BTC", "LONG", sl=96.0, e=100.0, c=3.0)],
              exchange=[{"symbol": "BTC-USDT", "direction": "LONG",
                         "contracts": 1.0, "avg_price": 100.0}],
              reconcile={"in_sync": False,
                         "size_mismatch": [{"symbol": "BTC", "local": 3.0, "exchange": 1.0}]}))
    add(_case("partial_order_state_is_refreshed", config=LIVE,
              orders={"C1": FakeOrder("C1", "PARTIAL"),
                      "C2": FakeOrder("C2", "FILLED"),
                      "C3": FakeOrder("C3", "TIMEOUT")}))

    # --- WARUNEK WYJSCIA 4: brakujacy SL ---
    add(_case("missing_sl_falls_back_to_percent", config=LIVE,
              exchange=[{"symbol": "SOL-USDT", "direction": "LONG",
                         "contracts": 5.0, "avg_price": 100.0}],
              reconcile={"in_sync": True}))
    add(_case("local_position_without_sl_is_not_armed",
              local=[FakePosition("XRP", "LONG", sl_price=None, entry_price=1.0)]))
    add(_case("persisted_trailing_sl_is_never_weakened", config=LIVE,
              local=[pos("BTC", "LONG", sl=96.0, e=100.0, c=1.0)],
              exchange=[{"symbol": "BTC-USDT", "direction": "LONG",
                         "contracts": 1.0, "avg_price": 100.0}],
              protection_state={"BTC|LONG": _Attachment(99.0, 1.0)},
              reconcile={"in_sync": True}))

    # --- awarie: nie wolno ich czytac jako "plasko" ---
    add(_case("exchange_position_query_failure", config=LIVE,
              local=[pos("BTC", "LONG")],
              exchange=RuntimeError("HTTP 500"),
              reconcile={"in_sync": True}))
    add(_case("reconcile_failure_is_recorded", config=LIVE,
              local=[pos("BTC", "LONG")],
              reconcile=RuntimeError("recon down")))
    # Ta sama awaria w PAPER nie moze blokowac wejsc - nie ma gieldy,
    # ktorej stanu nie potwierdzilismy. Asymetria jest cala pointa.
    add(_case("paper_reconcile_failure_does_not_block_entries",
              config={"RECOVERY_RECONCILE_IN_PAPER": True},
              local=[pos("BTC", "LONG")],
              reconcile=RuntimeError("recon down")))

    # --- kill switch ---
    add(_case("kill_switch_from_file_halts", kill_file=(True, "manual_ops")))
    add(_case("paper_leftover_close_all_is_cleared",
              kill_active=True, kill_reason="manual_close_all"))
    add(_case("live_kill_switch_stays_halted", config=LIVE,
              kill_active=True, kill_reason="disaster"))

    return cases


def evaluate(case: dict) -> dict:
    import config
    import restart_recovery as rr

    out = {"case": case["case"]}
    overrides = dict(FORCED_CONFIG)
    overrides.update(case.get("config") or {})
    saved = {key: getattr(config, key, _MISSING) for key in overrides}
    real_path = rr.Path
    try:
        for key, value in overrides.items():
            setattr(config, key, value)
        if case.get("kill_file"):
            exists, text = case["kill_file"]
            rr.Path = _FakeKillPath(exists, text)
        else:
            rr.Path = _FakeKillPath(False, "")

        protection = RecordingProtection(
            case.get("protection_state"), case.get("kill_active"),
            case.get("kill_reason"),
        )
        risk = RecordingRisk()
        trader = FakeTrader(case.get("local") or [])
        cancel = case.get("cancel_result")
        executor = RecordingExecutor(
            orders=case.get("orders"),
            cancel_result=[] if isinstance(cancel, Exception) else cancel,
            cancel_raises=isinstance(cancel, Exception),
        )
        rec = case.get("reconcile")
        reconciler = RecordingReconciler(
            exchange_positions=case.get("exchange") or [],
            report=None if isinstance(rec, Exception) else rec,
            raises=rec if isinstance(rec, Exception) else None,
        )

        recovery = rr.RestartRecovery(
            risk=risk, trader=trader, logger=None, protection=protection,
            reconciler=reconciler, executor=executor, account_sync=None,
        )
        report = recovery.run()

        report = dict(report)
        report.pop("ts", None)          # zegar nie jest deterministyczny
        report["errors"] = [str(e).split(":")[0] for e in report.get("errors") or []]
        out["report"] = report
        out["risk"] = {
            "halted": bool(risk.is_halted), "paused": bool(risk.paused),
            "halt_reason": risk.halt_reason,
            "risk_state": risk.risk_state,
            "reduce_only_reason": risk.reduce_only_reason,
        }
        out["protection_calls"] = protection.calls
        out["executor_calls"] = executor.calls
        out["reconciler_calls"] = reconciler.calls
        out["sl_callback_attached"] = sorted(
            p.symbol for p in trader.positions if getattr(p, "on_sl_updated", None)
        )
    except Exception as exc:
        out["raised"] = f"RAISED:{type(exc).__name__}: {str(exc)[:160]}"
    finally:
        rr.Path = real_path
        for key, value in saved.items():
            if value is _MISSING:
                if hasattr(config, key):
                    delattr(config, key)
            else:
                setattr(config, key, value)
    return out


def run_gate() -> dict:
    cases = build_corpus()
    results = [evaluate(case) for case in cases]
    halted = sum(1 for r in results if (r.get("risk") or {}).get("halted"))
    rearmed = sum(len([c for c in (r.get("protection_calls") or [])
                       if c.get("op") == "attach"]) for r in results)
    reduce_only = sum(1 for r in results
                      if (r.get("risk") or {}).get("risk_state") == "REDUCE_ONLY")
    raised = [r["case"] for r in results if r.get("raised")]
    errors = sorted({e for r in results for e in ((r.get("report") or {}).get("errors") or [])})
    return {
        "meta": {"cases": len(results), "halted": halted, "sl_rearmed": rearmed,
                 "reduce_only": reduce_only,
                 "distinct_errors": len(errors), "raised": len(raised)},
        "config": config_fingerprint(),
        "errors": errors,
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
    parser = argparse.ArgumentParser(description="Bramka charakterystyki restartu")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    args = parser.parse_args()

    current = json.loads(json.dumps(run_gate()))
    meta = current["meta"]
    print(f"[restart] przypadkow {meta['cases']} | halt {meta['halted']}"
          f" | reduce_only {meta['reduce_only']}"
          f" | uzbrojen SL {meta['sl_rearmed']} | roznych bledow {meta['distinct_errors']}"
          f" | wyjatkow {meta['raised']} | config {current['config'].get('hash')}")

    if args.write_baseline:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(
            json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[restart] zapisano baseline: {args.baseline}")
        return 0

    if not args.baseline.is_file():
        print(f"[restart] BRAK baseline ({args.baseline}). "
              f"Utworz: python tools/restart_gate.py --write-baseline")
        return 2

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    problems = compare(baseline, current)
    if not problems:
        print("[restart] IDENTYCZNIE — restart bez zmian.")
        return 0
    print(f"[restart] ROZNI SIE ({len(problems)} pozycji):")
    for line in problems[:60]:
        print(line)
    if len(problems) > 60:
        print(f"  ... i {len(problems) - 60} wiecej")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
