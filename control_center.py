"""Operational Control Center: readiness, watchdog, diagnostics and PAPER export."""
from __future__ import annotations

import json
import time
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path

import config

BASE = Path(__file__).resolve().parent
_SENSITIVE_CONFIG_PARTS = ("KEY", "SECRET", "PASSPHRASE", "PASSWORD", "TOKEN")


def safe_config_snapshot() -> dict:
    """Public runtime settings only; credentials must never enter support exports."""
    return {
        key: value
        for key, value in vars(config).items()
        if key.isupper()
        and isinstance(value, (str, int, float, bool, type(None)))
        and not any(part in key for part in _SENSITIVE_CONFIG_PARTS)
    }


def _item(name, ok, detail="", blocking=False):
    return {"name": name, "status": "READY" if ok else ("BLOCKED" if blocking else "DEGRADED"),
            "detail": str(detail or ""), "blocking": bool(blocking and not ok)}


def readiness(rt, state: dict) -> dict:
    sources = str(state.get("sources") or "")
    age = max(0.0, time.time() - float(getattr(rt, "last_heartbeat", 0) or 0))
    engine_on = bool(getattr(rt, "engine_enabled", False))
    max_age = float(getattr(config, "WATCHDOG_MAX_CYCLE_AGE_SEC", 180))
    if int(getattr(rt, "cycle", 0) or 0) <= 2:
        max_age = max(max_age, 600.0)
    heartbeat_ok = (not engine_on) or age <= max_age
    ex = state.get("exchange_account") or {}
    live = str(state.get("mode") or "DEMO").upper() == "LIVE"
    reconcile = getattr(getattr(rt, "reconciler", None), "last_report", None) or {}
    protection = getattr(rt, "protection", None)
    protected = len(getattr(protection, "by_key", {}) or {}) if protection else 0
    positions = state.get("display_positions") or []
    items = [
        _item("Engine heartbeat", heartbeat_ok, f"age {age:.1f}s" if engine_on else "engine stopped", engine_on),
        _item("BloFin market data",
              "blofin: ok" in sources.lower() or "universe:blofinusdt(" in sources.lower(),
              sources or "no status", True),
        _item("Cross-market context", "binance" in sources.lower(), sources or "no status", False),
        _item("CoinGecko context", "coingecko" in sources.lower(), sources or "no status", False),
        _item("Account API", (not live) or not ex.get("error"), ex.get("error") or state.get("mode"), live),
        _item("Reconciliation", (not live) or reconcile.get("in_sync", True), "in sync" if reconcile.get("in_sync", True) else "position drift", live),
        _item("Position protection", not positions or protected >= len(positions), f"protected {protected}/{len(positions)}", live),
    ]
    overall = "BLOCKED" if any(x["blocking"] for x in items) else (
        "DEGRADED" if any(x["status"] == "DEGRADED" for x in items) else "READY"
    )
    return {"overall": overall, "items": items, "heartbeat_age_sec": round(age, 1)}


def rejection_summary(rejects) -> dict:
    rows = list(rejects or [])
    counts = Counter(str(r.get("reason") or "UNKNOWN").split("(")[0] for r in rows)
    return {"total": len(rows), "reasons": [{"reason": k, "count": v} for k, v in counts.most_common(12)]}


def lifecycle(state: dict) -> list:
    rows = []
    for signal in (state.get("signals") or [])[:20]:
        impact = signal.get("_ob_impact") or {}
        rows.append({"symbol": signal.get("symbol"), "direction": signal.get("direction"),
                     "engine": signal.get("engine") or "trend", "stage": "RISK_READY",
                     "reason": signal.get("decision_path") or "candidate", "impact_pct": impact.get("impact_pct")})
    for reject in (state.get("rejects") or [])[:20]:
        rows.append({"symbol": reject.get("symbol"), "direction": reject.get("direction"),
                     "engine": reject.get("engine"), "stage": "REJECTED", "reason": reject.get("reason")})
    return rows[:30]


def protection_view(rt, state: dict) -> list:
    manager = getattr(rt, "protection", None)
    attachments = getattr(manager, "by_key", {}) or {}
    out = []
    for pos in state.get("display_positions") or []:
        sym = str(pos.get("symbol") or pos.get("instId") or "").split("-")[0].upper()
        direction = str(pos.get("direction") or pos.get("side") or "").upper()
        key = f"{sym}:{direction}"
        att = attachments.get(key)
        out.append({"symbol": sym, "direction": direction, "local_sl": pos.get("sl"),
                    "exchange_sl": getattr(att, "exchange_sl_price", None) if att else None,
                    "status": "PROTECTED" if att else "PROTECTION_MISSING",
                    "last_sync": getattr(att, "updated_at", None) if att else None})
    return out


def execution_comparison(state: dict) -> list:
    out = []
    for p in (state.get("closed_positions") or [])[:30]:
        planned = p.get("strategy_price") or p.get("entry")
        actual = p.get("execution_price") or p.get("entry")
        slip = None
        try:
            slip = abs(float(actual) - float(planned)) / float(planned) * 100
        except (TypeError, ValueError, ZeroDivisionError):
            pass
        out.append({"symbol": p.get("symbol"), "engine": p.get("engine"), "planned_entry": planned,
                    "actual_entry": actual, "slippage_pct": slip, "realized_r": p.get("realized_r"),
                    "pnl": p.get("pnl")})
    return out


def enrich(rt, state: dict) -> dict:
    state["readiness"] = readiness(rt, state)
    state["rejection_summary"] = rejection_summary(state.get("rejects"))
    state["signal_lifecycle"] = lifecycle(state)
    state["protection_view"] = protection_view(rt, state)
    state["execution_comparison"] = execution_comparison(state)
    return state


def export_paper_session(state: dict) -> Path:
    exports = BASE / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = exports / f"CryptoEdge_PAPER_session_{stamp}.zip"
    snapshot = json.dumps(state, ensure_ascii=False, indent=2, default=str)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("session_state.json", snapshot)
        archive.writestr("config_snapshot.json", json.dumps(safe_config_snapshot(), indent=2))
        for name in ("decision_telemetry.jsonl", "bot_log.csv", "bot_state.json"):
            path = BASE / "logs" / name
            if path.exists():
                archive.write(path, f"logs/{name}")
    return target
