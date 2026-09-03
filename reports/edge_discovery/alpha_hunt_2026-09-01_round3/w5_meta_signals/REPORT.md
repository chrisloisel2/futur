# W5 — Meta Signals (Alpha Hunt Round 3, 2026-09-01)

Axis F: signals whose economic value sits in a **different decision layer** than a normal
BUY/SELL alpha — ENABLE/DISABLE, REDUCE/INCREASE_RISK, SELECT_ASSET, SELECT_HORIZON. Read-only
on all existing data/reports; no new data collection; `src/institutional/` and
`configs/live_alpha_registry.yaml` untouched (read for context only, e.g.
`VOL_FORECAST_LAYER_V1`'s risk-overlay spec as the template for this axis).

This report picks up an earlier launch of this exact task that was interrupted by a session-wide
rate limit before writing anything to disk. Partial work was recovered from scratch
(`g1_liq_cascade_meta.py`, already fully run with results in `evidence/g1_liq_cascade_meta.json`;
`g2_build_daily_panel.py`, already run; `g3_xsmom_meta.py`, written but not yet executed) and
reused as-is — Group 1 and the daily OHLCV panel needed no rework. Group 3 was run for the first
time in this session. Groups 4-6 are new.

## 1. Methodology

**Core A/B construction.** For every candidate: define a base signal exactly as it already
exists (a frozen live alpha, or a round-2 PROMISING mechanism, reproduced from its own source
code/data, not reinvented). **WITHOUT** = the base signal's existing/naive policy (always
enabled, flat sizing, fixed horizon, or "trade every candidate" for SELECT_ASSET). **WITH** =
the same base signal, same costs, same population, but gated/sized/routed by the meta-signal.
Both arms are evaluated on the **same net-of-cost bps series** so the delta isolates the
meta-signal's marginal contribution, not a different cost model or a different base-signal
definition.

**PIT and train/test discipline.** Every gated (threshold-based) test fits its threshold and
its *direction* (which side of the threshold is better) on a **train half** (first ~50-60% of
the population by time or by index, matching each base signal's own established split
convention) and evaluates WITH vs WITHOUT purely on the **held-out test half** — this is the
number that counts. A second "full-context" number (both halves, using the train-fit rule) is
reported alongside for scale/economic-plausibility context only, and is explicitly labeled as
such — it is not evidence on its own, since the direction was picked partly by looking at that
population. Continuous (non-thresholded) sizing overlays — e.g. weight-by-|z-score| — don't
carry the same in-sample-fitting risk and are evaluated on the full sample, flagged as such in
each test.

**Decluster and N.** Every base signal reports **N_raw** (every row matching the entry filter)
and **N_independent** (same-symbol/same-mechanism episodes whose holding windows overlap
collapsed to the first of each cluster) — this project's round-2 scoreboard flagged declustering
failures as the single most common trap, so it's done first here for every base signal that
didn't already arrive pre-declustered from its own source report.

**Costs.** Liquidation-cascade base (Group 1): 14bps round-trip taker (this project's stated
convention for that dataset, matches the existing `LIQ_CASCADE_REPEAT_V1` registry entry and
round-2 W2's own cost model). Cross-sectional momentum (Group 3): 10bps round-trip
(5bps taker one-way x2, single-name long-only, matches round-2 W1's and the live
`CROSS_SECTIONAL_MOMENTUM_LIVE_V2` registry entry's stated convention). Funding-basis
disagreement (Group 4): 14bps flat (round-2 W4's stated base cost for this 2-leg calendar-basis
mechanism). Applied identically in WITH and WITHOUT.

**Group 5 uses a real existing artifact, not a fitted proxy.** Instead of re-deriving a vol
regime from raw OHLCV, Group 5 uses `VOL_FORECAST_LAYER_V1`'s own daily forecast output
(`reports/live_alpha_lab/VOL_FORECAST_LAYER_V1/decisions.parquet`, a FROZEN, already-built
institutional forecast — read-only, not modified) as a cross-strategy gate on Groups 1 and 3's
base signals. This directly tests whether a vol-regime signal built for one purpose (risk-overlay
sizing on BTC options) transfers as a meta-signal for unrelated alphas.

**Status vocabulary:** DEAD (negative or ~zero delta, or sign unstable between test/full),
WEAK (small/marginal positive delta, low significance, or improves-but-still-net-negative),
DATA_LIMITED (N too thin for the train/test split to mean anything either way), PROMISING
(material positive OOS delta, checked for single-year/single-regime concentration),
PROMISING-WITH-CAVEAT (as above but N is thin enough, or corroboration weak enough, that this is
a lead not a confirmed effect). Nothing here is elevated past PROMISING — per this project's
standing discipline, no mechanism moves toward `INDEPENDENT_CONFIRMATION` from a single-worker,
single-split backtest.

**Known caveat carried through Group 3 explicitly.** The long-only top-quintile weekly
cross-sectional-momentum baseline (chosen because it's the closest reconstruction of the live
`CROSS_SECTIONAL_MOMENTUM_LIVE_V2` candidate, itself unresolved re: net-bps validation — its own
registry entry states "non applique en Mode A ... pas directement applicable a cette
reconstruction long-only") produces a baseline mean of +263.96bps/week (N=131, PF=1.63, t=2.09)
— **much larger and fatter-tailed** than round-2 W1's more conservative long-short
quintile-spread number (+89.1bps, t=2.60). Diagnosed: this is not a bug — the top-quintile
long-only basket in 2020-2021 (thin, ~65-110-name universe, alt-season) shows genuine
multi-thousand-bps weekly swings (verified against raw closes), and the same construction turns
**negative** in 2025 (-333bps avg) and 2026 (-177bps avg). All Group 3 absolute-magnitude numbers
should be read as regime-dominated and noisy; the meta-signal **deltas** are the meaningful
output, and were spot-checked (see 3.2) to confirm they aren't single-year artifacts before
being called PROMISING.

---

## 2. Group 1 — Liquidation-cascade-repeat (`LIQ_CASCADE_REPEAT_V1`)

**Base signal:** exact reproduction of the frozen `LIQ_CASCADE_REPEAT_V1` entry rule
(`n_events_sym_24h>=2`, LONG_CASCADE -> LONG @ fwd_4h) from `data/events/liq_cascade_dataset.parquet`.
Cost 14bps RT. **Baseline (WITHOUT, always-enabled):** N_raw=5,457, N_independent=4,660
(gap>=4h decluster), mean **+9.87bps**, PF 1.099, win 51.4%, t=1.78. By year: strong 2021/2023,
weak 2022 (-33.1), flat 2025 (-0.03), recovering 2026 (+19.2).

| id | meta-signal | WITH vs WITHOUT (OOS test half) | full-context | status |
|---|---|---|---|---|
| T1.1 | ENABLE when BTC 24h vol > train-picked threshold | WITHOUT=3.46 (n=2330) -> WITH=21.33 (n=949), **delta=+17.87** | delta=+13.48 (n=2114) | **PROMISING** |
| T1.2 | ENABLE when market-wide cascade breadth (n_events_mktwide_30m) > 1 | WITHOUT=3.46 -> WITH=6.64 (n=1002), delta=+3.18 | delta=+18.32 (n=1908) | WEAK |
| T1.3 | SELECT_ASSET: on concurrent-candidate days, pick highest-vol_24h symbol | selected=-7.17 (n=748) vs naive-all=9.40, delta=**-16.57** | — | DEAD |
| T1.4 | SELECT_HORIZON {1h,4h,8h} by funding_z30 bucket, train-picked | WITHOUT(fixed 4h)=20.66 -> WITH(picked)=10.05, delta=**-10.61** | — | DEAD |
| T1.5 | REDUCE/INCREASE_RISK: size by \|oi_drop_z\| (continuous, full-sample) | flat=9.87 -> weighted=15.81, delta=+5.94, PF 1.16 | — | WEAK (no OOS split) |
| T1.6 | ENABLE by UTC session, train-picked best=ASIA | WITHOUT=3.46 -> WITH=12.18 (n=653), delta=+8.72, t=0.99 | — | WEAK |
| T1.7 | ENABLE by dist_low_24h (borrows `LIQ_CASCADE_FAR_FROM_LOW_V1`'s own feature) | WITHOUT=3.46 -> WITH=1.16 (n=1219), delta=**-2.30** | delta=+5.84 (sign-unstable vs OOS) | DEAD |
| T1.8 | ENABLE by day-of-week (weekday vs weekend) | weekday=15.29 (n=3901) vs weekend=-18.01 (n=759), delta=+5.42 vs unconditional | — | WEAK, exploratory (7-bucket multiple-testing risk, no OOS) |
| T1.9a-c | ENABLE_ALPHA rescue attempt on the currently-DEAD "onset" (1st-hit) sleeve, 3 gates tried | baseline=-10.60 (n=13,135); calm-vol gate=-15.84; far-from-low gate=-8.99; isolated-breadth gate=-13.59 — **none flip positive** | — | **DEAD (clean)** |
| T1.10 | REDUCE_RISK: inverse-size by \|ls_ratio_z\| crowding (continuous, full-sample) | flat=11.26 -> weighted=4.80, delta=**-6.46** | — | DEAD |
| T1.11 | Generalization: T1.1's rule, unrefit, applied to sibling `SHORT_SQUEEZE_EXHAUSTION` (BLOCKED, context only) | baseline=11.14 (n=2381) -> gated=20.85 (n=1480), delta=+9.71 | — | corroboration for T1.1, not a standalone candidate |
| T1.12 | ENABLE by \|taker_z\| extremity (aggressive-flow confirmation) | WITHOUT=9.48 (n=2192) -> WITH=-3.53 (n=1111), delta=**-13.01** | delta=-3.94 | DEAD |

**T1.1 drawdown check** (cumulative-bps, sequential order): WITHOUT max DD -26,848bps (n=4,660)
vs WITH(gated) max DD -22,840bps (n=2,114 full-context) — gate reduces both the tail (2022's
worst year) and the cumulative drawdown, consistent with the mean-bps improvement.

---

## 3. Group 3 — Cross-sectional momentum (`CROSS_SECTIONAL_MOMENTUM_LIVE_V2`-shaped)

**Base signal:** 7d trailing-return rank, top-quintile, long-only, weekly non-overlapping
rebalance, liq>=$1M 30d-median causal universe (312-symbol PIT panel via `data_v2/normalized`).
Cost 10bps RT. **Baseline (WITHOUT):** N=131 weeks, mean **+263.96bps/week**, PF 1.633, t=2.09
(see caveat in section 1 — regime-dominated, treat delta not magnitude as the signal).

| id | meta-signal | WITH vs WITHOUT (OOS test half) | full-context | status |
|---|---|---|---|---|
| T2.1 | ENABLE when BTC 20d realized vol > train-picked threshold | WITHOUT=156.81 (n=66) -> WITH=336.04 (n=14), **delta=+179.23**, PF 2.48 vs 1.45 | delta=+448.25 (n=46) | **PROMISING-WITH-CAVEAT** (thin N=14, but spot-checked non-single-year, see 3.2) |
| T2.2 | ENABLE by cross-sectional return-dispersion regime | WITHOUT=156.81 -> WITH=89.75 (n=53), delta=**-67.06** | delta=+36.56 (sign-unstable vs OOS) | DEAD |
| T2.3 | ENABLE by market breadth (trailing 7d % names positive) | WITHOUT=156.81 -> WITH=735.12 (n=24), **delta=+578.31**, PF 4.36 vs 1.45 | delta=+390.56 (n=56) | **PROMISING-WITH-CAVEAT** (thin N=24, spot-checked, see 3.2) |
| T2.4 | REDUCE/INCREASE_RISK: 0.5x high-BTC-vol / 1.5x low-vol weeks (continuous) | flat=263.96 -> weighted=158.60, delta=**-105.36** | — | DEAD (see 3.3 discussion) |
| T2.5 | SELECT_HORIZON {7d,14d} by BTC vol regime, train-picked | WITHOUT(fixed 7d)=121.28 (n=62) -> WITH(picked)=237.83 (n=62), **delta=+116.55** | — | PROMISING (shares BTC-vol driver with T2.1, not fully distinct) |

**T2.1/T2.3 drawdown check:** WITHOUT max DD -12,544bps (n=131 full). T2.1-gated: -2,246bps
(n=46 full-context). T2.3-gated: -4,412bps (n=56 full-context). Both gates cut drawdown by
64-82% alongside the mean-bps improvement — directionally consistent, not just a mean-shift
with a hidden worse tail.

### 3.2 — Spot-check: are T2.1/T2.3 just "trade only the good years"?

Checked directly (not just trusting the aggregate delta): T2.1's OOS-selected weeks (n=14) span
2023(1)/2024(5)/2025(3)/2026(5) — **not** concentrated in one year. Critically, in the two years
where the *unconditional* test population is net negative (2025: -343bps avg, 2026: -178bps
avg), the T2.1-gated subset is still net **positive** in both (+96bps, 2025; +40bps, 2026) — the
gate is doing real work inside bad years, not just picking a good year. T2.3 shows the same
pattern for 2023/2024 (large improvement) and 2025 (less-bad: -116 vs -343 unconditional), with
only 2026 (n=1, pure noise) worse. This is the strongest evidence in this report that a
meta-signal is doing something structural rather than curve-fit.

### 3.3 — T2.4: why does inverse-vol sizing hurt?

Standard risk-management intuition says "size down in high-vol regimes." Here it does the
opposite of help (delta=-105bps) because the base signal's own biggest positive outlier weeks
(2021 alt season) co-occur with *elevated* BTC vol — the same regime T2.1 independently found to
be enabling, not disabling. A REDUCE_RISK overlay built from a generic vol-targeting prior
directly fights this base signal's own skew. Useful cautionary finding: for this specific base
signal, "vol regime" is better used as an ENABLE/DISABLE gate (T2.1, positive) than as an
inverse-sizing overlay (T2.4, negative) — same feature, opposite decision-layer use, opposite
sign.

---

## 4. Group 4 — Funding-vs-quarterly-basis disagreement (M7, `derivatives_backfill` BTC/ETH curve)

**Base signal:** exact re-derivation of round-2 W4's M7 mechanism (`disagreement =
funding_ann_pct - basis_near_ann`, decile RICH/CHEAP classify, threshold fit on first 60% of
eligible days, episodes built and evaluated **only on the held-out last 40%** — same discipline
as W4's own script, reproduced independently against `evidence/all_results.json` to confirm
exact match: BTC k14d n=24/net+7.68bps, ETH k14d n=25/net+15.26bps, both match W4's published
numbers exactly). k14d chosen (not k7d, which W4 also tested and found roughly breakeven) because
it's W4's actually-PROMISING horizon. Cost 14bps flat. **Baseline (WITHOUT, BTC+ETH pooled):**
N=49 (already independent by construction — one non-overlapping episode per regime run), mean
**+11.55bps**, PF 2.97, t=2.46, win 71.4%.

| id | meta-signal | WITH vs WITHOUT | status |
|---|---|---|---|
| T3.1 | SELECT_ASSET: on 10 concurrent BTC+ETH weeks, pick larger-\|disagreement\| leg (a priori rule) | naive-avg-both=14.85 -> selected=11.24, delta=**-3.61** | DATA_LIMITED (n=10) / DEAD |
| T3.2 | REDUCE/INCREASE_RISK: size by within-symbol \|disagreement\| z-score (continuous) | flat=11.55 -> weighted=4.54, delta=**-7.01** | DEAD |
| T3.3 | ENABLE by BTC 20d realized-vol regime, train-picked | WITHOUT=5.66 (n=25) -> WITH=6.47 (n=17), delta=+0.81 | DATA_LIMITED (n=17, ~noise) |

**Clean negative result.** All three meta-signal formulations tried on M7 are DEAD or
DATA_LIMITED. Consistent with round-2 W4's own conclusion that this calendar-basis dataset is
close to exhausted once properly declustered — there wasn't much more to extract by adding a
decision layer on top, and the base sample (N=49) is thin enough that further conditioning
mostly just adds noise. Reported honestly rather than searching for a parameterization that
"worked."

---

## 5. Group 5 — Cross-strategy vol-regime transfer (`VOL_FORECAST_LAYER_V1` as a gate)

**Meta-signal source:** `VOL_FORECAST_LAYER_V1`'s own daily forecast (`combined_forecast_z`,
`iv_regime`) — a FROZEN, already-built institutional forward-RV forecast (BTC options
RV/IV-spread + far-OTM-put-share + block-flow, combined; not modified here, read-only). Tests
whether this existing signal transfers as an ENABLE/DISABLE gate for **other** alphas, distinct
from Groups 1/3's own raw-OHLCV vol proxies.

| id | base signal | gate | WITH vs WITHOUT (OOS) | status |
|---|---|---|---|---|
| T4.1 | Group 3 xsmom basket | `combined_forecast_z` regime, train-picked | WITHOUT=-253.44 (n=32) -> WITH=-435.75 (n=16), delta=**-182.31** | DEAD |
| T4.1b | Group 3 xsmom basket | `iv_regime` categorical (HIGH picked on train) | WITHOUT=-253.44 -> WITH=-123.73 (n=13), delta=+129.71 | WEAK — reduces the loss, but WITH is still net negative; a damage-limiting DISABLE signal, not an edge-creating ENABLE one |
| T4.2 | Group 1 liq-cascade-repeat | `combined_forecast_z` regime, train-picked | WITHOUT=14.70 (n=1836) -> WITH=-11.67 (n=917), delta=**-26.37** | DEAD |

**Important negative/methodological finding.** Group 5's vol-regime gate is built from
`VOL_FORECAST_LAYER_V1`'s BTC options-derived forward-RV *forecast*. Group 1's T1.1 gate (the
report's headline PROMISING result) is built from BTC 24h *realized* vol on the liquidation
dataset's own timestamps. Both target "is BTC in a stormy regime," but T4.2 (forecast-based)
finds the **opposite sign** from T1.1 (realized-based) on the *same base signal*
(liq-cascade-repeat): T1.1 says high realized vol enables the edge; T4.2 says the forecast-based
proxy for the same idea disables it OOS. This is not necessarily a contradiction of T1.1 (it
could equally be a genuine gap between "currently stormy" and "forecast to stay stormy," or
`combined_forecast_z`'s BTC-options-only construction not transferring to a cross-symbol
liquidation dataset) — but it is a concrete demonstration that **a vol-regime meta-signal built
for one strategy does not automatically transfer to another, even when both are nominally "the
same" market condition**. Don't assume fungibility of regime signals across strategies without
testing each transfer explicitly, exactly as done here.

Xsmom's test window under `VOL_FORECAST_LAYER_V1`'s coverage (2023-03 onward only, N=1,236 days)
happens to land almost entirely inside the base signal's already-identified bad regime (section
1 caveat) — both WITHOUT and WITH are net negative for T4.1/T4.1b, which limits how much can be
concluded either way from this specific test window.

---

## 6. Summary table

`N_raw`/`N_independent` are the **base signal's** population (before meta-signal gating); the
WITH arm's own N is in the per-test tables above. `PF_with`/`PF_without` blank = continuous
sizing overlay, PF not computed for the weighted series (time-boxed; mean-bps delta is the
reported evidence for those rows instead).

| candidate_id | family | economic_risk_factor | mechanism | base_signal | N_raw | N_independent | delta_net_bps (OOS) | PF_without | PF_with | stability | distinctness | status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| W5-T1.1 | LIQUIDATION_FAMILY | LIQUIDATION_FAMILY + vol-regime meta | ENABLE by BTC realized-vol regime | LIQ_CASCADE_REPEAT_V1 | 5,457 | 4,660 | +17.87 | 1.032 | 1.167 | consistent full-context; corroborated by T1.11 sibling transfer | distinct from T4.2 (same idea, forecast-vol, opposite sign) | **PROMISING** |
| W5-T1.2 | LIQUIDATION_FAMILY | LIQUIDATION_FAMILY + breadth meta | ENABLE by market-wide cascade breadth | LIQ_CASCADE_REPEAT_V1 | 5,457 | 4,660 | +3.18 | 1.032 | 1.056 | weak OOS signif. (t=0.39) | overlaps T1.1 partially (both "stress" proxies) | WEAK |
| W5-T1.3 | LIQUIDATION_FAMILY | LIQUIDATION_FAMILY + liquidity meta | SELECT_ASSET by vol_24h on concurrent days | LIQ_CASCADE_REPEAT_V1 | 4,375 | 4,375 | -16.57 | 1.092 | 0.937 | consistent negative | distinct | DEAD |
| W5-T1.4 | LIQUIDATION_FAMILY | LIQUIDATION_FAMILY + funding meta | SELECT_HORIZON by funding_z30 bucket | LIQ_CASCADE_REPEAT_V1 | 1,028 | 1,028 | -10.61 | 1.28 | 1.152 | consistent negative | distinct | DEAD |
| W5-T1.5 | LIQUIDATION_FAMILY | LIQUIDATION_FAMILY + OI-magnitude meta | REDUCE/INCREASE_RISK by \|oi_drop_z\| | LIQ_CASCADE_REPEAT_V1 | 4,660 | 4,660 | +5.94 | 1.099 | 1.158 | no OOS split (continuous) | distinct | WEAK |
| W5-T1.6 | LIQUIDATION_FAMILY | LIQUIDATION_FAMILY + session meta | ENABLE by UTC session (ASIA best) | LIQ_CASCADE_REPEAT_V1 | 2,330 | 2,330 | +8.72 | 1.032 | 1.124 | t=0.99, marginal | distinct | WEAK |
| W5-T1.7 | LIQUIDATION_FAMILY | LIQUIDATION_FAMILY (self-referential) | ENABLE by dist_low_24h | LIQ_CASCADE_REPEAT_V1 | 4,660 | 4,660 | -2.30 | 1.032 | 1.011 | sign flips OOS vs full | borrows sibling LIQ_CASCADE_FAR_FROM_LOW_V1's own feature — not distinct | DEAD |
| W5-T1.8 | LIQUIDATION_FAMILY | LIQUIDATION_FAMILY + calendar meta | ENABLE by weekday vs weekend | LIQ_CASCADE_REPEAT_V1 | 4,660 | 4,660 | +5.42 | 1.099 | 1.159 | no OOS split, 7-bucket search | distinct | WEAK |
| W5-T1.9a | LIQUIDATION_FAMILY | LIQUIDATION_FAMILY (onset rescue) | ENABLE onset by calm-BTC-vol gate | LIQ_CASCADE onset (1st-hit) | 13,135 | 13,135 | -5.24 (vs baseline -10.60) | 0.882 | 0.799 | consistent negative | distinct | DEAD |
| W5-T1.9b | LIQUIDATION_FAMILY | LIQUIDATION_FAMILY (onset rescue) | ENABLE onset by far-from-low gate | LIQ_CASCADE onset (1st-hit) | 13,135 | 13,135 | +1.61 (vs baseline -10.60) | 0.882 | 0.903 | still net negative | distinct | DEAD |
| W5-T1.9c | LIQUIDATION_FAMILY | LIQUIDATION_FAMILY (onset rescue) | ENABLE onset by isolated-breadth gate | LIQ_CASCADE onset (1st-hit) | 13,135 | 13,135 | -2.99 (vs baseline -10.60) | 0.882 | 0.842 | consistent negative | distinct | DEAD |
| W5-T1.10 | LIQUIDATION_FAMILY | LIQUIDATION_FAMILY + crowding meta | REDUCE_RISK by \|ls_ratio_z\| crowding | LIQ_CASCADE_REPEAT_V1 | 4,585 | 4,585 | -6.46 | 1.114 | — | no OOS split (continuous) | distinct | DEAD |
| W5-T1.11 | LIQUIDATION_FAMILY | LIQUIDATION_FAMILY (generalization check) | T1.1's rule, unrefit, on sibling SHORT_SQUEEZE_EXHAUSTION | SHORT_SQUEEZE_EXHAUSTION (BLOCKED) | 2,719 | 2,381 | +9.71 | 1.102 | 1.173 | corroborates T1.1 | not a standalone candidate | context-only |
| W5-T1.12 | LIQUIDATION_FAMILY | LIQUIDATION_FAMILY + flow meta | ENABLE by \|taker_z\| extremity | LIQ_CASCADE_REPEAT_V1 | 4,384 | 4,384 | -13.01 | 1.091 | 0.966 | consistent negative | distinct | DEAD |
| W5-T2.1 | CROSS_SECTIONAL_XSMOM | CROSS_SECTIONAL_XSMOM + vol-regime meta | ENABLE by BTC 20d realized-vol regime | xsmom top-quintile weekly | 131 | 131 | +179.23 | 1.446 | 2.483 | spot-checked non-single-year (3.2) | opposite decision-layer use vs T2.4 | **PROMISING-WITH-CAVEAT** |
| W5-T2.2 | CROSS_SECTIONAL_XSMOM | CROSS_SECTIONAL_XSMOM + dispersion meta | ENABLE by cross-sectional dispersion regime | xsmom top-quintile weekly | 131 | 131 | -67.06 | 1.446 | 1.241 | sign flips OOS vs full | distinct | DEAD |
| W5-T2.3 | CROSS_SECTIONAL_XSMOM | CROSS_SECTIONAL_XSMOM + breadth meta | ENABLE by market breadth regime | xsmom top-quintile weekly | 131 | 131 | +578.31 | 1.446 | 4.359 | spot-checked non-single-year (3.2) | distinct from T2.1 (breadth != vol, weak corr) | **PROMISING-WITH-CAVEAT** |
| W5-T2.4 | CROSS_SECTIONAL_XSMOM | CROSS_SECTIONAL_XSMOM + vol-regime meta (sizing) | REDUCE/INCREASE_RISK inverse-vol sizing | xsmom top-quintile weekly | 131 | 131 | -105.36 | 1.633 | — | no OOS split (continuous) | same feature as T2.1, opposite sign (3.3) | DEAD |
| W5-T2.5 | CROSS_SECTIONAL_XSMOM | CROSS_SECTIONAL_XSMOM + vol-regime meta (horizon) | SELECT_HORIZON {7d,14d} by BTC vol regime | xsmom top-quintile weekly | 124 | 124 | +116.55 | 1.324 | 1.697 | consistent | shares driver with T2.1, not fully independent | PROMISING |
| W5-T3.1 | CALENDAR_BASIS_CARRY | FUNDING_BASIS_CARRY + cross-asset meta | SELECT_ASSET: larger \|disagreement\| leg | M7 funding-basis-disagreement (BTC+ETH) | 49 | 49 | -3.61 | 3.816 | 2.668 | n=10, too thin | distinct | DATA_LIMITED |
| W5-T3.2 | CALENDAR_BASIS_CARRY | FUNDING_BASIS_CARRY + magnitude meta | REDUCE/INCREASE_RISK by \|disagreement\| z-score | M7 funding-basis-disagreement | 49 | 49 | -7.01 | 2.968 | — | no OOS split (continuous) | distinct | DEAD |
| W5-T3.3 | CALENDAR_BASIS_CARRY | FUNDING_BASIS_CARRY + vol-regime meta | ENABLE by BTC 20d realized-vol regime | M7 funding-basis-disagreement | 49 | 49 | +0.81 | 3.031 | 3.08 | n=17, ~noise | distinct | DATA_LIMITED |
| W5-T4.1 | CROSS_STRATEGY_VOL_TRANSFER | VOLATILITY_FAMILY -> CROSS_SECTIONAL_XSMOM | ENABLE xsmom by VOL_FORECAST_LAYER_V1 combined_forecast_z | xsmom top-quintile weekly | 63 | 63 | -182.31 | 0.404 | 0.254 | consistent negative | tests transfer of an existing engine's output | DEAD |
| W5-T4.1b | CROSS_STRATEGY_VOL_TRANSFER | VOLATILITY_FAMILY -> CROSS_SECTIONAL_XSMOM | ENABLE xsmom by VOL_FORECAST_LAYER_V1 iv_regime | xsmom top-quintile weekly | 63 | 63 | +129.71 | 0.404 | 0.641 | WITH still net negative | same source as T4.1, categorical variant | WEAK |
| W5-T4.2 | CROSS_STRATEGY_VOL_TRANSFER | VOLATILITY_FAMILY -> LIQUIDATION_FAMILY | ENABLE liq-cascade-repeat by VOL_FORECAST_LAYER_V1 combined_forecast_z | LIQ_CASCADE_REPEAT_V1 | 4,660 | 3,671 (matched) | -26.37 | 1.141 | 0.890 | consistent negative, opposite sign of T1.1 | directly comparable to T1.1 (same base, different vol proxy) — the sign flip IS the finding | DEAD |

**TOTAL_MECHANISMS_TESTED = 25**

---

## 7. Top findings (prose)

**1. The one candidate ready for a second worker's independent look: T1.1 — enable
`LIQ_CASCADE_REPEAT_V1` only when BTC 24h realized vol is elevated.** OOS delta +17.87bps on a
non-trivial N=949 gated trades (out of 2,330 test-half trades), PF improves from 1.03 to 1.17,
drawdown improves from -26,848 to -22,840 cumulative bps. Independently corroborated by T1.11:
the *exact same rule*, unrefit, transferred to a different (currently BLOCKED)
sibling engine (`SHORT_SQUEEZE_EXHAUSTION`) and still improved it (+9.71bps) — this is the kind
of out-of-mechanism generalization that's hard to get by chance. Economic story is intuitive:
liquidation cascades pay more when the broader market is already under stress, i.e. exhaustion
cascades are more likely to mark real capitulation rather than noise when BTC itself is volatile.

**2. Group 3's two vol/breadth-regime ENABLE gates (T2.1, T2.3) show the largest deltas in the
whole sweep, but carry the report's biggest caveat.** N=14 and N=24 OOS-gated weeks respectively
— thin by any standard. What elevates them above "probably curve-fit" is the explicit
spot-check in section 3.2: the gated subset stays profitable *inside* the two years (2025, 2026)
where the ungated baseline goes negative, not just "trades only in the good year." Still, with a
single train/test split and N this small, these are **leads for a second worker to re-derive
with k-fold or walk-forward validation**, not confirmed effects.

**3. Vol-regime meta-signals are decision-layer-specific and not fungible across strategies or
representations — demonstrated three separate ways in this report, not asserted.** (a) T2.1
(ENABLE by BTC vol, delta=+179) vs T2.4 (REDUCE_RISK inversely by the *same* BTC vol feature on
the *same* base signal, delta=-105) — opposite sign from opposite decision-layer use of one
feature. (b) T1.1 (realized BTC vol enables liq-cascade-repeat, delta=+18) vs T4.2
(`VOL_FORECAST_LAYER_V1`'s *forecast* of BTC vol, same base signal, delta=-26) — realized and
forecast vol are not interchangeable gates even when both notionally measure "is the market
stormy." (c) T4.1/T4.1b show `VOL_FORECAST_LAYER_V1`'s output doesn't rescue xsmom either. Net
practical implication: **don't reuse one engine's vol-regime output as a plug-in gate for
another strategy without testing the specific transfer** — it has to be re-derived and
re-validated per base signal, which is exactly what this axis is for.

**4. Two clean, honest negative results worth keeping on record so nobody re-runs them.**
Group 4 (funding-basis-disagreement, M7): all three meta-signal formulations tried (SELECT_ASSET,
magnitude-sizing, vol-regime ENABLE) are DEAD or DATA_LIMITED — the base sample (N=49) is thin
enough that this mechanism is close to fully mined out, consistent with round 2 W4's own
conclusion. T1.9 (liquidation "onset" rescue): three different ENABLE gates tried against the
already-known-negative first-hit sleeve, none flip it positive — the round-2 structural finding
("cascades only pay on repeat, not on first hit") holds up against every meta-signal angle tried
here; onset should stay excluded rather than rescued by conditioning.

**5. SELECT_ASSET and SELECT_HORIZON, as decision layers, mostly didn't pay off in this sweep**
(T1.3, T1.4, T3.1 all DEAD/DATA_LIMITED) — the one exception, T2.5 (SELECT_HORIZON for xsmom by
BTC vol regime, delta=+116.55, N=62), shares its driver with the already-flagged T2.1, so it's
corroborating evidence for "BTC vol regime matters to xsmom" more than a fully independent
finding.

**Overall**, this axis produced fewer PROMISING candidates than a typical direct-alpha sweep (as
expected — it's inherently lower-yield since every test needs a working base signal to layer
onto), but the ones that did surface (T1.1 particularly) are backed by both an OOS split and an
out-of-mechanism generalization check, which is a higher evidence bar than most single-mechanism
discovery-stage findings get.
