"""Dependency-free domain enumerations shared by runtime and replay."""
from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class Direction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class DecisionStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    WAIT = "WAIT"
    REJECTED = "REJECTED"
    NO_TRADE = "NO_TRADE"


class RiskStatus(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REDUCE_ONLY = "REDUCE_ONLY"
    HALTED = "HALTED"
    UNKNOWN = "UNKNOWN"


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    IOC = "IOC"
    FOK = "FOK"
    STOP = "STOP"


class OrderStatus(StrEnum):
    CREATED = "CREATED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELING = "CANCELING"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    TIMEOUT = "TIMEOUT"
    UNKNOWN = "UNKNOWN"
    EXPIRED = "EXPIRED"


class PositionStatus(StrEnum):
    OPEN = "OPEN"
    REDUCING = "REDUCING"
    CLOSED = "CLOSED"


class LiquidityRole(StrEnum):
    MAKER = "maker"
    TAKER = "taker"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class TradingStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REDUCE_ONLY = "REDUCE_ONLY"
    HALTED = "HALTED"
    KILL_SWITCH = "KILL_SWITCH"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class EventType(StrEnum):
    SNAPSHOT = "SNAPSHOT"
    DECISION = "DECISION"
    RISK_DECISION = "RISK_DECISION"
    ORDER = "ORDER"
    FILL = "FILL"
    POSITION = "POSITION"
    HEALTH = "HEALTH"

