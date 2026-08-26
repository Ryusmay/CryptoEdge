# ============================================================
# ETAP 4 — Accounting
# 20. rzeczywiste fee (taker/maker + slippage)
# 21. funding (accrual + settlement window)
# 22. PnL (realized / unrealized, breakdown)
# 23. Decimal w execution (size, price, notional)
# 24. real equity synchronization
# ============================================================

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP, getcontext
from typing import Optional, Dict, Any, Union
import time

import config

getcontext().prec = 28

Number = Union[int, float, str, Decimal]


def D(x: Number, default: str = "0") -> Decimal:
    """Bezpieczna konwersja na Decimal."""
    try:
        value = x if isinstance(x, Decimal) else Decimal(str(x))
        return value if value.is_finite() else Decimal(default)
    except Exception:
        return Decimal(default)


def quantize_dec(value: Decimal, step: Number, mode: str = "down") -> Decimal:
    """Zaokrąglij value do wielokrotności step."""
    step = D(step)
    if step <= 0:
        return value
    n = value / step
    if mode == "up":
        n = n.to_integral_value(rounding=ROUND_HALF_UP)  # approx
        # pure ceil:
        from decimal import ROUND_CEILING
        n = (value / step).to_integral_value(rounding=ROUND_CEILING)
    elif mode == "nearest":
        n = (value / step).to_integral_value(rounding=ROUND_HALF_UP)
    else:
        n = (value / step).to_integral_value(rounding=ROUND_DOWN)
    return n * step


def quantize_price(price: Number, tick: Number) -> Decimal:
    return quantize_dec(D(price), tick, mode="nearest")


def quantize_size(size: Number, lot: Number) -> Decimal:
    return quantize_dec(D(size), lot, mode="down")


# ------------------------------------------------------------------
# 20. Fees
# ------------------------------------------------------------------
def taker_rate() -> Decimal:
    return D(getattr(config, "TAKER_FEE", None) or getattr(config, "COMMISSION_RATE", 0.0006))


def maker_rate() -> Decimal:
    return D(getattr(config, "MAKER_FEE", 0.0002))


def slippage_rate() -> Decimal:
    return D(getattr(config, "SLIPPAGE", 0.0008))


def fee_usd(notional: Number, rate: Number = None, side: str = "taker") -> Decimal:
    """Opłata w USDT od notional."""
    n = D(notional).copy_abs()
    if rate is None:
        rate = taker_rate() if side == "taker" else maker_rate()
    return (n * D(rate)).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)


def entry_exit_costs(
    notional: Number,
    entry_side: str = "taker",
    exit_side: str = "taker",
    include_slippage: bool = True,
    slip_frac: Number = None,
) -> dict:
    """
    Koszty round-trip jako ułamek notional + kwoty USD.
    slip_frac = całkowity slip RT (jak replay). None → config SLIPPAGE (legacy, 1×).
    """
    n = D(notional).copy_abs()
    e_rate = taker_rate() if entry_side == "taker" else maker_rate()
    x_rate = taker_rate() if exit_side == "taker" else maker_rate()
    if slip_frac is not None:
        slip = D(slip_frac)
    elif include_slippage:
        slip = slippage_rate()
    else:
        slip = Decimal("0")
    frac = e_rate + x_rate + slip
    return {
        "fee_entry": fee_usd(n, e_rate),
        "fee_exit": fee_usd(n, x_rate),
        "slippage": (n * slip).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP),
        "total_usd": (n * frac).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP),
        "total_frac": frac,
        "entry_rate": e_rate,
        "exit_rate": x_rate,
        "slippage_rate": slip,
    }


# ------------------------------------------------------------------
# 21. Funding
# ------------------------------------------------------------------
def funding_payment(
    notional: Number,
    funding_rate: Number,
    direction: str,
    hours_held: Number = None,
    period_hours: Number = 8,
) -> Decimal:
    """
    Funding: LONG płaci gdy rate > 0; SHORT otrzymuje (i odwrotnie).
    Jeśli hours_held podane – proporcjonalnie do okna period_hours.
    Zwraca kwotę do ODJĘCIA od PnL (dodatnia = koszt dla pozycji).
    """
    n = D(notional).copy_abs()
    fr = D(funding_rate)
    if n == 0 or fr == 0:
        return Decimal("0")
    # pełne settlement
    raw = n * fr
    if hours_held is not None:
        raw = raw * (D(hours_held) / D(period_hours))
    # LONG: dodatni funding = płaci; SHORT: dodatni funding = dostaje
    if direction.upper() == "LONG":
        cost = raw
    else:
        cost = -raw
    return cost.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)


def accrue_funding_since(
    notional: Number,
    funding_rate: Number,
    direction: str,
    seconds_since: float,
    period_hours: float = 8.0,
) -> Decimal:
    """Proporcjonalny accrual od ostatniego ticka."""
    hours = D(seconds_since) / Decimal("3600")
    return funding_payment(notional, funding_rate, direction, hours_held=hours, period_hours=period_hours)


# ------------------------------------------------------------------
# 22. PnL
# ------------------------------------------------------------------
def price_change_frac(entry: Number, exit: Number, direction: str) -> Decimal:
    e, x = D(entry), D(exit)
    if e <= 0:
        return Decimal("0")
    if direction.upper() == "LONG":
        return (x - e) / e
    return (e - x) / e


def unrealized_pnl(
    notional: Number,
    entry: Number,
    mark: Number,
    direction: str,
    leverage: Number = 1,
    funding_paid: Number = 0,
) -> dict:
    """
    Unrealized bez fee wyjścia (fee dopiero przy close).
    pnl_usd = notional * price_change
    pnl_pct_on_margin = price_change * leverage * 100
    """
    n = D(notional).copy_abs()
    ch = price_change_frac(entry, mark, direction)
    gross = n * ch
    net = gross - D(funding_paid)
    lev = D(leverage) if D(leverage) > 0 else Decimal("1")
    margin = n / lev if lev else n
    return {
        "gross_usd": float(gross),
        "funding_paid": float(D(funding_paid)),
        "net_usd": float(net),
        "pnl_pct": float(ch * lev * Decimal("100")),
        "margin": float(margin),
        "price_change_frac": float(ch),
    }


def realized_pnl(
    notional: Number,
    entry: Number,
    exit: Number,
    direction: str,
    leverage: Number = 1,
    funding_paid: Number = 0,
    entry_side: str = "taker",
    exit_side: str = "taker",
    include_slippage: bool = True,
    slip_frac: Number = None,
) -> dict:
    """
    Realized PnL z fee open+close + funding.
    """
    n = D(notional).copy_abs()
    ch = price_change_frac(entry, exit, direction)
    gross = n * ch
    costs = entry_exit_costs(
        n, entry_side=entry_side, exit_side=exit_side,
        include_slippage=include_slippage, slip_frac=slip_frac,
    )
    fund = D(funding_paid)
    net = gross - costs["total_usd"] - fund
    lev = D(leverage) if D(leverage) > 0 else Decimal("1")
    return {
        "gross_usd": float(gross),
        "fee_entry": float(costs["fee_entry"]),
        "fee_exit": float(costs["fee_exit"]),
        "slippage": float(costs["slippage"]),
        "funding_paid": float(fund),
        "net_usd": float(net),
        "pnl_pct": float((net / n) * lev * Decimal("100")) if n else 0.0,
        "price_change_frac": float(ch),
        "cost_frac": float(costs["total_frac"]),
    }


# ------------------------------------------------------------------
# 23. Execution decimal helpers
# ------------------------------------------------------------------
def notional_to_contracts_dec(
    notional_usd: Number,
    price: Number,
    contract_value: Number,
    lot_size: Number,
) -> dict:
    """
    Decimal version of USD → contracts.
    contracts = notional / (price * contractValue)
    """
    n = D(notional_usd)
    px = D(price)
    cv = D(contract_value) if D(contract_value) > 0 else Decimal("1")
    if px <= 0 or n <= 0:
        return {"ok": False, "contracts": Decimal("0"), "error": "NON_POSITIVE"}
    contract_usd = px * cv
    raw = n / contract_usd
    contracts = quantize_size(raw, lot_size)
    return {
        "ok": contracts > 0,
        "contracts": contracts,
        "contracts_raw": raw,
        "contract_usd": contract_usd,
        "notional": contracts * contract_usd,
    }


def contracts_to_notional_dec(contracts: Number, price: Number, contract_value: Number) -> Decimal:
    return D(contracts) * D(price) * (D(contract_value) if D(contract_value) > 0 else Decimal("1"))


# ------------------------------------------------------------------
# 24. Equity ledger / sync
# ------------------------------------------------------------------
class EquityLedger:
    """
    Lokalny ledger kapitału z rozbiciem:
    equity = cash + unrealized
    cash zmienia się tylko na realized close / funding settlement / deposit
    """

    def __init__(self, starting: Number = None):
        start = D(starting if starting is not None else getattr(config, "STARTING_CAPITAL", 100))
        self.cash = start
        self.peak_equity = start
        self.realized_pnl_total = Decimal("0")
        self.funding_total = Decimal("0")
        self.fees_total = Decimal("0")
        self.exchange_equity: Optional[Decimal] = None
        self.exchange_available: Optional[Decimal] = None
        self.exchange_ts: float = 0.0
        self.mode = "PAPER"  # PAPER | LIVE

    def apply_realized(self, net_pnl: Number, fees: Number = 0, funding: Number = 0):
        net = D(net_pnl)
        self.cash += net
        self.realized_pnl_total += net
        self.fees_total += D(fees)
        self.funding_total += D(funding)
        eq = self.cash
        if eq > self.peak_equity:
            self.peak_equity = eq

    def sync_exchange(self, equity: Number, available: Number = None, ts: float = None):
        self.exchange_equity = D(equity)
        if available is not None:
            self.exchange_available = D(available)
        self.exchange_ts = ts or time.time()
        self.mode = "LIVE"
        # w LIVE cash podglądowy = exchange equity (bez double-count unrealized lokalnego)
        if self.exchange_equity > 0:
            self.cash = self.exchange_equity
            if self.cash > self.peak_equity:
                self.peak_equity = self.cash

    def equity_with_unrealized(self, unrealized: Number = 0) -> Decimal:
        if self.mode == "LIVE" and self.exchange_equity is not None:
            # exchange equity zwykle już zawiera UPL
            return self.exchange_equity
        return self.cash + D(unrealized)

    def snapshot(self, unrealized: Number = 0) -> dict:
        eq = self.equity_with_unrealized(unrealized)
        dd = Decimal("0")
        if self.peak_equity > 0:
            dd = (self.peak_equity - eq) / self.peak_equity
        return {
            "mode": self.mode,
            "cash": float(self.cash),
            "equity": float(eq),
            "peak_equity": float(self.peak_equity),
            "drawdown_pct": float(dd * 100),
            "realized_pnl_total": float(self.realized_pnl_total),
            "funding_total": float(self.funding_total),
            "fees_total": float(self.fees_total),
            "exchange_equity": float(self.exchange_equity) if self.exchange_equity is not None else None,
            "exchange_available": float(self.exchange_available) if self.exchange_available is not None else None,
            "exchange_age_s": (time.time() - self.exchange_ts) if self.exchange_ts else None,
        }
