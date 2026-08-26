"""Deterministic execution layer shared by candle, trade and L2 replay.

It deliberately separates strategy decisions from venue acceptance and fills.
Bar-only fills remain conservative; trade/L2 evidence can improve fidelity but
can never create a fill before the order reached the simulated venue.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import random
from typing import Iterable, Optional

import config


@dataclass
class ReplayOrder:
    order_id: str
    symbol: str
    side: str
    quantity: float
    limit_price: Optional[float]
    decision_ts_ms: int
    submitted_ts_ms: int
    accepted_ts_ms: int
    timeout_ts_ms: Optional[int] = None
    cancel_requested_ts_ms: Optional[int] = None
    canceled_ts_ms: Optional[int] = None
    state: str = "ACCEPTED"
    filled_quantity: float = 0.0
    avg_fill_price: Optional[float] = None
    fills: list[dict] = field(default_factory=list)

    @property
    def remaining(self) -> float:
        return max(0.0, self.quantity - self.filled_quantity)


def spread_bps(symbol: str, regime: str = "UNKNOWN") -> float:
    major = str(symbol or "").upper() in {"BTC", "ETH", "SOL"}
    value = float(getattr(config, "REPLAY_SPREAD_MAJOR_BPS", 1.5) if major else
                  getattr(config, "REPLAY_SPREAD_ALT_BPS", 6.0))
    if str(regime or "").upper() in {"PANIC", "FLASH_CRASH", "STRESS"}:
        value *= float(getattr(config, "REPLAY_SPREAD_PANIC_MULT", 2.5))
    return value


class ReplayExecutionEngine:
    def __init__(self, *, touch_model: str = None, seed: int = None,
                 submit_latency_ms: int = None, cancel_latency_ms: int = None):
        self.touch_model = str(touch_model or getattr(config, "REPLAY_LIMIT_TOUCH_MODEL", "pessimistic")).lower()
        self.rng = random.Random(int(seed if seed is not None else getattr(config, "REPLAY_RANDOM_SEED", 240824)))
        self.submit_latency_ms = int(submit_latency_ms if submit_latency_ms is not None else getattr(config, "REPLAY_SUBMIT_LATENCY_MS", 250))
        self.cancel_latency_ms = int(cancel_latency_ms if cancel_latency_ms is not None else getattr(config, "REPLAY_CANCEL_LATENCY_MS", 250))
        self.orders: dict[str, ReplayOrder] = {}

    def submit(self, *, order_id: str, symbol: str, side: str, quantity: float,
               decision_ts_ms: int, limit_price: float = None,
               timeout_ms: int = None) -> ReplayOrder:
        submitted = int(decision_ts_ms)
        accepted = submitted + max(0, self.submit_latency_ms)
        order = ReplayOrder(order_id, symbol, side.upper(), float(quantity), limit_price,
                            int(decision_ts_ms), submitted, accepted,
                            accepted + int(timeout_ms) if timeout_ms else None)
        self.orders[order_id] = order
        return order

    def request_cancel(self, order_id: str, ts_ms: int) -> None:
        order = self.orders[order_id]
        if order.state in {"FILLED", "CANCELED"}:
            return
        order.cancel_requested_ts_ms = int(ts_ms)
        order.canceled_ts_ms = int(ts_ms) + max(0, self.cancel_latency_ms)
        order.state = "CANCELING"

    def _fill(self, order: ReplayOrder, ts_ms: int, qty: float, px: float,
              liquidity: str, evidence: str) -> dict:
        qty = min(order.remaining, max(0.0, float(qty)))
        if qty <= 0:
            return {}
        old_value = order.filled_quantity * float(order.avg_fill_price or 0)
        order.filled_quantity += qty
        order.avg_fill_price = (old_value + qty * px) / order.filled_quantity
        event = {"ts_ms": int(ts_ms), "quantity": qty, "price": float(px),
                 "liquidity_role": liquidity, "evidence": evidence}
        order.fills.append(event)
        order.state = "FILLED" if order.remaining <= 1e-12 else "PARTIAL"
        return event

    def on_bar(self, order_id: str, *, ts_ms: int, open_: float, high: float,
               low: float, close: float, volume: float = 0, regime: str = "UNKNOWN") -> dict:
        order = self.orders[order_id]
        now = int(ts_ms)
        if now < order.accepted_ts_ms or order.state in {"FILLED", "CANCELED"}:
            return {}
        if order.canceled_ts_ms is not None and now >= order.canceled_ts_ms:
            order.state = "CANCELED"
            return {"state": "CANCELED", "ts_ms": order.canceled_ts_ms}
        if order.timeout_ts_ms is not None and now >= order.timeout_ts_ms and order.cancel_requested_ts_ms is None:
            self.request_cancel(order_id, order.timeout_ts_ms)

        if order.limit_price is None:
            half = spread_bps(order.symbol, regime) / 20_000.0
            px = float(open_) * (1 + half if order.side == "BUY" else 1 - half)
            return self._fill(order, now, order.remaining, px, "taker", "bar_open")

        limit = float(order.limit_price)
        crossed = low < limit if order.side == "BUY" else high > limit
        touched = low <= limit <= high
        if not touched:
            return {}
        eligible = crossed
        if not crossed and self.touch_model == "probabilistic":
            eligible = self.rng.random() < float(getattr(config, "REPLAY_LIMIT_TOUCH_FILL_PROB", 0.35))
        if not eligible:  # pessimistic touch-only bars do not fill
            return {}
        fraction = float(getattr(config, "REPLAY_PARTIAL_FILL_FRACTION", 0.50))
        cap_from_volume = max(0.0, float(volume or 0)) * 0.01
        qty = order.remaining if cap_from_volume <= 0 else min(order.remaining, max(order.remaining * fraction, cap_from_volume))
        return self._fill(order, now, qty, limit, "maker", "bar_cross")

    def on_trade(self, order_id: str, *, ts_ms: int, price: float, quantity: float,
                 aggressor: str) -> dict:
        order = self.orders[order_id]
        if int(ts_ms) < order.accepted_ts_ms or order.state in {"FILLED", "CANCELED"}:
            return {}
        opposing = (order.side == "BUY" and str(aggressor).upper() == "SELLER") or (
            order.side == "SELL" and str(aggressor).upper() == "BUYER")
        limit_ok = order.limit_price is None or (price <= order.limit_price if order.side == "BUY" else price >= order.limit_price)
        if not opposing or not limit_ok:
            return {}
        return self._fill(order, int(ts_ms), min(order.remaining, quantity),
                          float(order.limit_price or price), "maker", "trade")

    def on_l2(self, order_id: str, *, ts_ms: int, levels: Iterable[tuple[float, float]]) -> list[dict]:
        order = self.orders[order_id]
        if int(ts_ms) < order.accepted_ts_ms or order.state in {"FILLED", "CANCELED"}:
            return []
        events = []
        for price, quantity in levels:
            executable = order.limit_price is None or (price <= order.limit_price if order.side == "BUY" else price >= order.limit_price)
            if not executable:
                continue
            event = self._fill(order, int(ts_ms), quantity, price,
                               "taker" if order.limit_price is None else "maker", "l2")
            if event:
                events.append(event)
            if order.remaining <= 0:
                break
        return events
