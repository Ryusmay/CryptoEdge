# CryptoEdge

[![tests](https://github.com/Ryusmay/CryptoEdge/actions/workflows/tests.yml/badge.svg)](https://github.com/Ryusmay/CryptoEdge/actions/workflows/tests.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.10%20%7C%203.12-blue.svg)](https://www.python.org/)

**Paper-first research desk for crypto USDT-M perpetuals.**
Python engine + native PySide6 UI. Live order placement stays **off** unless you explicitly enable it.

CryptoEdge is a personal quantitative trading workstation: multi-timeframe daytrading (V2), hard risk gates, purged walk-forward, and a modular monolith so strategy, risk, and execution stay testable in isolation.

Ready for **ChatGPT Codex**, **GitHub Copilot**, and **Claude Code** via [`AGENTS.md`](AGENTS.md).

![CryptoEdge paper desk (v20.45, DEMO)](docs/images/desk-v20.45.png)

Native DESK: open positions, candidates, BloFin watchlist, session PnL and risk. Screenshot is **PAPER/DEMO** with an empty book — filled positions wait on modular migration ([ROADMAP](ROADMAP.md) M0).

> Not financial advice. Defaults to **PAPER**. API keys never belong in git (`.env` is gitignored).

## Why this repo exists

Most crypto “bots” mix signals, sizing, and exchange I/O in one loop. This project is the opposite:

- **Strategy** sees a market snapshot, not your wallet or LIVE flag
- **Risk** owns size, cluster caps, and vetoes
- **Execution** owns fills, SL/TP, reduce-only (paper adapter today)
- **Replay / walk-forward** reuse the same decision path as the desk

Architecture notes live in [`docs/architecture/`](docs/architecture/) (ADR-001 modular monolith, migration stages 0–7).

## How this codebase defends itself

The gates in `tools/` are the load-bearing part of the workflow, more than any
single strategy parameter. They exist because a trading codebase fails quietly:
a refactor that changes one number produces plausible output forever.

- Each gate replays a fixed set of cases and diffs the result against a
  committed baseline in `tests/baselines/`. A change that alters behaviour
  cannot land unnoticed.
- Every baseline carries a SHA-256 **config fingerprint** over the scalars in
  `config.py`. Change any threshold and every gate says so before it says
  anything else.
- Comparators use a **denylist**, not an allowlist, so a newly added field is
  compared by default instead of being silently ignored. Two real bugs got in
  through allowlists before this was inverted.
- A gate is only trusted after **sabotage**: deliberately break the thing it
  guards and confirm it fires. A gate that has never failed on purpose has not
  been shown to work. One real defect produced an output file of *identical
  length* to the correct one — length and row counts are not gates, byte
  comparison is.
- One gate is different in kind. The seven characterisation gates ask *did the
  result change*; `tools/decision_parity.py` asks *did the two tracks decide the
  same thing*. It feeds the live position manager and the replay bar processor
  the same position state and the same observation, then compares the resulting
  stop, partial flags, close and reason — never the R. Two tracks can reach the
  same average by different decisions, and a gate on the result stays silent
  while they do.

Worked example, from the changelog: parallelising the dataset builder had to
produce a byte-identical file. The gate compared SHA-256 of sequential vs
4-process output (identical), then the same run with reversed symbol order as
sabotage — same 53 003 bytes, different hash, caught.

Second worked example, and the reason the parity gate exists: replay and
runtime already shared their decision core, so every earlier check passed. What
differed was what each side *did* with the shared decision — the runtime moved
the break-even stop 0.18% past entry, the replay left it at entry, and on a
median stop distance of ~0.65% of price that is ~0.28R per break-even trade.
The gate reported it as `sl replay=100.0 runtime=100.18` in four of six
scenarios before the fix and zero of six after; undoing the fix turns four red
again.

## What the measurements currently say

A research desk that hides its own negative results is not a research desk.
Everything below is measured on frozen replay bundles under one configuration,
with the tooling in `tools/`.

- **The expectancy model was wrong on both sides, in opposite directions.**
  Modelled cost was overstated by an order of magnitude; modelled gross was
  wrong in *shape*, not just in parameters. The two errors partly cancelled,
  which is why every number the system produced looked plausible for months.
- **Measured venue microstructure**, not assumed: average spread equals exactly
  one tick on each of three independent instruments (BTC 0.0133 bps, XRP 0.7316,
  ZEC 0.1229) against a modelled flat 4 bps. Top-of-book depth dwarfs the
  modelled order, so market impact is ~zero — the model was charging
  participation against a whole candle's turnover, the wrong quantity entirely.
- **The replay was testing a different exit ladder than the bot executes.**
  Partial fractions were defined in three places with three meanings: the live
  bot closes 50% at TP1 and 50% *of the remainder* at TP2 (so 50/25/25), the
  replay had 0.5/0.3 hardcoded as fractions *of the original* (50/30/20), and
  the entry-pricing model ignores the third rung entirely and values the whole
  post-TP1 remainder as if it went to TP2. Found by building a gate that
  compares the two tracks decision-by-decision rather than by result.
- **Measured `p(TP1)` = 0.091** on 1224 trades across 49 symbols and 180 days.
  The model's prior is 0.55. An earlier version of this file reported 0.009;
  that number came from a measurement tool reading the wrong field, and the
  correction is in `docs/changelog/v20.68.0.md`. Both the wrong number and its
  retraction are kept on the record.
- **Mean realised R is indistinguishable from zero to slightly negative**
  (−0.0705 ± 0.0545 on 1224 trades, 49 symbols, 180 days). The interval assumes
  independent trades, which 49 correlated coins in one window are not, so the
  true interval is wider than stated. No feature known at entry separates
  winners from losers in this sample.
- **Longer horizons expose more move but do not yet capture it.** Going from
  10/10 h to 24/48 h took `MFE ≥ 1.0R` from 10.7% to 31.5% of trades, while
  stop-outs went from 9.8% to 33.7%. The amplitude is there; the exit ladder
  does not harvest it.

Raw datasets and long-form write-ups live in `docs/analysis/`, a working
directory kept out of this repository — publishing half-finished measurements
invites quoting numbers whose caveats nobody read. The headline figures are
reproduced above and in `docs/changelog/`, and every tool that produced them is
here, so any of it can be recomputed from the frozen bundles in `data/replay/`.

No claim here is a claim about live performance. They are statements about a
replay of frozen data, published beside the code that produced them so they can
be re-run and contradicted.

## Features

| Area | What you get |
|---|---|
| Engine V2 | 1H bias, 15m trigger, 5m veto, 4H context; major / alt / XAU profiles |
| Risk | Min 5% equity size floor, gross/cluster caps, loss-streak pause |
| Research | Purged walk-forward, historical replay, MAE/MFE, outcome datasets |
| Gates | Seven characterisation gates with committed baselines and config fingerprints, plus a decision-parity gate between replay and runtime |
| Desk UI | DESK / SCAN / LAB / REPLAY / HISTORY / SET |
| Venue | BloFin USDT-M for paper/live path; Binance public WS as confirm |
| Agents | `AGENTS.md` + Copilot instructions for Codex / Claude / Copilot |

## Quick start

Python **3.10+**.

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # optional keys; leave empty for paper
python app.py
```

Windows: `CryptoEdge.bat` or `URUCHOM.bat`.

1. **SET** — PAPER on, paste BloFin keys only if you need account reads
2. **Analysis** — start the scan
3. **Trading** — separate, confirmed control (not auto-armed)

```bash
python run_tests.py     # 1383 tests
```

### Research tooling

```bash
python tools/parity.py --require-aligned                # portfolio replay vs baseline
python tools/align_bundles.py --days 30 --symbols BTC ETH SOL XRP ZEC
python tools/risk_gate.py                               # also: exit_ entry_ exec_ fill_ restart_
python tools/decision_parity.py                         # replay vs runtime, decision by decision
python tools/decision_parity.py --sabotaz be            # same gate, with the fix undone
python tools/compare_arms.py armA.jsonl armB.jsonl      # paired, symbol-clustered bootstrap
python tools/fetch_bundles.py --days 180 --top 50       # frozen candles + funding from BloFin
python tools/outcome_dataset.py --days 90 --workers 4   # one row per trade
python tools/horizon_compare.py --stary A --nowy B      # two datasets, causes separated
python run_historical_replay.py --days 90 --symbols BTC ETH SOL --oos 0.30
```

`outcome_dataset.py` writes one row per trade with entry-known features and
outcome, one symbol at a time and **without portfolio constraints**, so the
population is not pre-trimmed by slot limits. `--workers N` spreads it across
processes; the output is byte-identical to the sequential run, and that is
asserted by a gate rather than assumed.

There are deliberately **two levels of measurement**, and confusing them is the
easiest way to misread this repository. `outcome_dataset.py` runs the
single-symbol replay, whose entry condition is a direction and no rejection
reason — no risk manager, no position cap, no expected-value filter. It measures
*signal quality*, and its population is everything the strategy emits.
`tools/parity.py` runs the portfolio replay, which applies the expected-net-R
gate and the shared slot limit, so it measures something closer to *what the bot
would have taken*. Neither is wrong; quoting one as the other is.

Candles come only from BloFin — the venue the strategy actually trades. Index
or spot candles from a data aggregator are a different instrument: different
wicks, different spread, no funding. Backtesting on them would measure a market
that does not exist.

### ChatGPT Codex

Open this repository in Codex (or `chatgpt.com` with GitHub connected). The agent reads [`AGENTS.md`](AGENTS.md) for boundaries: no secrets, paper-first, no strategy/exchange mixing.

```text
Task example: add a MarketDataPort adapter for Bybit klines; do not change V2 TP/SL.
```

## Repository layout

```
app.py / runtime.py     composition root
daytrading_engine_v2.py live strategy
cryptoedge/             domain, ports, adapters (modular split)
tools/                  characterisation gates + measurement tools
tests/baselines/        committed gate baselines with config fingerprints
data/replay/            frozen bundles the gates replay against
AGENTS.md               instructions for Codex / Claude Code / Copilot
docs/architecture/      ADR + migration plan
docs/changelog/         one file per version, with the evidence
ROADMAP.md              milestones M0–M5
tests/                  gates, parity, rate-limit
walk_forward_v2.py      purged folds
```

## Status

| | |
|---|---|
| Version | **20.73.0** |
| Tests | 1383, green on 3.10 and 3.12 |
| Default | PAPER, `LIVE_EXECUTION_ENABLED=False` |
| License | MIT |
| Migration | ADR-001 stages 0–5 closed; stage 6 (runtime/replay decision parity, EventClock, UI decoupling) in progress |
| Model | Expectancy model refuted by measurement; redesign onto a measured three-state base rate is open work |
| Plan | [ROADMAP.md](ROADMAP.md) — modules → V2/replay freeze → VPS → Tauri hookup → strategy brain |

### Known open problems

Listed because a reviewer will find them anyway, and finding them listed is a
better signal than finding them hidden.

- `market_regime` is `UNKNOWN` for every replay trade. The replay calls
  `apply_market_gates(..., "UNKNOWN")` with a literal, and that function never
  writes the field at all, so any regime-conditioned claim is unsupported. It is
  not a labelling fix: the regime feeds the expectancy calibrator, so supplying
  a real one changes entry decisions and has to be measured as its own arm.
- Two of the four known replay/runtime divergences are still open. The trailing
  anchor comes from a confirmed 1H swing in replay and from a chandelier stop in
  runtime, because the signal key the runtime prefers has **no producer anywhere
  in the repository**. And `refresh_volume_ranks` is only called from the live
  candidate scan, so in replay every symbol outside BTC/ETH/SOL falls back to
  the "alt" profile — different thresholds than the same symbol would get live.
- `_gross_expected_r` ignores `frac_tp2` and prices the entire post-TP1
  remainder as if it reached TP2. The trailing rung does not exist in the model
  that decides whether to enter.
- The `LOOKAHEAD_*` check on `MarketSnapshot` cannot fire in the live path: the
  runtime adapter builds the snapshot with `frames={}`, and the check skips
  empty frames. Live is in fact protected one layer lower — the feed drops
  candles whose `confirm` flag is 0 and the last forming bar — but the guarantee
  lives in the data source, not in a gate, so changing the feed would not turn
  anything red.
- The Tauri frontend has never been built or tested in CI.

This is an active research codebase, not a hosted product. Issues and PRs that improve tests, docs, or adapters are welcome. Strategy “edge” claims belong in walk-forward reports, not marketing — and in this repository the current walk-forward reports do not support one.

## Disclaimer

Trading perpetual futures can wipe a deposit. The author is not a broker. Run paper first. Rotate any key that ever touched a zip, chat, or screenshot.
