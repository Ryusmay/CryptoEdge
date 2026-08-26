"""Small immutable/legacy conversion helpers; no application dependencies."""
from __future__ import annotations

from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


def freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    if isinstance(value, set):
        return frozenset(freeze(v) for v in value)
    return value


def thaw(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): thaw(v) for k, v in value.items()}
    if isinstance(value, (tuple, list, frozenset, set)):
        return [thaw(v) for v in value]
    return value


def enum_value(enum_type, value: Any, default=None):
    if value is None or value == "":
        return default
    if isinstance(value, enum_type):
        return value
    raw = str(getattr(value, "value", value))
    for candidate in (raw, raw.upper(), raw.lower()):
        try:
            return enum_type(candidate)
        except ValueError:
            pass
    return default


def legacy_dict(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return dict(to_dict())
    data = getattr(value, "__dict__", None)
    return dict(data) if isinstance(data, dict) else {}

