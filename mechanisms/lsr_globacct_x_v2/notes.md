# Notes — `lsr_globacct_x_v2`

Commentary written after the run. Nothing here is a result; the results are in
`results/verdict.md`.

## What the run measured

The rule fired on 848 rebalances between 2021-12 and 2026-08, holding 30
positions each, over 307 distinct symbols. The gross edge is **+9.60 basis
points per position per day** with a standard error of 4.71. The round trip
assumed is 16 basis points, so the net is **-6.40**.

## Two separate reasons this cannot be promoted, and they are independent

**It is under the cost floor.** At taker fees the mechanism would need to
capture 167% of the move it predicts. Even ignoring the safety factor of three,
the break-even cost is 9.60 basis points, and the assumed round trip is 16.

**It is undecidable at its own family's bar.** The `crowd_positioning` family
carries 88 sealed trials, so the threshold is t = 3.25. The smallest edge this
sample could resolve at that bar is **15.3 basis points**; the observed edge is
9.6 with a t of 2.04. Even if the cost were zero, this sample could not tell
9.6 from noise at the bar the family has already paid for. That is not a
negative result. It is a statement that the question is not answerable with
this design and this much data.

## The only lever the cost floor leaves

The gross is fixed by the market. The cost is not: a pure maker execution would
be roughly four basis points of fees plus adverse selection instead of ten
basis points of fees plus spread. That is plausibly the difference between
-6.4 and something slightly positive, and it is **a different hypothesis**,
with a different hash, a different trial charged to the family, and a maker fill
rate that has to be measured before it can be priced. It is not a knob on this
one.

## The data limit worth remembering

The liquidity screen selects 307 symbols but 314 screened symbols have no
Binance Vision metrics file at all. The traded universe is therefore the
intersection of "liquid" and "backfilled", which is not the same universe a live
run would see. A forward run on the live REST feed does not have this problem,
which is another reason the forward window is the only honest next step.

## Where the coverage actually starts

The validation window declares 2020-09-01, but the first rebalance with enough
symbols is 2021-12-02: that is when the Vision metrics coverage becomes broad
enough to rank a cross-section. The verdict reports the real span, not the
declared one.

## 2026-09-10 — three rules, three statuses

The rebuild brief sets this mechanism to `INDECIDABLE / FORWARD_WATCH_ONLY / NO_LIVE`.
That is the status of the **mechanism**. This **rule** (top-100, 15v15, open→close, 2-day
step) is **COST_WALL** by its own verdict and is not re-run. The forward watch is carried
by a different preregistered rule, `FORWARD_CROWD_POSITIONING_V1`, sealed with an external
witness (see `sealed_forwards/EXTERNAL_WITNESSES.md`) — one look, not before 2028-12-06.
The indecidable historical evidence is the sweep rule (`reports/loop/`, sealed-window
t 2.399 vs 2.955). See `PROJECT_TRUTH.md`.
