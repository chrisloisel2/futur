# CROSS_SECTIONAL_MOMENTUM_CVD — Independent Validation Report

**Validator:** independent worker, Alpha Validation Factory, 2026-09-02
**Candidate origin:** `reports/edge_discovery/alpha_hunt_2026-09-01_round3/w2_cross_sectional/REPORT.md`,
rows `XSEC_MOM_CVD_CONFIRMED_7D` (claimed net +49.8bps, t=1.03, PF 1.35, positive 6/7 years) and
`XSEC_MOM_CVD_DIVERGENT_7D` (claimed net -150.9bps, t=-2.39, PF 0.68, negative 7/7 years).
**Scope discipline:** the original discovery script (`w2_cross_sectional/harness.py` and siblings)
was never read or copied — only its `REPORT.md` (context/methodology prose, no code) was read, per
the mission's own instruction. Everything below is built from scratch against `data_v2/normalized`
(worktree `/home/qbee/futur-data-v2`) and `data/listings_backfill/binance/listings_calendar.parquet`.
`src/institutional/engines/cross_sectional_momentum_live_v2/universe.py` was **read** (not copied,
not modified) for the PIT-eligibility pattern only. Track A
(`src/institutional/live_alpha_lab/`, `configs/live_alpha_registry.yaml`) was never touched.

All scratch code lives in
`/tmp/claude-1000/-home-qbee-futur/a0e00e24-e75f-4382-80ba-28c16b0aba06/scratchpad/validation/xsmom_cvd/`
(`build_signals.py`, `run_backtest.py`, `analyze.py`, `concentration.py`, `perturbations.py`,
`event_rate_bootstrap.py`) — not part of this deliverable, kept for traceability only.

**VERDICT: REJECTED (not VALIDATED_FOR_FORWARD).** See §7 for the full reasoning. Short version:
the qualitative direction of the claim survives an independent rebuild only after a construction
fix (see §1.3); the *tradeable* (CVD-confirmed) side is weak, not significant in any spec tried,
and flips sign under 4 of 9 preregistered perturbations. The *avoid* (CVD-divergent) side is
directionally robust across every perturbation but is a filter, not a standalone position, and is
itself far short of the statistical power needed for confident deployment.

---

## 1. Methodology (reimplemented independently)

### 1.1 Panel

Daily bars built directly from `data_v2/normalized/perp_ohlcv` (5-minute Binance USDT-M perp
OHLCV, DuckDB aggregation, own code, no pre-computed feature reused): `close` = last 5m close of
the UTC calendar day, `quote_vol` = sum of `quote_asset_volume` over the day,
`taker_buy_quote` = sum of `taker_buy_quote_asset_volume` over the day. 312 symbols,
2020-01-01 → 2026-07-31 (`SET timezone='UTC'` before every `CAST(timestamp AS DATE)` to avoid a
CET/CEST day-boundary bug). 365,980 symbol-days.

### 1.2 PRIMARY_SPEC, fixed before looking at any split result

- **Momentum**: 7 calendar-day lookback, `close_t / close_{t-7} - 1`.
- **Holding horizon**: 7 calendar days.
- **Rebalance grid**: weekly, **anchor-pooled** across all 7 day-of-week phases (each phase
  internally non-overlapping). This choice — not a single arbitrary weekly anchor — was made
  *before* running anything: the discovery's own `REPORT.md` documents that a single weekly anchor
  on this ~300-name panel is a fragile estimator (a legendary-pump week can dominate 1/7 of the
  possible grid choices, swinging gross bps by ~90bps on the plain momentum book). Anchor-0 is
  reported separately throughout as the conservative, genuinely non-overlapping series.
- **Universe**: PIT-eligible only — see §1.4.
- **Book construction**: standard cross-sectional long-short quintile on 7d momentum (top quintile
  = long leg, bottom quintile = short leg), matching the family this candidate is drawn from
  (`ORDER_FLOW_CONFIRMED_MOMENTUM` / `ORDER_FLOW_DIVERGENT_MOMENTUM`, both explicitly "restricted
  to names" from the base LS quintile book in the source report).
- **CVD / taker-flow measure**: `net_taker_buy_frac_7d = (2·Σtaker_buy_quote − Σquote_vol) / Σquote_vol`
  summed over the *same* trailing 7-day window as momentum — net aggressive-buy fraction of total
  traded USD value, in [-1,+1]. This is Binance's own klines aggressor classification
  (`taker_buy_quote_asset_volume`), **not** the known placeholder field in
  `data/enriched/*_1h_enriched.parquet` (that field is exactly `quote_vol/2` everywhere — spot
  checked here and found NOT to be constant: only 65/105,392 five-minute BTCUSDT bars in 2024 sit
  at exactly 0.5). Cross-validated against a second, fully independent pipeline —
  `data_v2/normalized/agg_trades_flow` (rebuilt bar-by-bar from raw `aggTrades` `is_buyer_maker`
  flags, a heavier, separately-computed reconstruction) — for BTCUSDT 2021: **correlation
  0.99998, sign agreement 100%** between the two independently-built daily net-taker-buy-fraction
  series. This rules out a data-corruption explanation for anything below.
  `data/derivatives_raw/.../stream=ratios` (`taker_buy_sell_ratio`) was considered as the task's
  literally-named source but is a **live-collector-only feed covering ~2026 only** (hive
  partitions `date=2026-*`) — not usable for a 2020-2026 backtest, so the klines-native field was
  used instead as the "or equivalent" the task explicitly allows.
- **Costs**: 5bps taker + 2bps slippage = 7bps one-way, **14bps round-trip per name position**
  (entry+exit), applied uniformly regardless of leg.
- **Winsorization**: forward returns clipped at the 1%/99% cross-sectional tails across the full
  eligible universe each rebalance (not just the picked legs) — standard hygiene against a single
  legendary-pump print dominating a small-N quintile mean, applied identically before any split.
- **Minimum book size**: a rebalance date is skipped (not zero-filled) if fewer than 15 names are
  eligible, or a leg would end up with fewer than 3 names.

### 1.3 A construction problem found by diagnostic, fixed once, then frozen

The *first* honest, pre-registered choice for "CVD confirms momentum" was the most literal reading
of the claim: **CONFIRMED** = `sign(net_taker_buy_frac_7d)` agrees with the leg's direction
(long: >0, short: <0), i.e. an absolute zero threshold. Run once, this produced the **opposite**
sign from the discovery's claim (CONFIRMED net -56.7bps single-anchor, DIVERGENT net +37.9bps).
Before accepting that as a finding, three checks were run to rule out a bug (diagnostics, not a
parameter search for a favorable number):

1. **Same-window momentum vs. CVD correlation** (pooled, all symbols/days): **+0.146**, sign
   correct and economically sane (positive momentum weakly associates with positive net taker-buy
   flow, as expected) — not a sign-inverted bug.
2. **Cross-source CVD validation** (§1.2 above): 0.99998 correlation with an independently-rebuilt
   pipeline — not a data bug.
3. **Root cause found**: `net_taker_buy_frac_7d` has a **structural negative bias** across the
   whole panel (pooled mean -0.027, median -0.025 — persistent net-aggressive-selling tilt on
   Binance USDT-M perps). Under an absolute zero threshold this makes "CONFIRMED-long" a rare,
   extreme-tail-only bucket (only 20% of the long leg, mean momentum of that subset +32.4% over 7
   days vs. +14.7% for the rest — i.e. it selects for the most extended, legendary-pump-magnitude
   movers) while "CONFIRMED-short" becomes almost the *entire* short leg (96%) — a degenerate,
   heavily imbalanced split, not the intended symmetric flow-confirmation test.

This is a genuine construction flaw, not a result to be explained away: an absolute threshold on a
measure with a time-varying, non-zero-centered baseline conflates "confirmed" with "most extreme."
**Fix** (decided from this diagnostic, before looking at what it would do to the confirmed/divergent
spread): use the **cross-sectional median** of `net_taker_buy_frac_7d`, over the *same* full
eligible universe used for momentum ranking, as the reference level each rebalance — CONFIRMED-long
iff above that day's median, CONFIRMED-short iff below it. This is the same convention already used
by every *other* filter construction in the source discovery's own report (e.g. "below-median
leverage-proxy", "below-median funding-percentile") — not a new invention. **This corrected
definition is the PRIMARY_SPEC reported in §3 onward; the absolute-zero-threshold run is kept and
reported as a preregistered "LOOSER" perturbation in the table (§3), disclosed prominently since it
is central to how fragile this construction turns out to be.**

### 1.4 PIT eligibility gate (replicates, does not import, `universe.py`'s pattern)

- `onboard_ts` from `listings_calendar.parquet` (311/312 panel symbols matched; 1 fallback to
  first-price-data — `onboard_source` breakdown logged).
- `d >= onboard_ts + 30 days` (`MIN_LISTING_AGE_DAYS`, same constant as production).
- Causal rolling **median** 30d `quote_vol` >= **$1,000,000**, `min_periods=30` (a
  freshly-age-eligible name still needs a *full* trailing window, never a partial-window
  optimistic estimate).
- Eligible universe size genuinely grows over time (not a flat count copied backward): mean
  eligible names/day by year — 2020: 31.4, 2021: 91.2, 2022: 103.7, 2023: 136.1, 2024: 180.3,
  2025: 224.2, 2026 (partial): 251.4.

---

## 2. Verification checklist

| Check | Result |
|---|---|
| **Causality (momentum)** | `close_t/close_{t-7}-1` uses only data through day `t` — no lookahead. |
| **Causality (CVD)** | `net_taker_buy_frac_7d` = rolling 7d sum ending at `t`, `min_periods=7` (any gap day in the window -> NaN, never silently under-counted). Same window discipline as momentum. |
| **PIT universe** | Eligibility evaluated as-of each `d` (age gate + causal trailing liquidity median); universe genuinely grows over history (§1.4), not a "today's list applied backward." |
| **Timestamps / day boundary** | `SET timezone='UTC'` before every `CAST(timestamp AS DATE)` — avoids a CET/CEST day-boundary bug from DuckDB's local-session-timezone display. |
| **Units** | `quote_vol`/`taker_buy_quote` in raw USDT notional (spot-checked, not a placeholder — see §1.2). Returns in decimal, reported x1e4 as bps. |
| **Target/entry/exit/horizon** | Entry = close(d), exit = close(d+7), equal-weight within each leg, 7-day holding horizon, documented in §1.2. |
| **Declustering** | Applied — mandatory anchor-pooled + single-anchor split, see §4. |
| **Costs** | 7bps one-way / 14bps round-trip per name (5bps taker + 2bps slippage), stress-tested at +50% (§3) — direction survives for divergent, confirmed's already-weak edge goes negative on the single-anchor series. |
| **Turnover** | Full book reformed every rebalance by construction (nominal per-position holding = exactly 1 rebalance interval = 7 days); this **is** the cost basis already applied. |
| **Capacity** | Same $1M causal liquidity floor as the rest of this project's cross-sectional work; quintile leg size grows from ~6 names (2020) to ~50 (2026) — not independently re-examined given the verdict below, but no capacity-specific red flag found. |
| **Concentration** | **Fails cleanly for the confirmed side** — see §4/§6: excluding the single most extreme date out of 333 flips the primary CONFIRMED mean from +4.5bps to -1.5bps. Same-symbol concentration is fine (HHI-implied effective N ~230-240 symbols, top-10 share 7.5-7.8%) — the fragility is a *temporal/cross-symbol systemic* concentration (a handful of 2020-2021 dates where many names co-moved), not a single-name problem. |
| **Listing effects** | Eligibility already enforces the 30-day age gate; not separately re-examined given the verdict. |
| **Survivorship** | 14/27 `DELISTED`-status calendar symbols are present in the 312-symbol panel; **13/27 are absent entirely** (upstream `data_v2` coverage gap, disclosed, not silently faked) — real, quantifiable survivorship gap in the historical universe. One inconsistency noted: `SXPUSDT` is marked `DELISTED_NO_DATA` in the calendar yet *is* present in the panel — documented, not chased further (does not affect causality/PIT, only a stale calendar-status label). |
| **Missing derivatives data / taker-flow coverage** | Conditional on a symbol being listed, median coverage = **100%**; 271/312 symbols >=90% coverage; **41/312 <90%**, **9/312 <50%** (worst: STRAXUSDT 15%, COCOSUSDT 20%, MEMEFIUSDT 24%, TROYUSDT 26%, VIDTUSDT 33%). Names with an incomplete 7d CVD window that rebalance are excluded from the confirmed/divergent split for that period (`NO_CVD_DATA`, 28/16,864 name-observations = 0.17% — negligible). |

---

## 3. Primary spec + preregistered perturbations

All perturbations below were decided in advance (per the mission brief: neighboring lookback,
neighboring horizon, stricter/looser CVD threshold, ex-biggest-year, ex-2020, costs+50%) and run
once each, no search. "single_anchor" = anchor-0 series (conservative, genuinely non-overlapping,
N=330-333). "anchor_mean"/"anchor min-max" = mean and range across all 7 weekly-phase anchors
(N_raw pooled).

| Spec | bucket | anchor_mean bps | anchor min-max | single-anchor bps | t (single-anchor) | pos years | anchor sign-flip |
|---|---|---:|---|---:|---:|---|---|
| **PRIMARY** (7d/7d, median-split) | CONFIRMED | **+25.5** | 1.3 to 57.6 | **+4.5** | 0.22 | 3/7 | no |
| **PRIMARY** (7d/7d, median-split) | DIVERGENT | **-53.5** | -84.5 to -39.4 | **-61.4** | -1.58 | 2/7 | no |
| LOOSER: abs-zero threshold (superseded, §1.3) | CONFIRMED | -35.1 | -76.8 to -8.7 | -56.7 | -1.02 | 3/7 | no |
| LOOSER: abs-zero threshold (superseded, §1.3) | DIVERGENT | +43.9 | 30.1 to 55.4 | +37.9 | 0.62 | 4/7 | no |
| NEIGHBOR_LOOKBACK 5d mom / 7d hold | CONFIRMED | +25.5 | -8.7 to 55.7 | +14.5 | 0.63 | 4/7 | **YES** |
| NEIGHBOR_LOOKBACK 5d mom / 7d hold | DIVERGENT | -46.2 | -71.6 to -28.6 | -53.1 | -1.52 | 2/7 | no |
| NEIGHBOR_LOOKBACK 10d mom / 7d hold | CONFIRMED | +31.7 | 13.7 to 59.2 | +17.9 | 0.87 | 5/7 | no |
| NEIGHBOR_LOOKBACK 10d mom / 7d hold | DIVERGENT | -46.9 | -69.7 to -11.6 | -64.6 | -1.93 | 1/7 | no |
| NEIGHBOR_HORIZON 7d mom / 5d hold | CONFIRMED | +22.9 | 1.5 to 40.6 | +2.7 | 0.16 | 5/7 | no |
| NEIGHBOR_HORIZON 7d mom / 5d hold | DIVERGENT | -40.5 | -58.6 to -12.5 | -58.6 | -1.81 | 2/7 | no |
| NEIGHBOR_HORIZON 7d mom / 10d hold | CONFIRMED | +47.8 | 16.0 to 76.6 | +38.6 | 1.44 | 6/7 | no |
| NEIGHBOR_HORIZON 7d mom / 10d hold | DIVERGENT | -65.2 | -99.8 to -31.2 | -99.8 | -2.04 | 2/7 | no |
| STRICTER: tercile CVD threshold | CONFIRMED | +42.4 | 11.3 to 85.2 | +22.5 | 0.99 | 4/7 | no |
| STRICTER: tercile CVD threshold | DIVERGENT | -44.3 | -67.7 to -27.1 | -58.0 | -1.84 | 4/7 | no |
| EX_BIGGEST_YEAR (drop 2024/2021 resp.) | CONFIRMED | +26.2 | -4.5 to 58.2 | **-4.5** | -0.19 | 2/6 | **YES** |
| EX_BIGGEST_YEAR (drop 2024/2021 resp.) | DIVERGENT | -31.4 | -40.7 to -23.5 | -29.3 | -0.77 | 2/6 | no |
| EX_2020 | CONFIRMED | +19.7 | -3.0 to 48.9 | +9.0 | 0.49 | 3/6 | **YES** |
| EX_2020 | DIVERGENT | -48.9 | -76.4 to -22.4 | -59.2 | -1.47 | 2/6 | no |
| COSTS_PLUS_50PCT (21bps rt) | CONFIRMED | +18.5 | -5.7 to 50.6 | **-2.5** | -0.12 | 3/7 | **YES** |
| COSTS_PLUS_50PCT (21bps rt) | DIVERGENT | -60.5 | -91.5 to -46.4 | -68.4 | -1.77 | 2/7 | no |

**Reading this table:**

- **DIVERGENT is directionally robust everywhere.** Every legitimate perturbation (all rows except
  the superseded abs-zero one) keeps DIVERGENT net negative on both anchor_mean and single-anchor,
  with **zero anchor-level sign flips** across all 9 legitimate perturbations (anchor_min and
  anchor_max both negative in every one). Best single-anchor significance reached: t=-2.04 (10d
  hold), still short of conventional 5%. This is a real, stable, economically coherent pattern:
  momentum without confirming taker flow is a reliable underperformer.
- **CONFIRMED is fragile.** Anchor-mean is positive in every legitimate perturbation (range
  +18.5 to +47.8bps — never negative on that more data-rich metric) but never comes close to
  significance (best t=1.44, 10d hold; primary spec t=0.22). On the conservative single-anchor
  series it **flips negative in 3 of 9 legitimate perturbations** (ex-biggest-year, costs+50%, and
  its anchor-level range crosses zero in 4 of 9: 5d lookback, ex-biggest-year, ex-2020,
  costs+50%). No spec anywhere reaches even t=1.5.
- **Neighboring horizon (10d hold) is the strongest confirmed result found** (t=1.44) — still not
  significant, and the corresponding DIVERGENT result at the same horizon is the strongest
  divergent result too (t=-2.04), so the two move together rather than one specifically firming up.
- Compare to the discovery's own claim: CONFIRMED +49.8bps/t=1.03/PF1.35/pos 6-7y, DIVERGENT
  -150.9bps/t=-2.39/PF0.68/neg 7-7y. My PRIMARY reproduces the *sign* of both but at roughly
  **1/10th the confirmed magnitude and ~1/5 the confirmed t-stat**, and **~40% the divergent
  magnitude with ~65% the divergent t-stat** — a materially weaker version of the same story, not
  a close independent reproduction (contrast with a companion validation in this same batch,
  `AMIHUD_ILLIQUIDITY_PREMIUM`, which reproduced its target within ~6% of the reported net edge —
  this candidate is not in that category).

**Sanity check on the underlying momentum/PIT/cost machinery itself:** the unfiltered
long-short 7d momentum book (no CVD split) reproduces the source report's own baseline
(`XSEC_MOM_7D_LS_REPCHK`, anchor-pooled net ~+12bps, single-anchor fragile/sign-flippy) reasonably
closely here: anchor-mean net +4.0bps, single-anchor -11.5bps, sign-flip across anchors (-24.0 to
+24.8bps). This gives confidence the core pipeline (PIT eligibility, quintile ranking, costs,
declustering) is not itself the source of the CONFIRMED/DIVERGENT divergence from the discovery's
claim — the divergence is specifically about the CVD-confirmation construction (§1.3) and its
inherent fragility.

---

## 4. Declustering detail

- **N_raw** (7 anchors pooled): CONFIRMED 2,330, DIVERGENT 2,317 (book-level rebalance-period
  observations with a non-empty leg).
- **N_independent** (single-anchor, non-overlapping weekly series, anchor=0): CONFIRMED 333,
  DIVERGENT 330, spanning 2020-03-11 -> 2026-07-22.
- **Same-symbol clustering**: not a problem. Name-level observation counts (anchor=0):
  CONFIRMED 13,055 name-rebalance obs across 312 distinct symbols (top-1 share 0.87%, top-10 share
  7.54%, HHI-implied effective N ~232); DIVERGENT 5,605 obs across 311 symbols (top-1 share 0.86%,
  top-10 share 7.81%, effective N ~243). No handful of names dominates either bucket.
- **Temporal / cross-symbol systemic clustering: this is the real finding.** For CONFIRMED, the
  single most-extreme rebalance date out of 333 (by absolute book-return contribution) accounts
  for enough of the total that **removing it alone flips the primary mean from +4.46bps to
  -1.48bps**; removing the top 5 (of 333) most extreme dates brings the mean to -2.11bps. The top-5
  dates account for only ~9% of *total absolute return mass* (not concentrated in magnitude terms)
  but their *net* effect is what tips a near-zero mean either way — a classic thin-margin fragility,
  not a fat-tail-dominance problem. Because same-symbol concentration is simultaneously low (see
  above), this is genuine **cross-symbol systemic clustering**: on a handful of dates in the
  2020-2021 mania (e.g. 2020-11-11, 2021-03-03, 2021-02-17), *many different* CVD-confirmed names
  moved together (a shared regime event — legendary altcoin-mania reversals/squeezes), not one
  name driving the date. Per-year decomposition confirms this is regime-, not date-, specific:
  CONFIRMED's positive full-history total (+1,484bps summed over 333 periods) is *entirely* carried
  by 2021/2023/2024 (121%, 111%, 186% of the total respectively — each individually exceeding
  100%), while 2020/2022/2025/2026 are all net negative and partially cancel it — i.e. no
  regime-independent, stably-positive edge, an edge that appears/disappears with the market regime.
  DIVERGENT's negative total is more evenly spread (2021 and 2023 are the largest negative
  contributors at 60% and 47% of the total respectively, but 2022/2025 are genuinely positive, not
  just smaller-negative) — still regime-flavored but far less knife-edge than CONFIRMED, consistent
  with its perturbation robustness above.
- **Declustering therefore materially changes the read**: the anchor-pooled N=2,330/2,317 numbers
  look reassuring (positive/negative in every anchor for CONFIRMED/DIVERGENT respectively in the
  primary spec), but the genuinely-independent N=333/330 single-anchor series — the correct
  inference unit per the mission brief — shows the CONFIRMED side is not distinguishable from
  noise at any reasonable significance level and is not robust to dropping a single date or a
  single perturbation choice.

---

## 5. Event rate / N_required / ETA

- `independent_events_per_week` = 1.00 (fixed weekly non-overlapping grid, by construction).
- Rate stability check (both buckets, measured on the same fixed grid): full history 1.00/week;
  last 2y 1.01/week; last 1y 1.02/week; last 6m 1.04/week — **rate_stable: true**, no coverage-driven
  degradation found (the tiny drift up reflects the shrinking 2026-partial-year denominator, not a
  real change).
- `conservative_event_rate` = min(last-2y, last-1y, last-6m) ~= **1.01/week** ~= 4.36/month.
- `expected_live_edge` = 0.5 x reimplemented CONFIRMED-bucket net edge. Using the PRIMARY
  single-anchor mean (+4.46bps): **expected_live_edge ~= +2.2bps** — economically negligible, and
  itself sits inside the sign-flip fragility documented in §4 (i.e. the *starting point* for this
  haircut is not a number this validator has confidence in).
- **N_required_statistical** via block-bootstrap (block size = 8 weeks, 5,000 resamples, on the
  single-anchor series, testing one-sided alpha=5%, power=80% against the primary-spec mean):
  - **CONFIRMED**: naive mean +4.46bps (SE 20.57bps, t=0.22); block-bootstrap mean +8.93bps
    (SE 18.27bps); implied per-observation sigma ~= 333.5bps -> **N_required ~= 34,623** independent
    weekly rebalances ~= **666 years** at the conservative event rate. Effectively unfalsifiable
    within any realistic timeframe at this effect size.
  - **DIVERGENT**: naive mean -61.43bps (SE 38.76bps, t=-1.58); block-bootstrap mean -63.51bps
    (SE 38.09bps); implied per-observation sigma ~= 692.0bps -> **N_required ~= 785** independent
    weekly rebalances ~= **15.1 years** — smaller than confirmed's requirement by ~44x, but still
    far beyond the 330 periods actually available (i.e. even the more robust half of this claim
    has not yet accumulated the sample needed for its own 80%-power bar).
- `minimum_calendar_span` = 6 months (weekly cross-sectional-style alpha, per project floor table).
- `ETA_from_event_count` = N_required / conservative_event_rate:
  - CONFIRMED: 34,623 weeks / 1.01/week ~= **34,280 weeks ~= 659 years**.
  - DIVERGENT: 785 weeks / 1.01/week ~= **777 weeks ~= 14.9 years**.
- `VALIDATION_ETA` = max(ETA_from_event_count, 6 months):
  - **CONFIRMED: ETA_P50 ~= ETA_CONSERVATIVE ~= 659 years** (P50/conservative barely differ — the
    effect size is so close to zero that the point estimate itself is not meaningfully more
    optimistic than a pessimistic read; under 3 of the 9 preregistered perturbations the
    single-anchor mean is outright negative, i.e. `N_required` is formally undefined/infinite for
    those specs — the "659 years" figure is already the optimistic case).
  - **DIVERGENT: ETA_P50 ~= 14.9 years, ETA_CONSERVATIVE** (using the weakest surviving
    perturbation, EX_BIGGEST_YEAR, single-anchor mean -29.3bps, roughly half the primary
    magnitude -> N_required scales ~4x) **~= 60 years**.
- **Evidence floors (30/50/100 independent events, descriptive, not a gate)**: N_independent = 333
  (confirmed) / 330 (divergent) already clears all three floors on raw count. As with the companion
  AMIHUD validation in this batch, this should not be conflated with the much stricter
  `N_required_statistical` figure above — the *historical backtest sample* is adequately sized;
  what is undersized is the *effect relative to its own noise*, particularly for CONFIRMED.

---

## 6. Turnover / capacity (brief, given the verdict)

Turnover is 100% of the leg by construction every week (full quintile re-ranking each rebalance),
already priced into the 14bps round-trip cost assumption used throughout. Capacity was not
separately investigated in depth given the verdict below — the same $1M causal liquidity floor
used elsewhere in this project's cross-sectional work applies, and quintile leg sizes (~6 names in
2020 growing to ~50 in 2026) are comparable to other validated candidates in this batch — but this
was not the deciding factor here and does not need to be, since the edge itself does not clear the
statistical/robustness bar regardless of how much capital it could absorb.

---

## 7. Verdict

**VALIDATED_FOR_FORWARD = FALSE. Overall status: REJECTED** (for the CVD-confirmed/divergent
momentum split as specified and as claimed by the discovery).

Reasoning against the mission's explicit gate list:

- **Mechanism survives, but only qualitatively and only after a construction fix.** The most
  literal, first-choice implementation of "CVD confirms momentum" (absolute zero threshold)
  produced the *opposite* sign from the claim. A diagnosed, documented, single correction (median-
  relative threshold, matching every other filter convention already used in this codebase's prior
  work) recovers the claimed *direction*. That the sign of the entire finding hinges on this one
  construction choice is itself evidence against robustness, not for it. **MARGINAL / FAIL on
  robustness-of-construction.**
- **Causal, PIT-clean**: yes, verified by construction and cross-validated against an independent
  data pipeline (0.99998 correlation). **PASS.**
- **Costs credible**: yes, matches project convention (14bps rt/name), tested at +50%. **PASS on
  credibility, FAIL on survival** — costs+50% flips CONFIRMED's single-anchor mean negative.
- **Positive net expectation for the confirmed (tradeable) side, specifically**: technically true
  in the primary spec (+4.5bps single-anchor, +25.5bps anchor-mean) but economically negligible,
  not statistically distinguishable from zero in any spec tried (max t=1.44 across the entire
  battery), and **negative in 3/9 preregistered perturbations** on the conservative series.
  **FAIL.**
- **No hidden concentration**: same-symbol concentration is fine, but there **is** a hidden
  temporal/cross-symbol concentration — the confirmed bucket's entire primary-spec edge depends on
  not excluding a single date out of 333. **FAIL.**
- **Reasonable stability across perturbations**: divergent side passes this; confirmed side does
  not (4/9 perturbations show an anchor-level sign flip, 3/9 flip the single-anchor point
  estimate). **FAIL** (on the side that matters for a forward position).
- **No blocking implementation bug**: none found — the reversal under the abs-zero-threshold
  spec was traced to a real, diagnosable, economically-explicable data property (structural
  negative mean of net taker-buy fraction across the panel), not a coding error, and the fix is
  principled rather than a fit to the desired outcome. **PASS** on "not a bug," but this doesn't
  rescue the underlying weak effect size.
- **Capacity compatible / declustering applied / economically understandable**: declustering
  applied and is what surfaces the fragility (§4); capacity not independently disqualifying;
  economic story for *why* flow-divergent momentum underperforms (leveraged/thin moves without
  real participation are more reversal-prone) is plausible and its empirical signature (DIVERGENT)
  is the more robust of the two buckets — but "the filter is coherent" is not the same as "the
  tradeable book that results from it is coherent," and it is the latter this validation is scoped
  to certify.

**What is and is not supported by this independent reimplementation:**

1. **DIVERGENT (avoid) side is directionally real and reasonably robust** — negative in every
   legitimate perturbation, no anchor-level sign flips anywhere, best single-anchor t=-2.04 (10d
   holding horizon). This is not itself a standalone position (per the mission's own framing, and
   per the source report, it's a "don't trade" signal), and even it needs ~15 more years of history
   at current effect size to reach 80%-power confirmation on its own primary-spec magnitude — so it
   is not being promoted here either, but it is the part of this claim most likely to be real.
2. **CONFIRMED (tradeable) side does not clear a reasonable bar for forward deployment.** Weak,
   never significant, sign-flips under a third of preregistered perturbations, and its primary-spec
   point estimate is entirely dependent on not excluding one out of 333 historical weeks. The
   discovery's claimed +49.8bps/t=1.03 does not reproduce at comparable strength; this
   reimplementation finds roughly +4.5bps/t=0.22 at best, and negative under several legitimate
   variants.

**Recommendation**: do not promote to forward/shadow deployment. If this mechanism is revisited,
the DIVERGENT/avoid side (not the confirmed/long side) is the more promising thread, and any future
work should treat the CVD-reference-level choice (§1.3) as a first-class design decision to be
stress-tested up front, not an afterthought — this validation's single biggest lesson is that the
entire sign of this construction turned on it.

**ETA (informational only, not itself the basis for the FAIL above):**
- CONFIRMED: ETA_P50 ~= ETA_CONSERVATIVE ~= 659 years (formally undefined/infinite under 3 of 9
  perturbations).
- DIVERGENT: ETA_P50 ~= 14.9 years, ETA_CONSERVATIVE ~= 60 years.
- minimum_calendar_span (practical monitoring floor, moot given the verdict) = 6 months.
