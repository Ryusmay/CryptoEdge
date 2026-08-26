"""Ring log 429 / WS / REST — DESK czyta snapshot, nie zgaduje."""
from __future__ import annotations

import time
from collections import deque
from typing import List

_EVENTS: deque = deque(maxlen=40)


def note(src: str, msg: str, exc: BaseException | None = None) -> str:
    extra = f": {type(exc).__name__}: {exc}" if exc is not None else ""
    line = f"[{src}] {msg}{extra}"
    print(line, flush=True)
    rec = {"src": str(src), "msg": f"{msg}{extra}", "ts": time.time()}
    _EVENTS.appendleft(rec)
    try:
        from runtime import BotRuntime
        rt = BotRuntime.get()
        buf = getattr(rt, "feed_events", None)
        if buf is not None:
            buf.appendleft(rec)
            rt.last_error = line[:240]
    except Exception:
        pass
    return line


def recent(n: int = 12) -> List[dict]:
    return list(_EVENTS)[: max(1, int(n))]
