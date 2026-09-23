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


class DecisionValidityStatus(StrEnum):
    """Czy decyzja wciaz opisuje rynek, na ktorym ma zostac wykonana.

    VALID - mozna wykonac; LATE - minal valid_until; STALE - rynek sie
    zmienil (nowsza wersja stanu albo dryf ceny ponad limit);
    INVALIDATED - cena przeszla poziom uniewazniajacy setup (np. SL).
    """
    VALID = "VALID"
    STALE = "STALE"
    LATE = "LATE"
    INVALIDATED = "INVALIDATED"


class EventType(StrEnum):
    SNAPSHOT = "SNAPSHOT"
    DECISION = "DECISION"
    RISK_DECISION = "RISK_DECISION"
    ORDER = "ORDER"
    FILL = "FILL"
    POSITION = "POSITION"
    HEALTH = "HEALTH"
    # Rozszerzenie pod wspolny schemat telemetrii (MIGRATION_PLAN, etap 6).
    # Celowo brak MARKET/FEATURE per tick: 300+ symboli x tick to wolumen,
    # ktory nie powinien isc przez szyne zdarzen; stan rynku = SNAPSHOT.
    ORDER_INTENT = "ORDER_INTENT"
    EXECUTION = "EXECUTION"
    PNL = "PNL"
    STRATEGY_HEALTH = "STRATEGY_HEALTH"
    MODEL = "MODEL"
    SIMULATION = "SIMULATION"

