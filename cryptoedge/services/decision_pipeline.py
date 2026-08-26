from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PipelineResult:
    snapshot: Any
    decision: Any
    risk: Any = None
    order: Any = None
    status: str = "ANALYZED"


class DecisionPipeline:
    """Jedyny przepływ decyzji używany przez runtime oraz replay."""

    def __init__(self, market_data: Any, strategy: Any, risk: Any,
                 execution: Any | None = None, portfolio: Any | None = None,
                 health: Any | None = None, order_factory: Any | None = None):
        self.market_data = market_data
        self.strategy = strategy
        self.risk = risk
        self.execution = execution
        self.portfolio = portfolio
        self.health = health
        self.order_factory = order_factory

    def analyze(self, symbol: str, *, decision_ts_ms: int | None = None) -> PipelineResult:
        snapshot = self.market_data.snapshot(symbol, decision_ts_ms=decision_ts_ms)
        decision = self.strategy.evaluate(snapshot)
        if self.health:
            self.health.report("strategy", "healthy")
        return PipelineResult(snapshot=snapshot, decision=decision)

    def process(self, symbol: str, *, decision_ts_ms: int | None = None,
                trading_enabled: bool = False) -> PipelineResult:
        analyzed = self.analyze(symbol, decision_ts_ms=decision_ts_ms)
        if not trading_enabled:
            return analyzed
        direction = (analyzed.decision.get("direction") if isinstance(analyzed.decision, dict)
                     else getattr(analyzed.decision, "direction", None))
        if str(direction or "").upper() not in ("LONG", "SHORT"):
            return PipelineResult(analyzed.snapshot, analyzed.decision, status="NO_TRADE")
        positions = self.portfolio.positions() if self.portfolio else ()
        risk = self.risk.assess(analyzed.decision, positions=positions)
        approved = risk.get("approved") if isinstance(risk, dict) else getattr(risk, "approved", False)
        if not approved:
            return PipelineResult(analyzed.snapshot, analyzed.decision, risk=risk, status="RISK_REJECTED")
        if self.execution is None or self.order_factory is None:
            return PipelineResult(analyzed.snapshot, analyzed.decision, risk=risk, status="APPROVED")
        # Jednostka quantity (contracts) zależy od registry instrumentu. Nie
        # wolno przekazać size_usd bezpośrednio do portu execution.
        command = self.order_factory(analyzed.decision, risk)
        order = self.execution.submit(command)
        return PipelineResult(analyzed.snapshot, analyzed.decision, risk=risk, order=order, status="SUBMITTED")
