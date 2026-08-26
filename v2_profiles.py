"""V2 instrument profiles: major / alt / metal.

Uniwersum zostaje pełne. Różnią się gałki (ATR, RANGE, size, 4H, 5m).
BTC/ETH/SOL zawsze major. Rank ≤ TOP_N po quoteVolume też major. XAU/XAG = metal.
"""
from __future__ import annotations

from typing import Dict, Iterable, Optional

import config


_volume_rank: Dict[str, int] = {}


def _sym(symbol: str) -> str:
    s = str(symbol or "").upper().replace("-USDT", "").replace("USDT", "")
    return s


def _set(name: str, default) -> set:
    raw = getattr(config, name, default)
    return {str(x).upper() for x in (raw or default or [])}


def refresh_volume_ranks(coins: Iterable[dict]) -> Dict[str, int]:
    """Raz na cykl generate(): rank 1 = największy quoteVolume."""
    global _volume_rank

    def vol(coin: dict) -> float:
        try:
            explicit = float(coin.get("blofin_quote_volume_24h") or 0)
        except (TypeError, ValueError):
            explicit = 0.0
        if explicit > 0:
            return explicit
        try:
            base = float(coin.get("blofin_base_volume_24h") or 0)
            px = float(coin.get("price") or 0)
        except (TypeError, ValueError):
            return 0.0
        if base > 0 and px > 0:
            return base * px
        try:
            return float(coin.get("blofin_volume_24h") or coin.get("volume_24h") or 0)
        except (TypeError, ValueError):
            return 0.0

    vols = {}
    for c in coins or []:
        s = _sym(c.get("symbol"))
        if s:
            vols[s] = vol(c)
    order = sorted(vols, key=lambda s: vols[s], reverse=True)
    _volume_rank = {s: i + 1 for i, s in enumerate(order)}
    return dict(_volume_rank)


def profile_for(symbol: str, coin: Optional[dict] = None) -> str:
    s = _sym(symbol)
    if s in _set("DAYTRADING_V2_PROFILE_METAL", ("XAU", "XAG")):
        return "metal"
    if s in _set("DAYTRADING_V2_PROFILE_MAJOR_ALWAYS", ("BTC", "ETH", "SOL")):
        return "major"
    top_n = int(getattr(config, "DAYTRADING_V2_PROFILE_MAJOR_TOP_N", 30) or 30)
    rank = _volume_rank.get(s)
    if rank is not None and rank <= top_n:
        return "major"
    return "alt"


def params_for(profile: str) -> dict:
    p = str(profile or "alt").lower()
    if p == "metal":
        return {
            "name": "metal",
            "swing_min_move_atr": float(getattr(config, "DAYTRADING_V2_METAL_SWING_MIN_MOVE_ATR", 2.0) or 2.0),
            "skip_range": True,
            "range_adx_max": float(getattr(config, "DAYTRADING_V2_ALT_RANGE_ADX_MAX", 18.0) or 18.0),
            "margin_pct": float(getattr(config, "DAYTRADING_V2_METAL_MARGIN_PCT", 5.0) or 5.0),
            "oppose_size_mult": 1.0,
            "skip_4h_oppose": True,
            "use_5m_veto": bool(getattr(config, "DAYTRADING_V2_METAL_USE_5M_VETO", False)),
            "use_4h_context": bool(getattr(config, "DAYTRADING_V2_METAL_USE_4H_CONTEXT", False)),
            "slip_one_way": float(getattr(config, "DAYTRADING_V2_SLIP_METAL", 0.0005) or 0.0005),
            "sl_atr_buffer": float(getattr(config, "DAYTRADING_V2_METAL_SL_ATR_BUFFER", 2.5) or 2.5),
            "trade": bool(getattr(config, "DAYTRADING_V2_METAL_TRADE", False)),
        }
    if p == "alt":
        return {
            "name": "alt",
            "swing_min_move_atr": float(getattr(config, "DAYTRADING_V2_ALT_SWING_MIN_MOVE_ATR", 2.8) or 2.8),
            "skip_range": bool(getattr(config, "DAYTRADING_V2_ALT_SKIP_RANGE", True)),
            "range_adx_max": float(getattr(config, "DAYTRADING_V2_ALT_RANGE_ADX_MAX", 18.0) or 18.0),
            "margin_pct": float(getattr(config, "DAYTRADING_V2_ALT_MARGIN_PCT", 5.0) or 5.0),
            "oppose_size_mult": float(getattr(config, "DAYTRADING_V2_ALT_4H_OPPOSE_SIZE_MULT", 0.50) or 0.50),
            "skip_4h_oppose": bool(getattr(config, "DAYTRADING_V2_ALT_SKIP_4H_OPPOSE", True)),
            "use_5m_veto": True,
            "use_4h_context": True,
            "slip_one_way": float(getattr(config, "DAYTRADING_V2_SLIP_ALT", 0.0015) or 0.0015),
            "sl_atr_buffer": float(getattr(config, "DAYTRADING_V2_SL_ATR_BUFFER", 1.5) or 1.5),
            "trade": True,
        }
    return {
        "name": "major",
        "swing_min_move_atr": float(getattr(config, "DAYTRADING_V2_SWING_MIN_MOVE_ATR", 2.0) or 2.0),
        "skip_range": False,
        "range_adx_max": 18.0,
        "margin_pct": float(getattr(config, "DAYTRADING_V2_MARGIN_PCT_FIXED", 7.5) or 7.5),
        "oppose_size_mult": float(getattr(config, "DAYTRADING_V2_4H_OPPOSE_SIZE_MULT", 0.70) or 0.70),
        "skip_4h_oppose": False,
        "use_5m_veto": True,
        "use_4h_context": True,
        "slip_one_way": float(getattr(config, "DAYTRADING_V2_SLIP_MAJOR", 0.0003) or 0.0003),
        "sl_atr_buffer": float(getattr(config, "DAYTRADING_V2_SL_ATR_BUFFER", 1.5) or 1.5),
        "trade": True,
    }


def replay_slip_round_trip(
    symbol: str,
    ohlcv: Optional[dict] = None,
    index: int = 0,
    price: float = 0.0,
    fallback: float = 0.0006,
) -> float:
    """Round-trip slip for V2 replay. Major ~6 bps, alt ≥30 bps + impact, metal ~10 bps."""
    if not symbol:
        return float(fallback)
    p = params_for(profile_for(symbol))
    base = float(p.get("slip_one_way") or 0.0003)
    eq = float(getattr(config, "STARTING_CAPITAL", 100) or 100)
    lev = float(getattr(config, "LEVERAGE", 10) or 10)
    notional = eq * (float(p.get("margin_pct") or 5.0) / 100.0) * lev
    vol = None
    vols = (ohlcv or {}).get("volumes") or []
    if 0 <= int(index) < len(vols):
        vol = vols[int(index)]
    try:
        from orderbook_impact import estimate_bar_slippage
        one = float(estimate_bar_slippage(
            notional, vol, price or None, base_slip=base,
        ).get("slip") or base)
    except Exception:
        one = base
    return min(0.02, max(2.0 * base, 2.0 * one))


def paper_slip_round_trip(symbol: str, signal: Optional[dict] = None) -> float:
    """Ten sam slip RT co replay — paper i WF liczą identycznie.

    Produkcyjny sygnał V2 niesie ``slip_rt`` policzony z tej samej zamkniętej
    świecy co decyzja. Gdy go brak (np. ręczny sygnał/test), używamy
    deterministycznego profilu symbolu. Nie sięgamy do globalnego STORE, bo
    mogłaby tam być już nowsza świeca niż ta, na której powstał sygnał.
    """
    if signal and signal.get("slip_rt") is not None:
        try:
            return min(0.02, max(0.0, float(signal.get("slip_rt"))))
        except (TypeError, ValueError):
            pass
    return replay_slip_round_trip(symbol)
