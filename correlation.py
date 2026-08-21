# ============================================================
# Analiza korelacji / rozjazdow cen miedzy zrodlami
# ============================================================

from typing import List, Dict, Optional


def _safe_pct_diff(a: float, b: float) -> Optional[float]:
    if a is None or b is None or a == 0:
        return None
    try:
        return abs(float(a) - float(b)) / float(a) * 100
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def analyze_source_divergence(coin: Dict) -> Dict:
    """
    Porownuje ceny CG / Binance / Bybit / Blofin.
    price glowne moze byc z Blofin – CG osobno jako coingecko_price.
    """
    cg = coin.get("coingecko_price") or (
        coin.get("price") if not coin.get("blofin_price") and not coin.get("binance_price") else None
    )
    bn = coin.get("binance_price")
    by = coin.get("bybit_price")
    bf = coin.get("blofin_price") or coin.get("price")

    diffs = {
        "cg_vs_binance": _safe_pct_diff(cg, bn),
        "cg_vs_bybit": _safe_pct_diff(cg, by),
        "cg_vs_blofin": _safe_pct_diff(cg, bf),
        "binance_vs_bybit": _safe_pct_diff(bn, by),
        "binance_vs_blofin": _safe_pct_diff(bn, bf),
        "bybit_vs_blofin": _safe_pct_diff(by, bf),
    }

    # Rozjazdy > 20% zwykle = inny token pod tym samym tickerem (np. DATA, META)
    MISMATCH_PCT = 20.0
    valid_real = [v for v in diffs.values() if v is not None and v <= MISMATCH_PCT]
    valid_all = [v for v in diffs.values() if v is not None]
    max_diff = max(valid_real) if valid_real else 0.0
    has_mismatch = any(v > MISMATCH_PCT for v in valid_all)

    warning = False
    level = "ok"
    if max_diff >= 2.0:
        warning = True
        level = "high"
    elif max_diff >= 1.0:
        warning = True
        level = "medium"
    elif max_diff >= 0.5:
        level = "low"
    if has_mismatch:
        level = level if level != "ok" else "mismatch"

    sources_available = sum(1 for x in (cg, bn, by, bf) if x)

    return {
        "diffs": diffs,
        "max_diff_pct": round(max_diff, 3),
        "symbol_mismatch": has_mismatch,
        "warning": warning,
        "level": level,
        "sources_available": sources_available,
        "prices": {
            "coingecko": cg,
            "binance": bn,
            "bybit": by,
            "blofin": bf,
        }
    }


def summarize_market_correlation(coins: List[Dict]) -> Dict:
    analyzed = []
    for c in coins:
        d = analyze_source_divergence(c)
        d["symbol"] = c.get("symbol")
        analyzed.append(d)

    multi = [a for a in analyzed if a["sources_available"] >= 2]
    diffs = [a["max_diff_pct"] for a in multi if a["max_diff_pct"] is not None and not a.get("symbol_mismatch")]

    avg_diff = sum(diffs) / len(diffs) if diffs else 0.0
    max_diff = max(diffs) if diffs else 0.0
    warn_count = sum(1 for a in multi if a["warning"])

    top_divergent = sorted(
        [a for a in multi if a["max_diff_pct"] > 0.3 and not a.get("symbol_mismatch")],
        key=lambda x: x["max_diff_pct"],
        reverse=True
    )[:8]

    mismatch_count = sum(1 for a in multi if a.get("symbol_mismatch"))
    return {
        "coins_total": len(coins),
        "coins_multi_source": len(multi),
        "avg_diff_pct": round(avg_diff, 3),
        "max_diff_pct": round(max_diff, 3),
        "mismatch_count": mismatch_count,
        "warning_count": warn_count,
        "top_divergent": top_divergent,
    }


def print_correlation_report(summary: Dict):
    print("\n=== KORELACJA ZRODEL (Blofin universe + CG/BN/BY) ===")
    print(f"  Pary w universe:     {summary['coins_total']}")
    print(f"  Multi-source:        {summary['coins_multi_source']}")
    print(f"  Sredni rozjazd cen:  {summary['avg_diff_pct']:.3f}%")
    print(f"  Max rozjazd:         {summary['max_diff_pct']:.3f}%")
    print(f"  Ostrzezenia (>=1%):  {summary['warning_count']}")
    if summary["top_divergent"]:
        print("  Top rozjazdy / anomalie miedzy zrodlami:")
        for d in summary["top_divergent"]:
            prices = d["prices"]
            print(
                f"    {d['symbol']:10} max {d['max_diff_pct']:.2f}% | "
                f"CG={prices['coingecko']} BN={prices['binance']} "
                f"BY={prices['bybit']} BF={prices.get('blofin')}"
            )
