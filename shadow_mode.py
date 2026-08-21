# ============================================================
# Shadow Mode — Reversal Engine tylko obserwuje
# ============================================================
#
# TREND ENGINE  → normalnie handluje (paper/live)
# REVERSAL ENGINE → loguje kandydatów, śledzi MFE/MAE/TP/SL
#                   BEZ egzekucji, dopóki REVERSAL_SHADOW_ONLY=True
#
# Po kilkuset kandydatach: decyzja czy dać real size.
# ============================================================

from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any

import config

LOGS = Path(__file__).resolve().parent / "logs"
SHADOW_CSV = LOGS / "reversal_shadow.csv"
SHADOW_STATE = LOGS / "reversal_shadow_state.json"


def shadow_only() -> bool:
    return bool(getattr(config, "REVERSAL_SHADOW_ONLY", True))


@dataclass
class ShadowCandidate:
    id: str
    symbol: str
    direction: str
    entry: float
    sl: float
    tp1: Optional[float] = None
    tp2: Optional[float] = None
    expected_r: float = 0.0
    expected_net_r: float = 0.0
    fee_r: float = 0.0
    spread_r: float = 0.0
    slip_r: float = 0.0
    impact_r: float = 0.0
    funding_r: float = 0.0
    gross_result_r: float = 0.0
    net_result_r: float = 0.0
    confidence: float = 0.0  # reversal_score / strength
    opened_ts: float = 0.0
    opened_iso: str = ""
    # tracking
    mfe: float = 0.0  # max favorable excursion (price %)
    mae: float = 0.0  # max adverse excursion (price %)
    mfe_r: float = 0.0
    mae_r: float = 0.0
    hit_tp1: bool = False
    hit_tp2: bool = False
    hit_sl: bool = False
    closed: bool = False
    close_reason: str = ""
    close_ts: float = 0.0
    close_price: float = 0.0
    bars_alive: int = 0
    time_to_tp1_s: Optional[float] = None
    time_to_tp2_s: Optional[float] = None
    time_to_sl_s: Optional[float] = None
    time_to_mfe_s: Optional[float] = None  # kiedy osiągnięto max MFE
    reasons: List[str] = field(default_factory=list)

    @property
    def risk_abs(self) -> float:
        return abs(self.entry - self.sl) if self.entry and self.sl else 0.0


class ShadowTracker:
    """Śledzi otwarte shadow candidates i finalizuje je przy TP/SL/timeout."""

    def __init__(self):
        LOGS.mkdir(parents=True, exist_ok=True)
        self.active: Dict[str, ShadowCandidate] = {}  # key=symbol
        self.closed: List[ShadowCandidate] = []
        self._ensure_csv()
        self._load_state()

    def _ensure_csv(self):
        if not SHADOW_CSV.exists():
            with open(SHADOW_CSV, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow([
                    "ts", "event", "id", "symbol", "direction",
                    "entry", "sl", "tp1", "tp2",
                    "expected_r", "expected_net_r", "confidence",
                    "fee_r", "spread_r", "slip_r", "impact_r", "funding_r",
                    "gross_result_r", "net_result_r",
                    "mfe", "mae", "mfe_r", "mae_r",
                    "hit_tp1", "hit_tp2", "hit_sl",
                    "close_reason", "close_price", "bars_alive",
                    "time_to_tp1_s", "time_to_tp2_s", "time_to_sl_s", "time_to_mfe_s",
                    "decision_path", "reversal_score",
                    "reasons",
                ])

    def _load_state(self):
        if not SHADOW_STATE.exists():
            return
        try:
            data = json.loads(SHADOW_STATE.read_text(encoding="utf-8"))
            for row in data.get("active") or []:
                c = ShadowCandidate(**{k: row[k] for k in ShadowCandidate.__dataclass_fields__ if k in row})
                self.active[c.symbol] = c
        except Exception as e:
            print(f"[Shadow] load state: {e}")

    def _save_state(self):
        try:
            payload = {
                "active": [asdict(c) for c in self.active.values()],
                "n_closed_session": len(self.closed),
            }
            SHADOW_STATE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[Shadow] save: {e}")

    def _log_row(self, event: str, c: ShadowCandidate):
        try:
            with open(SHADOW_CSV, "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow([
                    datetime.now(timezone.utc).isoformat(),
                    event,
                    c.id, c.symbol, c.direction,
                    c.entry, c.sl, c.tp1, c.tp2,
                    c.expected_r, c.expected_net_r, c.confidence,
                    c.fee_r, c.spread_r, c.slip_r, c.impact_r, c.funding_r,
                    c.gross_result_r, c.net_result_r,
                    round(c.mfe, 4), round(c.mae, 4), round(c.mfe_r, 3), round(c.mae_r, 3),
                    c.hit_tp1, c.hit_tp2, c.hit_sl,
                    c.close_reason, c.close_price, c.bars_alive,
                    c.time_to_tp1_s, c.time_to_tp2_s, c.time_to_sl_s, c.time_to_mfe_s,
                    "REVERSAL", c.confidence,
                    "|".join(c.reasons[:12]),
                ])
        except Exception as e:
            print(f"[Shadow] csv: {e}")

    def register_candidate(self, signal: dict) -> Optional[ShadowCandidate]:
        """Loguje REVERSAL_CANDIDATE — bez egzekucji."""
        if not shadow_only() and not bool(getattr(config, "REVERSAL_SHADOW_LOG_ALWAYS", True)):
            # gdy shadow off, nie rejestruj (real execution)
            pass
        sym = (signal.get("symbol") or "").upper()
        if not sym:
            return None
        if sym in self.active:
            current = self.active[sym]
            is_watch = "SHADOW_WATCH" in current.reasons
            is_confirmed = signal.get("setup") == "reversal_confirmed"
            if not (is_watch and is_confirmed):
                return None  # juz sledzony na rownym lub wyzszym poziomie jakosci
            # Pelny setup zastępuje miękki watch, aby statystyka kandydatow
            # nie tracila potwierdzonych reversal przez blokade symbolu.
            self.active.pop(sym, None)
            current.closed = True
            current.close_ts = time.time()
            current.close_reason = "UPGRADED_TO_CONFIRMED"
            current.close_price = float(signal.get("price") or current.entry)
            self._log_row("REVERSAL_WATCH_UPGRADED", current)
        entry = float(signal.get("price") or 0)
        sl = float(signal.get("sl_price") or 0)
        if entry <= 0 or sl <= 0:
            return None
        conf = float(signal.get("reversal_score") or signal.get("strength") or 0)
        cid = f"{sym}_{int(time.time())}"
        breakdown = signal.get("expected_r_breakdown") or {}
        c = ShadowCandidate(
            id=cid,
            symbol=sym,
            direction=str(signal.get("direction") or ""),
            entry=entry,
            sl=sl,
            tp1=signal.get("tp1_price"),
            tp2=signal.get("tp2_price"),
            expected_r=float(signal.get("expected_gross_r") or signal.get("expected_r") or 0),
            expected_net_r=float(signal.get("expected_net_r") or 0),
            fee_r=float(breakdown.get("fee_r") or 0),
            spread_r=float(breakdown.get("spread_r") or 0),
            slip_r=float(breakdown.get("slip_r") or 0),
            impact_r=float(breakdown.get("impact_r") or 0),
            funding_r=float(breakdown.get("funding_r") or 0),
            confidence=conf,
            opened_ts=time.time(),
            opened_iso=datetime.now(timezone.utc).isoformat(),
            reasons=list(signal.get("reasons") or [])[:20],
        )
        self.active[sym] = c
        self._log_row("REVERSAL_CANDIDATE", c)
        print(
            f"[Shadow] REVERSAL_CANDIDATE {c.direction} {c.symbol} "
            f"entry={c.entry:.6f} SL={c.sl:.6f} "
            f"TP1={c.tp1} TP2={c.tp2} "
            f"netR={c.expected_net_r:.2f} conf={c.confidence:.2f}"
        )
        self._save_state()
        return c

    def update_prices(self, price_map: Dict[str, float], max_age_hours: float = None):
        """Aktualizuj MFE/MAE i detekcję TP/SL."""
        max_age = float(
            max_age_hours
            if max_age_hours is not None
            else getattr(config, "REVERSAL_SHADOW_MAX_HOURS", 48.0)
        )
        now = time.time()
        done = []
        for sym, c in list(self.active.items()):
            px = price_map.get(sym)
            if px is None:
                continue
            px = float(px)
            c.bars_alive += 1
            risk = c.risk_abs or 1e-12
            if c.direction == "LONG":
                fav = (px - c.entry) / c.entry * 100.0
                adv = (c.entry - px) / c.entry * 100.0
                if px >= c.entry and fav > c.mfe:
                    c.mfe = fav
                    c.time_to_mfe_s = now - c.opened_ts
                if px <= c.entry:
                    c.mae = max(c.mae, adv)
                c.mfe_r = max(c.mfe_r, (px - c.entry) / risk)
                c.mae_r = max(c.mae_r, (c.entry - px) / risk)
                if c.tp1 and not c.hit_tp1 and px >= float(c.tp1):
                    c.hit_tp1 = True
                    c.time_to_tp1_s = now - c.opened_ts
                if c.tp2 and not c.hit_tp2 and px >= float(c.tp2):
                    c.hit_tp2 = True
                    c.time_to_tp2_s = now - c.opened_ts
                if px <= c.sl:
                    c.hit_sl = True
                    c.time_to_sl_s = now - c.opened_ts
                    c.close_reason = "SL"
                    c.close_price = px
                    done.append(sym)
            else:  # SHORT
                fav = (c.entry - px) / c.entry * 100.0
                adv = (px - c.entry) / c.entry * 100.0
                if px <= c.entry and fav > c.mfe:
                    c.mfe = fav
                    c.time_to_mfe_s = now - c.opened_ts
                if px >= c.entry:
                    c.mae = max(c.mae, adv)
                c.mfe_r = max(c.mfe_r, (c.entry - px) / risk)
                c.mae_r = max(c.mae_r, (px - c.entry) / risk)
                if c.tp1 and not c.hit_tp1 and px <= float(c.tp1):
                    c.hit_tp1 = True
                    c.time_to_tp1_s = now - c.opened_ts
                if c.tp2 and not c.hit_tp2 and px <= float(c.tp2):
                    c.hit_tp2 = True
                    c.time_to_tp2_s = now - c.opened_ts
                if px >= c.sl:
                    c.hit_sl = True
                    c.time_to_sl_s = now - c.opened_ts
                    c.close_reason = "SL"
                    c.close_price = px
                    done.append(sym)

            # timeout
            if (now - c.opened_ts) / 3600.0 >= max_age and sym not in done:
                c.close_reason = "TIMEOUT"
                c.close_price = px
                done.append(sym)
            # soft exit: hit TP2 → close shadow as success path
            elif c.hit_tp2 and sym not in done:
                c.close_reason = "TP2"
                c.close_price = px
                done.append(sym)

        for sym in done:
            c = self.active.pop(sym)
            c.closed = True
            c.close_ts = now
            risk = c.risk_abs or 1e-12
            move = (c.close_price - c.entry) if c.direction == "LONG" else (c.entry - c.close_price)
            c.gross_result_r = move / risk
            c.net_result_r = c.gross_result_r - (c.fee_r + c.spread_r + c.slip_r + c.impact_r + c.funding_r)
            self.closed.append(c)
            self._log_row("REVERSAL_SHADOW_CLOSE", c)
            print(
                f"[Shadow] CLOSE {c.direction} {c.symbol} reason={c.close_reason} "
                f"MFE={c.mfe:.2f}% MAE={c.mae:.2f}% "
                f"TP1={c.hit_tp1} TP2={c.hit_tp2} SL={c.hit_sl}"
            )
        if done:
            self._save_state()

    def summary(self) -> dict:
        all_c = list(self.closed)
        # also try load from csv count
        n = len(all_c)
        if n == 0:
            return {"n_closed": 0, "n_active": len(self.active)}
        tp1_rate = sum(1 for c in all_c if c.hit_tp1) / n
        tp2_rate = sum(1 for c in all_c if c.hit_tp2) / n
        sl_rate = sum(1 for c in all_c if c.hit_sl) / n
        avg_mfe = sum(c.mfe for c in all_c) / n
        avg_mae = sum(c.mae for c in all_c) / n
        def side_stats(side):
            rows = [c for c in all_c if c.direction == side]
            if not rows:
                return {"n": 0}
            return {
                "n": len(rows),
                "win_rate_net": round(sum(c.net_result_r > 0 for c in rows) / len(rows), 3),
                "avg_gross_r": round(sum(c.gross_result_r for c in rows) / len(rows), 3),
                "avg_net_r": round(sum(c.net_result_r for c in rows) / len(rows), 3),
            }
        return {
            "n_closed": n,
            "n_active": len(self.active),
            "tp1_rate": round(tp1_rate, 3),
            "tp2_rate": round(tp2_rate, 3),
            "sl_rate": round(sl_rate, 3),
            "avg_mfe_pct": round(avg_mfe, 3),
            "avg_mae_pct": round(avg_mae, 3),
            "by_direction": {"LONG": side_stats("LONG"), "SHORT": side_stats("SHORT")},
        }


_tracker: Optional[ShadowTracker] = None


def get_shadow_tracker() -> ShadowTracker:
    global _tracker
    if _tracker is None:
        _tracker = ShadowTracker()
    return _tracker


def should_execute(signal: dict) -> bool:
    """Reversal moze wejsc tylko do PAPER po pelnej kontroli; LIVE pozostaje zablokowany."""
    is_rev = signal.get("engine") == "reversal" or signal.get("setup") == "reversal_confirmed"
    if not is_rev:
        return True
    if shadow_only():
        signal["paper_reversal_block_reason"] = "REVERSAL_SHADOW_ONLY"
        return False
    if not bool(getattr(config, "PAPER_TRADING", True)):
        signal["paper_reversal_block_reason"] = "REVERSAL_LIVE_DISABLED"
        return False
    if not bool(getattr(config, "REVERSAL_PAPER_EXECUTION_ENABLED", False)):
        signal["paper_reversal_block_reason"] = "REVERSAL_PAPER_DISABLED"
        return False
    if signal.get("setup") != "reversal_confirmed":
        signal["paper_reversal_block_reason"] = "REVERSAL_NOT_CONFIRMED"
        return False
    minimum = int(getattr(config, "REVERSAL_PAPER_MIN_CONFIRMATIONS", 1) or 1)
    status = str(signal.get("confirmation_status") or "")
    if status not in ("CONFIRMED", "BYPASS") and int(signal.get("confirmation_count") or 0) < minimum:
        signal["paper_reversal_block_reason"] = "REVERSAL_NOT_CONFIRMED"
        return False
    if int(signal.get("confirmation_count") or 0) < minimum:
        signal["paper_reversal_block_reason"] = "REVERSAL_CONFIRMATIONS_LOW"
        return False
    try:
        from expected_net_r import expected_net_r, net_r_ok
        expected_net_r(signal)
        net_ok, net_reason = net_r_ok(signal)
        if bool(getattr(config, "REVERSAL_PAPER_REQUIRE_NET_R", True)) and not net_ok:
            signal["paper_reversal_block_reason"] = net_reason
            return False
    except Exception as exc:
        signal["paper_reversal_block_reason"] = f"REVERSAL_COST_CHECK_ERROR:{exc}"
        return False
    signal.pop("paper_reversal_block_reason", None)
    return True


def process_reversal_signals(signals: List[dict], price_map: Dict[str, float] = None):
    """
    Dla każdego sygnału reversal: zarejestruj shadow candidate.
    Aktualizuj ceny aktywnych.
    """
    tr = get_shadow_tracker()
    for s in signals or []:
        is_rev = s.get("engine") == "reversal" or s.get("setup") == "reversal_confirmed"
        if not is_rev:
            continue
        if s.get("direction") not in ("LONG", "SHORT"):
            continue
        if s.get("reject_reason"):
            continue
        try:
            from expected_net_r import expected_net_r
            expected_net_r(s)
        except Exception:
            s["expected_net_r"] = 0.0
        tr.register_candidate(s)
    if price_map:
        tr.update_prices(price_map)
    return tr.summary()


def register_reversal_watches(coins, regime: str = "UNKNOWN"):
    """
    Shadow-only: loguje ekstremalne ruchy nawet bez pełnego CONFIRM.
    Event: REVERSAL_WATCH (nie CANDIDATE) — do analizy populacji, zero egzekucji.
    """
    if not shadow_only() and not bool(getattr(config, "REVERSAL_SHADOW_LOG_ALWAYS", True)):
        return
    try:
        from reversal_engine import detect_extreme, detect_exhaustion
    except Exception as e:
        print(f"[Shadow] import: {e}")
        return
    tr = get_shadow_tracker()
    min_pct = float(getattr(config, "REVERSAL_SHADOW_WATCH_MIN_24H", 15.0) or 15.0)
    for coin in coins or []:
        sym = (coin.get("symbol") or "").upper()
        if not sym or sym in tr.active:
            continue
        ch24 = 0.0
        try:
            ch24 = float(coin.get("change_24h") or 0)
        except (TypeError, ValueError):
            pass
        if abs(ch24) < min_pct:
            continue
        extreme = detect_extreme(coin)
        if not extreme:
            # soft: force side from 24h for watch log
            extreme = {
                "side": "UP" if ch24 > 0 else "DOWN",
                "score": 0.3,
                "tags": [f"WATCH_24H({ch24:+.1f}%)"],
                "ch24": ch24,
                "ch1": float(coin.get("change_1h") or 0),
            }
        exh = detect_exhaustion(coin, extreme)
        direction = "SHORT" if extreme.get("side") == "UP" else "LONG"
        price = float(coin.get("price") or coin.get("blofin_price") or 0)
        if price <= 0:
            continue
        # soft SL 5% for tracking only
        sl = price * (1.05 if direction == "SHORT" else 0.95)
        sig = {
            "symbol": sym,
            "direction": direction,
            "price": price,
            "sl_price": sl,
            "tp1_price": price * (0.97 if direction == "SHORT" else 1.03),
            "tp2_price": price * (0.94 if direction == "SHORT" else 1.06),
            "reversal_score": float((exh or {}).get("score") or 0.2),
            "expected_net_r": 0.0,
            "reasons": ["SHADOW_WATCH", f"REGIME_{regime}"] + list(extreme.get("tags") or []) + list((exh or {}).get("tags") or []),
            "engine": "reversal",
            "setup": "reversal_watch",
        }
        # reuse register but mark as watch in log via reasons
        c = tr.register_candidate(sig)
        if c:
            # rewrite last event type conceptually via second log line
            try:
                tr._log_row("REVERSAL_WATCH", c)
            except Exception:
                pass
