# CryptoEdge v17.25 — Replay Performance and Resume

- As-of candle lookup now uses binary search (`bisect_right`) instead of
  scanning the complete timestamp history for every signal and timeframe.
- A 26k-bar microbenchmark reduced 5,000 boundary lookups from about 10.6 s to
  0.002 s on the test machine. Indicator calculation and API download still
  dominate total runtime, so this is not an end-to-end 5,000x speed-up.
- Completed IS/OOS results are cached per symbol under `data/replay/results`.
- The result cache is invalidated when strategy code, Daytrading configuration,
  costs, OOS split or candle range changes.
- A partial checkpoint is written after every completed or skipped symbol to
  `reports/replay/checkpoint_*.json`.
- Restarting the same replay restores completed symbol results instead of
  recalculating them.
- Candle cache expires after 24 hours, while `Pobierz dane ponownie` forces a
  fresh download and fresh calculations.
- Added regressions for the bounded as-of slice and cache expiry.
