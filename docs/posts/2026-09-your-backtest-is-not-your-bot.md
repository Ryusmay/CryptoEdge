# Your backtest and your bot share a decision core. That does not make them agree.

My replay and my live bot imported the same decision function. Every
result-level test passed for months. They were still trading two different
strategies, and the difference was worth about 0.28R on every trade that got
stopped at break-even.

This is the write-up of how that got found, what it cost, and why the gate
that found it is a different kind of gate than the seven I already had.

## The setup

CryptoEdge is a paper-first research desk for crypto USDT-M perpetuals. One
operator, Python, modular monolith. The relevant architectural claim is the
ordinary one: strategy logic lives in one place, and both the live runtime and
the historical replay call into it. Adapters supply data; policy is shared.

That claim was true. `v2_trade_lifecycle.decide_v2_lifecycle` really was the
single decision core. Both tracks really did call it.

I had seven characterisation gates — `parity`, `risk_gate`, `entry_gate`,
`exec_gate`, `restart_gate`, `fill_gate`, `exit_gate`. Each one replays a fixed
set of cases and diffs the output against a committed baseline. Each baseline
carries a SHA-256 fingerprint over every scalar in `config.py`, so changing a
threshold announces itself before anything else does. Comparators use a
denylist, not an allowlist, so a newly added field is compared by default —
two real bugs got in through allowlists before that got inverted.

1383 tests, green on 3.10 and 3.12.

All of it was measuring the wrong thing.

## What the gates could not see

Every one of those gates asks the same question: **did the result change?**

That question has a blind spot, and it is not subtle once you say it out loud.
Two tracks can produce the same average R by making different decisions. A gate
on the result stays silent while they do.

So I wrote a gate that asks a different question: **did the two tracks decide
the same thing?**

`tools/decision_parity.py` hands the live position manager and the replay bar
processor the same position state and the same observation, then compares the
state after — the stop, the partial flags, whether the position closed, and the
reason. Deliberately not the R. The R is exactly the quantity that can agree
while the decisions diverge.

First run, before any fix, on six scenarios:

```
step 3 (price 101.5): sl replay=100.0 runtime=100.18
step 4 (price 99.9):  sl replay=100.0 runtime=100.18
```

Four of six scenarios red. Same divergence every time.

## The 0.18%

The reducer returned, after TP1, a stop equal to `entry`. The replay wrote that
down and simulated it.

The runtime did not. Immediately afterwards, `paper_trader._update_trailing`
overwrote it with `entry * (1 ± DAYTRADING_BREAK_EVEN_BUFFER_PCT/100)` — the
break-even buffer, 0.18% past entry.

So the live bot moved its stop to 0.18% *beyond* entry, and the simulator kept
it *at* entry. The simulator was modelling a tighter stop than the bot actually
uses, on every single break-even exit.

Median stop distance in this system is ~0.65% of price. A 0.18% shift against a
0.65% risk unit is **≈0.28R per break-even trade**. Not a rounding error. It is
a quarter of a risk unit, silently, on a whole class of exits.

And here is the part that should worry anyone running a similar setup: this was
invisible in every number the project had ever published, because every number
came from the side that was wrong.

## It was worse than "replay is wrong"

Fixing it surfaced a second thing. The buffer in the *runtime* was not
deterministic either.

The exit gate's baseline had `sl_price = 100.0` recorded for the `v2_tp1`
scenario — no buffer — even though the buffer existed in the runtime code. It
depended on `_update_trailing` executing before the next exit check, which
depended on the call order of `update_pnl` and `check_exits`.

So the live bot's own break-even stop was a function of loop ordering. Not a
policy. An accident that mostly went one way.

Moving the buffer into `_stop_be()` inside the shared reducer fixed both tracks
at once, and made it a floor for the trailing anchor as well, so the anchor
cannot walk the stop back below a level the bot already reached.

## Three places, three meanings, one config name

While the parity gate was narrowing that down, it exposed the same failure mode
elsewhere. The partial-exit ladder was defined in three places with three
different meanings:

- the live bot closes 50% at TP1, then 50% **of the remainder** at TP2 → 50/25/25
- the replay had `0.5`/`0.3` hardcoded as fractions **of the original** → 50/30/20
- `_gross_expected_r`, the model that decides whether to enter at all, ignores
  `frac_tp2` entirely and prices the whole post-TP1 remainder as if it reached
  TP2

Same config name. Three meanings. The third one matters most: the trailing rung
does not exist in the model that decides whether to take the trade.

## Sabotage, or the gate is not trusted

A gate that has never failed on purpose has not been shown to work.

`tools/decision_parity.py --sabotaz be` reverts exactly this fix — the replay
goes back to bare `entry`. Result: **4 of 6 scenarios red**. Without sabotage,
0 of 6.

That is the whole protocol, and I would rather state it than imply it: break the
thing the gate guards, watch it fire, write down what you saw. One earlier defect
in this codebase produced an output file of *identical length* to the correct
one, which is why file size and row counts are not gates and byte comparison is.

## What the exit gate did when policy changed

Worth recording because it is the case people get wrong.

`tests/test_exit_gate_baseline` went red. It was supposed to: policy changed.
The diff was two entries, both `sl_price 100.0 → 100.18`. 63 cases, 42 exit
events, 16 reasons, 0 exceptions — unchanged. The other six gates: identical.

The baseline was rewritten **after reading the whole diff**, not after seeing
that it was red. Those are different actions that look the same in the commit.

An existing parity test also had to change: it asserted `new_sl == entry`, which
was an assertion about the policy I had just deliberately changed. Rewritten to
compute the value from config, plus `assertNotAlmostEqual` against bare entry so
it still has teeth.

That test had a second problem. It compared `quote` against `bar` while passing
**identical arguments** to both calls — it was comparing a call to itself and
could not fail. It now gets a real high/low range.

## What this cost, honestly

The measurements this repo publishes are not flattering, and the parity work is
part of why:

- measured `p(TP1)` = **0.091** on 1224 trades across 49 symbols and 180 days.
  The model's prior was **0.55**
- mean realised R is **−0.0705 ± 0.0545**, and that interval is optimistic —
  it assumes 1224 independent trades, where 49 correlated coins in one window
  are not independent
- modelled cost was overstated by an order of magnitude: measured average spread
  is exactly one tick on each of three instruments (BTC 0.0133 bps, XRP 0.7316,
  ZEC 0.1229) against a modelled flat 4 bps, and impact was being charged
  against a whole candle's turnover instead of book depth
- the two errors partly cancelled, which is why the output looked plausible for
  months

I also published a wrong number in the middle of this and had to withdraw it in
the next version: I claimed the broken `sl` field had "lifted the dataset mean
from −0.2019 to −0.0705". It had not. `realised_r` is computed from
`initial_risk`, not from the stored `sl` field, so the broken column could not
move the mean and did not:

```
paired 1224 | old-only 0 | new-only 0
differ in realised_r : 0
differ in sl field   : 111
mean realised_r old -0.070531  new -0.070531
```

−0.2019 was *my recomputation of R from the broken column*, an artefact of the
analysis reported as a property of the data. Both the claim and the retraction
stay in the changelog, because a changelog that only ever gets more right is not
a record of anything.

## The transferable part

If you run a backtest and a live bot off shared strategy code, the shared core
is not the guarantee you think it is. What diverges is not the decision — it is
what each adapter *does* with the decision after it comes back.

Concretely, go look at:

1. **Anything the runtime writes after calling the shared core.** Trailing
   updates, break-even nudges, rounding to tick size, minimum stop distances.
   My divergence lived entirely in the four lines after the shared call
   returned.
2. **Config names that mean different things on each side.** "Fraction" is the
   classic: of the original, or of the remainder?
3. **Ordering dependencies inside the live loop.** If `update_pnl` before
   `check_exits` gives a different stop than the reverse, you do not have a
   policy, you have a race.
4. **Whether your entry model prices the same ladder your exit code executes.**
   Mine did not. The third rung was missing from the model that decides whether
   to enter.

And then build the gate that compares decisions, not results. A gate on the
result will pass while all four of these are wrong. Mine did, seven times over,
for months.

---

*The code is MIT and public, gates included:
[github.com/Ryusmay/CryptoEdge](https://github.com/Ryusmay/CryptoEdge). Nothing
here is a claim about live performance — these are statements about a replay of
frozen data, published beside the code that produced them so they can be re-run
and contradicted. Default is PAPER; live order placement stays off.*
