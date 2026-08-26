from __future__ import annotations

from typing import Any, Sequence


class LegacyRiskAdapter:
    def __init__(self, manager: Any):
        self.manager = manager

    def assess(self, candidate: Any, *, positions: Sequence[Any] = ()):
        signal = candidate.to_legacy() if hasattr(candidate, "to_legacy") else dict(candidate)
        full_positions = list(positions or ())
        signal["_open_positions_ref"] = full_positions
        self.manager._positions_ref = full_positions
        directions = [getattr(p, "direction", None) for p in positions]
        size = self.manager.calculate_position_size(signal)
        signal["_planned_notional"] = float(size or 0.0)
        approved, reason = self.manager.can_open_position(signal, directions)
        if not approved:
            size = 0.0
        return {"approved": bool(approved), "reason": str(reason), "size_usd": float(size or 0.0),
                "candidate": signal}

    def state(self) -> str:
        if getattr(self.manager, "is_halted", False):
            return "HALTED"
        if getattr(self.manager, "paused", False):
            return "REDUCE_ONLY"
        return "ACTIVE"
