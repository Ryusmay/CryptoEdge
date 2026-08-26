from __future__ import annotations

from typing import Any, Mapping


class LegacyConfigProvider:
    """Izoluje globalny moduł config na krawędzi aplikacji."""

    def __init__(self, module: Any):
        self.module = module

    def get(self, name: str, default: Any = None) -> Any:
        return getattr(self.module, name, default)

    def snapshot(self, prefixes=("DAYTRADING_", "RISK_")) -> Mapping[str, Any]:
        return {
            key: value for key, value in vars(self.module).items()
            if key.startswith(tuple(prefixes)) and isinstance(value, (str, int, float, bool, type(None)))
        }
