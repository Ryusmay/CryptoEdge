class ReplayDecisionService:
    """Replay zmienia zegar i dane, nie pipeline decyzyjny."""

    def __init__(self, pipeline):
        self.pipeline = pipeline

    def step(self, symbol: str, decision_ts_ms: int, *, execute: bool = True):
        return self.pipeline.process(symbol, decision_ts_ms=decision_ts_ms,
                                     trading_enabled=execute)
