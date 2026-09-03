# W8 — SIGNAL ENSEMBLING — PREREGISTRATION

Worker: `w8_signal_ensembling`, Alpha Hunt Round 4 (2026-09-03).
Written **before** any test was run. Panel construction (a pure data-prep aggregation,
no outcome touched) was launched in parallel with the writing of this file; no result of
any kind had been observed at the time of writing.

---

## 0. The axis, stated as a falsifiable claim

Every worker of rounds 1–4 has hunted **isolated** mechanisms. Nobody has tested whether the
**combination** of the signals the project already owns beats its parts. The reason this is
worth a worker is not aesthetic, it is arithmetic:

> For an episodic strategy whose episodes tile the calendar at rate `f` episodes/year, the
> forward-confirmation horizon obeys
>
> `n_required = ((z_{1-a/2} + z_{power}) / (h * SR_episode))^2`  with `h = 0.5` (mandatory haircut)
> `ETA_years  = n_required / f = (2.80 / (0.5 * SR_episode))^2 / f = 31.4 / SR_annualised^2`
>
> because `SR_annualised = SR_episode * sqrt(f)`.

**Consequence 1 (the ruler).** `ETA` depends on *one* number only: the annualised Sharpe of
the episode-return stream. Not on bps, not on event count separately. `ETA < 3 years`
⟺ `SR_ann > 3.24` post-haircut. This is why the project's best validated alphas have absurd
ETAs: AMIHUD (17.0y) ⟹ `SR_ann ≈ 1.36`; LIQ_REPEAT_DENSITY (9.4y) ⟹ `SR_ann ≈ 1.83`.

**Consequence 2 (the bet).** Combining `K` signals with pairwise return correlation `rho` and
comparable individual Sharpe multiplies `SR_ann` by `sqrt(K / (1 + (K-1) rho))`, hence
divides `ETA` by that squared, i.e. by `K` when `rho = 0`. Ensembling is therefore, on paper,
**the only lever in this project that attacks the ETA bottleneck directly** — it does not need
a new mechanism, a new data feed, or a bigger bps.

**H0 (what I will try to reject):** the equal-weighted composite of the project's existing
signals has an `SR_ann` no larger than that of its best single component, i.e. `ETA` is not
improved. Prior belief: H0 is *probably true in part* — the project's signals are far more
correlated than their different names suggest (three registry entries already share
`correlation_family: CROSS_SECTIONAL_XSMOM`, four share `LIQ_CASCADE_DETECTOR`). The realistic
best case is a partial ETA division (2–4x), not K-fold.

**Falsification of my own axis:** if the measured pairwise correlations of the signal return
series are high (median |rho| > 0.5), the axis is dead by arithmetic and I will say so.

---

## 1. Two common bases (a composite only means something on ONE population)

The registry's 16 alphas do **not** live on a shared population (BTC options vs 49-symbol
liquidation events vs a 312-symbol daily panel). Averaging their published bps would be
meaningless. I therefore build two genuine common bases and, only at the end, combine
*across* them at the portfolio-return level (Track C), where the shared unit is the calendar
day.

### Track A — EVENT BASIS (liquidation-cascade family)
* Population: `data/events/liq_cascade_dataset.parquet` (49-symbol clean universe, 38,141
  events, 2021-01→2026-07 — the exact file `LIQ_CASCADE_REPEAT_V1`, `LIQ_CASCADE_FAR_FROM_LOW_V1`
  and round-3 W5 were built on). `data/events/cascade_dataset.parquet` (same 49 symbols,
  extends to 2026-08-27) is used ONLY as a held-out recency extension, never for fitting.
* Trade: LONG at `event_time`, hold 4h, exit at `fwd_4h`. One fixed horizon, no horizon search
  (round-3 W5 T1.4 already killed SELECT_HORIZON on this base).
* Both `kind`s pooled (the trade is always LONG so the unresolved SHORT_SQUEEZE *sign
  convention* does not enter); `LONG_CASCADE`-only reported as robustness.
* Why this basis for the ETA question: ~6,600 raw events/year. It is the project's only
  high-frequency episodic population. If a low ETA exists anywhere, it is here.

### Track B — CROSS-SECTIONAL DAILY BASIS (xsmom / Amihud family)
* Population: daily perp OHLCV panel from `/home/qbee/futur-data-v2/data_v2/normalized/perp_ohlcv`
  (venue=binance, 312 symbols), aggregated from 5m bars. Read-only source.
* Causal universe filter (PIT): 30d **trailing** median quote volume ≥ $1,000,000 and ≥ 60
  trailing days of history and ≥ 250 5m bars on the day. No survivorship filter, no
  forward-looking listing filter.
* Trade: cross-sectional decile portfolios, rebalanced weekly (registry convention) and daily
  (frequency variant). Horizon = rebalance interval, non-overlapping.

---

## 2. Signals (all pre-existing, none invented here)

Every signal below is either a frozen/shadow registry alpha, or a mechanism already tried and
reported in the rounds 1–3 graveyard. **A WEAK component is deliberately kept** — the whole
point of the axis is that a decorrelated WEAK signal can carry a composite.

### Track A signals (all columns of the event dataset that are pre-event by construction)
`n_events_sym_24h` (LIQ_CASCADE_REPEAT_V1), `dist_low_24h` (LIQ_CASCADE_FAR_FROM_LOW_V1),
`dist_low_7d`, `oi_drop_z`, `oi_drop_30m`, `oi_drop_1h`, `btc_vol_24h` (round-3 W5 T1.1),
`vol_24h` (W5 T1.3), `n_events_mktwide_30m` (W5 T1.2), `mins_since_prev_event` (round-2 #9),
`ls_ratio_z` (W5 T1.10), `toptrader_z` (WHALE_LSR_SCREEN_V1), `taker_z` (W5 T1.12),
`funding_z30` (round-2 #11, DEAD alone), `oi_vs_7d`, `oi_pctile_30d`, `oi_ret_2h`, `oi_ret_24h`
(SHORT_COVERING_CONTINUATION_V1 family), `px_accel` (round-2 #8), `px_ret_1h`, `ret_24h`,
`btc_ret_30m` (spillover), `taker_delta_1h`, `toptrader_delta_1h`, `hour_utc`→ASIA-session
dummy (W5 T1.6).
**Excluded as outcome-contaminated:** `fwd_*`, `MFE_4h`, `MAE_4h`, `label_full`, `px`, `row`.

### Track B signals
`MOM_7D` (CROSS_SECTIONAL_MOMENTUM_*_V1/V2), `MOM_30D`, `REV_1D`, `AMIHUD_7D`
(AMIHUD_ILLIQUIDITY_PREMIUM_V1, the only VALIDATED_FOR_FORWARD cross-sectional alpha),
`AMIHUD_30D`, `VOL_20D`, `MAX_RET_7D` (lottery/MAX effect), `VOLUME_SHOCK_Z`, `TURNOVER_30D`
(size proxy), `DIST_HIGH_30D`, `SKEW_30D`, `RANGE_20D`, `BETA_BTC_30D`, `IDIOVOL_30D`.

---

## 3. PIT discipline — the two things that would silently break this axis

1. **No full-sample standardisation.** Every z-score is causal: Track A uses an
   *expanding-window* rank of the signal against **strictly prior events only** (burn-in 2,000
   events), mapped through the normal quantile function. Track B uses the **same-day
   cross-section** (which is causal by construction — it uses no future day).
2. **No full-sample sign.** The direction of every signal is fixed by one of two declared
   protocols, never by looking at the whole sample:
   * `APRIORI` — sign fixed from the published project prior (registry `expected_net_bps` sign
     or the source REPORT.md's stated direction). Listed in §4 below, frozen now.
   * `WALKFORWARD` — sign = sign of the rank-IC computed on **strictly prior** observations
     (expanding, burn-in as above), recomputed at every step.
   The APRIORI composite is the headline (zero fitting of any kind). The WALKFORWARD composite
   is the realistic operating version. Any difference between them is reported, not hidden.

## 4. A-priori signs, frozen before any test

Positive = "higher raw value predicts higher forward return".

Track A: `n_events_sym_24h` **+** (repeat pays, corroborated 2x); `dist_low_24h` **+**,
`dist_low_7d` **+** (FAR_FROM_LOW); `oi_drop_z`/`oi_drop_30m`/`oi_drop_1h` **+** (deleveraging
magnitude, W5 T1.5); `btc_vol_24h` **+** (W5 T1.1); `vol_24h` **−** (W5 T1.3 SELECT_ASSET by
own-vol was negative); `n_events_mktwide_30m` **+** (W5 T1.2); `mins_since_prev_event` **−**
(clustered pays, = repeat); `ls_ratio_z` **−** (crowded longs, W5 T1.10); `toptrader_z` **−**
(WHALE_LSR expected_net_bps = −57.8); `taker_z` **−** (W5 T1.12); `oi_vs_7d`/`oi_pctile_30d`
**−**, `oi_ret_2h`/`oi_ret_24h` **−** (short-covering: OI down is the payer); `px_accel` **−**
(deceleration, round-2 #8); `px_ret_1h` **−**, `ret_24h` **−** (exhaustion/reversal);
`btc_ret_30m` **−**; `funding_z30` **−**; `taker_delta_1h` **−**; `toptrader_delta_1h` **−**;
`ASIA_SESSION` **+** (W5 T1.6).

Track B: `MOM_7D` **+**, `MOM_30D` **+**, `REV_1D` **−**, `AMIHUD_7D` **+**, `AMIHUD_30D` **+**,
`VOL_20D` **−**, `MAX_RET_7D` **−**, `VOLUME_SHOCK_Z` **+**, `TURNOVER_30D` **−**,
`DIST_HIGH_30D` **+**, `SKEW_30D` **−**, `RANGE_20D` **−**, `BETA_BTC_30D` **−**,
`IDIOVOL_30D` **−**.

*Any signal whose realised sign contradicts this table is reported as a contradiction, not
silently flipped.*

---

## 5. The six tests (fixed now, in this order)

| # | test | rule fixed in advance |
|---|---|---|
| E1 | **Correlation matrix** | Per-signal return series built identically (top-decile-minus-population for Track A; decile long-short for Track B), then Pearson **and** Spearman correlation of the *return* series, plus the mean daily cross-sectional correlation of the *scores*. Both matrices reported. No thresholding. |
| E2 | **Naive composite** | Equal weights, `1/K * sum(sign_i * z_i)`. No optimisation whatsoever. This is the reference. |
| E3 | **Vote / concordance** | Enter only if `#{i : sign_i * z_i > 0} >= K_min`. `K_min` swept over the full integer range; the whole curve is reported, not its argmax. |
| E4 | **Risk-weighted** | (a) inverse trailing volatility of each component's own return series; (b) confidence weighting `w_i ∝ max(0, trailing IC_i)`. Both weights estimated on an **expanding window ending strictly before** each evaluated observation. Only OOS performance reported. |
| E5 | **Orthogonalisation** | Each signal regressed on all others (same day / same event, causal cross-section); residual re-tested. A signal whose edge dies is declared a **DUPLICATE** — reported as portfolio-dedup information. |
| E6 | **The decisive test** | Does the composite beat its **best component**, net, declustered, walk-forward? "Best component" is itself chosen **walk-forward** (best trailing Sharpe at each point), never with hindsight. A hindsight-best-component number is reported alongside, explicitly labelled as an unattainable upper bound. |

## 6. Thresholds, frozen now

* Selection quantiles for Track A composite entry: **top 10% / 20% / 30%** of the composite's
  *trailing* distribution. All three reported; no other quantile will be tried.
* Track B portfolios: **deciles** (top 10% / bottom 10%) — the registry's own convention for
  AMIHUD_ILLIQUIDITY_PREMIUM_V1; quintiles reported as robustness only.
* Costs: `net_bps = gross_bps − 14`; stress `− 28`. **Track B costs are charged on the
  composite's own measured turnover**, `cost = turnover_fraction * 14bps` per rebalance, not on
  a per-component average (explicit pitfall in the mandate).
* Burn-in before any signal is usable: 2,000 events (Track A) / 60 trailing days + 26 weeks
  (Track B).

## 7. Declustering, decided before seeing anything

* **Track A** — L1: same-symbol overlapping 4h holding windows collapsed to the first event of
  each cluster. L2: calendar day (all symbols). L3: calendar month. Block bootstrap blocks =
  the L2 unit (day) for daily-level stats, L3 (month) for the conservative CI.
* **Track B** — L1: non-overlapping rebalance periods (already non-overlapping by
  construction). L2: calendar day. L3: calendar month.
* **The declustering is applied to the COMPOSITE's episodes, not the components'** (explicit
  pitfall in the mandate): two signals can be weakly return-correlated and still fire on the
  same days.

## 8. Gate + verdict

Full §2 gate of the BRIEFING on every composite, with `eta_forward_confirmation` reported
first. Verdicts strictly from the BRIEFING §3 list. `ETA > 3 years` ⟹
`UNCONFIRMABLE_IN_HORIZON` regardless of bps.

## 9. Declared risks of this specific axis

* **Overfitting the combination** — mitigated by E2 being the headline (zero free parameters)
  and by every estimated weight in E4 being expanding-window.
* **Multiple testing across `K_min` and quantiles** — mitigated by reporting whole curves, and
  by the fact that E2 (parameter-free) is the number that decides the verdict.
* **Composite turnover** — measured directly, charged directly.
* **`REFIT` honesty** — anything I decide after seeing a result is stamped `REFIT` in the
  report and is not allowed to change a verdict.
