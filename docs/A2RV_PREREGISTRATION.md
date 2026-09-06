# A2-RV-v1 (A2 dislocation-convergence, traded) — preregistration

Written and committed before looking at the confirm_24h_collect 28h window (still
collecting as of this writing — ~9h remaining of a 28h run started
2026-08-28T23:18:07Z). This document, the threshold below, and the evaluation script
(`scripts/run_a2rv_backtest.py`) are all frozen using only data that predates that
window: the DEV_PILOT tape Phase 5.2 already used. Nothing here is tuned against, or
even glances at, the window it will be evaluated on.

## Why a separate protocol from A2 itself

A2 (`search_family_id="A2:venue_dislocation_convergence:loo_fair_value_return"`) is a
sealed statistical finding: `<venue>__price_dislocation_bps` predicts
`loo_fair_value_return` at 2s with IC~0.195, same-sign-robust
(`reports/alpha_foundry_v5/experiments/20260818T170429Z_A2_2000ms.json` and siblings).
That is an information-theoretic result about a *target series* (the leave-one-out fair
value of the other venues), not yet a demonstration that the dislocation itself is
monetizable as a real trade with real fills and real fees — exactly the gap that made
Phase 5.2 a real statistical finding and a dead trade at the same time. A2's own sealed
verdict is not reopened or touched here.

## Mechanism -> trade

`<venue>__price_dislocation_bps` is computed in
`market_physics_v3/state_tape_stream.py` as the log-bps distance of that venue's own
`price_mid` from `leave_one_venue_out_fair_value` (`alpha_foundry_v5/targets.py`) — the
`price_weight`-weighted average of every *other* venue's mid. Positive = this venue
trading rich versus the consensus of the rest; negative = trading cheap.

The trade: at a timestamp where **some venue V's** `|V__price_dislocation_bps|` exceeds
a frozen extreme threshold, go:

- **LONG venue V** (weight +1.0): buy V's real `price_best_ask` at entry, sell V's real
  `price_best_bid` at exit.
- **SHORT a basket of every other venue**, weighted exactly like the LOO fair value's
  own denominator (`price_weight`, normalized among the non-V venues, mirroring
  `leave_one_venue_out_fair_value`'s construction so the short leg *is* the same anchor
  A2's target is measured against, not an approximation of it): sell each venue's real
  `price_best_bid` at entry, buy back its real `price_best_ask` at exit.

Direction of V's leg: if `V__price_dislocation_bps` is very negative (V cheap), long V /
short the basket, betting on convergence (V rises toward the basket, or the basket
falls toward V, or both). If very positive (V rich), the mirror: short V / long the
basket. Symmetric by construction — it doesn't matter which specific venue ends up
playing which role at a given instant.

## Threshold — frozen from DEV_PILOT, not the confirmation window

Same discipline as Phase 5.2's `freeze_thresholds`: 10th/90th percentile of
`V__price_dislocation_bps` per venue, computed once on
`/home/qbee/futur-data-v2/data/market_physics_v3/state_tape_stream/
run=1786830843094013751-1786852443241777168/cadence=100ms` (the DEV_PILOT tape, already
used and already behind us) and never recomputed on the tape this gets scored against.

## Horizon — 2s, locked

A2's own strongest, sealed IC was at `label_horizon_ms=2000`. This uses that horizon
only. Not re-scanned across A2's other 7 horizons (100ms-30s) looking for a better one
after the fact — that would just be re-introducing the multiple-comparisons problem A2's
own DEV_DISCOVERY protocol was built to control for, one level up.

## Cost model — reused, not reinvented

Identical to Phase 5.2's now-corrected accounting
(`market_physics_v3/phase5_2_execution_economics.py`, fixed 2026-08-29): real
`price_best_bid`/`price_best_ask` at entry and exit (spread cost is the natural
bid-ask gap, not a separate deduction), public standard-tier taker fees
(`TAKER_FEE_BPS`), and **the round-trip fee is charged twice** (entry fill + exit
fill) — the bug that fix corrected does not get reintroduced here.

## Latency sensitivity — preregistered grid, no post-hoc pick

Entry delayed by `{0, 50, 100, 250, 500}` ms from signal timestamp, all five reported.
Not "whichever one clears the gate" — all five, same as Phase 5.2's single
`delayed_entry_net_bps` stress check but as a full grid since this horizon (2s) is much
more latency-sensitive than Phase 5.2's 30s one.

## Capacity

Bottleneck-leg capacity (`min` across all legs — the long V leg and every short basket
leg — of that leg's real depth-at-5bps in USD divided by that leg's weight), same
method as Phase 5.2's `_leg_capacity_usd`, extended to an arbitrary number of legs (V
plus N-1 basket members, not fixed at 3).

## Symbols

BTCUSDT, ETHUSDT (primary, matching A2's own confirmed primary set), SOLUSDT (support).
Venues: binance, bybit, okx, hyperliquid (the four already in every A2 window).

## No tuning after confirmation

Every number above is fixed before the 28h window is read. If the result is
disappointing, the response is to report it, not to try a different threshold,
horizon, or venue weighting on the same window.

## What would make this a real result

Positive `net_edge_bps` **and** positive at every one of the five latency offsets
**and** `profit_factor >= 1.30` **and** capacity above the same $200,000 reference
Phase 5.2 used. Anything short of that is reported as such — most likely outcome,
given Phase 5.2's own experience, is that a 2s horizon with real fees on 5 legs (1
long + up to 4 short) is fee-dominated the same way Phase 5.2 was. That is a legitimate,
useful answer, not a failure to find one.
