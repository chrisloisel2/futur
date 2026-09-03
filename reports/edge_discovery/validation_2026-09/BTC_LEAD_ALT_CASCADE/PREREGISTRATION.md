# PREREGISTRATION — BTC_LEAD_ALT_CASCADE (wave 2, worker V2, 2026-09-03)

Written BEFORE any forward-return figure was computed by this worker. Only feature-shape
statistics (quantiles of |btc_ret_30m| among alt LONG_CASCADE events, sign split, null counts)
were inspected first — never a `fwd_*` column. `w1_event_sequences/evidence/` was NOT opened;
only `w1_event_sequences/REPORT.md` was read.

## 0. Claim under test

Round 3 W1 `w1_a12` (`reports/edge_discovery/alpha_hunt_2026-09-01_round3/w1_event_sequences/REPORT.md`):
restricting altcoin liquidation-cascade fades to the top decile of `|btc_ret_30m|` at the
moment of the event gives net14 +33.02 / net28 +19.02, PF 1.41, N_raw 3,489, N_indep 3,097
(same-symbol 24h decluster only), positive 5/6 years. Dataset `liq_cascade_dataset.parquet`.
Economic story: an alt cascade co-occurring with an outsized BTC move is a market-wide,
correlated forced deleveraging (mean-reverts once BTC pressure passes) rather than an
idiosyncratic alt-specific unwind.

Known trap (wave 1): a BTC shock is a MARKET-WIDE event → many alts cascade in the same
minutes → same-symbol declustering leaves N massively overstated. Cross-symbol L3 is the unit.

## 1. Data and population (A)

- Raw file: `data/events/cascade_dataset.parquet` (49 symbols, 2020-09→2026-08-27).
- `kind == 'LONG_CASCADE'`, `symbol != 'BTCUSDT'` (alts only — BTC cannot "lead" itself),
  `label_full == True`, `fwd_4h` not null, `event_time >= 2022-01-01 UTC`, listing age ≥ 30 d
  (same rule as the sibling preregistration), `btc_ret_30m` not null. UTC throughout.

## 2. "BTC shock" — defined BEFORE any return

- Feature: `btc_ret_30m` = BTCUSDT implied-price (`sum_open_interest_value/sum_open_interest`)
  6-bar (30 min) pct change at the last BTC 5m bar with `create_time <= event_time`
  (as-of backward). Co-occurrence window is therefore `(event_time − 30 min, event_time]`.
- Independent recompute: from `data/derivatives_backfill/binance_vision_metrics/BTCUSDT_metrics_5m.parquet`,
  own as-of join, compared line by line to the dataset column for ALL population-A events
  (tolerance 1e-9); mismatch count reported.
- Shock rule (CAUSAL): `shock := |btc_ret_30m| >= q90(t)` where `q90(t)` = 90th percentile of
  `|btc_ret_30m|` over population-A events with `event_time ∈ [t − 365 d, t)`, ≥ 200 prior
  events required (else excluded, count reported). `no_shock` := complement on A.

## 3. PRIMARY_SPEC (BLA-P0) — frozen here

- Trade: LONG the `shock` arm, horizon `fwd_4h`, one leg per event, equal weight.
- Cost: `net14 = gross − 14`, `net28 = gross − 28`.
- T1: `shock` alone — L3 mean net14, t, block bootstrap (blocks = L3 episodes, 5,000 draws),
  PF, year-by-year, ex-best-year, worst episode, cumulative max drawdown.
- T2: `shock − no_shock` (A − B on the same population A): difference of L3-episode means,
  Welch t, block bootstrap of the difference with blocks = pooled L3 episodes.

## 4. Declustering (three levels)

- L1: same symbol, < 24h chain (comparable to the claim's N_indep 3,097).
- L2: UTC calendar day, all symbols.
- L3 (inference unit): cross-symbol episode chain, gap < 4h → same episode; episode value =
  mean net bps of its legs. For the shock arm this is effectively "one BTC-shock episode".
- Also reported: number of distinct BTC-shock 30-min windows hosting ≥ 1 shock-arm event.

## 5. Preregistered perturbations (≤ 8, each run once)

| id | change vs PRIMARY |
|---|---|
| BLA-P1 | in-sample top decile of `|btc_ret_30m|` over all of A (the discovery's literal construction) |
| BLA-P2 | sign split: down-shock only (`btc_ret_30m <= q10_signed(t)`) and up-shock only (`btc_ret_30m >= q90_signed(t)`), same causal 365 d rule — mechanism predicts DOWN carries it |
| BLA-P3 | "precedes" variant: max `|btc_ret_30m|` over BTC bars in `(t − 2h, t − 30 min]` ≥ q90(t) AND current `|btc_ret_30m| < q90(t)` (pure lead, no co-occurrence) |
| BLA-P4 | top quintile (q80) instead of decile |
| BLA-P5 | population-independent z rule: `|btc_ret_30m| / sd_30d(t) >= 3`, `sd_30d` = causal trailing 30-day std of BTC 30-min returns (all bars, shift(1)) |
| BLA-P6 | discovery file `liq_cascade_dataset.parquet`, same rule |
| BLA-P7 | exclude the best-contributing UTC year (identified after; it is a mandated output, not a tuned parameter) |
| BLA-P8 | residual `shock ∧ ¬LIQ_CASCADE_REPEAT_V1` (mandated overlap residual) |

## 6. Mandatory checks

- Overlap with `LIQ_CASCADE_REPEAT_V1` ledger (read-only), (symbol, event_time UTC) exact and
  ±5 min; % of shock events in ledger; % of ledger in shock. ≤ 50 % required for independence;
  residual and intersection each tested separately (T1/T2).
- Overlap with `LIQ_CASCADE_FAR_FROM_LOW` far set (sibling): Jaccard.
- Interaction with the base "unconditional alt cascade fade" baseline: report `no_shock` arm
  stats too (it IS arm B).
- Capacity: as sibling (event-driven; dollar volume from `um_klines_1d` if cheap, else note).

## 7. Success criteria (fixed now)

- S1: `shock` alone: L3 mean net14 > 0, `t_L3 ≥ 1.645`, bootstrap `P(mean ≤ 0) < 0.05`.
- S2: `shock − no_shock` > 0, Welch `t ≥ 1.645` on L3 episodes, bootstrap `P(diff ≤ 0) < 0.05`.
- S3: L3 mean net28 > 0 (else `COST_FRAGILE`).
- S4: net14 > 0 in ≥ 4/5 UTC years 2022-2026 and ex-best-year net14 > 0.
- S5: overlap with `LIQ_CASCADE_REPEAT_V1` ≤ 50 %.
- Verdict: `VALIDATED_FOR_FORWARD` iff S1 ∧ S2 ∧ S4 ∧ S5; S1 or S2 clearly failing (t < 1.0)
  → `REJECTED`; otherwise `NEEDS_MORE_RESEARCH`. `sign_correction_required` if the shock arm
  is significantly NEGATIVE. If S5 fails → not independent → `OVERLAY_ON_LIQ_CASCADE_REPEAT_V1`
  at best (if the residual still passes) or `REJECT`.
- N_required / ETA: as sibling (block-bootstrap std of L3 shock episodes, 50 % haircut,
  α = 5 % one-sided, power 80 %, `minimum_calendar_days = 60`, rates 2y/6m, conservative = min,
  `confirmable_in_horizon = ETA_conservative < 3 y`).

No parameter is changed after seeing a return. Anything not listed here is not run.
