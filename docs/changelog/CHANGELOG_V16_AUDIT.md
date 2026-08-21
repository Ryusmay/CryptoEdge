# CryptoEdge PySide6 v16 — audit fixes

## Safety and state

- Blocked `PaperTrader` from creating synthetic local fills whenever `PAPER_TRADING=False`, even if the LIVE feature flag is enabled.
- Prevented Start/Resume from clearing kill-switch, recovery, reconciliation, drift, orphan, daily-loss, and drawdown halts.
- Recovery now halts new entries when an exchange-only orphan position is detected.
- Recovery preserves the more protective persisted stop (higher for LONG, lower for SHORT) instead of overwriting it with an older local snapshot.
- Restored positions reconnect their trailing-stop callback to the protection manager, so later SL tightening is propagated after restart.
- Open-position snapshots now preserve contract quantity, actual notional, contract value, funding, partial-TP stage/flags, TP plan, engine, original size, and actual risk.
- Active risk filters fail closed when reconciliation, portfolio, correlation, expected-R, dynamic-spread, or order-book-impact checks cannot be evaluated.

## Sizing and accounting

- Partial closes quantize contract amounts down to the instrument lot size and derive the accounting fraction from the executable contract quantity.
- Partial-close history records the closed contracts/notional rather than the remaining position quantity.
- Entry, mark, TP/SL, equity, strength, and sizing inputs reject non-finite or non-positive values where applicable.
- Decimal accounting sanitizes `NaN` and infinite values before they can contaminate fees, funding, equity, or PnL.

## Native PySide6 UI

- Position size now reads `size_usd`, matching the actual `Position` model.
- Position snapshots are copied under the trader lock before the UI reads them, avoiding concurrent mutation by the bot thread.
- Restored separate Execution Queue and Open Positions execution workspace.
- Restored scanner sorting by score, 24h, 7d, and price plus selected-asset and MTF summaries.
- Restored market context, decision funnel, latest events, closed-trade history, Trade Replay, and Strategy Health.
- Expanded Risk Center with capital, daily PnL, margin utilization, halt reason, and active warnings.
- Expanded System view with source status, cross-source divergence, runtime details, and event history.
- Added a distinct `STOP TRADING` action that pauses entries while analysis continues.

## Regression tests

- Added coverage for the LIVE/PaperTrader boundary.
- Added coverage for persistent kill-switch and reconciliation halts.
- Added coverage for exchange-orphan recovery halting.
- Added lifecycle round-trip coverage for partial exits, contract state, funding, TP plan, and trailing protection after restart.
- Added coverage for invalid/non-finite prices and Decimal values.

Verification: Python compile check passed; 72/72 tests passed in the audit environment. No live BloFin execution was enabled or invoked.
