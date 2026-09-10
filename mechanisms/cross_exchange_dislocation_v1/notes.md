# Notes — `cross_exchange_dislocation_v1`

Commentary written after the run. The results are in `results/verdict.md`.

## The most reliable tiny number in the repository

934 independent episodes, gross capture **+1.97 basis points**, standard error
0.030, so a t on the gross series of **65**. The dislocation between Binance and
OKX reverts, and it reverts almost every single time.

It also needs to capture **1215%** of what it predicts to break even, because
two legs of taker execution cost 24 basis points.

## Why a t of sixty-five should not be read as strength

The entry conditions select instants where the dislocation is already extreme,
and part of what reverts afterwards is the measurement noise in that extreme,
not a price that moved. The reliability is genuine but the quantity is around
two basis points, which is the scale at which the distinction stops mattering
for anything except an account that pays no fees.

The useful reading is the reverse of the usual one: this is a very well measured
**negative**. With a standard error of 0.03 basis points, this design excludes a
cross-venue capture larger than about 0.1 basis points beyond what was seen. The
gap between the two venues is not hiding a bigger edge that better execution
would reach.

## What would have to change, and it is not a parameter

Break-even needs the total two-leg round trip under 1.97 basis points, so under
one basis point a leg. No retail fee tier reaches that. A market-maker rebate
schedule, cross-margin between venues, and inventory already sitting on both
sides would be a different business, not a different threshold.

## The data limit that was declared in advance and still holds

Displayed quantities are not comparable across the two venues: Binance quotes
base units and OKX quotes contracts. Nothing in this run says anything about how
much size the dislocation could absorb, and nothing in it should be quoted as a
capacity estimate.

## 2026-09-10 — status policy vs measured verdict

The rebuild brief lists this mechanism as `RESEARCH_ONLY`. The measured verdict in
`results/verdict.json` is **COST_WALL** (gate 2). The verdict stands: a new rule in this
family is a new mechanism with a new `spec.json` and a new trial; this one is not re-run.
