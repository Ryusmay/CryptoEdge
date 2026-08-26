from __future__ import annotations

import time
from typing import Any

from cryptoedge.domain import MarketSnapshot


class LegacyMarketDataAdapter:
    """Adapter istniejącego feedera bez przecieku jego API do strategii."""

    _TIMEFRAMES = ("1D", "4H", "1H", "15m", "5m")

    def __init__(self, feeder: Any):
        self.feeder = feeder

    def universe(self):
        for name in ("get_market_data", "fetch_market_data", "get_coins"):
            fn = getattr(self.feeder, name, None)
            if callable(fn):
                return list(fn() or [])
        return []

    def snapshot(self, symbol: str, *, decision_ts_ms: int | None = None):
        now_ms = int(decision_ts_ms or time.time() * 1000)
        source = getattr(self.feeder, "blofin", self.feeder)
        frames = {}
        fetch = getattr(source, "fetch_klines_ohlcv", None)
        if callable(fetch):
            for tf in self._TIMEFRAMES:
                frames[tf] = fetch(symbol, interval=tf, limit=300) or {}
        ticker = {}
        for name in ("fetch_ticker", "get_ticker"):
            fn = getattr(source, name, None)
            if callable(fn):
                ticker = fn(symbol) or {}
                break
        return MarketSnapshot(
            symbol=str(symbol).upper(), event_ts_ms=now_ms,
            decision_ts_ms=now_ms, frames=frames, ticker=ticker,
            source="runtime",
        )

    def health(self):
        ws = getattr(self.feeder, "blofin_ws", None)
        connected = bool(getattr(ws, "is_connected", lambda: False)()) if ws else None
        return {"module": "market_data", "status": "healthy" if connected is True else "degraded",
                "connected": connected}


class RuntimeEngineMarketDataAdapter:
    """Lekki port dla skanu runtime, bez ponownego pobierania tych samych świec.

    Strategia legacy zachowuje własny cache/fetch. Port niesie ticker oraz
    wspólny zegar decyzji; adapter strategii pobiera brakujące ramki z
    istniejącego feedu. Replay wypełnia ramki historyczne w całości.
    """

    def __init__(self):
        self._tickers: dict[str, dict] = {}

    def update(self, ticker: dict) -> None:
        symbol = str((ticker or {}).get("symbol") or "").upper()
        if symbol:
            self._tickers[symbol] = dict(ticker)

    def snapshot(self, symbol: str, *, decision_ts_ms: int | None = None):
        now_ms = int(decision_ts_ms or time.time() * 1000)
        normalized = str(symbol).upper()
        return MarketSnapshot(symbol=normalized, event_ts_ms=now_ms,
                              decision_ts_ms=now_ms, frames={},
                              ticker=self._tickers.get(normalized, {}),
                              source="runtime")
