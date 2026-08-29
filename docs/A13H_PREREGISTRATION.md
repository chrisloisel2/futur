# A13-H (Hourly Residual Relative Value) — preregistration

Written and committed BEFORE `scripts/run_a13h_backtest.py` is executed against real
results. All three hypotheses below run exactly as specified here; nothing here changes
in response to what the backtest turns up. Distinct from canonical A13 (60s-4h horizons,
tick-level data) — no budget shared with it, per explicit instruction not to spend the
canonical A13 budget on this.

## Panel (already built, `scripts/build_a13h_panel.py`)

310 symbols, hourly, 2020-03 -> 2026-07, 6,988,225 (symbol, hour) rows. PIT universe from
`data_v2/instruments/instrument_master.parquet` (listing_ts/delisting_ts) with:
listing_ts + 90d <= t < delisting_ts, real bar present, trailing 720h mean daily quote
volume >= $5,000,000. Market factor = equal-weighted mean return of the eligible universe
at each t. `beta_720h` = causal rolling Cov/Var over the 720h window strictly before t.
`residual = ret_1h - beta_720h * market_ret_1h`.

Sanity already checked: beta_720h mean 1.00 (median 1.01), BTCUSDT's own beta ~0.64 (BTC is
*less* volatile than the broad-altcoin equal-weighted factor, as expected), residual mean
~0, std ~0.97%/h.

## Horizons

`{1h, 4h, 12h, 24h, 72h}` — chosen because this is what the hourly panel can actually
resolve distinctly. Canonical A13's 60s/5m/15m horizons all collapse to the same +1-bar
target on hourly data; that's the bug this fork exists to avoid.

## Cross-sectional deciles

At each t, ranked among symbols with `eligible=True` **and** `universe_size(t) >= 30`
(need enough names for deciles of >=3 to be meaningful; skips the early-2020 period where
the universe hasn't yet reached that size). Decile membership is computed fresh at every
rebalance from that instant's `residual` values only — no lookahead into future rankings.

## Rebalancing

Non-overlapping: a portfolio formed at t is held to t+h, then fully reformed from
residuals observed at t+h. Chosen over overlapping/rolling rebalancing so turnover and
cost accounting aren't double-counted across overlapping positions.

## Three hypotheses, preregistered together

**H1 — residual mean reversion.** Feature = `residual_i,t`. Long the bottom decile
(most negative residual), short the top decile (most positive residual). Bet: extreme
residuals revert.

**H2 — residual continuation.** Same feature, same deciles, opposite sides: long top
decile, short bottom decile. Bet: extreme residuals continue. **Both H1 and H2 run and
get reported in full — the sign is not chosen after seeing which one wins.**

**H3 — cross-sectional extreme spread convergence.** `Spread_t = mean(residual in top
decile) - mean(residual in bottom decile)`, measured at t. Primary test:
`IC(Spread_t, H1_forward_portfolio_return_{t,t+h})` — does a wider spread now predict a
bigger forward H1 convergence profit? Secondary, non-overfit cut: split H1's own realized
trades into terciles of `Spread_t` at formation and report P&L per tercile. H3 is not an
independently-constructed portfolio; it's a conditioning/sizing question on H1.

## Portfolio construction (identical machinery for H1 and H2)

Equal-dollar-weighted within each leg. Gross = 100% (50% long + 50% short) of one unit of
capital. Net delta ~= 0 by construction (equal dollar long and short). **Additional BTC-beta
hedge overlay**: each member's causal rolling 720h beta-to-BTCUSDT (same Cov/Var
methodology as the market-factor beta, BTCUSDT's own realized return as the regressor
instead of the market factor) is computed in the backtest script. The long and short legs'
dollar-weighted average beta-to-BTC is netted, and a BTCUSDT overlay position (sized to
zero out that net exposure) is added at each rebalance. This is on top of, not instead of,
the market-factor residualization already baked into `residual` itself.

## Cost model — an explicit proxy, not measured, and said so plainly

No tick-level book data exists for this 310-symbol universe (only OHLCV), so unlike Phase
5.2's execution-economics work (which used real bid/ask on BTC/ETH/SOL only, finding
~0.21bps realized spread cost), this cannot be measured directly. Reusing Phase 5.2's tiny
BTC/ETH/SOL spread number across ~300 mostly-illiquid altcoins would be dishonestly
optimistic -- that was an explicit caveat surfaced while checking what to reuse.

- **Fees**: 5.0bps taker per side (`TAKER_FEE_BPS["binance"]` from
  `market_physics_v3/phase5_2_execution_economics.py` -- reused, not reinvented, since this
  panel is also all-Binance).
- **Spread/slippage**: a liquidity-tiered proxy keyed off each symbol's own causal
  `trailing_daily_quote_volume_usd` (already in the panel, PIT-consistent) at formation
  time: >= $500M/day -> 1.0bps; >= $50M/day -> 5.0bps; below that (down to the $5M/day
  eligibility floor) -> 15.0bps. Flagged as a proxy, not ground truth -- a real
  depth-at-5bps measurement would be needed to trust this beyond a first-pass screen.
- **Capacity**: 1% of a symbol's own trailing daily quote volume, an ADV-participation
  rule of thumb (not Phase 5.2's depth-at-5bps method, which needs order-book data this
  panel doesn't have). Portfolio capacity per rebalance = the smallest per-leg capacity
  among that basket's members (bottleneck), same spirit as Phase 5.2's bottleneck-leg
  logic even though the underlying measurement differs.

## Metrics, reported per (hypothesis, horizon, temporal regime) cell

gross bps/trade, turnover, fees bps, spread+slippage bps, net bps, PF, Sharpe, maxDD,
capacity (USD), and **edge/turnover** (net bps divided by turnover) explicitly, since a
lower-IC/lower-turnover cut can dominate a higher-IC/higher-turnover one net of costs.

## Temporal regimes

`2019-2020` (merged, panel data starts 2020-03), `2021`, `2022`, `2023`, `2024`, `2025`,
`2026` (partial, through 2026-07). Any hypothesis/horizon whose `E[PnL_net] > 0` in only
one regime is reported as regime-specific, not a real edge.

## What would make this a real result

A hypothesis/horizon combination with net-positive PnL across multiple independent
regimes (not just one bull or one bear year), positive after the full cost proxy above,
with capacity that isn't trivially small. Anything short of that gets reported as such,
not rounded up.
