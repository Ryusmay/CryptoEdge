# CryptoEdge v17.8 — UI consolidation and mode-aware trading start

- START BOT no longer displays a LIVE warning in DEMO.
- The action button is labelled START DEMO or START LIVE from active account mode.
- LIVE account view with disabled execution blocks Start and explains the safety gate.
- Real LIVE execution retains an explicit high-severity confirmation.
- Reduced top-level navigation from 11 entries to 8 without removing data:
  - Market Scanner + Opportunities are now tabs under Markets.
  - Execution Queue + Positions are now tabs under Trading.
  - Operational Control + Risk Details are now tabs under Control Center.
- Fixed asset drill-down navigation after the menu reorganization.
- Full regression suite: 98/98 passed.
- Consolidated PySide6 full-window smoke test: passed.
