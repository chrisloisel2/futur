# Alpha Foundry V5 — 6h multimodal readiness interpretation

The first 6h multimodal tensor contains 648003 rows and 1322 columns. The readiness audit reports A1-A8 data-ready, A9-A16 blocked, with zero future availability violations, zero duplicate `(asof_ns, symbol)` keys and zero non-monotonic symbol timelines.

## What was actually unlocked

The Event/Trade Plane moved A3, A4 and A5 from blocked to data-ready. The Derivatives Plane moved A7 and A8 from blocked to data-ready. A1, A2 and A6 remain data-ready from the book/cross-venue plane.

This is a data-plane milestone, not an alpha result.

## Important interpretation of activity counts

Rolling-window active rows are not event counts. In particular:

- A3 `remove_count` active on ~643k rows means a rolling removal feature is non-zero on those grids, not that ~643k independent removals occurred.
- A7 liquidation features active on ~303k rows reflect persistence of liquidation observations inside rolling windows, not ~303k liquidation events.
- A8 `open_interest_change_pct` is different: V5 emits OI change only on the first grid that can see a new OI observation, so its ~9.9k active rows are much closer to actual state-update support.

ESS and event counts must therefore be evaluated from underlying event timestamps, not from rolling-feature row counts.

## Control-plane defects exposed by this run

1. The previous A14 pattern `*iv_*` falsely matched `deriv__...` columns, creating apparent options coverage without an Options Plane. A14 now requires the explicit `option__` namespace.
2. Clock sanity alone was labelled too strongly. V5 now distinguishes audited clocks from feature-level provenance and requires `FEATURE_PROVENANCE.json` before discovery.
3. A3 now requires actual trade activity; A7 requires depth/capacity; A8 requires fair-value price.

## Discovery boundary

Do not begin A1-A8 discovery until the existing tensor is provenance-sealed and readiness reports `FULL_FEATURE_PROVENANCE`. After that, run a mechanism-support audit that counts independent raw events, effective temporal support and per-venue/per-symbol coverage before spending multiplicity budget on hypothesis tests.
