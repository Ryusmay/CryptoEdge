# CryptoEdge v19.25.6

PANIC = **silny jednokierunkowy ruch** (UI: STRONG MOVE), nie halt i nie „tragedia”.

## Wejścia

- V2 / Trend / Reversal **otwierają** w PANIC.
- Size pełny (`REGIME_PANIC_*_SIZE_MULT=1.0`). Stary halt: ustaw `0`.
- Brak extra progu siły (`REGIME_PANIC_TREND_MIN_STRENGTH=0`, `DAYTRADING_PANIC_MIN_STRENGTH=0`).
- V2 nie dostaje `_size_mult=0.5` tylko dlatego, że reżim = PANIC.
- Router: płynna para + zgodny residual w PANIC = **kontynuacja**, nie fade.
- Reversal nadal wygrywa tylko przy confirmed + ekstremum 24h / illiquid.

## UI

Hint PANIC: „Silny jednokierunkowy ruch — pozycje dozwolone”.
