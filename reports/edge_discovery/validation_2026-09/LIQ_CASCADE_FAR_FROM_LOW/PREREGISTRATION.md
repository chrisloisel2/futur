# PREREGISTRATION — LIQ_CASCADE_FAR_FROM_LOW (wave 2, worker V2, 2026-09-03)

Written BEFORE any forward-return figure was computed by this worker. Only feature-shape
statistics (quantiles of `dist_low_24h`, null counts, monthly symbol counts, ledger row counts)
were inspected before writing this file — never a `fwd_*` column.

## 0. Claim under test

- Discovery: round 2 W2 row 4 (`reports/edge_discovery/alpha_hunt_2026-08-30/w2_liquidation_leverage/REPORT.md`):
  among `LONG_CASCADE` events, "far from the 24h low" beats "at the low"; far bucket net14
  +4.5 full / +15.5→+17.7 OOS (2025+), near-low flips negative OOS, PF 1.18-1.34. No numeric
  threshold published (bucket sizes only: far n≈6,709, near n=7,169 of ~26,838).
- Live: `LIQ_CASCADE_FAR_FROM_LOW_V1` (`scientific_status: RECONSTRUCTED`, `SIGNAL_SHADOW`),
  threshold `dist_low_24h >= 0.05` = rounded in-sample 75th percentile measured at freeze time
  (data-snooped by the registry's own admission). `freeze_spec.json` and the registry entry were
  read (read-only). `far_from_low_variant.py` / `repeat_variant.py` were NOT read.
- Economic mechanism (the definition I reimplement from): a liquidation cascade whose price is
  still printing the 24h low is a cascade still discovering its bottom (more forced selling
  ahead); a cascade whose price is already well above the 24h low is a secondary/late flush
  in a name that already bounced — the forced sellers are more exhausted → better 4h fade.

## 1. Data and population (E)

- Raw file: `data/events/cascade_dataset.parquet` (49-symbol frozen universe, 2020-09→2026-08-27,
  same detector/builder as the live alpha). Discovery's file `liq_cascade_dataset.parquet` is
  perturbation P6, not primary.
- `kind == 'LONG_CASCADE'` only (project SHORT policy; SHORT_SQUEEZE is blocked live anyway).
- `label_full == True` and `fwd_4h` not null.
- `event_time >= 2022-01-01 UTC` (only BTCUSDT exists before 2021-12-04 in this dataset;
  cross-symbol declustering and the causal cross-symbol percentile need a real universe).
- Listing age ≥ 30 days: `event_time >= onboard_ts + 30d` with `onboard_ts` from
  `data/listings_backfill/binance/listings_calendar.parquet` (fallback: first bar of the
  symbol's 5m metrics file + 30d). Count of dropped rows reported.
- `dist_low_24h` not null.
- All timestamps handled in UTC; calendar year = UTC year.

## 2. Feature and PIT verification (before any return)

- `dist_low_24h = px / min(px over trailing 288 five-minute bars incl. current, min_periods 144) − 1`,
  `px = sum_open_interest_value / sum_open_interest` (implied mark). Causal by construction
  (uses only bars ≤ event bar). I recompute it FROM SCRATCH from
  `data/derivatives_backfill/binance_vision_metrics/<sym>_metrics_5m.parquet` for ≥ 3 symbols
  (BTCUSDT, ETHUSDT, SOLUSDT) and compare line by line to the dataset column at each event
  (tolerance 1e-9); mismatch count reported.
- Forward label `fwd_4h` = log(px[row+1+48] / px[row+1]) (entry at the bar AFTER detection).
  Verified by recompute on the same symbols.

## 3. PRIMARY_SPEC (FFL-P0) — frozen here

- Threshold rule (CAUSAL, my own, not the live constant): for an event at time t,
  `thr(t)` = 75th percentile of `dist_low_24h` over all population-E events (all symbols) with
  `event_time ∈ [t − 365 d, t)`, requiring ≥ 200 prior events (else the event is excluded from
  classification; count reported).
  - `far`  := `dist_low_24h >= thr(t)`
  - `rest` := not far (complement on E)
  - `near_at_low` := `dist_low_24h == 0` exactly (parameter-free: the event bar IS the 24h low)
- Trade: LONG the `far` bucket, horizon `fwd_4h`, one leg per event, equal weight.
- Cost: `net14 = gross − 14 bps`, `net28 = gross − 28 bps` (project convention).
- Tests (all on the same population E):
  - T1: `far` alone — L3-declustered mean net14, t-stat, block-bootstrap (blocks = L3 episodes,
    5,000 draws), PF, year-by-year, ex-best-year, worst episode, cumulative max drawdown.
  - T2: `far − rest` (arm A − arm B) — difference of L3-episode means, Welch t on episode
    means, block bootstrap of the difference with blocks = pooled L3 episodes (both arms
    resampled together, so same-day cross-arm correlation is preserved).
  - T3: `far − near_at_low` — same construction (this is the literal discovery claim).

## 4. Declustering (three levels, always reported)

- L1: same symbol, events < 24h apart chained into one cluster (matches the discovery's
  `N_indep` convention, so the claimed N is directly comparable).
- L2: UTC calendar day, all symbols.
- L3 (inference unit): cross-symbol episode chain — any two events (any symbol) < 4h apart
  (= position-overlap window at `fwd_4h`) belong to the same episode; episode value = mean
  net bps over its legs. `t_stat_declustered` and the bootstrap use L3.
- Day-level t (L2) also reported as extra-conservative.

## 5. Preregistered perturbations (≤ 8, each run ONCE, reported as-is, no re-tuning)

| id | change vs PRIMARY |
|---|---|
| FFL-P1 | fixed `dist_low_24h >= 0.05` (= live spec constant) — the live-spec check |
| FFL-P2 | in-sample median split on E (far = ≥ median) |
| FFL-P3 | in-sample terciles on E (far = top third, near = bottom third) |
| FFL-P4 | `dist_low_7d` with the same causal 75th-pct rule |
| FFL-P5 | causal window 180 d instead of 365 d |
| FFL-P6 | discovery file `liq_cascade_dataset.parquet` (2021-01→2026-07), same rule |
| FFL-P7 | include 2021 (`event_time >= 2021-01-01`; BTC-only coverage before Dec-2021 — caveat) |
| FFL-P8 | alts only (exclude BTCUSDT) |

## 6. Mandatory checks

- Overlap with `LIQ_CASCADE_REPEAT_V1`: match `far` events to
  `reports/live_alpha_lab/LIQ_CASCADE_REPEAT_V1/decisions.parquet` (read-only) on
  (symbol, event_time UTC), exact and ±5 min. Report % of far events in the ledger and % of
  ledger events in far. Independence requires ≤ 50 %. Regardless of the %, T1/T2 are re-run on
  the residual `far ∧ ¬REPEAT` and on `far ∧ REPEAT` separately.
- Overlap with `BTC_LEAD_ALT_CASCADE` shock arm (my own set from the sibling validation):
  Jaccard reported.
- Decision-level agreement with the live ledger
  `reports/live_alpha_lab/LIQ_CASCADE_FAR_FROM_LOW_V1/decisions.parquet` (REPLAY rows): my
  fixed-0.05 classification on E vs the ledger's `far` set, on the common time range —
  agreement %, Jaccard, mismatch list summary.
- Capacity: mean 30-day dollar volume of the far-leg symbols from `um_klines_1d` if cheaply
  available; otherwise explicit "not measured" note (event-driven, 1 leg/event, small size).

## 7. Success criteria (fixed now)

- S1: `far` alone: L3 mean net14 > 0, `t_L3 ≥ 1.645`, block-bootstrap `P(mean ≤ 0) < 0.05`.
- S2: `far − rest` > 0 with Welch `t ≥ 1.645` on L3 episodes and bootstrap `P(diff ≤ 0) < 0.05`.
- S3: L3 mean net28 > 0 (else tag `COST_FRAGILE`).
- S4: net14 > 0 in ≥ 4 of 5 UTC years 2022-2026 for `far`, and ex-best-year net14 > 0.
- S5: overlap with `LIQ_CASCADE_REPEAT_V1` ≤ 50 % of far events.
- Verdict: `VALIDATED_FOR_FORWARD` iff S1 ∧ S2 ∧ S4 ∧ S5 (S3 failure → tag COST_FRAGILE, not a
  kill by itself). S1 or S2 clearly failing (t < 1.0) → `REJECTED`. In between → `NEEDS_MORE_RESEARCH`.
- `recommended_next_step`: `UPGRADE_LIVE_STATUS` iff verdict is VALIDATED_FOR_FORWARD AND the
  live constant (FFL-P1) itself passes S1-S4 AND ledger agreement ≥ 95 %. Otherwise
  `DOWNGRADE_LIVE_STATUS` (with the reason: edge absent, or edge present under a causal rule
  but the reconstructed constant fails, or ledger disagreement).
- N_required: block-bootstrap std of L3 far episodes, edge haircut 50 %, one-sided α = 5 %,
  power 80 %; `minimum_calendar_days = 60`; rates over 2y / 6m; `conservative = min`;
  `confirmable_in_horizon = ETA_conservative < 3 y`.

No parameter is changed after seeing a return. Anything not listed here is not run.
