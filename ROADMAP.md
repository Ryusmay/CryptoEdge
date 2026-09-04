# Roadmap

Personal research desk → maybe a product. **One operator. PAPER until the strategy (or the strategy-brain) is proven.**  
~6 hours/day. Sequence is strict: **modules → freeze V2/replay → VPS → plug Tauri → brain.** Parallel = chaos again.

Live execution stays off. 70% win rate is a **research target for the brain**, not a LIVE gate. With 2–3R targets, expectancy can be green well below 70% WR.

Public GitHub is a **showcase + version tags**, not a community support queue.

See also [`docs/architecture/MIGRATION_PLAN.md`](docs/architecture/MIGRATION_PLAN.md) (stages 0–7) and [`AGENTS.md`](AGENTS.md).

---

## Where it actually stands — 2026-09-04, v20.73.0

The milestones below are unchanged in intent. What changed is that the thing
they were sequencing towards turned out to need re-establishing first.

**The expectancy model failed measurement on both sides.** Cost was overstated
by an order of magnitude (a flat 4 bps spread against measured 0.0133–0.73 bps,
and market impact charged against a whole candle's turnover instead of book
depth). Gross was wrong in shape, not in parameters: measured `p(TP1)` = 0.091
against a prior of 0.55, on 1224 trades across 49 symbols and 180 days. (An
earlier revision of this file said 0.009; that came from a measurement tool
reading the wrong field and is retracted in `docs/changelog/v20.68.0.md`.) The
two errors partly cancelled, which is why the outputs looked plausible. Mean
realised R is **−0.0705 ± 0.0545**, and the interval is optimistic because it
assumes 1224 independent trades where 49 correlated coins share one window. No
feature known at entry separates outcomes.

**And the simulator was not simulating the bot.** Replay and runtime share the
decision core, so every result-level gate passed — while the two tracks applied
that shared decision differently. The break-even stop landed 0.18% apart, the
trailing anchor came from two different calculations, and the partial ladder
was 50/30/20 in replay against 50/25/25 live because the same config name meant
*fraction of the original* on one side and *fraction of the remainder* on the
other. None of this was visible in any number the project published, because
every number came from the side that was wrong. It surfaced only after building
a gate that compares the two tracks decision-by-decision
(`tools/decision_parity.py`, v20.72.0).

The direction chosen is to unify **toward the live bot**: the simulator imitates
reality, not the other way round. Two of four divergences are closed
(v20.72.0, v20.73.0); the trailing anchor and the volume-rank profile remain.

This does not move M1 later so much as change what M1 has to prove. “Freeze V2”
now means *first* rebuild the expectancy model onto a measured three-state base
rate with confidence intervals and an `n_min` fallback — no per-feature
multipliers, because none of them earned their place in the measurement.

**M0 status:** ADR stages 0–5 closed. Stage 6 (runtime/replay decision parity,
EventClock, UI decoupling) in progress; the decision-parity gate now exists and
is green, so the stage has a measurable exit condition for the first time.
Stage 7 not started.

Open parity defects, in the order they will be taken: the trailing anchor has
two sources (a confirmed 1H swing in replay, a chandelier stop live, because the
signal key the runtime prefers has no producer in the repository); the volume
rank is never refreshed in replay, so every symbol outside BTC/ETH/SOL falls
back to the "alt" profile; and `market_regime` is `UNKNOWN` for every replay
trade — that last one is not a labelling fix, because the regime feeds the
expectancy calibrator and therefore changes entry decisions.

The earlier claim that "the TP1 flag never fires" was **wrong** and is retracted
in `docs/changelog/v20.68.0.md`: the flag fires, the tool that read it was
looking at the wrong field name.

**Frontend:** the Tauri shell exists and has still never been built or tested
(`npm test`, `npm run build`, `cargo build` all unrun). M3 is honest about
being “already built” only in the sense that the code exists.

---

## M0 — Now → Oct 2026 — modular monolith

Finish ADR stages 5–7. Dual-path ends or becomes an explicit adapter.

- Execution only through the port (PAPER; LIVE path remains disabled)
- `close_policy` owns exits
- `DecisionPipeline` in runtime **and** replay
- `python run_tests.py` + V2 parity **identical**
- No TP/SL “while we’re here” retunes

**Done when:** `strategy/` does not import `config` or exchange clients; the old feeder exists only behind an adapter.

---

## M1 — Nov–Dec 2026 — freeze V2 + replay

“The strategy works” must be **reproducible**.

- Paper tick = replay OHLC (same fill model)
- Walk-forward 90D: BTC ETH SOL + 3 alts, **per profile**
- Universe as **SET presets** (`majors` / `top30` / `all USDT-m`) — not 450 pairs on REST on day one
- Report: n, WR, avg R, net R, DD, % BE — not WR alone

**Done when:** the same JSON comes from the desk and from `walk_forward_v2.py`. No gut-feel V2 commits.

LIVE: no. Brain: not inside `evaluate()`.

---

## M2 — Q1 2027 — VPS + market data

24/7 paper, WebSocket off the home IP (BloFin 429).

- `cryptoedge.service`, PAPER, logs on disk
- Full-universe BloFin WS only when preset is `all`
- Backup klines: Bybit/Binance adapter (asyncio queue), **never** for fills/SL/TP

**Done when:** 48h paper on VPS without a manual restart; health visible on the desk.

---

## M3 — after M0 — Tauri (already built)

The Tauri/React shell **exists**. It is waiting for the module boundary, not a rewrite.

- Wire it to `apps/api` (DTO projection only — no Python engine imports)
- Same SET/CLOSE/PAPER path as the desk
- PySide6 stays as fallback until button parity

Do **not** touch `frontend/` during stages 5–7.

---

## M4 — Q2 2027 — strategy brain (sidecar)

Picks a **strategy class / profile** for the regime. Does not guess the next candle.

- In: same `MarketSnapshot` + walk-forward OOS
- Out: `skip | major_v2 | alt_v2 | no-trade` + confidence
- 30-day **shadow**: logs only, does not trade
- Rules / LightGBM first; Kronos later or never

**Done when:** shadow lift vs plain V2 on the same OOS. No lift → brain stays off the desk.

70% WR is measured **here**, OOS, with costs. If net R dies at 70% WR, reject it.

---

## M5 — H2 2027 — product (maybe)

Only if M1 is green **and** M4 does not wreck it.

- Hosted paper or license + customer VPS (keys stay with the customer)
- Not a zip-with-DRM
- Pricing only after 90 days of VPS paper

---

## Weekly split (~36h)

| | |
|---|---|
| ~25h | current milestone (now: **M0 only**) |
| ~6h | paper + logs, no gut patches |
| ~5h | GitHub: one logical commit, not fifteen doc-only PR cosmetics |

Codex/Claude: adapters and tests. Human: ADR and V2 freeze.

---

## Frozen until M1 is done

- LIVE
- 450 pairs on REST
- New TP ladder
- Brain in the live loop
- Tauri rewrite
- Selling
