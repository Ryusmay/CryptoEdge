# CryptoEdge v17.5

Swing-trading bot for BloFin USDT perpetuals with a native PySide6 control panel.

## Mode

The distributed build is intended for PAPER testing. BloFin is the primary market-data and execution venue, Binance Futures is independent confirmation, and CoinGecko supplies broad market context.

`LIVE_EXECUTION_ENABLED` remains disabled by default. The BloFin API panel can test account access and display balance and positions using read-only GET requests without enabling execution.

## Start

Requirements: Python 3.10 or newer.

1. Run `CryptoEdge.bat`, or install `requirements.txt` and run `python app.py`.
2. Use Settings for PAPER/LIVE display mode and BloFin credentials.
3. Use Analysis to start scanning. Trading is a separate, explicitly confirmed control.

The default interface is PySide6. Since 19.9.0 the default layout is the DESK/SCAN/LAB shell (`config.UI_DESK_V2 = True`); set it to `False` to roll back to the previous 7-tab layout, which remains fully intact in the codebase. Tkinter remains only as an emergency fallback if PySide6 cannot start.

Since 19.13.0 the shell has six tabs: DESK, SCAN, LAB, REPLAY, HISTORY, SET. LAB (analysis), REPLAY (historical replay) and SET (settings) now share the same flat, dark card style as DESK/SCAN (`theme.py` — 2px radius, hairline borders) instead of the old large-header page layout; they reuse the exact same underlying data/refresh logic, only the widget layout changed. HISTORY is a new tab showing closed positions and performance-by-side (previously only reachable from the legacy 7-tab shell's Performance page).

## DAYTRADING_V2 gates

Since 19.10.3, the V2 entry gate is less strict than before, based on real PAPER session telemetry:

- `config.DAYTRADING_V2_BIAS_MIN_AGREE = 2` — the 1D/4H bias needs a majority (2 of 3: price vs. EMA200, EMA50 vs. EMA200, SuperTrend) instead of unanimous agreement on all three. Set to `3` to restore the old, fully unanimous requirement.
- `config.DAYTRADING_V2_ALLOW_4H_ANCHOR_WITHOUT_1D = True` — pairs without 200+ daily candles (recently listed pairs) no longer get hard-rejected outright; the 4H trend becomes the direction anchor instead, still subject to the same 1H swing/15m trigger/5m veto agreement as the standard path. Set to `False` to go back to hard-rejecting pairs without full 1D history.

`config.DAYTRADING_MIN_GATE_VOTES` (default `3`) is a separate setting for the legacy V1 engine (`daytrading_engine.py`), which is kept untouched for A/B comparison in Historical Replay and is not used by the live V2 engine.

### V2 sizing vs. the portfolio risk cap

V2 position size is a fixed 7.5% of equity as margin (`DAYTRADING_V2_MARGIN_PCT_FIXED`, range 5–10%) times leverage, independent of stop-loss distance and independent of dummy `strength=0.75`. From 19.25.2 the SL-dollar check `_portfolio_open_risk_ok` is skipped for V2 `capital_pct` (a 3% SL on 75% notional was exhausting the 2.5% portfolio cap on a single empty book). Gross/margin caps stay. `DAYTRADING_V2_MAX_RISK_PCT_PER_TRADE = 0` (off). Set it to e.g. `1.0` if you want the old shrink-to-fit SL$ ceiling.

## Control Center

PySide6 includes System Readiness, watchdog, Why No Trade, Signal Lifecycle, Position Protection, Expected vs Actual execution, entry reservations, router diagnostics, Expected Net R status, and PAPER session export.

Support exports exclude API keys, secrets, passphrases, passwords, and tokens. Export is blocked in LIVE mode.

## Research tools

The repository retains event backtesting, walk-forward analysis, Monte Carlo analysis, performance metrics, and log analysis. These tools do not run as part of `app.py` and do not alter LIVE/PAPER decisions.

Historical Daytrading Replay is available from the native UI or command line:

`python run_historical_replay.py --days 90 --symbols BTC ETH SOL --oos 0.30`

It downloads public closed candles and funding from BloFin, caches them in
`data/replay`, and writes chronological IS/OOS reports to `reports/replay`.

## Tests

Run:

```text
python run_tests.py
```

Current validated suite: 100 tests.
