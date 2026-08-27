from decimal import Decimal

from cryptoedge.services.decision_pipeline import DecisionPipeline
from cryptoedge.market_data import LegacyMarketDataAdapter
from cryptoedge.execution import (
    LegacyExecutionAdapter, PaperExecutionAdapter, SubmitOrder,
)
from cryptoedge.portfolio import PortfolioManager
from cryptoedge.risk import LegacyRiskAdapter
from cryptoedge.telemetry import HealthRegistry


def create_runtime_pipeline(*, market_data, strategy, risk, execution=None,
                            portfolio=None, health=None, order_factory=None) -> DecisionPipeline:
    return DecisionPipeline(market_data, strategy, risk, execution, portfolio, health, order_factory)


def open_entry(rt, trader, signal):
    """Wejscie PAPER przez ExecutionPort, z jawnym powrotem do starej sciezki.

    Dwa zabezpieczenia, oba celowe:

    1. **Tylko adapter PAPER.** W trybie LIVE `rt.execution_port` to
       `LegacyExecutionAdapter`, ktorego `submit` sklada PRAWDZIWE zlecenie
       na gieldzie. Wejscie papierowe nie moze tam trafic nigdy, wiec
       warunkiem jest konkretny typ portu, a nie samo "port istnieje".
    2. **Fallback jest strukturalny, nie ponawiajacy.** Decyzja "port czy
       wywolanie bezposrednie" zapada ZANIM cokolwiek zostanie wyslane.
       Nie ma `except` wokol `submit`, ktory probowalby otworzyc pozycje
       drugi raz: gdyby `submit` wywalil sie po tym, jak `open_position`
       juz sie wykonalo, ponowienie zrobiloby DWA wejscia zamiast jednego.

    Powod istnienia fallbacku: `attach_runtime_modules` jest w `app.py`
    opakowane w `try/except` ("Migracja modulowa nie moze wylaczyc
    zarzadzania istniejaca pozycja"). Skoro portowi wolno nie powstac,
    handel nie moze od niego zalezec. Druga sciezka znika przy usuwaniu
    legacy (etap 7).

    Rownowaznosc obu drog jest dowiedziona w
    `tests/test_paper_port_equivalence.py` na calym korpusie entry_gate.
    """
    port = getattr(rt, "execution_port", None)
    if not isinstance(port, PaperExecutionAdapter):
        return trader.open_position(signal)

    symbol = str(signal.get("symbol") or "").upper()
    result = port.submit(SubmitOrder(
        # Kolejka PAPER jest kluczowana symbolem, wiec id per symbol
        # wystarcza i nie udaje idempotency, ktorego ta ksiega nie ma.
        client_order_id=f"PAPER-{symbol}",
        symbol=symbol,
        side="buy" if str(signal.get("direction") or "").upper() == "LONG" else "sell",
        # PAPER liczy rozmiar sam, z sygnalu. To pole nie ma tu jeszcze
        # znaczenia i nabierze go dopiero przy rozdzieleniu decision/order.
        quantity=Decimal("1"),
        metadata={"signal": signal},
    ))
    # FILLED daje pozycje; ACCEPTED to zaparkowany limit, a wtedy stara
    # sciezka tez zwracala None.
    return result.raw if result.state == "FILLED" else None


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
        # Bez wlasnego zrodla ceny: cene zamkniecia ustala wolajacy przez
        # close_policy i podaje ja w ReducePosition.
        rt.execution_port = PaperExecutionAdapter(rt.trader)
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
