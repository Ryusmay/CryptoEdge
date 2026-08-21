# CryptoEdge Native UI v17

This release replaces the compact v16 Qt dashboard with a native Dark Modern
trading console while preserving the existing runtime, strategy, execution and
risk modules.

## UI

- Ten native PySide6 workspaces: Overview, Market Scanner, Opportunities / Signals,
  Analysis Workspace, Execution Queue, Positions, Risk Center, Performance,
  Logs & Events and Settings.
- Persistent DEMO/LIVE, Engine and Trade states.
- Uptime, next-cycle countdown, source freshness, market regime and risk strip.
- Native equity and drawdown chart (no web view and no synthetic chart points).
- Account KPI cards, top opportunities, queue, open positions and Event Center.
- LONG, SHORT, NEUTRAL and alert-level colour semantics.
- Settings edit the existing `settings_store` keys.

## Data integrity

- `DataAdapter` is the single UI projection.
- DEMO positions come from PaperTrader.
- LIVE positions come from the read-only Blofin exchange snapshot.
- Scanner, analysis, metrics, equity history, rejects and events come from the
  existing runtime state/logs.
- Empty or unavailable backend fields remain empty (`—`); the UI creates no mock
  opportunities, positions, events or chart values.

## Verification

- Python syntax/import compilation: passed.
- PySide6 offscreen construction: 10 pages and 10 navigation entries, passed.
- Full project unit/regression suite: 68/68 passed.
