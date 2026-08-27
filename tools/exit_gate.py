# -*- coding: utf-8 -*-
"""Bramka charakterystyki dla WYJSC z pozycji.

`tools/risk_gate.py` pilnuje decyzji o wejsciu, `tools/parity.py` pilnuje
replayu. Zamykanie pozycji - okolo 400 linii `paper_trader.check_exits()`
plus `check_tp_sl`, trailing, time stopy i partiale - nie bylo objete niczym
poza testami jednostkowymi wybranych galezi. A to kod, ktory decyduje o
realizacji straty i zysku.

    python tools/exit_gate.py                   # porownaj z baseline
    python tools/exit_gate.py --write-baseline  # zapisz nowy punkt odniesienia
    python tools/exit_gate.py --coverage        # jakie powody pokrywa korpus

CO JEST ZAPISYWANE. Nie tylko powod zamkniecia, ale takze `label` (to on
trafia do CSV i na ekran, a mapa etykiet jest niepelna), stan pozycji po
kazdym ticku oraz slad wywolan `on_sl_updated` - bez tego zmiana, ktora
przestaje wypychac SL na gielde, przeszlaby niezauwazona.

DETERMINIZM. Zegar jest wstrzykiwany: `paper_trader.time` zastepuje atrapa,
a `entry_time` liczone jest wzgledem stalego T0. Bez tego cooldown trailingu
(`TRAILING_MIN_UPDATE_INTERVAL_SEC` = 5 s) blokowalby kazde drugie
zaciesnienie w korpusie, ktory wykonuje sie w milisekundach - i bramka
zamrozilaby zachowanie, ktore w produkcji nie wystepuje.

CZEGO KORPUS CELOWO NIE OBEJMUJE: naliczania fundingu (petla zalezna od
`time.time()` i liczby okresow - wylaczone przez `FUNDING_ACCRUAL=False`,
ma wlasne testy jednostkowe) oraz wspolbieznosci.
"""
from __future__ import annotations

import argparse
import json
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from parity import config_fingerprint  # noqa: E402

DEFAULT_BASELINE = ROOT / "tests" / "baselines" / "exit_gate.json"

# Staly punkt odniesienia. Wiek pozycji liczymy wzgledem niego, nie wzgledem
# teraz - inaczej baseline dryfowalby z kazdym przebiegiem.
T0 = datetime(2026, 1, 1, 12, 0, 0)


class FakeClock:
    """Atrapa modulu `time` dla paper_tradera. Czas plynie tylko na zadanie."""

    def __init__(self):
        self.now = 1_000_000.0

    def advance(self, seconds: float) -> None:
        self.now += float(seconds or 0)

    def time(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:  # gdyby ktos zawolal
        self.advance(seconds)


class RecordingProtection:
    """Atrapa ProtectionManager: oddaje zaplanowany powod i notuje wywolania."""

    def __init__(self, reason_by_tick=None):
        self.reason_by_tick = list(reason_by_tick or [])
        self.tick = 0
        self.calls: list = []

    def check_local_protection(self, symbol, direction, current_price):
        idx = self.tick
        self.tick += 1
        if idx < len(self.reason_by_tick):
            return self.reason_by_tick[idx]
        return None

    def update_exchange_sl(self, symbol, direction, new_sl=None, size_contracts=None):
        self.calls.append({"op": "update_sl", "symbol": symbol,
                           "direction": direction, "new_sl": _round(new_sl)})

    def disarm(self, symbol, direction):
        self.calls.append({"op": "disarm", "symbol": symbol, "direction": direction})

    def is_killed(self):
        return False


def _stub_module(name: str, **attrs) -> None:
    """Podstawia atrape w sys.modules. Importy sa leniwe, wiec dziala pozniej."""
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    sys.modules[name] = mod


_STUBBED_MODULES = ("alerts", "decision_telemetry", "strength_calibration",
                    "day_expectancy_calibration")


def _restore_stubs(saved: dict) -> None:
    """Zdejmuje atrapy z sys.modules.

    Bramka dziala w tym samym interpreterze co reszta pakietu testow, wiec
    atrapa zostawiona w sys.modules truje kazdy test uruchomiony pozniej -
    `from day_expectancy_calibration import DayExpectancyCalibrator` konczy
    sie wtedy ImportError "unknown location". To nie jest teoria: tak wlasnie
    sypaly sie 3 testy po dolozeniu bramki wyjsc."""
    for name, module in saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def _install_stubs() -> dict:
    """Odcina wszystko, co pisze na dysk, wysyla powiadomienia albo trzyma
    stan miedzy przypadkami. Zwraca poprzednia zawartosc sys.modules,
    zeby wolajacy mogl ja przywrocic.

    Kalibratory sa tu najwazniejsze: to singletony zapisywane na dysk, wiec
    bez atrapy wynik przypadku N zalezalby od przypadkow 1..N-1 i od tego, co
    zostalo na dysku po poprzednim uruchomieniu."""
    saved = {name: sys.modules.get(name) for name in _STUBBED_MODULES}
    _stub_module("alerts", alert_close=lambda *a, **k: None,
                 alert_margin_call=lambda *a, **k: None,
                 alert_partial=lambda *a, **k: None,
                 alert_open=lambda *a, **k: None)
    _stub_module("decision_telemetry", decision_snapshot=lambda *a, **k: None,
                 outcome_snapshot=lambda *a, **k: None)

    class _NullCalibrator:
        def update_from_trade(self, *a, **k):
            return None

        def record(self, *a, **k):
            return None

        def expected_r(self, *a, **k):
            return 1.0

    _stub_module("strength_calibration", get_calibrator=lambda: _NullCalibrator())
    _stub_module("day_expectancy_calibration",
                 get_day_calibrator=lambda: _NullCalibrator())
    return saved


def _round(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return str(number)
    return round(number, 8)


def _row(**overrides) -> dict:
    """Wiersz pozycji w formacie restore_open_positions(). LONG @ 100, SL 96."""
    row = {
        "symbol": "BTC", "direction": "LONG", "entry_price": 100.0,
        "size_usd": 500.0, "leverage": 10, "engine": "trend",
        "strength": 0.70, "sl_price": 96.0, "tp_price": 112.0,
        "tp1_price": 104.0, "tp2_price": 108.0, "tp_plan": None,
        "entry_time": (T0 - timedelta(minutes=30)).isoformat(),
        "id": "GATE-0001", "highest_price": 100.0, "lowest_price": 100.0,
        "original_size": 500.0, "actual_notional": 500.0,
    }
    row.update(overrides)
    return row


def _signal(**overrides) -> dict:
    sig = {"symbol": "BTC", "direction": "LONG", "strength": 0.70}
    sig.update(overrides)
    return sig


def _case(name, row=None, ticks=None, cfg=None, protection=None, age_minutes=30):
    """Jeden przypadek: pozycja + seria tickow (cena, sygnal, uplyw czasu)."""
    return {
        "case": name,
        "row": _row(**(row or {})),
        "age_minutes": age_minutes,
        "ticks": list(ticks or []),
        "config": dict(cfg or {}),
        "protection": list(protection or []),
    }


def _tick(price, signal=None, advance_s=60):
    return {"price": price, "signal": signal, "advance_s": advance_s}


def build_corpus() -> list:
    """Deterministyczny korpus. Kolejnosc jest czescia kontraktu baseline."""
    cases = []
    add = cases.append

    # --- stop loss (LONG i SHORT, wokol progu) ---
    add(_case("sl_hit_long", ticks=[_tick(95.0)]))
    add(_case("sl_exact_touch_long", ticks=[_tick(96.0)]))
    add(_case("sl_one_tick_inside_long", ticks=[_tick(96.01)]))
    add(_case("sl_hit_short", row={"direction": "SHORT", "sl_price": 104.0,
                                   "tp1_price": 96.0, "tp2_price": 92.0,
                                   "tp_price": 88.0},
              ticks=[_tick(105.0)]))
    add(_case("sl_gap_far_below", ticks=[_tick(80.0)]))

    # --- margin call bije stop loss ---
    add(_case("margin_call_by_price", ticks=[_tick(91.0)]))
    add(_case("margin_call_disabled", ticks=[_tick(91.0)],
              cfg={"MARGIN_CALL_ENABLED": False}))

    # --- take profit (tylko daytrading przy NO_HARD_TP) ---
    add(_case("take_profit_daytrading", row={"engine": "daytrading"},
              ticks=[_tick(113.0)]))
    add(_case("take_profit_trend_blocked_by_no_hard_tp", ticks=[_tick(113.0)]))
    add(_case("take_profit_trend_with_hard_tp",
              ticks=[_tick(113.0)], cfg={"NO_HARD_TP": False}))

    # --- partiale ---
    add(_case("partial_tp1_hit", ticks=[_tick(104.5)]))
    add(_case("partial_tp1_then_tp2", ticks=[_tick(104.5), _tick(108.5)]))
    add(_case("partial_tp1_already_done", row={"partial_tp1_done": True,
                                               "partial_taken": True},
              ticks=[_tick(104.5)]))
    add(_case("partial_disabled", ticks=[_tick(104.5)],
              cfg={"PARTIAL_TP_ENABLED": False}))
    add(_case("partial_beats_margin_call", ticks=[_tick(104.5)],
              row={"tp1_price": 104.0}))

    # --- trailing ---
    add(_case("trailing_arms_then_holds",
              ticks=[_tick(112.0), _tick(110.0), _tick(109.0)]))
    add(_case("trailing_stop_hit",
              row={"trailing_active": True, "trailing_stop_price": 105.0},
              ticks=[_tick(104.0)]))
    add(_case("trailing_beats_stop_loss",
              row={"trailing_active": True, "trailing_stop_price": 105.0},
              ticks=[_tick(95.0)]))
    add(_case("breakeven_move_reports_stop_loss",
              row={"breakeven_active": True, "sl_price": 100.0},
              ticks=[_tick(99.0)]))
    return cases


def build_signal_corpus() -> list:
    """Wyjscia sterowane sygnalem, nie cena."""
    cases = []
    add = cases.append

    def mtf(long_votes=0, short_votes=0):
        return {"long_votes": long_votes, "short_votes": short_votes,
                "hold_long": False, "hold_short": False}

    # --- early loss cut: wiek + PnL% + MTF przeciw ---
    late = {"age_minutes": 61}
    add(_case("early_loss_cut", ticks=[_tick(99.0, _signal(mtf=mtf(short_votes=3)))],
              **late))
    add(_case("early_loss_cut_no_mtf_against",
              ticks=[_tick(99.0, _signal(mtf=mtf(long_votes=3)))], **late))
    add(_case("early_loss_cut_too_young",
              ticks=[_tick(99.0, _signal(mtf=mtf(short_votes=3)))], age_minutes=30))
    add(_case("early_loss_cut_pnl_not_deep_enough",
              ticks=[_tick(99.6, _signal(mtf=mtf(short_votes=3)))], **late))
    add(_case("early_loss_cut_without_mtf_requirement",
              ticks=[_tick(99.0, _signal())],
              cfg={"EARLY_LOSS_CUT_REQUIRE_MTF": False}, **late))

    # --- sygnal przeciwny ---
    # UWAGA na dzwignie: `trail_lock` wlacza sie przy pnl_pct > 5, a przy x10
    # ruch ceny o 1% daje juz +10% PnL. Cena musi wiec byc bardzo blisko
    # wejscia, zeby ta galaz w ogole byla osiagalna - inaczej trailing ja
    # zdusi i przypadek testuje co innego, niz sie wydaje.
    add(_case("opposite_signal_strong",
              ticks=[_tick(100.3, _signal(direction="SHORT", strength=0.75))]))
    add(_case("opposite_signal_below_threshold",
              ticks=[_tick(100.3, _signal(direction="SHORT", strength=0.69))]))
    add(_case("opposite_signal_suppressed_by_profit",
              ticks=[_tick(101.0, _signal(direction="SHORT", strength=0.75))]))
    add(_case("opposite_signal_blocked_by_trail_lock",
              row={"trailing_active": True},
              ticks=[_tick(112.0, _signal(direction="SHORT", strength=0.90))]))

    # --- supertrend flip: trzy formy zapisu tego samego ---
    add(_case("supertrend_flip_dict",
              ticks=[_tick(101.0, _signal(supertrend={"is_up": False}))]))
    add(_case("supertrend_flip_string",
              ticks=[_tick(101.0, _signal(supertrend="down"))]))
    add(_case("supertrend_flip_nested",
              ticks=[_tick(101.0, _signal(strategy={"supertrend": {"is_up": False}}))]))
    add(_case("supertrend_flip_not_blocked_by_trail_lock",
              row={"trailing_active": True},
              ticks=[_tick(112.0, _signal(supertrend={"is_up": False}))]))
    add(_case("supertrend_aligned_no_exit",
              ticks=[_tick(101.0, _signal(supertrend={"is_up": True}))]))
    add(_case("supertrend_flip_disabled",
              ticks=[_tick(101.0, _signal(supertrend={"is_up": False}))],
              cfg={"EXIT_ON_SUPERTREND_FLIP": False}))

    # --- HTF przeciwny ---
    add(_case("htf_opposite",
              ticks=[_tick(100.3, _signal(strategy_daily={"pass": True,
                                                          "direction": "SHORT"}))]))
    add(_case("htf_same_direction",
              ticks=[_tick(100.3, _signal(strategy_daily={"pass": True,
                                                          "direction": "LONG"}))]))
    add(_case("htf_opposite_suppressed_by_profit",
              ticks=[_tick(101.0, _signal(strategy_daily={"pass": True,
                                                          "direction": "SHORT"}))]))

    # --- take profit: osiagalny dopiero, gdy partiale sa juz za nami ---
    # Galaz partiala w check_tp_sl bije TP, wiec bez tych flag przypadek
    # "take profit" testuje w rzeczywistosci partiala.
    done = {"partial_tp1_done": True, "partial_tp2_done": True,
            "partial_taken": True, "partial_stage": 2}
    add(_case("take_profit_after_partials", row={**done, "engine": "daytrading"},
              ticks=[_tick(113.0)]))
    add(_case("take_profit_trend_no_hard_tp", row=done, ticks=[_tick(113.0)]))
    add(_case("take_profit_trend_hard_tp_on", row=done, ticks=[_tick(113.0)],
              cfg={"NO_HARD_TP": False}))
    add(_case("take_profit_ride_trend_blocks",
              row={**done, "engine": "daytrading", "ride_trend": True},
              ticks=[_tick(113.0)]))
    return cases


def build_edge_corpus() -> list:
    """Time stopy, V2, ochrona lokalna i wejscia zdegenerowane."""
    cases = []
    add = cases.append
    day = {"engine": "daytrading"}
    v2 = {"engine": "daytrading_v2"}

    # --- time stopy (tylko nie-V2) ---
    add(_case("day_time_stop", row=day, ticks=[_tick(99.5)], age_minutes=7 * 60))
    add(_case("day_time_stop_blocked_by_r", row=day, ticks=[_tick(107.0)],
              age_minutes=7 * 60))
    add(_case("day_hard_time_stop", row=day, ticks=[_tick(107.0)],
              age_minutes=11 * 60))
    add(_case("day_time_stop_not_for_trend", ticks=[_tick(99.5)],
              age_minutes=7 * 60))

    # --- V2: decyzje podejmuje czysty reduktor v2_trade_lifecycle ---
    add(_case("v2_sl_armed_by_config", row=v2, ticks=[_tick(95.0)],
              cfg={"DAYTRADING_V2_ENTRY_SL": True}))
    add(_case("v2_sl_disarmed_before_tp1", row=v2, ticks=[_tick(95.0)],
              cfg={"DAYTRADING_V2_ENTRY_SL": False}))
    add(_case("v2_tp1", row=v2, ticks=[_tick(104.5)]))
    add(_case("v2_tp1_then_tp2", row=v2, ticks=[_tick(104.5), _tick(108.5)]))
    add(_case("v2_time_stop", row=v2, ticks=[_tick(100.2)], age_minutes=25 * 60))
    add(_case("v2_htf_reversal", row=v2,
              ticks=[_tick(101.0, _signal(bias_1d="SHORT"))],
              cfg={"DAYTRADING_V2_EXIT_ON_HTF_REVERSAL": True}))
    add(_case("v2_ignores_opposite_signal", row=v2,
              ticks=[_tick(101.0, _signal(direction="SHORT", strength=0.90))]))

    # --- ochrona lokalna ma pierwszenstwo przed wszystkim ---
    add(_case("protection_emergency_sl", ticks=[_tick(95.0)],
              protection=["local_emergency_sl"]))
    add(_case("protection_beats_take_profit", row={"engine": "daytrading"},
              ticks=[_tick(113.0)], protection=["local_emergency_tp"]))
    add(_case("protection_silent", ticks=[_tick(99.0)], protection=[None]))

    # --- wejscia zdegenerowane ---
    add(_case("price_missing", ticks=[_tick(None)]))
    add(_case("price_zero", ticks=[_tick(0.0)]))
    add(_case("price_negative", ticks=[_tick(-5.0)]))
    add(_case("price_nan", ticks=[_tick(float("nan"))]))
    add(_case("signal_neutral_direction",
              ticks=[_tick(99.0, _signal(direction="NEUTRAL"))]))
    add(_case("signal_for_other_symbol_only",
              ticks=[_tick(99.0, _signal(symbol="ETH", direction="SHORT",
                                         strength=0.95))]))

    # --- pierwszenstwo ---
    add(_case("sl_beats_opposite_signal",
              ticks=[_tick(95.0, _signal(direction="SHORT", strength=0.95))]))
    add(_case("time_stop_beats_supertrend_flip", row=day,
              ticks=[_tick(99.5, _signal(supertrend={"is_up": False}))],
              age_minutes=7 * 60))
    return cases


_MISSING = object()

# Wymuszane w kazdym przypadku - patrz naglowek modulu.
FORCED_CONFIG = {
    "FUNDING_ACCRUAL": False,
    "ALERTS_ENABLED": False,
    "ALERT_PUSH": False,
    "ALERT_SOUND": False,
}


def _fresh_risk(capital: float = 10000.0):
    """RiskManager w jawnie ustawionym stanie. Cooldowny wygaszone."""
    from datetime import timedelta as _td
    from risk_manager import RiskManager

    risk = RiskManager(starting_capital=capital)
    risk.current_capital = capital
    risk.paper_capital = capital
    risk.daily_start_capital = capital
    risk.peak_equity = capital
    risk.paper_peak_equity = capital
    risk.daily_pnl = 0.0
    risk.open_positions_count = 0
    risk.is_halted = False
    risk.paused = False
    risk.last_regime = "TREND_UP"
    risk.consecutive_losses = 0
    risk.max_drawdown_pct = 0.0
    past = datetime.now() - _td(days=1)
    risk.loss_pause_until = past
    risk.symbol_cooldown = {}
    risk.engine_symbol_cooldown = {}
    risk.engine_symbol_loss_streak = {}
    risk._positions_ref = []
    risk._reconciler_ref = None
    return risk


def _snapshot(pos) -> dict:
    """Stan pozycji po ticku. Pola dobrane tak, by zmiana trailingu, partiala
    albo ksiegowania byla widoczna, a nie tylko sam fakt zamkniecia."""
    return {
        "status": getattr(pos, "status", None),
        "sl_price": _round(getattr(pos, "sl_price", None)),
        "trailing_stop_price": _round(getattr(pos, "trailing_stop_price", None)),
        "trailing_active": bool(getattr(pos, "trailing_active", False)),
        "breakeven_active": bool(getattr(pos, "breakeven_active", False)),
        "partial_taken": bool(getattr(pos, "partial_taken", False)),
        "partial_tp1_done": bool(getattr(pos, "partial_tp1_done", False)),
        "partial_tp2_done": bool(getattr(pos, "partial_tp2_done", False)),
        "partial_stage": getattr(pos, "partial_stage", None),
        "size_usd": _round(getattr(pos, "size_usd", None)),
        "pnl": _round(getattr(pos, "pnl", None)),
        "pnl_pct": _round(getattr(pos, "pnl_pct", None)),
        "highest_price": _round(getattr(pos, "highest_price", None)),
        "lowest_price": _round(getattr(pos, "lowest_price", None)),
    }


class RecordingLogger:
    """Zamiast wylaczac logger - podsluchujemy go.

    `label` (TP/SL/TRAIL/...) powstaje wewnatrz close_position i trafia
    wylacznie do CSV. To on jest widoczny dla uzytkownika, a mapa etykiet jest
    niepelna - czesc powodow leci surowa. Odtwarzanie tej mapy w bramce
    dublowaloby regule; taniej i uczciwiej jest zapisac to, co naprawde
    wychodzi."""

    def __init__(self):
        self.events: list = []

    def log_event(self, *args, **kwargs):
        event = str(args[0]) if args else str(kwargs.get("event") or "")
        exit_tag = None
        for value in list(args) + list(kwargs.values()):
            if isinstance(value, str) and value.startswith("exit="):
                exit_tag = value
                break
        self.events.append({"event": event, "exit": exit_tag})

    def __getattr__(self, name):
        return lambda *a, **k: None


def _wrap_reason(fn, kind: str, sink: list):
    """Powod zamkniecia nie jest zapisywany na pozycji - lapiemy go w locie."""

    def inner(*args, **kwargs):
        reason = kwargs.get("reason")
        if reason is None and len(args) >= 3:
            reason = args[2]
        sink.append({"kind": kind, "reason": str(reason)})
        return fn(*args, **kwargs)

    return inner


def _closed_row(pos) -> dict:
    return {
        "symbol": getattr(pos, "symbol", None),
        "exit_price": _round(getattr(pos, "exit_price", None)),
        "pnl": _round(getattr(pos, "pnl", None)),
        "pnl_pct": _round(getattr(pos, "pnl_pct", None)),
        "fees_paid": _round(getattr(pos, "fees_paid", None)),
        "size_usd": _round(getattr(pos, "size_usd", None)),
    }


def evaluate(case: dict) -> dict:
    """Przebieg jednego przypadku. Wyjatek jest wynikiem, nie awaria bramki -
    ukrycie go zamienilo by bramke w zrodlo falszywego spokoju."""
    import config
    import paper_trader as pt

    out = {"case": case["case"], "ticks": []}
    overrides = dict(FORCED_CONFIG)
    overrides.update(case.get("config") or {})
    saved = {key: getattr(config, key, _MISSING) for key in overrides}
    real_time = pt.time
    clock = FakeClock()
    pt.time = clock
    try:
        for key, value in overrides.items():
            setattr(config, key, value)
        risk = _fresh_risk()
        prot = RecordingProtection(case.get("protection")) if case.get("protection") else None
        logger = RecordingLogger()
        trader = pt.PaperTrader(risk, logger=logger, protection=prot)
        reasons: list = []
        trader.close_position = _wrap_reason(trader.close_position, "close", reasons)
        trader.partial_close = _wrap_reason(trader.partial_close, "partial", reasons)
        row = dict(case["row"])
        row["entry_time"] = (
            datetime.now() - timedelta(minutes=float(case.get("age_minutes") or 0))
        ).isoformat()
        trader.restore_open_positions([row])
        risk._positions_ref = list(trader.positions)
        pos = trader.positions[0] if trader.positions else None
        symbol = row["symbol"]

        for tick in case["ticks"]:
            clock.advance(tick.get("advance_s") or 0)
            signals = [tick["signal"]] if tick.get("signal") else []
            price_map = {symbol: tick["price"]} if tick["price"] is not None else {}
            before_closed = len(trader.closed_positions)
            before_partial = len(trader.partial_closes)
            before_reasons = len(reasons)
            before_events = len(logger.events)
            trader.check_exits(signals, price_map)
            new_closed = trader.closed_positions[before_closed:]
            new_partial = trader.partial_closes[before_partial:]
            out["ticks"].append({
                "price": _round(tick["price"]),
                "reasons": reasons[before_reasons:],
                "labels": logger.events[before_events:],
                "closed": [_closed_row(p) for p in new_closed],
                "partials": [_closed_row(p) for p in new_partial],
                "open_count": len(trader.positions),
                "position": _snapshot(pos) if pos is not None else None,
            })
        out["sl_updates"] = list(prot.calls) if prot is not None else []
        out["capital"] = _round(risk.current_capital)
        out["open_at_end"] = len(trader.positions)
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


def _reasons_of(result: dict) -> list:
    out = []
    for tick in result.get("ticks") or []:
        for row in tick.get("reasons") or []:
            out.append(str(row.get("reason")))
    return out


def run_gate() -> dict:
    saved = _install_stubs()
    try:
        cases = build_corpus() + build_signal_corpus() + build_edge_corpus()
        results = [evaluate(case) for case in cases]
        reasons = sorted({r for res in results for r in _reasons_of(res)})
        raised = [r["case"] for r in results if r.get("raised")]
        closes = sum(len(_reasons_of(r)) for r in results)
        return {
            "meta": {"cases": len(results), "exit_events": closes,
                     "distinct_reasons": len(reasons), "raised": len(raised)},
            "config": config_fingerprint(),
            "reasons": reasons,
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
        if before.get("raised") != after.get("raised"):
            problems.append(f"  ~ {name}.raised: "
                            f"{before.get('raised')!r} -> {after.get('raised')!r}")
        for field in ("sl_updates", "capital", "open_at_end"):
            if before.get(field) != after.get(field):
                problems.append(f"  ~ {name}.{field}: "
                                f"{before.get(field)!r} -> {after.get(field)!r}")
        old_ticks = before.get("ticks") or []
        new_ticks = after.get("ticks") or []
        if len(old_ticks) != len(new_ticks):
            problems.append(f"  ~ {name}: tickow {len(old_ticks)} -> {len(new_ticks)}")
            continue
        for i, (o_t, n_t) in enumerate(zip(old_ticks, new_ticks)):
            for field in ("reasons", "labels", "closed", "partials",
                          "open_count", "position"):
                if o_t.get(field) != n_t.get(field):
                    problems.append(
                        f"  ~ {name}[tick {i}].{field}: "
                        f"{o_t.get(field)!r} -> {n_t.get(field)!r}")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Bramka charakterystyki wyjsc z pozycji.")
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument("--coverage", action="store_true",
                    help="wypisz powody pokryte przez korpus")
    args = ap.parse_args(argv)

    current = json.loads(json.dumps(run_gate()))
    meta = current["meta"]
    print(f"[exit] przypadkow {meta['cases']} | zdarzen wyjscia {meta['exit_events']}"
          f" | roznych powodow {meta['distinct_reasons']} | wyjatkow {meta['raised']}"
          f" | config {current['config'].get('hash')}")

    if args.coverage:
        print("\nPowody pokryte przez korpus:")
        for reason in current["reasons"]:
            print(f"  {reason}")
        raised = [r["case"] for r in current["results"] if r.get("raised")]
        if raised:
            print("\nPrzypadki z wyjatkiem:")
            for name in raised:
                print(f"  {name}")
        return 0

    if args.write_baseline:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(
            json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[exit] zapisano baseline: {args.baseline}")
        return 0

    if not args.baseline.is_file():
        print(f"[exit] BRAK baseline ({args.baseline}). "
              f"Utworz: python tools/exit_gate.py --write-baseline")
        return 2

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    problems = compare(baseline, current)
    if not problems:
        print("[exit] IDENTYCZNIE — zamykanie pozycji bez zmian.")
        return 0
    print(f"[exit] ROZNI SIE ({len(problems)} pozycji):")
    for line in problems[:60]:
        print(line)
    if len(problems) > 60:
        print(f"  ... i {len(problems) - 60} wiecej")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
