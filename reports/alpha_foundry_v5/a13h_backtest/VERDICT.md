# A13-H (Hourly Residual Relative Value) — verdict

Preregistered in `docs/A13H_PREREGISTRATION.md` before this ran. 310 symbols, PIT universe
(`data_v2/instruments/instrument_master.parquet`), 6,988,225 panel rows, 2020-03 to
2026-07. 140,430 trade records across 5 horizons x 2 hypotheses x 7 regimes.
Full numbers: `SUMMARY_BY_REGIME.csv`, `SUMMARY_POOLED.csv`, `H3_SUMMARY.csv`,
`RECORDS.parquet` (raw per-rebalance records).

## H1 — residual mean reversion: does NOT clear the preregistered bar

Gross edge is real and consistently positive (mean reversion exists in the raw signal at
every horizon/regime except a couple of thin cells) but costs dominate at short horizons:
fee (10.5-12bps) + the liquidity-tiered spread proxy (14-24bps) round-trip regularly
exceeds gross by 3-10x at h=1h (net -19 to -33bps in every one of the 7 regimes).

Net only turns positive in **3 of 35** (horizon, regime) cells, all at the longest horizon
(72h): 2022 (+8.10bps), 2023 (+7.97bps), 2026 (+8.13bps, partial year). 2019-2020
(-28.8bps), 2021 (-0.11bps), 2024 (-23.2bps), 2025 (-20.6bps) are flat-to-negative at the
same horizon. The preregistration's own bar was net-positive PnL across multiple
independent regimes -- 3/7, non-consecutive, modest magnitude, does not meet that bar.
**Not a standalone edge as specified.**

## H2 — residual continuation: dead, cleanly

Net-negative in **all 35 of 35** cells, frequently severely (down to -77bps at h=72,
2026). Mirrors H1's gross exactly negated (as it must, same trade opposite sides) minus
the same costs -- there is no regime or horizon where continuation beats reversion net of
costs. Preregistered alongside H1 specifically so this sign wouldn't get quietly dropped
after the fact; reporting it in full as required. **Ruled out.**

## H3 — extreme-decile spread predicting H1's forward return: the one real lead

`IC(Spread_t, H1_net_bps)` is small but **positive at every single horizon** (0.023 to
0.056, `H3_SUMMARY.csv`) -- wider current top/bottom-decile residual spread does modestly
predict a better (less negative, or more positive) H1 outcome. Tercile split of H1's own
trades by `Spread_t` at formation:

| horizon | low-spread tercile net bps | mid | high |
|---|---|---|---|
| 1h | -28.25 | -27.51 | -25.34 |
| 4h | -27.39 | -25.15 | -26.57 |
| 12h | -27.11 | -22.73 | -21.55 |
| 24h | -27.02 | -11.81 | -8.09 |
| **72h** | **-10.17** | -19.27 | **+14.53** |

The 72h/high-spread-tercile cell (n=234 trades pooled across all years) is the strongest
single result in this whole run: net +14.53bps, materially better than the unconditional
72h pooled H1 average. Cut by regime (same 234 trades, no new backtest run needed since
`RECORDS.parquet` already carries both `regime` and `spread_t`):

| regime | net bps | n |
|---|---|---|
| 2019-2020 | +30.20 | 9 |
| 2021 | +16.33 | 92 |
| 2022 | +88.20 | 25 |
| 2023 | +56.29 | 16 |
| 2024 | +22.30 | 16 |
| **2025** | **-31.83** | 29 |
| **2026** | **-19.41** | 47 |

**5 of 7 regimes positive**, including both a bull year (2021, the largest single sample
at n=92) and a bear year (2022, the single strongest result at +88bps) -- a genuinely
broader base than H1 unconditioned managed (3/7). But **the two most recent regimes, 2025
and 2026, are both negative** (-31.83bps on n=29, -19.41bps on n=47) -- not thin samples,
and not one bad quarter each. Whatever this conditioning is capturing either decayed or
reversed recently. That is not a reason to discard it outright (5/7 including a real bear
year is still the most multi-regime-robust cut in this entire run), but it is a real,
stated caveat: this would need the 2025-2026 underperformance understood, not waved away,
before treating it as launchable.

## On the cost model

The liquidity-tiered spread/slippage proxy (1/5/15bps by ADV tier, `docs/
A13H_PREREGISTRATION.md`) is not measured -- there is no order-book data for this
310-symbol universe. It is the single biggest lever on every number above: at h=1h it is
1.5-2x the fee cost and roughly 3-6x the gross edge. A materially more optimistic (or
pessimistic) real-world number here would change which cells clear zero, though it would
not flip H2's verdict (negative gross before any cost is even applied) and would not by
itself get H1 to "multiple regimes positive" at short horizons, where the gross edge
itself is under 8bps against 20+ bps of pooled cost.

## Bottom line

Neither H1 nor H2 is a launchable standalone edge as preregistered. H3's 72h/high-spread
conditioning is the one genuinely interesting result -- 5/7 regimes positive including a
real bear year -- but with the two most recent regimes (2025, 2026) both negative, it is a
lead worth investigating further (why did it flip recently?), not yet a launchable edge
either. Nothing in this run clears the preregistered bar outright.
