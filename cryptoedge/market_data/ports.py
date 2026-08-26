from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence


class MarketDataPort(Protocol):
    """Jedyna granica, przez którą usługi pobierają dane rynkowe."""

    def universe(self) -> Sequence[Mapping[str, Any]]: ...
    def snapshot(self, symbol: str, *, decision_ts_ms: int | None = None) -> Any: ...
    def health(self) -> Mapping[str, Any]: ...


class SnapshotFactory(Protocol):
    def build(self, symbol: str, *, decision_ts_ms: int | None = None) -> Any: ...
