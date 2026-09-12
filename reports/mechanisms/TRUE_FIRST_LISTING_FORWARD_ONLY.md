# TRUE_FIRST_LISTING_FORWARD_ONLY

`true_first_listing_forward_only_v1` · family `news` · status **FORWARD_ONLY** · population `TRUE_BINANCE_PERP_FIRST` · rules_hash `0e0b9330a551381d…`

Definition only. No return computed, no verdict, no budget consumed, no look authorised. The spec below passes the research kernel's validator.

## Definition

The Binance perpetual is the first collected market anywhere. **Six** historical events. Not enough to conclude, and no backfill can create more. The hypothesis is forward-only: it waits for the P4 market-state tape to capture certified births live, and the module refuses any historical verdict by construction.

## Questions the mechanism must answer

1. How many true first listings per year does Binance produce? (P9: 6 of 174 launches over 2023-2026 were first anywhere; the rate is the binding constraint)
2. What is the minimal market-state tape for such an event: first book, first trade, first mark/index/OI/funding, tick L2 for 6 h? (P4 records exactly this on trigger)
3. How many events are needed before a first look, at the observed dispersion, to detect 30 bps? (see required_events)
4. What capacity conditions must be imposed at entry: CAPACITY_OK at +15 min within 20 bps for 1 000 USDT, or the event is paper-only.

## Hypothesis (as the spec states it)

> When a Binance USDS-M perpetual is the first market anywhere for an asset, the price over the primary horizon after the entry window moves by more than 30 bps gross in the direction fixed before the look, because the opening book is built with no external reference and the first participants set the price under leverage.

## Economic reason

With no prior market there is no arbitrage anchor: market makers quote wide, leveraged retail enters at the open, and the first hours are a price-discovery process rather than a repricing. The payer is the participant forced to trade at the open with no reference. Whether the discovery overshoots or underreacts is unknown and cannot be learned from six historical events.

## Required data

- P4 market_state_tape captures with trigger new_perp_listing (live)
- P9 cross-venue precedence at the time of each future launch (to certify 'first anywhere')
- P7 announcement body for the announced opening time
- account_actual fees

## Exclusions (by rule, before any look)

- any event with a dated listing on another venue before the Binance launch (it belongs to OTHER_VENUE_FIRST)
- any event with an undated market elsewhere (UNKNOWN_PRECEDENCE): 'first' is not certified
- any historical event: the six known ones are description material, never a sample

## Failure modes (what would make a result worthless)

- Count: at ~2 true first listings per year the sample needed for a 30 bps effect takes decades; the honest expectation is that this hypothesis is never decided.
- Certification drift: 'first anywhere' depends on the venue clients' coverage; adding a venue can move an event out of this population after the fact.
- The pooled H2 result (seq 8) is not evidence for this population: 6 events were inside a 174-event average.

## Entry / exit / cost as declared

```json
{
 "entry_rule": {
  "population": "TRUE_BINANCE_PERP_FIRST certified at launch time by the venue clients",
  "entry_at": "tradable_start + 15 min",
  "capacity_gate": "CAPACITY_OK at the entry window within 20 bps for 1 000 USDT",
  "direction": "fixed before the look",
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
  "notes": "declared; replaced by measured values at the look"
 },
 "promotion_criteria": {
  "n_min": 80,
  "gross_bps_min": 30,
  "gross_over_cost_min": 3.0,
  "t_min_one_sided_family_bonferroni": "threshold_t(family size at seal)",
  "capacity_ok_share_min": 0.8,
  "fee_provenance_required": "account_actual",
  "verdict_if_all_met": "FORWARD_SEAL_REQUIRED"
 },
 "kill_criteria": {
  "gross_bps_lt": 30,
  "gross_lt_cost_x": 3.0,
  "placebo_gte_gross": true
 },
 "sensitivities": {
  "horizon": [
   "60m",
   "24h"
  ]
 }
}
```

## The arithmetic that closes the historical route

At the per-event dispersion measured in regard seq 8 (σ ≈ 1546 bps), detecting a 30 bps effect at t = 2.33 needs about **14,418 events**. Binance produced 6 certified first listings in the collected years. The honest expectation is that this hypothesis is never decided on returns; what the forward tape can still establish is the *state* of a true birth (first book, first trade, first mark, first OI), which is data, not alpha.
