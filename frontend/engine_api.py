"""Lokalne HTTP API obok PySide6. Zero nowych zależności (stdlib).

Qt zostaje oknem głównym. Przeglądarka na http://127.0.0.1:47821/ jest
opcjonalnym lusterkiem tego samego runtime (DataAdapter-equivalent JSON
+ komendy silnika). Bind domyślnie tylko loopback.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import config
import version

WEB_DIR = Path(__file__).resolve().parent / "web"
MAX_BODY = 64 * 1024
MUTATING = {"start_trading", "stop", "close_all", "kill_switch"}


def sl_mark(row: dict) -> str:
    if row.get("trailing_active"):
        return "▲"
    if row.get("breakeven_active"):
        return "🔒−"
    return "↓"


def _num(value: Any, digits: int = 2) -> float | None:
    try:
        if value is None or value == "":
            return None
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _age(opened: Any, age_sec: Any = None) -> str:
    try:
        if age_sec is not None:
            sec = int(float(age_sec))
        elif opened:
            raw = opened
            if hasattr(raw, "timestamp"):
                sec = int(time.time() - raw.timestamp())
            else:
                text = str(raw).replace("Z", "+00:00")
                sec = int(time.time() - datetime.fromisoformat(text[:19]).timestamp())
        else:
            return "—"
        if sec < 60:
            return f"{sec}s"
        if sec < 3600:
            return f"{sec // 60}m"
        return f"{sec // 3600}h {(sec % 3600) // 60}m"
    except (TypeError, ValueError):
        return "—"


def _pnl_at_stop(item: Any, entry: Any, sl: Any, side: str, size: Any) -> float | None:
    if hasattr(item, "pnl_at_stop"):
        try:
            return float(item.pnl_at_stop())
        except Exception:
            pass
    try:
        entry_f, sl_f, size_f = float(entry), float(sl), float(size or 0)
        if entry_f <= 0:
            return None
        move = (sl_f - entry_f) / entry_f if str(side).upper() == "LONG" else (entry_f - sl_f) / entry_f
        return size_f * move
    except (TypeError, ValueError):
        return None


def _uptime(rt) -> str:
    started = getattr(rt, "started_at", None)
    try:
        sec = int(time.time() - float(started)) if started else 0
    except (TypeError, ValueError):
        sec = 0
    if sec >= 3600:
        return f"{sec // 3600}h {(sec % 3600) // 60}m"
    if sec >= 60:
        return f"{sec // 60}m"
    return f"{sec}s" if sec else "—"


def _closed_today(rows: list[dict]) -> list[dict]:
    today = datetime.now().date()
    out = []
    for row in rows:
        raw = row.get("time") or row.get("exit_time") or ""
        try:
            if hasattr(raw, "date"):
                day = raw.date()
            else:
                text = str(raw).replace("Z", "+00:00")
                day = datetime.fromisoformat(text[:19]).date()
            if day == today:
                out.append(row)
        except (TypeError, ValueError):
            continue
    return out


def _positions(rt) -> list[dict]:
    trader = getattr(rt, "trader", None)
    prices = getattr(rt, "last_price_map", {}) or {}
    source = []
    if trader:
        lock = getattr(trader, "lock", None)
        if lock:
            with lock:
                source = list(getattr(trader, "positions", []) or [])
        else:
            source = list(getattr(trader, "positions", []) or [])
    rows = []
    for item in source:
        if isinstance(item, dict):
            symbol = item.get("symbol") or "—"
            side = str(item.get("side") or item.get("direction") or "—").upper()
            entry = item.get("entry") or item.get("entry_price")
            sl = item.get("sl") or item.get("sl_price")
            size = item.get("size") or item.get("qty")
            mark = prices.get(symbol) or item.get("mark")
            row = {
                "symbol": symbol, "side": side, "entry": _num(entry, 8),
                "mark": _num(mark, 8), "sl": _num(sl, 8),
                "tp": _num(item.get("tp") or item.get("tp_price"), 8),
                "size": _num(size, 4), "margin": _num(item.get("margin"), 4),
                "pnl": _num(item.get("pnl") or item.get("unrealized_pnl"), 4),
                "pnl_pct": _num(item.get("pnl_pct"), 2),
                "trailing_active": bool(item.get("trailing_active")),
                "breakeven_active": bool(item.get("breakeven_active")),
                "engine": item.get("engine") or "",
                "age": item.get("age") or _age(item.get("opened") or item.get("entry_time")),
            }
            row["sl_mark"] = sl_mark(row)
            row["pnl_at_stop"] = _num(item.get("pnl_at_stop"), 4)
            rows.append(row)
            continue
        symbol = getattr(item, "symbol", "—")
        side = str(getattr(item, "side", getattr(item, "direction", "—"))).upper()
        entry = getattr(item, "entry_price", getattr(item, "entry", None))
        sl = getattr(item, "sl_price", getattr(item, "sl", None))
        size = getattr(item, "size_usd", getattr(item, "size", getattr(item, "qty", None)))
        mark = prices.get(symbol) or getattr(item, "mark_price", None)
        pnl = getattr(item, "unrealized_pnl", getattr(item, "pnl", 0.0))
        row = {
            "symbol": symbol, "side": side, "entry": _num(entry, 8),
            "mark": _num(mark, 8), "sl": _num(sl, 8),
            "tp": _num(getattr(item, "tp_price", None), 8),
            "size": _num(size, 4), "margin": _num(getattr(item, "margin", None), 4),
            "pnl": _num(pnl, 4), "pnl_pct": _num(getattr(item, "pnl_pct", None), 2),
            "trailing_active": bool(getattr(item, "trailing_active", False)),
            "breakeven_active": bool(getattr(item, "breakeven_active", False)),
            "engine": getattr(item, "engine", "") or "",
            "leverage": getattr(item, "leverage", 1),
            "age": _age(getattr(item, "entry_time", None)),
        }
        row["sl_mark"] = sl_mark(row)
        row["pnl_at_stop"] = _num(_pnl_at_stop(item, entry, sl, side, size), 4)
        rows.append(row)
    return rows


def _candidates(rt, limit: int = 8) -> list[dict]:
    st = {}
    logger = getattr(rt, "logger", None)
    raw = getattr(logger, "last_state", None) if logger else None
    if isinstance(raw, dict):
        st = raw
    merged = {}
    order = []
    for source in (st.get("scanner_assets") or [], st.get("analysis_board") or [], st.get("signals") or []):
        for row in source:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").upper()
            if not symbol:
                continue
            if symbol not in merged:
                merged[symbol] = {}
                order.append(symbol)
            merged[symbol].update({k: v for k, v in row.items() if v is not None})
            merged[symbol]["symbol"] = symbol
    risk = getattr(rt, "risk", None)
    out = []
    for symbol in order:
        row = merged[symbol]
        side = str(row.get("direction") or row.get("side") or "—").upper()
        reject = row.get("reject_reason")
        gate = "WAIT"
        if risk is not None:
            try:
                ok, reason = risk.can_open_position(dict(row))
            except Exception:
                ok, reason = False, reject
            if ok:
                gate = "OPEN"
            else:
                up = str(reason or "").upper()
                gate = "WAIT" if any(tag in up for tag in ("COOLDOWN", "WAIT", "PENDING")) else "BLOCK"
        elif reject:
            gate = "BLOCK"
        if side == "NEUTRAL" and gate != "OPEN":
            continue
        score = row.get("strength") or row.get("score")
        try:
            score_n = float(score)
            if abs(score_n) <= 1:
                score_n *= 100
        except (TypeError, ValueError):
            score_n = None
        rr = row.get("expected_net_r")
        out.append({
            "sym": symbol, "side": side if side in ("LONG", "SHORT") else "—",
            "score": round(score_n, 1) if score_n is not None else None,
            "gate": gate, "rr": _num(rr, 2),
        })
        if len(out) >= limit:
            break
    return out


def _present_event(row: dict) -> dict | None:
    """Translate detailed audit telemetry into a short operator-facing event."""
    event = str(row.get("event") or "").strip().upper()
    if not event or event in {"CYCLE", "SCAN", "SIGNAL", "DECISION", "TELEMETRY"}:
        return None

    ts = str(row.get("timestamp") or "")
    symbol = str(row.get("symbol") or "").strip().upper()
    direction = str(row.get("direction") or "").strip().upper()
    try:
        price = float(row.get("price") or 0)
    except (TypeError, ValueError):
        price = 0.0
    px = f"{price:.8f}".rstrip("0").rstrip(".") if price > 0 else "—"

    if event == "OPEN":
        side = f" {direction}" if direction in {"LONG", "SHORT"} else ""
        tag, text = "POZYCJA", f"{symbol}: otwarto{side} po {px}"
    elif event == "CLOSE":
        try:
            pnl = float(row.get("pnl") or 0)
        except (TypeError, ValueError):
            pnl = 0.0
        result = "zysk" if pnl >= 0 else "strata"
        tag, text = "POZYCJA", f"{symbol}: zamknięto po {px} · {result} {abs(pnl):.2f} USD"
    elif event in {"START", "ENGINE_START", "TRADING_START", "ANALYSIS_START"}:
        tag, text = "SYSTEM", "Bot został uruchomiony"
    elif event in {"STOP", "ENGINE_STOP", "TRADING_STOP"}:
        tag, text = "SYSTEM", "Bot został zatrzymany"
    elif event in {"WARMUP", "WARMUP_START"}:
        tag, text = "SYSTEM", "Silnik przygotowuje dane rynkowe"
    elif event in {"WARMUP_READY", "READY"}:
        tag, text = "SYSTEM", "Silnik jest gotowy"
    elif any(token in event for token in ("KILL", "HALT", "RISK", "MARGIN")):
        tag, text = "RYZYKO", f"{symbol + ': ' if symbol else ''}handel wstrzymany przez zabezpieczenia"
    elif any(token in event for token in ("ERROR", "ALERT", "FAIL")):
        tag, text = "UWAGA", f"{symbol + ': ' if symbol else ''}wykryto problem — szczegóły są w logach"
    else:
        # Internal event names and verbose reasons belong in audit logs, not the UI.
        return None

    return {
        "time": ts[11:19] if len(ts) >= 19 else ts[-8:],
        "tag": tag,
        "text": text,
    }


def _events(rt, limit: int = 5) -> list[dict]:
    path = Path(__file__).resolve().parent / "logs" / "bot_log.csv"
    rows = []
    try:
        if not path.exists():
            return rows
        import csv
        with path.open("r", encoding="utf-8") as handle:
            # CYCLE dominates the audit log, so inspect a wider tail and only
            # expose the few entries that matter to an operator.
            data = list(csv.DictReader(handle))[-500:]
        session_start = float(getattr(rt, "session_started_at", 0.0) or 0.0)
        seen = set()
        for row in reversed(data):
            if session_start:
                try:
                    raw_ts = str(row.get("timestamp") or "").replace("Z", "+00:00")
                    if datetime.fromisoformat(raw_ts).timestamp() < session_start:
                        continue
                except (TypeError, ValueError, OverflowError):
                    # Nie pokazuj w UI wpisu bez wiarygodnego czasu sesji;
                    # pozostaje on nadal w pliku audytowym.
                    continue
            item = _present_event(row)
            if item is None:
                continue
            key = (item["time"], item["tag"], item["text"])
            if key in seen:
                continue
            seen.add(key)
            rows.append(item)
            if len(rows) >= limit:
                break
    except (OSError, UnicodeError):
        pass
    return rows


def _engine_progress(rt, state: dict | None = None) -> dict:
    """One live status for UI; detailed warmup cycles remain in audit logs."""
    state = state if isinstance(state, dict) else {}
    analysis = bool(getattr(rt, "engine_enabled", False))
    trading = bool(getattr(rt, "trading_enabled", False))
    loading = bool(getattr(rt, "analysis_loading", False))
    snapshot = getattr(rt, "last_state_snapshot", None) or {}
    bootstrap = snapshot.get("warmup") or state.get("warmup") or {}
    cascade = state.get("engine_warmup") or {}

    universe = int(
        bootstrap.get("candidates")
        or cascade.get("total_coins")
        or state.get("universe_size")
        or len(getattr(rt, "last_coins", None) or [])
        or 0
    )
    available = int(
        bootstrap.get("ready_pairs")
        if bootstrap.get("ready_pairs") is not None
        else cascade.get("available_coins") or 0
    )
    available = max(0, min(available, universe)) if universe else max(0, available)

    if not analysis:
        phase, ready, message = "stopped", False, "Bot zatrzymany"
    elif loading or bool(bootstrap.get("active")):
        phase, ready = "warming", False
        message = (
            f"Rozgrzewanie bota · dostępne monety: {available}/{universe}"
            if universe else "Rozgrzewanie bota · pobieranie listy monet…"
        )
    else:
        ready = True
        if universe and available <= 0:
            available = universe
        phase = "trading" if trading else "analysis"
        tail = f" · dostępne monety: {available or universe}" if (available or universe) else ""
        message = (
            f"Bot rozgrzany · analiza gotowa · handel uruchomiony{tail}"
            if trading else f"Bot rozgrzany · analiza gotowa{tail}"
        )
    return {
        "phase": phase, "ready": ready, "available": available,
        "total": universe, "message": message,
    }


def _closed_history(rt, limit: int = 200) -> list[dict]:
    trader = getattr(rt, "trader", None)
    rows = list(getattr(trader, "closed_positions", []) or []) if trader else []
    out = []
    for item in rows[-limit:][::-1]:
        get = item.get if isinstance(item, dict) else lambda key, default=None: getattr(item, key, default)
        out.append({
            "time": str(get("exit_time", get("time", "")) or ""),
            "symbol": str(get("symbol", "") or "").upper(),
            "side": str(get("direction", get("side", "")) or "").upper(),
            "entry": _num(get("entry_price", get("entry", None)), 8),
            "exit": _num(get("exit_price", get("exit", None)), 8),
            "pnl": _num(get("pnl", None), 4),
            "pnl_pct": _num(get("pnl_pct", None), 2),
            "engine": str(get("engine", "") or "—"),
            "reason": str(get("exit_reason", get("reason", "")) or "—"),
        })
    return out


def _candles(symbol: str, tf: str) -> dict:
    from market_store import STORE
    symbol = str(symbol or "").upper().replace("USDT", "")
    tf = {"1h": "1H", "4h": "4H", "1d": "1D"}.get(str(tf or "15m").lower(), str(tf or "15m"))
    frame = STORE.get_ohlcv(symbol, tf) or {}
    allowed = ("opens", "highs", "lows", "closes", "volumes", "timestamps")
    return {"symbol": symbol, "tf": tf, "candles": {key: list(frame.get(key) or [])[-180:] for key in allowed}}


WATCHLIST_SYMBOLS = ("BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "BNB", "AVAX")


def _sparklines(symbols=WATCHLIST_SYMBOLS, points: int = 36) -> dict:
    """Real 15m close series for watchlist mini charts (never synthetic CSS)."""
    from market_store import STORE
    out = {}
    for symbol in symbols:
        try:
            frame = STORE.get_ohlcv(symbol, "15m") or {}
            closes = []
            for value in list(frame.get("closes") or [])[-points:]:
                number = float(value)
                if number > 0:
                    closes.append(round(number, 10))
            out[symbol] = closes
        except (TypeError, ValueError, AttributeError):
            out[symbol] = []
    return out


def build_status(rt) -> dict:
    """Projekcja runtime → JSON. Nie importuje Qt."""
    import config as cfg
    positions = _positions(rt)
    risk = getattr(rt, "risk", None)
    paper = bool(getattr(cfg, "PAPER_TRADING", True))
    capital = float(getattr(risk, "current_capital", 0) or 0) if risk else 0.0
    margin = sum(float(p.get("margin") or 0) for p in positions)
    unreal = sum(float(p.get("pnl") or 0) for p in positions)
    equity = capital + unreal
    daily = float(getattr(risk, "daily_pnl", 0) or 0) if risk else 0.0
    logger = getattr(rt, "logger", None)
    st = getattr(logger, "last_state", None) if logger else None
    if isinstance(st, dict) and st.get("daily_pnl") is not None:
        try:
            daily = float(st.get("daily_pnl") or daily)
        except (TypeError, ValueError):
            pass
    closed = []
    trader = getattr(rt, "trader", None)
    if trader:
        for p in list(getattr(trader, "closed_positions", []) or [])[-200:]:
            if isinstance(p, dict):
                closed.append(p)
            else:
                closed.append({
                    "time": getattr(p, "exit_time", ""),
                    "pnl": getattr(p, "pnl", 0),
                    "symbol": getattr(p, "symbol", ""),
                })
    today = _closed_today(closed)
    wins = [r for r in today if float(r.get("pnl") or 0) > 0]
    wr = (len(wins) / len(today) * 100.0) if today else 0.0
    daily_pct = (daily / equity * 100.0) if equity else 0.0
    limit = float(getattr(cfg, "DAILY_LOSS_LIMIT", 0.05) or 0.05) * 100.0
    used_pct = (margin / equity * 100.0) if equity else 0.0
    regime = "UNKNOWN"
    if isinstance(st, dict):
        info = st.get("market_regime") or {}
        regime = str(info.get("regime") or "UNKNOWN").upper()
    protection = getattr(rt, "protection", None)
    prices = getattr(rt, "last_price_map", {}) or {}
    majors = {}
    for sym in WATCHLIST_SYMBOLS:
        if prices.get(sym) is not None:
            majors[sym] = _num(prices.get(sym), 4)
    paused = bool(getattr(risk, "paused", False)) if risk else False
    risk_halted = bool(getattr(risk, "is_halted", False)) if risk else False
    effective_trading = bool(getattr(rt, "trading_enabled", False)) and not paused and not risk_halted
    try:
        from edge_monitor import snapshot as edge_snapshot
        edge = edge_snapshot(
            list(getattr(trader, "closed_positions", []) or []) if trader else [],
            list(getattr(risk, "reject_log", []) or []) if risk else [],
            replay_expectancy=(st or {}).get("replay_expectancy") if isinstance(st, dict) else None,
            portfolio_risk=(getattr(trader, "get_funds_breakdown", lambda: {})() or {}).get("portfolio") if trader else {},
        )
    except Exception as e:
        edge = {"error": str(e)}
    progress = _engine_progress(rt, st if isinstance(st, dict) else {})
    events = _events(rt)
    if bool(getattr(rt, "engine_enabled", False)):
        events.insert(0, {
            "time": time.strftime("%H:%M:%S"),
            "tag": "ROZGRZEWANIE" if progress["phase"] == "warming" else "SYSTEM",
            "text": progress["message"],
        })
        events = events[:5]
    return {
        "ok": True,
        "ts": time.time(),
        "version": version.display(),
        "engine": {
            "analysis": bool(getattr(rt, "engine_enabled", False)),
            "trading": effective_trading,
            "paused": paused,
            "loading": bool(getattr(rt, "analysis_loading", False)),
            "mode": "DEMO" if paper else "LIVE",
            "live_execution": bool(getattr(cfg, "LIVE_EXECUTION_ENABLED", False)),
            "risk_state": ("HALTED" if risk_halted else str(getattr(risk, "risk_state", "NORMAL"))) if risk else "UNKNOWN",
            "warmup": progress,
        },
        "account": {
            "equity": _num(equity, 4), "available": _num(max(0.0, capital - margin), 4),
            "margin": _num(margin, 4), "daily": _num(daily, 4),
            "unrealized": _num(unreal, 4), "positions": len(positions),
        },
        "session": {
            "mode": "DEMO" if paper else "LIVE",
            "equity": _num(equity, 4),
            "daily": _num(daily, 4),
            "daily_pct": _num(daily_pct, 2),
            "daily_limit_pct": limit,
            "unrealized": _num(unreal, 4),
            "positions": len(positions),
            "max_positions": int(getattr(cfg, "MAX_POSITIONS", 10) or 10),
            "closed_today": len(today),
            "winrate_today": _num(wr, 1),
            "uptime": _uptime(rt),
            "regime": regime,
            "kill_switch": bool(getattr(protection, "kill_switch_active", False)) or risk_halted,
            "used_pct": _num(used_pct, 1),
        },
        "positions": positions,
        "candidates": _candidates(rt),
        "events": events,
        "prices": majors,
        "sparklines": _sparklines(),
        "feed": str((st or {}).get("sources") or "") if isinstance(st, dict) else "",
        "edge_decay": edge,
    }


def run_action(rt, action: str) -> dict:
    action = (action or "").strip().lower().replace("-", "_")
    mapping = {
        "start_analysis": "start_analysis",
        "start_trading": "start_trading",
        "stop_trading": "stop_trading",
        "pause": "pause",
        "resume": "resume",
        "stop": "stop_engine",
        "stop_engine": "stop_engine",
        "close_all": "close_all",
        "kill_switch": "kill_switch",
        "reduce_only_on": "reduce_only_on",
        "reduce_only_off": "reduce_only_off",
    }
    method = mapping.get(action)
    if not method:
        return {"ok": False, "error": "unknown_action", "action": action}
    if action in ("start_trading",) and not bool(getattr(config, "PAPER_TRADING", True)):
        if not bool(getattr(config, "LIVE_EXECUTION_ENABLED", False)):
            return {"ok": False, "error": "live_execution_disabled"}
    fn = getattr(rt, method, None)
    if not callable(fn):
        return {"ok": False, "error": "not_available", "action": action}
    msg = fn() if action != "kill_switch" else fn("api")
    return {"ok": True, "action": action, "message": str(msg)}


def blofin_credentials_status() -> dict:
    import secrets_store
    values = secrets_store.load_secrets()
    complete = all(values.get(key) for key in secrets_store.SECRET_KEYS)
    partial = any(values.get(key) for key in secrets_store.SECRET_KEYS) and not complete
    return {
        "ok": True, "configured": complete, "partial": partial,
        "masked_key": secrets_store.mask(values.get("BLOFIN_API_KEY", "")) if values.get("BLOFIN_API_KEY") else "",
    }


def update_blofin_credentials(rt, body: dict) -> dict:
    import secrets_store
    action = str(body.get("action") or "save").lower()
    if action == "clear":
        if body.get("confirm") is not True:
            return {"ok": False, "error": "confirm_required"}
        secrets_store.save_secrets({key: "" for key in secrets_store.SECRET_KEYS})
        return {"ok": True, "message": "Klucze BloFin zostały usunięte", **blofin_credentials_status()}

    values = {
        "BLOFIN_API_KEY": str(body.get("api_key") or "").strip(),
        "BLOFIN_API_SECRET": str(body.get("api_secret") or "").strip(),
        "BLOFIN_API_PASSPHRASE": str(body.get("passphrase") or "").strip(),
    }
    if not all(values.values()):
        return {"ok": False, "error": "incomplete_credentials",
                "message": "Wpisz API Key, Secret i Passphrase"}
    if any(len(value) > 512 for value in values.values()):
        return {"ok": False, "error": "credential_too_long"}
    secrets_store.save_secrets(values)
    result = {"ok": True, "message": "Klucze zapisano bezpiecznie", **blofin_credentials_status()}
    if action != "test":
        return result

    feed = getattr(getattr(rt, "feeder", None), "blofin", None)
    if feed is None:
        return {"ok": False, "error": "connector_unavailable", "message": "Brak konektora BloFin"}
    balance = feed.fetch_futures_balance()
    if not balance:
        return {"ok": False, "error": "connection_failed",
                "message": str(getattr(feed, "last_error", None) or "BloFin nie zwrócił salda")[:240]}
    positions = feed.fetch_open_positions() or []
    return {
        **result, "message": "Połączenie z BloFin działa · tylko odczyt",
        "account": {
            "equity": _num(balance.get("equity"), 4),
            "available": _num(balance.get("available"), 4),
            "currency": str(balance.get("currency") or "USDT"),
            "open_positions": len(positions),
        },
    }


class EngineApi:
    def __init__(self, runtime, host: str | None = None, port: int | None = None):
        self.rt = runtime
        self.host = host or str(getattr(config, "ENGINE_API_HOST", "127.0.0.1") or "127.0.0.1")
        self.port = int(port or getattr(config, "ENGINE_API_PORT", 47821) or 47821)
        self.token = str(getattr(config, "ENGINE_API_TOKEN", "") or "")
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.url = ""
        self.replay = ReplayJob(runtime)

    def start(self) -> "EngineApi":
        if self.host not in ("127.0.0.1", "localhost", "::1") and not self.token:
            print(f"[API] bind {self.host} bez tokena — wymuszam 127.0.0.1")
            self.host = "127.0.0.1"
        last_err = None
        for port in range(self.port, self.port + 5):
            try:
                self.httpd = ThreadingHTTPServer((self.host, port), self._handler())
                self.port = port
                break
            except OSError as exc:
                last_err = exc
                self.httpd = None
        if self.httpd is None:
            print(f"[API] nie startuję ({last_err})")
            return self
        self.httpd.daemon_threads = True
        self.url = f"http://{self.host}:{self.port}/"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="engine-api")
        self.thread.start()
        print(f"[API] {self.url}  (Qt bez zmian, przeglądarka opcjonalna)")
        return self

    def stop(self):
        if self.httpd:
            try:
                self.httpd.shutdown()
            except Exception:
                pass
            try:
                self.httpd.server_close()
            except Exception:
                pass
            self.httpd = None

    def _handler(self):
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return

            def do_OPTIONS(self):
                self.send_response(204)
                api._cors(self)
                self.end_headers()

            def do_GET(self):
                api.handle_get(self)

            def do_POST(self):
                api.handle_post(self)

        return Handler

    def _cors(self, handler):
        origin = handler.headers.get("Origin") or ""
        allowed = {
            f"http://{self.host}:{self.port}",
            "http://127.0.0.1:1420",
            "http://localhost:1420",
            "http://tauri.localhost",
            "tauri://localhost",
        }
        if origin in allowed:
            handler.send_header("Access-Control-Allow-Origin", origin)
            handler.send_header("Vary", "Origin")
        handler.send_header("Access-Control-Allow-Headers", "Content-Type, X-CryptoEdge-Token")
        handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _auth_ok(self, handler) -> bool:
        if not self.token:
            return True
        got = handler.headers.get("X-CryptoEdge-Token") or ""
        return got == self.token

    def _send(self, handler, code: int, payload: dict | bytes, content_type="application/json; charset=utf-8"):
        if isinstance(payload, dict):
            raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        else:
            raw = payload
        try:
            handler.send_response(code)
            self._cors(handler)
            handler.send_header("Content-Type", content_type)
            handler.send_header("Cache-Control", "no-store")
            handler.send_header("Content-Length", str(len(raw)))
            handler.end_headers()
            handler.wfile.write(raw)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # UI polling can be cancelled during navigation/refresh.  The
            # client is gone, so retrying a 500 response only creates noise.
            return

    def handle_get(self, handler):
        parsed = urlparse(handler.path)
        path = parsed.path
        if path in ("/health", "/api/health"):
            modules = {}
            registry = getattr(self.rt, "module_health", None)
            if registry is not None:
                try:
                    from cryptoedge.apps.runtime import refresh_runtime_health
                    modules = refresh_runtime_health(self.rt)
                except Exception as exc:
                    modules = {"status": "degraded", "error": str(exc), "modules": {}}
            self._send(handler, 200, {"ok": True, "app": "CryptoEdge",
                                     "version": version.display(),
                                     "url": self.url, "module_health": modules})
            return
        if not self._auth_ok(handler) and path.startswith("/api/"):
            self._send(handler, 401, {"ok": False, "error": "unauthorized"})
            return
        if path in ("/", "/desk", "/desk.html"):
            html = WEB_DIR / "desk.html"
            if not html.exists():
                self._send(handler, 404, {"ok": False, "error": "desk.html missing"})
                return
            self._send(handler, 200, html.read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/api/status":
            try:
                self._send(handler, 200, build_status(self.rt))
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return
            except Exception as exc:
                self._send(handler, 500, {"ok": False, "error": str(exc)})
            return
        if path == "/api/history":
            self._send(handler, 200, {"ok": True, "rows": _closed_history(self.rt)})
            return
        if path == "/api/candles":
            query = parse_qs(parsed.query)
            payload = _candles((query.get("symbol") or [""])[0], (query.get("tf") or ["15m"])[0])
            self._send(handler, 200, {"ok": True, **payload})
            return
        if path == "/api/replay/status":
            self._send(handler, 200, self.replay.snapshot())
            return
        if path == "/api/settings/blofin":
            self._send(handler, 200, blofin_credentials_status())
            return
        self._send(handler, 404, {"ok": False, "error": "not_found"})

    def handle_post(self, handler):
        path = urlparse(handler.path).path
        if not self._auth_ok(handler):
            self._send(handler, 401, {"ok": False, "error": "unauthorized"})
            return
        length = int(handler.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self._send(handler, 413, {"ok": False, "error": "too_large"})
            return
        raw = handler.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeError, json.JSONDecodeError):
            body = {}
        if path == "/api/replay/start":
            result = self.replay.start(body)
            self._send(handler, 202 if result.get("ok") else 409, result)
            return
        if path == "/api/settings/blofin":
            try:
                result = update_blofin_credentials(self.rt, body)
            except Exception as exc:
                result = {"ok": False, "error": "secure_store_failed", "message": str(exc)[:240]}
            self._send(handler, 200 if result.get("ok") else 400, result)
            return
        if not path.startswith("/api/engine/"):
            self._send(handler, 404, {"ok": False, "error": "not_found"})
            return
        action = path.rsplit("/", 1)[-1]
        if action in MUTATING and body.get("confirm") is not True:
            self._send(handler, 400, {"ok": False, "error": "confirm_required", "action": action})
            return
        result = run_action(self.rt, action)
        print(f"[API] {action} -> {result.get('message') or result.get('error')}")
        self._send(handler, 200 if result.get("ok") else 409, result)


class ReplayJob:
    """Background V2 replay with a small, thread-safe UI projection."""

    def __init__(self, runtime):
        self.rt = runtime
        self.lock = threading.Lock()
        self.thread = None
        self.state_path = Path(__file__).resolve().parent / "logs" / "replay_job_state.json"
        self.state = self._load_state()

    def _load_state(self):
        state = None
        try:
            if self.state_path.exists():
                state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            state = None
        if isinstance(state, dict):
            if state.get("running"):
                state.update(
                    running=False, phase="interrupted", progress=0,
                    message="Replay przerwany przez restart aplikacji",
                    error="REPLAY_INTERRUPTED_BY_RESTART", finished_at=time.time(),
                )
            if state.get("phase") == "interrupted" and not state.get("result"):
                recovered = self._recover_latest_report()
                if recovered:
                    state.update(
                        message="Replay przerwany przez restart · ostatni raport zachowany",
                        symbols=recovered.get("symbols") or [],
                        completed=recovered.get("completed") or 0,
                        total=recovered.get("total") or 0,
                        result=recovered.get("result"),
                    )
            self._save_state(state)
            return {**self._idle(), **state}
        return self._recover_latest_report() or self._idle()

    def _recover_latest_report(self):
        """Po aktualizacji pokaż ostatni gotowy raport zamiast pustego ekranu."""
        try:
            report_dir = Path(__file__).resolve().parent / "reports" / "replay"
            paths = sorted(report_dir.glob("daytrading_v2_portfolio_replay_*.json"),
                           key=lambda path: path.stat().st_mtime, reverse=True)
            if not paths:
                return None
            report = json.loads(paths[0].read_text(encoding="utf-8"))
            portfolio = report.get("portfolio") or {}
            ins = portfolio.get("in_sample") or {}
            oos = portfolio.get("out_of_sample") or {}
            by_symbol = oos.get("by_symbol") or {}
            return {
                **self._idle(), "phase": "complete", "progress": 100,
                "message": "Ostatni replay zakończony", "finished_at": paths[0].stat().st_mtime,
                "completed": len(report.get("symbols_downloaded") or []),
                "total": len(report.get("symbols_downloaded") or []),
                "symbols": [{
                    "symbol": symbol, "status": "Przeskanowana", "detail": "Raport zapisany",
                    "bars_5m": 0, "trades_oos": int(metrics.get("trades") or 0),
                    "net_r_oos": round(float(metrics.get("net_r") or 0), 3),
                } for symbol, metrics in sorted(by_symbol.items())],
                "result": {
                    "trades_oos": int(oos.get("trades") or 0), "win_rate_oos": float(oos.get("win_rate") or 0),
                    "net_r_oos": float(oos.get("net_r") or 0), "avg_r_oos": float(oos.get("avg_r") or 0),
                    "profit_factor_oos": oos.get("profit_factor"),
                    "max_drawdown_r_oos": float(oos.get("max_drawdown_r") or 0),
                    "trades_is": int(ins.get("trades") or 0), "win_rate_is": float(ins.get("win_rate") or 0),
                    "net_r_is": float(ins.get("net_r") or 0), "avg_r_is": float(ins.get("avg_r") or 0),
                    "profit_factor_is": ins.get("profit_factor"),
                    "max_drawdown_r_is": float(ins.get("max_drawdown_r") or 0),
                    "report_path": str(paths[0]),
                },
            }
        except (OSError, ValueError, TypeError):
            return None

    def _save_state(self, state=None):
        try:
            payload = state if isinstance(state, dict) else self.state
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            tmp.replace(self.state_path)
        except OSError:
            pass

    @staticmethod
    def _idle():
        return {
            "ok": True, "running": False, "phase": "idle", "message": "Replay nieaktywny",
            "progress": 0, "started_at": None, "elapsed_s": 0, "current_symbol": None,
            "completed": 0, "total": 0, "symbols": [], "result": None, "error": None,
        }

    def snapshot(self):
        with self.lock:
            out = json.loads(json.dumps(self.state, ensure_ascii=False, default=str))
        if out.get("started_at"):
            end = float(out.get("finished_at") or time.time())
            out["elapsed_s"] = max(0, int(end - float(out["started_at"])))
        return out

    def start(self, body: dict):
        with self.lock:
            if self.state.get("running"):
                return {"ok": False, "error": "replay_already_running", "message": "Replay już trwa"}
            try:
                days = max(7, min(365, int(body.get("days") or 90)))
                oos = max(0.10, min(0.50, float(body.get("oos_fraction") or 0.30)))
                limit = max(1, min(100, int(body.get("liquid_limit") or 10)))
            except (TypeError, ValueError):
                return {"ok": False, "error": "invalid_request", "message": "Nieprawidłowe parametry replay"}
            mode = str(body.get("universe_mode") or "LIQUID").upper()
            if mode not in {"MANUAL", "LIQUID", "ALL"}:
                return {"ok": False, "error": "invalid_universe"}
            symbols = tuple(dict.fromkeys(
                str(value).upper().replace("-USDT", "").replace("USDT", "")
                for value in (body.get("symbols") or []) if value
            ))
            if mode == "MANUAL" and not symbols:
                return {"ok": False, "error": "symbols_required"}
            self.state = {
                **self._idle(), "running": True, "phase": "starting",
                "message": "Przygotowywanie testu replay…", "started_at": time.time(),
                "request": {"days": days, "oos_fraction": oos, "liquid_limit": limit,
                            "universe_mode": mode, "symbols": list(symbols)},
            }
            self._save_state()
        self.thread = threading.Thread(
            target=self._run, args=(days, oos, limit, mode, symbols),
            daemon=True, name="historical-replay-api",
        )
        self.thread.start()
        return {"ok": True, "message": "Replay uruchomiony"}

    def _row(self, symbol: str):
        rows = self.state["symbols"]
        for row in rows:
            if row.get("symbol") == symbol:
                return row
        row = {"symbol": symbol, "status": "Oczekuje", "detail": "—", "bars_5m": 0,
               "trades_oos": None, "net_r_oos": None}
        rows.append(row)
        return row

    def _progress(self, message: str):
        text = str(message or "")
        with self.lock:
            self.state["message"] = text
            if ": instrument " in text:
                symbol, counter = text.split(": instrument ", 1)
                try:
                    current, total = (int(value) for value in counter.split("/", 1))
                except (TypeError, ValueError):
                    current, total = 1, max(1, self.state.get("total") or 1)
                previous = self.state.get("current_symbol")
                if previous and previous != symbol:
                    old = self._row(previous)
                    if old["status"] not in {"Pominięta", "Błąd"}:
                        old["status"] = "Dane gotowe"
                self.state.update({"phase": "data", "current_symbol": symbol,
                                   "total": total, "completed": max(0, current - 1),
                                   "progress": round((current - 1) / max(1, total) * 40)})
                self._row(symbol).update(status="Pobieranie danych", detail="Świece BloFin")
            elif ": dane gotowe · " in text:
                symbol, detail = text.split(": dane gotowe · ", 1)
                row = self._row(symbol)
                digits = "".join(ch for ch in detail.split(" świec", 1)[0] if ch.isdigit())
                row.update(status="Dane gotowe", detail=detail,
                           bars_5m=int(digits or 0))
                self.state["completed"] = sum(r.get("status") == "Dane gotowe" for r in self.state["symbols"])
            elif ": pominięty" in text or ": symulacja nieudana" in text:
                symbol = text.split(":", 1)[0]
                self._row(symbol).update(status="Pominięta", detail=text.split("—", 1)[-1].strip())
            elif text.startswith("Portfelowy replay IS"):
                self.state.update(phase="in_sample", progress=45, message="Analiza części in-sample…")
            elif text.startswith("Portfelowy replay OOS"):
                self.state.update(phase="out_of_sample", progress=75, message="Analiza części out-of-sample…")
            elif "zakończony" in text.lower():
                self.state.update(phase="saving", progress=95, message="Zapisywanie raportu…")
            self._save_state()

    def _run(self, days, oos, limit, mode, symbols):
        try:
            feed = getattr(getattr(self.rt, "feeder", None), "blofin", None)
            if feed is None:
                raise RuntimeError("Brak źródła danych BloFin")
            from historical_replay import ReplayRequest, run_portfolio_replay_v2
            request = ReplayRequest(symbols=symbols if mode == "MANUAL" else (),
                                    universe_mode=mode, liquid_limit=limit,
                                    days=days, oos_fraction=oos)
            report = run_portfolio_replay_v2(feed, request, self._progress)
            portfolio = report.get("portfolio") or {}
            oos_result = portfolio.get("out_of_sample") or {}
            ins_result = portfolio.get("in_sample") or {}
            by_symbol = oos_result.get("by_symbol") or {}
            with self.lock:
                for symbol in set(report.get("symbols_downloaded") or []) | set(by_symbol):
                    row = self._row(symbol)
                    metrics = by_symbol.get(symbol) or {}
                    row.update(status="Przeskanowana", detail="Analiza OOS zakończona",
                               trades_oos=int(metrics.get("trades") or 0),
                               net_r_oos=round(float(metrics.get("net_r") or 0), 3))
                self.state.update(
                    running=False, phase="complete", progress=100,
                    message="Replay zakończony", completed=len(report.get("symbols_downloaded") or []),
                    finished_at=time.time(),
                    result={
                        "trades_oos": int(oos_result.get("trades") or 0),
                        "win_rate_oos": float(oos_result.get("win_rate") or 0),
                        "net_r_oos": float(oos_result.get("net_r") or 0),
                        "avg_r_oos": float(oos_result.get("avg_r") or 0),
                        "profit_factor_oos": oos_result.get("profit_factor"),
                        "max_drawdown_r_oos": float(oos_result.get("max_drawdown_r") or 0),
                        "trades_is": int(ins_result.get("trades") or 0),
                        "win_rate_is": float(ins_result.get("win_rate") or 0),
                        "net_r_is": float(ins_result.get("net_r") or 0),
                        "avg_r_is": float(ins_result.get("avg_r") or 0),
                        "profit_factor_is": ins_result.get("profit_factor"),
                        "max_drawdown_r_is": float(ins_result.get("max_drawdown_r") or 0),
                        "report_path": report.get("report_path"),
                    },
                )
                self._save_state()
        except Exception as exc:
            with self.lock:
                self.state.update(running=False, phase="error", message="Replay przerwany",
                                  finished_at=time.time(),
                                  error=str(exc), progress=0)
                self._save_state()


def start_engine_api(runtime, host=None, port=None) -> EngineApi:
    return EngineApi(runtime, host=host, port=port).start()
