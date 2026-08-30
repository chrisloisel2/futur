# W1 — Cross-Sectional Alpha Hunt (Alpha Hunt 2026-08-30, round 2)

**Scope:** genuinely new cross-sectional / basket-level economic mechanisms across the real PIT
universe (`data_v2/normalized`, 312 symbols, 2020-2026), read-only. Round 1 (`alpha_hunt_2026-08-29`,
esp. W4's `REPORT.md`) already tested and killed: A13-H-E1 (contemporaneous residual-RV percentile
reversion, single-name vs leave-one-out basket) and A12 (BTC/ETH residual-innovation →
follower catch-up, intraday horizons). Both are **DEAD** and not retested here. This round tries
different mechanisms, different signal types (raw return, funding, OI, flow, correlation,
dispersion — not just residual-return z-scores), and different horizons (daily-to-monthly, not
intraday-to-72h).

**Data used:** `data_v2/normalized/perp_ohlcv` (5m bars, resampled to daily via duckdb) +
`data_v2/normalized/event_feature_panel` (5m bars: funding, basis, OI, signed_volume/CVD,
residual_std_30d — resampled to daily via duckdb). Two small daily panels
(`daily_ohlcv.parquet` 17MB, `daily_features.parquet`) were built once with duckdb (out-of-core,
~20s each) from the 83GB source and then all further analysis ran in-memory on the resulting
~366k-row daily panel — no giant panel materialized to disk, nothing written outside the
scratchpad and this report's `evidence/` folder. Universe restricted to rows with
**trailing-30d median daily quote-volume ≥ $1M** ("liquid" cohort) unless noted, to keep results
economically meaningful and roughly capacity-realistic; a few checks explicitly split by
liquidity tier. Symbol-date grid is gap-aware (reindexed to each symbol's own continuous calendar
so a delisting/relisting gap never gets silently read as a 1-day return).

**Methodology caveat that applies to every mechanism below:** signals are built from data known at
the close of day *t* (funding/basis/OI/flow are daily aggregates of causal 5m fields); forward
returns run close[t] → close[t+H]. This is a daily-granularity approximation — it does not model
the panel's own `research_available_at` intraday lag, unlike W4's intraday work. Acceptable for a
daily-or-slower discovery pass, flagged so nobody mistakes this for tick-level PIT rigor.

**Cost model:** taker 5bps one-way (project convention, `data_v2/events/costs.py`). A market-neutral
quintile-spread basket (long top quintile, short bottom quintile, full rebalance) costs **20bps**
per round trip (4 legs: long entry, short entry, long exit, short exit) — applied as a flat floor
assuming **100% turnover every rebalance** (conservative; measured actual week-to-week turnover on
the best candidate is ~75%, so realistic net is somewhat better than reported). A single-name
long-only trade costs 10bps round trip (2 legs).

**Critical methodological fix made mid-session:** the first pass of several "regime conditioning"
tests (breadth, dispersion, correlation, flow lead-follow, skew) compared a conditional mean
forward return to **zero**. Because this 2020-2026 crypto sample has strong unconditional upward
drift, several of those looked "significant" purely from the drift, not from any real conditioning
effect. All were **redone** comparing the high-decile mean to the low-decile mean (Welch t-test) and
each decile's *excess* over the full-sample unconditional mean — this is what's reported below.
This matters: it flips several early "looks interesting" reads into honest DEAD verdicts.

---

## Ranked table

| rank | mechanism | dataset | horizon | events (n) | gross bps | est. net bps | t / p | stability | capacity | status |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | **M1 — cross-sectional momentum**, raw 7d trailing return → 7d fwd, quintile long-short, liq≥$1M | `perp_ohlcv` daily | 7d→7d | 335 rebal. periods (~29 names/quintile) | +109.1 | **+89.1** (full-turnover floor; real turnover ~75%/wk so likely better) | t=2.60, p=0.0098 | 6/7 years positive (2022 flat +7.5bps, 2023 negative -34.2bps); ex-2020 t drops to 1.84 (p=0.067) | works in mid/liquid tercile (t=2.9/2.31), **not** in illiquid tercile (t=0.19); majors-only alone is insignificant (t=0.72) — edge lives in the broad liquid-altcoin set, not majors or junk | **PROMISING, NEEDS_FULL_VALIDATION** |
| 2 | M2 — Sharpe-scaled momentum (tret/tvol), 14d→7d | same | 14d→7d | 334 | +110.7 | +90.7 | t=2.51, p=0.013 | 6/7 years positive | same universe as M1 | **PROMISING** — largely the same effect as #1 restated with a risk scale, not fully independent confirmation |
| 3 | M12 — extreme up-move + volume-spike, **asymmetric** continuation (long only) | `perp_ohlcv` daily, |ret| top 2.5% AND vol≥3× 30d median | 3d, 5d | 5,348 (3d) / 5,345 (5d) events, ~2.6 names/day | +114.7 (3d) / +178.8 (5d) raw; **+98.3 / +150.2 excess vs unconditional baseline** | ~+90 (3d) / ~+140 (5d) after 10bps single-name cost | t=2.23/2.38, p=0.026/0.017 (drift-adjusted) | **sign flips hard in 2022**: excess -142.9bps(3d)/-60.1bps(5d) that year vs positive every other year; down-tail side shows **no** effect at all (t=0.19/-0.43) — asymmetric, bear-regime-fragile | good — median qualifying-name daily $ volume ≈$260M, frequency ~2.6/day | **PROMISING but regime-fragile**, long-only (naturally sidesteps SHORT_REJECTED) |
| 4 | M15 — funding-crowding tail, 99th-pctile funding → continuation (long side) | `event_feature_panel` daily | 1d, 7d | 8,375 (1d) / 8,316 (7d) | +48.7 (1d) / +105.9 (7d) raw; **+43.0 / +63.4 excess** | ~+38 (1d) after 10bps; ~+53 (7d) | t=4.34 p<0.0001 (1d, very strong); t=2.46 p=0.014 (7d) | short-crowding side (1st pctile) much weaker: t=1.70(1d, marginal)/0.63(7d, n.s.) — asymmetric | frequency ~3.5/day | **PROMISING but likely redundant with #1** — crowded-long names are largely the same "recently hot" names momentum already flags; not confirmed as a distinct risk factor |
| 5 | M16 — OI-collapse → bounce, bottom-1% OI growth (14d) → 7d fwd | `event_feature_panel` daily | 14d lookback → 7d | 2,938 | +254.3 raw, **+267.1 excess** | ~+247 after 20bps | t=2.78, p=0.0054 | **does not replicate** at 30d lookback/14d horizon (t=1.35, n.s.) — one parameterization only | thin — top/bottom 1% of an already-liquid universe | **WEAK/NEEDS_FULL_VALIDATION** — found via limited grid (2 combos tried), flagged not confirmed |
| 6 | M4 — basis cross-sectional carry, long cheap-basis / short rich-basis quintile | `event_feature_panel` daily | 1d, 7d | 2,350(1d)/335(7d) | +13.3(1d)/+39.4(7d) | -6.7(1d)/+19.4(7d) | t=4.12(1d, but net negative)/1.73(7d, marginal) | 7/7 years positive at 1d but **decayed to negative in 2025 (-8.2bps) and 2026 (-3.1bps)** at 7d | wide (whole liquid universe) | **WEAK** — recent decay, and the only horizon that clears costs (7d) is only marginally significant |
| 7 | M7/M7b — OI growth quintile spread (continuation vs reversal) | `event_feature_panel` daily | 14d→7d, 30d→14d | 240/117 | continuation: -46.9/-69.2 (wrong sign, DEAD); reversal (fade high OI growth): +45.6/+79.9 | reversal net: +25.6(7d)/+59.9(14d) | t=1.68/1.03 (reversal, both n.s. at 5%) | 2/6 and 3/5 positive years for continuation (weak, inconsistent) | wide | **WEAK** — reversal direction directionally consistent with M16's stronger tail finding but not itself significant |
| 8 | M17 — correlation-cluster peer-momentum spillover (K=6 KMeans on trailing corr, exploratory) | `perp_ohlcv` daily | 7d→7d | 339 | +73.3 | +53.3 | t=1.90, p=0.059 | **unstable**: 4/7 years positive, 2020/2022/2026 negative, no clean monotonic effect once conditioned on the asset's own momentum quintile | n/a | **WEAK/inconclusive** — clustering itself uses full-sample correlation (mild look-ahead in group membership only), flagged as exploratory |
| 9 | M14 — cross-sectional skew of daily-return distribution → fwd BTC 5d | `perp_ohlcv` daily | 5d | 235/235 (deciles) | high-skew excess -55.9bps vs low-skew excess +31.8bps | n/a (not a tradeable spread as tested) | Welch t=-1.57, p=0.116 | directionally consistent with equity "MAX effect" (lottery-preference) but not significant | — | **WEAK** — right sign, not enough power |
| 10 | M6 — betting-against-beta (low-β-to-BTC long / high-β short), rolling 60d beta | `perp_ohlcv` daily | 7d, 14d | 332/165 | +41.9/+91.9 | +21.9/+71.9 | t=0.91/0.73 (n.s.) | 4/7 positive years | wide | **WEAK/NO_EDGE** |
| 11 | M3 — funding-rate cross-sectional carry (smooth quintile, persistent basket) | `event_feature_panel` daily | 1d, 7d | 2,345(1d)/334(7d) | +11.3(1d)/+30.1(7d) | **-8.7(1d, net negative)**/+10.1(7d) | t=3.0(1d)/0.96(7d, n.s.) | 6/7 and 4/7 positive years | wide | **WEAK/DEAD at daily rebalance** — gross too small to survive daily turnover cost; weekly rebalance not significant |
| 12 | M5 — idiosyncratic-vol premium (low-residual_std long / high short) | `event_feature_panel` daily | 7d, 14d | 324/161 | **-44.2/-188.5** (wrong sign vs equity low-vol anomaly — high-idio-vol mildly outperformed) | n/a, not significant | t=-1.03/-1.31 (n.s.) | 3/7 positive years | wide | **DEAD** — sign is reversed vs the equity analog (consistent with a "lottery preference" story in a retail-heavy, speculative asset class) but too weak to claim |
| 13 | M9 — cross-sectional return-dispersion regime → fwd BTC 5d | `perp_ohlcv` daily | 5d | 239/239 (deciles) | uncond 67.1bps; lo-disp excess +20.5, hi-disp excess -54.1 | — | Welch t=-1.02, p=0.31 | — | — | **DEAD** (rebaselined) — M9b (dispersion→forward realized dispersion, i.e. vol clustering) is trivially/mechanically significant (t≈32-41) but **not a directional edge**, diagnostic only |
| 14 | M10 — average correlation-to-BTC regime → fwd BTC 5d | `perp_ohlcv` daily | 5d | 239/239 | Welch spread +52.7bps | — | Welch t=0.71, p=0.48 | — | — | **DEAD** (rebaselined) — M10b (corr regime → fwd dispersion) is mechanical/tautological (corr and dispersion are definitionally related), not a tradeable edge |
| 15 | M11 — leader flow-imbalance (BTC/ETH signed-volume z-score) → follower basket, daily/weekly | `event_feature_panel` daily | 3d/7d/14d | 240-239 per leg | Welch spread -12.8 to -133.6bps, all wrong-signed or noise | — | Welch |t|<0.8, all p>0.44 | — | thin edge either way | **DEAD** (rebaselined) — a genuinely different signal (raw order flow, not residual-return z-score) from A12, same conclusion: no daily/weekly cross-sectional propagation from leaders |
| 16 | M8 — market-breadth divergence (% names with positive 5d return) → fwd BTC/mkt 5d | `perp_ohlcv` daily | 5d | 240/239 | Welch spread -23.6(BTC)/-81.2(mkt) | — | Welch t=-0.33/-0.87, p=0.74/0.38 | — | — | **DEAD** (rebaselined) — first-pass "vs zero" version looked interesting purely from unconditional drift |

*(19 mechanism-parameterizations from M1-M7 use the strict long-short quintile-spread backtest
framework; M8-M17 use decile-conditioning or event-tail tests as appropriate to the hypothesis —
full detail incl. every parameterization tried, not just winners, in
`evidence/all_mechanism_results.json` and the two yearly-breakdown CSVs.)*

---

## Detail on the two most promising candidates

### #1 — Cross-sectional momentum (M1/M2/M13)

- **Hypothesis:** names that outperformed the cross-section over the last week keep outperforming
  over the next week — slow information diffusion / trend-following flow in a still attention-
  constrained, partially retail-driven asset class. Classic equity/managed-futures momentum,
  applied here cross-sectionally at a **weekly** horizon using **raw** (not residual) returns —
  distinct from A13-H (sealed, single-name mean-reversion up to 72h) and A12 (leader→follower
  propagation, intraday) in mechanism, signal construction, and horizon.
- **Signal:** `tret_7d = close[t]/close[t-7]-1`, causal.
- **Construction:** liq≥$1M universe, quintile by `tret_7d`, long top quintile / short bottom
  quintile, equal-weight, non-overlapping 7-day rebalance (335 independent periods, avoids the
  overlapping-window autocorrelation problem other reports flagged).
- **Decile pattern (bps, full sample):** q0=8.6, q1=24.2, q2=16.7, q3=37.1, **q4=106.6** — the
  effect is concentrated almost entirely in top-decile continuation ("winners keep winning"), not
  bottom-decile reversal. This is good news for deployability: a **long-only top-quintile overlay**
  captures most of the edge and needs no short book (also sidesteps SHORT_REJECTED entirely, since
  nothing here requires shorting).
- **Robustness checks run:** liquidity tier split (works mid/liquid, not illiquid — capacity-
  friendly finding, not a junk-microcap artifact), majors-only (insignificant, t=0.72 — this is an
  *altcoin breadth* effect, not a BTC/ETH phenomenon), ex-2020 (t drops from 2.60 to 1.84, p=0.067
  — still directionally there but weaker without the earliest, thinnest-universe year), horizon
  decay (L14/H14 t=1.31, L30/H30 t=0.59 — effect fades past ~1-2 weeks), turnover check (~75%
  names replaced week to week, so the 100%-turnover cost floor used above is conservative — real
  net likely somewhat better than the +89bps floor reported).
- **What would kill this:** it not surviving an actual live-shadow test, or the 2022/2023
  weak/negative years turning out to be the "true" regime going forward rather than the outlier.
- **Verdict: PROMISING, NEEDS_FULL_VALIDATION.** This is the strongest, most economically
  sensible, most robust-under-perturbation finding of this session. Recommend: a proper
  out-of-sample validation on 2026-H2+ data as it accrues, and a long-only paper-trading probe
  given the decile pattern already suggests long-only captures most of it.

### #3 — Extreme up-move + volume-spike continuation (M12)

- **Hypothesis:** a genuine, high-conviction repricing (real news/catalyst) accompanied by unusual
  volume is only partially priced in on the trigger day; slow-moving/attention-constrained flow
  keeps chasing it for a few more days. Payer: liquidity providers / early sellers who under-
  reacted; distinct from M1 in that it conditions on a **rare tail** (top 2.5% move) **jointly with
  a volume anomaly** (≥3× 30-day median $ volume that day), not on a smooth trailing-return rank.
- **Signal:** same-day |ret| in top/bottom 2.5% cross-sectionally AND `quote_volume/liq_30d ≥ 3`.
- **Result is sharply asymmetric:** up-tail events show a real, drift-adjusted excess forward
  return (+98.3bps@3d, +150.2bps@5d, both p<0.03); down-tail events show **no** excess at all
  (p>0.65 both horizons) — so this is not a general "extreme move + volume continues" law, it's
  specifically an **upside** phenomenon (consistent with pump/momentum-chasing psychology being
  asymmetric in a still long-biased, retail-heavy market).
- **Critical weakness: 2022 flips the sign hard** (-142.9bps excess @3d, -60.1bps @5d — the only
  negative year by a wide margin; every other year 2020-2026 is positive, several strongly so). A
  bear-regime filter or vol-regime conditioning would likely be needed before this is deployable;
  as tested it is a **bull/neutral-regime effect**, not an all-weather one.
- **Verdict: PROMISING but regime-fragile.** Naturally long-only (no SHORT_REJECTED conflict).
  Good frequency/capacity (median qualifying name trades ~$260M/day). Needs a regime filter and
  genuine OOS test before it's more than a discovery-stage lead.

---

## Mechanisms tried and dropped quickly (brief)

- **M5 idiosyncratic-vol premium** — tested both directions of the equity low-vol anomaly; sign
  came out mildly reversed (high-idio-vol slightly outperformed) but t<1.4 either way. DEAD.
- **M6 betting-against-beta** — positive but t<1. WEAK/NO_EDGE.
- **M3 funding carry (smooth, persistent basket)** — real at daily rebalance (t=3.0) but the gross
  edge (11bps) doesn't clear the 20bps daily-rebalance cost floor; weekly rebalance loses
  significance. WEAK/DEAD as a standalone carry harvester.
- **M8 breadth divergence, M9 dispersion regime, M10 correlation regime, M11 flow lead-follow,
  M14 cross-sectional skew** — all looked mildly interesting on a naive "different from zero" read
  and all **died** once properly compared to the unconditional baseline via Welch t-test
  (all |t|<1.6, most |t|<1). This is the main "bug/methodology" finding of the session — see
  above.
- **M7 OI-growth quintile spread** — continuation direction is wrong-signed (crowding doesn't
  chase); the fade/reversal direction is directionally consistent with M16 but not itself
  significant. WEAK.
- **M17 cluster-peer momentum spillover** — marginal (p=0.059), unstable across years, and the
  own-momentum-conditioned table doesn't show a clean incremental effect. WEAK/inconclusive; this
  is the session's one attempt at a genuine "sector/cluster" mechanism (data-driven KMeans
  clustering on trailing return correlation, since no clean sector labels exist in this dataset) —
  flagged as exploratory because cluster *membership* uses full-sample correlation (a mild
  look-ahead in the grouping, not in the returns used to predict).
- **M4 basis carry** — real-looking at first (7/7 years positive at 1d) but the only cost-
  surviving horizon (7d) is only marginal (t=1.73) and has visibly decayed to negative in
  2025-2026. WEAK, possibly arbitraged away — consistent with round-1's finding that the calendar
  basis edge was overstated before proper decluster.

## Data pitfalls encountered / avoided

- Confirmed `data_v2/normalized/perp_ohlcv`'s `taker_buy_*` columns are **real** (ratio to total
  quote volume varies day to day, mean≈0.497 but with real dispersion, sd≈0.114) — unlike the
  `data/enriched/*_1h_enriched.parquet` fake placeholder flagged in the mission brief. Not
  ultimately used as a standalone signal this round (M11 used `signed_volume`/CVD from
  `event_feature_panel` instead, which is the documented real agg-trades-derived flow field).
- Confirmed via the same gap-aware reindexing approach used by round-1 W4 that PIT survivorship is
  respected (delisted/newly-listed symbols only contribute rows inside their real trading window;
  no forward/backward fill of prices across a real gap).
- All duckdb resampling was done as a single grouped aggregation query straight off the 83GB
  source with column/predicate pushdown — no intermediate giant panel written; the two output
  parquet files are 17MB and comparable, both left only in the session scratchpad, not the repo.

## What wasn't attempted (explicitly out of time/scope this round)

- A rigorous, non-look-ahead **rolling** sector/cluster construction (M17 used a static full-
  sample clustering) — would need periodic re-clustering on trailing-only data.
- Formal Newey-West / HAC standard errors for the handful of tests that do have some residual
  autocorrelation potential (M12/M15/M16 event-based tests draw overlapping-name events across
  nearby days); non-overlapping-period t-tests were used wherever the design allowed it (M1-M7,
  M13, M17), and the event-tail tests are flagged as "naive t-stat" the same way round-1's reports
  flag theirs.
- A genuine out-of-sample holdout split (fit-then-test on a strictly later, untouched period) for
  the two PROMISING candidates — this was a single-pass discovery grid across the full history;
  per the mission's own instructions, flagged as "found via limited grid, not yet OOS-confirmed."
