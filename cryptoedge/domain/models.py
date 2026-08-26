"""Immutable typed contracts at the boundaries of CryptoEdge modules."""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Optional
from uuid import uuid4

from ._compat import enum_value, freeze, legacy_dict, thaw
from .enums import (
    DecisionStatus, Direction, LiquidityRole, OrderSide, OrderType,
    PositionStatus, RiskStatus,
)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


_TIMEFRAME_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000,
                 "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000,
                 "4h": 14_400_000, "1d": 86_400_000, "1w": 604_800_000}


def _finite(name, value, *, positive=False, non_negative=False, optional=False):
    if value is None and optional:
        return
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    if positive and number <= 0:
        raise ValueError(f"{name} must be positive")
    if non_negative and number < 0:
        raise ValueError(f"{name} cannot be negative")


def _last_bar_closed(frame, timeframe, decision_ts_ms):
    if not frame:
        return True
    flags = frame.get("closed") or frame.get("is_closed")
    if isinstance(flags, (list, tuple)) and flags and not bool(flags[-1]):
        return False
    close_ts = frame.get("close_timestamps") or frame.get("close_ts")
    if isinstance(close_ts, (list, tuple)) and close_ts:
        return int(close_ts[-1]) <= int(decision_ts_ms)
    timestamps = frame.get("timestamps") or frame.get("ts") or ()
    width = _TIMEFRAME_MS.get(str(timeframe or "").lower(), 0)
    if not isinstance(timestamps, (list, tuple)) or not timestamps or width <= 0:
        return False
    return int(timestamps[-1]) + width <= int(decision_ts_ms)


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    symbol: str
    event_ts_ms: int
    decision_ts_ms: int
    frames: Mapping[str, Any] = field(default_factory=dict)
    ticker: Mapping[str, Any] = field(default_factory=dict)
    order_book: Mapping[str, Any] = field(default_factory=dict)
    funding: Mapping[str, Any] = field(default_factory=dict)
    source: str = "runtime"
    snapshot_id: str = field(default_factory=lambda: _id("snap"))
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", str(self.symbol).upper())
        object.__setattr__(self, "event_ts_ms", int(self.event_ts_ms))
        object.__setattr__(self, "decision_ts_ms", int(self.decision_ts_ms))
        for name in ("frames", "ticker", "order_book", "funding", "metadata"):
            object.__setattr__(self, name, freeze(getattr(self, name) or {}))
        if self.decision_ts_ms < self.event_ts_ms:
            raise ValueError("decision_ts_ms cannot precede event_ts_ms")

    @classmethod
    def from_legacy(cls, value: Any) -> "MarketSnapshot":
        data = legacy_dict(value)
        known = {"symbol", "event_ts_ms", "decision_ts_ms", "frames", "ticker",
                 "order_book", "funding", "source", "snapshot_id", "metadata"}
        metadata = dict(data.get("metadata") or {})
        metadata.update({k: v for k, v in data.items() if k not in known})
        event_ts = data.get("event_ts_ms", data.get("timestamp_ms", data.get("ts_ms", 0)))
        decision_ts = data.get("decision_ts_ms", event_ts)
        return cls(
            symbol=data.get("symbol") or data.get("coin") or "",
            event_ts_ms=event_ts or 0, decision_ts_ms=decision_ts or 0,
            frames=data.get("frames") or {}, ticker=data.get("ticker") or {},
            order_book=data.get("order_book") or {}, funding=data.get("funding") or {},
            source=data.get("source") or "runtime",
            snapshot_id=data.get("snapshot_id") or _id("snap"), metadata=metadata,
        )

    def to_legacy(self) -> dict:
        data = thaw(self.metadata)
        data.update({
            "symbol": self.symbol, "event_ts_ms": self.event_ts_ms,
            "decision_ts_ms": self.decision_ts_ms, "frames": thaw(self.frames),
            "ticker": thaw(self.ticker), "order_book": thaw(self.order_book),
            "funding": thaw(self.funding), "source": self.source,
            "snapshot_id": self.snapshot_id,
        })
        return data

    def validate_closed_bars(self) -> tuple[bool, str]:
        for tf in ("5m", "15m", "1H", "4H", "1D"):
            frame = self.frames.get(tf) or self.frames.get(tf.lower()) or {}
            if frame and not _last_bar_closed(frame, tf, self.decision_ts_ms):
                return False, f"LOOKAHEAD_{tf}"
        return True, "OK"

    def to_dict(self) -> dict:
        return self.to_legacy()


@dataclass(frozen=True, slots=True)
class StrategyDecision:
    symbol: str
    status: DecisionStatus
    decision_ts_ms: int
    direction: Optional[Direction] = None
    strategy_price: Optional[float] = None
    strength: float = 0.0
    engine: str = "unknown"
    reasons: tuple[str, ...] = ()
    decision_id: str = field(default_factory=lambda: _id("dec"))
    snapshot_id: Optional[str] = None
    expected_net_r: Optional[float] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", str(self.symbol).upper())
        object.__setattr__(self, "status", enum_value(DecisionStatus, self.status, DecisionStatus.NO_TRADE))
        object.__setattr__(self, "direction", enum_value(Direction, self.direction))
        object.__setattr__(self, "decision_ts_ms", int(self.decision_ts_ms))
        object.__setattr__(self, "reasons", tuple(str(v) for v in self.reasons))
        object.__setattr__(self, "metadata", freeze(self.metadata or {}))
        _finite("strength", self.strength)
        _finite("strategy_price", self.strategy_price, positive=True, optional=True)
        _finite("expected_net_r", self.expected_net_r, optional=True)
        if self.status == DecisionStatus.CANDIDATE and self.direction is None:
            raise ValueError("candidate decision requires direction")

    @classmethod
    def from_legacy(cls, value: Any, *, decision_ts_ms: int = 0) -> "StrategyDecision":
        data = legacy_dict(value)
        direction = enum_value(Direction, data.get("direction"))
        rejected = data.get("reject_reason") or data.get("rejected")
        status = data.get("status")
        if status is None:
            status = DecisionStatus.REJECTED if rejected else (
                DecisionStatus.CANDIDATE if direction else DecisionStatus.NO_TRADE
            )
        known = {"symbol", "coin", "status", "direction", "decision_ts_ms", "timestamp_ms",
                 "price", "strategy_price", "strength", "engine", "score_type", "reasons",
                 "decision_id", "snapshot_id", "expected_net_r"}
        return cls(
            symbol=data.get("symbol") or data.get("coin") or "", status=status,
            decision_ts_ms=data.get("decision_ts_ms", data.get("timestamp_ms", decision_ts_ms)) or 0,
            direction=direction, strategy_price=data.get("strategy_price", data.get("price")),
            strength=float(data.get("strength") or 0),
            engine=data.get("engine") or data.get("score_type") or "unknown",
            reasons=tuple(data.get("reasons") or ([str(rejected)] if rejected else [])),
            decision_id=data.get("decision_id") or _id("dec"), snapshot_id=data.get("snapshot_id"),
            expected_net_r=data.get("expected_net_r"),
            metadata={k: v for k, v in data.items() if k not in known},
        )

    def to_legacy(self) -> dict:
        data = thaw(self.metadata)
        data.update({
            "symbol": self.symbol, "status": self.status.value,
            "decision_ts_ms": self.decision_ts_ms,
            "direction": self.direction.value if self.direction else None,
            "price": self.strategy_price, "strategy_price": self.strategy_price,
            "strength": self.strength, "engine": self.engine,
            "reasons": list(self.reasons), "decision_id": self.decision_id,
            "snapshot_id": self.snapshot_id, "expected_net_r": self.expected_net_r,
        })
        return data


@dataclass(frozen=True, slots=True)
class EntryCandidate:
    decision: StrategyDecision
    entry_price: float
    stop_price: float
    target_prices: tuple[float, ...]
    candidate_id: str = field(default_factory=lambda: _id("cand"))
    limit_price: Optional[float] = None
    setup: Optional[str] = None
    profile: Optional[str] = None
    regime: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.decision.direction is None:
            raise ValueError("entry candidate requires directional decision")
        object.__setattr__(self, "target_prices", tuple(float(v) for v in self.target_prices))
        object.__setattr__(self, "metadata", freeze(self.metadata or {}))
        _finite("entry_price", self.entry_price, positive=True)
        _finite("stop_price", self.stop_price, positive=True)
        _finite("limit_price", self.limit_price, positive=True, optional=True)
        for value in self.target_prices:
            _finite("target_price", value, positive=True)

    @property
    def symbol(self) -> str:
        return self.decision.symbol

    @property
    def direction(self) -> Direction:
        return self.decision.direction  # type: ignore[return-value]

    @classmethod
    def from_legacy(cls, value: Any, *, decision_ts_ms: int = 0) -> "EntryCandidate":
        data = legacy_dict(value)
        decision = StrategyDecision.from_legacy(data, decision_ts_ms=decision_ts_ms)
        targets = [data.get("tp1_price"), data.get("tp2_price"), data.get("tp_price")]
        targets = tuple(float(v) for v in targets if v is not None)
        known = {"price", "fill_price", "sl_price", "tp1_price", "tp2_price", "tp_price",
                 "limit_price", "setup", "v2_profile", "reversal_profile", "market_regime",
                 "candidate_id"}
        return cls(
            decision=decision, entry_price=float(data.get("fill_price") or data.get("price") or 0),
            stop_price=float(data.get("sl_price") or 0), target_prices=targets,
            candidate_id=data.get("candidate_id") or _id("cand"), limit_price=data.get("limit_price"),
            setup=data.get("setup"), profile=data.get("v2_profile") or data.get("reversal_profile"),
            regime=data.get("market_regime"),
            metadata={k: v for k, v in data.items() if k not in known},
        )

    def to_legacy(self) -> dict:
        data = self.decision.to_legacy()
        data.update({"candidate_id": self.candidate_id, "price": self.entry_price,
                     "sl_price": self.stop_price, "limit_price": self.limit_price,
                     "setup": self.setup, "v2_profile": self.profile,
                     "market_regime": self.regime})
        if self.target_prices:
            data["tp1_price"] = self.target_prices[0]
            data["tp_price"] = self.target_prices[-1]
        if len(self.target_prices) > 1:
            data["tp2_price"] = self.target_prices[1]
        for key, value in thaw(self.metadata).items():
            data.setdefault(key, value)
        return data


@dataclass(frozen=True, slots=True)
class RiskDecision:
    status: RiskStatus
    candidate_id: str
    reason: str
    risk_decision_id: str = field(default_factory=lambda: _id("risk"))
    size_usd: float = 0.0
    risk_usd: float = 0.0
    margin_usd: float = 0.0
    leverage: int = 1
    projected_daily_loss_pct: Optional[float] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", enum_value(RiskStatus, self.status, RiskStatus.UNKNOWN))
        object.__setattr__(self, "metadata", freeze(self.metadata or {}))
        _finite("size_usd", self.size_usd, non_negative=True)
        _finite("risk_usd", self.risk_usd, non_negative=True)
        _finite("margin_usd", self.margin_usd, non_negative=True)
        _finite("projected_daily_loss_pct", self.projected_daily_loss_pct, optional=True)
        if int(self.leverage) <= 0:
            raise ValueError("leverage must be positive")

    @property
    def approved(self) -> bool:
        return self.status == RiskStatus.APPROVED

    @classmethod
    def from_legacy(cls, value: Any, *, candidate_id: str = "") -> "RiskDecision":
        data = legacy_dict(value)
        approved = bool(data.get("approved", data.get("ok", False)))
        status = data.get("status") or (RiskStatus.APPROVED if approved else RiskStatus.REJECTED)
        known = {"status", "approved", "ok", "candidate_id", "reason", "risk_decision_id",
                 "size_usd", "risk_usd", "margin_usd", "leverage", "projected_daily_loss_pct"}
        return cls(status=status, candidate_id=data.get("candidate_id") or candidate_id,
                   reason=str(data.get("reason") or ""),
                   risk_decision_id=data.get("risk_decision_id") or _id("risk"),
                   size_usd=float(data.get("size_usd") or 0), risk_usd=float(data.get("risk_usd") or 0),
                   margin_usd=float(data.get("margin_usd") or 0), leverage=int(data.get("leverage") or 1),
                   projected_daily_loss_pct=data.get("projected_daily_loss_pct"),
                   metadata={k: v for k, v in data.items() if k not in known})

    def to_legacy(self) -> dict:
        data = thaw(self.metadata)
        data.update({"status": self.status.value, "approved": self.approved,
                "candidate_id": self.candidate_id, "reason": self.reason,
                "risk_decision_id": self.risk_decision_id, "size_usd": self.size_usd,
                "risk_usd": self.risk_usd, "margin_usd": self.margin_usd,
                "leverage": self.leverage, "projected_daily_loss_pct": self.projected_daily_loss_pct})
        return data


@dataclass(frozen=True, slots=True)
class OrderIntent:
    symbol: str
    direction: Direction
    side: OrderSide
    quantity: float
    order_type: OrderType
    decision_id: str
    risk_decision_id: str
    client_order_id: str = field(default_factory=lambda: _id("CE")[:32])
    limit_price: Optional[float] = None
    reduce_only: bool = False
    leverage: int = 1
    margin_mode: str = "isolated"
    created_ts_ms: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", str(self.symbol).upper())
        object.__setattr__(self, "direction", enum_value(Direction, self.direction))
        object.__setattr__(self, "side", enum_value(OrderSide, self.side))
        object.__setattr__(self, "order_type", enum_value(OrderType, self.order_type, OrderType.MARKET))
        object.__setattr__(self, "metadata", freeze(self.metadata or {}))
        _finite("quantity", self.quantity, positive=True)
        if self.limit_price is not None:
            _finite("limit_price", self.limit_price, positive=True)
        if int(self.leverage) <= 0:
            raise ValueError("leverage must be positive")
        if self.direction is None or self.side is None:
            raise ValueError("order intent requires direction and side")

    @classmethod
    def from_legacy(cls, value: Any) -> "OrderIntent":
        data = legacy_dict(value)
        direction = enum_value(Direction, data.get("direction"))
        raw_side = data.get("side")
        side = enum_value(OrderSide, raw_side)
        if side is None and direction is not None:
            side = OrderSide.BUY if direction == Direction.LONG else OrderSide.SELL
        known = {"symbol", "inst_id", "direction", "side", "quantity", "size", "order_type",
                 "decision_id", "risk_decision_id", "client_order_id", "limit_price", "price",
                 "reduce_only", "leverage", "margin_mode", "created_ts_ms", "decision_ts_ms"}
        return cls(symbol=data.get("symbol") or str(data.get("inst_id") or "").split("-")[0],
                   direction=direction, side=side, quantity=float(data.get("quantity", data.get("size", 0))),
                   order_type=data.get("order_type") or "MARKET", decision_id=data.get("decision_id") or "",
                   risk_decision_id=data.get("risk_decision_id") or "",
                   client_order_id=data.get("client_order_id") or _id("CE")[:32],
                   limit_price=data.get("limit_price", data.get("price")),
                   reduce_only=bool(data.get("reduce_only", False)), leverage=int(data.get("leverage") or 1),
                   margin_mode=data.get("margin_mode") or "isolated",
                   created_ts_ms=int(data.get("created_ts_ms", data.get("decision_ts_ms", 0)) or 0),
                   metadata={k: v for k, v in data.items() if k not in known})

    def to_legacy(self) -> dict:
        data = thaw(self.metadata)
        data.update({"symbol": self.symbol, "inst_id": f"{self.symbol}-USDT",
                "direction": self.direction.value, "side": self.side.value.lower(),
                "size": self.quantity, "quantity": self.quantity,
                "order_type": self.order_type.value.lower(), "decision_id": self.decision_id,
                "risk_decision_id": self.risk_decision_id, "client_order_id": self.client_order_id,
                "price": self.limit_price, "limit_price": self.limit_price,
                "reduce_only": self.reduce_only, "leverage": self.leverage,
                "margin_mode": self.margin_mode, "created_ts_ms": self.created_ts_ms})
        return data


@dataclass(frozen=True, slots=True)
class Fill:
    order_id: str
    client_order_id: str
    symbol: str
    quantity: float
    price: float
    ts_ms: int
    liquidity_role: LiquidityRole = LiquidityRole.UNKNOWN
    fee: float = 0.0
    fill_id: str = field(default_factory=lambda: _id("fill"))
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", str(self.symbol).upper())
        object.__setattr__(self, "liquidity_role", enum_value(LiquidityRole, self.liquidity_role, LiquidityRole.UNKNOWN))
        object.__setattr__(self, "metadata", freeze(self.metadata or {}))
        _finite("quantity", self.quantity, positive=True)
        _finite("price", self.price, positive=True)
        _finite("fee", self.fee)

    @classmethod
    def from_legacy(cls, value: Any, **context: Any) -> "Fill":
        data = {**context, **legacy_dict(value)}
        known = {"order_id", "client_order_id", "symbol", "quantity", "size", "price", "fill_price",
                 "ts_ms", "timestamp_ms", "liquidity_role", "fee", "fill_id"}
        return cls(order_id=str(data.get("order_id") or ""), client_order_id=str(data.get("client_order_id") or ""),
                   symbol=data.get("symbol") or "", quantity=float(data.get("quantity", data.get("size", 0))),
                   price=float(data.get("price", data.get("fill_price", 0))),
                   ts_ms=int(data.get("ts_ms", data.get("timestamp_ms", 0)) or 0),
                   liquidity_role=data.get("liquidity_role") or "unknown", fee=float(data.get("fee") or 0),
                   fill_id=data.get("fill_id") or _id("fill"),
                   metadata={k: v for k, v in data.items() if k not in known})

    def to_legacy(self) -> dict:
        data = thaw(self.metadata)
        data.update({"fill_id": self.fill_id, "order_id": self.order_id,
                "client_order_id": self.client_order_id, "symbol": self.symbol,
                "quantity": self.quantity, "price": self.price, "ts_ms": self.ts_ms,
                "liquidity_role": self.liquidity_role.value, "fee": self.fee})
        return data


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    position_id: str
    symbol: str
    direction: Direction
    status: PositionStatus
    quantity: float
    entry_price: float
    mark_price: float
    opened_ts_ms: int
    size_usd: float = 0.0
    margin_usd: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    fees: float = 0.0
    funding: float = 0.0
    stop_price: Optional[float] = None
    target_prices: tuple[float, ...] = ()
    closed_ts_ms: Optional[int] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", str(self.symbol).upper())
        object.__setattr__(self, "direction", enum_value(Direction, self.direction))
        object.__setattr__(self, "status", enum_value(PositionStatus, self.status, PositionStatus.OPEN))
        object.__setattr__(self, "target_prices", tuple(float(v) for v in self.target_prices))
        object.__setattr__(self, "metadata", freeze(self.metadata or {}))
        if self.direction is None:
            raise ValueError("position snapshot requires direction")
        _finite("quantity", self.quantity, non_negative=True)
        _finite("entry_price", self.entry_price, positive=True)
        _finite("mark_price", self.mark_price, positive=True)
        _finite("size_usd", self.size_usd, non_negative=True)
        _finite("margin_usd", self.margin_usd, non_negative=True)
        for name in ("realized_pnl", "unrealized_pnl", "fees", "funding"):
            _finite(name, getattr(self, name))
        if self.stop_price is not None:
            _finite("stop_price", self.stop_price, positive=True)
        for target in self.target_prices:
            _finite("target_price", target, positive=True)

    @classmethod
    def from_legacy(cls, value: Any) -> "PositionSnapshot":
        data = legacy_dict(value)
        entry = float(data.get("entry_price") or 0)
        quantity = data.get("quantity", data.get("size_contracts"))
        if quantity is None:
            quantity = float(data.get("size_usd") or 0) / entry if entry else 0
        targets = [data.get("tp1_price"), data.get("tp2_price"), data.get("tp_price")]
        known = {"id", "position_id", "symbol", "direction", "status", "quantity", "size_contracts",
                 "entry_price", "mark_price", "opened_ts_ms", "entry_ts_ms", "size_usd", "margin",
                 "margin_usd", "pnl", "realized_pnl", "unrealized_pnl", "fees", "funding_paid", "funding",
                 "sl_price", "stop_price", "tp1_price", "tp2_price", "tp_price", "closed_ts_ms"}
        return cls(position_id=str(data.get("position_id") or data.get("id") or _id("pos")),
                   symbol=data.get("symbol") or "", direction=data.get("direction"), status=data.get("status") or "OPEN",
                   quantity=float(quantity or 0), entry_price=entry,
                   mark_price=float(data.get("mark_price") or entry),
                   opened_ts_ms=int(data.get("opened_ts_ms", data.get("entry_ts_ms", 0)) or 0),
                   size_usd=float(data.get("size_usd") or 0),
                   margin_usd=float(data.get("margin_usd", data.get("margin", 0)) or 0),
                   realized_pnl=float(data.get("realized_pnl", data.get("pnl", 0)) or 0),
                   unrealized_pnl=float(data.get("unrealized_pnl") or 0), fees=float(data.get("fees") or 0),
                   funding=float(data.get("funding", data.get("funding_paid", 0)) or 0),
                   stop_price=data.get("stop_price", data.get("sl_price")),
                   target_prices=tuple(float(v) for v in targets if v is not None),
                   closed_ts_ms=data.get("closed_ts_ms"),
                   metadata={k: v for k, v in data.items() if k not in known})

    def to_legacy(self) -> dict:
        data = thaw(self.metadata)
        data.update({"position_id": self.position_id, "id": self.position_id,
                "symbol": self.symbol, "direction": self.direction.value, "status": self.status.value,
                "quantity": self.quantity, "entry_price": self.entry_price, "mark_price": self.mark_price,
                "opened_ts_ms": self.opened_ts_ms, "size_usd": self.size_usd,
                "margin_usd": self.margin_usd, "margin": self.margin_usd,
                "realized_pnl": self.realized_pnl, "unrealized_pnl": self.unrealized_pnl,
                "fees": self.fees, "funding": self.funding, "funding_paid": self.funding,
                "sl_price": self.stop_price, "closed_ts_ms": self.closed_ts_ms})
        if self.target_prices:
            data["tp1_price"] = self.target_prices[0]
            data["tp_price"] = self.target_prices[-1]
        if len(self.target_prices) > 1:
            data["tp2_price"] = self.target_prices[1]
        return data

    def marked(self, price: float, unrealized_pnl: float) -> "PositionSnapshot":
        return replace(self, mark_price=float(price), unrealized_pnl=float(unrealized_pnl))
