"""Twarda polityka instrumentow dopuszczonych do strategii CryptoEdge."""
from __future__ import annotations

import config


# Akcje, ETF-y, indeksy, metale i towary nie maja w CryptoEdge poprawnego
# modelu sesji/corporate actions i nie moga trafic do strategii krypto.
DEFAULT_TRADITIONAL_SYMBOLS = {
    "XAU", "XAG", "GOLD", "SILVER", "DXY", "VIX", "SPX", "NDX", "DJI",
    "SPY", "QQQ", "DIA", "IWM", "VOO", "VTI", "UVXY", "SVXY", "SMH",
    "TLT", "GLD", "SLV", "USO", "UNG", "ARKK", "EEM", "HYG", "XLF",
    "XLK", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB", "XLRE",
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "TSLA",
    "AVGO", "ORCL", "AMD", "INTC", "QCOM", "MU", "ARM", "TSM", "ASML",
    "NFLX", "DIS", "UBER", "ABNB", "COIN", "MSTR", "HOOD", "PLTR",
    "JPM", "BAC", "GS", "MS", "V", "MA", "PYPL", "WMT", "COST",
    "KO", "PEP", "MCD", "NKE", "BA", "CAT", "GE", "XOM", "CVX",
    "LLY", "UNH", "JNJ", "PFE", "MRK", "ABBV", "NVO", "BABA", "PDD",
    # Instrumenty tradycyjne zaobserwowane w feedzie BloFin.
    "SOXS", "SOXL", "SPXS", "SPXL", "TQQQ", "SQQQ", "ADI", "SPCX",
    # BloFin xStocks / pre-market / indeksy i towary bez assetClass w API.
    "SKHY", "SAMSUNG", "SKHYNIX", "ANTHROPIC", "OPENAI", "STXX",
    "LRCX", "GLW", "BMNR", "AAOI", "AMAT", "ASTS", "NOW", "IBM",
    "DELL", "SLX", "BE", "COHR", "NBIS", "URNM", "WTIOIL", "CBRS",
    "RKLB", "CRWV", "LITE", "DRAM", "BILL", "SNDK", "EWJ", "NG",
    "XCU", "CRCL", "ALAB", "O", "BTCDOM", "ETHBTC",
}

_SYNTHETIC_PREFIXES = ("CSOP",)
_LEVERAGED_SUFFIXES = ("2L", "2S", "3L", "3S", "5L", "5S")


def normalize_symbol(symbol: str) -> str:
    value = str(symbol or "").upper().strip()
    if value.endswith("-USDT"):
        value = value[:-5]
    elif value.endswith("USDT"):
        value = value[:-4]
    return value.replace("-", "")


def traditional_symbols() -> set[str]:
    extra = getattr(config, "TRADITIONAL_MARKET_SYMBOLS", None) or []
    return DEFAULT_TRADITIONAL_SYMBOLS | {normalize_symbol(x) for x in extra}


def is_traditional_market_symbol(symbol: str, instrument: dict | None = None) -> bool:
    sym = normalize_symbol(symbol)
    if not sym or sym in traditional_symbols():
        return True
    if sym.startswith(_SYNTHETIC_PREFIXES) or sym.endswith(_LEVERAGED_SUFFIXES):
        return True
    row = instrument or {}
    text = " ".join(str(row.get(k) or "") for k in (
        "assetClass", "category", "underlyingType", "instrumentType", "name",
    )).lower()
    return any(token in text for token in (
        "stock", "equity", "etf", "index", "commodity", "metal", "forex",
    ))


def crypto_perpetual_allowed(symbol: str, instrument: dict | None = None) -> bool:
    if is_traditional_market_symbol(symbol, instrument):
        return False
    row = instrument or {}
    inst_id = str(row.get("instId") or f"{normalize_symbol(symbol)}-USDT").upper()
    quote = str(row.get("quoteCurrency") or row.get("quote") or "USDT").upper()
    state = str(row.get("state") or "live").lower()
    inst_type = str(row.get("instType") or "SWAP").upper()
    contract = str(row.get("contractType") or "linear").lower()
    return (
        inst_id.endswith("-USDT") and quote == "USDT"
        and state in ("", "live", "online", "trading")
        and inst_type in ("", "SWAP", "PERPETUAL")
        and "inverse" not in contract
    )
