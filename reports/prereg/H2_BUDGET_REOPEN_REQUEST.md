# H2 — budget reopen request (CONDITIONAL, not submitted)

> **No budget is requested.** The P10 gate is closed: 0 clean events against a threshold of
> 80, and execution cost is unknown. This file states in advance what a request would contain, so
> that when the gate opens the ask is already specified and cannot be inflated after seeing anything.
> Freeze `4f077025c30f6bfd…`, commit `1f55ec3f03b2`. Budget today: **0**. Requested: **0**.

## The rule this request must obey

Budget is credited by new independent episodes, `floor(episodes / 400)`, capped at 5 per source. P6 to P9 added
no episode: they enriched 174 events that were already counted. **Enrichment credits nothing.** A reopen is
therefore not automatic — it is a decision to spend a test that other sources earned.

## What would be asked, and for what

| item | value |
|---|---|
| tests requested | 1 |
| mechanism | a new `event_*_v1` spec, not a re-run of `event_listing_perp_fade_v1` |
| population | stated in the spec before the look, one of the two described in the retest draft |
| events available | 129 clean, of which 6 are genuine first listings |
| family | `news`, currently 5 sealed hypotheses; a sixth raises the bar for all of them |
| freeze | this dataset freeze, by sha256, with the per-event Vision manifest and body hashes it already carries |

## Preconditions, all of which are currently unmet

1. At least 80 clean events in a re-run of this freeze.
2. Execution cost measured: actual account fee (read-only key) and spread/slippage derived from the depth archives.
3. A population choice written into the spec, with its own economic reason for why someone is forced to trade.
4. The preregistration pushed to the remote before any price is read, and the LOOK_LEDGER entry written by the
   harness at the moment of the look.

## What would make this request withdraw itself

If the chosen population is the genuine first listings, 6 events cannot
decide anything at a dispersion of 1 546 bps. In that case the correct action is not to spend a test but to keep
collecting: the P4 market-state tape records every new launch live, and the count grows on its own.
