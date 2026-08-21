from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel, QModelIndex, QObject, QPointF, QRectF, QRunnable,
    QSortFilterProxyModel, QThreadPool, Qt, QTimer, Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea, QSpinBox,
    QStackedWidget, QStyledItemDelegate, QStyleOptionViewItem, QTabWidget,
    QTableView, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

import config
import control_center
import secrets_store
import settings_store
import theme
from historical_replay import ReplayRequest, run_portfolio_replay_v2

BASE = Path(__file__).resolve().parent
STATE_FILE = BASE / "logs" / "bot_state.json"
STATE_ALT_FILE = BASE / "logs" / "bot_state_alt.json"

C = {
    "bg": "#050a11", "side": "#071019", "panel": "#0b141f", "panel2": "#0e1a28",
    "line": "#192b3d", "line2": "#23435e", "text": "#e8f0f8", "muted": "#8292a6",
    "cyan": "#2bc4ff", "blue": "#4d7fff", "green": "#25d791", "red": "#ff5568",
    "amber": "#f4ba45", "purple": "#a889ff",
}

# Compatibility labels kept for the original v16 parity audit.
UI_PARITY_LABELS = (
    "EXECUTION QUEUE", "CLOSED TRADES", "TRADE REPLAY", "STRATEGY HEALTH",
    "DATA SOURCES", "SYSTEM EVENTS", "MARKET CONTEXT", "DECISION FUNNEL",
)
SCANNER_SORTS = ["SCORE","24H","7D","PRICE"]
MTF_TIMEFRAMES = ("15m","1h","4h","1d")


def number(value: Any, digits: int = 2, prefix: str = "") -> str:
    try:
        if value is None:
            return "—"
        return f"{prefix}{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return str(value) if value not in (None, "") else "—"


def percent(value: Any, digits: int = 2, signed: bool = True) -> str:
    try:
        spec = "+" if signed else ""
        return f"{float(value):{spec}.{digits}f}%"
    except (TypeError, ValueError):
        return "—"


def direction(value: Any) -> str:
    value = str(value or "NEUTRAL").upper()
    return value if value in {"LONG", "SHORT", "NEUTRAL"} else "NEUTRAL"


def friendly_status(value: Any) -> str:
    raw = str(value or "WATCH").strip().upper()
    if "PATH=" in raw:
        raw = raw.split("PATH=", 1)[1].split("|", 1)[0]
    labels = {
        "NO_TRADE": "Brak wejścia", "REJECT": "Brak wejścia", "REJECTED": "Odrzucony",
        "NEUTRAL": "Obserwacja", "WATCH": "Obserwacja", "WAIT": "Oczekiwanie",
        "WAIT_ENTRY": "Czeka na wejście", "READY": "Gotowy do wejścia",
        "OPEN_OK": "Wejście zaakceptowane", "OPEN": "Pozycja otwarta",
        "CLOSED": "Pozycja zamknięta", "LONG": "Sygnał LONG", "SHORT": "Sygnał SHORT",
    }
    return labels.get(raw, raw.replace("_", " ").title())


def friendly_reason(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "Brak dodatkowych informacji"
    if "REJECT=" in raw:
        raw = raw.split("REJECT=", 1)[1].split("|", 1)[0]
    code = raw.split("(", 1)[0].upper()
    detail = raw[len(code):].strip("() ") if raw.upper().startswith(code) else ""
    labels = {
        "DAY_HTF_CONFLICT": "Niezgodny kierunek trendu 1h i 4h",
        "DAY_5M_TIMING_WAIT": "Czeka na potwierdzenie wejścia na 5m",
        "DAY_ADX_WEAK": "Trend jest zbyt słaby",
        "DAY_CHOP": "Rynek jest zbyt chaotyczny",
        "DAY_15M_NOT_ALIGNED": "Układ 15m nie potwierdza kierunku",
        "DAY_NEAR_BARRIER": "Cena znajduje się zbyt blisko przeszkody",
        "DAY_NOT_IN_LIQUID_TOP": "Para poza wybranym koszykiem płynności",
        "DAY_BLOFIN_DATA_NA": "Brakuje wymaganych danych BloFin",
        "DAY_PRICE_ATR_NA": "Brakuje ceny lub zmienności ATR",
        "DAY_NO_SYMBOL": "Brakuje symbolu instrumentu",
        "DAY_HTF_ALIGN": "Trend 1h i 4h jest zgodny",
        "DAY_15M_SETUP": "Układ 15m potwierdza setup",
        "DAY_5M_CONFIRM": "Wejście potwierdzone na 5m",
        "DAY_CLOSED_CANDLES": "Analiza korzysta z zamkniętych świec",
        "EXPECTED_NET_R_LOW": "Oczekiwany zysk po kosztach jest za niski",
        "OB_THIN": "Orderbook jest zbyt płytki",
        "PUMP_CHASE": "Cena jest po gwałtownym ruchu — bez pogoni za rynkiem",
        "PANIC": "Aktywny reżim podwyższonego ryzyka",
        "DATA_STALE": "Dane rynkowe są nieaktualne",
    }
    text = labels.get(code)
    if text:
        return f"{text} ({detail})" if detail else text
    if raw.upper() in ("NO_TRADE", "REJECT", "REJECTED"):
        return "Warunki wejścia nie zostały spełnione"
    return raw.replace("_", " ").replace("|", " · ").title()


def score_value(row: dict) -> float:
    raw = row.get("strength", row.get("score", 0)) or 0
    try:
        raw = float(raw)
        return raw * 100 if abs(raw) <= 1 else raw
    except (TypeError, ValueError):
        return 0.0


def rr_value(row: dict) -> float | None:
    try:
        entry = float(row.get("price") or row.get("entry"))
        stop = float(row.get("sl_price") or row.get("sl"))
        take = float(row.get("tp_price") or row.get("tp1_price") or row.get("tp"))
        if entry == stop:
            return None
        return abs(take - entry) / abs(entry - stop)
    except (TypeError, ValueError):
        return None


def format_fibonacci(fib: Any) -> str:
    """Human-readable Fibonacci confluence summary; never expose raw JSON in the UI."""
    if not isinstance(fib, dict) or not fib:
        return "Brak aktywnej mapy Fibonacci"
    fmap = fib.get("map") if isinstance(fib.get("map"), dict) else fib
    if not fmap.get("ok", True):
        return f"Mapa niedostępna\nPowód  {fmap.get('reason') or 'brak prawidłowego swingu'}"
    zone_names = {
        "primary_0.5_0.618": "Główna strefa 0.500–0.618",
        "deep_0.786": "Głęboka strefa 0.786",
        "shallow": "Płytkie cofnięcie 0.236–0.382",
        "full_retrace": "Pełne cofnięcie",
        "confluence_retest": "Potwierdzony retest",
        "none": "Poza strefą wejścia",
    }
    zone = str(fmap.get("zone") or "none")
    status = "POTWIERDZONE" if fib.get("in_primary") or fmap.get("in_primary") else "OBSERWACJA" if fmap.get("in_zone") else "POZA STREFĄ"
    lines = [
        f"Status       {status}",
        f"Strefa       {zone_names.get(zone, zone.replace('_', ' ').title())}",
        f"Kierunek     {fib.get('direction') or fmap.get('side') or '—'}",
        f"Retracement  {percent(float(fmap.get('retracement')) * 100, 1, False) if fmap.get('retracement') is not None else '—'}",
        f"Confluence   {number(fib.get('confluence_score', (fmap.get('confluence') or {}).get('score')), 2)}",
        f"Wiarygodność {number(fib.get('weight', fmap.get('weight')), 2)}" + (" · swing zastępczy" if fib.get("degraded") or (fmap.get("swing") or {}).get("degraded") else ""),
    ]
    levels = fmap.get("levels") or {}
    preferred = [("0.382", "38.2%"), ("0.5", "50.0%"), ("0.618", "61.8%"), ("0.786", "78.6%")]
    shown = [f"{label}  {number(levels.get(key), 8)}" for key, label in preferred if levels.get(key) is not None]
    if shown:
        lines.extend(["", "KLUCZOWE POZIOMY", "   ·   ".join(shown[:2]), "   ·   ".join(shown[2:])])
    tags = fib.get("confluence_tags") or (fmap.get("confluence") or {}).get("tags") or []
    if tags:
        lines.extend(["", "Potwierdzenia", " · ".join(str(tag).replace("FIB_", "").replace("_", " ") for tag in tags[:4])])
    return "\n".join(lines)


class DataAdapter:
    """Single, read-only UI projection of runtime plus its persisted rich state."""

    def __init__(self, runtime):
        self.rt = runtime

    def state(self) -> dict:
        value = None
        try:
            runtime_state = getattr(getattr(self.rt, "logger", None), "last_state", None)
            if isinstance(runtime_state, dict) and runtime_state:
                value = dict(runtime_state)
            else:
                candidates = [path for path in (STATE_FILE, STATE_ALT_FILE) if path.exists()]
                if candidates:
                    newest = max(candidates, key=lambda path: path.stat().st_mtime)
                    value = json.loads(newest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            value = None
        if not isinstance(value, dict):
            value = {}
        live = not bool(getattr(config, "PAPER_TRADING", True))
        sync = getattr(self.rt, "account_sync", None)
        exchange = sync.sync(force=False) if sync else {}
        exchange = exchange or {}
        local_positions = list(value.get("open_positions") or [])
        exchange_positions = list(exchange.get("positions") or [])
        value["mode"] = "LIVE" if live else "DEMO"
        value["exchange_account"] = exchange
        value["display_positions"] = exchange_positions if live else local_positions
        value["position_source"] = "BLOFIN" if live else "PAPER ENGINE"
        return control_center.enrich(self.rt, value)

    def mode(self) -> str:
        return "DEMO" if bool(getattr(config, "PAPER_TRADING", True)) else "LIVE"

    def positions(self) -> list[dict]:
        if self.mode() == "LIVE":
            state = self.state()
            exchange = state.get("exchange_account") or {}
            source = state.get("display_positions") or exchange.get("positions") or state.get("exchange_positions") or []
            return [{
                "symbol": item.get("symbol") or item.get("inst_id") or "—",
                "side": item.get("direction") or item.get("side") or "—",
                "entry": item.get("entry") or item.get("entry_price"),
                "mark": item.get("mark") or item.get("mark_price"),
                "size": item.get("size") or item.get("qty"),
                "margin": item.get("margin"),
                "sl": item.get("sl") or item.get("sl_price"),
                "tp": item.get("tp") or item.get("tp_price"),
                "pnl": item.get("pnl") or item.get("unrealized_pnl") or 0.0,
                "opened": item.get("opened") or item.get("opened_at") or "",
            } for item in source if isinstance(item, dict)]
        trader = getattr(self.rt, "trader", None)
        if not trader:
            return []
        lock = getattr(trader, "lock", None)
        if lock:
            with lock:
                source = list(getattr(trader, "positions", []) or [])
        else:
            source = list(getattr(trader, "positions", []) or [])
        prices = getattr(self.rt, "last_price_map", {}) or {}
        result = []
        for item in source:
            symbol = getattr(item, "symbol", "—")
            result.append({
                "symbol": symbol,
                "side": getattr(item, "side", getattr(item, "direction", "—")),
                "entry": getattr(item, "entry_price", getattr(item, "entry", None)),
                "mark": prices.get(symbol),
                "size": getattr(item, "size_usd", getattr(item, "size", getattr(item, "qty", None))),
                "margin": getattr(item, "margin", None),
                "sl": getattr(item, "sl", getattr(item, "sl_price", None)),
                "tp": getattr(item, "tp", getattr(item, "tp_price", getattr(item, "tp1", None))),
                "pnl": getattr(item, "unrealized_pnl", getattr(item, "pnl", 0.0)),
                "opened": getattr(item, "opened_at", getattr(item, "opened_iso", "")),
            })
        return result

    def account(self) -> dict:
        st, mode, positions = self.state(), self.mode(), self.positions()
        exchange = st.get("exchange_account") or {}
        if mode == "LIVE":
            margin = exchange.get("used_margin")
            if margin is None:
                margin = sum(float(item.get("margin") or 0) for item in positions)
            return {
                "mode": mode, "capital": exchange.get("balance", exchange.get("equity")),
                "equity": exchange.get("equity"), "available": exchange.get("available"),
                "margin": margin, "daily": exchange.get("daily_pnl"),
                "unrealized": exchange.get("unrealized_pnl", sum(float(item.get("pnl") or 0) for item in positions)),
                "positions": len(positions),
            }
        risk = getattr(self.rt, "risk", None)
        capital = float(getattr(risk, "current_capital", 0) or 0)
        margin = sum(float(x.get("margin") or 0) for x in positions)
        unrealized = sum(float(x.get("pnl") or 0) for x in positions)
        return {
            "mode": mode, "capital": capital, "equity": capital + unrealized,
            "available": max(0.0, capital - margin), "margin": margin,
            "daily": st.get("daily_pnl", getattr(risk, "daily_pnl", 0)),
            "unrealized": unrealized, "positions": len(positions),
        }

    def scanner(self) -> list[dict]:
        st = self.state()
        # Scanner daje szeroki rynek, analysis_board i signals dodaja pelne
        # szczegoly. Nie wolno wybierac tylko pierwszej niepustej kolekcji.
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
                merged[symbol].update({key: value for key, value in row.items() if value is not None})
                merged[symbol]["symbol"] = symbol
        return [merged[symbol] for symbol in order]

    def queue(self) -> list[dict]:
        rows = []
        for row in self.scanner():
            side = direction(row.get("direction") or row.get("signal_status"))
            decision = str(row.get("decision") or row.get("signal_status") or "").upper()
            if side != "NEUTRAL" or decision in {"OPEN_OK", "READY", "WAIT_ENTRY"}:
                rows.append(row)
        return sorted(rows, key=score_value, reverse=True)

    def closed(self) -> list[dict]:
        trader = getattr(self.rt, "trader", None)
        rows = list(getattr(trader, "closed_positions", []) or []) if trader else []
        if rows:
            return [{
                "time": getattr(p, "exit_time", ""), "side": getattr(p, "direction", ""),
                "symbol": getattr(p, "symbol", ""), "entry": getattr(p, "entry_price", None),
                "exit": getattr(p, "exit_price", None), "pnl": getattr(p, "pnl", None),
                "pnl_pct": getattr(p, "pnl_pct", None), "engine": getattr(p, "engine", "—"),
                "path": getattr(p, "decision_path", "—"),
            } for p in rows[-200:]][::-1]
        return list(self.state().get("closed_positions") or [])

    def events(self, limit: int = 300) -> list[dict]:
        path = BASE / "logs" / "bot_log.csv"
        try:
            if path.exists():
                with path.open("r", encoding="utf-8") as handle:
                    return list(csv.DictReader(handle))[-limit:][::-1]
        except (OSError, UnicodeError, csv.Error):
            pass
        return []

    def equity(self) -> list[dict]:
        return [x for x in (self.state().get("equity_history") or []) if isinstance(x, dict)]

    # ------------------------------------------------------------------
    # UI_DESK_V2 (DESK/SCAN/LAB) - patrz theme.py. Dodane bez zmiany zadnej
    # z powyzszych metod, zeby stary UI (za UI_DESK_V2=False) mial zero
    # ryzyka regresji.
    # ------------------------------------------------------------------
    def candidates(self, limit: int = 8) -> list[dict]:
        """SYM/SIDE/SCORE/GATE/WHY dla tabeli DESK/SCAN. GATE ∈ OPEN|WAIT|BLOCK,
        liczone przez risk.can_open_position() (prawdziwa bramka wejscia),
        nie przez zgadywanie ze stringow statusu - patrz spec: 'gate ∈ OPEN
        | WAIT | BLOCK - zamiast Obserwacja'."""
        risk = getattr(self.rt, "risk", None)
        rows = self.queue()[:max(1, int(limit))]
        out = []
        for row in rows:
            symbol = str(row.get("symbol") or "").upper()
            side = direction(row.get("direction"))
            score = round(score_value(row))
            reject_reason = row.get("reject_reason")
            gate, why = "WAIT", "liquidity"
            if risk is not None:
                try:
                    ok, reason = risk.can_open_position(dict(row))
                except Exception:
                    ok, reason = False, reject_reason or "UNKNOWN"
                if ok:
                    gate = "OPEN"
                    why = "liquidity"
                else:
                    reason_up = str(reason or "").upper()
                    # Miekkie/przejsciowe powody (miną same, bez zmiany
                    # setupu) -> WAIT; reszta -> BLOCK. Przyblizone, do
                    # dostrojenia razem z realnym uzyciem.
                    gate = "WAIT" if any(tag in reason_up for tag in ("COOLDOWN", "WAIT", "PENDING")) else "BLOCK"
                    why = friendly_reason(reason) if reason else why
            elif reject_reason:
                gate, why = "BLOCK", friendly_reason(reject_reason)
            out.append({"sym": symbol, "side": side, "score": score, "gate": gate, "why": why})
        return out

    def why_no_trade(self) -> dict:
        """Procentowy rozklad powodow odrzucen z risk.reject_log, zagregowany
        do 3 kategorii (liquidity/regime/corr), znormalizowany do sumy 100%
        miedzy soba. Rejects spoza wzorcow regime/corr trafiaja do liquidity
        jako najbardziej ogolnej kategorii - przyblizenie, nie precyzyjna
        taksonomia."""
        risk = getattr(self.rt, "risk", None)
        reject_log = list(getattr(risk, "reject_log", None) or [])
        buckets = {"regime": 0, "corr": 0, "liquidity": 0}
        for row in reject_log:
            reason = str(row.get("reason") or "").upper()
            if "CORR" in reason:
                buckets["corr"] += 1
            elif any(tag in reason for tag in ("REGIME", "PANIC", "TREND", "RANGE")):
                buckets["regime"] += 1
            else:
                buckets["liquidity"] += 1
        total = sum(buckets.values())
        if total <= 0:
            return {"liquidity": 0, "regime": 0, "corr": 0}
        return {k: round(v / total * 100) for k, v in buckets.items()}

    def regime(self) -> str:
        info = self.state().get("market_regime") or {}
        return str(info.get("regime") or "UNKNOWN").upper()

    def feed_status(self) -> str:
        # "sources" jest gotowym tekstem do wyswietlenia (feeder.sources_status()
        # w app.py zwraca str, np. "Universe:BlofinUSDT(180) | BinanceFAPI: OK
        # (527 pairs) | ..."), nie mapa - dict(str) rzucal ValueError co kazdy
        # refresh (kazdy znak string'a byl traktowany jako sekwencja dl. 1
        # zamiast pary klucz/wartosc), przez co _refresh_impl_v2() nigdy nie
        # aktualizowal SCAN po pierwszym pelnym skanie. Patrz native_ui.py
        # (str(data.get("sources"))) dla tego samego pola w starym shellu.
        return str(self.state().get("sources") or "")

    def scan_rows(self, universe_filter: str = "LIQUID") -> list[dict]:
        """Pelniejsze wiersze dla SCAN (# SYM PRICE 15M 24H spark SCORE PATH
        GATE) - candidates() na DESK ma za malo pol (max 8, tylko sym/side/
        score/gate/why). universe_filter ∈ LIQUID|MAJORS|ALL - LIQUID = tylko
        symbole faktycznie ewaluowane pelna kaskada w tym cyklu (bez
        reject_reason = V2_NOT_IN_LIQUID_TOP / V2_COLD_START_WARMING_UP -
        to drugie: 21.08.2026, kandydat jest w docelowym uniwersum V2, ale
        jeszcze nie doszla na niego kolej w partii rozgrzewania cold-startu,
        patrz DAYTRADING_V2_COLD_START_BATCH_SIZE), MAJORS = podzbior
        config.BINANCE_WS_MAJOR_SYMBOLS, ALL = caly scanner()."""
        risk = getattr(self.rt, "risk", None)
        rows = self.scanner()
        majors = {s.upper() for s in (getattr(config, "BINANCE_WS_MAJOR_SYMBOLS", None) or [])}
        out = []
        for row in rows:
            symbol = str(row.get("symbol") or "").upper()
            if not symbol:
                continue
            not_in_liquid_top = row.get("reject_reason") in (
                "V2_NOT_IN_LIQUID_TOP", "DAY_NOT_IN_LIQUID_TOP", "V2_COLD_START_WARMING_UP",
            )
            if universe_filter == "LIQUID" and not_in_liquid_top:
                continue
            if universe_filter == "MAJORS" and symbol not in majors:
                continue

            side = direction(row.get("direction"))
            score = round(score_value(row))
            price = row.get("price")
            chg_24h = row.get("blofin_change_24h", row.get("change_24h"))
            # 15m: brak dedykowanego pola liczbowego w sygnale - przyblizamy
            # grubym UP/DOWN/FLAT z kierunku sygnalu (spec: "w TREND: licz
            # UP/DOWN/FLAT z 15m, nie zostawiaj myslnika" - lepsze przyblizenie
            # niz pusty myslnik, jawnie oznaczone jako takie ponizej).
            chg_15m = row.get("chg_15m")
            trend_15m = "UP" if side == "LONG" else ("DOWN" if side == "SHORT" else "FLAT")
            path = str(row.get("engine") or row.get("decision_path") or "—")

            gate, why = "WAIT", "liquidity"
            reject_reason = row.get("reject_reason")
            if risk is not None:
                try:
                    ok, reason = risk.can_open_position(dict(row))
                except Exception:
                    ok, reason = False, reject_reason or "UNKNOWN"
                if ok:
                    gate = "OPEN"
                else:
                    reason_up = str(reason or "").upper()
                    gate = "WAIT" if any(tag in reason_up for tag in ("COOLDOWN", "WAIT", "PENDING")) else "BLOCK"
                    why = friendly_reason(reason) if reason else why
            elif reject_reason:
                gate, why = "BLOCK", friendly_reason(reject_reason)

            out.append({
                "sym": symbol, "side": side, "price": price, "chg_15m": chg_15m,
                "trend_15m": trend_15m, "chg_24h": chg_24h, "score": score,
                "path": path, "gate": gate, "why": why,
            })
        return out


class Card(QFrame):
    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(14, 12, 14, 12)
        self.body.setSpacing(8)
        if title:
            label = QLabel(title.upper())
            label.setObjectName("CardTitle")
            self.body.addWidget(label)


class KPI(Card):
    def __init__(self, title: str):
        super().__init__(title)
        self.value = QLabel("—")
        self.value.setObjectName("KPI")
        self.sub = QLabel("")
        self.sub.setObjectName("Muted")
        self.body.addWidget(self.value)
        self.body.addWidget(self.sub)

    def update_value(self, value: str, sub: str = "", tone: str = ""):
        self.value.setText(value)
        self.sub.setText(sub)
        self.value.setProperty("tone", tone)
        self.value.style().unpolish(self.value)
        self.value.style().polish(self.value)


class StatePill(QLabel):
    def set_state(self, text: str, tone: str = "muted"):
        self.setText(text)
        self.setProperty("tone", tone)
        self.style().unpolish(self)
        self.style().polish(self)


class MarketChart(QWidget):
    """Lekki wykres świecowy z niezależnie włączanymi nakładkami cenowymi."""

    def __init__(self):
        super().__init__()
        self.data = {}
        self.levels = {}
        self.visible_bars = 100
        self.interval = "1h"
        self.overlays = {"ema": True, "trade_plan": True, "levels": False, "viper": False}
        self.message = "Select an asset to load BloFin candles"
        self.setMinimumHeight(390)

    def set_overlay_visibility(self, **overlays):
        self.overlays.update({name: bool(value) for name, value in overlays.items() if name in self.overlays})
        self.update()

    @staticmethod
    def _ema(values, period):
        if len(values) < period:
            return []
        alpha = 2.0 / (period + 1.0)
        out, value = [None] * (period - 1), sum(values[:period]) / period
        out.append(value)
        for item in values[period:]:
            value = item * alpha + value * (1.0 - alpha)
            out.append(value)
        return out

    def set_loading(self, message):
        self.message = message
        self.update()

    def set_market_data(self, data, levels=None, source="BloFin", interval="1h"):
        self.data = dict(data or {})
        self.levels = dict(levels or {})
        self.interval = interval
        self.message = f"{source} · closed candles"
        self.update()

    def wheelEvent(self, event):
        self.visible_bars = max(30, min(220, self.visible_bars + (-10 if event.angleDelta().y() > 0 else 10)))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(C["panel"]))
        closes = list(self.data.get("closes") or [])[-self.visible_bars:]
        highs = list(self.data.get("highs") or [])[-len(closes):]
        lows = list(self.data.get("lows") or [])[-len(closes):]
        volumes = list(self.data.get("volumes") or [])[-len(closes):]
        if len(closes) < 3 or len(highs) != len(closes) or len(lows) != len(closes):
            painter.setPen(QColor(C["muted"]))
            painter.drawText(self.rect(), Qt.AlignCenter, self.message)
            return
        left, right, top = 62.0, 14.0, 24.0
        volume_h, bottom = 72.0, 22.0
        price_rect = QRectF(left, top, max(10.0, self.width() - left - right), max(80.0, self.height() - top - bottom - volume_h))
        volume_rect = QRectF(left, price_rect.bottom() + 8, price_rect.width(), volume_h - 8)
        relevant_levels = [
            float(value) for name, value in self.levels.items()
            if isinstance(value, (int, float)) and float(value) > 0
            and ((name in ("ENTRY", "SL", "TP") and self.overlays["trade_plan"])
                 or (name not in ("ENTRY", "SL", "TP") and self.overlays["levels"]))
        ]
        lo, hi = min(lows + relevant_levels), max(highs + relevant_levels)
        pad = max((hi - lo) * 0.05, hi * 0.0001)
        lo, hi = lo - pad, hi + pad
        y = lambda value: price_rect.bottom() - (float(value) - lo) / max(hi - lo, 1e-12) * price_rect.height()
        step = price_rect.width() / len(closes)
        body_w = max(1.0, min(8.0, step * 0.62))
        painter.setPen(QPen(QColor(C["line"]), 1))
        for i in range(5):
            yy = price_rect.top() + i * price_rect.height() / 4
            painter.drawLine(QPointF(price_rect.left(), yy), QPointF(price_rect.right(), yy))
            price = hi - i * (hi - lo) / 4
            painter.setPen(QColor(C["muted"]))
            painter.drawText(2, int(yy + 4), f"{price:.8g}")
            painter.setPen(QPen(QColor(C["line"]), 1))
        opens = list(self.data.get("opens") or [])[-len(closes):]
        previous = closes[0]
        max_volume = max(volumes or [1]) or 1
        for i, close in enumerate(closes):
            open_price = opens[i] if len(opens) == len(closes) else previous
            previous = close
            x = price_rect.left() + (i + 0.5) * step
            color = QColor(C["green"] if close >= open_price else C["red"])
            painter.setPen(QPen(color, 1))
            painter.drawLine(QPointF(x, y(highs[i])), QPointF(x, y(lows[i])))
            body_top, body_bottom = y(max(open_price, close)), y(min(open_price, close))
            painter.fillRect(QRectF(x - body_w / 2, body_top, body_w, max(1.2, body_bottom - body_top)), color)
            if i < len(volumes):
                vh = float(volumes[i] or 0) / max_volume * volume_rect.height()
                painter.fillRect(QRectF(x - body_w / 2, volume_rect.bottom() - vh, body_w, vh), QColor(color.red(), color.green(), color.blue(), 120))
        all_closes = list(self.data.get("closes") or [])
        ema_pair = {"5m": (21, 55), "15m": (21, 55), "1h": (34, 89), "4h": (50, 200), "1d": (50, 200)}.get(
            self.interval, (34, 89)
        )
        if self.overlays["ema"]:
            ema_colors = ((ema_pair[0], C["cyan"]), (ema_pair[1], C["amber"]))
            for period, color_name in ema_colors:
                series = self._ema(all_closes, period)[-len(closes):]
                points = [(i, value) for i, value in enumerate(series) if value is not None]
                if len(points) < 2:
                    continue
                path = QPainterPath()
                for j, (i, value) in enumerate(points):
                    point = QPointF(price_rect.left() + (i + 0.5) * step, y(value))
                    path.moveTo(point) if j == 0 else path.lineTo(point)
                painter.setPen(QPen(QColor(color_name), 1.4))
                painter.drawPath(path)
        viper = self.data.get("_viper") or {}
        profile = [item for item in (viper.get("levels") or []) if self.overlays["viper"] and lo <= float(item.get("price") or 0) <= hi]
        max_profile = max((float(item.get("total_volume") or 0) for item in profile), default=0.0)
        profile_width = float(viper.get("chart_distance") or 90)
        bar_height = max(2.0, min(float(viper.get("bar_size") or 14), step * 2.0))
        for item in profile:
            total = float(item.get("total_volume") or 0)
            if total <= 0 or max_profile <= 0:
                continue
            width = profile_width * total / max_profile
            buy_width = width * float(item.get("buy_volume") or 0) / total
            sell_width = width - buy_width
            yy = y(float(item["price"]))
            x_right = price_rect.right() - 4
            painter.fillRect(QRectF(x_right - width, yy - bar_height / 2, width, bar_height), QColor(95, 108, 108, 145))
            if sell_width > 0:
                painter.fillRect(QRectF(x_right - width, yy - bar_height / 2, sell_width, bar_height), QColor("#800000"))
            if buy_width > 0:
                painter.fillRect(QRectF(x_right - buy_width, yy - bar_height / 2, buy_width, bar_height), QColor("#00ff00"))
            painter.setPen(QColor("#ffffff"))
            painter.drawText(int(x_right - width - 58), int(yy + 4), f"{float(item['price']):.8g}")
        level_colors = {
            "ENTRY": C["cyan"], "SL": C["red"], "TP": C["green"], "P": "#ffffff",
            "R1": "#800000", "R2": "#800000", "R3": "#800000",
            "S1": "#00ff00", "S2": "#00ff00", "S3": "#00ff00",
            "RES": "#800000", "SUP": "#00ff00", "VIPER": "#ffffff",
        }
        for name, value in self.levels.items():
            is_trade_plan = name in ("ENTRY", "SL", "TP")
            if (is_trade_plan and not self.overlays["trade_plan"]) or (not is_trade_plan and not self.overlays["levels"]):
                continue
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if not (lo <= value <= hi):
                continue
            color = level_colors.get(name.split()[0], C["purple"])
            yy = y(value)
            painter.setPen(QPen(QColor(color), 1, Qt.DashLine))
            painter.drawLine(QPointF(price_rect.left(), yy), QPointF(price_rect.right(), yy))
            painter.drawText(int(price_rect.left() + 5), int(yy - 3), f"{name}  {value:.8g}")
        painter.setPen(QColor(C["muted"]))
        painter.drawText(
            int(price_rect.left()), 15,
            f"{self.message} · wheel: zoom" + (f" · EMA {ema_pair[0]}/{ema_pair[1]}" if self.overlays["ema"] else ""),
        )


class PriceTickerSignals(QObject):
    updated = Signal(dict)


class PriceTickerTask(QRunnable):
    """BTC/ETH co 1s z Binance/Bybit/CoinGecko (Blofin celowo pomijany -
    to osobne, szybkie zrodlo niezalezne od wolnego cyklu bota/analizy)."""
    def __init__(self, feeder):
        super().__init__()
        self.feeder = feeder
        self.signals = PriceTickerSignals()

    def run(self):
        prices = {"BTC": None, "ETH": None}
        if self.feeder is not None:
            try:
                bn = self.feeder.binance.fetch_all_tickers() or {}
            except Exception:
                bn = {}
            try:
                by = self.feeder.bybit.fetch_all_tickers() or {}
            except Exception:
                by = {}
            cg = None
            for sym in ("BTC", "ETH"):
                price = (bn.get(sym) or {}).get("binance_price") or (by.get(sym) or {}).get("bybit_price")
                if price is None:
                    try:
                        if cg is None:
                            cg = self.feeder._refresh_coingecko_top() or {}
                        price = (cg.get(sym) or {}).get("price")
                    except Exception:
                        pass
                prices[sym] = price
        try:
            self.signals.updated.emit(prices)
        except RuntimeError:
            # Okno/aplikacja zdazyla sie zamknac zanim to zadanie w tle
            # skonczylo prace (zadanie odpalane co 1s, wiec zawsze jest jakies
            # w locie) - odbiorca sygnalu juz nie istnieje po stronie Qt/C++.
            # Nie ma juz nic do zaktualizowania, wiec po prostu konczymy cicho
            # zamiast wypluwac traceback po zamknieciu aplikacji.
            pass


class ChartLoadSignals(QObject):
    loaded = Signal(str, str, dict, str)


class ChartLoadTask(QRunnable):
    def __init__(self, feeder, symbol, interval):
        super().__init__()
        self.feeder, self.symbol, self.interval = feeder, symbol, interval
        self.signals = ChartLoadSignals()

    def run(self):
        blofin_bar = {"5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1D"}.get(self.interval, "1H")
        data, source = {}, "BloFin"
        try:
            feed = getattr(self.feeder, "blofin", None)
            if feed:
                data = feed.fetch_klines_ohlcv(self.symbol, bar=blofin_bar, limit=380) or {}
            if len(data.get("closes") or []) < 20:
                source = "Binance fallback"
                feed = getattr(self.feeder, "binance", None)
                data = feed.fetch_klines_ohlcv(self.symbol, interval=self.interval, limit=380) if feed else {}
        except Exception as exc:
            data, source = {"error": str(exc)}, "Chart error"
        self.signals.loaded.emit(self.symbol, self.interval, data or {}, source)


class ReplaySignals(QObject):
    progress = Signal(str)
    completed = Signal(dict)
    failed = Signal(str)


class ReplayTask(QRunnable):
    """21.08.2026: przelaczone z run_historical_replay() (silnik V1,
    daytrading_engine.py, niezalezne per-symbol ksiazki bez konkurencji
    o sloty) na run_portfolio_replay_v2() - to samo, co realnie handluje
    bot (DayTradingEngineV2, STRATEGY_MODE=DAYTRADING_V2 w settings.json),
    plus realistyczna, wspolna ksiazka pozycji z limitem MAX_POSITIONS,
    ktorej V1 w ogole nie symulowal. Powod: replay V1 dawal wyniki, ktore
    nic nie mowily o strategii faktycznie live/paper - zobacz rozmowe
    z 21.08.2026 (upload daytrading_replay_90d... okazal sie testowac
    zupelnie inny silnik niz ten skonfigurowany)."""

    def __init__(self, feed, request):
        super().__init__()
        self.feed, self.request = feed, request
        self.signals = ReplaySignals()

    def run(self):
        try:
            result = run_portfolio_replay_v2(self.feed, self.request, self.signals.progress.emit)
            self.signals.completed.emit(result)
        except Exception as exc:
            self.signals.failed.emit(str(exc))


class EquityChart(QWidget):
    """Dependency-free native equity and drawdown plot."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.points: list[dict] = []
        self.setMinimumHeight(240)

    def set_points(self, points: list[dict]):
        self.points = points[-240:]
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(42, 12, -12, -28)
        split = int(rect.height() * .68)
        equity_rect = QRectF(rect.left(), rect.top(), rect.width(), split)
        draw_rect = QRectF(rect.left(), rect.top() + split + 18, rect.width(), rect.height() - split - 18)
        painter.setPen(QPen(QColor(C["line"]), 1))
        for i in range(5):
            y = equity_rect.top() + i * equity_rect.height() / 4
            painter.drawLine(QPointF(equity_rect.left(), y), QPointF(equity_rect.right(), y))
        if len(self.points) < 2:
            painter.setPen(QColor(C["muted"]))
            painter.drawText(equity_rect, Qt.AlignCenter, "Equity history will appear after runtime cycles")
            return
        values = [float(x.get("equity", x.get("capital", 0)) or 0) for x in self.points]
        lo, hi = min(values), max(values)
        if hi == lo:
            hi += 1
        path = QPainterPath()
        for i, value in enumerate(values):
            x = equity_rect.left() + i * equity_rect.width() / max(1, len(values) - 1)
            y = equity_rect.bottom() - (value - lo) / (hi - lo) * equity_rect.height()
            path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
        painter.setPen(QPen(QColor(C["green"]), 2))
        painter.drawPath(path)
        dds = [abs(float(x.get("drawdown_pct", 0) or 0)) for x in self.points]
        max_dd = max(dds) or 1
        dd_path = QPainterPath()
        for i, value in enumerate(dds):
            x = draw_rect.left() + i * draw_rect.width() / max(1, len(dds) - 1)
            y = draw_rect.top() + value / max_dd * draw_rect.height()
            dd_path.moveTo(x, y) if i == 0 else dd_path.lineTo(x, y)
        painter.setPen(QPen(QColor(C["red"]), 1.5))
        painter.drawPath(dd_path)
        painter.setPen(QColor(C["muted"]))
        painter.drawText(4, 26, number(hi, 2))
        painter.drawText(4, int(equity_rect.bottom()), number(lo, 2))
        painter.setPen(QColor(C["red"]))
        painter.drawText(4, int(draw_rect.top() + 12), "DD")


class GateBadge(QLabel):
    """Pigulka OPEN/WAIT/BLOCK - kolor z theme.gate_tone(), nie z ad-hoc
    stringow. Uzywana na DESK (kandydaci) i SCAN (tabela)."""

    def __init__(self, gate: str = "WAIT", parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.set_gate(gate)

    def set_gate(self, gate: str):
        text_color, bg, border = theme.gate_tone(gate)
        self.setText(str(gate or "WAIT").upper())
        self.setStyleSheet(
            f"color:{text_color}; background:{bg}; border:1px solid {border}; "
            f"border-radius:2px; padding:2px 8px; font-weight:700; font-size:10px;"
        )


class MiniBar(QWidget):
    """Pozioma belka procentowa - uzywana w panelu WHY NO TRADE (DESK)."""

    def __init__(self, color: str = theme.CYAN, parent=None):
        super().__init__(parent)
        self._pct = 0.0
        self._color = color
        self.setMinimumHeight(6)
        self.setMaximumHeight(6)

    def set_percent(self, pct: float):
        self._pct = max(0.0, min(100.0, float(pct or 0)))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(theme.LINE))
        filled_w = self.width() * (self._pct / 100.0)
        if filled_w > 0:
            painter.fillRect(QRectF(0, 0, filled_w, self.height()), QColor(self._color))


class WhyNoTradeChip(QFrame):
    """Jeden chip z panelu WHY NO TRADE - etykieta + procent + MiniBar."""

    def __init__(self, label: str, color: str, parent=None):
        super().__init__(parent)
        self.setObjectName("V2Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)
        self._label = QLabel(label)
        self._label.setStyleSheet(f"color:{theme.MUTED}; font-size:10px; font-weight:700;")
        self._value = QLabel("0%")
        self._value.setStyleSheet(f"color:{theme.TEXT}; font-size:16px; font-weight:700;")
        self._bar = MiniBar(color)
        layout.addWidget(self._label)
        layout.addWidget(self._value)
        layout.addWidget(self._bar)

    def set_percent(self, pct: float):
        self._value.setText(f"{round(pct)}%")
        self._bar.set_percent(pct)


class DeskPage(QWidget):
    """Strona DESK (UI_DESK_V2): 22/48/30 - pozycje+equity | wykres | kandydaci.
    Jedyna strona, ktora 'musi dzialac w paperze' (cytat ze specyfikacji) -
    reuzywa istniejace EquityChart/MarketChart/ChartLoadTask, nie duplikuje ich."""

    symbol_selected = Signal(str)

    def __init__(self, window: "MainWindow", parent=None):
        super().__init__(parent)
        self.setObjectName("DeskV2Root")
        self.window_ = window  # dostep do window_.data (DataAdapter), chart_pool, itd.
        self._build()

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        left = QVBoxLayout()
        left.setSpacing(10)

        mode_card = Card("ACCOUNT MODE")
        mode_row = QHBoxLayout()
        self.desk_demo_button = QPushButton("●  DEMO", objectName="ModeDemo")
        self.desk_live_button = QPushButton("●  LIVE", objectName="ModeLive")
        for button in (self.desk_demo_button, self.desk_live_button):
            button.setCheckable(True)
            button.setAutoExclusive(True)
        # Reuzywa dokladnie ta sama, w pelni zabezpieczona sciezke co Control
        # Center/SET (MainWindow.request_dashboard_mode -> apply_account_mode)
        # - zero duplikacji logiki blokujacej zmiane trybu przy otwartych
        # pozycjach / braku przetestowanych kluczy LIVE.
        self.desk_demo_button.clicked.connect(lambda: self.window_.request_dashboard_mode(False))
        self.desk_live_button.clicked.connect(lambda: self.window_.request_dashboard_mode(True))
        self.desk_mode_status = StatePill(objectName="Pill")
        mode_row.addWidget(self.desk_demo_button)
        mode_row.addWidget(self.desk_live_button)
        mode_row.addWidget(self.desk_mode_status)
        mode_row.addStretch()
        mode_card.body.addLayout(mode_row)
        left.addWidget(mode_card)

        self.equity_kpi = KPI("EQUITY")
        self.equity_chart = EquityChart()
        self.equity_chart.setMinimumHeight(90)
        self.equity_chart.setMaximumHeight(110)
        self.equity_kpi.body.addWidget(self.equity_chart)
        left.addWidget(self.equity_kpi)

        stats_card = Card()
        stats_row = QHBoxLayout()
        self.free_label = self._stat_pair(stats_row, "FREE")
        self.used_label = self._stat_pair(stats_row, "USED")
        self.daily_label = self._stat_pair(stats_row, "DAILY PNL")
        stats_card.body.addLayout(stats_row)
        left.addWidget(stats_card)

        self.positions_count = QLabel("0/0")
        self.positions_count.setObjectName("KPI")
        pos_header = Card("POSITIONS")
        pos_header.body.addWidget(self.positions_count)
        left.addWidget(pos_header)

        positions_card = Card("OPEN POSITIONS")
        self.positions_table = QTableWidget(0, 7)
        self.positions_table.setObjectName("V2Table")
        self.positions_table.setHorizontalHeaderLabels(["SYM", "SIDE", "SIZE", "MARK", "ENTRY", "SL", "PNL"])
        self.positions_table.verticalHeader().setVisible(False)
        self.positions_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.positions_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.positions_table.horizontalHeader().setStretchLastSection(True)
        self.positions_table.cellClicked.connect(self._on_position_clicked)
        positions_card.body.addWidget(self.positions_table)
        self.close_all_btn = QPushButton("CLOSE ALL")
        self.close_all_btn.setObjectName("V2CloseAll")
        self.close_all_btn.clicked.connect(self._on_close_all)
        positions_card.body.addWidget(self.close_all_btn)
        left.addWidget(positions_card, 1)

        left_widget = QWidget()
        left_widget.setLayout(left)
        root.addWidget(left_widget, 22)

        center = QVBoxLayout()
        center.setSpacing(6)
        self.chart_header = QLabel("SELECT A SYMBOL")
        self.chart_header.setObjectName("V2CardTitle")
        center.addWidget(self.chart_header)
        self.chart = MarketChart()
        center.addWidget(self.chart, 1)
        tf_row = QHBoxLayout()
        self.tf_buttons: dict[str, QPushButton] = {}
        for tf in ("1m", "5m", "15m", "1h", "4h", "1d"):
            btn = QPushButton(tf)
            btn.setCheckable(True)
            btn.setChecked(tf == "15m")
            btn.clicked.connect(lambda _checked, t=tf: self._on_timeframe_clicked(t))
            self.tf_buttons[tf] = btn
            tf_row.addWidget(btn)
        tf_row.addStretch()
        center.addLayout(tf_row)
        center_widget = QWidget()
        center_widget.setLayout(center)
        root.addWidget(center_widget, 48)

        right = QVBoxLayout()
        right.setSpacing(10)
        candidates_card = Card("NEXT CANDIDATES")
        self.candidates_table = QTableWidget(0, 4)
        self.candidates_table.setObjectName("V2Table")
        self.candidates_table.setHorizontalHeaderLabels(["SYM", "SIDE", "SCORE", "GATE"])
        self.candidates_table.verticalHeader().setVisible(False)
        self.candidates_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.candidates_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.candidates_table.horizontalHeader().setStretchLastSection(True)
        self.candidates_table.cellClicked.connect(self._on_candidate_clicked)
        candidates_card.body.addWidget(self.candidates_table)
        right.addWidget(candidates_card, 1)

        why_card = Card("WHY NO TRADE")
        why_row = QHBoxLayout()
        self.why_chips = {
            "liquidity": WhyNoTradeChip("LIQUIDITY", theme.CYAN),
            "regime": WhyNoTradeChip("REGIME", theme.WAIT),
            "corr": WhyNoTradeChip("CORR", theme.PURPLE),
        }
        for chip in self.why_chips.values():
            why_row.addWidget(chip)
        why_card.body.addLayout(why_row)
        right.addWidget(why_card)

        right_widget = QWidget()
        right_widget.setLayout(right)
        root.addWidget(right_widget, 30)

        self._selected_timeframe = "15m"
        self._current_chart_symbol: str | None = None

    def sync_mode_buttons(self, demo: bool):
        """Trzyma DEMO/LIVE w synchronizacji z realnym trybem. Wolane
        zarowno z apply_state() (pelny skan) JAK I bezposrednio co 1s tick
        z MainWindow._refresh_impl_v2() (tak jak mode_pill_v2) - inaczej po
        odrzuconej/anulowanej probie zmiany trybu (np. brak kluczy LIVE)
        przycisk zostawal wizualnie "checked" na zla strone az do
        nastepnego PELNEGO skanu (15-30s), bo Qt przelacza checked-state
        checkable+autoExclusive przycisku na sam klik, niezaleznie od tego,
        czy apply_account_mode() faktycznie zmienil tryb."""
        self.desk_demo_button.setChecked(demo)
        self.desk_live_button.setChecked(not demo)
        self.desk_mode_status.set_state(
            "DEMO · NO REAL ORDERS" if demo else "LIVE · REAL ACCOUNT",
            "green" if demo else "red",
        )

    @staticmethod
    def _stat_pair(row: QHBoxLayout, title: str) -> QLabel:
        col = QVBoxLayout()
        label_title = QLabel(title)
        label_title.setObjectName("V2CardTitle")
        value = QLabel("—")
        value.setObjectName("V2Mono")
        col.addWidget(label_title)
        col.addWidget(value)
        wrap = QWidget()
        wrap.setLayout(col)
        row.addWidget(wrap)
        return value

    # ------------------------------------------------------------------
    # Odswiezanie
    # ------------------------------------------------------------------
    def apply_state(self, data: "DataAdapter"):
        """Pelny apply_state (co pelny skan, 15-30s) - patrz apply_tick()
        dla szybszej sciezki (ceny/PnL co 1s)."""
        account = data.account()
        self.sync_mode_buttons(account["mode"] == "DEMO")
        self.equity_kpi.update_value(number(account.get("equity"), 2, "$"))
        self.equity_chart.set_points(data.equity())
        self.free_label.setText(number(account.get("available"), 2, "$"))
        self.used_label.setText(number(account.get("margin"), 2, "$"))
        daily = account.get("daily")
        self.daily_label.setText(number(daily, 2, "+$" if (daily or 0) >= 0 else "$"))

        positions = data.positions()
        max_pos = int(getattr(config, "MAX_POSITIONS", 10) or 10)
        self.positions_count.setText(f"{len(positions)}/{max_pos}")
        self._fill_positions(positions)

        candidates = data.candidates(limit=8)
        self._fill_candidates(candidates)

        why = data.why_no_trade()
        for key, chip in self.why_chips.items():
            chip.set_percent(why.get(key, 0))

        if self._current_chart_symbol is None and positions:
            self.select_symbol(str(positions[0].get("symbol") or ""))

    def apply_tick(self, prices: dict):
        """Szybka sciezka (co ~1s): tylko ceny/PnL w tabeli pozycji, bez
        przebudowy calej strony - patrz spec 'Odswiezanie: tick 1 s tylko
        ceny i PnL wierszy'."""
        if not prices:
            return
        for row in range(self.positions_table.rowCount()):
            sym_item = self.positions_table.item(row, 0)
            if sym_item is None:
                continue
            symbol = sym_item.text()
            price = prices.get(symbol)
            if price is None:
                continue
            mark_item = self.positions_table.item(row, 3)
            if mark_item is not None:
                mark_item.setText(number(price, 4))

    def _fill_positions(self, positions: list[dict]):
        table = self.positions_table
        table.setRowCount(len(positions))
        for row, pos in enumerate(positions):
            symbol = str(pos.get("symbol") or "—")
            side = direction(pos.get("side"))
            entry = pos.get("entry")
            sl = pos.get("sl")
            mark = pos.get("mark")
            pnl = pos.get("pnl")
            values = [symbol, side, number(pos.get("size"), 3), number(mark, 4), number(entry, 4), number(sl, 4), number(pnl, 2, "+$" if (pnl or 0) >= 0 else "$")]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 1:
                    item.setForeground(QColor(theme.side_color(side)))
                if col == 5:
                    # SL zielony/cyjan gdy pozycja juz na plusie (spec: "SL
                    # zielony/cyjan gdy juz na plusie").
                    profitable = (pnl or 0) > 0
                    item.setForeground(QColor(theme.LONG if profitable else theme.MUTED))
                if col == 6:
                    item.setForeground(QColor(theme.LONG if (pnl or 0) >= 0 else theme.SHORT))
                table.setItem(row, col, item)

    def _fill_candidates(self, candidates: list[dict]):
        table = self.candidates_table
        table.setRowCount(len(candidates))
        for row, cand in enumerate(candidates):
            table.setItem(row, 0, QTableWidgetItem(cand.get("sym", "—")))
            side_item = QTableWidgetItem(cand.get("side", "—"))
            side_item.setForeground(QColor(theme.side_color(cand.get("side"))))
            table.setItem(row, 1, side_item)
            table.setItem(row, 2, QTableWidgetItem(str(cand.get("score", "—"))))
            badge = GateBadge(cand.get("gate", "WAIT"))
            table.setCellWidget(row, 3, badge)

    # ------------------------------------------------------------------
    # Interakcja
    # ------------------------------------------------------------------
    def select_symbol(self, symbol: str):
        if not symbol:
            return
        self._current_chart_symbol = symbol
        self.chart_header.setText(f"{symbol}USDT · {self._selected_timeframe.upper()} · BLOFIN")
        self.chart.set_loading(f"Ładowanie {symbol}…")
        feeder = getattr(self.window_.rt, "feeder", None)
        task = ChartLoadTask(feeder, symbol, self._selected_timeframe)
        task.signals.loaded.connect(self._on_chart_loaded)
        self.window_.chart_pool.start(task)
        self.symbol_selected.emit(symbol)

    def _on_chart_loaded(self, symbol: str, interval: str, data: dict, source: str):
        if symbol != self._current_chart_symbol or interval != self._selected_timeframe:
            return
        if data.get("error"):
            self.chart.set_loading(f"Błąd danych: {data['error']}")
            return
        levels = self._protection_levels(symbol)
        self.chart.set_market_data(data, levels=levels, source=source, interval=interval)

    def _protection_levels(self, symbol: str) -> dict:
        protection = getattr(self.window_.rt, "protection", None)
        attachments = getattr(protection, "attachments", None) or {}
        for key, item in attachments.items():
            if str(item.get("symbol") or "").upper() == symbol.upper():
                return {
                    "ENTRY": item.get("entry_price") or item.get("entry"),
                    "SL": item.get("sl_price"),
                    "TP": item.get("tp_price"),
                }
        return {}

    def _on_timeframe_clicked(self, tf: str):
        self._selected_timeframe = tf
        for name, btn in self.tf_buttons.items():
            btn.setChecked(name == tf)
        if self._current_chart_symbol:
            self.select_symbol(self._current_chart_symbol)

    def _on_position_clicked(self, row: int, _col: int):
        item = self.positions_table.item(row, 0)
        if item is not None:
            self.select_symbol(item.text())

    def _on_candidate_clicked(self, row: int, _col: int):
        item = self.candidates_table.item(row, 0)
        if item is not None:
            self.select_symbol(item.text())

    def _on_close_all(self):
        # Wykonanie zostawiamy istniejacej infrastrukturze (ta sama sciezka,
        # co stary UI) - DeskPage tylko wywoluje, nie duplikuje logiki.
        try:
            self.window_.close_all()
        except Exception as exc:
            print(f"[DeskV2] close_all błąd: {exc}")


class ScanTableModel(QAbstractTableModel):
    """Model dla SCAN - QAbstractTableModel, NIE QTableWidget (spec: "177
    wierszy x cykl zabije UI"). Kolumny: # SYM PRICE 15M 24H TREND SCORE
    PATH GATE - spark (TREND) i GATE malowane przez ScanItemDelegate, nie
    osobne widgety per-komorka."""

    COLUMNS = ["#", "SYM", "PRICE", "15M", "24H", "TREND (15M)", "SCORE", "PATH", "GATE"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self._rows = list(rows or [])
        self.endResetModel()

    def row_dict(self, row: int) -> dict:
        return self._rows[row] if 0 <= row < len(self._rows) else {}

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole or orientation != Qt.Horizontal:
            return None
        return self.COLUMNS[section]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        col = index.column()
        if role == Qt.DisplayRole:
            if col == 0:
                return str(index.row() + 1)
            if col == 1:
                return row.get("sym", "—")
            if col == 2:
                return number(row.get("price"), 4)
            if col == 3:
                chg = row.get("chg_15m")
                return percent(chg) if chg is not None else row.get("trend_15m", "NA")
            if col == 4:
                chg = row.get("chg_24h")
                return percent(chg) if chg is not None else "NA"
            if col == 5:
                return ""  # sparkline - malowane przez delegat
            if col == 6:
                return str(row.get("score", "—"))
            if col == 7:
                return row.get("path", "—")
            if col == 8:
                return row.get("gate", "WAIT")
        if role == Qt.UserRole and col == 5:
            return row.get("spark")
        if role == Qt.TextAlignmentRole and col in (0, 2, 3, 4, 6):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ForegroundRole:
            if col == 1:
                return QColor(theme.side_color(row.get("side")))
            if col in (3, 4):
                val = row.get("chg_15m") if col == 3 else row.get("chg_24h")
                if isinstance(val, (int, float)):
                    return QColor(theme.LONG if val >= 0 else theme.SHORT)
        return None


class ScanFilterProxy(QSortFilterProxyModel):
    """Filtrowanie w modelu (filterAcceptsRow), nie przez przebudowe tabeli -
    spec: search + LONG/SHORT/BOTH. Filtr LIQUID/MAJORS/ALL dzieje sie wyzej
    (ScanPage.apply_state wola scan_rows(universe_filter) na nowo, bo to
    zmienia SAM ZESTAW wierszy, nie tylko widocznosc istniejacych)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._search = ""
        self._side_filter = "BOTH"

    def set_search(self, text: str) -> None:
        self._search = str(text or "").strip().upper()
        self.invalidateFilter()

    def set_side_filter(self, side: str) -> None:
        self._side_filter = side
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:
        model = self.sourceModel()
        row = model.row_dict(source_row)
        if self._search and self._search not in str(row.get("sym") or "").upper():
            return False
        if self._side_filter != "BOTH" and row.get("side") != self._side_filter:
            return False
        return True


class ScanItemDelegate(QStyledItemDelegate):
    """Maluje GATE (pigulka), SCORE (pasek) i TREND (sparkline) bezposrednio
    przez QPainter - NIE setCellWidget/setIndexWidget per wiersz (to samo
    zabiloby wydajnosc, ktorej QAbstractTableModel mial unikac)."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        col = index.column()
        if col == 8:
            self._paint_gate(painter, option, index)
            return
        if col == 6:
            self._paint_score(painter, option, index)
            return
        if col == 5:
            self._paint_spark(painter, option, index)
            return
        super().paint(painter, option, index)

    @staticmethod
    def _paint_gate(painter: QPainter, option, index) -> None:
        painter.save()
        gate = index.data(Qt.DisplayRole) or "WAIT"
        text_color, bg, border = theme.gate_tone(gate)
        rect = option.rect.adjusted(4, 4, -4, -4)
        painter.setPen(QPen(QColor(border), 1))
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(rect, 2, 2)
        painter.setPen(QColor(text_color))
        painter.drawText(rect, Qt.AlignCenter, str(gate).upper())
        painter.restore()

    @staticmethod
    def _paint_score(painter: QPainter, option, index) -> None:
        painter.save()
        try:
            score = max(0.0, min(100.0, float(index.data(Qt.DisplayRole) or 0)))
        except (TypeError, ValueError):
            score = 0.0
        bar_h = 4
        bar_rect = QRectF(option.rect.left() + 4, option.rect.center().y() + 6, option.rect.width() - 30, bar_h)
        painter.fillRect(bar_rect, QColor(theme.LINE))
        filled = QRectF(bar_rect.left(), bar_rect.top(), bar_rect.width() * score / 100.0, bar_h)
        painter.fillRect(filled, QColor(theme.CYAN))
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(option.rect.adjusted(0, -6, -4, 0), Qt.AlignRight | Qt.AlignVCenter, f"{int(score)}")
        painter.restore()

    @staticmethod
    def _paint_spark(painter: QPainter, option, index) -> None:
        painter.save()
        spark = index.data(Qt.UserRole) or []
        if spark and len(spark) >= 2:
            lo, hi = min(spark), max(spark)
            span = (hi - lo) or 1.0
            rect = option.rect.adjusted(4, 6, -4, -6)
            n = len(spark)
            path = QPainterPath()
            for i, v in enumerate(spark):
                x = rect.left() + rect.width() * i / (n - 1)
                y = rect.bottom() - (v - lo) / span * rect.height()
                path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
            up = spark[-1] >= spark[0]
            painter.setPen(QPen(QColor(theme.LONG if up else theme.SHORT), 1.4))
            painter.drawPath(path)
        painter.restore()


class ScanPage(QWidget):
    """Strona SCAN (UI_DESK_V2): tabela QTableView/QAbstractTableModel +
    filtry (search, LIQUID/MAJORS/ALL, LONG/SHORT/BOTH) + drawer po prawej
    z podsumowaniem klikniętego wiersza i przyciskiem do LAB."""

    view_full_analysis = Signal(str)

    def __init__(self, window: "MainWindow", parent=None):
        super().__init__(parent)
        self.setObjectName("ScanV2Root")
        self.window_ = window
        self._universe_filter = "LIQUID"
        self._selected_symbol: str | None = None
        self._build()

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        left = QVBoxLayout()
        left.setSpacing(8)
        top_row = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search symbol..")
        self.search_box.textChanged.connect(self._on_search_changed)
        top_row.addWidget(self.search_box, 1)
        self.universe_buttons: dict[str, QPushButton] = {}
        for name in ("LIQUID", "MAJORS", "ALL"):
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setChecked(name == "LIQUID")
            btn.clicked.connect(lambda _checked, n=name: self._on_universe_clicked(n))
            self.universe_buttons[name] = btn
            top_row.addWidget(btn)
        self.side_buttons: dict[str, QPushButton] = {}
        for name in ("LONG", "SHORT", "BOTH"):
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setChecked(name == "BOTH")
            btn.clicked.connect(lambda _checked, n=name: self._on_side_clicked(n))
            self.side_buttons[name] = btn
            top_row.addWidget(btn)
        left.addLayout(top_row)

        self.model = ScanTableModel()
        self.proxy = ScanFilterProxy()
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setObjectName("V2Table")
        self.table.setModel(self.proxy)
        self.table.setItemDelegate(ScanItemDelegate())
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.clicked.connect(self._on_row_clicked)
        left.addWidget(self.table, 1)

        self.footer = QLabel("FEED: — | CYCLE: —")
        self.footer.setObjectName("Muted")
        left.addWidget(self.footer)

        left_widget = QWidget()
        left_widget.setLayout(left)
        root.addWidget(left_widget, 70)

        self.drawer = Card("SELECTED")
        self.drawer_symbol = QLabel("—")
        self.drawer_symbol.setObjectName("KPI")
        self.drawer.body.addWidget(self.drawer_symbol)
        self.drawer_why = QLabel("Kliknij wiersz, żeby zobaczyć szczegóły.")
        self.drawer_why.setWordWrap(True)
        self.drawer_why.setObjectName("Muted")
        self.drawer.body.addWidget(self.drawer_why)
        self.drawer_netr = self._drawer_stat("Expected Net R")
        self.drawer_size = self._drawer_stat("Size")
        self.drawer_sl = self._drawer_stat("Stop Loss")
        self.drawer_tp = self._drawer_stat("Take Profit")
        self.view_full_btn = QPushButton("VIEW FULL ANALYSIS")
        self.view_full_btn.clicked.connect(self._on_view_full_clicked)
        self.drawer.body.addWidget(self.view_full_btn)
        self.drawer.body.addStretch()
        root.addWidget(self.drawer, 30)

    def _drawer_stat(self, title: str) -> QLabel:
        row = QHBoxLayout()
        label = QLabel(title)
        label.setObjectName("Muted")
        value = QLabel("—")
        value.setObjectName("V2Mono")
        row.addWidget(label)
        row.addStretch()
        row.addWidget(value)
        self.drawer.body.addLayout(row)
        return value

    # ------------------------------------------------------------------
    def apply_state(self, data: "DataAdapter"):
        rows = data.scan_rows(self._universe_filter)
        self.model.set_rows(rows)
        feed = data.feed_status()
        self.footer.setText(f"FEED: {feed} | uniwersum: {len(rows)}")

    def _on_search_changed(self, text: str):
        self.proxy.set_search(text)

    def _on_universe_clicked(self, name: str):
        self._universe_filter = name
        for n, btn in self.universe_buttons.items():
            btn.setChecked(n == name)
        self.apply_state(self.window_.data)

    def _on_side_clicked(self, name: str):
        for n, btn in self.side_buttons.items():
            btn.setChecked(n == name)
        self.proxy.set_side_filter(name)

    def _on_row_clicked(self, proxy_index):
        source_index = self.proxy.mapToSource(proxy_index)
        row = self.model.row_dict(source_index.row())
        if not row:
            return
        self._selected_symbol = row.get("sym")
        self.drawer_symbol.setText(f"SELECTED: {self._selected_symbol}")
        self.drawer_why.setText(f"{row.get('side','—')} · GATE {row.get('gate','—')} · {row.get('why','—')}")
        self.drawer_netr.setText(number(row.get("expected_net_r"), 2))
        self.drawer_size.setText(number(row.get("size"), 4, "$"))
        self.drawer_sl.setText(number(row.get("sl_price"), 4))
        self.drawer_tp.setText(number(row.get("tp1_price"), 4))

    def _on_view_full_clicked(self):
        if self._selected_symbol:
            self.view_full_analysis.emit(self._selected_symbol)


class MainWindow(QMainWindow):
    NAV = [
        ("⌂", "Dzień"), ("⌕", "Markets"),
        ("☷", "Trading"), ("◉", "Lab"),
        ("▶", "Replay"), ("☰", "Logi"), ("⚙", "Ustawienia"),
    ]

    def __init__(self, runtime):
        super().__init__()
        self.rt = runtime
        self.data = DataAdapter(runtime)
        self.selected_symbol: str | None = None
        self._settings_fields: dict[str, QWidget] = {}
        self._secret_fields: dict[str, QLineEdit] = {}
        self.chart_pool = QThreadPool.globalInstance()
        self._chart_request_key = None
        self._chart_tasks = []
        self._replay_task = None
        self._last_state_mtime: float | None = None
        self._observed_cycle_interval: float = 30.0  # startowy placeholder, kalibruje się realnym rytmem zapisu
        try:
            from version import display as _ver
            self.setWindowTitle(f"CryptoEdge {_ver()} · Native Trading Console")
        except Exception:
            self.setWindowTitle("CryptoEdge · Native Trading Console")
        self.resize(1600, 940)
        self.setMinimumSize(1220, 760)
        self.setStyleSheet(self.styles())
        self.build()
        self.refresh()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)

    @staticmethod
    def styles() -> str:
        return f"""
        * {{ font-family:'Segoe UI'; font-size:12px; }}
        QMainWindow, QWidget {{ background:{C['bg']}; color:{C['text']}; }}
        QFrame#Sidebar {{ background:{C['side']}; border-right:1px solid {C['line']}; }}
        QFrame#Top, QFrame#Ops {{ background:#07111c; border-bottom:1px solid {C['line']}; }}
        QFrame#Card {{ background:{C['panel']}; border:1px solid {C['line']}; border-radius:10px; }}
        QLabel#Brand {{ font-size:19px; font-weight:700; }}
        QLabel#Title {{ font-size:20px; font-weight:700; }}
        QLabel#AnalysisSection {{ color:{C['cyan']}; font-size:11px; font-weight:800; padding:12px 2px 4px 2px; }}
        QLabel#AnalysisValue {{ color:{C['text']}; line-height:1.35; padding:2px; }}
        QLabel#Subtitle, QLabel#Muted {{ color:{C['muted']}; }}
        QLabel#CardTitle {{ color:#a7b8ca; font-size:10px; font-weight:700; letter-spacing:1px; }}
        QLabel#KPI {{ font-size:22px; font-weight:700; }}
        QLabel#KPI[tone='green'] {{ color:{C['green']}; }} QLabel#KPI[tone='red'] {{ color:{C['red']}; }}
        QLabel#Pill {{ border:1px solid {C['line2']}; border-radius:7px; padding:5px 9px; font-weight:700; }}
        QLabel#Pill[tone='green'] {{ color:{C['green']}; background:#0d3026; border-color:#195440; }}
        QLabel#Pill[tone='red'] {{ color:{C['red']}; background:#35151d; border-color:#6b2634; }}
        QLabel#Pill[tone='amber'] {{ color:{C['amber']}; background:#33260f; border-color:#6b4d17; }}
        QLabel#Pill[tone='blue'] {{ color:{C['cyan']}; background:#0c2b3d; border-color:#145678; }}
        QLabel#StatusBanner {{ border:1px solid {C['line2']}; border-radius:9px; padding:12px 16px; font-size:15px; font-weight:700; }}
        QLabel#StatusBanner[tone='green'] {{ color:{C['green']}; background:#0d3026; border-color:#195440; }}
        QLabel#StatusBanner[tone='red'] {{ color:{C['red']}; background:#35151d; border-color:#6b2634; }}
        QLabel#StatusBanner[tone='amber'] {{ color:{C['amber']}; background:#33260f; border-color:#6b4d17; }}
        QLabel#StatusBanner[tone='muted'] {{ color:{C['muted']}; background:{C['panel2']}; border-color:{C['line']}; }}
        QLabel#LiveTicker {{ font-size:18px; font-weight:700; padding:4px 18px 4px 0; }}
        QPushButton {{ color:{C['text']}; background:{C['panel2']}; border:1px solid {C['line']}; border-radius:7px; padding:7px 11px; font-weight:600; }}
        QPushButton:hover {{ border-color:{C['cyan']}; }}
        QPushButton#Good {{ color:{C['green']}; background:#0d3026; border-color:#1b654c; }}
        QPushButton#Danger {{ color:#ff8795; background:#35151d; border-color:#742b3a; }}
        QPushButton#Primary {{ color:#76d9ff; background:#0c2b3d; border-color:#175b7c; }}
        QPushButton#ModeDemo, QPushButton#ModeLive {{ min-width:125px; padding:10px 16px; font-size:13px; }}
        QPushButton#ModeDemo:checked {{ color:{C['green']}; background:#0d3026; border:2px solid #25d791; }}
        QPushButton#ModeLive:checked {{ color:#ffffff; background:#4a1721; border:2px solid {C['red']}; }}
        QPushButton#Nav {{ text-align:left; color:{C['muted']}; background:transparent; border:0; padding:10px 13px; }}
        QPushButton#Nav:hover, QPushButton#Nav:checked {{ color:{C['text']}; background:#0e2230; border-left:2px solid {C['green']}; }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background:{C['panel2']}; border:1px solid {C['line']}; border-radius:7px; padding:7px; color:{C['text']}; }}
        QCheckBox {{ spacing:8px; }} QCheckBox::indicator {{ width:17px; height:17px; }}
        QTableWidget {{ background:{C['panel']}; alternate-background-color:#0d1824; border:0; gridline-color:#142536; selection-background-color:#143448; }}
        QHeaderView::section {{ background:#08121d; color:#91a5ba; border:0; border-bottom:1px solid {C['line']}; padding:8px; font-size:10px; font-weight:700; }}
        QScrollBar:vertical {{ background:#07101a; width:10px; }} QScrollBar::handle:vertical {{ background:#274058; border-radius:5px; min-height:30px; }}
        """

    def build(self):
        if bool(getattr(config, "UI_DESK_V2", False)):
            self.build_v2()
            return
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self.build_top())
        outer.addWidget(self.build_ops())
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.build_sidebar())
        self.stack = QStackedWidget()
        row.addWidget(self.stack, 1)
        outer.addLayout(row, 1)
        builders = [self.overview, self.markets_workspace,
                    self.trading_workspace, self.safety_workspace,
                    self.historical_replay_page, self.events_page, self.settings_page]
        for builder in builders:
            self.stack.addWidget(builder())

    # ------------------------------------------------------------------
    # UI_DESK_V2 - shell + DESK/SCAN/LAB/SET. Kompletnie osobna sciezka od
    # build() powyzej - stary UI (build_top/build_ops/build_sidebar/stack z
    # 7 stronami) pozostaje NIETKNIETY i osiagalny przez UI_DESK_V2=False.
    # ------------------------------------------------------------------
    def build_v2(self):
        self.setStyleSheet(self.styles() + theme.qss())
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self.build_top_v2())

        self.stack_v2 = QStackedWidget()
        self.desk_page = DeskPage(self)
        self.scan_page = ScanPage(self)
        # LAB = przeniesienie istniejacego Analysis Workspace (spec: "LAB =
        # przeniesienie istniejacego Analysis Workspace (chart + why)") -
        # reuzywa 1:1 cala logike (select_analysis_symbol/refresh_analysis/
        # market_chart/analysis_labels), zamiast budowac drugi raz od zera.
        self.lab_page = self.analysis_page()
        # REPLAY = Historical Daytrading Replay (research/backtest tool, nie
        # zmienia decyzji LIVE/PAPER - patrz README) - w starym shellu byl
        # osobna zakladka; bez tego wpisu byl calkowicie nieosiagalny z UI_DESK_V2.
        self.replay_page_v2 = self.historical_replay_page()
        # HISTORY = nowa zakladka (user: "warto tez dodac historie zamknietych
        # pozycji") - nie byla czescia oryginalnej referencji DESK/SCAN/LAB/
        # REPLAY/SET, ale spec wprost dopuszcza wiecej zakladek.
        self.history_page_v2 = self.history_page()
        self.set_page = self.settings_page()  # reuzywa istniejacej strony ustawien 1:1
        for page in (self.desk_page, self.scan_page, self.lab_page, self.replay_page_v2,
                     self.history_page_v2, self.set_page):
            self.stack_v2.addWidget(page)
        outer.addWidget(self.stack_v2, 1)
        self.desk_page.symbol_selected.connect(self._on_v2_symbol_selected)
        self.scan_page.view_full_analysis.connect(self._on_v2_view_full_analysis)
        self.nav_buttons_v2["DESK"].setChecked(True)

    def build_top_v2(self) -> QWidget:
        """Kompaktowy pasek: logo/tryb, ANALIZA/HANDEL jako StatePill (nie
        osobne START/STOP), BTC/ETH, uptime, pigulka rezimu, menu '...', pod
        tym 5 przyciskow nawigacji DESK/SCAN/LAB/REPLAY/SET."""
        top = QFrame(objectName="V2TopBar")
        outer = QVBoxLayout(top)
        outer.setContentsMargins(14, 8, 14, 0)
        outer.setSpacing(0)

        bar = QHBoxLayout()
        brand = QLabel("CRYPTOEDGE")
        brand.setObjectName("Brand")
        bar.addWidget(brand)
        self.mode_pill_v2 = StatePill(objectName="V2StatePill")
        bar.addWidget(self.mode_pill_v2)
        bar.addSpacing(12)
        self.analiza_pill_v2 = StatePill(objectName="V2StatePill")
        bar.addWidget(self.analiza_pill_v2)
        self.handel_pill_v2 = StatePill(objectName="V2StatePill")
        bar.addWidget(self.handel_pill_v2)
        bar.addSpacing(12)
        self.btc_ticker_v2 = QLabel("BTC —")
        self.btc_ticker_v2.setObjectName("V2Mono")
        self.eth_ticker_v2 = QLabel("ETH —")
        self.eth_ticker_v2.setObjectName("V2Mono")
        bar.addWidget(self.btc_ticker_v2)
        bar.addWidget(self.eth_ticker_v2)
        bar.addStretch()
        self.uptime_v2 = QLabel("UPTIME —")
        self.uptime_v2.setObjectName("Muted")
        bar.addWidget(self.uptime_v2)
        self.regime_pill_v2 = QLabel("—")
        self.regime_pill_v2.setObjectName("V2RegimePill")
        bar.addWidget(self.regime_pill_v2)
        menu_btn = QPushButton("…")
        menu_btn.setFixedWidth(34)
        menu_btn.setToolTip("Start analysis / start trading, pause, stop, close all")
        menu_btn.clicked.connect(self._show_v2_menu)
        self._v2_menu_button = menu_btn
        bar.addWidget(menu_btn)
        outer.addLayout(bar)

        nav_row = QHBoxLayout()
        nav_row.setContentsMargins(0, 6, 0, 0)
        nav_row.setSpacing(0)
        self.nav_buttons_v2: dict[str, QToolButton] = {}
        for name in ("DESK", "SCAN", "LAB", "REPLAY", "HISTORY", "SET"):
            btn = QToolButton()
            btn.setObjectName("V2Nav")
            btn.setText(name)
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.clicked.connect(lambda _checked, n=name: self._go_v2(n))
            self.nav_buttons_v2[name] = btn
            nav_row.addWidget(btn)
        nav_row.addStretch()
        outer.addLayout(nav_row)
        return top

    def _go_v2(self, name: str):
        pages = {
            "DESK": self.desk_page, "SCAN": self.scan_page, "LAB": self.lab_page,
            "REPLAY": self.replay_page_v2, "HISTORY": self.history_page_v2, "SET": self.set_page,
        }
        page = pages.get(name)
        if page is not None:
            self.stack_v2.setCurrentWidget(page)

    def _on_v2_symbol_selected(self, symbol: str):
        self.selected_symbol = symbol

    def _on_v2_view_full_analysis(self, symbol: str):
        # SCAN -> "VIEW FULL ANALYSIS" -> stack na LAB i select_symbol()
        # (spec: "przycisk 'pelna analiza' -> stack na LAB i select_symbol()").
        # LAB = analysis_page() (reuzywany 1:1) - metoda napedzajaca to juz
        # istniejaca select_analysis_symbol() na MainWindow, nie osobna
        # metoda na widgecie strony.
        self._go_v2("LAB")
        self.select_analysis_symbol(symbol)

    def _show_v2_menu(self):
        # QMenu: Start analysis, Start trading, Pause, Resume, Stop trading,
        # Stop bot, Close all. Start* reuzywaja dokladnie te same metody co
        # stary shell (self.start_analysis/self.start_trading) - to jedyny
        # sposob na uruchomienie bota z layoutu DESK/SCAN/LAB, bo pigulki
        # ANALIZA/HANDEL w gornym pasku to tylko wskazniki stanu, nie przyciski.
        menu = QMenu(self)
        menu.addAction("Start analysis", self.start_analysis)
        menu.addAction("Start trading", self.start_trading)
        menu.addSeparator()
        menu.addAction("Pause", self.pause)
        menu.addAction("Resume", self.resume)
        menu.addAction("Stop trading", self.stop_trading)
        menu.addAction("Stop bot", self.stop_engine)
        menu.addSeparator()
        menu.addAction("Close all", self.close_all)
        menu.exec(self._v2_menu_button.mapToGlobal(self._v2_menu_button.rect().bottomLeft()))

    def build_top(self) -> QWidget:
        top = QFrame(objectName="Top")
        top.setFixedHeight(64)
        row = QHBoxLayout(top)
        row.setContentsMargins(16, 9, 16, 9)
        brand = QLabel("◈  CRYPTOEDGE", objectName="Brand")
        row.addWidget(brand)
        try:
            from version import display as _ver
            self.version_pill = StatePill(objectName="Pill")
            self.version_pill.set_state(_ver(), "blue")
            row.addWidget(self.version_pill)
        except Exception:
            self.version_pill = None
        self.mode_pill = StatePill(objectName="Pill")
        row.addWidget(self.mode_pill)
        self.engine_pill = StatePill(objectName="Pill")
        self.trade_pill = StatePill(objectName="Pill")
        row.addWidget(self.engine_pill)
        row.addWidget(self.trade_pill)
        row.addStretch()
        self.clock = QLabel()
        self.uptime = QLabel(objectName="Muted")
        self.cycle_timer = QLabel(objectName="Muted")
        row.addWidget(self.clock)
        row.addWidget(self.uptime)
        row.addWidget(self.cycle_timer)
        controls = [
            ("ANALIZA", self.start_analysis, "Primary"), ("START BOT", self.start_trading, "Good"),
            ("STOP TRADING",self.stop_trading,"Danger"), ("PAUSE", self.pause, ""),
            ("RESUME", self.resume, ""), ("STOP BOT", self.stop_engine, "Danger"),
            ("CLOSE ALL", self.close_all, "Danger"),
        ]
        for text, slot, style in controls:
            button = QPushButton(text, objectName=style)
            button.clicked.connect(slot)
            row.addWidget(button)
            if text == "START BOT":
                self.start_trade_button = button
        return top

    def build_ops(self) -> QWidget:
        bar = QFrame(objectName="Ops")
        bar.setFixedHeight(50)
        row = QHBoxLayout(bar)
        row.setContentsMargins(16, 7, 16, 7)
        self.ops_engine = StatePill(objectName="Pill")
        self.ops_data = StatePill(objectName="Pill")
        self.ops_trade = StatePill(objectName="Pill")
        self.ops_cycle = StatePill(objectName="Pill")
        for widget, stretch in [(self.ops_engine, 1), (self.ops_data, 2), (self.ops_trade, 1), (self.ops_cycle, 1)]:
            row.addWidget(widget, stretch)
        self.ops_strategy = self.ops_engine
        self.ops_market = self.ops_data
        self.ops_regime = self.ops_cycle
        self.ops_sources = self.ops_data
        self.ops_risk = self.ops_trade
        return bar

    def build_sidebar(self) -> QWidget:
        side = QFrame(objectName="Sidebar")
        side.setFixedWidth(205)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(10, 16, 10, 12)
        layout.addWidget(QLabel("WORKSPACE", objectName="CardTitle"))
        self.nav_buttons = []
        for index, (icon, title) in enumerate(self.NAV):
            button = QPushButton(f"{icon}   {title}", objectName="Nav")
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, i=index: self.go(i))
            layout.addWidget(button)
            self.nav_buttons.append(button)
        self.nav_buttons[0].setChecked(True)
        layout.addStretch()
        self.side_status = QLabel(objectName="Muted")
        self.side_status.setWordWrap(True)
        layout.addWidget(self.side_status)
        layout.addWidget(QLabel("F5  refresh  ·  P  pause/resume", objectName="Muted"))
        return side

    def compact_page(self, subtitle: str = ""):
        """Jak page(), ale bez wielkiego naglowka Title - uzywane przez
        REPLAY/SET/HISTORY w UI_DESK_V2 (spec: "nie zostawiaj starego
        wygladu zakladek jak np ustawienia, lab czy replay"). Tozsamosc
        strony pokazuje juz gorny pasek nawigacji (DESK/SCAN/LAB/REPLAY/SET),
        wiec druga, duza etykieta tytulu na samej stronie byla zbednym
        powtorzeniem i najbardziej rzucajaca sie w oczy roznica wzgledem
        referencji. Nadal QScrollArea (te strony maja wiecej tresci niz
        mieści sie na ekranie), tylko bez wysokiego naglowka."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        content.setObjectName("DeskV2Root")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        if subtitle:
            sub = QLabel(subtitle, objectName="Muted")
            sub.setWordWrap(True)
            layout.addWidget(sub)
        scroll.setWidget(content)
        return scroll, layout

    def page(self, title: str, subtitle: str = ""):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        head = QHBoxLayout()
        label = QLabel(title, objectName="Title")
        head.addWidget(label)
        if subtitle:
            head.addWidget(QLabel(subtitle, objectName="Subtitle"))
        head.addStretch()
        layout.addLayout(head)
        scroll.setWidget(content)
        return scroll, layout

    def table(self, headers: list[str], minimum: int = 170) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setMinimumHeight(minimum)
        return table

    def add_row(self, table: QTableWidget, values: list[Any], tones: dict[int, str] | None = None):
        row = table.rowCount()
        table.insertRow(row)
        tones = tones or {}
        for column, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            tone = tones.get(column)
            if tone in C:
                item.setForeground(QColor(C[tone]))
                if tone in {"green", "red", "amber", "cyan"}:
                    item.setFont(QFont("Segoe UI", 9, QFont.DemiBold))
            table.setItem(row, column, item)

    @staticmethod
    def tabbed_workspace(tabs: list[tuple[str, QWidget]]) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        widget = QTabWidget()
        for title, page in tabs:
            widget.addTab(page, title)
        layout.addWidget(widget)
        return container

    def markets_workspace(self):
        return self.tabbed_workspace([
            ("Market Scanner", self.scanner_page()),
            ("Opportunities / Signals", self.opportunities_page()),
            ("Performance", self.performance_page()),
        ])

    def trading_workspace(self):
        return self.tabbed_workspace([
            ("Execution & Decisions", self.execution_page()),
            ("Open Positions", self.positions_page()),
        ])

    def safety_workspace(self):
        self.safety_tabs = QTabWidget()
        analysis_widget = self.analysis_page()
        for title, page in [
            ("Operational Control", self.control_center_page()),
            ("Risk Details", self.risk_page()),
            ("Analysis Workspace", analysis_widget),
        ]:
            self.safety_tabs.addTab(page, title)
        self._safety_analysis_tab_index = self.safety_tabs.indexOf(analysis_widget)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.safety_tabs)
        return container

    def overview(self):
        widget, layout = self.page("Dzień", "Kapitał, pozycje, sygnały i sesja — bez przełączania zakładek")
        self.day_empty = QLabel("Cykl 0 — kliknij ANALIZA, żeby ruszył skan. Handel jest osobnym przyciskiem.", objectName="StatusBanner")
        self.day_empty.setProperty("tone", "muted")
        self.day_empty.setWordWrap(True)
        layout.addWidget(self.day_empty)
        mode_card = Card("ACTIVE ACCOUNT MODE")
        mode_row = QHBoxLayout()
        self.dashboard_demo_button = QPushButton("●  DEMO / PAPER", objectName="ModeDemo")
        self.dashboard_live_button = QPushButton("●  LIVE / BLOFIN", objectName="ModeLive")
        for button in (self.dashboard_demo_button, self.dashboard_live_button):
            button.setCheckable(True)
            button.setAutoExclusive(True)
        self.dashboard_demo_button.clicked.connect(lambda: self.request_dashboard_mode(False))
        self.dashboard_live_button.clicked.connect(lambda: self.request_dashboard_mode(True))
        self.dashboard_mode_status = StatePill(objectName="Pill")
        mode_row.addWidget(self.dashboard_demo_button)
        mode_row.addWidget(self.dashboard_live_button)
        mode_row.addWidget(self.dashboard_mode_status)
        mode_row.addStretch()
        mode_card.body.addLayout(mode_row)
        self.dashboard_mode_note = QLabel(objectName="Muted")
        self.dashboard_mode_note.setWordWrap(True)
        mode_card.body.addWidget(self.dashboard_mode_note)
        layout.addWidget(mode_card)
        ticker_card = Card("BTC / ETH — LIVE (Binance/Bybit/CoinGecko, 1s)")
        ticker_row = QHBoxLayout()
        self.btc_ticker_label = QLabel("BTC —", objectName="LiveTicker")
        self.eth_ticker_label = QLabel("ETH —", objectName="LiveTicker")
        ticker_row.addWidget(self.btc_ticker_label)
        ticker_row.addWidget(self.eth_ticker_label)
        ticker_row.addStretch()
        ticker_card.body.addLayout(ticker_row)
        layout.addWidget(ticker_card)
        metrics = QGridLayout()
        self.kpis = {}
        specs = [("capital", "Capital"), ("available", "Free Margin"), ("margin", "Used Margin"),
                 ("equity", "Equity"), ("daily", "Daily PnL"), ("positions", "Positions"), ("dd", "Max DD")]
        for index, (key, title) in enumerate(specs):
            card = KPI(title)
            self.kpis[key] = card
            metrics.addWidget(card, 0, index)
        layout.addLayout(metrics)
        content = QGridLayout()
        equity = Card("Equity & Drawdown")
        self.equity_chart = EquityChart()
        equity.body.addWidget(self.equity_chart)
        content.addWidget(equity, 0, 0, 2, 2)
        top = Card("Top Opportunities")
        self.top_table = self.table(["SYMBOL", "SIDE", "SCORE", "GŁOSY", "DECISION"], 225)
        self.top_table.cellDoubleClicked.connect(self.open_analysis_from_top)
        top.body.addWidget(self.top_table)
        content.addWidget(top, 0, 2)
        queue = Card("Execution Queue")
        self.queue_mini = self.table(["#", "SYMBOL", "SIDE", "SCORE", "STATUS"], 225)
        queue.body.addWidget(self.queue_mini)
        content.addWidget(queue, 0, 3)
        positions = Card("Open Positions")
        self.positions_mini = self.table(["SIDE", "SYMBOL", "ENTRY", "MARK", "PNL", "SL", "TP"], 190)
        positions.body.addWidget(self.positions_mini)
        content.addWidget(positions, 1, 2)
        events = Card("Event Center")
        self.events_mini = self.table(["TIME", "LEVEL", "EVENT", "SYMBOL"], 190)
        events.body.addWidget(self.events_mini)
        content.addWidget(events, 1, 3)
        layout.addLayout(content, 1)
        session = Card("Sesja · zamknięcia + equity")
        self.session_table = self.table(["CZAS", "SIDE", "SYMBOL", "PNL $", "PNL %", "PATH"], 160)
        session.body.addWidget(self.session_table)
        layout.addWidget(session)
        return widget

    def scanner_page(self):
        widget, layout = self.page("Market Scanner", "Full monitored universe — neutral assets stay visible")
        tools = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search symbol…")
        self.search.textChanged.connect(self.refresh_scanner)
        self.signal_filter = QComboBox()
        self.signal_filter.addItems(["ALL", "LONG", "SHORT", "NEUTRAL"])
        self.signal_filter.currentTextChanged.connect(self.refresh_scanner)
        self.scanner_sort = QComboBox()
        self.scanner_sort.addItems(SCANNER_SORTS)
        self.scanner_sort.currentTextChanged.connect(self.refresh_scanner)
        tools.addWidget(self.search, 2)
        tools.addWidget(self.signal_filter)
        tools.addWidget(self.scanner_sort)
        layout.addLayout(tools)
        self.scanner_table = self.table(["#", "SYMBOL", "PRICE", "1H", "24H", "7D", "TREND", "SIGNAL", "SCORE", "R:R", "DECISION"], 520)
        self.scanner_table.cellDoubleClicked.connect(self.open_analysis_from_scanner)
        layout.addWidget(self.scanner_table, 1)
        return widget

    def opportunities_page(self):
        widget, layout = self.page("Opportunities / Signals", "Ranked setups and explicit NO_TRADE decisions")
        self.opportunities_table = self.table(["#", "SYMBOL", "SIDE", "SCORE", "R:R", "TREND", "MTF", "STATUS", "DECISION PATH"], 410)
        self.opportunities_table.cellDoubleClicked.connect(self.open_analysis_from_opportunity)
        layout.addWidget(self.opportunities_table, 1)
        split = QHBoxLayout()
        thesis = Card("Selection Summary")
        self.opportunity_summary = QLabel("Select a setup to inspect it in Analysis Workspace.", objectName="Muted")
        self.opportunity_summary.setWordWrap(True)
        thesis.body.addWidget(self.opportunity_summary)
        split.addWidget(thesis, 1)
        matrix = Card("Signal Legend")
        legend = QLabel("LONG  qualified bullish setup\nSHORT  qualified bearish setup\nNEUTRAL  monitored, no qualified entry\nNO_TRADE  rejected by decision/risk path")
        legend.setWordWrap(True)
        matrix.body.addWidget(legend)
        split.addWidget(matrix, 1)
        layout.addLayout(split)
        return widget

    def analysis_page(self):
        # 21.08.2026: przebudowa wizualna LAB pod referencje UI_DESK_V2 (spec
        # uzytkownika: "LAB = to jest obecny Analysis Workspace, tylko bez
        # pustych 'Brak danych MTF'" + wzorzec DESK/SCAN - naglowek symbol/
        # side/score/price + pigulka Accepted/Rejected, lewo=wykres, prawo=
        # WHY/WHY NOT/MTF/wskazniki/Net R w skompresowanych kartach). Zero
        # self.page() (duzy tytul + QScrollArea calej strony, stary wyglad) -
        # ALE zachowuje 1:1 te same atrybuty (self.analysis_labels[...],
        # self.market_chart, self.analysis_symbol_select, self.chart_*) i
        # metody refresh_analysis()/select_analysis_symbol()/
        # load_analysis_chart()/update_chart_overlays() BEZ ZMIAN - to czysta
        # przebudowa layoutu/stylu, nie logiki.
        widget = QWidget()
        widget.setObjectName("DeskV2Root")
        root = QVBoxLayout(widget)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        header = QHBoxLayout()
        header.addWidget(QLabel("SYMBOL", objectName="V2CardTitle"))
        self.analysis_symbol_select = QComboBox()
        self.analysis_symbol_select.setMinimumWidth(150)
        self.analysis_symbol_select.currentTextChanged.connect(self.select_analysis_symbol)
        header.addWidget(self.analysis_symbol_select)
        self.analysis_title = QLabel("Select an asset in SCAN.", objectName="V2Mono")
        header.addWidget(self.analysis_title, 1)
        self.analysis_status_banner = QLabel("Wybierz symbol, aby zobaczyć status setupu.", objectName="StatusBanner")
        self.analysis_status_banner.setProperty("tone", "muted")
        self.analysis_status_banner.setWordWrap(False)
        header.addWidget(self.analysis_status_banner)
        root.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(10)

        chart_card = Card("BLOFIN MARKET CHART")
        chart_tools = QHBoxLayout()
        self.chart_interval = QComboBox()
        self.chart_interval.addItems(["5m", "15m", "1h", "4h", "1d"])
        self.chart_interval.setCurrentText("1h")
        self.chart_interval.currentTextChanged.connect(lambda _: self.load_analysis_chart(force=True))
        chart_tools.addWidget(self.chart_interval)
        self.chart_overlay_ema = QCheckBox("EMA")
        self.chart_overlay_ema.setChecked(True)
        self.chart_overlay_plan = QCheckBox("ENTRY / SL / TP")
        self.chart_overlay_plan.setChecked(True)
        self.chart_overlay_levels = QCheckBox("FIB + S/R + PIVOT")
        self.chart_overlay_viper = QCheckBox("VIPER")
        for overlay in (self.chart_overlay_ema, self.chart_overlay_plan, self.chart_overlay_levels, self.chart_overlay_viper):
            overlay.toggled.connect(self.update_chart_overlays)
            chart_tools.addWidget(overlay)
        chart_tools.addStretch()
        reload_chart = QPushButton("REFRESH")
        reload_chart.clicked.connect(lambda: self.load_analysis_chart(force=True))
        chart_tools.addWidget(reload_chart)
        open_tv = QPushButton("OPEN IN TRADINGVIEW")
        open_tv.clicked.connect(self.open_selected_tradingview)
        chart_tools.addWidget(open_tv)
        chart_card.body.addLayout(chart_tools)
        self.market_chart = MarketChart()
        self.update_chart_overlays()
        chart_card.body.addWidget(self.market_chart, 1)
        body.addWidget(chart_card, 62)

        # Prawa kolumna: przewijalna (wiecej sekcji niz DESK), zeby nic sie
        # nie ucinalo na mniejszych oknach - same karty (Card/V2Card) w
        # kolejnosci zblizonej do referencji: WHY -> WHY NOT -> MTF MATRIX ->
        # wskazniki/plynnosc -> plan/R:R -> Expected Net R -> model/audyt.
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.NoFrame)
        right_content = QWidget()
        right = QVBoxLayout(right_content)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(8)
        self.analysis_labels = {}
        sections = [
            ("WHY", "pros", 70),
            ("WHY NOT", "cons", 70),
            ("MTF MATRIX", "mtf", 80),
            ("INDICATORS", "indicators", 90),
            ("LIQUIDITY / ORDER BOOK", "liquidity", 60),
            ("PLAN · ENTRY/SL/TP/R:R", "plan", 70),
            ("Expected Net R", "expectancy", 70),
            ("FIBONACCI CONFLUENCE", "fib", 90),
            ("Engine Router", "router", 90),
            ("Decision Telemetry", "telemetry", 70),
        ]
        for title, key, min_height in sections:
            card = Card(title)
            label = QLabel("—", objectName="AnalysisValue")
            label.setWordWrap(True)
            label.setMinimumHeight(min_height)
            label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            card.body.addWidget(label)
            self.analysis_labels[key] = label
            right.addWidget(card)
        # "decision"/"path" nie maja wlasnej karty (spec: naglowek juz pokazuje
        # symbol/side/score + pigulke Accepted/Rejected) - ale refresh_analysis()
        # nadal robi labels["decision"].setText(...)/labels["path"].setText(...),
        # wiec musza istniec jako realne QLabel, inaczej KeyError. Trzymane
        # jako ukryte (nie dodane do layoutu) - stan i tak jest widoczny w
        # naglowku (analysis_status_banner) i to on jest zrodlem prawdy dla usera.
        self.analysis_labels["decision"] = QLabel(self)
        self.analysis_labels["decision"].hide()
        self.analysis_labels["path"] = QLabel(self)
        self.analysis_labels["path"].hide()
        right.addStretch()
        right_scroll.setWidget(right_content)
        body.addWidget(right_scroll, 38)

        root.addLayout(body, 1)
        return widget

    def execution_page(self):
        widget, layout = self.page("Execution Queue", "What the bot may do next — derived from current analysis")
        self.execution_table = self.table(["#", "SYMBOL", "SIDE", "SCORE", "R:R", "ENTRY", "SL", "TP", "STATUS", "PATH"], 390)
        layout.addWidget(self.execution_table, 1)
        card = Card("Execution Safety")
        self.execution_note = QLabel(objectName="Muted")
        self.execution_note.setWordWrap(True)
        card.body.addWidget(self.execution_note)
        layout.addWidget(card)
        note = QLabel(
            "Detailed lifecycle, fills, reservations and PAPER session export are available once in Control Center.",
            objectName="Muted",
        )
        layout.addWidget(note)
        return widget

    def positions_page(self):
        widget, layout = self.page("Positions", "Account-mode aware: PaperTrader in DEMO, exchange snapshot in LIVE")
        self.positions_table = self.table(["SIDE", "SYMBOL", "ENTRY", "MARK", "SIZE", "MARGIN", "SL", "TP", "PNL", "OPENED"], 480)
        layout.addWidget(self.positions_table, 1)
        close = QPushButton("CLOSE ALL POSITIONS", objectName="Danger")
        close.clicked.connect(self.close_all)
        layout.addWidget(close, 0, Qt.AlignRight)
        return widget

    def risk_page(self):
        widget, layout = self.page("Risk Center", "Limits, exposure and active safeguards")
        row = QGridLayout()
        self.risk_kpis = {}
        for index, key in enumerate(["STATUS", "EXPOSURE", "RISK / TRADE", "DAILY DD", "MAX DD", "MARGIN"]):
            card = KPI(key)
            self.risk_kpis[key] = card
            row.addWidget(card, 0, index)
        layout.addLayout(row)
        details = QHBoxLayout()
        status = Card("Runtime Risk State")
        self.risk_text = QLabel("—")
        self.risk_text.setWordWrap(True)
        status.body.addWidget(self.risk_text)
        details.addWidget(status, 1)
        warnings = Card("Active Alerts")
        self.risk_warnings = QLabel("No active warnings.")
        self.risk_warnings.setWordWrap(True)
        warnings.body.addWidget(self.risk_warnings)
        details.addWidget(warnings, 1)
        rejects = Card("Recent Rejects")
        self.rejects_table = self.table(["SYMBOL", "REASON", "TIME"], 240)
        rejects.body.addWidget(self.rejects_table)
        details.addWidget(rejects, 2)
        layout.addLayout(details, 1)
        note = QLabel(
            "Operational readiness, no-trade reasons and position protection are consolidated in Control Center.",
            objectName="Muted",
        )
        layout.addWidget(note)
        return widget

    def control_center_page(self):
        widget, layout = self.page(
            "Control Center", "Account mode, operational readiness and full decision lifecycle"
        )
        # Account Mode card zyje teraz w settings_page() (self.account_mode_select
        # itd.) - to jedna instancja widgetu, wspoldzielona z UI_DESK_V2 SET
        # (set_page = self.settings_page()). Duplikowanie jej tutaj tworzyloby
        # DRUGI QComboBox pod tym samym atrybutem self.account_mode_select,
        # przez co Control Center i Settings przestalyby sie zgadzac (ostatnio
        # zbudowana strona "wygrywalaby" atrybut, a druga stalaby sie martwym
        # widgetem). apply_account_mode()/request_dashboard_mode() dalej
        # dzialaja bez zmian - operuja na jedynej instancji z Settings.
        grid = QGridLayout()
        readiness = Card("System Readiness / Watchdog")
        self.cc_readiness_overall = StatePill(objectName="Pill")
        readiness.body.addWidget(self.cc_readiness_overall)
        self.cc_readiness_table = self.table(["COMPONENT", "STATUS", "DETAIL"], 230)
        readiness.body.addWidget(self.cc_readiness_table)
        grid.addWidget(readiness, 0, 0)
        no_trade = Card("Why No Trade?")
        self.cc_no_trade_table = self.table(["REASON", "COUNT", "SHARE"], 230)
        no_trade.body.addWidget(self.cc_no_trade_table)
        grid.addWidget(no_trade, 0, 1)
        lifecycle = Card("Signal Lifecycle")
        self.cc_lifecycle_table = self.table(["SYMBOL", "SIDE", "ENGINE", "STAGE", "REASON"], 230)
        lifecycle.body.addWidget(self.cc_lifecycle_table)
        grid.addWidget(lifecycle, 1, 0)
        protection = Card("Position Protection")
        self.cc_protection_table = self.table(
            ["SYMBOL", "SIDE", "STATUS", "LOCAL SL", "EXCHANGE SL", "LAST SYNC"], 230
        )
        protection.body.addWidget(self.cc_protection_table)
        grid.addWidget(protection, 1, 1)
        comparison = Card("Expected vs Actual")
        self.cc_execution_compare_table = self.table(
            ["SYMBOL", "ENGINE", "PLANNED", "FILL", "SLIPPAGE", "REALIZED R", "PNL"], 210
        )
        comparison.body.addWidget(self.cc_execution_compare_table)
        grid.addWidget(comparison, 2, 0)
        reservations = Card("Entry Reservations / Session")
        self.cc_reservations_table = self.table(["SYMBOL", "ENGINE", "TTL"], 140)
        reservations.body.addWidget(self.cc_reservations_table)
        self.cc_reservations_note = QLabel(objectName="Muted")
        self.cc_reservations_note.setWordWrap(True)
        reservations.body.addWidget(self.cc_reservations_note)
        export_button = QPushButton("EXPORT PAPER SESSION", objectName="Primary")
        export_button.clicked.connect(self.export_paper_session)
        reservations.body.addWidget(export_button)
        grid.addWidget(reservations, 2, 1)
        layout.addLayout(grid)
        return widget

    def performance_page(self):
        widget, layout = self.page("Performance", "Equity, drawdown and completed-trade quality")
        row = QHBoxLayout()
        chart = Card("Equity & Drawdown")
        self.performance_chart = EquityChart()
        chart.body.addWidget(self.performance_chart)
        row.addWidget(chart, 2)
        side = Card("Performance by Side")
        self.side_performance = self.table(["SIDE", "TRADES", "WINS", "LOSSES", "WIN RATE", "PNL"], 230)
        side.body.addWidget(self.side_performance)
        self.performance_summary = QLabel(objectName="Muted")
        self.performance_summary.setWordWrap(True)
        side.body.addWidget(self.performance_summary)
        row.addWidget(side, 1)
        layout.addLayout(row)
        closed = Card("Closed Trades / Replay")
        self.closed_table = self.table(["TIME", "SIDE", "SYMBOL", "ENTRY", "EXIT", "PNL", "PNL %", "ENGINE", "PATH"], 260)
        closed.body.addWidget(self.closed_table)
        layout.addWidget(closed, 1)
        return widget

    def historical_replay_page(self):
        widget, layout = self.compact_page(
            "BloFin closed candles · next-open execution · conservative costs · chronological OOS",
        )
        controls = Card("TEST CONFIGURATION")
        row = QHBoxLayout()
        row.addWidget(QLabel("UNIVERSE"))
        self.replay_universe = QComboBox()
        self.replay_universe.addItems(["MANUAL", "LIQUID", "ALL"])
        self.replay_universe.setCurrentText("LIQUID")
        self.replay_universe.setToolTip(
            "MANUAL: dokładnie wpisane symbole · LIQUID: automatyczny ranking BloFin · ALL: wszystkie poprawne perpetuals"
        )
        row.addWidget(self.replay_universe)
        row.addWidget(QLabel("SYMBOLS"))
        self.replay_symbols = QLineEdit("BTC, ETH, SOL")
        self.replay_symbols.setPlaceholderText("BTC, ETH, SOL")
        self.replay_symbols.setToolTip("Lista jest używana wyłącznie w trybie MANUAL")
        row.addWidget(self.replay_symbols, 2)
        row.addWidget(QLabel("LIQUID TOP"))
        self.replay_liquid_limit = QSpinBox()
        self.replay_liquid_limit.setRange(5, 100)
        self.replay_liquid_limit.setValue(30)
        self.replay_liquid_limit.setToolTip("Limit rankingu jest używany wyłącznie w trybie LIQUID")
        row.addWidget(self.replay_liquid_limit)
        row.addWidget(QLabel("DAYS"))
        self.replay_days = QSpinBox()
        self.replay_days.setRange(7, 365)
        self.replay_days.setValue(90)
        row.addWidget(self.replay_days)
        row.addWidget(QLabel("OOS %"))
        self.replay_oos = QSpinBox()
        self.replay_oos.setRange(10, 50)
        self.replay_oos.setValue(30)
        row.addWidget(self.replay_oos)
        self.replay_refresh_cache = QCheckBox("Pobierz dane ponownie")
        row.addWidget(self.replay_refresh_cache)
        self.replay_counterfactual = QCheckBox("Audyt filtrów HTF/ADX")
        self.replay_counterfactual.setChecked(False)
        self.replay_counterfactual.setEnabled(False)
        self.replay_counterfactual.setToolTip(
            "Niedostępne dla silnika V2 (DAYTRADING_V2) - to audyt specyficzny dla starego "
            "silnika V1, ktory nie mial gate'ow HTF/ADX w tej formie."
        )
        row.addWidget(self.replay_counterfactual)
        self.replay_start = QPushButton("START REPLAY", objectName="Primary")
        self.replay_start.clicked.connect(self.start_historical_replay)
        row.addWidget(self.replay_start)
        controls.body.addLayout(row)
        self.replay_universe.currentTextChanged.connect(self._sync_replay_universe_controls)
        self._sync_replay_universe_controls(self.replay_universe.currentText())
        self.replay_status = QLabel(
            "Gotowy. Pierwsze uruchomienie pobierze dane publiczne z BloFin; kolejne mogą użyć cache.",
            objectName="Muted",
        )
        self.replay_status.setWordWrap(True)
        controls.body.addWidget(self.replay_status)
        self._sync_replay_universe_controls(self.replay_universe.currentText())
        layout.addWidget(controls)

        summary = Card("Portfolio Result")
        self.replay_summary = QLabel("Brak raportu", objectName="AnalysisValue")
        self.replay_summary.setWordWrap(True)
        summary.body.addWidget(self.replay_summary)
        self.replay_filter_audit = QLabel("Audyt kontrfaktyczny: brak wyniku", objectName="Muted")
        self.replay_filter_audit.setWordWrap(True)
        summary.body.addWidget(self.replay_filter_audit)
        layout.addWidget(summary)

        details = Card("In-Sample vs Out-of-Sample")
        self.replay_table = self.table(
            ["SYMBOL", "SAMPLE", "TRADES", "WIN RATE", "NET R", "AVG R", "PROFIT FACTOR", "MAX DD R"], 280
        )
        details.body.addWidget(self.replay_table)
        layout.addWidget(details, 1)
        note = QLabel(
            "Silnik DAYTRADING_V2 (ten sam co live/paper). Replay nie optymalizuje progów na OOS. "
            "Wynik OOS jest ważniejszy niż in-sample. Brak historycznego L2 oznacza modelowany "
            "slippage/impact. Portfelowa symulacja dzieli JEDNĄ, wspólną pulę slotów (MAX_POSITIONS) "
            "między wszystkie symbole naraz - sygnały odrzucone wyłącznie z braku wolnego slotu liczą "
            "się osobno (patrz „odrzucone (brak slotu)” w podsumowaniu), nie jako strata.",
            objectName="Muted",
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        return widget

    def _sync_replay_universe_controls(self, mode):
        mode = str(mode or "").upper()
        manual = mode == "MANUAL"
        liquid = mode == "LIQUID"
        self.replay_symbols.setEnabled(manual)
        self.replay_liquid_limit.setEnabled(liquid)
        if manual:
            hint = "MANUAL: replay obejmie dokładnie symbole wpisane w polu SYMBOLS."
        elif liquid:
            hint = "LIQUID: symbole zostaną dobrane automatycznie z rankingu płynności BloFin; pole SYMBOLS jest nieaktywne."
        else:
            hint = "ALL: replay obejmie wszystkie poprawne perpetuals BloFin; pole SYMBOLS jest nieaktywne."
        if hasattr(self, "replay_status"):
            self.replay_status.setText(hint)

    def events_page(self):
        widget, layout = self.page("Logs & Events", "Signals, executions, risk warnings, data and system events")
        tools = QHBoxLayout()
        self.event_filter = QComboBox()
        self.event_filter.addItems(["ALL", "SYSTEM", "MARKET", "DATA", "RISK", "SIGNAL", "EXECUTION", "ERROR"])
        self.event_filter.currentTextChanged.connect(self.refresh_events)
        tools.addWidget(QLabel("LEVEL"))
        tools.addWidget(self.event_filter)
        tools.addStretch()
        layout.addLayout(tools)
        self.events_table = self.table(["TIME", "LEVEL", "EVENT", "SYMBOL", "SIDE", "PNL", "CAPITAL"], 520)
        layout.addWidget(self.events_table, 1)
        return widget

    def health_page(self):
        """Legacy entry point; strategy health now lives in Performance."""
        return self.performance_page()

    def system_page(self):
        """Legacy entry point; source/runtime status now lives in Logs & Events."""
        return self.events_page()

    def settings_page(self):
        widget, layout = self.compact_page("Values map directly to existing settings_store keys")
        current = settings_store.load_settings()

        # Account Mode: przeniesione tutaj z control_center_page() (Control
        # Center / stary shell "Safety" tab), zeby byla dostepna rowniez z
        # UI_DESK_V2 SET (settings_page() jest 1:1 wspoldzielone z set_page).
        # Jedyna instancja self.account_mode_select w calej aplikacji -
        # apply_account_mode() i request_dashboard_mode() (dashboard shortcut,
        # tylko stary shell) operuja na tym samym widgecie bez zmian.
        mode_card = Card("Account Mode")
        mode_row = QHBoxLayout()
        self.account_mode_select = QComboBox()
        self.account_mode_select.addItems(["DEMO (PAPER)", "LIVE (BLOFIN)"])
        self.account_mode_select.setCurrentIndex(0 if bool(getattr(config, "PAPER_TRADING", True)) else 1)
        apply_mode = QPushButton("APPLY ACCOUNT MODE", objectName="Primary")
        apply_mode.clicked.connect(self.apply_account_mode)
        self.account_mode_status = QLabel(objectName="Muted")
        self.account_mode_status.setWordWrap(True)
        mode_row.addWidget(self.account_mode_select)
        mode_row.addWidget(apply_mode)
        mode_row.addWidget(self.account_mode_status, 1)
        mode_card.body.addLayout(mode_row)
        mode_warning = QLabel(
            "Changing account mode is allowed only while the engine is stopped and no positions are open. "
            "LIVE account display does not enable order execution; LIVE_EXECUTION_ENABLED remains a separate safety gate.",
            objectName="Muted",
        )
        mode_warning.setWordWrap(True)
        mode_card.body.addWidget(mode_warning)
        layout.addWidget(mode_card)
        cards = QGridLayout()
        groups = [
            ("Trading Mode", ["PAPER_TRADING", "STARTING_CAPITAL", "MIN_SIGNAL_STRENGTH", "AGGRESSIVE_MODE"]),
            ("Strategy Guards", ["BLOCK_OB_THIN", "BLOCK_PUMP_CHASE_PCT", "BLOCK_RANGE_REGIME", "BLOCK_STRAT_NA_IN_RANGE", "REQUIRE_PRIMARY_STRATEGY"]),
            ("Alerts", ["ALERTS_ENABLED", "ALERT_ON_OPEN", "ALERT_ON_CLOSE", "ALERT_ON_HALT", "ALERT_ON_MARGIN_CALL", "ALERT_ON_FEED_FAIL", "ALERT_SOUND", "ALERT_PUSH"]),
        ]
        for group_index, (title, keys) in enumerate(groups):
            card = Card(title)
            form = QFormLayout()
            for key in keys:
                value = current.get(key, settings_store.DEFAULTS[key])
                if isinstance(value, bool):
                    field = QCheckBox()
                    field.setChecked(value)
                elif isinstance(value, int):
                    field = QSpinBox()
                    field.setRange(0, 1_000_000)
                    field.setValue(value)
                else:
                    field = QDoubleSpinBox()
                    field.setRange(0.0, 1_000_000.0)
                    field.setDecimals(4)
                    field.setValue(float(value))
                self._settings_fields[key] = field
                form.addRow(key.replace("_", " ").title(), field)
            card.body.addLayout(form)
            cards.addWidget(card, 0, group_index)
        layout.addLayout(cards)

        api_card = Card("BloFin API · read-only account preview")
        api_row = QHBoxLayout()
        api_form = QFormLayout()
        saved_secrets = secrets_store.load_secrets()
        secret_labels = {
            "BLOFIN_API_KEY": "API Key",
            "BLOFIN_API_SECRET": "API Secret",
            "BLOFIN_API_PASSPHRASE": "Passphrase",
        }
        for key, label in secret_labels.items():
            field = QLineEdit()
            field.setText(saved_secrets.get(key, ""))
            field.setEchoMode(QLineEdit.Password)
            field.setClearButtonEnabled(True)
            field.setPlaceholderText(f"Enter BloFin {label}")
            self._secret_fields[key] = field
            api_form.addRow(label, field)
        api_row.addLayout(api_form, 1)

        api_status_box = QVBoxLayout()
        self.api_status = QLabel(secrets_store.status_label(), objectName="Muted")
        self.api_status.setWordWrap(True)
        api_status_box.addWidget(self.api_status)
        api_buttons = QHBoxLayout()
        save_api = QPushButton("SAVE API CREDENTIALS", objectName="Primary")
        save_api.clicked.connect(self.save_api_credentials)
        test_api = QPushButton("SAVE & TEST CONNECTION", objectName="Good")
        test_api.clicked.connect(self.test_blofin_connection)
        clear_api = QPushButton("CLEAR CREDENTIALS", objectName="Danger")
        clear_api.clicked.connect(self.clear_api_credentials)
        api_buttons.addWidget(save_api)
        api_buttons.addWidget(test_api)
        api_buttons.addWidget(clear_api)
        api_status_box.addLayout(api_buttons)
        api_row.addLayout(api_status_box, 2)
        api_card.body.addLayout(api_row)

        self.api_positions = self.table(
            ["SYMBOL", "SIDE", "SIZE", "ENTRY", "MARK", "PNL", "LEVERAGE", "LIQUIDATION"], 150
        )
        api_card.body.addWidget(self.api_positions)
        api_note = QLabel(
            "The connection test performs authenticated GET requests only. It does not switch to LIVE, "
            "enable execution or place/cancel orders.", objectName="Muted"
        )
        api_note.setWordWrap(True)
        api_card.body.addWidget(api_note)
        layout.addWidget(api_card)
        note = QLabel("Mode and capital changes are persisted immediately after Save. Restart CryptoEdge before switching a running LIVE/DEMO session. Existing positions are never converted between modes.", objectName="Muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        save = QPushButton("SAVE SETTINGS", objectName="Good")
        save.clicked.connect(self.save_settings)
        layout.addWidget(save, 0, Qt.AlignRight)
        layout.addStretch()
        return widget

    def history_page(self):
        """HISTORY (UI_DESK_V2, nowa zakladka - user: "warto tez dodac
        historie zamknietych pozycji"). Referencja (DESK/SCAN/LAB/REPLAY/SET)
        nie przewidywala osobnej zakladki na historie, ale user wprost
        poprosil o dodanie jej, a spec dopuszcza wiecej zakladek jesli
        architektura tego wymaga ("moze byc wiecej zakladek jesli tego
        wymaga nasza architektura"). Reuzywa 1:1 istniejace atrybuty
        (self.closed_table/self.side_performance/self.performance_summary)
        i refresh_performance() ze starego performance_page() (osiagalnego
        tylko z UI_DESK_V2=False) - tu tylko nowy, plaski layout V2Table
        zamiast starego page()+Card z domyslnym QTableWidget stylem."""
        widget = QWidget()
        widget.setObjectName("DeskV2Root")
        root = QHBoxLayout(widget)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        left = QVBoxLayout()
        left.setSpacing(10)
        summary_card = Card("PERFORMANCE BY SIDE")
        self.side_performance = QTableWidget(0, 6)
        self.side_performance.setObjectName("V2Table")
        self.side_performance.setHorizontalHeaderLabels(["SIDE", "TRADES", "WINS", "LOSSES", "WIN RATE", "PNL"])
        self.side_performance.verticalHeader().setVisible(False)
        self.side_performance.setEditTriggers(QTableWidget.NoEditTriggers)
        self.side_performance.setSelectionBehavior(QTableWidget.SelectRows)
        self.side_performance.horizontalHeader().setStretchLastSection(True)
        summary_card.body.addWidget(self.side_performance)
        left.addWidget(summary_card)
        stats_card = Card("SUMMARY")
        self.performance_summary = QLabel(objectName="AnalysisValue")
        self.performance_summary.setWordWrap(True)
        stats_card.body.addWidget(self.performance_summary)
        left.addWidget(stats_card)
        left.addStretch()
        left_widget = QWidget()
        left_widget.setLayout(left)
        root.addWidget(left_widget, 30)

        right = QVBoxLayout()
        closed_card = Card("CLOSED POSITIONS")
        self.closed_table = QTableWidget(0, 9)
        self.closed_table.setObjectName("V2Table")
        self.closed_table.setHorizontalHeaderLabels(
            ["TIME", "SIDE", "SYMBOL", "ENTRY", "EXIT", "PNL", "PNL %", "ENGINE", "PATH"]
        )
        self.closed_table.verticalHeader().setVisible(False)
        self.closed_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.closed_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.closed_table.horizontalHeader().setStretchLastSection(True)
        closed_card.body.addWidget(self.closed_table)
        right.addWidget(closed_card, 1)
        right_widget = QWidget()
        right_widget.setLayout(right)
        root.addWidget(right_widget, 70)
        return widget

    def go(self, index: int):
        self.stack.setCurrentIndex(index)
        for i, button in enumerate(self.nav_buttons):
            button.setChecked(i == index)

    def refresh(self):
        try:
            if bool(getattr(config, "UI_DESK_V2", False)):
                self._refresh_impl_v2()
            else:
                self._refresh_impl()
            self._last_ui_error = None
        except Exception as exc:
            message = f"UI refresh failed: {type(exc).__name__}: {exc}"
            setattr(self.rt, "last_ui_error", message)
            if hasattr(self, "ops_data"):
                self.ops_data.set_state("DATA  UI ERROR", "red")
            if hasattr(self, "side_status"):
                self.side_status.setText(message)
            if getattr(self, "_last_ui_error", None) != message:
                print(f"[PySide6] {message}")
            self._last_ui_error = message
        self._dispatch_price_ticker()

    def _refresh_impl_v2(self):
        """Odpowiednik _refresh_impl() dla UI_DESK_V2 - osobna sciezka, bo
        stara odwoluje sie do dziesiatek widgetow starego shellu (self.clock,
        self.uptime, itd.), ktorych w V2 w ogole nie ma."""
        now = time.time()
        trading_started = getattr(self.rt, "trading_started_at", None)
        if trading_started:
            elapsed = max(0, int(now - trading_started))
            self.uptime_v2.setText(f"UPTIME {elapsed // 3600:02d}:{elapsed % 3600 // 60:02d}:{elapsed % 60:02d}")
        else:
            self.uptime_v2.setText("UPTIME —")

        engine_enabled = bool(getattr(self.rt, "engine_enabled", False))
        trading_enabled = bool(getattr(self.rt, "trading_enabled", False))
        analysis_loading = bool(getattr(self.rt, "analysis_loading", False))
        trade_paused = bool(getattr(getattr(self.rt, "risk", None), "paused", False))
        current_mode = self.data.mode()
        # 21.08.2026: self.mode_pill_v2/analiza_pill_v2/handel_pill_v2 byly
        # tworzone jako bare StatePill() bez objectName - QSS #V2StatePill w
        # theme.py (obwodka/tlo/kolor per tone) w ogole sie nie stosowal, wiec
        # ANALIZA/HANDEL wygladaly identycznie w kazdym stanie ("brak jasnego
        # sygnalu co sie dzieje"). Teraz objectName jest ustawiony (patrz
        # build_top_v2) i stany sa rozroznione zarowno kolorem JAK I tekstem,
        # nie samym kolorem - loading/paused maja wlasny tekst i ton (patrz
        # theme.qss() dla tone='loading'/'paused'/'live'/'demo').
        self.mode_pill_v2.set_state(current_mode, "live" if current_mode == "LIVE" else "demo")
        if engine_enabled and analysis_loading:
            self.analiza_pill_v2.set_state("ANALIZA: SKANOWANIE…", "loading")
        elif engine_enabled:
            self.analiza_pill_v2.set_state("ANALIZA: ON", "on")
        else:
            self.analiza_pill_v2.set_state("ANALIZA: OFF", "off")
        if trading_enabled:
            self.handel_pill_v2.set_state("HANDEL: ON", "on")
        elif trade_paused:
            self.handel_pill_v2.set_state("HANDEL: PAUZA", "paused")
        else:
            self.handel_pill_v2.set_state("HANDEL: OFF", "off")
        if hasattr(self, "desk_page"):
            self.desk_page.sync_mode_buttons(current_mode == "DEMO")

        regime = self.data.regime()
        self.regime_pill_v2.setText(theme.regime_label(regime))
        self.regime_pill_v2.setToolTip(regime)
        self.regime_pill_v2.setStyleSheet(
            f"color:{theme.regime_color(regime)}; border-color:{theme.regime_color(regime)};"
        )

        # Pelny apply_state tylko przy realnej zmianie stanu (pelny skan,
        # 15-30s) - nie co 1s tyknięcie timera. Patrz spec: "Odswiezanie:
        # tick 1 s tylko ceny i PnL wierszy. Pełny apply_state przy pełnym
        # skanie." Ten sam wzorzec mtime co stara sciezka (_last_state_mtime).
        try:
            mtime = STATE_FILE.stat().st_mtime
            changed = self._last_state_mtime is None or mtime > self._last_state_mtime + 0.5
            self._last_state_mtime = mtime
        except OSError:
            changed = self._last_state_mtime is None

        if changed:
            self.desk_page.apply_state(self.data)
            if hasattr(self, "scan_page"):
                self.scan_page.apply_state(self.data)
            if hasattr(self, "lab_page"):
                self.refresh_analysis()
            if hasattr(self, "history_page_v2"):
                self.refresh_performance()

    def _dispatch_price_ticker(self):
        # BTC/ETH co 1s (dokladnie ta czestotliwosc co self.timer) - pomijamy
        # jesli poprzednie zadanie jeszcze nie wrocilo, zeby nie zapychac puli
        # watkow gdyby siec akurat byla wolna. Pomijamy tez, jesli okno jest
        # w trakcie zamykania (patrz closeEvent) - nowe zadanie i tak nie
        # zdazyloby nic sensownie zaktualizowac.
        if getattr(self, "_shutting_down", False):
            return
        if getattr(self, "_price_ticker_inflight", False):
            return
        feeder = getattr(self.rt, "feeder", None)
        if feeder is None:
            return
        self._price_ticker_inflight = True
        task = PriceTickerTask(feeder)
        task.signals.updated.connect(self._on_price_ticker_updated)
        self.chart_pool.start(task)

    def _on_price_ticker_updated(self, prices: dict):
        self._price_ticker_inflight = False
        btc, eth = prices.get("BTC"), prices.get("ETH")
        if hasattr(self, "btc_ticker_label"):
            self.btc_ticker_label.setText(f"BTC {number(btc, 2) if btc is not None else '—'}")
        if hasattr(self, "eth_ticker_label"):
            self.eth_ticker_label.setText(f"ETH {number(eth, 2) if eth is not None else '—'}")
        if hasattr(self, "btc_ticker_v2"):
            self.btc_ticker_v2.setText(f"BTC {number(btc, 2) if btc is not None else '—'}")
        if hasattr(self, "eth_ticker_v2"):
            self.eth_ticker_v2.setText(f"ETH {number(eth, 2) if eth is not None else '—'}")
        if hasattr(self, "desk_page"):
            self.desk_page.apply_tick(prices)

    def _refresh_impl(self):
        st, account = self.data.state(), self.data.account()
        mode = account["mode"]
        self.start_trade_button.setText("START LIVE" if mode == "LIVE" else "START DEMO")
        self.mode_pill.set_state(mode, "green" if mode == "DEMO" else "red")
        if hasattr(self, "dashboard_demo_button"):
            demo = mode == "DEMO"
            self.dashboard_demo_button.setChecked(demo)
            self.dashboard_live_button.setChecked(not demo)
            self.dashboard_mode_status.set_state(
                "ACTIVE: DEMO · NO REAL ORDERS" if demo else "ACTIVE: LIVE · REAL ACCOUNT",
                "green" if demo else "red",
            )
            live_execution = bool(getattr(config, "LIVE_EXECUTION_ENABLED", False))
            self.dashboard_mode_note.setText(
                "PaperTrader uses a local simulated account."
                if demo else
                f"BloFin account selected · real order execution is "
                f"{'ENABLED' if live_execution else 'DISABLED by safety gate'}."
            )
        engine = bool(getattr(self.rt, "engine_enabled", False))
        trade = bool(getattr(self.rt, "trading_enabled", False))
        analysis_loading = bool(getattr(self.rt, "analysis_loading", False))
        paused = bool(getattr(getattr(self.rt, "risk", None), "paused", False))
        if engine and analysis_loading:
            self.engine_pill.set_state("ANALYSIS IN PROGRESS", "blue")
        else:
            self.engine_pill.set_state("ENGINE RUNNING" if engine else "ENGINE OFF", "green" if engine else "muted")
        self.trade_pill.set_state("TRADE ON" if trade else ("TRADE PAUSED" if paused else "TRADE OFF"), "green" if trade else "amber" if paused else "muted")
        now = time.time()
        self.clock.setText(time.strftime("%H:%M:%S"))
        # Licznik liczy od startu HANDLU (trading_started_at), nie od startu
        # analizy (started_at) - dopoki handel nie wystartowal, nie ma co liczyc.
        trading_started = getattr(self.rt, "trading_started_at", None)
        if trading_started:
            elapsed = max(0, int(now - trading_started))
            self.uptime.setText(f"UPTIME {elapsed // 3600:02d}:{elapsed % 3600 // 60:02d}:{elapsed % 60:02d}")
        else:
            self.uptime.setText("UPTIME —  (handel nie wystartował)")
        interval = self._observed_cycle_interval
        age = None
        try:
            mtime = STATE_FILE.stat().st_mtime
            age = max(0.0, now - mtime)
            # Kalibracja na realnym rytmie zapisu stanu (cykl jest ograniczony
            # obliczeniami/fetchem, nie stala sleep - LOOP_INTERVAL_SECONDS=1
            # nie odzwierciedla realnego czasu cyklu). Poprzednio: interval
            # czytany z REFRESH_SECONDS/SCAN_INTERVAL_SECONDS - stale, ktore
            # nie istnieja w config.py, wiec zawsze cicho spadalo do 30, bez
            # zwiazku z rzeczywistym rytmem, stad licznik czesto pokazywal 0.
            if self._last_state_mtime is not None and mtime > self._last_state_mtime + 0.5:
                observed = mtime - self._last_state_mtime
                if 0.5 <= observed <= 600:
                    self._observed_cycle_interval = observed
                    interval = observed
            self._last_state_mtime = mtime
        except OSError:
            heartbeat = float(getattr(self.rt, "last_heartbeat", 0) or 0)
            if heartbeat > 0:
                age = max(0.0, now - heartbeat)
        remaining = max(0, int(interval - age)) if age is not None else 0
        self.cycle_timer.setText(f"NEXT CYCLE ~{remaining:02d}s")
        regime_data = st.get("market_regime") or {}
        regime = str(regime_data.get("regime") or regime_data.get("name") or "UNKNOWN").upper()
        universe = st.get("universe_size", len(self.data.scanner()))
        cycle_n = int(getattr(self.rt, "cycle", st.get("cycle", 0)) or 0)
        sources = st.get("sources") or ""
        if isinstance(sources, dict):
            sources = " · ".join(f"{key}:{value}" for key, value in list(sources.items())[:4])
        data_ok = "blofin: ok" in str(sources).lower() or "universe:blofinusdt(" in str(sources).lower()
        if engine and analysis_loading:
            self.ops_engine.set_state("SILNIK  skan…", "blue")
        else:
            self.ops_engine.set_state("SILNIK  ON" if engine else "SILNIK  OFF", "green" if engine else "muted")
        if data_ok:
            self.ops_data.set_state(f"DANE  OK · {universe} par", "green")
        elif sources:
            self.ops_data.set_state(f"DANE  {str(sources)[:80]}", "amber")
        else:
            self.ops_data.set_state("DANE  czekam na cykl" if engine else "DANE  OFF", "amber" if engine else "muted")
        risk = getattr(self.rt, "risk", None)
        halted = bool(getattr(risk, "is_halted", False))
        dd = (st.get("metrics") or {}).get("max_drawdown_pct", st.get("max_drawdown_pct", 0))
        if halted:
            self.ops_trade.set_state("HANDEL  HALT", "red")
        elif trade:
            self.ops_trade.set_state("HANDEL  ON", "green")
        elif paused:
            self.ops_trade.set_state("HANDEL  pauza", "amber")
        else:
            self.ops_trade.set_state("HANDEL  OFF", "muted")
        self.ops_cycle.set_state(
            f"CYKL  {cycle_n} · {regime} · {remaining}s",
            "red" if "PANIC" in regime else "blue",
        )
        bot_status = "LOADING ANALYSIS" if engine and analysis_loading else "RUNNING" if engine else "STOPPED"
        strategy_mode = str(st.get("strategy_mode") or getattr(config, "STRATEGY_MODE", "DAYTRADING")).upper()
        runtime_strategy = str(getattr(config, "STRATEGY_MODE", "DAYTRADING") or "DAYTRADING").upper()
        if hasattr(self, "day_empty"):
            if cycle_n <= 0 and not engine:
                self.day_empty.setText("Cykl 0 — kliknij ANALIZA. Handel włączysz osobno (START DEMO/LIVE).")
                self.day_empty.setProperty("tone", "muted")
                self.day_empty.show()
            elif engine and cycle_n <= 1 and analysis_loading:
                self.day_empty.setText("Pierwszy cykl: zimny cache klines, to może potrwać. Watchdog nie znaczy, że BloFin padł.")
                self.day_empty.setProperty("tone", "amber")
                self.day_empty.show()
            else:
                self.day_empty.hide()
        self.side_status.setText(f"Bot status: {bot_status}\nAccount: {mode}\nStrategy: {strategy_mode}\nCycle: {getattr(self.rt, 'cycle', st.get('cycle', 0))}")
        money = lambda value: number(value, 4 if abs(float(value or 0)) < 1000 else 2, "$")
        metric_values = {
            "capital": (money(account.get("capital")), f"{mode} account"),
            "available": (money(account.get("available")), "available for new positions"),
            "margin": (money(account.get("margin")), "used margin"),
            "equity": (money(account.get("equity")), "total equity"),
            "daily": (money(account.get("daily")), percent((account.get("daily") or 0) / max(float(account.get("equity") or 1), 1) * 100)),
            "positions": (f"{account.get('positions', 0)} / {getattr(config, 'MAX_OPEN_POSITIONS', '—')}", "open slots"),
            "dd": (percent(dd, 2, False), "from equity peak"),
        }
        for key, (value, sub) in metric_values.items():
            tone = "green" if key == "daily" and float(account.get("daily") or 0) > 0 else "red" if key == "daily" and float(account.get("daily") or 0) < 0 else ""
            self.kpis[key].update_value(value, sub, tone)
        points = self.data.equity()
        self.equity_chart.set_points(points)
        self.performance_chart.set_points(points)
        self.refresh_scanner()
        self.refresh_opportunities()
        self.refresh_positions()
        self.refresh_analysis()
        self.refresh_execution()
        self.refresh_risk()
        self.refresh_control_center()
        self.refresh_performance()
        self.refresh_events()

    def scanner_rows(self) -> list[dict]:
        rows = list(self.data.scanner())
        query = self.search.text().strip().upper() if hasattr(self, "search") else ""
        selected = self.signal_filter.currentText() if hasattr(self, "signal_filter") else "ALL"
        order = self.scanner_sort.currentText() if hasattr(self, "scanner_sort") else "SCORE"
        key = {"24H": "change_24h", "7D": "change_7d", "PRICE": "price"}.get(order)
        rows.sort(key=(lambda row: float(row.get(key) or -1e99)) if key else score_value, reverse=True)
        return [row for row in rows if (not query or query in str(row.get("symbol") or "").upper()) and (selected == "ALL" or direction(row.get("direction")) == selected)]

    def refresh_scanner(self):
        if not hasattr(self, "scanner_table"):
            return
        self.scanner_table.setRowCount(0)
        for index, row in enumerate(self.scanner_rows(), 1):
            side = direction(row.get("direction") or row.get("signal_status"))
            rr = rr_value(row)
            tones = {4: "green" if float(row.get("change_24h") or 0) >= 0 else "red", 7: "green" if side == "LONG" else "red" if side == "SHORT" else "cyan", 8: "amber"}
            decision = row.get("decision_path") or row.get("decision") or row.get("signal_status") or "WATCH"
            self.add_row(self.scanner_table, [index, str(row.get("symbol") or "—").upper(), number(row.get("price"), 8), percent(row.get("change_1h")), percent(row.get("change_24h")), percent(row.get("change_7d")), row.get("trend") or "—", friendly_status(side), number(score_value(row), 1), number(rr, 2), friendly_status(decision)], tones)

    def refresh_opportunities(self):
        self.opportunities_table.setRowCount(0)
        rows = sorted(self.data.scanner(), key=score_value, reverse=True)
        self.top_table.setRowCount(0)
        for index, row in enumerate(rows[:100], 1):
            side = direction(row.get("direction") or row.get("signal_status"))
            mtf = row.get("mtf_summary") or {}
            mtf_text = " · ".join(f"{tf}:{mtf.get(tf, '—')}" for tf in ("15m", "1h", "4h", "1d")) if isinstance(mtf, dict) else str(mtf)
            decision_text = friendly_status(row.get("decision_path") or row.get("decision") or "WATCH")
            tone = "green" if side == "LONG" else "red" if side == "SHORT" else "cyan"
            self.add_row(self.opportunities_table, [index, str(row.get("symbol") or "—").upper(), friendly_status(side), number(score_value(row), 1), number(rr_value(row), 2), row.get("trend") or "—", mtf_text, friendly_status(row.get("signal_status") or "NEUTRAL"), decision_text], {2: tone, 3: "amber"})
            if index <= 6:
                funnel = row.get("funnel") or {}
                votes = funnel.get("votes")
                min_v = funnel.get("min_votes") or 2
                vote_txt = f"{votes}/{min_v}" if votes is not None else "—"
                self.add_row(self.top_table, [str(row.get("symbol") or "—").upper(), friendly_status(side), number(score_value(row), 1), vote_txt, decision_text], {1: tone, 2: "amber"})

    def refresh_execution(self):
        self.execution_table.setRowCount(0)
        self.queue_mini.setRowCount(0)
        for index, row in enumerate(self.data.queue()[:50], 1):
            side = direction(row.get("direction"))
            status = friendly_status(row.get("signal_status") or row.get("decision") or "WATCH")
            tone = "green" if side == "LONG" else "red"
            self.add_row(self.execution_table, [index, str(row.get("symbol") or "—").upper(), friendly_status(side), number(score_value(row), 1), number(rr_value(row), 2), number(row.get("price"), 8), number(row.get("sl_price"), 8), number(row.get("tp_price") or row.get("tp1_price"), 8), status, friendly_status(row.get("decision_path") or "WATCH")], {2: tone, 3: "amber"})
            if index <= 6:
                self.add_row(self.queue_mini, [index, str(row.get("symbol") or "—").upper(), side, number(score_value(row), 1), status], {2: tone, 3: "amber"})
        mode = self.data.mode()
        self.execution_note.setText(f"{mode}: queue is a projection of current scanner decisions. Orders are executed only by BotRuntime when trading is enabled and risk checks pass. Empty queue means no qualified candidate — the UI does not synthesize one.")

    def refresh_positions(self):
        rows = self.data.positions()
        self.positions_table.setRowCount(0)
        self.positions_mini.setRowCount(0)
        for row in rows:
            side = direction(row.get("side"))
            tone = "green" if side == "LONG" else "red"
            pnl_tone = "green" if float(row.get("pnl") or 0) >= 0 else "red"
            self.add_row(self.positions_table, [side, row.get("symbol"), number(row.get("entry"), 8), number(row.get("mark"), 8), number(row.get("size"), 4), number(row.get("margin"), 2), number(row.get("sl"), 8), number(row.get("tp"), 8), number(row.get("pnl"), 4), row.get("opened") or "—"], {0: tone, 8: pnl_tone})
            self.add_row(self.positions_mini, [side, row.get("symbol"), number(row.get("entry"), 6), number(row.get("mark"), 6), number(row.get("pnl"), 3), number(row.get("sl"), 6), number(row.get("tp"), 6)], {0: tone, 4: pnl_tone})

    def asset_by_symbol(self, symbol: str | None) -> dict | None:
        wanted = str(symbol or "").upper()
        return next((row for row in self.data.scanner() if str(row.get("symbol") or "").upper() == wanted), None)

    def refresh_analysis(self):
        rows = self.data.scanner()
        symbols = [str(row.get("symbol") or "").upper() for row in rows if row.get("symbol")]
        if hasattr(self, "analysis_symbol_select"):
            current_items = [self.analysis_symbol_select.itemText(i) for i in range(self.analysis_symbol_select.count())]
            if current_items != symbols:
                self.analysis_symbol_select.blockSignals(True)
                self.analysis_symbol_select.clear()
                self.analysis_symbol_select.addItems(symbols)
                self.analysis_symbol_select.blockSignals(False)
        if not rows:
            self.analysis_title.setText("Analysis is running · waiting for the first completed market cycle…")
            if hasattr(self, "analysis_status_banner"):
                self.analysis_status_banner.setText("⏳  Trwa pierwszy cykl analizy — dane pojawią się za chwilę.")
                self.analysis_status_banner.setProperty("tone", "muted")
                self.analysis_status_banner.style().unpolish(self.analysis_status_banner)
                self.analysis_status_banner.style().polish(self.analysis_status_banner)
            return
        if not self.selected_symbol or self.selected_symbol not in symbols:
            self.selected_symbol = max(rows, key=score_value).get("symbol") or symbols[0]
        if hasattr(self, "analysis_symbol_select"):
            self.analysis_symbol_select.blockSignals(True)
            self.analysis_symbol_select.setCurrentText(self.selected_symbol)
            self.analysis_symbol_select.blockSignals(False)
        row = self.asset_by_symbol(self.selected_symbol)
        if not row:
            return
        self.load_analysis_chart()
        side, rr = direction(row.get("direction")), rr_value(row)
        self.analysis_title.setText(f"{self.selected_symbol}  ·  {side}  ·  Score {number(score_value(row), 1)}  ·  Price {number(row.get('price'), 8)}")
        status_raw = str(row.get("decision") or row.get("signal_status") or side or "").strip()
        status_text = friendly_status(status_raw)
        reason_text = friendly_reason(row.get("decision_why") or row.get("reject_reason") or row.get("signal_summary"))
        tone = self._analysis_status_tone(status_raw, side)
        icon = {"green": "✓", "red": "✓", "amber": "⏳", "muted": "•"}[tone]
        self.analysis_status_banner.setText(f"{icon}  {self.selected_symbol} · {status_text}  —  {reason_text}")
        self.analysis_status_banner.setProperty("tone", tone)
        self.analysis_status_banner.style().unpolish(self.analysis_status_banner)
        self.analysis_status_banner.style().polish(self.analysis_status_banner)
        mtf = row.get("mtf_summary") or (row.get("score_components") or {}).get("mtf") or {}
        if isinstance(mtf, dict) and mtf:
            # Spec LAB: "jesli brak swiec, pokaz NA, nie cztery myslniki w
            # jednej linii" - kazdy interwal dostaje jawne NA, nie jest po
            # cichu pomijany (poprzednio: brakujace wiersze znikaly z listy,
            # zamiast pokazac NA na swoim miejscu).
            mtf_pairs = [(tf.upper(), mtf.get(tf)) for tf in ("15m", "1h", "4h", "1d")]
            mtf_text = "\n".join(
                f"{tf:>3}   {value if value not in (None, '', '—') else 'NA'}"
                for tf, value in mtf_pairs
            )
        elif isinstance(mtf, str) and mtf.strip():
            mtf_text = mtf
        else:
            mtf_text = "NA (brak danych multi-timeframe dla tego cyklu)"
        liquidity = row.get("liquidity") or {}
        fib = row.get("trend_fib") or {}
        pros = row.get("pros") or row.get("reasons") or row.get("for") or []
        cons = row.get("cons") or row.get("against") or []
        labels = self.analysis_labels
        labels["decision"].setText(f"{status_text}\n{reason_text}")
        labels["path"].setText(friendly_status(row.get("decision_path") or "WATCH"))
        labels["plan"].setText(f"Entry  {number(row.get('price'), 8)}\nSL     {number(row.get('sl_price'), 8)}\nTP     {number(row.get('tp_price') or row.get('tp1_price'), 8)}\nR:R    {number(rr, 2)}")
        labels["mtf"].setText(mtf_text)
        indicator_data = row.get("indicators") or {}
        chop = indicator_data.get("choppiness")
        chop_state = indicator_data.get("choppiness_state") or "—"
        indicator_fields = [
            ("Trend", row.get("trend")), ("RSI", number(row.get("rsi"), 2) if row.get("rsi") is not None else None),
            ("MACD", row.get("macd")), ("ATR", percent(row.get("atr_pct"), 2, False) if row.get("atr_pct") is not None else None),
            ("CHOP", f"{number(chop, 2)} ({chop_state})" if chop is not None else None),
            ("24H", percent(row.get("change_24h")) if row.get("change_24h") is not None else None),
        ]
        indicator_lines = [f"{name:<6} {value}" for name, value in indicator_fields if value not in (None, "—")]
        labels["indicators"].setText("\n".join(indicator_lines) or "Wskaźniki jeszcze się liczą dla tego cyklu.")
        liquidity_lines = []
        if liquidity.get("score") is not None:
            liquidity_lines.append(f"Score  {number(liquidity.get('score'), 1)} · Grade {liquidity.get('grade', '—')}")
        if row.get("ob_bias"):
            liquidity_lines.append(f"OB bias  {row.get('ob_bias')}")
        if row.get("ob_imbalance") is not None:
            liquidity_lines.append(f"Imbalance  {number(row.get('ob_imbalance'), 3)}")
        labels["liquidity"].setText("\n".join(liquidity_lines) or "Brak jeszcze danych orderbooka dla tego cyklu.")
        self._set_reason_list(labels["pros"], pros, positive=True)
        self._set_reason_list(labels["cons"], cons, positive=False)
        labels["fib"].setText(format_fibonacci(fib))
        labels["router"].setText(
            f"Signal engine  {row.get('engine') or '—'}\n"
            f"Preferred     {row.get('preferred_engine') or '—'}\n"
            f"Reason        {friendly_reason(row.get('engine_route_reason'))}\n"
            f"Liquidity     {row.get('liquidity_bucket') or '—'}\n"
            f"Residual 24h  {percent(row.get('residual_momentum_24h'), 2)}\n"
            f"Benchmark     {percent(row.get('benchmark_return_24h'), 2)}"
        )
        expected = row.get("expected_net_r") or row.get("net_expected_r")
        calibration = row.get("expected_r_calibration") or {}
        labels["expectancy"].setText(
            f"Expected Net R  {number(expected, 3)}\n"
            f"Status          {row.get('expected_r_status') or calibration.get('status') or 'UNKNOWN'}\n"
            f"Sample          {calibration.get('n', '—')}\n"
            f"Size multiplier {number(row.get('_size_mult'), 3)}"
        )
        labels["telemetry"].setText(
            f"Decision ID  {row.get('decision_id') or 'pending'}\n"
            f"Source       {row.get('signal_source') or row.get('source') or '—'}\n"
            f"Route ver.   {row.get('engine_route_version') or '—'}\n"
            f"Status       {friendly_status(row.get('signal_status') or row.get('decision') or 'WATCH')}"
        )

    @staticmethod
    def _analysis_status_tone(status_raw: str, side: str) -> str:
        """Kolor bannera: zielony/czerwony = zaakceptowany LONG/SHORT,
        bursztynowy = aktywnie czeka na potwierdzenie, szary = nic się nie dzieje."""
        status = str(status_raw or "").strip().upper()
        actionable = {"READY", "OPEN_OK", "OPEN"}
        waiting = {"WAIT", "WAIT_ENTRY"}
        if status in actionable:
            return "red" if side == "SHORT" else "green"
        if status in waiting:
            return "amber"
        return "muted"

    @staticmethod
    def _set_reason_list(label: QLabel, values: list, positive: bool) -> None:
        """Renderuje liste powodow z kolorem (zielony ✓ / czerwony ×) przez rich text,
        zamiast plaskiego tekstu bez rozroznienia dobre/zle."""
        color = C["green"] if positive else C["red"]
        mark = "✓" if positive else "×"
        if not values:
            label.setTextFormat(Qt.PlainText)
            label.setText("—")
            return
        lines = [
            f"<span style='color:{color};'>{mark}</span>&nbsp;&nbsp;{friendly_reason(value)}"
            for value in values
        ]
        label.setTextFormat(Qt.RichText)
        label.setText("<br>".join(lines))

    def select_analysis_symbol(self, symbol: str):
        symbol = str(symbol or "").upper()
        if not symbol:
            return
        self.selected_symbol = symbol
        self._chart_request_key = None
        self.refresh_analysis()

    def chart_levels(self, row: dict) -> dict:
        levels = {
            "ENTRY": row.get("price"),
            "SL": row.get("sl_price"),
            "TP": row.get("tp_price") or row.get("tp1_price"),
        }
        fib = row.get("trend_fib") or {}
        fib_map = fib.get("map") if isinstance(fib.get("map"), dict) else fib
        for name, value in list((fib_map.get("levels") or {}).items())[:6]:
            levels[f"FIB {name}"] = value
        indicators = row.get("indicators") or {}
        for name, value in (indicators.get("pivot_points") or {}).items():
            if name in ("P", "R1", "R2", "R3", "S1", "S2", "S3"):
                levels[name] = value
        structure = indicators.get("support_resistance") or {}
        for index, item in enumerate((structure.get("supports") or [])[:3], 1):
            levels[f"SUP {index}"] = item.get("price")
        for index, item in enumerate((structure.get("resistances") or [])[:3], 1):
            levels[f"RES {index}"] = item.get("price")
        return {name: value for name, value in levels.items() if value is not None}

    def update_chart_overlays(self):
        if not hasattr(self, "market_chart"):
            return
        self.market_chart.set_overlay_visibility(
            ema=self.chart_overlay_ema.isChecked(),
            trade_plan=self.chart_overlay_plan.isChecked(),
            levels=self.chart_overlay_levels.isChecked(),
            viper=self.chart_overlay_viper.isChecked(),
        )

    def load_analysis_chart(self, force=False):
        symbol = str(self.selected_symbol or "").upper()
        if not symbol or not hasattr(self, "market_chart"):
            return
        interval = self.chart_interval.currentText() if hasattr(self, "chart_interval") else "1h"
        key = (symbol, interval)
        if not force and self._chart_request_key == key:
            return
        self._chart_request_key = key
        self.market_chart.set_loading(f"Loading {symbol} {interval} candles from BloFin…")
        task = ChartLoadTask(getattr(self.rt, "feeder", None), symbol, interval)
        task.signals.loaded.connect(self.on_chart_loaded)
        self._chart_tasks.append(task)
        self.chart_pool.start(task)

    def on_chart_loaded(self, symbol, interval, data, source):
        self._chart_tasks = [task for task in self._chart_tasks if not (task.symbol == symbol and task.interval == interval)]
        if (symbol, interval) != self._chart_request_key:
            return
        row = dict(self.asset_by_symbol(symbol) or {})
        if len(data.get("closes") or []) < 3:
            self.market_chart.set_loading(f"No candle data for {symbol} {interval} · {data.get('error') or source}")
            return
        try:
            from indicators_full import compute_indicators
            calculated = compute_indicators(data, interval)
            if calculated:
                current = dict(row.get("indicators") or {})
                for key in ("pivot_points", "support_resistance", "viper"):
                    current[key] = calculated.get(key)
                row["indicators"] = current
                data = dict(data)
                data["_viper"] = calculated.get("viper") or {}
        except Exception:
            pass
        self.market_chart.set_market_data(data, self.chart_levels(row), source, interval)

    def open_selected_tradingview(self):
        if not self.selected_symbol:
            return
        from tradingview_link import open_chart
        interval = {"5m": "5", "15m": "15", "1h": "60", "4h": "240", "1d": "D"}.get(
            self.chart_interval.currentText(), "60"
        )
        open_chart(self.selected_symbol, interval=interval)

    def refresh_risk(self):
        st, account = self.data.state(), self.data.account()
        risk = getattr(self.rt, "risk", None)
        halted = bool(getattr(risk, "is_halted", False))
        reason = getattr(risk, "halt_reason", None) or "—"
        equity = float(account.get("equity") or 0)
        margin = float(account.get("margin") or 0)
        exposure = margin / max(equity, 1) * 100
        daily = float(account.get("daily") or 0)
        daily_dd = max(0.0, -daily / max(equity, 1) * 100)
        max_dd = float((st.get("metrics") or {}).get("max_drawdown_pct", st.get("max_drawdown_pct", 0)) or 0)
        values = {"STATUS": ("HALTED" if halted else "SAFE", "red" if halted else "green"), "EXPOSURE": (percent(exposure, 2, False), ""), "RISK / TRADE": (percent(getattr(config, "RISK_PER_TRADE_PCT", getattr(config, "RISK_PCT", 0)), 2, False), ""), "DAILY DD": (percent(daily_dd, 2, False), ""), "MAX DD": (percent(max_dd, 2, False), ""), "MARGIN": (number(margin, 2, "$"), "")}
        for key, (value, tone) in values.items():
            self.risk_kpis[key].update_value(value, "", tone)
        self.risk_text.setText(f"Mode: {account['mode']}\nHalted: {halted}\nPaused: {bool(getattr(risk, 'paused', False))}\nReason: {reason}\nCapital: {number(account.get('capital'), 4, '$')}\nOpen positions: {account.get('positions', 0)}")
        warnings = []
        if halted:
            warnings.append(f"CRITICAL · {reason}")
        if exposure >= 70:
            warnings.append(f"WARNING · High exposure {exposure:.1f}%")
        if bool(getattr(risk, "paused", False)):
            warnings.append("INFO · New entries are paused")
        if self.ops_data.property("tone") == "red":
            warnings.append("ERROR · State data are stale or offline")
        self.risk_warnings.setText("\n".join(warnings) or "No active risk warnings.")
        self.rejects_table.setRowCount(0)
        for reject in list(st.get("rejects") or [])[-50:][::-1]:
            if isinstance(reject, dict):
                self.add_row(self.rejects_table, [reject.get("symbol", "—"), friendly_reason(reject.get("reason") or reject.get("reject_reason")), reject.get("time") or reject.get("timestamp") or "—"])

    def refresh_control_center(self):
        if not hasattr(self, "cc_readiness_table"):
            return
        st = self.data.state()
        readiness = st.get("readiness") or {}
        overall = str(readiness.get("overall") or "UNKNOWN").upper()
        self.cc_readiness_overall.set_state(
            f"{overall} · heartbeat {number(readiness.get('heartbeat_age_sec'), 1)}s",
            "green" if overall == "READY" else "red" if overall == "BLOCKED" else "amber",
        )
        self.cc_readiness_table.setRowCount(0)
        for item in readiness.get("items") or []:
            status = str(item.get("status") or "UNKNOWN").upper()
            self.add_row(self.cc_readiness_table, [
                item.get("name") or "—", status, item.get("detail") or "—"
            ], {1: "green" if status == "READY" else "red" if status == "BLOCKED" else "amber"})

        rejection = st.get("rejection_summary") or {}
        total = int(rejection.get("total") or 0)
        self.cc_no_trade_table.setRowCount(0)
        for item in rejection.get("reasons") or []:
            count = int(item.get("count") or 0)
            self.add_row(self.cc_no_trade_table, [
                friendly_reason(item.get("reason") or "UNKNOWN"), count,
                percent(count / total * 100 if total else 0, 1, False),
            ])

        self.cc_lifecycle_table.setRowCount(0)
        for item in st.get("signal_lifecycle") or []:
            side = direction(item.get("direction"))
            stage = str(item.get("stage") or "—")
            self.add_row(self.cc_lifecycle_table, [
                item.get("symbol") or "—", friendly_status(side), item.get("engine") or "—", friendly_status(stage),
                friendly_reason(item.get("reason")),
            ], {1: "green" if side == "LONG" else "red" if side == "SHORT" else "cyan",
                3: "red" if stage == "REJECTED" else "green"})

        self.cc_protection_table.setRowCount(0)
        for item in st.get("protection_view") or []:
            status = str(item.get("status") or "UNKNOWN")
            self.add_row(self.cc_protection_table, [
                item.get("symbol") or "—", item.get("direction") or "—", status,
                number(item.get("local_sl"), 8), number(item.get("exchange_sl"), 8),
                item.get("last_sync") or "—",
            ], {2: "green" if status == "PROTECTED" else "red"})

        self.cc_execution_compare_table.setRowCount(0)
        for item in st.get("execution_comparison") or []:
            pnl = float(item.get("pnl") or 0)
            self.add_row(self.cc_execution_compare_table, [
                item.get("symbol") or "—", item.get("engine") or "—",
                number(item.get("planned_entry"), 8), number(item.get("actual_entry"), 8),
                percent(item.get("slippage_pct"), 3, False), number(item.get("realized_r"), 3),
                number(pnl, 4, "$"),
            ], {6: "green" if pnl >= 0 else "red"})

        self.cc_reservations_table.setRowCount(0)
        book = getattr(getattr(self.rt, "trader", None), "entry_reservations", None)
        reservations = book.snapshot() if book and hasattr(book, "snapshot") else []
        for item in reservations:
            self.add_row(self.cc_reservations_table, [
                item.get("symbol") or "—", item.get("engine") or "—", f"{number(item.get('ttl_sec'), 1)}s"
            ])
        max_positions = int(getattr(config, "MAX_OPEN_POSITIONS", getattr(config, "MAX_POSITIONS", 0)) or 0)
        self.cc_reservations_note.setText(
            f"Open: {len(st.get('display_positions') or [])} · Reserved: {len(reservations)} · Limit: {max_positions}"
        )
        current_live = not bool(getattr(config, "PAPER_TRADING", True))
        self.account_mode_select.blockSignals(True)
        self.account_mode_select.setCurrentIndex(1 if current_live else 0)
        self.account_mode_select.blockSignals(False)
        execution = bool(getattr(config, "LIVE_EXECUTION_ENABLED", False))
        self.account_mode_status.setText(
            f"Active: {'LIVE · BloFin account' if current_live else 'DEMO · local paper account'} · "
            f"Live execution: {'ENABLED' if execution else 'DISABLED'}"
        )

    def refresh_performance(self):
        st, closed = self.data.state(), self.data.closed()
        metrics = st.get("metrics") or {}
        self.closed_table.setRowCount(0)
        if hasattr(self, "session_table"):
            self.session_table.setRowCount(0)
        by_side = {"LONG": [], "SHORT": []}
        for row in closed:
            side = direction(row.get("side") or row.get("direction"))
            if side in by_side:
                by_side[side].append(row)
            self.add_row(self.closed_table, [str(row.get("time") or row.get("exit_time") or "")[-19:], side, row.get("symbol") or "—", number(row.get("entry"), 8), number(row.get("exit"), 8), number(row.get("pnl"), 4), percent(row.get("pnl_pct")), row.get("engine") or "—", row.get("path") or row.get("decision_path") or "—"], {1: "green" if side == "LONG" else "red", 5: "green" if float(row.get("pnl") or 0) >= 0 else "red"})
            if hasattr(self, "session_table"):
                self.add_row(self.session_table, [str(row.get("time") or row.get("exit_time") or "")[-19:], side, row.get("symbol") or "—", number(row.get("pnl"), 4), percent(row.get("pnl_pct")), row.get("path") or row.get("decision_path") or "—"], {1: "green" if side == "LONG" else "red", 3: "green" if float(row.get("pnl") or 0) >= 0 else "red"})
        self.side_performance.setRowCount(0)
        for side in ("LONG", "SHORT", "TOTAL"):
            rows = closed if side == "TOTAL" else by_side[side]
            wins = [row for row in rows if float(row.get("pnl") or 0) > 0]
            losses = [row for row in rows if float(row.get("pnl") or 0) < 0]
            pnl = sum(float(row.get("pnl") or 0) for row in rows)
            wr = len(wins) / len(rows) * 100 if rows else 0
            self.add_row(self.side_performance, [side, len(rows), len(wins), len(losses), percent(wr, 1, False), number(pnl, 4, "$")], {0: "green" if side == "LONG" else "red" if side == "SHORT" else "cyan", 5: "green" if pnl >= 0 else "red"})
        self.performance_summary.setText(f"Profit factor  {number(metrics.get('profit_factor'), 2)}\nExpectancy  {number(metrics.get('expectancy'), 4)}\nNet PnL  {number(metrics.get('net_pnl'), 4, '$')}\nMetrics are shown only when emitted by the runtime.")

    @staticmethod
    def event_level(event: dict) -> str:
        text = " ".join(str(event.get(key) or "") for key in ("level", "event", "reason")).upper()
        if any(word in text for word in ("ERROR", "FAIL", "CRITICAL", "HALT")):
            return "ERROR"
        if any(word in text for word in ("RISK", "MARGIN", "REJECT")):
            return "RISK"
        if any(word in text for word in ("OPEN", "CLOSE", "EXEC", "ORDER")):
            return "EXECUTION"
        if any(word in text for word in ("SIGNAL", "LONG", "SHORT")):
            return "SIGNAL"
        if any(word in text for word in ("FEED", "DATA", "SOURCE", "API")):
            return "DATA"
        if any(word in text for word in ("REGIME", "MARKET")):
            return "MARKET"
        return "SYSTEM"

    def refresh_events(self):
        if not hasattr(self, "events_table"):
            return
        rows = self.data.events()
        selected = self.event_filter.currentText() if hasattr(self, "event_filter") else "ALL"
        self.events_table.setRowCount(0)
        self.events_mini.setRowCount(0)
        for row in rows:
            level = self.event_level(row)
            if selected != "ALL" and selected != level:
                continue
            tone = "red" if level in {"ERROR", "RISK"} else "amber" if level in {"MARKET", "SIGNAL"} else "green" if level == "EXECUTION" else "cyan"
            when = str(row.get("timestamp") or row.get("time") or "")[-19:]
            values = [when, level, row.get("event") or row.get("reason") or "—", row.get("symbol") or "—", row.get("direction") or "—", row.get("pnl") or "—", row.get("capital") or "—"]
            self.add_row(self.events_table, values, {1: tone})
        for row in rows[:6]:
            level = self.event_level(row)
            tone = "red" if level in {"ERROR", "RISK"} else "amber" if level in {"MARKET", "SIGNAL"} else "green" if level == "EXECUTION" else "cyan"
            self.add_row(self.events_mini, [str(row.get("timestamp") or row.get("time") or "")[-8:], level, row.get("event") or row.get("reason") or "—", row.get("symbol") or "—"], {1: tone})

    def start_historical_replay(self):
        if self._replay_task is not None:
            return
        feed = getattr(getattr(self.rt, "feeder", None), "blofin", None)
        if feed is None:
            QMessageBox.critical(self, "Historical Replay", "Brak źródła danych BloFin w runtime.")
            return
        symbols = tuple(dict.fromkeys(
            token.strip().upper().replace("-USDT", "").replace("USDT", "")
            for token in self.replay_symbols.text().replace(";", ",").split(",") if token.strip()
        ))
        universe_mode = self.replay_universe.currentText()
        if universe_mode == "MANUAL" and not symbols:
            QMessageBox.warning(self, "Historical Replay", "Podaj przynajmniej jeden symbol.")
            return
        request = ReplayRequest(
            symbols=symbols if universe_mode == "MANUAL" else (),
            universe_mode=universe_mode,
            liquid_limit=self.replay_liquid_limit.value(),
            days=self.replay_days.value(),
            oos_fraction=self.replay_oos.value() / 100.0,
            force_download=self.replay_refresh_cache.isChecked(),
            counterfactual_audit=self.replay_counterfactual.isChecked(),
        )
        self.replay_table.setRowCount(0)
        self.replay_summary.setText("Test trwa. Wynik pojawi się po zakończeniu części out-of-sample.")
        self.replay_status.setText("Uruchamianie…")
        self.replay_start.setEnabled(False)
        task = ReplayTask(feed, request)
        task.signals.progress.connect(self.replay_status.setText)
        task.signals.completed.connect(self.historical_replay_completed)
        task.signals.failed.connect(self.historical_replay_failed)
        self._replay_task = task
        self.chart_pool.start(task)

    def historical_replay_completed(self, report: dict):
        # 21.08.2026: report jest teraz z run_portfolio_replay_v2()
        # (strategy=DAYTRADING_V2) - inny ksztalt niz stary V1: brak
        # report["symbols"][sym]["in_sample"]["metrics"/"counterfactual_filters"],
        # za to portfolio.in_sample/out_of_sample maja "by_symbol"
        # ({symbol: {trades, net_r}}, bez win_rate/PF/maxDD na poziomie
        # symbolu) i "rejected_for_slots" (ile sygnalow odrzucono WYLACZNIE
        # bo zabraklo wolnego slotu MAX_POSITIONS - realna, portfelowa
        # konkurencja, ktorej V1 w ogole nie symulowal).
        self._replay_task = None
        self.replay_start.setEnabled(True)
        self.replay_table.setRowCount(0)
        portfolio = report.get("portfolio") or {}
        universe = report.get("universe") or {}
        oos = portfolio.get("out_of_sample") or {}
        ins = portfolio.get("in_sample") or {}
        self.replay_summary.setText(
            f"SILNIK DAYTRADING_V2 (portfel, MAX_POSITIONS={report.get('max_positions', '—')})\n"
            f"OUT-OF-SAMPLE  ·  {oos.get('trades', 0)} transakcji  ·  "
            f"win rate {percent(float(oos.get('win_rate') or 0) * 100, 1, False)}  ·  "
            f"net {number(oos.get('net_r'), 2)}R  ·  PF {number(oos.get('profit_factor'), 2)}  ·  "
            f"max DD {number(oos.get('max_drawdown_r'), 2)}R  ·  "
            f"odrzucone (brak slotu) {oos.get('rejected_for_slots', 0)}\n"
            f"IN-SAMPLE  ·  {ins.get('trades', 0)} transakcji  ·  "
            f"net {number(ins.get('net_r'), 2)}R  ·  "
            f"odrzucone (brak slotu) {ins.get('rejected_for_slots', 0)}\n"
            f"Universe {universe.get('mode') or '—'}  ·  przetestowano {universe.get('tested_count', 0)}  ·  "
            f"pominięto {universe.get('skipped_count', 0)}\n"
            f"Raport: {report.get('report_path') or '—'}"
        )
        self.replay_filter_audit.setText(
            "Audyt kontrfaktyczny HTF/ADX: niedostępny dla V2 (to mechanizm silnika V1 - "
            "V2 nie ma odpowiednika, patrz per-symbol tabela niżej)."
        )
        for sample_label, sample_key in (("IN-SAMPLE", "in_sample"), ("OUT-OF-SAMPLE", "out_of_sample")):
            by_symbol = (portfolio.get(sample_key) or {}).get("by_symbol") or {}
            for symbol, row in sorted(by_symbol.items(), key=lambda kv: kv[1].get("net_r", 0)):
                trades = int(row.get("trades") or 0)
                net_r = float(row.get("net_r") or 0)
                avg_r = net_r / trades if trades else 0.0
                self.add_row(self.replay_table, [
                    symbol, sample_label, trades,
                    "—", number(net_r, 2), number(avg_r, 3), "—", "—",
                ], {1: "cyan" if sample_key == "out_of_sample" else "muted", 4: "green" if net_r > 0 else "red"})
        self.replay_status.setText("Replay zakończony. Dane pozostają w lokalnym cache.")

    def historical_replay_failed(self, message: str):
        self._replay_task = None
        self.replay_start.setEnabled(True)
        self.replay_status.setText("Błąd: " + message)
        self.replay_summary.setText(
            "Replay nie został policzony. Sprawdź połączenie z BloFin; częściowy lub stary cache nie jest traktowany jako wynik."
        )

    def open_analysis(self, table: QTableWidget, row: int, symbol_column: int):
        item = table.item(row, symbol_column)
        if item:
            self.selected_symbol = item.text().upper()
            self.refresh_analysis()
            self.go(self.NAV.index(("◉", "Lab")))
            self.safety_tabs.setCurrentIndex(self._safety_analysis_tab_index)

    def open_analysis_from_scanner(self, row: int, column: int):
        self.open_analysis(self.scanner_table, row, 1)

    def open_analysis_from_opportunity(self, row: int, column: int):
        self.open_analysis(self.opportunities_table, row, 1)

    def open_analysis_from_top(self, row: int, column: int):
        self.open_analysis(self.top_table, row, 0)

    def save_settings(self):
        values = settings_store.load_settings()
        for key, field in self._settings_fields.items():
            if isinstance(field, QCheckBox):
                values[key] = field.isChecked()
            elif isinstance(field, QComboBox):
                values[key] = field.currentText()
            else:
                values[key] = field.value()
        if not settings_store.save_settings(values):
            QMessageBox.critical(self, "Ustawienia", "Nie udało się zapisać ustawień na dysku.")
            return
        settings_store.apply_settings(values)
        QMessageBox.information(self, "Settings", f"Settings saved. Active strategy: {values.get('STRATEGY_MODE', 'DAYTRADING')}.")
        self.refresh()

    def _api_values(self) -> dict[str, str]:
        return {key: field.text().strip() for key, field in self._secret_fields.items()}

    def save_api_credentials(self, show_message: bool = True) -> bool:
        values = self._api_values()
        present = [key for key, value in values.items() if value]
        if present and len(present) != len(values):
            self.api_status.setText("Incomplete credentials: API Key, Secret and Passphrase are all required.")
            if show_message:
                QMessageBox.warning(self, "BloFin API", self.api_status.text())
            return False
        try:
            secrets_store.save_secrets(values)
        except Exception as exc:
            self.api_status.setText(f"Could not securely save credentials: {exc}")
            if show_message:
                QMessageBox.critical(self, "BloFin API", self.api_status.text())
            return False
        self.api_status.setText(secrets_store.status_label())
        if show_message:
            QMessageBox.information(self, "BloFin API", "Credentials saved securely.")
        return True

    def clear_api_credentials(self):
        if not self.confirm("Clear BloFin credentials", "Remove saved BloFin API credentials from this computer?"):
            return
        for field in self._secret_fields.values():
            field.clear()
        if self.save_api_credentials(show_message=False):
            self.api_positions.setRowCount(0)
            self.api_status.setText("BloFin: credentials removed")

    def test_blofin_connection(self):
        if not self.save_api_credentials(show_message=False):
            return
        values = self._api_values()
        if not all(values.values()):
            self.api_status.setText("Enter API Key, Secret and Passphrase before testing.")
            QMessageBox.warning(self, "BloFin API", self.api_status.text())
            return
        feeder = getattr(getattr(self.rt, "feeder", None), "blofin", None)
        if feeder is None:
            self.api_status.setText("BloFin data connector is not available in the running application.")
            QMessageBox.critical(self, "BloFin API", self.api_status.text())
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            balance = feeder.fetch_futures_balance()
            if not balance:
                error = getattr(feeder, "last_error", None) or "BloFin returned no balance data."
                raise RuntimeError(error)
            positions = feeder.fetch_open_positions() or []
            self.api_positions.setRowCount(0)
            for position in positions:
                leverage = position.get("leverage")
                values = [
                    position.get("symbol") or "—", position.get("direction") or "—",
                    number(position.get("size"), 6), number(position.get("entry"), 6),
                    number(position.get("mark"), 6), number(position.get("pnl"), 2),
                    f"{number(leverage, 2)}x" if leverage is not None else "—",
                    number(position.get("liquidation"), 6),
                ]
                self.add_row(self.api_positions, values, {1: "green" if position.get("direction") == "LONG" else "red"})
            currency = balance.get("currency") or "USDT"
            self.api_status.setText(
                f"Connected · Equity: {number(balance.get('equity'), 2)} {currency} · "
                f"Available: {number(balance.get('available'), 2)} {currency} · "
                f"Open positions: {len(positions)} · READ ONLY"
            )
        except Exception as exc:
            self.api_positions.setRowCount(0)
            self.api_status.setText(f"Connection failed: {exc}")
            QMessageBox.warning(self, "BloFin API", self.api_status.text())
        finally:
            QApplication.restoreOverrideCursor()

    def export_paper_session(self):
        if not bool(getattr(config, "PAPER_TRADING", True)):
            QMessageBox.warning(
                self, "Paper session export",
                "Export is restricted to PAPER mode so a LIVE account snapshot is not copied into a support archive.",
            )
            return
        try:
            state = self.data.state()
            target = control_center.export_paper_session(state)
            QMessageBox.information(
                self, "Paper session exported",
                f"Session archive created:\n{target}\n\nAPI credentials were not included.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Paper session export", f"Export failed: {exc}")

    def apply_account_mode(self):
        requested_live = self.account_mode_select.currentIndex() == 1
        current_live = not bool(getattr(config, "PAPER_TRADING", True))
        if requested_live == current_live:
            self.account_mode_status.setText("Selected mode is already active.")
            return
        if bool(getattr(self.rt, "engine_enabled", False)) or bool(getattr(self.rt, "trading_enabled", False)):
            self.account_mode_select.setCurrentIndex(1 if current_live else 0)
            QMessageBox.warning(self, "Account mode", "Stop analysis and trading before changing DEMO/LIVE mode.")
            return
        sync = getattr(self.rt, "account_sync", None)
        local_positions = list(getattr(getattr(self.rt, "trader", None), "positions", []) or [])
        exchange_positions = []
        exchange_error = None
        if sync:
            try:
                snapshot = sync.sync(force=True) or {}
                exchange_positions = list(snapshot.get("positions") or sync.get_exchange_positions() or [])
                exchange_error = snapshot.get("positions_error") or snapshot.get("error")
            except Exception as exc:
                exchange_error = str(exc)
        elif current_live or requested_live:
            exchange_error = "Brak aktywnego modułu synchronizacji rachunku BloFin."
        # Account view is not position ownership. Manually opened BloFin positions may remain
        # open when leaving the read-only LIVE view. Only positions managed by CryptoEdge block.
        blocking_local = local_positions if requested_live else []
        managed_exchange = []
        external_exchange = list(exchange_positions)
        if current_live and not requested_live and bool(getattr(config, "LIVE_EXECUTION_ENABLED", False)):
            local_keys = {
                (str(getattr(p, "symbol", "")).upper(), str(getattr(p, "direction", "")).upper())
                for p in local_positions
            }
            managed_exchange = [
                p for p in exchange_positions
                if (str(p.get("symbol") or p.get("inst_id") or "").split("-")[0].upper(),
                    str(p.get("direction") or p.get("side") or "").upper()) in local_keys
            ]
            external_exchange = [p for p in exchange_positions if p not in managed_exchange]
        blocking_exchange = managed_exchange
        if exchange_error and requested_live:
            # DEMO -> LIVE: przed wejsciem w tryb live wymagamy potwierdzonego,
            # czystego stanu konta - tu blokada zostaje twarda i bez wyjatku.
            self.account_mode_select.setCurrentIndex(1 if current_live else 0)
            QMessageBox.warning(
                self, "Nie można potwierdzić pozycji BloFin",
                "Nie udało się pobrać świeżego stanu pozycji. Ze względów bezpieczeństwa tryb nie został zmieniony.\n\n"
                f"Błąd: {exchange_error}",
            )
            return
        if exchange_error and not requested_live:
            # 21.08.2026: LIVE -> DEMO z bledem pobierania stanu gieldy (np.
            # zle uprawnienia klucza API, endpoint padl) NIE moze byc twardo
            # zablokowana bez wyjscia - realny przypadek: uzytkownik dodal
            # klucze, saldo sie nie zaladowalo, a nastepna proba powrotu do
            # DEMO wpadala w ta sama blokade w kolko (zaden retry tego nie
            # naprawi, jesli przyczyna jest trwala np. zle permission na
            # kluczu). Przejscie do DEMO nie wysyla zadnych zlecen ani nie
            # zamyka pozycji na gieldzie - jedyne ryzyko to utrata
            # WIDOCZNOSCI istniejacych pozycji na BloFin, dokladnie to samo
            # ryzyko co przy juz znanych external_exchange nizej - wiec
            # traktujemy to tak samo: jawne potwierdzenie zamiast trwalej
            # blokady.
            if not self.confirm(
                "Nie można potwierdzić pozycji BloFin",
                "Nie udało się pobrać świeżego stanu konta/pozycji z BloFin, więc nie wiadomo, czy na "
                "giełdzie zostały otwarte pozycje.\n\n"
                f"Błąd: {exchange_error}\n\n"
                "Przejście na DEMO nie wysyła żadnych zleceń ani nie zamyka pozycji na giełdzie – "
                "jedynie przestaje je tu wyświetlać. Jeśli masz otwarte pozycje na BloFin, zostaną "
                "tam bez zmian. Kontynuować mimo to?",
            ):
                self.account_mode_select.setCurrentIndex(1)
                return
            # Uzytkownik potwierdzil mimo niepewnego stanu - kontynuuj dalej
            # tak, jakby exchange_positions bylo puste (bo faktycznie jest -
            # nieudany fetch nie nadpisuje _last_positions, patrz
            # account_sync.py sync()), zamiast ja tu resetowac na sile.
        if blocking_local or blocking_exchange:
            self.account_mode_select.setCurrentIndex(1 if current_live else 0)
            paper_symbols = ", ".join(str(getattr(p, "symbol", "?")) for p in blocking_local[:8])
            live_symbols = ", ".join(str(p.get("symbol") or p.get("inst_id") or "?") for p in blocking_exchange[:8])
            details = []
            if blocking_local:
                details.append(f"PAPER ({len(blocking_local)}): {paper_symbols}")
            if blocking_exchange:
                details.append(f"BLOFIN LIVE ({len(blocking_exchange)}): {live_symbols}")
            guidance = (
                "Zamknij pozycje na BloFin, zatrzymaj bota i ponów zmianę. Stan giełdy zostanie odświeżony automatycznie."
                if blocking_exchange else
                "Zamknij pozycje PAPER przyciskiem CLOSE ALL, a następnie ponów zmianę."
            )
            self.account_mode_status.setText("Zmiana zablokowana · " + " · ".join(details))
            QMessageBox.warning(
                self, "Otwarte pozycje blokują zmianę trybu",
                "Nie można bezpiecznie zmienić źródła rachunku.\n\n" + "\n".join(details) + "\n\n" + guidance,
            )
            return
        if current_live and not requested_live and external_exchange:
            live_symbols = ", ".join(
                str(p.get("symbol") or p.get("inst_id") or "?") for p in external_exchange[:8]
            )
            if not self.confirm(
                "Powrót do DEMO z otwartymi pozycjami BloFin",
                f"Na BloFin pozostanie {len(external_exchange)} pozycji otwartych poza botem: {live_symbols}.\n\n"
                "CryptoEdge przełączy się na PAPER i przestanie wyświetlać ten rachunek. "
                "Pozycje NIE zostaną zamknięte ani zmodyfikowane. Kontynuować?",
            ):
                self.account_mode_select.setCurrentIndex(1)
                return
        if requested_live and (not sync or not sync.ready_for_live()):
            self.account_mode_select.setCurrentIndex(0)
            QMessageBox.warning(
                self, "Account mode",
                "Configure and test API Key, Secret and Passphrase in Settings before selecting LIVE.",
            )
            return
        target = "LIVE (read-only account view)" if requested_live else "DEMO (paper account)"
        if not self.confirm("Change account mode", f"Switch CryptoEdge to {target}?"):
            self.account_mode_select.setCurrentIndex(1 if current_live else 0)
            return
        values = settings_store.load_settings()
        values["PAPER_TRADING"] = not requested_live
        settings_store.save_settings(values)
        settings_store.apply_settings(values)
        paper_field = self._settings_fields.get("PAPER_TRADING")
        if isinstance(paper_field, QCheckBox):
            paper_field.setChecked(not requested_live)
        if sync:
            sync.sync(force=True)
        self.refresh()
        QMessageBox.information(
            self, "Account mode",
            f"Mode changed to {target}. LIVE order execution remains "
            f"{'enabled' if bool(getattr(config, 'LIVE_EXECUTION_ENABLED', False)) else 'disabled'}.",
        )

    def request_dashboard_mode(self, requested_live: bool):
        """Dashboard shortcut using the same guarded mode-change path as Control Center."""
        if not hasattr(self, "account_mode_select"):
            return
        current_live = not bool(getattr(config, "PAPER_TRADING", True))
        if requested_live == current_live:
            self.refresh()
            return
        self.account_mode_select.setCurrentIndex(1 if requested_live else 0)
        self.apply_account_mode()
        # Przy anulowaniu lub blokadzie natychmiast przywroc wizualnie tryb faktyczny.
        self.refresh()

    def confirm(self, title: str, text: str) -> bool:
        return QMessageBox.question(self, title, text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def start_analysis(self):
        # hasattr-guardy: ta metoda jest wywolywana z obu shelli (stary
        # top-bar i menu "..." w UI_DESK_V2) - pigulki ponizej istnieja
        # tylko w starym shellu, refresh() powyzej juz poprawnie odswieza
        # widoczny stan niezaleznie od aktywnego layoutu.
        settings_store.apply_settings()
        keep_trade = bool(getattr(self.rt, "trading_enabled", False))
        self.rt.start_analysis()
        self.refresh()
        if hasattr(self, "engine_pill"):
            self.engine_pill.set_state("ANALYSIS STARTING", "blue")
        if hasattr(self, "trade_pill"):
            if keep_trade or bool(getattr(self.rt, "trading_enabled", False)):
                self.trade_pill.set_state("TRADE ON", "good")
            else:
                self.trade_pill.set_state("TRADE OFF", "amber")
        if hasattr(self, "ops_data"):
            self.ops_data.set_state("DATA  LOADING", "blue")

    def start_trading(self):
        settings_store.apply_settings()
        live = not bool(getattr(config, "PAPER_TRADING", True))
        live_execution = bool(getattr(config, "LIVE_EXECUTION_ENABLED", False))
        if live and not live_execution:
            QMessageBox.warning(
                self, "LIVE execution disabled",
                "CryptoEdge is showing the LIVE BloFin account, but real order execution is disabled by the "
                "LIVE_EXECUTION_ENABLED safety gate. Switch to DEMO for paper trading.",
            )
            return
        if live:
            warning = (
                "LIVE MODE: start real trading on BloFin? This can open positions using real funds. "
                "Confirm only after checking account, protection and readiness."
            )
            title = "Start LIVE trading"
        else:
            warning = "Start DEMO paper trading? No real exchange orders will be placed."
            title = "Start DEMO trading"
        if self.confirm(title, warning):
            self.rt.start_trading()
            self.refresh()

    def stop_trading(self):
        self.rt.stop_trading()
        self.refresh()

    def pause(self):
        self.rt.pause()
        self.refresh()

    def resume(self):
        self.rt.resume()
        self.refresh()

    def stop_engine(self):
        if self.confirm("Stop engine", "Stop analysis and trading engine?"):
            self.rt.stop_engine()
            self.refresh()

    def close_all(self):
        if self.confirm("Close all", "Close every open position? In LIVE this affects the real account."):
            self.rt.kill_switch("manual_close_all")
            self.refresh()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_F5:
            self.refresh()
        elif event.key() == Qt.Key_P:
            risk = getattr(self.rt, "risk", None)
            self.resume() if risk and getattr(risk, "paused", False) else self.pause()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        # Zatrzymaj timer i przestan odpalac nowe zadania w tle od razu przy
        # zamykaniu - zmniejsza okno wyscigu, w ktorym PriceTickerTask (co 1s)
        # probuje emitowac sygnal do juz usunietego okna. Nie eliminuje tego
        # calkowicie dla zadan juz w locie (patrz try/except w run()), ale
        # ogranicza ich liczbe niemal do zera.
        self._shutting_down = True
        if hasattr(self, "timer"):
            self.timer.stop()
        super().closeEvent(event)


def run_pyside6_ui(runtime):
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("CryptoEdge")
    app.setStyle("Fusion")
    window = MainWindow(runtime)
    window.show()
    return app.exec()
