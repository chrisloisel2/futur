# PREREGISTRATION — XSEC_RESIDUAL_MOMENTUM_14D

**Worker:** V3, Alpha Validation Factory wave 2. **Written:** 2026-09-03 ~10:55 UTC, BEFORE any
return figure was computed. Shares panel, PIT universe, grid, costs, declustering units,
bootstrap and resource rules with `../XSEC_MOMENTUM_HORIZON_EXTENSION/PREREGISTRATION.md`
(sections 2, 4, 6, 7, 8 apply verbatim unless overridden here).

**Claim under test** (`w2_cross_sectional/REPORT.md` row XSEC_RESID_MOM_14D, evidence NOT
opened): "14d beta-to-BTC-stripped residual momentum", long-short quintile (28 bps cost),
gross +92.8 / net +64.8 bps per 14 d, PF 1.34, t 0.87, N_indep 167, 5/7 years, anchors [55, 167]
gross. No beta window is published → the rule below is preregistered, not fitted.

## 1. PRIMARY_SPEC — frozen

| Item | Rule |
|---|---|
| Market factor | BTCUSDT daily log return `b_t` from the same daily panel. |
| Beta | OLS slope (with intercept) of the symbol's daily log return `r_t` on `b_t` over the **60 calendar days ending at d inclusive** `[d−59, d]`, requiring >= 40 valid pairs (else ineligible for this candidate that date, counted). Formation window lies inside the estimation window (standard residual-momentum construction, Blitz-Huij-Martens 2011). Causal: nothing after d enters. |
| Signal | `resid14(d) = Σ_{t=d−13..d} (r_t − β_i(d) · b_t)` = 14-day log momentum minus β × BTC 14-day log momentum ("beta-stripped momentum"; the intercept is NOT subtracted — subtracting it would add a 60-day mean-reversion term; that variant is perturbation P2). |
| Construction | Rank eligible names by `resid14` descending. **LS**: LONG top quintile / SHORT bottom quintile, equal weight, 14-day non-overlapping holding, cost 28 bps (net) / 42 bps (stress = +50 %; the briefing's "−28 stress" convention for 14-bps mechanisms maps to +50 % here). **LONG-ONLY LEG** reported separately with its own gate: top quintile vs equal-weighted eligible universe (excess), cost 14 / 28. |
| Verdict statistic | Per project SHORT policy the deliverable is the LONG leg: verdict criteria (§7 of the sibling prereg) are applied to the **LONG-leg excess** (`excess_net14`, `t_L3`, bootstrap, years, ex-2021, anchors). The LS spread is reported with the same criteria as a relative-value variant (short leg = hedge, AMIHUD precedent) but cannot validate the candidate on its own if the LONG leg fails. |
| Universe / grid / winsorization / exit rule / costs on real turnover | Identical to the sibling PRIMARY_SPEC (PIT, $1M floor, n_eligible >= 20, anchor 0 = first date with n_eligible >= 20 for THIS signal, 1/99 winsorization across the eligible cross-section). |

## 2. Preregistered perturbations (≤ 8)

| # | Perturbation | Purpose |
|---|---|---|
| P1 | Beta window 90 d (>= 60 valid pairs) | beta-estimation sensitivity |
| P2 | Full residual: subtract intercept too, `Σ (r_t − α − β b_t)` | construction variant |
| P3 | Vol-scaled: `resid14 / std(residuals over the 60 d window)` (Blitz et al. standardisation) | construction variant |
| P4 | Exclude 2021 | regime concentration (mandatory) |
| P5 | Cost +50 % | cost fragility |
| P6 | All 14 anchors pooled | phase robustness |
| P7 | Liquidity floor $2M | cohort sensitivity |
| P8 | No winsorization | tail sensitivity |

## 3. Mandatory "same factor?" checks

- Spearman rank correlation per rebalance date (mean ± std): resid14 vs raw mom14, vs mom7, vs Amihud illiq_avg_30d; distribution of β across the eligible universe (median, IQR) to show the strip is material.
- Portfolio-return correlation (14-day windows): resid LS and resid LONG-leg excess vs raw 14D LO excess / 14D LS, vs 7d→7d LO excess and LS (compounded), vs Amihud LS (compounded). Jaccard overlap of the long legs (resid vs raw mom14) per date.
- Explicit test of the claim "residual survives and improves on raw at 14d": paired difference (resid LS − raw LS) per period, t on L3 clusters — arm A − arm B on the same dates.

## 4. Success criteria

Same as sibling §7, applied to the LONG-leg excess for the candidate verdict; LS spread reported
with its own line. `confirmable_in_horizon` = `eta_conservative < 1095 d`, floor 182 d.
