from __future__ import annotations
import time
from typing import Any, Dict, List
import config
from backfill_queue import BACKFILL
from market_store import STORE
from rate_limiter import PUBLIC_BUCKET


class WarmupController:
    def __init__(self):
        self.started_at: float = 0.0
        self.active: bool = False
        self.ready: bool = False
        self.phase: str = "idle"
        self.candidates: List[str] = []
        self.last_log: str = ""

    def start(self) -> None:
        self.started_at = time.monotonic()
        self.active = True
        self.ready = False
        self.phase = "seed"
        self.candidates = []
        BACKFILL.q.clear()
        BACKFILL.seen.clear()
        BACKFILL.done = 0
        BACKFILL.failed = 0

    def elapsed(self) -> float:
        if not self.started_at:
            return 0.0
        return time.monotonic() - self.started_at

    def _phase_for(self, t: float) -> str:
        if t < 15:
            return "seed"
        if t < 45:
            return "snapshot"
        if t < 285:
            return "backfill"
        return "gate"

    def status(self) -> Dict[str, Any]:
        need = int(getattr(config, "WARMUP_MIN_PAIRS_READY", 20))
        return {
            "active": self.active,
            "ready": self.ready,
            "phase": self.phase,
            "elapsed_s": round(self.elapsed(), 1),
            "candidates": len(self.candidates),
            "ready_pairs": STORE.ready_count(self.candidates),
            "need_pairs": need,
            "backfill_pending": BACKFILL.pending(),
            "backfill_done": BACKFILL.done,
            "bucket": round(PUBLIC_BUCKET.level(), 2),
            "ws": STORE.snapshot().get("ws_alive"),
        }

    def enqueue_backfill(self, symbols: List[str]) -> None:
        self.candidates = list(symbols)[: int(getattr(config, "DAYTRADING_V2_MAX_CANDIDATES", 30))]
        lim_1h = int(getattr(config, "WARMUP_CANDLES_1H", 180))
        lim_15 = int(getattr(config, "WARMUP_CANDLES_15M", 120))
        need_1h = int(getattr(config, "WARMUP_NEED_1H", 80))
        need_15 = int(getattr(config, "WARMUP_NEED_15M", 40))
        for s in self.candidates:
            if STORE.candle_count(s, "1H") < need_1h:
                BACKFILL.enqueue(s, "1H", lim_1h)
        for s in self.candidates:
            if STORE.candle_count(s, "15m") < need_15:
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
        duration = float(getattr(config, "WARMUP_SECONDS", 300))

        if self.phase in ("snapshot", "backfill", "gate") and coins and not self.candidates:
            symbols = []
            for c in coins:
                sym = str(c.get("symbol") or "")
                if not sym or sym in (getattr(config, "DAYTRADING_V2_EXCLUDED_SYMBOLS", None) or []):
                    continue
                symbols.append(sym)
            self.enqueue_backfill(symbols)

        if self.phase in ("backfill", "gate"):
            self.drain(feeder)

        need = int(getattr(config, "WARMUP_MIN_PAIRS_READY", 20))
        ready_n = STORE.ready_count(self.candidates)
        bucket_ok = PUBLIC_BUCKET.level() >= 0.40
        ws = STORE.snapshot()
        feed_ok = bool(ws.get("ws_alive")) or (ws.get("ticker_age_s") is not None and ws["ticker_age_s"] < 30)

        if t >= duration:
            if ready_n >= max(8, need // 2) and feed_ok:
                self.ready = True
                self.active = False
                self.phase = "ready"
            else:
                self.phase = "gate"
                self.ready = False

        # 21.08.2026: wczesniejsze wyjscie (gotowe juz po t>=60s, jesli
        # ready_n/feed/bucket sa OK) domyslnie WYLACZONE - user chce, zeby
        # rozruch trwal pelne WARMUP_SECONDS (300s), tak jak nazwa i log
        # startowy ("start 5 min") sugeruja, zeby Blofin nie dostal
        # nawalu zapytan zaraz po cold-starcie. Mechanizm zostaje w kodzie
        # (nie kasujemy), tylko za flaga, dla kogos kto jednak wolalby
        # szybszy start kosztem mniejszego marginesu bezpieczenstwa.
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
