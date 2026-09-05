# W8 — SIGNAL ENSEMBLING — REPORT

Worker `w8_signal_ensembling`, Alpha Hunt Round 4.
Compute run 2026-09-03 (Tracks A and B), completed 2026-09-05 (Track C + deliverables) after
a session interruption. Preregistration: `PREREGISTRATION.md`, written before any test and
**not modified since** — every threshold, sign and decluster rule below is the preregistered
one. Machine-readable: `RESULTS.json` (150 mechanism entries). Scripts: `evidence/`.

**Axis in one line.** Every worker of rounds 1–4 hunted *isolated* mechanisms. This one asks
whether *combining* the signals the project already owns divides the forward-confirmation
**ETA**, which is the project's real bottleneck (Amihud 17 y, LIQ_REPEAT_DENSITY 9.4 y).
The result is treated as an ETA result, not a bps result.

---

## 0. Executive summary

| question | answer |
|---|---|
| Correlation matrix on a common base, never produced before | **Delivered** (§1): Track A 25×25, Track B 14×14, and the cross-basis matrix |
| Does the naive equal-weighted composite beat its components? | **Track A yes, Track B no** (§6). On Track B the naive composite is *dead* (ETA 194 y) while its best component is at 8.9 y |
| Does anything reach `ETA < 3 years`? | **No.** Best parameter-free composite = **4.64 y**; best walk-forward-weighted, selection-flagged variant = **3.02 y** |
| Is the axis worth continuing? | **Yes, and it is quantified**: the cross-basis correlation is ≈ 0 (median \|ρ\| = **0.035**), so ETA divides cleanly by adding independent sleeves. One more sleeve of `SR_ann ≥ 1.6` takes the project under 3 years |

**Headline verdict of the axis: `PROMISING_NEEDS_VALIDATION`** — the missing gate cell is
`eta_forward_confirmation`, which stays above 3 years for every composite tested. No composite
is `VALIDATED_FOR_FORWARD`.

**Best composite found** (`C4::LONGONLY_A+AMIHUD30+MOM30::EQUAL_CAPITAL`, zero free parameters):

| field | value |
|---|---|
| `n_raw` / `n_ind_L1` / `L2` / `L3` | 1429 / 1429 / 1429 days / 47 months |
| `net_bps` (per day of allocated capital) | **+12.95** |
| `net_bps_stress28` | **+9.97** |
| `t_stat_declustered` (day) / `t_stat_L3` (month) | **+5.14** / **+5.76** |
| `bootstrap_ci95` (month blocks) | **[+8.71, +17.60]** |
| `year_by_year` | 2022 +12.2 · 2023 +12.1 · 2024 +12.3 · 2025 +9.3 · 2026 +24.0 — **every year positive** |
| `ex_best_year` | +11.36 (dropping 2026) |
| `sr_annualised` | 2.60 |
| `n_required` (haircut 50 %) | 1696 days |
| `event_rate` | 7.0 trading days / week (the book is always on) |
| `eta_forward_confirmation` | **4.64 years** (1696 d) |
| `verdict` | **`UNCONFIRMABLE_IN_HORIZON`** (ETA > 3 y) — but the most stable object this project has produced |

---

## 1. THE CORRELATION MATRICES (deliverable #1)

The project has never measured these. They are reported before any performance number
because they *bound* what ensembling can do, and because they are the input the live
portfolio layer needs for deduplication.

### 1.1 Track B — cross-sectional daily basis, 14 signals, correlation of the **return series**

312-symbol PIT panel, decile long/short, daily rebalance, 2298 rebalances (2020-04 → 2026-07),
net of each sleeve's own turnover cost.

```
                MOM_7D MOM_30D REV_1D AMI_7D AMI_30D VOL_20 MAX_7D VOLSHK TURN_30 DHIGH SKEW RANGE BETA IDIOV
MOM_7D            1.00   0.58  -0.42   0.06   0.16  -0.24  -0.59   0.60   0.13   0.45 -0.35 -0.19  0.03 -0.23
MOM_30D           0.58   1.00  -0.22  -0.14   0.01  -0.34  -0.43   0.40  -0.05   0.37 -0.44 -0.32 -0.05 -0.36
REV_1D           -0.42  -0.22   1.00  -0.11  -0.12   0.06   0.24  -0.42  -0.14  -0.38  0.17  0.00  0.02  0.06
AMIHUD_7D         0.06  -0.14  -0.11   1.00   0.82  -0.14  -0.12   0.14   0.74  -0.08  0.05 -0.13 -0.04 -0.12
AMIHUD_30D        0.16   0.01  -0.12   0.82   1.00  -0.23  -0.21   0.21   0.76  -0.07 -0.03 -0.25 -0.08 -0.24
VOL_20D          -0.24  -0.34   0.06  -0.14  -0.23   1.00   0.71  -0.30   0.06   0.21  0.37  0.85  0.36  0.73
MAX_RET_7D       -0.59  -0.43   0.24  -0.12  -0.21   0.71   1.00  -0.55   0.01  -0.11  0.35  0.58  0.23  0.49
VOLUME_SHOCK_Z    0.60   0.40  -0.42   0.14   0.21  -0.30  -0.55   1.00   0.18   0.34 -0.29 -0.22 -0.09 -0.24
TURNOVER_30D      0.13  -0.05  -0.14   0.74   0.76   0.06   0.01   0.18   1.00   0.10  0.06  0.08  0.08  0.06
DIST_HIGH_30D     0.45   0.37  -0.38  -0.08  -0.07   0.21  -0.11   0.34   0.10   1.00  0.07  0.37  0.25  0.38
SKEW_30D         -0.35  -0.44   0.17   0.05  -0.03   0.37   0.35  -0.29   0.06   0.07  1.00  0.43  0.33  0.59
RANGE_20D        -0.19  -0.32   0.00  -0.13  -0.25   0.85   0.58  -0.22   0.08   0.37  0.43  1.00  0.43  0.85
BETA_BTC_30D      0.03  -0.05   0.02  -0.04  -0.08   0.36   0.23  -0.09   0.08   0.25  0.33  0.43  1.00  0.39
IDIOVOL_30D      -0.23  -0.36   0.06  -0.12  -0.24   0.73   0.49  -0.24   0.06   0.38  0.59  0.85  0.39  1.00
```

Summary: median \|ρ\| = 0.231, mean ρ = +0.076, 13 of 91 pairs above 0.5.
Effective number of independent bets (eigenvalue entropy) = **7.6 out of 14**.
The weekly matrix (327 rebalances) is in `RESULTS.json`; it tells the same story
(median \|ρ\| = 0.167, ENB = 8.4).

**Three blocks fall out of the matrix, and they matter operationally:**

1. **Liquidity/size block** — `AMIHUD_7D ~ AMIHUD_30D` = 0.82, `AMIHUD_30D ~ TURNOVER_30D` = **0.76**.
   The "illiquidity premium" *is* the low-turnover (small-size) premium on this panel. E5
   confirms it: 95 % of `AMIHUD_30D`'s cross-sectional variance is explained by the other 13
   signals, and its orthogonalised residual keeps no significant edge on the daily horizon.
2. **Risk block** — `VOL_20D ~ RANGE_20D` = 0.85, `RANGE_20D ~ IDIOVOL_30D` = 0.85,
   `VOL_20D ~ IDIOVOL_30D` = 0.73, `MAX_RET_7D` 0.58–0.71 with the block. One factor, five names.
3. **Momentum block** — `MOM_7D ~ MOM_30D` = 0.58, `MOM_7D ~ VOLUME_SHOCK_Z` = 0.60,
   `MOM_7D ~ DIST_HIGH_30D` = 0.45.

**The single most actionable number in this matrix: `corr(AMIHUD_30D, MOM_7D) = +0.16` daily,
`+0.07` weekly.** `configs/live_alpha_registry.yaml` files `AMIHUD_ILLIQUIDITY_PREMIUM_V1`
under `correlation_family: CROSS_SECTIONAL_XSMOM`, the same family as the three momentum
entries, "same operational family". Measured on a common base, **Amihud and momentum are
essentially uncorrelated in return**; Amihud belongs with `TURNOVER_30D` (a size/liquidity
family), not with momentum. See §7.

### 1.2 Track A — liquidation-cascade event basis, 25 pre-event signals

38 125 evaluable events, 49-symbol clean universe, LONG 4 h at `event_time`.
Two matrices, both in `RESULTS.json`:

* **score-level** (event-by-event, causal expanding-ECDF z-scores): median \|ρ\| = 0.073,
  p90 = 0.331, 13/300 pairs above 0.5. The signals *look* almost orthogonal.
* **return-level** (monthly return of each signal's own top-decile sleeve): median \|ρ\| = **0.313**,
  p90 = 0.674, **84/300 pairs above 0.5**.

**This gap is the single most important methodological finding of the axis** and it is exactly
the trap the mandate warned about: *two signals with near-zero score correlation fire on the
same days.* Ranking events differently is not the same as producing different returns. Any
diversification budget computed from score correlations on this basis would be wrong by a
factor of ~4: ENB from returns = **2.9 independent bets out of 25** (`1/(1+(K−1)ρ̄)` form),
8.5 by eigen-entropy. Top return-correlated pairs: `oi_drop_30m~oi_drop_1h` 0.89,
`oi_drop_1h~oi_ret_2h` 0.86, `btc_vol_24h~toptrader_z` 0.82, `dist_low_24h~dist_low_7d` 0.81.

### 1.3 Cross-basis — the matrix that decides the axis

Both bases restated as daily net return-on-notional series on a common calendar
(2023-01-02 → 2026-06-26, 1272 days):

| Track A sleeve | × `B_EW_APRIORI` | × `B_EW_WALKFORWARD` | × `B_CONFIDENCE_WF` | × `B_AMIHUD_30D` |
|---|---|---|---|---|
| `A_EW_APRIORI_q90` | −0.010 | −0.014 | +0.043 | +0.038 |
| `A_EW_WALKFORWARD_q90` | +0.036 | +0.033 | +0.030 | +0.023 |
| `A_EW_WALKFORWARD_q80` | +0.064 | +0.045 | +0.038 | +0.013 |
| `A_CONFIDENCE_IC_WF_q80` | +0.067 | +0.068 | +0.030 | +0.016 |

**Median \|ρ\| = 0.035, max \|ρ\| = 0.068 over all 16 cross-basis pairs.** The liquidation-cascade
event book and the cross-sectional daily book are, for practical purposes, **statistically
independent**. This is the first time the project has measured it, and it is what makes §5 work.

---

## 2. The ruler: ETA depends on one number, and the ruler checks out

From `PREREGISTRATION.md` §0: with the mandatory 50 % haircut,
`n_required = ((z_{1−α/2}+z_{power}) / (0.5·SR_episode))²` and `SR_ann = SR_episode·√f`, so

> **`ETA_years = 31.4 / SR_annualised²`** — nothing else. Not bps, not event count separately.
> `ETA < 3 y ⟺ SR_ann > 3.24`.

Sanity check against the project's own published ETAs, computed independently here:

| published alpha | published ETA | SR_ann implied by the ruler | this worker's independent measurement |
|---|---|---|---|
| `AMIHUD_ILLIQUIDITY_PREMIUM_V1` | 17.0 y | 1.36 | weekly decile L/S → SR 1.52, **ETA 13.5 y** |
| `LIQ_REPEAT_DENSITY` | 9.4 y | 1.83 | — |

The ruler reproduces the project's numbers. Every ETA below is computed with it, always on
**declustered** series, always with the 50 % haircut.

---

## 3. Track A — the liquidation-cascade event basis (25 signals)

Population `data/events/liq_cascade_dataset.parquet`, 38 125 events with `fwd_4h`, LONG-only
4 h hold, one fixed horizon (no horizon search). All z-scores are block-wise expanding ECDF
against **strictly prior** events (burn-in 2000); all walk-forward signs are the sign of the
Spearman IC on strictly prior months; all selection thresholds are quantiles of the **prior**
score distribution. Declustering L1 = same-symbol overlapping 4 h windows collapsed,
L2 = calendar day, L3 = month; t-stats are computed on the **daily** portfolio series.

| mechanism | n_L1 | n_L2 | n_L3 | net | net@28 | t_dcl | t_L3 | CI95 | ex-best-yr | SR_ann | ETA | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `EW_APRIORI_q90` (0 free params) | 2640 | 605 | 48 | +52.0 | +38.0 | 1.25 | 0.92 | [−6.3, +27.1] | +40.0 | 0.73 | 58.5 y | `WEAK` |
| `EW_APRIORI_q80` | 5648 | 975 | 48 | +17.0 | +3.0 | 1.51 | 1.35 | — | — | 0.83 | 45.2 y | `WEAK` |
| `EW_WALKFORWARD_q90` | 4027 | 655 | 47 | +31.1 | +17.1 | **3.62** | 2.48 | [+12.4, +45.8] | +27.4 | 1.90 | **8.7 y** | `UNCONFIRMABLE_IN_HORIZON` |
| `EW_WALKFORWARD_q80` | 7161 | 940 | 48 | +14.4 | +0.4 | 3.89 | 3.19 | [+10.5, +31.8] | +3.0 | 2.06 | 7.4 y | `UNCONFIRMABLE_IN_HORIZON` |
| `EW_WALKFORWARD_q70` | 9846 | 1114 | 48 | +8.0 | −6.0 | 4.69 | 4.34 | — | — | 2.55 | 4.8 y | `COST_FRAGILE` |
| `INVVOL_WF_q80` | 5075 | — | — | +26.0 | +12.0 | 3.48 | — | — | — | 2.04 | 7.6 y | `UNCONFIRMABLE_IN_HORIZON` |
| **`CONFIDENCE_IC_WF_q80`** | 4164 | 607 | 43 | **+35.1** | **+21.1** | **4.55** | 3.58 | [+18.4, +53.6] | +31.7 | **2.76** | **4.13 y** | `UNCONFIRMABLE_IN_HORIZON` |
| `VOTE K≥15` (best of curve) | 4862 | — | — | +26.1 | +12.1 | 4.24 | — | — | — | — | 5.2 y | `UNCONFIRMABLE_IN_HORIZON` |
| **`E6` walk-forward BEST COMPONENT** | 4198 | — | — | +24.4 | — | **0.33** | — | — | — | 0.12 | **2181 y** | `DEAD` |

Not one of the 25 individual components reaches `t_declustered ≥ 2` *and* a positive
stress-28 edge *and* an ETA below 3 y: 3 are `UNCONFIRMABLE_IN_HORIZON`, 2 `COST_FRAGILE`,
16 `WEAK`, 4 `DEAD` (full table in `RESULTS.json`). **The composite is worth roughly
5–10× its own best usable component in ETA terms.**

The a-priori sign table was materially wrong here too: replacing a-priori signs by
walk-forward signs moves the q90 composite from `t = 1.25`, ETA 58 y to `t = 3.62`, ETA 8.7 y.
Reported, not hidden — the a-priori composite stays the zero-parameter reference and it is
`WEAK`.

**The vote (E3) curve is reported in full** (`RESULTS.json`, `A_E3`): monotone and interpretable
— `K≥1..10` are all `DEAD` (negative net), the edge appears only from `K≥11` and grows with
`K_min` while N collapses (K≥22: +176 bps but n_L1 = 61). No argmax was used to set a verdict.

---

## 4. Track B — the cross-sectional daily basis (14 signals): the honest reference

Population: 312-symbol PIT perp panel aggregated from
`/home/qbee/futur-data-v2/data_v2/normalized/perp_ohlcv` (read-only), causal universe filter
(30-day **trailing** median quote volume ≥ $1 M, ≥ 60 trailing days, ≥ 250 5-min bars/day),
365 670 symbol-days, 2404 days, mean eligible universe ≈ 140 names. Decile long/short,
non-overlapping rebalances. **Costs are charged on each sleeve's own measured turnover**
(`cost = turnover × 14 bps`, stress 28), never on a per-component average.

### 4.1 The naive equal-weighted composite, as demanded — and it is dead

| composite (daily rebalance) | net | net@28 | turnover | t_dcl | SR_ann | ETA | verdict |
|---|---|---|---|---|---|---|---|
| **`EW_APRIORI` (0 free params)** | **+7.17** | +4.35 | 0.201 | **1.01** | 0.40 | **194 y** | `WEAK` |
| `EW_WALKFORWARD` | +6.42 | +2.94 | 0.248 | 0.90 | 0.36 | 246 y | `DEAD` |
| `EW_APRIORI` long-only excess | −2.25 | −5.19 | 0.210 | −0.66 | −0.26 | — | `DEAD` |
| `INVVOL_WF` | +10.43 | +7.97 | 0.176 | 1.53 | 0.61 | 84 y | `WEAK` |
| `CONFIDENCE_WF` (weights ∝ trailing IC) | +20.51 | +18.29 | 0.159 | 2.99 | 1.19 | 22 y | `UNCONFIRMABLE_IN_HORIZON` |
| best component `AMIHUD_30D` | **+25.62** | +25.19 | **0.031** | **4.72** | **1.88** | **8.9 y** | `UNCONFIRMABLE_IN_HORIZON` |

The weekly horizon says the same: composite +90.2 bps / ETA 56.5 y vs `AMIHUD_30D`
+161.4 bps / ETA 13.5 y.

**Why the naive composite fails here — two measured reasons, not hand-waving:**

1. **8 of the 14 a-priori signs are contradicted by the data** (§4.2). The composite is an
   equal-weight mixture of 6 right signs and 8 wrong ones.
2. **The composite pays for the turnover of its noisiest members.** `AMIHUD_30D` turns over
   3.1 % of the book per day (cost 0.43 bps); the composite turns over 20.1 % (cost 2.8 bps
   out of a 10 bps gross). Averaging a slow signal with fast ones destroys the slow signal's
   main advantage. This is invisible if costs are charged per component instead of on the
   composite's real turnover.

### 4.2 A-priori sign contradictions (declared, not silently flipped)

| a-priori sign confirmed | a-priori sign **contradicted** |
|---|---|
| `MOM_7D` +, `MOM_30D` +, `AMIHUD_7D` +, `AMIHUD_30D` +, `TURNOVER_30D` −, `DIST_HIGH_30D` + | `REV_1D` (−→ +, no 1-day reversal), `VOL_20D`, `MAX_RET_7D` (−→ **+**, the lottery/MAX effect is *positive* here), `VOLUME_SHOCK_Z`, `SKEW_30D`, `RANGE_20D`, `BETA_BTC_30D`, `IDIOVOL_30D` |

All eight contradictions point the same way: **the equity "low-risk / anti-lottery" anomaly
family is inverted in crypto perpetuals 2020-2026 — risk pays.** `MAX_RET_7D` short-side is
the sharpest (−28.0 bps daily under the a-priori sign, `t = −3.50`, i.e. +28 bps with the
opposite sign). This is a reusable prior for the project and it was *preregistered as a
falsifiable sign table*, so the contradiction is a result, not a refit opportunity. **I did not
flip any sign to build a headline composite**; the walk-forward-sign variants are reported
separately and, on this basis, they do not help either.

---

## 5. Track C — the cross-basis composite: where the ETA actually moves

Preregistered as the last test: the two bases share exactly one unit, the calendar day.
Convention (declared, not fitted): each sleeve is a **net return-on-notional in bps per
calendar day**; an idle sleeve contributes 0 that day (this leaves `SR_ann`, hence ETA,
mathematically invariant: mean scales by *f*, sd by √*f*, obs/yr by 1/*f*).
`EQUAL_CAPITAL` is the parameter-free headline; `INVVOL_WF` uses expanding, strictly-prior,
shifted volatilities. Track A costs 14 bps (28 stress) **per episode**; Track B costs
`turnover × 14` (28) per rebalance. **Nothing is averaged across sleeves.**

| basket | ρ̄ | weighting | n days | net | net@28 | t_dcl | t_L3 | CI95 | SR_ann | ETA | best single, same window | ETA ÷ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `A_EW_WF_q90` + `B_EW_APRIORI` (both param-free) | +0.033 | EQUAL | 1429 | +6.5 | +2.0 | 1.45 | 1.33 | [−2.7, +16.2] | 0.73 | 58.8 y | A alone, 10.8 y | **×0.18** |
| same | | INVVOL_WF | 1309 | +8.7 | +3.7 | 2.25 | 1.61 | [+0.5, +17.2] | 1.19 | 22.3 y | | ×0.49 |
| `A_EW_WF_q90` + `AMIHUD_30D` | +0.015 | EQUAL | 1429 | +13.5 | +10.0 | 4.16 | 4.17 | [+7.4, +19.9] | 2.10 | 7.09 y | A alone, 10.8 y | ×1.53 |
| `AMIHUD_30D` + `MOM_30D` (within B) | +0.014 | EQUAL | 2298 | +22.4 | +20.9 | 4.51 | 4.06 | [+12.2, +33.7] | 1.80 | 9.70 y | AMIHUD, 8.9 y | ×0.91 |
| `A_EW_WF_q90`+`AMIHUD_30D`+`MOM_30D` | +0.009 | INVVOL_WF | 1309 | +14.6 | +11.0 | 4.61 | 4.61 | [+8.8, +21.2] | 2.44 | 5.29 y | A alone, 10.8 y | ×2.04 |
| **long-only ✱ `A_EW_WF_q90`+`AMIHUD30`+`MOM30`** | +0.01 | **EQUAL** | 1429 | **+12.95** | **+9.97** | **5.14** | **5.76** | **[+8.71, +17.60]** | **2.60** | **4.64 y** | `AMIHUD30` long leg, 9.05 y | **×1.95** |
| same | | INVVOL_WF | 1309 | +13.08 | +10.07 | 5.36 | 5.84 | [+8.78, +17.77] | 2.83 | **3.92 y** | | ×2.31 |
| `A_CONF_IC_WF_q80`+`AMIHUD30` **[REFIT]** | +0.016 | EQUAL | 1280 | +16.0 | +12.5 | 4.73 | 4.77 | [+9.7, +23.0] | 2.53 | 4.92 y | A alone, 5.97 y | ×1.21 |
| long-only ✱ `A_CONF_IC_WF_q80`+`AMIHUD30`+`MOM30` **[REFIT]** | +0.003 | INVVOL_WF | 1160 | +14.71 | +11.42 | **5.74** | 5.93 | [+9.69, +19.65] | **3.22** | **3.02 y** | `A_CONF_IC_WF_q80`, 5.97 y | ×1.98 |

✱ **long-only** = the Track B legs are the LONG leg alone (top decile minus the same-day
eligible-universe mean, i.e. hedged against the universe basket, **not** against a
bottom-decile short); Track A is long-only by construction. This is the version compliant
with the project's SHORT policy (BRIEFING §8.11), and the long legs are reported separately
below. **[REFIT]** marks baskets whose sleeve was chosen after seeing the Track A results —
their numbers are not allowed to change a verdict.

**Long legs alone** (project rule 11), daily, 2298 rebalances:

| long leg (excess vs universe) | net | net@28 | t_dcl | SR_ann | ETA | verdict |
|---|---|---|---|---|---|---|
| `AMIHUD_30D` | +17.27 | +16.64 | 4.78 | 1.90 | 8.66 y | `UNCONFIRMABLE_IN_HORIZON` |
| `AMIHUD_7D` | +15.73 | +13.85 | 4.67 | 1.86 | 9.04 y | `UNCONFIRMABLE_IN_HORIZON` |
| `TURNOVER_30D` | +13.87 | +13.17 | 4.06 | 1.62 | 11.99 y | `UNCONFIRMABLE_IN_HORIZON` |
| `MOM_30D` | +16.26 | +14.22 | 2.65 | 1.06 | 28.05 y | `UNCONFIRMABLE_IN_HORIZON` |
| `MOM_7D` | +13.73 | +9.46 | 2.14 | 0.85 | 43.17 y | `UNCONFIRMABLE_IN_HORIZON` |
| `EW_APRIORI` composite | −2.25 | −5.19 | −0.66 | −0.26 | — | `DEAD` |

**Result:** for Amihud the short (liquid) leg adds essentially nothing — L/S SR 1.88 vs
long-leg-only SR 1.90. The edge lives entirely in the long, illiquid leg. Good news for a
project that forbids standalone directional shorts.

**Three things this table establishes:**

1. **The √K mechanism is real and measured.** With ρ̄ ≈ 0, the quadrature bound
   `SR_comb = √(ΣSRᵢ²)` is 2.43 for the A+AMIHUD+MOM triple; measured `INVVOL_WF` = 2.44.
   The combination captures ~100 % of the theoretical diversification.
2. **But `EQUAL_CAPITAL` is not `EQUAL_RISK`.** The parameter-free basket gets 2.60 where
   the quadrature bound of its three long legs is 2.81 — the sleeves have very different daily
   volatilities, and equal capital is not equal risk. This is
   the honest cost of refusing to fit anything.
3. **A dead sleeve destroys the composite.** The parameter-free A+B pair (row 1) is *worse*
   than sleeve A alone (×0.18) purely because `B_EW_APRIORI` has SR ≈ 0. Ensembling divides
   ETA only among sleeves that each carry an edge; it is not a way to rescue dead signals.

### 5.1 The roadmap number

`ETA < 3 y ⟺ SR_ann > 3.24`. The parameter-free composite sits at **2.60**; the
walk-forward-weighted at **2.83**. With cross-basis ρ ≈ 0, one additional independent sleeve
of `SR_ann = √(3.24² − 2.60²) = 1.93` (parameter-free) or **1.58** (walk-forward-weighted)
takes the project **under the 3-year bar**. That is one more AMIHUD-class alpha — not ten.
**This is the most useful output of this worker**: the ETA bottleneck is now a countable
distance, not a wall.

---

## 6. E6 — the decisive test: does the composite beat its BEST component?

Preregistered rule: the best component is itself chosen **walk-forward** (best trailing
statistic at each step), never with hindsight; the hindsight-best number is reported
alongside and explicitly labelled unattainable.

| basis | composite (walk-forward, net, declustered) | walk-forward best component | hindsight best (unattainable) | **verdict on the test** |
|---|---|---|---|---|
| **A** (event) | `EW_WALKFORWARD_q90` ETA **8.7 y**, t 3.62 · `CONFIDENCE_IC_WF_q80` ETA **4.13 y**, t 4.55 | +24.4 bps, **t 0.33**, **ETA 2181 y** (picker oscillates over 10 different components) | `dist_low_7d`, t 4.80 but net **+2.7 bps**, ETA 4.9 y, `COST_FRAGILE` | **The composite WINS, decisively.** ~500× on ETA vs the attainable benchmark |
| **B** (cross-sectional) | `EW_APRIORI` ETA **194 y**, t 1.01 · `CONFIDENCE_WF` ETA 22 y | +20.8 bps, t 3.33, **ETA 16.4 y** (picker oscillates between `AMIHUD_30D`, `DIST_HIGH_30D`, `MOM_7D`) | `AMIHUD_30D`, t 4.72, **ETA 8.9 y** | **The composite LOSES**, and badly. Even the walk-forward picker beats it 12× |
| **C** (cross-basis) | long-only triple ETA **4.64 y** (param-free) / 3.92 y (WF) | best single sleeve on the same window: `AMIHUD_30D` long leg, 9.05 y | — | **The composite wins, ×1.95 to ×2.31** |

**The answer to the mandate's decisive question is conditional, and the condition is the one
thing that matters for the live portfolio:**

> **Ensembling divides ETA when no single component dominates, and destroys it when one does.**

Track A is 25 signals none of which is individually usable (best usable one is `COST_FRAGILE`
at +2.7 bps net): pooling them is the only way to extract anything, and it works. Track B has
one component with `SR_ann` 1.9 and eight with negative edge under their a-priori sign:
pooling dilutes the good one by ~5× in ETA terms. The √K argument assumes **comparable**
individual Sharpes; the project's cross-sectional family violates that assumption badly.

---

## 7. E5 orthogonalisation → concrete deduplication input for the live portfolio

Each signal regressed on all the others (betas estimated on **strictly prior** observations
only), residual re-tested. A signal whose edge dies is a `DUPLICATE`.

* **Track A**: 9 `DUPLICATE` / 1 `INDEPENDENT_CONTRIBUTOR` / 15 no edge either way. Notably
  `dist_low_24h` (day-level t 4.35 → −0.89, R² 0.83) and `dist_low_7d` (t 4.80 → −1.29) — i.e.
  **`LIQ_CASCADE_FAR_FROM_LOW_V1`'s signal carries no information the rest of the event
  feature set doesn't already carry** (both are `DEAD`/`COST_FRAGILE` on net anyway, so this
  is a statement about the *information*, not about a live edge being removed). Same for `ret_24h` (4.62 → 0.09) and `px_ret_1h`
  (2.94 → −1.37): the "far from low" family is a repackaging of recent-return state.
* **Track B daily**: 4 `DUPLICATE` (`MOM_7D`, `MOM_30D`, `AMIHUD_7D`, `TURNOVER_30D`), 10 no
  edge. `AMIHUD_30D` survives as the least redundant member of the liquidity block but with
  R² = 0.95 explained by the others — it is a *linear combination* of the panel's other
  characteristics, dominated by `TURNOVER_30D`.

**Recommendations for `configs/live_alpha_registry.yaml`** (read-only for me — these are
proposals for the coordinator, not edits):

1. `AMIHUD_ILLIQUIDITY_PREMIUM_V1` should **not** share `correlation_family` with the momentum
   entries: measured return ρ with `MOM_7D` = +0.16 daily / +0.07 weekly. Suggested family:
   `CROSS_SECTIONAL_LIQUIDITY_SIZE`. Treating it as correlated with momentum currently costs
   the live portfolio real diversification budget it has already paid for.
2. Conversely the four `LIQ_CASCADE_DETECTOR` entries are **more** correlated than their
   scores suggest: their *return* series correlate at a median 0.31 with pairs up to 0.89. Any
   dedup based on signal-score correlation on that family is optimistic by roughly 4×.
3. A cross-family budget between the liquidation-cascade book and the cross-sectional book can
   be set at ρ = 0 with confidence: measured median \|ρ\| = 0.035, max 0.068, over 1272 days.

---

## 8. What I killed, and why

| killed | why |
|---|---|
| **The naive equal-weighted composite on the cross-sectional basis** | ETA 194 y (daily) / 56.5 y (weekly) vs 8.9 y for `AMIHUD_30D` alone. Preregistered as the headline reference; it is `WEAK`, and I report it as the headline anyway |
| **The vote / concordance rule (E3) on Track B** | Every `K_min` from 1 to 10 is `DEAD` on the daily horizon, with *monotonically more negative* net as `K_min` rises (`K10`: −3.34 bps, t −59). Agreement among these 14 signals is an anti-signal |
| **Score-correlation as a diversification measure** | Median \|ρ\| 0.073 on scores vs 0.313 on returns for the same 25 Track A signals. Killed as a dedup input; only return correlation is reported |
| **The walk-forward best-component picker as a strategy** (Track A) | t = 0.33, ETA 2181 y. It oscillates between 10 components and buys each one's mean-reversion. Useful only as the E6 benchmark |
| **8 of 14 preregistered a-priori signs on the cross-sectional basis** | Contradicted by the data, all in the same direction ("risk pays"). Reported as contradictions; no sign was flipped to build a headline |
| **`INVVOL` weighting as a free lunch** | On both bases inverse-vol weighting is *worse* than IC-confidence weighting and, on Track B weekly, worse than the naive composite. Volatility is not the scarce resource here; edge is |
| **The idea that ensembling can rescue a dead sleeve** | Measured: pairing a strong sleeve with a dead one multiplies ETA by 5.5 (row 1 of §5) |

---

## 9. PIT, declustering and cost audit (what would make this wrong, and why it isn't)

* **No full-sample standardisation.** Track A: block-wise expanding ECDF against strictly
  prior events, burn-in 2000, NaN before. Track B: same-day cross-sectional rank → normal
  quantile, which uses no other day. Verified by construction in `a1_track_a_ensemble.py:causal_z`
  and `b1_track_b_ensemble.py:xs_z`.
* **No full-sample sign, no full-sample weight, no full-sample threshold.** Every sign is
  a-priori (frozen in the preregistration) or the sign of a **strictly prior** IC. Every E4
  weight and every selection quantile is estimated on an expanding window ending strictly
  before the evaluated observation (`causal_quantile_threshold`, expanding `.shift(1)`).
  Only out-of-sample performance is reported.
* **Declustering is applied to the COMPOSITE's episodes**, as the mandate demands, not to the
  components': Track A L1 collapses same-symbol overlapping 4 h windows *of the composite's
  selected events*, L2 = calendar day (t-stats are computed on the daily portfolio series,
  never on raw event counts), L3 = month (block-bootstrap blocks). Track B rebalances are
  non-overlapping by construction; L2 = day, L3 = month.
* **Costs on real turnover.** Track B: `cost = measured turnover × 14 bps` per rebalance
  (turnover defined as ½Σ\|Δw\|, so a full round trip of the book = 14 bps, consistent with the
  project convention), stress 28. Track A: 14 bps (28) per episode. In Track C the two cost
  models are kept separate per sleeve and never averaged.
* **Stress-28 survival**: the headline long-only composite keeps **+9.97 bps** at 28 bps.
  The Track A `EW_WALKFORWARD_q80` variant does *not* (+0.44) and is flagged accordingly;
  `q70` is `COST_FRAGILE`.
* **Multiple testing / REFIT declarations.** Preregistered degrees of freedom: 3 quantiles
  (Track A), decile vs quintile (Track B), `K_min` swept with the *whole curve* reported.
  Post-hoc choices, all stamped `[REFIT]` in the tables and in `RESULTS.json` `flags`:
  (a) choosing `A_CONFIDENCE_IC_WF_q80` as the Track C sleeve after seeing Track A's E4 table;
  (b) the specific basket compositions of §5 beyond the parameter-free pair. **No verdict in
  this report rests on a `[REFIT]` number** — the headline (4.64 y) is the parameter-free
  equal-capital basket, and it is still `UNCONFIRMABLE_IN_HORIZON`.
* **An inconsistency I found in my own Track A gate, reported rather than patched.** In
  `a1_track_a_ensemble.py:gate`, `net_bps` is the **episode**-equal-weighted mean while
  `t_stat_declustered` is computed on the **day**-equal-weighted portfolio series. For
  5 of the 25 Track A *components* the two disagree in sign (`dist_low_24h`: −10.1 bps per
  episode but +27.9 bps per day — a few very busy, very negative days dominate the episode
  average), which makes those five components' individual verdicts unreliable. **It does not
  touch any conclusion of this report**: all twelve Track A *composites* agree in sign under
  both weightings, and every Track C number — including the headline — is computed on daily
  series only, where the two coincide by construction. A day-weighted `net_bps` would be the
  correct fix for a future run.
* **Known limitation.** Track A's `y_bps` is `fwd_4h` from the pre-built event dataset, whose
  own PIT construction I inherited rather than re-verified; and Track B's execution assumption
  is "trade at the close that generated the signal". Both are the project's existing
  conventions, reused unchanged so the numbers stay comparable with the registry's.

---

## 10. Verdicts

| mechanism family | best variant | net / net@28 | N_ind L3 | ETA | verdict |
|---|---|---|---|---|---|
| Track A composite, zero free parameters | `EW_APRIORI_q90` | +52.0 / +38.0 | 48 mo | 58.5 y | `WEAK` |
| Track A composite, walk-forward signs | `EW_WALKFORWARD_q90` | +31.1 / +17.1 | 47 mo | 8.7 y | `UNCONFIRMABLE_IN_HORIZON` |
| Track A composite, walk-forward IC weights | `CONFIDENCE_IC_WF_q80` | +35.1 / +21.1 | 43 mo | 4.13 y | `UNCONFIRMABLE_IN_HORIZON` |
| Track A vote | `K≥15` | +26.1 / +12.1 | — | 5.2 y | `UNCONFIRMABLE_IN_HORIZON` |
| Track B composite, zero free parameters | `EW_APRIORI` daily | +7.2 / +4.4 | 76 mo | 194 y | `WEAK` |
| Track B composite, walk-forward IC weights | `CONFIDENCE_WF` daily | +20.5 / +18.3 | 76 mo | 22.1 y | `UNCONFIRMABLE_IN_HORIZON` |
| Track B vote | all `K_min` | ≤ 0 | — | — | `DEAD` |
| Track B best component (reference, not a deliverable of this worker) | `AMIHUD_30D` daily | +25.6 / +25.2 | 76 mo | 8.9 y | `UNCONFIRMABLE_IN_HORIZON` |
| **Cross-basis composite, parameter-free, long-only** | `A_EW_WF_q90+AMIHUD30+MOM30` | **+12.95 / +9.97** | **47 mo** | **4.64 y** | **`UNCONFIRMABLE_IN_HORIZON`** |
| Cross-basis composite, walk-forward risk weights, long-only | same, `INVVOL_WF` | +13.08 / +10.07 | 44 mo | 3.92 y | `UNCONFIRMABLE_IN_HORIZON` |
| Cross-basis composite, selected sleeves `[REFIT]` | `A_CONF_IC_WF_q80+AMIHUD30+MOM30` | +14.71 / +11.42 | 39 mo | 3.02 y | `UNCONFIRMABLE_IN_HORIZON` |

**Axis verdict: `PROMISING_NEEDS_VALIDATION`.** Missing gate cell: `eta_forward_confirmation`.
Everything else passes — declustered `t` up to 5.4, month-level `t` up to 5.8, bootstrap CI
strictly positive, stress-28 positive, **every calendar year positive**, no single-year
concentration (`ex_best_year` +11.4 vs +12.95 headline). It is the closest this project has
come to a confirmable alpha, and it is short by a factor of 1.55 in ETA.

---

## 11. What I would do next (for the coordinator, not done here)

1. **Fix the registry's correlation families** (§7.1–7.2). Free diversification, zero research.
2. **Prefer the daily rebalance of Amihud to the weekly one.** Same signal, ETA 8.9 y instead
   of 13.5 y, because turnover falls to 3.1 %/day and the cost drag nearly vanishes. The
   registry's weekly convention is costing ~35 % of the ETA.
3. **Stop hunting for the biggest bps and start hunting for the most *independent* sleeve.**
   The project needs exactly one more sleeve with `SR_ann ≥ 1.6` at ρ ≈ 0 to break the 3-year
   bar. Candidate sources with measured independence from both existing books: the options /
   DVOL family and the Hyperliquid metaorder family (neither is on either basis used here).
4. **Do not deploy an equal-weighted composite of the cross-sectional family.** It is measurably
   worse than its best member, and the reason (turnover dilution + inverted signs) is structural.
5. **Retest the composite of §5 as a portfolio-layer object, not as an alpha.** It is a
   two-book allocation rule, and the live `PORTFOLIO_SHADOW_LAYER` is where it belongs.

---

## Files

* `PREREGISTRATION.md` — hypotheses, signs and thresholds, unmodified since before the tests.
* `RESULTS.json` — 150 mechanism entries with the full BRIEFING §2 gate, the six correlation
  matrices, the ENB figures and the E5 duplicate classifications.
* `evidence/a1_track_a_ensemble.py` — Track A causal transforms, declustering, gate, ETA ruler.
* `evidence/a2_track_a_composites.py` — Track A E1–E6 (`a2_track_a_results.json`).
* `evidence/b0_build_daily_panel.py` — daily panel from `data_v2` (writes to scratch only).
* `evidence/b1_track_b_ensemble.py`, `evidence/b2_track_b_composites.py` — Track B E1–E6
  (`b2_track_b_results.json`).
* `evidence/c0_daily_sleeve_series.py` — every sleeve restated on the common daily calendar
  (`c0_daily_sleeves.parquet`, 0.5 MB).
* `evidence/c1_track_c_cross_basis.py` — cross-basis correlation, pairs and baskets
  (`c1_track_c_results.json`).
* `evidence/c2_longonly_legs.py` — long legs alone + the policy-compliant baskets
  (`c2_longonly_results.json`).
* `evidence/z_build_results.py` — assembles `RESULTS.json` from the four result files.

Re-execution order: `b0` (writes the panel to scratch) → `a1` → `a2` → `b2` → `c0` → `c1` →
`c2` → `z`. Everything reads `data/`, `data_v2/` and `configs/` read-only.
