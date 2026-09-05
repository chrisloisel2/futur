# W7_OPTIONS_VOL_SURFACE — REPORT (Alpha Hunt Round 4, 2026-09-03)

Axis: the Deribit options surface (IV term structure, skew, dealer gamma, pin risk, VRP,
DVOL cross-asset, block flow) used to predict **perp direction**. Preregistered in
`PREREGISTRATION.md` before any outcome test. Read-only on all `data/`, `src/institutional/`,
`reports/live_alpha_lab/`, `configs/`. Nothing written outside this worker directory.

> Write-up completed 2026-09-05 after the round-4 session limit. No new computation was run
> at write-up time: every number here comes from `evidence/results_*.json`, which was verified
> field-by-field against `RESULTS.json` (36 gate rows, 0 mismatches). The write-up pass only
> **added** derived §2 fields — headline row per mechanism, the second ETA figure, the
> execution ruling and the independence note — through `evidence/consolidate.py`, which is
> idempotent and regenerates `RESULTS.json` byte-identically.

---

## 0. Bottom line

**7/7 preregistered mechanisms fail. No `VALIDATED_FOR_FORWARD` candidate.** Five of the seven
are `DEAD` and two are `WEAK` (M2 was tested in three variants, so `RESULTS.json` carries nine
entries and 36 gate rows). The axis is not killed by bad luck on any one test — it is killed by an
arithmetic ceiling that I computed and preregistered *before* running anything, and which the
results then landed against exactly where predicted.

Three findings are worth carrying forward even though nothing is tradable:

1. **The ETA identity.** `ETA_years = 31.4 / SR_net²`. The episode rate cancels out exactly.
   This means the briefing's instruction to "look for mechanisms with a high rate of
   independent episodes" is, on its own, **wrong** — frequency does not buy confirmability,
   Sharpe does, and since the 14bps cost is charged per episode, raising the frequency usually
   *lowers* Sharpe and so *worsens* the ETA. M7 is the empirical proof: 3086 independent
   episodes, ETA still 10–56 years. The lever that works is reducing σ (market-neutral pairs,
   cross-sectional tilts), not raising the frequency.
2. **A declustering bug I found in my own first pass**, which made M5's true t of 1.06 look
   like a t of 4.49. Documented in full in §5 — this is the fifth time this trap has been hit in
   this project, and this time the counter-example is preserved in `RESULTS.json` under the
   deliberately ugly key `gate_per_asset_WRONG`.
3. **Dealer gamma does not exist in this dataset in any testable form.** Not "is weak" —
   its own central prediction fails with the *wrong sign* before any trading rule is applied.
   §4 explains what data would be needed to test it properly.

| verdict | mechanisms |
|---|---|
| `VALIDATED_FOR_FORWARD` | — (none) |
| `PROMISING_NEEDS_VALIDATION` | — (none) |
| `WEAK` | M6 (DVOL BTC-vs-ETH divergence), M7 (options block delta flow → perp) |
| `DEAD` | M1 (IV term structure), M2 ×2 (skew velocity 1d/3d), M2c (skew capitulation), M3 (dealer gamma), M4 (pin risk), M5 (VRP → alts) |

---

## 1. Methodology

### 1.1 What this axis is — and what it deliberately is not

Round 2's W6 already mined this data for **volatility forecasting** and its three findings
(`rv_iv_spread`, `far_otm_put_share`, `block_count_24h`) are frozen inside
`VOL_FORECAST_LAYER_V1` (SHADOW_LIVE since 2026-08-31, `RISK_OVERLAY_ONLY`, BTCUSDT, target =
forward realised vol). **W7 does not re-test any of that.** My contribution is the
*directional and flow* half of the surface, which the project had never touched:

| W6 round 2 (frozen in `VOL_FORECAST_LAYER_V1`) | W7 round 4 (this report) |
|---|---|
| RV/IV spread → forward **RV** | IV term-structure slope → perp **direction** (M1) |
| Far-OTM put **share** → forward **RV** | Skew **velocity** and post-panic normalisation → direction (M2, M2c) |
| Block **count/notional**, daily → forward **RV** | **Delta-weighted** block flow, **1-hour** → direction (M7) |
| — (OI-weighted constructions explicitly skipped) | Dealer **gamma** proxy → momentum/reversion **regime** (M3) |
| — (max-pain skipped for lack of OI) | Pin risk / strike magnet → direction (M4) |
| VRP existence on BTC | VRP **exported** as a regime onto 41 alt perps (M5) |
| — (no ETH leg at all) | **DVOL_ETH** vs DVOL_BTC → BTC/ETH pair (M6) |

The only place the two axes touch is the block-flow input, and there the weighting (BS delta
vs raw notional), the horizon (1h vs 1d) and the target (direction vs RV) all differ. The one
W7 result that speaks to vol forecasting is a **negative** one that contradicts rather than
duplicates the layer: short-gamma days do **not** show higher forward |return| (§3.3).

### 1.2 Data and coverage

| source | coverage used | role |
|---|---|---|
| `data/options_backfill/deribit/trades/BTC/*.parquet` | 2023-01-01 → 2026-09-03, ~1340 days, 16.2M trades; ~1294 usable once the perp panel's end date binds | per-trade IV / strike / expiry / direction / `is_block`. **BTC only** |
| `data/options_backfill/deribit/DVOL_{BTC,ETH}_1d.parquet` | 2021-03-24 → 2026-09-03, 1990 d | the only ETH options series that exists here |
| `/home/qbee/futur-data-v2/data_v2/normalized/perp_ohlcv/` | to **2026-07-31** | forward outcomes, BTC/ETH + 41-name alt cross-section |

The perp panel ends **2026-07-31**; that, not the options data, is the binding end date on
every forward outcome. Trade-level mechanisms (M1–M4, M7) get the ~1294-day window;
DVOL-only mechanisms (M5, M6) get the ~1990-day window. **The two windows are never spliced**
and each mechanism's year-by-year table is computed on its own window.

**There is no open interest anywhere in this dataset.** Confirmed for the third time in this
project (after A14 and W6-round2). Every gamma/pin construction here is a *flow-accumulation
proxy*, labelled as such, never presented as a real GEX.

### 1.3 PIT and causality

- Deribit and Binance are different venues on different clocks. Joins are on the **UTC
  calendar day**, never `nearest`. A forward outcome for day `d` starts at a bar timestamp
  `>= d+1 00:00 UTC`.
- Every rolling feature (z-scores, percentiles, EWMAs, gamma accumulation) is **causal** —
  `gate.py::causal_z` / `causal_pct` use trailing windows ending at or before the decision
  timestamp. There is **no full-sample standardisation anywhere**; that is the specific bug
  that inflated W6-round2's M10 terciles.
- Signal at day `d` uses only trades with `ts <= d 23:59:59 UTC`; the position is taken at
  `d+1 00:00`.
- Where a sign had to be learned rather than derived from the mechanism, it was learned on
  the **first half only** and the gate was run **out-of-sample on the second half**
  (`sign_learned` / `oos_start` are stamped on those rows: M1, M2, M6). M2c, M3, M4 and M7
  have their direction fixed by the mechanism itself and are not sign-fitted at all.

### 1.4 One gate, one code path

Every mechanism is reduced to the same representation — a daily (or hourly) **position
matrix** and the matching **forward return matrix** — and then passed through the single
function `evidence/gate.py::run_gate`. No mechanism can quietly use a friendlier statistic
than another: the §2 columns are all derived from that one representation.

### 1.5 Costs are charged on turnover, not per signal

Project convention: `net = gross − 14bps` per **round trip**, implemented as **7bps per unit
of |position change|**; stress = 28bps round trip = 14bps per unit. Because the cost is
applied to `pos.diff().abs()`, a mechanism that holds the same position for five days pays
one round trip, not five — this matters for M6 (7.9-day episodes) and against M2's 1.2-day
episodes, which pay a round trip almost every day. Everything reported as `net_bps` is
per **episode**, so it already includes the whole holding period's turnover.

### 1.6 Declustering — on this axis, **N is a count of days**

Three levels are reported for every row, per briefing §1.2. But the important thing about
**this** axis is what those levels degenerate to:

> **My universe is BTC and ETH only.** Level 1 (same-asset / 24h) is therefore a *no-op*: with
> one asset there is exactly one observation per day, so **L1 ≡ L2 ≡ the calendar-day count**.
> Level 2 — the calendar day — is the only decluster that does any work, and level 3
> (contiguous position episode) is what the t-stats are actually computed on. **My independent
> N is a number of DAYS, never a number of observations**, and it is hard-capped by the data:
> ~1294 usable trade-days in total. After episode declustering the real numbers are between
> **42 and 393**.

Per mechanism, explicitly:

| mechanism | universe | L1 | **L2 (days)** | L3 (episodes) | what actually limits it |
|---|---|---|---|---|---|
| M1 term structure | BTC | 246 | **246** | 100 | L1≡L2 by construction; 100 episodes of 2.46 d is the whole evidence base |
| M2 skew velocity 1d | BTC | 272 | **272** | 223 | a near-daily-flipping signal buys almost no independence over the day count — and pays a round trip for each flip |
| M2 skew velocity 3d | BTC | 267 | **267** | 151 | idem, 1.77-day episodes |
| M2c skew capitulation | BTC | 179 | **179** | 70 | ~20 capitulation episodes per year. 70 is *everything* |
| M3 dealer gamma | BTC | 245–574 | **245–574** | 155–393 | worse than the day count: the proxy's 1d autocorrelation is **0.80**, so consecutive same-quintile days are near-duplicates |
| M4 pin risk | BTC, monthly | 42 | **42** | 42 | one observation per monthly expiry — ~12/yr, 50 in the whole sample. The structural floor of the axis |
| M5 VRP → alts | 41 alts | 342 | **342** | 66 | **the trap**: per-asset counting claimed L1=12958 / L3=2496. One basket-day is ONE observation (§5) |
| M6 DVOL BTC-vs-ETH | BTC+ETH | 535 | **535** | 68 | two legs are ONE pair trade; the uncollapsed pair rows reported L1 = 1050–1124 asset-days for 525–562 actual daily observations |
| M7 block delta flow | BTC, hourly | 3845 | **3845** | 3086 | the one mechanism counted in hours, not days — and the only one whose ETA is not day-bound |

M7 is the exception that proves the rule, and it is instructive: it *does* have thousands of
independent episodes, and it *still* has an ETA of 10 to 56 years — because frequency is not
what ETA depends on (§1.7).

### 1.7 The ETA identity — computed and preregistered before any test

Sizing `n_required` at power 80%, α 5% on a **50%-haircut** edge, and dividing by the episode
rate `R`, gives a closed form in which `R` cancels exactly:

```
n_required = (z₀.₉₇₅ + z₀.₈₀)² · (σ / (0.5·μ))²  =  31.4 · (σ/μ)²
ETA_years  = n_required / R  =  31.4 · (σ/μ)² / R  =  31.4 / SR_annual²      [since SR = (μ/σ)·√R]
```

| SR net (discovery) | ETA @ 50% haircut |
|---|---|
| 1.0 | 31.4 y |
| 2.0 | 7.8 y |
| **3.24** | **3.0 y** — the `VALIDATED_FOR_FORWARD` boundary |
| 5.6 | 1.0 y |

So the bar for this axis is **net annualised Sharpe ≥ 3.24**, and the briefing's stretch goal
(ETA < 1 y) is **SR ≥ 5.6**. The best number reached anywhere in W7 is **SR 1.22** — and 0.21
after its confound control. Nothing was close; and I said in advance that a majority of
`UNCONFIRMABLE_IN_HORIZON`-shaped outcomes was expected, so this cannot be read as a post-hoc
excuse.

**Two ETA numbers are reported for every mechanism**, and they answer slightly different
questions:
- **empirical** = `n_required / event_rate`, with the event rate measured on the **last 6
  months only** (deliberately conservative — it penalises a mechanism that has stopped firing);
- **identity** = `31.4 / SR_net²`, which uses the in-sample rate implicitly and is the
  like-for-like comparison across mechanisms.

Where they disagree the **larger governs**, because a disagreement means the mechanism has
been firing more often lately than its Sharpe can justify. M6 is the case in point: 12.2 y
empirical vs 21.3 y by identity. Both are far beyond the 3-year bar.

### 1.8 Execution: this project has **no options execution**

No option, no variance swap, no DVOL future can be traded here. That is why round 2's three
PROMISING options findings became a risk overlay instead of a sleeve. I preregistered
(`PREREGISTRATION §0b`) that any signal requiring an options trade would be marked
`NO_VEHICLE` and could not be rated better than `DATA_LIMITED`, and I designed every mechanism
so that **options data enter only as a signal while the position is taken in perps**.

Ruling, mechanism by mechanism:

| mechanism | vehicle | ruling |
|---|---|---|
| M1 IV term structure | BTCUSDT perp, daily | **PERP-EXPRESSIBLE.** Traded ATM IV by expiry bucket builds the signal; the position is a plain long/short perp. Vehicle fine — the statistics kill it |
| M2 skew velocity (1d, 3d) | BTCUSDT perp, daily | **PERP-EXPRESSIBLE.** Skew is a signal input only. Vehicle fine, statistics dead |
| M2c skew capitulation | BTCUSDT perp, long-only | **PERP-EXPRESSIBLE.** Long the perp after a put-panic normalises |
| M3 dealer gamma | BTCUSDT perp, regime switch | **PERP-EXPRESSIBLE BY DESIGN.** A true GEX play would be an options trade (`NO_VEHICLE` here); instead the gamma proxy is used only as a *regime switch* on a perp momentum/reversion position. The vehicle is legitimate — what is missing is open interest, which makes the **signal** a proxy |
| M4 pin risk | BTCUSDT perp, 1–3d pre-expiry | **PERP-EXPRESSIBLE.** Direction = sign(magnet strike − spot). No options leg. Vehicle fine; the magnet does not exist |
| M5 VRP → alts | 41-name alt perp basket / beta tilt | **PERP-EXPRESSIBLE.** VRP is an index-derived regime label; the position is alt perps. This is the one mechanism whose *expression* is not BTC/ETH — which is exactly where the N inflated (§5) |
| M6 DVOL BTC-vs-ETH | BTCUSDT/ETHUSDT dollar-neutral perp pair | **PERP-EXPRESSIBLE.** Trading the DVOL divergence itself would be `NO_VEHICLE` and is deliberately *not* what is tested |
| M7 block delta flow | BTCUSDT perp, hourly | **PERP-EXPRESSIBLE — the vehicle is the whole idea.** The hypothesis is precisely that the dealer's hedge lands in the perp. It dies because the effect is 5× below the perp's own taker cost |

**None of the seven was killed by the vehicle.** All seven are tradable as perp positions; all
seven fail on evidence. That is the useful form of this negative result: the axis is not
"blocked pending an options venue", it is *tested and empty for direction*.

---

## 2. Results — the full §2 gate

### 2.1 Headline gate table — one row per mechanism

| mechanism | verdict | perp? | n_raw | L1 | **L2 = days** | L3 | net bps | net bps @28 | t (L3) | bootstrap CI95 | SR net | ex-best-yr | n_req | ep/wk | **ETA emp.** | **ETA ident.** |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| M1_IV_TERM_STRUCTURE_DIRECTION | `DEAD` | yes | 246 | 246 | **246** | 100 | -22.11 | -29.25 | -0.63 | [-92, 46] | -0.492 | -52 (t=-1.53) | 8007 | 1.15 | **133.0 y** | **129.7 y** |
| M2_SKEW_VELOCITY_1D | `DEAD` | yes | 272 | 272 | **272** | 223 | -2.32 | -11.99 | -0.12 | [-38, 35] | -0.277 | -17 (t=-0.51) | 450976 | 2.73 | **3165.0 y** | **409.2 y** |
| M2_SKEW_VELOCITY_3D | `DEAD` | yes | 267 | 267 | **267** | 151 | -27.34 | -35.45 | -1.07 | [-79, 22] | -0.997 | -36 (t=-1.24) | 4149 | 1.69 | **47.0 y** | **31.6 y** |
| M2c_SKEW_CAPITULATION_NORMALISATION | `DEAD` | yes | 179 | 179 | **179** | 70 | -9.89 | -16.89 | -0.19 | [-108, 92] | -0.180 | -40 (t=-0.72) | 62021 | 0.61 | **1931.5 y** | **969.0 y** |
| M3_DEALER_GAMMA_PROXY | `DEAD` | yes | 245 | 245 | **245** | 155 | 12.12 | 1.23 | 0.46 | [-38, 64] | 0.200 | -7 (t=-0.25) | 22762 | 0.35 | **1260.2 y** | **784.9 y** |
| M4_PIN_RISK_STRIKE_MAGNET | `DEAD` | yes | 42 | 42 | **42** | 42 | -30.48 | -37.48 | -1.14 | [-81, 24] | -0.744 | -40 (t=-1.41) | 1009 | 0.23 | **83.8 y** | **56.7 y** |
| M5_VRP_CROSS_ASSET_RISK_APPETITE | `DEAD` | yes | 342 | 342 | **342** | 66 | 110.85 | 103.85 | 1.06 | [-88, 327] | 0.406 | +67 (t=+0.61) | 1857 | 0.23 | **154.2 y** | **190.5 y** |
| M6_DVOL_BTC_VS_ETH_DIVERGENCE | `WEAK` | yes | 535 | 535 | **535** | 68 | 77.44 | 70.33 | 2.50 | [23, 142] | 1.215 | +57 (t=+2.12) | 342 | 0.54 | **12.2 y** | **21.3 y** |
| M7_OPTIONS_BLOCK_FLOW_TO_PERP | `WEAK` | yes | 3845 | 3845 | **3845** | 3086 | -5.14 | -12.97 | -3.96 | [-8, -3] | -0.749 | -7 (t=-4.80) | 6168 | 11.38 | **10.4 y** | **56.0 y** |

### 2.2 Year-by-year of the same headline rows (net bps / episode, n episodes)

| mechanism | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 | ex-best-year |
|---|---|---|---|---|---|---|---|
| M1_IV_TERM_STRUCTURE_DIRECTION | — | — | — | +135 (n=16) | -52 (n=52) | -53 (n=32) | -52 (t=-1.53, −2024) |
| M2_SKEW_VELOCITY_1D | — | — | — | -89 (n=28) | +11 (n=118) | +9 (n=77) | -17 (t=-0.51, −2025) |
| M2_SKEW_VELOCITY_3D | — | — | — | -139 (n=22) | -8 (n=81) | -8 (n=48) | -36 (t=-1.24, −2026) |
| M2c_SKEW_CAPITULATION_NORMALISATION | — | — | +121 (n=13) | -30 (n=15) | -78 (n=26) | +13 (n=16) | -40 (t=-0.72, −2023) |
| M3_DEALER_GAMMA_PROXY | — | — | +3 (n=48) | -17 (n=39) | -7 (n=55) | +217 (n=13) | -7 (t=-0.25, −2026) |
| M4_PIN_RISK_STRIKE_MAGNET | — | — | -37 (n=12) | -16 (n=12) | -67 (n=12) | +26 (n=6) | -40 (t=-1.41, −2026) |
| M5_VRP_CROSS_ASSET_RISK_APPETITE | +321 (n=9) | +61 (n=15) | +212 (n=10) | +358 (n=10) | -98 (n=15) | -103 (n=7) | +67 (t=+0.61, −2024) |
| M6_DVOL_BTC_VS_ETH_DIVERGENCE | — | — | +674 (n=1) | +48 (n=29) | +114 (n=24) | +32 (n=14) | +57 (t=+2.12, −2025) |
| M7_OPTIONS_BLOCK_FLOW_TO_PERP | — | — | -1 (n=981) | -5 (n=827) | -8 (n=941) | -10 (n=337) | -7 (t=-4.80, −2023) |

### 2.3 Sample windows and episode length

| mechanism | primary gate row | sample | mean episode (days) | gross bps/ep | why this row |
|---|---|---|---|---|---|
| M1_IV_TERM_STRUCTURE_DIRECTION | `M1_IV_TERM_STRUCTURE_DIRECTION` | 2024-10-09 → 2026-07-17 | 2.46 | -14.97 | only row; OOS (sign fitted on the first half only). |
| M2_SKEW_VELOCITY_1D | `M2_SKEW_VELOCITY_1D` | 2024-10-09 → 2026-07-17 | 1.22 | 7.35 | only row for the 1d velocity feature; OOS. |
| M2_SKEW_VELOCITY_3D | `M2_SKEW_VELOCITY_3D` | 2024-10-09 → 2026-07-17 | 1.77 | -19.23 | only row for the 3d velocity feature; OOS. |
| M2c_SKEW_CAPITULATION_NORMALISATION | `M2c_SKEW_CAPITULATION_NORMALISATION` | 2023-01-01 → 2026-07-17 | 2.56 | -2.89 | only row; direction preregistered LONG, no sign fitting. |
| M3_DEALER_GAMMA_PROXY | `M3_gamma_detrended_60d_momentum_arm` | 2023-01-01 → 2026-07-17 | 1.58 | 23.00 | BEST of the 9 arms tested (the only one with positive net bps). Choosing the best of 9 is itself optimistic and it still fails; the other 8 are in the gate block. |
| M4_PIN_RISK_STRIKE_MAGNET | `M4_pin_magnet_lag1d` | 2023-01-01 → 2026-07-17 | 1.00 | -23.48 | preregistered horizon (closest to expiry). lag2d is the best of the 3 lags (net +38.7, t=1.41) but the 3 lags disagree in sign, so it is a 1-in-3 multiple-comparison cell, reported in the gate block and not promoted. |
| M5_VRP_CROSS_ASSET_RISK_APPETITE | `M5_high_vrp_long_basket_COLLAPSED` | 2021-03-24 → 2026-07-31 | 5.18 | 117.85 | the CORRECTLY declustered basket row. The per-asset row (t=4.49) is retained in gate_per_asset_WRONG as the counter-example, never as the result. |
| M6_DVOL_BTC_VS_ETH_DIVERGENCE | `COLLAPSED_CORRECT` | 2023-12-26 → 2026-07-31 | 7.87 | 84.33 | the correctly declustered pair row (basket = ONE instrument), OOS from 2023-12-26. |
| M7_OPTIONS_BLOCK_FLOW_TO_PERP | `F1_M7_hold1h` | 2021-01-01 → 2026-07-31 | 1.25 | 2.70 | 1h is where the hedging effect actually lives; the 4/12/24/72h rows show it decaying, all in the gate block. |

All 36 gate rows (every arm, every horizon, every declustering variant) are in the appendix,
§7, and machine-readable in `RESULTS.json`.

---

## 3. Mechanism by mechanism

### 3.1 M1 — IV term-structure slope / inversion → perp direction — `DEAD`

*Hypothesis.* Slope = median traded ATM IV (|ln(K/S)| ≤ 0.05) of near expiries minus far
expiries. Inversion (near above far) marks stress; test whether it predicts BTC perp
**direction**. Distinct from W6-M7/M8, which tested slope level and slope change against
forward **RV** and found both confound-killed by vol clustering; the directional target had
never been run.

*Result.* Sign learned on the first half, gate run OOS from 2024-10-09: net **−22.1 bps**
per episode, t = **−0.63** on **100** independent episodes, SR **−0.49**, CI95 [−92, +46].
The full-sample sign-fitted version — which is optimistic by construction — is *also* negative
(−11.3 bps). The two-arm descriptive test (inverted vs contango, forward 1d, full sample) gives
+36.2 vs +6.2 bps, diff +30.0, **t = 1.57, p = 0.12, Cohen's d = 0.12** — no separation.

*Why it is dead and not merely weak.* Both the OOS trading rule and the full-sample descriptive
arm-vs-arm test point the same way: nothing there. Independent N = 100 episodes over 246 days.

### 3.2 M2 / M2c — skew velocity and post-panic normalisation — `DEAD` (×3)

*Hypothesis.* Not the skew **level** (already used by the validated `LIQ_REPEAT_SKEW_OVERLAY`
as a conditioner on cascade-repeat probability, and by W6-M6 as far-OTM put share → RV) but its
**velocity**: `d_skew_1d`, `d_skew_3d`, and a "normalisation after shock" state (skew in the
top decile within 5d, then falling for 3d = capitulation finished → long).

*Result.* 1d velocity: net **−2.3 bps**, t = −0.12, 223 episodes. 3d velocity: net
**−27.3 bps**, t = −1.07, 151 episodes. Capitulation-normalisation (direction preregistered
LONG, no sign fitting): net **−9.9 bps** over **70** episodes, t = −0.19, and the two-arm test
against all other days gives −1.1 vs +11.6 bps, **t = −0.61, p = 0.54** — the normalising days
are, if anything, slightly *worse* than average days.

*Note on the 1d variant.* Its gross is positive (+7.4 bps) and its net is −2.3: it is killed
by turnover, not by the absence of an effect. But it is not `COST_FRAGILE` in the project's
sense — `COST_FRAGILE` means dying between 14 and 28bps, and this dies at a gross that never
reaches the 14bps floor. Its 1.22-day episodes mean it pays a round trip almost daily.

### 3.3 M3 — dealer gamma proxy → momentum/reversion regime — `DEAD`

This was the core of the axis: the mechanism the project has never had. W6-round2 explicitly
*skipped* every OI-weighted construction rather than fake one; I built the flow-accumulated
proxy instead and tested it honestly.

*Construction.* Each trade's `direction` gives the taker side; assume taker = customer, so
dealer position = −customer position. Each contract is weighted by Black-Scholes gamma at the
prevailing index price and decayed/expired at its expiry, aggregated to a daily
`dealer_gamma_proxy`. Tested in **3 drift-robust constructions** (raw level, 60d-detrended
level, 1d gamma flow) × **3 arms** (momentum / reversion / combined) = 9 gate rows.

*Proxy quality — stated honestly, as preregistered.* Gross accumulated |position| grows
**152×** over the sample (1393 → 211890), because closing legs of positions opened before
2023-01-01 have no opening leg in the sample and because taker == customer is violated by
market-maker taker flow. The **level is therefore uninterpretable**; only trailing-window
relative measures are used. The proxy's 1d autocorrelation is 0.80 and it is positive only
16.5% of days.

*Result — the mechanism's own central prediction fails first, with the wrong sign.* Before any
trading rule: short-gamma days should show **higher** forward |return| than long-gamma days.
Detrended construction: **157 vs 178 bps, t = −1.30** — the wrong sign. Raw level: 165 vs 154,
t = +0.83, p = 0.41. Flow: 158 vs 155, t = +0.24, p = 0.81. The §1.3 arm-vs-arm test of
momentum payoff under short vs long gamma gives **t = −0.02 to −1.05, p = 0.29 to 0.98**.
Every one of the 9 trading arms is net-negative except the detrended momentum arm (+12.1 bps,
t = 0.46, SR 0.20) — and that is the best of 9, which is itself an optimistic selection, and it
collapses to +1.2 bps under the 28bps stress and to −6.7 bps ex-2026.

*Verdict.* `DEAD` as tested. A **true** GEX test is `DATA_LIMITED`: it needs open interest per
strike/expiry (Deribit publishes it per instrument via `get_book_summary_by_currency`), which
does not exist in this dataset. I am not calling the *idea* dead — I am calling the version
testable with the data we have dead, and I have stated exactly what would change the answer.

### 3.4 M4 — pin risk / strike magnet — `DEAD`

*Result.* 50 monthly expiries, 126 observations, **42** usable episodes. Price moves **toward**
the magnet strike on only **40.5%** of lag-1 days — a magnet would need > 50%. The three lags
disagree in sign (−30.5 / +38.7 / −58.0 bps net), so the single positive cell (lag-2, t = 1.41)
is a 1-in-3 multiple-comparison artifact, not a mechanism.

*The preregistered killer fired exactly as written.* I predicted before testing: event rate
~12–16/yr → per-episode σ is a 2-day BTC move (~450 bps) and μ would need to be ~200 bps to
clear the frontier. Measured: 0.23 episodes/week, and even taking the flattering lag-2 cell at
face value, ETA = **55 years**. There is no amount of additional history that fixes this —
12 expiries a year is a structural ceiling.

### 3.5 M5 — VRP as cross-asset risk appetite → alts — `DEAD` *(the cautionary result)*

*Hypothesis.* `VRP = DVOL_BTC(t) − RV_BTC(t−30d)`, causal, as a risk-appetite gauge exported
onto a 41-name alt cross-section. Preregistered as one of the two structurally best ETA
candidates because a cross-sectional expression is the only σ-reduction available on this axis.

*What per-asset counting said.* Net **+2.95 bps/day**, **t = +4.49** on "2496 episodes",
surviving the 28bps stress (+2.77). An apparently strong, cost-robust edge.

*What it actually is.* The 41 alts move together and the signal is a single BTC-derived
regime label: **one basket-day is ONE observation**, not 41. L2 (calendar days) = 342 was
already telling the truth while L3 was reporting 2496. Collapsed to the single synthetic
instrument it really is: net +110.9 bps/episode over **66** real episodes, **t = +1.06**,
CI95 **[−88, +327]** straddling zero, SR 0.41.

*And then the year-by-year finishes it.* The effect is 2021–2024 only and **negative in 2025
and 2026** (−98 and −103 bps/episode). Ex-best-year drops it to t = 0.61. The arm-vs-arm tests
(high-VRP vs low-VRP, on the basket, on the beta tilt, and on the risk-on expression) give
t = 1.26 / 1.02 / 1.32, p = 0.21 / 0.31 / 0.19 — no separation on any of the three. The
unconditional beta-tilt control is itself negative (SR −0.38), so there was no base effect for
the VRP conditioner to improve.

*Verdict.* `DEAD`. Both the WRONG per-asset gate and the CORRECT collapsed gate are kept in
`RESULTS.json` (`gate_per_asset_WRONG` / `gate_collapsed_CORRECT`) so the size of the illusion
stays visible: **t 4.49 → 1.06 on identical data**.

### 3.6 M6 — DVOL_ETH vs DVOL_BTC divergence → BTC/ETH pair — `WEAK`

The best mechanism of the axis, and the only one to clear t > 2 after correct declustering.
Preregistered as the structurally most likely to clear the ETA frontier, because a
dollar-neutral pair roughly halves σ relative to an outright. `DVOL_ETH` is used here for the
first time in this project.

*Result.* Sign learned on the first half (−1 ⇒ high DVOL_ETH/DVOL_BTC ⇒ short BTC / long ETH),
gate OOS from 2023-12-26: net **+77.4 bps** per episode, **t = +2.50** on **68** independent
episodes, bootstrap CI95 **[+22.6, +142.0]** excluding zero, **all four years positive**,
survives ex-best-year (t = 2.12) and survives the 28bps stress (SR 1.22 → 0.98). By the
project's usual round-2 standards this would have been reported as PROMISING.

*It then fails two controls, and that is why it is `WEAK` and not `PROMISING_NEEDS_VALIDATION`.*

1. **Confound.** `DVOL_ETH/DVOL_BTC` is **0.69 Spearman-correlated** with the trailing
   *realised* vol ratio. Orthogonalising the signal against relative realised vol and relative
   30d momentum collapses it to net **+14.8 bps, t = 0.71, SR 0.21**. The options index is
   mostly a laundered realised-vol ratio — and the realised-vol ratio traded on its own gives
   t = 1.16, i.e. the "options" content adds nothing identifiable.
2. **Knife edge.** The raw continuous Spearman IC is **−0.0024** — indistinguishable from
   zero. And tightening the quintile rule toward the tails, where a real effect should
   *strengthen*, weakens it monotonically: t = 2.28 / **2.50** / 1.83 / 1.07 at thresholds
   0.75 / **0.80** / 0.85 / 0.90. The result lives only at the exact preregistered cut.

*And independently of both controls, the ETA disqualifies it anyway*: 12.2 y empirical, 21.3 y
by identity, against a 3-year bar. `RESULTS.json` records
`UNCONFIRMABLE_IN_HORIZON` as the secondary verdict — the verdict that would have applied had
the controls passed — so the ETA finding is not lost behind the confound finding.

*Note on the pair vs the outright.* The same signal traded outright on BTC gives net −16.6 bps,
t = −0.24. The pair construction is doing real σ-reduction work (SR 1.22 vs −0.19); it simply
does not have enough μ to matter.

### 3.7 M7 — options block **delta** flow → perp, intraday — `WEAK`

*Hypothesis.* A large Deribit block forces the dealer to delta-hedge in the perp within hours:
customer net-long delta ⇒ dealer buys perp ⇒ upward pressure. Direction **preregistered from
the mechanism, never fitted**. Distinct from W6-round2 M3 (raw notional block flow, daily
horizon, DEAD): delta weighting and the 1-hour horizon are both new.

*The mechanism is real and correctly signed — that is the useful finding.* Gross **+3.06 bps**
per episode, **t = +2.09** declustered on **2719** independent episodes, in the direction the
hedging story predicts.

*It is simply ~5× too small to trade.* The project cost floor is 14 bps round trip, so net is
**−5.1 bps** at 1h. Holding longer to amortise the cost does not work, because the effect is
specifically a **1-hour** effect that decays as the holding period lengthens — gross per
episode **2.70 → 2.55 → 1.13 → 0.54 → 0.37 bps** at 1 / 4 / 12 / 24 / 72h — so gross falls at
least as fast as turnover cost does. Every horizon is net-negative.

*Not `COST_FRAGILE`.* That label means dying between 14 and 28 bps. This dies an order of
magnitude below 14. Calling it `COST_FRAGILE` would overstate how close it is.

*Why it is the most interesting failure of the round.* M7 has **3086 independent episodes** —
by far the highest event rate on the axis, exactly what the briefing asked me to hunt for. Its
ETA is still 10.4 y empirical / 56.0 y by identity. This is the ETA identity's prediction made
flesh: raising the episode rate does not buy confirmability when each episode pays 14 bps for
3 bps of signal.

---

## 4. What I killed, and why

Ordered by how much it would have cost the project to *not* kill it.

**1. M5 (VRP → alts) — killed by my own declustering, after it had already looked like a win.**
This is the one that would have shipped. It had t = 4.49, a positive edge surviving the 28bps
stress, and 2496 "episodes". It was wrong because 41 correlated alt perps driven by one BTC
regime label are **one instrument**, and one basket-day is one observation. Collapsed: t = 1.06,
CI95 straddling zero, negative in 2025 and 2026. Killed. **This trap has now been hit five
times in this project's history**; the counter-example is preserved verbatim in `RESULTS.json`
so the sixth worker can see the exact size of the illusion (§5).

**2. M6 (DVOL BTC-vs-ETH) — killed by a confound and a knife edge, not by its headline stats.**
It cleared everything the project's round-2 standard asks for: t = 2.50 declustered, bootstrap
CI excluding zero, all four years positive, survives ex-best-year and cost stress. It dies
because (a) the signal is 0.69-correlated with the realised-vol ratio and collapses to t = 0.71
when orthogonalised against it, and (b) the raw IC is zero and the effect *weakens* as the
threshold moves toward the tails, which is backwards for a real effect. **A signal that only
works at one specific quantile cut, and whose content is explained by a non-options variable,
is not an options edge.** Killed at the control stage, which is where round 2 would have
stopped and shipped it.

**3. M3 (dealer gamma) — killed by its own central prediction, before any trading rule.**
The seductive part of this mechanism is that the story is *good*: negative dealer gamma means
dealers hedge with the move, which should mean momentum and higher realised vol. If I had gone
straight to a trading rule I would have run 9 arms, found the one positive cell (+12.1 bps,
detrended momentum, SR 0.20) and had to argue about it. Instead the diagnostic that the story
itself demands — do short-gamma days have higher forward |return|? — comes back **157 vs 178
bps with the wrong sign, t = −1.3**. When a mechanism's own precondition fails, the trading
arms are noise-mining. Killed cleanly. Retest condition stated: per-strike open interest.

**4. M4 (pin risk) — killed by arithmetic I wrote down before testing.**
I preregistered the expected killer: ~12–16 expiries/yr means per-episode σ is a 2-day BTC move
(~450 bps) and μ would need ~200 bps. Measured 40.5% of moves toward the magnet (needs > 50%),
three lags disagreeing in sign, 42 episodes, ETA 55 years on the best cell. **No quantity of
additional history fixes a 12-per-year event rate.** Killed, and it stays killed.

**5. M1 and M2/M2c (term structure, skew velocity, capitulation) — killed by plain absence.**
Four gate rows (five counting M1's sign-fitted full-sample variant), every one net-negative
OOS, with the descriptive two-arm tests agreeing
(p = 0.12 and p = 0.54). No confound analysis was needed because there was nothing to explain.
Worth recording that the *level*-based uses of skew are already validated elsewhere
(`LIQ_REPEAT_SKEW_OVERLAY`) — it is the **velocity** framing, which is what I added, that is
empty.

**6. M7 (block delta flow) — killed by the cost floor, but its finding is kept.**
The one I most wanted to save, because the effect is *real* (t = +2.09 gross, correctly signed,
2719 episodes) and it is the only high-frequency mechanism on the axis. It dies at 3 bps
against a 14 bps floor, and holding longer does not amortise because the effect decays faster
than the turnover. Killed as a sleeve. **Kept as a fact**: delta-hedged options block flow does
push the perp within the hour, and that is available for free to anything already paying the
spread for another reason.

**What I did *not* kill, because I did not test it:** anything targeting forward realised
volatility. That is W6-round2's ground, frozen in `VOL_FORECAST_LAYER_V1`, and re-running it
would have manufactured a duplicate rather than a finding.

---

## 5. The bug I found in my own work

My first pass counted **per-asset** episodes. On a multi-asset expression this is silently
catastrophic:

- a 41-name alt basket registered **41 "independent" observations per calendar day**;
- a 2-leg BTC/ETH pair registered **2 per trade**.

Effect on M5: **t = 4.49 → 1.06** on identical data, `n_independent_L3` 2496 → 66. Effect on
M6: the uncollapsed pair rows counted 1050–1124 asset-days where a two-leg pair provides only
525–562 daily observations, and the collapsed instrument has 535.

**Fix.** Every multi-asset expression is collapsed to **one synthetic instrument** — the
portfolio's daily return series — *before* it reaches the gate. All multi-asset numbers in this
report are the collapsed ones. The uncollapsed numbers are deliberately retained in
`RESULTS.json` under `gate_per_asset_WRONG` rather than deleted, so that the size of the
illusion is documented rather than merely asserted.

**The general rule this yields**, which I would offer to the next worker: *the number of
independent observations is bounded by the number of independent copies of the SIGNAL, not by
the number of instruments the signal is applied to.* A single BTC-derived regime label applied
to 41 alts has one signal per day, so it has one observation per day — L2 was right and L3 was
lying, which is the reverse of the usual assumption that deeper declustering is safer. Here the
trap was caught by an **L2-vs-L3 inconsistency**: L3 (2496) exceeded L2 (342), which is
impossible for genuine episodes, since an episode spans one or more days and can never be more
numerous than days. That check is one line and I recommend it as a standing diagnostic.

---

## 6. What would change these answers

| mechanism | what is missing | is it obtainable? |
|---|---|---|
| M3 dealer gamma | **Open interest per strike/expiry** (Deribit `get_book_summary_by_currency`). Without it, inventory must be accumulated from trades under taker == customer, and gross accumulated position drifts 152× | Yes — a live collector could start today, but it is forward-only: no OI history means no backtest, so the ETA problem returns in a new form |
| M4 pin risk | Nothing data-side. It is capped at ~12 independent events/year | **No.** This is structural, not a data gap |
| M6 DVOL divergence | An ETH options **trade-level** surface (skew, term structure) rather than only the DVOL index, to test whether anything survives orthogonalisation against realised vol | Deribit publishes ETH trades; the backfill here has BTC only |
| M7 block flow | Nothing. The effect is measured and real; it is the 14bps cost floor that is binding | Only a maker-fill capability would change this — and the execution probe (A16) is already DEAD standalone |
| M1/M2/M2c | Nothing plausible. Both OOS rules and full-sample descriptives agree there is no effect | — |
| M5 VRP | Genuinely more independent regime episodes — i.e. more *years*, not more assets | No |

---

## 7. Appendix — all 36 gate rows

| # | mechanism | gate row | block | n_raw | L1 | L2 | L3 | net bps | net@28 | t (L3) | CI95 | SR | n_req | ep/wk | ETA (y) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | M1_IV_TERM_STRUCTURE_DIRECTION | `M1_IV_TERM_STRUCTURE_DIRECTION` ★ | gate | 246 | 246 | 246 | 100 | -22.11 | -29.25 | -0.63 | [-92, 46] | -0.492 | 8007 | 1.15 | 133.0 |
| 2 | M2_SKEW_VELOCITY_1D | `M2_SKEW_VELOCITY_1D` ★ | gate | 272 | 272 | 272 | 223 | -2.32 | -11.99 | -0.12 | [-38, 35] | -0.277 | 450976 | 2.73 | 3165.0 |
| 3 | M2_SKEW_VELOCITY_3D | `M2_SKEW_VELOCITY_3D` ★ | gate | 267 | 267 | 267 | 151 | -27.34 | -35.45 | -1.07 | [-79, 22] | -0.997 | 4149 | 1.69 | 47.0 |
| 4 | M2c_SKEW_CAPITULATION_NORMALISATION | `M2c_SKEW_CAPITULATION_NORMALISATION` ★ | gate | 179 | 179 | 179 | 70 | -9.89 | -16.89 | -0.19 | [-108, 92] | -0.180 | 62021 | 0.61 | 1931.5 |
| 5 | M3_DEALER_GAMMA_PROXY | `M3_gamma_level_momentum_arm` | gate | 316 | 316 | 316 | 194 | -18.14 | -29.76 | -0.81 | [-61, 26] | -0.493 | 9212 | 1.15 | 153.0 |
| 6 | M3_DEALER_GAMMA_PROXY | `M3_gamma_level_reversion_arm` | gate | 258 | 258 | 258 | 173 | -35.11 | -46.19 | -1.70 | [-76, 6] | -1.004 | 1886 | 0.27 | 134.2 |
| 7 | M3_DEALER_GAMMA_PROXY | `M3_gamma_level_combined` | gate | 574 | 574 | 574 | 363 | -26.43 | -37.92 | -1.69 | [-57, 5] | -1.012 | 3967 | 1.39 | 54.9 |
| 8 | M3_DEALER_GAMMA_PROXY | `M3_gamma_detrended_60d_momentum_arm` ★ | gate | 245 | 245 | 245 | 155 | 12.12 | 1.23 | 0.46 | [-38, 64] | 0.200 | 22762 | 0.35 | 1260.2 |
| 9 | M3_DEALER_GAMMA_PROXY | `M3_gamma_detrended_60d_reversion_arm` | gate | 274 | 274 | 274 | 180 | -33.74 | -44.66 | -1.41 | [-81, 12] | -0.811 | 2842 | 0.35 | 157.3 |
| 10 | M3_DEALER_GAMMA_PROXY | `M3_gamma_detrended_60d_combined` | gate | 519 | 519 | 519 | 330 | -12.77 | -23.91 | -0.71 | [-49, 22] | -0.479 | 20465 | 0.69 | 566.5 |
| 11 | M3_DEALER_GAMMA_PROXY | `M3_gamma_flow_1d_momentum_arm` | gate | 278 | 278 | 278 | 247 | -19.59 | -27.21 | -1.31 | [-48, 10] | -0.906 | 4496 | 1.08 | 80.0 |
| 12 | M3_DEALER_GAMMA_PROXY | `M3_gamma_flow_1d_reversion_arm` | gate | 282 | 282 | 282 | 251 | -15.37 | -22.93 | -1.08 | [-44, 13] | -0.738 | 6792 | 0.81 | 161.2 |
| 13 | M3_DEALER_GAMMA_PROXY | `M3_gamma_flow_1d_combined` | gate | 560 | 560 | 560 | 393 | -21.74 | -30.96 | -1.61 | [-48, 4] | -1.016 | 4776 | 1.54 | 59.5 |
| 14 | M4_PIN_RISK_STRIKE_MAGNET | `M4_pin_magnet_lag1d` ★ | gate | 42 | 42 | 42 | 42 | -30.48 | -37.48 | -1.14 | [-81, 24] | -0.744 | 1009 | 0.23 | 83.8 |
| 15 | M4_PIN_RISK_STRIKE_MAGNET | `M4_pin_magnet_lag2d` | gate | 42 | 42 | 42 | 42 | 38.73 | 31.73 | 1.41 | [-15, 92] | 0.606 | 664 | 0.23 | 55.2 |
| 16 | M4_PIN_RISK_STRIKE_MAGNET | `M4_pin_magnet_lag3d` | gate | 42 | 42 | 42 | 42 | -57.97 | -64.97 | -1.67 | [-125, 11] | -0.976 | 471 | 0.23 | 39.1 |
| 17 | M5_VRP_CROSS_ASSET_RISK_APPETITE | `F2_M5_high_vrp_long` | gate_per_asset_WRONG | 12958 | 12958 | 342 | 2496 | 2.95 | 2.77 | 4.49 | [2, 4] | 0.410 | 3893 | 9.46 | 7.9 |
| 18 | M5_VRP_CROSS_ASSET_RISK_APPETITE | `F2_M5_low_vrp_short` | gate_per_asset_WRONG | 14092 | 14092 | 370 | 2019 | 1.90 | 1.72 | 3.11 | [1, 3] | 0.246 | 6560 | 17.35 | 7.2 |
| 19 | M5_VRP_CROSS_ASSET_RISK_APPETITE | `M5_high_vrp_long_basket_COLLAPSED` ★ | gate_collapsed_CORRECT | 342 | 342 | 342 | 66 | 110.85 | 103.85 | 1.06 | [-88, 327] | 0.406 | 1857 | 0.23 | 154.2 |
| 20 | M5_VRP_CROSS_ASSET_RISK_APPETITE | `M5_low_vrp_short_basket_COLLAPSED` | gate_collapsed_CORRECT | 370 | 370 | 370 | 53 | 71.70 | 64.70 | 0.71 | [-125, 260] | 0.242 | 3271 | 0.42 | 148.2 |
| 21 | M5_VRP_CROSS_ASSET_RISK_APPETITE | `M5_beta_tilt_unconditional_CONTROL_COLLAPSED` | gate_collapsed_CORRECT | 1956 | 1956 | 1956 | 1 | -10341.34 | -4077.88 | — | [-10341, -10341] | -0.303 | — | — | inf |
| 22 | M5_VRP_CROSS_ASSET_RISK_APPETITE | `M5_beta_tilt_high_vrp_COLLAPSED` | gate_collapsed_CORRECT | 342 | 342 | 342 | 66 | -31.07 | -19.79 | -0.42 | [-178, 112] | -0.210 | 11977 | 0.23 | 994.7 |
| 23 | M6_DVOL_BTC_VS_ETH_DIVERGENCE | `M6_dvol_z_divergence_pair` | gate | 1050 | 1050 | 525 | 146 | 21.14 | 17.64 | 0.77 | [-31, 76] | 0.656 | 7806 | 0.54 | 277.8 |
| 24 | M6_DVOL_BTC_VS_ETH_DIVERGENCE | `M6_dvol_z_divergence_outright_btc` | gate | 525 | 525 | 525 | 73 | -16.60 | -23.60 | -0.24 | [-152, 120] | -0.190 | 39932 | 0.27 | 2842.5 |
| 25 | M6_DVOL_BTC_VS_ETH_DIVERGENCE | `M6_dvol_dvol_ratio_pair` | gate | 1124 | 1124 | 562 | 138 | 36.26 | 32.76 | 0.86 | [-44, 123] | 1.106 | 5846 | 1.08 | 104.0 |
| 26 | M6_DVOL_BTC_VS_ETH_DIVERGENCE | `M6_dvol_dvol_ratio_outright_btc` | gate | 562 | 562 | 562 | 69 | -50.96 | -57.96 | -0.51 | [-252, 133] | -0.431 | 8395 | 0.54 | 298.8 |
| 27 | M6_DVOL_BTC_VS_ETH_DIVERGENCE | `COLLAPSED_CORRECT` ★ | gate | 535 | 535 | 535 | 68 | 77.44 | 70.33 | 2.50 | [23, 142] | 1.215 | 342 | 0.54 | 12.2 |
| 28 | M6_DVOL_BTC_VS_ETH_DIVERGENCE | `F3_M6_pair_hold1d` | gate/horizon_variants | 1068 | 1068 | 534 | 136 | 38.67 | 35.17 | 0.92 | [-42, 129] | 1.215 | 5068 | 1.08 | 90.2 |
| 29 | M6_DVOL_BTC_VS_ETH_DIVERGENCE | `F3_M6_pair_hold3d` | gate/horizon_variants | 1282 | 1282 | 641 | 60 | 42.60 | 36.65 | 0.48 | [-125, 222] | 0.691 | 8030 | 0.61 | 250.1 |
| 30 | M6_DVOL_BTC_VS_ETH_DIVERGENCE | `F3_M6_pair_hold5d` | gate/horizon_variants | 1386 | 1386 | 693 | 54 | 43.88 | 38.57 | 0.43 | [-152, 240] | 0.661 | 9016 | 0.61 | 280.8 |
| 31 | M6_DVOL_BTC_VS_ETH_DIVERGENCE | `F3_M6_pair_hold10d` | gate/horizon_variants | 1540 | 1540 | 770 | 28 | 98.57 | 91.82 | 0.48 | [-282, 493] | 0.767 | 3778 | 0.31 | 235.3 |
| 32 | M7_OPTIONS_BLOCK_FLOW_TO_PERP | `F1_M7_hold1h` ★ | gate | 3845 | 3845 | 3845 | 3086 | -5.14 | -12.97 | -3.96 | [-8, -3] | -0.749 | 6168 | 11.38 | 10.4 |
| 33 | M7_OPTIONS_BLOCK_FLOW_TO_PERP | `F1_M7_hold4h` | gate | 9675 | 9675 | 9675 | 2315 | -0.88 | -4.31 | -0.95 | [-3, 1] | -0.238 | 79947 | 9.35 | 163.9 |
| 34 | M7_OPTIONS_BLOCK_FLOW_TO_PERP | `F1_M7_hold12h` | gate | 16593 | 16593 | 16593 | 1676 | -0.73 | -2.58 | -0.98 | [-2, 1] | -0.151 | 54350 | 7.38 | 141.1 |
| 35 | M7_OPTIONS_BLOCK_FLOW_TO_PERP | `F1_M7_hold24h` | gate | 20858 | 20858 | 20858 | 1241 | -0.80 | -2.13 | -1.18 | [-2, 1] | -0.142 | 27750 | 5.38 | 98.8 |
| 36 | M7_OPTIONS_BLOCK_FLOW_TO_PERP | `F1_M7_hold72h` | gate | 27022 | 27022 | 27022 | 779 | -0.42 | -1.21 | -0.58 | [-2, 1] | -0.073 | 72134 | 3.12 | 443.8 |

**36 gate rows total.** ★ = the headline row promoted into §2.1.

Superseded rows are kept in `RESULTS.json.superseded_rows` rather than deleted: the M1
full-sample sign-fitted variant (−11.3 bps, also negative), the three per-asset M5 beta-tilt
arms (superseded by their collapsed versions), and the two first-pass M7 rows that used
overlapping forward windows (superseded by the non-overlapping `F1_M7_hold*` family). None of
them changes a verdict.

---

## 8. Deliverables and reproduction

```
reports/edge_discovery/alpha_hunt_2026-09-03_round4/w7_options_vol_surface/
├── PREREGISTRATION.md          hypotheses + thresholds, written before any outcome test
├── REPORT.md                   this file
├── RESULTS.json                machine-readable, 9 mechanism entries / 36 gate rows
└── evidence/
    ├── gate.py                 the single §2 gate applied to every mechanism
    ├── prep.py                 shared loaders / paths
    ├── build_options_panel.py  Deribit trades → daily surface panel + strike notional + hourly block flow
    ├── build_returns.py        perp forward-return matrices (data_v2 normalized)
    ├── run_m1_m2_m6.py         M1, M2, M2c, M6 (pair + outright controls)
    ├── run_m3_gamma.py         M3, 3 constructions × 3 arms + the |return| diagnostic
    ├── run_m4_m5_m7.py         M4, M5, M7 first pass
    ├── run_followups.py        M7 non-overlapping horizons, M5 risk-on, M6 hold variants
    ├── run_collapsed.py        the declustering fix — multi-asset → one synthetic instrument
    ├── run_m6_robustness.py    M6 confound / partial IC / threshold sensitivity / substitution
    ├── results_*.json          raw outputs of each of the above
    └── consolidate.py          assembles RESULTS.json (idempotent; re-run reproduces it exactly)
```

Reproduce with `.venv/bin/python` (3.8) from the repo root; `consolidate.py` reads only the
`results_*.json` files in `evidence/` and rewrites `RESULTS.json` in place.

**Resource discipline.** Folder total 3.9 MB (4 parquet panels, 3.6 MB). No intermediate was
written outside this directory or the worker scratch; nothing was deleted anywhere.

---

## 9. Summary for the coordinator

| mechanism | verdict | net14 | net28 | N indep (L3) | ETA emp / ident | perp? |
|---|---|---|---|---|---|---|
| M1 IV term structure → direction | `DEAD` | −22.1 | −29.3 | 100 | 133 y / 130 y | yes |
| M2 skew velocity 1d | `DEAD` | −2.3 | −12.0 | 223 | 3165 y / 409 y | yes |
| M2 skew velocity 3d | `DEAD` | −27.3 | −35.5 | 151 | 47 y / 32 y | yes |
| M2c skew capitulation | `DEAD` | −9.9 | −16.9 | 70 | 1932 y / 969 y | yes |
| M3 dealer gamma proxy | `DEAD` | +12.1 (best of 9) | +1.2 | 155 | 1260 y / 785 y | yes |
| M4 pin risk / strike magnet | `DEAD` | −30.5 | −37.5 | 42 | 84 y / 57 y | yes |
| M5 VRP → alt cross-section | `DEAD` | +110.9 | +103.9 | 66 | 154 y / 190 y | yes |
| M6 DVOL BTC-vs-ETH pair | `WEAK` | +77.4 | +70.3 | 68 | 12 y / 21 y | yes |
| M7 block delta flow → perp | `WEAK` | −5.1 | −13.0 | 3086 | 10 y / 56 y | yes |

**No candidate promoted.** Nothing from W7 should enter `configs/validation_registry.yaml`.

Three things worth carrying to the next round, none of them a strategy:

1. **`ETA = 31.4 / SR²`, and the episode rate cancels.** Hunting for high-frequency mechanisms
   does not shorten confirmation time; raising Sharpe by cutting σ does. M7 is the proof by
   counter-example (3086 episodes, ETA still ≥ 10 y).
2. **L2-vs-L3 inconsistency is a cheap detector for the declustering trap.** When L3 exceeds
   L2, the "episodes" are counting instruments rather than signals. That check would have
   caught M5 immediately, and it is a one-line diagnostic.
3. **Delta-hedged options block flow moves the BTC perp within the hour** (+3.06 bps gross,
   t = +2.09, 2719 independent episodes, correctly signed). Not tradable at a 14 bps floor, but
   free to any strategy already paying the spread.
