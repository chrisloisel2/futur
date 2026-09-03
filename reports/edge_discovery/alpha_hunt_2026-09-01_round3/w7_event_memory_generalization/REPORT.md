# W7 — Event History / Memory Features: Generalizing the Liquidation-Cascade Repeat Effect

**Worker:** W7, Alpha Hunt Round 3. **Axis B:** event-history/memory features.
**Mandate:** round 2's headline finding (W2 + W9, corroborated two ways) is that liquidation
cascades pay only on **repeat** occurrence within a symbol/24h window — 1st hit ≈ -6 to -19bps
net, 3rd+ hit ≈ +42 to +87bps net. This worker's job was to test whether that same "memory
matters" shape (weak/negative first occurrence → strong positive on repeat) generalizes to
**other** event families: OI shocks, volume shocks, taker-flow (CVD) shocks, basis shocks,
funding-rate extremes, options implied-vol shocks (Deribit DVOL), and crowd-positioning flips
(top-account LSR). Read-only mining of data already on disk; no new collection.

## 0. Recovered partial work

A prior, rate-limit-interrupted run of this exact task had already built a working pipeline
(`build_panel.py` → `run_events.py` → `run_analysis.py` → `driver.py`) covering 5 derivatives-panel
event types × up to 6 memory-feature constructions (32 candidates, 116 result rows) in
`/tmp/.../scratchpad/round3/w7/`. It was verified for correctness (PIT rolling-z construction,
forward-return alignment, causal memory-feature construction, greedy declustering) and reused
as-is. This report **extends** it with two more event families (Deribit DVOL implied-vol shocks,
and top-account LSR positioning flips) to broaden family diversity per the mandate, for a final
**42 candidates / 148 bucket-level observations**.

## 1. Methodology

**Data sources.**
- Derivatives-panel events (OI_SHOCK, VOLUME_SHOCK, CVD_SHOCK, BASIS_SHOCK, FUNDING_EXTREME):
  hourly-resampled `data-v2/normalized/event_feature_panel` (50-symbol universe from
  `configs/portfolio_v1_1_parallel_50.yaml`), which already carries causal (backward-looking)
  `basis_z_7d` and `funding_rate_percentile_90d` fields; OI/volume/CVD z-scores built here.
- Options implied-vol shocks (DVOL_SHOCK): `data/options_backfill/deribit/DVOL_{BTC,ETH}_1d.parquet`,
  2021-03 → 2026-09, daily. Genuinely different data source (options market, not futures flow/OI).
- Positioning flips (LSR_EXTREME): `data/positioning/*_top_account.parquet` (Binance top-account
  long/short ratio, 5-min, 47/50 universe symbols) — but **only from 2026-07-16**, i.e. ~7 weeks
  of history. Forward returns for this family use `data/enriched/*_1h_enriched.parquet` (a
  live-refreshed close-price feed) instead of the stale `event_feature_panel` (which stops
  2026-07-31) — only 10/47 symbols (the live paper-trading fleet: BTC/ETH/SOL/BNB/XRP/DOGE/
  ADA/AVAX/DOT/LINK) turned out to have `enriched` data refreshed past July, so the effective
  LSR sample is these 10 symbols over ~27 days.

**Event definitions** (all thresholds are the analyst's own choice, documented, not tuned to
outcome): OI_SHOCK = |causal rolling z of `oi_delta_pct_1h`| ≥ 2.5 (720h window/480h min-periods,
baseline computed on `shift(1)` history only); VOLUME_SHOCK = causal z of log(volume) ≥ 3.0
(heavier tail, higher bar), direction = sign of the accompanying bar return; CVD_SHOCK = |causal
z of signed taker volume| ≥ 2.5; BASIS_SHOCK = |`basis_z_7d`| ≥ 2.0 (panel-native causal field);
FUNDING_EXTREME = actual settlement events with `funding_rate_percentile_90d` ≥ 0.9 or ≤ 0.1 (not
every hour it persists — dedup on real settlement flag); DVOL_SHOCK = |causal z of daily
log-change in DVOL close, 90d window/60d min-periods| ≥ 2.0; LSR_EXTREME = |causal z of hourly
log-change in top-account long/short ratio, 720h/480h| ≥ 2.5.

**PIT discipline.** Every rolling baseline (mean/std for z-scores, funding percentile) is computed
strictly on data before the event bar (`shift(1)` inside the rolling window). Memory features
(`time_since_prev`, `count_Nh`, `cumint_Nh`, `run_length_same_sign`, `is_alternating_step`,
`accel_vs_decel`) are recomputed per `(symbol, event_type, direction)` group and use only events
strictly before the current event's timestamp — verified by code inspection of `lib.py`
(`add_memory_features`, `add_direction_sequence_features`). Forward returns are computed strictly
forward from the event bar's close (searchsorted position, never touching pre-event data).

**Decluster / independence.** A separate greedy chronological decluster (`lib.py::decluster`) marks
an event "independent" only if it is ≥ horizon_h away from the last **kept** independent event in
the same group — this is deliberately distinct from (and stricter than) the overlapping
`count_24h`-style feature construction, exactly per the mandate ("still report an honest
independent-episode count for OUTCOME significance, separate from the necessarily-overlapping
feature construction"). `horizon_h` = the forward-return horizon (4h for hourly events, 24h for
DVOL's daily events). **Caveat found and reported honestly:** for DVOL (one observation per
calendar day), a 24h decluster horizon is nearly a no-op (raw ≈ independent count in almost every
DVOL bucket) since consecutive daily rows are already ≥24h apart by construction — it does not
collapse a multi-day vol-regime cluster (e.g. a crash week) into one independent episode. A
robustness check with a stricter 7-day decluster gap still retains 80/116 (up) and 73/87 (down)
independent DVOL events, i.e. events are not overwhelmingly concentrated into a handful of crash
weeks, but the DVOL sample should still be read as more fragile than its raw N suggests.

**Costs.** Flat 14bps round-trip (5bps taker + 2bps slippage × 2 legs — project-standard net14
convention used across round 2). All `net_bps` = `gross_bps − 14`.

**Statistics.** Simple bucket means first (mandate: "simple models first"). Bucket = a categorical
split of the memory feature (repeat-count bins, time-since-prev bins, intensity terciles,
streak-length bins, accel/decel/flat). `t_stat` is computed on the **gross** signed return
(not net) — i.e. it tests whether the underlying return distribution differs from zero, not
whether the strategy clears costs; always read `t_stat` together with `net_bps`.

## 2. Per-event-type findings

**OI_SHOCK (forced-deleveraging / crowded-build exhaustion — flagged liq-cascade-adjacent by
round 2's own economics: OI-down-shock literally IS the forced-deleveraging that liquidation
cascades are made of).** Down/low direction (deleveraging fade = long) shows a **clean, monotonic
memory effect** across every feature tested: count_24h (-7.0→-5.3→-2.8→+6.9bps net across
0/1/2/3+ repeats), time_since_prev (cold -9.9 → hot(<24h) +1.0bps, t=3.18), cumint_7d tercile
(low -8.5 → high +8.3bps, t=4.80), accel vs decel (+11.6bps net when accelerating, t=3.59, vs
-7.1 decelerating). Up/high direction (crowded-build, not deleveraging) shows **no such effect**
— confirms the asymmetry is specific to the deleveraging/liquidation-adjacent side, exactly as
round 2's own reasoning predicts. **This corroborates round 2's finding through an independent
event definition (no liquidation feed used) but is very likely the SAME underlying mechanism
family, not a new one** — distinctness LOW.

**VOLUME_SHOCK (panic/blow-off exhaustion).** No usable memory pattern. Up-direction (panic
volume) is deeply net-negative at every repeat count (-26 to -24bps, no improvement). Down-direction
(blow-off volume) is wildly non-monotonic (0:-3.7, 1:-79.1, 2:+12.9, 3+:+32.5bps) — the huge dip at
bucket 1 kills any clean story; reads as noise around a structurally weak/negative base rate, not
a repeat-strengthens effect. **DEAD.**

**CVD_SHOCK (aggressive taker-flow exhaustion).** Down/low direction (aggressive-sell shock, fade
long) shows the **cleanest, most statistically robust generalization found in this study**: count_24h
net -9.5→-5.3→+7.6→+15.2bps, t-stat rising 2.22→2.51→3.87→4.48 monotonically; time_since_prev
cold -26.1bps (t=-2.54, *significantly negative*) → hot(<24h) +3.6bps (t=6.14, N=16,798); cumint_7d
tercile low -18.7 → high +15.6bps (t=7.54, the single strongest t-stat in the whole study). Up/high
direction (aggressive-buy shock) shows no such pattern (flat/negative throughout) — again an
asymmetry pointing at a selling/deleveraging-specific mechanism. **Important caveat on
distinctness:** liquidation-driven forced selling shows up simultaneously as aggressive taker-sell
flow, as an OI-down-shock, and often as a volume shock — CVD_SHOCK-down repeat-strengthening is
plausibly **overlapping with the original liq-cascade mechanism** (viewed through a different data
lens) rather than a truly independent discovery. Flagged PROMISING but **not** claimed as a new
family without a direct event-level overlap check against the round-2 `liq_cascade_dataset`
(not performed here — out of scope/time for this worker, recommended as a fast follow-up).

**BASIS_SHOCK (term-structure dislocation).** Both directions show the *same qualitative shape*
(repeat/hot/high-intensity buckets outperform isolated/cold/low-intensity ones) with high gross
t-stats (up to 7.10), but the **net edge tops out near breakeven** (best bucket nets: +2.1bps up,
+2.6bps down) — costs eat essentially the whole thing. This is a genuinely different economic
mechanism (carry/term-structure, not futures flow/OI) showing a directionally-consistent but
economically negligible memory effect. **WEAK** — real pattern, not tradeable.

**FUNDING_EXTREME (crowded-positioning fade).** No repeat-strengthens pattern at all — if
anything, the opposite. count_24h buckets are flat/non-monotonic and mostly net-negative
throughout, with several event types even getting *worse* on repeat (down-direction: -14.7 →
-16.2 → -20.2 → -16.9bps). Most strikingly, `time_since_prev` on the up-direction shows the
**isolated/cold bucket net +9.9bps (t=3.85, N=1,895, genuinely significant)** while the "hot"
(recently repeated) bucket is flat/negative (-12.9bps) — the **reverse** of the liq-cascade shape.
Interpretation: a funding extreme that's been persistently crowded for a while (many recent
occurrences) is a stale, already-arbitraged signal; a *fresh* funding extreme after a long quiet
period is the one that still has edge. **DEAD** for the "repeat strengthens" hypothesis — but the
reversed cold-bucket result is flagged separately as a small, distinct, honestly-reported curiosity
(not this worker's mandate to chase further).

**DVOL_SHOCK (Deribit implied-vol regime shock — genuinely new family: options market, not
futures positioning/flow/funding).** Up-direction (IV spike = fear event, fade = long BTC/ETH)
shows a strong, fairly clean version of the pattern across all three features tested: count_30d
net +209→+257→+572bps (t up to 6.26), time_since_prev cold +157 → hot(<7d) +518bps (t=6.19),
cumint_90d tercile zero +264 → high +400bps (t=3.97). Down-direction (IV crush = complacency,
fade = short) is much weaker/mixed (non-monotonic, one feature even reverses). **Caveats that
matter more here than anywhere else in this report:** N per bucket is tiny (13-52), the 5-year
history contains only a handful of true macro tail episodes (2021 top, LUNA/3AC May 2022, FTX
Nov 2022, 2023-24 macro selloffs) that likely dominate the extreme buckets (one bucket has
PF=23.2 — a value that size in a 30-observation bucket is almost certainly a few huge winners,
not a stable edge), and BTC+ETH are highly correlated so this is really "2 symbols" worth of
independent macro history, not 2 symbols × many episodes. **PROMISING-WITH-CAVEAT**: directionally
the cleanest new-family result in the study, but treat as hypothesis-generating, not confirmed —
needs either more history (post-2026 data as it accrues) or extension to single-name Deribit IV
where available before it could be trusted operationally.

**LSR_EXTREME (top-account long/short-ratio flip — genuinely different mechanism: positioning
composition, not price/flow/OI/funding).** No usable conclusion can be drawn — **DATA_LIMITED**.
Only ~27 days of effective sample (10 symbols with fresh forward-return coverage), producing
7-65 raw / 4-59 independent observations per bucket; results bounce with no consistent sign or
direction across buckets (e.g. count_24h up-direction: bucket "0" +12.5bps vs bucket "1" -30.3bps
— a reversal with no economic story, consistent with pure sampling noise at this N). This is a
real data-coverage gap, not a negative finding about the mechanism: the positioning collector
only started 2026-07-16 project-wide, and only 10/47 universe symbols have live-refreshed price
data past July 31 to compute forward returns against. Round 2's W10 separately found a related
but different signal (whale extreme-long → underperformance, -57.8bps, n=87, no memory
conditioning) on the same raw data type — worth revisiting this axis once more positioning
history accrues.

## 3. Results table

Baseline bucket = first-occurrence / most-isolated / no-prior-streak / decelerating (as
applicable per feature); extreme bucket = most-repeat / most-recent-repeat / highest-intensity /
accelerating (as applicable). `gross_bps`/`net_bps`/`PF`/`t_stat` shown are for the **extreme**
bucket (N_raw/N_independent likewise); `stability` states the extreme-bucket direction and
baseline→extreme trend so the "does repetition help" contrast is visible in one row.

| candidate_id | family | event_type | direction | memory_feature | N_raw | N_indep | gross_bps | net_bps | PF | t_stat | stability | distinctness (vs liq-cascade) | status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| W7-001 | oi_shock_memory | OI_SHOCK | up | count_24h | 2,975 | 1,538 | 10.2 | -3.8 | 1.08 | 0.94 | weak monotonic ↑, base -11.4→-3.8 | n/a (no effect) | WEAK |
| W7-002 | oi_shock_memory | OI_SHOCK | down | count_24h | 1,264 | 693 | 20.9 | 6.9 | 1.21 | 1.57 | clean monotonic ↑, base -7.0→+6.9 | LOW — liq-cascade-adjacent by construction | PROMISING |
| W7-003 | volume_shock_memory | VOLUME_SHOCK | up | count_24h | 1,796 | 861 | -9.8 | -23.8 | 0.95 | -0.42 | flat/negative, no improvement | — | DEAD |
| W7-004 | volume_shock_memory | VOLUME_SHOCK | down | count_24h | 1,121 | 537 | 46.5 | 32.5 | 1.26 | 1.77 | non-monotonic (dips at bucket 1) | — | WEAK |
| W7-005 | taker_flow_shock_memory | CVD_SHOCK | up | count_24h | 4,045 | 2,381 | -10.9 | -24.9 | 0.93 | -1.06 | flat/negative | — | DEAD |
| W7-006 | taker_flow_shock_memory | CVD_SHOCK | down | count_24h | 8,609 | 4,843 | 29.2 | 15.2 | 1.22 | 4.48 | clean monotonic ↑, t rises 2.2→4.5 | MODERATE-LOW — likely overlaps liq-driven selling, unverified | PROMISING |
| W7-007 | basis_shock_memory | BASIS_SHOCK | up | count_24h | 20,033 | 9,324 | 16.1 | 2.1 | 1.23 | 6.51 | clean monotonic ↑, net ≈ breakeven | HIGH — different economic factor (carry) | WEAK |
| W7-008 | basis_shock_memory | BASIS_SHOCK | down | count_24h | 19,156 | 9,299 | 16.6 | 2.6 | 1.18 | 4.64 | mostly monotonic ↑, net ≈ breakeven | HIGH | WEAK |
| W7-009 | funding_extreme_memory | FUNDING_EXTREME | up | count_24h | 29,705 | 29,691 | -1.1 | -15.1 | 0.99 | -0.80 | non-monotonic, worse at extreme | HIGH — different factor | DEAD |
| W7-010 | funding_extreme_memory | FUNDING_EXTREME | down | count_24h | 1,556 | 1,556 | -2.9 | -16.9 | 0.96 | -0.49 | monotonic ↓ (gets worse w/ repeats) | HIGH | DEAD |
| W7-011 | oi_shock_memory | OI_SHOCK | up | time_since_prev | 11,875 | 6,996 | 3.3 | -10.7 | 1.03 | 0.83 | flat, hot ≈ cold | n/a | DEAD |
| W7-012 | oi_shock_memory | OI_SHOCK | down | time_since_prev | 7,238 | 4,590 | 15.0 | 1.0 | 1.16 | 3.18 | cold -9.9→hot +1.0, significant gross | LOW — same family as W7-002 | PROMISING |
| W7-013 | volume_shock_memory | VOLUME_SHOCK | up | time_since_prev | 4,103 | 1,913 | -17.0 | -31.0 | 0.91 | -1.28 | flat/negative | — | DEAD |
| W7-014 | volume_shock_memory | VOLUME_SHOCK | down | time_since_prev | 3,540 | 1,707 | -11.1 | -25.1 | 0.94 | -0.74 | reversed (hot worse than cold) | — | DEAD |
| W7-015 | taker_flow_shock_memory | CVD_SHOCK | up | time_since_prev | 11,530 | 7,695 | -5.7 | -19.7 | 0.95 | -1.29 | flat/negative | — | DEAD |
| W7-016 | taker_flow_shock_memory | CVD_SHOCK | down | time_since_prev | 25,318 | 16,798 | 17.6 | 3.6 | 1.16 | 6.14 | cold -26.1(sig.neg)→hot +3.6(sig) | MODERATE-LOW, same family as W7-006 | PROMISING |
| W7-017 | basis_shock_memory | BASIS_SHOCK | up | time_since_prev | 38,749 | 21,735 | 11.5 | -2.5 | 1.16 | 7.10 | cold -11.7→hot -2.5, net still neg | HIGH | WEAK |
| W7-018 | basis_shock_memory | BASIS_SHOCK | down | time_since_prev | 39,265 | 22,466 | 13.2 | -0.8 | 1.15 | 6.30 | cold -9.0→hot -0.8, net ≈ breakeven | HIGH | WEAK |
| W7-019 | funding_extreme_memory | FUNDING_EXTREME | up | time_since_prev | 50,721 | 50,707 | 1.1 | -12.9 | 1.01 | 1.00 | reversed: cold +9.9(sig,N=1895)→hot -12.9 | HIGH — opposite-sign finding | DEAD |
| W7-020 | funding_extreme_memory | FUNDING_EXTREME | down | time_since_prev | 11,918 | 11,918 | -4.0 | -18.0 | 0.95 | -1.90 | reversed (hot worse than cold) | HIGH | DEAD |
| W7-021 | oi_shock_memory | OI_SHOCK | down | cumint_7d_tercile | 6,135 | 4,588 | 22.3 | 8.3 | 1.25 | 4.80 | low -8.5→high +8.3, significant | LOW, same family as W7-002/012 | PROMISING |
| W7-022 | taker_flow_shock_memory | CVD_SHOCK | down | cumint_7d_tercile | 14,342 | 10,012 | 29.6 | 15.6 | 1.26 | 7.54 | low -18.7→high +15.6, strongest t in study | MODERATE-LOW, same family as W7-006/016 | PROMISING |
| W7-023 | basis_shock_memory | BASIS_SHOCK | up | cumint_7d_tercile | 18,769 | 9,963 | 14.4 | 0.4 | 1.19 | 5.62 | low -11.3→high +0.4, net ≈ 0 | HIGH | WEAK |
| W7-024 | funding_extreme_memory | FUNDING_EXTREME | up | cumint_7d_tercile | 21,123 | 21,112 | -0.4 | -14.4 | 0.99 | -0.23 | flat, no pattern | HIGH | DEAD |
| W7-025 | oi_shock_memory | OI_SHOCK | both | run_length_same_sign | 23,329 | 17,302 | 9.2 | -4.8 | 1.10 | 4.20 | improves but net neg (dirs pooled) | LOW, dilutes W7-002/012/021 | WEAK |
| W7-026 | taker_flow_shock_memory | CVD_SHOCK | both | run_length_same_sign | 41,324 | 30,655 | 5.7 | -8.3 | 1.06 | 3.09 | improves but net neg (dirs pooled) | MODERATE-LOW, dilutes W7-006/016/022 | WEAK |
| W7-027 | funding_extreme_memory | FUNDING_EXTREME | both | run_length_same_sign | 73,856 | 73,842 | -0.4 | -14.4 | 1.00 | -0.42 | flat, baseline is N=48 noise | HIGH | DEAD |
| W7-028 | oi_shock_memory | OI_SHOCK | both | is_alternating_step | 23,346 | 16,377 | 10.7 | -3.3 | 1.12 | 4.78 | continuation > flip, net neg pooled | LOW, dilutes W7-002 family | WEAK |
| W7-029 | taker_flow_shock_memory | CVD_SHOCK | both | is_alternating_step | 41,356 | 30,164 | 5.4 | -8.6 | 1.05 | 2.83 | continuation slightly > flip | MODERATE-LOW | WEAK |
| W7-030 | funding_extreme_memory | FUNDING_EXTREME | both | is_alternating_step | 16,658 | 16,658 | 2.9 | -11.1 | 1.04 | 1.68 | flip slightly less bad (opp. of hypothesis) | HIGH | DEAD |
| W7-031 | oi_shock_memory | OI_SHOCK | down | accel_vs_decel_12h | 4,317 | 2,067 | 25.6 | 11.6 | 1.29 | 3.59 | accelerating >> decel/flat, significant | LOW, same family as W7-002/012/021 | PROMISING |
| W7-032 | funding_extreme_memory | FUNDING_EXTREME | up | accel_vs_decel_12h | 7,314 | 7,307 | -1.2 | -15.2 | 0.99 | -0.39 | accelerating worse than flat (opposite) | HIGH | DEAD |
| W7-033 | options_iv_shock_memory | DVOL_SHOCK | up | count_30d_same_direction | 30 | 30 | 585.5 | 571.5 | 23.21 | 6.26 | strong monotonic ↑, tiny N, PF outlier-driven | HIGH — genuinely new (options market) | PROMISING-WITH-CAVEAT |
| W7-034 | options_iv_shock_memory | DVOL_SHOCK | up | time_since_prev | 40 | 40 | 532.0 | 518.0 | 13.05 | 6.19 | cold +157→hot +518, corroborates W7-033 | HIGH, same new family as W7-033 | PROMISING-WITH-CAVEAT |
| W7-035 | options_iv_shock_memory | DVOL_SHOCK | up | cumint_90d_tercile | 51 | 51 | 413.9 | 399.9 | 3.85 | 3.97 | zero +264→high +400, corroborates | HIGH, same family as W7-033 | PROMISING-WITH-CAVEAT |
| W7-036 | options_iv_shock_memory | DVOL_SHOCK | down | count_30d_same_direction | 21 | 21 | 160.3 | 146.3 | 3.89 | 1.94 | non-monotonic (peaks at bucket 1) | HIGH, but weaker than up-side | WEAK |
| W7-037 | options_iv_shock_memory | DVOL_SHOCK | down | time_since_prev | 18 | 18 | 131.2 | 117.2 | 3.87 | 1.73 | direction-consistent, not significant | HIGH, weak corroboration | WEAK |
| W7-038 | options_iv_shock_memory | DVOL_SHOCK | down | cumint_90d_tercile | 35 | 35 | 159.8 | 145.8 | 3.80 | 2.51 | reversed (zero-bucket best) | HIGH, contradicts up-side shape | WEAK |
| W7-039 | positioning_flip_memory | LSR_EXTREME | up | count_24h_same_direction | 32 | 24 | -16.3 | -30.3 | 0.68 | -0.65 | non-monotonic, N too small to trust | HIGH — genuinely new (positioning) | DATA_LIMITED |
| W7-040 | positioning_flip_memory | LSR_EXTREME | up | time_since_prev | 35 | 24 | -5.6 | -19.6 | 0.88 | -0.22 | no consistent sign | HIGH | DATA_LIMITED |
| W7-041 | positioning_flip_memory | LSR_EXTREME | down | count_24h_same_direction | 22 | 18 | -8.1 | -22.1 | 0.88 | -0.17 | no consistent sign | HIGH | DATA_LIMITED |
| W7-042 | positioning_flip_memory | LSR_EXTREME | down | time_since_prev | 30 | 22 | -12.0 | -26.0 | 0.81 | -0.30 | no consistent sign | HIGH | DATA_LIMITED |

Full bucket-level detail (148 rows, all buckets not just baseline/extreme) is preserved in
`results_table.csv` in the W7 scratch dir
(`/tmp/claude-1000/-home-qbee-futur/a0e00e24-e75f-4382-80ba-28c16b0aba06/scratchpad/round3/w7/`),
alongside all source scripts (`build_panel.py`, `run_events.py`, `run_analysis.py`, `driver.py`,
`run_extra_dvol.py`, `run_extra_lsr.py`, `run_extra_analysis.py`, `lib.py`).

**TOTAL_MECHANISMS_TESTED: 42** (event-type × memory-feature × direction/pooled combinations,
spanning 7 event types and 6 memory-feature constructions; 148 individual event-history-bucket
observations underlying them).

## 4. Top findings, in prose

**The liq-cascade "repeat matters" shape generalizes to exactly the two event families that are
economically closest to liquidation cascades themselves — and essentially nowhere else.**
OI-down-shocks (forced deleveraging) and CVD-down-shocks (aggressive taker-sell exhaustion) both
show a clean, monotonically-strengthening memory effect: near-zero-to-negative on the first
occurrence, solidly net-positive (statistically significant, PF 1.2-1.3) by the 3rd+ repeat or
in the "hot"/high-cumulative-intensity bucket. But both are asymmetric to the deleveraging/selling
side only (the up/crowded-build and up/aggressive-buy counterparts show no effect), and both are
plausibly the **same underlying mechanism as the original liq-cascade finding** viewed through
different data (OI change and taker flow are physical footprints of the same forced-selling
event that triggers liquidations) — this worker did not have time to run a direct event-level
overlap check against the round-2 `liq_cascade_dataset`, and flags that as the natural next step
before crediting these as independent discoveries rather than corroborations.

**Basis shocks show the same directional shape but the net edge is economically negligible**
(best bucket nets +0.4 to +2.6bps after 14bps costs, despite gross t-stats up to 7.1) — a real,
distinct-mechanism (carry/term-structure) pattern that simply isn't tradeable.

**Funding-rate extremes show NO repeat-strengthens pattern — and on one feature (time_since_prev,
up-direction) show the opposite**: an isolated/fresh funding extreme after a quiet period nets
+9.9bps (t=3.85, N=1,895, genuinely significant), while a funding extreme that has been recently
repeating is flat-to-negative. This says something coherent about market structure — persistent
crowding gets arbitraged away, a fresh dislocation does not — but it directly contradicts rather
than confirms the mandate's hypothesis, and is reported as a DEAD result for "memory strengthens
the edge" (with the reversed finding noted as a curiosity, not chased further).

**Volume shocks show no usable pattern** (either structurally negative regardless of repeat count,
or too noisy/non-monotonic to trust).

**Options implied-vol shocks (Deribit DVOL) are the one genuinely NEW mechanism family (distinct
data source: options market, not futures positioning/flow) that shows the pattern cleanly** — IV
spike (fear) events fade better the more recently/frequently they've repeated, corroborated
across three independent memory features (count, time-since-prev, cumulative intensity) with the
strongest bps magnitudes in the whole study (net +209 to +572bps). This is flagged
PROMISING-WITH-CAVEAT rather than PROMISING outright: N per bucket is only 13-52, spans just
BTC+ETH, and the extreme buckets are almost certainly dominated by a handful of true 2021-2026
crypto-market crises (LUNA, FTX) rather than a stable, frequently-recurring statistical edge — a
7-day-decluster robustness check confirms events aren't literally all one cluster (80/116 and
73/87 survive), but the underlying sample is still thin by any standard.

**Positioning flips (top-account LSR) could not be tested to any useful conclusion** — a genuine
data-coverage gap (positioning history only 7 weeks; fresh forward-return price coverage only for
10/47 symbols) rather than a negative finding about the mechanism. Reported honestly as
DATA_LIMITED across all 4 candidates rather than forced into a DEAD/WEAK verdict the data cannot
support.

**Net read for round 3 synthesis:** the "event memory matters" idea is real but **narrower** than
round 2's framing suggested — it is not a universal property of extreme-event outcomes, it is
specific to forced-deleveraging-adjacent mechanisms (liquidations, OI-down-shocks, aggressive-sell
shocks — plausibly all one family) plus, more speculatively, options-market vol-crash exhaustion
(a different but smaller/thinner-sampled family). It does NOT generalize to funding extremes,
volume shocks, or (on the evidence available) positioning flips.
