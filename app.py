# ============================================================
# CryptoEdge Application
# Uruchom: python app.py
# UI: natywne okno PySide6 (patrz pyside6_ui.py)
# ============================================================

import json
import sys
import time
import threading
from datetime import datetime, timezone
from pathlib import Path

# Konsola -> logs/console.log (429, warmup, traceback) ZANIM cokolwiek sie wydrukuje.
try:
    from console_capture import install as _install_console
    _CONSOLE_LOG = _install_console()
    print(f"[App] Konsola zapisywana do {_CONSOLE_LOG}")
except Exception as _e:
    print(f"[App] console_capture: {_e}")

import config
import settings_store
from data_feeder import DataFeeder
from scan_scheduling import is_full_scan_due
from warmup import WARMUP, warmup_applies
from market_store import STORE
from event_bus import build_event_bus
from grpc_service import GrpcServer
from signal_engine import SignalEngine
from cryptoedge.risk.manager import RiskManager
from cryptoedge.execution.paper import PaperTrader
from logger import BotLogger
from correlation import summarize_market_correlation, print_correlation_report
from runtime import BotRuntime
from account_sync import AccountSync

try:
    from cryptoedge.apps.runtime import open_entry
except Exception as _exc:  # pragma: no cover - awaryjna sciezka importu
    # Ta sama zasada, co przy attach_runtime_modules nizej: migracja
    # modulowa nie moze wylaczyc handlu. Gdy pakiet sie nie zaladuje,
    # wejscie idzie stara droga, prosto do ksiegi.
    print(f"[Modules] open_entry degraded: {_exc}")

    def open_entry(rt, trader, signal):
        return trader.open_position(signal)

BASE = Path(__file__).resolve().parent

# Zastosuj zapisany tryb przed utworzeniem silników i pierwszym cyklem.
settings_store.apply_settings()


def load_previous_state(risk: RiskManager, trader: PaperTrader) -> bool:
    """Odtwarza stan wyłącznie dla LIVE.

    PAPER jest niezależną sesją symulacyjną: start aplikacji zawsze zaczyna od
    STARTING_CAPITAL i pustego portfela. Historyczne CSV/telemetria zostają na
    dysku do analizy, ale nie mogą ponownie tworzyć pozycji ani przenosić PnL.
    """
    if bool(getattr(config, "PAPER_TRADING", True)):
        print("[App] PAPER: nowa sesja — nie wczytuję starego kapitału ani pozycji")
        return False
    state_rel = str(getattr(config, "STATE_FILE", "logs/bot_state.json") or "logs/bot_state.json")
    state_path = BASE / state_rel
    if not state_path.exists():
        return False
    try:
        prev = json.loads(state_path.read_text(encoding="utf-8"))
        if prev.get("risk"):
            risk.current_capital = float(prev["risk"].get("capital") or risk.current_capital)
            risk.peak_equity = float(prev["risk"].get("peak_equity") or risk.current_capital)
            risk.daily_pnl = float(prev["risk"].get("daily_pnl") or 0)
        if prev.get("saved_positions"):
            trader.restore_open_positions(prev["saved_positions"])
        print(f"[App] Zaladowano stan (kapital ${risk.current_capital:.4f})")
        return True
    except Exception as e:
        print(f"[App] Nie udalo sie zaladowac stanu: {e}")
        return False


def _bot_version() -> str:
    """Wersja z version.py (jedno zrodlo prawdy) - z bezpiecznym fallbackiem,
    zeby brak/uszkodzenie version.py nigdy nie wywalilo zapisu stanu cyklu."""
    try:
        from version import tag
        return tag()
    except Exception:
        return "unknown"


def refresh_open_position_prices(feeder, trader, price_map: dict) -> dict:
    """Bypass the scanner cache for protective exits on open positions."""
    out = dict(price_map or {})
    try:
        symbols = [p.symbol for p in (getattr(trader, "positions", None) or [])]
        if symbols:
            out.update(feeder.blofin.fetch_last_prices(symbols))
    except Exception as e:
        print(f"[Protect] fresh price snapshot: {e}")
    return out


def persist_market_preview(rt: BotRuntime, coins, cycle) -> None:
    """Zapis uniwersum/cen bez sygnalow. Warmup i Blad cyklu nie zostawiaja pustego UI."""
    coins = list(coins or [])
    price_map = {c["symbol"]: c.get("price") for c in coins if c.get("symbol")}
    rt.last_price_map = price_map
    rt.last_price_map_ts = time.time()
    btc_chg = next((c.get("change_24h") for c in coins if c.get("symbol") == "BTC"), None)
    persist_cycle(rt, coins, [], [], {}, {}, btc_chg, cycle)


def persist_cycle(rt: BotRuntime, coins, signals, active, corr, mctx, btc_change, cycle, signal_engine=None):
    risk, trader, logger, feeder = rt.risk, rt.trader, rt.logger, rt.feeder
    status = risk.get_status()
    funds = trader.get_funds_breakdown()
    status.update(funds)
    open_sum = trader.get_open_summary()
    price_map = rt.last_price_map

    for p in open_sum:
        cur = price_map.get(p["symbol"])
        p["market"] = cur
        if cur and p.get("entry"):
            raw = (cur - p["entry"]) / p["entry"] * 100
            if p["direction"] == "SHORT":
                raw = -raw
            p["market_diff_pct"] = round(raw, 2)
        else:
            p["market_diff_pct"] = None

    dash_signals = []
    for s in active[:12]:
        dash_signals.append({
            "symbol": s.get("symbol"),
            "direction": s.get("direction"),
            "strength": s.get("strength"),
            "price": s.get("price"),
            "change_24h": s.get("change_24h"),
            "change_1h": s.get("change_1h"),
            "rs": s.get("rs"),
            "rsi": s.get("rsi"),
            "macd_signal": s.get("macd_signal"),
            "reasons": s.get("reasons") or [],
            "tp_price": s.get("tp_price"),
            "sl_price": s.get("sl_price"),
            "source_div": (s.get("source_div") or {}).get("max_diff_pct"),
            "trend": s.get("trend"),
            "trend_score": s.get("trend_score"),
            "categories": s.get("categories") or [],
            "sectors": s.get("sectors") or [],
            "strategy_pass": (s.get("strategy") or {}).get("pass"),
            "strategy_direction": (s.get("strategy") or {}).get("direction"),
            "strategy_adx": (s.get("strategy") or {}).get("adx"),
            "strategy_st": (s.get("strategy") or {}).get("supertrend"),
            "decision_path": s.get("decision_path"),
            "engine": s.get("engine"),
            "setup": s.get("setup"),
            "confirmation_status": s.get("confirmation_status"),
            "confirmation_count": s.get("confirmation_count"),
            "expected_net_r": s.get("expected_net_r"),
            "expected_gross_r": s.get("expected_gross_r"),
            "expected_r_breakdown": s.get("expected_r_breakdown") or {},
            "trend_fib": s.get("trend_fib"),
            "score_components": s.get("score_components") or {},
            "divergence": s.get("divergence") or s.get("source_divergence"),
            "tp1_price": s.get("tp1_price"),
            "tp2_price": s.get("tp2_price"),
            "vol_flag": s.get("vol_flag"),
            "volume_24h": s.get("volume_24h"),
            "ob_imbalance": (s.get("order_book") or {}).get("ob_imbalance"),
            "ob_bias": (s.get("order_book") or {}).get("ob_bias"),
            "ob_spread_pct": (s.get("order_book") or {}).get("ob_spread_pct"),
        })

    # Market scanner: zachowujemy szeroki obraz rynku (nie tylko aktywne setupy).
    # UI może dzięki temu działać jak Opportunity Scanner / MTF workspace.
    try:
        analysis_src = list(getattr(rt.signal_engine, "last_analysis_board", []) or [])
    except Exception:
        analysis_src = []
    signal_lookup = {}
    for src in list(signals or []) + list(active or []) + analysis_src:
        sym = str(src.get("symbol") or "").upper()
        if sym:
            signal_lookup[sym] = src
    scanner_assets = []
    for coin in (coins or []):
        sym = str(coin.get("symbol") or "").upper()
        if not sym:
            continue
        src = signal_lookup.get(sym, {})
        ana = src.get("analysis") if isinstance(src.get("analysis"), dict) else src
        mtf = (ana.get("mtf_summary") if isinstance(ana, dict) else None) or src.get("mtf_summary") or {}
        scanner_assets.append({
            "symbol": sym,
            "price": coin.get("price", src.get("price")),
            "change_1h": coin.get("change_1h", src.get("change_1h")),
            "change_24h": coin.get("change_24h", src.get("change_24h")),
            "change_7d": coin.get("change_7d", src.get("change_7d")),
            "direction": src.get("direction", ana.get("direction") if isinstance(ana, dict) else None) or "NEUTRAL",
            "strength": src.get("strength", ana.get("strength") if isinstance(ana, dict) else 0) or 0,
            "trend": src.get("trend", ana.get("trend") if isinstance(ana, dict) else None),
            "rsi": src.get("rsi", ana.get("rsi") if isinstance(ana, dict) else None),
            "macd": src.get("macd_signal", src.get("macd", ana.get("macd") if isinstance(ana, dict) else None)),
            "atr_pct": src.get("atr_pct", ana.get("atr_pct") if isinstance(ana, dict) else None),
            "volume_24h": src.get("volume_24h", coin.get("volume_24h")),
            "vol_flag": src.get("vol_flag"),
            "signal_status": src.get("signal_status", ana.get("signal_status") if isinstance(ana, dict) else None) or "NEUTRAL",
            "signal_summary": src.get("signal_summary", ana.get("signal_summary") if isinstance(ana, dict) else None),
            "decision_path": src.get("decision_path", ana.get("decision_path") if isinstance(ana, dict) else None),
            "decision": src.get("decision", ana.get("decision") if isinstance(ana, dict) else None),
            "decision_why": src.get("decision_why", ana.get("decision_why") if isinstance(ana, dict) else None),
            "tp_price": src.get("tp_price", ana.get("tp_price") if isinstance(ana, dict) else None),
            "sl_price": src.get("sl_price", ana.get("sl_price") if isinstance(ana, dict) else None),
            "tp1_price": src.get("tp1_price", ana.get("tp1_price") if isinstance(ana, dict) else None),
            "tp2_price": src.get("tp2_price", ana.get("tp2_price") if isinstance(ana, dict) else None),
            "trend_fib": src.get("trend_fib", ana.get("trend_fib") if isinstance(ana, dict) else None),
            "fib_retracement": src.get("fib_retracement", ana.get("fib_retracement") if isinstance(ana, dict) else None),
            "divergence": src.get("divergence", src.get("source_divergence", ana.get("divergence") if isinstance(ana, dict) else None)),
            "liquidity": src.get("liquidity", ana.get("liquidity") if isinstance(ana, dict) else None),
            "ob_bias": (src.get("order_book") or {}).get("ob_bias", src.get("ob_bias")),
            "ob_imbalance": (src.get("order_book") or {}).get("ob_imbalance", src.get("ob_imbalance")),
            "mtf_summary": mtf,
            "score_components": src.get("score_components", ana.get("score_components") if isinstance(ana, dict) else {}) or {},
            "reasons": list(src.get("reasons") or (ana.get("pros") if isinstance(ana, dict) else []) or [])[:6],
            "cons": list(src.get("cons") or (ana.get("cons") if isinstance(ana, dict) else []) or [])[:6],
        })
    scanner_assets.sort(key=lambda x: (float(x.get("strength") or 0), abs(float(x.get("change_24h") or 0))), reverse=True)

    # Krótka historia equity do wykresów UI / audytu. Trzymamy ograniczony bufor,
    # żeby stan bota nie puchł bez końca.
    equity_history = []
    try:
        state_file = BASE / "logs" / "bot_state.json"
        if state_file.exists():
            prev = json.loads(state_file.read_text(encoding="utf-8"))
            equity_history = list(prev.get("equity_history") or [])[-499:]
    except Exception:
        equity_history = []
    eq_now = status.get("equity", status.get("capital"))
    if eq_now is not None:
        equity_history.append({
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "equity": round(float(eq_now), 4),
            "capital": round(float(status.get("capital") or 0), 4),
            "daily_pnl": round(float(status.get("daily_pnl") or 0), 4),
            "drawdown_pct": round(float(status.get("max_drawdown_pct") or 0), 2),
        })

    v2_rows = [
        row for row in (signals or [])
        if str(row.get("engine") or "").lower() == "daytrading_v2"
    ]
    v2_cold = sum(
        1 for row in v2_rows
        if str(row.get("reject_reason") or "").upper() == "V2_COLD_START_WARMING_UP"
    )
    engine_warmup = {
        "active": bool(v2_rows) and v2_cold > 0,
        "ready": bool(v2_rows) and v2_cold == 0,
        "available_coins": max(0, len(v2_rows) - v2_cold),
        "total_coins": len(v2_rows),
        "remaining_coins": v2_cold,
    }

    logger.save_state(status, open_sum, extra={
        "cycle": cycle,
        "equity_history": equity_history,
        "universe_size": len(coins),
        "sources": feeder.sources_status(),
        "last_error": rt.last_error,
        "feed_events": list(getattr(rt, "feed_events", []) or [])[:8],
        "btc_change_24h": btc_change,
        "btc_price": next((c["price"] for c in coins if c["symbol"] == "BTC"), None),
        "eth_price": next((c["price"] for c in coins if c["symbol"] == "ETH"), None),
        "closed_positions": trader.get_closed_summary(25),
        "correlation": {
            "multi_source": corr.get("coins_multi_source"),
            "avg_diff_pct": corr.get("avg_diff_pct"),
            "max_diff_pct": corr.get("max_diff_pct"),
            "warning_count": corr.get("warning_count"),
            "top_divergent": [
                {"symbol": d["symbol"], "max_diff_pct": d["max_diff_pct"]}
                for d in (corr.get("top_divergent") or [])[:5]
            ],
        },
        "signals": dash_signals,
        "strategy_mode": str(getattr(config, "STRATEGY_MODE", "DAYTRADING") or "DAYTRADING").upper(),
        "reversal_diag": getattr(signal_engine, "last_reversal_diag", None),
        "bot_version": _bot_version(),
        "scanner_assets": scanner_assets[:150],
        "closed_count": len(trader.closed_positions),
        "trade_count": trader.trade_count,
        "market": mctx if isinstance(mctx, dict) else {},
        "saved_positions": trader.export_open_positions(),
        "pending_orders": trader.pending_limit_orders(),
        "metrics": trader.session_metrics(),
        "rejects": list(getattr(risk, "reject_log", []) or []),
        "market_regime": getattr(getattr(rt, "signal_engine", None), "last_regime", {}) or {},

        "analysis_board": (
            sorted(
                list(getattr(rt.signal_engine, "last_analysis_board", []) or []),
                key=lambda x: str(x.get("symbol") or "").upper(),
            )
            if (rt.signal_engine and getattr(rt.signal_engine, "last_analysis_board", None))
            else sorted(
                [
                    (s.get("analysis") or {
                        "symbol": s.get("symbol"),
                        "direction": s.get("direction"),
                        "strength": s.get("strength"),
                        "decision": "OPEN_OK" if s.get("direction") not in (None, "NEUTRAL") else "SKIP",
                        "decision_why": ", ".join((s.get("reasons") or [])[:3]) or "—",
                        "signal_status": (
                            "OK" if s.get("direction") not in (None, "NEUTRAL") and not s.get("reject_reason")
                            else ("INEFFECTIVE" if s.get("reject_reason") else "NEUTRAL")
                        ),
                        "signal_summary": (
                            f"Nieefektywna: {s.get('reject_reason')}"
                            if s.get("reject_reason")
                            else (", ".join((s.get("reasons") or [])[:3]) or "brak sygnału")
                        ),
                        "pros": list(s.get("reasons") or [])[:6],
                        "cons": [s["reject_reason"]] if s.get("reject_reason") else [],
                        "price": s.get("price"),
                        "rsi": s.get("rsi"),
                        "macd": s.get("macd_signal") or s.get("macd"),
                        "trend": s.get("trend"),
                        "change_24h": s.get("change_24h"),
                        "change_1h": s.get("change_1h"),
                    })
                    for s in (signals or active or [])
                ],
                key=lambda x: str(x.get("symbol") or "").upper(),
            )
        ),


        "config": {
            "leverage": config.LEVERAGE,
            "max_positions": config.MAX_POSITIONS,
            "tp_pct": config.TAKE_PROFIT_PCT,
            "sl_pct": config.STOP_LOSS_PCT,
            "daily_loss_limit": config.DAILY_LOSS_LIMIT,
            "starting_capital": config.STARTING_CAPITAL,
        },
        "running": rt.running,
        "exchange_account": _exchange_snapshot(rt),
        "warmup": (getattr(rt, "last_state_snapshot", None) or {}).get("warmup"),
        "engine_warmup": engine_warmup,
    })

    # Punkt 9 planu: zdarzenia cyklu/odrzucen do "laboratorium" (Redis Streams,
    # patrz event_bus.py). Domyslnie wylaczone (EVENT_BUS_ENABLED=False) -
    # wtedy to zero-cost (NullBackend, brak prob polaczenia). Kazdy blad
    # publikacji jest juz obslugiwany wewnatrz event_bus.py (zwraca False,
    # nigdy nie rzuca) - nie owijamy tego w dodatkowy try/except tutaj.
    bus = getattr(rt, "event_bus", None)
    if bus is not None:
        bus.publish_cycle(
            cycle=cycle, universe_size=len(coins), signals_count=len(signals),
            active_count=len(active), btc_change_24h=btc_change,
            equity=status.get("equity"), open_positions=len(open_sum),
        )
        for s in signals:
            reason = s.get("reject_reason")
            if reason:
                bus.publish_reject(
                    cycle=cycle, symbol=s.get("symbol"), reason=reason,
                    engine=s.get("engine"), strength=s.get("strength"),
                )


def _exchange_snapshot(rt: BotRuntime) -> dict:
    """Read-only snapshot konta Blofin (LIVE). Paper → pusty."""
    try:
        sync = getattr(rt, "account_sync", None)
        if not sync:
            return {"mode": "PAPER", "read_only": True, "positions": []}
        # odśwież max co LIVE_BALANCE_CACHE_SECONDS
        return sync.sync(force=False) or sync.snapshot()
    except Exception as e:
        return {"mode": "LIVE", "error": str(e), "read_only": True, "positions": []}


def bot_loop(rt: BotRuntime):
    """Watek zawsze zywy; handel dopiero po Start (engine_enabled)."""
    feeder = rt.feeder
    signal_engine = rt.signal_engine
    risk = rt.risk
    trader = rt.trader
    logger = rt.logger
    rt.running = True
    print(f"[{datetime.now().strftime('%H:%M:%S')}] UI gotowe – kliknij START w aplikacji aby uruchomic bota.")

    def wait_for_next_cycle(seconds):
        """Czekaj na interwal lub natychmiastowa akcje ANALIZA/START z UI."""
        wake = getattr(rt, "analysis_wakeup", None)
        if wake is None:
            time.sleep(seconds)
            return
        wake.wait(max(0.0, float(seconds)))
        wake.clear()

    while rt.running:
        try:
            try:
                rt.apply_control_files(BASE)
            except Exception as e:
                print(f"[Control] {e}")
            if not getattr(rt, "engine_enabled", False):
                # STOP: bez nowych sygnalow, ale TP/SL dla otwartych pozycji.
                # Cena tylko dla trzymanych symboli (1 zbiorcze zapytanie) -
                # bez fetch_top_coins() na calym uniwersum, ktore tu nie jest
                # w ogole potrzebne (nic sie nie analizuje w tej galezi).
                try:
                    if trader.positions:
                        price_map = refresh_open_position_prices(feeder, trader, dict(rt.last_price_map or {}))
                        if price_map:
                            rt.last_price_map = price_map
                            rt.last_price_map_ts = time.time()
                            trader.update_positions(price_map)
                            trader.check_exits([], price_map)
                except Exception as e:
                    print(f"[STOP-monitor] {e}")
                wait_for_next_cycle(max(1.0, float(getattr(config, "LOOP_INTERVAL_SECONDS", 3))))
                continue

            # Pelny skan (uniwersum + generowanie sygnalow) leci we wlasnym,
            # wolniejszym rytmie (FULL_SCAN_INTERVAL_SECONDS) - patrz komentarz
            # przy tej stalej w config.py. Miedzy pelnymi skanami: szybki tick
            # (swieza cena tylko dla otwartych pozycji + trailing/SL/emergency/
            # halt-checks), w tempie LOOP_INTERVAL_SECONDS.
            full_scan_interval = max(1.0, float(getattr(config, "FULL_SCAN_INTERVAL_SECONDS", 20.0)))
            last_full_scan = float(getattr(rt, "last_full_scan_ts", 0.0) or 0.0)
            due_for_full_scan = is_full_scan_due(last_full_scan, full_scan_interval)

            if not due_for_full_scan:
                rt.heartbeat()
                try:
                    # Saldo/pozycje LIVE odswiezane co LIVE_BALANCE_CACHE_SECONDS
                    # (domyslnie 15s, wlasny cache w AccountSync.sync()) - wolamy
                    # co fast-tick, ale realnie hita siec tylko raz na okno cache.
                    # Wczesniej wolane tylko raz przy starcie bota (force=True),
                    # przez co saldo/pozycje LIVE nigdy sie nie odswiezaly w
                    # trakcie sesji.
                    sync = getattr(rt, "account_sync", None)
                    if sync is not None:
                        sync.sync(force=False)
                    if trader.positions:
                        price_map = refresh_open_position_prices(feeder, trader, dict(rt.last_price_map or {}))
                        if price_map:
                            rt.last_price_map = price_map
                            rt.last_price_map_ts = time.time()
                            trader.update_positions(price_map)
                            trader.check_exits(list(getattr(rt, "last_signals", None) or []), price_map)
                            funds_tmp = trader.get_funds_breakdown()
                            equity = funds_tmp.get("equity", risk.current_capital)
                            if risk.check_drawdown(equity):
                                print(f"[Risk] {risk.halt_reason}")
                                if getattr(config, "CLOSE_ALL_ON_DRAWDOWN", True) and trader.positions:
                                    trader.close_all(price_map, reason="drawdown")
                            if risk.is_halted and getattr(config, "CLOSE_ALL_ON_DAILY_LIMIT", True):
                                if risk.halt_reason and "Daily" in risk.halt_reason and trader.positions:
                                    trader.close_all(price_map, reason="daily_limit")
                except Exception as e:
                    print(f"[FastTick] {e}")
                wait_for_next_cycle(config.LOOP_INTERVAL_SECONDS)
                continue

            rt.last_full_scan_ts = time.time()
            rt.cycle += 1
            rt.heartbeat()
            cycle = rt.cycle
            try:
                rt.maybe_reconcile(cycle)
            except Exception as e:
                print(f"[Reconcile] {e}")
            now = datetime.now().strftime("%H:%M:%S")
            print(f"\n{'='*55}\n Cykl #{cycle} | {now}\n{'='*55}")
            try:
                snap = getattr(rt, "last_state_snapshot", None) or {}
                snap["sources"] = feeder.sources_status()
                rt.last_state_snapshot = snap
            except Exception:
                pass

            coins = feeder.fetch_top_coins()
            rt.last_coins = coins
            rt.heartbeat()
            if not coins:
                print("Brak danych – czekam...")
                wait_for_next_cycle(config.LOOP_INTERVAL_SECONDS)
                continue

            try:
                STORE.set_tickers({c.get("symbol"): c for c in coins if c.get("symbol")})
                if getattr(feeder, "blofin", None) is not None:
                    ws = getattr(feeder.blofin, "ws", None) or getattr(feeder, "blofin_ws", None)
                    alive = bool(getattr(ws, "connected", False) or getattr(ws, "is_connected", False))
                    STORE.mark_ws(alive)
            except Exception as e:
                print(f"[Warmup] store seed: {e}")
            if warmup_applies():
                try:
                    if not WARMUP.active and not WARMUP.ready:
                        WARMUP.start()
                        dur = int(getattr(config, "WARMUP_SECONDS", 90))
                        print(f"[Warmup] start {dur}s – ANALIZA bez wejsc do bramki")
                    wst = WARMUP.tick(feeder, coins) or {}
                except Exception as e:
                    print(f"[Warmup] tick: {e}")
                    wst = {"active": True, "ready": False, "phase": "error", "error": str(e)}
                snap = getattr(rt, "last_state_snapshot", None) or {}
                snap["warmup"] = wst
                rt.last_state_snapshot = snap
                warming = bool(getattr(WARMUP, "active", False) and not getattr(WARMUP, "ready", False))
                if wst.get("error") and not getattr(WARMUP, "ready", False):
                    warming = True
                if warming:
                    print("[Warmup] pominieto generate_signals / otwarcia")
                    try:
                        persist_market_preview(rt, coins, cycle)
                    except Exception as pe:
                        print(f"[Warmup] persist: {pe}")
                    wait_for_next_cycle(config.LOOP_INTERVAL_SECONDS)
                    continue

            if feeder.is_data_stale():
                age = feeder.data_age_seconds()
                print(f"[STALE] Dane maja {age:.0f}s – tylko zarzadzanie pozycjami")
                try:
                    from alerts import alert_feed_fail
                    alert_feed_fail(f"Stale data {age:.0f}s")
                except Exception:
                    pass
                price_map = {c["symbol"]: c["price"] for c in coins}
                price_map = refresh_open_position_prices(feeder, trader, price_map)
                rt.last_price_map = price_map
                rt.last_price_map_ts = time.time()
                trader.update_positions(price_map)
                trader.check_exits([], price_map)
                wait_for_next_cycle(config.LOOP_INTERVAL_SECONDS)
                continue

            btc_change = feeder.get_btc_change(coins)
            risk.set_btc_change(btc_change)
            print(f"Universe: {len(coins)} par | BTC 24h: {btc_change:+.2f}%")
            print(f"Zrodla: {feeder.sources_status()}")
            mctx = feeder.get_market_context()
            try:
                print(f"Rynek: {feeder.market_ctx.status_line(mctx)}")
            except Exception:
                mctx = mctx or {}

            corr = summarize_market_correlation(coins)
            print_correlation_report(corr)

            _scan_t0 = time.time()
            signals = signal_engine.generate_signals(coins, btc_change)
            _scan_duration = time.time() - _scan_t0
            _scan_budget = max(1.0, float(getattr(config, "FULL_SCAN_INTERVAL_SECONDS", 20.0)))
            if _scan_duration > _scan_budget:
                # Siatka bezpieczenstwa dla adaptacyjnego DAYTRADING_MAX_CANDIDATES_WS_CONNECTED
                # (brak limitu, gdy WS polaczony) - jesli czas obliczen
                # wskaznikow (nie mierzony bezposrednio przy dostrajaniu
                # limitow REST) zacznie przekraczac budzet skanu, to musi
                # byc widoczne w logu, nie ciche.
                print(
                    f"[App] UWAGA: pełny skan trwał {_scan_duration:.1f}s, "
                    f"przekraczając budżet FULL_SCAN_INTERVAL_SECONDS={_scan_budget:.0f}s "
                    f"({len(coins)} kandydatów) - rozważ obniżenie DAYTRADING_MAX_CANDIDATES_WS_CONNECTED"
                )
            rt.last_signals = signals
            try:
                trader.v2_engine = getattr(signal_engine, "daytrading_engine_v2", None)
            except Exception:
                pass
            try:
                reg = getattr(signal_engine, "last_regime", {}) or {}
                risk.set_regime(reg.get("regime") or "UNKNOWN")
            except Exception:
                pass
            active = [s for s in signals if s["direction"] != "NEUTRAL"]
            print(f"Aktywne sygnaly: {len(active)}")
            if risk.reject_log:
                rej = ", ".join(
                    "%s(%s)" % (r.get("symbol"), r.get("reason"))
                    for r in risk.reject_log[:5]
                )
                print("Odrzucone (ostatnie): " + rej)
            try:
                signal_engine.print_analysis(signals, limit=6)
            except Exception as pe:
                print(f"[App] print_analysis: {pe}")
            # Odrzucone z filtrów sygnału → reject_log (UI Odrzucone)
            for s in signals:
                if not bool(s.get("decision_fresh", True)):
                    continue
                rr = s.get("reject_reason")
                if rr:
                    try:
                        risk.log_reject(s.get("symbol"), s.get("direction"), s.get("strength"), rr, signal=s)
                    except Exception:
                        pass
                elif (
                    s.get("direction") != "NEUTRAL"
                    and str(s.get("engine") or "").lower() not in ("daytrading_v2", "daytradingv2")
                    and s.get("strategy_mode") != "DAYTRADING_V2"
                    and float(s.get("strength") or 0) < config.MIN_SIGNAL_STRENGTH
                ):
                    try:
                        risk.log_reject(s.get("symbol"), s.get("direction"), s.get("strength"),
                                        f"WEAK({s.get('strength'):.2f})", signal=s)
                    except Exception:
                        pass
            logger.log_signals([
                s for s in signals if bool(s.get("decision_fresh", True))
            ])
            # Shadow Mode: rejestruj reversal candidates + update prices
            try:
                from shadow_mode import process_reversal_signals
                px_map = {s["symbol"]: float(s["price"]) for s in (signals or [])
                          if s.get("symbol") and s.get("price")}
                process_reversal_signals([
                    s for s in (signals or []) if bool(s.get("decision_fresh", True))
                ], price_map=px_map)
            except Exception:
                pass


            price_map = {c["symbol"]: c["price"] for c in coins}
            price_map = refresh_open_position_prices(feeder, trader, price_map)
            rt.last_price_map = price_map
            rt.last_price_map_ts = time.time()
            trader.update_positions(price_map)
            trader.check_exits(signals, price_map)

            funds_tmp = trader.get_funds_breakdown()
            equity = funds_tmp.get("equity", risk.current_capital)
            if risk.check_drawdown(equity):
                print(f"[Risk] {risk.halt_reason}")
                if getattr(config, "CLOSE_ALL_ON_DRAWDOWN", True) and trader.positions:
                    trader.close_all(price_map, reason="drawdown")

            if risk.is_halted and getattr(config, "CLOSE_ALL_ON_DAILY_LIMIT", True):
                if risk.halt_reason and "Daily" in risk.halt_reason and trader.positions:
                    trader.close_all(price_map, reason="daily_limit")

            # synchronizacja licznika pozycji (po ręcznym close)
            try:
                risk.sync_open_count(len(trader.positions))
            except Exception:
                pass

            paper = bool(getattr(config, "PAPER_TRADING", True))
            trade_on = bool(getattr(rt, "trading_enabled", False))
            candidates = sorted(
                [s for s in signals
                 if s.get("direction") != "NEUTRAL"
                 and bool(s.get("decision_fresh", True))],
                key=lambda x: float(x.get("strength") or 0),
                reverse=True,
            )
            opened = 0
            skips = []
            block = ""
            if not trade_on:
                block = "TRADE_OFF"
                print("[ANALIZA] Cykle aktywne – handel OFF (Start handlu)")
            elif risk.paused:
                block = "PAUSED"
                print("[PAUSE] Nowe wejscia wstrzymane – Start handlu / Resume")
            elif risk.is_halted:
                block = risk.halt_reason or "HALT"
                print(f"[HALT] {block}")
            else:
                try:
                    limit_fills = trader.process_limit_queue(price_map)
                    for pos in limit_fills:
                        opened += 1
                except Exception as e:
                    print(f"[Paper] limit queue: {e}")
                for sig in candidates:
                    if risk.open_positions_count >= config.MAX_POSITIONS:
                        skips.append({
                            "symbol": sig.get("symbol"), "direction": sig.get("direction"),
                            "strength": sig.get("strength"), "why": "MAX_POSITIONS",
                        })
                        break
                    eng_sig = str(sig.get("engine") or "").lower()
                    is_v2_sig = eng_sig in ("daytrading_v2", "daytradingv2") or sig.get("strategy_mode") == "DAYTRADING_V2"
                    # V2: reject_reason = twardy skip (strength=0.75 to stała, nie jakość).
                    # Trend: słaby + reject jak wcześniej.
                    if sig.get("reject_reason") and (
                        is_v2_sig or float(sig.get("strength") or 0) < config.MIN_SIGNAL_STRENGTH
                    ):
                        skips.append({
                            "symbol": sig.get("symbol"), "direction": sig.get("direction"),
                            "strength": sig.get("strength"),
                            "reject": sig.get("reject_reason"), "why": "PREFILTER_REJECT",
                        })
                        continue
                    before = risk.open_positions_count
                    # Wejscie idzie przez ExecutionPort, gdy port PAPER
                    # istnieje; inaczej prosto do ksiegi, jak dotad.
                    # Rownowaznosc obu drog: tests/test_paper_port_equivalence.py
                    pos = open_entry(rt, trader, sig)
                    if pos:
                        opened += 1
                    elif len(skips) < 10:
                        can, why = risk.can_open_position(sig)
                        pending = trader.has_pending_limit(sig.get("symbol"))
                        skips.append({
                            "symbol": sig.get("symbol"), "direction": sig.get("direction"),
                            "strength": float(sig.get("strength") or 0),
                            "reject": sig.get("reject_reason"),
                            "why": why if not can else (
                                "LIMIT_PENDING" if pending else f"OPEN_NONE(count={before})"
                            ),
                            "net_r": sig.get("expected_net_r"),
                            "quality": sig.get("quality") or sig.get("strength"),
                        })
                if opened == 0 and candidates:
                    print(
                        f"[Open] 0 otwarć z {len(candidates)} kandydatów | "
                        f"trade_on={trade_on} paused={risk.paused} halt={risk.is_halted} "
                        f"cap=${risk.current_capital:.2f} open={risk.open_positions_count}/{config.MAX_POSITIONS}"
                    )
                    for item in skips[:5]:
                        print(f"       · {item.get('symbol')} {item.get('why')} rr={item.get('reject')}")
            if not skips and block:
                for sample in candidates[:8]:
                    can, why = (False, block)
                    try:
                        if trade_on and not risk.paused and not risk.is_halted:
                            can, why = risk.can_open_position(sample)
                    except Exception:
                        why = block
                    skips.append({
                        "symbol": sample.get("symbol"),
                        "direction": sample.get("direction"),
                        "strength": sample.get("strength"),
                        "reject": sample.get("reject_reason"),
                        "why": why if not can else block,
                        "net_r": sample.get("expected_net_r"),
                    })
            try:
                from collections import Counter
                v2_hist = Counter()
                for s in signals or []:
                    eng = str(s.get("engine") or "")
                    mode = str(s.get("strategy_mode") or "")
                    if "daytrading_v2" in eng.lower() or mode == "DAYTRADING_V2":
                        rr = s.get("reject_reason")
                        if rr:
                            v2_hist[str(rr)] += 1
                        elif s.get("direction") in ("LONG", "SHORT"):
                            v2_hist["_PASS"] += 1
                if opened == 0 and v2_hist:
                    top_rr = ", ".join(f"{k}:{v}" for k, v in v2_hist.most_common(6))
                    print(f"[V2] reject histogram: {top_rr}")
                logger.log_cycle({
                    "cycle": cycle,
                    "trade_on": trade_on,
                    "paused": bool(risk.paused),
                    "halted": bool(risk.is_halted),
                    "block": block,
                    "candidates": len(candidates),
                    "opened": opened,
                    "open_positions": risk.open_positions_count,
                    "capital": risk.current_capital,
                    "universe": len(coins or []),
                    "skips": skips[:12],
                    "v2_reject_histogram": dict(v2_hist.most_common(12)),
                    "reversal_diag": getattr(signal_engine, "last_reversal_diag", None),
                    "top": [
                        {
                            "symbol": s.get("symbol"),
                            "dir": s.get("direction"),
                            "str": round(float(s.get("strength") or 0), 4),
                            "rr": s.get("reject_reason"),
                            "net_r": s.get("expected_net_r"),
                        }
                        for s in candidates[:8]
                    ],
                })
            except Exception as le:
                print(f"[Logger] cycle: {le}")

            persist_cycle(rt, coins, signals, active, corr, mctx, btc_change, cycle, signal_engine=signal_engine)
            # Gotowosc UI dopiero po pelnym cyklu zakonczonym zapisem stanu.
            rt.last_completed_cycle = cycle
            rt.analysis_loading = False
            try:
                logger.maybe_cleanup()
            except Exception as e:
                print(f"[Logger] cleanup: {e}")

            status = risk.get_status()
            funds = trader.get_funds_breakdown()
            print(f"Kapital ${status['capital']:.4f} | Wolne ${funds.get('free_margin',0):.4f} | "
                  f"Zajete ${funds.get('used_margin',0):.4f} | Equity ${funds.get('equity',0):.4f}")
            try:
                ex = _exchange_snapshot(rt)
                if ex.get("mode") == "LIVE" and ex.get("source") == "blofin":
                    print(
                        f"[Blofin READ-ONLY] equity=${float(ex.get('equity') or 0):.4f} "
                        f"avail=${float(ex.get('available') or 0):.4f} "
                        f"pozycje giełda: {ex.get('positions_count', 0)}"
                    )
            except Exception as e:
                print(f"[App] exchange snapshot: {e}")
                try:
                    from feed_log import note
                    note("App", "exchange snapshot", e)
                except Exception:
                    pass

        except Exception as e:
            rt.last_error = str(e)
            print(f"[App] Blad cyklu: {e}")
            try:
                persist_market_preview(rt, getattr(rt, "last_coins", None) or [], cycle)
            except Exception as pe:
                print(f"[App] persist po bledzie: {pe}")

        wait_for_next_cycle(config.LOOP_INTERVAL_SECONDS)

    print("[App] Silnik bota zatrzymany.")


def run_ui_window(rt: BotRuntime) -> None:
    """Uruchamia natywne UI PySide6. Lokalne HTTP API jest osobnym wątkiem."""
    try:
        from pyside6_ui import run_pyside6_ui
        print("[App] Start CryptoEdge Native UI (PySide6)...")
        run_pyside6_ui(rt)
        return
    except ImportError as e:
        print(f"[App] PySide6 nie jest zainstalowane: {e}")
        print("[App] Zainstaluj zaleznosc: pip install PySide6")
    except Exception as e:
        print(f"[App] PySide6 UI error: {e}")
        import traceback; traceback.print_exc()
    try:
        from native_ui import run_native_ui
        print("[App] Fallback do Tkinter.")
        run_native_ui(rt)
    except Exception as e:
        print(f"[App] Native fallback failed: {e}")


def main():
    # React/Tauri uruchamia ten sam runtime bez tworzenia drugiego okna.
    # Analiza i handel pozostaja wylaczone do chwili nacisniecia Start w UI.
    web_ui_mode = "--web-ui" in sys.argv
    try:
        from version import display as _ver
        _ver_s = _ver()
    except Exception:
        _ver_s = "?"
    print("=" * 60)
    print(f"  CryptoEdge {_ver_s}")
    print(f"  Paper · x{config.LEVERAGE} · max {config.MAX_POSITIONS} poz. · {config.LOOP_INTERVAL_SECONDS}s")
    print("  UI → PySide6 Native Dark Modern + lokalne API 127.0.0.1")
    print("=" * 60)

    # settings PRZED RiskManager – inaczej STARTING_CAPITAL z UI nigdy nie trafia do silnika
    settings_store.apply_settings()
    try:
        import secrets_store
        secrets_store.apply_secrets()
        print(f"[App] API: {secrets_store.status_label()}")
        # Bot nigdy nie sklada realnych zlecen (LIVE_EXECUTION_ENABLED=False,
        # brak kodu skladania zlecen) - klucz o pelnych uprawnieniach TRADE
        # jest niepotrzebnym ryzykiem. Wystarczy READ (odczyt salda/pozycji).
        if secrets_store.has_blofin_keys() and bool(getattr(config, "PAPER_TRADING", True)):
            print(
                "[App] Wskazówka: bot działa w trybie PAPER i nie składa realnych zleceń - "
                "wystarczy klucz Blofin z uprawnieniem READ (bez TRADE/TRANSFER). "
                "Jeśli Twój klucz ma więcej uprawnień niż READ, rozważ utworzenie osobnego, "
                "węższego klucza tylko do odczytu."
            )
    except Exception as e:
        print(f"[App] secrets: {e}")

    feeder = DataFeeder()
    try:
        feeder.blofin.probe_public()
    except Exception as e:
        print(f"[App] Blofin probe: {e}")
    # Best-effort: probuje faktycznie sprawdzic uprawnienia klucza (nie tylko
    # ogolna wskazowke wyzej) - patrz obszerny komentarz w
    # BlofinFeed.fetch_api_key_permissions() o niepewnosci co do dokladnego
    # endpointu. Kazdy blad/None jest cicho ignorowany - to czysty dodatek,
    # nigdy nie blokuje ani nie zmienia zachowania bota.
    try:
        if secrets_store.has_blofin_keys() and bool(getattr(config, "PAPER_TRADING", True)):
            perms = feeder.blofin.fetch_api_key_permissions()
            if perms and any(p in ("TRADE", "TRANSFER") for p in perms):
                print(
                    f"[App] Uwaga: skonfigurowany klucz Blofin ma uprawnienia {perms}, "
                    "a bot w trybie PAPER potrzebuje tylko READ. Rozważ zawężenie klucza."
                )
    except Exception:
        pass
    signal_engine = SignalEngine(data_feeder=feeder)
    risk = RiskManager(float(getattr(config, "STARTING_CAPITAL", 100) or 100))
    logger = BotLogger()

    # Etap 1+2: registry / executor / protection / reconcile
    from instrument_registry import InstrumentRegistry
    from cryptoedge.execution.blofin import BloFinExecutor
    from protection import ProtectionManager
    from position_reconciler import PositionReconciler
    from restart_recovery import RestartRecovery

    registry = InstrumentRegistry(feeder=feeder)
    risk.instrument_registry = registry
    risk.feeder = feeder
    try:
        registry.ensure_loaded()
        n_reg = registry.count()
        if n_reg:
            print(f"[App] InstrumentRegistry: {n_reg} par")
        else:
            err = registry.last_error or getattr(feeder.blofin, "last_error", None) or "brak odpowiedzi"
            print(f"[App] InstrumentRegistry: 0 par — {err}")
            print("[App] Bez listy instrumentów nie ma uniwersum (0 sygnałów).")
    except Exception as e:
        print(f"[App] InstrumentRegistry: {e}")
    executor = BloFinExecutor(registry=registry)
    protection = ProtectionManager(executor=executor, registry=registry)
    trader = PaperTrader(risk, logger=logger, protection=protection)

    load_previous_state(risk, trader)

    # PAPER zawsze jest nową sesją. RiskManager i PaperTrader zostały właśnie
    # utworzone, a load_previous_state celowo niczego dla PAPER nie odtwarza.
    if getattr(config, "PAPER_TRADING", True):
        target = float(getattr(config, "STARTING_CAPITAL", 100) or 100)
        risk.current_capital = target
        risk.peak_equity = target
        risk.daily_pnl = 0.0
        trader.positions = []
        # Pause z poprzedniej sesji / po SL nie wraca sam. Tylko przycisk Pause.
        if bool(getattr(config, "AUTO_UNPAUSE_ON_START", True)):
            if getattr(risk, "paused", False) and not getattr(risk, "is_halted", False):
                risk.paused = False
                print("[App] AUTO_UNPAUSE – Pause tylko z UI")

    if not config.PAPER_TRADING:
        print("[App] TRYB LIVE – kapital z konta Blofin futures")
    else:
        print(f"[App] TRYB PAPER – kapital ${config.STARTING_CAPITAL:.2f} (equity ${float(risk.current_capital):.2f})")

    rt = BotRuntime.get()
    rt.attach(feeder, signal_engine, risk, trader, logger)
    rt.protection = protection
    rt.executor = executor
    rt.event_bus = build_event_bus(config)
    if bool(getattr(config, "GRPC_SERVICE_ENABLED", False)):
        # Drugi interfejs (obok HTTP) - opcjonalny, wylaczony domyslnie.
        # Kazdy blad startu jest juz obslugiwany wewnatrz GrpcServer.start()
        # (zwraca False, loguje, nigdy nie rzuca) - bot dziala dalej normalnie
        # nawet jesli grpcio nie jest zainstalowany albo port jest zajety.
        rt.grpc_server = GrpcServer(snapshot_provider=rt.snapshot, port=int(getattr(config, "GRPC_SERVICE_PORT", 50061)))
        rt.grpc_server.start()
    account_sync = AccountSync(feeder, risk)
    rt.account_sync = account_sync
    reconciler = PositionReconciler(feeder=feeder, account_sync=account_sync)
    rt.reconciler = reconciler
    risk._reconciler_ref = reconciler
    try:
        from cryptoedge.apps.runtime import attach_runtime_modules
        attach_runtime_modules(rt)
    except Exception as exc:
        # Migracja modułowa nie może wyłączyć zarządzania istniejącą pozycją.
        print(f"[Modules] attach degraded: {exc}")
    try:
        recovery = RestartRecovery(
            risk=risk, trader=trader, logger=logger,
            protection=protection, reconciler=reconciler,
            executor=executor, account_sync=account_sync,
        )
        recovery.run()
    except Exception as e:
        print(f"[App] Recovery: {e}")
    try:
        bal = account_sync.sync(force=True)
        if bal:
            print(f"[App] Kapital: {bal.get('mode')} equity=${float(bal.get('equity') or 0):.4f} ({bal.get('source')})")
        # UWAGA: `bal` to zawsze niepusty dict (snapshot() zwraca fallback nawet
        # przy calkowitym niepowodzeniu), wiec sam `if bal` nigdy nie jest Falsy -
        # poprzedni `elif not config.PAPER_TRADING` byl martwym kodem i realny
        # powod niepowodzenia (np. zly klucz API) nigdy nie trafial do logow,
        # tylko cichy "equity=$0.0000 (none)" bez zadnego wyjasnienia. Sprawdzamy
        # teraz jawnie source != "blofin" (jedyny prawdziwy sukces synchronizacji).
        if not config.PAPER_TRADING and (not bal or bal.get("source") != "blofin"):
            err = (bal.get("error") if bal else None) or account_sync._last_error or "nieznany blad"
            print(f"[App] OSTRZEZENIE: nie udalo sie pobrac realnego salda z konta Blofin - {err}")
            print("[App] Kapital LIVE pozostaje na ostatniej lokalnie zapisanej wartosci, "
                  "NIE jest to realne saldo gieldy. Sprawdz klucze API w Opcjach "
                  "(przycisk 'Test connection') zanim zaczniesz handel LIVE.")
    except Exception as e:
        print(f"[App] Balance sync: {e}")

    # bootstrap state
    status = risk.get_status()
    status.update({"used_margin": 0, "free_margin": status["capital"],
                   "unrealized_pnl": 0, "equity": status["capital"]})
    logger.save_state(status, trader.get_open_summary(), extra={
        "cycle": 0, "signals": [], "running": True,
        "config": {
            "leverage": config.LEVERAGE,
            "max_positions": config.MAX_POSITIONS,
            "starting_capital": config.STARTING_CAPITAL,
            "daily_loss_limit": config.DAILY_LOSS_LIMIT,
        },
        "metrics": trader.session_metrics(),
        "rejects": list(getattr(risk, "reject_log", []) or []),
        "analysis_board": list(getattr(rt.signal_engine, "last_analysis_board", []) or []),
        "saved_positions": trader.export_open_positions(),
    })

    if bool(getattr(config, "HEADLESS", False)) and not web_ui_mode:
        rt.engine_enabled = True
        rt.trading_enabled = bool(getattr(config, "HEADLESS_TRADING", False))
        rt.mark_started()
        print("[App] HEADLESS: analiza ON" + (" + handel" if rt.trading_enabled else " (handel OFF)"))

    # Bot w tle (daemon – zyje dopoki glowny watek)
    threading.Thread(target=bot_loop, args=(rt,), daemon=True, name="bot").start()

    try:
        if bool(getattr(config, "ENGINE_API_ENABLED", True)):
            from engine_api import start_engine_api
            rt.engine_api = start_engine_api(rt)
    except Exception as e:
        print(f"[API] start failed: {e}")

    if bool(getattr(config, "HEADLESS", False)) or web_ui_mode:
        mode_name = "TAURI" if web_ui_mode else "HEADLESS"
        print(f"[App] {mode_name} — silnik + API, bez okna PySide6. Ctrl+C kończy.")
        rt.running = True
        try:
            while True:
                time.sleep(30)
        except KeyboardInterrupt:
            rt.running = False
            print(f"[App] {mode_name} stop")
        return

    print("[App] Uruchamiam natywne okno CryptoEdge (PySide6). HTML: http://127.0.0.1:47821/")
    run_ui_window(rt)
    print("[App] Koniec.")


if __name__ == "__main__":
    main()
