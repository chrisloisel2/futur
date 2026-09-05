# W6_HIGH_FREQUENCY_EPISODES — REPORT

**Worker:** W6, Alpha Hunt Round 4 (`reports/edge_discovery/alpha_hunt_2026-09-03_round4/`)
**Axis:** attack the **denominator** of `ETA = n_required / event_rate`, not the numerator (`net_bps`).
**Preregistration:** `PREREGISTRATION.md`, written 2026-09-03 before any edge statistic. Not amended.
**Status:** the 2026-09-03 session was cut after the inventory (phase 1). Phases 2-5 were run
2026-09-05 from the preregistered grid, unchanged. All scripts in `evidence/scripts/` are
re-executable end to end.

**Grid:** 24 preregistered mechanism specs x 3 horizons (1h/4h/12h) x 3 liquidity tiers = **261 cells**,
258 of them scored through the full §2 gate. Every cell is reported, win or lose (preregistration §6).

---

## 0. TL;DR

| | |
|---|---|
| `VALIDATED_FOR_FORWARD` | **0** |
| `PROMISING_NEEDS_VALIDATION` | **0** |
| `UNCONFIRMABLE_IN_HORIZON` | 8 — six are `M8_VOLSHOCK_REVERSION_6x` across tiers/horizons; the other two are T_DEEP cells with no statistical support at all (`M4_FLOW_IMBALANCE_FADE_0.5` h12, 39 independent episodes, ETA 522 y; `M2_RESID_CONTINUATION_1H_z4.0` h12, t_day 0.79, ETA 8 805 y) |
| `COST_FRAGILE` | 7 |
| `WEAK` | 11 |
| `DEAD` | 232 |
| `DATA_LIMITED` | 3 |
| **Best ETA in the whole grid** | **5.21 years** (`M8_VOLSHOCK_REVERSION_6x`, h=4, T_ALL) |

Three results, in order of importance to the project:

**(1) The ETA problem is a Sharpe problem, not an event-count problem.** With the preregistered
arithmetic (50 % haircut, 80 % power, 5 % two-sided), the ETA formula collapses to an exact identity:

> a mechanism is forward-confirmable within **N years** if and only if its **annualised NET Sharpe on
> the traded series is ≥ 5.60 / √N**.
> 1 y → 5.60 · 2 y → 3.96 · 3 y → 3.24 · 5 y → 2.51 · 10 y → 1.77 · 17 y → 1.36.

This is why `AMIHUD_ILLIQUIDITY_PREMIUM_V1` (+105.7 net bps) sits at ~17 years: +105.7 bps weekly is a
Sharpe of ~1.4, and no amount of bps fixes that. **Going faster only helps if it raises Sharpe.**
The project's true objective function is `Sharpe`, and `net_bps` is at best a proxy for its numerator.

**(2) The episode-rate denominator is NOT the binding constraint — and it is cheap to buy.**
The inventory (§1) shows intraday families delivering **50-580 L1-independent episodes/week** against
~1-10/week for the weekly alphas, and the hourly cross-sectional family needs only **+3.05 net bps per
rebalance** to confirm inside one year — a **35× lower bar** than the ~+105 bps the weekly alphas need.
The denominator problem is solvable. That part of the worker's thesis is confirmed.

**(3) But the numerator collapses faster than the denominator grows. The binding constraint is the
COST WALL.** Only **18 of 258** scored cells produce a *gross* edge above the 14 bps round trip, and 12
of those 18 are the same volume-shock mechanism (`M8_VOLSHOCK_REVERSION_3x/6x`). **98 of 258 cells are statistically overwhelming**
(|t| ≥ 3 on the day-declustered series, over 1700-2300 independent days) — and their **median gross
edge is 3.78 bps**, i.e. 3.7× *smaller* than the cost of trading it. Signal at hourly frequency is
abundant, real and utterly unpayable.

**Falsification check (preregistration §8):** the thesis was "if the intraday families decluster down to
the same weekly rate as the existing families, going faster is not a way out". They do **not** — the
rates hold up (§1) and the break-even bps genuinely falls (§2). The thesis fails for a *different*
reason than the one preregistered, and that reason is worth more than the original hypothesis: at
hourly cadence the per-episode edge shrinks roughly as √horizon while the per-episode cost stays flat at
14 bps, so `net_bps` goes negative long before the episode rate becomes useful. **Zero of 258 cells reach
an annualised GROSS Sharpe of 5.60 — even with free execution, nothing in this grid confirms in a year.**

---

## 1. THE INVENTORY OF INDEPENDENT-EPISODE RATES

*This is the primary deliverable and it was computed before any edge statistic was looked at. It has
value independently of whether any edge was found — which is just as well, because none was.*

Read the columns: `N_raw` is what a naive count would report; `N_indep_L1` is after same-symbol/24 h
declustering; `N_indep_L2_days` / `N_indep_L3_weeks` are the calendar-day and ISO-week units.
`L1 survival` = `N_indep_L1 / N_raw` — **this is the declustering trap made numerical.** A funding-percentile
screen looks like 920 k observations and survives at 6 %; a 6× volume shock looks like 8 k and survives
at 81 %. Rows `Y_EXISTING_EVENT_*` are the project's current event families, on the same axis, as a
benchmark. (`day cov 6m` slightly exceeding 1.00 is a ±1-day boundary rounding in the phase-1 script;
it is capped at 1.0 everywhere it enters an ETA.)

### T1 — INDEPENDENT-EPISODE-RATE INVENTORY (T_LIQ triggers; sorted by L1 rate)

| family | N_raw | N_indep_L1 | N_indep_L2_days | N_indep_L3_weeks | L1/week (full) | L1/week (last 6m) | L1 survival | day cov 6m | symbols |
|---|---|---|---|---|---|---|---|---|---|
| Z_UNIVERSE_ALL_SYMBOL_HOURS_T_ALL | 7059253 | 294987 | 2313 | 331 | 893.13 | 1292.43 | 0.04 | 1.01 | 307 |
| Z_UNIVERSE_ALL_SYMBOL_HOURS_T_LIQ | 3860562 | 163047 | 2313 | 331 | 493.65 | 436.92 | 0.04 | 1.01 | 301 |
| A_RESID_1H_z>=1.5 | 371635 | 92662 | 2297 | 330 | 281.16 | 254.88 | 0.25 | 1.01 | 301 |
| A_RESID_4H_z>=1.5 | 381944 | 74983 | 2287 | 330 | 227.71 | 210.51 | 0.20 | 1.01 | 301 |
| E_BASIS_Z>=2 | 196633 | 83427 | 2309 | 331 | 252.59 | 173.41 | 0.42 | 1.01 | 247 |
| G_XS_HOURLY_PORTFOLIO_vs | 48829 | 48829 | 2035 | 292 | 168.00 | 168.04 | 1.00 | 1.00 | 301 |
| G_XS_HOURLY_PORTFOLIO_fi_1h | 48829 | 48829 | 2035 | 292 | 168.00 | 168.04 | 1.00 | 1.00 | 301 |
| G_XS_HOURLY_PORTFOLIO_z1 | 48829 | 48829 | 2035 | 292 | 168.00 | 168.04 | 1.00 | 1.00 | 301 |
| G_XS_HOURLY_PORTFOLIO_doi_1h | 40855 | 40855 | 1704 | 245 | 167.84 | 167.96 | 1.00 | 1.00 | 301 |
| G_XS_HOURLY_PORTFOLIO_bz1 | 48729 | 48729 | 2035 | 292 | 167.66 | 164.87 | 1.00 | 1.00 | 247 |
| H_FUNDING_P90 (control) | 920357 | 53668 | 2109 | 322 | 164.19 | 148.83 | 0.06 | 1.01 | 297 |
| C_OI_FLUSH<=-1% | 117376 | 50241 | 1704 | 244 | 206.51 | 138.33 | 0.43 | 1.01 | 301 |
| C_OI_BUILD>=1% | 121103 | 50145 | 1704 | 244 | 206.12 | 131.91 | 0.41 | 1.01 | 300 |
| A_RESID_1H_z>=2.5 | 112185 | 45700 | 2271 | 330 | 138.79 | 121.68 | 0.41 | 1.01 | 300 |
| Y_EXISTING_EVENT_liq_cascade | 38141 | 21696 | 1726 | 282 | 75.67 | 109.25 | 0.57 | 1.01 | 49 |
| Y_EXISTING_EVENT_cascade | 39629 | 22550 | 1840 | 307 | 72.44 | 107.94 | 0.57 | 1.00 | 49 |
| B_FLOWIMB_1H>=0.30 | 30406 | 16356 | 2222 | 330 | 49.76 | 90.49 | 0.54 | 1.01 | 291 |
| A_RESID_4H_z>=2.5 | 116614 | 31871 | 2228 | 328 | 97.29 | 88.39 | 0.27 | 1.01 | 300 |
| D_VOLSHOCK>=3x | 49966 | 31324 | 2237 | 330 | 95.13 | 84.97 | 0.63 | 1.01 | 300 |
| Y_EXISTING_EVENT_premium | 27437 | 16217 | 1707 | 280 | 56.67 | 74.04 | 0.59 | 1.00 | 49 |
| C_OI_BUILD>=2% | 62530 | 30442 | 1704 | 244 | 125.13 | 70.66 | 0.49 | 1.01 | 297 |
| C_OI_FLUSH<=-2% | 51680 | 28031 | 1703 | 244 | 115.22 | 68.60 | 0.54 | 1.01 | 300 |
| A_RESID_1H_z>=4.0 | 32125 | 18299 | 2173 | 328 | 55.69 | 48.88 | 0.57 | 1.01 | 298 |
| E_BASIS_Z>=3 | 26484 | 20886 | 2210 | 331 | 63.26 | 32.47 | 0.79 | 0.99 | 245 |
| Y_EXISTING_EVENT_ignition | 8569 | 6844 | 1442 | 242 | 23.85 | 29.43 | 0.80 | 0.99 | 49 |
| Y_EXISTING_EVENT_spillover | 7853 | 5526 | 569 | 210 | 23.08 | 18.35 | 0.70 | 0.39 | 48 |
| D_VOLSHOCK>=6x | 8390 | 6826 | 1822 | 321 | 20.89 | 18.20 | 0.81 | 0.90 | 291 |
| Y_EXISTING_EVENT_crowding | 2274 | 2274 | 783 | 220 | 7.45 | 6.66 | 1.00 | 0.48 | 49 |
| B_FLOWIMB_1H>=0.50 | 3510 | 664 | 517 | 183 | 2.02 | 5.30 | 0.19 | 0.55 | 110 |
| F_FLOW_PRICE_DIVERGENCE | 518 | 326 | 299 | 144 | 1.00 | 1.78 | 0.63 | 0.24 | 82 |

**What the inventory says.**

* The intraday trigger families produce **32 to 255 L1-independent episodes/week** in the last 6 months,
  against **6.7 to 109/week** for the project's existing event families. Cadence is genuinely available.
* The hourly **cross-sectional portfolio** families are the structural outlier: **168 independent
  portfolio episodes/week by construction** (one per hour), `L1 survival = 1.00`, and coverage on
  **2035 distinct calendar days**. Nothing else in the corpus comes close on this axis.
* The declustering trap is real and family-specific. `H_FUNDING_P90` — included **as the preregistered
  negative control** — is the worst offender: 920 357 raw rows collapse to 53 668 L1 episodes (**6 %
  survival**), and §4 shows they collapse further to **4.5 effective independent bets/day** at h=12.
  The control behaved exactly as predicted: the inventory discriminates.
* `B_FLOWIMB_1H>=0.50` and `F_FLOW_PRICE_DIVERGENCE` are rate-starved (5.3 and 1.8/week) and were
  correctly flagged as such before any bps was computed. Both ended `DEAD`/`DATA_LIMITED`.

---

## 2. THE BREAK-EVEN: MINIMUM `net_bps` FOR SUB-1-YEAR CONFIRMABILITY

Preregistration §5, verbatim: for a family whose day-mean series has dispersion `sd_daymean`,

```
net_bps_min_1y = 2 · sd_daymean · sqrt(7.849 / 365) = 0.2933 · sd_daymean
net_bps_min_2y = 0.2074 · sd_daymean      net_bps_min_3y = 0.1694 · sd_daymean
```

`gross needed <1y` adds the 14 bps round trip; `shortfall` = best gross actually observed minus that.
**The shortfall column is negative in every row of the table** — that is the finding.
### T2 — MINIMUM NET BPS FOR <1-YEAR CONFIRMABILITY, per family (T_LIQ)

| family | h | L1/wk (6m) | N_L1 | N_days | sd_daymean | NET BPS MIN <1y | net min <2y | best gross obs | gross needed <1y | shortfall | capacity $ |
|---|---|---|---|---|---|---|---|---|---|---|---|
| G_cross_sectional_hourly | 1 | 168.89 | 48828.00 | 2035.00 | 10.42 | 3.05 | 2.16 | 1.15 | 17.05 | -15.90 | 4.41e+06 |
| H_funding_control | 1 | 148.83 | 53657.00 | 2109.00 | 20.13 | 5.90 | 4.17 | 0.50 | 19.90 | -19.40 | 308399.98 |
| G_cross_sectional_hourly | 4 | 42.19 | 12206.00 | 2035.00 | 20.56 | 6.03 | 4.26 | 2.31 | 20.03 | -17.72 | 1.76e+07 |
| G_cross_sectional_hourly | 12 | 14.04 | 4068.00 | 2035.00 | 35.35 | 10.37 | 7.33 | 4.49 | 24.37 | -19.88 | 5.29e+07 |
| C_oi | 1 | 101.29 | 40293.50 | 1704.00 | 55.72 | 16.34 | 11.55 | 2.13 | 30.34 | -28.21 | 934762.15 |
| E_basis | 1 | 102.94 | 52156.50 | 2259.50 | 55.72 | 16.34 | 11.55 | 8.48 | 30.34 | -21.86 | 460097.50 |
| A_resid_move | 1 | 121.68 | 45700.00 | 2271.00 | 57.50 | 16.86 | 11.92 | 7.85 | 30.86 | -23.02 | 1.28e+06 |
| B_flow | 1 | 47.90 | 8510.00 | 1369.50 | 68.36 | 20.05 | 14.18 | 3.76 | 34.05 | -30.29 | 333336.53 |
| H_funding_control | 4 | 148.79 | 53656.00 | 2109.00 | 76.62 | 22.47 | 15.89 | 1.57 | 36.47 | -34.90 | 1.23e+06 |
| E_basis | 4 | 102.86 | 52153.00 | 2259.50 | 96.53 | 28.31 | 20.02 | 6.57 | 42.31 | -35.74 | 1.84e+06 |
| A_resid_move | 4 | 121.61 | 45694.00 | 2271.00 | 96.84 | 28.40 | 20.08 | 12.53 | 42.40 | -29.87 | 5.13e+06 |
| C_oi | 4 | 101.25 | 40289.00 | 1704.00 | 106.68 | 31.29 | 22.12 | 2.14 | 45.29 | -43.14 | 3.74e+06 |
| F_divergence | 1 | 1.78 | 326.00 | 299.00 | 146.52 | 42.97 | 30.39 | -3.91 | 56.97 | -60.88 | 507857.55 |
| B_flow | 4 | 47.88 | 8509.00 | 1369.50 | 147.72 | 43.32 | 30.63 | 8.70 | 57.32 | -48.62 | 1.33e+06 |
| A_resid_move | 12 | 121.37 | 45667.00 | 2271.00 | 153.98 | 45.16 | 31.93 | 11.42 | 59.16 | -47.73 | 1.54e+07 |
| D_volshock | 1 | 51.59 | 19075.00 | 2029.50 | 154.88 | 45.42 | 32.12 | 18.86 | 59.42 | -40.56 | 2.68e+06 |
| E_basis | 12 | 102.78 | 52125.50 | 2259.00 | 159.03 | 46.64 | 32.98 | 4.88 | 60.64 | -55.76 | 5.52e+06 |
| C_oi | 12 | 101.05 | 40275.50 | 1704.00 | 183.50 | 53.82 | 38.06 | 6.93 | 67.82 | -60.89 | 1.12e+07 |
| H_funding_control | 12 | 148.59 | 53641.00 | 2109.00 | 219.51 | 64.38 | 45.52 | 2.84 | 78.38 | -75.54 | 3.7e+06 |
| F_divergence | 4 | 1.78 | 326.00 | 299.00 | 234.25 | 68.70 | 48.58 | 2.51 | 82.70 | -80.19 | 2.03e+06 |
| B_flow | 12 | 47.84 | 8504.50 | 1369.50 | 238.41 | 69.92 | 49.44 | 12.29 | 83.92 | -71.63 | 4e+06 |
| D_volshock | 4 | 51.57 | 19074.50 | 2029.50 | 248.88 | 72.99 | 51.61 | 37.89 | 86.99 | -49.11 | 1.07e+07 |
| D_volshock | 12 | 51.41 | 19060.00 | 2029.50 | 402.84 | 118.15 | 83.54 | 58.31 | 132.15 | -73.84 | 3.22e+07 |
| F_divergence | 12 | 1.78 | 326.00 | 299.00 | 408.09 | 119.69 | 84.63 | 7.63 | 133.69 | -126.06 | 6.09e+06 |
### T2b — same break-even, computed from the TRIGGER INVENTORY alone (no edge estimate involved)

| trigger family | sd_day h1 | net_min_1y h1 | sd_day h4 | net_min_1y h4 | net_min_2y h4 | sd_day h12 | net_min_1y h12 | capacity $ (h4) |
|---|---|---|---|---|---|---|---|---|
| Z_UNIVERSE_ALL_SYMBOL_HOURS_T_LIQ | 10.12 | 2.97 | 39.77 | 11.66 |  | 113.66 | 33.33 |  |
| Z_UNIVERSE_ALL_SYMBOL_HOURS_T_ALL | 10.17 | 2.98 | 39.97 | 11.72 |  | 113.90 | 33.41 |  |
| E_BASIS_Z>=2 | 25.75 | 7.55 | 53.60 | 15.72 | 11.12 | 121.97 | 35.77 | 1.52e+06 |
| A_RESID_1H_z>=1.5 | 25.88 | 7.59 | 66.40 | 19.48 | 13.77 | 161.39 | 47.33 | 2.99e+06 |
| A_RESID_4H_z>=1.5 | 23.69 | 6.95 | 72.03 | 21.13 | 14.94 | 177.35 | 52.01 | 2.91e+06 |
| H_FUNDING_P90 (control) | 21.38 | 6.27 | 81.34 | 23.85 | 16.87 | 231.05 | 67.76 | 1.23e+06 |
| B_FLOWIMB_1H>=0.30 | 41.66 | 12.22 | 81.82 | 24.00 | 16.97 | 161.87 | 47.47 | 714793.10 |
| C_OI_FLUSH<=-1% | 35.11 | 10.30 | 86.80 | 25.46 | 18.00 | 197.13 | 57.82 | 2.91e+06 |
| C_OI_BUILD>=1% | 43.94 | 12.89 | 110.75 | 32.48 | 22.97 | 237.98 | 69.80 | 3.55e+06 |
| A_RESID_1H_z>=2.5 | 57.87 | 16.97 | 128.20 | 37.60 | 26.59 | 256.55 | 75.24 | 5.16e+06 |
| C_OI_FLUSH<=-2% | 69.27 | 20.32 | 145.48 | 42.67 | 30.17 | 298.06 | 87.42 | 3.93e+06 |
| A_RESID_4H_z>=2.5 | 62.06 | 18.20 | 148.19 | 43.46 | 30.73 | 319.05 | 93.57 | 5.11e+06 |
| E_BASIS_Z>=3 | 83.03 | 24.35 | 150.73 | 44.21 | 31.26 | 257.83 | 75.62 | 2.16e+06 |
| C_OI_BUILD>=2% | 73.95 | 21.69 | 168.65 | 49.46 | 34.98 | 335.85 | 98.50 | 4.96e+06 |
| D_VOLSHOCK>=3x | 87.71 | 25.72 | 179.35 | 52.60 | 37.19 | 329.29 | 96.58 | 7.98e+06 |
| B_FLOWIMB_1H>=0.50 | 96.01 | 28.16 | 215.66 | 63.25 | 44.73 | 345.80 | 101.42 | 1.95e+06 |
| F_FLOW_PRICE_DIVERGENCE | 146.43 | 42.94 | 233.05 | 68.35 | 48.33 | 408.15 | 119.70 | 2.03e+06 |
| A_RESID_1H_z>=4.0 | 148.17 | 43.46 | 275.08 | 80.68 | 57.05 | 459.95 | 134.90 | 9.08e+06 |
| D_VOLSHOCK>=6x | 220.50 | 64.67 | 396.83 | 116.38 | 82.30 | 623.74 | 182.93 | 1.35e+07 |

**Reading of §2.** The ranking is the point: **the hourly cross-sectional family needs +3.05 net bps
per rebalance to be confirmable in a year.** The single-symbol intraday families need 16-45 bps. The
project's weekly alphas need ~100+. Buying breadth (many small simultaneous bets) is *by far* the
cheapest way to lower the confirmation bar — an order of magnitude cheaper than making the signal
better. `T2b` shows the same numbers derived from trigger dispersion alone, with no edge estimate
anywhere in the calculation, so this ranking cannot be an artefact of any fit.

And then: the best *gross* edge ever observed in the cross-sectional family is **+1.15 bps** against a
**+17.05 bps** requirement. The bar came down 35×; the signal came down further.

---

## 3. THE COST WALL

Cost convention (preregistration §3, briefing §1.4 and addendum §8.9):
* **Directional single-symbol:** flat 14 bps round trip per episode, 28 under stress. No netting credit
  taken, even for symbols that re-trigger in consecutive hours.
* **Cross-sectional hourly:** charged on **real turnover**, not per signal — 7 bps one-way per unit of
  gross notional actually traded, `cost = 7 · mean(Σᵢ|wᵢ(t) − wᵢ(t−h)|)` on the implementable
  non-overlapping *h*-spaced rebalance schedule. A portfolio that fully rotates each period gives
  `Σ|Δw| = 2` → exactly 14 bps, so this convention can only ever be **cheaper** than the flat one,
  never more expensive. Measured turnover came out at **1.14-1.82 → 8.0-12.7 bps** per rebalance; the
  flat-14 figures are carried in `RESULTS.json` as `net_bps_flat14` so the credit can be undone.
* Because the traded object is a beta-hedged *residual*, a real implementation also pays a BTC/ETH hedge
  leg. The mandatory `−28` stress is exactly the budget for that leg; nothing here is called an edge
  unless it survives 28.
### T5 — THE COST WALL (T_LIQ): best GROSS edge per family vs the round trip

| family | h | best gross bps | best cell | max |t_day| | cost bps | gross/cost |
|---|---|---|---|---|---|---|
| A_resid_move | 1 | 7.85 | M1_RESID_REVERSION_1H_z4.0 | 7.36 | 14.00 | 0.56 |
| A_resid_move | 4 | 12.53 | M1_RESID_REVERSION_1H_z4.0 | 5.83 | 14.00 | 0.90 |
| A_resid_move | 12 | 11.42 | M1_RESID_REVERSION_1H_z4.0 | 3.42 | 14.00 | 0.82 |
| B_flow | 1 | 3.76 | M5_FLOW_IMBALANCE_FOLLOW_0.5 | 0.79 | 14.00 | 0.27 |
| B_flow | 4 | 8.70 | M5_FLOW_IMBALANCE_FOLLOW_0.5 | 0.67 | 14.00 | 0.62 |
| B_flow | 12 | 12.29 | M5_FLOW_IMBALANCE_FOLLOW_0.5 | 1.30 | 14.00 | 0.88 |
| C_oi | 1 | 2.13 | M6_OI_BUILD_FADE_0.02 | 2.96 | 14.00 | 0.15 |
| C_oi | 4 | 2.14 | M6_OI_BUILD_FADE_0.02 | 2.25 | 14.00 | 0.15 |
| C_oi | 12 | 6.93 | M6_OI_BUILD_FADE_0.02 | 3.15 | 14.00 | 0.49 |
| D_volshock | 1 | 18.86 | M8_VOLSHOCK_REVERSION_6.0x | 5.28 | 14.00 | 1.35 |
| D_volshock | 4 | 37.89 | M8_VOLSHOCK_REVERSION_6.0x | 6.15 | 14.00 | 2.71 |
| D_volshock | 12 | 58.31 | M8_VOLSHOCK_REVERSION_6.0x | 4.35 | 14.00 | 4.16 |
| E_basis | 1 | 8.48 | M10_BASIS_Z_REVERSION_3.0 | 4.14 | 14.00 | 0.61 |
| E_basis | 4 | 6.57 | M10_BASIS_Z_REVERSION_3.0 | 4.27 | 14.00 | 0.47 |
| E_basis | 12 | 4.88 | M10_BASIS_Z_REVERSION_2.0 | 2.81 | 14.00 | 0.35 |
| F_divergence | 1 | -3.91 | M11_FLOW_PRICE_DIVERGENCE | 0.60 | 14.00 | -0.28 |
| F_divergence | 4 | 2.51 | M11_FLOW_PRICE_DIVERGENCE | 0.29 | 14.00 | 0.18 |
| F_divergence | 12 | 7.63 | M11_FLOW_PRICE_DIVERGENCE | 1.00 | 14.00 | 0.54 |
| G_cross_sectional_hourly | 1 | 1.15 | M14_XS_OI_SHOCK | 4.40 | 11.55 | 0.10 |
| G_cross_sectional_hourly | 4 | 2.31 | M14_XS_OI_SHOCK | 3.64 | 11.94 | 0.19 |
| G_cross_sectional_hourly | 12 | 4.49 | M14_XS_OI_SHOCK | 3.63 | 12.10 | 0.37 |
| H_funding_control | 1 | 0.50 | M17_FUNDING_CROWDING_FADE | 1.76 | 14.00 | 0.04 |
| H_funding_control | 4 | 1.57 | M17_FUNDING_CROWDING_FADE | 1.16 | 14.00 | 0.11 |
| H_funding_control | 12 | 2.84 | M17_FUNDING_CROWDING_FADE | 0.84 | 14.00 | 0.20 |
### T3a — TOP 30 CELLS BY ETA ASCENDING (primary sort key)

| mechanism | h | tier | N_raw | N_L1 | N_days | N_weeks | gross | net14 | net28 | t_day | CI95 net | ex-best-yr | L1/wk | ETA (y) | ETA0 (y) | Sharpe | capacity $ | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| M8_VOLSHOCK_REVERSION_6.0x | 4 | T_ALL | 19928 | 15195.00 | 2083.00 | 328.00 | 40.81 | 26.81 | 12.81 | 8.92 | [19,38] | 12.66 | 74.16 | 5.21 | 2.34 | 2.45 | 4.71e+06 | UNCONFIRMABLE_IN_HORIZON |
| M8_VOLSHOCK_REVERSION_6.0x | 12 | T_ALL | 19921 | 15188.00 | 2083.00 | 328.00 | 63.84 | 49.84 | 35.84 | 6.99 | [29,62] | 31.27 | 74.04 | 5.83 | 3.40 | 2.32 | 1.41e+07 | UNCONFIRMABLE_IN_HORIZON |
| M8_VOLSHOCK_REVERSION_6.0x | 1 | T_ALL | 19928 | 15195.00 | 2083.00 | 328.00 | 15.12 | 1.12 | -12.88 | 8.03 | [4,16] | -0.47 | 74.16 | 15.16 | 2.57 | 1.44 | 1.18e+06 | COST_FRAGILE |
| M8_VOLSHOCK_REVERSION_6.0x | 12 | T_LIQ | 8386 | 6822.00 | 1822.00 | 321.00 | 58.31 | 44.31 | 30.31 | 4.35 | [16,69] | 20.68 | 18.16 | 15.39 | 8.91 | 1.43 | 4.05e+07 | UNCONFIRMABLE_IN_HORIZON |
| M8_VOLSHOCK_REVERSION_6.0x | 4 | T_LIQ | 8390 | 6826.00 | 1822.00 | 321.00 | 37.89 | 23.89 | 9.89 | 4.85 | [11,45] | 10.15 | 18.20 | 15.76 | 6.97 | 1.41 | 1.35e+07 | UNCONFIRMABLE_IN_HORIZON |
| M1_RESID_REVERSION_1H_z4.0 | 12 | T_ALL | 52973 | 30029.00 | 2246.00 | 329.00 | 20.68 | 6.68 | -7.32 | 5.52 | [6,28] | -1.69 | 128.57 | 19.84 | 6.05 | 1.26 | 1.27e+07 | COST_FRAGILE |
| M8_VOLSHOCK_REVERSION_6.0x | 1 | T_LIQ | 8390 | 6826.00 | 1822.00 | 321.00 | 18.86 | 4.86 | -9.14 | 4.95 | [4,26] | -1.21 | 18.20 | 20.60 | 5.51 | 1.23 | 3.37e+06 | COST_FRAGILE |
| M1_RESID_REVERSION_1H_z4.0 | 4 | T_ALL | 53037 | 30067.00 | 2246.00 | 329.00 | 11.95 | -2.05 | -16.05 | 6.35 | [3,17] | -6.48 | 128.96 | 26.62 | 4.43 | 1.09 | 4.23e+06 | WEAK |
| M8_VOLSHOCK_REVERSION_3.0x | 12 | T_ALL | 95827 | 58297.00 | 2289.00 | 330.00 | 24.61 | 10.61 | -3.39 | 6.22 | [2,16] | 10.12 | 264.17 | 31.89 | 4.64 | 0.99 | 8.98e+06 | COST_FRAGILE |
| M8_VOLSHOCK_REVERSION_3.0x | 4 | T_ALL | 95884 | 58337.00 | 2289.00 | 330.00 | 13.58 | -0.42 | -14.42 | 9.06 | [1,9] | -2.35 | 264.91 | 33.99 | 2.19 | 0.96 | 3e+06 | WEAK |
| M8_VOLSHOCK_REVERSION_3.0x | 4 | T_LIQ | 49964 | 31323.00 | 2237.00 | 330.00 | 14.84 | 0.84 | -13.16 | 6.15 | [-1,12] | -2.62 | 84.93 | 63.90 | 4.63 | 0.70 | 7.98e+06 | COST_FRAGILE |
| M6_OI_BUILD_FADE_0.02 | 12 | T_ALL | 108067 | 54293.00 | 1704.00 | 244.00 | 11.04 | -2.96 | -16.96 | 5.19 | [-2,12] | -10.09 | 200.16 | 66.21 | 4.87 | 0.69 | 6.44e+06 | WEAK |
| M8_VOLSHOCK_REVERSION_3.0x | 12 | T_LIQ | 49929 | 31298.00 | 2237.00 | 330.00 | 24.93 | 10.93 | -3.07 | 4.23 | [-2,18] | 8.84 | 84.66 | 80.66 | 10.37 | 0.62 | 2.39e+07 | COST_FRAGILE |
| M6_OI_BUILD_FADE_0.02 | 12 | T_LIQ | 62500 | 30429.00 | 1704.00 | 244.00 | 6.93 | -7.07 | -21.07 | 3.15 | [-7,16] | -13.34 | 70.51 | 144.46 | 13.55 | 0.47 | 1.49e+07 | DEAD |
| M3_RESID_REVERSION_4H_z2.5 | 1 | T_DEEP | 23224 | 6345.00 | 1846.00 | 306.00 | 2.16 | -11.84 | -25.84 | 5.28 | [-3,10] | -12.13 | 13.88 | 157.45 | 5.99 | 0.45 | 5.99e+06 | DEAD |
| M8_VOLSHOCK_REVERSION_6.0x | 4 | T_DEEP | 919 | 811.00 | 548.00 | 248.00 | 39.48 | 25.48 | 11.48 | 1.71 | [-2,82] | 6.87 | 1.58 | 252.32 | 112.53 | 0.35 | 6.79e+07 | UNCONFIRMABLE_IN_HORIZON |
| M1_RESID_REVERSION_1H_z4.0 | 12 | T_LIQ | 32072 | 18271.00 | 2173.00 | 328.00 | 11.42 | -2.58 | -16.58 | 2.58 | [-11,19] | -10.46 | 48.69 | 303.44 | 26.34 | 0.32 | 2.73e+07 | WEAK |
| M1_RESID_REVERSION_1H_z4.0 | 4 | T_LIQ | 32121 | 18297.00 | 2173.00 | 328.00 | 12.53 | -1.47 | -15.47 | 3.58 | [-6,13] | -8.99 | 48.84 | 336.18 | 14.04 | 0.31 | 9.08e+06 | WEAK |
| M3_RESID_REVERSION_4H_z2.5 | 4 | T_DEEP | 23222 | 6344.00 | 1846.00 | 306.00 | -3.92 | -17.92 | -31.92 | 2.49 | [-10,19] | -18.62 | 13.88 | 406.48 | 27.33 | 0.28 | 2.4e+07 | DEAD |
| M11_FLOW_PRICE_DIVERGENCE | 12 | T_LIQ | 518 | 326.00 | 299.00 | 144.00 | 7.63 | -6.37 | -20.37 | 1.00 | [-37,63] | -14.26 | 1.78 | 465.05 | 92.38 | 0.26 | 6.09e+06 | DEAD |
| M4_FLOW_IMBALANCE_FADE_0.5 | 12 | T_DEEP | 437 | 39.00 | 40.00 | 15.00 | 34.09 | 20.09 | 6.09 | 0.58 | [-68,126] | 17.25 | 0.38 | 522.00 | 168.21 | 0.25 | 1.39e+07 | UNCONFIRMABLE_IN_HORIZON |
| M8_VOLSHOCK_REVERSION_6.0x | 12 | T_DEEP | 918 | 810.00 | 548.00 | 248.00 | 33.38 | 19.38 | 5.38 | 1.06 | [-26,89] | -23.08 | 1.58 | 740.15 | 257.22 | 0.21 | 2.04e+08 | UNCONFIRMABLE_IN_HORIZON |
| M8_VOLSHOCK_REVERSION_3.0x | 4 | T_DEEP | 8074 | 5452.00 | 1742.00 | 309.00 | 15.32 | 1.32 | -12.68 | 2.59 | [-10,16] | 1.07 | 11.64 | 969.67 | 31.45 | 0.18 | 3.94e+07 | COST_FRAGILE |
| M1_RESID_REVERSION_1H_z4.0 | 4 | T_DEEP | 6471 | 3701.00 | 1493.00 | 300.00 | 3.80 | -10.20 | -24.20 | 1.63 | [-19,25] | -11.64 | 7.86 | 1340.02 | 68.49 | 0.15 | 3.83e+07 | DEAD |
| M5_FLOW_IMBALANCE_FOLLOW_0.5 | 1 | T_DEEP | 437 | 39.00 | 40.00 | 15.00 | 4.33 | -9.67 | -23.67 | 1.45 | [-19,21] | -10.10 | 0.38 | 1859.35 | 28.64 | 0.13 | 1.16e+06 | DEAD |
| M5_FLOW_IMBALANCE_FOLLOW_0.5 | 4 | T_DEEP | 437 | 39.00 | 40.00 | 15.00 | 3.81 | -10.19 | -24.19 | 0.68 | [-47,51] | -10.44 | 0.38 | 2222.08 | 106.23 | 0.12 | 4.62e+06 | DEAD |
| M3_RESID_REVERSION_4H_z2.5 | 12 | T_ALL | 190415 | 53338.00 | 2275.00 | 329.00 | 9.14 | -4.86 | -18.86 | 3.24 | [-8,10] | -12.79 | 231.54 | 3450.22 | 17.37 | 0.10 | 6.76e+06 | WEAK |
| M8_VOLSHOCK_REVERSION_6.0x | 1 | T_DEEP | 919 | 811.00 | 548.00 | 248.00 | 5.63 | -8.37 | -22.37 | 1.01 | [-22,40] | -16.17 | 1.58 | 5278.92 | 247.66 | 0.08 | 1.7e+07 | DEAD |
| M2_RESID_CONTINUATION_1H_z4.0 | 12 | T_DEEP | 6461 | 3694.00 | 1492.00 | 300.00 | 49.06 | 35.06 | 21.06 | 0.79 | [-35,46] | -0.35 | 7.90 | 8805.48 | 237.33 | 0.06 | 1.15e+08 | UNCONFIRMABLE_IN_HORIZON |
| M6_OI_BUILD_FADE_0.02 | 12 | T_DEEP | 11166 | 5562.00 | 1589.00 | 244.00 | -49.68 | -63.68 | -77.68 | 1.04 | [-29,31] | -66.25 | 11.46 | 16141.53 | 142.54 | 0.04 | 6.57e+07 | DEAD |

**The wall, stated numerically.**

* **18 of 258** scored cells have `gross_bps > 14`. **8 of 258** have `gross_bps > 28`.
  **12 of those 18 are `M8_VOLSHOCK_REVERSION_3x/6x`** (§5); the remaining 6 are all T_DEEP or
  rate-starved cells with |t_day| ≤ 1.6.
* **98 of 258** cells have `|t_stat_declustered| ≥ 3` — statistically overwhelming, on 1700-2300
  independent calendar days. Their **median |gross| is 3.78 bps.** Ten of those 98 clear 14 bps.
* Family by family, `gross/cost` never exceeds 1 except for the volume-shock family; the hourly
  cross-sectional family — the one with the best confirmability structure — sits at **0.08-0.32**.

The mechanism is not mysterious. Per-episode edge scales roughly with √horizon (it is a fraction of the
move you are betting against), while per-episode cost is flat. At h=1 the residual-reversion signal is a
genuine, 6-8σ, +2 bps effect. It costs 14 bps to collect. Compressing the holding period multiplies the
number of times you pay 14 bps; it does not multiply the alpha.

---

## 4. DIAGNOSTIC D1 — "MANY OBSERVATIONS ≠ MANY INDEPENDENT EPISODES", QUANTIFIED

The briefing's declustering trap has been rediscovered four times in this project. Here it is as a single
number. For every scored cell define the **effective** number of independent episodes contributed per
calendar day:

```
k_eff = (sd_episode_L1 / sd_daymean)²          redundancy = raw_episodes_per_day / k_eff
```

If a day's episodes were independent, `sd_daymean = sd_episode/√k` and `redundancy = 1`. Anything above 1
is the factor by which naive counting overstates the information in the sample — and `n_required` scales
with `sd_daymean²`, so redundancy translates one-for-one into ETA.
### T4 — BREADTH EFFICIENCY: raw episodes/day vs EFFECTIVE independent episodes/day

| family | h | raw eps/day | k_eff/day | redundancy | sd_episode | sd_daymean | net_min_1y |
|---|---|---|---|---|---|---|---|
| A_resid_move | 1 | 42.15 | 20.04 | 2.15 | 282.74 | 57.50 | 16.86 |
| A_resid_move | 4 | 42.15 | 13.55 | 2.71 | 435.86 | 106.96 | 31.37 |
| A_resid_move | 12 | 42.12 | 11.10 | 3.18 | 716.73 | 204.99 | 60.12 |
| B_flow | 1 | 9.09 | 3.76 | 2.75 | 88.45 | 58.54 | 17.17 |
| B_flow | 4 | 9.09 | 2.86 | 3.20 | 171.37 | 123.37 | 36.18 |
| B_flow | 12 | 9.09 | 1.91 | 4.79 | 292.44 | 242.45 | 71.11 |
| C_oi | 1 | 42.33 | 17.36 | 2.72 | 215.56 | 56.76 | 16.65 |
| C_oi | 4 | 42.33 | 13.55 | 3.11 | 382.23 | 106.68 | 31.29 |
| C_oi | 12 | 42.30 | 11.61 | 3.35 | 624.26 | 183.50 | 53.82 |
| D_volshock | 1 | 7.10 | 5.66 | 1.58 | 347.95 | 151.60 | 44.46 |
| D_volshock | 4 | 7.10 | 4.80 | 1.74 | 523.34 | 253.27 | 74.28 |
| D_volshock | 12 | 7.10 | 3.36 | 2.57 | 754.57 | 469.02 | 137.56 |
| E_basis | 1 | 19.94 | 11.59 | 1.94 | 194.00 | 57.11 | 16.75 |
| E_basis | 4 | 19.93 | 8.88 | 2.30 | 296.13 | 99.47 | 29.17 |
| E_basis | 12 | 19.92 | 7.78 | 2.62 | 467.54 | 167.69 | 49.18 |
| F_divergence | 1 | 1.85 | 1.34 | 1.38 | 133.29 | 116.40 | 34.14 |
| F_divergence | 4 | 1.85 | 1.55 | 1.19 | 245.65 | 197.11 | 57.81 |
| F_divergence | 12 | 1.85 | 1.21 | 1.53 | 381.17 | 347.83 | 102.01 |
| G_cross_sectional_hourly | 1 | 23.98 | 21.41 | 1.12 | 48.20 | 10.42 | 3.05 |
| G_cross_sectional_hourly | 4 | 23.97 | 14.00 | 1.56 | 91.05 | 20.56 | 6.03 |
| G_cross_sectional_hourly | 12 | 23.97 | 11.66 | 1.99 | 141.89 | 44.81 | 13.14 |
| H_funding_control | 1 | 436.27 | 52.09 | 6.33 | 167.12 | 20.13 | 5.90 |
| H_funding_control | 4 | 436.23 | 12.05 | 28.77 | 298.35 | 76.62 | 22.47 |
| H_funding_control | 12 | 436.13 | 4.54 | 82.99 | 503.21 | 219.51 | 64.38 |

**Reading of D1.**
* **`G_cross_sectional_hourly` at h=1 has redundancy 1.12** — 24 hourly rebalances/day deliver **21.4
  effective independent bets/day**. This is the only construct in the corpus that is close to
  information-efficient, and it is precisely why its break-even is 3.05 bps.
* **The preregistered negative control fails spectacularly and correctly:** `H_funding_control` shows 436
  raw episodes/day collapsing to **52 effective at h=1 (redundancy 6.3)** and **4.5 effective at h=12
  (redundancy 83)**. A naive N of 920 357 is worth about 4.5 bets a day. This is the trap, in one row.
* Redundancy rises monotonically with horizon in **every** family (overlapping forward windows plus
  cross-sectional correlation). Longer holds do not just cost more calendar time per episode; they
  destroy independence within the day as well.
* Practical rule this yields: **to be confirmable in one year a mechanism needs
  `net_bps ≥ 0.293 · sd_episode / √k_eff`** — and `k_eff`, not the raw episode count, is the quantity to
  engineer.

---

## 5. DIAGNOSTIC D2 — THE ONE SURVIVOR IS A KNOWN EDGE IN DISGUISE

`M8_VOLSHOCK_REVERSION_6x` (trigger: hourly taker volume ≥ 6× its 24 h average **and** |1 h residual
move| ≥ 1.5σ; side: fade the move) is the only mechanism in the grid worth a second look:

| tier | h | N_raw | N_L1 | net14 | net28 | t_day | t_week | CI95 net | ex-best-year | L1/week | ETA | Sharpe | capacity |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| T_ALL | 4 | 19 928 | 15 195 | +26.8 | **+12.8** | +8.92 | +6.42 | [+19.0, +37.8] | +12.7 | 74.2 | **5.21 y** | 2.45 | $4.7 M |
| T_ALL | 12 | 19 921 | 15 188 | +49.8 | **+35.8** | +6.99 | +6.13 | [+28.5, +62.4] | +31.3 | 74.0 | **5.83 y** | 2.32 | $14.1 M |
| T_LIQ | 12 | 8 386 | 6 822 | +44.3 | +30.3 | +4.35 | +4.14 | [+16, +69] | +20.7 | 18.2 | 15.39 y | 1.43 | $40.5 M |

It survives the 28 bps stress, its bootstrap CI excludes zero, its week-level t agrees with its day-level
t, and it is **positive in every single year 2020-2026** at T_ALL/h12 (+25.7, +25.6, +32.7, +12.2, +27.8,
+106.8, +60.4). It is not regime-dependent by the preregistered rule. Its independent-episode rate is
excellent (74/week). And its headline **ETA is still 5.2-5.8 years** → `UNCONFIRMABLE_IN_HORIZON`,
because 74 episodes/week at a per-episode dispersion of ~1400 bps is an annualised Sharpe of 2.3-2.5,
against the 3.24 needed for a 3-year confirmation. *This single row is the entire thesis of the worker:
a large, stress-proof, every-year-positive edge with 74 independent episodes a week is still not
confirmable, because ETA is set by Sharpe.*

**And it is almost certainly not new.** Briefing §4 records that liquidation cascades pay on repetition
(already in shadow as `LIQ_CASCADE_REPEAT_V1`). A 6× volume shock with a ≥1.5σ move is a plausible proxy
for the same phenomenon, so I tested it directly against `data/events/{liq_cascade,cascade}_dataset.parquet`
(49 symbols, so this check reaches only the 14.3 % of M8 episodes on covered symbols, n = 2 858):

| M8 subset, covered symbols, h=12 | N_raw | N_L1 | net14 | net28 | t_day | ETA |
|---|---|---|---|---|---|---|
| within ±12 h of a cascade event (62.2 % of them) | 1 778 | 1 517 | **+58.8** | **+44.8** | **+2.65** | 30.5 y |
| disjoint from any cascade event | 1 080 | 958 | **−17.0** | −31.0 | −0.44 | ∞ |

**All of M8's edge sits inside the known cascade family; the cascade-disjoint remainder is negative.**
On the tested subsample M8 is a noisier re-expression of an edge the project already owns, not an
addition to it. I am reporting this as a KILL rather than as a candidate. (Caveat stated plainly: the
event corpus covers 49 of 304 symbols, so this is decisive only on that 14.3 %. Extending the cascade
detector to the full 304-symbol universe would settle it — see §8.)

---

## 6. WHAT I KILLED, AND WHY

| # | mechanism / family | verdict | why |
|---|---|---|---|
| M1/M2 | 1 h residual reversion / continuation, z ∈ {1.5, 2.5, 4.0} | **DEAD** (best cell WEAK) | Reversion is real and 6-8σ, but gross is +1.4 to +12.5 bps against 14. Mirror arms are exact negatives by construction, so the contrast *is* the gross: the market pays ~2 bps to fade an hourly residual spike and charges 14 to collect it. |
| M3 | 4 h residual reversion | **DEAD** | Same shape, gross +1.4 to +3.4 bps, t_day up to +9.07 — the single most statistically significant cell in the grid and one of the least tradable. |
| M4/M5 | taker flow-imbalance fade / follow | **DEAD** | No sign stability across horizons or thresholds; |t_day| ≤ 1.3 at the primary tier. The 0.50 threshold is rate-starved (5.3 L1/week); its apparent +34 bps h12 edge at T_DEEP rests on **39 independent episodes** (t_day 0.58, ETA 522 y) → `UNCONFIRMABLE_IN_HORIZON`, not an edge. |
| M6/M7 | OI build fade / OI flush bounce | **DEAD** | The preregistered H2/H3 (fresh crowding fades, forced deleveraging over-shoots) are directionally right for *build* at h=12 (+3.8 to +11.0 bps gross, t_day 2.9-5.2) and nowhere near cost. Flush is weaker than build, contrary to H3. |
| M8/M9 | volume-shock reversion / continuation | **UNCONFIRMABLE_IN_HORIZON** (at the 6× threshold) / **COST_FRAGILE** (at the 3× threshold) / **DEAD** (the continuation arm, everywhere) | The only real net edge in the grid — and D2 shows it is the known cascade edge. Best ETA 5.2 y. The continuation mirror is symmetrically and strongly negative, which is the arm-vs-arm contrast for this family. |
| M10 | basis-z reversion | **DEAD** | +2.6 to +4.9 bps gross at z≥2 with t_day 2.8-4.3; the more extreme z≥3 threshold has a *larger* point estimate (+3.4 to +8.5) whose t_day collapses to ≤0.3, i.e. it is noise, not a stronger signal. Consistent with briefing §4: basis is arbitraged out. |
| M11 | flow/price divergence | **DEAD** (T_LIQ/T_ALL), **DATA_LIMITED** (T_DEEP, 49 raw) | 518 raw, 326 L1 episodes over 6.5 years (1.8/week) at T_LIQ. Preregistered H4 (aggressive flow leads price) unsupported: t_day ≤ 1.0. |
| M12-M16 | hourly cross-sectional decile long/short | **DEAD** | The structurally correct answer to the ETA problem — 168 independent episodes/week, break-even 3.05 bps — and the gross spread is +0.4 to +4.5 bps against a *measured turnover* cost of 8.0-12.7. `M14_XS_OI_SHOCK` is the least-bad (gross +1.15 h1 / +4.49 h12, t_day +4.4/+3.6) and still loses by 8-10 bps. |
| M17 | funding-crowding fade (**negative control**) | **DEAD** | As preregistered, kept to prove the inventory discriminates. Gross +0.5 to +2.8 bps; 920 k raw observations worth 4.5 effective bets/day. It did its job. |

**Nothing was retuned.** No threshold was moved after seeing a result, no cell was dropped, no tier was
added post hoc. The three tiers, three horizons, 24 specs and every threshold are exactly as written in
`PREREGISTRATION.md` §6 on 2026-09-03.

---

## 7. METHOD

**Data.** `/home/qbee/futur-data-v2/data_v2/normalized/event_feature_panel/venue=binance` — the canonical
causal Data-V2 panel, 312 symbols, 5 m dense grid, collapsed to hourly decision points
(`minute == 0`): **8 787 448 hourly rows, 2020-01-01 → 2026-08-01**, 307 symbols after the BTC/ETH
exclusion; 7 059 253 rows pass the T_ALL liquidity/coverage filter.

**PIT contract.** `research_available_at = timestamp + 305 s` on the source panel. Every feature at
decision hour `H` uses bars `≤ H` only. **Entry at the close of bar `H + 10 min`** — 295 s of slack after
the feature is actually knowable. Forward labels are built exclusively with `LEAD` from bar `i+2`, so no
forward window ever touches its own signal window. `residual_return_*` are *trailing* beta-hedged returns
with causal, daily-frozen betas (`data_v2/events/residuals.py`); they are features, never labels.
BTCUSDT/ETHUSDT are the hedge factors and are excluded from every statistic. Status: **PIT_VERIFIED**
against the panel contract (see §8 for the one residual caveat).

**Everything is measured on residual (beta-hedged) returns**, which is this report's answer to briefing
rule #3: the market's strong unconditional drift is hedged out by construction, and every mirrored family
is additionally scored as an arm-vs-arm contrast. For M1/M2, M4/M5 and M8/M9 the two arms are *exact
negatives on an identical population* — reporting both is a presentation of the contrast, not an
independent test, and this report says so rather than dressing it up as one.

**Declustering, 3 levels, from the first calculation** (briefing rule #2):
L1 = same symbol / 24 h greedy forward scan (for cross-sectional mechanisms: non-overlapping forward
windows); L2 = calendar day, inference on the day-mean series; L3 = ISO week. **Headline
`t_stat_declustered` is the L2 Newey-West (lag 5) t**, with the optimistic L1-episode t and the
conservative L3-week t both reported. `bootstrap_ci95` = 2000-resample moving-block bootstrap on the
daily series, block = 5 days, with a block = 1 version reported alongside.

**ETA.** Two-sided α = 0.05, power 80 %, `(1.96 + 0.8416)² = 7.849`, **mandatory 50 % haircut on the
discovered edge**. Headline `eta_forward_confirmation = n_required_days / day_coverage_recent6m`.
`event_rate` = L1-independent episodes per week measured over the **last 6 months only** (conservative).
Three further ETAs are reported per cell: `eta_stress28_years`, `eta_L3week_years` (the conservative
bound if days are not independent) and `eta_at_zero_cost_years` (isolates "is there signal" from "is it
payable").

**Verdicts** are assigned mechanically by `assign_verdict()` from preregistration §7, with one
conservatism added at recovery time and declared as such: the raw-episode net **and** the day-mean net
must both be positive, so a mechanism carried only by a handful of heavy days cannot be scored above
`WEAK`. That is strictly stricter than what was preregistered.

**Capacity.** `capacity_usd_estimate` = median over episodes of `0.10 · dv_1h · horizon_hours` (10 % of
taker volume over the holding period), reported everywhere, never used as a filter. Range across the
grid: **$27.7 k** (`M4_FLOW_IMBALANCE_FADE_0.3` h1, T_ALL) to **$203.5 M**
(`M8_VOLSHOCK_REVERSION_6x` h12, T_DEEP). The one mechanism with a real edge, M8 at
T_ALL, carries **$4.7 M (h4) / $14.1 M (h12)** — and its T_LIQ variant, at $40.5 M capacity, has an ETA
of 15.4 years. Capacity and confirmability are in direct tension here: M8's ETA is 3× better on the
micro-cap tier precisely because that is where the edge lives.

---

## 8. LIMITATIONS — WHAT WOULD CHANGE THESE ANSWERS

1. **The cost model is a constant, and that is the whole game.** Every conclusion above is a statement
   about 14/28 bps taker round trips. The report deliberately publishes `max_sustainable_cost_bps`
   (= gross) and `cost_bps_for_1y_confirm` for all 258 cells so that a real execution layer can be
   dropped in. For the hourly cross-sectional family, sub-1-year confirmability needs a round trip below
   **−0.82 bps at h=1** (i.e. a net rebate) and **−4.56 bps at h=12** (best cell in each case) — which is why I am reporting this
   as a wall and not as a to-do. W5/W8's execution-cost layer is the only lever that moves any of these
   cells, and the numbers above say by how much it would have to move them.
2. **D2 reaches only 14.3 % of M8's episodes** (the cascade corpus covers 49 of 304 symbols). Running the
   existing cascade detector over the full 304-symbol PIT universe would turn a strong presumption into a
   proof. Until then M8 is `UNCONFIRMABLE_IN_HORIZON` *and* presumed non-orthogonal.
3. **`taker_buy_*` placeholder trap:** avoided by construction — flow features come from the Data-V2
   panel's `aggressive_buy_usd/aggressive_sell_usd`, not from `data/enriched`, and every row requires
   `nflow_1h == 12` (complete flow coverage in the trailing hour).
4. **Universe-growth artefact:** the panel's cross-section goes from ~28 to ~300 names. Cross-sectional
   cells require ≥30 eligible names per hour, which effectively starts the G family in 2020-2021; the
   per-year tables are the guard against reading pre-2022 breadth as signal.
5. **Sub-hourly cadence is untested here and is the one genuine gap.** The grid stops at a 1 h decision
   clock because that is the finest cadence the PIT panel supports over 6.5 years. Whether 1-5 minute
   mechanisms escape the cost wall cannot be answered from this data — and briefing §4 already records
   that the microstructure corpora (`market_physics_v3` = 2 days, `microstructure_reduced` since
   2026-08-31) are `DATA_LIMITED` and mono-regime. The §1 arithmetic is nonetheless directly reusable
   there: compute `k_eff` and `net_bps_min_1y` first, and only then look at bps.
6. `n_required` uses a one-sample normal approximation and assumes the forward regime resembles the
   discovery regime. The 50 % haircut is the only allowance made for that; it is a convention, not a
   measurement.

---

## 9. WHAT THE PROJECT SHOULD TAKE FROM THIS

1. **Adopt the identity as a screening rule before any research spend:**
   `ETA(years) = 31.4 / (annualised net Sharpe)²`. Confirmable in 1 y ⇔ Sharpe ≥ 5.60; 3 y ⇔ 3.24.
   Any candidate whose plausible Sharpe is below ~1.8 cannot be forward-confirmed this decade, whatever
   its bps. This can be applied to a proposal on a napkin, before a single backtest.
2. **Stop optimising `net_bps`; optimise `k_eff`** — effective independent bets per day. The cheapest
   available lever is breadth: the hourly cross-sectional construct cuts the required edge by 35× versus
   the weekly alphas. The project's existing confirmable-in-principle alphas would benefit far more from
   being re-expressed as broad daily portfolios than from another 20 bps of signal.
3. **Route the ETA problem to execution, not to research.** 98 statistically overwhelming cells with a
   median gross of 3.78 bps are sitting behind a 14 bps toll. Nothing in the signal-hunting programme
   reaches them. This is a direct, quantified brief for W5/W8's execution-cost layer, and
   `cost_bps_for_1y_confirm` in `RESULTS.json` tells it exactly which cells open at which cost.
4. **Treat `M8_VOLSHOCK_REVERSION_6x` as a corroboration of `LIQ_CASCADE_REPEAT_V1`, not as a candidate**
   — and, if the cascade detector is ever extended to the full universe, as a possible cheaper *trigger*
   for it (a volume-shock screen needs no liquidation feed, and fires on 304 symbols instead of 49).
5. **Do not fund another breadth-first hunt at hourly cadence on this panel.** 24 specs × 3 horizons ×
   3 tiers, drawn from every feature family the panel exposes, produced zero payable edges and one
   rediscovery. The constraint is not the idea supply.

---

## 10. DELIVERABLES

| path | content |
|---|---|
| `REPORT.md` | this file |
| `RESULTS.json` | machine-readable: 261 cells with every §2 gate field, the inventory, the family break-evens, D1 and D2 |
| `PREREGISTRATION.md` | hypotheses and thresholds, 2026-09-03, unamended |
| `evidence/scripts/build_hourly_panel.py` | 5 m PIT panel → hourly decision panel (sharded, ~45 s for 312 symbols) |
| `evidence/scripts/lib_hf.py` | declustering, gate statistics, ETA arithmetic |
| `evidence/scripts/run_inventory.py` | phase 1 — the episode-rate inventory |
| `evidence/scripts/run_mechanisms.py` | phase 2 — the preregistered grid, full gate, 3 tiers (~175 s) |
| `evidence/scripts/run_diagnostics.py` | phase 4 — D1 breadth efficiency, D2 orthogonality |
| `evidence/scripts/make_results.py`, `build_results_json.py`, `make_tables_md.py` | phases 3 and 5 |
| `evidence/results/INVENTORY.{json,csv}` | table T1 |
| `evidence/results/MECHANISMS.json`, `RESULTS_CELLS.json`, `GRID_BY_ETA.csv` | the raw and derived grid |
| `evidence/results/FAMILY_BREAKEVEN_T_LIQ.csv` | table T2 |
| `evidence/results/D1_BREADTH_EFFICIENCY.csv`, `D2_ORTHOGONALITY_M8.json` | the diagnostics |
| `evidence/results/TABLES.md` | all tables including the full 261-cell grid |

**Reproduction.** `.venv/bin/python` (3.8). From `evidence/scripts/`:

```bash
S=<scratch>                     # ~520 MB
python build_hourly_panel.py --out $S/hourly --shard 0 --nshard 2   # and --shard 1
export W6_HOURLY="$S/hourly/*.parquet"
python run_inventory.py && python run_mechanisms.py && python make_results.py
python run_diagnostics.py && python make_tables_md.py && python build_results_json.py
```

**Resources used.** Peak scratch 519 MB (hourly panel), all inside the session scratchpad, nothing
written outside `reports/edge_discovery/alpha_hunt_2026-09-03_round4/w6_high_frequency_episodes/`.
Disk stayed at 58 GB free throughout. Total compute ≈ 6 minutes.

---

## APPENDIX A — THE COMPLETE GRID, 261 CELLS, ETA ASCENDING

Column key: `N_L1` / `N_days` / `N_weeks` = independent sample sizes at declustering levels L1/L2/L3.
`gross` = mean signed forward residual return in bps before cost. `net14` / `net28` = after the 14 bps
round trip and the mandatory 28 bps stress (cross-sectional rows use their measured-turnover cost, see
§3). `t_day` = Newey-West t on the day-mean series (the headline declustered statistic). `CI95 net` =
2000-resample moving-block bootstrap, 5-day blocks. `ex-best-yr` = net bps with the single best year
removed. `L1/wk` = independent episodes per week over the last 6 months. `ETA (y)` = headline forward
confirmation time; `ETA0 (y)` = the same with execution assumed free. `Sharpe` = annualised net Sharpe of
the day series. Blank `ETA` means the net edge is ≤ 0, i.e. infinite.

### T3b — ALL 261 CELLS, ETA ASCENDING

| mechanism | h | tier | N_raw | N_L1 | N_days | N_weeks | gross | net14 | net28 | t_day | CI95 net | ex-best-yr | L1/wk | ETA (y) | ETA0 (y) | Sharpe | capacity $ | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| M8_VOLSHOCK_REVERSION_6.0x | 4 | T_ALL | 19928 | 15195.00 | 2083.00 | 328.00 | 40.81 | 26.81 | 12.81 | 8.92 | [19,38] | 12.66 | 74.16 | 5.21 | 2.34 | 2.45 | 4.71e+06 | UNCONFIRMABLE_IN_HORIZON |
| M8_VOLSHOCK_REVERSION_6.0x | 12 | T_ALL | 19921 | 15188.00 | 2083.00 | 328.00 | 63.84 | 49.84 | 35.84 | 6.99 | [29,62] | 31.27 | 74.04 | 5.83 | 3.40 | 2.32 | 1.41e+07 | UNCONFIRMABLE_IN_HORIZON |
| M8_VOLSHOCK_REVERSION_6.0x | 1 | T_ALL | 19928 | 15195.00 | 2083.00 | 328.00 | 15.12 | 1.12 | -12.88 | 8.03 | [4,16] | -0.47 | 74.16 | 15.16 | 2.57 | 1.44 | 1.18e+06 | COST_FRAGILE |
| M8_VOLSHOCK_REVERSION_6.0x | 12 | T_LIQ | 8386 | 6822.00 | 1822.00 | 321.00 | 58.31 | 44.31 | 30.31 | 4.35 | [16,69] | 20.68 | 18.16 | 15.39 | 8.91 | 1.43 | 4.05e+07 | UNCONFIRMABLE_IN_HORIZON |
| M8_VOLSHOCK_REVERSION_6.0x | 4 | T_LIQ | 8390 | 6826.00 | 1822.00 | 321.00 | 37.89 | 23.89 | 9.89 | 4.85 | [11,45] | 10.15 | 18.20 | 15.76 | 6.97 | 1.41 | 1.35e+07 | UNCONFIRMABLE_IN_HORIZON |
| M1_RESID_REVERSION_1H_z4.0 | 12 | T_ALL | 52973 | 30029.00 | 2246.00 | 329.00 | 20.68 | 6.68 | -7.32 | 5.52 | [6,28] | -1.69 | 128.57 | 19.84 | 6.05 | 1.26 | 1.27e+07 | COST_FRAGILE |
| M8_VOLSHOCK_REVERSION_6.0x | 1 | T_LIQ | 8390 | 6826.00 | 1822.00 | 321.00 | 18.86 | 4.86 | -9.14 | 4.95 | [4,26] | -1.21 | 18.20 | 20.60 | 5.51 | 1.23 | 3.37e+06 | COST_FRAGILE |
| M1_RESID_REVERSION_1H_z4.0 | 4 | T_ALL | 53037 | 30067.00 | 2246.00 | 329.00 | 11.95 | -2.05 | -16.05 | 6.35 | [3,17] | -6.48 | 128.96 | 26.62 | 4.43 | 1.09 | 4.23e+06 | WEAK |
| M8_VOLSHOCK_REVERSION_3.0x | 12 | T_ALL | 95827 | 58297.00 | 2289.00 | 330.00 | 24.61 | 10.61 | -3.39 | 6.22 | [2,16] | 10.12 | 264.17 | 31.89 | 4.64 | 0.99 | 8.98e+06 | COST_FRAGILE |
| M8_VOLSHOCK_REVERSION_3.0x | 4 | T_ALL | 95884 | 58337.00 | 2289.00 | 330.00 | 13.58 | -0.42 | -14.42 | 9.06 | [1,9] | -2.35 | 264.91 | 33.99 | 2.19 | 0.96 | 3e+06 | WEAK |
| M8_VOLSHOCK_REVERSION_3.0x | 4 | T_LIQ | 49964 | 31323.00 | 2237.00 | 330.00 | 14.84 | 0.84 | -13.16 | 6.15 | [-1,12] | -2.62 | 84.93 | 63.90 | 4.63 | 0.70 | 7.98e+06 | COST_FRAGILE |
| M6_OI_BUILD_FADE_0.02 | 12 | T_ALL | 108067 | 54293.00 | 1704.00 | 244.00 | 11.04 | -2.96 | -16.96 | 5.19 | [-2,12] | -10.09 | 200.16 | 66.21 | 4.87 | 0.69 | 6.44e+06 | WEAK |
| M8_VOLSHOCK_REVERSION_3.0x | 12 | T_LIQ | 49929 | 31298.00 | 2237.00 | 330.00 | 24.93 | 10.93 | -3.07 | 4.23 | [-2,18] | 8.84 | 84.66 | 80.66 | 10.37 | 0.62 | 2.39e+07 | COST_FRAGILE |
| M6_OI_BUILD_FADE_0.02 | 12 | T_LIQ | 62500 | 30429.00 | 1704.00 | 244.00 | 6.93 | -7.07 | -21.07 | 3.15 | [-7,16] | -13.34 | 70.51 | 144.46 | 13.55 | 0.47 | 1.49e+07 | DEAD |
| M3_RESID_REVERSION_4H_z2.5 | 1 | T_DEEP | 23224 | 6345.00 | 1846.00 | 306.00 | 2.16 | -11.84 | -25.84 | 5.28 | [-3,10] | -12.13 | 13.88 | 157.45 | 5.99 | 0.45 | 5.99e+06 | DEAD |
| M8_VOLSHOCK_REVERSION_6.0x | 4 | T_DEEP | 919 | 811.00 | 548.00 | 248.00 | 39.48 | 25.48 | 11.48 | 1.71 | [-2,82] | 6.87 | 1.58 | 252.32 | 112.53 | 0.35 | 6.79e+07 | UNCONFIRMABLE_IN_HORIZON |
| M1_RESID_REVERSION_1H_z4.0 | 12 | T_LIQ | 32072 | 18271.00 | 2173.00 | 328.00 | 11.42 | -2.58 | -16.58 | 2.58 | [-11,19] | -10.46 | 48.69 | 303.44 | 26.34 | 0.32 | 2.73e+07 | WEAK |
| M1_RESID_REVERSION_1H_z4.0 | 4 | T_LIQ | 32121 | 18297.00 | 2173.00 | 328.00 | 12.53 | -1.47 | -15.47 | 3.58 | [-6,13] | -8.99 | 48.84 | 336.18 | 14.04 | 0.31 | 9.08e+06 | WEAK |
| M3_RESID_REVERSION_4H_z2.5 | 4 | T_DEEP | 23222 | 6344.00 | 1846.00 | 306.00 | -3.92 | -17.92 | -31.92 | 2.49 | [-10,19] | -18.62 | 13.88 | 406.48 | 27.33 | 0.28 | 2.4e+07 | DEAD |
| M11_FLOW_PRICE_DIVERGENCE | 12 | T_LIQ | 518 | 326.00 | 299.00 | 144.00 | 7.63 | -6.37 | -20.37 | 1.00 | [-37,63] | -14.26 | 1.78 | 465.05 | 92.38 | 0.26 | 6.09e+06 | DEAD |
| M4_FLOW_IMBALANCE_FADE_0.5 | 12 | T_DEEP | 437 | 39.00 | 40.00 | 15.00 | 34.09 | 20.09 | 6.09 | 0.58 | [-68,126] | 17.25 | 0.38 | 522.00 | 168.21 | 0.25 | 1.39e+07 | UNCONFIRMABLE_IN_HORIZON |
| M8_VOLSHOCK_REVERSION_6.0x | 12 | T_DEEP | 918 | 810.00 | 548.00 | 248.00 | 33.38 | 19.38 | 5.38 | 1.06 | [-26,89] | -23.08 | 1.58 | 740.15 | 257.22 | 0.21 | 2.04e+08 | UNCONFIRMABLE_IN_HORIZON |
| M8_VOLSHOCK_REVERSION_3.0x | 4 | T_DEEP | 8074 | 5452.00 | 1742.00 | 309.00 | 15.32 | 1.32 | -12.68 | 2.59 | [-10,16] | 1.07 | 11.64 | 969.67 | 31.45 | 0.18 | 3.94e+07 | COST_FRAGILE |
| M1_RESID_REVERSION_1H_z4.0 | 4 | T_DEEP | 6471 | 3701.00 | 1493.00 | 300.00 | 3.80 | -10.20 | -24.20 | 1.63 | [-19,25] | -11.64 | 7.86 | 1340.02 | 68.49 | 0.15 | 3.83e+07 | DEAD |
| M5_FLOW_IMBALANCE_FOLLOW_0.5 | 1 | T_DEEP | 437 | 39.00 | 40.00 | 15.00 | 4.33 | -9.67 | -23.67 | 1.45 | [-19,21] | -10.10 | 0.38 | 1859.35 | 28.64 | 0.13 | 1.16e+06 | DEAD |
| M5_FLOW_IMBALANCE_FOLLOW_0.5 | 4 | T_DEEP | 437 | 39.00 | 40.00 | 15.00 | 3.81 | -10.19 | -24.19 | 0.68 | [-47,51] | -10.44 | 0.38 | 2222.08 | 106.23 | 0.12 | 4.62e+06 | DEAD |
| M3_RESID_REVERSION_4H_z2.5 | 12 | T_ALL | 190415 | 53338.00 | 2275.00 | 329.00 | 9.14 | -4.86 | -18.86 | 3.24 | [-8,10] | -12.79 | 231.54 | 3450.22 | 17.37 | 0.10 | 6.76e+06 | WEAK |
| M8_VOLSHOCK_REVERSION_6.0x | 1 | T_DEEP | 919 | 811.00 | 548.00 | 248.00 | 5.63 | -8.37 | -22.37 | 1.01 | [-22,40] | -16.17 | 1.58 | 5278.92 | 247.66 | 0.08 | 1.7e+07 | DEAD |
| M2_RESID_CONTINUATION_1H_z4.0 | 12 | T_DEEP | 6461 | 3694.00 | 1492.00 | 300.00 | 49.06 | 35.06 | 21.06 | 0.79 | [-35,46] | -0.35 | 7.90 | 8805.48 | 237.33 | 0.06 | 1.15e+08 | UNCONFIRMABLE_IN_HORIZON |
| M6_OI_BUILD_FADE_0.02 | 12 | T_DEEP | 11166 | 5562.00 | 1589.00 | 244.00 | -49.68 | -63.68 | -77.68 | 1.04 | [-29,31] | -66.25 | 11.46 | 16141.53 | 142.54 | 0.04 | 6.57e+07 | DEAD |
| M6_OI_BUILD_FADE_0.02 | 4 | T_DEEP | 11174 | 5568.00 | 1590.00 | 244.00 | -20.13 | -34.13 | -48.13 | 1.74 | [-16,18] | -35.82 | 11.43 | 169690.16 | 59.46 | 0.01 | 2.19e+07 | DEAD |
| M10_BASIS_Z_REVERSION_2.0 | 1 | T_ALL | 355378 | 150900.00 | 2312.00 | 331.00 | 1.79 | -12.21 | -26.21 | 4.59 | [-13,-11] | -12.86 | 536.94 |  | 9.50 | -9.46 | 134685.20 | DEAD |
| M10_BASIS_Z_REVERSION_2.0 | 4 | T_ALL | 355347 | 150889.00 | 2312.00 | 331.00 | 1.91 | -12.09 | -26.09 | 4.91 | [-11,-8] | -13.44 | 536.67 |  | 6.49 | -4.94 | 538744.85 | DEAD |
| M10_BASIS_Z_REVERSION_2.0 | 12 | T_ALL | 355233 | 150837.00 | 2312.00 | 331.00 | 3.26 | -10.74 | -24.74 | 4.35 | [-11,-5] | -12.98 | 535.89 |  | 8.98 | -2.25 | 1.62e+06 | DEAD |
| M10_BASIS_Z_REVERSION_3.0 | 1 | T_ALL | 51797 | 40353.00 | 2290.00 | 331.00 | 4.70 | -9.30 | -23.30 | 1.70 | [-14,-9] | -12.91 | 127.17 |  | 60.20 | -3.80 | 156719.50 | DEAD |
| M10_BASIS_Z_REVERSION_3.0 | 4 | T_ALL | 51791 | 40348.00 | 2290.00 | 331.00 | 2.58 | -11.42 | -25.42 | 0.70 | [-16,-8] | -15.90 | 127.05 |  | 357.90 | -2.41 | 626943.80 | DEAD |
| M10_BASIS_Z_REVERSION_3.0 | 12 | T_ALL | 51753 | 40313.00 | 2290.00 | 331.00 | 0.30 | -13.70 | -27.70 | 0.03 | [-21,-7] | -19.97 | 126.78 |  | 229002.83 | -1.63 | 1.88e+06 | DEAD |
| M11_FLOW_PRICE_DIVERGENCE | 1 | T_ALL | 1702 | 1390.00 | 864.00 | 253.00 | -0.19 | -14.19 | -28.19 | 0.31 | [-19,-8] | -16.94 | 11.51 |  | 994.18 | -2.57 | 54155.54 | DEAD |
| M11_FLOW_PRICE_DIVERGENCE | 4 | T_ALL | 1702 | 1390.00 | 864.00 | 253.00 | 1.84 | -12.16 | -26.16 | 0.15 | [-24,-2] | -16.65 | 11.51 |  | 3557.52 | -1.39 | 216622.18 | DEAD |
| M11_FLOW_PRICE_DIVERGENCE | 12 | T_ALL | 1702 | 1390.00 | 864.00 | 253.00 | 7.60 | -6.40 | -20.40 | 0.78 | [-25,15] | -14.64 | 11.51 |  | 139.46 | -0.35 | 649866.53 | DEAD |
| M12_XS_RESID_REVERSAL_1H | 1 | T_ALL | 50657 | 50657.00 | 2112.00 | 303.00 | 1.01 | -10.80 | -22.61 | 4.75 | [-11,-10] | -10.93 | 168.89 |  | 4.81 | -27.70 | 7.61e+06 | DEAD |
| M12_XS_RESID_REVERSAL_1H | 4 | T_ALL | 50654 | 12664.00 | 2112.00 | 303.00 | 0.60 | -11.35 | -23.29 | 1.57 | [-12,-11] | -11.67 | 42.19 |  | 52.30 | -14.86 | 3.04e+07 | DEAD |
| M12_XS_RESID_REVERSAL_1H | 12 | T_ALL | 50646 | 4221.00 | 2112.00 | 303.00 | 0.18 | -11.91 | -24.01 | 0.31 | [-13,-11] | -12.21 | 14.04 |  | 1386.15 | -8.83 | 9.13e+07 | DEAD |
| M13_XS_FLOW_REVERSAL_1H | 1 | T_ALL | 50657 | 50657.00 | 2112.00 | 303.00 | 0.43 | -11.35 | -23.12 | 3.62 | [-12,-11] | -11.40 | 168.89 |  | 9.94 | -48.12 | 1.86e+06 | DEAD |
| M13_XS_FLOW_REVERSAL_1H | 4 | T_ALL | 50654 | 12664.00 | 2112.00 | 303.00 | -0.16 | -12.17 | -24.18 | -0.74 | [-13,-12] | -12.40 | 42.19 |  | inf | -22.97 | 7.45e+06 | DEAD |
| M13_XS_FLOW_REVERSAL_1H | 12 | T_ALL | 50645 | 4221.00 | 2112.00 | 303.00 | -0.87 | -12.98 | -25.09 | -1.83 | [-14,-12] | -13.61 | 14.04 |  | inf | -12.95 | 2.24e+07 | DEAD |
| M14_XS_OI_SHOCK | 1 | T_ALL | 40860 | 40860.00 | 1704.00 | 244.00 | 0.93 | -9.81 | -20.55 | 5.53 | [-10,-9] | -9.85 | 168.89 |  | 3.54 | -31.46 | 6.1e+06 | DEAD |
| M14_XS_OI_SHOCK | 4 | T_ALL | 40857 | 10217.00 | 1704.00 | 244.00 | 1.98 | -9.68 | -21.34 | 5.08 | [-10,-9] | -9.77 | 42.19 |  | 4.17 | -13.40 | 2.44e+07 | DEAD |
| M14_XS_OI_SHOCK | 12 | T_ALL | 40849 | 3406.00 | 1704.00 | 244.00 | 3.54 | -8.41 | -20.36 | 4.31 | [-10,-7] | -8.55 | 14.04 |  | 5.25 | -5.84 | 7.32e+07 | DEAD |
| M15_XS_VOLSHOCK | 1 | T_ALL | 50657 | 50657.00 | 2112.00 | 303.00 | -0.49 | -8.64 | -16.79 | -3.06 | [-9,-8] | -8.75 | 168.89 |  | inf | -24.30 | 2.57e+06 | DEAD |
| M15_XS_VOLSHOCK | 4 | T_ALL | 50654 | 12664.00 | 2112.00 | 303.00 | -1.10 | -11.54 | -21.98 | -2.42 | [-12,-11] | -11.92 | 42.19 |  | inf | -11.09 | 1.03e+07 | DEAD |
| M15_XS_VOLSHOCK | 12 | T_ALL | 50646 | 4221.00 | 2112.00 | 303.00 | -0.15 | -12.05 | -23.96 | -0.14 | [-14,-10] | -13.59 | 14.04 |  | inf | -5.14 | 3.08e+07 | DEAD |
| M16_XS_BASIS_REVERSAL | 1 | T_ALL | 50634 | 50634.00 | 2112.00 | 303.00 | 0.55 | -11.16 | -22.87 | 4.32 | [-11,-11] | -11.23 | 168.89 |  | 7.07 | -42.79 | 4.83e+06 | DEAD |
| M16_XS_BASIS_REVERSAL | 4 | T_ALL | 50631 | 12661.00 | 2112.00 | 303.00 | 0.90 | -11.37 | -23.63 | 2.98 | [-12,-11] | -11.57 | 42.19 |  | 13.13 | -19.65 | 1.93e+07 | DEAD |
| M16_XS_BASIS_REVERSAL | 12 | T_ALL | 50623 | 4221.00 | 2112.00 | 303.00 | 1.24 | -11.35 | -23.95 | 2.27 | [-12,-10] | -11.59 | 14.04 |  | 25.64 | -10.50 | 5.8e+07 | DEAD |
| M17_FUNDING_CROWDING_FADE | 1 | T_ALL | 1952630 | 108901.00 | 2174.00 | 327.00 | 0.67 | -13.33 | -27.33 | 3.41 | [-13,-12] | -13.34 | 419.14 |  | 16.02 | -13.20 | 87285.09 | DEAD |
| M17_FUNDING_CROWDING_FADE | 4 | T_ALL | 1952403 | 108899.00 | 2174.00 | 327.00 | 2.54 | -11.46 | -25.46 | 2.68 | [-13,-7] | -12.04 | 419.07 |  | 25.54 | -2.76 | 349168.88 | DEAD |
| M17_FUNDING_CROWDING_FADE | 12 | T_ALL | 1951843 | 108874.00 | 2174.00 | 327.00 | 6.53 | -7.47 | -21.47 | 1.84 | [-14,3] | -11.00 | 418.44 |  | 49.20 | -0.57 | 1.05e+06 | DEAD |
| M1_RESID_REVERSION_1H_z1.5 | 1 | T_ALL | 646003 | 165276.00 | 2308.00 | 330.00 | 1.40 | -12.60 | -26.60 | 6.85 | [-11,-10] | -12.85 | 717.66 |  | 3.55 | -8.84 | 290342.35 | DEAD |
| M1_RESID_REVERSION_1H_z1.5 | 4 | T_ALL | 645926 | 165270.00 | 2308.00 | 330.00 | 2.60 | -11.40 | -25.40 | 6.68 | [-10,-7] | -11.71 | 717.50 |  | 3.79 | -4.05 | 1.16e+06 | DEAD |
| M1_RESID_REVERSION_1H_z1.5 | 12 | T_ALL | 645577 | 165203.00 | 2308.00 | 330.00 | 3.12 | -10.88 | -24.88 | 4.36 | [-11,-5] | -11.14 | 715.87 |  | 9.32 | -2.35 | 3.48e+06 | DEAD |
| M1_RESID_REVERSION_1H_z2.5 | 1 | T_ALL | 187665 | 78067.00 | 2301.00 | 330.00 | 2.73 | -11.27 | -25.27 | 7.14 | [-9,-5] | -11.77 | 329.74 |  | 3.41 | -3.02 | 552237.20 | DEAD |
| M1_RESID_REVERSION_1H_z2.5 | 4 | T_ALL | 187646 | 78061.00 | 2301.00 | 330.00 | 4.99 | -9.01 | -23.01 | 6.61 | [-6,1] | -9.64 | 329.66 |  | 3.83 | -0.66 | 2.21e+06 | DEAD |
| M1_RESID_REVERSION_1H_z2.5 | 12 | T_ALL | 187498 | 78015.00 | 2301.00 | 330.00 | 6.78 | -7.22 | -21.22 | 4.70 | [-6,4] | -7.65 | 328.92 |  | 7.55 | -0.14 | 6.63e+06 | DEAD |
| M1_RESID_REVERSION_1H_z4.0 | 1 | T_ALL | 53041 | 30068.00 | 2246.00 | 329.00 | 3.78 | -10.22 | -24.22 | 6.31 | [-5,4] | -14.51 | 128.96 |  | 5.16 | -0.04 | 1.06e+06 | DEAD |
| M2_RESID_CONTINUATION_1H_z1.5 | 1 | T_ALL | 646003 | 165276.00 | 2308.00 | 330.00 | -1.40 | -15.40 | -29.40 | -6.85 | [-18,-17] | -16.22 | 717.66 |  | inf | -14.79 | 290342.35 | DEAD |
| M2_RESID_CONTINUATION_1H_z1.5 | 4 | T_ALL | 645926 | 165270.00 | 2308.00 | 330.00 | -2.60 | -16.60 | -30.60 | -6.68 | [-21,-18] | -17.11 | 717.50 |  | inf | -9.81 | 1.16e+06 | DEAD |
| M2_RESID_CONTINUATION_1H_z1.5 | 12 | T_ALL | 645577 | 165203.00 | 2308.00 | 330.00 | -3.12 | -17.12 | -31.12 | -4.36 | [-23,-17] | -18.17 | 715.87 |  | inf | -6.02 | 3.48e+06 | DEAD |
| M2_RESID_CONTINUATION_1H_z2.5 | 1 | T_ALL | 187665 | 78067.00 | 2301.00 | 330.00 | -2.73 | -16.73 | -30.73 | -7.14 | [-23,-19] | -17.60 | 329.74 |  | inf | -9.09 | 552237.20 | DEAD |
| M2_RESID_CONTINUATION_1H_z2.5 | 4 | T_ALL | 187646 | 78061.00 | 2301.00 | 330.00 | -4.99 | -18.99 | -32.99 | -6.61 | [-29,-22] | -21.12 | 329.66 |  | inf | -6.39 | 2.21e+06 | DEAD |
| M2_RESID_CONTINUATION_1H_z2.5 | 12 | T_ALL | 187498 | 78015.00 | 2301.00 | 330.00 | -6.78 | -20.78 | -34.78 | -4.70 | [-32,-22] | -25.04 | 328.92 |  | inf | -4.21 | 6.63e+06 | DEAD |
| M2_RESID_CONTINUATION_1H_z4.0 | 1 | T_ALL | 53041 | 30068.00 | 2246.00 | 329.00 | -3.78 | -17.78 | -31.78 | -6.31 | [-32,-23] | -21.81 | 128.96 |  | inf | -4.97 | 1.06e+06 | DEAD |
| M2_RESID_CONTINUATION_1H_z4.0 | 4 | T_ALL | 53037 | 30067.00 | 2246.00 | 329.00 | -11.95 | -25.95 | -39.95 | -6.35 | [-45,-31] | -29.89 | 128.96 |  | inf | -4.23 | 4.23e+06 | DEAD |
| M2_RESID_CONTINUATION_1H_z4.0 | 12 | T_ALL | 52973 | 30029.00 | 2246.00 | 329.00 | -20.68 | -34.68 | -48.68 | -5.52 | [-56,-34] | -42.07 | 128.57 |  | inf | -3.30 | 1.27e+07 | DEAD |
| M3_RESID_REVERSION_4H_z1.5 | 1 | T_ALL | 655325 | 132717.00 | 2302.00 | 330.00 | 1.39 | -12.61 | -26.61 | 9.07 | [-11,-9] | -12.77 | 582.94 |  | 2.03 | -9.38 | 286660.95 | DEAD |
| M3_RESID_REVERSION_4H_z1.5 | 4 | T_ALL | 655239 | 132705.00 | 2302.00 | 330.00 | 2.78 | -11.22 | -25.22 | 5.24 | [-10,-5] | -11.42 | 582.71 |  | 6.47 | -2.76 | 1.15e+06 | DEAD |
| M3_RESID_REVERSION_4H_z1.5 | 12 | T_ALL | 654815 | 132640.00 | 2302.00 | 330.00 | 3.51 | -10.49 | -24.49 | 2.48 | [-13,-4] | -12.34 | 581.08 |  | 29.57 | -1.46 | 3.44e+06 | DEAD |
| M3_RESID_REVERSION_4H_z2.5 | 1 | T_ALL | 190613 | 53393.00 | 2275.00 | 329.00 | 2.17 | -11.83 | -25.83 | 8.35 | [-7,-3] | -13.53 | 232.63 |  | 2.47 | -2.23 | 563435.65 | DEAD |
| M3_RESID_REVERSION_4H_z2.5 | 4 | T_ALL | 190589 | 53388.00 | 2275.00 | 329.00 | 4.93 | -9.07 | -23.07 | 5.40 | [-6,3] | -9.40 | 232.59 |  | 6.36 | -0.25 | 2.25e+06 | DEAD |
| M4_FLOW_IMBALANCE_FADE_0.3 | 1 | T_ALL | 142767 | 64825.00 | 2293.00 | 330.00 | -0.30 | -14.30 | -28.30 | -0.23 | [-15,-13] | -14.72 | 496.65 |  | inf | -10.61 | 27696.43 | DEAD |
| M4_FLOW_IMBALANCE_FADE_0.3 | 4 | T_ALL | 142730 | 64816.00 | 2293.00 | 330.00 | -1.60 | -15.60 | -29.60 | -1.92 | [-18,-14] | -16.37 | 496.34 |  | inf | -5.92 | 110795.23 | DEAD |
| M4_FLOW_IMBALANCE_FADE_0.3 | 12 | T_ALL | 142671 | 64789.00 | 2293.00 | 330.00 | -6.21 | -20.21 | -34.21 | -2.10 | [-23,-14] | -20.52 | 495.72 |  | inf | -3.41 | 332430.38 | DEAD |
| M4_FLOW_IMBALANCE_FADE_0.5 | 1 | T_ALL | 9058 | 4060.00 | 1250.00 | 263.00 | -1.79 | -15.79 | -29.79 | -0.06 | [-17,-11] | -16.05 | 42.62 |  | inf | -5.43 | 40040.74 | DEAD |
| M4_FLOW_IMBALANCE_FADE_0.5 | 4 | T_ALL | 9058 | 4060.00 | 1250.00 | 263.00 | -5.94 | -19.94 | -33.94 | -2.11 | [-27,-14] | -21.39 | 42.62 |  | inf | -3.37 | 160162.95 | DEAD |
| M4_FLOW_IMBALANCE_FADE_0.5 | 12 | T_ALL | 9057 | 4060.00 | 1250.00 | 263.00 | -14.61 | -28.61 | -42.61 | -1.49 | [-38,-11] | -29.49 | 42.62 |  | inf | -1.89 | 480597.79 | DEAD |
| M5_FLOW_IMBALANCE_FOLLOW_0.3 | 1 | T_ALL | 142767 | 64825.00 | 2293.00 | 330.00 | 0.30 | -13.70 | -27.70 | 0.23 | [-15,-13] | -14.17 | 496.65 |  | 3491.80 | -10.42 | 27696.43 | DEAD |
| M5_FLOW_IMBALANCE_FOLLOW_0.3 | 4 | T_ALL | 142730 | 64816.00 | 2293.00 | 330.00 | 1.60 | -12.40 | -26.40 | 1.92 | [-14,-10] | -13.32 | 496.34 |  | 56.41 | -4.43 | 110795.23 | DEAD |
| M5_FLOW_IMBALANCE_FOLLOW_0.3 | 12 | T_ALL | 142671 | 64789.00 | 2293.00 | 330.00 | 6.21 | -7.79 | -21.79 | 2.10 | [-14,-5] | -9.65 | 495.72 |  | 45.20 | -1.75 | 332430.38 | DEAD |
| M5_FLOW_IMBALANCE_FOLLOW_0.5 | 1 | T_ALL | 9058 | 4060.00 | 1250.00 | 263.00 | 1.79 | -12.21 | -26.21 | 0.06 | [-17,-11] | -12.28 | 42.62 |  | 25552.88 | -5.36 | 40040.74 | DEAD |
| M5_FLOW_IMBALANCE_FOLLOW_0.5 | 4 | T_ALL | 9058 | 4060.00 | 1250.00 | 263.00 | 5.94 | -8.06 | -22.06 | 2.11 | [-14,-1] | -8.22 | 42.62 |  | 27.37 | -1.23 | 160162.95 | DEAD |
| M5_FLOW_IMBALANCE_FOLLOW_0.5 | 12 | T_ALL | 9057 | 4060.00 | 1250.00 | 263.00 | 14.61 | 0.61 | -13.39 | 1.49 | [-17,10] | -3.54 | 42.62 |  | 48.82 | -0.29 | 480597.79 | WEAK |
| M6_OI_BUILD_FADE_0.01 | 1 | T_ALL | 213900 | 91987.00 | 1704.00 | 244.00 | 1.29 | -12.71 | -26.71 | 3.60 | [-13,-11] | -12.75 | 370.14 |  | 10.67 | -8.94 | 351970.15 | DEAD |
| M6_OI_BUILD_FADE_0.01 | 4 | T_ALL | 213880 | 91980.00 | 1704.00 | 244.00 | 3.39 | -10.61 | -24.61 | 4.36 | [-11,-6] | -10.81 | 370.11 |  | 7.58 | -2.99 | 1.41e+06 | DEAD |
| M6_OI_BUILD_FADE_0.01 | 12 | T_ALL | 213800 | 91951.00 | 1704.00 | 244.00 | 6.19 | -7.81 | -21.81 | 4.50 | [-8,2] | -11.55 | 369.48 |  | 6.29 | -0.50 | 4.22e+06 | DEAD |
| M6_OI_BUILD_FADE_0.02 | 1 | T_ALL | 108114 | 54314.00 | 1704.00 | 244.00 | 2.55 | -11.45 | -25.45 | 4.95 | [-11,-8] | -11.54 | 200.55 |  | 6.42 | -4.69 | 537066.38 | DEAD |
| M6_OI_BUILD_FADE_0.02 | 4 | T_ALL | 108106 | 54310.00 | 1704.00 | 244.00 | 5.71 | -8.29 | -22.29 | 5.05 | [-8,-0] | -8.57 | 200.51 |  | 5.80 | -0.91 | 2.15e+06 | DEAD |
| M7_OI_FLUSH_BOUNCE_0.01 | 1 | T_ALL | 198630 | 89515.00 | 1704.00 | 244.00 | 0.37 | -13.63 | -27.63 | 2.00 | [-14,-11] | -14.71 | 378.86 |  | 34.19 | -9.03 | 298466.46 | DEAD |
| M7_OI_FLUSH_BOUNCE_0.01 | 4 | T_ALL | 198606 | 89509.00 | 1704.00 | 244.00 | 0.91 | -13.09 | -27.09 | 2.34 | [-14,-9] | -14.66 | 378.66 |  | 26.13 | -5.05 | 1.19e+06 | DEAD |
| M7_OI_FLUSH_BOUNCE_0.01 | 12 | T_ALL | 198481 | 89460.00 | 1704.00 | 244.00 | -0.53 | -14.53 | -28.53 | 0.97 | [-16,-8] | -16.70 | 377.92 |  | 143.33 | -2.98 | 3.58e+06 | DEAD |
| M7_OI_FLUSH_BOUNCE_0.02 | 1 | T_ALL | 81728 | 46364.00 | 1704.00 | 244.00 | 0.18 | -13.82 | -27.82 | 1.49 | [-15,-10] | -15.79 | 174.69 |  | 59.29 | -5.33 | 464979.78 | DEAD |
| M7_OI_FLUSH_BOUNCE_0.02 | 4 | T_ALL | 81720 | 46360.00 | 1704.00 | 244.00 | 0.76 | -13.24 | -27.24 | 1.32 | [-15,-8] | -15.30 | 174.65 |  | 82.85 | -2.96 | 1.86e+06 | DEAD |
| M7_OI_FLUSH_BOUNCE_0.02 | 12 | T_ALL | 81654 | 46326.00 | 1704.00 | 244.00 | -1.16 | -15.16 | -29.16 | 0.77 | [-18,-5] | -18.68 | 174.46 |  | 226.65 | -1.75 | 5.58e+06 | DEAD |
| M8_VOLSHOCK_REVERSION_3.0x | 1 | T_ALL | 95887 | 58338.00 | 2289.00 | 330.00 | 2.76 | -11.24 | -25.24 | 7.76 | [-7,-2] | -12.88 | 264.95 |  | 3.01 | -1.40 | 748906.45 | DEAD |
| M9_VOLSHOCK_CONTINUATION_3.0x | 1 | T_ALL | 95887 | 58338.00 | 2289.00 | 330.00 | -2.76 | -16.76 | -30.76 | -7.76 | [-26,-21] | -20.79 | 264.95 |  | inf | -7.85 | 748906.45 | DEAD |
| M9_VOLSHOCK_CONTINUATION_3.0x | 4 | T_ALL | 95884 | 58337.00 | 2289.00 | 330.00 | -13.58 | -27.58 | -41.58 | -9.06 | [-37,-29] | -28.48 | 264.91 |  | inf | -6.62 | 3e+06 | DEAD |
| M9_VOLSHOCK_CONTINUATION_3.0x | 12 | T_ALL | 95827 | 58297.00 | 2289.00 | 330.00 | -24.61 | -38.61 | -52.61 | -6.22 | [-44,-30] | -38.92 | 264.17 |  | inf | -4.21 | 8.98e+06 | DEAD |
| M9_VOLSHOCK_CONTINUATION_6.0x | 1 | T_ALL | 19928 | 15195.00 | 2083.00 | 328.00 | -15.12 | -29.12 | -43.12 | -8.03 | [-44,-32] | -30.67 | 74.16 |  | inf | -5.54 | 1.18e+06 | DEAD |
| M9_VOLSHOCK_CONTINUATION_6.0x | 4 | T_ALL | 19928 | 15195.00 | 2083.00 | 328.00 | -40.81 | -54.81 | -68.81 | -8.92 | [-66,-47] | -60.73 | 74.16 |  | inf | -4.87 | 4.71e+06 | DEAD |
| M9_VOLSHOCK_CONTINUATION_6.0x | 12 | T_ALL | 19921 | 15188.00 | 2083.00 | 328.00 | -63.84 | -77.84 | -91.84 | -6.99 | [-90,-57] | -87.23 | 74.04 |  | inf | -3.76 | 1.41e+07 | DEAD |
| M10_BASIS_Z_REVERSION_2.0 | 1 | T_DEEP | 37077 | 15130.00 | 2149.00 | 314.00 | 3.72 | -10.28 | -24.28 | 3.05 | [-13,-8] | -12.18 | 24.42 |  | 19.34 | -3.58 | 2.26e+06 | DEAD |
| M10_BASIS_Z_REVERSION_2.0 | 4 | T_DEEP | 37075 | 15129.00 | 2149.00 | 314.00 | 1.22 | -12.78 | -26.78 | 1.34 | [-16,-6] | -12.89 | 24.42 |  | 92.43 | -2.09 | 9.04e+06 | DEAD |
| M10_BASIS_Z_REVERSION_2.0 | 12 | T_DEEP | 37053 | 15124.00 | 2149.00 | 314.00 | 3.89 | -10.11 | -24.11 | 0.86 | [-19,-3] | -16.23 | 24.34 |  | 220.30 | -1.17 | 2.71e+07 | DEAD |
| M10_BASIS_Z_REVERSION_3.0 | 1 | T_DEEP | 4906 | 3791.00 | 1556.00 | 306.00 | 15.22 | 1.22 | -12.78 | 1.59 | [-15,-1] | -9.97 | 5.02 |  | 95.54 | -0.82 | 3.76e+06 | WEAK |
| M10_BASIS_Z_REVERSION_3.0 | 4 | T_DEEP | 4906 | 3791.00 | 1556.00 | 306.00 | 4.71 | -9.29 | -23.29 | 0.38 | [-26,4] | -10.01 | 5.02 |  | 1722.25 | -0.53 | 1.5e+07 | DEAD |
| M10_BASIS_Z_REVERSION_3.0 | 12 | T_DEEP | 4898 | 3783.00 | 1555.00 | 306.00 | -1.58 | -15.58 | -29.58 | -0.32 | [-44,9] | -42.15 | 5.03 |  | inf | -0.51 | 4.51e+07 | DEAD |
| M11_FLOW_PRICE_DIVERGENCE | 1 | T_DEEP | 49 |  |  |  |  |  |  |  |  |  |  |  |  |  |  | DATA_LIMITED |
| M11_FLOW_PRICE_DIVERGENCE | 4 | T_DEEP | 49 |  |  |  |  |  |  |  |  |  |  |  |  |  |  | DATA_LIMITED |
| M11_FLOW_PRICE_DIVERGENCE | 12 | T_DEEP | 49 |  |  |  |  |  |  |  |  |  |  |  |  |  |  | DATA_LIMITED |
| M12_XS_RESID_REVERSAL_1H | 1 | T_DEEP | 1702 | 1702.00 | 85.00 | 18.00 | -0.29 | -12.07 | -23.85 | 0.13 | [-18,-6] | -12.24 | 0.00 |  | 2485.86 | -2.90 | 1.09e+07 | DEAD |
| M12_XS_RESID_REVERSAL_1H | 4 | T_DEEP | 1702 | 434.00 | 85.00 | 18.00 | 2.61 | -9.31 | -21.24 | 0.27 | [-19,-1] | -11.00 | 0.00 |  | 954.48 | -1.52 | 4.36e+07 | DEAD |
| M12_XS_RESID_REVERSAL_1H | 12 | T_DEEP | 1702 | 154.00 | 85.00 | 18.00 | 4.09 | -8.19 | -20.47 | 0.47 | [-20,5] | -11.30 | 0.00 |  | 388.75 | -0.88 | 1.31e+08 | DEAD |
| M13_XS_FLOW_REVERSAL_1H | 1 | T_DEEP | 1702 | 1702.00 | 85.00 | 18.00 | -1.12 | -13.29 | -25.46 | -1.10 | [-16,-11] | -14.61 | 0.00 |  | inf | -5.56 | 5.31e+06 | DEAD |
| M13_XS_FLOW_REVERSAL_1H | 4 | T_DEEP | 1702 | 434.00 | 85.00 | 18.00 | -3.26 | -15.67 | -28.09 | -1.24 | [-27,-11] | -16.78 | 0.00 |  | inf | -2.31 | 2.12e+07 | DEAD |
| M13_XS_FLOW_REVERSAL_1H | 12 | T_DEEP | 1702 | 154.00 | 85.00 | 18.00 | -2.04 | -14.43 | -26.83 | -0.71 | [-26,-7] | -18.39 | 0.00 |  | inf | -1.44 | 6.37e+07 | DEAD |
| M14_XS_OI_SHOCK | 1 | T_DEEP | 613 | 613.00 | 45.00 | 8.00 | 0.09 | -11.37 | -22.84 | 0.92 | [-16,-2] |  | 0.00 |  | 38.59 | -2.59 | 8.69e+06 | DEAD |
| M14_XS_OI_SHOCK | 4 | T_DEEP | 613 | 204.00 | 45.00 | 8.00 | -5.05 | -17.16 | -29.26 | -1.45 | [-35,-9] |  | 0.00 |  | inf | -2.67 | 3.48e+07 | DEAD |
| M14_XS_OI_SHOCK | 12 | T_DEEP | 613 | 79.00 | 45.00 | 8.00 | -8.94 | -21.48 | -34.03 | -1.45 | [-46,-12] |  | 0.00 |  | inf | -3.07 | 1.04e+08 | DEAD |
| M15_XS_VOLSHOCK | 1 | T_DEEP | 1702 | 1702.00 | 85.00 | 18.00 | -0.55 | -8.56 | -16.56 | -0.27 | [-12,-5] | -20.04 | 0.00 |  | inf | -2.50 | 4.69e+06 | DEAD |
| M15_XS_VOLSHOCK | 4 | T_DEEP | 1702 | 434.00 | 85.00 | 18.00 | -3.77 | -14.29 | -24.81 | -1.05 | [-28,-6] | -41.92 | 0.00 |  | inf | -1.85 | 1.87e+07 | DEAD |
| M15_XS_VOLSHOCK | 12 | T_DEEP | 1702 | 154.00 | 85.00 | 18.00 | -7.07 | -19.54 | -32.01 | -0.73 | [-44,2] | -57.06 | 0.00 |  | inf | -1.11 | 5.62e+07 | DEAD |
| M16_XS_BASIS_REVERSAL | 1 | T_DEEP | 1192 | 1192.00 | 59.00 | 16.00 | 1.56 | -9.56 | -20.69 | 0.46 | [-13,-7] | -16.47 | 0.00 |  | 392.23 | -3.86 | 7.47e+06 | DEAD |
| M16_XS_BASIS_REVERSAL | 4 | T_DEEP | 1192 | 302.00 | 59.00 | 16.00 | -2.01 | -14.02 | -26.03 | -0.77 | [-26,-7] | -19.19 | 0.00 |  | inf | -2.18 | 2.99e+07 | DEAD |
| M16_XS_BASIS_REVERSAL | 12 | T_DEEP | 1192 | 104.00 | 59.00 | 16.00 | -14.36 | -27.08 | -39.80 | -2.15 | [-46,-14] | -66.81 | 0.00 |  | inf | -2.45 | 8.96e+07 | DEAD |
| M17_FUNDING_CROWDING_FADE | 1 | T_DEEP | 152433 | 9268.00 | 1773.00 | 294.00 | -0.26 | -14.26 | -28.26 | -0.60 | [-17,-13] | -14.30 | 17.66 |  | inf | -5.63 | 1.9e+06 | DEAD |
| M17_FUNDING_CROWDING_FADE | 4 | T_DEEP | 152424 | 9268.00 | 1773.00 | 294.00 | -1.17 | -15.17 | -29.17 | -0.92 | [-24,-10] | -16.94 | 17.66 |  | inf | -1.87 | 7.61e+06 | DEAD |
| M17_FUNDING_CROWDING_FADE | 12 | T_DEEP | 152396 | 9267.00 | 1773.00 | 294.00 | -7.43 | -21.43 | -35.43 | -1.42 | [-45,-9] | -21.75 | 17.66 |  | inf | -1.16 | 2.28e+07 | DEAD |
| M1_RESID_REVERSION_1H_z1.5 | 1 | T_DEEP | 70576 | 17367.00 | 2137.00 | 313.00 | 2.99 | -11.01 | -25.01 | 3.97 | [-11,-6] | -11.12 | 38.85 |  | 9.53 | -3.05 | 4.04e+06 | DEAD |
| M1_RESID_REVERSION_1H_z1.5 | 4 | T_DEEP | 70563 | 17367.00 | 2137.00 | 313.00 | 1.32 | -12.68 | -26.68 | 1.62 | [-15,-5] | -15.76 | 38.85 |  | 59.00 | -1.80 | 1.61e+07 | DEAD |
| M1_RESID_REVERSION_1H_z1.5 | 12 | T_DEEP | 70514 | 17354.00 | 2137.00 | 313.00 | -4.11 | -18.11 | -32.11 | 0.64 | [-20,-3] | -18.31 | 38.73 |  | 402.42 | -1.09 | 4.84e+07 | DEAD |
| M1_RESID_REVERSION_1H_z2.5 | 1 | T_DEEP | 22164 | 9002.00 | 1991.00 | 311.00 | 5.02 | -8.98 | -22.98 | 3.06 | [-11,0] | -13.91 | 19.17 |  | 17.82 | -0.80 | 6.28e+06 | DEAD |
| M1_RESID_REVERSION_1H_z2.5 | 4 | T_DEEP | 22161 | 9001.00 | 1991.00 | 311.00 | -0.73 | -14.73 | -28.73 | 1.48 | [-17,5] | -15.12 | 19.17 |  | 74.67 | -0.45 | 2.51e+07 | DEAD |
| M1_RESID_REVERSION_1H_z2.5 | 12 | T_DEEP | 22139 | 8992.00 | 1990.00 | 311.00 | -22.39 | -36.39 | -50.39 | -0.28 | [-35,1] | -45.52 | 19.31 |  | inf | -0.79 | 7.54e+07 | DEAD |
| M1_RESID_REVERSION_1H_z4.0 | 1 | T_DEEP | 6471 | 3701.00 | 1493.00 | 300.00 | 7.68 | -6.32 | -20.32 | 1.62 | [-16,7] | -17.33 | 7.86 |  | 71.90 | -0.31 | 9.58e+06 | DEAD |
| M1_RESID_REVERSION_1H_z4.0 | 12 | T_DEEP | 6461 | 3694.00 | 1492.00 | 300.00 | -49.06 | -63.06 | -77.06 | -0.79 | [-74,7] | -79.74 | 7.90 |  | inf | -0.67 | 1.15e+08 | DEAD |
| M2_RESID_CONTINUATION_1H_z1.5 | 1 | T_DEEP | 70576 | 17367.00 | 2137.00 | 313.00 | -2.99 | -16.99 | -30.99 | -3.97 | [-22,-17] | -18.48 | 38.85 |  | inf | -6.68 | 4.04e+06 | DEAD |
| M2_RESID_CONTINUATION_1H_z1.5 | 4 | T_DEEP | 70563 | 17367.00 | 2137.00 | 313.00 | -1.32 | -15.32 | -29.32 | -1.62 | [-23,-13] | -18.37 | 38.85 |  | inf | -3.26 | 1.61e+07 | DEAD |
| M2_RESID_CONTINUATION_1H_z1.5 | 12 | T_DEEP | 70514 | 17354.00 | 2137.00 | 313.00 | 4.11 | -9.89 | -23.89 | -0.64 | [-25,-8] | -14.09 | 38.73 |  | inf | -1.65 | 4.84e+07 | DEAD |
| M2_RESID_CONTINUATION_1H_z2.5 | 1 | T_DEEP | 22164 | 9002.00 | 1991.00 | 311.00 | -5.02 | -19.02 | -33.02 | -3.06 | [-28,-17] | -20.94 | 19.17 |  | inf | -3.45 | 6.28e+06 | DEAD |
| M2_RESID_CONTINUATION_1H_z2.5 | 4 | T_DEEP | 22161 | 9001.00 | 1991.00 | 311.00 | 0.73 | -13.27 | -27.27 | -1.48 | [-33,-11] | -18.01 | 19.17 |  | inf | -1.75 | 2.51e+07 | DEAD |
| M2_RESID_CONTINUATION_1H_z2.5 | 12 | T_DEEP | 22139 | 8992.00 | 1990.00 | 311.00 | 22.39 | 8.39 | -5.61 | 0.28 | [-29,7] | 0.14 | 19.31 |  | 2133.25 | -0.55 | 7.54e+07 | WEAK |
| M2_RESID_CONTINUATION_1H_z4.0 | 1 | T_DEEP | 6471 | 3701.00 | 1493.00 | 300.00 | -7.68 | -21.68 | -35.68 | -1.62 | [-35,-12] | -25.19 | 7.86 |  | inf | -1.63 | 9.58e+06 | DEAD |
| M2_RESID_CONTINUATION_1H_z4.0 | 4 | T_DEEP | 6471 | 3701.00 | 1493.00 | 300.00 | -3.80 | -17.80 | -31.80 | -1.63 | [-53,-9] | -31.90 | 7.86 |  | inf | -1.20 | 3.83e+07 | DEAD |
| M3_RESID_REVERSION_4H_z1.5 | 1 | T_DEEP | 73231 | 14188.00 | 2098.00 | 313.00 | 1.92 | -12.08 | -26.08 | 4.87 | [-10,-4] | -13.78 | 32.43 |  | 6.69 | -2.13 | 3.89e+06 | DEAD |
| M3_RESID_REVERSION_4H_z1.5 | 4 | T_DEEP | 73223 | 14187.00 | 2098.00 | 313.00 | 1.41 | -12.59 | -26.59 | 2.36 | [-13,2] | -16.19 | 32.43 |  | 28.98 | -0.63 | 1.56e+07 | DEAD |
| M3_RESID_REVERSION_4H_z1.5 | 12 | T_DEEP | 73164 | 14176.00 | 2097.00 | 313.00 | -14.40 | -28.40 | -42.40 | -0.26 | [-30,-3] | -37.15 | 32.50 |  | inf | -0.99 | 4.67e+07 | DEAD |
| M3_RESID_REVERSION_4H_z2.5 | 12 | T_DEEP | 23198 | 6335.00 | 1845.00 | 306.00 | -40.38 | -54.38 | -68.38 | -0.16 | [-48,12] | -71.39 | 13.88 |  | inf | -0.47 | 7.19e+07 | DEAD |
| M4_FLOW_IMBALANCE_FADE_0.3 | 1 | T_DEEP | 1464 | 751.00 | 569.00 | 221.00 | 1.18 | -12.82 | -26.82 | 0.69 | [-18,-6] | -13.51 | 5.44 |  | 197.79 | -2.43 | 1.23e+06 | DEAD |
| M4_FLOW_IMBALANCE_FADE_0.3 | 4 | T_DEEP | 1464 | 751.00 | 569.00 | 221.00 | -0.60 | -14.60 | -28.60 | -0.08 | [-25,-3] | -14.94 | 5.44 |  | inf | -1.56 | 4.94e+06 | DEAD |
| M4_FLOW_IMBALANCE_FADE_0.3 | 12 | T_DEEP | 1462 | 749.00 | 569.00 | 221.00 | 4.83 | -9.17 | -23.17 | -0.17 | [-33,4] | -21.07 | 5.41 |  | inf | -0.92 | 1.48e+07 | DEAD |
| M4_FLOW_IMBALANCE_FADE_0.5 | 1 | T_DEEP | 437 | 39.00 | 40.00 | 15.00 | -4.33 | -18.33 | -32.33 | -1.45 | [-49,-9] | -18.56 | 0.38 |  | inf | -1.96 | 1.16e+06 | DEAD |
| M4_FLOW_IMBALANCE_FADE_0.5 | 4 | T_DEEP | 437 | 39.00 | 40.00 | 15.00 | -3.81 | -17.81 | -31.81 | -0.68 | [-79,19] | -19.80 | 0.38 |  | inf | -0.97 | 4.62e+06 | DEAD |
| M5_FLOW_IMBALANCE_FOLLOW_0.3 | 1 | T_DEEP | 1464 | 751.00 | 569.00 | 221.00 | -1.18 | -15.18 | -29.18 | -0.69 | [-22,-10] | -15.40 | 5.44 |  | inf | -3.23 | 1.23e+06 | DEAD |
| M5_FLOW_IMBALANCE_FOLLOW_0.3 | 4 | T_DEEP | 1464 | 751.00 | 569.00 | 221.00 | 0.60 | -13.40 | -27.40 | 0.08 | [-25,-3] | -13.87 | 5.44 |  | 15742.32 | -1.47 | 4.94e+06 | DEAD |
| M5_FLOW_IMBALANCE_FOLLOW_0.3 | 12 | T_DEEP | 1462 | 749.00 | 569.00 | 221.00 | -4.83 | -18.83 | -32.83 | 0.17 | [-32,5] | -20.51 | 5.41 |  | 3565.06 | -0.73 | 1.48e+07 | DEAD |
| M5_FLOW_IMBALANCE_FOLLOW_0.5 | 12 | T_DEEP | 437 | 39.00 | 40.00 | 15.00 | -34.09 | -48.09 | -62.09 | -0.58 | [-154,40] | -49.24 | 0.38 |  | inf | -0.62 | 1.39e+07 | DEAD |
| M6_OI_BUILD_FADE_0.01 | 1 | T_DEEP | 22262 | 9257.00 | 1681.00 | 244.00 | -2.35 | -16.35 | -30.35 | 1.29 | [-16,-5] | -16.55 | 20.46 |  | 93.26 | -1.61 | 4.26e+06 | DEAD |
| M6_OI_BUILD_FADE_0.01 | 4 | T_DEEP | 22261 | 9257.00 | 1681.00 | 244.00 | -10.63 | -24.63 | -38.63 | 0.89 | [-20,0] | -25.50 | 20.46 |  | 170.92 | -0.83 | 1.7e+07 | DEAD |
| M6_OI_BUILD_FADE_0.01 | 12 | T_DEEP | 22247 | 9249.00 | 1680.00 | 244.00 | -29.19 | -43.19 | -57.19 | 0.10 | [-37,8] | -56.61 | 20.53 |  | 14029.63 | -0.56 | 5.11e+07 | DEAD |
| M6_OI_BUILD_FADE_0.02 | 1 | T_DEEP | 11174 | 5568.00 | 1590.00 | 244.00 | -4.49 | -18.49 | -32.49 | 1.99 | [-13,9] | -21.30 | 11.43 |  | 48.02 | -0.20 | 5.48e+06 | DEAD |
| M7_OI_FLUSH_BOUNCE_0.01 | 1 | T_DEEP | 22511 | 9513.00 | 1674.00 | 244.00 | 0.84 | -13.16 | -27.16 | 1.49 | [-15,-7] | -15.50 | 23.64 |  | 71.03 | -2.35 | 3.79e+06 | DEAD |
| M7_OI_FLUSH_BOUNCE_0.01 | 4 | T_DEEP | 22506 | 9513.00 | 1674.00 | 244.00 | 3.17 | -10.83 | -24.83 | 1.95 | [-13,7] | -11.19 | 23.64 |  | 39.74 | -0.30 | 1.52e+07 | DEAD |
| M7_OI_FLUSH_BOUNCE_0.01 | 12 | T_DEEP | 22475 | 9503.00 | 1674.00 | 244.00 | 0.58 | -13.42 | -27.42 | 1.08 | [-21,13] | -17.51 | 23.57 |  | 127.91 | -0.28 | 4.55e+07 | DEAD |
| M7_OI_FLUSH_BOUNCE_0.02 | 1 | T_DEEP | 10174 | 5483.00 | 1555.00 | 243.00 | 1.80 | -12.20 | -26.20 | 1.72 | [-15,-2] | -15.57 | 12.17 |  | 60.37 | -1.05 | 4.82e+06 | DEAD |
| M7_OI_FLUSH_BOUNCE_0.02 | 4 | T_DEEP | 10172 | 5482.00 | 1555.00 | 243.00 | 1.54 | -12.46 | -26.46 | 1.06 | [-21,10] | -18.09 | 12.17 |  | 128.14 | -0.35 | 1.93e+07 | DEAD |
| M7_OI_FLUSH_BOUNCE_0.02 | 12 | T_DEEP | 10154 | 5472.00 | 1554.00 | 243.00 | -0.12 | -14.12 | -28.12 | 0.92 | [-27,24] | -23.13 | 12.27 |  | 154.95 | -0.07 | 5.78e+07 | DEAD |
| M8_VOLSHOCK_REVERSION_3.0x | 1 | T_DEEP | 8074 | 5452.00 | 1742.00 | 309.00 | 5.83 | -8.17 | -22.17 | 3.31 | [-8,9] | -8.56 | 11.64 |  | 17.32 | -0.01 | 9.85e+06 | DEAD |
| M8_VOLSHOCK_REVERSION_3.0x | 12 | T_DEEP | 8065 | 5445.00 | 1742.00 | 309.00 | 11.83 | -2.17 | -16.17 | 0.73 | [-32,20] | -12.31 | 11.64 |  | 413.47 | -0.12 | 1.18e+08 | WEAK |
| M9_VOLSHOCK_CONTINUATION_3.0x | 1 | T_DEEP | 8074 | 5452.00 | 1742.00 | 309.00 | -5.83 | -19.83 | -33.83 | -3.31 | [-37,-20] | -23.27 | 11.64 |  | inf | -2.70 | 9.85e+06 | DEAD |
| M9_VOLSHOCK_CONTINUATION_3.0x | 4 | T_DEEP | 8074 | 5452.00 | 1742.00 | 309.00 | -15.32 | -29.32 | -43.32 | -2.59 | [-44,-18] | -33.68 | 11.64 |  | inf | -1.82 | 3.94e+07 | DEAD |
| M9_VOLSHOCK_CONTINUATION_3.0x | 12 | T_DEEP | 8065 | 5445.00 | 1742.00 | 309.00 | -11.83 | -25.83 | -39.83 | -0.73 | [-48,4] | -26.81 | 11.64 |  | inf | -0.67 | 1.18e+08 | DEAD |
| M9_VOLSHOCK_CONTINUATION_6.0x | 1 | T_DEEP | 919 | 811.00 | 548.00 | 248.00 | -5.63 | -19.63 | -33.63 | -1.01 | [-68,-6] | -29.05 | 1.58 |  | inf | -0.63 | 1.7e+07 | DEAD |
| M9_VOLSHOCK_CONTINUATION_6.0x | 4 | T_DEEP | 919 | 811.00 | 548.00 | 248.00 | -39.48 | -53.48 | -67.48 | -1.71 | [-110,-26] | -54.20 | 1.58 |  | inf | -0.70 | 6.79e+07 | DEAD |
| M9_VOLSHOCK_CONTINUATION_6.0x | 12 | T_DEEP | 918 | 810.00 | 548.00 | 248.00 | -33.38 | -47.38 | -61.38 | -1.06 | [-117,-2] | -50.35 | 1.58 |  | inf | -0.49 | 2.04e+08 | DEAD |
| M10_BASIS_Z_REVERSION_2.0 | 1 | T_LIQ | 196633 | 83427.00 | 2309.00 | 331.00 | 2.56 | -11.44 | -25.44 | 4.14 | [-13,-10] | -12.44 | 173.41 |  | 11.47 | -8.28 | 380471.35 | DEAD |
| M10_BASIS_Z_REVERSION_2.0 | 4 | T_LIQ | 196618 | 83421.00 | 2309.00 | 331.00 | 2.76 | -11.24 | -25.24 | 4.27 | [-12,-8] | -11.48 | 173.29 |  | 9.29 | -4.09 | 1.52e+06 | DEAD |
| M10_BASIS_Z_REVERSION_2.0 | 12 | T_LIQ | 196547 | 83393.00 | 2309.00 | 331.00 | 4.88 | -9.12 | -23.12 | 2.81 | [-13,-6] | -12.03 | 173.02 |  | 24.58 | -2.25 | 4.56e+06 | DEAD |
| M10_BASIS_Z_REVERSION_3.0 | 1 | T_LIQ | 26484 | 20886.00 | 2210.00 | 331.00 | 8.48 | -5.52 | -19.52 | 0.26 | [-17,-9] | -12.42 | 32.47 |  | 2498.16 | -3.04 | 539723.65 | DEAD |
| M10_BASIS_Z_REVERSION_3.0 | 4 | T_LIQ | 26483 | 20885.00 | 2210.00 | 331.00 | 6.57 | -7.43 | -21.43 | -0.35 | [-22,-8] | -15.37 | 32.43 |  | inf | -1.95 | 2.16e+06 | DEAD |
| M10_BASIS_Z_REVERSION_3.0 | 12 | T_LIQ | 26455 | 20858.00 | 2209.00 | 331.00 | 3.38 | -10.62 | -24.62 | -1.12 | [-30,-9] | -21.96 | 32.54 |  | inf | -1.59 | 6.47e+06 | DEAD |
| M11_FLOW_PRICE_DIVERGENCE | 1 | T_LIQ | 518 | 326.00 | 299.00 | 144.00 | -3.91 | -17.91 | -31.91 | 0.60 | [-24,8] | -22.11 | 1.78 |  | 356.47 | -0.60 | 507857.55 | DEAD |
| M11_FLOW_PRICE_DIVERGENCE | 4 | T_LIQ | 518 | 326.00 | 299.00 | 144.00 | 2.51 | -11.49 | -25.49 | 0.29 | [-38,18] | -12.55 | 1.78 |  | 1194.13 | -0.40 | 2.03e+06 | DEAD |
| M12_XS_RESID_REVERSAL_1H | 1 | T_LIQ | 48828 | 48828.00 | 2035.00 | 291.00 | 0.86 | -10.93 | -22.71 | 2.99 | [-12,-10] | -11.50 | 168.89 |  | 13.69 | -18.95 | 6.6e+06 | DEAD |
| M12_XS_RESID_REVERSAL_1H | 4 | T_LIQ | 48824 | 12206.00 | 2035.00 | 291.00 | -0.48 | -12.42 | -24.36 | -0.89 | [-13,-12] | -13.11 | 42.19 |  | inf | -11.52 | 2.64e+07 | DEAD |
| M12_XS_RESID_REVERSAL_1H | 12 | T_LIQ | 48815 | 4068.00 | 2035.00 | 291.00 | -1.69 | -13.78 | -25.88 | -1.88 | [-16,-12] | -14.28 | 14.04 |  | inf | -7.43 | 7.91e+07 | DEAD |
| M13_XS_FLOW_REVERSAL_1H | 1 | T_LIQ | 48829 | 48829.00 | 2035.00 | 291.00 | 0.48 | -11.35 | -23.18 | 3.06 | [-12,-11] | -11.41 | 168.89 |  | 14.76 | -34.60 | 2.4e+06 | DEAD |
| M13_XS_FLOW_REVERSAL_1H | 4 | T_LIQ | 48825 | 12207.00 | 2035.00 | 291.00 | -0.24 | -12.31 | -24.38 | -0.76 | [-13,-12] | -12.59 | 42.19 |  | inf | -17.75 | 9.61e+06 | DEAD |
| M13_XS_FLOW_REVERSAL_1H | 12 | T_LIQ | 48813 | 4069.00 | 2035.00 | 291.00 | -0.98 | -13.19 | -25.40 | -1.58 | [-14,-12] | -13.94 | 14.04 |  | inf | -9.55 | 2.88e+07 | DEAD |
| M14_XS_OI_SHOCK | 1 | T_LIQ | 40855 | 40855.00 | 1704.00 | 244.00 | 1.15 | -9.47 | -20.09 | 4.40 | [-10,-9] | -9.78 | 168.82 |  | 7.09 | -17.24 | 5.12e+06 | DEAD |
| M14_XS_OI_SHOCK | 4 | T_LIQ | 40852 | 10217.00 | 1704.00 | 244.00 | 2.31 | -9.25 | -20.81 | 3.64 | [-10,-8] | -10.06 | 42.19 |  | 9.20 | -7.38 | 2.05e+07 | DEAD |
| M14_XS_OI_SHOCK | 12 | T_LIQ | 40842 | 3406.00 | 1704.00 | 244.00 | 4.49 | -7.39 | -19.28 | 3.63 | [-10,-5] | -8.93 | 14.04 |  | 8.89 | -3.06 | 6.14e+07 | DEAD |
| M15_XS_VOLSHOCK | 1 | T_LIQ | 48829 | 48829.00 | 2035.00 | 291.00 | -0.38 | -8.36 | -16.34 | -1.60 | [-9,-8] | -8.49 | 168.89 |  | inf | -15.34 | 2.51e+06 | DEAD |
| M15_XS_VOLSHOCK | 4 | T_LIQ | 48826 | 12207.00 | 2035.00 | 291.00 | -0.77 | -11.06 | -21.35 | -1.16 | [-12,-10] | -11.61 | 42.19 |  | inf | -7.24 | 1e+07 | DEAD |
| M15_XS_VOLSHOCK | 12 | T_LIQ | 48817 | 4069.00 | 2035.00 | 291.00 | -0.40 | -12.37 | -24.34 | -0.29 | [-15,-9] | -14.45 | 14.04 |  | inf | -3.50 | 3.01e+07 | DEAD |
| M16_XS_BASIS_REVERSAL | 1 | T_LIQ | 48728 | 48728.00 | 2035.00 | 291.00 | 0.39 | -11.17 | -22.72 | 2.26 | [-12,-11] | -11.35 | 165.71 |  | 25.25 | -32.96 | 4.41e+06 | DEAD |
| M16_XS_BASIS_REVERSAL | 4 | T_LIQ | 48725 | 12188.00 | 2035.00 | 291.00 | 0.44 | -11.79 | -24.02 | 1.13 | [-12,-11] | -12.38 | 41.57 |  | 109.03 | -14.85 | 1.76e+07 | DEAD |
| M16_XS_BASIS_REVERSAL | 12 | T_LIQ | 48715 | 4065.00 | 2035.00 | 291.00 | 0.75 | -11.91 | -24.56 | 0.97 | [-13,-10] | -12.58 | 13.88 |  | 152.36 | -8.17 | 5.29e+07 | DEAD |
| M17_FUNDING_CROWDING_FADE | 1 | T_LIQ | 920093 | 53657.00 | 2109.00 | 322.00 | 0.50 | -13.50 | -27.50 | 1.76 | [-14,-12] | -13.76 | 148.83 |  | 56.85 | -12.54 | 308399.98 | DEAD |
| M17_FUNDING_CROWDING_FADE | 4 | T_LIQ | 920009 | 53656.00 | 2109.00 | 322.00 | 1.57 | -12.43 | -26.43 | 1.16 | [-15,-9] | -13.44 | 148.79 |  | 130.56 | -3.00 | 1.23e+06 | DEAD |
| M17_FUNDING_CROWDING_FADE | 12 | T_LIQ | 919788 | 53641.00 | 2109.00 | 322.00 | 2.84 | -11.16 | -25.16 | 0.84 | [-19,0] | -13.39 | 148.59 |  | 235.13 | -0.85 | 3.7e+06 | DEAD |
| M1_RESID_REVERSION_1H_z1.5 | 1 | T_LIQ | 371635 | 92662.00 | 2297.00 | 330.00 | 2.14 | -11.86 | -25.86 | 6.33 | [-11,-9] | -12.13 | 254.88 |  | 4.16 | -7.44 | 746459.25 | DEAD |
| M1_RESID_REVERSION_1H_z1.5 | 4 | T_LIQ | 371589 | 92661.00 | 2297.00 | 330.00 | 2.73 | -11.27 | -25.27 | 5.83 | [-10,-6] | -11.66 | 254.84 |  | 5.28 | -3.49 | 2.99e+06 | DEAD |
| M1_RESID_REVERSION_1H_z1.5 | 12 | T_LIQ | 371390 | 92626.00 | 2297.00 | 330.00 | 2.59 | -11.41 | -25.41 | 3.42 | [-12,-5] | -11.64 | 254.29 |  | 15.97 | -2.06 | 8.96e+06 | DEAD |
| M1_RESID_REVERSION_1H_z2.5 | 1 | T_LIQ | 112185 | 45700.00 | 2271.00 | 330.00 | 3.75 | -10.25 | -24.25 | 5.55 | [-9,-4] | -10.66 | 121.68 |  | 5.77 | -2.32 | 1.29e+06 | DEAD |
| M1_RESID_REVERSION_1H_z2.5 | 4 | T_LIQ | 112172 | 45694.00 | 2271.00 | 330.00 | 3.95 | -10.05 | -24.05 | 4.94 | [-8,1] | -10.70 | 121.61 |  | 7.35 | -0.70 | 5.16e+06 | DEAD |
| M1_RESID_REVERSION_1H_z2.5 | 12 | T_LIQ | 112078 | 45667.00 | 2271.00 | 330.00 | 2.28 | -11.72 | -25.72 | 2.84 | [-11,2] | -14.78 | 121.37 |  | 21.41 | -0.53 | 1.55e+07 | DEAD |
| M1_RESID_REVERSION_1H_z4.0 | 1 | T_LIQ | 32125 | 18299.00 | 2173.00 | 328.00 | 7.85 | -6.15 | -20.15 | 3.72 | [-8,4] | -13.11 | 48.88 |  | 15.08 | -0.27 | 2.27e+06 | DEAD |
| M2_RESID_CONTINUATION_1H_z1.5 | 1 | T_LIQ | 371635 | 92662.00 | 2297.00 | 330.00 | -2.14 | -16.14 | -30.14 | -6.33 | [-19,-17] | -16.49 | 254.88 |  | inf | -12.93 | 746459.25 | DEAD |
| M2_RESID_CONTINUATION_1H_z1.5 | 4 | T_LIQ | 371589 | 92661.00 | 2297.00 | 330.00 | -2.73 | -16.73 | -30.73 | -5.83 | [-22,-18] | -17.61 | 254.84 |  | inf | -8.37 | 2.99e+06 | DEAD |
| M2_RESID_CONTINUATION_1H_z1.5 | 12 | T_LIQ | 371390 | 92626.00 | 2297.00 | 330.00 | -2.59 | -16.59 | -30.59 | -3.42 | [-23,-16] | -18.47 | 254.29 |  | inf | -4.86 | 8.96e+06 | DEAD |
| M2_RESID_CONTINUATION_1H_z2.5 | 1 | T_LIQ | 112185 | 45700.00 | 2271.00 | 330.00 | -3.75 | -17.75 | -31.75 | -5.55 | [-24,-19] | -19.23 | 121.68 |  | inf | -6.98 | 1.29e+06 | DEAD |
| M2_RESID_CONTINUATION_1H_z2.5 | 4 | T_LIQ | 112172 | 45694.00 | 2271.00 | 330.00 | -3.95 | -17.95 | -31.95 | -4.94 | [-29,-20] | -20.91 | 121.61 |  | inf | -4.83 | 5.16e+06 | DEAD |
| M2_RESID_CONTINUATION_1H_z2.5 | 12 | T_LIQ | 112078 | 45667.00 | 2271.00 | 330.00 | -2.28 | -16.28 | -30.28 | -2.84 | [-30,-17] | -22.85 | 121.37 |  | inf | -2.95 | 1.55e+07 | DEAD |
| M2_RESID_CONTINUATION_1H_z4.0 | 1 | T_LIQ | 32125 | 18299.00 | 2173.00 | 328.00 | -7.85 | -21.85 | -35.85 | -3.72 | [-32,-20] | -23.79 | 48.88 |  | inf | -3.15 | 2.27e+06 | DEAD |
| M2_RESID_CONTINUATION_1H_z4.0 | 4 | T_LIQ | 32121 | 18297.00 | 2173.00 | 328.00 | -12.53 | -26.53 | -40.53 | -3.58 | [-41,-22] | -33.43 | 48.84 |  | inf | -2.68 | 9.08e+06 | DEAD |
| M2_RESID_CONTINUATION_1H_z4.0 | 12 | T_LIQ | 32072 | 18271.00 | 2173.00 | 328.00 | -11.42 | -25.42 | -39.42 | -2.58 | [-47,-17] | -38.01 | 48.69 |  | inf | -1.86 | 2.73e+07 | DEAD |
| M3_RESID_REVERSION_4H_z1.5 | 1 | T_LIQ | 381944 | 74983.00 | 2287.00 | 330.00 | 1.61 | -12.39 | -26.39 | 7.36 | [-11,-9] | -12.56 | 210.51 |  | 3.14 | -7.35 | 728107.78 | DEAD |
| M3_RESID_REVERSION_4H_z1.5 | 4 | T_LIQ | 381896 | 74976.00 | 2287.00 | 330.00 | 2.75 | -11.25 | -25.25 | 4.34 | [-10,-5] | -11.51 | 210.39 |  | 9.84 | -2.21 | 2.91e+06 | DEAD |
| M3_RESID_REVERSION_4H_z1.5 | 12 | T_LIQ | 381679 | 74946.00 | 2287.00 | 330.00 | 1.75 | -12.25 | -26.25 | 1.37 | [-16,-5] | -14.61 | 210.04 |  | 102.41 | -1.47 | 8.73e+06 | DEAD |
| M3_RESID_REVERSION_4H_z2.5 | 1 | T_LIQ | 116614 | 31871.00 | 2228.00 | 328.00 | 3.37 | -10.63 | -24.63 | 7.09 | [-7,-2] | -10.95 | 88.39 |  | 3.68 | -1.29 | 1.28e+06 | DEAD |
| M3_RESID_REVERSION_4H_z2.5 | 4 | T_LIQ | 116598 | 31866.00 | 2228.00 | 328.00 | 4.42 | -9.58 | -23.58 | 4.59 | [-6,6] | -10.09 | 88.36 |  | 8.95 | -0.01 | 5.11e+06 | DEAD |
| M3_RESID_REVERSION_4H_z2.5 | 12 | T_LIQ | 116503 | 31843.00 | 2228.00 | 328.00 | 3.35 | -10.65 | -24.65 | 2.27 | [-13,11] | -18.81 | 88.16 |  | 35.62 | -0.04 | 1.53e+07 | DEAD |
| M4_FLOW_IMBALANCE_FADE_0.3 | 1 | T_LIQ | 30406 | 16356.00 | 2222.00 | 330.00 | -0.59 | -14.59 | -28.59 | 0.78 | [-15,-12] | -14.77 | 90.49 |  | 308.44 | -6.24 | 178698.28 | DEAD |
| M4_FLOW_IMBALANCE_FADE_0.3 | 4 | T_LIQ | 30401 | 16354.00 | 2222.00 | 330.00 | -2.81 | -16.81 | -30.81 | -0.19 | [-18,-11] | -17.22 | 90.46 |  | inf | -3.46 | 714834.35 | DEAD |
| M4_FLOW_IMBALANCE_FADE_0.3 | 12 | T_LIQ | 30390 | 16345.00 | 2222.00 | 330.00 | -8.16 | -22.16 | -36.16 | -1.30 | [-24,-12] | -22.94 | 90.38 |  | inf | -2.37 | 2.14e+06 | DEAD |
| M4_FLOW_IMBALANCE_FADE_0.5 | 1 | T_LIQ | 3510 | 664.00 | 517.00 | 183.00 | -3.76 | -17.76 | -31.76 | 0.79 | [-18,-1] | -17.90 | 5.30 |  | 132.56 | -1.58 | 487974.78 | DEAD |
| M4_FLOW_IMBALANCE_FADE_0.5 | 4 | T_LIQ | 3510 | 664.00 | 517.00 | 183.00 | -8.70 | -22.70 | -36.70 | 0.67 | [-24,11] | -23.37 | 5.30 |  | 198.52 | -0.52 | 1.95e+06 | DEAD |
| M4_FLOW_IMBALANCE_FADE_0.5 | 12 | T_LIQ | 3510 | 664.00 | 517.00 | 183.00 | -12.29 | -26.29 | -40.29 | 0.48 | [-35,22] | -27.18 | 5.30 |  | 366.53 | -0.30 | 5.86e+06 | DEAD |
| M5_FLOW_IMBALANCE_FOLLOW_0.3 | 1 | T_LIQ | 30406 | 16356.00 | 2222.00 | 330.00 | 0.59 | -13.41 | -27.41 | -0.78 | [-16,-13] | -13.79 | 90.49 |  | inf | -6.88 | 178698.28 | DEAD |
| M5_FLOW_IMBALANCE_FOLLOW_0.3 | 4 | T_LIQ | 30401 | 16354.00 | 2222.00 | 330.00 | 2.81 | -11.19 | -25.19 | 0.19 | [-17,-10] | -12.85 | 90.46 |  | 5706.69 | -3.31 | 714834.35 | DEAD |
| M5_FLOW_IMBALANCE_FOLLOW_0.3 | 12 | T_LIQ | 30390 | 16345.00 | 2222.00 | 330.00 | 8.16 | -5.84 | -19.84 | 1.30 | [-16,-4] | -6.70 | 90.38 |  | 114.77 | -1.32 | 2.14e+06 | DEAD |
| M5_FLOW_IMBALANCE_FOLLOW_0.5 | 1 | T_LIQ | 3510 | 664.00 | 517.00 | 183.00 | 3.76 | -10.24 | -24.24 | -0.79 | [-27,-10] | -10.27 | 5.30 |  | inf | -2.55 | 487974.78 | DEAD |
| M5_FLOW_IMBALANCE_FOLLOW_0.5 | 4 | T_LIQ | 3510 | 664.00 | 517.00 | 183.00 | 8.70 | -5.30 | -19.30 | -0.67 | [-39,-4] | -5.37 | 5.30 |  | inf | -1.31 | 1.95e+06 | DEAD |
| M5_FLOW_IMBALANCE_FOLLOW_0.5 | 12 | T_LIQ | 3510 | 664.00 | 517.00 | 183.00 | 12.29 | -1.71 | -15.71 | -0.48 | [-50,7] | -1.90 | 5.30 |  | inf | -0.89 | 5.86e+06 | WEAK |
| M6_OI_BUILD_FADE_0.01 | 1 | T_LIQ | 121103 | 50145.00 | 1704.00 | 244.00 | 1.00 | -13.00 | -27.00 | 1.88 | [-14,-10] | -13.07 | 131.91 |  | 35.61 | -5.42 | 886471.20 | DEAD |
| M6_OI_BUILD_FADE_0.01 | 4 | T_LIQ | 121089 | 50140.00 | 1704.00 | 244.00 | 1.88 | -12.12 | -26.12 | 2.13 | [-14,-6] | -12.49 | 131.87 |  | 30.78 | -2.02 | 3.55e+06 | DEAD |
| M6_OI_BUILD_FADE_0.01 | 12 | T_LIQ | 121043 | 50122.00 | 1704.00 | 244.00 | 3.81 | -10.19 | -24.19 | 2.93 | [-11,4] | -13.28 | 131.60 |  | 15.51 | -0.25 | 1.06e+07 | DEAD |
| M6_OI_BUILD_FADE_0.02 | 1 | T_LIQ | 62530 | 30442.00 | 1704.00 | 244.00 | 2.13 | -11.87 | -25.87 | 2.96 | [-12,-6] | -12.04 | 70.66 |  | 17.66 | -2.52 | 1.24e+06 | DEAD |
| M6_OI_BUILD_FADE_0.02 | 4 | T_LIQ | 62524 | 30438.00 | 1704.00 | 244.00 | 2.14 | -11.86 | -25.86 | 2.25 | [-13,-0] | -12.43 | 70.62 |  | 29.99 | -0.86 | 4.96e+06 | DEAD |
| M7_OI_FLUSH_BOUNCE_0.01 | 1 | T_LIQ | 117376 | 50241.00 | 1704.00 | 244.00 | 0.80 | -13.20 | -27.20 | 1.56 | [-14,-11] | -14.41 | 138.33 |  | 60.79 | -6.77 | 727643.62 | DEAD |
| M7_OI_FLUSH_BOUNCE_0.01 | 4 | T_LIQ | 117362 | 50239.00 | 1704.00 | 244.00 | 0.30 | -13.70 | -27.70 | 0.98 | [-16,-9] | -15.26 | 138.29 |  | 143.67 | -3.59 | 2.91e+06 | DEAD |
| M7_OI_FLUSH_BOUNCE_0.01 | 12 | T_LIQ | 117270 | 50205.00 | 1704.00 | 244.00 | -2.18 | -16.18 | -30.18 | 0.09 | [-20,-8] | -18.00 | 138.02 |  | 13448.90 | -2.25 | 8.73e+06 | DEAD |
| M7_OI_FLUSH_BOUNCE_0.02 | 1 | T_LIQ | 51680 | 28031.00 | 1703.00 | 244.00 | 0.67 | -13.33 | -27.33 | 0.94 | [-16,-10] | -15.61 | 68.60 |  | 166.79 | -3.28 | 983053.10 | DEAD |
| M7_OI_FLUSH_BOUNCE_0.02 | 4 | T_LIQ | 51675 | 28028.00 | 1703.00 | 244.00 | -0.54 | -14.54 | -28.54 | 0.07 | [-21,-8] | -16.72 | 68.60 |  | 24140.75 | -2.10 | 3.93e+06 | DEAD |
| M7_OI_FLUSH_BOUNCE_0.02 | 12 | T_LIQ | 51625 | 28001.00 | 1703.00 | 244.00 | -3.35 | -17.35 | -31.35 | 0.03 | [-25,-4] | -22.47 | 68.48 |  | 134286.09 | -1.28 | 1.18e+07 | DEAD |
| M8_VOLSHOCK_REVERSION_3.0x | 1 | T_LIQ | 49966 | 31324.00 | 2237.00 | 330.00 | 5.20 | -8.80 | -22.80 | 5.28 | [-7,1] | -9.19 | 84.97 |  | 5.69 | -0.64 | 1.99e+06 | DEAD |
| M9_VOLSHOCK_CONTINUATION_3.0x | 1 | T_LIQ | 49966 | 31324.00 | 2237.00 | 330.00 | -5.20 | -19.20 | -33.20 | -5.28 | [-29,-21] | -21.90 | 84.97 |  | inf | -5.34 | 1.99e+06 | DEAD |
| M9_VOLSHOCK_CONTINUATION_3.0x | 4 | T_LIQ | 49964 | 31323.00 | 2237.00 | 330.00 | -14.84 | -28.84 | -42.84 | -6.15 | [-40,-27] | -30.37 | 84.93 |  | inf | -4.51 | 7.98e+06 | DEAD |
| M9_VOLSHOCK_CONTINUATION_3.0x | 12 | T_LIQ | 49929 | 31298.00 | 2237.00 | 330.00 | -24.93 | -38.93 | -52.93 | -4.23 | [-46,-26] | -39.44 | 84.66 |  | inf | -2.86 | 2.39e+07 | DEAD |
| M9_VOLSHOCK_CONTINUATION_6.0x | 1 | T_LIQ | 8390 | 6826.00 | 1822.00 | 321.00 | -18.86 | -32.86 | -46.86 | -4.95 | [-54,-32] | -39.12 | 18.20 |  | inf | -3.54 | 3.37e+06 | DEAD |
| M9_VOLSHOCK_CONTINUATION_6.0x | 4 | T_LIQ | 8390 | 6826.00 | 1822.00 | 321.00 | -37.89 | -51.89 | -65.89 | -4.85 | [-73,-39] | -55.21 | 18.20 |  | inf | -2.83 | 1.35e+07 | DEAD |
| M9_VOLSHOCK_CONTINUATION_6.0x | 12 | T_LIQ | 8386 | 6822.00 | 1822.00 | 321.00 | -58.31 | -72.31 | -86.31 | -4.35 | [-97,-44] | -72.72 | 18.16 |  | inf | -2.32 | 4.05e+07 | DEAD |
