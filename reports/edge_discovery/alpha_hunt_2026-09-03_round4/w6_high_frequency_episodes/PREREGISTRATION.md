# W6_HIGH_FREQUENCY_EPISODES — PREREGISTRATION

**Worker:** W6, Alpha Hunt Round 4 (`reports/edge_discovery/alpha_hunt_2026-09-03_round4/`)
**Written:** 2026-09-03, **BEFORE** any edge statistic was computed.
**Axis:** hunt the *denominator* of `ETA = n_required / event_rate` — independent-episode
rate — not the numerator (bps).

---

## 0. Why this axis exists (the problem being attacked)

`reports/edge_discovery/validation_2026-09/VALIDATION_AND_FORWARD_SCOREBOARD.md`:

| alpha | net_bps | N indep | ETA to forward-confirm |
|---|---|---|---|
| AMIHUD_ILLIQUIDITY_PREMIUM_V1 | +105.7 | 332 | **~17.0 y** |
| LIQ_REPEAT_DENSITY | +22.1 | 1165 | **~9.4 y** |
| LIQ_REPEAT_SKEW_OVERLAY | — | 579 | **~11.4 y** (46 y haircut) |
| LIQ_REPEAT_VOL_GATE | — | — | rejected at 28–38.5 y |

Every one of these is weekly-or-slower and/or heavily clustered. The project has been
optimising `net_bps` and paying no attention to `event_rate`. This worker inverts the
objective function: **primary sort key is `eta_forward_confirmation` ascending, not
`net_bps` descending.** A mechanism at +6 net bps with 300 independent episodes/day beats
a mechanism at +100 net bps with 1 episode/week, because only the first one can ever be
confirmed.

**Deliberate exclusion:** any mechanism whose natural cadence is weekly or slower is out
of scope, *however juicy it looks*. That ground is covered and is sterile for confirmation.

---

## 1. Data and PIT contract (fixed before any test)

**Source:** `/home/qbee/futur-data-v2/data_v2/normalized/event_feature_panel/venue=binance/symbol=*/year=*/event_feature_panel_5m.parquet`
— the canonical causal Data-V2 panel, 312 symbols, 5m dense grid, 2019→2026-07.

**Verified PIT properties** (checked before writing this document, not after):
* `research_available_at = timestamp + 305 s` exactly, on every row inspected.
* `residual_return_1h/_15m/_5m` are **trailing** beta-hedged returns vs BTC/ETH with
  causal, daily-frozen betas (`data_v2/events/residuals.py`, `shift(1)` + `_freeze_daily`).
  They are features, not labels. All forward labels are constructed by me via `LEAD`.
* `funding_rate` is causally forward-filled from its own settlement bar only.
* `oi_delta_pct_1h` is computed after dense-grid reindexing (gaps → NaN, not silent pairing).

**Decision/entry convention (frozen here):**
* Decision hour `H` = a 5m bar with `minute == 0`. All features use bars `<= H`.
* Feature is knowable at `H + 305 s`.
* **Entry at the close of bar `H + 10 min`** (2 bars later, 295 s of slack after
  availability). Forward returns therefore start at bar index `i+2`.
* `fwd_1h  = LEAD(r1h, 14)` = residual log return from bar `i+2` to `i+14`.
* `fwd_4h  = LEAD(r1h,14)+LEAD(r1h,26)+LEAD(r1h,38)+LEAD(r1h,50)`.
* `fwd_12h` = the same chain, 12 terms, to `LEAD(r1h,146)`.
* No forward window ever touches the signal window. No label is ever used as a feature.

**BTCUSDT and ETHUSDT are the hedge factors** — their "residual" is the raw return.
They are **excluded from every statistic** in this report.

**Everything is measured on residual (beta-hedged) returns.** This is the project's
answer to briefing rule #3 (this market has a strong unconditional drift): the
unconditional drift is hedged out by construction, and every mechanism is additionally
scored as an arm-vs-arm contrast (below), never as "arm A is positive".

---

## 2. Universe / liquidity tiers (fixed here, capacity-driven)

`dv_7d` = trailing 7-day taker USD volume (`aggressive_buy_usd + aggressive_sell_usd`,
2016-bar rolling sum, causal).

| tier | rule | intent |
|---|---|---|
| `T_DEEP` | `dv_7d >= 2e9` (≈ $285M/day) | large-capacity subset |
| `T_LIQ` (**primary**) | `dv_7d >= 2e8` (≈ $28M/day) | the tradable universe |
| `T_ALL` | `dv_7d >= 2e7` (≈ $2.9M/day) | breadth test / micro-cap warning tier |

Rows also require `sd30` (30d residual vol) non-null, `nflow_1h == 12` (full flow
coverage in the trailing hour), and a non-null forward label at the tested horizon.

`capacity_usd_estimate` per mechanism = median over episodes of
`0.10 * dv_1h * horizon_hours` (10 % of taker volume over the holding period), reported,
never used as a filter.

---

## 3. Cost model (frozen)

Project convention, briefing §1.4:
* `net_bps       = gross_bps - 14`
* `net_bps_stress28 = gross_bps - 28`

For **single-symbol directional** mechanisms `gross_bps = mean(side * fwd_resid)` where
`side ∈ {+1,-1}`; the 14 bps is one taker round trip. Because the traded object is a
*residual* (beta-hedged) return, a real implementation also pays the BTC/ETH hedge leg —
**the mandatory `-28` stress is exactly the budget for that hedge leg**, so nothing is
`PROMISING` here unless it survives 28.

For **cross-sectional long/short** mechanisms, the portfolio is normalised to **one unit
of gross notional** (0.5 long / 0.5 short), so
`gross_bps = (mean r_long - mean r_short) / 2` and the same `-14 / -28` applies to that
one unit. This keeps XS and directional mechanisms on the same axis and is *stricter*
than quoting the raw decile spread.

High-frequency-specific trap acknowledged: a mechanism firing 40×/day pays the cost 40×
as often. That is already inside these numbers because every episode is costed
individually; nothing is annualised.

---

## 4. Declustering (3 levels, mandatory, computed from the first pass)

| level | unit | how |
|---|---|---|
| `L1` | same symbol / 24 h | greedy forward scan per symbol, keep an episode only if ≥24 h since the last kept one for that symbol |
| `L2` | calendar day, all symbols | count of distinct UTC days with ≥1 episode; **inference done on the day-mean series** (equal-weight mean of that day's episodes) |
| `L3` | ISO calendar week, all symbols | count of distinct ISO weeks; inference on the week-mean series |

`t_stat_declustered` **headline = the L2 (day-clustered) t-stat**, because that is the
statistic that actually governs a portfolio traded daily across a correlated
cross-section. The L1-episode t-stat is reported alongside as the optimistic bound.
`bootstrap_ci95` = block bootstrap, 2000 resamples, blocks = calendar days (L2).

---

## 5. ETA arithmetic (frozen formula)

Two-sided α = 0.05, power = 80 % ⇒ `(1.96 + 0.8416)^2 = 7.849`.
**Mandatory 50 % haircut on the discovered edge.**

* Episode-level (optimistic):
  `n_req_L1 = 7.849 * (sd_episode / (0.5 * net_bps))^2`,
  `rate_L1` = L1 episodes/week measured over the **last 6 months of data** (conservative),
  `eta_episode_days = 7 * n_req_L1 / rate_L1`.
* Day-clustered (**headline**, and the one the results table is sorted by):
  `n_req_day = 7.849 * (sd_daymean / (0.5 * net_bps_daymean))^2` in **days**;
  `eta_forward_confirmation = n_req_day` days (one day accrues per calendar day),
  floored at the mechanism's own observed day-coverage rate.

Verdict `UNCONFIRMABLE_IN_HORIZON` is assigned on the **headline** ETA > 3 years.

**Break-even target computed BEFORE searching** (deliverable in its own right):
for a family with day-mean dispersion `sd_daymean`, the minimum net edge that makes the
mechanism confirmable within one calendar year is
`net_bps_min_1y = 2 * sd_daymean * sqrt(7.849 / 365) = 0.2933 * sd_daymean`.
Analogously `net_bps_min_2y = 0.2074 * sd_daymean`.

---

## 6. Hypotheses and thresholds — PREREGISTERED GRID

All thresholds are round numbers in **already-normalised units** (z-scores against the
panel's own causal `residual_std_30d`, ratios in [-1,1], percentages). None is fitted.
**The entire grid below is reported in `REPORT.md`, win or lose** — no cell is dropped.

Normalised features at hour `H`:
* `z1  = r1h / sd30` (trailing 1 h residual move, in units of its own 30 d residual vol)
* `z4  = r4h / (2 * sd30)`
* `fi_1h`, `fi_15m` = taker flow imbalance `(buy-sell)/(buy+sell)` over 1 h / 15 min
* `doi_1h`, `doi_4h` = open-interest % change over 1 h / 4 h
* `vs   = dv_1h / (dv_24h / 24)` (volume shock ratio)
* `bz1`, `bz7` = basis z-scores
* `fpct` = |funding| percentile over trailing 90 d

### Family A — short-horizon residual move (the highest-frequency family)
* **M1 `RESID_REVERSION_1H`** — trigger `|z1| >= θ`, side `= -sign(z1)`. θ ∈ {1.5, 2.5, 4.0}.
  *H1: idiosyncratic 1 h residual spikes over-shoot and partially revert.*
* **M2 `RESID_CONTINUATION_1H`** — same trigger, side `= +sign(z1)` (the mirror; M1 and M2
  cannot both win — reporting both is the arm-vs-arm contrast for this family).
* **M3 `RESID_REVERSION_4H`** — trigger `|z4| >= θ`, side `= -sign(z4)`. θ ∈ {1.5, 2.5}.

### Family B — taker flow imbalance
* **M4 `FLOW_IMBALANCE_FADE`** — `|fi_1h| >= θ`, side `= -sign(fi_1h)`. θ ∈ {0.30, 0.50}.
* **M5 `FLOW_IMBALANCE_FOLLOW`** — same trigger, side `= +sign(fi_1h)` (mirror arm).

### Family C — open interest
* **M6 `OI_BUILD_FADE`** — `doi_1h >= θ` and `|z1| >= 1.0`, side `= -sign(z1)`.
  *H2: OI built into a move = fresh crowded positioning → fades.* θ ∈ {0.01, 0.02}.
* **M7 `OI_FLUSH_BOUNCE`** — `doi_1h <= -θ` and `|z1| >= 1.0`, side `= -sign(z1)`.
  *H3: OI destroyed into a move = forced deleveraging → over-shoot → bounce.* θ ∈ {0.01, 0.02}.

### Family D — volume shock
* **M8 `VOLSHOCK_REVERSION`** — `vs >= θ` and `|z1| >= 1.5`, side `= -sign(z1)`. θ ∈ {3, 6}.
* **M9 `VOLSHOCK_CONTINUATION`** — same trigger, side `= +sign(z1)` (mirror arm).

### Family E — basis (intraday cadence, unlike funding)
* **M10 `BASIS_Z_REVERSION`** — `|bz1| >= θ`, side `= -sign(bz1)`. θ ∈ {2.0, 3.0}.

### Family F — flow/price disagreement
* **M11 `FLOW_PRICE_DIVERGENCE`** — `sign(fi_1h) != sign(z1)`, `|fi_1h| >= 0.30`,
  `|z1| >= 1.0`; side `= +sign(fi_1h)`. *H4: aggressive flow leads price.*

### Family G — hourly CROSS-SECTIONAL long/short (structurally the highest episode rate:
one portfolio episode per hour ⇒ ~168 independent episodes/week by construction)
At every hour `H`, rank all `T_LIQ`-eligible symbols with `xs_size >= 30`:
* **M12 `XS_RESID_REVERSAL_1H`** — long bottom decile of `z1`, short top decile.
* **M13 `XS_FLOW_REVERSAL_1H`** — long bottom decile of `fi_1h`, short top decile.
* **M14 `XS_OI_SHOCK`** — long bottom decile of `doi_1h`, short top decile.
* **M15 `XS_VOLSHOCK`** — long top decile of `vs`, short bottom decile.
* **M16 `XS_BASIS_REVERSAL`** — long bottom decile of `bz1`, short top decile.

### Family H — funding (INCLUDED ONLY AS THE NEGATIVE CONTROL for the ETA thesis)
* **M17 `FUNDING_CROWDING_FADE`** — `fpct >= 0.90`, side `= -sign(fr)`, evaluated at 12 h.
  Expected to show a *lower* episode rate than A–G; it is here to demonstrate that the
  inventory table discriminates, not because a new funding edge is expected
  (funding is documented as arbitraged out, briefing §4).

**Horizons tested for every mechanism: 1 h, 4 h, 12 h.** That is 3 horizons × the grid
above. Every cell is reported. Family-wise multiplicity is handled by (a) reporting the
whole grid, (b) the mandatory 50 % haircut in `n_required`, and (c) requiring
sign-consistency across horizons and across years before anything is scored above `WEAK`.

---

## 7. Decision rules fixed in advance

1. A mechanism is scored above `WEAK` **only if** `net_bps_stress28 > 0`.
2. It is `REGIME_DEPENDENT` if `ex_best_year` net bps flips sign or loses > 60 % of the
   full-sample effect.
3. It is `UNCONFIRMABLE_IN_HORIZON` if headline ETA > 3 years, regardless of bps.
4. It is `VALIDATED_FOR_FORWARD` only if: stress28 > 0, headline ETA < 3 y,
   day-clustered |t| ≥ 3.0, bootstrap CI95 excludes 0, no single year carries the effect,
   and the mirror arm (where one exists) is correspondingly negative.
5. The mirror-arm requirement is the arm-vs-arm discipline: for every M with a mirror
   (M1/M2, M4/M5, M8/M9), a claimed edge must appear as a *contrast between the two arms
   on the same population*, not merely as a positive number.
6. `PIT_UNVERIFIED` is stamped on anything I cannot trace to the panel's causal contract.

## 8. What would falsify the worker's thesis

If the inventory shows that after 3-level declustering the intraday families collapse to
the same weekly-ish independent-episode rate as the liquidation/cross-sectional families,
then "go faster" is not a way out of the ETA problem, and that is the finding — reported
as such, with no edge claim attached.
