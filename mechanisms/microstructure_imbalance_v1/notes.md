# Notes — `microstructure_imbalance_v1`

Commentary written after the run. The results are in `results/verdict.md`.

## The signal is real and it is sixteen times too small

Over ten days, three symbols and 232 independent episodes, the rule produced a
gross drift of **+0.78 basis points** over sixty seconds, with a t on the gross
series of **4.30**. That clears the `microstructure` family bar of 1.64 with
room to spare. It is a real effect.

It is also **1599% of it away from paying for itself.** The taker round trip is
12.55 basis points. Break-even needs the cost under 0.78; the three-times wall
needs it under 0.26.

## What this closes, and how firmly

A pure maker execution at two basis points a side is four basis points of fees
before any adverse selection at all, which is still five times the entire gross
edge. So the closure is not marginal and it is not about fee tiers: at a
sixty-second horizon on these three symbols, top-of-book imbalance does not pay
for its own execution by more than an order of magnitude, for any fee schedule a
non-market-maker account can obtain.

The honest scope of that statement is: this horizon, this definition of
imbalance, this venue, ten days. A different horizon or a different definition
is a different hypothesis and costs a trial.

## What was measured along the way and is worth keeping

The capture holds about 76% of one-second buckets, and the measured feed latency
is 111 milliseconds median, 307 at the 95th percentile, against a budget of
fifteen seconds. Latency is not what stops this mechanism. Cost is.

The median spread on these symbols is about 0.013 basis points, which is why the
cost model is dominated entirely by fees. Any future microstructure mechanism
here should be priced the same way: on this venue set, spread is a rounding
error and the fee schedule is the whole question.

## 2026-09-10 — status policy vs measured verdict

The rebuild brief lists this mechanism as `RESEARCH_ONLY`. The measured verdict in
`results/verdict.json` is **COST_WALL** (gate 2). The verdict stands: a new rule in this
family is a new mechanism with a new `spec.json` and a new trial; this one is not re-run.
