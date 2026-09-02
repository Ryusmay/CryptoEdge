# Roadmap

Personal research desk → maybe a product. **One operator. PAPER until the strategy (or the strategy-brain) is proven.**  
~6 hours/day. Sequence is strict: **modules → freeze V2/replay → VPS → plug Tauri → brain.** Parallel = chaos again.

Live execution stays off. 70% win rate is a **research target for the brain**, not a LIVE gate. With 2–3R targets, expectancy can be green well below 70% WR.

Public GitHub is a **showcase + version tags**, not a community support queue.

See also [`docs/architecture/MIGRATION_PLAN.md`](docs/architecture/MIGRATION_PLAN.md) (stages 0–7) and [`AGENTS.md`](AGENTS.md).

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
