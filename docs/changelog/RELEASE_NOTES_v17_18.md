# CryptoEdge v17.18 — Liquidity Universe & Telemetry Integrity

## Zmiany

- BloFin `volCurrency24h` jest traktowany jako wolumen bazowy i przeliczany na obrót USDT (`base volume × last price`).
- Ranking TOP N daytradingu korzysta z porównywalnego quote volume, z zachowaniem minimalnego progu wolumenu USD.
- Zachowane są istniejące blokady spreadu, orderbook depth, VWAP impact oraz fill ratio.
- Odrzucenia `DAY_NOT_IN_LIQUID_TOP` nie zaśmiecają telemetryki per symbol.
- `signals_history.csv` zapisuje teraz również readiness wybranych kandydatów DAYTRADING, wraz z trybem i powodem oczekiwania.
- Znaczniki czasu w stanie, historii sygnałów i reversal shadow są jawnie zapisywane w UTC z offsetem.
- Zapisany `STRATEGY_MODE` jest stosowany przed utworzeniem silników i pierwszym cyklem.

## Walidacja

- Dodano regresję rankingu base-volume vs quote-volume.
- Dodano regresję redukcji telemetryki monet spoza koszyka.
- Dodano regresję historii neutralnych kandydatów daytradingowych.
