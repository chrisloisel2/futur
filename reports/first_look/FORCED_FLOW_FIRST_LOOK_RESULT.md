# Forced-flow first look — result (regard seq 9, 2026-09-10)

Prereg `forced_liquidation_reaction_v1_PREREG.md` (commit a6c45df, pushed) · tape frozen at 19:54:31 UTC (24 930 liquidations,
sha `b78d1b50…`, one session) · universe sha `a61be7254e287739…` · family `liquidation`, threshold_t(2) = 1.96 · budget 1 → **0** ·
prices: Binance REST aggTrades, 1 891 windows, entry median 2.0 s after the exchange timestamp.
Ledger: written as seq 8 on a branch started from main, re-chained to seq 9 at merge (content unchanged, local hash kept).

| H | mechanism | universe | primary | n | clusters | mean | median | t | win | wall | top cluster | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| H1 | `forced_liquidation_reaction_v1` | ≥ 50 k$ | 30s | 368 | 51 | -7.0 | -0.8 | -1.45 | 0.45 | 44 | 0.27 | **REJECTED_NO_GROSS** |
| H2 | `forced_liquidation_exhaustion_v1` | ≥ 250 k$ | 5m | 62 | 15 | +13.8 | +10.2 | 1.18 | 0.71 | 43 | 0.78 | **REJECTED_COST_WALL** |

Sensitivities (descriptive): H1 — 30s: -7.0 (med -0.8, t -1.45) · 5s: -1.2 (med -0.7, t -1.38) · 5m: -10.9 (med -7.7, t -1.00) · H2 — 5m: +13.8 (med +10.2, t 1.18) · 30s: +10.6 (med +5.4, t 1.73) · 30m: -6.7 (med -0.5, t -0.54).

Buckets at the primary horizon (descriptive, never selected on):
- H1 — group=BTC/ETH: -4.3 (n 157) · group=alts: -9.0 (n 211) · notional=100k-250k: -6.7 (n 126) · notional=250k-1M: -10.0 (n 53) · notional=50k-100k: -5.9 (n 180) · notional=>=1M: -14.0 (n 9) · side=long: -9.1 (n 272) · side=short: -1.0 (n 96)
- H2 — imbalance=against_flow: +19.2 (n 34) · imbalance=with_flow: +7.1 (n 28) · notional=250k-1M: +12.7 (n 53) · notional=>=1M: +20.1 (n 9) · side=long: +16.4 (n 54) · side=short: -4.2 (n 8) · spread=<=0.1bps: +8.7 (n 45) · spread=>0.1bps: +27.2 (n 17)

## Reading

**H1 — no continuation 2 s after the message.** Over 368 large liquidations the 30-s move in the
direction of the forced flow is −7 bps (median −0.8, 45 % positive, t −1.45); at 5 s it is −1.2 bps and at
5 min −10.9. Whatever the cascade does, it has done it before the public forceOrder message reaches a
reader 2 s later; what follows leans slightly *against* the flow. A clean, well-measured zero (SE 4.8 bps).

**H2 — a reversal exists, and it is smaller than the cost.** After the 62 extreme liquidations the price
comes back +13.8 bps in 5 min (median +10.2, 71 % positive, t 1.18): the sign the hypothesis predicted, at
one third of the 43 bps wall. Buckets say where it lives (liquidated longs +16, book imbalance against the
flow +19, wide spread +27, ≥ 1 M$ +20) — descriptive only, and the whole thing sits in one session where one
cascade cluster carries 78 % of the positive contributions. `REJECTED_COST_WALL`, as preregistered.

## Caveats the prereg declared

- One session (11:12 → 19:54 UTC, 2026-09-10) with one dominant cascade at 12:00 UTC. The tape keeps
  collecting; a new preregistration on a **later** period is possible (the burned period recorded in the
  kernel ledger is 2026-09-10 only) but it is a new hypothesis with a new budget, and the budget is 0.
- H2 could not reach FORWARD_SEAL on this tape by construction (15 clusters < 20); it did not get that far.
- Bybit excluded (no sub-minute history); COIN-M excluded (dapi).

## Status after the look

- 0 promotion. `forced_liquidation_reaction_v1`: REJECTED_NO_GROSS. `forced_liquidation_exhaustion_v1`: REJECTED_COST_WALL.
- Budget: **0 tests**. Nothing can be tested until new data credits budget or the H3 forward window matures.
