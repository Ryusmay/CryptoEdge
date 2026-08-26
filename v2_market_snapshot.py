"""Wspolny, as-of kontrakt danych dla runtime i replay V2."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional


_TF_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000,
    "1d": 86_400_000,
}


def timeframe_ms(value: str) -> int:
    return int(_TF_MS.get(str(value or "").lower(), 0))


def last_bar_closed(frame: dict, timeframe: str, decision_ts_ms: int) -> bool:
    """True only when the complete last bar was available at decision time.

    Exchange candle timestamps normally denote bar *open*, therefore comparing
    that timestamp with decision time is insufficient and leaks an open candle.
    Explicit close timestamps/closed flags take precedence when supplied.
    """
    if not frame:
        return True
    flags = frame.get("closed") or frame.get("is_closed")
    if isinstance(flags, list) and flags and not bool(flags[-1]):
        return False
    close_ts = frame.get("close_timestamps") or frame.get("close_ts")
    if isinstance(close_ts, list) and close_ts:
        return int(close_ts[-1]) <= int(decision_ts_ms)
    timestamps = list(frame.get("timestamps") or [])
    width = timeframe_ms(timeframe)
    if not timestamps or width <= 0:
        return False
    return int(timestamps[-1]) + width <= int(decision_ts_ms)


@dataclass(frozen=True)
class V2MarketSnapshot:
    symbol: str
    event_ts_ms: int
    decision_ts_ms: int
    frames: Dict[str, dict] = field(default_factory=dict)
    ticker: dict = field(default_factory=dict)
    order_book: dict = field(default_factory=dict)
    funding: dict = field(default_factory=dict)
    source: str = "runtime"

    def validate_closed_bars(self) -> tuple[bool, str]:
        for tf in ("5m", "15m", "1H", "4H", "1D"):
            frame = self.frames.get(tf) or self.frames.get(tf.lower()) or {}
            if frame and not last_bar_closed(frame, tf, self.decision_ts_ms):
                return False, f"LOOKAHEAD_{tf}"
        return True, "OK"

    def to_dict(self) -> dict:
        return asdict(self)


class EventClock:
    """Ten sam monotoniczny zegar decyzja -> submit -> fill dla live/replay."""
    def __init__(self, latency_ms: int = 250):
        self.latency_ms = max(0, int(latency_ms))

    def submitted_at(self, decision_ts_ms: int) -> int:
        return int(decision_ts_ms)

    def earliest_fill_at(self, decision_ts_ms: int) -> int:
        return int(decision_ts_ms) + self.latency_ms

    def fill_allowed(self, decision_ts_ms: int, fill_ts_ms: int) -> bool:
        return int(fill_ts_ms) >= self.earliest_fill_at(decision_ts_ms)
