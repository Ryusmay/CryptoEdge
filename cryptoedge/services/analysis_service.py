from __future__ import annotations


class AnalysisService:
    def __init__(self, pipeline):
        self.pipeline = pipeline

    def scan(self, symbols, *, decision_ts_ms=None):
        return [self.pipeline.process(symbol, decision_ts_ms=decision_ts_ms,
                                      trading_enabled=False) for symbol in symbols]
