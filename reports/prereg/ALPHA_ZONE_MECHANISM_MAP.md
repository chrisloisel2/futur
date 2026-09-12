# ALPHA ZONE MECHANISM MAP (P11)

No mechanism here has been run. Each is a definition: a population, a reference price, exclusions, failure modes,
and a spec that the research kernel validates. Budget is 0; a look on any of them needs a sealed preregistration.

| mechanism | population | historical events | status | reference price | conditioning | what blocks a look today |
|---|---|---|---|---|---|---|
| `other_venue_first_binance_perp_effect_v1` | OTHER_VENUE_FIRST (MEXC, OKX, Bybit, KuCoin) | 137 dated (P9) | RESEARCH_ONLY | the first venue's own price | lead-time bucket, first venue | account_actual fee (credential); capacity measured for 160/174 |
| `mexc_to_binance_migration_effect_v1` | MEXC_FIRST | 113 | RESEARCH_ONLY | MEXC price | ONE pre-Binance feature named before the look | same, plus the MEXC tape coverage per event |
| `true_first_listing_forward_only_v1` | TRUE_BINANCE_PERP_FIRST | 6 | FORWARD_ONLY | none (no prior market) | capacity gate at entry | event count: ~14,418 needed at σ 1 546 bps |

## Relations

- All three sit in the `news` family, which already carries 5 sealed hypotheses (regard seq 7 and 8). A look on any
  new one is judged at `threshold_t(6)` or higher, and raises the bar for the others.
- The MEXC mechanism is a sub-population of the OTHER_VENUE_FIRST mechanism: a result on one is evidence about the
  other, and a preregistration must say which one it is testing to avoid paying the family bar twice for one idea.
- `H2_POOLED` (`event_listing_perp_fade_v1`, regard seq 8, INDECIDABLE) is **retired as a population**: it averaged the
  three rows above. It is not re-read.

## What none of them may do

Compute a return before a sealed preregistration; choose a horizon, a conditioning or a direction after a look;
use the published fee to promote; count a paper-only event (DEPTH_TOO_THIN / SPREAD_TOO_WIDE at entry) as executable.
