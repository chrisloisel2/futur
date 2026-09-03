# AMIHUD_ILLIQUIDITY_PREMIUM — Independent Validation Report

**Validator:** independent worker, Alpha Validation Factory, 2026-09-02
**Candidate origin:** `reports/edge_discovery/alpha_hunt_2026-09-01_round3/w2_cross_sectional/REPORT.md`,
row `XSEC_AMIHUD_ILLIQ_7D` (claimed +99.3bps net/rebalance, t=2.92, PF 1.86, 7/7 years positive).
**Scope discipline:** the original discovery script was never read or copied. Everything below was
built from scratch against `data_v2/normalized` (worktree `/home/qbee/futur-data-v2`) and
`data/listings_backfill/binance/listings_calendar.parquet`. `src/institutional/engines/cross_sectional_momentum_live_v2/universe.py`
was **read** (not copied, not modified) for the PIT-eligibility pattern only. Track A
(`src/institutional/live_alpha_lab/`, `configs/live_alpha_registry.yaml`) was never touched.

All scratch code lives in `/tmp/claude-1000/-home-qbee-futur/a0e00e24-e75f-4382-80ba-28c16b0aba06/scratchpad/validation/amihud/`
(`01_build_daily_panel.py` … `06_decluster_nreq.py`) — not part of this deliverable, kept for
traceability only.

---

## 1. Methodology (reimplemented independently)

**Panel.** Daily bars built directly from `data_v2/normalized/perp_ohlcv` 5-minute Binance perp
OHLCV via DuckDB (own aggregation, no pre-computed feature reused): `daily_close` = close of the
last 5m bar of the UTC calendar day, `daily_dollar_volume` = sum of `quote_asset_volume` over the
day. 312 symbols, 2020-01 → 2026-09 (matches the discovery's own stated panel).

**Amihud illiquidity measure**, computed strictly causally:
```
r(s,t)          = close(s,t)/close(s,t-1) - 1
illiq(s,t)       = |r(s,t)| / dollar_volume(s,t)
illiq_avg(s,d)   = mean( illiq(s,t) for t in [d-30, d-1] )     # PRIMARY window W=30, ends STRICTLY before d
```
Classic Amihud (2002) ~1-month trailing window; matches "mean |daily return| / dollar volume over
a trailing window" from the mission brief.

**PIT eligibility gate at rebalance date `d`** (mirrors, does not import, the
`cross_sectional_momentum_live_v2/universe.py` pattern):
- `onboard_ts` from `listings_calendar.parquet`, falling back to first real-price date when the
  symbol has no calendar entry (same convention as production).
- `d >= onboard_ts + 30 days` (MIN_LISTING_AGE_DAYS, same constant as production).
- Causal rolling **median** 30d dollar volume over `[d-30, d-1]` >= **$1,000,000** (primary liquidity
  floor).
- `illiq_avg(s,d)` computable from >=20 valid days in its trailing window (else excluded, never
  imputed).
- Forward close known at both `d` and `d+horizon` (else excluded that period — missing data is
  never interpolated or carried forward).

**Portfolio construction.** Rank the eligible universe descending by `illiq_avg` (most illiquid
first) each rebalance date. LONG the top quintile (most illiquid, subject to the liquidity floor
above so it's never literally untradeable names), SHORT the bottom quintile (most liquid).
Equal-weighted within each leg. Forward return per name = simple close-to-close return over the
holding horizon (7 calendar days, primary). Per-period cross-sectional 1%/99% winsorization
applied across the **full eligible cross-section** that period (not just the picked legs) before
computing leg means — a standard technique against single-name blowup weeks, implemented
independently here.

**Costs.** 5bps taker + 2bps slippage = 7bps one-way per leg, 4 legs for a long-short book
(enter+exit × long+short) = **28bps round-trip**, per the mission's standing cost convention.

**Declustering / multi-anchor check.** A 7-day horizon on a weekly non-overlapping grid has 7
possible calendar phase offsets ("anchors"). All 7 were rolled independently to test whether the
sign/magnitude of the result depends on which day-of-week rebalancing happens to land on — a
general, well-known robustness technique, applied here from scratch (not copied). The PRIMARY
reported t-stat/N/yearly breakdown uses **anchor=0** (first reproducible, arbitrary, fixed *before*
inspecting any result). Anchor-pooled mean/std is the stability diagnostic.

---

## 2. Verification checklist

| Check | Result |
|---|---|
| **Causality** (illiquidity measure) | `illiq_avg(d)` uses `shift(1)` + rolling mean over `[d-30, d-1]` — day `d` itself never enters its own signal. Verified by construction and spot-checked (first rebalance 2020-03-18 has a `[2020-02-17, 2020-03-17]` window). |
| **PIT universe** | Eligibility evaluated as-of each `d` (onboard age + causal trailing liquidity median), not "current universe copied backward." `n_eligible` genuinely grows over time: median 133 names, min 21 (early 2020), max 258 (2025-26) — not a flat count. |
| **Timestamps / off-by-one** | Rebalance grid anchor=0: 332 periods, every single gap exactly 7 days, zero missing weeks 2020-03-18→2026-07-22 — confirms no skipped/duplicated periods. |
| **Units** | Dollar volume in raw USDT (spot-checked: long-leg trailing 30d median dollar volume $1.4M–$13.6M range, short-leg $46M–$510M — economically sane for real Binance perps). Returns in decimal, reported in bps (×1e4). |
| **Target/entry/exit** | Entry = close(d), exit = close(d+7), equal-weight, long-short, documented above — no ambiguity. |
| **Horizon** | 7 calendar days (primary), matches discovery's `XSEC_AMIHUD_ILLIQ_7D` naming. |
| **Declustering** | Applied — see §4. Non-overlapping grid by construction (horizon == rebalance spacing); ACF and symbol-persistence checked explicitly (not just assumed away). |
| **Costs** | 7bps one-way / 28bps round-trip LS, matches project convention; stress-tested at +50% (§3, perturbation 6) — stays significant. |
| **Turnover** | Full leg rebalance every period by construction (quintile membership re-ranked weekly); this **is** the strategy's turnover, already priced into the 28bps/period cost. |
| **Capacity** | See §5 — worst-case (5th pct) implied participation rate ≈0.38% of ADV at a $300k book. Comfortable. |
| **Concentration** | Top-2 symbols by absolute PnL contribution = **5.8%** of total abs PnL; top-2 dates = **4.7%**. Not driven by a handful of names or dates. |
| **Listing effects** | Median listing age of LONG (illiquid) leg selections = **687 days**; only **2.6%** of long-leg picks are <60 days old. NOT a newly-listed-thin-name strategy in disguise. |
| **Survivorship** | 14/27 `DELISTED`-status calendar symbols are present in the 312-symbol panel with real data through their actual delisting date — e.g. **LUNAUSDT through 2022-05-13**, the real Terra collapse date, confirming genuine inclusion of blow-up events, not truncation. 13/27 delisted symbols are absent entirely (upstream `data_v2` coverage gap, disclosed, never silently faked). |
| **Missing data handling** | A name missing either its trailing-window data or its forward close is excluded from that period only — never imputed, never carried forward. |
| **Data sanity** | Largest daily moves in the panel (UNFIUSDT +622% 2022-06-07, DOGEUSDT +259% 2021-01-28, etc.) are real, well-documented crypto events, not obvious data glitches — and the concentration check above confirms they don't dominate results (winsorization + broad leg sizes work as intended). |

---

## 3. Primary spec + preregistered perturbations

PRIMARY_SPEC fixed *before* any result was inspected: W=30d illiquidity window, H=7d horizon,
$1M causal liquidity floor, anchor=0, 7bps one-way / 28bps RT costs, long-short quintile.

| Spec | N | net bps | t-stat | p-value | PF | years+ |
|---|---|---|---|---|---|---|
| **PRIMARY** (W=30, H=7, liq=$1M, cost=7bps) | 332 | **+105.7** | **3.02** | 0.0027 | 1.67 | 6/7 |
| Perturbation 1: window=20 (vs 30) | 332 | +86.2 | 2.48 | 0.0136 | 1.53 | 6/7 |
| Perturbation 2: horizon=5 (vs 7) | 465 | +60.9 | 2.62 | 0.0090 | 1.45 | 6/7 |
| Perturbation 3: liq_floor=$2M (vs $1M) | 327 | +109.3 | 3.09 | 0.0022 | 1.70 | **7/7** |
| Perturbation 4: exclude 2021 (biggest year) | 280 | +50.9 | 1.72 | 0.086 | 1.34 | 5/6 |
| Perturbation 5: exclude 2020 | 290 | +117.7 | 3.21 | 0.0015 | 1.83 | 5/6 |
| Perturbation 6: cost +50% (10.5bps 1-way / 42bps RT) | 332 | +91.7 | 2.62 | 0.0091 | 1.55 | 6/7 |

**No sign flips anywhere.** The weakest case is perturbation 4 (excluding 2021, the biggest
positive year at +400.9bps net that year) — significance drops to a marginal t=1.72 (p=0.086),
confirming a meaningful share of the historical magnitude is concentrated in the 2021 bull market,
but the direction survives (+50.9bps net, still profitable, PF 1.34).

**Yearly breakdown, PRIMARY (anchor=0):**

| 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 (partial) |
|---|---|---|---|---|---|---|
| +22.5 | +400.9 | +68.6 | +61.7 | +97.9 | **-0.2** | +49.9 |

2025 was essentially flat (net -0.2bps, the only non-positive year) — worth flagging as a possible
early sign of edge decay or just noise given the small per-year sample; 2026 YTD recovered to
+49.9bps.

**Anchor robustness (all 7 possible weekly rebalance-day phases, W=30/H=7/liq=$1M/cost=7bps):**

| anchor | N | net bps | std bps | t-stat |
|---|---|---|---|---|
| 0 | 332 | 105.7 | 636.8 | 3.02 |
| 1 | 332 | 103.6 | 644.7 | 2.93 |
| 2 | 332 | 110.7 | 652.6 | 3.09 |
| 3 | 332 | 97.1 | 661.2 | 2.67 |
| 4 | 332 | 88.1 | 609.8 | 2.63 |
| 5 | 332 | 87.6 | 615.2 | 2.60 |
| 6 | 332 | 95.0 | 659.2 | 2.62 |

Gross bps range across anchors: 115.6–138.7 (mean 126.3, std 8.8) — the tightest anchor dispersion
seen in this validator's experience of this project's cross-sectional constructs, matching the
discovery's own characterization ("tightest anchor dispersion of any long-short construction
tested"). **Every single anchor is independently significant (t between 2.60 and 3.09).**

**Independent reproduction vs. discovery claim:** primary net +105.7bps / t=3.02 / PF 1.67 / 6-of-7
years vs. the discovery's own +99.3bps / t=2.92 / PF 1.86 / 7-of-7 years — a from-scratch,
independently-coded reimplementation lands within ~6% of the reported net edge and a comparable
t-stat. This is strong evidence the original finding is not an implementation artifact.

---

## 4. Declustering detail

- **N_raw** (7 anchors × ~332 periods pooled) = **2,324**.
- **N_independent** (primary single-anchor, non-overlapping weekly series) = **332**, spanning
  2020-03-18 → 2026-07-22, with the rebalance-date gap exactly 7 days in every one of the 331
  transitions (zero skipped weeks) — periods do not overlap in calendar time by construction
  (holding period == rebalance spacing).
- **Residual return autocorrelation:** ACF(lag1) of the net-return series = **+0.154** (modest,
  not negligible); ACF(lag2-5) ≈ 0 (-0.01 to -0.07). A Newey-West-style AR(1) effective-N
  correction (`N_eff = N·(1-ρ)/(1+ρ)`) gives N_eff ≈ 243, which would lower the reported t-stat
  from 3.02 to ≈2.59 — still significant at conventional levels, but this is disclosed as a real,
  non-zero source of over-statement in the naive N=332 t-stat.
- **Same-symbol (cross-sectional) clustering:** week-over-week symbol overlap in the long
  (illiquid) leg = **89.5%**, short (liquid) leg = **95.2%** — i.e. largely the *same* instruments
  sit in each leg for consecutive weeks. This is economically expected (illiquidity is a
  slow-moving characteristic, like a value/quality tilt, not a fast-rotating signal), but it means
  the "332 independent weekly bets" are not 332 independent draws on *different* instruments —
  a meaningful share of the statistical power comes from repeatedly re-testing the forward returns
  of a fairly stable illiquid cohort, not from cross-sectional variety. This is flagged as the
  single most important methodological caveat of this validation.
- **Temporal/systemic clustering:** no single illiquidity regime dominates — concentration check
  above (§2) already shows top-2 dates are only 4.7% of total abs PnL.
- **Robustness of N_required to the ACF(1)=0.154 finding:** block-bootstrap simulations at block
  sizes 1, 2, and 4 (see §5) gave consistent N_required (~850-950), i.e. the modest residual
  autocorrelation does not materially change the forward power-analysis conclusion.

---

## 5. Capacity check

Assumed shadow book: **$300,000**, split equally long/short across the primary spec's average leg
size (≈29 names long, ≈29 names short) → **≈$5,143 per name**.

| | long (illiquid) leg | short (liquid) leg |
|---|---|---|
| trailing 30d dollar volume, p05 | $1.37M | $46.0M |
| trailing 30d dollar volume, median | $6.80M | $239.0M |

Implied participation rate (position size / trailing 30d daily dollar volume):
- **worst case (5th percentile name): 0.38% of ADV**
- **median name: 0.08% of ADV**

Both are comfortably below any realistic capacity-impact threshold (typical caps used elsewhere in
this project are in the low single-digit percent of ADV). A few-hundred-thousand-dollar shadow
book is capacity-compatible with this construction.

---

## 6. Event rate / N_required / ETA

- `independent_events_per_week` = **1.0** (fixed weekly non-overlapping grid).
- `independent_events_per_day` = 0.143, `independent_events_per_month` ≈ 4.35.
- Rate over sub-windows (all measured against the same fixed weekly grid, so essentially constant
  by construction — no gaps found):
  - last 2y: 105 periods (1.007/week)
  - last 1y: 53 periods (1.016/week)
  - last 6m: 27 periods (1.033/week)
  - **rate_stable: true** — no visible change in event availability over time.
- `conservative_event_rate` = min(last-2y, last-1y, last-6m) = **1.007/week** (≈0.144/day).
- `expected_live_edge` = 0.5 × 105.7bps = **52.8bps net** (LIVE_EFFECT_HAIRCUT = 50%, standing
  project default).
- **N_required_statistical**: computed via **block-bootstrap simulation** (preferred over a naive
  iid formula, per the mission brief, given 332 own declustered episodes are available) — the 332
  historical net-return episodes were demeaned, recentered to `expected_live_edge`, and resampled
  with replacement (block sizes 1, 2, and 4 tested to bracket the ACF(1)=0.154 residual
  autocorrelation found in §4) across a grid of candidate N, measuring the fraction of 1,200-1,500
  simulated one-sided t-tests (α=5%) that reject the null. Power curve (block_size=1):

  | N | 100 | 300 | 500 | 700 | 800 | 900 | 1000 | 1200 |
  |---|---|---|---|---|---|---|---|---|
  | power | 0.18 | 0.41 | 0.60 | 0.74 | 0.76 | **0.82** | 0.85 | 0.92 |

  Block sizes 2 and 4 gave consistent results (N≈900-1000 for 80% power), confirming the residual
  autocorrelation does not materially change the answer.
  → **N_required_statistical ≈ 900 independent weekly episodes.**
- `minimum_calendar_span` = **182 days (6 months)** — this is a weekly cross-sectional-style
  alpha, per the project's floor table.
- `ETA_from_event_count` = 900 / conservative_event_rate(≈0.144/day) ≈ **6,257 days ≈ 894 weeks
  ≈ 17.1 years**.
- `VALIDATION_ETA_CONSERVATIVE` = max(6,257 days, 182 days) = **6,257 days (≈17.1 years)**.
- `VALIDATION_ETA_P50` (using last-1y rate 1.016/week) ≈ **6,198 days (≈17.0 years)**.
- **Evidence floors** (descriptive, not a gate): N_independent = 332 already clears all three
  floors — 30 (EARLY), 50 (DEVELOPING), and 100 (meaningful statistical target). The *historical
  backtest itself* is well past minimum evidence. The 17-year figure above is a **different,
  much stricter number**: how long it would take to statistically reconfirm, at conventional
  power, the *smaller haircut-adjusted live edge* specifically — not a statement that the
  historical finding itself is under-evidenced. The two should not be conflated; both are reported
  here explicitly so they aren't.

**Why N_required is so large:** the per-bet signal-to-noise ratio is inherently low for a weekly
cross-sectional book on ~300 names (net mean 105.7bps vs. per-period std 636.8bps ≈ a weekly
Sharpe of 0.17, annualized ≈1.19 pre-haircut; post-haircut at 52.8bps mean the weekly Sharpe is
≈0.083, annualized ≈0.60). This is not unusual for a real risk-premium-style factor, but it does
mean fresh forward statistical confirmation at 80% power is not realistic within any practical
shadow-trading window — monitoring should be evaluated on sign/mechanism-consistency and cost
survival over the 6-month floor, not on achieving fresh significance.

---

## 7. Verdict

**VALIDATED_FOR_FORWARD = TRUE**, with two documented caveats.

Reasoning against the mission's explicit gate list:
- Reimplementation agrees economically with the discovery: mechanism (illiquidity compensation,
  long the most-illiquid-eligible quintile / short the most liquid) reproduces net +105.7bps /
  t=3.02 / PF 1.67 vs. the discovery's own +99.3bps / t=2.92 / PF 1.86 — close agreement from a
  fully independent build. **PASS.**
- Causal: verified by construction (shift(1) + rolling window) and spot-checked. **PASS.**
- PIT: eligibility gate evaluated as-of each rebalance date, universe size genuinely varies over
  time. **PASS.**
- Costs credible: 7bps one-way / 28bps RT matches project convention; survives a +50% cost stress
  test (t=2.62). **PASS.**
- Net expectation positive: primary and all 6 preregistered perturbations, and all 7 rebalance-day
  anchors, are net positive with no sign flips anywhere. **PASS.**
- No lookahead/leakage found in the signal, the eligibility gate, or the forward-return
  computation. **PASS.**
- No blocking bug found. **PASS.**
- No hidden extreme concentration: top-2 symbols = 5.8% of abs PnL, top-2 dates = 4.7%. **PASS.**
- No dominant listing effect: median long-leg listing age 687 days, only 2.6% <60 days old.
  **PASS.**
- Stability across anchor perturbations: exceptionally tight (gross bps range 115.6-138.7 across
  all 7 anchors, all individually significant). **PASS.**
- Capacity compatible with a few-hundred-thousand-dollar shadow book: worst-case implied
  participation rate 0.38% of ADV. **PASS.**
- Declustering applied: non-overlapping primary series (N_indep=332), ACF and symbol-persistence
  both explicitly checked (not assumed away), block-bootstrap N_required robust across block
  sizes. **PASS.**
- Mechanism economically understandable: classic Amihud (2002) illiquidity premium — investors
  demand compensation for holding harder-to-trade names; distinct from momentum/carry/basis
  factors already tested in the same sweep. **PASS.**

**Caveats to carry forward (documented, not disqualifying):**
1. **2021-concentration.** A meaningful share of the historical magnitude sits in the 2021 bull
   year (+400.9bps that year alone); excluding it drops significance to a marginal t=1.72
   (p=0.086), though the direction survives (+50.9bps, PF 1.34). Read the headline t-stat as
   somewhat 2021-flattered, not purely a stable structural constant.
2. **17-year statistical ETA.** Because the per-bet signal-to-noise ratio of a weekly ~300-name
   cross-sectional book is inherently low, formally reconfirming the smaller haircut-adjusted live
   edge at 80% power would require ≈900 independent weekly episodes ≈17 years — far beyond any
   practical shadow-trading window. **Recommendation:** treat forward/shadow monitoring against
   the 6-month minimum-calendar-span floor and mechanism-consistency criteria (sign survives,
   cost assumption survives, no new concentration), not against achieving fresh statistical
   significance, which is not a realistic near-term bar for this class of alpha. The 2025 flat
   year (net -0.2bps) is the one data point worth actively watching for decay during that window.

**ETA (informational, not a pass/fail input):**
- ETA_P50 ≈ 6,198 days ≈ 885 weeks ≈ **17.0 years**
- ETA_CONSERVATIVE ≈ 6,257 days ≈ 894 weeks ≈ **17.1 years**
- minimum_calendar_span (practical monitoring floor) = 182 days (6 months)
