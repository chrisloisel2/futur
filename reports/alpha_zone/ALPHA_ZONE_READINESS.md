# ALPHA ZONE READINESS — P11 (2026-09-12T14:59:18 UTC)

What is ready and what is not, zone by zone, from what P6–P11 measured. No return computed, no verdict, no budget consumed, no test launched. `capital_deployable` remains **false**.

## Executive verdict

- `H2_POOLED`: dead / invalid population
- `TRUE_FIRST_LISTING`: forward-only / too few historical events
- `OTHER_VENUE_FIRST`: closest research zone
- `MEXC_TO_BINANCE`: highest mechanistic interest
- `H3_DELISTING`: forward sealed / needs future events
- `FORCED_FLOW_PUBLIC`: rejected as direct signal
- `PRE_LIQUIDATION_PRESSURE`: possible future family, not built here

## Readiness table

| zone | event_count | data_ready | execution_ready | capacity_ready | mechanism_ready | prereg_ready | alpha_test_allowed | reason |
|---|---|---|---|---|---|---|---|---|
| `H2_POOLED` | 174 | yes | no | yes | no | no | **no** | one number averaged over 113 MEXC-first, 24 other-venue-first and 6 true-first events; retired as a population (regard seq 8 burned) |
| `TRUE_FIRST_LISTING` | 6 | no | no | no | yes | no | **no** | 6 historical events; ~14 400 needed at the observed dispersion; the P4 tape must capture certified births live |
| `OTHER_VENUE_FIRST` | 137 | yes | no | yes | yes | no | **no** | 137 dated events, external reference before t0, capacity measured for 160/174, mechanism defined; blocked by the actual fee (credential) |
| `MEXC_TO_BINANCE` | 113 | yes | no | yes | yes | no | **no** | 113 MEXC-first events with a pre-Binance tape for 112; conditioning state exists; same credential blocker |
| `H3_DELISTING` | 56 | yes | no | yes | yes | no | **no** | sealed 2026-09-11 -> 2028-09-11; only announcements after the seal count; measured cost required at the look |
| `FORCED_FLOW_PUBLIC` | 368 | yes | no | yes | yes | no | **no** | regard seq 9: -7 bps continuation, +13.8 bps reversal under a 43 bps wall; the public message is the corpse, not the signal |
| `PRE_LIQUIDATION_PRESSURE` | 0 | no | no | no | no | no | **no** | would need OI build-up, mark/index divergence and thin depth BEFORE a liquidation; the P4 tape records the state but no mechanism is defined |

## Blockers

| kind | blocker | affects | events | route | cost | cleared |
|---|---|---|---|---|---|---|
| `credential_blocker` | actual account fees unknown (no read-only API key) | OTHER_VENUE_FIRST, MEXC_TO_BINANCE, H3_DELISTING, TRUE_FIRST_LISTING | 153 | set BINANCE_READONLY_API_KEY / _SECRET to a key with no trading, withdrawal or transfer permission; re-run P8 --collect | free | no |
| `free_blocker` | capacity at 20 bps not observable for pre-2026 launches (archive generation) | OTHER_VENUE_FIRST, MEXC_TO_BINANCE | 115 | structural for history; the P4 live tape records 20 bps and tick L2 for every future launch; a preregistration may accept 1 % capacity as UNKNOWN-but-fills | free | no |
| `free_blocker` | spread and slippage in the cost chains are still declared numbers | OTHER_VENUE_FIRST, MEXC_TO_BINANCE | 160 | the depth features now carry an effective-spread proxy and slippage bounds per window; a preregistration must name which window it uses | free | no |
| `free_blocker` | venue precedence unknown | OTHER_VENUE_FIRST | 31 | Gate first-candle per pair; OKX/Bybit announcement archives via the P7 body archiver | free | no |
| `free_blocker` | announced opening time and first traded bar disagree by more than 15 min | OTHER_VENUE_FIRST, MEXC_TO_BINANCE | 8 | human decision per event on which timestamp is the event; excluded by rule until then | free | no |
| `provider_blocker` | no free depth or index reference on the launch day | OTHER_VENUE_FIRST | 7 | H2_PROVIDER_REQUEST_WINDOWS.csv, P0 rows only (targeted windows, never a subscription) | paid, small | no |
| `forward_only_blocker` | too few true first listings in history | TRUE_FIRST_LISTING | 6 | P4 market_state_tape captures certified births live; no backfill can create more | time | no |
| `forward_only_blocker` | H3 seal counts only announcements after 2026-09-11 | H3_DELISTING | 0 | wait; ~18-20 months at the 2025 rate | time | no |
| `conceptual_blocker` | MEXC pre-Binance volume may be wash-traded; pump and volume features inherit it | MEXC_TO_BINANCE | 113 | compare MEXC volume to OKX/Bybit-first events; treat volume features as suspect until then | free | no |
| `conceptual_blocker` | the public liquidation message arrives after the move | FORCED_FLOW_PUBLIC | 368 | none: structural; the family is closed as a direct signal | none | no |

## The ten answers

1. **closest alpha zone** — OTHER_VENUE_FIRST (137 dated events; mechanism other_venue_first_binance_perp_effect_v1); its MEXC_TO_BINANCE sub-zone (113 events) is where the mechanism is most specific
2. **dead zone** — H2_POOLED (invalid as a single population) and FORCED_FLOW_PUBLIC as a direct signal
3. **forward only zone** — TRUE_FIRST_LISTING (6 historical events) and H3_DELISTING (sealed, future announcements only)
4. **data ready not execution ready** — OTHER_VENUE_FIRST and MEXC_TO_BINANCE: reference price, capacity, pre-Binance tape and announcement times exist; the fee actually charged does not
5. **free blockers** — capacity at 20 bps not observable for pre-2026 launches (archive generation); spread and slippage in the cost chains are still declared numbers; venue precedence unknown; announced opening time and first traded bar disagree by more than 15 min
6. **provider blockers** — no free depth or index reference on the launch day (7 events)
7. **hypothesis worth a future prereg** — mexc_to_binance_migration_effect_v1 with ONE pre-Binance conditioning named in advance, or other_venue_first_binance_perp_effect_v1 on the full dated population with the first venue's price as reference
8. **hypothesis not to test** — H2_POOLED again at any horizon; TRUE_FIRST_LISTING on history; any hypothesis whose reference is BTC instead of the first venue; anything conditioned after a look
9. **next concrete act** — configure a read-only key (no trading / withdrawal / transfer) and re-run P8; then write a preregistration for one named population with the first venue's price as reference — not a bot, not a test
10. **capital deployable remains false** — yes; budget 0; no test launched by this branch; decision NO_ALPHA_TEST

## Final decision

**NO_ALPHA_TEST** — actual fees unknown (no read-only key); the published fee may reject, never promote.

Fees confirmed: no. Capacity confirmed: yes (160 of 174 measured; `CAPACITY_OK` at some window in the first hour for 47; median effective-spread proxy at t0 28.368 bps). Budget: 0. capital_deployable: **false**. Alpha test launched by this branch: no.

## What this branch established

- H2 is {'TRUE_BINANCE_PERP_FIRST': 6, 'MEXC_FIRST': 113, 'OKX_FIRST': 8, 'BYBIT_FIRST': 10, 'KUCOIN_FIRST': 6, 'GATE_FIRST_UNKNOWN_DATE': 13, 'UNKNOWN_PRECEDENCE': 18} — not one population. Regard seq 8 averaged them; it is retired, not re-read.
- MEXC before Binance: 113 events, tape for 112; median 7-day return before the launch is descriptive material for a conditioning, not a signal.
- Capacity: measured for 160 launches; the free archive resolves 20 bps only from 2026 ({'20': 59, '100': 115}); the P4 tape closes that for the future.
- Execution: mode `no_credentials`; cost chains {'H2': 'unknown', 'H3': 'unknown'}. A credential, not a dataset, is what separates the closest zone from a preregistration.

