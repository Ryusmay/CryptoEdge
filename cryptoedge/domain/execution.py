"""Ekonomiczne zalozenia wykonania, ktore decyzja niesie ze soba.

Po co: model decyzyjny (V2, przyszly Brain, LightGBM) liczy oczekiwany wynik
przy KONKRETNYM sposobie wykonania - limit czy market, maker czy taker, jaki
spread i poslizg. Jesli adapter wykonawczy robi co innego, oczekiwany wynik
jest liczony dla innej transakcji niz ta, ktora powstaje. Tak bylo w
jev-trader (prompt modelu: "IOC market, crosses the spread"; wykonanie:
post-only limit wewnatrz spreadu) i tak jest dzis w CryptoEdge:
`expected_net_r` liczy maker+taker dla wejscia limitem, a paper ksieguje to
samo wejscie jako taker (paper_trader.Position: fill_kind "limit" nie jest
w zbiorze maker). Ten kontrakt pozwala te dwie strony porownac jawnie.

Kontrakt NIE zna gieldy: nie ma tu BloFin, instId ani typow zlecen venue.
Brak wartosci to `None` ("nie wiadomo"), nigdy zero - zero kosztu to
pomiar, brak danych to nie pomiar.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from ._compat import enum_value, freeze, legacy_dict, thaw
from .enums import LiquidityRole, OrderType


def _optional_non_negative(name: str, value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"{name} must be finite")
    if number < 0:
        raise ValueError(f"{name} cannot be negative")
    return number


_MAKER_FILL_KINDS = frozenset({"maker", "resting_limit", "limit_maker"})


@dataclass(frozen=True, slots=True)
class ExecutionAssumptions:
    order_type: OrderType = OrderType.MARKET
    post_only: bool = False
    entry_role: LiquidityRole = LiquidityRole.UNKNOWN
    exit_role: LiquidityRole = LiquidityRole.UNKNOWN
    maker_fee_rate: Optional[float] = None
    taker_fee_rate: Optional[float] = None
    spread_frac: Optional[float] = None
    slippage_round_trip_frac: Optional[float] = None
    latency_ms: Optional[float] = None
    fill_probability: Optional[float] = None
    ttl_ms: Optional[int] = None
    source: str = "unknown"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "order_type", enum_value(OrderType, self.order_type, OrderType.MARKET))
        object.__setattr__(self, "entry_role", enum_value(LiquidityRole, self.entry_role, LiquidityRole.UNKNOWN))
        object.__setattr__(self, "exit_role", enum_value(LiquidityRole, self.exit_role, LiquidityRole.UNKNOWN))
        object.__setattr__(self, "post_only", bool(self.post_only))
        object.__setattr__(self, "metadata", freeze(self.metadata or {}))
        for name in ("maker_fee_rate", "taker_fee_rate", "spread_frac",
                     "slippage_round_trip_frac", "latency_ms"):
            object.__setattr__(self, name, _optional_non_negative(name, getattr(self, name)))
        probability = _optional_non_negative("fill_probability", self.fill_probability)
        if probability is not None and probability > 1:
            raise ValueError("fill_probability must be within [0, 1]")
        object.__setattr__(self, "fill_probability", probability)
        if self.ttl_ms is not None:
            object.__setattr__(self, "ttl_ms", int(_optional_non_negative("ttl_ms", self.ttl_ms)))
        if self.post_only and self.order_type != OrderType.LIMIT:
            raise ValueError("post_only requires a LIMIT order")
        if self.post_only and self.entry_role == LiquidityRole.TAKER:
            raise ValueError("post_only order cannot assume a taker entry")

    def _fee_for(self, role: LiquidityRole) -> Optional[float]:
        if role == LiquidityRole.MAKER:
            return self.maker_fee_rate
        if role == LiquidityRole.TAKER:
            return self.taker_fee_rate
        return None

    @property
    def fee_round_trip_frac(self) -> Optional[float]:
        """Oplata za wejscie + wyjscie jako ulamek notional; None gdy rola
        albo stawka nie jest znana."""
        entry = self._fee_for(self.entry_role)
        exit_ = self._fee_for(self.exit_role)
        if entry is None or exit_ is None:
            return None
        return entry + exit_

    @classmethod
    def from_signal(cls, value: Any, *, maker_fee_rate: Optional[float] = None,
                    taker_fee_rate: Optional[float] = None,
                    source: str = "signal") -> "ExecutionAssumptions":
        """Zalozenia, przy ktorych liczono sygnal V2.

        Ta sama regula co w `expected_net_r`: sygnal z `limit_price` to
        wejscie limitem (maker), bez niego - market (taker). Wyjscie zawsze
        taker, jak w replay i paper. Stawki podaje wolajacy (domena nie
        czyta config).
        """
        data = legacy_dict(value)
        limit = data.get("limit_price") is not None
        return cls(
            order_type=OrderType.LIMIT if limit else OrderType.MARKET,
            entry_role=LiquidityRole.MAKER if limit else LiquidityRole.TAKER,
            exit_role=LiquidityRole.TAKER,
            maker_fee_rate=maker_fee_rate, taker_fee_rate=taker_fee_rate,
            slippage_round_trip_frac=data.get("slip_rt"),
            fill_probability=data.get("fill_probability"),
            source=source,
        )

    @classmethod
    def from_fill_kind(cls, fill_kind: Any, *, maker_fee_rate: Optional[float] = None,
                       taker_fee_rate: Optional[float] = None,
                       source: str = "paper") -> "ExecutionAssumptions":
        """Jak ksieguje wejscie paper (`paper_trader.Position`): maker tylko
        dla jawnych rodzajow maker, wszystko inne - taker."""
        kind = str(fill_kind or "market").lower()
        maker = kind in _MAKER_FILL_KINDS
        return cls(
            order_type=OrderType.LIMIT if (maker or kind == "limit") else OrderType.MARKET,
            entry_role=LiquidityRole.MAKER if maker else LiquidityRole.TAKER,
            exit_role=LiquidityRole.TAKER,
            maker_fee_rate=maker_fee_rate, taker_fee_rate=taker_fee_rate,
            source=source, metadata={"fill_kind": kind},
        )

    def to_legacy(self) -> dict:
        data = thaw(self.metadata)
        data.update({
            "order_type": self.order_type.value, "post_only": self.post_only,
            "entry_role": self.entry_role.value, "exit_role": self.exit_role.value,
            "maker_fee_rate": self.maker_fee_rate, "taker_fee_rate": self.taker_fee_rate,
            "fee_round_trip_frac": self.fee_round_trip_frac,
            "spread_frac": self.spread_frac,
            "slippage_round_trip_frac": self.slippage_round_trip_frac,
            "latency_ms": self.latency_ms, "fill_probability": self.fill_probability,
            "ttl_ms": self.ttl_ms, "source": self.source,
        })
        return data


def execution_mismatches(assumed: ExecutionAssumptions,
                         realized: ExecutionAssumptions) -> tuple[str, ...]:
    """Roznice istotne ekonomicznie miedzy tym, co zalozyla decyzja, a tym,
    co zrobil (albo zrobi) adapter. Pola nieznane po ktorejkolwiek stronie
    sa pomijane - brak informacji to nie rozbieznosc i nie zgodnosc."""
    out: list[str] = []
    if assumed.order_type != realized.order_type:
        out.append(f"ORDER_TYPE {assumed.order_type.value}!={realized.order_type.value}")
    for name in ("entry_role", "exit_role"):
        a, r = getattr(assumed, name), getattr(realized, name)
        if LiquidityRole.UNKNOWN in (a, r):
            continue
        if a != r:
            out.append(f"{name.upper()} {a.value}!={r.value}")
    a_fee, r_fee = assumed.fee_round_trip_frac, realized.fee_round_trip_frac
    if a_fee is not None and r_fee is not None and abs(a_fee - r_fee) > 1e-12:
        out.append(f"FEE_ROUND_TRIP {a_fee:.6f}!={r_fee:.6f}")
    return tuple(out)
