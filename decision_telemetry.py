"""Compact durable decision/outcome telemetry for PAPER research."""
from __future__ import annotations

import json
import math
import threading
import time
import uuid
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

import config

_LOCK = threading.Lock()
_RECENT_REJECTIONS: Dict[tuple, float] = {}


def _number(value: Any):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _append(row: Dict[str, Any]) -> None:
    if not bool(getattr(config, "DECISION_TELEMETRY_ENABLED", True)):
        return
    try:
        path = Path(getattr(config, "DECISION_TELEMETRY_PATH", "logs/decision_telemetry.jsonl"))
        if not path.is_absolute():
            # Unit tests must never contaminate PAPER/LIVE research logs, even
            # when an individual test forgets to patch the configured path.
            in_test_process = any(
                name == "tests" or name.startswith("tests.")
                for name in sys.modules
            )
            root = Path(tempfile.gettempdir()) / "cryptoedge-tests" if in_test_process else Path(__file__).resolve().parent
            path = root / path
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK, path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass  # obserwacja nie może zmieniać decyzji tradingowej


def decision_snapshot(signal: Dict[str, Any], decision: str, reason: str = "") -> str:
    decision_id = str(signal.get("decision_id") or uuid.uuid4().hex)
    signal["decision_id"] = decision_id
    impact = signal.get("_ob_impact") or {}
    liquidity = signal.get("liquidity") or {}
    candle = signal.get("candle_confirmation") or {}
    rsi_structure = signal.get("rsi_structure") or {}
    target_path = signal.get("target_path") or {}
    probabilities = signal.get("expected_r_probabilities") or {}
    decision_upper = str(decision).upper()
    now = time.time()
    if decision_upper == "REJECT":
        skipped = tuple(getattr(config, "DECISION_TELEMETRY_SKIP_REASONS", ()) or ())
        if any(str(reason or "").startswith(prefix) for prefix in skipped):
            return decision_id
        key = (
            str(signal.get("symbol") or ""), str(signal.get("direction") or ""),
            str(signal.get("engine") or signal.get("score_type") or ""),
            str(reason or ""), str(signal.get("market_regime") or ""),
        )
        ttl = max(0.0, float(getattr(config, "DECISION_TELEMETRY_DEDUPE_SECONDS", 300) or 0))
        with _LOCK:
            previous = _RECENT_REJECTIONS.get(key)
            if ttl and previous is not None and now - previous < ttl:
                return decision_id
            _RECENT_REJECTIONS[key] = now
            if len(_RECENT_REJECTIONS) > 20000:
                cutoff = now - max(ttl, 1.0)
                for old_key, old_ts in list(_RECENT_REJECTIONS.items()):
                    if old_ts < cutoff:
                        _RECENT_REJECTIONS.pop(old_key, None)
    _append({
        "event": "DECISION", "ts": now, "decision_id": decision_id,
        # Wiersz musi wskazywac stan rynku, ktory go wywolal - inaczej
        # odrzucenie jest samotnym napisem bez kontekstu.
        "snapshot_id": signal.get("snapshot_id"),
        "decision_ts_ms": signal.get("decision_ts_ms"),
        "decision": decision_upper, "reason": str(reason or ""),
        "symbol": signal.get("symbol"), "direction": signal.get("direction"),
        "strategy_mode": str(signal.get("strategy_mode") or getattr(config, "STRATEGY_MODE", "DAYTRADING")).upper(),
        "engine": signal.get("engine") or signal.get("score_type"),
        "profile": signal.get("reversal_profile") or signal.get("v2_profile"),
        "preferred_engine": signal.get("preferred_engine"),
        "regime": signal.get("market_regime"),
        "panic_trigger": signal.get("panic_trigger"),
        "regime_atr_ratio": _number(signal.get("regime_atr_ratio")),
        "regime_atr_percentile": _number(signal.get("regime_atr_percentile")),
        "regime_realized_vol": _number(signal.get("regime_realized_vol")),
        "liquidity_bucket": signal.get("liquidity_bucket"),
        "strength": _number(signal.get("strength")), "price": _number(signal.get("price")),
        "expected_net_r": _number(signal.get("expected_net_r")),
        "expected_r_status": signal.get("expected_r_status"),
        "net_reward_potential_r": _number(signal.get("net_reward_potential_r")),
        "p_tp1": _number(probabilities.get("p_tp1")),
        "p_tp2_given_tp1": _number(probabilities.get("p_tp2_given_tp1")),
        "setup_probability_multiplier": _number(signal.get("setup_probability_multiplier")),
        "candle_confirmation_score": _number(candle.get("score")),
        "candle_touch_age": _number(candle.get("touch_age")),
        "candle_directional_wick": _number(candle.get("directional_wick_fraction")),
        "candle_close_location": _number(candle.get("close_location")),
        "rsi_divergence_confirmed": bool(rsi_structure.get("divergence")),
        "rsi_failure_swing": bool(rsi_structure.get("failure_swing")),
        "rsi_slope": _number(rsi_structure.get("rsi_slope")),
        "target_obstacle_r": _number(target_path.get("obstacle_r")),
        "target_clearance": _number(target_path.get("clearance")),
        "tp1_capped_by_structure": bool(target_path.get("tp1_capped")),
        "planned_notional": _number(signal.get("_planned_notional")),
        "spread_pct": _number(liquidity.get("spread_pct") or signal.get("ob_spread_pct")),
        "vwap": _number(impact.get("vwap")), "impact_pct": _number(impact.get("impact_pct")),
        "fill_ratio": _number(impact.get("fill_ratio")),
        "residual_momentum_24h": _number(signal.get("residual_momentum_24h")),
        "signal_source": signal.get("signal_source"),
    })
    return decision_id


def outcome_snapshot(position: Any, pnl: float, reason: str) -> None:
    hold_seconds = None
    try:
        hold_seconds = max(0.0, (position.exit_time - position.entry_time).total_seconds())
    except (TypeError, AttributeError):
        pass
    _append({
        "event": "OUTCOME", "ts": time.time(),
        "decision_id": getattr(position, "decision_id", None),
        "position_id": getattr(position, "id", None),
        "symbol": getattr(position, "symbol", None),
        "direction": getattr(position, "direction", None),
        "strategy_mode": str(getattr(config, "STRATEGY_MODE", "DAYTRADING")).upper(),
        "engine": getattr(position, "engine", None),
        "profile": getattr(position, "reversal_profile", None) or getattr(position, "v2_profile", None),
        "entry_price": _number(getattr(position, "entry_price", None)),
        "exit_price": _number(getattr(position, "exit_price", None)),
        "pnl_usd": _number(pnl), "pnl_pct": _number(getattr(position, "pnl_pct", None)),
        "reason": str(reason or ""), "hold_seconds": hold_seconds,
        "actual_notional": _number(getattr(position, "actual_notional", None)),
        "funding_paid": _number(getattr(position, "funding_paid", None)),
    })
