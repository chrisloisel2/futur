# W3 — Relative-Value Constructions (Alpha Hunt Round 3, 2026-09-01)

Worker W3, relative-value axis: asset-vs-BTC/ETH/sector relative strength, perp-vs-quarterly-curve
shape (single-asset and cross-asset), cross-asset funding/OI/positioning rank, relative
liquidation pressure, relative volatility. Read-only on `data/derivatives_raw/`,
`data/derivatives_backfill/binance_vision_quarterly/`, `data/derivatives_backfill/binance_vision_metrics/`,
`data/events/liq_cascade_dataset.parquet`, and `data_v2/normalized/event_feature_panel/` (read via
the `futur-data-v2` worktree). No writes to any source dataset, no touches to `src/institutional/`
or `configs/live_alpha_registry.yaml`, Track A (Live Alpha Lab frozen alphas) never read or
influenced.

**Continuity note**: this task was interrupted by a session-wide rate limit twice, both times
after the analysis was complete but before the final report was written. The battery of 67
backtests below (`battery.py`, using the shared `engine.py` harness) and the intermediate parquets
were produced and persisted to scratch in those prior sessions; this pass reads
`results.json` back, verifies it against the harness's own methodology (`engine.py`), and writes
this report. No new backtests were run — the ~30-40 mechanism target was already met (67 candidate
backtests / 28 distinct mechanism families) so no additional analysis was needed.

## 1. Methodology note

**Universe & data build.** `build_master_panel.py` pulls daily OHLC/volume/OI/funding/basis/
basis_z/aggressive-buy-sell-tape for all Binance USDM symbols from
`data_v2/normalized/event_feature_panel/venue=binance/symbol=*` (worktree `/home/qbee/futur-data-v2`),
resampled from intraday to 1D. `build_metrics_panel.py` adds daily OI, top-trader LSR, global
account LSR, and taker buy/sell-volume LSR from `data/derivatives_backfill/binance_vision_metrics/*_metrics_5m.parquet`.
`build_curve.py` builds a BTC/ETH quarterly-futures term-structure panel (front/back annualized
basis, steepness = back minus front) from `data/derivatives_backfill/binance_vision_quarterly/`.
Liquidation-cascade features come from `data/events/liq_cascade_dataset.parquet` (the same
dataset round 2's W2/W9 used, but re-cut here as a cross-sectional *rank* across the universe
rather than a single-symbol repeat-count timer). Sectors (`sector_map.py`) are a manually
documented, single-primary-label taxonomy (L1/L2/DEFI/ORACLE_INFRA/STORAGE_DATA/AI/GAMING/MEME/
PRIVACY/PAYMENTS) over the liquid subset of the universe — approximate by construction, documented
rather than silently assumed.

**PIT discipline** (`engine.py:enrich_master`): entry lag is always 1 day — a signal known at
close *t* enters a position at close *t+1* and exits at close *t+1+h*; every rolling statistic
(realized vol, vol-of-vol, trailing dollar-volume median, trailing beta vs BTC, funding/basis
z-scores) uses `min_periods` equal to its own window so no partial-window value leaks in; listing
age is a PIT per-symbol observation count, not calendar time since a real listing date. Eligibility
requires `listing_age >= 90` observations **and** trailing-30d dollar volume >= $2M before a name
enters any cross-section.

**Declustering**: every cross-sectional test (`engine.xsec_backtest`) rebalances on a
non-overlapping grid — every `horizon_days` calendar days, so no two holding periods overlap by
construction. `N_independent` = number of non-overlapping rebalance dates (or basket-dates for
sector rotation, or pair-dates for the BTC/ETH curve pair); `N_raw` = total long+short leg
observations before declustering, reported alongside it so an inflated pseudo-N is always visible.
The BTC/ETH quarterly-curve pair trades (`engine.pair_backtest`) are single-pair time series, not
cross-sectional legs, so `N_raw == N_independent` there by construction.

**Costs**: 14bps round-trip baseline (5bps taker + 2bps slippage, doubled for entry+exit —
matching round 2's "@14bps" convention, see `engine.py` docstring). `net_bps` in the table below
is gross minus this flat 14bps. A stress column (`net@28bps`) doubles the round-trip cost (28bps)
as a cost-sensitivity check — several PROMISING candidates only clear costs comfortably at 14bps
and are noted where the 28bps margin is thin.

**Status thresholds** (`engine.status_from_stats`, applied mechanically, not hand-picked):
`DATA_LIMITED` if `N_independent < 30`; `PROMISING` if `net_bps > 3` **and** `t_stat > 1.5`
**and** stable (>=3 years of history, >=60% of years net-positive); `WEAK` if `net_bps > 0` and
`t_stat > 1.0`; else `DEAD`. Every one of the 67 backtests cleared the `N_independent >= 30` floor,
so no result is `DATA_LIMITED` in this batch.

**Direction resolution, not cherry-picking**: every mechanism was tested in both directions
(momentum/continuation *and* fade/reversal) as two separate, equally-reported rows, since for most
of these constructions the sign was not obvious ex ante (e.g. is a crowded-funding name a fade or
a momentum continuation?). Both directions appear in the table below — the "wrong-sign" companion
of every PROMISING/WEAK result is shown, not silently dropped, so the asymmetry between the two is
itself visible evidence rather than an assumed prior.

**Known collapse trap, kept as a methodological finding, not hidden**: `D-RVBTC-NAIVE-RANK-7D`
("asset return minus BTC return, ranked cross-sectionally") produces a rank order that is
**mathematically identical** to plain cross-sectional momentum on the same date — subtracting the
same cross-sectional constant (BTC's return, common to every name that day) from every name before
ranking cannot change relative order. This is not a numerical coincidence; it is proven by
construction. It is kept in the results table for the record but excluded from the distinct-
mechanism count. The fix — own-history z-scoring, which normalizes each name by a *per-asset,
time-varying* denominator rather than subtracting a cross-sectionally common constant — is what
makes the z-scored BTC/ETH/sector-relative-strength families (below) genuinely distinct from plain
momentum, and is why that family's z-scored variants are reported as a separate, non-collapsing
construction.

**Capacity**: `avg_dvol30d` (trailing 30d dollar volume, averaged across the long+short legs) is
reported per candidate as a rough capacity proxy where it applies. It is not computed for the
sector-basket, quarterly-curve, and liquidation-cascade-based constructions (marked `N/A` in the
table) since those don't have symmetric single-name long/short legs the same way — `N/A` means
capacity-unmeasured, not capacity-zero.

## 2. Results

### 2a. Family-level summary — one row per distinct mechanism, economic story and best-of-direction result

| family | economic_risk_factor | mechanism (best variant) | variants | best_status | best_net_bps |
|---|---|---|---|---|---|
| vs_BTC_relative_strength(NAIVE-COLLAPSED) | market_wide_momentum | cross-sectional rank of ret7d - btc_ret7d, long-high/short-low, rebal 7d | 1 | **DEAD** (excluded — collapses to momentum) | +5.1 |
| vs_BTC_relative_strength_zscore | idiosyncratic_alt_vs_market_beta | rank relstrength_vs_btc_7d_z (own-history 60d z-score), continuation, rebal 7d | 5 | **DEAD** | +1.7 |
| vs_ETH_relative_strength_zscore | idiosyncratic_alt_vs_altcoin_beta | rank relstrength_vs_eth_7d_z (own-history 60d z-score), continuation, rebal 7d | 2 | **DEAD** | -8.2 |
| vs_sector_relative_strength | idiosyncratic_alt_vs_sector_beta | rank vs_sector_7d within sector-tagged universe, **reversal**, rebal 7d | 4 | **PROMISING** | +46.4 |
| sector_rotation | sector_beta_dispersion | rank 10 sector baskets by trailing-7d avg return, continuation, top/bottom third, rebal 7d | 2 | **PROMISING** (low-df caveat, see below) | +103.0 |
| quarterly_curve_steepness | term_structure_shape | BTC/ETH back-minus-front annualized basis steepness, own-history z-score, mom/fade, 7D/14D | 8 | **DEAD** | +43.1 (best single row, not stable enough) |
| quarterly_curve_steepness_cross_asset | term_structure_relative_value | BTC steepness minus ETH steepness, z-scored; long BTC/short ETH when relatively steeper, rebal 7d | 1 | **PROMISING** | +77.8 |
| cross_asset_funding_rank | positioning_crowding | rank funding_rate across universe, PRICE-ONLY fwd7d, rebal 7d | 2 | **DEAD** | -4.7 |
| funding_spread_vs_btc_zscore | positioning_crowding_relative_to_anchor | rank own-history 90d z-score of (funding - btc_funding), fade, rebal 7d | 2 | **WEAK** | +28.2 |
| funding_rank_momentum | positioning_flow_acceleration | rank of (funding_rank_t - funding_rank_t-7), fade, rebal 7d | 2 | **DEAD** | -10.2 |
| cross_asset_OI_buildup_rank | positioning_buildup | rank 7d OI% change across universe, **fade**, rebal 7d | 2 | **PROMISING** | +32.5 |
| OI_buildup_vs_sector | sector_relative_positioning_buildup | rank (oi_chg7d - sector_avg_oi_chg7d), fade, rebal 7d | 2 | **DEAD** | -7.0 |
| OI_intensity_vs_liquidity | leverage_fragility | rank (OI_usd / trailing dollar volume), mom, rebal 7d | 2 | **DEAD** | +5.4 |
| relative_liquidation_pressure | forced_deleveraging_relative | rank trailing-7d cascade-event count across universe, **reversion**, rebal 7d | 2 | **WEAK** | +24.1 |
| relative_liquidation_direction_skew | forced_deleveraging_directional | rank share of cascades that were long-liquidations, long-liq-heavy underperforms, rebal 7d | 2 | **DEAD** | +19.8 |
| relative_volatility_rank | low_vol_risk_premium | rank trailing-14d realized vol, high-vol-outperforms, rebal 7d | 2 | **DEAD** | +0.7 |
| relative_vol_of_vol_rank | vol_regime_instability | rank trailing vol-of-vol, high-vovol-underperforms, rebal 7d | 2 | **DEAD** | +1.3 |
| volatility_vs_sector | sector_relative_vol_premium | rank (rvol - sector_avg_rvol), mom, rebal 7d | 2 | **DEAD** | +5.1 |
| basis_vs_sector | sector_relative_richness | rank (basis - sector_avg_basis), mom, rebal 7d | 2 | **DEAD** | -12.1 |
| basis_richening_rank | positioning_flow_via_basis | rank (basis_z_1d - basis_z_7d) — basis **velocity**, not level — fade, rebal 7d | 2 | **PROMISING** | +29.7 |
| composite_crowding_score | positioning_crowding_composite | rank sum(funding_vs_btc_z rank, oi_chg7d rank), fade, rebal 7d | 2 | **DEAD** | +5.3 |
| relative_positioning_top-trader_LSR | smart_money_positioning | rank top-trader LSR across universe, mom, rebal 7d | 2 | **DEAD** | -9.0 |
| relative_positioning_taker_buy/sell_volume_ratio | aggressive_flow_positioning | rank taker buy/sell volume ratio, **momentum**, rebal 7d | 2 | **PROMISING** | +51.9 |
| relative_positioning_global_account_LSR | retail_positioning | rank global account LSR, **fade**, rebal 7d | 2 | **PROMISING** | +51.5 |
| smart_money_vs_retail_divergence | informed_vs_uninformed_positioning | rank (toptrader_lsr_rank - global_lsr_rank), momentum, rebal 7d | 2 | **WEAK** | +31.4 |
| relative_liquidity_attention_flow | capital_rotation | rank 7d change in share of universe dollar volume, fade, rebal 7d | 2 | **DEAD** | +17.3 |
| relative_orderflow_imbalance | aggressive_flow_positioning_cross_sectional | rank cross-sectional CVD imbalance, momentum, rebal 7d | 2 | **DEAD** | +20.8 |
| relative_funding_persistence | chronic_crowding_relative_to_peers | rank consecutive days in own top-funding decile, fade, rebal 7d | 2 | **DEAD** | +15.6 |
| price_OI_divergence_rank | short_covering_relative_to_universe | rank (price-mom-rank minus OI-chg-rank), fade, rebal 7d | 2 | **DEAD** | +1.3 |

### 2b. Full candidate-level numeric results (67 backtests)

`stability` = years net-positive / years with data. `net@28bps` = gross minus doubled round-trip
cost (cost-sensitivity check). `cost_sens` = whether the edge survives that doubled cost.

| candidate_id | family | N_raw | N_indep | gross_bps | net_bps | net@28bps | PF | stability | capacity | cost_sens | status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| D-RVBTC-NAIVE-RANK-7D | vs_BTC_relative_strength(NAIVE-COLLAPSED) | 17,156 | 324 | +19.1 | +5.1 | -8.9 | 1.08 | 5/7 | MED($295M) | fails | **DEAD** |
| D-RVBTC-Z-A_mom-7D | vs_BTC_relative_strength_zscore | 17,036 | 324 | +15.7 | +1.7 | -12.3 | 1.08 | 4/7 | MED($179M) | fails | **DEAD** |
| D-RVBTC-Z-A_rev-7D | vs_BTC_relative_strength_zscore | 17,036 | 324 | -15.7 | -29.7 | -43.7 | 0.93 | 3/7 | MED($179M) | fails | **DEAD** |
| D-RVBTC-Z-A_mom-1D | vs_BTC_relative_strength_zscore | 120,312 | 2,276 | -0.1 | -14.1 | -28.1 | 1.00 | 3/7 | MED($263M) | fails | **DEAD** |
| D-RVBTC-Z-A_rev-1D | vs_BTC_relative_strength_zscore | 120,312 | 2,276 | +0.1 | -13.9 | -27.9 | 1.00 | 4/7 | MED($263M) | fails | **DEAD** |
| D-RVBTC-Z-A_mom-7D-HEDGED | vs_BTC_relative_strength_zscore | 16,952 | 324 | +19.9 | +5.9 | -8.1 | 1.10 | 5/7 | MED($180M) | fails | **DEAD** |
| D-RVETH-Z-B_mom-7D | vs_ETH_relative_strength_zscore | 17,036 | 324 | +5.8 | -8.2 | -22.2 | 1.03 | 3/7 | MED($269M) | fails | **DEAD** |
| D-RVETH-Z-B_rev-7D | vs_ETH_relative_strength_zscore | 17,036 | 324 | -5.8 | -19.8 | -33.8 | 0.97 | 4/7 | MED($269M) | fails | **DEAD** |
| D-RVSECTOR-C_mom-7D | vs_sector_relative_strength | 9,812 | 324 | -60.4 | -74.4 | -88.4 | 0.75 | 2/7 | MED($268M) | fails | **DEAD** |
| D-RVSECTOR-C_rev-7D | vs_sector_relative_strength | 9,812 | 324 | +60.4 | +46.4 | +32.4 | 1.34 | 5/7 | MED($268M) | survives | **PROMISING** |
| D-RVSECTOR-C_mom-1D | vs_sector_relative_strength | 68,814 | 2,275 | -9.2 | -23.2 | -37.2 | 0.89 | 1/7 | MED($240M) | fails | **DEAD** |
| D-RVSECTOR-C_rev-1D | vs_sector_relative_strength | 68,814 | 2,275 | +9.2 | -4.8 | -18.8 | 1.13 | 6/7 | MED($240M) | fails | **DEAD** |
| D-SECTOR-ROTATION-D_mom-7D | sector_rotation | 758 | 259 | +117.0 | +103.0 | +89.0 | 1.49 | 6/6 | N/A | survives | **PROMISING** |
| D-SECTOR-ROTATION-D_rev-7D | sector_rotation | 758 | 259 | -117.0 | -131.0 | -145.0 | 0.67 | 0/6 | N/A | fails | **DEAD** |
| D-CURVESHAPE-BTC-MOM-7D | quarterly_curve_steepness | 157 | 157 | +24.2 | +10.2 | -3.8 | 1.11 | 3/5 | N/A | fails | **DEAD** |
| D-CURVESHAPE-BTC-FADE-7D | quarterly_curve_steepness | 157 | 157 | -24.2 | -38.2 | -52.2 | 0.90 | 2/5 | N/A | fails | **DEAD** |
| D-CURVESHAPE-BTC-MOM-14D | quarterly_curve_steepness | 78 | 78 | -54.8 | -68.8 | -82.8 | 0.85 | 2/5 | N/A | fails | **DEAD** |
| D-CURVESHAPE-BTC-FADE-14D | quarterly_curve_steepness | 78 | 78 | +54.8 | +40.8 | +26.8 | 1.17 | 3/5 | N/A | survives | **DEAD** |
| D-CURVESHAPE-ETH-MOM-7D | quarterly_curve_steepness | 157 | 157 | +57.1 | +43.1 | +29.1 | 1.19 | 1/5 | N/A | survives | **DEAD** |
| D-CURVESHAPE-ETH-FADE-7D | quarterly_curve_steepness | 157 | 157 | -57.1 | -71.1 | -85.1 | 0.84 | 4/5 | N/A | fails | **DEAD** |
| D-CURVESHAPE-ETH-MOM-14D | quarterly_curve_steepness | 78 | 78 | +2.4 | -11.6 | -25.6 | 1.00 | 1/5 | N/A | fails | **DEAD** |
| D-CURVESHAPE-ETH-FADE-14D | quarterly_curve_steepness | 78 | 78 | -2.4 | -16.4 | -30.4 | 1.00 | 4/5 | N/A | fails | **DEAD** |
| D-CURVESHAPE-BTCvsETH-PAIR-7D | quarterly_curve_steepness_cross_asset | 157 | 157 | +91.8 | +77.8 | +63.8 | 1.58 | 4/5 | N/A | survives | **PROMISING** |
| D-FUNDRANK-F_fade-7D | cross_asset_funding_rank | 17,094 | 324 | -9.3 | -23.3 | -37.3 | 0.96 | 3/7 | MED($225M) | fails | **DEAD** |
| D-FUNDRANK-F_mom-7D | cross_asset_funding_rank | 17,094 | 324 | +9.3 | -4.7 | -18.7 | 1.05 | 4/7 | MED($225M) | fails | **DEAD** |
| D-FUNDVSBTC-Z-F3_fade-7D | funding_spread_vs_btc_zscore | 16,980 | 324 | +42.2 | +28.2 | +14.2 | 1.27 | 5/7 | MED($187M) | survives | **WEAK** |
| D-FUNDVSBTC-Z-F3_mom-7D | funding_spread_vs_btc_zscore | 16,980 | 324 | -42.2 | -56.2 | -70.2 | 0.79 | 2/7 | MED($187M) | fails | **DEAD** |
| D-FUNDRANKCHG-F4_fade-7D | funding_rank_momentum | 16,892 | 323 | +3.8 | -10.2 | -24.2 | 1.02 | 3/7 | MED($298M) | fails | **DEAD** |
| D-FUNDRANKCHG-F4_mom-7D | funding_rank_momentum | 16,892 | 323 | -3.8 | -17.8 | -31.8 | 0.98 | 4/7 | MED($298M) | fails | **DEAD** |
| D-OICHGRANK-G_mom-7D | cross_asset_OI_buildup_rank | 15,120 | 241 | -46.5 | -60.5 | -74.5 | 0.72 | 2/6 | MED($154M) | fails | **DEAD** |
| D-OICHGRANK-G_fade-7D | cross_asset_OI_buildup_rank | 15,120 | 241 | +46.5 | +32.5 | +18.4 | 1.38 | 4/6 | MED($154M) | survives | **PROMISING** |
| D-OICHG-VS-SECTOR-G3_mom-7D | OI_buildup_vs_sector | 8,528 | 242 | -7.0 | -21.0 | -35.0 | 0.96 | 4/6 | MED($203M) | fails | **DEAD** |
| D-OICHG-VS-SECTOR-G3_fade-7D | OI_buildup_vs_sector | 8,528 | 242 | +7.0 | -7.0 | -21.0 | 1.05 | 2/6 | MED($203M) | fails | **DEAD** |
| D-OIPERDVOL-G5_fade-7D | OI_intensity_vs_liquidity | 15,154 | 242 | -19.4 | -33.4 | -47.4 | 0.90 | 1/6 | MED($200M) | fails | **DEAD** |
| D-OIPERDVOL-G5_mom-7D | OI_intensity_vs_liquidity | 15,154 | 242 | +19.4 | +5.4 | -8.6 | 1.11 | 5/6 | MED($200M) | fails | **DEAD** |
| D-LIQPRESSURE-RANK-H_mom-7D | relative_liquidation_pressure | 4,044 | 286 | -38.1 | -52.1 | -66.1 | 0.84 | 2/6 | N/A | fails | **DEAD** |
| D-LIQPRESSURE-RANK-H_rev-7D | relative_liquidation_pressure | 4,044 | 286 | +38.1 | +24.1 | +10.1 | 1.19 | 4/6 | N/A | survives | **WEAK** |
| D-LIQDIRSKEW-RANK-H3_a-7D | relative_liquidation_direction_skew | 1,742 | 207 | +33.8 | +19.8 | +5.8 | 1.15 | 5/6 | N/A | survives | **DEAD** |
| D-LIQDIRSKEW-RANK-H3_b-7D | relative_liquidation_direction_skew | 1,742 | 207 | -33.8 | -47.8 | -61.8 | 0.87 | 1/6 | N/A | fails | **DEAD** |
| D-RVOLRANK-I_a-7D | relative_volatility_rank | 17,156 | 324 | -14.7 | -28.6 | -42.6 | 0.95 | 3/7 | HIGH($472M) | fails | **DEAD** |
| D-RVOLRANK-I_b-7D | relative_volatility_rank | 17,156 | 324 | +14.7 | +0.7 | -13.3 | 1.05 | 4/7 | HIGH($472M) | fails | **DEAD** |
| D-VOLOFVOLRANK-I2_a-7D | relative_vol_of_vol_rank | 17,156 | 324 | +15.3 | +1.3 | -12.7 | 1.08 | 5/7 | HIGH($376M) | fails | **DEAD** |
| D-VOLOFVOLRANK-I2_b-7D | relative_vol_of_vol_rank | 17,156 | 324 | -15.3 | -29.3 | -43.3 | 0.93 | 2/7 | HIGH($376M) | fails | **DEAD** |
| D-RVOL-VS-SECTOR-I3_fade-7D | volatility_vs_sector | 9,812 | 324 | -19.1 | -33.1 | -47.1 | 0.92 | 3/7 | HIGH($310M) | fails | **DEAD** |
| D-RVOL-VS-SECTOR-I3_mom-7D | volatility_vs_sector | 9,812 | 324 | +19.1 | +5.1 | -8.9 | 1.08 | 4/7 | HIGH($310M) | fails | **DEAD** |
| D-BASIS-VS-SECTOR-J1_fade-7D | basis_vs_sector | 9,186 | 324 | -1.9 | -15.9 | -29.9 | 0.99 | 2/7 | MED($290M) | fails | **DEAD** |
| D-BASIS-VS-SECTOR-J1_mom-7D | basis_vs_sector | 9,186 | 324 | +1.9 | -12.1 | -26.1 | 1.01 | 5/7 | MED($290M) | fails | **DEAD** |
| D-BASISMOTION-RANK-J2_fade-7D | basis_richening_rank | 15,754 | 324 | +43.7 | +29.7 | +15.7 | 1.29 | 5/7 | HIGH($378M) | survives | **PROMISING** |
| D-BASISMOTION-RANK-J2_mom-7D | basis_richening_rank | 15,754 | 324 | -43.7 | -57.7 | -71.7 | 0.77 | 2/7 | HIGH($378M) | fails | **DEAD** |
| D-CROWDCOMPOSITE-K_fade-7D | composite_crowding_score | 15,046 | 242 | +19.3 | +5.3 | -8.7 | 1.17 | 3/6 | MED($162M) | fails | **DEAD** |
| D-CROWDCOMPOSITE-K_mom-7D | composite_crowding_score | 15,046 | 242 | -19.3 | -33.3 | -47.3 | 0.85 | 3/6 | MED($162M) | fails | **DEAD** |
| D-TOPTRADER_LSR-fade-7D | relative_positioning_top-trader_LSR | 13,296 | 196 | -5.0 | -19.0 | -33.0 | 0.97 | 2/6 | MED($246M) | fails | **DEAD** |
| D-TOPTRADER_LSR-mom-7D | relative_positioning_top-trader_LSR | 13,296 | 196 | +5.0 | -9.0 | -23.0 | 1.03 | 4/6 | MED($246M) | fails | **DEAD** |
| D-TAKER_LSR-fade-7D | relative_positioning_taker_buy/sell_volume_ratio | 14,530 | 224 | -65.9 | -79.9 | -93.9 | 0.67 | 1/6 | MED($246M) | fails | **DEAD** |
| D-TAKER_LSR-mom-7D | relative_positioning_taker_buy/sell_volume_ratio | 14,530 | 224 | +65.9 | +51.9 | +37.9 | 1.49 | 5/6 | MED($246M) | survives | **PROMISING** |
| D-GLOBAL_LSR-fade-7D | relative_positioning_global_account_LSR | 15,002 | 239 | +65.5 | +51.5 | +37.5 | 1.43 | 5/6 | HIGH($386M) | survives | **PROMISING** |
| D-GLOBAL_LSR-mom-7D | relative_positioning_global_account_LSR | 15,002 | 239 | -65.5 | -79.5 | -93.5 | 0.70 | 1/6 | HIGH($386M) | fails | **DEAD** |
| D-SMARTRETAIL-DIVERGENCE-N4_fade-7D | smart_money_vs_retail_divergence | 13,284 | 196 | -45.4 | -59.4 | -73.4 | 0.73 | 1/6 | MED($282M) | fails | **DEAD** |
| D-SMARTRETAIL-DIVERGENCE-N4_mom-7D | smart_money_vs_retail_divergence | 13,284 | 196 | +45.4 | +31.4 | +17.4 | 1.36 | 5/6 | MED($282M) | survives | **WEAK** |
| D-DVOLSHARE-RANK-O_mom-7D | relative_liquidity_attention_flow | 17,132 | 324 | -31.3 | -45.3 | -59.3 | 0.86 | 2/7 | MED($187M) | fails | **DEAD** |
| D-DVOLSHARE-RANK-O_fade-7D | relative_liquidity_attention_flow | 17,132 | 324 | +31.3 | +17.3 | +3.3 | 1.16 | 5/7 | MED($187M) | survives | **DEAD** |
| D-CVDIMB-RANK-P_mom-7D | relative_orderflow_imbalance | 17,108 | 324 | +34.8 | +20.8 | +6.8 | 1.19 | 5/7 | HIGH($341M) | survives | **DEAD** |
| D-CVDIMB-RANK-P_fade-7D | relative_orderflow_imbalance | 17,108 | 324 | -34.8 | -48.8 | -62.8 | 0.84 | 2/7 | HIGH($341M) | fails | **DEAD** |
| D-FUNDPERSIST-RANK-Q_fade-7D | relative_funding_persistence | 17,156 | 324 | +29.6 | +15.6 | +1.6 | 1.19 | 4/7 | MED($185M) | survives | **DEAD** |
| D-FUNDPERSIST-RANK-Q_mom-7D | relative_funding_persistence | 17,156 | 324 | -29.6 | -43.6 | -57.6 | 0.84 | 3/7 | MED($185M) | fails | **DEAD** |
| D-PRICEOI-DIVERGENCE-R_mom-7D | price_OI_divergence_rank | 15,120 | 241 | -15.3 | -29.3 | -43.3 | 0.91 | 3/6 | MED($245M) | fails | **DEAD** |
| D-PRICEOI-DIVERGENCE-R_fade-7D | price_OI_divergence_rank | 15,120 | 241 | +15.3 | +1.3 | -12.7 | 1.10 | 3/6 | MED($245M) | fails | **DEAD** |

Note: DEAD rows with `cost_sens=survives` (e.g. `D-CURVESHAPE-ETH-MOM-7D`, `D-DVOLSHARE-RANK-O_fade-7D`,
`D-CVDIMB-RANK-P_mom-7D`, `D-FUNDPERSIST-RANK-Q_fade-7D`, `D-LIQDIRSKEW-RANK-H3_a-7D`) clear the
28bps stress cost but fail the `status_from_stats` t-stat/stability bar at 14bps — cost was never
their binding constraint, statistical strength or year-stability was.

Full untruncated `mechanism`/`distinctness`/`note` text for every row (including per-row rationale
for why each construction is a genuinely distinct economic bet from round-2 and from its siblings
in this table) is preserved verbatim in
`/tmp/claude-1000/-home-qbee-futur/a0e00e24-e75f-4382-80ba-28c16b0aba06/scratchpad/round3/w3/results.json`,
alongside the harness (`engine.py`, `battery.py`) and data-build scripts (`build_master_panel.py`,
`build_metrics_panel.py`, `build_curve.py`, `prep2.py`, `sector_map.py`) and intermediate parquets.

## 3. TOTAL_MECHANISMS_TESTED

**67 individual parameterized backtests**, spanning **29 family constructions**, of which one
(`D-RVBTC-NAIVE-RANK-7D`) is explicitly excluded from the distinct-mechanism count because it is a
proven mathematical restatement of plain cross-sectional momentum, not a new economic bet →
**28 distinct relative-value mechanisms tested**. Status breakdown across all 67 rows: **57 DEAD,
3 WEAK, 7 PROMISING**, 0 BLOCKED, 0 DATA_LIMITED (every construction cleared `N_independent >= 30`).
At the family level (28 distinct, post-exclusion): 18 families DEAD in both directions, 3 families
best-WEAK, 7 families best-PROMISING.

## 4. Top findings in prose

**The base rate strongly extends round 2's funding/basis-decay finding to the broader relative-
positioning space: most rank/relative-value constructions here are dead too (57/67, 85%), and the
survivors cluster into a specific, interpretable pattern rather than being scattered noise.**
Plain-level positioning ranks (funding level, funding-rank momentum, funding persistence duration,
basis level vs sector) are uniformly DEAD or at best marginal — consistent with round 2's
conclusion that funding/basis mean-reversion is largely arbitraged away 2025-2026, extended here
to funding's cross-sectional *rank* rather than its time-series level. What survives falls into
three buckets: (a) constructions that condition out a shared factor (sector-relative, cross-asset-
relative) rather than ranking a raw level; (b) constructions using the *rate of change/velocity* of
a positioning variable rather than its level; and (c) constructions built on data sources (taker
tape, global-account LSR) that are architecturally distinct from the funding/basis/OI axis round 2
already worked over.

**Two positioning-flow findings tell a coherent, complementary story rather than a contradiction:**
`D-TAKER_LSR-mom-7D` (net +51.9bps, t=2.16, PF=1.49, 5/6 years positive — the single strongest
t-stat in this entire batch) says names with an aggressively-buy-skewed taker tape **continue**
rising, while `D-GLOBAL_LSR-fade-7D` (net +51.5bps, t=2.05, PF=1.43, 5/6 years) says names where
**retail account count** is heavily long **fade**. These are not the same bet: taker LSR measures
short-horizon *execution flow* (informed continuation), global-account LSR measures the *stock* of
retail positioning (crowded contrarian). Both clear the 28bps stress cost with real margin
(+37.9bps and +37.5bps respectively). `D-SMARTRETAIL-DIVERGENCE-N4_mom-7D` (WEAK, net +31.4bps,
t=1.09) — top-trader LSR rank minus global LSR rank, i.e. smart money more long than retail predicts
continuation — sits directly between these two as a plausible synthesis, though it doesn't clear
the PROMISING bar on its own.

**A genuinely new cross-asset construction survives cleanly: BTC-vs-ETH quarterly-curve steepness**
(`D-CURVESHAPE-BTCvsETH-PAIR-7D`, net +77.8bps, t=1.94, PF=1.58, 4/5 years, survives 28bps stress
at +63.8bps). This is a true relative-value trade — long the underlying whose term structure is
comparatively steeper, short the other's — made possible only because both BTC and ETH have
quarterly-futures curve data. It is explicitly distinct from the already-live
`FUNDING_BASIS_DISAGREEMENT_V2` alpha (which trades funding vs quarterly-basis *level* disagreement
on a single asset) and from round-2 W4's construction: this is curve *shape* (steepness between two
curve points), cross-asset, not curve level vs spot. Every **single-asset** curve-steepness
variant (8 of them, BTC and ETH, 7D/14D, mom/fade) was DEAD — the edge only appears once BTC and
ETH's curves are compared to *each other*, i.e. it is a genuinely relative-value signal, not
recoverable from either curve in isolation.

**Basis *velocity* (not level) is a second distinct basis-family survivor**:
`D-BASISMOTION-RANK-J2_fade-7D` (net +29.7bps, t=1.58, PF=1.29, 5/7 years) ranks how fast each
name's basis z-score is moving (`basis_z_1d - basis_z_7d`) and fades the fastest-richening names —
different from both round-2 M4 (basis *level* rank, now decayed) and this round's `basis_vs_sector`
(sector-relative basis level, DEAD). Paired with `D-OICHGRANK-G_fade-7D` (fade heaviest 7d OI
build-up relative to the universe, net +32.5bps, t=1.83, PF=1.38, 4/6 years, survives 28bps stress)
and `D-RVSECTOR-C_rev-7D` (within-sector laggards bounce vs sector leaders, net +46.4bps, t=1.73,
PF=1.34, 5/7 years), these three form a believable "crowding/dispersion mean-reverts within a
peer group" cluster distinct from anything round 2 tested.

**Caveat on the largest number in the table**: `D-SECTOR-ROTATION-D_mom-7D` (leading sector basket
keeps leading, net +103.0bps, t=1.96, PF=1.49, 6/6 years positive) is the single largest net_bps
figure this round and formally clears PROMISING, but its own `capacity_note` flags a real problem —
the cross-section is only **10 sector baskets**, so `N_independent=259` overstates true statistical
degrees of freedom (10 correlated basket-return series resampled 259 times is not 259 independent
draws the way 324 individual-name cross-sections are). Treat this as a real, striking, but
lower-confidence result pending a block-bootstrap or basket-level significance re-check before
sizing — not a false positive, but a number that needs a harder test than the mechanical
`status_from_stats` threshold applies to name-level cross-sections.

**Two WEAK results worth flagging as directionally consistent but sub-threshold**: relative
liquidation-cascade intensity mean-reverts (`D-LIQPRESSURE-RANK-H_rev-7D`, net +24.1bps, t=1.02,
4/6 years) — a cross-sectional-rank cut of the same liquidation dataset round 2's W2/W9 used
single-symbol repeat-count timing on, here asking "does this name relative to *peers* look
liquidation-heavy" rather than "has this symbol been hit twice in 24h" — and funding spread vs BTC,
z-scored, fades (`D-FUNDVSBTC-Z-F3_fade-7D`, net +28.2bps, t=1.33, 5/7 years, survives 28bps stress
at +14.2bps) — genuinely distinct from the plain funding-rank fade (DEAD, collapses in spirit to
round-2's decayed funding mean-reversion) because the BTC-anchor z-score uses a per-asset,
time-varying denominator rather than a common cross-sectional subtraction.
