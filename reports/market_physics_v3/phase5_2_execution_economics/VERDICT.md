# Phase 5.2 — EXECUTION_ECONOMICS verdict: CLOSED_NO_EDGE

## Mechanism

`okx__queue_imbalance_l5`, horizon 30s, target = LOO fair value of
binance+bybit+hyperliquid (OKX excluded by construction). Sealed
`CONFIRMED_INFORMATION_CANDIDATE` on 2026-08-17 (`reports/market_physics_v3/
phase5_2_confirm/result/SUMMARY.json`) — that statistical verdict is not
reopened or touched here.

## What changed

Simulated the trade this mechanism implies: enter top/bottom decile of the
feature (non-overlapping, 30s hold), execute a weighted split across
binance/bybit/hyperliquid (weights = the same `venue__price_weight` the LOO
target itself uses), cost each leg with **real observed
`venue__price_spread_bps` at entry/exit** (half-spread) plus **public
standard-tier taker fees** (binance 5.0bps, bybit 5.5bps, hyperliquid
3.5bps — not account-specific, can only be lower with a real VIP tier).
2,680 non-overlapping trades across the same 3 symbols (BTCUSDT, ETHUSDT
primary; SOLUSDT support) Phase 5.2 confirmed on, same 12h tape.

## Result

| metric | value |
|---|---|
| gross edge / trade | **+0.64 bps** |
| round-trip cost (fees + real spread) | **~9.9 bps** |
| net edge / trade | **-9.25 bps** |
| net edge at 2x costs | -19.14 bps |
| net edge, entry delayed 300ms | -9.29 bps |
| net edge, top 5% contributors removed | -9.67 bps |
| net edge, recent half only | -9.17 bps |
| profit factor | 0.006 (gate requires ≥1.30) |
| capacity (median, 5bps depth) | ~$2.96M/trade |

All 6 `economic_gate()` checks fail, for every symbol individually and
combined. This is not a borderline or fee-tier-sensitive result: closing a
~15x gap between gross edge and round-trip cost would require taker fees
near zero, which no realistic account tier reaches. Realized slippage from
spread-crossing is tiny (~0.20bps) — the entire gap is taker fees on a
3-venue split, an unavoidable structural cost of executing where the
signal's own target is defined (binance+bybit+hyperliquid, weighted).

## Why this doesn't retroactively question the DEV_DISCOVERY/CONFIRMATION verdict

The statistical claim (OKX order-flow information precedes the other three
venues' fair value at 30s, IC-detectable, same-sign robust) can be entirely
real and still not be a tradeable edge — a 30s-horizon signal that only
survives if executed with near-zero fees is an information-theoretic result,
not an economic one. This is exactly why the pipeline separates
`INDEPENDENT_CONFIRMATION` from `EXECUTION_ECONOMICS`: passing the first
does not imply passing the second.

## Verdict

**CLOSED_NO_EDGE at EXECUTION_ECONOMICS.** Not advanced to PAPER_LIVE. Not a
candidate for the edge scoreboard as a tradeable sleeve. Do not re-run this
mechanism against friendlier cost assumptions to make it pass — the gap is
structural (fee-dominated, ~15x), not a modeling artifact.

Code: `market_physics_v3/phase5_2_execution_economics.py`,
`scripts/execution_economics_market_physics_phase5_2.py`. Full per-symbol
breakdown and assumptions: `SUMMARY.json` in this directory.
