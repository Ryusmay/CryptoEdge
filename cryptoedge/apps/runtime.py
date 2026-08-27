from cryptoedge.services.decision_pipeline import DecisionPipeline
from cryptoedge.market_data import LegacyMarketDataAdapter
from cryptoedge.execution import LegacyExecutionAdapter, PaperExecutionAdapter
from cryptoedge.portfolio import PortfolioManager
from cryptoedge.risk import LegacyRiskAdapter
from cryptoedge.telemetry import HealthRegistry


def create_runtime_pipeline(*, market_data, strategy, risk, execution=None,
                            portfolio=None, health=None, order_factory=None) -> DecisionPipeline:
    return DecisionPipeline(market_data, strategy, risk, execution, portfolio, health, order_factory)


def _paper_mark_price(rt):
    """Mark price dla PAPER: ostatnia znana cena, ale nigdy przeterminowana.

    Zamkniecie po starej cenie to ten sam blad, przed ktorym runtime broni
    sie juz przy close_all i kill switchu - port nie moze byc furtka obok.
    """
    stale_after = float(getattr(rt, "PRICE_MAP_STALE_AFTER_S", 120.0))

    def mark(symbol):
        try:
            if float(rt.price_map_age_s()) > stale_after:
                return None
        except Exception:
            return None
        book = getattr(rt, "last_price_map", None) or {}
        sym = str(symbol or "").upper()
        return book.get(sym) or book.get(sym.replace("-USDT", ""))

    return mark


def attach_runtime_modules(rt):
    """Komponuje moduły wokół istniejącego runtime bez zmiany jego stanu."""
    health = HealthRegistry()
    health.report("market_data", "warming")
    health.report("strategy", "warming")
    health.report("risk", "healthy")
    import config
    from ..domain import trading_mode
    paper = trading_mode.is_paper(config)
    live_enabled = trading_mode.live_execution_armed(config)
    health.report("execution", "healthy" if paper else (
        "healthy" if live_enabled and getattr(rt, "executor", None) else "degraded"),
        "PAPER_LOCAL" if paper else "LIVE")
    health.report("portfolio", "healthy")
    rt.module_health = health
    rt.market_data_port = LegacyMarketDataAdapter(rt.feeder)
    rt.risk_port = LegacyRiskAdapter(rt.risk)
    rt.portfolio_manager = PortfolioManager(rt.trader)
    if live_enabled and getattr(rt, "executor", None) is not None:
        rt.execution_port = LegacyExecutionAdapter(
            rt.executor, getattr(rt, "reconciler", None), enabled=live_enabled,
            live=live_enabled,
        )
    elif paper:
        # PAPER tez ma venue - lokalna ksiege - i tez zasluguje na port.
        # Bez tego "execution_port is None" znaczylo raz PAPER, a raz
        # "LIVE bez executora", czyli dwie rozne rzeczy pod jednym stanem.
        rt.execution_port = PaperExecutionAdapter(
            rt.trader, mark_price=_paper_mark_price(rt),
        )
    else:
        # LIVE bez executora: brak venue to brak portu, nie port papierowy.
        rt.execution_port = None
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
