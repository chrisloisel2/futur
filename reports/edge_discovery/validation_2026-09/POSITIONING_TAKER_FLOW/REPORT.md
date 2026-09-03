# Independent Validation — POSITIONING_TAKER_FLOW

**Validator**: independent worker, Alpha Validation Factory, 2026-09-02
**Claim under test** (`reports/edge_discovery/alpha_hunt_2026-09-01_round3/w3_relative_value/REPORT.md`,
row `D-TAKER_LSR-mom-7D`): the long/short ratio of **taker volumes** (a flow measure) predicts
**same-direction price continuation**. Reported: net +51.9bps, t=2.16, N_indep=224 (7D rebalance
grid, multi-year backfill from `data/derivatives_backfill/binance_vision_metrics/`).

**This validation**: independent reimplementation from the economic definition, using only
`data/positioning/` + `data/derivatives_raw` mark-price ticks, as mandated by the mission brief.
Original discovery code/scripts were not read. `src/institutional/live_alpha_lab/` and
`configs/live_alpha_registry.yaml` were not touched; `WHALE_LSR_SCREEN_V1`'s spec in the registry
was read only for context on how this project treats positioning-data provenance/thresholds.

## 0. Critical data-source finding (read this first)

`data/positioning/` is **not** the same dataset the original discovery used. It is a live-collected
archive (`scripts/archive_binance_positioning.py`, `src/institutional/data/positioning_archiver.py`)
of the Binance fapi `/futures/data/*Ratio` endpoints, which **only retain 30 days of history on the
exchange side**. The archiver has been running since **2026-07-16**, so as of today
(2026-09-02) `data/positioning/` covers **~48 calendar days / ~45 usable days after burn-in**, one
single market regime, for all 47 symbols present. The original discovery instead used
`data/derivatives_backfill/binance_vision_metrics/*_metrics_5m.parquet` (2020-09-01 → 2026-08-31,
verified directly: `BTCUSDT_metrics_5m.parquet` spans 630,370 5m rows over ~6 years), which is why
it could report 5-7 years of stability (5/6 years positive) and N_indep=224.

**Consequence**: this is a fundamentally shorter-sample, single-regime, out-of-sample-in-time
reimplementation, not a replication of the original multi-year backtest. Evidence floors and
N_required/ETA below are computed honestly against what `data/positioning/` can actually support,
not rescued by claiming a longer history the mandated data source doesn't have.

**Second finding, directly relevant to PIT**: `positioning_archiver.py`'s own docstring states the
Vision `metrics` bulk dumps the *original* discovery relied on — same ratios, same 5m granularity —
carry a **documented J-2 (2-day) publication lag** ("Les dumps Vision `metrics` couvrent les mêmes
ratios à 5 min mais avec un retard J-2"). The W3 report's stated PIT discipline applies a uniform
1-day entry lag to every feature (`engine.py:enrich_master`, "entry lag is always 1 day"). If the
underlying Vision-metrics data for taker/global LSR is really not published until D+2, a 1-day
entry lag is **insufficient by construction** for those two specific features — the original
`D-TAKER_LSR-mom-7D` and `D-GLOBAL_LSR-fade-7D` results may have used data one day earlier than it
was actually available. This is not fatal to a 7-day-horizon strategy but is a real, documented,
previously-unflagged PIT gap in the original discovery, worth fixing before that result is ever
used for a decision. My reimplementation uses the live fapi-fetched `data/positioning/` (not the
Vision dumps), which does not have this specific J-2 lag; I apply my own conservative causal
availability buffer instead (§2).

## 1. Reimplementation methodology

**Universe**: 47 symbols = every `{SYM}_taker_vol.parquet` present in `data/positioning/`, derived
by an independent glob (not copied from `configs/whale_lsr_screen_universe.yaml`, which was only
read for context and happens to describe the same 47-name freeze for a different alpha).

**Signal**: hourly bars built from the raw 5-minute `taker_vol` file. `taker_buy`/`taker_sell` are
**summed** (not averaged) over each hour — additive flow quantities — then
`taker_log_ratio = log(sum(buyVol)/sum(sellVol))`. An hour is only kept if >=8 of the <=12 possible
5m readings are present (>=66% coverage), else set to NaN (missing-data handling, not silently
forward-filled).

**Causal z-score** (own-history, strictly PIT): for each symbol, `z_t = (x_t - mean_{t-window..t-1}) /
std_{t-window..t-1}`, i.e. the baseline mean/std uses `shift(1).rolling(window)` -- the current bar's
own value is *excluded* from its own baseline (stricter than a same-window causal convention that
includes the current point). `window = 72h` (3 days), `min_periods = 60` (2.5 days) -- bars before
the 60th valid observation are NaN, giving an explicit ~2.5-day burn-in (not a silent partial-window
value).

**Causal availability lag** (item 2 of the brief -- Binance positioning-data API lag): the `timestamp`
field in `data/positioning/*.parquet` is the bucket's right-edge (close) time. There is no
`recv_time` column recorded (unlike `data/derivatives_raw`'s mark-price stream, which does record
`recv_time`/`latency_ms`), so the archiver's own local-disk write lag (up to 6h, its polling cadence)
cannot be used to bound true API-publication lag -- that would conflate archival cadence with
availability. Absent a measured number, I apply a **documented, conservative 15-minute buffer**
(PRIMARY_SPEC) before a bar's signal can be acted on -- 3x the 5-minute bucket size, matching this
repo's own convention for a stale-data safety margin (`src/institutional/live_alpha_lab/marks.py`:
"collecteur ~5min, marge x3"). A 30-minute buffer is tested as a stress perturbation.

**Entry/exit fills**: entry executed at the first raw (non-resampled) mark-price tick at-or-after
`entry_signal_ts + lag_buffer`; exit at the first tick at-or-after `entry_exec_ts + horizon`. If no
future tick exists (tail of the sample), the episode is dropped rather than fabricated -- this trims
the last `horizon` hours of usable episodes, an honest look-ahead boundary, not a leak.

**Direction (POSITIONING_TAKER_FLOW claim)**: `direction = sign(z)` -- long when taker flow is
buy-skewed, short when sell-skewed (momentum/continuation, as claimed).

**Costs**: 14bps round trip baseline (5bps taker + 2bps slippage, doubled -- matches the source
report's own "@14bps" convention), applied once per episode as a flat deduction from gross bps.

**Declustering** (mandatory, S4 of the brief): per symbol, contiguous same-sign runs of `|z| >=
threshold` are merged into one episode (entry = first bar of the run) -- this is exactly the
"positioning extremes persist for multiple consecutive readings" concern the brief calls out. A
further 24h (=horizon) cooldown per symbol prevents a new episode from starting inside the
evaluation window of the prior one for that symbol. `N_raw` = every hourly `|z|>=threshold` reading
before this decluster; `N_independent` = retained episodes after it. See S4 below for a *third*,
more severe layer of clustering (cross-symbol systemic) that further reduces the effective N.

## 2. PRIMARY_SPEC and perturbations

Threshold was chosen from the **unconditional distribution of `z` alone**, before computing any
forward return: `|z|>=2.0` on `taker_z` empirically fires on 5.09% of causal hourly readings pooled
across the universe (44,271 valid hourly obs) -- close to the ~4.6% expected under a normal
approximation, i.e. a genuinely "extreme" cut, not a return-tuned one. Horizon = 24h was chosen
because the mandated data source only spans ~45 usable days: the original's 7-day non-overlapping
grid would yield only ~6 non-overlapping windows, far below any usable evidence floor, so a shorter,
data-appropriate horizon was pre-registered instead (documented here, before results, per the
brief's instruction to "pick and document your horizon before results").

| spec | threshold\|z\| | horizon | lag buffer | cost | N_indep | gross bps | net bps | t (naive) | t (day-clustered) | win rate | PF |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **PRIMARY_SPEC** | 2.0 | 24h | 15min | 14bps | 978 | -4.1 | **-18.1** | -1.49 | -0.97 | 45.0% | 0.86 |
| neighbor threshold (looser) | 1.5 | 24h | 15min | 14bps | 1,436 | +4.2 | -9.8 | -0.93 | -0.45 | 47.8% | 0.92 |
| neighbor threshold (tighter) | 2.5 | 24h | 15min | 14bps | 493 | +7.8 | -6.2 | -0.36 | -0.96 | 48.7% | 0.95 |
| neighbor horizon (shorter) | 2.0 | 12h | 15min | 14bps | 1,356 | -7.3 | -21.3 | -3.26 | -1.93 | 43.9% | 0.77 |
| neighbor horizon (longer) | 2.0 | 48h | 15min | 14bps | 638 | +3.8 | -10.2 | -0.45 | +0.37 | 49.7% | 0.94 |
| stress: lag buffer 30min | 2.0 | 24h | 30min | 14bps | 978 | -1.8 | -15.8 | -1.30 | -0.76 | 46.0% | 0.88 |
| stress: costs +50% (21bps) | 2.0 | 24h | 15min | 21bps | 978 | -4.1 | -25.1 | -2.06 | -1.45 | 42.8% | 0.81 |
| ex-biggest-shock-day (2026-08-21) | 2.0 | 24h | 15min | 14bps | 965 | -3.3 | -17.3 | -1.40 | -0.85 | 45.0% | 0.87 |
| mirror direction (fade, informational -- not the tested claim) | 2.0 | 24h | 15min | 14bps | 978 | +4.1 | -9.9 | -0.81 | -0.95 | 46.9% | 0.92 |

`ex-2020` / `ex-biggest-year` are **N/A** -- the sample is 48 days in a single year, so these
anchors from the mission template don't apply; substituted with "ex-largest-single-BTC-day-move"
(2026-08-21, the day with the largest |BTC daily return| in the window) as the closest available
analog of a dominant single-event robustness check.

**Every single spec, including every perturbation, has a negative net_bps.** Gross bps hovers near
zero (-7.3 to +7.8bps) across all specs -- i.e. there is essentially **no raw predictive edge before
costs** in this sample, and the realistic 14bps round-trip cost pushes every variant negative. The
mirror (fade) direction is also net-negative and statistically indistinguishable from zero -- so this
is not a case of "right idea, wrong sign": neither direction shows a usable edge.

## 3. Declustering detail

- **Same-symbol clusters**: handled by contiguous-run merge + 24h cooldown (see S1). `N_raw` (hourly
  `|z|>=2.0` readings) = 2,255; `N_independent` (episodes) = 978 -- a 2.3x reduction, i.e. most
  extreme readings are single- or few-hour excursions, not long persistent runs (taker flow is a
  short-horizon variable, consistent with its near-normal marginal z-distribution).
- **Symbol concentration**: not a problem. 47/47 symbols contributed episodes; top-1 symbol
  (ADAUSDT) = 2.66% of episodes, top-5 = 12.5%. Median 21 episodes/symbol, min 15.
- **Cross-symbol systemic clustering -- the dominant effect, and the headline finding of this
  section**: grouping episodes across *all* symbols into clusters whenever consecutive entries are
  within 3 hours of each other collapses the 978 "independent" episodes into just **49 systemic
  clusters** (avg 20.0 episodes/cluster, max 81 episodes in a single cluster, 97.1% of all episodes
  belong to a cluster of 5+). In plain terms: most of the 978 nominally-independent, per-symbol-
  declustered episodes are not economically independent draws -- they are dozens of symbols all
  crossing the same extreme-taker-flow threshold within a few hours of each other, almost certainly
  driven by the same handful of market-wide volatility/deleveraging events during this 48-day
  window. **The true effective N for inference is closer to 49, not 978** -- a ~20x overstatement if
  the naive per-episode count were taken at face value. This is exactly why the day-clustered t-stat
  (-0.97) is so much weaker than the naive per-episode t-stat (-1.49): day-clustering only partially
  captures this, since a single systemic cluster can itself span multiple days or multiple clusters
  can occur within one day.

## 4. Event rate / N_required / ETA

`data/positioning/`'s window (2026-07-16 -> 2026-09-02, 48.4 calendar days, ~44.8 days of episode-
generating history after burn-in) is far short of the "last 2y/1y/6m" breakdown the brief asks for --
there is no 2y/1y/6m of history to break down. Substituted with a first-half/second-half split of
the available window as the closest stability check.

| metric | value |
|---|---|
| N_independent, full sample | 978 |
| independent episodes / day (full period) | 21.9 |
| independent episodes / week (full period) | 153.0 |
| independent episodes / month, 30d-equiv (full period) | 655.6 |
| episodes/day, first half of window | 18.4 |
| episodes/day, second half of window | 22.0 |
| **conservative_event_rate** (min of the two halves) | **18.4/day** |
| N_independent, **systemic-cluster-adjusted** (S3) | **49** (over 44.8 days ~ 1.1 clusters/day) |

- `expected_live_edge = 0.5 x net_bps(PRIMARY_SPEC) = 0.5 x (-18.1) = -9.06bps` -- **negative**.
- **N_required (block-day-bootstrap, one-sided alpha=5%, power=80%): not computable / N/A.** The
  formula `n = ((z_alpha+z_beta)*sigma/effect)^2` requires a positive target effect; here the
  reimplemented, cost-adjusted expected live edge is negative, so there is no sample size at which
  this strategy, as specified, would be expected to power a *positive* live effect -- collecting more
  data would not "unlock" this candidate, it would refine an estimate that is already pointing the
  wrong way. (For reference, the block-bootstrap did run: naive sd=381.3bps, bootstrap SE of the
  mean=13.9bps vs a naive iid SE of 12.2bps -> design effect ~1.29, a mild but real clustering
  inflation on top of the much larger 20x effect already identified via systemic-cluster counting in S3.)
- **ETA_from_event_count / VALIDATION_ETA: N/A**, same reason. `minimum_calendar_span = 60 days`
  would still apply if this were re-tested with a positive point estimate on more data.
- **Evidence floors (30/50/100)**: naive N_independent (978) clears all three floors nominally, but
  the systemic-cluster-adjusted N (49) sits **between the 30 and 50 floor** -- i.e. once the dominant
  source of non-independence is accounted for, this candidate does not clearly clear even the lowest
  evidence floor for genuinely independent confirmation.

## 5. Cross-signal correlation note (POSITIONING_TAKER_FLOW vs GLOBAL_ACCOUNT_LSR_FADE)

Computed on the same 47-symbol, same-window panel, using my own independently-built `taker_z` and
`global_z` series:

- Pooled Pearson correlation of the two causal z-scores (same symbol-hour): **r = -0.035**
  (Spearman: -0.035). Per-symbol correlations range from -0.13 to +0.04 (mean -0.036).
- Raw (pre-z) log-ratio correlation: **r = +0.004** -- essentially zero.
- Episode-level: of 978 TAKER_FLOW episodes, only 203 (21%) have a GLOBAL_LSR_FADE episode for the
  same symbol within a 6h window; among those 203, direction agreement is 51.7% -- coin-flip level.
- Return correlation on those 203 overlapping episodes: **r = 0.024** -- essentially zero.

**Conclusion: the two signals are genuinely distinct, not the same information viewed two ways.**
This corroborates the original discovery's "flow vs stock" framing econometrically. The practical
implication is the opposite of what the framing implies for sizing, though: since *neither* signal
shows a validated positive net edge in this reimplementation, their independence doesn't create a
double-counting risk today -- but if either is revisited later (more history, different regime) and
found to work, this near-zero correlation means they genuinely could be run as separate risk
sleeves without redundant exposure.

## 6. Verification checklist

| item | status | note |
|---|---|---|
| Causality / no look-ahead in signal | OK | strict `shift(1).rolling` baseline, current bar excluded |
| PIT (signal availability) | OK, with caveat | 15min conservative buffer applied to live fapi data (no measured true lag available); original discovery's Vision-metrics source has a documented J-2 lag not obviously reflected in its stated 1-day entry lag (S0) |
| Timestamps / API lag handled | OK | see S1, S0 |
| Units | OK | log(buyVol/sellVol) for a flow ratio; log(longShortRatio) for the account-stock ratio (symmetric treatment for z-scoring) |
| Target/entry/exit/horizon defined pre-results | OK | S2 |
| Declustering | OK, and material | S3 -- systemic clustering is the dominant effect, ~20x |
| Costs | OK | 14bps baseline (matches source convention), 21bps stress |
| Turnover | high | ~22 episodes/day across 47 names at 24h holding ~ near-constant portfolio churn |
| Capacity | rough proxy only | median ~$1.08M/hour taker $ activity across the universe (order-of-magnitude only, not a rebuilt trailing-30d-$vol model) -- moot given the verdict |
| Concentration | OK (low, per-symbol) | top-1 = 2.7%, top-5 = 12.5% -- but see systemic clustering (S3), a different and more serious form of concentration |
| Listing effects | N/A / low risk | all 47 names actively traded through the full 48-day window, no listings/delistings observed |
| Survivorship | N/A / low risk | short window, active-only universe, not multi-year -- cannot meaningfully assess long-run survivorship with this data source |
| Missing data (shorter positioning history) | **material, documented** | S0 -- data/positioning/ is structurally capped at the archiver's runtime (~48 days), by Binance's 30-day API retention |

## 7. Verdict

**VALIDATED_FOR_FORWARD = FALSE.**

**Verdict: REJECTED.**

Reasoning: the independent reimplementation, built causally and PIT-consciously from the mandated
data source, shows near-zero gross predictive edge before costs and a **negative** net edge in every
one of PRIMARY_SPEC and all seven pre-registered perturbations (no parameter combination rescues a
positive result; the mirror/fade direction is also non-significant). The apparent (weak, non-
significant) naive t-stat of -1.49 is itself an overstatement of confidence once cross-symbol
systemic clustering is accounted for (true effective N ~ 49, not 978). This does not confirm the
mechanism claimed in the source report (+51.9bps, t=2.16, momentum/continuation). Given the strength
and consistency of the negative sign, this is a REJECTED verdict rather than NEEDS_MORE_RESEARCH --
but the caveat is explicit and important: this rejection is based on a single, short (~45-usable-
day), single-regime window, because that is all the mandated `data/positioning/` source currently
contains. It should not be read as a rejection of the original multi-year finding on its own data
(which this validation did not and was not asked to re-touch) -- only as a failure to independently
confirm the mechanism on fresh, live-collected, causally-lag-handled data from the same exchange.
