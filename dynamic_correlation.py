# ============================================================
# P2.9 – Dynamiczna korelacja portfolio (rolling returns)
# ============================================================

from __future__ import annotations
import math
import time
from collections import defaultdict, deque
from typing import Dict, Deque, List, Optional, Any, Tuple


class DynamicCorrelation:
    """
    Per-symbol bufor log-returns → pairwise Pearson na oknie.
    Używane do blokady / kary gdy nowy symbol jest silnie skorelowany
    z już otwartymi pozycjami w tym samym kierunku.
    """

    def __init__(self, window: int = 48, min_obs: int = 20):
        self.window = window
        self.min_obs = min_obs
        self._rets: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=window))
        self._last_px: Dict[str, float] = {}
        self._timed_rets: Dict[str, Dict[int, float]] = {}
        self._last_update = 0.0

    def update_price(self, symbol: str, price: float):
        sym = (symbol or "").upper()
        if not sym or price is None or float(price) <= 0:
            return
        px = float(price)
        prev = self._last_px.get(sym)
        self._last_px[sym] = px
        if prev and prev > 0:
            r = math.log(px / prev)
            self._rets[sym].append(r)
        self._last_update = time.time()

    def update_batch(self, prices: Dict[str, float]):
        for s, p in (prices or {}).items():
            self.update_price(s, p)

    def set_close_series(self, symbol: str, closes: List[float], timestamps: List[int] = None):
        """Seed aligned closed-bar returns; avoids fake tick-on-scan correlation."""
        sym = (symbol or "").upper()
        values = [float(x) for x in (closes or []) if x is not None and float(x) > 0]
        if not sym or len(values) < 2:
            return
        returns = [math.log(b / a) for a, b in zip(values[:-1], values[1:]) if a > 0 and b > 0]
        self._rets[sym] = deque(returns[-self.window:], maxlen=self.window)
        if timestamps and len(timestamps) == len(values):
            pairs = [(int(ts), math.log(b / a)) for ts, a, b in
                     zip(timestamps[1:], values[:-1], values[1:]) if a > 0 and b > 0]
            self._timed_rets[sym] = dict(pairs[-self.window:])
        self._last_px[sym] = values[-1]
        self._last_update = time.time()

    def correlation(self, a: str, b: str) -> Optional[float]:
        a, b = a.upper(), b.upper()
        ta, tb = self._timed_rets.get(a) or {}, self._timed_rets.get(b) or {}
        common = sorted(set(ta).intersection(tb))[-self.window:]
        if common:
            ra, rb = [ta[t] for t in common], [tb[t] for t in common]
        else:
            # Legacy fallback only for callers which cannot supply timestamps.
            ra = list(self._rets.get(a) or [])
            rb = list(self._rets.get(b) or [])
        n = min(len(ra), len(rb))
        if n < self.min_obs:
            return None
        ra, rb = ra[-n:], rb[-n:]
        ma = sum(ra) / n
        mb = sum(rb) / n
        num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
        da = math.sqrt(sum((x - ma) ** 2 for x in ra))
        db = math.sqrt(sum((y - mb) ** 2 for y in rb))
        if da < 1e-12 or db < 1e-12:
            return 0.0
        return max(-1.0, min(1.0, num / (da * db)))

    def max_corr_with_open(
        self,
        symbol: str,
        direction: str,
        open_positions: List[Any],
    ) -> Tuple[Optional[float], Optional[str]]:
        """
        Max |corr| z pozycjami w tym samym kierunku.
        Zwraca (corr, other_symbol).
        """
        sym = (symbol or "").upper()
        direction = (direction or "").upper()
        best = None
        best_sym = None
        for p in open_positions or []:
            if hasattr(p, "symbol"):
                osym = (p.symbol or "").upper()
                odir = (getattr(p, "direction", "") or "").upper()
            elif isinstance(p, dict):
                osym = (p.get("symbol") or "").upper()
                odir = (p.get("direction") or "").upper()
            else:
                continue
            if not osym or osym == sym:
                continue
            # korelacja szkodzi głównie gdy ten sam kierunek
            if odir and direction and odir != direction:
                continue
            c = self.correlation(sym, osym)
            if c is None:
                continue
            if best is None or abs(c) > abs(best):
                best = c
                best_sym = osym
        return best, best_sym

    def gate(
        self,
        symbol: str,
        direction: str,
        open_positions: List[Any],
        max_corr: float = 0.75,
    ) -> Tuple[bool, str, dict]:
        """
        (ok, reason, detail)
        Reject gdy corr ≥ max_corr z otwartą pozycją tego samego kierunku.
        """
        c, other = self.max_corr_with_open(symbol, direction, open_positions)
        detail = {"corr": c, "with": other, "max_corr": max_corr}
        if c is None:
            return True, "OK", detail
        if abs(c) >= float(max_corr):
            return False, f"DYN_CORR({other}:{c:+.2f}>={max_corr})", detail
        return True, "OK", detail


_CORR: Optional[DynamicCorrelation] = None


def get_correlation_engine() -> DynamicCorrelation:
    global _CORR
    if _CORR is None:
        try:
            import config
            w = int(getattr(config, "DYN_CORR_WINDOW", 48))
            m = int(getattr(config, "DYN_CORR_MIN_OBS", 20))
        except Exception:
            w, m = 48, 20
        _CORR = DynamicCorrelation(window=w, min_obs=m)
    return _CORR
