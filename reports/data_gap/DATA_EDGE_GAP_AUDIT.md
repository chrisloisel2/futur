# DATA EDGE GAP AUDIT — P4 (2026-09-11)

No price look, no budget consumed, no verdict or horizon modified, no new trading hypothesis.
Base commit `eb0bcc6cc857`. Machine-readable twin: `DATA_EDGE_GAP_AUDIT.json`.

## Where the project stands (unchanged)

| mechanism | verdict |
|---|---|
| `event_reaction_v1` | REJECTED_NO_GROSS |
| `event_cross_venue_lag_v1` | REJECTED_NO_GROSS |
| `event_listing_reversal_v1` | INDECIDABLE |
| `event_delisting_pressure_v1` | INDECIDABLE |
| `event_listing_perp_fade_v1` | INDECIDABLE |
| `forced_liquidation_reaction_v1` | REJECTED_NO_GROSS (branch p3-forced-flow-first-look) |
| `forced_liquidation_exhaustion_v1` | REJECTED_COST_WALL (branch p3-forced-flow-first-look) |

Validated sleeves 0 · capital deployable false · budget 0 · one forward seal active (H3 delisting, 2026-09-11 → 2028-09-11).

## What the last three looks actually had in hand

- **Events**: 7 449 official announcements with second-level publication timestamps, but the body is stored as a hash: the announced launch minute, the delisting minute and the borrow-suspension sentence are not in the dataset (I22).
- **Prices**: Binance Vision 1-minute klines and REST aggTrades. Every hypothesis was measured as a return against BTCUSDT with a declared cost (4 + 4 or 8 + 6 bps), because the dataset contained no reference price, no depth, no OI and no funding for the events.
- **Forced flow**: a tape that is clean (99.9 % enriched, ages ≥ 0, delay measured) and structurally blind: the Binance stream publishes one order per second per symbol and Bybit aggregates per second; OI is null for 98.7 % of records and depth for 100 %.
- **Vision**, checked by existence only: for the 174 H2 launches, aggTrades 174, metrics (OI) 174, premiumIndex klines 167, bookDepth 166; for the 46 H3 perp events, bookDepth 41, metrics 45. The state data exists in the public archive; it was never built.

## Field audits

Full tables: `H2_LISTING_FADE_DATA_REQUIREMENTS.md`, `H3_DELISTING_DATA_REQUIREMENTS.md`, `FORCED_FLOW_DATA_REQUIREMENTS.md`.

## Data classes — decisions

| class | decision | priority | cost | effort | impact H2 | impact H3 | impact forced-flow |
|---|---|---|---|---|---|---|---|
| Binance Vision derivatives state around each event (indexPrice/markPrice/premiumIndex 1m, metrics OI 5-min, fundingRate, bookDepth 1-min, aggTrades) | **BUILD** | P0_REQUIRED | free (public archive) | 1.5 d | makes the statistic observable (overshoot vs the external reference instead of vs BTC) and adds the two variance-reducing states (OI, funding); executability from depth | measured capacity/cost for the 41 perp names -> the forward seal's cost requirement becomes satisfiable | none (latency, not state) |
| Announcement body text + daily exchangeInfo / margin-pairs snapshots (status transitions, borrow suspension, exact launch/delisting times) | **BUILD** | P0_REQUIRED | free | 0.5 d | exact announced launch time; symbol status transitions; closes I22 | borrow suspension at announcement and last tradable minute: decides the executable short path per event | none |
| Forward tick-level L2 + bookTicker recorder armed at announcement time (listings, delistings) until +24 h | **BUILD** | P1_HIGH_VALUE | free, ~1-3 GB per event | 2 d | first_orderbook_ts, spread at +15 min, slippage of a real 100 k$ short on future listings | depth at publication for future delistings (feeds the forward seal) | none |
| Account actual fees (commissionRate), leverage brackets, margin borrow rates via a READ-ONLY key | **BUILD** | P1_HIGH_VALUE | free (needs a read-only key) | 0.5 d | actual taker fee replaces published VIP0 | borrow cost and availability | none |
| Event tape extension: Gate / MEXC / KuCoin / Bitget official listing pages + fapi/v1/constituents (index composition) | **BUILD** | P1_HIGH_VALUE | free | 1 d | venue precedence for the 84 assets with no trace; which venues form the index | none | none |
| Bybit public trading archives (tick trades per symbol-day) for the 75 assets with a Bybit perp before the Binance launch | **BUILD** | P2_NICE_TO_HAVE | free | 1 d | a second external reference where the Binance index is thin | none | Bybit liquidations still unmeasurable sub-minute |
| Vendor tick-level L2 history for Binance futures (Tardis / Kaiko class) | **BUY** | P2_NICE_TO_HAVE | paid, per-month subscription | 1 d | closes the 8/174 gap and gives sub-minute spread | spot-only names remain uncovered (no vendor has Binance spot depth pre-2024 at tick level either) | none |
| Un-throttled / complete liquidation data (true cascade size and sequence) | **IGNORE** | P0_REQUIRED | does not exist publicly | 0 d | none | none | the only thing that would change the verdict, and it is not obtainable: every vendor consumes the same throttled stream |
| Liquidation-level heatmaps / positioning estimates (vendor) | **IGNORE** | P2_NICE_TO_HAVE | paid | 0 d | none | none | derived from OI + leverage guesses: noise |
| More symbols, more venues' klines, longer daily history | **IGNORE** | P2_NICE_TO_HAVE | free | 0 d | none: the constraint is per-event dispersion, not N | none: delistings are rare by nature | none |
| News / social / sentiment feeds, ML feature stores | **IGNORE** | P2_NICE_TO_HAVE | paid | 0 d | none | none | none |
| On-chain / DEX pre-listing prices for the 84 assets with no CEX trace | **IGNORE** | P2_NICE_TO_HAVE | free but heavy | 3 d | a reference for 84 assets, but the Binance index already embeds the venues that matter; DEX prices are manipulable at listing time | none | none |

## 1. Top 5 to obtain immediately

1. Vision indexPrice / markPrice / premiumIndex 1-min klines for every H2 launch window (167/174 available) and every future listing
2. Vision metrics (OI 5-min) + fundingRate for the same windows (174/174)
3. Announcement body text + daily exchangeInfo and margin-pairs snapshots (launch/delisting minute, status transitions, borrow suspension)
4. Vision bookDepth 1-min for the 166 H2 launches and the 41 H3 perp announcements (measured depth / capacity / slippage)
5. Vision aggTrades for the 174 launch days (first trade, opening print, trade-size distribution) — then the forward tick L2 recorder for future events

## 2. Top 5 to ignore

1. Un-throttled or 'complete' liquidation feeds: they do not exist; every vendor reads the same 1/s/symbol stream
2. Liquidation-level heatmaps and positioning estimates: OI plus leverage guesses, noise by construction
3. More breadth: more symbols, more venues' klines, longer daily history — N is not the constraint anywhere
4. News / social / sentiment / ML feature stores — forbidden, and they add degrees of freedom, not information
5. Sub-second private or co-located feeds: not purchasable at this tier, and the P1 verdict already closed the market-making route

## 3. Breadth, quality, or spectrum?

**Spectrum.**
- Breadth — saturated: 696 symbols of daily perp data since 2019, 7 449 official events, ~25 000 liquidations per day, 174 + 56 event universes already frozen; every rejection of the last three looks was a well-measured zero, not a small sample.
- Quality — verified: timestamps to the second (publication minute carries the pump; first Vision bar = launch minute; liquidation crash starts in the publication minute), positive controls recover injected effects within 3 %, ledgers hash-chained.
- Spectrum — what is missing is the STATE around each event: the external reference price (index), leverage crowding (OI, funding), depth (capacity), and the two body-text timestamps that decide executability. The looks measured returns against BTC with declared costs because the dataset had nothing else. That is a spectrum gap, not a breadth or quality gap.

## 4. Recommendation: the dataset to build before any new alpha research

**`event_window_state_v1`** — one row per (event, minute) from t0 - 60 min to t0 + 24 h. Must have: index price 1m; mark price 1m; premium 1m; trades (aggTrades) tick with entry/exit prints; OI 5-min; funding at settlements; bookDepth 1-min at 1/2.5/5 % where Vision has it, else null with a coverage flag; exact status timestamps (announced launch, first trade, delisting minute, SETTLING); borrow/margin status at announcement; published and actual fees.
For: event_listing_perp_fade_v1 (174, frozen); event_delisting_pressure_v1 (56 + 22 unresolved, frozen) and the forward window; every future listing/delisting caught by the event-tape timer. Rule: built by pure functions of the frozen universes; hashed; no return computed by the builder; no look without a preregistration and budget > 0.

Build event_window_state_v1 from Binance Vision + the announcement-body/status layer for the two frozen universes and for every future event, before any new alpha research. It costs nothing, it is hashable, and it is the only thing that changes what the existing hypotheses can say. Budget stays 0 until new independent episodes arrive; the H3 forward seal is the only clock running.

## Forbidden (noise, not information)

- any price look on the frozen universes without a new preregistration and budget > 0 (budget is 0)
- re-testing H2 / H3 / forced-flow at another horizon on the same events
- conditioning variables chosen after seeing per-event returns
- vendor heatmaps and sentiment feeds
- more klines breadth as a substitute for state

## Budget note

The loop credits budget by new independent episodes (floor(episodes / 400), cap 5 per source). The state dataset adds
no episode: it credits nothing. It changes what an existing test can measure, not how many tests can be run.
