# CryptoEdge v17.5 — PySide6 Control Center / PAPER

## UI parity restored

- Added System Readiness and watchdog status to the PySide6 Risk Center.
- Added Why No Trade rejection aggregation and Position Protection status.
- Added Signal Lifecycle, Expected vs Actual execution, and entry-slot reservations.
- Added PAPER session export directly from PySide6.
- Added Engine Router, residual momentum, Expected Net R calibration, and decision telemetry to Analysis Workspace.
- Connected PySide6 to the existing `control_center` backend rather than duplicating trading logic.

## Security

- PAPER support exports now exclude every config key containing KEY, SECRET, PASSPHRASE, PASSWORD, or TOKEN.
- PAPER export is blocked while the application is in LIVE mode.
- BloFin connection preview remains read-only and never calls execution methods.

## Validation

- Python compilation: passed.
- PySide6 offscreen window smoke test: passed.
- Full regression suite: 94/94 passed.
