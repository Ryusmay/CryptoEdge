# CryptoEdge v19.25.2

A1–A7 z audytu 19.25.1 — V2 jest jedynym żywym silnikiem wejść.

## A1 — bez dummy siły
V2 nie pokazuje `0.75`. W scanner / opportunities / top / queue / execution / DESK candidates / SCAN: **PASS** albo **—**.

## A2 — stałe 7,5% margin
`DAYTRADING_V2_MARGIN_STRENGTH_SCALED=False`, `DAYTRADING_V2_MARGIN_PCT_FIXED=7.5` (przycięte do 5–10%). x10 Isolated → notional ~75% equity na wejście, niezależnie od dummy strength.

## A3 — portfolio SL$ nie zeruje V2
`_portfolio_open_risk_ok` (ryzyko dolarowe z odległości SL) **pomijane** przy V2 `capital_pct`. Limity gross/margin zostają. `DAYTRADING_V2_MAX_RISK_PCT_PER_TRADE=0` (wyłączony SIZE_CAP_RISK).

## A4 — flagi UI na V2
`generate_signals` V2 wracał zanim odpalały się pętle Trend. Teraz `apply_v2_ui_gates()`:
- `BLOCK_RANGE_REGIME`
- `BLOCK_PUMP_CHASE_PCT` (0 = wyłączone)
- `BLOCK_OB_THIN`
- `REQUIRE_PRIMARY_STRATEGY` w `DayTradingEngineV2` (4h mismatch → soft, nie twardy blok gdy flaga off)

## A5 / A7 — SRC_DIVERGENCE i MIN_SIGNAL mają sens
- Rozjazd źródeł ≥ `BN_BF_DIVERGENCE_HARD_PCT` (3%) twardo blokuje V2.
- `MIN_SIGNAL_STRENGTH` **nie** ocenia dummy 0.75 na V2 (can_open + log WEAK + PANIC).
- `REQUIRE_PRIMARY` Trend-style (`STRAT_NA_*`) **nie** leci na V2 — checklista 1D/4h jest w silniku.

## A6 — Reversal realnie startuje
- Próg ekstremum 24h: **12%** (nie 18).
- Exhaustion min: **0.20**; brak RSI na tickerze = `EXH_NO_RSI_USE_1H` + proxy 1h.
- V2 `evaluate()` kopiuje RSI/MACD/highs/lows 1h na coin.
- Dla ekstremów bez 1h: `_hydrate_1h_from_feeder` (max `REVERSAL_MAX_CANDIDATES` klinek / cykl).
- Diagnostyka: `hydrated_1h` w `[Reversal] 0 sygnałów | ...`.

## Uwaga na stary `logs/settings.json`
Jeśli masz zapisane `MIN_SIGNAL_STRENGTH=0.55` albo `BLOCK_PUMP_CHASE_PCT=28`, UI nadpisze config. Usuń `logs/settings.json` albo ustaw w UI: pump **22**, min **0.48**.
