# W1_CALENDAR_CLOCK — PREREGISTRATION
**Written 2026-09-03, BEFORE any outcome test was run.**
Axis: structure calendaire et horloge du marché (funding clock, sessions géographiques,
week-end, fin de mois/trimestre, horloge x événement, horloge comme méta-conditionneur).

Everything below (universe, windows, signals, cost model, decluster protocol, verdict
thresholds) is fixed here. Anything changed after seeing a result is stamped `REFIT` in
REPORT.md.

---

## 0. Reconnaissance done before writing this file (structure only, no outcomes)

Structural facts established first, none of them an outcome measurement:
- `event_feature_panel` (312 symbols, 5m, `venue=binance`) has exactly the columns needed:
  `funding_rate`, `funding_is_settlement`, `time_since_last_funding`, `basis`,
  `residual_logret_5m`, `residual_return_15m/_1h`, `oi`, `close`, `volume`,
  `research_available_at`. 105.4M rows across 2020-2026.
- **Funding settles at exactly 00:00 / 08:00 / 16:00 UTC** (verified on BTCUSDT 2025:
  365 settlements at each of the three hours, zero elsewhere).
- **PIT semantics verified in source** (`scripts/build_event_feature_panel.py`,
  `data_v2/temporal/available_at.py`):
  - `funding_rate` is joined `merge_asof(direction="backward")` → it is the rate of the
    **last settlement at or before the bar**. It is *backward-looking*. The **upcoming**
    settlement's rate is NOT in this panel, so it cannot be used and will not be.
  - `research_available_at = timestamp + 5m05s` uniformly, every year. Funding is
    explicitly documented as acquired from the live `/fapi/v1/fundingRate` REST endpoint
    ("continuous accretion, not a lagged daily batch archive"), `provably_live_observable
    =True` — so the 5m05s figure is genuine, not an artifact.
- **DuckDB renders `timestamp[ns, tz=UTC]` in local time (+01:00) unless `SET
  TimeZone='UTC'`.** On a clock axis this single default would shift every hour bucket by
  one and silently invalidate the entire study. `SET TimeZone='UTC'` is asserted at the
  top of every script and re-verified in each output.
- Universe size at a $10M/day floor: ~110 (2021) → ~181 (2025) symbols. Sufficient for
  quintile cross-sections throughout.
- `data/events/liq_cascade_dataset.parquet` (38,141 rows) already carries `hour_utc` and
  `dow` — the clock x event interaction (Family E) needs no reconstruction.

## 1. PIT / execution convention (fixed)

A 5m row labelled `T` covers `[T, T+5m)`; its close is the price at `T+5m`, knowable at
`T+5m+5s`. Therefore:

> **One full 5m bar of implementation lag.** A signal formed from bars up to and including
> row `i` (information through close-time `c_i`) is entered at the **close of row `i+1`**
> (price at `c_i + 5m`). Exit at the close of the designated row.

No window ever straddles a funding settlement instant unless explicitly declared, so no
mechanism silently collects or pays an unmodelled funding cashflow. Where a window does
straddle, the funding cashflow is added explicitly to the return.

## 2. Universe (fixed)

Symbol eligible at event time `F` iff:
1. 30-day median daily dollar volume, computed on UTC days strictly before `F`'s day, is
   `>= $10M` (PIT: previous-day watermark, never same-day).
2. `>= 30` days elapsed since the symbol's first bar with `volume > 0` (listing burn-in —
   `data/listings_backfill` shows listing pops are a distinct effect, out of scope here).
3. `close` non-null across every bar the mechanism's window needs.
4. Cross-section at `F` has `>= 20` eligible symbols, else the event is dropped entirely.

Period: 2020-01-01 → 2026-08-31 (panel end). Year-by-year always reported.

## 3. Cost model (fixed)

Briefing convention is `net_bps = gross_bps - 14`, stress `- 28`. Most mechanisms here are
**cross-sectional dollar-neutral 2-leg baskets** (long quintile vs short quintile), so the
round-2 W4 precedent applies ("M8 trades two spreads → cost doubled"). Reported for every
mechanism:

| field | meaning |
|---|---|
| `gross_bps` | raw spread edge per episode |
| `net_bps` | `gross - 14` (briefing-literal, per-leg-equivalent) |
| `net_bps_stress28` | `gross - 28` (briefing-literal stress) |
| `net_bps_2leg` | `gross - 28` (**real cost of a 2-leg basket, base**) |
| `net_bps_2leg_stress56` | `gross - 56` (**real cost of a 2-leg basket, stress**) |

**The verdict is decided on the 2-leg columns** for 2-leg mechanisms and on the
briefing-literal columns for 1-leg (directional) mechanisms. Both are always printed.

## 4. Declustering protocol (fixed) — the binding constraint on this axis

A clock effect is maximally clustered: every symbol sees the same hour at the same instant.
Therefore declustering is applied **at the first calculation**, never as a post-hoc check.

- **Observation** = one (clock event, basket) spread return. Cross-sectional dollar-neutral
  construction removes the market factor by design, which is what makes any of this
  admissible at all.
- **L1** — same-symbol / 24h: number of distinct `(symbol, UTC day)` slots contributing.
- **L2** — calendar day, all symbols: number of distinct UTC days with >= 1 event.
  **L2 is the primary unit for the gate.** All headline t-stats are computed on
  day-aggregated observations (mean of that day's events), never on raw event count.
- **L3** — macro unit: calendar **week** (and, where the mechanism is regime-flavoured, a
  vol-regime split reported alongside).
- `t_stat_declustered` = `mean_day / (sd_day / sqrt(n_days))`.
- `bootstrap_ci95` = moving-block bootstrap over **calendar weeks** of day-level
  observations, 5,000 resamples.
- `n_independent_L2_eff` = variance-inflation-adjusted effective N:
  `n_raw * (se_iid / se_block)^2`. Reported so an over-conservative day aggregation is
  visible rather than hidden — but it is never used to *upgrade* a verdict.

## 5. Power / ETA (fixed — the field that decides the round)

- Haircut: the discovery estimate is halved (mandatory).
- `n_required` = `(z_.975 + z_.80)^2 / (0.5 * IR_day)^2` = `7.849 / (0.5 * IR_day)^2`,
  in **independent days**, where `IR_day = mean_day / sd_day`.
- `event_rate` = independent L2 episodes per week measured over the **last 6 months only**
  (2026-03-01 → 2026-08-31), conservative.
- `eta_forward_confirmation` = `n_required / event_rate` weeks → days and years.
- `ETA > 3 years` ⇒ `UNCONFIRMABLE_IN_HORIZON` regardless of bps.

For a daily-frequency mechanism (`event_rate = 7/wk`), `ETA < 3y` requires
`IR_day > 0.169`, i.e. a **discovered annualised Sharpe > 3.2**. This bar is pre-computed
and stated here so no result can be talked up after the fact.

## 6. Mechanisms pre-registered, with pre-committed directional hypotheses

Windows are relative to settlement time `F ∈ {00:00, 08:00, 16:00}` UTC, expressed in
close-times, and all obey §1.

### Family A — FUNDING CLOCK
Signal is **always** the last *settled* funding rate (PIT-safe) and/or contemporaneous
`basis`; the upcoming rate is unavailable and unused.

- **A1 pre-settlement drift.** Rank universe by last funding at `F-60m`; long bottom
  quintile (most negative), short top quintile. Hold `[F-55m, F]`.
  *H_A1: spread > 0* (crowded longs pay; they unwind into settlement, pushing high-funding
  names down).
- **A2 post-settlement reversion.** Same ranking observed at `F`; hold `[F+5m, F+60m]`.
  *H_A2: spread < 0* (mechanical pre-settlement pressure reverses once the cashflow passes).
- **A3 straddle window.** Hold `[F-55m, F+60m]`, funding cashflow added explicitly.
  *H_A3: sign of A1 + A2 combined; no independent prediction.*
- **A4 magnitude-conditioned A1/A2** using `funding_rate_percentile_90d >= 0.90`.
  *H_A4: |effect| larger than A1/A2 on the same population (arm-vs-arm, not vs zero).*
- **A5 settlement-hour heterogeneity.** A1/A2 split by `F ∈ {00,08,16}`.
  *H_A5: 00:00 differs from 08:00/16:00 (00:00 is Asia-only liquidity).* Judged
  arm-vs-arm.
- **A6 basis-signal variant.** Same windows, ranking on `basis` at the decision bar rather
  than last funding. *H_A6: same sign as A1/A2, stronger (basis is the live driver).*

### Family B — SESSION CLOCK
Sessions (UTC): ASIA `00-08`, EU `07-15`, US `13-21`. Overlaps are real; the
non-overlapping partition `ASIA 00-07`, `EU 07-13`, `US 13-21`, `LATE 21-24` is used for
the partition-based tests and stated per test.

- **B1 hour-of-day, market factor.** Equal-weight universe mean return by UTC hour.
  1-leg, directional, `N = days`. *H_B1: no reliable hour-of-day drift after cost* (this is
  a null-hypothesis test I expect to confirm; it is cheap and it bounds Family B).
- **B2 inter-session reversion/continuation, cross-sectional.** Rank by residual return in
  session `S`, trade the quintile spread over session `S+1`.
  *H_B2: reversion (spread < 0) — short-horizon cross-sectional crypto reversal is the
  documented prior.*
- **B3 US-open impulse on alts.** At 13:00 UTC rank by trailing 30d BTC-beta; long high-beta
  / short low-beta over `[13:05, 15:00]`. *H_B3: spread > 0 on days when BTC's Asia+EU
  return is positive, < 0 when negative — i.e. beta pays with the sign of the market;
  tested as an interaction, arm-vs-arm.*
- **B4 overnight-vs-day reversal.** Rank by ASIA-session residual return, trade `US`
  session. Special case of B2, kept separate because it is the classic equity analogue.

### Family C — WEEKEND CLOCK
- **C1 Friday→Monday cross-sectional reversal** (rank on Fri 21:00 UTC trailing week
  residual, trade Sat 00:00 → Mon 00:00). *H_C1: reversion.*
- **C2 weekend cascade amplification** — interaction with `liq_cascade_dataset`, arm-vs-arm
  weekend vs weekday. *H_C2: cascades are larger and pay more on weekends (thin books).*
- **C3 Sunday-evening gap** (Sun 20:00 → Mon 02:00) conditioned on weekend drift.
  *H_C3: continuation into Monday liquidity.*
- **Pre-declared ETA problem:** event rate <= 1/week ⇒ `n_required` in days maps to
  `n_required` weeks. Unless `IR_week > 0.169*sqrt(7) ≈ 0.45` these are
  `UNCONFIRMABLE_IN_HORIZON` by construction. Stated *before* measurement.

### Family D — MONTH / QUARTER END
- **D1 month-end rebalance** (last 2 UTC days of month vs rest), **D2 quarterly-expiry week**
  (last Friday of Mar/Jun/Sep/Dec).
- **Pre-declared:** 12 (resp. 4) episodes/year. Even a huge edge cannot reach power inside
  3 years. These are measured for the record and will be classified
  `UNCONFIRMABLE_IN_HORIZON` unless they turn out to be an *enormous* effect; the
  measurement's purpose is to document the size, not to propose a trade.

### Family E — CLOCK x EVENT
- **E1 cascade payoff by hour/session** — `liq_cascade_dataset`, `fwd_1h/4h/8h` split by
  `hour_utc` bucket. Judged **arm-vs-arm** (bucket A minus bucket B on the same population),
  never "bucket A is positive".
- **E2 cascade repeat-density x weekend** — does the already-known repeat effect
  (`LIQ_CASCADE_REPEAT_V1`) differ weekday vs weekend? Arm-vs-arm.

### Family F — CLOCK AS META-CONDITIONER
- **F1** Does hour-of-day / session gate the sign of a cross-sectional reversal signal
  (ENABLE/DISABLE meta-signal)? Arm-vs-arm across hour buckets, with the multiple-comparison
  cost of 24 buckets stated explicitly (Bonferroni or bootstrap max-t).

## 7. Multiple comparisons (fixed)

Family A has 6 mechanisms x 2 windows; Family B 4; C 3; D 2; E 2; F 1 → ~24 headline tests.
Any single test's nominal `p < 0.05` is reported as nominal. A mechanism is only claimed as
real if it survives a **max-t bootstrap across the family it belongs to**, or if
`p < 0.05/24 = 0.0021`. Stated before measurement.

## 8. Verdict thresholds (fixed, from briefing §3)

- `VALIDATED_FOR_FORWARD` — full gate, ETA < 3y, survives the *2-leg* stress column,
  not concentrated in one year, declustered at L2, and survives §7.
- `PROMISING_NEEDS_VALIDATION` — real edge, one gate cell missing (named).
- `UNCONFIRMABLE_IN_HORIZON` — ETA >= 3y. **Expected to be the modal outcome on this axis**;
  saying so up front is the point of pre-registering.
- `COST_FRAGILE` / `REGIME_DEPENDENT` / `WEAK` / `DEAD` / `DATA_LIMITED` per briefing.

## 9. Disk discipline

Scratch only: `/tmp/claude-1000/.../scratchpad/w1`. Intermediate parquets capped < 1 GB
total, deleted at the end. Nothing written under `data/`. Only
`reports/edge_discovery/alpha_hunt_2026-09-03_round4/w1_calendar_clock/` is written.

---

## Amendment 1 (written before any Family A outcome was computed)

- **Outliers.** Primary estimate uses **no winsorisation**. A robustness variant winsorising
  each symbol's window log-return at ±10% is reported alongside. If the two disagree
  materially the mechanism is downgraded, not upgraded.
- **Buckets.** Quintiles (5 buckets) on the ranking signal; long = bottom quintile, short =
  top quintile, equal weight inside each. Minimum 20 eligible symbols per event (§2.4).
- **Sign discipline.** Each mechanism carries a pre-committed sign (§6). A result with the
  opposite sign is reported as `SIGN_OPPOSITE_TO_HYPOTHESIS` and may NOT be re-labelled as a
  discovery in the other direction without being re-tested on a disjoint period.
- **`n_independent_L2 < 30` ⇒ `DATA_LIMITED`** automatically (no verdict from a handful of
  days).
- **Verdicts are produced mechanically** by `evidence/gate.py::auto_verdict` from the
  numbers, with no per-mechanism discretion.
