"""Jedyny bufor danych rynkowych dla V2. REST/WS tylko uzupelniaja ten slownik."""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional


class MarketStore:
    def __init__(self):
        self._lock = threading.Lock()
        self.instruments: List[dict] = []
        self.instruments_ts: float = 0.0
        self.tickers: Dict[str, dict] = {}
        self.tickers_ts: float = 0.0
        self.ohlcv: Dict[str, Dict[str, dict]] = {}
        self.ohlcv_ts: Dict[str, Dict[str, float]] = {}
        self.ws_alive: bool = False
        self.ws_last_ts: float = 0.0

    def set_tickers(self, tickers: Dict[str, dict], from_ws: bool = False) -> None:
        with self._lock:
            self.tickers.update(tickers or {})
            self.tickers_ts = time.time()
            if from_ws:
                self.ws_alive = True
                self.ws_last_ts = time.time()

    def mark_ws(self, alive: bool) -> None:
        with self._lock:
            self.ws_alive = bool(alive)
            if alive:
                self.ws_last_ts = time.time()

    def put_ohlcv(self, symbol: str, tf: str, frame: dict) -> None:
        if not frame:
            return
        with self._lock:
            self.ohlcv.setdefault(symbol, {})[tf] = frame
            self.ohlcv_ts.setdefault(symbol, {})[tf] = time.time()

    def get_ohlcv(self, symbol: str, tf: str) -> Optional[dict]:
        with self._lock:
            return (self.ohlcv.get(symbol) or {}).get(tf)

    def candle_count(self, symbol: str, tf: str) -> int:
        frame = self.get_ohlcv(symbol, tf) or {}
        return len(frame.get("closes") or [])

    def has_pair_ready(self, symbol: str, need_1h: int = 80, need_15m: int = 40) -> bool:
        return self.candle_count(symbol, "1H") >= need_1h and self.candle_count(symbol, "15m") >= need_15m

    def ready_count(self, symbols: List[str], need_1h: int = 80, need_15m: int = 40) -> int:
        return sum(1 for s in symbols if self.has_pair_ready(s, need_1h, need_15m))

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "instruments": len(self.instruments),
                "tickers": len(self.tickers),
                "ws_alive": self.ws_alive,
                "ws_age_s": (time.time() - self.ws_last_ts) if self.ws_last_ts else None,
                "ticker_age_s": (time.time() - self.tickers_ts) if self.tickers_ts else None,
            }


STORE = MarketStore()
