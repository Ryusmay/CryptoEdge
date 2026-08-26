"""Publiczny, stabilny import aktywnego silnika strategii."""

from daytrading_engine_v2 import DayTradingEngineV2


def create_engine(*args, **kwargs):
    """Późne wiązanie zachowuje mockowanie i hot-swap adaptera legacy."""
    from daytrading_engine_v2 import DayTradingEngineV2 as Engine
    return Engine(*args, **kwargs)


__all__ = ["DayTradingEngineV2", "create_engine"]
