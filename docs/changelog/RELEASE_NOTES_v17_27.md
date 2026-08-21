# CryptoEdge v17.27 — Daytrading Fibonacci visibility

- Daytrading Engine now emits a complete `trend_fib` snapshot for Analysis Workspace.
- The map is calculated before ADX, CHOP, 15m alignment and 5m timing exits, so
  monitored/no-trade candidates retain valid Fibonacci context.
- For neutral/conflicting HTF bias the display direction is inferred from the
  last ordered 15m impulse. It is display/confluence context only and cannot
  create or reverse a trading signal.
- The map includes 0.236, 0.382, 0.5, 0.618 and 0.786 levels, retracement,
  zone, direction and confluence state.
- The daytrading `analysis_board` now preserves `trend_fib` instead of dropping it.
- Symbols outside the active liquid candidate basket are not fetched solely to
  decorate the UI; for them Fibonacci remains unavailable by design.
