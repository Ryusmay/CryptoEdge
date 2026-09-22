"""Stable, immutable domain contracts for modular CryptoEdge."""
from .enums import (
    DecisionStatus, DecisionValidityStatus, Direction, EventType, LiquidityRole,
    OrderSide, OrderStatus, OrderType, PositionStatus, RiskStatus, TradingStatus,
)
from .events import DomainEvent
from .execution import ExecutionAssumptions, execution_mismatches
from .validity import (
    DecisionTiming, DecisionValidity, assess_validity, valid_until_after_bars,
)
from .models import (
    EntryCandidate, Fill, MarketSnapshot, OrderIntent, PositionSnapshot,
    RiskDecision, StrategyDecision,
)
from .trading_mode import (
    coerce_paper_flag, is_live, is_paper, live_execution_armed, mode_label,
)

__all__ = [
    "DecisionStatus", "DecisionTiming", "DecisionValidity", "DecisionValidityStatus",
    "Direction", "DomainEvent", "EntryCandidate", "EventType",
    "ExecutionAssumptions", "assess_validity", "execution_mismatches",
    "valid_until_after_bars",
    "Fill", "LiquidityRole", "MarketSnapshot", "OrderIntent", "OrderSide",
    "OrderStatus", "OrderType", "PositionSnapshot", "PositionStatus",
    "RiskDecision", "RiskStatus", "StrategyDecision", "TradingStatus",
    "coerce_paper_flag", "is_live", "is_paper", "live_execution_armed",
    "mode_label",
]

