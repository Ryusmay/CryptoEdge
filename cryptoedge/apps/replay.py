from cryptoedge.replay.service import ReplayDecisionService
from cryptoedge.services.decision_pipeline import DecisionPipeline


def create_replay_pipeline(*, market_data, strategy, risk, execution=None,
                           portfolio=None, health=None, order_factory=None) -> ReplayDecisionService:
    pipeline = DecisionPipeline(market_data, strategy, risk, execution, portfolio, health, order_factory)
    return ReplayDecisionService(pipeline)
