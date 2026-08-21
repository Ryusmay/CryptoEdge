# ============================================================
# 9. Dynamic spread threshold
# soft: z-score + volatility + basis + execution cost
# ============================================================

from __future__ import annotations
from typing import Deque, Dict, Optional, Any
from collections import defaultdict, deque
import math
import time


class SpreadTracker:
    """Per-symbol rolling spread history do z-score."""

    def __init__(self, window: int = 60):
        self.window = window
        self._hist: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=window))

    def push(self, symbol: str, spread_pct: float):
        if spread_pct is None:
            return
        try:
            self._hist[symbol.upper()].append(float(spread_pct))
        except (TypeError, ValueError):
            pass

    def zscore(self, symbol: str, spread_pct: float) -> Optional[float]:
        h = list(self._hist.get(symbol.upper()) or [])
        if len(h) < 8:
            return None
        mean = sum(h) / len(h)
        var = sum((x - mean) ** 2 for x in h) / len(h)
        std = math.sqrt(var) if var > 0 else 0.0
        if std < 1e-9:
            return 0.0
        return (float(spread_pct) - mean) / std


_TRACKER = SpreadTracker(window=80)


def dynamic_spread_threshold(
    signal: dict,
    base_max_pct: float = 0.12,
) -> Dict[str, Any]:
    """
    Zamiast hard MAX_SPREAD:
      limit = base * (1 + k_vol*atr + k_basis*|basis|) 
      reject gdy spread_z > Z_MAX lub spread > limit lub cost_model
    """
    import config
    ob = (signal or {}).get("order_book") or {}
    sp = ob.get("ob_spread_pct")
    if sp is None:
        sp = signal.get("spread_pct")
    try:
        sp = float(sp) if sp is not None else None
    except (TypeError, ValueError):
        sp = None

    symbol = (signal.get("symbol") or "").upper()
    # 1) score vs HISTORII (bez bieżącej próbki)
    z = _TRACKER.zscore(symbol, sp) if sp is not None else None
    # 2) dopiero potem append bieżącego spreadu
    if sp is not None and symbol:
        _TRACKER.push(symbol, sp)

    atr_pct = signal.get("atr_pct")
    try:
        atr_pct = float(atr_pct) if atr_pct is not None else 0.0
    except (TypeError, ValueError):
        atr_pct = 0.0

    basis = signal.get("basis_pct")
    if basis is None:
        layers = signal.get("price_layers") or {}
        basis = layers.get("basis_pct")
    try:
        basis = abs(float(basis)) if basis is not None else 0.0
    except (TypeError, ValueError):
        basis = 0.0

    # execution cost proxy: half-spread + taker fee*2
    fee = float(getattr(config, "TAKER_FEE", getattr(config, "COMMISSION_RATE", 0.0006)))
    half_sp = (sp / 100.0 / 2.0) if sp is not None else 0.0
    exec_cost_pct = (half_sp + fee * 2) * 100.0  # in %

    k_vol = float(getattr(config, "SPREAD_K_VOL", 0.15))
    k_basis = float(getattr(config, "SPREAD_K_BASIS", 0.5))
    base = float(getattr(config, "MAX_SPREAD_PCT", base_max_pct) or base_max_pct)
    # szerszy limit przy wyższej zmienności / basis
    limit = base * (1.0 + k_vol * atr_pct + k_basis * basis)
    limit = min(limit, float(getattr(config, "MAX_SPREAD_PCT_HARD", 0.50)))  # absolute ceiling

    z_max = float(getattr(config, "SPREAD_ZSCORE_MAX", 2.5))
    cost_max = float(getattr(config, "MAX_EXEC_COST_PCT", 0.25))

    reasons = []
    ok = True
    if sp is not None and sp > limit:
        ok = False
        reasons.append(f"SPREAD_DYN({sp:.3f}%>lim{limit:.3f}%)")
    if z is not None and z > z_max:
        ok = False
        reasons.append(f"SPREAD_Z({z:.2f}>{z_max})")
    if exec_cost_pct > cost_max:
        ok = False
        reasons.append(f"EXEC_COST({exec_cost_pct:.3f}%>{cost_max}%)")

    return {
        "ok": ok,
        "reason": reasons[0] if reasons else "OK",
        "reasons": reasons,
        "spread_pct": sp,
        "spread_z": round(z, 3) if z is not None else None,
        "limit_pct": round(limit, 4),
        "exec_cost_pct": round(exec_cost_pct, 4),
        "atr_pct": atr_pct,
        "basis_pct": basis,
    }
