# W1_CALENDAR_CLOCK — REPORT
**Round 4 (depth), `alpha_hunt_2026-09-03_round4`. Written 2026-09-05 after the session that
started the work was interrupted on 2026-09-03 before any deliverable was produced.**

Axis: calendar structure and market clock — funding clock, geographic sessions, weekend,
month/quarter end, clock x event, clock as a meta-conditioner.

Pre-registration: [`PREREGISTRATION.md`](PREREGISTRATION.md), written before any outcome was
computed. It is quoted, not rewritten. Where the data forced a departure it is flagged
`REFIT` or `DEPARTURE` in the text.

---

## 0. Executive summary

| # | claim | status |
|---|---|---|
| 1 | **The crypto clock is real.** The SIGN of 6h cross-sectional autocorrelation depends on the UTC entry hour: reversion in the Asia hours (h00–h06, up to +19.1bps), continuation in the EU/US hours (h13–h18, down to −15.6bps), flat at the handovers (h07, h19–h21). Arm-vs-arm on paired calendar days it clears a family-wise max-t over 24 arms (**h02 − h15 = +34.7bps, t=8.50**, crit 3.07; 13/24 contrasts significant), the sign is stable TRAIN 2020-23 → TEST 2024-26, and it **survives a 1h entry-gap control at every significant hour** (h02 retains 99% of its size). Not a bid-ask bounce. | **Established** |
| 2 | **It does not pay for its own execution.** Every arm is a 2-leg dollar-neutral basket costing 28bps base / 56bps stress per round trip. At quintiles the largest arm is 19.1bps and 0/24 clear the base cost. Exactly **one cell out of 72** in the depth sweep clears the stress cost (h15, 12h hold, ventiles: 61.9bps gross, +5.9 net at stress) — and it is `UNCONFIRMABLE_IN_HORIZON` on a 5.37y ETA, is the max of a sweep whose hours were picked in-sample, straddles two unmodelled funding settlements that work against it, and holds 5–9 names per leg. | `COST_FRAGILE` / `UNCONFIRMABLE_IN_HORIZON` |
| 3 | **LATE→ASIA (t=5.21) is a bid-ask-bounce artefact.** It decays monotonically as the entry is pushed off the boundary (14.08 → 8.46 → 6.59bps at +0h/+1h/+2h) and does not replicate out of sample (TEST 2024-26: t = 1.14 / 1.04 / 0.98). Honest kill of what was on track to be the find of the worker. | `DEAD` (killed) |
| 4 | **EU→US continuation is the only session arm that survives.** It *strengthens* with the entry gap (−10.66 → −14.44bps at +1h) and replicates out of sample with the sign frozen on TRAIN (TEST +14.20bps, t=2.96), era-stable (−9.6 / −11.3 / −11.4bps across 2020-22 / 23-24 / 25-26). Real; 14.4bps gross against a 28bps 2-leg cost. | `COST_FRAGILE` |
| 5 | **The funding clock is a 2–4bps footprint.** 92 cells; max \|gross\| anywhere **7.52bps**. The most certain number on the axis (t=55.4) is the funding cashflow itself — an accounting identity, not an edge. Arbitraged flat, corroborating the project's standing result on a cut not previously taken. | `COST_FRAGILE` |
| 6 | **No weekend, month-end or quarter-expiry conditioning.** Month-end +8.7bps (t=0.35), quarter-expiry week +10.6bps (t=0.45), Friday→Monday reversal −12.1bps (t=−0.66), all arm-vs-arm. `H_C2` (cascades pay more on weekends) rejected in sign, DiD t=−0.94. | `DEAD` |
| 7 | **Liquidation cascades are session-conditional.** The cascade bounce is **absent in the EU session** and present everywhere else: US − EU = +25.5bps at fwd_8h, t=3.14 (Bonferroni crit for 18 arm tests: 2.99). A screen for the existing `LIQ_CASCADE_REPEAT_V1` shadow alpha, not a standalone alpha. | `PROMISING_NEEDS_VALIDATION` (as a conditioner) |
| 8 | **Two real bugs found and fixed.** One silently voided the entire first clock map — and the void answer was the comfortable one. See §2. | — |

**No mechanism on this axis reaches `VALIDATED_FOR_FORWARD`.** 332 mechanisms were run
(24 void, 21 superseded, 287 live); 113 are classed better than `WEAK`; every one of them
dies on cost, on ETA, or on both.

**Best ETA anywhere on the axis: 3.49 years** (2.92y after the data-end correction of §3),
for the combined two-episodes-per-day clock strategy — the only object here to reach the
Sharpe > 3.2 bar that PREREG §5 pre-computed as the confirmability threshold. It reached it
by **episode frequency, not effect size**, which is the transferable lesson: on every
mechanism in this study, doubling the episode rate helped the ETA more than doubling the bps.

## 1. What is different from the interrupted first pass

The first pass (2026-09-03 10:13–10:25) produced `build_panels.py`, `common.py`, `gate.py`,
`family_a*.py`, `family_b*.py`, `clock_map.py` and their `results_*.json`, then stopped
before writing anything. This pass:

1. **verified** the existing results rather than re-running them — Family A (17 + 75 cells)
   and Family B headline (32 cells) are reused as computed;
2. found and fixed **two bugs** (§2), one of which voids `results_clock_map.json` entirely;
3. rebuilt the 24h clock profile (`clock_map_v2.py`) and its depth pass;
4. rebuilt the arm-vs-arm comparison, which had never actually run;
5. ran families **C, D, E, F**, pre-registered in §6 but never executed;
6. produced `REPORT.md` and `RESULTS.json`.

---

## 2. Two bugs found — both would have produced a confident wrong answer

### 2.1 `clock_map.py`: pandas offset-rolling on a microsecond index → an expanding window

`clock_map.py` built its 6h momentum signal with

```python
h.set_index("hour_end").groupby("symbol")["resid_logret_hour"].rolling("6h", min_periods=6).sum()
```

DuckDB's `.df()` returns `datetime64[us, UTC]`. Under pandas 2.0.3 an **offset** rolling
window on a non-nanosecond datetime index silently degenerates into an **expanding** window:
the `"6h"` offset is compared at nanosecond scale against microsecond index values, so every
window starts at row 0. No warning, no exception.

Verified exactly (`verify_b2_vs_clockmap.py`):

```
roll6.iloc[37]      = -0.003940151664816158
series.iloc[:38].sum() = -0.00394015166481616      <- identical
ns-index reference  = -0.009341195882985738        <- the correct 6h sum
```

So the "6h residual momentum" was in fact **the cumulative residual return since the
symbol's first eligible bar**. Correlation with the true signal: **0.035**.

*How it was caught:* `CLOCKMAP_h13` is, by construction, exactly the `B2_EU_to_US` session
mechanism (signal = the EU session, entry 13:05, exit 21:00). The two disagreed: +0.80bps
(t=0.27) vs −10.66bps (t=−3.53). Two computations of the same trade cannot both be right.
Cross-checking one family against another is what found this; neither result was implausible
on its own.

*Consequence:* every number in `results_clock_map.json` is void — and the void answer was
the *comfortable* one ("all 24 arms DEAD, the clock conditions nothing"). Superseded by
`results_clock_map_v2.json`. Blast radius checked: no other W1 script uses an offset rolling
window (`grep` in `evidence/`); `common.py::eligibility` uses an integer window and is fine.

**Reusable lesson for the project:** with pandas 2.x, `df.rolling("<offset>")` is only safe
on a `datetime64[ns]` index. Any DuckDB → pandas path can hand you `[us]`. `clock_lib.py`
now provides `resid_roll_hours()` (integer window + explicit contiguity guard) and
`assert_roll_ok()` (cross-check against a ns-index reference), and asserts on every use.

### 2.2 `family_b_depth.py`: the arm-vs-arm join key made the clock claim untestable

```python
arm_series[s0] = sp.set_index("B_end")["ret_next_spread"]   # index = 07:00 / 13:00 / 21:00 / 00:00
...
j = pd.concat([arm_series[a], arm_series[b]], axis=1).dropna()
```

Each arm is indexed by *its own* session boundary, so the outer concat overlaps nowhere and
`dropna()` empties the frame. Every comparison in `results_family_b_depth.json` reads
`n_days: 0, diff_bps: NaN, t: NaN`. **The clock claim — the whole point of the axis — was
never actually tested**, and the NaN was not noticed.

Fixed in `family_b_armfix.py` by indexing every arm on its **originating calendar day** (the
day whose session produced the signal; the LATE arm's boundary is `d+24h`, so its floor is
`d+1` and is shifted back). A second correction was needed: the four transitions hold for
6h / 8h / 3h / 7h respectively, so raw bps confound the clock with the holding period. Every
contrast is therefore reported both raw and **per hour held**, and the decisive
horizon-matched contrast is the clock map, where all 24 hours use an identical 6h signal and
8h hold.

---

## 3. Method (as pre-registered)

**Data.** `event_feature_panel` (`/home/qbee/futur-data-v2/data_v2/normalized/`,
`venue=binance`), 312 symbols, 5m bars, compacted to an hourly panel (6.06M eligible rows)
and a funding-settlement event panel. `data/events/liq_cascade_dataset.parquet` (38,141
cascades) for Family E.

> **DEPARTURE from PREREG §2.** The pre-registration states the period runs to 2026-08-31.
> The panel actually ends **2026-07-31** (`max(timestamp) = 2026-07-31 23:55`). This is not
> a free parameter — it mechanically lowers every `event_rate` (measured over
> 2026-03-01..2026-09-01) by a factor 1.196 and therefore *inflates* every ETA by the same
> factor. The headline ETA is left uncorrected because it is the conservative direction;
> `RESULTS.json` carries `eta_forward_confirmation_years_datecorrected` beside it.

**Timezone.** DuckDB renders tz-aware timestamps in local time (+01:00) unless
`SET TimeZone='UTC'`. On a clock axis that single default would shift every bucket by one
hour and silently invalidate the study. Asserted at the top of every script
(`hour(TIMESTAMPTZ '2025-03-01 08:00:00+00') == 8`).

**PIT.** A 5m row labelled `T` covers `[T, T+5m)`; its close is the price at `T+5m`. Every
signal formed through close-time `c_i` is entered at the close of row `i+1`, i.e. **one full
5m bar of implementation lag**. Funding is always the last *settled* rate (backward-looking);
the upcoming rate is not in the panel and is never used.

**Universe.** 30-day median dollar volume ≥ $10M computed on days *strictly before* the
event day; ≥ 30 days since first bar (listing burn-in); ≥ 20 eligible symbols in the
cross-section or the event is dropped.

**Cost.** Nearly every mechanism here is a **2-leg dollar-neutral quintile basket**, so the
briefing's `−14 / −28` is doubled: **base 28bps, stress 56bps per round trip**, counted on
executed turnover (briefing §8.9). One-leg (directional) mechanisms — Family B1 and the
cascade families — use `−14 / −28`. Both column sets are printed for everything.

**Declustering.** A clock effect is the most clustered object in this dataset: every symbol
sees the same hour at the same instant, so **L2 (calendar day) is the binding level, not the
loosest one**. Every headline t-stat is computed on day-aggregated observations. The naive
t is printed beside it with the inflation factor, so the size of the trap is visible: it is
~1.0 for the cross-sectional mechanisms (the dollar-neutral construction already removes the
common factor) and **up to 15.0** for the cascade mechanisms, which are event-clustered.

**Power / ETA.** `n_required = 7.849 / (0.5 * IR_day)^2` independent days, with the mandatory
50% haircut; `event_rate` = independent L2 episodes/week over the last 6 months;
`ETA = n_required / event_rate`. PREREG §5 pre-computed the bar: a daily mechanism needs
`IR_day > 0.169` (discovered annualised Sharpe > 3.2) to confirm inside 3 years.

**Multiple comparisons.** Every family is judged against a **family-wise max-|t| critical
value** from a joint week-block bootstrap under the null, not against 1.96. Critical values
actually used: Family A 2.9–3.1, Family B final 3.20, clock map levels 3.096, clock map
contrasts 3.073, C/D/E/F 2.969.

**Verdicts** are produced mechanically by `evidence/gate.py::auto_verdict` from the numbers.
No per-mechanism discretion.

---

## 4. The 24-hour clock profile — the central result

`evidence/clock_map_v2.py`. One mechanism, 24 times; **only the UTC entry hour changes**:

```
signal = residual return over the 6 contiguous hours [H-6h, H)      (causal, beta-residual)
entry  = price at H+5m           (one 5m implementation bar)
exit   = price at H+8h
trade  = equal-weight bottom-quintile-by-signal MINUS top-quintile  (dollar-neutral)
         spread > 0 = reversion ;  spread < 0 = continuation
```

One observation per arm per day ⇒ the L2 decluster is **exact by construction**. `H=13` is
exactly the `B2_EU_to_US` session mechanism, so the map carries its own cross-check.

Family-wise max-|t| critical value over the 24 arms: **3.096**.

| hour | gross bps | t (L2) | TRAIN 20-23 | TEST 24-26 | TEST t (sign frozen) | signif | regime |
|---:|---:|---:|---:|---:|---:|:--:|---|
| 00 | +14.43 | 4.97 | +19.20 | +8.14 | 1.81 | *** | reversion |
| 01 | +16.25 | 5.52 | +21.14 | +9.79 | 2.03 | *** | reversion |
| 02 | **+19.13** | **6.66** | +23.20 | +13.76 | **3.02** | *** | reversion |
| 03 | +17.20 | 5.89 | +24.71 | +7.27 | 1.61 | *** | reversion |
| 04 | +15.74 | 5.31 | +15.55 | +16.00 | **3.58** | *** | reversion |
| 05 | +12.82 | 4.05 | +16.06 | +8.54 | 1.88 | *** | reversion |
| 06 | +9.98 | 3.39 | +11.57 | +7.86 | 1.74 | *** | reversion |
| 07 | +0.67 | 0.23 | −1.06 | +2.96 | −0.65 | | — (handover) |
| 08 | −5.61 | −1.87 | −9.59 | −0.34 | 0.07 | | continuation |
| 09 | −2.53 | −0.78 | −1.97 | −3.26 | 0.67 | | continuation |
| 10 | −6.70 | −2.12 | −6.62 | −6.80 | 1.37 | | continuation |
| 11 | −4.98 | −1.62 | −8.23 | −0.69 | 0.14 | | continuation |
| 12 | −3.37 | −1.09 | −2.91 | −3.96 | 0.79 | | continuation |
| 13 | −10.97 | −3.44 | −10.65 | −11.41 | 2.21 | *** | continuation |
| 14 | −15.44 | −4.79 | −16.05 | −14.62 | 2.84 | *** | continuation |
| 15 | **−15.63** | **−5.30** | −19.11 | −11.03 | 2.28 | *** | continuation |
| 16 | −10.42 | −3.57 | −13.91 | −5.81 | 1.28 | *** | continuation |
| 17 | −9.59 | −3.08 | −14.07 | −3.67 | 0.76 | | continuation |
| 18 | −8.48 | −2.63 | −10.20 | −6.20 | 1.25 | | continuation |
| 19 | −3.71 | −1.21 | −10.05 | +4.67 | −0.99 | | — |
| 20 | +2.63 | 0.86 | +2.60 | +2.66 | 0.57 | | — |
| 21 | +2.17 | 0.74 | +2.20 | +2.13 | 0.46 | | — |
| 22 | +5.70 | 1.91 | +6.77 | +4.29 | 0.88 | | reversion |
| 23 | +6.14 | 2.06 | +6.17 | +6.10 | 1.28 | | reversion |

The profile is **smooth and contiguous**, not a scatter of lucky hours: a seven-hour
reversion block (h00–h06), a clean zero-crossing at the Asia→EU handover (h07), a six-hour
continuation block (h13–h18), and a second crossing at the US→late handover (h19–h20). The
sign is the same in TRAIN and TEST for every one of the 11 family-significant arms.

### 4.1 Arm-vs-arm — never against zero

This market has a strong unconditional drift, so "hour H is positive" is not evidence.
Each hour is therefore compared to **the mean of the other 23 arms on the same calendar
day**, and the family-wise max-|t| over the 24 *contrasts* is **3.073**.

| contrast | diff bps | t | CI95 | signif |
|---|---:|---:|---|:--:|
| h02 − mean(other 23) | +18.84 | 6.82 | [13.43, 24.21] | *** |
| h03 − mean(other 23) | +16.83 | 6.11 | [10.88, 22.53] | *** |
| h01 − mean(other 23) | +15.84 | 5.40 | [9.72, 21.97] | *** |
| h04 − mean(other 23) | +15.31 | 5.51 | [10.10, 21.02] | *** |
| h00 − mean(other 23) | +13.94 | 4.65 | [8.20, 19.79] | *** |
| h05 − mean(other 23) | +12.26 | 4.24 | [6.35, 18.29] | *** |
| h06 − mean(other 23) | +9.31 | 3.39 | [3.56, 15.08] | *** |
| h15 − mean(other 23) | −17.41 | −6.53 | [−22.79, −11.93] | *** |
| h14 − mean(other 23) | −17.21 | −5.80 | [−23.00, −10.92] | *** |
| h13 − mean(other 23) | −12.55 | −4.25 | [−18.02, −7.13] | *** |
| h16 − mean(other 23) | −11.97 | −4.56 | [−17.19, −6.88] | *** |
| h17 − mean(other 23) | −11.11 | −3.86 | [−17.04, −5.66] | *** |
| h18 − mean(other 23) | −9.95 | −3.33 | [−16.03, −4.23] | *** |
| h07 − mean(other 23) | −0.40 | −0.15 | [−5.86, 4.75] | |

13 of 24 contrasts clear the family-wise bar. Extreme pair and session boundaries, paired on
the same days:

| contrast | diff bps | n paired days | t | CI95 |
|---|---:|---:|---:|---|
| **h02 − h15** | **+34.72** | 2188 | **8.50** | [26.26, 43.61] |
| h13 − h00 | −25.41 | 2188 | −5.89 | [−33.96, −17.03] |
| h13 − h21 | −13.07 | 2188 | −3.12 | [−20.85, −5.51] |
| h21 − h00 | −12.17 | 2187 | −2.93 | [−19.61, −4.84] |
| h13 − h07 | −11.65 | 2189 | −2.79 | [−20.31, −3.84] |
| h07 − h21 | −1.60 | 2188 | −0.39 | [−9.58, 6.24] |

Two things about how to read these contrasts:

* **Adjacent arms overlap and that makes the contrast conservative, not generous.** h02's
  signal window is `[20:00, 02:00)` and its hold `[02:05, 10:00]`; h03's are shifted by one
  hour. So "h02 − mean(other 23)" subtracts a mean that partly contains h02's own neighbours,
  which biases the contrast *toward* zero. The seven-arm reversion block is therefore **one
  finding observed seven times, not seven independent findings** — and the contrast still
  clears the family-wise bar.
* **The extreme contrast is clean.** h02 and h15 share nothing: signal windows
  `[20:00, 02:00)` vs `[09:00, 15:00)`, holds `[02:05, 10:00]` vs `[15:05, 23:00]` — disjoint
  in both. `h02 − h15 = +34.72bps, t=8.50` is a comparison of two non-overlapping trades on
  the same 2188 calendar days.

**The clock conditions the sign of short-horizon cross-sectional autocorrelation. This is
established.** It is the answer to pre-registered hypothesis F1, and it is positive — the
first genuinely clock-conditional result on this axis.

### 4.1b Year-by-year — the profile is not one good year

`year_by_year` and `ex_best_year` are §2 gate columns; for the clock map they are the
strongest part of the case, because this project has repeatedly found "edges" that were a
2021 artefact (see the B1 null in §6.5, where 20 of 24 hours have a *negative* ex-best-year).

| arm | gross | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 | ex-best-year | ex-best t |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| h02 (reversion) | +19.13 | +30 | +47 | +16 | +4 | +18 | +10 | +13 | **+13.53** (drop 2021) | 4.59 |
| h03 (reversion) | +17.20 | +38 | +53 | +17 | −2 | +13 | +4 | +3 | +9.96 (drop 2021) | 3.22 |
| h13 (continuation) | −10.97 | −22 | +2 | −19 | −10 | −10 | −4 | −28 | **−13.56** (drop 2021) | −4.09 |
| h15 (continuation) | −15.63 | −32 | −29 | −23 | −0 | −6 | −7 | −27 | **−18.74** (drop 2023) | −5.45 |

h02 carries the right sign in **all seven** calendar years and h15 in six of seven (2023 is
flat, not inverted). Removing the single best year *strengthens* the two continuation arms
rather than weakening them. Nothing here is `REGIME_DEPENDENT`.

### 4.2 Why it still is not a trade

`max |gross| across the 24 arms = 19.13bps`, against a **28bps** 2-leg base cost.
**0 of 24 arms clear the base cost.** The `h02 − h15` contrast is 34.7bps, but it is the
difference of two separate round trips (the h02 leg is closed at 10:00, the h15 leg opens at
15:05 — no netting), so it costs 2 x 28 = 56bps to harvest. Per episode it is ~17.4bps
against 28bps. See §5 for the concentration and holding-period sweep that tests whether any
configuration closes the gap.

---

## 5. Clock map, depth pass — can the effect be traded?

`evidence/clock_map_depth.py`. Three questions: does the profile survive a bid-ask-bounce
control at *every* hour; does concentration or a longer hold lift any arm over its cost; and
what does a combined daily strategy look like when the hours are chosen **on TRAIN only**.

### 5.1 Bounce control across all 24 hours

Same trade, entry moved from `H+5m` to `H+1h` with the exit held fixed at `H+8h`. A bounce
artefact must shrink; a real effect need not. `retention = gap1h / gap0h`.

| hour | gap +5m (bps) | t | gap +1h (bps) | t | retention |
|---:|---:|---:|---:|---:|---:|
| 00 | +14.43 | 4.97 | +9.67 | 3.64 | 0.67 |
| 01 | +16.25 | 5.52 | +14.20 | 5.26 | 0.87 |
| **02** | **+19.13** | **6.66** | **+18.96** | **7.03** | **0.99** |
| 03 | +17.20 | 5.89 | +15.21 | 5.52 | 0.88 |
| 04 | +15.74 | 5.31 | +11.61 | 4.32 | 0.74 |
| 05 | +12.82 | 4.05 | +10.71 | 3.60 | 0.84 |
| 06 | +9.98 | 3.39 | +8.14 | 2.97 | 0.82 |
| 07 | +0.67 | 0.23 | +0.59 | 0.22 | 0.88 |
| 13 | −10.97 | −3.44 | −14.62 | −4.92 | 1.33 |
| 14 | −15.43 | −4.79 | −14.36 | −4.89 | 0.93 |
| 15 | −15.63 | −5.30 | −10.63 | −3.93 | 0.68 |
| 16 | −10.42 | −3.57 | −8.85 | −3.36 | 0.85 |
| 17 | −9.59 | −3.08 | −9.76 | −3.35 | 1.02 |
| 18 | −8.48 | −2.63 | −6.54 | −2.16 | 0.77 |
| 20–22 | +2.63 … +5.70 | ≤1.90 | +0.24 … +2.73 | ≤0.98 | 0.09–0.48 |

**The clock effect is not a bid-ask bounce.** Every family-significant arm retains 67–133% of
its size and its t-stat when the entry is pushed a full hour past the signal window; h02
retains 99% and h13 *gains*. The arms that collapse under the control (h20–h22, retention
0.09 / −0.85 / 0.48) are exactly the ones that were never significant in the first place —
the control kills the noise and spares the signal, which is what a valid control should do.

This is the sharp contrast with §6.2: `LATE→ASIA` loses 40% of its size and all of its
out-of-sample significance under the same control. The two results are compatible and the
difference is structural — `LATE→ASIA` ranks on a **3-hour** signal window in the day's
thinnest book, while the clock map ranks on a uniform **6-hour** window at every hour. The
short, thin signal window is what carries the bounce.

> The competing explanation for the profile is a **liquidity cycle rather than geography**:
> the Asia hours are the thinnest of the day (noise-driven price moves that mean-revert), the
> US hours the deepest (information-driven moves that continue). This study cannot separate
> "geography" from "book depth" — they are collinear by construction. What it does establish
> is that the effect is not *mechanical* bounce, since it survives a 1h entry gap.

### 5.2 Holding period x concentration sweep

72 cells (hours 2, 4, 14, 15 x holds 4/8/12h x quintile/decile/ventile x entry gap 0/1h),
family-wise max-|t| critical value over the 77 cells of this pass: **3.488**.
`test_bps` / `test_t` are TEST 2024-2026 with the sign frozen on TRAIN 2020-2023.

> **Sign convention — read this before the table.** `gate.py` computes its `net_bps_*`
> columns on the *signed* spread. A continuation arm has a negative spread
> (losers − winners < 0) and is traded in the reverse direction, so its economically
> meaningful net is `|gross| − 28` and `|gross| − 56`, not the signed column stored in
> `RESULTS.json`. `auto_verdict` already tests `abs(gross_bps)`, so no verdict is affected;
> the table below prints `|gross|` and the nets derived from it, and `RESULTS.json` keeps
> the raw signed columns as computed.

| hour | hold | buckets | gap | \|gross\| bps | t | net base (−28) | net stress (−56) | TEST bps | TEST t | ETA y | verdict |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 02 | 8h | q5 | 0 | 19.13 | 6.66 | −8.87 | −36.87 | +13.76 | 3.02 | 5.10 | `COST_FRAGILE` |
| 02 | 8h | q20 | 0 | 34.81 | 4.76 | **+6.81** | −21.19 | +28.15 | 2.28 | 9.97 | `COST_FRAGILE` |
| 02 | 8h | q20 | 1 | 34.03 | 5.04 | **+6.03** | −21.97 | +24.99 | 2.29 | 8.91 | `COST_FRAGILE` |
| 02 | 12h | q20 | 0 | 39.63 | 4.49 | **+11.63** | −16.37 | +30.42 | 2.02 | 11.24 | `COST_FRAGILE` |
| 14 | 12h | q10 | 0 | 35.74 | −5.91 | **+7.74** | −20.26 | +29.05 | 2.93 | 6.51 | `COST_FRAGILE` |
| 14 | 12h | q20 | 0 | 55.69 | −5.84 | **+27.69** | −0.31 | +43.73 | 2.69 | 6.68 | `COST_FRAGILE` |
| 15 | 12h | q10 | 0 | 40.18 | −6.79 | **+12.18** | −15.82 | +31.48 | 3.22 | 4.95 | `COST_FRAGILE` |
| **15** | **12h** | **q20** | **0** | **61.89** | **−6.51** | **+33.89** | **+5.89** | **+43.70** | **2.62** | **5.37** | **`UNCONFIRMABLE_IN_HORIZON`** |
| 15 | 12h | q20 | 1 | 51.76 | −5.80 | +23.76 | −4.24 | +31.58 | 2.06 | 6.78 | `COST_FRAGILE` |

**Exactly one cell of 72 survives the 56bps stress cost**: `h15, 12h hold, ventiles` —
61.89bps gross, +33.89 net at base cost and **+5.89 at stress**, t=−6.51 on 2188 independent
days, ex-best-year 70.77bps, out-of-sample +43.70bps (t=2.62). `auto_verdict` classes it
**`UNCONFIRMABLE_IN_HORIZON`** on its 5.37-year ETA (4.49y after the date correction of §3),
and that is the right answer. Four further reasons make it a non-deliverable, each fatal
on its own:

1. **Concentration always makes the ETA worse on this axis.** It buys gross bps by paying in
   variance: h02 goes 19.1 → 34.8bps but its t falls 6.66 → 4.76 and its ETA *doubles*,
   5.10y → 9.97y. The 12h/ventile cells are the largest bps and among the slowest to confirm.
2. **It is the maximum of a 72-cell sweep whose four hours were themselves chosen from the
   full-sample map of §4.** The family-wise max-t (3.488) covers the 72 cells; it does not
   cover the hour selection. The only cleanly out-of-sample object here is §5.3.
3. **Unmodelled funding.** PREREG §1 requires any window straddling a settlement to carry its
   funding cashflow explicitly. The 12h holds cross **two** settlements (`15:05 → 03:00`
   crosses 16:00 and 00:00) and the 8h holds cross one; that cashflow is **not** in these
   numbers, and it works *against* the position — this is a long-winners basket, winners
   carry higher funding, and Family A measured the extreme-quintile funding differential at
   ~4bps per settlement (A3c). A −4 to −8bps correction puts this cell back at or below the
   stress line. **Named missing gate cell.**
4. **Capacity.** A ventile of a 110–180 symbol universe is 5–9 names per leg, and the
   winsorised check of §6.3 showed the edge concentrating in exactly that tail.

### 5.3 The combined daily strategy — the clean out-of-sample object

Hours chosen **on TRAIN 2020-2023 only** (reversion **h03**, +24.71bps on TRAIN;
continuation **h15**, −19.11bps on TRAIN), 8h hold, then measured. One episode = one 2-leg
round trip; the strategy runs two non-overlapping episodes per day (the h03 leg closes at
11:00, the h15 leg opens at 15:05), so it is fed to the gate as two observations per day and
`gross_bps` is per episode, keeping the 28/56 columns literally correct.

| variant | gross bps/episode | net base | net stress | t (L2) | IR_day | Sharpe ann. | n days | ETA y | ETA y corr. | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| quintiles, FULL 2020-26 | +16.39 | −11.61 | −39.61 | 8.05 | 0.1721 | **3.29** | 2188 | 3.49 | **2.92** | `COST_FRAGILE` |
| deciles, FULL | +25.20 | −2.80 | −30.80 | 7.54 | 0.1612 | 3.08 | 2188 | 3.98 | 3.33 | `COST_FRAGILE` |
| ventiles, FULL | +31.25 | **+3.25** | −24.75 | 5.70 | 0.1218 | 2.33 | 2188 | 6.97 | 5.83 | `COST_FRAGILE` |
| quintiles, **TEST 2024-26**, hours frozen on TRAIN | +9.15 | −18.85 | −46.85 | 2.77 | 0.0903 | 1.73 | 943 | 12.67 | 10.60 | `WEAK` |
| placebo: continuation arm's days shuffled | +16.39 | −11.61 | −39.61 | 7.94 | 0.1697 | 3.24 | 2188 | 3.59 | 3.00 | — |

Three things to read here:

* **The best ETA on the entire axis is here: 3.49 years (2.92y date-corrected).** The
  FULL-sample annualised Sharpe of **3.29** is just above the bar PREREG §5 pre-computed for
  a daily mechanism to confirm inside 3 years (`IR_day > 0.169`, Sharpe > 3.2). It is the
  only object this worker produced that reaches it — because it takes **two** episodes a day
  rather than one, which is exactly the "high episode frequency" property briefing §2 asks
  workers to hunt for. The lesson generalises past this axis: *episode frequency beat effect
  size for ETA every single time in this study.*
* **It halves out of sample.** TEST 2024-2026 with the hours frozen on TRAIN: +9.15bps,
  t=2.77 — below the family-wise critical value of 3.488, hence `WEAK`. The FULL-sample
  Sharpe of 3.29 is not what a forward book would have earned.
* **The placebo is a null, and informatively so.** Shuffling the continuation arm's day
  labels leaves the mean identical (by construction) and the t almost unchanged
  (7.94 vs 8.05), which says the two arms are close to **independent across days**. That
  supports treating them as two episodes per day rather than one correlated bet — the
  assumption the ETA calculation rests on. It is *not* a signal placebo; the signal placebo
  is `EUUS_PLACEBO_random_rank` in §6.3 (−0.53bps, t=−0.26).

At +16.4bps per episode against a 28bps 2-leg round trip, the strategy **never pays for its
own execution**, at any concentration, in or out of sample.

**This is the axis in one line: a real, replicated, mechanistically coherent effect that is
smaller than its transaction cost, whose only cost-clearing configuration is
in-sample-selected, capacity-free, funding-unmodelled and unconfirmable inside the horizon.**

---

## 6. Family B — session clock: one kill, one survivor

`evidence/family_b.py` (headline, reused as computed) and `evidence/family_b_armfix.py`
(final pass). Sessions (UTC, non-overlapping partition): ASIA `[00,07)`, EU `[07,13)`,
US `[13,21)`, LATE `[21,24)`. Signal = that session's residual return; entry = next boundary
+5m; exit = the following boundary.

### 6.1 The entry-gap sweep — the control that decides both cases

A cross-sectional signal measured up to a boundary and traded 5 minutes later is the
textbook setting for a **bid-ask-bounce artefact**: the "loser" quintile is disproportionately
made of symbols whose last print was at the bid, so it mechanically prints a positive return
on the next trade. The control is to push the entry away from the boundary while holding the
exit fixed. **A bounce artefact must shrink; a real flow effect need not.**

| arm | gap +0h | +1h | +2h | +3h | TRAIN 20-23 (gap0) | TEST 24-26 t, sign frozen (gap 0/1/2/3) |
|---|---:|---:|---:|---:|---:|---|
| ASIA→EU | +6.75 | +5.41 | +3.21 | +3.25 | +10.13 | 0.55 / 0.65 / 0.18 / 0.23 |
| **EU→US** | **−10.66** | **−14.44** | **−12.10** | −6.61 | −10.37 | **2.19 / 2.96 / 2.43 / 1.75** |
| US→LATE | +3.05 | −0.43 | −1.80 | 0.00¹ | +2.40 | 1.51 / −1.23 / −1.36 / — |
| **LATE→ASIA** | **+14.08** | +8.46 | +6.59 | +8.67 | +20.98 | 1.14 / 1.04 / 0.98 / 1.84 |

¹ the US→LATE transition holds only 3h, so a 3h gap leaves a zero-length position.

### 6.2 KILL — `LATE→ASIA` is a bid-ask-bounce artefact

`B2_LATE_to_ASIA` was the strongest single number the axis produced: **+14.08bps, t=5.21**
on 2200 independent days, ex-best-year +12.15bps. It fails both controls:

* **it decays with the gap**: 14.08 → 8.46 → 6.59bps at +0h/+1h/+2h — it loses 53% of its
  size by moving the entry one hour away from the boundary, which is the signature of a
  bounce, not of information;
* **it does not replicate out of sample**: with the sign frozen on TRAIN 2020-2023, the TEST
  2024-2026 t-stats are 1.14 / 1.04 / 0.98 / 1.84 — never significant at any gap. TRAIN
  +20.98bps vs TEST +4.88bps is a 77% decay.

Structurally it is also the arm most exposed to the artefact: the LATE session is only 3
hours long and is the thinnest book of the day, so its residual-return ranking is the
noisiest of the four and the bounce component is the largest fraction of it.

**Verdict `DEAD` (bid-ask-bounce artefact).** This is the most valuable negative of the
axis: it was on track to be reported as the find of the worker.

> This kill is about the *session-boundary* trade with a 3h LATE signal window. It is **not**
> a kill of the Asia-hours reversion block in §4, which uses a uniform 6h signal window and a
> uniform 8h hold and is tested against its own gap control (§5.2). The two are different
> objects and must not be conflated.

### 6.3 SURVIVOR — `EU→US` continuation

`B2_EU_to_US`: rank symbols by their EU-session (07:00–13:00) residual return, enter at
13:05, exit at 21:00. The quintile spread (losers − winners) is **negative**: winners
continue. It passes both controls:

* **it strengthens with the gap**: −10.66 → −14.44bps at +1h (and only decays at +3h, when
  the hold has shrunk from 8h to 5h) — the opposite of a bounce;
* **it replicates out of sample** at every gap, with the sign frozen on TRAIN 2020-2023:
  TEST 2024-2026 = +11.04 (t=2.19), **+14.20 (t=2.96)**, +10.55 (t=2.43), +6.47 (t=1.75);
* **it is stable across eras**: −9.64 / −11.28 / −11.44bps for 2020-22 / 2023-24 / 2025-26 —
  unusually flat for this project, and notably *not* arbitraged away in 2025-26;
* the **placebo** (same population, random ranking) gives −0.53bps, t=−0.26.

> **`SIGN_OPPOSITE_TO_HYPOTHESIS`.** PREREG §6 pre-committed `H_B2: reversion (spread > 0)`.
> The result is continuation. Amendment 1 forbids re-labelling that as a discovery in the
> other direction without a disjoint period — which is exactly what the TRAIN/TEST split
> above provides, and the sign was fixed on TRAIN before TEST was looked at. Reported as a
> continuation finding on that basis, and only on that basis.

**But it does not pay.** 14.44bps gross against a 28bps 2-leg base cost. Concentration makes
it worse, not better, because it buys size with variance:

| variant | gross bps | t (L2) | net 2-leg | verdict |
|---|---:|---:|---:|---|
| quintiles (q5) | −10.66 | −3.53 | −38.66 | `COST_FRAGILE` |
| deciles (q10) | −14.94 | −3.07 | −42.94 | `WEAK` |
| ventiles (q20) | −19.61 | −2.61 | −47.61 | `WEAK` |
| liquidity LOW tercile | −3.79 | −1.20 | −31.79 | `WEAK` |
| liquidity MID tercile | −4.20 | −1.07 | −32.20 | `WEAK` |
| liquidity HIGH tercile | −19.75 | −3.14 | −47.75 | `WEAK` |
| winsorised ±10% | −5.19 | −2.30 | −33.19 | `WEAK` |

The winsorised variant losing half the edge is a genuine warning (PREREG Amendment 1: "if the
two disagree materially the mechanism is downgraded, not upgraded") — a material part of the
8h continuation lives in the fat tail of large movers.

**Verdict `COST_FRAGILE`** (gross 14.44bps ≤ 28bps 2-leg base cost). ETA 8.8y — moot, since
it dies on cost first.

### 6.4 Arm-vs-arm, correctly aligned (the clock claim at session resolution)

This is the comparison that returned `n_days=0, NaN` in the first pass (§2.2). Paired on the
originating calendar day; family-wise max-|t| critical value 3.201.

| contrast | diff bps | n paired days | t | CI95 |
|---|---:|---:|---:|---|
| LATE→ASIA − EU→US (raw) | +24.62 | 2200 | 6.09 | [16.68, 32.66] |
| ASIA→EU − EU→US (raw) | +17.41 | 2202 | 4.27 | [9.11, 25.12] |
| US→LATE − EU→US (raw) | +13.71 | 2202 | 4.09 | [6.83, 20.17] |
| LATE→ASIA − US→LATE (raw) | +11.04 | 2200 | 3.52 | [4.80, 17.63] |
| LATE→ASIA − ASIA→EU (raw) | +7.44 | 2200 | 1.88 | [−0.53, 15.87] |
| LATE→ASIA − EU→US (**per hour held**) | +3.33 | 2200 | 6.19 | [2.30, 4.41] |
| ASIA→EU − EU→US (**per hour held**) | +2.46 | 2202 | 4.13 | [1.29, 3.60] |
| US→LATE − EU→US (**per hour held**) | +2.35 | 2202 | 3.60 | [1.05, 3.63] |

The clock claim survives horizon normalisation: EU→US is different from every other
transition by ~2.3–3.3bps **per hour held**, in the same direction. The session view and the
hour-by-hour view of §4 agree, which is the corroboration that matters — they are computed
from different aggregations of the same panel and were built by different code paths.

### 6.5 B1 — hour-of-day market factor (the pre-registered null)

24 one-leg arms, equal-weight universe mean return by UTC hour, family-wise max-|t| over the
24 buckets. `H_B1` predicted no reliable hour-of-day drift after cost. **Confirmed**: the
largest |t| is 2.66 (h01) against a 24-bucket critical value; 19 of 24 arms are `DEAD` and
the rest `WEAK`. Crucially, `ex_best_year` is *negative* for 20 of the 24 hours — the raw
hour-of-day drift is a 2021 artefact. This is why every other mechanism on the axis is
cross-sectional and dollar-neutral: **there is no exploitable hour-of-day drift in the market
factor**, only in the cross-section.

---

## 7. Family A — funding clock: a 2–4bps footprint, arbitraged flat

`evidence/family_a.py` (17 mechanisms) and `evidence/family_a_depth.py` (75-cell sweep:
window x concentration x settlement hour x liquidity tercile x era). Signal is always the
last *settled* funding rate or the contemporaneous basis; the upcoming rate is not in the
panel and was never used.

| mechanism | gross bps | t (L2) | net 2-leg | ETA y | verdict |
|---|---:|---:|---:|---:|---|
| A1 pre-settlement drift (funding rank, [F−55m, F]) | +1.97 | 4.12 | −26.03 | 13.4 | `COST_FRAGILE` |
| A2 post-settlement reversion ([F+5m, F+60m]) | +0.95 | 1.76 | −27.05 | 73.7 | `WEAK` |
| A2b same, one extra bar of lag | +1.16 | 2.29 | −26.84 | 43.4 | `WEAK` |
| A3 straddle + explicit funding carry | +3.72 | 5.11 | −24.28 | 8.7 | `COST_FRAGILE` |
| A3c *pure carry component (decomposition only)* | +4.05 | 55.35 | −23.96 | 0.07 | `COST_FRAGILE` |
| A4 pre, extreme funding (pct90) | +2.94 | 1.86 | −25.06 | 48.5 | `WEAK` |
| A4b post, extreme funding (pct90) | −3.53 | −1.67 | −31.53 | 60.1 | `WEAK` |
| A5 pre, settlement hour 00 UTC | +3.79 | 5.27 | −24.21 | 8.2 | `COST_FRAGILE` |
| A5 pre, settlement hour 08 UTC | +2.06 | 2.63 | −25.94 | 32.8 | `WEAK` |
| A5 pre, settlement hour 16 UTC | +0.06 | 0.07 | −27.94 | 41450 | `DEAD` |
| A6 pre, basis rank | +3.17 | 7.25 | −24.83 | 4.3 | `COST_FRAGILE` |
| A6b post, basis rank | +1.67 | 3.48 | −26.33 | 18.8 | `COST_FRAGILE` |
| A1w / A2w winsorised ±10% | +1.84 / +1.29 | 4.14 / 2.68 | −26.16 / −26.71 | 13.3 / 31.6 | `COST_FRAGILE` / `WEAK` |

**A3c is the tell.** The pure funding cashflow received by a long-low/short-high spread is
**+4.05bps with t=55.4** — the single most statistically certain number produced on this
axis, and it is a mechanical accounting identity, not an edge. The entire *price* footprint
of the funding clock (A1 + A2 ≈ 2.9bps) is smaller than the cashflow that causes it, and
both are an order of magnitude below the 28bps cost of trading the spread.

**Depth sweep, 75 cells.** Maximum |gross| anywhere: **7.52bps**
(`H16|basis_sig|q10|post120`). **0 of 75 cells clear 28bps gross.** The pre-registered
amplifiers all fail: concentration to ventiles buys size but loses t (q5 → q20:
3.14 → 6.38bps but t 7.20 → 5.83, and ETA 4.3y → 6.7y); the 00:00 UTC settlement hour is
genuinely the strongest arm (7.05bps at q10 vs 2.60/2.31 at 08/16 UTC) but three times too
small; thin liquidity helps marginally (LIQ_LOW 4.73 vs LIQ_HIGH 2.59) and the "triple
amplifier" (thin x 00 UTC x decile) reaches only 6.37bps.

Era split confirms the project's standing result rather than extending it: ERA_2020_22
+5.04bps → ERA_2023_24 +2.83 → ERA_2025_26 +3.82 at `basis_sig|q10|pre55`, with the post-
settlement windows going *negative* in 2025-26 (−1.84bps at post60). **Funding/basis is
arbitraged flat**, consistent with the round-2/3 finding; nothing here changes it.

**Verdict for the whole family: `COST_FRAGILE`.** A real, highly significant, permanently
sub-cost footprint.

---

## 8. Families C, D, E, F — pre-registered, never run before this pass

`evidence/family_cdef.py`. Family-wise max-|t| over the 19 mechanism cells: **2.969**.

### 8.1 Family C — weekend clock: `DEAD`

| mechanism | n (L2) | gross bps | t | verdict |
|---|---:|---:|---:|---|
| C1 Friday 21:00 signal → Sat 00:00–Mon 00:00 XS reversal | 311 | −12.10 | −0.66 | `DEAD` |
| C3 weekend drift → Sun 20:05–Mon 02:00 | 312 | −1.60 | −0.20 | `DEAD` |

Both are 1 episode/week by construction. PREREG §6 pre-declared that unless
`IR_week > 0.45` these are `UNCONFIRMABLE_IN_HORIZON` before measurement; they are in fact
simply not there (`IR_day` −0.037 and −0.012), so they die on significance before ETA is
even consulted. C1 also carries the opposite sign to `H_C1`, at t=−0.66 — noise.

### 8.2 Family D/F — month end, quarter expiry, weekend as meta-conditioners: `DEAD`

One common daily mechanism (signal = residual over the 24h ending 00:00 UTC, entry 00:05,
exit next 00:00; baseline +5.17bps, t=0.93) split by calendar arm and judged **arm-vs-arm**:

| conditioner | n days in arm | arm − rest (bps) | t | CI95 |
|---|---:|---:|---:|---|
| month end (last 2 UTC days) | 144 | +8.69 | 0.35 | [−39.70, 57.08] |
| quarterly expiry week (Mar/Jun/Sep/Dec, days 22–28) | 168 | +10.63 | 0.45 | [−36.16, 57.43] |
| weekend (Sat/Sun) | 624 | +26.99 | 2.19 | [2.83, 51.15] |
| Monday | 312 | +24.06 | 1.72 | [−3.39, 51.51] |

Month-end and quarter-expiry: **nothing**, exactly as pre-declared. The weekend arm is the
only one with a nominally positive contrast (+27bps, t=2.19) but it is **below the family-wise
critical value of 2.969**, and the arm's own level (+24.46bps gross) is still under the 28bps
2-leg cost. `WEAK` — worth one line in a future weekend-specific study, not a mechanism.

### 8.3 Family E — clock x event: the one usable conditioner

`data/events/liq_cascade_dataset.parquet`, 38,141 cascades, 2021-01-04 → 2026-07-04.
One-leg directional (cost 14/28bps). **Note the clustering inflation factor: up to 15.0** —
the naive t on raw events would have been 4.5 where the declustered t is −0.30. This is the
axis's own warning about its L2 unit, and it is the largest inflation measured anywhere in
this worker.

Arm-vs-arm, paired on calendar days (18 arm comparisons ⇒ Bonferroni |t| > 2.99):

| contrast | diff bps | n paired days | t |
|---|---:|---:|---:|
| **cascade fwd_8h: US session − EU session** | **+25.53** | 1322 | **3.14** |
| cascade fwd_4h: US session − EU session | +15.97 | 1323 | 2.80 |
| cascade fwd_8h: LATE − EU | +24.11 | 872 | 1.74 |
| cascade fwd_4h: LATE − EU | +17.42 | 872 | 1.85 |
| cascade fwd_4h: ASIA − EU | +11.83 | 1205 | 1.82 |
| cascade fwd_4h: ASIA − US | −0.32 | 1348 | −0.06 |

Levels (for reading the contrast, not for judging): fwd_4h ASIA +12.77 / EU **+0.99** /
US +14.77 / LATE +16.41 bps; fwd_8h ASIA +11.60 / EU **−2.14** / US +16.86 / LATE +15.58.

**The liquidation-cascade bounce is absent in the EU session and present everywhere else.**
The EU arm is not merely weaker — it is zero (t=0.21 and −0.30 declustered). This is a real
clock x event interaction and it clears Bonferroni for the arm family at fwd_8h.

It is **not a standalone alpha**: the best session arm is +16.9bps gross one-leg, i.e.
+2.9bps after the 14bps base cost and −11bps under stress. It is a **screen/gate** for the
already-shadowed `LIQ_CASCADE_REPEAT_V1`: *do not take cascade signals fired between 07:00
and 13:00 UTC*. Verdict `PROMISING_NEEDS_VALIDATION` **as a conditioner**, with the missing
gate cell named explicitly: it has never been tested as an overlay on the actual
`LIQ_CASCADE_REPEAT_V1` position stream, only on the raw cascade dataset.

### 8.4 Family E2 — does the weekend change the repeat-cascade effect? No.

The project's standing result (1st cascade ≈ negative, 3rd+ ≈ positive) is reproduced here
as a control, not re-tested: repeat≥3 minus first = **+12.38bps (fwd_4h)** and **+11.76bps
(fwd_8h)** on weekdays. The weekend interaction is absent:

| | repeat≥3 − first (bps) | n days | t |
|---|---:|---:|---:|
| weekday | +12.38 | 535 | 1.35 |
| weekend | −1.43 | 118 | −0.08 |
| **difference-in-differences (weekend − weekday)** | **−21.24** | — | **−0.94** |

`H_C2` predicted cascades pay *more* on weekends (thin books). **Rejected**: the point
estimate is the wrong sign and the DiD t is −0.94. The weekend arm has only 118–120
independent days, so it is `DATA_LIMITED` on its own terms as well.

### 8.5 Family F — clock as a meta-conditioner: **confirmed**, see §4

F1 asked whether the hour of day gates the sign of a cross-sectional reversal signal, with
the multiple-comparison cost of 24 buckets stated up front. Answer: **yes** — 13 of 24
hour contrasts clear a family-wise max-t of 3.073, the extreme contrast is +34.7bps at
t=8.50, and the profile is contiguous rather than scattered. F1 is the one pre-registered
hypothesis on this axis that is confirmed outright. It is also the only one that produced a
figure larger than the trading cost — and only as a *contrast*, which cannot be traded as
such (§4.2).

---

## 9. The complete §2 gate — every mechanism classed better than `WEAK`

Columns are the briefing §2 fields verbatim. `net14/net28` are the briefing-literal one-leg
columns; `net_2leg / net_2leg_str56` are the real cost of a two-leg dollar-neutral basket and
are **the columns the verdict is decided on** for every cross-sectional mechanism here (the
one-leg columns govern the `E1_*`/`E2_*` cascade rows and `B1_*`). `t_naive` is the t-stat on
raw events — printed only to expose the size of the clustering trap — and `clust_infl` is
`|t_naive / t_declust|`. `ETA_y_corr` is the ETA after correcting for the panel ending
2026-07-31 (see §3); the uncorrected `ETA_y` is the headline because it is conservative.

`ETA_y` is blank for 5 period-restricted diagnostic cells (the `ERA_*` splits): their
window ends before the 2026-03-01..2026-09-01 event-rate measurement window, so their
recent event rate is 0 by construction. `RESULTS.json` carries
`eta_forward_confirmation_note` saying so on each of them rather than leaving a bare null
or inventing a number; read the corresponding full-sample cell's ETA instead.

Total mechanisms run: **332** — 287 live, 45 void/superseded (§2). Classed better than `WEAK`: **113**. `VALIDATED_FOR_FORWARD`: **0**.

### 9.1 All mechanisms by family and verdict

| family | COST_FRAGILE | DEAD | UNCONFIRMABLE_IN_HORIZON | WEAK | TOTAL |
|---|---|---|---|---|---|
| A_FUNDING_CLOCK | 7 | 2 | 0 | 8 | 17 |
| A_FUNDING_CLOCK_DEPTH | 23 | 14 | 0 | 38 | 75 |
| B_SESSION_CLOCK | 2 | 20 | 0 | 10 | 32 |
| B_SESSION_CLOCK_FINAL | 8 | 9 | 0 | 26 | 43 |
| CDEF_WEEKEND_MONTHEND_EVENT | 2 | 9 | 0 | 8 | 19 |
| CLOCK_MAP_DEPTH | 59 | 1 | 1 | 16 | 77 |
| CLOCK_MAP_V2 | 11 | 4 | 0 | 9 | 24 |

### 9.2 Every mechanism better than `WEAK`, all §2 columns

| mechanism | family | n_raw | L1 | L2 | L3 | gross | net14 | net28 | net_2leg | net_2leg_str56 | t_declust | t_naive | clust_infl | ci95 | ex_best_yr | n_req_days | ep/wk | ETA_y | ETA_y_corr | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CLK_h15_hold12h_q20_gap0h | CLOCK_MAP_DEPTH | 2188 | 24149 | 2188 | 313 | -61.891 | -75.891 | -89.891 | -89.891 | -117.891 | -6.513 | -6.513 | 1 | [-82.0, -42.6] | -70.771 | 1619.6 | 5.783 | 5.37 | 4.47 | UNCONFIRMABLE_IN_HORIZON |
| CLK_h14_hold12h_q20_gap0h | CLOCK_MAP_DEPTH | 2188 | 24149 | 2188 | 313 | -55.688 | -69.688 | -83.688 | -83.688 | -111.688 | -5.839 | -5.839 | 1 | [-74.8, -36.0] | -61.432 | 2014.9 | 5.783 | 6.68 | 5.55 | COST_FRAGILE |
| CLK_h15_hold12h_q20_gap1h | CLOCK_MAP_DEPTH | 2188 | 24149 | 2188 | 313 | -51.761 | -65.761 | -79.761 | -79.761 | -107.761 | -5.795 | -5.795 | 1 | [-68.9, -35.0] | -60.979 | 2045.5 | 5.783 | 6.78 | 5.64 | COST_FRAGILE |
| CLK_h14_hold12h_q20_gap1h | CLOCK_MAP_DEPTH | 2188 | 24149 | 2188 | 313 | -51.359 | -65.359 | -79.359 | -79.359 | -107.359 | -5.775 | -5.775 | 1 | [-68.3, -33.1] | -57.513 | 2059.6 | 5.783 | 6.83 | 5.68 | COST_FRAGILE |
| CLK_h15_hold8h_q20_gap0h | CLOCK_MAP_DEPTH | 2189 | 24240 | 2189 | 313 | -40.811 | -54.811 | -68.811 | -68.811 | -96.811 | -5.219 | -5.219 | 1 | [-57.6, -25.1] | -47.642 | 2522.7 | 5.821 | 8.31 | 6.91 | COST_FRAGILE |
| CLK_h15_hold12h_q10_gap0h | CLOCK_MAP_DEPTH | 2188 | 48091 | 2188 | 313 | -40.181 | -54.181 | -68.181 | -68.181 | -96.181 | -6.785 | -6.785 | 1 | [-51.7, -28.5] | -46.177 | 1492.1 | 5.783 | 4.95 | 4.12 | COST_FRAGILE |
| CLK_h02_hold12h_q20_gap0h | CLOCK_MAP_DEPTH | 2188 | 24149 | 2188 | 313 | 39.632 | 25.632 | 11.632 | 11.632 | -16.368 | 4.486 | 4.486 | 1 | [23.6, 56.4] | 30.077 | 3414 | 5.821 | 11.24 | 9.35 | COST_FRAGILE |
| CLK_h02_hold12h_q20_gap1h | CLOCK_MAP_DEPTH | 2188 | 24149 | 2188 | 313 | 38.847 | 24.847 | 10.847 | 10.847 | -17.153 | 4.694 | 4.694 | 1 | [23.3, 54.7] | 28.903 | 3117.5 | 5.821 | 10.26 | 8.53 | COST_FRAGILE |
| CLK_h14_hold12h_q10_gap0h | CLOCK_MAP_DEPTH | 2188 | 48091 | 2188 | 313 | -35.742 | -49.742 | -63.742 | -63.742 | -91.742 | -5.914 | -5.914 | 1 | [-49.0, -22.6] | -40.21 | 1964 | 5.783 | 6.51 | 5.41 | COST_FRAGILE |
| CLK_h02_hold8h_q20_gap0h | CLOCK_MAP_DEPTH | 2188 | 24149 | 2188 | 313 | 34.811 | 20.811 | 6.811 | 6.811 | -21.189 | 4.763 | 4.763 | 1 | [21.4, 49.4] | 26.48 | 3028 | 5.821 | 9.97 | 8.29 | COST_FRAGILE |
| CLK_h14_hold8h_q20_gap0h | CLOCK_MAP_DEPTH | 2189 | 24240 | 2189 | 313 | -34.083 | -48.083 | -62.083 | -62.083 | -90.083 | -4.433 | -4.433 | 1 | [-49.0, -18.8] | -40.201 | 3496.8 | 5.821 | 11.51 | 9.57 | COST_FRAGILE |
| CLK_h02_hold8h_q20_gap1h | CLOCK_MAP_DEPTH | 2188 | 24149 | 2188 | 313 | 34.026 | 20.026 | 6.026 | 6.026 | -21.974 | 5.038 | 5.038 | 1 | [20.6, 47.4] | 25.307 | 2706 | 5.821 | 8.91 | 7.41 | COST_FRAGILE |
| CLK_h14_hold12h_q10_gap1h | CLOCK_MAP_DEPTH | 2188 | 48091 | 2188 | 313 | -33.713 | -47.713 | -61.713 | -61.713 | -89.713 | -5.894 | -5.894 | 1 | [-46.1, -21.6] | -37.595 | 1977.7 | 5.783 | 6.55 | 5.45 | COST_FRAGILE |
| CLK_h15_hold12h_q10_gap1h | CLOCK_MAP_DEPTH | 2188 | 48091 | 2188 | 313 | -32.648 | -46.648 | -60.648 | -60.648 | -88.648 | -5.796 | -5.796 | 1 | [-44.4, -21.2] | -37.947 | 2045.1 | 5.783 | 6.78 | 5.64 | COST_FRAGILE |
| CLK_COMBINED_q20 | CLOCK_MAP_DEPTH | 4376 | 193134 | 2188 | 313 | 31.252 | 17.252 | 3.252 | 3.252 | -24.748 | 5.698 | 5.729 | 1.01 | [20.0, 43.0] | 21.095 | 2115.5 | 5.821 | 6.97 | 5.8 | COST_FRAGILE |
| CLK_h15_hold8h_q20_gap1h | CLOCK_MAP_DEPTH | 2189 | 24240 | 2189 | 313 | -30.811 | -44.811 | -58.811 | -58.811 | -86.811 | -4.324 | -4.324 | 1 | [-44.8, -17.4] | -38.119 | 3676.5 | 5.821 | 12.11 | 10.07 | COST_FRAGILE |
| CLK_h14_hold8h_q20_gap1h | CLOCK_MAP_DEPTH | 2189 | 24240 | 2189 | 313 | -29.967 | -43.967 | -57.967 | -57.967 | -85.967 | -4.269 | -4.269 | 1 | [-45.2, -16.0] | -35.427 | 3771.6 | 5.821 | 12.42 | 10.33 | COST_FRAGILE |
| CLK_h02_hold12h_q10_gap0h | CLOCK_MAP_DEPTH | 2188 | 48091 | 2188 | 313 | 28.287 | 14.287 | 0.287 | 0.287 | -27.713 | 4.829 | 4.829 | 1 | [17.7, 39.5] | 21.016 | 2946.2 | 5.821 | 9.7 | 8.07 | COST_FRAGILE |
| CLK_h02_hold12h_q10_gap1h | CLOCK_MAP_DEPTH | 2188 | 48091 | 2188 | 313 | 27.959 | 13.959 | -0.041 | -0.041 | -28.041 | 5.063 | 5.063 | 1 | [18.7, 38.6] | 20.594 | 2679.5 | 5.821 | 8.82 | 7.33 | COST_FRAGILE |
| CLK_h15_hold8h_q10_gap0h | CLOCK_MAP_DEPTH | 2189 | 48346 | 2189 | 313 | -27.84 | -41.84 | -55.84 | -55.84 | -83.84 | -5.857 | -5.857 | 1 | [-37.7, -17.9] | -33.883 | 2003.2 | 5.821 | 6.6 | 5.49 | COST_FRAGILE |
| CLK_h15_hold4h_q20_gap0h | CLOCK_MAP_DEPTH | 2189 | 24240 | 2189 | 313 | -26.244 | -40.244 | -54.244 | -54.244 | -82.244 | -4.69 | -4.69 | 1 | [-37.3, -15.0] | -30.107 | 3124.7 | 5.821 | 10.29 | 8.56 | COST_FRAGILE |
| CLK_h14_hold4h_q20_gap0h | CLOCK_MAP_DEPTH | 2189 | 24240 | 2189 | 313 | -26.233 | -40.233 | -54.233 | -54.233 | -82.233 | -4.551 | -4.551 | 1 | [-36.8, -15.4] | -30.455 | 3318.5 | 5.821 | 10.93 | 9.09 | COST_FRAGILE |
| CLK_h02_hold8h_q10_gap0h | CLOCK_MAP_DEPTH | 2188 | 48091 | 2188 | 313 | 25.515 | 11.515 | -2.485 | -2.485 | -30.485 | 5.532 | 5.532 | 1 | [16.3, 35.3] | 18.657 | 2244.8 | 5.821 | 7.39 | 6.14 | COST_FRAGILE |
| CLK_COMBINED_q10 | CLOCK_MAP_DEPTH | 4376 | 193134 | 2188 | 313 | 25.2 | 11.2 | -2.8 | -2.8 | -30.8 | 7.54 | 7.519 | 1 | [18.4, 32.1] | 17.954 | 1208.5 | 5.821 | 3.98 | 3.31 | COST_FRAGILE |
| CLK_h02_hold8h_q10_gap1h | CLOCK_MAP_DEPTH | 2188 | 48091 | 2188 | 313 | 25.187 | 11.187 | -2.813 | -2.813 | -30.813 | 5.886 | 5.886 | 1 | [16.4, 32.9] | 18.234 | 1983 | 5.821 | 6.53 | 5.43 | COST_FRAGILE |
| CLK_h14_hold12h_q5_gap0h | CLOCK_MAP_DEPTH | 2188 | 96316 | 2188 | 313 | -24.973 | -38.973 | -52.973 | -52.973 | -80.973 | -6.337 | -6.337 | 1 | [-33.7, -15.7] | -28.078 | 1710.8 | 5.783 | 5.67 | 4.71 | COST_FRAGILE |
| CLK_h15_hold12h_q5_gap0h | CLOCK_MAP_DEPTH | 2188 | 96316 | 2188 | 313 | -24.937 | -38.937 | -52.937 | -52.937 | -80.937 | -6.75 | -6.75 | 1 | [-32.5, -17.2] | -28.339 | 1507.5 | 5.783 | 5 | 4.16 | COST_FRAGILE |
| CLK_h14_hold12h_q5_gap1h | CLOCK_MAP_DEPTH | 2188 | 96316 | 2188 | 313 | -23.985 | -37.985 | -51.985 | -51.985 | -79.985 | -6.496 | -6.496 | 1 | [-32.6, -15.8] | -27.258 | 1627.7 | 5.783 | 5.39 | 4.48 | COST_FRAGILE |
| CLK_h14_hold4h_q20_gap1h | CLOCK_MAP_DEPTH | 2189 | 24240 | 2189 | 313 | -22.117 | -36.117 | -50.117 | -50.117 | -78.117 | -4.403 | -4.403 | 1 | [-32.5, -11.5] | -25.681 | 3545.8 | 5.821 | 11.67 | 9.7 | COST_FRAGILE |
| CLK_h02_hold12h_q5_gap0h | CLOCK_MAP_DEPTH | 2188 | 96316 | 2188 | 313 | 21.702 | 7.702 | -6.298 | -6.298 | -34.298 | 6.006 | 6.006 | 1 | [15.0, 29.0] | 16.17 | 1904.2 | 5.821 | 6.27 | 5.21 | COST_FRAGILE |
| CLK_h02_hold12h_q5_gap1h | CLOCK_MAP_DEPTH | 2188 | 96316 | 2188 | 313 | 21.535 | 7.535 | -6.465 | -6.465 | -34.465 | 6.288 | 6.288 | 1 | [15.5, 27.5] | 15.84 | 1737.3 | 5.821 | 5.72 | 4.76 | COST_FRAGILE |
| CLK_h14_hold8h_q10_gap0h | CLOCK_MAP_DEPTH | 2189 | 48346 | 2189 | 313 | -21.526 | -35.526 | -49.526 | -49.526 | -77.526 | -4.335 | -4.335 | 1 | [-31.6, -11.0] | -24.971 | 3656.6 | 5.821 | 12.04 | 10.01 | COST_FRAGILE |
| CLK_h15_hold8h_q10_gap1h | CLOCK_MAP_DEPTH | 2189 | 48346 | 2189 | 313 | -20.31 | -34.31 | -48.31 | -48.31 | -76.31 | -4.646 | -4.646 | 1 | [-29.0, -11.5] | -25.294 | 3184.4 | 5.821 | 10.48 | 8.71 | COST_FRAGILE |
| CLK_h15_hold12h_q5_gap1h | CLOCK_MAP_DEPTH | 2188 | 96316 | 2188 | 313 | -19.904 | -33.904 | -47.904 | -47.904 | -75.904 | -5.646 | -5.646 | 1 | [-26.8, -12.5] | -23.625 | 2154.8 | 5.783 | 7.14 | 5.94 | COST_FRAGILE |
| CLK_h04_hold8h_q10_gap0h | CLOCK_MAP_DEPTH | 2188 | 48091 | 2188 | 313 | 19.565 | 5.565 | -8.435 | -8.435 | -36.435 | 4.174 | 4.174 | 1 | [10.9, 28.4] | 16.124 | 3942.8 | 5.821 | 12.98 | 10.79 | COST_FRAGILE |
| CLK_h14_hold8h_q10_gap1h | CLOCK_MAP_DEPTH | 2189 | 48346 | 2189 | 313 | -19.388 | -33.388 | -47.388 | -47.388 | -75.388 | -4.231 | -4.231 | 1 | [-29.8, -10.0] | -22.837 | 3840 | 5.821 | 12.64 | 10.51 | COST_FRAGILE |
| CLOCKMAP_h02 | CLOCK_MAP_V2 | 2188 | 96316 | 2188 | 313 | 19.129 | 5.129 | -8.871 | -8.871 | -36.871 | 6.656 | 6.656 | 1 | [13.3, 24.7] | 13.527 | 1550.4 | 5.821 | 5.1 | 4.24 | COST_FRAGILE |
| CLK_h02_hold8h_q5_gap0h | CLOCK_MAP_DEPTH | 2188 | 96316 | 2188 | 313 | 19.129 | 5.129 | -8.871 | -8.871 | -36.871 | 6.656 | 6.656 | 1 | [13.3, 25.1] | 13.527 | 1550.4 | 5.821 | 5.1 | 4.24 | COST_FRAGILE |
| CLK_h02_hold8h_q5_gap1h | CLOCK_MAP_DEPTH | 2188 | 96316 | 2188 | 313 | 18.963 | 4.963 | -9.037 | -9.037 | -37.037 | 7.03 | 7.03 | 1 | [13.7, 24.2] | 13.197 | 1390 | 5.821 | 4.58 | 3.81 | COST_FRAGILE |
| CLK_h15_hold4h_q10_gap0h | CLOCK_MAP_DEPTH | 2189 | 48346 | 2189 | 313 | -18.798 | -32.798 | -46.798 | -46.798 | -74.798 | -5.176 | -5.176 | 1 | [-26.1, -11.9] | -21.632 | 2565.7 | 5.821 | 8.45 | 7.03 | COST_FRAGILE |
| CLOCKMAP_h03 | CLOCK_MAP_V2 | 2188 | 96316 | 2188 | 313 | 17.197 | 3.197 | -10.803 | -10.803 | -38.803 | 5.886 | 5.886 | 1 | [11.0, 23.1] | 9.961 | 1983 | 5.821 | 6.53 | 5.43 | COST_FRAGILE |
| CLK_h14_hold4h_q10_gap0h | CLOCK_MAP_DEPTH | 2189 | 48346 | 2189 | 313 | -17.129 | -31.129 | -45.129 | -45.129 | -73.129 | -4.47 | -4.47 | 1 | [-24.8, -9.1] | -19.302 | 3440 | 5.821 | 11.33 | 9.42 | COST_FRAGILE |
| CLK_COMBINED_h03rev_h15con_FULL | CLOCK_MAP_DEPTH | 4376 | 193134 | 2188 | 313 | 16.394 | 2.394 | -11.606 | -11.606 | -39.606 | 8.049 | 7.897 | 0.98 | [12.3, 21.0] | 11.465 | 1060.4 | 5.821 | 3.49 | 2.9 | COST_FRAGILE |
| CLK_COMBINED_PLACEBO_shuffled_continuation_arm | CLOCK_MAP_DEPTH | 4376 | 193134 | 2188 | 313 | 16.394 | 2.394 | -11.606 | -11.606 | -39.606 | 7.936 | 7.897 | 1 | [12.3, 20.3] | 13.222 | 1090.8 | 5.821 | 3.59 | 2.99 | COST_FRAGILE |
| CLOCKMAP_h01 | CLOCK_MAP_V2 | 2188 | 96316 | 2188 | 313 | 16.249 | 2.249 | -11.751 | -11.751 | -39.751 | 5.515 | 5.515 | 1 | [10.1, 22.5] | 11.524 | 2258.2 | 5.821 | 7.44 | 6.19 | COST_FRAGILE |
| CLK_h02_hold4h_q20_gap1h | CLOCK_MAP_DEPTH | 2188 | 24149 | 2188 | 313 | 16.106 | 2.106 | -11.894 | -11.894 | -39.894 | 3.576 | 3.576 | 1 | [7.4, 25.5] | 12.656 | 5371.9 | 5.821 | 17.69 | 14.71 | COST_FRAGILE |
| CLK_h04_hold8h_q5_gap0h | CLOCK_MAP_DEPTH | 2188 | 96316 | 2188 | 313 | 15.742 | 1.742 | -12.258 | -12.258 | -40.258 | 5.311 | 5.311 | 1 | [10.2, 21.7] | 14.135 | 2435.6 | 5.821 | 8.02 | 6.67 | COST_FRAGILE |
| CLOCKMAP_h04 | CLOCK_MAP_V2 | 2188 | 96316 | 2188 | 313 | 15.742 | 1.742 | -12.258 | -12.258 | -40.258 | 5.311 | 5.311 | 1 | [10.5, 21.4] | 14.135 | 2435.6 | 5.821 | 8.02 | 6.67 | COST_FRAGILE |
| CLK_h15_hold8h_q5_gap0h | CLOCK_MAP_DEPTH | 2189 | 96818 | 2189 | 313 | -15.628 | -29.628 | -43.628 | -43.628 | -71.628 | -5.298 | -5.298 | 1 | [-21.7, -9.5] | -18.736 | 2448.1 | 5.821 | 8.06 | 6.7 | COST_FRAGILE |
| CLOCKMAP_h15 | CLOCK_MAP_V2 | 2189 | 96818 | 2189 | 313 | -15.628 | -29.628 | -43.628 | -43.628 | -71.628 | -5.298 | -5.298 | 1 | [-21.4, -9.6] | -18.736 | 2448.1 | 5.821 | 8.06 | 6.7 | COST_FRAGILE |
| CLK_h14_hold8h_q5_gap0h | CLOCK_MAP_DEPTH | 2189 | 96818 | 2189 | 313 | -15.435 | -29.435 | -43.435 | -43.435 | -71.435 | -4.787 | -4.787 | 1 | [-21.7, -8.7] | -17.631 | 2999.6 | 5.821 | 9.88 | 8.22 | COST_FRAGILE |
| CLOCKMAP_h14 | CLOCK_MAP_V2 | 2189 | 96818 | 2189 | 313 | -15.435 | -29.435 | -43.435 | -43.435 | -71.435 | -4.787 | -4.787 | 1 | [-21.7, -8.4] | -17.631 | 2999.6 | 5.821 | 9.88 | 8.22 | COST_FRAGILE |
| CLK_h14_hold4h_q10_gap1h | CLOCK_MAP_DEPTH | 2189 | 48346 | 2189 | 313 | -14.991 | -28.991 | -42.991 | -42.991 | -70.991 | -4.397 | -4.397 | 1 | [-22.1, -7.8] | -17.169 | 3555.1 | 5.821 | 11.71 | 9.74 | COST_FRAGILE |
| E1_cascade_fwd_4h_US | CDEF_WEEKEND_MONTHEND_EVENT | 18063 | 14891 | 1616 | 273 | 14.767 | 0.767 | -13.233 | 0.767 | -13.233 | 4.154 | 2.632 | 0.63 | [7.4, 22.0] | 13.221 | 2940.6 | 4.755 | 11.85 | 9.85 | COST_FRAGILE |
| B2_EU_to_US_gap1h_FULL | B_SESSION_CLOCK_FINAL | 2202 | 100186 | 2202 | 316 | -14.44 | -28.44 | -42.44 | -42.44 | -70.44 | -5.076 | -5.076 | 1 | [-19.7, -8.7] | -16.298 | 2683.1 | 5.821 | 8.83 | 7.34 | COST_FRAGILE |
| CLOCKMAP_h00 | CLOCK_MAP_V2 | 2188 | 96316 | 2188 | 313 | 14.431 | 0.431 | -13.569 | -13.569 | -41.569 | 4.972 | 4.972 | 1 | [9.2, 20.5] | 10.41 | 2779.3 | 5.821 | 9.15 | 7.61 | COST_FRAGILE |
| CLK_h04_hold12h_q5_gap0h | CLOCK_MAP_DEPTH | 2188 | 96316 | 2188 | 313 | 14.381 | 0.381 | -13.619 | -13.619 | -41.619 | 3.943 | 3.943 | 1 | [7.3, 21.0] | 11.093 | 4418.4 | 5.821 | 14.55 | 12.1 | COST_FRAGILE |
| CLK_h14_hold8h_q5_gap1h | CLOCK_MAP_DEPTH | 2189 | 96818 | 2189 | 313 | -14.36 | -28.36 | -42.36 | -42.36 | -70.36 | -4.895 | -4.895 | 1 | [-20.7, -8.4] | -16.713 | 2868.3 | 5.821 | 9.44 | 7.85 | COST_FRAGILE |
| B2_LATE_to_ASIA | B_SESSION_CLOCK | 2200 | 99659 | 2200 | 315 | 14.077 | 0.077 | -13.923 | -13.923 | -41.923 | 5.213 | 5.213 | 1 | [8.3, 19.9] | 12.152 | 2541.4 | 5.821 | 8.37 | 6.96 | COST_FRAGILE |
| B2_LATE_to_ASIA_gap0h_FULL | B_SESSION_CLOCK_FINAL | 2200 | 99659 | 2200 | 315 | 14.077 | 0.077 | -13.923 | -13.923 | -41.923 | 5.213 | 5.213 | 1 | [8.7, 20.0] | 12.152 | 2541.4 | 5.821 | 8.37 | 6.96 | COST_FRAGILE |
| CLOCKMAP_h05 | CLOCK_MAP_V2 | 2188 | 96316 | 2188 | 313 | 12.819 | -1.181 | -15.181 | -15.181 | -43.181 | 4.047 | 4.047 | 1 | [6.1, 19.5] | 10.598 | 4193.6 | 5.821 | 13.81 | 11.48 | COST_FRAGILE |
| E1_cascade_fwd_4h_ASIA | CDEF_WEEKEND_MONTHEND_EVENT | 8282 | 7233 | 1404 | 259 | 12.77 | -1.23 | -15.23 | -1.23 | -15.23 | 3.307 | 5.341 | 1.61 | [5.9, 20.1] | 8.885 | 4030.3 | 4.603 | 16.78 | 13.95 | COST_FRAGILE |
| CLK_h14_hold4h_q5_gap0h | CLOCK_MAP_DEPTH | 2189 | 96818 | 2189 | 313 | -12.56 | -26.56 | -40.56 | -40.56 | -68.56 | -5.112 | -5.112 | 1 | [-17.6, -7.0] | -14.428 | 2629.6 | 5.821 | 8.66 | 7.2 | COST_FRAGILE |
| CLK_h02_hold4h_q10_gap0h | CLOCK_MAP_DEPTH | 2188 | 48091 | 2188 | 313 | 12.297 | -1.703 | -15.703 | -15.703 | -43.703 | 3.616 | 3.616 | 1 | [5.5, 19.1] | 9.578 | 5254.9 | 5.821 | 17.3 | 14.39 | COST_FRAGILE |
| B2_EU_to_US_gap2h_FULL | B_SESSION_CLOCK_FINAL | 2202 | 100186 | 2202 | 316 | -12.098 | -26.098 | -40.098 | -40.098 | -68.098 | -4.674 | -4.674 | 1 | [-17.1, -6.6] | -13.634 | 3164.6 | 5.821 | 10.42 | 8.66 | COST_FRAGILE |
| CLK_h02_hold4h_q10_gap1h | CLOCK_MAP_DEPTH | 2188 | 48091 | 2188 | 313 | 11.969 | -2.031 | -16.031 | -16.031 | -44.031 | 4.235 | 4.235 | 1 | [6.2, 18.0] | 9.156 | 3830.5 | 5.821 | 12.61 | 10.49 | COST_FRAGILE |
| CLK_h04_hold8h_q5_gap1h | CLOCK_MAP_DEPTH | 2188 | 96316 | 2188 | 313 | 11.609 | -2.391 | -16.391 | -16.391 | -44.391 | 4.318 | 4.318 | 1 | [6.6, 16.9] | 9.888 | 3684.2 | 5.821 | 12.13 | 10.09 | COST_FRAGILE |
| CLK_h14_hold4h_q5_gap1h | CLOCK_MAP_DEPTH | 2189 | 96818 | 2189 | 313 | -11.485 | -25.485 | -39.485 | -39.485 | -67.485 | -5.428 | -5.428 | 1 | [-16.1, -7.2] | -13.115 | 2332.7 | 5.821 | 7.68 | 6.39 | COST_FRAGILE |
| EUUS_ERA_2023_24 | B_SESSION_CLOCK_FINAL | 731 | 39157 | 731 | 106 | -11.281 | -25.281 | -39.281 | -39.281 | -67.281 | -3.228 | -3.228 | 1 | [-17.9, -5.1] | -12.151 | 2203.2 | 0 | — | — | COST_FRAGILE |
| CLK_h15_hold4h_q10_gap1h | CLOCK_MAP_DEPTH | 2189 | 48346 | 2189 | 313 | -11.268 | -25.268 | -39.268 | -39.268 | -67.268 | -3.627 | -3.627 | 1 | [-17.3, -5.5] | -13.464 | 5224.3 | 5.821 | 17.2 | 14.3 | COST_FRAGILE |
| CLK_h15_hold4h_q5_gap0h | CLOCK_MAP_DEPTH | 2189 | 96818 | 2189 | 313 | -11.249 | -25.249 | -39.249 | -39.249 | -67.249 | -5.055 | -5.055 | 1 | [-15.6, -7.0] | -12.618 | 2689.6 | 5.821 | 8.86 | 7.37 | COST_FRAGILE |
| CLOCKMAP_h13 | CLOCK_MAP_V2 | 2189 | 96818 | 2189 | 313 | -10.974 | -24.974 | -38.974 | -38.974 | -66.974 | -3.443 | -3.443 | 1 | [-17.2, -4.3] | -13.558 | 5797.3 | 5.821 | 19.09 | 15.87 | COST_FRAGILE |
| B2_EU_to_US_gap0h_FULL | B_SESSION_CLOCK_FINAL | 2202 | 100186 | 2202 | 316 | -10.657 | -24.657 | -38.657 | -38.657 | -66.657 | -3.525 | -3.525 | 1 | [-16.6, -4.7] | -13.46 | 5563.2 | 5.821 | 18.32 | 15.23 | COST_FRAGILE |
| EUUS_concentration_q5 | B_SESSION_CLOCK_FINAL | 2202 | 100186 | 2202 | 316 | -10.657 | -24.657 | -38.657 | -38.657 | -66.657 | -3.525 | -3.525 | 1 | [-16.4, -5.2] | -13.46 | 5563.2 | 5.821 | 18.32 | 15.23 | COST_FRAGILE |
| B2_EU_to_US | B_SESSION_CLOCK | 2202 | 100186 | 2202 | 316 | -10.657 | -24.657 | -38.657 | -38.657 | -66.657 | -3.525 | -3.525 | 1 | [-16.7, -4.6] | -13.46 | 5563.2 | 5.821 | 18.32 | 15.23 | COST_FRAGILE |
| CLK_h15_hold8h_q5_gap1h | CLOCK_MAP_DEPTH | 2189 | 96818 | 2189 | 313 | -10.626 | -24.626 | -38.626 | -38.626 | -66.626 | -3.934 | -3.934 | 1 | [-16.2, -5.3] | -13.537 | 4440.6 | 5.821 | 14.62 | 12.16 | COST_FRAGILE |
| CLOCKMAP_h16 | CLOCK_MAP_V2 | 2189 | 96818 | 2189 | 313 | -10.42 | -24.42 | -38.42 | -38.42 | -66.42 | -3.571 | -3.571 | 1 | [-16.3, -4.8] | -13.113 | 5390.2 | 5.821 | 17.75 | 14.76 | COST_FRAGILE |
| CLK_h02_hold4h_q5_gap0h | CLOCK_MAP_DEPTH | 2188 | 96316 | 2188 | 313 | 10.25 | -3.75 | -17.75 | -17.75 | -45.75 | 4.947 | 4.947 | 1 | [6.0, 14.4] | 8.456 | 2807.1 | 5.821 | 9.24 | 7.68 | COST_FRAGILE |
| CLK_h02_hold4h_q5_gap1h | CLOCK_MAP_DEPTH | 2188 | 96316 | 2188 | 313 | 10.084 | -3.916 | -17.916 | -17.916 | -45.916 | 5.693 | 5.693 | 1 | [6.4, 13.6] | 9.24 | 2119.6 | 5.821 | 6.98 | 5.8 | COST_FRAGILE |
| CLOCKMAP_h06 | CLOCK_MAP_V2 | 2189 | 96818 | 2189 | 313 | 9.975 | -4.025 | -18.025 | -18.025 | -46.025 | 3.39 | 3.39 | 1 | [4.3, 16.3] | 7.936 | 5980.8 | 5.821 | 19.69 | 16.37 | COST_FRAGILE |
| CLK_h04_hold4h_q5_gap0h | CLOCK_MAP_DEPTH | 2188 | 96316 | 2188 | 313 | 9.569 | -4.431 | -18.431 | -18.431 | -46.431 | 4.505 | 4.505 | 1 | [5.6, 13.6] | 5.917 | 3384.5 | 5.821 | 11.14 | 9.26 | COST_FRAGILE |
| B2_LATE_to_ASIA_gap3h_FULL | B_SESSION_CLOCK_FINAL | 2200 | 99659 | 2200 | 315 | 8.668 | -5.332 | -19.332 | -19.332 | -47.332 | 4.552 | 4.552 | 1 | [5.0, 12.5] | 6.941 | 3333.6 | 5.821 | 10.98 | 9.13 | COST_FRAGILE |
| B2_LATE_to_ASIA_gap1h_FULL | B_SESSION_CLOCK_FINAL | 2200 | 99659 | 2200 | 315 | 8.462 | -5.538 | -19.538 | -19.538 | -47.538 | 3.498 | 3.498 | 1 | [3.9, 13.4] | 7.239 | 5645.2 | 5.821 | 18.59 | 15.46 | COST_FRAGILE |
| H16|basis_sig|q10|post120 | A_FUNDING_CLOCK_DEPTH | 2201 | 46042 | 2201 | 316 | 7.515 | -6.485 | -20.485 | -20.485 | -48.485 | 4.131 | 4.131 | 1 | [4.0, 11.1] | 6.322 | 4049.4 | 5.821 | 13.33 | 11.08 | COST_FRAGILE |
| ERA_2020_22|basis_sig|q10|post120 | A_FUNDING_CLOCK_DEPTH | 2679 | 33623 | 894 | 129 | 7.451 | -6.549 | -20.549 | -20.549 | -48.549 | 3.635 | 3.607 | 0.99 | [3.2, 11.8] | 6.389 | 2123.9 | 0 | — | — | COST_FRAGILE |
| H00|basis_sig|q10|pre55 | A_FUNDING_CLOCK_DEPTH | 2202 | 46032 | 2202 | 316 | 7.049 | -6.951 | -20.951 | -20.951 | -48.951 | 6.608 | 6.608 | 1 | [4.9, 9.3] | 5.608 | 1583.2 | 5.821 | 5.21 | 4.33 | COST_FRAGILE |
| ALL|basis_sig|q20|pre55 | A_FUNDING_CLOCK_DEPTH | 6603 | 52071 | 2202 | 316 | 6.384 | -7.616 | -21.616 | -21.616 | -49.616 | 5.832 | 5.913 | 1.01 | [4.2, 8.7] | 4.244 | 2032.4 | 5.821 | 6.69 | 5.56 | COST_FRAGILE |
| LIQLOW_H00|basis_sig|q10|pre55 | A_FUNDING_CLOCK_DEPTH | 2070 | 14995 | 2070 | 297 | 6.367 | -7.633 | -21.633 | -21.633 | -49.633 | 3.534 | 3.534 | 1 | [3.0, 9.9] | 3.785 | 5204 | 5.821 | 17.13 | 14.24 | COST_FRAGILE |
| LIQ_LOW|basis_sig|q10|post120 | A_FUNDING_CLOCK_DEPTH | 6207 | 32950 | 2070 | 297 | 6.055 | -7.945 | -21.945 | -21.945 | -49.945 | 3.644 | 3.619 | 0.99 | [2.6, 9.9] | 5.638 | 4892.9 | 5.821 | 16.11 | 13.4 | COST_FRAGILE |
| ERA_2020_22|basis_sig|q10|pre55 | A_FUNDING_CLOCK_DEPTH | 2679 | 33623 | 894 | 129 | 5.036 | -8.964 | -22.964 | -22.964 | -50.964 | 3.739 | 3.834 | 1.03 | [2.1, 8.0] | 2.805 | 2007.9 | 0 | — | — | COST_FRAGILE |
| ALL|fr_prev|q20|pre55 | A_FUNDING_CLOCK_DEPTH | 6606 | 53094 | 2202 | 316 | 5 | -9 | -23 | -23 | -51 | 4.043 | 4.142 | 1.02 | [2.7, 7.6] | 3.796 | 4228.7 | 5.821 | 13.92 | 11.57 | COST_FRAGILE |
| LIQ_LOW|basis_sig|q10|pre55 | A_FUNDING_CLOCK_DEPTH | 6207 | 32950 | 2070 | 297 | 4.731 | -9.269 | -23.269 | -23.269 | -51.269 | 3.992 | 3.961 | 0.99 | [2.5, 6.9] | 4.682 | 4078.3 | 5.821 | 13.43 | 11.17 | COST_FRAGILE |
| A3c_pure_funding_carry_component | A_FUNDING_CLOCK | 6606 | 173326 | 2202 | 316 | 4.045 | -9.955 | -23.955 | -23.955 | -51.955 | 55.352 | 87.395 | 1.58 | [3.7, 4.4] | 3.722 | 22.6 | 5.821 | 0.07 | 0.06 | COST_FRAGILE |
| LIQLOW_H00|basis_sig|q10|pre15 | A_FUNDING_CLOCK_DEPTH | 2070 | 14995 | 2070 | 297 | 3.985 | -10.015 | -24.015 | -24.015 | -52.015 | 4.294 | 4.294 | 1 | [2.2, 5.7] | 2.777 | 3525.1 | 5.821 | 11.61 | 9.65 | COST_FRAGILE |
| ALL|basis_sig|q10|pre55 | A_FUNDING_CLOCK_DEPTH | 6603 | 95951 | 2202 | 316 | 3.985 | -10.015 | -24.015 | -24.015 | -52.015 | 5.834 | 5.903 | 1.01 | [2.6, 5.5] | 3.102 | 2031.2 | 5.821 | 6.69 | 5.56 | COST_FRAGILE |
| A5_pre_settle_hour_00 | A_FUNDING_CLOCK | 2202 | 99707 | 2202 | 316 | 3.793 | -10.207 | -24.207 | -24.207 | -52.207 | 5.266 | 5.266 | 1 | [2.4, 5.2] | 2.902 | 2493.3 | 5.821 | 8.21 | 6.83 | COST_FRAGILE |
| A3_straddle_with_explicit_funding_carry | A_FUNDING_CLOCK | 6606 | 173326 | 2202 | 316 | 3.717 | -10.283 | -24.283 | -24.283 | -52.283 | 5.109 | 5.201 | 1.02 | [2.2, 5.2] | 2.623 | 2648.9 | 5.821 | 8.72 | 7.25 | COST_FRAGILE |
| ALL|basis_sig|q10|post120 | A_FUNDING_CLOCK_DEPTH | 6603 | 95951 | 2202 | 316 | 3.588 | -10.412 | -24.412 | -24.412 | -52.412 | 3.536 | 3.496 | 0.99 | [1.5, 5.6] | 2.896 | 5530.2 | 5.821 | 18.21 | 15.14 | COST_FRAGILE |
| ALL|basis_sig|q5|post120 | A_FUNDING_CLOCK_DEPTH | 6603 | 163942 | 2202 | 316 | 3.237 | -10.763 | -24.763 | -24.763 | -52.763 | 4.694 | 4.854 | 1.03 | [1.8, 4.9] | 2.65 | 3138.3 | 5.821 | 10.33 | 8.59 | COST_FRAGILE |
| A6_pre_basis_rank | A_FUNDING_CLOCK | 6603 | 163964 | 2202 | 316 | 3.17 | -10.83 | -24.83 | -24.83 | -52.83 | 7.247 | 7.272 | 1 | [2.2, 4.1] | 2.336 | 1316.5 | 5.821 | 4.33 | 3.6 | COST_FRAGILE |
| ALL|basis_sig|q5|pre55 | A_FUNDING_CLOCK_DEPTH | 6603 | 163942 | 2202 | 316 | 3.144 | -10.856 | -24.856 | -24.856 | -52.856 | 7.203 | 7.227 | 1 | [2.2, 4.1] | 2.312 | 1332.4 | 5.821 | 4.39 | 3.65 | COST_FRAGILE |
| ALL|fr_prev|q10|pre55 | A_FUNDING_CLOCK_DEPTH | 6606 | 100000 | 2202 | 316 | 2.9 | -11.1 | -25.1 | -25.1 | -53.1 | 3.788 | 3.97 | 1.05 | [1.4, 4.5] | 2.034 | 4817.9 | 5.821 | 15.86 | 13.19 | COST_FRAGILE |
| ERA_2020_22|basis_sig|q10|pre15 | A_FUNDING_CLOCK_DEPTH | 2679 | 33623 | 894 | 129 | 2.891 | -11.109 | -25.109 | -25.109 | -53.109 | 4.268 | 4.298 | 1.01 | [1.4, 4.2] | 1.966 | 1540.5 | 0 | — | — | COST_FRAGILE |
| ERA_2023_24|basis_sig|q10|pre55 | A_FUNDING_CLOCK_DEPTH | 2193 | 38699 | 731 | 106 | 2.83 | -11.17 | -25.17 | -25.17 | -53.17 | 3.73 | 3.612 | 0.97 | [1.2, 4.3] | 2.731 | 1649.4 | 0 | — | — | COST_FRAGILE |
| H00|basis_sig|q10|pre15 | A_FUNDING_CLOCK_DEPTH | 2202 | 46032 | 2202 | 316 | 2.624 | -11.376 | -25.376 | -25.376 | -53.376 | 4.933 | 4.933 | 1 | [1.5, 3.7] | 2.133 | 2841.4 | 5.821 | 9.36 | 7.78 | COST_FRAGILE |
| ALL|basis_sig|q20|pre15 | A_FUNDING_CLOCK_DEPTH | 6603 | 52071 | 2202 | 316 | 2.405 | -11.595 | -25.595 | -25.595 | -53.595 | 4.49 | 4.466 | 0.99 | [1.3, 3.5] | 1.58 | 3429.4 | 5.821 | 11.29 | 9.39 | COST_FRAGILE |
| ALL|fr_prev|q5|pre55 | A_FUNDING_CLOCK_DEPTH | 6606 | 173450 | 2202 | 316 | 2.137 | -11.863 | -25.863 | -25.863 | -53.863 | 4.411 | 4.657 | 1.06 | [1.1, 3.2] | 1.202 | 3553.2 | 5.821 | 11.7 | 9.73 | COST_FRAGILE |
| A1_pre_settlement_drift_funding_rank | A_FUNDING_CLOCK | 6606 | 173326 | 2202 | 316 | 1.973 | -12.027 | -26.027 | -26.027 | -54.027 | 4.119 | 4.316 | 1.05 | [1.0, 3.0] | 1.156 | 4074.8 | 5.821 | 13.42 | 11.16 | COST_FRAGILE |
| A1w_pre_winsor10 | A_FUNDING_CLOCK | 6606 | 173326 | 2202 | 316 | 1.836 | -12.164 | -26.164 | -26.164 | -54.164 | 4.138 | 4.319 | 1.04 | [0.9, 2.8] | 0.998 | 4038.3 | 5.821 | 13.3 | 11.06 | COST_FRAGILE |
| A6b_post_basis_rank | A_FUNDING_CLOCK | 6603 | 163964 | 2202 | 316 | 1.669 | -12.331 | -26.331 | -26.331 | -54.331 | 3.476 | 3.444 | 0.99 | [0.7, 2.7] | 1.431 | 5722.6 | 5.821 | 18.84 | 15.67 | COST_FRAGILE |
| ALL|basis_sig|q10|pre15 | A_FUNDING_CLOCK_DEPTH | 6603 | 95951 | 2202 | 316 | 1.636 | -12.364 | -26.364 | -26.364 | -54.364 | 4.897 | 4.903 | 1 | [0.9, 2.3] | 1.204 | 2882.8 | 5.821 | 9.49 | 7.89 | COST_FRAGILE |
| ALL|basis_sig|q5|pre15 | A_FUNDING_CLOCK_DEPTH | 6603 | 163942 | 2202 | 316 | 1.109 | -12.891 | -26.891 | -26.891 | -54.891 | 4.959 | 5.006 | 1.01 | [0.6, 1.6] | 0.767 | 2810.8 | 5.821 | 9.25 | 7.69 | COST_FRAGILE |
| ALL|basis_sig|q5|post15 | A_FUNDING_CLOCK_DEPTH | 6603 | 163942 | 2202 | 316 | 1.032 | -12.968 | -26.968 | -26.968 | -54.968 | 3.584 | 3.553 | 0.99 | [0.4, 1.7] | 0.745 | 5381.3 | 5.821 | 17.72 | 14.73 | COST_FRAGILE |

**Year-by-year (gross bps) for the top rows:**

| mechanism | year_by_year |
|---|---|
| CLK_h15_hold12h_q20_gap0h | 20:-129 21:-118 22:-63 23:-23 24:-18 25:-30 26:-113 |
| CLK_h14_hold12h_q20_gap0h | 20:-73 21:-122 22:-42 23:-27 24:-27 25:-27 26:-101 |
| CLK_h15_hold12h_q20_gap1h | 20:-104 21:-116 22:-52 23:-18 24:-20 25:-6 26:-97 |
| CLK_h14_hold12h_q20_gap1h | 20:-94 21:-105 22:-41 23:-26 24:-21 25:-23 26:-93 |
| CLK_h15_hold8h_q20_gap0h | 20:-62 21:-82 22:-66 23:-7 24:-9 25:-19 26:-64 |
| CLK_h15_hold12h_q10_gap0h | 20:-71 21:-88 22:-32 23:-10 24:-15 25:-20 26:-81 |
| CLK_h02_hold12h_q20_gap0h | 20:+33 21:+87 22:+38 23:+20 24:+33 25:+20 26:+44 |
| CLK_h02_hold12h_q20_gap1h | 20:+38 21:+89 22:+41 23:+18 24:+35 25:+20 26:+26 |
| CLK_h14_hold12h_q10_gap0h | 20:-55 21:-69 22:-32 23:-16 24:-22 25:-13 26:-69 |
| CLK_h02_hold8h_q20_gap0h | 20:+30 21:+76 22:+32 23:+15 24:+35 25:+22 26:+28 |
| CLK_h14_hold8h_q20_gap0h | 20:-31 21:-61 22:-49 23:-4 24:-18 25:-20 26:-69 |
| CLK_h02_hold8h_q20_gap1h | 20:+34 21:+78 22:+35 23:+13 24:+37 25:+22 26:+11 |
| CLK_h14_hold12h_q10_gap1h | 20:-63 21:-63 22:-31 23:-14 24:-16 25:-15 26:-64 |
| CLK_h15_hold12h_q10_gap1h | 20:-52 21:-82 22:-26 23:-8 24:-13 25:-6 26:-68 |
| CLK_COMBINED_q20 | 20:+40 21:+82 22:+46 23:+0 24:+18 25:+1 26:+40 |
| CLK_h15_hold8h_q20_gap1h | 20:-36 21:-79 22:-55 23:-2 24:-11 25:+6 26:-48 |
| CLK_h14_hold8h_q20_gap1h | 20:-52 21:-44 22:-48 23:-3 24:-12 25:-16 26:-62 |
| CLK_h02_hold12h_q10_gap0h | 20:+29 21:+65 22:+33 23:+7 24:+22 25:+19 26:+19 |
| CLK_h02_hold12h_q10_gap1h | 20:+41 21:+65 22:+34 23:+5 24:+24 25:+19 26:+8 |
| CLK_h15_hold8h_q10_gap0h | 20:-51 21:-58 22:-34 23:+2 24:-11 25:-13 26:-56 |

---

## 10. What I killed, and why

Negative results are the deliverable of this axis. Each of these was pushed far enough that
the kill is informative, not a shrug.

**1. `LATE→ASIA` cross-sectional reversal (+14.08bps, t=5.21, 2200 independent days) — a
bid-ask-bounce artefact.** The single strongest number the axis produced and the one that
was on track to be reported as the find of the worker. Killed by two controls that were
pre-registered as controls, not invented afterwards: it loses 53% of its size when the entry
is moved one hour off the boundary (14.08 → 6.59bps at +2h), and with the sign frozen on
TRAIN 2020-2023 it never reaches significance on TEST 2024-2026 at any gap (t = 1.14 / 1.04
/ 0.98 / 1.84). Mechanism of the artefact: the LATE session is 3 hours long and the thinnest
book of the day, so the "loser" quintile is disproportionately symbols whose last print sat
at the bid, and the next print mechanically bounces up. *Why the kill has value:* the whole
session-clock family produces spreads of this shape, and without the gap control any of them
would have been reportable.

**2. The entire first clock map (`results_clock_map.json`, 24 mechanisms) — void, and the
void answer was the comfortable one.** A pandas offset-rolling window on a microsecond
datetime index silently became an expanding window, so the "6h momentum" was the cumulative
return since listing (corr 0.035 with the true signal). Every arm came out `DEAD`/`WEAK`,
which is exactly what a tired reader expects a clock study to conclude, so nothing looked
wrong. It was caught only because `CLOCKMAP_h13` and `B2_EU_to_US` are the same trade computed
by two different code paths and disagreed. *Lesson:* build at least one deliberate redundancy
between families so they can contradict each other.

**3. The arm-vs-arm test in `family_b_depth.py` — never ran.** Every comparison read
`n_days: 0, diff_bps: NaN`. The clock claim, which is the entire point of the axis, had not
been tested at all, and the NaN went unnoticed in a printed table. *Lesson:* a NaN in a
headline comparison is a failed test, not a missing value.

**4. Weekend reversal (C1) and Sunday-evening gap (C3).** −12.10bps (t=−0.66) and −1.60bps
(t=−0.20). Not marginal — absent. `H_C1` (reversion) is contradicted in sign and in
significance. The ETA objection pre-declared in PREREG §6 never had to be invoked.

**5. Month-end and quarterly-expiry conditioning (D1, D2).** Arm-vs-arm +8.69bps (t=0.35)
and +10.63bps (t=0.45), with CI95 spanning ±40-57bps. There is no month-end or expiry-week
effect on cross-sectional autocorrelation in this universe. Pre-declared as unconfirmable at
12 and 4 episodes/year; measured anyway, and there is nothing there to be unconfirmable about.

**6. `H_C2`: cascades pay more on weekends (thin books).** Rejected in sign. The repeat-cascade
effect is +12.38bps on weekdays and −1.43bps on weekends; the difference-in-differences is
−21.24bps at t=−0.94. The weekend cascade population is only 118 independent days, so the
honest statement is "no evidence of amplification, and not enough weekend days to detect a
moderate one" — `DATA_LIMITED`, not "no effect".

**7. The funding clock as a tradeable object.** 92 cells (17 headline + 75 depth). The
maximum |gross| anywhere is 7.52bps against a 28bps 2-leg cost, and the mechanically certain
part of it (the funding cashflow itself, t=55.4) is an accounting identity. Every
pre-registered amplifier — decile/ventile concentration, the 00:00 UTC settlement hour, thin
liquidity, the triple amplifier, extreme-funding conditioning — was tried and none reaches a
quarter of the cost. This corroborates the project's standing "funding/basis is arbitraged
flat" result on a clock cut that had not been taken before.

**8. Hour-of-day drift in the market factor (B1, the pre-registered null).** Confirmed null,
and informatively so: `ex_best_year` is negative for 20 of the 24 hours. Whatever hour-of-day
drift exists in the raw index is a 2021 artefact. This is why every other mechanism on the
axis is dollar-neutral.

**9. The clock effect itself, as a trade.** This is the hardest one, because the effect is
real, large in t (h02 − h15 = +34.7bps at t=8.50 over 2188 paired days), contiguous across
the 24-hour profile, sign-stable out of sample, and — unlike LATE→ASIA — it **survives the
bounce control at every significant hour** (§5.1). It still does not pay. At quintiles the
largest arm is 19.13bps against a 28bps 2-leg round trip and 0/24 arms clear base cost; the
contrast that reaches 34.7bps is two separate round trips (56bps) to harvest. Pushed to
ventiles and a 12h hold, exactly one cell of 72 clears the 56bps stress (h15: 61.9bps gross,
+5.9 net at stress) — and it is `UNCONFIRMABLE_IN_HORIZON` at 5.37y, is the argmax of a sweep
whose hours were selected in-sample, crosses two funding settlements whose cashflow is
unmodelled and adverse (−4 to −8bps), and holds 5–9 names per leg. **A significant,
replicated, bounce-proof, mechanistically sensible effect that is still smaller than its own
transaction cost everywhere it can actually be traded.** This is the honest headline of the
axis, and it is a negative one.

**10. The temptation I did not take.** `CLK_h15_hold12h_q20_gap0h` clears every cost column
and would have been reportable as the worker's find. It is reported as
`UNCONFIRMABLE_IN_HORIZON` because `auto_verdict` says so mechanically from its ETA, and the
four reasons in §5.2 are listed beside it rather than argued away. Note what would have made
it look better: it is the *largest* number in the sweep precisely because ventile
concentration maximises bps — and ventile concentration also **doubles the ETA** (h02:
5.10y → 9.97y). On this axis the configuration that maximises the headline bps is
systematically the one that is slowest to confirm.

---

## 11. Limitations and what would change the verdict

1. **Cost is the binding constraint on this entire axis, not statistics.** Every mechanism
   here is a 2-leg basket paying 28bps base (1 of 332 cells clears 56bps, and that one is
   unconfirmable in horizon — see §5.2). The only thing that would change the verdict on
   the clock effect is a cheaper implementation: maker/passive execution (the project's
   `execution_probe` fill data exists since 2026-07-12 and was found `DEAD` standalone by
   A16, but has never been tested as a *cost layer* on a daily cross-sectional basket — that
   is W5/W8's axis, and the clock map is a natural client for it), or netting the h02 and
   h15 legs into a single continuously-held book instead of two round trips. Both are
   out of scope here and both are concrete, testable follow-ups.
2. **The panel ends 2026-07-31**, one month earlier than PREREG §2 assumed. Every `event_rate`
   is measured on 21.9 weeks of a 26.1-week window, so every ETA is inflated by x1.196. The
   uncorrected (conservative) figure is the headline; `RESULTS.json` carries the corrected one.
3. **`resid_logret_hour` is taken from the panel's own residualisation** and was not
   re-derived. If that residualisation is biased, the cross-sectional signal inherits it.
   The market-factor test (B1) and the dollar-neutral construction bound the damage but do
   not eliminate it.
4. **The 6h/8h clock-map configuration was inherited from the interrupted first pass**, not
   pre-registered. §5 sweeps holding period and concentration around it under a family-wise
   max-t, but the *choice of 6h signal / 8h hold as the centre* of that sweep is a free
   parameter set before any v2 result was seen and is declared here rather than defended.
5. **Family E's session split is measured on the raw cascade dataset**, not on the actual
   `LIQ_CASCADE_REPEAT_V1` position stream. That is the named missing gate cell for its
   `PROMISING_NEEDS_VALIDATION` verdict.
6. **No intraday microstructure was used.** `market_physics_v3` is 2 days and
   `microstructure_reduced` starts 2026-08-31; both are mono-regime (briefing §4). A
   bid-ask-bounce diagnosis made from 5m bars is an inference from the gap control, not a
   direct measurement of the spread. Direct confirmation on `microstructure_reduced` becomes
   possible once that collector has a few months of history, and would settle the LATE→ASIA
   kill definitively rather than by control.

---

## 12. Reproducing this

```bash
cd /home/qbee/futur
SCRATCH=/tmp/claude-1000/-home-qbee-futur/<session>/scratchpad/w1   # set in evidence/common.py
R=reports/edge_discovery/alpha_hunt_2026-09-03_round4/w1_calendar_clock/evidence

.venv/bin/python $R/build_panels.py            # 3 compact panels -> SCRATCH (~380 MB)
.venv/bin/python $R/family_a.py                # Family A headline        (17 cells)
.venv/bin/python $R/family_a_depth.py          # Family A depth sweep     (75 cells)
.venv/bin/python $R/family_b.py                # Family B headline        (32 cells)
.venv/bin/python $R/verify_b2_vs_clockmap.py   # the reconciliation that found the bug
.venv/bin/python $R/clock_map_v2.py            # THE 24h CLOCK MAP        (24 arms)
.venv/bin/python $R/clock_map_depth.py         # clock depth sweep + combined strategy
.venv/bin/python $R/family_b_armfix.py         # fixed arm-vs-arm + EU->US full gate
.venv/bin/python $R/family_cdef.py             # families C, D, E, F
.venv/bin/python $R/finalize.py                # -> RESULTS.json
```

Files in `evidence/`:

| file | role |
|---|---|
| `common.py` | universe eligibility (PIT), cross-sectional quintile spread, UTC-asserted DuckDB connection |
| `gate.py` | the briefing §2 gate, identical for every mechanism; `auto_verdict` is mechanical |
| `clock_lib.py` | **bug-fixed** rolling window (integer + contiguity guard) with self-verification; panel loader; paired arm contrast |
| `build_panels.py` | builds `daily_liquidity` / `hourly` / `funding_events` from `event_feature_panel` |
| `verify_b2_vs_clockmap.py` | reconciliation of the same trade computed two ways — the script that found the pandas bug |
| `family_a.py`, `family_a_depth.py` | funding clock (reused from the first pass, verified, not re-run) |
| `family_b.py` | session clock headline (reused from the first pass) |
| `family_b_armfix.py` | **supersedes** `family_b_depth.py`: fixed arm-vs-arm + gap sweep + EU→US gate |
| `clock_map_v2.py` | **supersedes** `clock_map.py`: the 24h profile |
| `clock_map_depth.py` | clock depth sweep, bounce control on all 24 hours, combined strategy |
| `family_cdef.py` | weekend / month-end / clock×event / meta-conditioner |
| `finalize.py` | assembles `RESULTS.json` |

Superseded but retained (nothing deleted, per project rule): `clock_map.py` +
`results_clock_map.json` (VOID — see §2.1), `family_b_depth.py` +
`results_family_b_depth.json` (arm-vs-arm block void — see §2.2; its mechanism rows are
valid and are reproduced in `family_b_armfix.py`).

**Resource discipline.** Peak scratch 380 MB (3 parquets), no writes outside
`w1_calendar_clock/`, DuckDB capped, at most two concurrent Python processes, disk stayed at
58 GB free throughout.
