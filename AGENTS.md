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
7. Never run `tools/calibrate_expectancy.py`. It mutates production calibration state merely by being run, which makes any measurement taken afterwards uninterpretable.
8. Do not run `git checkout -- <file>` while there are uncommitted changes.

## How to make a claim here

This repository treats an unverified number as worse than no number. Four rules
follow from that, and they are not stylistic:

- **Gate before move.** Before changing behaviour, run the relevant gate in
  `tools/` and record the baseline. After changing it, run the gate again and
  publish the diff. A change whose effect was never measured is not finished.
- **Sabotage the gate.** A gate is trustworthy only after you deliberately break
  the thing it guards and watch it fire. State the sabotage and its result. Note
  that file size and row counts are not gates: a real defect once produced a
  file of *identical length*, and only a byte comparison caught it.
- **Attribute one cause at a time.** If two changes land together, measure them
  apart — extract the previous version of a file with `git show HEAD:<path>` and
  run it, rather than reasoning about which change probably did what.
- **“I don’t know” beats “I know wrongly.”** Missing data must surface as `None`
  or an empty result, never as a zero that reads like a measurement. A broken
  funding accrual reported `funding_r = 0.0` on 560 of 560 trades, which read as
  “funding costs nothing” and meant “there was nothing to count”.
- **Retract in place.** A number that turns out to be wrong is corrected where it
  was published, with the evidence for the correction, not quietly dropped.
  `docs/changelog/v20.71.0.md` carries a claim of mine that the next version had
  to withdraw; both the claim and the withdrawal stay, because a changelog that
  only ever gets more right is not a record of anything.

### One rule about where behaviour is allowed to live

**Adapters supply data. Policy lives in one shared module.**

Replay and runtime already share their decision core, which is why every
result-level check passed while the two tracks quietly diverged. What differed
was what each adapter *did* with the shared decision: the runtime moved the
break-even stop 0.18% past entry in its own trailing code, the replay wrote the
bare entry; the replay had partial fractions hardcoded in a function signature
while the live path read them from `config.py`, with the two numbers meaning
*fraction of the original* and *fraction of the remainder* respectively.

If you find yourself writing a policy decision inside `paper_trader.py` or
inside `daytrading_backtester.py`, it belongs in `v2_trade_lifecycle.py` or in
`config.py` instead. `tools/decision_parity.py` is the gate for this: it hands
both tracks the same position state and the same observation and compares the
resulting stop, partial flags, close and reason — never the R, because two
tracks can reach the same average by different decisions.

Measured values belong in `docs/analysis/*.json` or `*.md` **with provenance** —
what was measured, when, against what — and never as a fresh constant in
`config.py`. Every claim carries a file and line number, or the command that was
run. Distinguish measured from assumed from inherited.

`docs/analysis/` is a local working directory and is **not published**. Anything
that should survive for a reader outside this machine goes into
`docs/changelog/<version>.md` with the numbers written out, not linked.

## Layout

- `app.py` / `runtime.py` — composition root
- `daytrading_engine_v2.py` — live V2 strategy
- `cryptoedge/` — domain, ports, adapters
- `tools/` — characterisation gates and measurement tools
- `tests/baselines/` — committed gate baselines, each carrying a config fingerprint
- `tests/` — architecture boundaries, rate limits, parity baselines
- `docs/analysis/` — measured results with provenance (local only, not published)
- `data/replay/` — frozen bundles the gates replay against
- `walk_forward_v2.py` — purged folds

## Checks

```bash
python run_tests.py
python tools/parity.py            # portfolio replay vs committed baseline
python tools/risk_gate.py         # also: exit_ entry_ exec_ fill_ restart_
python tools/decision_parity.py   # replay vs runtime, decision by decision
```

`decision_parity.py` must be green before and after any change to exits. It is
the only check that fails when the two tracks disagree while both still look
sane on their own.

If you touch strategy, risk, or execution, say whether `tests/baselines/parity_v2.json` is expected to stay identical — and if it is not, run the gate and paste the diff.

A gate that reports `RÓŻNI SIĘ` only in the config-hash line means the decisions
did not change and the fingerprint did. That is a rebaseline, not a regression.
A gate that reports changed decisions is a claim you now have to defend with a
measurement.

Rewriting a baseline is a deliberate act, never a way to make a red gate green.
Record the old numbers and the new ones side by side in `docs/changelog/`, and
say which change caused which part of the difference.

## Style

Match existing Python: no new framework, no drive-by refactors, no comments that restate the code. Prefer a small adapter over a new engine.
