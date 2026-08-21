# CryptoEdge v17.7 — Dedicated Control Center

- Added a dedicated Control Center navigation page in the active PySide6 UI.
- Added an explicit DEMO (PAPER) / LIVE (BLOFIN) account-mode selector.
- Mode changes are blocked while analysis/trading is active or positions exist.
- LIVE selection requires complete BloFin credentials.
- Account mode remains separate from the `LIVE_EXECUTION_ENABLED` safety gate.
- Consolidated Readiness, Why No Trade, Signal Lifecycle, Position Protection,
  Expected vs Actual, entry reservations, and PAPER export on the new page.
- Full regression suite: 96/96 passed.
- PySide6 offscreen full-window smoke test: passed.
