# CryptoEdge v17.9 — live UI state synchronization

- PySide6 now reads the latest cycle snapshot directly from the running logger.
- `bot_state.json` is a fallback rather than a hard dependency for the UI.
- `bot_state_alt.json` is supported and the newest disk snapshot is selected.
- Runtime heartbeat is used for data freshness when a state file is unavailable.
- UI refresh failures are visible as DATA UI ERROR and recorded in `last_ui_error`.
- Verified a running in-memory cycle appears in the Market Scanner without disk state.
- Full regression suite: 100/100 passed.
