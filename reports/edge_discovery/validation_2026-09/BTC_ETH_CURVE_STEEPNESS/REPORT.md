# BTC_ETH_CURVE_STEEPNESS — Independent Validation Report

**Validator:** independent worker, Alpha Validation Factory, 2026-09-02
**Candidate origin:** `reports/edge_discovery/alpha_hunt_2026-09-01_round3/w3_relative_value/REPORT.md`,
family `quarterly_curve_steepness_cross_asset`, row `D-CURVESHAPE-BTCvsETH-PAIR-7D` (claimed
+77.8bps net, t=1.94, PF=1.58, 4/5 years positive, N=157). Claim under test: a pair trade on the
DIFFERENCE between BTC and ETH quarterly-curve steepness works, while the same report explicitly
states all 8 tested **single-asset** curve-steepness variants (BTC/ETH x 7D/14D x mom/fade) were
DEAD -- i.e. the edge is claimed to exist *only* in the cross-asset comparison.

**Scope discipline:** the discovery worker's `build_curve.py` / `engine.py` / `battery.py` were
never read or copied. Everything below was built from scratch against
`data/derivatives_backfill/binance_vision_quarterly/` and
`data/derivatives_backfill/um_klines_1d/`. `configs/live_alpha_registry.yaml` was **read only**
(the `FUNDING_BASIS_DISAGREEMENT_V2` entry, for economic context on the adjacent single-asset
basis alpha and its cost/near-dte-floor conventions) -- never modified, and
`src/institutional/live_alpha_lab/` was never touched.

All scratch code lives in
`/tmp/claude-1000/-home-qbee-futur/a0e00e24-e75f-4382-80ba-28c16b0aba06/scratchpad/validation/btc_eth_steepness/`
(`build_curve.py`, `engine.py`, `run_all.py`, `run_all_restricted.py`) -- not part of this
deliverable, kept for traceability only.

---

## 1. Methodology (reimplemented independently)

**Steepness construction chosen (PRIMARY_SPEC, decided before any result was viewed).** The task
brief offered two natural constructions: "quarterly-future price minus perp price" or "near-quarter
minus far-quarter". I chose **near-quarter minus far-quarter**, because it needs only the quarterly
data itself (no perp join for the signal, minimizing extra PIT/data-source risk) and is the more
literal reading of "curve steepness":

```
near(t)  = quarterly contract with smallest positive days-to-expiry (DTE) at date t
far(t)   = quarterly contract with 2nd-smallest positive DTE at date t   ("front"/"back" pair)
steepness_bps(t) = 1e4 * ln(far_price(t)/near_price(t)) * 365.25 / (far_dte(t) - near_dte(t))
```
i.e. the annualized log-slope implied by the near->far curve segment, in bps/year. A `near_dte >= 7`
floor drops expiry-week noise (this floor already exists in this repo's live registry for the
adjacent single-asset `FUNDING_BASIS_DISAGREEMENT_V2` alpha -- reused as a documented, pre-existing
convention, not invented for this candidate).

**Pair-trade interpretation.** The task brief's own language -- "long the cheap curve's *exposure*"
and "2-legs-per-side execution, so costs are roughly double a single-leg trade" -- points at trading
the two assets' own **perp exposure** (BTC vs ETH, 2 legs total, literally double a 1-leg trade),
timed by the curve-steepness differential as signal, rather than a 4-leg calendar-spread-vs-
calendar-spread execution (which would not be described as merely "double"). PRIMARY_SPEC therefore
trades `um_klines_1d` BTC/ETH perp forward returns; the quarterly curve is signal-only.

```
spread_bps(t) = steepness_BTC(t) - steepness_ETH(t)
z(t)          = causal rolling z-score of spread_bps, window W=60 valid observations,
                min_periods=W (no partial-window leakage), using only data through t
Entry (episode start) at t if |z(t)| >= Z_ENTRY=1.5 and no other episode of this series is open
Direction (PRIMARY = continuation, matching the claim under test):
   z(t) >= +1.5  -> long BTC perp / short ETH perp
   z(t) <= -1.5  -> long ETH perp / short BTC perp
Entry lag = 1 day (signal known at close(t), position at close(t+1))
Horizon h = 7 calendar days -> exit at close(t+1+h)
Return = position_sign * (ln(BTC_exit/BTC_entry) - ln(ETH_exit/ETH_entry))
```

**Declustering -- episode-based, as directed by the mission brief for RV/spread-episode signals**
(not the discovery report's own apparent methodology, which describes an always-in-market
non-overlapping weekly grid for its whole 67-candidate battery -- see SS3 diagnostic run for that
comparison). Once an episode opens, no new episode of the *same* series (cross-asset, or
single-asset BTC-only, or single-asset ETH-only) can open until the current one's exit date has
passed.

**Costs.** Cross-asset (2-leg) trade: **28bps round-trip baseline** (= 2x this repo's standing
14bps single-leg round-trip convention: 5bps taker + 2bps slippage, doubled for entry+exit, doubled
again for the second leg), **42bps stress** (+50%). Single-asset (1-leg) variants: 14bps baseline,
matching a fair, consistent per-leg comparison for the differential check in SS3.

**Data-quality finding used to set the analysis window (decided from data structure, not from any
result).** Checking near/far contract overlap by date revealed that **before 2023-08-18, the two
quarterly contracts almost never traded simultaneously** -- Binance's quarterly listing window was
only ~90-100 days pre-2023 (vs. ~183 days from late 2023 onward), so 2021-2022 has only 8 scattered
single-day roll-transition snapshots (26/332 dates in 2021, 21/365 in 2022 with >=2 active
contracts), not genuine continuous curve data. Using those isolated points inside a rolling
60-observation z-window would silently blend unrelated market regimes separated by months/years.
PRIMARY_SPEC therefore restricts the panel to `date >= 2023-08-18` (continuous daily near+far
coverage from that date through 2026-08-31). This barely changes the numbers (the sparse pre-2023
rows are too few to matter much) but is the economically correct thing to do and is reported as
"RESTRICTED" below.

---

## 2. Verification checklist

| Check | Result |
|---|---|
| **Causality** | `z(t)` uses `rolling(window=60, min_periods=60)` ending at `t` inclusive; entry is lagged 1 day past the trigger date. Verified by construction. |
| **PIT** | No forward-filled or backward-filled prices used for signal construction; `price_at_or_after` only looks forward from the *target* execution date to bridge small data gaps (never backward), and is applied identically to entry and exit. |
| **Timestamps / contract rolls** | Near/far selection is **not** driven by any hardcoded roll calendar -- purely "smallest two positive-DTE contracts on date t", re-derived fresh every date. `gap_days` (far_dte - near_dte) is a clean, near-constant 91 days (min 84, from `describe()`), and BTC/ETH `near_expiry`/`far_expiry` match on **100% of dates** (0 mismatches across 1,046 rows) -- confirms the roll structure is handled correctly and BTC/ETH are always compared on the identical expiry pair. |
| **Units** | `steepness_bps` is an annualized log-slope in bps/year (mean ~750-800bps ~ 7.5-8% annualized contango over the sample, economically sane for the mostly-bull 2021-2026 period); not a raw price difference. |
| **Target/entry/exit/horizon** | Documented above: entry lag 1d, horizon 7 calendar days, non-overlapping episodes. |
| **Declustering** | Applied -- see SS4. `N_raw`=193 raw threshold-crossing days vs `N_independent`=57 episodes (RESTRICTED primary), a ~3.4x inflation if daily observations were naively counted. |
| **Costs** | 28bps round-trip (2-leg) baseline, 42bps stress, explicitly justified as double a 14bps single-leg reference. |
| **Turnover** | Full round-trip (both legs, entry+exit) per episode by construction; already priced into the per-episode cost. |
| **Capacity** | Execution legs (BTC/ETH perp) are highly liquid -- not a binding constraint on the traded instrument. **However**, the *signal* is derived from the quarterly-futures market, which has **no volume field in this dataset** (`binance_vision_quarterly` parquets carry only `close`, no volume) -- signal-side liquidity/robustness is genuinely **UNMEASURED** here, consistent with the source report's own "N/A" capacity notation for this family. Flagged as a real caveat, not resolved. |
| **Concentration** | **Severe.** RESTRICTED primary spec: 2024 alone contributes +3,833bps of cumulative episode PnL (16/57 episodes); 2023+2025+2026 combined (41/57 episodes, 72% of the sample) sum to **-2,893bps**. Excluding 2024 (`ex_biggest_year` perturbation) flips the whole result to net -74.2bps, 0/3 years positive. The entire positive headline number is a single-year artifact. |
| **Listing effects** | Handled via the 2023-08-18 restriction (see SS1); confirmed the roll/pairing itself is clean (0 expiry mismatches) independent of the coverage-window issue. |
| **Survivorship** | BTC and ETH have symmetric contract counts (24 quarterly contracts each, 2021-03 through 2026-12 expiries) with 0 expiry-pairing mismatches -- no evidence of a missing/gapped contract on either side. |
| **Missing data** | `um_klines_1d` perp data (used for the traded legs' forward returns) is **stale, ending 2026-06-30** -- about 2 months behind today (2026-09-02). This truncates the most recent event-rate window (last episode trigger usable = 2026-06-17) and means "last 6 months" in SS5 is itself incomplete relative to the true present. Disclosed, not silently worked around. |
| **Data sanity spot-check** | The single worst single-asset ETH episode (-3,168bps, short ETH momentum, 2025-05-05->05-12) checked against raw `ETHUSDT_1d.parquet`: ETH genuinely rallied $1,819->$2,494 (+37%) that week -- a real, documented event, not a data glitch. |

---

## 3. Primary spec + preregistered perturbations, and the crux differential check

**PRIMARY_SPEC** fixed before any result was inspected: near-far annualized log-slope, W=60,
Z_ENTRY=1.5, h=7d, continuation direction, 28bps cost, RESTRICTED to `date>=2023-08-18`.

| Spec | N_raw | N_indep | net bps | t-stat | PF | years+/total |
|---|---|---|---|---|---|---|
| **PRIMARY** cross-asset, continuation | 193 | 57 | **+17.1** | **0.28** | 1.10 | 1/4 |
| Companion: cross-asset, **fade** | 193 | 57 | -73.1 | -1.19 | 0.66 | 2/4 |
| Perturbation: z_window=45 (vs 60) | 201 | 64 | -15.1 | -0.24 | 0.93 | 2/4 |
| Perturbation: z_window=75 (vs 60) | 178 | 54 | +3.1 | 0.05 | 1.02 | 1/4 |
| Perturbation: z_entry=1.0 (vs 1.5) | 379 | 93 | -73.0 | -1.24 | 0.67 | 0/4 |
| Perturbation: z_entry=2.0 (vs 1.5) | 89 | 33 | -11.1 | -0.11 | 0.95 | 1/4 |
| Perturbation: ex-biggest-year (2024) | -- | 39 | -74.2 | -1.48 | 0.57 | 0/3 |
| Perturbation: cost +50% (42bps) | 193 | 57 | +3.1 | 0.05 | 1.02 | 1/4 |
| **Diagnostic** (not primary): always-in-market non-overlapping 7D grid, sign(z), no threshold -- the framing that appears to match the *general* methodology the source report describes for its whole 67-candidate battery | -- | 139 | **-97.2** | **-1.93** | 0.62 | **0/4** |

**No perturbation reproduces the discovery's claimed +77.8bps/t=1.94/PF=1.58.** Every single
neighboring parameter choice -- window, threshold, ex-2024, cost stress -- is flat-to-negative;
only the exact PRIMARY_SPEC parameters land (marginally, insignificantly) positive. The
always-in-market grid diagnostic, which more closely mirrors how the source report describes its
general backtest methodology, is **significantly negative** (t=-1.93), the opposite sign of the
claim.

**Crux differential check -- the central claim under test.** The discovery report's core finding is
that all 8 single-asset curve-steepness variants (BTC/ETH x 7D/14D x mom/fade) are DEAD while only
the cross-asset comparison works. Reimplementing the *same construction family* (own-history
z-score of a single asset's steepness, momentum/fade, 7D horizon, 14bps single-leg cost):

| Spec | N_raw | N_indep | net bps | t-stat | PF | years+/total |
|---|---|---|---|---|---|---|
| single-asset **BTC**, continuation | 294 | 61 | +90.3 | 1.18 | 1.53 | 3/4 |
| single-asset BTC, fade | 294 | 61 | -118.3 | -1.55 | 0.57 | 0/4 |
| single-asset **ETH**, continuation | 317 | 61 | **+259.6** | **2.35 (p=0.011)** | **2.26** | **4/4** |
| single-asset ETH, fade | 317 | 61 | -287.6 | -2.60 | 0.41 | 0/4 |

**This directly contradicts the discovery's central claim.** Under an independent, best-faith
reimplementation, single-asset **ETH** curve-steepness momentum is the single strongest,
most-significant, most-stable result in the entire battery (t=2.35, PF=2.26, 4/4 years positive) --
not dead. Single-asset BTC momentum is also directionally positive (though not individually
significant). Meanwhile the cross-asset PRIMARY_SPEC is weak/insignificant (t=0.28) and the closer
grid-diagnostic replication is significantly *negative*. The differential the discovery reports
(single-asset dead, cross-asset alone works) **reverses** rather than merely fails to replicate.

I did not tune the steepness construction to chase this differential either direction -- this is
the same near-far construction used throughout, applied identically to the single-asset and
cross-asset cuts.

---

## 4. Declustering detail

- **N_raw** (RESTRICTED primary, raw `|z|>=1.5` threshold-crossing days) = **193**.
- **N_independent** (non-overlapping episodes, no two holding periods overlap by construction) =
  **57**, spanning 2023-10-29 -> 2026-06-17.
- Decluster ratio ~3.4x -- i.e. the same dislocation persists above threshold for ~3-4 calendar days
  on average before either reverting below threshold or exiting the horizon-blocked window; this is
  expected for a 60-observation-smoothed z-score of a moderately mean-reverting spread.
- **Regime concentration** (SS2/SS3): 16/57 episodes (28%) fall in 2024, which alone contributes
  +3,833bps of cumulative PnL versus -2,893bps for the other 41/57 episodes (72%) pooled across
  2023/2025/2026. This is the dominant finding of this validation -- a single calendar year, not a
  persistent structural relationship, explains the entire positive headline number.
- Direction balance is not degenerate (28 long-BTC/short-ETH vs. 27 long-ETH/short-BTC episodes),
  so the concentration is temporal (one year), not directional.

---

## 5. Event rate / N_required / ETA

- Full-history rate (RESTRICTED primary episodes, 2023-10-29->2026-06-17, ~2.63y span):
  **0.400/week, 1.74/month, 20.9/year**.
- Sub-window rates (last usable trigger = 2026-06-17, itself truncated by the `um_klines_1d` staleness
  noted in SS2 -- true "last 6m" coverage is incomplete):
  - last 2y: n=43, 0.412/week
  - last 1y: n=25, 0.479/week
  - last 6m: n=15, 0.577/week (likely inflated by the truncated denominator, not a real acceleration)
  - **rate_stable: roughly yes** -- no decay found; if anything a mild uptick, though the last-6m
    figure should be discounted given the data staleness caveat.
- `conservative_event_rate` = **~19-21 episodes/year** (full-history / last-2y range, the lower,
  more defensible end rather than the possibly-truncation-inflated last-6m figure).
- `expected_live_edge` = 0.5 x 17.1bps = **+8.5bps net** -- reported per the mission template, but
  this haircut is close to moot: the underlying +17.1bps PRIMARY figure is itself not statistically
  distinguishable from zero (t=0.28) and is a single-year artifact (SS3/SS4), so a "50% haircut of a
  non-finding" is not a meaningful operating number.
- **N_required_statistical**, via block-bootstrap (block size 4, resampling the 55 RESTRICTED
  primary episode net returns with replacement, simulating power of a one-sided alpha=5% t-test across
  a grid of N):

  | N | 57 | 100 | 200 | 400 | 800 | 1,500 | 3,000 | 5,000 | 8,000 |
  |---|---|---|---|---|---|---|---|---|---|
  | power | 0.09 | 0.13 | 0.15 | 0.23 | 0.41 | 0.56 | **0.81** | 0.95 | 0.99 |

  -> **N_required_statistical (block-bootstrap) ~ 2,800-3,000 independent episodes.**
  Cross-checked against the analytic normal approximation (`((z_0.05+z_0.80)/(mean/std))^2` with
  effect size mean/std = 17.1/454.1 = 0.0377): **N_required ~ 4,361** -- same order of magnitude,
  confirming the bootstrap result is not an artifact of the block-resampling method.
- `minimum_calendar_span` = 6 months per the mission's RARE-RELATIVE-VALUE floor -- **explicitly
  insufficient here.** At the observed conservative event rate (~20/year), even the low-end
  N_required (~2,800) implies:
  - `ETA_from_event_count` ~ 2,800 / 20 ~ **140 years** (bootstrap-based)
  - `ETA_from_event_count` ~ 4,361 / 20 ~ **218 years** (analytic-based)
- `VALIDATION_ETA_P50` ~ **140 years**, `VALIDATION_ETA_CONSERVATIVE` ~ **218 years** -- both
  `max(ETA_from_event_count, 182 days)`, dominated entirely by the event-count term. This is
  categorically infeasible; no realistic forward-monitoring window resolves it.
- **Evidence floors**: N_independent=57 clears the 30 and 50 floors but not 100 -- moot regardless,
  since the statistically required N (thousands) is 30-80x the entire floor table.

**Why N_required is astronomically large:** the observed effect size is tiny relative to episode
noise (mean 17.1bps vs. std 454bps per 7-day episode -- a per-episode Sharpe-like ratio of 0.038).
This is not "a real but hard-to-detect edge" so much as a symptom of an effect that is statistically
indistinguishable from noise at any achievable sample size for this event rate.

---

## 6. Verdict

**VALIDATED_FOR_FORWARD = FALSE -- REJECTED.**

Reasoning against the mission's explicit gate list:
- Reimplementation confirms the mechanism survives / real: **FAIL.** PRIMARY_SPEC net (+17.1bps,
  t=0.28) is not distinguishable from zero; a diagnostic closer to the discovery's likely
  methodology (always-in-market grid) is significantly *negative* (t=-1.93, 0/4 years positive).
- Confirms the crux differential (single-asset dead, cross-asset alone works): **FAIL, and
  reverses.** Single-asset ETH momentum (t=2.35, PF=2.26, 4/4 years) is the strongest, most stable
  result in the entire battery under an identically-constructed independent reimplementation of the
  same 8-variant family the discovery calls DEAD. Single-asset BTC momentum is also
  directionally positive. This is the single most decision-relevant finding of this validation.
- Causal / PIT / no leakage: **PASS** (verified by construction, SS2).
- Contract-roll handling: **PASS** (0 expiry-pairing mismatches, clean ~91-day gap throughout,
  SS1/SS2).
- Credible 2-leg costs: **PASS** as a cost *model* (28bps baseline / 42bps stress, correctly
  doubled vs. a single-leg reference) -- but the edge does not clear even baseline costs robustly:
  several perturbations and the ex-2024 cut go negative already at 28bps, before any stress.
- Positive net expectation, stable across reasonable perturbations: **FAIL.** Every single
  preregistered perturbation (z_window, z_entry in both directions, ex-biggest-year, cost stress)
  is flat-to-negative; only the exact PRIMARY_SPEC parameters land marginally positive. This is the
  signature of a non-robust, over-fit-to-one-configuration result, not a stable structural edge.
- No hidden concentration: **FAIL.** A single year (2024, 28% of episodes) explains the entire
  positive headline; the other 72% of episodes are net negative in aggregate (SS3/SS4).
- Capacity compatible: **PARTIAL / caveat.** Traded legs (BTC/ETH perp) are highly liquid and not
  a binding constraint, but the underlying signal's own market (quarterly futures) has no volume
  data available in this dataset to confirm robustness at size -- genuinely unmeasured, not passed.
- Declustering applied: **PASS** (episode-based, SS4).
- Economically understandable: mechanism itself (relative curve steepness as a directional signal)
  is plausible in principle, but the specific empirical result does not support it as currently
  measured.

**Bottom line.** An independent, best-faith reimplementation from the stated economic definition --
built without reading the discovery worker's code, using a defensible construction the task brief
itself offered as an option, correct causal/PIT handling, correct roll-structure handling (verified
0 mismatches), and a fair 2-leg cost model -- does **not** reproduce the claimed cross-asset edge,
and specifically **reverses** the claim's central differential (cross-asset-only) finding. Combined
with extreme specification sensitivity (every neighboring perturbation flips negative) and an
N_required for statistical resolution that is 100+ years away at the observed event rate, this is a
clean REJECTED rather than a NEEDS_MORE_RESEARCH: more data collected at the natural event rate of
this construction will not resolve the question within any operationally relevant horizon, and the
crux claim already reverses under reasonable alternative implementation choices rather than merely
being statistically ambiguous.

**Recommendation:** do not forward `BTC_ETH_CURVE_STEEPNESS` to shadow/live. If this family is
revisited, the highest-value next step is not more history but reconciling *why* the single-asset
ETH momentum construction is strong here (t=2.35) yet reported DEAD in the source discovery -- that
discrepancy (likely driven by a different exact steepness definition, e.g. perp-vs-quarterly basis
rather than near-far calendar slope, or a different z-window/threshold) is a more promising thread
than the cross-asset pair trade itself, but would need its own from-scratch validation, not a
parameter search inside this one.
