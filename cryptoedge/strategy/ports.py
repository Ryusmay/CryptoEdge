from typing import Any, Protocol


class StrategyPort(Protocol):
    """Strategia jest czystą funkcją snapshot -> decyzja."""

    def evaluate(self, snapshot: Any) -> Any: ...
