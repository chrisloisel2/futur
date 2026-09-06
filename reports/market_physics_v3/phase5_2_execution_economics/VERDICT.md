# Phase 5.2 — EXECUTION_ECONOMICS verdict: CLOSED_NO_EDGE

## Mechanism

`okx__queue_imbalance_l5`, horizon 30s, target = LOO fair value of
binance+bybit+hyperliquid (OKX excluded by construction). Sealed
`CONFIRMED_INFORMATION_CANDIDATE` on 2026-08-17 (`reports/market_physics_v3/
phase5_2_confirm/result/SUMMARY.json`) — that statistical verdict is not
reopened or touched here.

## Revision (2026-08-29): methodology corrected, verdict unchanged

The first pass (2026-08-28) had four real bugs, caught in review: entry
thresholds were recomputed on the confirmation tape itself instead of frozen
from the prior DEV_PILOT window (in-sample circularity); gross PnL used the
LOO fair value's own per-row floating weights rather than real per-venue
prices at fixed entry weights; capacity was a weighted sum across venues
instead of the binding (bottleneck) leg; and `fill_rate` was a bare `1.0`
literal instead of a computed value. All four are fixed below. The
corrected run also removes a real double-count the first pass had: charging
half-spread as a separate cost *and* implicitly moving on mid-derived
prices meant spread was priced once as a cost and once again inside a
mid-to-mid return that never should have included it — the corrected
version instead uses real bid/ask directly, so the spread cost is paid
exactly once, where it actually occurs.

## Second revision (2026-08-29, same day): the "corrected" fee count was still wrong

An external audit caught it: `Trade.net_bps` was `gross_bps - fee_bps`, one
fee deduction, even though `gross_bps` already reflects two real
transactions (buy the ask at entry, sell the bid at exit) -- a real taker
fee is charged on every fill, not once per position. This prose already
said "paid twice for entry+exit" below while the code charged it once; the
code was wrong, not the prose. The old `net_edge_cost_x2_bps` field (kept
as a secondary "2x costs" stress-test row) was, it turns out, the actually-
correct round-trip number all along -- it's now just `net_edge_bps`, the
separate field is gone, and every number in the table below is the
corrected one. The qualitative verdict does not change: it fails the gate
either way, now by a wider margin.

## Method

Simulated the trade this mechanism implies: enter top/bottom decile of the
feature (non-overlapping, 30s hold), thresholds **frozen from the DEV_PILOT
window** (`market_physics_v3/phase5_2_execution_economics.py::freeze_thresholds`,
per-symbol 10th/90th percentile, computed once, never touched by the
confirmation tape being scored). Execute a weighted split across
binance/bybit/hyperliquid, weights = `venue__price_weight` at entry,
**fixed through exit**. Gross PnL uses **real `venue__price_best_bid`/
`price_best_ask`** (buy the ask to open long / sell the bid to close;
mirrored for short) — spread cost is the natural bid-ask gap, not a
separate deduction. Cost = public standard-tier taker fees only (binance
5.0bps, bybit 5.5bps, hyperliquid 3.5bps — not account-specific). Capacity
= `min` across legs of (that leg's real `ask_depth_5bps`/`bid_depth_5bps`
in USD, divided by that leg's entry weight) — the bottleneck leg, not a
sum. `fill_rate = min(1.0, capacity_usd / $200,000)`, the same reference
size as `GatePolicy.execution_min_capacity_usd`. 3,047 non-overlapping
trades across the same 3 symbols (BTCUSDT, ETHUSDT primary; SOLUSDT
support) Phase 5.2 confirmed on, same 12h confirmation tape.

## Result

| metric | first pass (buggy) | 1st correction (still-wrong single fee) | current (round-trip fee) |
|---|---|---|---|
| gross edge / trade | +0.64 bps | +0.38 bps | **+0.38 bps** |
| net edge / trade | -9.25 bps | -4.44 bps | **-9.27 bps** |
| net edge, entry delayed 300ms | -9.29 bps | -4.49 bps | **-9.31 bps** |
| net edge, top 5% contributors removed | -9.67 bps | -4.83 bps | **-9.67 bps** |
| net edge, recent half only | -9.17 bps | -4.40 bps | **-9.22 bps** |
| profit factor | 0.006 | 0.032 | **0.005** (gate requires ≥1.30) |
| capacity (bottleneck leg, median) | ~$2.96M (was a weighted sum) | $3.13M | **$3.13M** |
| fill_rate at $200k reference | 1.0 (hardcoded) | 1.0 (computed) | **1.0 (computed: capacity ≫ $200k)** |
| realized spread cost / trade | ~0.20bps (half-spread proxy) | ~0.21bps | **~0.21bps (real bid-ask gap)** |
| n trades | 2,680 | 3,047 | **3,047** |

All 5 `economic_gate()` checks still fail (the redundant `cost_x2` check is
gone, folded into the now-correct `net_edge` one), for every symbol
individually and combined. Gross edge (~0.4bps) is dominated by taker fees
alone (~4.5-5.5bps *per fill*, and a full round trip is two fills across a
3-venue split -- ~9-11bps of fees against ~0.4bps of signal). Closing this
gap would require near-zero taker fees, not a data or modeling fix.

## Why this doesn't retroactively question the DEV_DISCOVERY/CONFIRMATION verdict

The statistical claim (OKX order-flow information precedes the other three
venues' fair value at 30s, IC-detectable, same-sign robust) can be entirely
real and still not be a tradeable edge — a 30s-horizon signal that only
survives if executed with near-zero fees is an information-theoretic result,
not an economic one. This is exactly why the pipeline separates
`INDEPENDENT_CONFIRMATION` from `EXECUTION_ECONOMICS`: passing the first
does not imply passing the second.

## Verdict

**CLOSED_NO_EDGE at EXECUTION_ECONOMICS**, confirmed under corrected
methodology. Not advanced to PAPER_LIVE. Not a candidate for the edge
scoreboard as a tradeable sleeve. Do not re-run this mechanism against
friendlier cost assumptions to make it pass — the gap is structural
(fee-dominated), not a modeling artifact.

Code: `market_physics_v3/phase5_2_execution_economics.py`,
`scripts/execution_economics_market_physics_phase5_2.py`. Full per-symbol
breakdown and assumptions: `SUMMARY.json` in this directory.
