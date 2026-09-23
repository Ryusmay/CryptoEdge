# I corrected my cost model against the wrong exchange, and the correction pointed the wrong way

Three weeks ago I found that my backtest was charging a flat 4 bps spread
against instruments whose real spread varies 337-fold. I measured the real
thing, wrote it up, and recorded the conclusion: the constant **overstates
cost for 18 of 19 symbols and understates for 1**.

I measured Binance. My bot trades on BloFin.

I have now measured BloFin, over two full 24-hour windows. Against the same
constant, at the same order sizes the model actually assumes, it understates
cost for **5 of 19 symbols on the two-day mean, and for 9 of 19 on the worse
of the two days**.

> *Correction, v20.74.0.* The first version of this post gave "understates
> for 9 of 19" as the result, without saying it was the worse day. On the
> two-day mean — which is what the engine now reads — it is 5 of 19. The four
> in between (ENA, SUI, TAO, XRP) sit 0.04–0.48 bps under the constant on
> average and over it on one of the two days; two days are not enough to
> decide them. Both numbers are now pinned by a test. The thesis below does
> not depend on which one you take: the proxy said 1 of 19.

The shape error I found was real. Its sign was not. This is the write-up of
how a correction can be both right about the defect and wrong about the
direction, and what it takes to notice.

## The original defect

`expected_net_r` decides whether a trade is worth taking. When there is no
order book — and in replay there never is — it charged
`DEFAULT_SPREAD_FRAC = 0.0004`, a flat 4 bps, on every symbol. It also
charged a market-impact term computed as the order's participation in a
whole 5-minute candle's turnover.

Neither number had ever been compared with a market.

So I compared them. Nineteen symbols, the full replay universe, from a live
market-data snapshot. Measured spreads ran from 0.0133 bps on BTC to 4.48 bps
on TRUMP. A single constant standing in for a quantity that moves two and a
half orders of magnitude is a shape error, not a tuning error, and I said so.

I also ran a credibility check I'd recommend to anyone measuring a book: the
spread, expressed in ticks of each instrument's own price grid. Eighteen of
nineteen came out at exactly 1.00 ± 0.03 ticks — the signature of a book that
is always tight. The single outlier, XMR at 1.74 ticks, has the thinnest
top-of-book in the set, so it isn't always quoted. That check earned its
place immediately: on the first pass it reported 10.02, 9.89 and 0.10 ticks
for three symbols. Not bad data — my own tick sizes, wrong by exactly 10×.

All of that stands. What I wrote next is the problem.

## The part I got wrong

The file I produced says, in its own provenance block, that it measures
`binance_swap` as a proxy, that BloFin is smaller, and that these numbers are
therefore a **lower bound** on cost.

I wrote that caveat down and then published the conclusion as if it weren't
there. "Overstates for 18 of 19" is a statement about Binance. It was
reported as a statement about my bot.

A lower bound tells you the direction of the error but nothing about its
size, and the whole claim was about size.

## Measuring the venue I actually trade

BloFin REST `/market/books`, depth 100, 24 hourly snapshots, twice: one full
day starting 2026-09-13, another starting 2026-09-15. Both complete, 24 of 24
snapshots, no dropped rounds.

And this time I recorded the right quantity. Not the spread — the **round-trip
cost of walking the actual book** at a given order size:

```
rt_bps_avg by order size, BloFin, full day 2026-09-15 → 09-16

symbol        50      75     250    1000    5000   25000  USD
BTC        0.040   0.041   0.059   0.088   0.151   0.240
ETH        0.336   0.337   0.348   0.356   0.421   0.509
SOL        1.285   1.285   1.285   1.285   1.297   1.431
XRP        4.944   4.947   4.951   4.993   5.042   5.183
PEPE       5.503   5.503   5.503   5.503   5.569   7.838
TRUMP     10.729  10.729  10.729  11.123  13.175  18.904
PUMP       9.087   9.087   9.087   9.736  13.562  18.944
```

This table answers two separate questions that the spread alone cannot.

**Does order size matter at the size I trade?** No. The model's notional is
75 USD for BTC/ETH/SOL and 50 for everything else. Across that range the cost
is flat to three decimals on most symbols — BTC moves from 0.040 to 0.041
between 50 and 75 USD. The curve only bends past 1000 USD, and by 25 000 USD
BTC costs 5.9× what it does at 50.

So the market-impact term is right to be approximately zero here. It is right
by accident: the model computes it from candle turnover, a quantity that has
nothing to do with whether my order fits on the first level of the book. A
term that reaches the correct answer through an unrelated calculation will
stop being correct without warning.

**Is the constant too high or too low?** Both. At the notional the model
assumes, against 4 bps:

| | understates | overstates |
|---|---:|---:|
| BloFin, worse of the two days | 9 of 19 | 10 of 19 |
| BloFin, two-day mean | 5 of 19 | 14 of 19 |
| Binance proxy | 1 of 19 | 18 of 19 |

Which BloFin row you quote depends on how you combine two samples, and two
samples are not a distribution. What does not depend on it: the proxy's
answer is not either of them.

The names where it understates are the ones that hurt: PUMP measured 9.1–11.1
bps, TRUMP 10.7–13.1, 1000BONK 7.6–9.6, PEPE 5.5–6.9.

PEPE is the one that should have stopped me. The constant undercharges it by
1.4–1.7×. On the proxy its spread read **0.296 bps** — so the correction I
was preparing to apply, the one justified by "the constant overstates for 18
of 19", would have replaced an undercharge of 1.7× with an undercharge of
**19–23×**. The fix was aimed at BTC, where the constant really is ~300×
too high, and would have made the worst symbol in the book several times
worse on the way past.

## Why the proxy was so far off

BloFin's spreads are wider than Binance's on every one of the nineteen — by
about 3× on BTC (0.0133 → 0.0402), 6.5× on XRP, 14× on ZEC, 19× on PEPE.
"Smaller venue, wider spread" was the right intuition. It was worth between
3× and 19× depending on the symbol, which is exactly the range over which the
original conclusion's arithmetic mattered.

Intuition told me the sign. Only measurement told me whether the sign was
enough.

## A correction inside the correction

Writing this up, I first reported that BloFin's top-of-book depth is smaller
than the proxy's "by four orders of magnitude", making my 50–75 USD order
larger than the whole first level.

That reads worse than the data supports, and I withdrew it within the hour.
Depth in these files is recorded as the **minimum across 24 snapshots** — the
worst moment of the day, deliberately conservative. The walked round-trip
cost at 75 USD on BTC is 0.0408 bps on average and 0.3441 at its worst. The
book refills. Quoting the minimum as though it were the typical state is the
same error as quoting a proxy as though it were the venue, at smaller scale,
by me, three weeks later.

The correct statement of the gap is the count: 9 of 19, not a depth ratio.

## Wiring it in, and what that cost

The first version of this post ended with "not wiring it in". Since v20.74.0
the engine does, through the parity gate, and the obvious way to do it would
have been wrong.

The slip model already had a "measured" path: take the spread, and if the
order fits on the first level of the book, call that the whole round-trip
cost; if it does not fit, fall back to the old candle-turnover model. Point
that path at the BloFin file and **12–15 of 19 symbols — BTC and ETH among
them — silently fall back to the old model**, because BloFin's depth is
recorded as the minimum across snapshots and 50–75 USD does not fit on the
worst moment of the day. The swap would have looked like "now using BloFin"
while quietly reverting most of the universe to the model this project had
already shown to be the worst of the three.

So the engine now reads the walked round-trip cost at the planned order size
directly, averaged across the two days, from a file built reproducibly by a
tool in the repo. On the frozen 30-day replay: 45 trades before and after,
identical rejection funnel, identical entries; every trade costs slightly
more; net +9.2340R → +9.0790R, OOS −5.0926R → −5.1052R. No decision flipped
in this sample. The larger universe, where the costly names live, has not
been re-run yet.

## The transferable part

1. **A proxy measurement is a measurement of the proxy.** Mine said so in its
   own metadata. I wrote the caveat and then published past it. If your data
   file contains the sentence "these numbers are a lower bound", your
   conclusion may not contain the phrase "overstates for 18 of 19".
2. **Measure the quantity the model uses, not the one that is easy to get.**
   Spread is easy. What the model needs is the cost of getting in and out at
   *my* size, which is a walk through the book. Those differ, and the
   difference is where the model was wrong.
3. **A flat cost curve is a finding, not a null result.** Learning that
   nothing changes between 50 and 1000 USD told me the impact term is
   negligible here — and that the model's version of it is computed from
   the wrong quantity and merely happens to land near zero.
4. **Record the conservative and typical cases separately, and label them.**
   I had both. I quoted the wrong one and had to retract it.

---

*Code and data are MIT and public:
[github.com/Ryusmay/CryptoEdge](https://github.com/Ryusmay/CryptoEdge). The
six measurement files, the generated comparison and the caveats are in
`data/`. Nothing here is a claim about live performance — the default path is
PAPER and live order placement stays off.*
