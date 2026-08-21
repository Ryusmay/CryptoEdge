# ============================================================
# Order book impact simulator – VWAP / impact / fill
# ============================================================

from __future__ import annotations
from typing import List, Tuple, Dict, Optional, Any


Level = Tuple[float, float]  # (price, size_base)


def _levels_from_ob(ob: dict, side: str) -> List[Level]:
    """
    side=buy → walk asks; side=sell → walk bids.
    Akceptuje surowe listy albo zagnieżdżone w order_book.
    """
    if not ob:
        return []
    key = "asks" if side.lower() in ("buy", "long") else "bids"
    raw = ob.get(key) or ob.get(f"ob_{key}") or []
    out: List[Level] = []
    for row in raw:
        try:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                p, s = float(row[0]), float(row[1])
            elif isinstance(row, dict):
                p = float(row.get("price") or row.get("px") or 0)
                s = float(row.get("size") or row.get("sz") or row.get("qty") or 0)
            else:
                continue
            if p > 0 and s > 0:
                out.append((p, s))
        except (TypeError, ValueError):
            continue
    return out


def simulate_market_order(
    order_book: dict,
    side: str,
    notional_usd: float,
    mid: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Symuluje market order po order booku.
    Returns:
      vwap, impact_pct (vs mid/best), filled_usd, unfilled_usd,
      levels_used, ok, reason
    """
    side_l = (side or "").lower()
    buy = side_l in ("buy", "long")
    levels = _levels_from_ob(order_book, "buy" if buy else "sell")
    if not levels:
        return {
            "ok": False,
            "reason": "NO_OB_LEVELS",
            "vwap": None,
            "impact_pct": None,
            "filled_usd": 0.0,
            "unfilled_usd": float(notional_usd or 0),
            "levels_used": 0,
        }

    notional = float(notional_usd or 0)
    if notional <= 0:
        return {
            "ok": False,
            "reason": "ZERO_NOTIONAL",
            "vwap": None,
            "impact_pct": None,
            "filled_usd": 0.0,
            "unfilled_usd": 0.0,
            "levels_used": 0,
        }

    # reference price: mid → best
    if mid is None:
        mid = order_book.get("ob_mid") or order_book.get("mid")
    if mid is None:
        bb = order_book.get("ob_best_bid")
        ba = order_book.get("ob_best_ask")
        if bb and ba:
            mid = (float(bb) + float(ba)) / 2.0
        else:
            mid = levels[0][0]
    mid = float(mid)
    best = levels[0][0]

    remain = notional
    cost = 0.0  # quote spent
    base = 0.0
    levels_used = 0
    for price, size in levels:
        level_usd = price * size
        if level_usd <= 0:
            continue
        take_usd = min(remain, level_usd)
        take_base = take_usd / price
        cost += take_usd
        base += take_base
        remain -= take_usd
        levels_used += 1
        if remain <= 1e-9:
            break

    filled = cost
    unfilled = max(0.0, remain)
    if base <= 0 or filled <= 0:
        return {
            "ok": False,
            "reason": "NO_FILL",
            "vwap": None,
            "impact_pct": None,
            "filled_usd": 0.0,
            "unfilled_usd": notional,
            "levels_used": levels_used,
        }

    vwap = cost / base
    # impact vs mid (preferred) and vs best
    impact_mid = abs(vwap - mid) / mid * 100.0 if mid else None
    impact_best = abs(vwap - best) / best * 100.0 if best else None
    fill_ratio = filled / notional if notional else 0.0

    return {
        "ok": True,
        "reason": "OK",
        "vwap": round(vwap, 10),
        "mid": mid,
        "best": best,
        "impact_pct": round(impact_mid, 5) if impact_mid is not None else None,
        "impact_vs_best_pct": round(impact_best, 5) if impact_best is not None else None,
        "filled_usd": round(filled, 4),
        "unfilled_usd": round(unfilled, 4),
        "fill_ratio": round(fill_ratio, 4),
        "levels_used": levels_used,
        "side": "buy" if buy else "sell",
        "notional": notional,
    }


def impact_gate(
    order_book: dict,
    direction: str,
    notional_usd: float,
    max_impact_pct: float = 0.35,
    min_fill_ratio: float = 0.95,
) -> tuple:
    """
    (ok, reason, sim_dict)
    Reject gdy impact > threshold lub fill < min_fill_ratio.
    """
    side = "buy" if (direction or "").upper() == "LONG" else "sell"
    sim = simulate_market_order(order_book, side, notional_usd)
    if not sim.get("ok"):
        return False, f"OB_IMPACT_{sim.get('reason')}", sim
    impact = sim.get("impact_pct")
    if impact is not None and impact > float(max_impact_pct):
        return False, f"OB_IMPACT({impact:.3f}%>{max_impact_pct}%)", sim
    fr = float(sim.get("fill_ratio") or 0)
    if fr < float(min_fill_ratio):
        return False, f"OB_IMPACT_UNFILLED({fr*100:.0f}%<{min_fill_ratio*100:.0f}%)", sim
    return True, "OK", sim


def max_safe_notional(
    order_book: dict,
    direction: str,
    max_impact_pct: float = 0.35,
    min_fill_ratio: float = 0.95,
    target_notional: float = None,
    depth_cap_frac: float = 0.35,
) -> dict:
    """
    PRIORYTET 13 — liquidity → estimated impact → maximum safe notional.

    Zamiast tylko OB_THIN true/false:
      „sygnał chce $5k, book uniesie ~$1.4k przy impact ≤ max”.

    Returns:
      safe_notional, depth_usd, capped_from, sim (dla target lub safe)
    """
    side = "buy" if (direction or "").upper() == "LONG" else "sell"
    levels = _levels_from_ob(order_book, side)
    depth_usd = 0.0
    for p, s in levels:
        depth_usd += float(p) * float(s)

    # soft cap: nie bierz więcej niż fraction głębokości booka
    depth_cap = depth_usd * float(depth_cap_frac) if depth_usd > 0 else 0.0

    if not levels:
        return {
            "safe_notional": 0.0,
            "depth_usd": 0.0,
            "capped_from": "NO_LEVELS",
            "sim": None,
        }

    # binary search max notional z impact ≤ max i fill ≥ min
    lo, hi = 0.0, max(depth_usd, float(target_notional or 0), 1.0)
    if depth_cap > 0:
        hi = min(hi, depth_cap * 1.5)  # search upper
    best = 0.0
    best_sim = None
    for _ in range(18):
        mid = (lo + hi) / 2.0
        if mid <= 0:
            break
        sim = simulate_market_order(order_book, side, mid)
        ok = bool(sim.get("ok"))
        impact = sim.get("impact_pct")
        fr = float(sim.get("fill_ratio") or 0)
        impact_ok = impact is None or float(impact) <= float(max_impact_pct)
        fill_ok = fr >= float(min_fill_ratio)
        if ok and impact_ok and fill_ok:
            best = mid
            best_sim = sim
            lo = mid
        else:
            hi = mid

    if depth_cap > 0 and best > depth_cap:
        best = depth_cap
        best_sim = simulate_market_order(order_book, side, best)

    capped_from = None
    if target_notional is not None and float(target_notional) > 0:
        if best + 1e-9 < float(target_notional):
            capped_from = "OB_SAFE_CAP"
    if depth_usd <= 0:
        capped_from = capped_from or "NO_DEPTH"

    return {
        "safe_notional": round(max(0.0, best), 4),
        "depth_usd": round(depth_usd, 2),
        "depth_cap": round(depth_cap, 4),
        "capped_from": capped_from,
        "sim": best_sim,
        "max_impact_pct": max_impact_pct,
    }


def estimate_bar_slippage(
    notional_usd: float,
    bar_volume_base: float = None,
    price: float = None,
    atr_pct: float = None,
    base_slip: float = None,
) -> dict:
    """
    Model (nie historyczny OB):
      slip ≈ base + impact_from_participation + vol_buffer

    participation = notional / (bar_volume_usd)
    impact ≈ k * participation^0.6   (concave, classic)
    vol_buffer ≈ 0.15 * atr_pct when atr elevated

    Returns fraction of price (0.001 = 0.1%).
    """
    import config
    base = float(base_slip if base_slip is not None else getattr(config, "DEFAULT_SLIPPAGE", 0.0003))
    k = float(getattr(config, "BT_IMPACT_K", 0.08))  # impact scale
    max_slip = float(getattr(config, "BT_MAX_SLIPPAGE", 0.012))  # 1.2% hard cap
    min_slip = base

    participation = 0.0
    bar_usd = 0.0
    try:
        px = float(price or 0)
        vol = float(bar_volume_base or 0)
        if px > 0 and vol > 0:
            bar_usd = vol * px
            if bar_usd > 0:
                participation = max(0.0, float(notional_usd or 0) / bar_usd)
    except (TypeError, ValueError):
        participation = 0.0

    impact = k * (participation ** 0.6) if participation > 0 else 0.0

    vol_buf = 0.0
    try:
        ap = float(atr_pct or 0)
        # akceptuj % (4.0) albo fraction (0.04)
        # 4.0 → 4%; 0.04 → 4%; wartości >0.25 uznaj za %
        if ap > 0.25:
            ap = ap / 100.0
        if ap > 0.015:  # >1.5% ATR
            vol_buf = 0.15 * ap
        elif ap > 0.008:
            vol_buf = 0.08 * ap
    except (TypeError, ValueError):
        pass

    # liquidity: participation już karze niski volume;
    # dodatkowy floor gdy bar prawie pusty
    if participation >= 0.15:
        impact = max(impact, base * 3.0)

    slip = min(max_slip, max(min_slip, base + impact + vol_buf))
    return {
        "slip": slip,
        "base": base,
        "impact": impact,
        "vol_buffer": vol_buf,
        "participation": participation,
        "bar_volume_usd": bar_usd,
        "model": "f(notional,volume,ATR,liquidity)",
    }
