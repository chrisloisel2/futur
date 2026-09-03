# Independent Validation — GLOBAL_ACCOUNT_LSR_FADE

**Validator**: independent worker, Alpha Validation Factory, 2026-09-02
**Claim under test** (`reports/edge_discovery/alpha_hunt_2026-09-01_round3/w3_relative_value/REPORT.md`,
row `D-GLOBAL_LSR-fade-7D`): the long/short ratio of **global accounts** (retail, a stock-of-position
measure) predicts a **mean-reversion fade** -- crowded-long retail names underperform, crowded-short
names outperform. Reported: net +51.5bps, t=2.05, N_indep=239 (7D rebalance grid, multi-year
backfill from `data/derivatives_backfill/binance_vision_metrics/`).

**This validation**: independent reimplementation from the economic definition, using only
`data/positioning/` + `data/derivatives_raw` mark-price ticks, as mandated by the mission brief.
Original discovery code/scripts were not read. `src/institutional/live_alpha_lab/` and
`configs/live_alpha_registry.yaml` were not touched; `WHALE_LSR_SCREEN_V1`'s spec in the registry
was read only for context on how this project treats positioning-data provenance/thresholds (it
uses `top_position` LSR, a related but distinct feature from `global_account` LSR used here).

This report shares its data pipeline and methodology with the companion validation of
`POSITIONING_TAKER_FLOW` (same panel, same universe, same window, same causal/PIT handling), run
as an economically separate test per the mission brief. Numbers here were computed independently
per-candidate; only the shared panel-construction code is common.

## 0. Critical data-source finding (read this first)

`data/positioning/` is **not** the same dataset the original discovery used. It is a live-collected
archive (`scripts/archive_binance_positioning.py`, `src/institutional/data/positioning_archiver.py`)
of the Binance fapi `/futures/data/*Ratio` endpoints, which **only retain 30 days of history on the
exchange side**. The archiver has been running since **2026-07-16**, so as of today
(2026-09-02) `data/positioning/` covers **~48 calendar days / ~45 usable days after burn-in**, one
single market regime, for all 47 symbols present. The original discovery instead used
`data/derivatives_backfill/binance_vision_metrics/*_metrics_5m.parquet` (2020-09-01 -> 2026-08-31,
verified directly: `BTCUSDT_metrics_5m.parquet` spans 630,370 5m rows over ~6 years), which is why
it could report 5-7 years of stability (5/6 years positive) and N_indep=239.

**Consequence**: this is a fundamentally shorter-sample, single-regime, out-of-sample-in-time
reimplementation, not a replication of the original multi-year backtest. Evidence floors and
N_required/ETA below are computed honestly against what `data/positioning/` can actually support,
not rescued by claiming a longer history the mandated data source doesn't have.

**Second finding, directly relevant to PIT**: `positioning_archiver.py`'s own docstring states the
Vision `metrics` bulk dumps the *original* discovery relied on -- same ratios, same 5m granularity --
carry a **documented J-2 (2-day) publication lag** ("Les dumps Vision `metrics` couvrent les mêmes
ratios à 5 min mais avec un retard J-2"). The W3 report's stated PIT discipline applies a uniform
1-day entry lag to every feature. If the underlying Vision-metrics data for global-account LSR is
really not published until D+2, a 1-day entry lag is **insufficient by construction** -- the
original `D-GLOBAL_LSR-fade-7D` result may have used data one day earlier than it was actually
available. My reimplementation uses the live fapi-fetched `data/positioning/` (not the Vision
dumps), which does not have this specific J-2 lag; I apply my own conservative causal availability
buffer instead (S2).

## 1. Reimplementation methodology

**Universe**: 47 symbols = every `{SYM}_global_account.parquet` present in `data/positioning/`,
derived by an independent glob (not copied from `configs/whale_lsr_screen_universe.yaml`, read only
for context).

**Signal**: hourly bars built from the raw 5-minute `global_account` file. `global_log_ratio =
log(longShortRatio)`, using the **last** reading within each hour (a stock/level variable, not a
flow -- summing would be wrong; a point-in-time snapshot is the correct hourly representative). An
hour is only kept if >=8 of the <=12 possible 5m readings are present, else NaN.

**Causal z-score** (own-history, strictly PIT): identical construction to the companion report --
`z_t = (x_t - mean_{t-window..t-1}) / std_{t-window..t-1}`, baseline computed via
`shift(1).rolling(72h, min_periods=60)`, current bar excluded from its own baseline, ~2.5-day
burn-in.

**Causal availability lag**: same as companion report -- 15-minute conservative buffer (PRIMARY_SPEC,
3x the 5-minute bucket, matching this repo's `marks.py` "marge x3" convention), 30-minute buffer as
stress perturbation. No `recv_time` column exists in `data/positioning/*.parquet` to measure true
API-publication lag directly.

**Entry/exit fills**: entry at the first raw mark-price tick at-or-after `entry_signal_ts +
lag_buffer`; exit at the first tick at-or-after `entry_exec_ts + horizon`. Episodes with no future
tick available (tail of sample) are dropped, not fabricated.

**Direction (GLOBAL_ACCOUNT_LSR_FADE claim)**: `direction = -sign(z)` -- short when retail
long/short ratio is extremely high (crowded long, fade), long when extremely low (crowded short,
fade of the short crowd / squeeze thesis).

**Costs**: 14bps round trip baseline (matches source convention), applied once per episode.

**Declustering** (mandatory, S4 of the brief): identical construction to the companion report --
contiguous same-sign `|z|>=threshold` runs merged per symbol, plus a 24h cooldown. Because
global-account LSR is a **slow-moving stock variable** (retail positioning shifts gradually), its
causal z-score has materially fatter tails than a mean-reverting flow variable even after 3-day
rolling standardization: unconditionally, `|z|>=2.0` fires on 16.7% of hourly readings (vs 5.09%
for taker flow at the same threshold) -- i.e. genuine multi-hour/multi-day persistence in "extreme"
readings, exactly the concern the brief flags ("positioning extremes plausibly persist for multiple
consecutive readings"). The run-based decluster is therefore doing real work here, not a formality.

## 2. PRIMARY_SPEC and perturbations

Threshold `|z|>=2.0` was fixed identically to the companion candidate (same statistical definition
of "extreme" applied uniformly to both signals, not tuned per-candidate) -- chosen from the
unconditional distribution of `z` alone, before computing any forward return. Horizon = 24h, for
the same reason as the companion report: the mandated data source spans only ~45 usable days, so
the original's 7-day non-overlapping grid would yield ~6 windows, below any usable evidence floor.

| spec | threshold\|z\| | horizon | lag buffer | cost | N_indep | gross bps | net bps | t (naive) | t (day-clustered) | win rate | PF |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **PRIMARY_SPEC** | 2.0 | 24h | 15min | 14bps | 685 | -33.6 | **-47.6** | -2.68 | -1.54 | 46.0% | 0.72 |
| neighbor threshold (looser) | 1.5 | 24h | 15min | 14bps | 936 | -38.3 | -52.3 | -3.59 | -1.93 | 45.5% | 0.68 |
| neighbor threshold (tighter) | 2.5 | 24h | 15min | 14bps | 443 | -39.1 | -53.1 | -2.36 | -1.30 | 48.5% | 0.70 |
| neighbor horizon (shorter) | 2.0 | 12h | 15min | 14bps | 886 | -12.4 | -26.4 | -2.52 | -0.98 | 44.7% | 0.76 |
| neighbor horizon (longer) | 2.0 | 48h | 15min | 14bps | 515 | -42.1 | -56.1 | -1.78 | -0.92 | 46.4% | 0.76 |
| stress: lag buffer 30min | 2.0 | 24h | 30min | 14bps | 685 | -27.4 | -41.4 | -2.35 | -1.33 | 47.0% | 0.75 |
| stress: costs +50% (21bps) | 2.0 | 24h | 15min | 21bps | 685 | -33.6 | -54.6 | -3.08 | -1.77 | 44.4% | 0.69 |
| ex-biggest-shock-day (2026-08-21) | 2.0 | 24h | 15min | 14bps | 670 | -23.5 | -37.5 | -2.14 | -1.23 | 46.6% | 0.77 |
| mirror direction (momentum, informational -- not the tested claim) | 2.0 | 24h | 15min | 14bps | 685 | +33.6 | +19.6 | +1.10 | +0.64 | 48.9% | 1.14 |

`ex-2020` / `ex-biggest-year` are **N/A** for the same reason as the companion report (48-day, single
year sample); substituted with "ex-largest-single-BTC-day-move" (2026-08-21).

**Every single spec testing the claimed fade direction, including every perturbation, has a
negative net_bps, and most are negative even before costs (gross bps -12.4 to -42.1).** This is a
larger-magnitude, more consistently signed rejection than the companion TAKER_FLOW candidate: this
is not "no edge," it is "edge with the opposite sign from the one claimed." The mirror/momentum
direction (informational only, not the candidate under test, computed exactly like the source
report's own both-directions convention) shows a positive but statistically insignificant net edge
(+19.6bps, t=1.10 naive, t=0.64 day-clustered) -- directionally opposite to the claim, a real but
weak and non-actionable hint, explicitly not adopted as a rescued alternative (that would violate
the no-parameter-rescue rule: testing the opposite direction only after seeing the primary result
fail).

## 3. Declustering detail

- **Same-symbol clusters**: `N_raw` (hourly `|z|>=2.0` readings) = 7,376; `N_independent` (episodes)
  = 685 -- a 10.8x reduction (vs 2.3x for taker flow), confirming the stock-variable persistence
  described in S1: global-account LSR extremes cluster into long multi-hour runs far more than
  taker-flow extremes do.
- **Symbol concentration**: not a problem. 47/47 symbols contributed episodes; top-1 symbol
  (ARBUSDT) = 2.77% of episodes, top-5 = 13.3%. Median 14 episodes/symbol, min 8.
- **Cross-symbol systemic clustering -- the dominant effect, and the headline finding of this
  section, exactly as in the companion report**: grouping episodes across all symbols into clusters
  whenever consecutive entries are within 3 hours of each other collapses the 685 "independent"
  episodes into just **63 systemic clusters** (avg 10.9 episodes/cluster, max 65 episodes in one
  cluster, 94.7% of episodes belong to a cluster of 5+). **The true effective N for inference is
  closer to 63, not 685** -- roughly an 11x overstatement if the naive per-episode count were taken
  at face value. As with the companion candidate, this is why the day-clustered t-stat (-1.54) is
  materially weaker than the naive per-episode t-stat (-2.68), which on its own would look like a
  "significant" rejection but is not once genuine cross-sectional independence is accounted for.

## 4. Event rate / N_required / ETA

Same data-window limitation as the companion report: 2026-07-16 -> 2026-09-02 (48.4 calendar days,
~44.8 usable days after burn-in) is far short of a "last 2y/1y/6m" breakdown. Substituted with a
first-half/second-half split.

| metric | value |
|---|---|
| N_independent, full sample | 685 |
| independent episodes / day (full period) | 15.3 |
| independent episodes / week (full period) | 107.2 |
| independent episodes / month, 30d-equiv (full period) | 459.2 |
| episodes/day, first half of window | 11.7 |
| episodes/day, second half of window | 16.6 |
| **conservative_event_rate** (min of the two halves) | **11.7/day** |
| N_independent, **systemic-cluster-adjusted** (S3) | **63** (over 44.8 days ~ 1.4 clusters/day) |

- `expected_live_edge = 0.5 x net_bps(PRIMARY_SPEC) = 0.5 x (-47.6) = -23.78bps` -- **negative**.
- **N_required (block-day-bootstrap, one-sided alpha=5%, power=80%): not computable / N/A.** As
  with the companion candidate, the formula requires a positive target effect; the reimplemented,
  cost-adjusted expected live edge is negative. (For reference, the block-bootstrap did run: naive
  sd=463.8bps, bootstrap SE of the mean=37.8bps vs a naive iid SE of 17.7bps -> design effect ~4.55
  -- a substantially larger clustering inflation than the companion candidate's 1.29x, consistent
  with global-account LSR's much heavier same-symbol run persistence identified in S3.)
- **ETA_from_event_count / VALIDATION_ETA: N/A**, same reason. `minimum_calendar_span = 60 days`
  would still apply if this were re-tested with a positive point estimate on more data.
- **Evidence floors (30/50/100)**: naive N_independent (685) clears all three floors nominally, but
  the systemic-cluster-adjusted N (63) only just clears the 50 floor and does not reach 100 -- once
  the dominant source of non-independence is accounted for, the sample is thinner than it looks, on
  top of already pointing the wrong way.

## 5. Cross-signal correlation note (GLOBAL_ACCOUNT_LSR_FADE vs POSITIONING_TAKER_FLOW)

Identical computation to the companion report (same panel, same independently-built z-scores):

- Pooled Pearson correlation of the two causal z-scores (same symbol-hour): **r = -0.035**
  (Spearman: -0.035). Per-symbol correlations range from -0.13 to +0.04 (mean -0.036).
- Raw (pre-z) log-ratio correlation: **r = +0.004** -- essentially zero.
- Episode-level: of 685 GLOBAL_LSR_FADE episodes, 203 have a TAKER_FLOW episode for the same symbol
  within a 6h window; among those, direction agreement is 51.7% -- coin-flip level.
- Return correlation on those 203 overlapping episodes: **r = 0.024** -- essentially zero.

**Conclusion: the two signals are genuinely distinct, not the same information viewed two ways.**
This corroborates the original discovery's "flow vs stock" framing econometrically: a fast-moving
execution-flow ratio and a slow-moving retail-account-count ratio really do carry near-independent
information in this sample. The practical implication for portfolio construction: since *neither*
signal shows a validated positive net edge here, their independence doesn't create a double-
counting risk today -- but the near-zero correlation means that if one of them is later confirmed
(more history, different regime), it would not be redundant with the other.

## 6. Verification checklist

| item | status | note |
|---|---|---|
| Causality / no look-ahead in signal | OK | strict `shift(1).rolling` baseline, current bar excluded |
| PIT (signal availability) | OK, with caveat | 15min conservative buffer applied to live fapi data (no measured true lag available); original discovery's Vision-metrics source has a documented J-2 lag not obviously reflected in its stated 1-day entry lag (S0) |
| Timestamps / API lag handled | OK | see S1, S0 |
| Units | OK | log(longShortRatio), a dimensionless stock-of-position ratio, last-value-per-hour (correct for a level variable) |
| Target/entry/exit/horizon defined pre-results | OK | S2 |
| Declustering | OK, and material | S3 -- systemic clustering dominant (~11x), same-symbol run persistence also material (10.8x, vs 2.3x for the flow candidate) |
| Costs | OK | 14bps baseline (matches source convention), 21bps stress |
| Turnover | moderate-high | ~15 episodes/day across 47 names at 24h holding |
| Capacity | rough proxy only | median ~$1.08M/hour taker-$-activity across the universe used as a liquidity proxy (order-of-magnitude only) -- moot given the verdict |
| Concentration | OK (low, per-symbol) | top-1 = 2.8%, top-5 = 13.3% -- but see systemic clustering (S3), a different and more serious form of concentration |
| Listing effects | N/A / low risk | all 47 names actively traded through the full 48-day window |
| Survivorship | N/A / low risk | short window, active-only universe -- cannot meaningfully assess long-run survivorship with this data source |
| Missing data (shorter positioning history) | **material, documented** | S0 -- data/positioning/ is structurally capped at the archiver's runtime (~48 days), by Binance's 30-day API retention |

## 7. Verdict

**VALIDATED_FOR_FORWARD = FALSE.**

**Verdict: REJECTED.**

Reasoning: the independent reimplementation, built causally and PIT-consciously from the mandated
data source, shows a **negative** net edge in PRIMARY_SPEC and every one of seven pre-registered
perturbations, most with negative gross bps as well (not merely a cost-driven flip) -- a stronger
and more consistent rejection than the companion TAKER_FLOW candidate. The naive t-stat (-2.68)
would nominally read as "significant," but this overstates confidence once cross-symbol systemic
clustering is accounted for (true effective N ~63, not 685; day-clustered t=-1.54). This does not
confirm the mechanism claimed in the source report (+51.5bps, t=2.05, fade/mean-reversion). The
informational mirror direction (momentum) shows a directionally-opposite, weak, non-significant
positive edge (+19.6bps, t=1.10) -- worth noting as a hint for any future, properly pre-registered
re-test, but explicitly not chased here (that would be exactly the parameter-rescue-after-seeing-
results the brief prohibits). As with the companion report, this REJECTED verdict is based on a
single, short (~45-usable-day), single-regime window -- all that the mandated `data/positioning/`
source currently contains -- and should be read as a failure to independently confirm the mechanism
on fresh, live-collected, causally-lag-handled data, not as a re-adjudication of the original
multi-year finding on its own (different, longer) data source.
