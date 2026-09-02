# Agent instructions (ChatGPT Codex, Claude Code, Copilot)

You are working on **CryptoEdge**: a paper-first Python desk for crypto USDT-M perpetuals.

Read [`docs/architecture/ADR-001-modular-monolith.md`](docs/architecture/ADR-001-modular-monolith.md) and [`docs/architecture/MODULE_BOUNDARIES.md`](docs/architecture/MODULE_BOUNDARIES.md) before changing architecture.

## Non-negotiable

1. Do **not** commit `.env`, `logs/secrets.bin`, API keys, or dumps of `BLOFIN_*`.
2. Default path is **PAPER**. Do not turn on live order placement unless the user asks.
3. Do not mix venues on position levels: fills/SL/TP stay BloFin; Binance WS is confirmation only.
4. `strategy` must not import exchange clients, UI, or LIVE flags. It consumes `MarketSnapshot`.
5. Dual-path (legacy + `cryptoedge/` ports) is intentional until migration stage 7. Do not delete the old path without parity.
6. Do not retune V2 entry/TP/SL “while refactoring”. Strategy changes need a walk-forward or the existing parity gate.

## Layout

- `app.py` / `runtime.py` — composition root
- `daytrading_engine_v2.py` — live V2 strategy
- `cryptoedge/` — domain, ports, adapters
- `tests/` — architecture boundaries, rate limits, parity baselines
- `walk_forward_v2.py` — purged folds

## Checks

```bash
python run_tests.py
```

If you touch strategy, risk, or execution, say whether `tests/baselines/parity_v2.json` is expected to stay identical.

## Style

Match existing Python: no new framework, no drive-by refactors, no comments that restate the code. Prefer a small adapter over a new engine.
