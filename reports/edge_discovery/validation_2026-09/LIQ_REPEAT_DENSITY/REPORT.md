# Independent Validation — LIQ_REPEAT_DENSITY

**Candidate**: regime-conditioning of the frozen live alpha `LIQ_CASCADE_REPEAT_V1`
(repeat liquidation-cascade fade) by market-wide "cascade density" — systemic
(multi-symbol) vs isolated (single-symbol) episodes.

**Source claim under test**: `reports/edge_discovery/alpha_hunt_2026-09-01_round3/w4_regime_conditional/REPORT.md`,
test A6: systemic episodes net +39.5bps declustered (t=5.68) vs isolated at
breakeven -1.2bps (t=2.43), 5/6 years positive.

**Validator**: independent worker, no access to the discovery script. Reimplemented
from the economic definition only, using: (1) the frozen spec in
`configs/live_alpha_registry.yaml` (read-only), (2) the frozen production code in
`src/institutional/engines/liq_cascade/detector.py` and `dataset.py` (read-only, for
understanding the causal feature pipeline only — never modified), and (3) the raw
event dataset directly. `src/institutional/live_alpha_lab/` and the registry file
were never written to. All numbers in this report were computed for the first time
by this worker; no code or output from the original A6 discovery run was read.

**Verdict: `VALIDATED_FOR_FORWARD = TRUE`** (with caveats — see §6).

---

## 1. Methodology — independent reimplementation

### 1.1 Raw data

Two candidate raw files exist under `data/events/`:
- `liq_cascade_dataset.parquet` (38,141 rows, 2021-01→2026-07) — the file A6 cites.
- `cascade_dataset.parquet` (39,629 rows, 2020-09→2026-08, 49 symbols) — broader and
  more current; its time range matches the live `LIQ_CASCADE_REPEAT_V1`
  `reports/live_alpha_lab/LIQ_CASCADE_REPEAT_V1/decisions.parquet` (5,668 decisions,
  starting 2020-10-08), which is built from the same detector.

**Chosen primary raw dataset: `cascade_dataset.parquet`** — superset, matches what
production actually replayed. Both files share the same detector/feature pipeline
(`detect_cascades` + `build_event_dataset`), so this choice is a data-freshness
decision, not a methodology fork.

### 1.2 Causality verification (done first, before any return number)

`n_events_sym_24h` (same-symbol repeat count, trailing 24h, strictly causal) was
**independently recomputed from scratch** on raw `(symbol, event_time)` pairs — not
imported from `dataset.py` — and compared row-by-row against the dataset's own
column: **0/39,629 mismatches**. The detector's z-score features are also confirmed
causal by inspection (`shift(1).rolling(...)`, strictly-past window, `min_periods`
warm-up) — read-only, unmodified.

### 1.3 Base signal (reimplements frozen `LIQ_CASCADE_REPEAT_V1`, from spec not code)

`kind == LONG_CASCADE AND n_events_sym_24h >= 2` (3rd+ same-symbol cascade in
trailing 24h = "exhaustion" bucket), `horizon = fwd_4h`, `direction = LONG`,
`cost = 14bps round-trip` (matches the registry's `cost_model`).

### 1.4 Universe-growth / listing-effect restriction

Only `BTCUSDT` exists in the dataset before 2021-12-04; 28 more symbols appear that
month (verified via monthly distinct-symbol counts), growing to 49 by 2024. Before
Dec-2021, cross-symbol density is **mechanically zero** (no other symbol exists to
cascade) — an "isolated" label there would be a data-coverage artifact, not a
market regime. **Restriction applied: `event_time >= 2022-01-01`** (one-month
buffer). This drops 78/5,661 base-signal rows (1.4%). N used = **5,583**.

*Residual caveat*: the universe still grows 28→49 symbols across 2022-2026, so
early-2022 density readings are somewhat suppressed relative to later years purely
by symbol count. This biases **against** finding a systemic/isolated split in the
earliest part of the sample (conservative direction), not toward it — confirmed by
the "ex-2022" perturbation below still being strongly positive for the systemic
bucket.

### 1.5 Own "cascade density" classifier (built from raw data, not reused from the discovery)

Deliberately **not** the dataset's existing `n_events_mktwide_30m` column (which is
what A6 used) — a genuinely separate construction, computed here from raw
`(symbol, event_time, kind)` arrays:

> `DENSITY_60M(t, sym)` = count of **distinct OTHER symbols** with ≥1
> `LONG_CASCADE` event whose `event_time` falls in the **strictly-prior** window
> `(t − 60min, t)`.

Design choices, fixed **before** any return was computed:
- **60-minute window** (vs A6's 30min) — a different anchor, chosen as a plausible
  "cascade episode" timescale.
- **Distinct-symbol count**, not raw event count — avoids one symbol's own event
  train inflating "density."
- **Same-kind only** (`LONG_CASCADE`, not `SHORT_SQUEEZE`) — the economic
  hypothesis is specifically a market-wide *down*-flush; mixing in squeezes (an
  opposite-stress regime) would muddy the mechanism.
- **Split rule**: median split of `DENSITY_60M` over the eligible (≥2022-01-01)
  base-signal population — systemic if `≥ median`, isolated if `< median`. Median
  = **1.0** (distribution is heavily mass-concentrated: 33.8% of base-signal rows
  have `DENSITY_60M == 0`, 19.3% have exactly 1).

This is `PRIMARY_SPEC`, registered before step 3 of the analysis pipeline ran (steps
1-2 only touched causality and the shape of the density distribution, never a
return figure).

---

## 2. Verification checklist

| Item | Result |
|---|---|
| Causality (density uses only strictly-prior info) | **Pass** — verified by construction (`bisect` on sorted timestamps, right edge = event's own `t`, never includes it or future events) and by independent recompute of `n_events_sym_24h` (0 mismatches) |
| PIT | **Pass** — detector's rolling stats are `shift(1)` + causal rolling window; own density measure strictly prior |
| Timestamps/units | **Pass** — UTC throughout; `fwd_4h` is a log-return, converted to bps (`×10,000`), net of 14bps round-trip cost, matching registry's `cost_model` convention |
| Density threshold definition | **Pass but flagged** — median split is legitimate (fixed before results, not tuned to spread), but the metric is not smoothly continuous: 34% mass at exactly 0. A tercile-split perturbation initially produced an **empty isolated bucket** because the 33rd percentile itself equals 0 (`<` vs `≤` boundary bug) — caught during robustness testing (not a data bug, a script bug in this validator's own perturbation code), fixed, documented in §4 |
| Horizon | **Pass** — `fwd_4h`, matches frozen base alpha exactly |
| Declustering | **Pass, and this is the central finding of this validation — see §4** |
| Costs | **Pass** — 14bps primary, 21bps (+50%) perturbation still holds |
| Turnover/capacity | **Pass** — systemic-bucket legs ≈ 2.17/day post-2022 pooled across 49 symbols; small, matches parent alpha's "petite taille/event" capacity note |
| Concentration | **Checked explicitly, not disqualifying — see §4.3** |
| Listing effects | **Handled** (§1.4); residual noted, conservative direction |
| Survivorship | `RNDRUSDT` stops appearing 2024-07 (Render token migration/delisting from this dataset's coverage) — no special handling needed since events, not positions, are the unit; does not distort the aggregate result (RNDR is a minor contributor) |
| Missing data | Monthly event counts 2022-2026 checked — no zero/near-zero months indicating a data outage (min month = Mar-2022 at 303, next-lowest Nov-2023 at 337; both plausible calm-market months, not gaps) |

---

## 3. Primary spec + preregistered perturbations

All buckets below use the **episode-level** (declustered, see §4) mean net return
as the unit — the correct inference unit for this candidate. `t` is a one-sample
t-test on episode-level net bps.

| Spec | Systemic: mean net (t, N_episodes) | Isolated: mean net (t, N_episodes) |
|---|---|---|
| **PRIMARY** (60min, median split, 14bps, 2022-2026) | **+22.12bps (t=3.48, N=1,165)** | −13.35bps (t=−1.80, N=1,322) |
| P1a: window=30min | +21.31bps (t=3.08, N=1,040) | +0.62bps (t=0.10, N=1,459) |
| P1b: window=90min | +27.61bps (t=3.90, N=909) | −6.03bps (t=−0.83, N=1,535) |
| P2: tercile split (top/bottom 3rd, 60min) | +29.03bps (t=3.47, N=607) | −13.35bps (t=−1.80, N=1,322)† |
| P3: ex-2023 (biggest single-year contributor) | +15.36bps (t=2.03, N=912) | −24.24bps (t=−2.91, N=1,033) |
| P4: ex-2022 (earliest year in restricted sample) | +23.14bps (t=3.41, N=1,006) | −4.44bps (t=−0.54, N=1,110) |
| P5: costs +50% (21bps) | +15.12bps (t=2.38, N=1,165) | −20.35bps (t=−2.74, N=1,322) |

† Isolated bucket identical to PRIMARY under P2 because the tercile's lower cut
(33rd pct) coincides exactly with the median (both = `DENSITY_60M ≤ 0`).

**No parameter search was performed.** Every row above is one of the five anchors
named in the validation brief, run once, reported as-is (P3 is in fact the
*weakest* systemic result of the six — kept, not discarded).

**Result: the systemic/isolated split survives every perturbation.** Systemic is
positive in all 6 specs (range +15.1 to +29.0bps, t = 2.03 to 3.90 — every single
one clears the one-sided 5% critical value of 1.645). Isolated is never
meaningfully positive (range −24.2 to +0.62bps) and is negative or indistinguishable
from zero in 5/6 specs. The mechanism reimplements: **direction and existence of
the split are confirmed**, though the *magnitude* is materially smaller than A6's
reported +39.5bps/−1.2bps (see §4 for why — this is the declustering correction).

Block-bootstrap (day-block, 5,000 resamples) on the PRIMARY spec:
- Systemic: mean 22.18bps, 90% CI [11.36, 33.57], 95% CI [9.38, 35.88], **P(mean<0) = 0.0002**.
- Isolated: mean −13.40bps, 90% CI [−25.67, −1.08], 95% CI [−27.56, +1.42], P(mean<0) = 0.962.

Per-year (episode-level, PRIMARY): systemic is positive in **5/5** years
(2022 +15.7, 2023 +46.5, 2024 +27.1, 2025 +3.9, 2026 +14.0 bps) — weak but still
positive in 2025. Isolated is mixed (2022 −60.0, 2023 +25.6, 2024 −23.4,
2025 −19.1, 2026 +3.5) — no consistent sign, consistent with "near breakeven /
no real edge."

---

## 4. Declustering — the central judgment call for this candidate

### 4.1 Why row-level counting overstates N for the systemic bucket

A systemic-density episode is, by construction, a moment when **several symbols
cascade together** — one crash day naturally produces many rows in the systemic
bucket (one per symbol caught in it). Counting each row as an independent
observation inflates N and the t-stat. The original discovery's reported
`N_indep=2,047` (systemic) used only a per-symbol ≥4h decluster (standard
practice for the *base* alpha), which does **not** collapse simultaneous
cross-symbol legs of the same market event into one observation. That is exactly
the trap this validation was asked to check for.

### 4.2 Three-layer decluster applied here (systemic bucket)

| Layer | N | Description |
|---|---|---|
| Raw rows | 3,696 | All systemic-labeled base-signal rows |
| After per-symbol ≥4h decluster | 3,248 | Removes 448 same-symbol overlapping-horizon repeats (same trap as the base alpha) |
| **After cross-symbol episode chain-cluster (≥4h gap, any symbol)** | **1,165** | **The correct inference unit.** Chronologically chains together any two per-symbol-declustered systemic signals less than 4h apart (matching `fwd_4h` horizon = position-overlap window), regardless of symbol. One episode = one observation, valued at the mean net bps across its legs |
| Day-level (extra-conservative, one obs/calendar day) | 798 | Reported per brief's explicit ask for "same-event / same crash day" clusters |

Episode composition: mean 2.79 legs/episode (max 52 — the 2025-10-10/11 broad
alt-market flush, 26 distinct symbols), mean 2.67 distinct symbols/episode (max 34).
798 distinct calendar days host 1,165 episodes (295 days have >1 cluster),
confirming genuine same-day cross-symbol clustering is common and was correctly
worth correcting for.

**Isolated bucket, same treatment, for contrast**: 1,887 raw → 1,785 (per-symbol
decluster, −102) → **1,322 episodes** (mean 1.35 legs/episode, max 10) across 845
days. Isolated episodes barely cluster (mostly single-leg) — a useful internal
consistency check: the bucket defined as "not systemic" behaves like genuinely
separate events, as it should.

**Effect of the correction**: systemic mean net drops from the row-level reference
of +30.55bps (t=4.61, N=3,696 — the number closest to what a naive per-symbol-only
decluster would report) to +22.12bps (t=3.48, N=1,165) once cross-symbol episode
clustering is applied, and to +14.03bps (t=2.09, N=798) under the most conservative
day-level treatment. **The finding survives all three treatments** at conventional
significance, but the correct (episode-level) number is materially smaller and
less significant than A6's reported +39.5bps/t=5.68 — consistent with A6 having
used a shallower decluster.

### 4.3 Concentration check (is this 1-2 crash days?)

- Top-2 episodes: 14.6% of total systemic net-bps sum. Top-5: 28.9%.
- Top-2 **calendar days**: 23.4% of total. Top-5 days: 36.8%.
- 54.1% of the 1,165 episodes are individually net-positive (broad-based, not a
  small number of outlier wins carrying an otherwise-flat population).

**Not disqualifying** — there is real fat-tailedness (a few single-leg altcoin
cascades return >1,000bps, e.g. WLDUSDT 2025-09-08 +2,320bps, ARUSDT 2022-06-30
+1,246bps), but no 1-2 day/episode dominance of the aggregate result.

---

## 5. Event rate, N_required, ETA

Unit: **independent systemic-density episode** (post cross-symbol chain-cluster,
§4.2), PRIMARY spec.

| Window | N episodes | per day | per week | per month |
|---|---|---|---|---|
| Full history (2022-01→now) | 1,165 | 0.685 | 4.80 | 20.6 |
| Last 2y | 568 | 0.778 | 5.45 | 23.3 |
| Last 1y | 283 | 0.775 | 5.43 | 23.3 |
| Last 6m | 151 | 0.830 | 5.81 | 24.9 |

**Rate is stable-to-slightly-increasing**, not declining — driven by the growing
symbol universe (28→49), not a shrinking edge. `conservative_event_rate` = **0.775
episodes/day** (last 1y, the lowest of the three recent windows).

`expected_live_edge = 0.5 × 22.12bps = 11.06bps` (half the reimplemented
episode-level systemic net edge).

**N_required_statistical** (block-bootstrap, day-block resample, one-sided
α=5%, power=80%): the bootstrap-implied effective per-episode std is **229.1bps**
(bootstrap SE of the mean × √N = 6.71 × √1,165), close to the naive i.i.d. std of
217.1bps — within-day correlation adds only modestly to the naive estimate.
Cohen's d = 11.06 / 229.1 = **0.048**. `N_required = ((1.645+0.842)/0.048)^2 ≈ 2,654`
episodes.

*Sensitivity*: using a MAD-based robust std (152.7bps, less sensitive to the fat
tail) instead gives N_required ≈ 1,179 — roughly half. Reported here as a
sensitivity note; **2,654 is the primary, more conservative figure** since it
comes from the block-bootstrap itself as instructed.

- `minimum_calendar_span` = 60 days (EVENT/LIQUIDATION-style floor, per brief).
- `ETA_from_event_count` = 2,654 / 0.775 ≈ **3,423 days ≈ 9.4 years**.
- `VALIDATION_ETA` = max(3,423, 60) = **3,423 days**.
- `ETA_P50` (using last-2y rate 0.778/day) ≈ 3,411 days ≈ **9.34 years**.
- `ETA_CONSERVATIVE` ≈ **3,423 days ≈ 9.38 years** (P50 and conservative nearly
  coincide — the rate is stable; the long ETA is driven by the *noise* of
  individual episode returns relative to the assumed half-edge, not by event
  scarcity).

**Evidence floors** (calendar days to accumulate N forward episodes at
`conservative_event_rate`):

| Floor N | Days | ~Months |
|---|---|---|
| 30 | 38.7 | 1.3 |
| 50 | 64.5 | 2.1 |
| 100 | 129.0 | 4.3 |

---

## 6. Verdict

### `VALIDATED_FOR_FORWARD = TRUE`

**Basis**: independent reimplementation — different raw file, different density
metric (own 60min/distinct-symbol/same-kind construction vs the original's
30min/all-kind/raw-count), stricter episode-level declustering, five preregistered
perturbations, block bootstrap — all confirm that systemic-density repeat cascades
genuinely differ from isolated ones: causal, PIT-valid, credibly costed, positive
net expectation specific to the systemic bucket, no leakage, no blocking bug (the
one bug found was in this validator's own perturbation script, caught and fixed
before being reported), no disqualifying concentration, 5/5 years positive,
capacity-compatible (small legs/day), economically coherent (market-wide flushes
exhaust forced sellers faster than idiosyncratic single-name repeats), and —
critically — the mechanism survives the correct EPISODE-level declustering that the
original discovery likely did not apply (its `N_indep=2,047` looks row/per-symbol
based, not cross-symbol-episode based).

**Caveats, both material**:
1. **Magnitude, not just decimals, differs from the discovery claim.** Correctly
   declustered: systemic +22.1bps (t=3.48, N=1,165 episodes), isolated −13.4bps
   (t=−1.80, N=1,322 episodes) — roughly half the discovery's point estimates and
   t-stats. The *existence and direction* of the effect reproduces; the *size*
   claimed by A6 (+39.5bps/t=5.68) does not, because of the declustering
   correction in §4.
2. **Full standalone statistical reconfirmation is a ~9.4-year exercise**
   (N_required ≈ 2,654 fresh episodes ÷ ~0.775/day), driven by high per-episode
   variance (fat right/left tails from violent altcoin cascades) relative to a
   modest assumed forward edge (half of 22bps), not by a scarce event rate (which
   is actually stable/growing). Recommend **not** treating this as a standalone
   N_required gate: since this candidate is a conditioning layer on an
   **already-shadow-live** alpha (`LIQ_CASCADE_REPEAT_V1`, `operational_status:
   SIGNAL_SHADOW` since 2026-08-31), the practical path is to fold the systemic/
   isolated split into that alpha's existing accumulating decision stream (as a
   sizing/filter tag) rather than starting a fresh, separate N_required clock —
   and use the evidence floors (30/50/100 forward systemic episodes, reachable in
   ~1.3/2.1/4.3 months at the conservative rate) as near-term checkpoints.

**Not recommended**: launching this as a brand-new standalone alpha with its own
freeze/live-start clock and a naive expectation of statistical confirmation within
the project's usual multi-month horizon — the tail-driven variance makes that
unrealistic at the conventional 80%-power bar.

---

## Appendix: files

- Scratch scripts (not committed, for reproducibility record only):
  `/tmp/claude-1000/-home-qbee-futur/a0e00e24-e75f-4382-80ba-28c16b0aba06/scratchpad/validation/liq_density/step{1..5}_*.py`
- `step1_verify_causality.py` — independent recompute of `n_events_sym_24h`, 0 mismatches
- `step2_density_and_returns.py` — own `DENSITY_60M/30M/90M` construction from raw arrays
- `step3_primary_analysis.py` — PRIMARY_SPEC: base signal, median split, per-symbol +
  episode-level declustering, per-year/concentration breakdown
- `step4_bootstrap_and_daylevel.py` — day-level decluster, block bootstrap (day-block, 5,000 draws)
- `step5_perturbations.py` — the 5 preregistered anchor perturbations (P1a/P1b/P2/P3/P4/P5)
