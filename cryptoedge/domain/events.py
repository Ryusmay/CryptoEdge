"""Immutable traceable events emitted between CryptoEdge modules."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional
from uuid import uuid4

from ._compat import enum_value, freeze, thaw
from .enums import EventType


@dataclass(frozen=True, slots=True)
class DomainEvent:
    event_type: EventType
    ts_ms: int
    source: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: f"evt_{uuid4().hex}")
    session_id: Optional[str] = None
    symbol: Optional[str] = None
    snapshot_id: Optional[str] = None
    decision_id: Optional[str] = None
    risk_decision_id: Optional[str] = None
    order_id: Optional[str] = None
    position_id: Optional[str] = None
    strategy_version: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_type", enum_value(EventType, self.event_type))
        object.__setattr__(self, "ts_ms", int(self.ts_ms))
        object.__setattr__(self, "symbol", str(self.symbol).upper() if self.symbol else None)
        object.__setattr__(self, "payload", freeze(self.payload or {}))
        if self.event_type is None:
            raise ValueError("domain event requires a known event_type")

    @classmethod
    def from_legacy(cls, value: Mapping[str, Any]) -> "DomainEvent":
        data = dict(value)
        known = {"event_type", "type", "ts_ms", "timestamp_ms", "source", "payload", "event_id",
                 "session_id", "symbol", "snapshot_id", "decision_id", "risk_decision_id",
                 "order_id", "position_id", "strategy_version"}
        payload = dict(data.get("payload") or {})
        payload.update({k: v for k, v in data.items() if k not in known})
        return cls(event_type=data.get("event_type") or data.get("type"),
                   ts_ms=data.get("ts_ms", data.get("timestamp_ms", 0)) or 0,
                   source=data.get("source") or "legacy", payload=payload,
                   event_id=data.get("event_id") or f"evt_{uuid4().hex}",
                   session_id=data.get("session_id"), symbol=data.get("symbol"),
                   snapshot_id=data.get("snapshot_id"), decision_id=data.get("decision_id"),
                   risk_decision_id=data.get("risk_decision_id"), order_id=data.get("order_id"),
                   position_id=data.get("position_id"), strategy_version=data.get("strategy_version"))

    def to_legacy(self) -> dict:
        return {"event_type": self.event_type.value, "ts_ms": self.ts_ms,
                "source": self.source, "payload": thaw(self.payload), "event_id": self.event_id,
                "session_id": self.session_id, "symbol": self.symbol, "snapshot_id": self.snapshot_id,
                "decision_id": self.decision_id, "risk_decision_id": self.risk_decision_id,
                "order_id": self.order_id, "position_id": self.position_id,
                "strategy_version": self.strategy_version}
