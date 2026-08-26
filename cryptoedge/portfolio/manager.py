from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Iterable


@dataclass(frozen=True)
class PortfolioView:
    positions: tuple[Any, ...]
    realized_pnl: float
    unrealized_pnl: float


class PortfolioManager:
    """Jeden właściciel pozycji i księgowości, niezależny od UI."""

    def __init__(self, legacy_trader: Any | None = None):
        self._legacy = legacy_trader
        self._positions: dict[str, Any] = {}
        self._realized = 0.0
        self._lock = RLock()

    def positions(self) -> tuple[Any, ...]:
        if self._legacy is not None:
            return tuple(getattr(self._legacy, "positions", ()) or ())
        with self._lock:
            return tuple(self._positions.values())

    def apply_fill(self, fill: Any) -> None:
        raise TypeError(
            "raw fill is not a position; aggregate it in FillLedger and apply a PositionSnapshot"
        )

    def upsert(self, position: Any) -> None:
        if self._legacy is not None:
            raise RuntimeError("legacy trader remains the position owner during migration")
        position_id = str(getattr(position, "position_id", None) or "")
        if not position_id:
            raise ValueError("position_id is required")
        with self._lock:
            self._positions[position_id] = position

    def remove(self, position_id: str, realized_pnl: float = 0.0) -> None:
        with self._lock:
            self._positions.pop(str(position_id), None)
            self._realized += float(realized_pnl)

    def view(self) -> PortfolioView:
        positions = self.positions()
        unrealized = sum(float(getattr(p, "pnl", 0.0) or 0.0) for p in positions)
        return PortfolioView(positions, self._realized, unrealized)
