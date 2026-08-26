"""Kubełki WHY NO TRADE (DESK). Limit BloFin/V2 funnel, nie V1 liquidity/corr."""


def why_bucket(reason) -> str:
    """SETUP = 4h/1h/15m; TIMING = pauza; DATA = feed/universe; RISK = portfel."""
    u = str(reason or "").upper()
    if any(tag in u for tag in (
        "STALE", "DATA_NA", "INDICATORS_NA", "NOT_IN_LIQUID", "COLD_START",
        "OB_THIN", "BLOFIN", "EXCLUDED", "NO_SYMBOL", "PRICE_ATR",
    )):
        return "data"
    if any(tag in u for tag in (
        "COOLDOWN", "LOSS_STREAK", "ALREADY_TRADED", "PAUSE", "WAIT", "PENDING",
        "WARMING",
    )):
        return "timing"
    if any(tag in u for tag in (
        "CLUSTER", "MAX_POS", "GROSS", "EXPOSURE", "PORTFOLIO", "CORR",
        "MARGIN", "RISK_PCT", "DAILY", "DRAWDOWN", "HALT", "FUNDING_EXTREME",
    )):
        return "risk"
    return "setup"
