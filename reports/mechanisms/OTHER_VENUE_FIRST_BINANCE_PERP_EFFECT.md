# OTHER_VENUE_FIRST_BINANCE_PERP_EFFECT

`other_venue_first_binance_perp_effect_v1` · family `news` · status **RESEARCH_ONLY** · population `OTHER_VENUE_FIRST` · rules_hash `0693380e46c9bb24…`

Definition only. No return computed, no verdict, no budget consumed, no look authorised. The spec below passes the research kernel's validator.

## Definition

An asset already has a price on at least one dated venue before the Binance perpetual opens. The event is therefore not a market birth but the **arrival of Binance perp as new infrastructure**: leverage, short access, Binance liquidity, index inclusion. The right reference is the first venue's own price, not BTC. P9 counts 137 such events among the 174 H2 launches (MEXC 113, Bybit 10, OKX 8, KuCoin 6), with a median external lead of 11 days.

## Questions the mechanism must answer

1. Does the Binance perpetual opening change the asset's liquidity (depth within 20 bps, effective spread) relative to its state on the first venue?
2. Does it change realised volatility relative to the pre-Binance 7-day volatility on the first venue?
3. Is there a migration effect: does volume move from the first venue to Binance, and over what horizon?
4. Does any effect depend on the external lead time (hours, days, months) between the first listing and the Binance launch?
5. Does it depend on MEXC having been the first venue, versus OKX / Bybit / KuCoin?
6. Does it depend on the pre-Binance pump level (pre_binance_pump_score) on the first venue?

## Hypothesis (as the spec states it)

> When Binance opens a USDS-M perpetual on an asset that already has a dated market on another venue, the asset's price on Binance, measured against the first venue's own price, moves by more than 30 bps gross over the primary horizon after the entry window, because the arrival of leveraged access and short access changes who can trade the asset and at what size.

## Economic reason

The first venue priced the asset without leverage or with thin depth; Binance brings leveraged retail, index inclusion and a short path. The participants forced to act are the holders on the first venue who could not short, and the market makers who must quote a new book against a known external price. The mispricing is bounded by the cost of arbitrage between the two venues, which the pre-Binance tape and the Binance depth archives make measurable.

## Required data

- H2_CAUSAL_POPULATION_MATRIX (population, lead time)
- H2_DEPTH_CAPACITY_FEATURES (capacity status per window)
- MEXC_PRE_BINANCE_FEATURES or the equivalent for the first venue (pre-t0 state)
- Vision window archives (mark, index, premium, trades) for the Binance side
- ACCOUNT_EXECUTION_REALITY with account_actual fees (currently unknown)

## Exclusions (by rule, before any look)

- events whose population is TRUE_BINANCE_PERP_FIRST (no external reference: a different mechanism)
- events whose venue precedence is UNKNOWN_PRECEDENCE or GATE_FIRST_UNKNOWN_DATE (the premise cannot be checked)
- events flagged BAD_TIMESTAMP (announced opening and first traded bar disagree by more than 15 min)
- events flagged PROVIDER_NEEDED (no free depth or index reference for the launch day)
- events whose capacity status at the entry window is NO_DEPTH or BAD_BOOK (executability unmeasured)
- events whose first-venue pre-Binance tape is not_collected (the conditioning state does not exist)

## Failure modes (what would make a result worthless)

- The effect is a re-labelled H2 fade: if the pooled result reappears with the same dispersion, the population split added nothing.
- Reference contamination: measuring against BTC instead of the first venue's own price re-creates the seq 8 error.
- Lead-time confounding: long-lead assets are old tokens, short-lead assets are fresh launches; an effect that tracks lead time may be an age effect.
- MEXC dominance: 113 of 137 events are MEXC-first; a result on the pooled OTHER_VENUE_FIRST population is mostly a MEXC result and must be stated as such.
- Capacity illusion: an effect that only exists where DEPTH_TOO_THIN cannot be executed and must be reported as paper-only.
- Survivorship of the first venue: assets delisted from MEXC before the Binance launch are missing from the tape.

## Entry / exit / cost as declared

```json
{
 "entry_rule": {
  "population": "OTHER_VENUE_FIRST (MEXC_FIRST, OKX_FIRST, BYBIT_FIRST, KUCOIN_FIRST)",
  "entry_at": "tradable_start + 15 min",
  "reference_price": "first venue's own price at the same minute, not BTC",
  "capacity_gate": "capacity_status at the entry window in (CAPACITY_OK, UNKNOWN); DEPTH_TOO_THIN and SPREAD_TOO_WIDE reported as paper-only",
  "direction": "fixed in the preregistration, not chosen after the look",
  "min_bps_expected": 30
 },
 "exit_rule": {
  "exit_at": "entry + 6h (open of the first bar)",
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
  "notes": "declared; must be replaced by account_actual fee and by the depth-derived spread and slippage before any promotion"
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
  "pre_launch_drift_gte_gross": true
 },
 "sensitivities": {
  "horizon": [
   "60m",
   "24h"
  ]
 }
}
```

## Why this is the closest research zone

It is the only population with (a) more than a hundred events, (b) a measurable external reference before t0, (c) capacity measured at the Binance side for 160 of 174 launches, and (d) a conditioning state (the pre-Binance tape). What it lacks is the account's actual fee — a credential, not a dataset.
