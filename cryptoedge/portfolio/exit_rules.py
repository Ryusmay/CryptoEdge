"""Wyjscia sterowane cena dla sciezki NIE-V2 - czysty reduktor.

Blizniak `v2_trade_lifecycle.decide_v2_lifecycle`. V2 mial swoj czysty
reduktor od dawna; sciezka legacy (trend, daytrading, reversal) miala te sama
decyzje rozsypana po metodzie `Position.check_tp_sl()`, ktora dodatkowo
**zapisywala** `self._partial_stage` w srodku zapytania.

Tutaj etap partiala jest wartoscia zwracana, nie efektem ubocznym. Adapter
w `Position` decyduje, kiedy ja zapisac. To ten sam wzorzec, ktory w ryzyku
usunal cztery bledy: funkcja odpowiada, wywolujacy zapisuje.

Kolejnosc decyzji jest kontraktem i jest zachowana co do litery:
partial -> margin call -> trailing -> stop loss -> take profit.
Partial bije margin call - swieca, ktora przecina TP1 i jednoczesnie
wywraca depozyt, konczy sie partialem, nie likwidacja.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

V2_ENGINES = ("daytrading_v2", "daytradingv2")


def _config(cfg=None):
    if cfg is not None:
        return cfg
    import config as _cfg
    return _cfg


@dataclass(frozen=True)
class PriceExitView:
    """Stan pozycji widziany przez reguly wyjscia. Bez metod, bez I/O."""
    direction: str
    engine: str = "trend"
    pnl: float = 0.0
    pnl_pct: float = 0.0
    margin: float = 0.0
    margin_call_price: Optional[float] = None
    sl_price: Optional[float] = None
    tp_price: Optional[float] = None
    tp1_price: Optional[float] = None
    tp2_price: Optional[float] = None
    partial_tp_price: Optional[float] = None
    tp_plan: object = None
    trailing_active: bool = False
    trailing_stop_price: Optional[float] = None
    partial_tp1_done: bool = False
    partial_tp2_done: bool = False
    partial_taken: bool = False
    breakeven_active: bool = False
    ride_trend: bool = True
    hard_tp_after_stop: bool = False

    @property
    def is_long(self) -> bool:
        return self.direction == "LONG"

    @property
    def is_v2(self) -> bool:
        return str(self.engine or "").lower() in V2_ENGINES


@dataclass(frozen=True)
class PriceExitDecision:
    """`partial_stage` zamiast zapisu na pozycji - patrz naglowek."""
    action: Optional[str] = None
    partial_stage: int = 0


def _crossed(is_long: bool, price: float, level: float, downside: bool) -> bool:
    """Czy cena przebila poziom. `downside` = poziom pod pozycja dla LONG."""
    if is_long:
        return price <= level if downside else price >= level
    return price >= level if downside else price <= level


def _hard_tp_disabled(view: PriceExitView, cfg) -> bool:
    """Czy twardy TP jest wylaczony.

    Uwaga na domyslna wartosc: `ride_trend` domyslnie True, wiec brak tego
    pola oznacza "jedziemy z trendem", czyli TP wylaczony. Daytrading ma TP
    zawsze wlaczony, a po STOP silnika honorujemy ustawiony TP.
    """
    disabled = bool(getattr(cfg, "NO_HARD_TP", True)) or bool(view.ride_trend)
    if str(view.engine or "") == "daytrading":
        disabled = False
    if view.hard_tp_after_stop:
        disabled = False
    return disabled


def _partial_decision(view: PriceExitView, price: float, cfg) -> Optional[PriceExitDecision]:
    if not bool(getattr(cfg, "PARTIAL_TP_ENABLED", True)):
        return None
    plan = view.tp_plan
    if not view.partial_tp1_done:
        hit = False
        tp1 = view.tp1_price or view.partial_tp_price
        if tp1 is not None:
            hit = _crossed(view.is_long, price, float(tp1), downside=False)
        if not hit and not plan:
            # Sciezka legacy bez planu R:R - wyzwalacz procentowy na PnL.
            trigger = float(getattr(cfg, "PARTIAL_TP_TRIGGER_PCT", 50.0))
            hit = float(view.pnl_pct or 0) >= trigger
        if hit:
            return PriceExitDecision("partial_tp", 1)
        return None
    # Etap 2 istnieje tylko przy planie R:R.
    if plan and not view.partial_tp2_done and view.tp2_price is not None:
        if _crossed(view.is_long, price, float(view.tp2_price), downside=False):
            return PriceExitDecision("partial_tp", 2)
    return None


def _margin_call(view: PriceExitView, price: float, cfg) -> bool:
    if not bool(getattr(cfg, "MARGIN_CALL_ENABLED", True)):
        return False
    threshold = float(getattr(cfg, "MARGIN_CALL_THRESHOLD", 0.80))
    if view.margin > 0 and (-float(view.pnl or 0)) >= view.margin * threshold:
        return True
    if view.margin_call_price is None:
        return False
    return _crossed(view.is_long, price, float(view.margin_call_price), downside=True)


def _stop_is_live(view: PriceExitView, cfg) -> bool:
    """V2 bez SL od wejscia uzbraja stop dopiero po partialu, trailingu albo BE."""
    if not view.is_v2:
        return True
    if bool(getattr(cfg, "DAYTRADING_V2_ENTRY_SL", False)):
        return True
    return bool(view.partial_taken or view.trailing_active or view.breakeven_active)


def decide_price_exit(view: PriceExitView, price, cfg=None) -> PriceExitDecision:
    """Jedna decyzja o wyjsciu sterowanym cena. Nic nie zapisuje."""
    cfg = _config(cfg)
    try:
        price = float(price)
    except (TypeError, ValueError):
        return PriceExitDecision()
    if price != price or price in (float("inf"), float("-inf")) or price <= 0:
        return PriceExitDecision()

    partial = _partial_decision(view, price, cfg)
    if partial is not None:
        return partial

    if _margin_call(view, price, cfg):
        return PriceExitDecision("margin_call")

    if view.trailing_active and view.trailing_stop_price is not None:
        if _crossed(view.is_long, price, float(view.trailing_stop_price), downside=True):
            return PriceExitDecision("trailing_stop")

    if _stop_is_live(view, cfg) and view.sl_price is not None:
        if _crossed(view.is_long, price, float(view.sl_price), downside=True):
            return PriceExitDecision("stop_loss")

    if not _hard_tp_disabled(view, cfg) and view.tp_price:
        if _crossed(view.is_long, price, float(view.tp_price), downside=False):
            return PriceExitDecision("take_profit")

    return PriceExitDecision()
