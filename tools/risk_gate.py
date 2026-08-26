"""Bramka charakterystyki dla decyzji ryzyka.

Bramka parytetu (tools/parity.py) pokrywa replay, a replay woła pipeline
z `risk=None` i ma własne limity slotów. Znaczy to, że ~700 linii
`risk_manager.py` - `can_open_position()` i `calculate_position_size()` wraz
z pomocnikami - nie jest objęte żadną bramką. Przeniesienie tej logiki do
`cryptoedge/risk/` byłoby dziś nieudowadnialne, a to jest kod decydujący
o tym, czy wolno otworzyć pozycję i jak dużą.

Ta bramka domyka lukę: deterministyczny korpus sygnałów przechodzi przez
prawdziwy RiskManager, a werdykt (zgoda, powód, rozmiar) trafia do baseline.

    python tools/risk_gate.py                   # porównaj z baseline
    python tools/risk_gate.py --write-baseline  # zapisz nowy punkt odniesienia
    python tools/risk_gate.py --coverage        # jakie powody pokrywa korpus

Czego korpus CELOWO nie obejmuje: aktywnych cooldownów. Ich powód zawiera
pozostałe minuty liczone z `datetime.now()`, więc wynik dryfowałby między
przebiegami. Cooldowny mają własne testy jednostkowe; tutaj są wygaszone.
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

DEFAULT_BASELINE = ROOT / "tests" / "baselines" / "risk_gate.json"


def _signal(**overrides) -> dict:
    """Sygnal bazowy: poprawny, przechodzacy. Kazdy przypadek psuje jedna rzecz."""
    base = {
        "symbol": "BTC", "direction": "LONG", "price": 100.0, "strength": 0.75,
        "trend_score": 0.75, "engine": "daytrading_v2",
        "strategy_mode": "DAYTRADING_V2", "sl_price": 96.0,
        "expected_net_r": 0.9, "expected_r_status": "OK",
        "market_regime": "TREND_UP", "v2_profile": "major",
        "atr_pct": 1.2, "leverage": 10,
    }
    base.update(overrides)
    return base


def _state(**overrides) -> dict:
    base = {
        "capital": 1000.0, "open_positions_count": 0, "is_halted": False,
        "paused": False, "regime": "TREND_UP", "open_directions": [],
        "risk_state": "NORMAL", "daily_pnl": 0.0,
    }
    base.update(overrides)
    return base


def build_corpus() -> list[dict]:
    """Deterministyczny korpus. Kolejnosc jest czescia kontraktu baseline."""
    cases: list[dict] = []

    def add(name, signal=None, state=None):
        cases.append({"case": name, "signal": _signal(**(signal or {})),
                      "state": _state(**(state or {}))})

    add("baseline_ok")
    # --- wejscie niepoprawne formalnie ---
    add("direction_empty", {"direction": ""})
    add("direction_garbage", {"direction": "SIDEWAYS"})
    add("direction_lowercase", {"direction": "long"})
    add("price_zero", {"price": 0.0})
    add("price_negative", {"price": -5.0})
    add("price_none", {"price": None})
    add("price_nan", {"price": float("nan")})
    add("price_inf", {"price": float("inf")})
    add("strength_none", {"strength": None})
    add("strength_nan", {"strength": float("nan")})
    add("strength_text", {"strength": "mocny"})
    # --- sila sygnalu ---
    # engine="trend", bo dla DAYTRADING_V2 bramka sily jest CELOWO pomijana
    # (V2 ma wlasne kontrole w silniku i podaje stala strength=0.75).
    for value in (0.0, 0.30, 0.47, 0.48, 0.50, 0.60, 0.75, 0.95, 1.0):
        add(f"strength_trend_{value:.2f}",
            {"engine": "trend", "strength": value, "trend_score": value})
    add("strength_v2_low_is_skipped_by_design",
        {"strength": 0.05, "trend_score": 0.05})
    for value in (0.30, 0.60, 0.90):
        add(f"strength_reversal_{value:.2f}",
            {"engine": "reversal", "strength": value, "reversal_score": value})
    # --- silniki ---
    for engine in ("daytrading_v2", "reversal", "trend", "swing", ""):
        add(f"engine_{engine or 'empty'}", {"engine": engine})
    add("reversal_weak", {"engine": "reversal", "strength": 0.40, "reversal_score": 0.40})
    add("reversal_strong", {"engine": "reversal", "strength": 0.85, "reversal_score": 0.85})
    # --- Expected Net R (v20.13.1) ---
    add("net_r_negative", {"expected_net_r": -0.10})
    add("net_r_zero", {"expected_net_r": 0.0})
    add("net_r_tiny", {"expected_net_r": 0.01})
    add("net_r_prior_only", {"expected_net_r": 0.05, "expected_r_status": "PRIOR_ONLY"})
    add("net_r_low_sample", {"expected_net_r": 0.05, "expected_r_status": "LOW_SAMPLE"})
    add("net_r_prior_only_ok", {"expected_net_r": 0.40, "expected_r_status": "PRIOR_ONLY"})
    add("net_r_nan", {"expected_net_r": float("nan")})
    add("net_r_missing", {"expected_net_r": None})
    # --- stop-loss: to on realnie steruje Expected Net R i sizingiem ---
    add("sl_missing", {"sl_price": None})
    add("sl_equal_price", {"sl_price": 100.0})
    add("sl_hair_tight", {"sl_price": 99.99})
    add("sl_tight", {"sl_price": 99.5})
    add("sl_normal", {"sl_price": 96.0})
    add("sl_wide", {"sl_price": 85.0})
    add("sl_very_wide", {"sl_price": 50.0})
    add("sl_inverted_for_long", {"sl_price": 104.0})
    add("sl_short_normal", {"direction": "SHORT", "sl_price": 104.0})
    add("sl_short_inverted", {"direction": "SHORT", "sl_price": 96.0})
    # --- projekcja dziennej straty ---
    for notional in (0.0, 10.0, 500.0, 5000.0, 100000.0):
        add(f"planned_notional_{notional:.0f}", {"_planned_notional": notional})
    return cases


def build_strategy_corpus() -> list[dict]:
    """Filtr strategii primary (4h) + fallback MTF.

    Ta galaz byla dotad calkowicie niepokryta: w baseline nie bylo ani jednego
    powodu STRAT_*, bo caly korpus jechal na DAYTRADING_V2, a warunek wejscia
    brzmi `not is_day and not is_v2`. Zeby ja obudzic, trzeba jawnie zejsc
    z V2 - stad `strategy_mode` inne niz DAYTRADING_V2 w kazdym przypadku.

    Pokrywa tez mnozniki `_size_mult` (0.6 przy STRAT_FAIL + MTF, 0.5 przy
    konflikcie kierunku + MTF). Sa one zapisywane do baseline, bo bez nich
    nie da sie udowodnic, ze przeniesienie mutacji sygnalu niczego nie
    przesunelo.
    """
    cases: list[dict] = []

    def add(name, signal=None, state=None):
        base = {"engine": "trend", "strategy_mode": "SWING", "strength": 0.75,
                "trend_score": 0.75}
        base.update(signal or {})
        cases.append({"case": name, "signal": _signal(**base),
                      "state": _state(**(state or {}))})

    def mtf(long_votes=0, short_votes=0):
        return {"long_votes": long_votes, "short_votes": short_votes}

    # --- strat obecny, ocena 4h nieudana ---
    add("strat_fail_no_mtf", {"strategy": {"pass": False}})
    add("strat_fail_mtf_majority",
        {"strategy": {"pass": False}, "mtf": mtf(long_votes=2)})
    add("strat_fail_mtf_below_min",
        {"strategy": {"pass": False}, "mtf": mtf(long_votes=1)})
    add("strat_fail_mtf_wrong_direction",
        {"strategy": {"pass": False}, "mtf": mtf(short_votes=3)})
    for marker in ("PRIMARY_MTF_FALLBACK", "PRIMARY_SOFT_PASS", "STRAT_SOFT_ALIGN"):
        add(f"strat_fail_soft_{marker.lower()}",
            {"strategy": {"pass": False}, "reasons": [marker]})
    add("strat_fail_soft_unknown_marker",
        {"strategy": {"pass": False}, "reasons": ["SOMETHING_ELSE"]})
    # --- strat obecny, ocena udana ---
    add("strat_pass_same_direction",
        {"strategy": {"pass": True, "direction": "LONG"}})
    add("strat_pass_conflict_no_mtf",
        {"strategy": {"pass": True, "direction": "SHORT"}})
    add("strat_pass_conflict_with_mtf",
        {"strategy": {"pass": True, "direction": "SHORT"}, "mtf": mtf(long_votes=2)})
    add("strat_pass_no_direction",
        {"strategy": {"pass": True}})
    add("strat_pass_short_signal_same_direction",
        {"direction": "SHORT", "sl_price": 104.0,
         "strategy": {"pass": True, "direction": "SHORT"}})
    # --- brak oceny 4h (STRAT_PRIMARY_NA) ---
    add("strat_na_mtf_majority", {"mtf": mtf(long_votes=2)})
    add("strat_na_no_mtf_strong", {"strength": 0.75, "trend_score": 0.75})
    add("strat_na_no_mtf_medium", {"strength": 0.52, "trend_score": 0.52})
    add("strat_na_no_mtf_weak", {"strength": 0.40, "trend_score": 0.40})
    add("strat_na_range_below_threshold",
        {"market_regime": "RANGE", "strength": 0.60, "trend_score": 0.60},
        {"regime": "RANGE"})
    add("strat_na_range_at_threshold",
        {"market_regime": "RANGE", "strength": 0.68, "trend_score": 0.68},
        {"regime": "RANGE"})
    add("strat_na_range_above_threshold",
        {"market_regime": "RANGE", "strength": 0.80, "trend_score": 0.80},
        {"regime": "RANGE"})
    add("strat_na_range_with_mtf",
        {"market_regime": "RANGE", "strength": 0.50, "trend_score": 0.50,
         "mtf": mtf(long_votes=3)},
        {"regime": "RANGE"})
    # STRAT_NA_WEAK jest osiagalny TYLKO przez reversal. Dla trendu bramka
    # sily (MIN_SIGNAL_STRENGTH=0.48) odrzuca wczesniej, wiec porownanie
    # `strength < min_str` na koncu galezi nigdy by nie padlo. Reversal ma
    # wlasny, nizszy prog (REVERSAL_MIN_STRENGTH=0.32) i tamtedy przechodzi.
    add("strat_na_weak_via_reversal",
        {"engine": "reversal", "strength": 0.40, "reversal_score": 0.40,
         "mtf": mtf(long_votes=3)})
    add("strat_na_reversal_above_min",
        {"engine": "reversal", "strength": 0.60, "reversal_score": 0.60,
         "mtf": mtf(long_votes=3)})
    add("strat_na_range_weak_via_reversal",
        {"engine": "reversal", "strength": 0.40, "reversal_score": 0.40,
         "market_regime": "RANGE"},
        {"regime": "RANGE"})
    # --- galaz DAYTRADING (nie V2) ---
    def add_day(name, signal):
        base = {"engine": "daytrading", "strategy_mode": "DAYTRADING",
                "strength": 0.75, "trend_score": 0.75}
        base.update(signal)
        cases.append({"case": name, "signal": _signal(**base), "state": _state()})

    add_day("day_setup_missing", {})
    add_day("day_setup_unknown", {"setup": "cos_innego"})
    for setup in ("intraday_5m_confirmed", "intraday_15m_confirmed", "intraday_confirmed"):
        add_day(f"day_{setup}_non_native", {"setup": setup})
        add_day(f"day_{setup}_native",
                {"setup": setup, "signal_source": "BLOFIN_NATIVE"})
    return cases


def build_state_corpus() -> list[dict]:
    """Ten sam poprawny sygnal, rozny stan konta i portfela."""
    cases: list[dict] = []

    def add(name, state):
        cases.append({"case": name, "signal": _signal(), "state": _state(**state)})

    add("state_halted", {"is_halted": True})
    add("state_paused", {"paused": True})
    add("state_capital_zero", {"capital": 0.0})
    add("state_capital_below_one", {"capital": 0.5})
    add("state_capital_tiny", {"capital": 5.0})
    for count in (0, 1, 5, 9, 10, 11):
        add(f"state_open_{count}", {"open_positions_count": count})
    for regime in ("TREND_UP", "TREND_DOWN", "RANGE", "PANIC", "UNKNOWN"):
        add(f"state_regime_{regime}", {"regime": regime})
    # Heat limit: ile pozycji w tym samym kierunku juz stoi.
    for same in (0, 3, 6, 7, 10):
        add(f"state_same_direction_{same}", {"open_directions": ["LONG"] * same,
                                             "open_positions_count": same})
    add("state_opposite_directions", {"open_directions": ["SHORT"] * 8,
                                      "open_positions_count": 8})
    add("state_reduce_only", {"risk_state": "REDUCE_ONLY"})
    add("state_reduce_only_lowercase", {"risk_state": "reduce_only"})
    # Dzienny limit straty: ile budzetu zostalo na dzis.
    for pnl in (0.0, -10.0, -35.0, -39.9, -40.0, -50.0):
        add(f"state_daily_pnl_{pnl:+.1f}", {"daily_pnl": pnl})
    # PANIC ma osobne progi per silnik.
    cases.append({"case": "panic_reversal", "signal": _signal(engine="reversal", strength=0.60,
                                                              reversal_score=0.60),
                  "state": _state(regime="PANIC")})
    cases.append({"case": "panic_daytrading_weak", "signal": _signal(strength=0.60, trend_score=0.60),
                  "state": _state(regime="PANIC")})
    cases.append({"case": "panic_daytrading_strong", "signal": _signal(strength=0.90, trend_score=0.90),
                  "state": _state(regime="PANIC")})
    return cases


def _fresh_risk(state: dict):
    """RiskManager w kontrolowanym stanie. Wszystko, co bramka czyta, jest
    ustawione jawnie - inaczej baseline zalezalby od kolejnosci testow."""
    from risk_manager import RiskManager
    from datetime import datetime, timedelta

    risk = RiskManager(starting_capital=float(state["capital"]))
    risk.current_capital = float(state["capital"])
    risk.paper_capital = float(state["capital"])
    risk.daily_start_capital = float(state["capital"]) or 1.0
    risk.peak_equity = float(state["capital"])
    risk.paper_peak_equity = float(state["capital"])
    risk.daily_pnl = float(state.get("daily_pnl", 0.0))
    risk.risk_state = str(state.get("risk_state", "NORMAL"))
    risk.open_positions_count = int(state["open_positions_count"])
    risk.is_halted = bool(state["is_halted"])
    risk.paused = bool(state["paused"])
    risk.halt_reason = "TEST_HALT" if state["is_halted"] else None
    risk.last_regime = str(state["regime"])
    risk.last_btc_change = 0.0
    risk.consecutive_losses = 0
    # Cooldowny celowo wygaszone - ich powod zawiera pozostale minuty liczone
    # z datetime.now(), wiec baseline by dryfowal miedzy przebiegami.
    past = datetime.now() - timedelta(days=1)
    risk.loss_pause_until = past
    risk.symbol_cooldown = {}
    risk.engine_symbol_cooldown = {}
    risk.engine_symbol_loss_streak = {}
    risk._positions_ref = []
    risk._reconciler_ref = None
    return risk


def _round(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return str(number)
    return round(number, 8)


def evaluate(case: dict) -> dict:
    """Werdykt ryzyka dla jednego przypadku. Wyjatek tez jest wynikiem -
    ukrycie go zamienilo by bramke w zrodlo falszywego spokoju."""
    risk = _fresh_risk(case["state"])
    signal = dict(case["signal"])
    out = {"case": case["case"]}
    try:
        approved, reason = risk.can_open_position(
            signal, open_directions=list(case["state"]["open_directions"]),
        )
        out["approved"] = bool(approved)
        out["reason"] = str(reason)
        # can_open_position mutuje sygnal w miejscu. Mnoznik rozmiaru jest
        # jedynym widocznym skutkiem galezi, ktore NIE odrzucaja - bez niego
        # bramka przepuscilaby zmiane 0.6 na 0.5 bez slowa.
        out["size_mult"] = _round(signal.get("_size_mult"))
    except Exception as exc:
        out["approved"] = None
        out["reason"] = f"RAISED:{type(exc).__name__}: {str(exc)[:120]}"
        return out
    try:
        out["size_usd"] = _round(risk.calculate_position_size(dict(case["signal"])))
    except Exception as exc:
        out["size_usd"] = f"RAISED:{type(exc).__name__}: {str(exc)[:120]}"
    return out


def run_gate() -> dict:
    cases = build_corpus() + build_strategy_corpus() + build_state_corpus()
    results = [evaluate(case) for case in cases]
    reasons = sorted({str(r.get("reason") or "") for r in results})
    approved = sum(1 for r in results if r.get("approved") is True)
    raised = sum(1 for r in results if str(r.get("reason", "")).startswith("RAISED:"))
    return {
        "meta": {"cases": len(results), "approved": approved,
                 "rejected": len(results) - approved - raised, "raised": raised,
                 "distinct_reasons": len(reasons)},
        "config": config_fingerprint(),
        "reasons": reasons,
        "results": results,
    }


def compare(baseline: dict, current: dict) -> list[str]:
    problems: list[str] = []
    old_cfg = (baseline.get("config") or {}).get("hash")
    new_cfg = (current.get("config") or {}).get("hash")
    if old_cfg != new_cfg:
        problems.append(f"KONFIGURACJA: hash {old_cfg} -> {new_cfg}")
        old_w = (baseline.get("config") or {}).get("watched") or {}
        new_w = (current.get("config") or {}).get("watched") or {}
        for name in sorted(set(old_w) | set(new_w)):
            if old_w.get(name) != new_w.get(name):
                problems.append(f"  {name}: {old_w.get(name)!r} -> {new_w.get(name)!r}")
    old_map = {r["case"]: r for r in baseline.get("results") or []}
    new_map = {r["case"]: r for r in current.get("results") or []}
    for name in sorted(set(old_map) | set(new_map)):
        before, after = old_map.get(name), new_map.get(name)
        if before is None:
            problems.append(f"  + NOWY PRZYPADEK {name}: {after.get('reason')}")
            continue
        if after is None:
            problems.append(f"  - USUNIETY PRZYPADEK {name}: {before.get('reason')}")
            continue
        for field in ("approved", "reason", "size_mult", "size_usd"):
            if before.get(field) != after.get(field):
                problems.append(
                    f"  ~ {name}.{field}: {before.get(field)!r} -> {after.get(field)!r}")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Bramka charakterystyki decyzji ryzyka.")
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument("--coverage", action="store_true",
                    help="pokaz, jakie powody odrzucenia pokrywa korpus")
    args = ap.parse_args(argv)

    current = run_gate()
    meta = current["meta"]
    print(f"[risk] przypadkow {meta['cases']} | zgod {meta['approved']} | "
          f"odmow {meta['rejected']} | wyjatkow {meta['raised']} | "
          f"roznych powodow {meta['distinct_reasons']} | config {current['config']['hash']}")

    if args.coverage:
        print("\nPowody pokryte przez korpus:")
        for reason in current["reasons"]:
            print(f"  {reason}")
        return 0

    if args.write_baseline:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[risk] zapisano baseline: {args.baseline}")
        return 0

    if not args.baseline.exists():
        print(f"[risk] BRAK BASELINE: {args.baseline}")
        print("[risk] uruchom najpierw: python tools/risk_gate.py --write-baseline")
        return 2

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    problems = compare(baseline, json.loads(json.dumps(current)))
    if not problems:
        print("[risk] IDENTYCZNIE — decyzje ryzyka bez zmian.")
        return 0
    print(f"[risk] RÓŻNI SIĘ ({len(problems)} pozycji):")
    for line in problems:
        print(line)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
