# ============================================================
# Wskaźniki techniczne: RSI + MACD (czysty Python)
# ============================================================

from typing import List, Dict, Optional, Tuple

def calculate_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    """Jedno źródło RSI: standard Wildera z indicators_full."""
    from indicators_full import _rsi
    return _rsi(closes, period)


def ema(values: List[float], period: int) -> List[float]:
    """Jedno źródło EMA z indicators_full."""
    from indicators_full import _ema
    return _ema(values, period)


def calculate_macd(closes: List[float],
                   fast: int = 12,
                   slow: int = 26,
                   signal: int = 9) -> Optional[Dict]:
    """
    Oblicza MACD.
    Zwraca: macd_line, signal_line, histogram, cross (bullish/bearish/none)
    """
    from indicators_full import _macd
    result = _macd(closes, fast, slow, signal)
    if not result:
        return None
    return {
        "macd": round(result["macd"], 6),
        "signal": round(result["signal"], 6),
        "histogram": round(result["hist"], 6),
        "cross": result["cross"],
    }


def analyze_rsi_macd(closes: List[float]) -> Dict:
    """Łączna analiza RSI + MACD."""
    rsi = calculate_rsi(closes)
    macd = calculate_macd(closes)

    result = {
        "rsi": rsi,
        "macd": macd,
        "rsi_signal": "neutral",
        "macd_signal": "neutral",
        "combined": "neutral"
    }

    if rsi is not None:
        if rsi >= 70:
            result["rsi_signal"] = "overbought"
        elif rsi <= 30:
            result["rsi_signal"] = "oversold"
        elif rsi >= 55:
            result["rsi_signal"] = "bullish"
        elif rsi <= 45:
            result["rsi_signal"] = "bearish"

    if macd:
        if macd["cross"] == "bullish":
            result["macd_signal"] = "bullish_cross"
        elif macd["cross"] == "bearish":
            result["macd_signal"] = "bearish_cross"
        elif macd["histogram"] > 0:
            result["macd_signal"] = "bullish"
        else:
            result["macd_signal"] = "bearish"

    # Combined
    bull = 0
    bear = 0
    if result["rsi_signal"] in ("oversold", "bullish"):
        bull += 1
    if result["rsi_signal"] in ("overbought", "bearish"):
        bear += 1
    if result["macd_signal"] in ("bullish_cross", "bullish"):
        bull += 1
    if result["macd_signal"] in ("bearish_cross", "bearish"):
        bear += 1

    if bull >= 2:
        result["combined"] = "bullish"
    elif bear >= 2:
        result["combined"] = "bearish"

    return result
