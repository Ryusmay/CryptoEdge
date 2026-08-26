from cryptoedge.services.decision_pipeline import DecisionPipeline
from cryptoedge.market_data import LegacyMarketDataAdapter
from cryptoedge.execution import LegacyExecutionAdapter
from cryptoedge.portfolio import PortfolioManager
from cryptoedge.risk import LegacyRiskAdapter
from cryptoedge.telemetry import HealthRegistry


def create_runtime_pipeline(*, market_data, strategy, risk, execution=None,
                            portfolio=None, health=None, order_factory=None) -> DecisionPipeline:
    return DecisionPipeline(market_data, strategy, risk, execution, portfolio, health, order_factory)


def attach_runtime_modules(rt):
    """Komponuje moduły wokół istniejącego runtime bez zmiany jego stanu."""
    health = HealthRegistry()
    health.report("market_data", "warming")
    health.report("strategy", "warming")
    health.report("risk", "healthy")
    import config
    paper = getattr(config, "PAPER_TRADING", True)
    if isinstance(paper, str):
        paper = paper.strip().lower() in ("1", "true", "yes", "on", "demo", "paper")
    live_enabled = (not bool(paper)) and bool(getattr(config, "LIVE_EXECUTION_ENABLED", False))
    health.report("execution", "healthy" if paper else (
        "healthy" if live_enabled and getattr(rt, "executor", None) else "degraded"),
        "PAPER_LOCAL" if paper else "LIVE")
    health.report("portfolio", "healthy")
    rt.module_health = health
    rt.market_data_port = LegacyMarketDataAdapter(rt.feeder)
    rt.risk_port = LegacyRiskAdapter(rt.risk)
    rt.portfolio_manager = PortfolioManager(rt.trader)
    rt.execution_port = (LegacyExecutionAdapter(
        rt.executor, getattr(rt, "reconciler", None), enabled=live_enabled,
        live=live_enabled,
    ) if live_enabled and getattr(rt, "executor", None) is not None else None)
    rt.execution_mode = "PAPER" if paper else "LIVE"
    return rt


def refresh_runtime_health(rt):
    registry = getattr(rt, "module_health", None)
    if registry is None:
        return {}
    coins = list(getattr(rt, "last_coins", None) or [])
    signals = list(getattr(rt, "last_signals", None) or [])
    loading = bool(getattr(rt, "analysis_loading", False))
    # Petla glowna. Bez tego /health mowilo "healthy" przez 7,5 h po tym, jak
    # silnik przestal wykonywac cykle (25.08.2026) - kazdy modul raportowal
    # ostatni znany stan, bo nikt nie pytal, kiedy ten stan powstal.
    try:
        alive = rt.liveness()
    except Exception as exc:
        registry.report("runtime_loop", "degraded", f"liveness_failed: {str(exc)[:120]}")
    else:
        loop_status = {
            "ok": "healthy", "off": "healthy", "starting": "warming",
            "degraded": "degraded", "frozen": "failed",
        }.get(alive.get("state"), "degraded")
        registry.report(
            "runtime_loop", loop_status,
            f"state={alive.get('state')} cycle={alive.get('cycle')} "
            f"cycle_age_s={alive.get('cycle_age_s')} "
            f"price_map_age_s={alive.get('price_map_age_s')}",
        )
    registry.report("market_data", "healthy" if coins else "warming",
                    f"universe={len(coins)}")
    registry.report("strategy", "warming" if loading else (
        "healthy" if signals or not getattr(rt, "engine_enabled", False) else "degraded"),
        f"decisions={len(signals)}")
    risk = getattr(rt, "risk", None)
    risk_state = "halted" if getattr(risk, "is_halted", False) else (
        "degraded" if getattr(risk, "paused", False) else "healthy")
    registry.report("risk", risk_state, str(getattr(risk, "halt_reason", "") or ""))
    reconciler = getattr(rt, "reconciler", None)
    if reconciler is not None:
        report = getattr(reconciler, "last_report", None) or {}
        registry.report("reconciliation", "healthy" if report.get("in_sync") is True else "degraded",
                        "not_checked" if not report else "")
    registry.report("portfolio", "healthy", f"positions={len(getattr(rt.trader, 'positions', ()) or ())}")
    mode = getattr(rt, "execution_mode", "UNKNOWN")
    if mode == "PAPER":
        registry.report("execution", "healthy", "PAPER_LOCAL")
    else:
        port = getattr(rt, "execution_port", None)
        ready = bool(port and getattr(getattr(port, "executor", None), "ready", lambda: False)())
        registry.report("execution", "healthy" if ready else "degraded", "LIVE")
    return registry.snapshot()
