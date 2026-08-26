# CryptoEdge v19.25.5

Martwy config: **podpiąć albo usunąć**. V2 nadal jedyny żywy silnik; 1D i rozjazd źródeł nie wracają jako bramki.

## Podpięte

| Flaga | Gdzie |
|---|---|
| `LIVE_SYNC_BALANCE` | `account_sync` — LIVE equity z BloFin (False = zostaw cache) |
| `RECONCILE_EVERY_CYCLES` | `BotRuntime.maybe_reconcile` — LIVE, co N pełnych cykli |
| `CONTROL_PAUSE_FILE` / `_CLOSE_ALL` / `_RESUME` | `BotRuntime.apply_control_files` — puste pliki w folderze bota |
| `STALE_KLINES_SECONDS` | V2: 4h/1h/15m starsze niż długość bara + slack → `V2_STALE_KLINES_*` |
| `FILTER_UNIVERSE_BY_REGISTRY` | `DataFeeder` — tylko pary z registry (domyślnie off) |
| `TOP_N_FETCH` | CoinGecko `per_page` |
| `RECOVERY_REATTACH_EXCHANGE_SL` | recovery: `attach_protection(..., place_exchange=)` |
| `FUNDING_PERIOD_HOURS` | default interwału funding gdy giełda nie podaje |
| `STRATEGY_AGG_4H_FROM_1H` | proxy 4h z 1h w `evaluate_strategy_tf` |
| `ENTRY_DELAY_SECONDS` | sleep przed LIVE market (cap 30s; 0 = off) |
| `REGIME_ATR_PERIOD` / `REGIME_ATR_MA` | `regime_model` |
| `REGIME_PANIC_TREND_SIZE_MULT` | alias `REGIME_PANIC_SIZE_MULT` (0 = brak Trend w PANIC) |
| `REVERSAL_TP_R_MULT` | fallback TP2 gdy fib ≈ 1R |
| `LOG_FILE` / `SIGNALS_FILE` / `STATE_FILE` | logger + `load_previous_state` |
| `ORDER_WAIT_FILL_SECONDS` | poll fill po market |
| `REVERSAL_LIVE_EXECUTION_ENABLED` | shadow_mode LIVE |

## Usunięte (legacy, zero odczytu)

`UNIVERSE_MODE`, `STALE_POSITION_MINUTES` / `_MIN_PNL_PCT`, `CLOSE_ONLY_MAX_TP`, `MIN_SIGNAL_STRENGTH_TREND`, `DAYTRADING_TIMING_REQUIRE_ST` / `_MACD`, `REVERSAL_SL_PCT` / `TP_PCT` / `MULTI_TP`, `ACCOUNTING_DECIMAL`, `MIN_ORDERBOOK_DEPTH`, `REGIME_TREND_ADX_PROXY`.
