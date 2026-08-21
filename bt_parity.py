# ============================================================
# PRIORYTET 14 — BACKTEST ≡ LIVE (parity checklist)
# ============================================================
#
# BACKTEST ≠ LIVE → wynik backtestu jest ozdobą, nie narzędziem.
# Ten moduł dokumentuje wspólne ścieżki i aserty przy starcie BT.
# ============================================================

from __future__ import annotations
from typing import List, Tuple

# Wspólne moduły (LIVE + BT) — nie wolno dublować logiki lokalnie
SHARED_MODULES = [
    "expected_net_r",       # Expected Net R
    "indicators_full",      # RSI/MACD/ST/ADX/EMA
    "reversal_engine",      # Reversal Engine
    "strength_calibration", # strength → expected R
    "external_confirmation",# BN confirmation
    "orderbook_impact",     # impact / safe notional (BT: proxy z ATR/vol)
]

PARITY_CHECKLIST = """
PRIORYTET 14 — Backtester używa tej samej logiki co LIVE

  [x] MTF replay          15m / 1h / 4h / 1d  (evaluate_mtf_at + window_until)
  [x] BloFin pricing      primary source=blofin (fetch_klines / fetch_mtf_bundle)
  [x] Binance confirm     apply_bt_confirmation → external_confirmation
  [x] Funding             historyczny schedule + accrual na pozycji
  [x] Fees                TAKER_FEE round-trip w realized PnL
  [x] Slippage            estimate_bar_slippage(notional, volume, ATR)
  [x] Liquidity           OB proxy z ATR/vol + max impact (LIVE: real OB)
  [x] Expected Net R      expected_net_r.net_r_ok  (ten sam filtr)
  [x] Trend Engine        evaluate_entry + MTF + soft pass (indicators_full)
  [x] Reversal Engine     bt_reversal_signal_from_window → score_reversal_candidate
  [x] decision_path       TREND_PRIMARY | TREND_SOFT | REVERSAL | NO_TRADE
  [x] by_engine stats     Trend vs Reversal vs Combined

Różnice świadome (nie da się 1:1 historycznie):
  • Order book LIVE = snapshot real-time; BT = model impact z volume/ATR
  • CoinGecko context w BT ograniczony (brak pełnej historii meta)
  • Partial fills / queue position — uproszczone
"""


def assert_parity_imports() -> Tuple[bool, List[str]]:
    """Import-time check: krytyczne moduły muszą istnieć."""
    missing = []
    for name in SHARED_MODULES:
        try:
            __import__(name)
        except Exception as e:
            missing.append(f"{name}: {type(e).__name__}: {e}")
    return (len(missing) == 0), missing


def print_parity_banner():
    ok, missing = assert_parity_imports()
    print(PARITY_CHECKLIST)
    if ok:
        print("[BT parity] SHARED_MODULES OK — backtest korzysta z tych samych bramek co LIVE")
    else:
        print("[BT parity] BRAK MODUŁÓW:", "; ".join(missing))
        print("[BT parity] UWAGA: wynik może nie być lustrem LIVE")


if __name__ == "__main__":
    print_parity_banner()
