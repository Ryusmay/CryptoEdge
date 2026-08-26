from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from threading import RLock


@dataclass(frozen=True)
class ModuleHealth:
    module: str
    status: str
    updated_at: float
    message: str = ""
    errors: int = 0


class HealthRegistry:
    def __init__(self):
        self._items: dict[str, ModuleHealth] = {}
        self._lock = RLock()

    def report(self, module: str, status: str, message: str = "", errors: int = 0):
        item = ModuleHealth(module, status, time.time(), message, int(errors))
        with self._lock:
            self._items[module] = item
        return item

    def snapshot(self) -> dict:
        with self._lock:
            items = {name: asdict(item) for name, item in self._items.items()}
        overall = "healthy"
        if any(v["status"] in ("failed", "halted") for v in items.values()):
            overall = "failed"
        elif any(v["status"] in ("degraded", "warming") for v in items.values()):
            overall = "degraded"
        return {"status": overall, "modules": items}
