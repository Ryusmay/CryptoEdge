from __future__ import annotations


class TradingService:
    def __init__(self, pipeline):
        self.pipeline = pipeline

    def process(self, symbols, *, decision_ts_ms=None, enabled=True):
        return [self.pipeline.process(symbol, decision_ts_ms=decision_ts_ms,
                                      trading_enabled=enabled) for symbol in symbols]
