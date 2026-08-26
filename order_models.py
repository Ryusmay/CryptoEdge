# ============================================================
# Order state machine + clientOrderId
# timeout ≠ failed
# ============================================================

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional, Dict, Any, List


class OrderState(str, Enum):
    CREATED = "CREATED"           # lokalnie utworzone, jeszcze nie wysłane
    SUBMITTING = "SUBMITTING"     # request w locie
    SUBMITTED = "SUBMITTED"       # giełda przyjęła (mamy orderId), stan live/pending
    PARTIAL = "PARTIAL"           # częściowo wypełnione
    FILLED = "FILLED"             # w pełni wypełnione
    CANCELING = "CANCELING"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"         # giełda odrzuciła (znany powód)
    TIMEOUT = "TIMEOUT"           # brak odpowiedzi / timeout sieci – NIE to samo co REJECTED
    UNKNOWN = "UNKNOWN"           # niespójny stan – wymaga reconciliacji
    EXPIRED = "EXPIRED"


# Dozwolone przejścia (uproszczone)
_ALLOWED = {
    OrderState.CREATED: {OrderState.SUBMITTING, OrderState.REJECTED},
    OrderState.SUBMITTING: {
        OrderState.SUBMITTED, OrderState.PARTIAL, OrderState.FILLED,
        OrderState.REJECTED, OrderState.TIMEOUT, OrderState.UNKNOWN,
    },
    OrderState.SUBMITTED: {
        OrderState.PARTIAL, OrderState.FILLED, OrderState.CANCELING,
        OrderState.CANCELED, OrderState.REJECTED, OrderState.EXPIRED, OrderState.UNKNOWN,
    },
    OrderState.PARTIAL: {
        OrderState.FILLED, OrderState.CANCELING, OrderState.CANCELED, OrderState.UNKNOWN,
    },
    OrderState.CANCELING: {
        OrderState.CANCELED, OrderState.FILLED, OrderState.PARTIAL, OrderState.UNKNOWN,
    },
    OrderState.TIMEOUT: {
        OrderState.SUBMITTED, OrderState.PARTIAL, OrderState.FILLED,
        OrderState.CANCELED, OrderState.REJECTED, OrderState.UNKNOWN,
    },
    OrderState.UNKNOWN: {
        OrderState.SUBMITTED, OrderState.PARTIAL, OrderState.FILLED,
        OrderState.CANCELED, OrderState.REJECTED, OrderState.EXPIRED,
    },
    # terminal
    OrderState.FILLED: set(),
    OrderState.CANCELED: set(),
    OrderState.REJECTED: set(),
    OrderState.EXPIRED: set(),
}


def new_client_order_id(prefix: str = "CE") -> str:
    """
    Blofin: max 32 znaków, alfanumeryczne.
    Format: CE + timestamp_ms(base36-ish) + random → <=32
    """
    ts = int(time.time() * 1000)
    rnd = uuid.uuid4().hex[:8]
    cid = f"{prefix}{ts}{rnd}"
    return cid[:32]


@dataclass
class Order:
    client_order_id: str
    symbol: str                         # BTC
    inst_id: str                        # BTC-USDT
    side: str                           # buy | sell
    direction: str                      # LONG | SHORT (logiczny)
    order_type: str = "market"          # market | limit | ioc | fok
    size: float = 0.0                   # kontrakty (żądane)
    price: Optional[float] = None       # limit price
    reduce_only: bool = False
    leverage: int = 10
    margin_mode: str = "cross"
    position_side: str = "net"          # net | long | short

    state: OrderState = OrderState.CREATED
    order_id: Optional[str] = None      # exchange id

    # fill
    filled_size: float = 0.0
    avg_fill_price: Optional[float] = None
    fee: float = 0.0
    liquidity_role: Optional[str] = None  # maker | taker, from actual fill
    decision_ts_ms: Optional[int] = None
    submitted_ts_ms: Optional[int] = None
    accepted_ts_ms: Optional[int] = None
    first_fill_ts_ms: Optional[int] = None
    last_fill_ts_ms: Optional[int] = None
    canceled_ts_ms: Optional[int] = None
    fill_latency_ms: Optional[int] = None
    fill_events: List[Dict[str, Any]] = field(default_factory=list)

    # meta
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_error: Optional[str] = None
    reject_reason: Optional[str] = None
    timeout: bool = False               # True gdy TIMEOUT (do reconciliacji)
    raw_submit: Optional[dict] = None
    raw_status: Optional[dict] = None
    history: List[str] = field(default_factory=list)

    def transition(self, new_state: OrderState, note: str = "") -> bool:
        cur = self.state if isinstance(self.state, OrderState) else OrderState(self.state)
        new = new_state if isinstance(new_state, OrderState) else OrderState(new_state)
        allowed = _ALLOWED.get(cur, set())
        if new == cur:
            return True
        if new not in allowed and cur not in (OrderState.UNKNOWN, OrderState.TIMEOUT):
            # wymuszone przejście do UNKNOWN zamiast crasha
            self.history.append(f"{cur.value}→{new.value} BLOCKED ({note})")
            self.state = OrderState.UNKNOWN
            self.updated_at = time.monotonic()
            return False
        self.history.append(f"{cur.value}→{new.value}" + (f" | {note}" if note else ""))
        self.state = new
        self.updated_at = time.monotonic()
        if new == OrderState.TIMEOUT:
            self.timeout = True
        return True

    def record_fill(self, quantity: float, price: float, *, fee: float = 0.0,
                    liquidity_role: str = None, ts_ms: int = None) -> None:
        """Append an immutable partial/full-fill event and update VWAP fields."""
        event_ts = int(ts_ms or time.time() * 1000)
        qty = max(0.0, float(quantity or 0))
        px = max(0.0, float(price or 0))
        if qty <= 0 or px <= 0:
            return
        old_qty = max(0.0, float(self.filled_size or 0))
        old_value = old_qty * float(self.avg_fill_price or 0)
        self.filled_size = old_qty + qty
        self.avg_fill_price = (old_value + qty * px) / self.filled_size
        self.fee += float(fee or 0)
        role = str(liquidity_role or "").lower()
        if role in ("maker", "taker"):
            self.liquidity_role = role if self.liquidity_role in (None, role) else "mixed"
        self.first_fill_ts_ms = self.first_fill_ts_ms or event_ts
        self.last_fill_ts_ms = event_ts
        base_ts = self.decision_ts_ms or self.submitted_ts_ms
        self.fill_latency_ms = event_ts - int(base_ts) if base_ts is not None else None
        self.fill_events.append({
            "ts_ms": event_ts, "quantity": qty, "price": px,
            "fee": float(fee or 0), "liquidity_role": role or None,
        })

    @property
    def is_terminal(self) -> bool:
        return self.state in (
            OrderState.FILLED, OrderState.CANCELED,
            OrderState.REJECTED, OrderState.EXPIRED,
        )

    @property
    def is_working(self) -> bool:
        return self.state in (
            OrderState.SUBMITTING, OrderState.SUBMITTED,
            OrderState.PARTIAL, OrderState.CANCELING,
            OrderState.TIMEOUT, OrderState.UNKNOWN,
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["state"] = self.state.value if isinstance(self.state, OrderState) else str(self.state)
        return d


# Mapowanie stanów Blofin → nasze
BLOFIN_STATE_MAP = {
    "live": OrderState.SUBMITTED,
    "partially_filled": OrderState.PARTIAL,
    "partial": OrderState.PARTIAL,
    "filled": OrderState.FILLED,
    "canceled": OrderState.CANCELED,
    "cancelled": OrderState.CANCELED,
    "rejected": OrderState.REJECTED,
    "expired": OrderState.EXPIRED,
}


def map_blofin_state(raw: str) -> OrderState:
    return BLOFIN_STATE_MAP.get((raw or "").lower().strip(), OrderState.UNKNOWN)
