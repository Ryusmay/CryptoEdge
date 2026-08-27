# -*- coding: utf-8 -*-
"""Bramka charakterystyki dla WYKONANIA wejscia.

`risk_gate.py` pilnuje **decyzji** - czy wolno wejsc i jak duzo. Ta bramka
pilnuje **wykonania**: co naprawde robi `PaperTrader.open_position()` od
sygnalu do powstalej pozycji. Miedzy jednym a drugim jest polowa spreadu,
shadow mode, sizing, rezerwacja slotu, kolejka limitow, kwantyzacja do lot
size i konstrukcja Position.

Dlaczego osobna bramka. Cztery bledy naprawione w v20.23.0 - v20.26.0 mialy
wspolna przyczyne: `calculate_position_size()` i `can_open_position()` mutuja
ten sam slownik na przemian, a `open_position()` uzywa rozmiaru policzonego
PRZED bramka. Zadna z istniejacych bramek tego nie widziala, bo obie patrza
na pojedyncze wywolanie, nie na sekwencje.

Dlatego kluczowe pole tego baseline to `signal_mutations`: pelna lista
kluczy, ktore `open_position()` dopisuje do sygnalu, z wartosciami. Zmiana
kolejnosci mutacji zmieni te liste, nawet gdy werdykt zostanie ten sam.

    python tools/entry_gate.py                   # porownaj z baseline
    python tools/entry_gate.py --write-baseline  # zapisz punkt odniesienia
    python tools/entry_gate.py --coverage        # jakie wyniki pokrywa korpus
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
from exit_gate import (  # noqa: E402  - ta sama maszyneria determinizmu
    FakeClock, RecordingLogger, _install_stubs, _restore_stubs, _round,
    _fresh_risk, _MISSING,
)

DEFAULT_BASELINE = ROOT / "tests" / "baselines" / "entry_gate.json"

FORCED_CONFIG = {
    "FUNDING_ACCRUAL": False,
    "ALERTS_ENABLED": False,
    "ALERT_PUSH": False,
    "ALERT_SOUND": False,
}

# Klucze wpisywane przez sam korpus - nie sa mutacja open_position().
_INPUT_KEYS = frozenset({
    "symbol", "direction", "price", "strength", "trend_score", "reversal_score",
    "engine", "strategy_mode", "sl_price", "tp_price", "tp1_price", "tp2_price",
    "market_regime", "atr_pct", "leverage", "expected_net_r",
    "expected_r_status", "order_book", "mtf", "strategy", "setup",
    "signal_source", "reasons", "limit_price", "limit_fill_now",
})


def _signal(**overrides) -> dict:
    """Sygnal bazowy: poprawny, przechodzacy. Kazdy przypadek psuje jedno."""
    sig = {
        "symbol": "BTC", "direction": "LONG", "price": 100.0, "strength": 0.75,
        "trend_score": 0.75, "engine": "daytrading_v2",
        "strategy_mode": "DAYTRADING_V2", "sl_price": 96.0,
        "expected_net_r": 0.9, "expected_r_status": "OK",
        "market_regime": "TREND_UP", "atr_pct": 1.2, "leverage": 10,
    }
    sig.update(overrides)
    return sig


def _case(name, signal=None, state=None, cfg=None):
    return {"case": name, "signal": _signal(**(signal or {})),
            "state": dict(state or {}), "config": dict(cfg or {})}


def build_corpus() -> list:
    cases = []
    add = cases.append
    non_v2 = {"engine": "trend", "strategy_mode": "SWING"}

    # --- sciezka szczesliwa, rozne silniki ---
    add(_case("open_v2_clean"))
    add(_case("open_trend_clean", non_v2))
    add(_case("open_daytrading_clean",
              {"engine": "daytrading", "strategy_mode": "DAYTRADING",
               "setup": "intraday_15m_confirmed", "signal_source": "BLOFIN_NATIVE"}))
    add(_case("open_short", {"direction": "SHORT", "sl_price": 104.0}))

    # --- limit zaparkowany zamiast wejscia market ---
    # Korpus nie mial ani jednego takiego przypadku (zmierzone: PARKED=0),
    # a to trzeci mozliwy wynik wejscia obok "otwarto" i "odrzucono":
    # zlecenie zyje dalej jako working order. Bez tego przypadku roznica
    # miedzy "nie otworzono, bo odrzucono" a "nie otworzono, bo czeka
    # w kolejce" nie byla przez bramke widziana wcale.
    add(_case("park_v2_limit_in_zone", {"limit_price": 99.0}))

    # --- wejscie odrzucone przez bramke ryzyka ---
    add(_case("reject_invalid_direction", {"direction": "FLAT"}))
    add(_case("reject_invalid_price", {"price": 0.0}))
    add(_case("reject_weak_trend", dict(non_v2, strength=0.30, trend_score=0.30)))
    add(_case("reject_halted", state={"is_halted": True}))
    add(_case("reject_paused", state={"paused": True}))
    add(_case("reject_no_slots", state={"open_positions_count": 10}))
    add(_case("reject_capital_too_low", state={"capital": 0.5}))

    # --- rozmiar zbyt maly, zeby zlozyc zlecenie ---
    add(_case("size_below_exchange_minimum",
              dict(non_v2, sl_price=50.0, strength=0.48, trend_score=0.48,
                   atr_pct=9.0), state={"capital": 100.0}))
    add(_case("size_zero_capital", state={"capital": 0.0}))

    # --- polowa spreadu doliczana do ceny wejscia PRZED sizingiem ---
    # Szeroki spread wywraca trade na TRZECH niezaleznych bramkach, zanim
    # w ogole zdazy zmienic cene: dynamicznym limicie spreadu, filtrze
    # Expected Net R i bramce plynnosci order booka. Kazda z nich ma tu wlasny
    # przypadek - bez tego "przypadek spreadu" testuje losowo jedna z nich.
    no_ob = {"OB_IMPACT_FILTER": False, "OB_SIZE_FROM_LIQUIDITY": False}
    quiet = dict(no_ob, USE_EXPECTED_NET_R_FILTER=False,
                 DYNAMIC_SPREAD_FILTER=False)
    narrow = {"order_book": {"ob_spread_pct": 0.20}}   # ponizej limitu ~0.295%
    wide = {"order_book": {"ob_spread_pct": 0.40}}     # powyzej limitu

    add(_case("spread_baseline_no_book", cfg=quiet))
    add(_case("spread_widens_entry_long", narrow, cfg=quiet))
    add(_case("spread_widens_entry_short",
              dict(narrow, direction="SHORT", sl_price=104.0), cfg=quiet))
    add(_case("spread_disabled_by_config", narrow,
              cfg=dict(quiet, USE_ORDERBOOK_SPREAD=False)))
    add(_case("spread_malformed_is_ignored",
              {"order_book": {"ob_spread_pct": "szeroko"}}, cfg=quiet))

    # Trzy bramki, ktore szeroki spread wywraca - kazda osobno.
    add(_case("wide_spread_hits_dynamic_limit", wide, cfg=no_ob))
    add(_case("wide_spread_hits_net_r", wide,
              cfg=dict(no_ob, DYNAMIC_SPREAD_FILTER=False)))
    add(_case("wide_spread_hits_ob_levels", wide))
    return cases


def _position_row(pos) -> dict:
    if pos is None:
        return None
    return {
        "symbol": getattr(pos, "symbol", None),
        "direction": getattr(pos, "direction", None),
        "entry_price": _round(getattr(pos, "entry_price", None)),
        "size_usd": _round(getattr(pos, "size_usd", None)),
        "actual_notional": _round(getattr(pos, "actual_notional", None)),
        "margin": _round(getattr(pos, "margin", None)),
        "leverage": _round(getattr(pos, "leverage", None)),
        "sl_price": _round(getattr(pos, "sl_price", None)),
        "initial_sl_price": _round(getattr(pos, "initial_sl_price", None)),
        "initial_risk_abs": _round(getattr(pos, "initial_risk_abs", None)),
        "tp_price": _round(getattr(pos, "tp_price", None)),
        "strength": _round(getattr(pos, "strength", None)),
        "actual_risk_usd": _round(getattr(pos, "actual_risk_usd", None)),
        "margin_call_price": _round(getattr(pos, "margin_call_price", None)),
    }


def _mutations(signal: dict) -> dict:
    """Klucze dopisane przez open_position(), z wartosciami.

    To jest sedno tej bramki. Kolejnosc mutacji miedzy sizingiem a bramka
    ryzyka byla przyczyna czterech bledow; tutaj jest widoczna jako dane.
    """
    out = {}
    for key in sorted(signal):
        if key in _INPUT_KEYS:
            continue
        value = signal[key]
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out[key] = _round(value)
        elif isinstance(value, (str, bool)) or value is None:
            out[key] = value
        elif isinstance(value, (list, tuple)):
            out[key] = [str(v) for v in value]
        elif isinstance(value, dict):
            out[key] = {k: _round(v) if isinstance(v, (int, float))
                        and not isinstance(v, bool) else str(v)
                        for k, v in sorted(value.items())}
        else:
            out[key] = str(type(value).__name__)
    return out


def evaluate(case: dict) -> dict:
    import config
    import paper_trader as pt

    out = {"case": case["case"]}
    overrides = dict(FORCED_CONFIG)
    overrides.update(case.get("config") or {})
    saved = {key: getattr(config, key, _MISSING) for key in overrides}
    real_time = pt.time
    pt.time = FakeClock()
    try:
        for key, value in overrides.items():
            setattr(config, key, value)
        state = case.get("state") or {}
        risk = _fresh_risk(float(state.get("capital", 10000.0)))
        risk.open_positions_count = int(state.get("open_positions_count", 0))
        risk.is_halted = bool(state.get("is_halted", False))
        risk.halt_reason = "TEST_HALT" if risk.is_halted else None
        risk.paused = bool(state.get("paused", False))

        rejects: list = []
        real_reject = risk.log_reject

        def _log_reject(*args, **kwargs):
            reason = kwargs.get("reason")
            if reason is None and len(args) >= 4:
                reason = args[3]
            rejects.append(str(reason))
            return real_reject(*args, **kwargs)

        risk.log_reject = _log_reject

        # open_position() pracuje na WLASNEJ kopii sygnalu, wiec mutacji nie
        # widac z zewnatrz. Podgladamy je tam, gdzie powstaja - po sizingu
        # i po bramce ryzyka. To daje wiecej niz stan koncowy: pokazuje
        # KOLEJNOSC, ktora byla przyczyna czterech bledow.
        snapshots: dict = {}
        real_size = risk.calculate_position_size
        real_gate = risk.can_open_position

        def _sized(sig, *a, **k):
            result = real_size(sig, *a, **k)
            snapshots["after_sizing"] = _mutations(sig)
            snapshots["size_returned"] = _round(result)
            return result

        def _gated(sig, *a, **k):
            result = real_gate(sig, *a, **k)
            snapshots["after_gate"] = _mutations(sig)
            return result

        risk.calculate_position_size = _sized
        risk.can_open_position = _gated
        trader = pt.PaperTrader(risk, logger=RecordingLogger(), protection=None)
        signal = dict(case["signal"])
        pos = trader.open_position(signal)

        out["opened"] = pos is not None
        out["position"] = _position_row(pos)
        out["rejects"] = rejects
        out["signal_mutations"] = snapshots.get("after_gate") or snapshots.get("after_sizing") or {}
        out["mutations_after_sizing"] = snapshots.get("after_sizing") or {}
        out["size_returned"] = snapshots.get("size_returned")
        out["caller_signal_untouched"] = _mutations(signal) == {}
        out["open_count"] = len(trader.positions)
        out["limit_parked"] = sorted(trader._limit_queue.keys())
        out["capital"] = _round(risk.current_capital)
    except Exception as exc:
        out["raised"] = f"RAISED:{type(exc).__name__}: {str(exc)[:160]}"
    finally:
        pt.time = real_time
        for key, value in saved.items():
            if value is _MISSING:
                delattr(config, key)
            else:
                setattr(config, key, value)
    return out


def run_gate() -> dict:
    # Atrapy musza zniknac po przebiegu: bramka dziala w tym samym
    # interpreterze co reszta testow, a zostawiona atrapa wywracala
    # pozniejsze testy bledem "unknown location".
    saved = _install_stubs()
    try:
        cases = build_corpus()
        results = [evaluate(case) for case in cases]
        opened = sum(1 for r in results if r.get("opened"))
        # Zaparkowany limit to trzeci wynik, nie odmowa. Wczesniej wpadal
        # do "rejected", bo formula liczyla wszystko, co nie otworzylo
        # pozycji - i tym samym mylila "nie wpuszczono" z "czeka w kolejce".
        parked = sum(1 for r in results if r.get("limit_parked") and not r.get("opened"))
        raised = [r["case"] for r in results if r.get("raised")]
        reasons = sorted({x for r in results for x in (r.get("rejects") or [])})
        return {
            "meta": {"cases": len(results), "opened": opened, "parked": parked,
                     "rejected": len(results) - opened - parked - len(raised),
                     "raised": len(raised), "distinct_rejects": len(reasons)},
            "config": config_fingerprint(),
            "rejects": reasons,
            "results": results,
        }
    finally:
        _restore_stubs(saved)


def compare(baseline: dict, current: dict) -> list:
    problems: list = []
    old_cfg = (baseline.get("config") or {}).get("hash")
    new_cfg = (current.get("config") or {}).get("hash")
    if old_cfg != new_cfg:
        problems.append(f"KONFIGURACJA: hash {old_cfg} -> {new_cfg}")
    old_map = {r["case"]: r for r in baseline.get("results") or []}
    new_map = {r["case"]: r for r in current.get("results") or []}
    for name in sorted(set(old_map) | set(new_map)):
        before, after = old_map.get(name), new_map.get(name)
        if before is None:
            problems.append(f"  + NOWY PRZYPADEK {name}")
            continue
        if after is None:
            problems.append(f"  - USUNIETY PRZYPADEK {name}")
            continue
        for field in ("raised", "opened", "position", "rejects",
                      "signal_mutations", "mutations_after_sizing",
                      "size_returned", "caller_signal_untouched",
                      "open_count", "limit_parked", "capital"):
            if before.get(field) != after.get(field):
                problems.append(f"  ~ {name}.{field}: "
                                f"{before.get(field)!r} -> {after.get(field)!r}")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Bramka charakterystyki wykonania wejscia.")
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument("--coverage", action="store_true")
    args = ap.parse_args(argv)

    current = json.loads(json.dumps(run_gate()))
    meta = current["meta"]
    print(f"[entry] przypadkow {meta['cases']} | otwartych {meta['opened']}"
          f" | odrzuconych {meta['rejected']} | wyjatkow {meta['raised']}"
          f" | roznych powodow {meta['distinct_rejects']}"
          f" | config {current['config'].get('hash')}")

    if args.coverage:
        print("\nPowody odrzucenia pokryte przez korpus:")
        for reason in current["rejects"]:
            print(f"  {reason}")
        for res in current["results"]:
            if res.get("raised"):
                print(f"  WYJATEK {res['case']}: {res['raised']}")
        return 0

    if args.write_baseline:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(
            json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[entry] zapisano baseline: {args.baseline}")
        return 0

    if not args.baseline.is_file():
        print(f"[entry] BRAK baseline ({args.baseline}). "
              f"Utworz: python tools/entry_gate.py --write-baseline")
        return 2

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    problems = compare(baseline, current)
    if not problems:
        print("[entry] IDENTYCZNIE — wykonanie wejscia bez zmian.")
        return 0
    print(f"[entry] ROZNI SIE ({len(problems)} pozycji):")
    for line in problems[:60]:
        print(line)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
