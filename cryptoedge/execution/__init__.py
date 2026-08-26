"""CryptoEdge execution boundary."""

from .ledger import Fill, FillAggregate, FillLedger
from .bridge import domain_fill_to_ledger
from .legacy import ExecutionDisabled, LegacyExecutionAdapter
from .lifecycle import InvalidTransition, OrderLifecycle, OrderStatus
from .ports import (
    CancelOrder, ExecutionPort, ExecutionResult, ReconciliationResult,
    ReducePosition, SubmitOrder,
)

__all__ = [
    "CancelOrder", "ExecutionPort", "ExecutionResult", "Fill", "FillAggregate",
    "FillLedger", "InvalidTransition", "LegacyExecutionAdapter", "ExecutionDisabled",
    "domain_fill_to_ledger", "OrderLifecycle",
    "OrderStatus", "ReconciliationResult", "ReducePosition", "SubmitOrder",
]
