# LIQ_REPEAT_VOL_GATE — Independent Validation Report

**Worker:** independent validation (Track B), 2026-09-02
**Candidate under test:** discovery report `reports/edge_discovery/alpha_hunt_2026-09-01_round3/w5_meta_signals/REPORT.md`, test T1.1 — "ENABLE `LIQ_CASCADE_REPEAT_V1` (already live, `SIGNAL_SHADOW`) only when BTC's 24h realized volatility is elevated."
**Constraint compliance:** no discovery script read or copied. `src/institutional/live_alpha_lab/` and `configs/live_alpha_registry.yaml` untouched (registry read-only for spec). `src/institutional/engines/liq_cascade/` read-only, not modified. No parameter search for the delta-maximizing threshold.

---

## 1. Methodology

### 1.1 Base signal — source of truth

Used the **actual persisted decisions** of the frozen, `SIGNAL_SHADOW` live alpha:
`reports/live_alpha_lab/LIQ_CASCADE_REPEAT_V1/decisions.parquet` (5,668 rows, all
`kind==LONG_CASCADE`, `repeat_bucket==exhaustion`, `direction==LONG`, `horizon==fwd_4h`,
49 symbols, `event_time` spanning 2020-10-08 -> 2026-08-31). This is the frozen spec's own
REPLAY history, not a re-derivation -- it is exactly what `LIQ_CASCADE_REPEAT_V1` has
actually decided at every point in time, per `freeze_spec.json`.

PnL was **computed independently** here (not imported from `dataset.py`'s own `fwd_4h`
column): for each decision, entry = the implied price (`sum_open_interest_value /
sum_open_interest`, from `data/derivatives_backfill/binance_vision_metrics/{symbol}
_metrics_5m.parquet`) at the first 5-min bar after `event_time`; exit = entry + 4h
(240 min, matching the frozen `horizon: fwd_4h`). `net_bps = (exp(logret)-1)*1e4 - 14`
(14bps round-trip taker, matches the registry's `cost_model` exactly). 5,546/5,668
decisions had a computable forward return (122 dropped: too close to the data's end,
2026-08 events without a full 4h of forward data yet -- correctly excluded, not
zero-filled). 0 dropped for missing price data.

Sanity check: my reimplemented WITHOUT-arm economics (mean +18.83bps, PF 1.196, t=3.0,
N=4,755 over the eligible window) land in the same ballpark as the registry's own stated
`expected_net_bps: 27.1 (full-sample)` -- the gap is expected (my population excludes
2020-2021-09, see 1.2) and confirms the reimplementation pipeline is not obviously broken.

### 1.2 Vol gate — independent reimplementation

Built entirely from BTCUSDT's own 5-min metrics parquet (same price series the base
engine trades -- the fully-current source through 2026-08-31; the alternative real-OHLC
sources checked (`data/derivatives_backfill/bybit/perp_klines_1h/BTCUSDT.parquet` to
2026-07-21, `data/derivatives_backfill/um_klines_1d/BTCUSDT_1d.parquet` to 2026-06-30,
`data/derivatives/BTCUSDT_1h.parquet` to 2026-06-28) are all stale relative to the base
population's 2026-08-31 end and were rejected for that reason). **Not** the discovery
script; a fresh implementation (`build_gate.py`, `analyze.py`, `anchors.py`,
`eta_dual.py` -- see scratch folder).

**PRIMARY_SPEC (preregistered before looking at any WITH/WITHOUT number):**
- `vol_24h` = rolling std of BTC 5-min log returns over a trailing **288-bar (24h)**
  window (min_periods=288, i.e. no partial-window estimates), scaled by `sqrt(288)`.
  Causal by construction (bar *t*'s value uses only bars <= *t*).
- Threshold = the **causal 730-day (2y) rolling median** (50th percentile) of a
  **daily-resampled** `vol_24h` series, with an explicit `shift(1)` before the rolling
  window (so day *D*'s threshold uses only days strictly < *D*) and `min_periods=365`
  (need >=1y of prior daily vol observations before the gate is "eligible").
- `GATE = vol_24h(event_time) > threshold(day before event_time)`, merged onto each
  event via `merge_asof(direction='backward')`.
- This design needs **no train/test split**: every single decision's gate value uses
  only strictly-prior BTC volatility observations, so there is no fitting step at all
  (stronger PIT discipline than the discovery report's train-fit/test-eval split). A
  secondary time-split stability check is still reported (section 5) as corroboration.

Eligibility starts 2021-09-22 (first day with 365 days of prior daily vol history);
5,524/5,546 decisions are eligible, 22 pre-eligibility decisions excluded from **both**
arms (fair population).

### 1.3 WITH/WITHOUT construction

`WITHOUT` = same-symbol-declustered eligible population (see section 4) -- the base
alpha's own always-on policy. `WITH` = the subset of that exact same declustered trade
list where `GATE==True`. Same costs (14bps RT), same population, same horizon, both
arms -- only the boolean gate differs, isolating its marginal contribution.

---

## 2. Verification checklist

| Check | Result |
|---|---|
| Causality (vol never uses future returns) | PASS -- `vol_24h` at bar *t* uses bars <= *t* only (current bar's own realized return, known at *t*, is legitimate; nothing from *t+1* onward touches it) |
| PIT of the threshold | PASS -- `shift(1)` + rolling on a *daily* series means day *D*'s threshold is fixed using only days *< D*; merge_asof backward means a same-day intraday event uses the prior day's frozen threshold, zero leakage |
| Timestamps | PASS -- all UTC tz-aware throughout; `event_time` grid confirmed 5-min-aligned in `decisions.parquet` |
| Units | PASS -- log returns converted via `(exp(x)-1)*1e4` to bps consistently; vol scaled by `sqrt(288)` consistently across all window-length anchors |
| Gate threshold definition | Documented explicitly above; primary = 50th pctile / 730D lookback, **not searched** |
| Horizon match | PASS -- `fwd_4h` (240min) used identically for base signal and gate; matches frozen `horizon` field |
| Declustering | Done at 3 levels (see section 4) -- **this is where the key finding is** |
| Costs | PASS -- 14bps RT applied identically both arms; +50% (21bps) anchor tested, delta unchanged (additive cost cancels out of the delta by construction) |
| Turnover / capacity | WITHOUT: 4,755 declustered trades / ~5y (~950/yr); gated: 2,721 trades (~46% pass rate) but **only 268 independent regime-episodes total** (~54-70/yr recently) -- see section 4, materially lower true capacity than the trade count suggests |
| Concentration | **Material** -- up to 94 gated trades share a single vol-elevated regime episode (median 5/episode); flagged as the central risk finding, not glossed over |
| Listing effects / survivorship | Fixed 50-symbol universe (`configs/portfolio_v1_1_parallel_50.yaml`), not selected ex-post; newer/shorter-history alts (e.g. RNDRUSDT, N=36) naturally contribute fewer events -- expected, not a bias |
| Missing data | 122/5,668 decisions dropped for incomplete forward window near the data's end (correctly excluded, not imputed); 0 missing price data |

---

## 3. Primary result

| Arm | N | mean net bps | PF | t-stat | win rate | max DD (cum. bps) |
|---|---|---|---|---|---|---|
| WITHOUT (always-on) | 4,755 | **+18.83** | 1.196 | 3.01 | 51.5% | -24,718 |
| WITH (vol-gated) | 2,721 | **+30.71** | 1.287 | 3.06 | 52.4% | -22,671 |
| **Delta (WITH - WITHOUT)** | | **+11.88bps** | +0.091 | | +0.9pt | **improves by 2,047bps** |

Directionally confirms the discovery claim: gate improves mean bps, PF, win rate, and
cuts drawdown. Magnitude differs from the discovery's reported OOS-half number
(+17.87bps on a train/test split) because my population is the full causal history
(no train/test split needed given the fully-causal rolling-threshold design) rather
than a single held-out test half -- not the same population, as expected and required
by the task ("not the exact same decimal delta").

**Year-by-year** (WITHOUT / WITH mean bps, delta): 2021 +60.2/+195.4 (delta +135.2,
N=11, thin), 2022 -27.8/-2.8 (delta +25.0 -- the worst year is *de-fanged*, not just
improved), 2023 +33.9/+39.5 (delta +5.6), 2024 +23.9/+22.9 (delta **-1.0**,
flat/negligible -- the one non-improving year), 2025 +17.1/+47.5 (delta +30.4, largest
single-year contribution), 2026 +28.1/+41.0 (delta +12.9). 5 of 6 years improve; 2024
is flat, not negative.

---

## 4. Declustering (mandatory) — the key finding

| Level | Definition | N |
|---|---|---|
| N_raw (gated, before any decluster) | every eligible gated decision | 2,318-3,201 depending on anchor pctile; 2,721 at primary p50 |
| N_independent, same-symbol | gap >= 4h within symbol (base alpha's own convention, matches its holding horizon) | **2,721** |
| N_independent, cross-symbol systemic (chain-link) | consecutive gated events **across all symbols**, sorted by time, chained if <4h apart | **849** (max cluster size 57, median 2) |
| N_independent, vol-regime-run | gated trades grouped by the contiguous BTC elevated-vol calendar-day run they fall in | **268** (max cluster size 94, median 5) |

This is exactly the risk the task flagged: *"vol-elevated periods often cluster in
time, which could make your N gated trades much less independent than it looks."* It
does. The naive same-symbol-declustered count (2,721, close to the discovery report's
own N=949-per-test-half order of magnitude) overstates independent evidence by **up to
~10x** once cross-symbol systemic clustering is accounted for. "Is BTC vol elevated" is
a slow-moving macro state (regimes persist for weeks), so most of the "2,721 independent
trades" are really a handful of shared macro episodes viewed through 49 different
symbols. Both alternative decluster units (systemic chain-link and regime-run) point the
same direction and are of the same order of magnitude -- this is not an artifact of one
particular clustering choice.

---

## 5. Anchor perturbations (preregistered, no search)

| Case | WITHOUT mean | WITH mean | Delta (bps) | PF (wo->wi) | DD (wo->wi) |
|---|---|---|---|---|---|
| **PRIMARY** (24h window, p50, 730D lookback) | 18.83 | 30.71 | **+11.88** | 1.196->1.287 | -24,718->-22,671 |
| p40 (neighboring pctile) | 18.83 | 28.29 | +9.46 | 1.196->1.277 | -24,718->-23,775 |
| p60 (neighboring pctile) | 18.83 | 39.25 | +20.42 | 1.196->1.362 | -24,718->-21,789 |
| 12h window (neighboring length) | 18.83 | 48.85 | +30.02 | 1.196->1.476 | -24,718->-20,545 |
| 48h window (neighboring length) | 18.83 | 34.68 | +15.85 | 1.196->1.315 | -24,718->**-25,070** (slightly worse) |
| ex-2025 (biggest single-year contributor) | 19.27 | 26.61 | **+7.34** | 1.210->1.265 | unchanged pre-2025 |
| ex-2020 | 18.83 | 30.71 | +11.88 (no-op -- eligibility already excludes 2020) | -- | -- |
| costs +50% (21bps RT) | 11.83 | 23.71 | +11.88 (unchanged -- additive cost cancels in the delta) | 1.119->1.215 | -26,373->-23,833 |

Every anchor stays **positive**, and PF improves in every case. The only soft spot: the
48h-window variant's drawdown is marginally *worse* on the WITH arm than WITHOUT
(-25,070 vs -24,718) even though its mean/PF still improve -- a minor inconsistency, not
a reversal. Excluding the single largest-contributing year (2025) still leaves a
positive, PF-improving delta (+7.34bps) -- the effect is not a single-year artifact.
**Conclusion: the mechanism's direction is robust to reasonable respecification.**

---

## 6. Event rate, N_required, ETA

`expected_live_edge = 0.5 x 11.88bps = 5.94bps` (reimplemented delta, not the
discovery's +17.87bps, per instructions).

**Event rates** (independent gated episodes/day, min of systemic-chain and regime-run
counts at each window):

| Window | N (systemic / regime, conservative=min) | days | per day | per week | per month |
|---|---|---|---|---|---|
| Full history (2021-09->2026-08) | 849 / 268 -> **268** | 1,800 | 0.149 | 1.04 | 4.53 |
| Last 2y | 439 / 125 -> **125** | 730 | 0.171 | 1.20 | 5.21 |
| Last 1y | 210 / 57 -> **57** | 365 | 0.156 | 1.09 | 4.75 |
| Last 6m | 106 / 35 -> **35** | 182 | 0.192 | 1.35 | 5.85 |

Rate is broadly stable (not collapsing), slightly *higher* in the most recent 6m window
-- a mild positive, though vol-regime non-stationarity means this should keep being
monitored.

**Block-bootstrap N_required** (5,000 reps, blocks = calendar month, one-sided alpha=5%,
power=80%), computed at **both** decluster units to avoid hanging the result on a single
assumption:

| Unit | N (full hist.) | mean bps/unit | bootstrap sigma/unit | N_required | conservative rate/day | ETA (days / years) |
|---|---|---|---|---|---|---|
| Systemic chain-link | 849 | 16.49 | 183.4 | 5,893 | 0.575 | 10,242d / **28.0y** |
| Vol-regime-run | 268 | 17.51 | 112.0 | 2,197 | 0.156 | 14,070d / **38.5y** |

`minimum_calendar_span = 60 days` (event/liquidation-style alpha) is dwarfed by both.

- **ETA_P50 = 10,242 days (~28.0 years)** [less-punitive unit]
- **ETA_CONSERVATIVE = 14,070 days (~38.5 years)** [most-punitive unit]

**Evidence floors** (at the regime-run/conservative rate, and at the systemic/faster rate):

| Floor | N | days (conservative) | days (faster unit) |
|---|---|---|---|
| EARLY | 30 | 192 | 60 (floor) |
| DEVELOPING | 50 | 320 | 87 |
| MEANINGFUL | 100 | 640 (~1.75y) | 174 (~0.5y) |

Both decluster units independently converge on the same qualitative conclusion: full
frequentist statistical confirmation is **decades away**, an order of magnitude beyond
any practical forward-validation runway -- even though the practical "meaningful"
evidence floor (100 independent episodes) is reachable in 0.5-1.75 years. The reason is
structural, not a tuning artifact: the true edge is small (5.94bps) relative to
per-independent-episode noise (sigma~112-183bps), and independent episodes accrue slowly
(~35-70/yr) because "is BTC vol elevated" is a persistent macro state, not a fast-moving
one.

---

## 7. Economic mechanism

Can be stated clearly: a repeat liquidation cascade on a single alt is ambiguous
evidence on its own -- it could be idiosyncratic noise (thin book, a large order) or
genuine forced deleveraging. When BTC-wide realized volatility is simultaneously
elevated, the same alt-level cascade is more likely to be part of a genuine systemic
deleveraging event (correlated liquidations across the market) rather than a local
noise event, and systemic capitulation cascades are more likely to mark real exhaustion
-- the price move is more likely to actually be "priced in" by the time of entry, making
the reversion trade (LONG after the cascade) more reliable. This is economically
coherent and matches the corroborating T1.11 finding (unrefit transfer to
`SHORT_SQUEEZE_EXHAUSTION`, context-only per instructions, not independently re-verified
here since that engine remains explicitly blocked). **The same economic story is exactly
why the independence problem in section 4 exists**: "BTC-wide stress" is by definition a
market-wide, persistent state -- so of course cascades across many symbols cluster
together whenever it's true. The mechanism and the concentration risk are two sides of
the same coin.

---

## 8. Verdict

**VALIDATED_FOR_FORWARD = false.**
**Status: NEEDS_MORE_RESEARCH.**

**What survives independent reimplementation (real, not forced):**
- Mechanism direction reproduces with an independently-sourced base signal, an
  independently-built causal vol gate, and independent PnL computation: +11.88bps
  delta, PF 1.196->1.287, drawdown improved, win rate improved.
- Robust across every preregistered anchor (percentile, window length, ex-biggest-year,
  costs+50%) -- always positive, PF always improves.
- Causal/PIT-clean by construction (no train/test split even needed -- every decision
  uses strictly-prior data).
- No leakage, no blocking bug found.
- Economically coherent mechanism, clearly stateable.
- Positive net expectation in both arms.

**Why it does not clear the bar:**
- **Material, quantified concentration** that the discovery report's own N=949
  understated by up to ~10x: only 268 truly independent vol-regime episodes exist in
  5 years of history (up to 94 correlated trades sharing one episode). This is not
  "hidden" any more (it's documented here), but it is real and disqualifying for a
  clean pass under the task's "no hidden concentration" bar.
- **N_required/ETA computed honestly via block bootstrap on the correctly-declustered
  unit is 28-39 years** under both tested decluster schemes -- not a borderline "just
  needs a bit more forward time" result, but two to three orders of magnitude beyond
  any practical forward-validation horizon. Reaching the softer "meaningful" (N=100)
  evidence floor is realistic (0.5-1.75y), but full statistical confirmation is not,
  and that gap should be understood explicitly rather than glossed over.
- One anchor (48h vol window) shows a (mild) drawdown regression on the WITH arm,
  a small but real stability wrinkle.

**Recommendation:** this is a real, economically-sensible, historically-corroborated
lead -- not a dead mechanism -- but it should not be promoted to forward/live capital
allocation as a statistically-confirmable edge under the standard frequentist bar used
elsewhere in this validation framework, because the true independent sample size is
capped by BTC's own macro vol-regime frequency (a handful of persistent regimes per
year), not by trade count or calendar time. Two possible paths forward, neither
attempted here (out of scope for a single-worker validation pass): (a) treat it as a
**qualitative/Bayesian risk-reduction overlay** informed by the 5-year historical
record (which is consistently supportive) rather than something to be frequentist-
confirmed forward; or (b) look for a faster-changing companion proxy for "systemic
stress" that decorrelates faster than BTC's own trailing realized vol, to shrink the
episode-to-episode redundancy documented in section 4. Track A owners should decide
which, if either, is worth pursuing -- this report's job was to validate, not to
redesign.

---

## Appendix — scratch artifacts

`/tmp/claude-1000/-home-qbee-futur/a0e00e24-e75f-4382-80ba-28c16b0aba06/scratchpad/validation/liq_vol_gate/`:
`build_gate.py` (base signal + vol gate construction), `analyze.py` (primary A/B,
declustering, event rates, block bootstrap, stability split, year breakdown),
`anchors.py` (preregistered perturbation grid), `eta_dual.py` (dual-decluster-unit
N_required/ETA), plus intermediate parquet/json outputs.
