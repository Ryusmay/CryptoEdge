# Metodologia CryptoEdge (anty-overfit)

## Zasada
**H i COW są przykładami problemu, nie dowodem, że konkretny próg działa.**

Nie wolno:
- ustawiać `REVERSAL_MIN_24H_PCT = 30`, bo H miał +32%
- stroić RSI pod jeden setup COW
- optymalizować pod 1–2 monety / 1 dzień rynku

## Kolejność pracy
1. **Hipoteza** — np. „po ekstremalnym impulsie + wyczerpaniu + potwierdzeniu struktury edge jest dodatni”
2. **Reguły ogólne** — pipeline EXTREME → EXHAUSTION → CONFIRMATION (nie surowy % flip)
3. **Shadow Mode** — `REVERSAL_SHADOW_ONLY=True`, zero egzekucji reversal
4. **Dane** — setki kandydatów, MFE/MAE, czas do TP/SL, reżimy
5. **Walidacja** — test reżimów, OOS, walk-forward; Trend vs Reversal osobno
6. **Strojenie** — tylko gdy n jest duże i wynik stabilny na populacji

## Co mierzymy zanim damy size
- Expectancy/R, PF, WR, Median R, Max DD, Sharpe/Sortino
- Reversal: MFE/MAE, time-to-TP/SL, vs impuls / RSI / liquidity
- Per reżim: TREND_UP/DOWN, RANGE, HIGH_VOL, PANIC, RECOVERY

## Progi w configu
Wartości startowe to **priory hipotezy**, nie optimum z backtestu.
Każda zmiana progu: uzasadnienie na próbie, nie na anegdocie.

## Fibonacci Confluence Engine (nie „Fibonacci Strategy”)

```
                MARKET
                   ↓
             SWING DETECTOR   (pivot + ATR/% + min bars, no look-ahead)
                   ↓
             FIBONACCI MAP
                   ↓
       ┌───────────┴───────────┐
       ↓                       ↓
 TREND CONTINUATION        REVERSAL
 pullback 0.5–0.618        extreme + divergence
 confirmation              structure reversal
       └───────────┬───────────┘
                   ↓
             EXPECTED NET R  (+ asymmetry TP vs SL)
                   ↓
                ENTRY
```

Fibo = mapa + confluence + weryfikacja R:R.  
Nigdy samodzielny sygnał „0.618 → BUY”.  
V2: `swing_structure` (retracement/extension na 1h). Reversal: `fibonacci_map` w `reversal_engine.py`.
