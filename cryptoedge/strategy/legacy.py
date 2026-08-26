from __future__ import annotations

from typing import Any
from threading import RLock

from ..domain._compat import thaw


class LegacyV2StrategyAdapter:
    """Wspólny adapter DayTradingEngineV2 dla runtime i replay."""

    def __init__(self, engine: Any):
        self.engine = engine
        self._evaluate_lock = RLock()

    def evaluate(self, snapshot: Any):
        valid = getattr(snapshot, "validate_closed_bars", lambda: (True, "OK"))()
        if not valid[0]:
            return {"symbol": snapshot.symbol, "direction": "NEUTRAL", "reject_reason": valid[1]}
        ticker = dict(getattr(snapshot, "ticker", {}) or {})
        ticker.setdefault("symbol", getattr(snapshot, "symbol", ""))
        frames = getattr(snapshot, "frames", {}) or {}
        original_fetch = getattr(self.engine, "_fetch", None)
        if original_fetch is None:
            return self.engine.evaluate(ticker, now_ts=getattr(snapshot, "decision_ts_ms", 0) / 1000)

        # MarketSnapshot zamraża ramki (MappingProxyType + tuple), a kod legacy
        # zakłada zwykłe dicty i listy. Bez rozmrożenia asdict() w
        # V2MarketSnapshot.to_dict() wywala się na
        # "cannot pickle 'mappingproxy' object", co kładło CAŁY replay.
        # Rozmrażamy leniwie i najwyżej raz na ramkę w obrębie jednego
        # evaluate() - konwersja jest w gorącej pętli, więc nie może się
        # powtarzać przy każdym _fetch.
        thawed: dict = {}

        def snapshot_fetch(symbol, bar, limit):
            key = bar if bar in frames else str(bar).lower()
            if key not in thawed:
                captured = frames.get(bar)
                if captured is None:
                    captured = frames.get(str(bar).lower())
                thawed[key] = thaw(captured) if captured is not None else None
            captured = thawed.get(key)
            # Runtime może dostarczyć lekki snapshot tickera, podczas gdy
            # replay zawsze dostarcza komplet historycznych ramek. Brak
            # ramki nie może zmieniać istniejącej semantyki runtime.
            return captured if captured is not None else original_fetch(symbol, bar, limit)

        # Adapter legacy wymaga czasowej podmiany źródła. Lock jest twardą
        # barierą przed cross-symbol data bleed do czasu natywnego API
        # evaluate(snapshot) w silniku.
        with self._evaluate_lock:
            self.engine._fetch = snapshot_fetch
            try:
                return self.engine.evaluate(ticker, now_ts=getattr(snapshot, "decision_ts_ms", 0) / 1000)
            finally:
                self.engine._fetch = original_fetch
