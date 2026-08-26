"""Stable, immutable domain contracts for modular CryptoEdge."""
from .enums import (
    DecisionStatus, Direction, EventType, LiquidityRole, OrderSide, OrderStatus,
    OrderType, PositionStatus, RiskStatus, TradingStatus,
)
from .events import DomainEvent
from .models import (
    EntryCandidate, Fill, MarketSnapshot, OrderIntent, PositionSnapshot,
    RiskDecision, StrategyDecision,
)
from .trading_mode import (
    coerce_paper_flag, is_live, is_paper, live_execution_armed, mode_label,
)

__all__ = [
    "DecisionStatus", "Direction", "DomainEvent", "EntryCandidate", "EventType",
    "Fill", "LiquidityRole", "MarketSnapshot", "OrderIntent", "OrderSide",
    "OrderStatus", "OrderType", "PositionSnapshot", "PositionStatus",
    "RiskDecision", "RiskStatus", "StrategyDecision", "TradingStatus",
    "coerce_paper_flag", "is_live", "is_paper", "live_execution_armed",
    "mode_label",
]

