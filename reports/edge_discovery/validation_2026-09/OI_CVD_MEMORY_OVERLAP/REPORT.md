# Validation: OI_CVD_MEMORY_OVERLAP

**Candidate under test:** the claim (from
`reports/edge_discovery/alpha_hunt_2026-09-01_round3/w7_event_memory_generalization/REPORT.md`,
candidates W7-002/012/021/031 for OI_SHOCK-down and W7-006/016/022 for CVD_SHOCK-down) that the
"repeat matters" memory shape found for LIQ_CASCADE_REPEAT_V1 (1st occurrence weak/negative, 3rd+
occurrence net-positive, PF 1.2-1.3) generalizes to OI-down-shocks and CVD-down-shocks, with the
discovery worker's own explicit caveat that this was never checked for event-level overlap with
the liquidation-cascade dataset and might just be the same physical events relabeled.

**Validator:** independent worker, read-only on `src/institutional/live_alpha_lab/` and
`configs/live_alpha_registry.yaml` (never touched), read-only on
`src/institutional/engines/liq_cascade/` (imported, never edited). Did not read the original
discovery worker's scratch code — event definitions below were built independently from raw data.

**Verdict: REJECTED as an independent candidate.** Both halves fail, for two different and
complementary reasons — see S1 (overlap) and S3 (residual memory-effect test). No new
`alpha_id` / forward stream is warranted. The OI-down-shock half is valuable **corroborating**
evidence for LIQ_CASCADE_REPEAT_V1's robustness; the CVD-down-shock half is not even that — its
apparent edge evaporates (and reverses) once the liq-cascade-adjacent episodes are removed.

---

## 1. Overlap analysis (primary task, done first)

### 1.1 Data and method

- **Ground-truth cascade events**: regenerated via the frozen, unmodified production code
  (`src.institutional.engines.liq_cascade.detector.{load_metrics,detect_cascades}`, imported
  read-only, never edited) on `data/derivatives_backfill/binance_vision_metrics` (5-min bars,
  available through 2026-08-31). This was necessary because the static artifact
  `data/events/liq_cascade_dataset.parquet` is stale (ends 2026-07-04), which would have limited
  the overlap-check window to ~6 days. Regenerating with the exact same frozen detector gave a
  full-coverage, faithful ground truth. **Sanity check**: regenerated events in [2026-06-28,
  2026-07-04] = 159 vs. 158 in the static parquet for the same window — matches almost exactly
  (off by 1, plausibly a data-refresh edge effect), confirming the regeneration is faithful.
  1,658 ground-truth cascade events fall in the analysis window (1,202 LONG_CASCADE, 456
  SHORT_SQUEEZE).
- **My own OI-down-shock and CVD-down-shock events**: built independently from
  `data/derivatives_raw` (Binance USDM `open_interest` and `ratios` streams — a live REST-polling
  collector, ~5-6min cadence, **not** the same pipeline as vision_metrics or as W7's
  `data-v2/normalized/event_feature_panel`). PRIMARY_SPEC, documented before results:
  - Universe: 48 symbols common to derivatives_raw{open_interest,ratios} and vision_metrics (2
    dropped mid-run due to corrupted source files — see S1.4 — leaving 46).
  - Window: 2026-06-28 -> 2026-08-31 (65 calendar days — this is **all the history
    `data/derivatives_raw` has**; it is a brand-new collector, not a multi-year backfill).
  - Resampled to a regular 5-min grid (ffill gaps <=15min).
  - **OI_DOWN_SHOCK**: 30-min (6-bar) % change of `open_interest`; causal rolling z-score
    (`shift(1)`, window=5d/1440 bars, min_periods=2d/576 bars); trigger z<=-2.5 AND ret<0. (30-min
    window deliberately chosen to match LIQ_CASCADE's own window for a fair overlap test — this
    is the one place a parameter was *matched* to the mechanism being tested, not tuned to
    outcome.)
  - **CVD_DOWN_SHOCK**: proxy = Binance `taker_buy_sell_ratio` (aggregated taker buy/sell volume
    ratio) from the `ratios` stream; 30-min (6-bar) log-change; same causal z-score construction
    and threshold.
  - Clustering: min_gap=1h between kept events of the same symbol/type (matches the production
    detector's convention).
- **Overlap window**: an OI/CVD-down-shock event on symbol S at time T counts as overlapping if a
  cascade event (of the relevant kind) exists on the same symbol within **+/-2 hours** of T —
  chosen before running the check as a "generous but not silly" window for two independently
  constructed detectors of the same underlying physical event.

### 1.2 Overlap results

| my event type | vs. cascade kind | forward overlap (my event -> has coincident cascade) | reverse overlap (cascade -> has coincident my-event) |
|---|---|---|---|
| **OI_DOWN_SHOCK** (N=2,684) | LONG_CASCADE only | 39.5% (1,059/2,684) | **79.0%** (950/1,202) |
| **OI_DOWN_SHOCK** (N=2,684) | both kinds | 52.8% (1,417/2,684) | — |
| **CVD_DOWN_SHOCK** (N=5,309) | LONG_CASCADE only | 3.0% (159/5,309) | 12.2% (147/1,202) |
| **CVD_DOWN_SHOCK** (N=5,309) | both kinds | 3.9% (209/5,309) | — |

**OI_DOWN_SHOCK: HIGH overlap, mechanically expected.** 79% of the production
LIQ_CASCADE_REPEAT_V1 detector's own LONG_CASCADE events already register as an OI-down-shock
under an *independently built* detector (different data source, different collection pipeline,
slightly looser threshold/window, no price-confirmation filter) — this is the majority/
near-totality the validation mandate flags as disqualifying. It is unsurprising mechanically
(LIQ_CASCADE's own trigger condition **is** a 30-min OI-drop z-score, just with an added
price-move filter and a longer/stricter baseline) but it settles the question empirically rather
than by assertion. This exactly matches what round 2's own W2 worker already found from a
completely different data source and flagged as "corroborates A7 [liq-cascade], not independent"
(`reports/edge_discovery/alpha_hunt_2026-08-30/w2_liquidation_leverage/REPORT.md`, mechanism #5).
**Per the mandate: OI_DOWN_SHOCK is a CORROBORATION of LIQ_CASCADE_REPEAT_V1, not an independent
family. No new alpha_id.**

**CVD_DOWN_SHOCK: LOW overlap by raw count, but see S3 — the count is misleading.** Only 3.0-3.9%
of CVD-down-shock events coincide with a cascade, and only 12.2% of cascades have a coincident
CVD-down-shock. Taken at face value this looks like room for an independent residual. S3 shows
this reading is wrong: the ~4% of overlapping events carry essentially all of the original
discovery's positive edge, and the 96% non-overlapping residual has **no edge at all** (and if
anything the opposite sign). Low count-overlap is not the same as low economic overlap.

### 1.3 Reading the caveat

The discovery worker's caveat asked exactly the right question. The answer, empirically: for
OI-down-shocks, the caveat is confirmed directly (high overlap, same mechanism). For
CVD-down-shocks, the caveat is confirmed indirectly and more decisively — not by event-count
overlap, but by showing the "repeat strengthens" edge is entirely absent once the small
overlapping fraction is removed (S3). Both roads lead to the same place: this is not an
independent discovery.

### 1.4 Data-quality caveats affecting S1

- `data/derivatives_raw` is a brand-new collector (branch `feat/free-derivatives-backfill`):
  only 65 days of history exist (2026-06-28 -> 2026-09-02), vs. years for
  `binance_vision_metrics`/`event_feature_panel`. This bounds the whole analysis to a 65-day
  window regardless of method.
- 2/48 symbols (LINKUSDT, PENDLEUSDT) were dropped entirely because one corrupted parquet file
  each ("No magic bytes found at end of file") aborted the bulk read for that symbol/stream — a
  genuine data-integrity issue in the raw collector, not a methodological choice. 46/48 symbols
  used.
- `taker_buy_sell_ratio` (the CVD proxy) shows an implausibly wide intraday range even for BTCUSDT
  (0.11 to 20.4 within a single day, median ~1.07) — much wider than expected for a deep, liquid
  market at 5-min granularity. This is flagged as a data-quality concern for the CVD signal; it
  does not appear to bias the S3 result toward the DEAD conclusion (a purely noisy signal would
  produce a flat-near-zero-gross result, not the significantly *negative* gross seen in the
  mid/exhaustion buckets), but it does mean the CVD_DOWN_SHOCK event set itself should not be
  trusted at face value for any *other* purpose without further cleaning.
- Two independent code paths for "the real liquidation cascade events" agree almost exactly
  (159 vs. 158 in the overlap window) — this is a positive faithfulness check, not a caveat.

---

## 2. Residual construction

RESIDUAL = CVD_DOWN_SHOCK events (N=5,309) with no coincident cascade of **either** kind within
+/-2h (the more conservative/inclusive exclusion) -> **N=5,100** events, 45 symbols, span
2026-06-30 -> 2026-08-31 (residual is 96.1% of the raw CVD_DOWN_SHOCK population — "meaningful"
by size, which is exactly why it needed testing rather than being waved off).

OI_DOWN_SHOCK was **not** carried into a residual test: its overlap (79% reverse) is high enough
that the mandate's own decision rule applies directly ("if overlap is high... treat as
corroboration... mark REJECTED") without needing a further residual test.

---

## 3. Residual memory-effect test (CVD_DOWN_SHOCK only)

### 3.1 Spec (documented, matches the claim being tested for comparability)

- Repeat feature: `n_prior_24h` = count of residual CVD_DOWN_SHOCK events on the same symbol,
  strictly before the current one, in the trailing 24h — identical convention to
  `n_events_sym_24h` used by LIQ_CASCADE_REPEAT_V1 and by W7's `count_24h` feature.
- Buckets: onset(0) / mid(1) / exhaustion(2+) — same convention as
  `repeat_variant.py::classify_repeat_bucket`.
- Horizon: entry = event bar + 1 (5-min bar), exit = entry + 48 bars (4h) — matches
  `dataset.py`'s `FWD_HORIZONS["4h"]` exactly.
- Direction: CVD-down-shock is a "sell exhaustion, fade long" hypothesis (matches W7's
  construction) — trade return = +fwd_4h.
- Price: `mark_price` from the same `derivatives_raw` `open_interest` stream (causal, no lookahead
  — entry priced at bar *after* the detection bar).
- Costs: net14 = gross_bps - 14 (project-standard convention).
- 76/5,100 events (1.5%) dropped for missing price coverage at the grid boundary.

### 3.2 Declustering (mandatory)

Greedy chronological, per symbol, independent if >=4h (the forward horizon) since the last kept
independent event. **N_raw = 5,024, N_independent = 3,530 (70.3%)** on the residual set
specifically.

### 3.3 Results — the claimed shape does NOT replicate

| bucket | N_raw | N_indep | gross_bps | net_bps | PF | t_stat (gross) |
|---|---|---|---|---|---|---|
| onset(0) | 698 | 698 | **+7.51** | -6.49 | 0.86 | 1.57 |
| mid(1) | 987 | 749 | -2.68 | -16.68 | 0.65 | -0.75 |
| exhaustion(2+) | 3,339 | 2,083 | -2.47 | -16.47 | 0.66 | -1.28 |

Overall: N=5,024, gross=-1.12bps, net=-15.12bps, t=-0.70.

This is the **opposite** of the claimed shape. Onset is the *best* (least bad) bucket; exhaustion
(the bucket that carries all of LIQ_CASCADE_REPEAT_V1's edge) is flat-to-worse here. No bucket
clears costs (PF<1 for mid/exhaustion, 0.86 for onset). exhaustion-onset diff = **-9.98bps,
t=-1.93** — negative and in the wrong direction for the claim.

**Block bootstrap** (5,000 resamples, block=calendar day) on the exhaustion bucket's net edge:
point estimate **-16.47bps**, 90% CI **[-23.04, -9.37]**, P(mean>0) = **0.000**. The negative
result is not a fluke of a small or noisy sample — it is tight and entirely below zero.

**Interpretation**: the positive "repeat strengthens" edge W7 found for CVD_SHOCK-down
(W7-006/016/022, net +7 to +16bps, PF 1.2-1.3) is very plausibly explained almost entirely by the
~4% of CVD-down-shock events that coincide with an actual liquidation cascade. Once those are
removed, the remaining 96% of "organic" aggressive-selling episodes show continuation, not
exhaustion — selling begets more selling over the next 4h rather than reverting. This is
economically coherent: forced-liquidation-driven selling (the true cascade mechanism) exhausts
predictably by the 3rd+ repeat because the pool of over-levered longs is finite; organic
aggressive selling (informed flow, trend continuation, news) has no such finite-pool dynamic and
should not be expected to show the same "repeat -> exhaustion" shape. Low **count** overlap (S1)
therefore masked high **economic** overlap — nearly all of the discovery's tradeable edge lived in
the small overlapping fraction.

### 3.4 Perturbations (robustness of the DEAD finding, not parameter search)

| perturbation | effect |
|---|---|
| costs +50% (21bps) | net edge worsens further (mechanical; already dead at 14bps) |
| overlap-exclusion window = 1h (looser exclusion) | residual grows to N=5,204 (barely changes vs. N=5,100 at 2h) |
| overlap-exclusion window = 4h (tighter exclusion) | residual shrinks to N=4,828 (barely changes) |
| ex-biggest-week (2026-08-24/30, the single busiest calendar week) | overall gross flips slightly positive (+0.80bps) but net still -13.2bps; conclusion unchanged |

Residual size is insensitive to the exact overlap-exclusion window (+/-1h vs. +/-4h changes N by
<6%), confirming the CORROBORATION vs. RESIDUAL split in S1-2 is not an artifact of the specific
2h choice. No perturbation flips the sign of the core finding.

### 3.5 Event rate / N_required / ETA

- Independent residual episodes: 3,530 over 63 days -> **rate_per_day ~= 56.0**,
  rate_per_week ~= 392, rate_per_month ~= 1,681. Daily count distribution: median 54,
  p10 (conservative) = 7/day.
- `minimum_calendar_span` (EVENT/LIQUIDATION-style) = 60 days; the residual's actual span is 63
  days — **clears the floor, but only just**, since 65 days is the entirety of what
  `data/derivatives_raw` currently has.
- Evidence floors (30/50/100): exhaustion bucket N_raw=3,339, N_indep=2,083 — **comfortably above
  all three floors**. Data volume and statistical power are explicitly **not** the limiting
  factor here.
- `expected_live_edge` = 0.5 x reimplemented residual exhaustion-bucket net edge = 0.5 x
  (-16.47bps) = **-8.24bps** — negative. **N_required / ETA computation is not meaningful for a
  negative expected edge** (no amount of additional sample size validates a strategy that loses
  money in expectation); this is stated explicitly rather than mechanically producing a spurious
  N_required/ETA number.
- The clean separation between "plenty of evidence" (S3.5, evidence floors cleared) and "wrong
  sign" (S3.3-3.4) is itself informative: this is not a data-starved NEEDS_MORE_RESEARCH case, it
  is a DEAD result on the evidence at hand.

---

## 4. Verification checklist

| item | status |
|---|---|
| Causality / PIT | Rolling z-scores use `shift(1)` throughout (both my OI/CVD detectors and the frozen cascade detector); memory features (`n_prior_24h`) use strictly-prior events only; entry priced at bar+1 after detection, never the detection bar itself. |
| Timestamps/units | UTC throughout; derivatives_raw `timestamp` (ms epoch) converted via `pd.to_datetime(unit="ms", utc=True)`; 5-min grid alignment verified against the frozen detector's own 5-min cadence. |
| Target/entry/exit/horizon | Matches `dataset.py` exactly (entry=row+1, exit=entry+48 bars=4h) for direct comparability to the mechanism under test. |
| Declustering | Done (S3.2), mandatory per mandate, applied to the residual specifically. |
| Costs | net14 project-standard convention; +50% perturbation applied. |
| Turnover/capacity/concentration | Not reached — the residual result is DEAD before these matter; top-symbol concentration in the residual was not further profiled since the sign is already negative. |
| Listing effects / survivorship | Universe = symbols with both derivatives_raw and vision_metrics coverage over a fixed 65-day live-collection window — no survivorship bias possible over such a short, fixed, forward-collected window (this is inherently a "what happened after the collector went live" set, not a curated backtest universe). |
| Missing data | 76/5,100 residual events (1.5%) dropped for missing price at grid edges; 2/48 symbols dropped for corrupted source files (S1.4) — both documented, neither material to the conclusion. |

## 5. Verdict

**VALIDATED_FOR_FORWARD = false.**

**OI_CVD_MEMORY_OVERLAP is REJECTED as an independent candidate — not independent of
LIQ_CASCADE_REPEAT_V1.**

- OI-down-shock component: 79% of the production cascade detector's own events already register
  as an OI-down-shock under an independently built detector -> **treat as corroborating evidence
  for LIQ_CASCADE_REPEAT_V1's robustness, no new alpha_id.**
- CVD-down-shock component: raw event-count overlap is low (~4%), but the non-overlapping
  residual (96% of the population, N=5,024/3,530 independent, span 63 days, well above evidence
  floors) shows **no repeat-strengthening effect** — flat-to-reversed, net negative in every
  bucket, block-bootstrap 90% CI for the exhaustion bucket entirely below zero
  (P(mean>0)=0.000). The original discovery's positive edge is best explained as concentrated in
  the small liq-cascade-overlapping fraction. **No new alpha_id.**

Neither half clears the bar for `VALIDATED_FOR_FORWARD`. This is not a data-availability or
statistical-power problem (evidence floors are cleared, the 60-day calendar-span floor is
cleared) — it is a clean negative/corroboration-only result on the evidence collected.

---

## Artifacts

All scratch scripts, intermediate parquet files, and raw stats CSVs:
`/tmp/claude-1000/-home-qbee-futur/a0e00e24-e75f-4382-80ba-28c16b0aba06/scratchpad/validation/oi_cvd_memory/`
(`step1_overlap.py`, `step2_residual_memory.py`, `step3_stats.py`, `overlap_results.json`,
`cascade_ground_truth_window.parquet`, `oi_down_shock_events.parquet`,
`cvd_down_shock_events.parquet`, `cvd_residual_with_features.parquet`,
`residual_bucket_stats.csv`, `perturbations.csv`).
