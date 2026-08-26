from __future__ import annotations
import time
from typing import Any, Dict, List, Optional
import config
from backfill_queue import BACKFILL
from market_store import STORE
from rate_limiter import PUBLIC_BUCKET


def _store_snap() -> Dict[str, Any]:
    fn = getattr(STORE, "snapshot", None)
    if not callable(fn):
        return {}
    try:
        out = fn()
        return out if isinstance(out, dict) else {}
    except Exception:
        return {}


def warmup_applies() -> bool:
    """Warmup tylko gdy V2 faktycznie generuje sygnaly."""
    return bool(getattr(config, "WARMUP_ENABLED", True)) and config.daytrading_v2_active()


def _frame_fresh(bar: str, data: dict) -> bool:
    if not (data or {}).get("closes"):
        return False
    if bar not in ("4H", "1H", "15m"):
        return True
    try:
        from daytrading_engine_v2 import klines_stale_reason
        return klines_stale_reason({bar: data}) is None
    except Exception:
        return True


class WarmupController:
    def __init__(self):
        self.started_at: float = 0.0
        self.active: bool = False
        self.ready: bool = False
        self.phase: str = "idle"
        self.candidates: List[str] = []
        self.last_log: str = ""
        self._seeded: bool = False
        self._seed_stats: Dict[str, int] = {}

    def start(self) -> None:
        self.started_at = time.monotonic()
        self.active = True
        self.ready = False
        self.phase = "seed"
        self.candidates = []
        self._seeded = False
        self._seed_stats = {}
        BACKFILL.q.clear()
        BACKFILL.seen.clear()
        BACKFILL.done = 0
        BACKFILL.failed = 0

    def elapsed(self) -> float:
        if not self.started_at:
            return 0.0
        return time.monotonic() - self.started_at

    def _phase_for(self, t: float) -> str:
        duration = float(getattr(config, "WARMUP_SECONDS", 90))
        if t < 10:
            return "seed"
        if t < 20:
            return "snapshot"
        if t < max(25.0, duration - 5.0):
            return "backfill"
        return "gate"

    def _need(self) -> Dict[str, int]:
        return {
            "need_1h": int(getattr(config, "WARMUP_NEED_1H", 80)),
            "need_15m": int(getattr(config, "WARMUP_NEED_15M", 40)),
            "need_4h": int(getattr(config, "WARMUP_NEED_4H", 40)),
        }

    def status(self) -> Dict[str, Any]:
        need = int(getattr(config, "WARMUP_MIN_PAIRS_READY", 20))
        nkw = self._need()
        return {
            "active": self.active,
            "ready": self.ready,
            "phase": self.phase,
            "elapsed_s": round(self.elapsed(), 1),
            "candidates": len(self.candidates),
            "ready_pairs": STORE.ready_count(self.candidates, **nkw),
            "need_pairs": need,
            "backfill_pending": BACKFILL.pending(),
            "backfill_done": BACKFILL.done,
            "bucket": round(PUBLIC_BUCKET.level(), 2),
            "ws": _store_snap().get("ws_alive"),
            "seed": dict(self._seed_stats),
        }

    def seed_klines(self, feeder, symbols: List[str]) -> Dict[str, int]:
        """E: STORE z dysku BloFin (0 REST). Dziury idą w BACKFILL → BloFin."""
        if self._seeded:
            return self._seed_stats
        self._seeded = True
        disk_n = self._seed_from_disk(symbols, feeder)
        self._seed_stats = {"disk": disk_n}
        if disk_n:
            print(f"[Warmup] seed disk={disk_n} (BloFin cache)")
        return self._seed_stats

    def _seed_from_disk(self, symbols: List[str], feeder=None) -> int:
        try:
            import disk_cache
        except Exception:
            return 0
        want = {s.upper() for s in symbols}
        n = 0
        cache_dir = getattr(disk_cache, "CACHE_DIR", None)
        if cache_dir is None:
            return 0
        try:
            paths = list(cache_dir.glob("ohlcv_*.json"))
        except Exception:
            return 0
        blofin = getattr(feeder, "blofin", None) if feeder is not None else None
        for path in paths:
            stem = path.stem
            if not stem.startswith("ohlcv_"):
                continue
            parts = stem[6:].rsplit("_", 2)
            if len(parts) != 3:
                continue
            inst, bar, _limit = parts
            if bar not in ("4H", "1H", "15m", "1D"):
                continue
            symbol = inst[:-5].upper() if inst.upper().endswith("-USDT") else inst.upper()
            if symbol not in want:
                continue
            if STORE.candle_count(symbol, bar) > 0:
                continue
            hit = disk_cache.load(stem)
            data = (hit or {}).get("data") if isinstance(hit, dict) else None
            if not isinstance(data, dict) or not _frame_fresh(bar, data):
                continue
            STORE.put_ohlcv(symbol, bar, data)
            ts = float((hit or {}).get("ts") or time.time())
            if blofin is not None and hasattr(blofin, "ohlc_cache"):
                blofin.ohlc_cache[stem] = (ts, data)
            n += 1
        return n

    def enqueue_backfill(self, symbols: List[str], feeder=None) -> None:
        cap = int(getattr(config, "DAYTRADING_V2_MAX_CANDIDATES", 0) or 0)
        self.candidates = list(symbols) if cap <= 0 else list(symbols)[:cap]
        self.seed_klines(feeder, self.candidates)
        lim_4h = int(getattr(config, "WARMUP_CANDLES_4H", 260))
        lim_1h = int(getattr(config, "WARMUP_CANDLES_1H", 260))
        lim_15 = int(getattr(config, "WARMUP_CANDLES_15M", 300))
        need = self._need()
        for s in self.candidates:
            if STORE.candle_count(s, "4H") < need["need_4h"]:
                BACKFILL.enqueue(s, "4H", lim_4h)
            if STORE.candle_count(s, "1H") < need["need_1h"]:
                BACKFILL.enqueue(s, "1H", lim_1h)
            if STORE.candle_count(s, "15m") < need["need_15m"]:
                BACKFILL.enqueue(s, "15m", lim_15)

    def drain(self, feeder) -> int:
        blofin = getattr(feeder, "blofin", None)
        if blofin is None:
            return 0
        if PUBLIC_BUCKET.level() < float(getattr(config, "WARMUP_MIN_BUCKET", 0.35)):
            return 0

        def fetch(symbol, bar, limit):
            return blofin.fetch_klines_ohlcv(symbol, bar=bar, limit=limit)

        return BACKFILL.drain(fetch, STORE.put_ohlcv)

    def tick(self, feeder, coins: List[dict]) -> Dict[str, Any]:
        if not self.active:
            return self.status()
        t = self.elapsed()
        self.phase = self._phase_for(t)
        duration = float(getattr(config, "WARMUP_SECONDS", 90))

        if self.phase in ("seed", "snapshot", "backfill", "gate") and coins and not self.candidates:
            symbols = []
            for c in coins:
                sym = str(c.get("symbol") or "")
                if not sym or sym in (getattr(config, "DAYTRADING_V2_EXCLUDED_SYMBOLS", None) or []):
                    continue
                symbols.append(sym)
            self.enqueue_backfill(symbols, feeder=feeder)

        if self.phase in ("backfill", "gate"):
            self.drain(feeder)

        need = int(getattr(config, "WARMUP_MIN_PAIRS_READY", 20))
        ready_n = STORE.ready_count(self.candidates, **self._need())
        bucket_ok = PUBLIC_BUCKET.level() >= 0.40
        ws = _store_snap()
        feed_ok = bool(ws.get("ws_alive")) or (ws.get("ticker_age_s") is not None and ws["ticker_age_s"] < 30)

        if t >= duration:
            if ready_n >= max(8, need // 2) and feed_ok:
                self.ready = True
                self.active = False
                self.phase = "ready"
            else:
                self.phase = "gate"
                self.ready = False

        if bool(getattr(config, "WARMUP_ALLOW_EARLY_READY", False)):
            if ready_n >= need and feed_ok and bucket_ok and t >= 60:
                self.ready = True
                self.active = False
                self.phase = "ready"

        st = self.status()
        line = (
            f"[Warmup] {st['phase']} {st['elapsed_s']:.0f}s | "
            f"ready {st['ready_pairs']}/{st['need_pairs']} | "
            f"queue {st['backfill_pending']} done {st['backfill_done']} | "
            f"bucket {st['bucket']:.0%}"
        )
        if line != self.last_log:
            print(line)
            self.last_log = line
        return st


WARMUP = WarmupController()
