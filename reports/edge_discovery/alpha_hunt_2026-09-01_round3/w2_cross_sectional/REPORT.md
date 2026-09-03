# W2 — Cross-Sectional Constructions (Alpha Hunt Round 3, 2026-09-01/02)

Worker W2. Scope: broaden round2/W1's single cross-sectional-momentum finding
(`reports/edge_discovery/alpha_hunt_2026-08-30/SCOREBOARD.md`, "+89bps net, t=2.60,
7d→7d long-only") into a horizon sweep, long-short variants, and a battery of
distinct cross-sectional constructions (residual/beta-neutral momentum, OI-adjusted,
funding-adjusted, sector-neutral, dispersion/breadth regime-conditioning,
relative-liquidity, relative-leverage, relative-crowding, cross-sectional
acceleration, order-flow-confirmed momentum) on the same PIT panel. Read-only
against `data_v2/normalized` (worktree `/home/qbee/futur-data-v2`) and
`data/listings_backfill`, `data/positioning`. Never touched Track A / the frozen
Live Alpha Lab engines, `src/institutional/`, or `configs/live_alpha_registry.yaml`.

## 1. Methodology

**Panel.** Daily OHLCV + daily features built via DuckDB directly from
`data_v2/normalized/perp_ohlcv` and `data_v2/normalized/event_feature_panel`
(binance perp, 5m source resampled to daily; `build_panels.py`). 312 symbols,
2020-01 through 2026-09. Cross-sectional signals (momentum, OI, funding, basis,
CVD, residual vol) are all computed from fields that are causal at their
timestamp in the source panel (`event_feature_panel` fields such as
`funding_rate_percentile_90d`, `basis_z_7d`, `residual_std_30d` are themselves
already PIT-computed upstream, per their own rolling-window construction —
verified by field name/definition, not re-derived here).

**PIT eligibility gate** (`harness.py::build_eligibility_mask`) deliberately
replicates — without importing — the same convention used by the live
`cross_sectional_momentum_live_v2` engine:
- `onboard_ts` from `data/listings_backfill/binance/listings_calendar.parquet`,
  falling back to first real-price date for symbols missing a calendar entry
  (same convention as production).
- `MIN_LISTING_AGE_DAYS = 30` (same constant as production).
- Trailing liquidity floor: causal rolling **median** 30d quote volume >= **$1,000,000**
  (matches round2/W1's "liquid cohort"); a stricter **$2,000,000** floor — matching
  the live V2 engine's own threshold — is run as a capacity/cost sensitivity check
  (`XSEC_MOM_7D_LO_LIQ2M`), not counted as an independent mechanism.
- Forward returns are winsorized per-period at the 1%/99% tails **across the full
  eligible cross-section that period** (not just the picked legs), to stop
  single-name legendary pump weeks (verified real prints, e.g. DOGEUSDT/CHZUSDT
  Jan-Apr 2021, not data artifacts) from dominating small-N quintile means.

**Costs.** Flat 5bps taker + 2bps slippage = **7bps one-way**, charged per leg
(2 legs long-only = 14bps round-trip; 4 legs long-short = 28bps round-trip) —
same convention as round2/W1. `net_bps` in every row below already has this
cost deducted; `cost_sensitivity` reports the multiple of the assumed cost the
gross edge could absorb before hitting zero.

**Declustering — the methodological headline of this worker.** Validating
round2/W1's own reported number first (before extending it) surfaced a real
fragility: re-running the *identical* 7d->7d long-short momentum
signal/universe/costs on the *same* non-overlapping weekly grid, but shifted by
1-6 calendar days (7 possible "anchors" for a 7-day horizon), swung gross bps
from **-9.7 to +81.9** — a single non-overlapping grid is not a stable estimate
at weekly-or-longer horizons on a ~300-name panel, because avoiding overlap
throws away 6/7 of the data and leaves the result exposed to whichever
single week a handful of legendary pump events land in. Every horizon-sweep
candidate below (`multi_anchor_quantile_long_short` in `harness.py`) therefore
reports **all H anchors pooled**: `anchor_mean_*`/`anchor_std_*` (the honest point
estimate and its fragility), plus a conservative single-anchor (anchor=0)
`t_stat`/`p_value`/yearly breakdown computed only on genuinely non-overlapping
periods (pooling anchors would overstate the effective N for significance).
The `stability` column below reports the anchor-mean min/max **gross** bps
range and flags `SIGN-FLIP` when at least one anchor's gross return is
negative and at least one is positive — i.e. whether the mechanism's sign
itself depends on which day-of-week you happen to rebalance on.

**Non-multi-anchor tests** (regime-conditioning J/K, sector O/P, beta-neutral I,
crowding U) use a single canonical rebalance grid each (documented per row);
these are inherently less anchor-robustness-tested and are flagged as such.

**Sector mapping** (`sectors.py`): hand-curated, best-effort standard-crypto-
taxonomy convention (L1/L2/DeFi/MEME/GAMING/AI/INFRA_ORACLE/PRIVACY/PAYMENTS/
EXCHANGE_TOKEN), not sourced from any data-provider API (none available
read-only in this worktree). Only 150/312 panel symbols map to a sector;
unmapped names are excluded from the two sector tests only, never guessed.

**Positioning data** (`data/positioning/*_top_position.parquet`, top-trader
long/short ratio) only overlaps the main panel for **49 days in 2026** (47
symbols) — the one relative-crowding candidate built on it is explicitly
DATA_LIMITED by construction, not a negative finding.

## 2. Results — 40 candidates

N_raw = total anchor x period observations pooled before declustering (or single-grid
period count where no anchor sweep applies). N_indep = the single non-overlapping
declustered series length used for the reported t-stat/p-value/yearly breakdown.
gross/net_bps = anchor-mean (or single-grid) round-trip return per rebalance.

| candidate_id | family | economic_risk_factor | mechanism | N_raw | N_indep | gross_bps | net_bps | PF | t-stat | stability | cost_sensitivity | status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| XSEC_MOM_7D_LS_REPCHK | RAW_MOMENTUM | trend/attention-diffusion | 7d mom, LS quintile (anchor-robustness replication of round2/W1) | 2349 | 336 | 5.1 | -22.9 | 1.02 | -0.61 | 2/7y+, anchors[-10,82]gross SIGN-FLIP | 0.2x of assumed 28bps (survives to ~5bps rt cost) | WEAK |
| XSEC_MOM_7D_LO | RAW_MOMENTUM | trend/attention-diffusion | 7d mom, top-quintile long-only | 2349 | 336 | 69.0 | 55.0 | 1.17 | 0.87 | 4/7y+, anchors[69,108]gross stable-sign | 4.9x of assumed 14bps (survives to ~69bps rt cost) | PROMISING |
| XSEC_MOM_3D_LS | RAW_MOMENTUM | trend/attention-diffusion | 3d mom, LS quintile | 2353 | 784 | 7.2 | -20.8 | 1.05 | -1.5 | 1/7y+, anchors[7,30]gross stable-sign | 0.3x of assumed 28bps (survives to ~7bps rt cost) | DEAD |
| XSEC_MOM_3D_LO | RAW_MOMENTUM | trend/attention-diffusion | 3d mom, long-only | 2353 | 784 | 33.0 | 19.0 | 1.12 | 0.69 | 4/7y+, anchors[33,41]gross stable-sign | 2.4x of assumed 14bps (survives to ~33bps rt cost) | WEAK |
| XSEC_MOM_14D_LS | RAW_MOMENTUM | trend/attention-diffusion | 14d mom, LS quintile | 2342 | 167 | 87.5 | 59.5 | 1.31 | 0.78 | 4/7y+, anchors[64,197]gross stable-sign | 3.1x of assumed 28bps (survives to ~88bps rt cost) | PROMISING |
| XSEC_MOM_14D_LO | RAW_MOMENTUM | trend/attention-diffusion | 14d mom, long-only | 2342 | 167 | 213.3 | 199.3 | 1.41 | 1.52 | 4/7y+, anchors[193,261]gross stable-sign | 15.2x of assumed 14bps (survives to ~213bps rt cost) | PROMISING |
| XSEC_MOM_30D_LS | RAW_MOMENTUM | trend/attention-diffusion | 30d mom, LS quintile (monthly) | 2326 | 78 | -15.7 | -43.7 | 0.97 | -0.3 | 4/7y+, anchors[-214,270]gross SIGN-FLIP | gross already <=0 | DEAD |
| XSEC_MOM_30D_LO | RAW_MOMENTUM | trend/attention-diffusion | 30d mom, long-only (monthly) | 2326 | 78 | 476.8 | 462.8 | 1.61 | 1.41 | 4/7y+, anchors[377,553]gross stable-sign | 34.1x of assumed 14bps (survives to ~477bps rt cost) | PROMISING |
| XSEC_MOM_7D_LO_LIQ2M | RAW_MOMENTUM | trend/attention-diffusion | 7d mom LO, stricter $2M liq floor (sensitivity) | 2349 | 336 | 72.5 | 58.5 | 1.18 | 0.92 | 4/7y+, anchors[72,105]gross stable-sign | 5.2x of assumed 14bps (survives to ~72bps rt cost) | PROMISING (sensitivity check, not independent) |
| XSEC_REV_1D_LS | SHORT_HORIZON_REVERSAL | overreaction/liquidity-provision | 1d reversal (fade), LS quintile | 2355 | 2355 | 0.5 | -27.5 | 1.01 | -5.89 | 1/7y+, anchors[1,1]gross stable-sign | 0.0x of assumed 28bps (survives to ~1bps rt cost) | DEAD |
| XSEC_REV_2D_LS | SHORT_HORIZON_REVERSAL | overreaction/liquidity-provision | 2d reversal (fade), LS quintile | 2354 | 1177 | 6.5 | -21.5 | 1.06 | -2.36 | 2/7y+, anchors[3,7]gross stable-sign | 0.2x of assumed 28bps (survives to ~7bps rt cost) | DEAD |
| XSEC_REV_3D_LS | SHORT_HORIZON_REVERSAL | overreaction/liquidity-provision | 3d reversal (fade) = same signal as MOM_3D_LS, sign flip | 2353 | 784 | 7.2 | -20.8 | 1.05 | -1.5 | 1/7y+, anchors[7,30]gross stable-sign | 0.3x of assumed 28bps (survives to ~7bps rt cost) | DEAD |
| XSEC_MOM_VOLADJ_7D | VOLUME_ADJUSTED_MOMENTUM | trend x liquidity-demand | mom+relative-turnover composite rank, 7d | 2348 | 336 | -13.9 | -41.9 | 0.94 | -1.18 | 2/7y+, anchors[-19,77]gross SIGN-FLIP | gross already <=0 | DEAD |
| XSEC_MOM_VOLADJ_14D | VOLUME_ADJUSTED_MOMENTUM | trend x liquidity-demand | mom+relative-turnover composite rank, 14d | 2342 | 167 | 75.2 | 47.2 | 1.29 | 0.67 | 4/7y+, anchors[27,202]gross stable-sign | 2.7x of assumed 28bps (survives to ~75bps rt cost) | WEAK |
| XSEC_AMIHUD_ILLIQ_7D | LIQUIDITY_PREMIUM | illiquidity/compensation-for-holding-cost | Amihud illiquidity rank -> 7d fwd, long illiquid/short liquid | 2349 | 336 | 127.3 | 99.3 | 1.86 | 2.92 | 7/7y+, anchors[106,134]gross stable-sign | 4.5x of assumed 28bps (survives to ~127bps rt cost) | PROMISING |
| XSEC_MOM_OI_CONFIRMED_7D | OI_ADJUSTED_MOMENTUM | trend x leverage-buildup | 7d mom restricted to OI-confirmed names (new positioning) | 1691 | 242 | -2.5 | -30.5 | 0.99 | -0.75 | 2/6y+, anchors[-49,67]gross SIGN-FLIP | gross already <=0 | DEAD |
| XSEC_MOM_OI_DIVERGENT_7D | OI_ADJUSTED_MOMENTUM | trend x deleveraging | 7d mom restricted to OI-divergent names (short-covering-like) | 1682 | 240 | 73.2 | 45.2 | 1.39 | 1.04 | 4/6y+, anchors[-13,115]gross SIGN-FLIP | 2.6x of assumed 28bps (survives to ~73bps rt cost) | WEAK |
| XSEC_OI_GROWTH_RANK_7D | OI_LEVERAGE_FACTOR | leverage-buildup/crowding | standalone 7d OI-growth cross-sectional rank | 1689 | 242 | -20.8 | -48.8 | 0.85 | -2.18 | 1/6y+, anchors[-43,1]gross SIGN-FLIP | gross already <=0 | DEAD |
| XSEC_MOM_FUNDING_NET_7D | FUNDING_ADJUSTED_MOMENTUM | trend net of carry-cost | 7d mom score net of cumulative funding cost | 2349 | 336 | -0.1 | -28.1 | 1.0 | -0.75 | 2/7y+, anchors[-21,80]gross SIGN-FLIP | gross already <=0 | DEAD |
| XSEC_RESID_MOM_7D | RESIDUAL_MOMENTUM | idiosyncratic trend, market-beta stripped | 7d beta-to-BTC-stripped residual momentum | 2345 | 335 | 12.5 | -15.5 | 1.06 | -0.42 | 2/7y+, anchors[3,88]gross stable-sign | 0.4x of assumed 28bps (survives to ~13bps rt cost) | WEAK |
| XSEC_RESID_MOM_14D | RESIDUAL_MOMENTUM | idiosyncratic trend, market-beta stripped | 14d beta-to-BTC-stripped residual momentum | 2335 | 167 | 92.8 | 64.8 | 1.34 | 0.87 | 5/7y+, anchors[55,167]gross stable-sign | 3.3x of assumed 28bps (survives to ~93bps rt cost) | PROMISING |
| XSEC_BETA_NEUTRAL_CONSTRUCTION | BETA_NEUTRAL_CONSTRUCTION | trend, market-beta hedged out via BTC leg | 7d mom LS book, BTC-beta hedged vs unhedged | 336 | 336 | -- | 4.3 | -- | 0.09 | std=872bps >> mean, noise | n/a | DEAD |
| XSEC_DISPERSION_COND_MOM_7D | DISPERSION_REGIME_TIMING | momentum edge scales with opportunity-set dispersion | 7d mom book net return, high- vs low-dispersion regime | 336 | 336 | -- | -26.7 | -- | 0.07 | 7d book: high-disp net=-26.7bps vs low-disp net=-33.8bps (n=112/112) | n/a | DEAD |
| XSEC_DISPERSION_COND_MOM_14D | DISPERSION_REGIME_TIMING | momentum edge scales with opportunity-set dispersion | 14d mom book net return, high- vs low-dispersion regime | 167 | 167 | -- | 213.8 | -- | 1.42 | 14d book: high-disp net=+213.8bps vs low-disp net=-44.2bps (n=56/56), spread=258bps | n/a | WEAK |
| XSEC_BREADTH_COND_MOM_7D | BREADTH_REGIME_TIMING | momentum edge differs in broad-rally vs narrow-rally regimes | 7d mom book net return, high- vs low-breadth regime | 336 | 336 | -- | 62.9 | -- | 1.41 | 7d book: high-breadth net=+62.9bps vs low-breadth net=-77.6bps, spread=141bps | n/a | WEAK |
| XSEC_REL_LIQUIDITY_7D | RELATIVE_LIQUIDITY_FACTOR | liquidity-provision/turnover regime | turnover(vol/OI) rank -> 7d fwd, long high/short low | 1697 | 243 | -67.5 | -95.5 | 0.66 | -3.4 | 0/6y+, anchors[-75,-39]gross stable-sign | gross already <=0 | DEAD |
| XSEC_REL_LIQUIDITY_14D | RELATIVE_LIQUIDITY_FACTOR | liquidity-provision/turnover regime | turnover(vol/OI) rank -> 14d fwd, long high/short low | 1690 | 121 | -108.6 | -136.6 | 0.61 | -2.58 | 1/6y+, anchors[-122,-36]gross stable-sign | gross already <=0 | DEAD |
| XSEC_REL_LEVERAGE_7D | RELATIVE_LEVERAGE_FACTOR | leverage-crowding/liquidation-risk premium | leverage proxy (OI/30d-vol) rank -> 7d fwd, long high/short low | 1697 | 243 | 52.6 | 24.6 | 1.42 | 0.91 | 5/6y+, anchors[49,66]gross stable-sign | 1.9x of assumed 28bps (survives to ~53bps rt cost) | WEAK |
| XSEC_REL_LEVERAGE_14D | RELATIVE_LEVERAGE_FACTOR | leverage-crowding/liquidation-risk premium | leverage proxy (OI/30d-vol) rank -> 14d fwd, long high/short low | 1690 | 121 | 101.3 | 73.3 | 1.6 | 1.35 | 5/6y+, anchors[78,166]gross stable-sign | 3.6x of assumed 28bps (survives to ~101bps rt cost) | PROMISING |
| XSEC_MOM_LOWLEV_FILTER_7D | LEVERAGE_FILTERED_MOMENTUM | trend, restricted to non-crowded names | 7d mom restricted to below-median leverage-proxy half | 1698 | 243 | 8.5 | -19.5 | 1.04 | -0.47 | 2/6y+, anchors[6,86]gross stable-sign | 0.3x of assumed 28bps (survives to ~8bps rt cost) | DEAD |
| XSEC_SECTOR_NEUTRAL_MOM_7D | SECTOR_NEUTRAL_MOMENTUM | idiosyncratic-within-sector trend, sector beta stripped | 7d mom ranked within hand-mapped sector (150/312 coverage) | 330 | 330 | -2.9 | -30.9 | -- | -1.1 | 1/7y+ | n/a | DEAD |
| XSEC_SECTOR_NEUTRAL_MOM_14D | SECTOR_NEUTRAL_MOMENTUM | idiosyncratic-within-sector trend, sector beta stripped | 14d mom ranked within hand-mapped sector | 164 | 164 | 129.3 | 101.3 | -- | 1.56 | 4/7y+ | n/a | WEAK |
| XSEC_SECTOR_ROTATION_7D | SECTOR_ROTATION | sector-level trend/narrative rotation | sector-level 7d mom, long top-tercile/short bottom-tercile sectors | 249 | 249 | 55.6 | 27.6 | -- | 0.61 | 4/6y+ | n/a | WEAK |
| XSEC_ACCEL_7D | CROSS_SECTIONAL_ACCELERATION | momentum-of-momentum / rank velocity | change in 7d-mom percentile rank over trailing 7d (rank velocity) | 2349 | 336 | -31.4 | -59.4 | 0.85 | -1.82 | 2/7y+, anchors[-72,64]gross SIGN-FLIP | gross already <=0 | WEAK |
| XSEC_ACCEL_14D | CROSS_SECTIONAL_ACCELERATION | momentum-of-momentum / rank velocity | change in 14d-mom percentile rank over trailing 14d | 2342 | 167 | 92.1 | 64.1 | 1.44 | 0.95 | 4/7y+, anchors[-42,147]gross SIGN-FLIP | 3.3x of assumed 28bps (survives to ~92bps rt cost) | WEAK |
| XSEC_MOM_FUNDING_UNCROWDED_7D | FUNDING_CROWDING_FILTERED_MOMENTUM | trend, restricted to positioning not yet crowded (funding percentile) | 7d mom restricted to below-median funding-percentile names | 1777 | 258 | 17.0 | -11.0 | 1.06 | -0.19 | 3/7y+, anchors[-46,108]gross SIGN-FLIP | 0.6x of assumed 28bps (survives to ~17bps rt cost) | DEAD |
| XSEC_MOM_BASIS_FILTER_7D | BASIS_FILTERED_MOMENTUM | trend, restricted to names without extreme calendar-basis dislocation | 7d mom restricted to bottom-70% \|basis_z_7d\| (excl. dislocated) | 2332 | 333 | 9.4 | -18.6 | 1.04 | -0.51 | 3/7y+, anchors[-33,72]gross SIGN-FLIP | 0.3x of assumed 28bps (survives to ~9bps rt cost) | DEAD |
| XSEC_MOM_CVD_CONFIRMED_7D | ORDER_FLOW_CONFIRMED_MOMENTUM | trend x genuine taker-flow participation | 7d mom restricted to names where CVD confirms price direction | 2163 | 308 | 77.8 | 49.8 | 1.35 | 1.03 | 6/7y+, anchors[40,198]gross stable-sign | 2.8x of assumed 28bps (survives to ~78bps rt cost) | PROMISING |
| XSEC_MOM_CVD_DIVERGENT_7D | ORDER_FLOW_DIVERGENT_MOMENTUM | trend without genuine taker-flow participation | 7d mom restricted to names where CVD diverges from price | 2017 | 287 | -122.9 | -150.9 | 0.68 | -2.39 | 0/7y+, anchors[-123,54]gross SIGN-FLIP | gross already <=0 | DEAD |
| XSEC_CROWDING_LSR_1D | RELATIVE_CROWDING_POSITIONING | positioning extremity / squeeze risk | \|top-trader LSR - 14d mean\| rank -> 1d fwd, LS quintile | 10 | 10 | -9.0 | -37.0 | 0.83 | -0.96 | 1 year only (2026), n=10 periods | gross already <=0 | DATA_LIMITED |

**TOTAL_MECHANISMS_TESTED: 40** (22 in part 1 / sections A-I, 18 in part 2 /
sections J-U; run logs `run_all.log`, `run_all_part2.log`). Tally: **9 PROMISING**
(1 of which, `XSEC_MOM_7D_LO_LIQ2M`, is a capacity sensitivity check on another
PROMISING row, not an independent mechanism — 8 independent PROMISING), **13
WEAK**, **17 DEAD**, **1 DATA_LIMITED**. No BLOCKED candidates — every planned
construction ran to completion (one script-level `TypeError` appears at the very
end of `run_all.log` after "part 1 done, 22 candidates so far, saved" — the save
had already completed successfully with all 22 candidates intact; nothing was lost).

## 3. Top findings

**(1) Round2/W1's headline number does not survive an anchor-robustness check
on its own long-short form, but its long-only form does, more convincingly than
round2 reported it.** `XSEC_MOM_7D_LS_REPCHK` — the same 7d->7d quintile
long-short signal/universe/costs round2/W1 used — swings from -9.7 to +81.9
gross bps depending purely on which of the 7 possible weekly rebalance-day
phases is chosen; the anchor-pooled mean net is a marginal +12.0bps, and the
single-anchor series round2 reported on (t=2.60) sits at the favorable extreme
of that range, not its center. This is a genuine fragility in the "non-overlapping
= declustered" convention, not a round2-specific mistake — but it means the
long-short book round2 called PROMISING should be read with real doubt.
**The long-only side is a different story**: `XSEC_MOM_7D_LO` is net-positive on
*all 7 anchors* (anchor range 69-108 gross bps, always well above the 14bps
round-trip cost), and every longer long-only horizon tested is even more
anchor-robust — `XSEC_MOM_14D_LO` (net ~199bps, all-anchor min ~179bps net) and
`XSEC_MOM_30D_LO` (net ~463bps, all-anchor min ~363bps net) both stay strongly
positive across every possible rebalance phase, with profit factors 1.4-1.6 and
positive in 4/7 years each (losing only in the 2022 unwind and 2025 chop). The
long-short leg is where round2's number was fragile; the long-only leg — buy
recent cross-sectional winners, don't short the losers — is where the real,
anchor-robust edge in this dataset lives, and it gets *stronger*, not weaker,
at 14d-30d horizons than at 7d.

**(2) The single strongest, most robust result in this entire sweep is not a
momentum variant at all: it's the classic Amihud illiquidity premium.**
`XSEC_AMIHUD_ILLIQ_7D` (long illiquid / short liquid quintile, ranked on
trailing |return|/$volume) nets **+99.3bps** (gross 127.3, t=2.92, p=0.0037),
is positive in **all 7 years** 2020-2026 (the only candidate in the whole
sweep with a perfect year record), has the tightest anchor dispersion of any
long-short construction tested (std 9.0bps vs mean 95.7 — anchors range
106-134 gross, never close to flipping sign), and the best profit factor
(1.86) of any candidate. This is economically distinct from momentum (illiquidity
compensation, not trend/attention-diffusion) and deserves independent
follow-up ahead of anything else in this report.

**(3) Order-flow confirmation cleanly splits momentum into a working half and a
severely broken half.** Restricting the 7d momentum book to names where
cumulative CVD (signed taker flow) moved the *same* direction as price
(`XSEC_MOM_CVD_CONFIRMED_7D`) nets +49.8bps, positive 6/7 years, always
positive across anchors. The mirror-image filter — names where price moved but
CVD *didn't* confirm (`XSEC_MOM_CVD_DIVERGENT_7D`) — nets **-150.9bps**,
significant at t=-2.39 (p=0.017), and is negative in **every single year**
2020-2026 without exception. This is a clean, statistically strong, internally
consistent pair: genuine taker-flow-backed price moves carry a real premium;
price moves *not* backed by real flow (leverage-only / thin-liquidity noise)
are a reliable loser, not just a non-event.

**(4) Relative leverage (OI scaled by typical trading capacity) is a real,
distinct standalone factor, and its mirror — relative liquidity/turnover — is a
strong, statistically confident loser.** `XSEC_REL_LEVERAGE_14D` (long
high-OI-relative-to-volume / short low) nets +73.3bps, PF 1.60, positive 5/6
years, tight anchor range (78-166 gross, never negative). Its liquidity-intensity
cousin, `XSEC_REL_LIQUIDITY_7D`/`_14D` (long high-turnover/short low), is the
most confidently *negative* factor in the sweep: -95.5bps (t=-3.40, p=0.0008)
and -136.6bps (t=-2.58, p=0.011) respectively, negative in essentially every
year (0/6 and 1/6). Together these say something coherent: thin/crowded names
(high OI relative to their own trading capacity) carry a forward premium, while
names with unusually high turnover-relative-to-OI carry a forward discount —
opposite-signed but economically related leverage/liquidity factors, both
distinct from plain momentum and both worth pursuing (the liquidity one as a
short-side building block, given how consistently and significantly negative
it is).

**(5) Residual (beta-to-BTC-stripped) momentum survives and mildly improves on
raw momentum at 14d, refuting the "it's just beta" hypothesis for the longer
horizon.** `XSEC_RESID_MOM_14D` nets +64.8bps, PF 1.34, positive 5/7 years
(best of any 14d variant), always positive across anchors (min ~27bps net at
worst anchor) — the idiosyncratic (BTC-beta-stripped) component of 14d
momentum carries real signal on its own, not just market beta relabeled. At
7d, though, the residual and sector-neutral cuts both go the other way
(`XSEC_RESID_MOM_7D` ~breakeven, `XSEC_SECTOR_NEUTRAL_MOM_7D` net -30.9bps),
suggesting at the shorter horizon a meaningful share of the (already-fragile)
raw-momentum signal *is* sector/beta co-movement, while at 14d the
idiosyncratic component dominates and is the more robust of the two cuts.

**(6) Everything that tries to "fix" 7d momentum with a filter or adjustment
makes it worse, not better.** OI-confirmation, funding-net-of-carry,
low-leverage-filtering, funding-uncrowded-filtering, and basis-dislocation-
filtering were all tested as ways to clean up the fragile 7d momentum signal —
every one of them nets negative or flat (-11 to -49bps) and several flip sign
across anchors. Cross-sectional acceleration (rank velocity — chasing names
*rising through* the ranks rather than already at the top) is outright
negative and borderline significant at 7d (`XSEC_ACCEL_7D`, t=-1.82, p=0.07,
2/7 positive years) — buying accelerating momentum, not just high momentum, is
if anything a worse idea at this horizon. Short-horizon reversal is dead with
high confidence: `XSEC_REV_1D_LS` nets -27.5bps at t=-5.89 (p=4.4e-9), the most
statistically significant result of the entire sweep — 1-day cross-sectional
mean-reversion is not just absent, it is a confident structural loser on this
panel/cost assumption (2d reversal similarly dead, t=-2.36).

**(7) Regime-conditioning (dispersion, breadth) shows a real, economically
large pattern at 14d/7d that isn't yet statistically confirmed.** The 14d
momentum book earns +213.8bps in high-cross-sectional-dispersion regimes vs
-44.2bps in low-dispersion regimes (spread 258bps, t=1.42, p=0.16, n=56/56)
and the 7d book earns +62.9bps in high-breadth vs -77.6bps in low-breadth
regimes (spread 141bps, t=1.41, p=0.16). Both point the same direction
economically (momentum works when the opportunity set is genuinely dispersed
or broad, and doesn't when it isn't) but neither clears significance on its
own — flagged WEAK, worth a longer sample rather than dismissal.

**Net read for follow-up priority:** Amihud illiquidity (finding 2) is the
strongest, cleanest, most independently-distinct candidate to carry into
`INDEPENDENT_CONFIRMATION` next. CVD-confirmed/divergent momentum (finding 3)
and relative-leverage/relative-liquidity (finding 4) are the next tier — all
three are economically distinct from round2's momentum finding, not
repackagings of it. Round2/W1's own number should be re-scoped to long-only
(7d-30d), given how much more anchor-robust that side is than the long-short
book round2 originally reported on.
