# W7 — OPTIONS / VOL SURFACE — PREREGISTRATION
**Written 2026-09-03, BEFORE running any outcome test.** Round 4, alpha hunt.
Worker: W7_OPTIONS_VOL_SURFACE. Repo: /home/qbee/futur (read-only outside my folder).

---

## 0. The ETA frontier of this axis — computed FIRST, as instructed

The briefing (§2) makes `eta_forward_confirmation` the governing field, defined as
`n_required / event_rate` with `n_required` sized at power 80%, alpha 5%, on a **50%-haircut**
edge. For a strategy that takes `R` independent episodes per year with per-episode edge `mu`
and per-episode return dispersion `sigma`, this collapses to a closed form:

```
n_required = (z_.975 + z_.80)^2 * (sigma / (0.5*mu))^2 = 7.849 * 4 * (sigma/mu)^2
ETA_years  = n_required / R = 31.4 * (sigma/mu)^2 / R = 31.4 / SR_annual^2
```

because `SR_annual = (mu/sigma) * sqrt(R)`. **The episode rate cancels.** ETA depends on one
number only: the annualized Sharpe of the tradable expression of the mechanism.

| SR (discovery, net of 14bps) | ETA @50% haircut (the §2 gate) | ETA @full effect |
|---|---|---|
| 1.0 | 31.4 y | 7.8 y |
| 2.0 | 7.8 y | 2.0 y |
| **3.24** | **3.0 y** — the §3 `VALIDATED_FOR_FORWARD` boundary | 0.75 y |
| 5.6 | 1.0 y | 0.25 y |

**Consequences I commit to before seeing any result:**

1. The `VALIDATED_FOR_FORWARD` bar for this axis is **net annualized Sharpe ≥ 3.24**, and the
   `ETA < 1 year` stretch goal named in the briefing is **SR ≥ 5.6**. I will report SR as the
   primary statistic for every mechanism and derive ETA from it, rather than reporting bps and
   discovering the ETA at the end (the round-3 failure mode).
2. "More episodes" does **not** improve ETA on its own — this refutes the natural reading of
   "cherche activement des mécanismes à haute fréquence d'épisodes". Raising `R` helps only
   insofar as it raises SR, i.e. only if per-episode `mu/sigma` does not fall as fast as
   `1/sqrt(R)`. Since the 14bps cost is charged **per episode**, raising `R` usually *lowers*
   SR here. I preregister that I will therefore prefer **low-`sigma` expressions** (market-
   neutral pairs, cross-sectional tilts) over high-`R` expressions as the route to ETA.
3. My universe is BTC + ETH (2 assets, daily correlation ~0.85 → effective breadth ~1.1).
   Breadth cannot rescue SR. The only `sigma` reduction available to me inside the axis is the
   **BTC/ETH market-neutral pair** (M6) or exporting a BTC-derived regime onto a wide alt
   cross-section (M5). Those two are preregistered as the structurally most likely to clear the
   frontier, and everything outright-directional on BTC alone is preregistered as **expected to
   fail the ETA gate even if its bps is good**.

I expect a majority of `UNCONFIRMABLE_IN_HORIZON` verdicts on this axis and I am declaring that
in advance so it cannot be read as a post-hoc excuse.

## 0b. Execution-vehicle rule (briefing pitfall (c))

The project has **no options execution**. For every mechanism I state explicitly whether the
signal is expressible as a **perp position** (BTCUSDT / ETHUSDT / alt basket, Binance USDM,
taker). Anything that requires buying/selling an option, a variance swap, or a DVOL future is
marked `NO_VEHICLE` and cannot be better than `DATA_LIMITED`, regardless of its statistics.
This is what happened to all three of W6-round2's PROMISING findings (they became a risk
overlay, `VOL_FORECAST_LAYER_V1`, not a sleeve). I am not repeating that.

---

## 1. Data (declared before use, with coverage limits — pitfall (b))

| source | coverage | role |
|---|---|---|
| `data/options_backfill/deribit/DVOL_{BTC,ETH}_1d.parquet` | **2021-03-24 → 2026-09-03, 1990 d** | the only ETH options series that exists here; the long leg |
| `data/options_backfill/deribit/trades/BTC/*.parquet` | 2023-01 → 2026-09 (~1340 d) | per-trade IV/strike/expiry/direction/is_block; BTC only |
| `data/options_backfill/deribit/features/BTC_daily.parquet` | 1328 d | precomputed daily aggregates (used only as cross-check) |
| `/home/qbee/futur-data-v2/data_v2/normalized/perp_ohlcv/venue=binance/symbol=*` | 5m bars | forward outcomes, BTC/ETH + alt cross-section |

**Coverage asymmetry is load-bearing**: trade-level mechanisms (M1–M4, M7) get ~1340 days;
DVOL-only mechanisms (M5, M6) get ~1990 days. Any mechanism restricted to the trades window has
its ETA and its year-by-year table computed on that window, and I will not silently splice.

**No open interest anywhere in this dataset** (confirmed twice already, by A14 and by W6-round2).
Every gamma/pin construction below is therefore a **flow-accumulation proxy, not true OI**, and
I preregister that I will label it `PROXY_QUALITY: <assessment>` and will not present it as a
real GEX.

## 2. Causality / PIT rules I bind myself to

- Deribit and Binance are different venues/clocks. Joins are on the **UTC calendar day**, and a
  forward outcome for day `d` starts at bar timestamp `>= d+1 00:00 UTC`. Never `nearest`.
- All rolling features (z-scores, percentiles, EWMAs, gamma accumulations) are **causal**:
  computed from a trailing window ending at or before the decision timestamp. No full-sample
  standardization anywhere — this is the specific bug that inflated W6-round2's M10 terciles.
- Signal at day `d` uses only trades with `ts <= d 23:59:59 UTC`; position taken at `d+1 00:00`.

## 3. Declustering plan (3 levels, per §1.2) — fixed now

- **L1** same-asset / 24h window → for a daily panel this equals the number of asset-days.
- **L2** calendar day (all assets pooled) → **on this axis L2 is the binding constraint and is
  essentially `n_raw / 2`**, because my universe is 2 assets. This is the fatal trap named in my
  brief and I treat L2 as the default N for every t-stat.
- **L3** macro unit = **contiguous signal episode** (consecutive days in the same signal state),
  and for regime mechanisms additionally the **vol regime block**. Reported per mechanism.
- t-stats are computed on L3 episodes. Block bootstrap uses L3 blocks. No exceptions.

## 4. Mechanisms and thresholds — FIXED BEFORE TESTING

For each: hypothesis → tradable expression → pass threshold. Any threshold I move afterwards is
stamped `REFIT` in the report.

**M1 — IV term-structure slope & inversion → directional perp return.**
Slope = median traded ATM IV (moneyness |ln(K/S)| ≤ 0.05) of expiries 7–45d minus 45–180d,
daily. Hypothesis: inversion (slope > 0, near above far) marks stress; test whether it predicts
BTC perp *direction*, not RV. *Distinct from W6-M7/M8, which tested slope level and slope change
against forward RV and found both confound-killed by vol clustering; the directional target was
never run.* Vehicle: BTC perp, daily. Pass: |net SR| ≥ 1.0 with correct sign stability.

**M2 — Skew dynamics (velocity + normalization), not level.**
`skew = IV(25d put) − IV(25d call)` proxied by moneyness bands; features = `d_skew_1d`,
`d_skew_3d`, and a "normalization after shock" state (skew in top decile at `t−k`, then falling).
Hypothesis: skew normalizing after a put-panic = capitulation done → long BTC perp. *Distinct
from `LIQ_REPEAT_SKEW_OVERLAY` (validated), which uses the skew LEVEL as a conditioner on liq-
cascade repeat probability, and from W6-M6 (far-OTM put SHARE → forward RV).* Vehicle: BTC perp.
Pass: net SR ≥ 1.0, episode count ≥ 30.

**M3 — Dealer gamma proxy → momentum vs mean-reversion regime. (the core of my axis)**
Build a causal running inventory: each trade's `direction` gives the taker side; assume taker =
customer, so **dealer position = −customer position**. Weight each contract by Black-Scholes
gamma at the prevailing index price, decay/expire positions at their expiry. Aggregate to a
daily `dealer_gamma_proxy`. Hypothesis: dealer gamma < 0 → dealers hedge with the move →
**momentum** in the perp and higher RV; gamma > 0 → they lean against it → **reversion**.
Vehicle: sign-conditioned BTC perp momentum/reversion, daily. Pass: the *difference* between the
two arms (per §1.3, never "arm A is positive") ≥ 1.0 SR-equivalent and sign-stable by year.
`PROXY_QUALITY` to be stated honestly — no OI means the level is unanchored; only the
**variation** of the proxy is interpretable, so I test the z-scored variation, not the raw sign.

**M4 — Strike magnet / pin risk near large expiries.**
Proxy OI-at-strike by cumulative traded notional per (strike, expiry), causal. On the last
2 days before a monthly/quarterly expiry, test whether spot is pulled toward the max-notional
strike. Vehicle: BTC perp, direction = sign(magnet_strike − spot). Pass: net SR ≥ 1.0. I
preregister the expected killer: **event rate ~12–16/yr → sigma per episode is a 2-day BTC move
(~450bps) and mu would have to be ~200bps** to clear the frontier. Expected `UNCONFIRMABLE`.

**M5 — Variance risk premium as cross-asset risk appetite → alts.**
`VRP = DVOL_BTC(t) − RV_BTC(t−30d, realized)`, causal (backward-looking RV only). Hypothesis:
VRP is a risk-appetite gauge that transmits to alts. Vehicle that keeps sigma low: a
**cross-sectional high-beta minus low-beta alt tilt** conditioned on VRP regime (not an outright
beta bet — see §0 point 2). Pass: net SR ≥ 1.5 (higher bar: this one has the breadth to earn it).

**M6 — DVOL BTC vs DVOL ETH divergence → BTC/ETH rotation.**
`div = z(DVOL_ETH) − z(DVOL_BTC)` on causal trailing windows, 1990 days of coverage.
Hypothesis: relative vol repricing leads relative spot performance. Vehicle: **market-neutral
BTC/ETH perp pair**, dollar-neutral, daily rebalance. *Uses `DVOL_ETH`, which W6-round2 never
touched (it had no ETH trade data and did not use the ETH index).* Pass: net SR ≥ 1.5 — this is
my structurally best ETA candidate because the pair's sigma is roughly half an outright's.

**M7 — Options block flow → perp move via dealer delta hedge, intraday.**
**Delta-weighted** signed flow (BS delta per contract × direction × size), hourly, restricted to
`is_block=True`. Hypothesis: a large block forces the dealer to delta-hedge in the perp within
hours. *Distinct from W6-M3, which used raw notional block flow at DAILY horizon and was DEAD;
the delta weighting and the 1–4h horizon are both new.* Vehicle: BTC perp, 1h/4h. Pass: net SR
≥ 1.0 after charging 14bps per round trip at that turnover.

## 5. Kill rules (binding)

- Any mechanism whose net edge dies between 14 and 28bps → `COST_FRAGILE`, not PROMISING.
- Any mechanism whose edge is concentrated in one year (`ex_best_year` flips sign or loses >60%
  of the effect) → `REGIME_DEPENDENT`.
- Any mechanism with SR < 3.24 → ETA > 3y → `UNCONFIRMABLE_IN_HORIZON` **even if bps is large**,
  per §3. I will still report its bps and t-stat so the finding is reusable as a conditioner.
- Any mechanism needing OI, options execution, or sub-day cross-venue alignment → `DATA_LIMITED`
  / `NO_VEHICLE`, stated, never approximated silently.
