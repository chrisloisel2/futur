# Preregistration — `lsr_globacct_x_v2`

Written before the rule was run. The decision-relevant fields live in
`spec.json`; their SHA-256 is the `rules_hash` carried by every verdict and by
the forward seal. Editing any of them produces a different hash and voids the
seal.

## The claim

Across Binance USDT-M perpetuals, the assets whose retail account base is least
long outperform the assets whose base is most long, over the next day, on a
market-neutral basis, by more than the round trip on both baskets.

## Why it should exist

`count_long_short_ratio` counts **accounts**, not notional. It is therefore a
census of small positions. Those accounts hold the thinnest margin buffer, so
they are the ones liquidated into a move rather than out of it, and whoever
absorbs that flow is paid for holding the unpopular side. The payment is
recovered over hours, not milliseconds, and requires balance sheet on two legs,
which is why it is not competed away instantly.

## The rule, in one paragraph

Every second day at 00:00 UTC, screen the universe to the hundred symbols with
the largest thirty-day median quote volume as of the previous close. Rank the
survivors by the mean global account long/short ratio over the preceding
twenty-four hours. Buy the fifteen lowest, sell the fifteen highest, equally
weighted, enter at the open of the day's bar and exit at its close. No stop, no
discretion, no filter beyond the liquidity screen.

## What is preregistered, and what is not tested

One rule. No sensitivities are declared, so this mechanism charges exactly one
trial to the `crowd_positioning` family. There is no threshold to tune: the
basket size, the screen and the step are fixed above and any change to them is a
different hypothesis with a different hash.

## Declustering, chosen before the result

`fixed_windows`, one day, cross-sectional: one rebalance is one episode,
regardless of how many symbols it holds. The two-day rebalance step is strictly
greater than the one-day window, so consecutive rebalances cannot chain into a
single episode. This choice is made here, in advance, because choosing it after
seeing a result would be one more trial.

## Contamination — this cannot be promoted on history

This repository has already examined crowd positioning on this data at length,
including an iteration over eighty-six derived signals. The historical run below
is therefore a **replication**, not a discovery, and the ceiling on its verdict
is `PROMISING_NEEDS_FORWARD`. The `crowd_positioning` family is seeded in the
multiplicity ledger with those past trials and the burned period is recorded, so
the sealer will physically refuse a forward window that overlaps it.

## Latency — which feed gate 1 applies to

The replication reads the Binance Vision daily archive, which is published at
T+1. That publication delay is a property of the archive, not of the mechanism:
the live path reads `futures/data/globalLongShortAccountRatio`, whose freshness
is measured from the live archive under `data/positioning/`. Gate 1 is applied
to the live figure, and the forward window must run on the live path. If the
live feed ever exceeds six hours of staleness, the mechanism is unexecutable and
the verdict is `REJECTED` regardless of its history.

## What would kill it

- net edge at or below zero after one round trip;
- profit factor at double cost at or below 1.0;
- failure to clear the 95th percentile of its own placebo null;
- a t on the **gross** series below the threshold the family's trial count
  implies;
- the edge disappearing when any single year or any single symbol is removed.

## What promotion would require

A sealed forward window on the live feed, closing above the promotion criteria
in `spec.json`, with at least two hundred independent episodes. Nothing below
that reaches paper capital.
