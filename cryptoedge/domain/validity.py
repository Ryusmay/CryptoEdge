"""Ochrona przed wykonaniem decyzji, ktora stracila waznosc.

Scenariusz: setup wykryty -> analiza trwa -> rynek sie zmienia -> decyzja
dociera za pozno -> system i tak otwiera pozycje. W CryptoEdge odstep miedzy
ocena symbolu a proba wejscia to caly skan universum (sekwencyjnie, REST
w petli), a `decision_fresh` pilnuje tylko "jedna decyzja na swiece 15m",
nie tego, ile czasu minelo od zamkniecia swiecy ani czy cena odjechala.

jev-trader rozwiazuje pokrewny problem inaczej (flaga `busy`: blok, ktory
przyszedl w trakcie decyzji, jest pomijany jako `late`), ale decyzja, ktora
juz trwa, jest wysylana nawet jesli przyszlo kilka blokow - czyli chroni
przed zatorem, nie przed wykonaniem spoznionej decyzji. Tu chodzi o to
drugie: Execution Router pyta `assess_validity` tuz przed wyslaniem.

Czysta funkcja, bez zegara systemowego: `now_ms` podaje wolajacy (runtime
- zegar scienny, replay - zegar zdarzen). Nieznane wejscia (None) pomijaja
dany test i trafiaja do `checks_skipped` - brak danych nie jest "VALID".
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

from ._compat import enum_value
from .enums import DecisionValidityStatus, Direction


def _opt_float(name: str, value: Any, *, positive: bool = False) -> Optional[float]:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    if positive and number <= 0:
        raise ValueError(f"{name} must be positive")
    return number


@dataclass(frozen=True, slots=True)
class DecisionTiming:
    """Co decyzja wie o wlasnej waznosci w chwili powstania."""
    created_ts_ms: int
    valid_until_ms: Optional[int] = None
    market_version: Optional[str] = None
    reference_price: Optional[float] = None
    max_price_drift_frac: Optional[float] = None
    invalidation_price: Optional[float] = None
    direction: Optional[Direction] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_ts_ms", int(self.created_ts_ms))
        if self.valid_until_ms is not None:
            object.__setattr__(self, "valid_until_ms", int(self.valid_until_ms))
            if self.valid_until_ms < self.created_ts_ms:
                raise ValueError("valid_until_ms cannot precede created_ts_ms")
        if self.market_version is not None:
            object.__setattr__(self, "market_version", str(self.market_version))
        object.__setattr__(self, "reference_price", _opt_float("reference_price", self.reference_price, positive=True))
        object.__setattr__(self, "invalidation_price", _opt_float("invalidation_price", self.invalidation_price, positive=True))
        drift = _opt_float("max_price_drift_frac", self.max_price_drift_frac)
        if drift is not None and drift < 0:
            raise ValueError("max_price_drift_frac cannot be negative")
        object.__setattr__(self, "max_price_drift_frac", drift)
        object.__setattr__(self, "direction", enum_value(Direction, self.direction))


@dataclass(frozen=True, slots=True)
class DecisionValidity:
    status: DecisionValidityStatus
    reason: str
    age_ms: int
    price_drift_frac: Optional[float] = None
    checks_skipped: tuple[str, ...] = ()

    @property
    def executable(self) -> bool:
        return self.status == DecisionValidityStatus.VALID

    def to_legacy(self) -> dict:
        return {"validity_status": self.status.value, "validity_reason": self.reason,
                "decision_age_ms": self.age_ms, "price_drift_frac": self.price_drift_frac,
                "validity_checks_skipped": list(self.checks_skipped)}


def valid_until_after_bars(decision_ts_ms: int, bar_ms: int, bars: int = 1) -> int:
    """Waznosc liczona w barach od chwili decyzji (V2: decyzja na zamknieciu
    swiecy 15m, zlecenie limit zyje 1 bar - DAYTRADING_V2_LIMIT_TIMEOUT_15M_BARS)."""
    if bar_ms <= 0 or bars < 0:
        raise ValueError("bar_ms must be positive and bars non-negative")
    return int(decision_ts_ms) + int(bar_ms) * int(bars)


def _crossed_invalidation(timing: DecisionTiming, price: float) -> bool:
    level = timing.invalidation_price
    if level is None or timing.direction is None:
        return False
    if timing.direction == Direction.LONG:
        return price <= level
    return price >= level


def assess_validity(timing: DecisionTiming, *, now_ms: int,
                    current_market_version: Optional[str] = None,
                    current_price: Optional[float] = None) -> DecisionValidity:
    """Kolejnosc: INVALIDATED > LATE > STALE > VALID.

    INVALIDATED jest najmocniejsze: setup juz nie istnieje, niezaleznie od
    czasu. LATE przed STALE, bo termin jest deterministyczny i tani do
    sprawdzenia rowniez w replay.
    """
    now_ms = int(now_ms)
    age_ms = now_ms - timing.created_ts_ms
    skipped: list[str] = []
    price = _opt_float("current_price", current_price, positive=True)

    drift: Optional[float] = None
    if price is not None and timing.reference_price is not None:
        drift = abs(price - timing.reference_price) / timing.reference_price

    if price is None or timing.invalidation_price is None or timing.direction is None:
        skipped.append("invalidation")
    elif _crossed_invalidation(timing, price):
        return DecisionValidity(DecisionValidityStatus.INVALIDATED,
                                f"PRICE_CROSSED_INVALIDATION({timing.invalidation_price})",
                                age_ms, drift, tuple(skipped))

    if age_ms < 0:
        # Zegar wolajacego przed chwila decyzji: blad sklejenia zegarow,
        # nie "bardzo swieza decyzja".
        return DecisionValidity(DecisionValidityStatus.STALE, "CLOCK_BEFORE_DECISION",
                                age_ms, drift, tuple(skipped))

    if timing.valid_until_ms is None:
        skipped.append("valid_until")
    elif now_ms > timing.valid_until_ms:
        return DecisionValidity(DecisionValidityStatus.LATE,
                                f"PAST_VALID_UNTIL(+{now_ms - timing.valid_until_ms}ms)",
                                age_ms, drift, tuple(skipped))

    if timing.market_version is None or current_market_version is None:
        skipped.append("market_version")
    elif str(current_market_version) != timing.market_version:
        return DecisionValidity(DecisionValidityStatus.STALE,
                                f"MARKET_VERSION({timing.market_version}->{current_market_version})",
                                age_ms, drift, tuple(skipped))

    if drift is None or timing.max_price_drift_frac is None:
        skipped.append("price_drift")
    elif drift > timing.max_price_drift_frac:
        return DecisionValidity(DecisionValidityStatus.STALE,
                                f"PRICE_DRIFT({drift:.5f}>{timing.max_price_drift_frac:.5f})",
                                age_ms, drift, tuple(skipped))

    return DecisionValidity(DecisionValidityStatus.VALID, "OK", age_ms, drift, tuple(skipped))
