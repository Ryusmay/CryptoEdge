from __future__ import annotations
from collections import deque
from typing import Callable, Deque, Optional, Tuple
import config

Job = Tuple[str, str, int]


class BackfillQueue:
    def __init__(self, max_per_drain: int = 8):
        self.q: Deque[Job] = deque()
        self.seen = set()
        self.max_per_drain = max_per_drain
        self.done = 0
        self.failed = 0

    def enqueue(self, symbol: str, bar: str, limit: int) -> None:
        key = (symbol, bar, int(limit))
        if key in self.seen:
            return
        self.seen.add(key)
        self.q.append(key)

    def pending(self) -> int:
        return len(self.q)

    def drain(self, fetch_fn: Callable, store_put: Callable, max_jobs: Optional[int] = None) -> int:
        n = int(max_jobs if max_jobs is not None else getattr(config, "BACKFILL_MAX_JOBS_PER_DRAIN", self.max_per_drain))
        ran = 0
        while self.q and ran < max(1, n):
            symbol, bar, limit = self.q.popleft()
            try:
                frame = fetch_fn(symbol, bar, limit)
                if frame and (frame.get("closes") or frame.get("close")):
                    store_put(symbol, bar, frame)
                    self.done += 1
                else:
                    self.failed += 1
            except Exception:
                self.failed += 1
            ran += 1
        return ran


BACKFILL = BackfillQueue()
