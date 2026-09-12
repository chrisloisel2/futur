# MEXC_TO_BINANCE_MIGRATION_EFFECT

`mexc_to_binance_migration_effect_v1` · family `news` · status **RESEARCH_ONLY** · population `MEXC_FIRST` · rules_hash `5eda99eb55197d9c…`

Definition only. No return computed, no verdict, no budget consumed, no look authorised. The spec below passes the research kernel's validator.

## Definition

An asset exists first on MEXC, then arrives on a Binance perpetual. The MEXC dynamics **before** Binance (pump, liquidity, volume build-up, lead time, exhaustion) are the conditioning state; the question is whether that state carries information about the behaviour after Binance. This branch builds the pre-Binance tape and the features; it computes nothing after t0.

## Questions the mechanism must answer

1. Does a pre-Binance pump on MEXC (high pre_binance_pump_score) precede a fade on Binance?
2. Does an illiquid MEXC market (low pre_binance_liquidity_proxy) precede price discovery on Binance rather than a fade?
3. Does an explosion of MEXC volume in the last 24 h before the launch mark anticipation of the Binance listing?
4. Is the effect different for long MEXC lead times (months) versus short ones (hours to days)?
5. Does the MEXC exhaustion score (drawdown from the 7-day high plus volume drying up) carry information about the post-Binance behaviour?

## Hypothesis (as the spec states it)

> When an asset that first traded on MEXC arrives on a Binance USDS-M perpetual, the Binance price measured against the MEXC price over the primary horizon after the entry window moves by more than 30 bps gross in the direction fixed by the preregistered conditioning on the MEXC pre-launch state, because MEXC positioning built without a short path is unwound once Binance provides one.

## Economic reason

MEXC lists early and thin; holders accumulate without leverage or a short path; the Binance perpetual is the first venue where the position can be hedged or attacked at size. The payer is the MEXC holder who bought the pre-listing run-up and the leveraged Binance entrant of the first minutes. The size of the unwind should scale with what was built on MEXC before, which the pre-Binance tape measures.

## Required data

- MEXC_PRE_BINANCE_FEATURES (pre-t0 state on MEXC, daily and hourly)
- H2_CAUSAL_POPULATION_MATRIX (MEXC_FIRST rows, lead time)
- H2_DEPTH_CAPACITY_FEATURES (Binance book at the entry window)
- Vision window archives on the Binance side
- account_actual fees

## Exclusions (by rule, before any look)

- every population other than MEXC_FIRST
- MEXC_FIRST events whose MEXC pre-Binance tape is not_collected (no conditioning state)
- events whose MEXC market at t0 is the perpetual only when the question is about spot discovery, and vice versa (state the market in the preregistration)
- events flagged BAD_TIMESTAMP or PROVIDER_NEEDED
- events whose Binance capacity at the entry window is NO_DEPTH or BAD_BOOK
- the conditioning variable is chosen BEFORE the look; a conditioning chosen after seeing Binance returns is a second look

## Failure modes (what would make a result worthless)

- Conditioning on a pre-Binance feature and then choosing the horizon after the look: that is data mining, and the family bar does not protect against it.
- MEXC data quality: wash trading on MEXC inflates pre_binance_volume_* and the pump score; a result driven by inflated volume is a MEXC artefact.
- 5-minute history is absent on MEXC for older launches: any 6-hour pre-Binance feature is only available for recent events, which biases the sample toward 2025-2026.
- Survivorship: assets delisted from MEXC before the Binance launch have no tape and are silently absent.
- The effect may be the OTHER_VENUE_FIRST effect with a MEXC label: it must be compared to OKX/Bybit-first events, not tested alone.
- Small conditioning bins: splitting 113 events by pump score and lead time yields cells of 10-20 events, far below the dispersion seen in seq 8.

## Entry / exit / cost as declared

```json
{
 "entry_rule": {
  "population": "MEXC_FIRST with a collected pre-Binance tape",
  "entry_at": "tradable_start + 15 min",
  "reference_price": "MEXC price at the same minute",
  "conditioning": "one pre-Binance feature named in the preregistration (pump score, liquidity proxy, lead-time bucket or exhaustion score), never two",
  "direction": "fixed per conditioning bin before the look",
  "min_bps_expected": 30
 },
 "exit_rule": {
  "exit_at": "entry + 6h",
  "no_stop": true
 },
 "cost_model": {
  "maker_fee_bps": 2.0,
  "taker_fee_bps": 5.0,
  "maker_ratio": 0.0,
  "spread_bps": 8.0,
  "slippage_bps": 6.0,
  "adverse_selection_bps": 0.0,
  "n_legs": 1,
  "notes": "declared; replaced by account_actual fee and depth-derived spread/slippage before any promotion"
 },
 "promotion_criteria": {
  "gross_bps_min": 30,
  "gross_over_cost_min": 3.0,
  "t_min_one_sided_family_bonferroni": "threshold_t(family size at seal)",
  "n_eff_min": 30,
  "top1_share_max": 0.1,
  "capacity_ok_share_min": 0.8,
  "fee_provenance_required": "account_actual",
  "verdict_if_all_met": "FORWARD_SEAL_REQUIRED"
 },
 "kill_criteria": {
  "gross_bps_lt": 30,
  "gross_lt_cost_x": 3.0,
  "placebo_gte_gross": true,
  "conditioning_permutation_gte_gross": true
 },
 "sensitivities": {
  "horizon": [
   "60m",
   "24h"
  ]
 }
}
```

## Why this is the highest mechanistic interest

MEXC is the first venue for 113 of the 137 dated events: the OTHER_VENUE_FIRST zone is mostly a MEXC zone. MEXC lists early, thin and without a short path, so what is built there before Binance is a position that the Binance perpetual is the first place to unwind or attack. The failure modes are equally concrete: MEXC wash volume, missing 5-minute history for older launches, and the temptation to pick the conditioning after seeing Binance returns.
