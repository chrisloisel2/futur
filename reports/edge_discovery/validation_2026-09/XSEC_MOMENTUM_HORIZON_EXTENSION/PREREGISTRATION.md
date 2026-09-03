# PREREGISTRATION — XSEC_MOMENTUM_HORIZON_EXTENSION (14D_LO / 30D_LO / 14D_LS)

**Worker:** V3, Alpha Validation Factory wave 2. **Written:** 2026-09-03 ~10:55 UTC, BEFORE any
return figure was computed (only schemas, resource checks and the production universe code were
inspected at this point).

**Claim under test** (`reports/edge_discovery/alpha_hunt_2026-09-01_round3/w2_cross_sectional/REPORT.md`,
rows XSEC_MOM_14D_LO / XSEC_MOM_30D_LO / XSEC_MOM_14D_LS; discovery scripts/evidence NOT opened):
- XSEC_MOM_14D_LO: 14d momentum, top-quintile long-only, net +199.3 bps / 14d rebalance, PF 1.41,
  t 1.52, N_indep 167, 4/7 years, anchors [193, 261] gross.
- XSEC_MOM_30D_LO: 30d momentum, long-only monthly, net +462.8, PF 1.61, t 1.41, N_indep 78, 4/7 years.
- XSEC_MOM_14D_LS: 14d momentum, quintile long-short, net +59.5 (28 bps cost), PF 1.31, t 0.78.

**Independence protocol.** Everything is rebuilt from `data_v2/normalized/perp_ohlcv` (5m bars,
DuckDB projection of `timestamp, close, quote_asset_volume` only) and
`data/listings_backfill/binance/listings_calendar.parquet`. Declared read-only reuse:
`src/institutional/engines/cross_sectional_momentum_live_v2/universe.py` was READ for the PIT
eligibility convention (onboard_ts + 30d age, causal 30d rolling-median liquidity, full window
required); `build_pit_eligibility_log()` will be CALLED on my own daily panel for >= 1 rebalance
date and compared line by line against my own eligibility recomputation. `signal.py` trailing
formulas were read (not imported into the validation path).

## 1. Choice of PRIMARY_SPEC (fixed before any result) and why

PRIMARY = **14-day formation / 14-day holding, top-quintile LONG-ONLY**, judged on its
**excess over the equal-weighted PIT-eligible universe** (arm A − arm B).

Why 14D_LO and not 30D_LO or 14D_LS: (i) it has the largest declustered N of the three (167 vs 78
rebalances) and the smallest calendar floor (182 d vs 365 d); (ii) it is the horizon shared with
the sibling candidate XSEC_RESIDUAL_MOMENTUM_14D, so the same panel/grid serves both; (iii) per
project SHORT policy the LONG leg is the deliverable — 14D_LS is a variant whose LONG leg is the
primary itself. 30D_LO and 14D_LS are preregistered perturbations/variants of the same
construction, not separate searches.

Why the statistic is the EXCESS and not the raw long-only return: round-4 briefing §1.3 —
"compare arms, never A > 0". A long-only alt-crypto book 2020-2026 carries a large unconditional
drift; the discovery's t=1.52 is measured against zero. The mechanism (cross-sectional
continuation) is only evidenced if top-quintile names beat the eligible universe they were
selected from. Raw (vs-zero) numbers are ALSO reported for comparability with the claim, but the
verdict is on the excess.

## 2. PRIMARY_SPEC — frozen

| Item | Rule |
|---|---|
| Panel | Daily bars from 5m perp bars, UTC days: `close_d` = close of last 5m bar of the UTC day, `dv_d` = Σ quote_asset_volume of the day, `nbar_d` = 5m bar count. Reindexed on a gap-free daily calendar per symbol (missing day = NaN, never filled). |
| Universe at rebalance d (PIT) | (a) `onboard_ts` from listings_calendar (fallback: first real close date, logged); (b) `d >= onboard_ts + 30 d`; (c) causal rolling **median** of `dv` over the 30 days ending at d inclusive, full 30-day window required, **>= $1,000,000** (discovery's primary floor; $2M live-V2 floor is a perturbation); (d) `close_d` and `close_{d-14}` both present (formation computable); (e) at least one close in `(d, d+14]` (exit possible). |
| Minimum breadth | A rebalance is skipped (counted, reported) if `n_eligible < 20`. |
| Signal | `mom14(d) = close_d / close_{d-14} − 1` (calendar days). |
| Construction | Rank eligible names by mom14 descending; LONG top `ceil(0.20 × n_eligible)` names, equal weight, entry at `close_d`, exit at `close_{d+14}`. If `close_{d+14}` is missing (delisting / gap) the exit is the **last available close in (d, d+14]** (forced exit at last print — a delisted name is NOT dropped). |
| Arm B (benchmark) | Equal-weighted holding of the full eligible universe over the same window, same exit rule. |
| Winsorization | Per period, forward simple returns winsorized at 1%/99% across the FULL eligible cross-section (same as discovery/AMIHUD validation). Conservative for a long-only top quintile (caps pump names). Unwinsorized version = perturbation. |
| Grid | Non-overlapping, every 14 calendar days. Anchor 0 = the first calendar date with `n_eligible >= 20`; all 14 phases reported as perturbation P6. |
| Costs | Project convention: `net14 = gross − 14` (7 bps one-way × 2 legs, full turnover), `net28 = gross − 28`. ALSO reported: cost on REAL turnover, `cost_real = 7 bps × Σ_i |w_new,i − w_drift,i|` (w_drift = previous equal weights drifted by realised returns) — with explicit round-trips/year (26.1 rebalances/yr at 14 d, 12.2 at 30 d). |
| Primary statistic | `excess_gross = (R_top − R_universe) × 1e4`; `excess_net14 = excess_gross − 14`; `excess_net28 = excess_gross − 28`. Raw `R_top` (vs zero) net14/net28 reported alongside. |
| Sample | 2020-01 → 2026-08 (last complete holding window). Year-by-year mandatory; 2025 reported separately; ex-2021 mandatory. |

## 3. Preregistered perturbations (≤ 8, robustness tests — NOT a search grid)

| # | Perturbation | Purpose |
|---|---|---|
| P1 | **30D_LO**: formation 30 d, holding 30 d, same construction (claimed XSEC_MOM_30D_LO) | horizon variant of the claim |
| P2 | **14D_LS**: LONG top quintile / SHORT bottom quintile, cost 28 bps (claimed XSEC_MOM_14D_LS); its LONG leg = PRIMARY, reported separately by construction | claimed LS variant; SHORT policy |
| P3 | Liquidity floor $2M (live V2 engine floor) | capacity / cohort sensitivity |
| P4 | Exclude 2021 | regime concentration (mandatory) |
| P5 | Cost +50 % (21 / 42 bps) | cost fragility |
| P6 | All 14 rebalance phases (anchors 0..13), pooled mean/std/min/max, count of positive anchors | phase robustness (the discovery's own headline check) |
| P7 | No winsorization | tail-handling sensitivity |
| P8 | 1-day execution lag: signal at `close_d`, entry `close_{d+1}`, exit `close_{d+15}` | realistic execution / short-term reversal contamination |

Reported for every spec: gross, net14, net28, cost_real, PF, n_raw, n_L1, n_L2, n_L3, t_L3,
bootstrap CI95, year_by_year, ex_best_year, worst_episode, max_drawdown (cumulative bps).

## 4. Declustering units (fixed)

- **n_raw** = name-level long-leg positions (symbol × rebalance) at anchor 0.
- **L1** (same symbol / horizon) = n_raw (each name held once per 14 d window; no same-symbol duplication within a window). Also reported: `n_anchor_pooled` = 14 anchors × periods (the discovery's N_raw definition).
- **L2** (rebalance date, all symbols) = number of non-overlapping rebalance periods (anchor 0).
- **L3** (macro episode) = **calendar month** of the rebalance date (≈ 2 periods/month at 14 d; ≈ 1 at 30 d — for 30D, L3 ≈ L2 and this is stated). Inference: cluster-robust (Liang-Zeger) SE on L3 clusters for `t_stat_declustered`; **block bootstrap with month blocks** (10,000 resamples) for CI95 and N_required. Supplementary L3' = BTC realised-vol regime episode (runs of 30d BTC vol above/below its causal trailing-365d median) reported for the PRIMARY as a diagnostic.

## 5. Mandatory overlap checks (same factor?)

- Cross-sectional Spearman rank correlation per rebalance date, averaged (mean ± std): mom14 vs mom7 (`close_d/close_{d-7}−1`), mom14 vs Amihud `illiq_avg_30d` (mean |daily return| / dv over the 30 days ending d−1, as in the frozen AMIHUD_ILLIQUIDITY_PREMIUM_V1 spec), mom14 vs resid_mom14 (sibling candidate).
- Portfolio-return correlation on the same 14-day windows: PRIMARY excess vs (a) 7d→7d top-quintile LO excess and 7d LS (two consecutive 7-day periods compounded to the 14-day window), (b) Amihud LS (W=30, H=7, $1M) compounded to 14 d, (c) residual-momentum 14D LS. Leg-overlap share (Jaccard) between the PRIMARY long leg and the 7d LO leg / the Amihud long leg on common dates.

## 6. Capacity

Trailing 30d median dollar volume of long-leg names (p05 / median) and implied participation of a
$300k book at equal weight; same for the bottom-quintile (short) leg of P2.

## 7. Success criteria for `VALIDATED_FOR_FORWARD` (all required, PRIMARY_SPEC only)

1. `excess_net14 > 0` with `t_L3 >= 1.645` (one-sided 5 %) AND month-block-bootstrap 5th
   percentile of the mean `excess_net14 > 0`.
2. `excess_net28 > 0` (stress) — else at best `COST_FRAGILE`.
3. `>= 4/7` calendar years with positive `excess_net14` AND ex-2021 `excess_net14 > 0` — else `REGIME_DEPENDENT`.
4. Anchor robustness (P6): pooled-mean gross excess > 0 and >= 10/14 anchors with positive gross excess.
5. Raw long-only `net14 > 0` (a product must also beat zero).

Failure of PRIMARY on (1) → `REJECTED` (or `NEEDS_MORE_RESEARCH` only if `1.0 <= t_L3 < 1.645`
with 2-5 all passing). **No parameter is changed after seeing results.** Perturbations P1/P2 get
their own verdict lines with the same criteria but are never used to rescue the PRIMARY.

`confirmable_in_horizon` = `eta_conservative < 1095 d`, with `eta_conservative = max(N_required ×
14 d (30 d for P1) / conservative_rate, 182 d (365 d for P1))`, N_required from month-block
bootstrap at one-sided α = 5 %, power 80 %, mean haircut 50 %.

## 8. Resource discipline

`SET memory_limit='1200MB'; SET threads=2;` `SET TimeZone='UTC'`. Only intermediate: one daily
panel parquet (~750k rows, < 20 MB) in scratch `V3_XSEC_HORIZON/`. `df -h /` before writing it;
no intermediate written if free < 23 GB.
