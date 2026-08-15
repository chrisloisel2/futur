# MARKET PHYSICS / DATA V3 — PRE-REGISTERED RESEARCH PROTOCOL

## 0. Purpose

Alpha Discovery V3.x showed that more transformations on the existing 5-minute panel were not enough to create a robust candidate. Data V3 moves the representation boundary downward to event-level market physics while keeping Data V2 and Alpha Discovery V3.2 untouched.

The objective is not to manufacture a PASS. The objective is to observe additional causal state that was previously destroyed or absent: order-book structure, event flow, venue fragmentation, leverage/liquidation state, options state, external capital context and execution truth.

## 1. Scientific status

This branch is research-only and additive.

- No modification to Alpha Discovery V3.2 gates.
- No HOLDOUT unlock.
- No direct PaperBroker integration until feed semantics and quality are proven.
- Historical 2020-2026 results remain historical qualification, not pristine future evidence.
- Forward/paper-live evidence remains the true unseen confirmation layer.

## 2. Canonical clocks

Every event-level market record must preserve two clocks:

1. `event_ts_ns`: exchange/market event time when the source exposes it.
2. `receive_ts_ns`: local point-in-time availability on the research collector.

Hard invariants:

- `receive_ts_ns >= event_ts_ns` when both represent comparable clocks.
- NTP must be synchronized for live collection.
- Raw wire is stored before parsing.
- Normalized events are append-only.
- Point-in-time research availability is defined by `receive_ts_ns`, not by market event time.

A record with `event_ts_ns <= asof_ns` but `receive_ts_ns > asof_ns` is unavailable at `asof_ns` and must not enter a feature state. This protects replay from delayed-message lookahead.

For reconstructed state, `event_ts_ns` measures market chronology and transport lag; `receive_ts_ns` defines when the strategy could actually know the state.

## 3. Feed-quality states

A feed is classified as one of:

- `EVENT_LEVEL`
- `PIT_AGGREGATED`
- `AGGREGATED_ONLY`
- `STRANDED`
- `MISSING`
- `UNKNOWN`

An aggregated 5-minute series can never satisfy an event-level requirement.

Venue-level `EVENT_LEVEL` is not equivalent to modality readiness. A venue may be connected and healthy while one modality (deep book, true tick tape, OI, liquidation, etc.) remains absent or semantically weaker.

## 4. Event freshness versus backfill

Event-level storage is lossless: delayed/bootstrap/reconnect messages are retained rather than discarded.

Live qualification measures:

`transport_lag_ms = (receive_ts_ns - event_ts_ns) / 1e6`

The initial qualification threshold is fixed at 5,000 ms. Events at or below the threshold are counted as fresh; older events are counted as stale/backfill for qualification purposes.

This threshold is a feed-quality gate, not an alpha parameter and must not be retuned based on PnL.

A venue with freshness telemetry must show:

- at least 100 total messages,
- at least 100 fresh normalized events,
- at least one fresh book event,
- at least one fresh trade event,
- at least one fresh derivative event,
- zero parse errors,
- zero sequence gaps,
- zero reconnects during the qualification smoke,
- zero subscription errors,
- a clean shutdown,
- raw-wire evidence,
- normalized run-local storage,
- zero dead letters inside the qualification run window.

Historical delayed records remain on disk and are not silently deleted.

## 5. P0 market data

Priority venues:

- Binance USD-M
- Bybit linear
- OKX swaps
- Hyperliquid

Priority symbols for protocol qualification:

- BTCUSDT
- ETHUSDT
- SOLUSDT

Required physical layers:

- reconstructible L2/order-book state,
- BBO (explicit or derivable from a proven deep book),
- event trade flow,
- true individual trade tape where the venue exposes it,
- mark/index/premium where available,
- open interest/funding where available,
- liquidation state where available,
- raw replay stream.

Venue qualification does not automatically promote generic `l2_book_events`, `tick_trades` or `bbo`; those require the transverse venue x symbol x modality gate.

## 6. Exchange-specific semantic rules

### Binance USD-M

The 2026 routed WebSocket split is treated as part of the protocol, not as an implementation detail:

- `/public/ws` carries high-frequency `depth` and `bookTicker` streams.
- `/market/ws` carries `aggTrade`, `markPrice@1s` and `forceOrder` streams.
- Public and market traffic run on separate connections with shared venue health but independent connection/reconnect state.

`aggTrade` is preserved as `granularity=aggregate`; it does not satisfy the generic `tick_trades` requirement merely because it is event-level.

Deep-book bootstrap follows the documented USD-M local-book procedure:

1. open/buffer diff-depth WebSocket events,
2. obtain REST `/fapi/v1/depth?symbol=<symbol>&limit=1000`,
3. discard buffered events with `u < lastUpdateId`,
4. require the first processed event to bridge `U <= lastUpdateId <= u`,
5. require each subsequent `pu` to equal the previous processed `u`,
6. seed the local book from the REST snapshot and then apply aligned deltas.

Buffered WebSocket deltas received before the REST snapshot are not point-in-time usable until the snapshot arrives. Their normalized `receive_ts_ns` is therefore clamped to snapshot availability for coherent-book replay; original wire receipt remains preserved in raw-wire storage.

A REST/bootstrap failure is tracked separately from WebSocket parser errors. The in-memory bootstrap buffer is bounded; resets are lossless because raw wire remains append-only.

### Bybit

Order-book snapshots/deltas, public trades, ticker derivatives and liquidation messages are normalized independently. A proven deep `orderbook.50` state may supply BBO from its best levels without requiring a redundant BBO channel.

### OKX

Incremental sequence continuity is tracked by channel. `bbo-tbt` / `books5` snapshot streams do not advance the incremental `books` sequence cursor and do not wipe the deeper book. Historical dead letters remain append-only, while qualification inspects only the current smoke window.

### Hyperliquid

The normalized contract preserves:

- `WsLevel.n` as `order_count`,
- buyer/seller wallet addresses when present,
- transaction hash when present,
- robust trade identity based on `(block_time, coin, tid)`.

L2 is treated as snapshot state. BBO does not wipe deeper state. Older trades delivered around startup/reconnect are retained but classified by receive-time lag before live qualification.

## 7. Stream provenance and granularity

Every normalized book event must identify `source_stream` when the venue exposes a distinguishable stream. Update-range metadata such as Binance `U/u/pu` and OKX `prevSeqId/seqId` is retained when available.

Every normalized trade event should retain:

- `source_stream`,
- `granularity = individual | aggregate` when known.

An aggregate event stream may be useful for signed flow but cannot be promoted to a true tick tape.

Missing provenance never silently counts as ready in the Phase 3 modality gate.

## 8. External context

Stablecoin daily state is bridged from existing data with conservative T+1 UTC research availability and is classified `PIT_AGGREGATED`.

News and macro data stay `STRANDED` until their historical `available_at` semantics are proven. Source publication time must not be confused with collector availability time.

## 9. Multi-horizon research

No single universal target is imposed on all physical signals.

Candidate horizons include:

- 100 ms-5 s for queue/microprice/order-flow state,
- 5 s-5 min for absorption, liquidity depletion and venue convergence,
- 1-60 min for liquidation/forced-flow regimes,
- 30 min-4 h for funding/OI/options/cross-sectional regimes,
- 4 h-24 h+ for external capital, macro and slower derivatives state.

Each alpha sleeve must be tested at its economically natural horizon.

## 10. Information audit before alpha modeling

Required diagnostics include:

- IC decay by horizon,
- reverse causality,
- cross-sectional rank IC,
- effective sample size,
- block-shuffle nulls,
- structural-break maps,
- symbol/venue concentration,
- cost and delay stresses.

Multiple-testing controls remain mandatory when searching many hypotheses.

## 11. Execution truth

Before a signal is considered deployable, research must capture or reconstruct:

- decision time,
- send time,
- exchange acknowledgement,
- first/last fill,
- maker/taker status,
- requested/filled quantity,
- average fill,
- fee/rebate,
- decision mid,
- future markouts.

No OHLCV-based fake limit-fill assumption can validate execution alpha.

## 12. Reconstructed-book invariants

`LocalOrderBook` is fail-closed:

- BBO never mutates the deep book.
- A deep delta received before a genuine deep snapshot is retained on disk but ignored for reconstructed readiness.
- Missing `source_stream` cannot make a book ready.
- Order-count metadata survives reconstruction when exposed.
- A deep snapshot groups all levels of one snapshot message before later deltas are applied.

`SynchronizedBookEngine` uses receive-time synchronized state. A venue is excluded when:

- its required deep book is not bootstrapped,
- its latest snapshot had not been received by `asof_ns`,
- local receive age exceeds the fixed state threshold,
- transport lag exceeds the fixed quality threshold.

The state also records the receive-time span across venues. A set of individually fresh books is not declared synchronized when their local snapshots are too far apart.

Cross-venue fair-value weighting separately penalizes:

- local receive age,
- transport lag,
- spread,
- and rewards usable depth.

## 13. Venue x symbol x modality gate

For the 4 P0 venues x 3 P0 symbols, the matrix independently audits:

- deep-book event presence,
- genuine deep snapshot presence,
- deep incrementals,
- explicit BBO or BBO derivable from deep state,
- fresh event trade flow,
- fresh individual trades,
- trade source/granularity provenance,
- fresh OI/funding/mark/index counts,
- max transport lag by modality.

Generic readiness is promoted only if every required cell passes. In particular:

- `l2_book_events = EVENT_LEVEL` requires every P0 cell to have a genuine deep snapshot and fresh book events.
- `bbo = EVENT_LEVEL` requires explicit BBO or a proven deep book in every cell.
- `tick_trades = EVENT_LEVEL` requires fresh `granularity=individual` evidence in every cell; Binance `aggTrade` alone intentionally blocks this promotion.
- `ready_for_synchronized_books` requires reconstructible deep books, BBO and fresh event trade flow for every required cell; it is separate from the stricter true tick-tape gate.

## 14. Qualification evidence before Phase 3

As of 2026-08-15 on qbee:

- 43/43 unit tests passed on Python 3.8.10 after receive-time/freshness hardening.
- Stablecoin bridge: 3,154 daily rows, 2017-11-29 through 2026-07-18, conservative T+1 availability.
- Bybit: qualified `EVENT_LEVEL` from 2,244 messages / 7,452 events, with zero parse errors, sequence gaps, subscription errors and dead letters.
- OKX: qualified `EVENT_LEVEL` from 2,602 messages / 20,444 events (19,686 book, 159 trade, 599 derivative), 22 subscription ACKs, zero parse errors/gaps/reconnects/subscription errors and clean shutdown.
- Hyperliquid: qualified `EVENT_LEVEL` from 511 messages / 1,881 events. Of these, 1,801 were fresh under the fixed 5 s gate and 80 were stale startup trades. Fresh evidence included 1,300 book events, 153 trades and 348 derivative events; parse errors, gaps, reconnects, subscription errors and dead letters were zero. Maximum observed trade transport lag was about 72.2 s, confirming why receive-time availability is mandatory.
- The venue-level cross-venue family is therefore qualified across Binance, Bybit, OKX and Hyperliquid, but P0 modality readiness is not yet claimed.

## 15. Phase 3 next gate

Before any new alpha/model search:

1. validate the expanded Phase 3 unit suite on qbee,
2. rerun P0 smokes because source-stream/trade-granularity provenance is new,
3. require Binance public+market routed connections and successful REST deep-book bootstrap for BTC/ETH/SOL,
4. generate the 4 venues x 3 symbols modality matrix,
5. inspect every blocking cell rather than manually promoting statuses,
6. only after reconstructible synchronized books are proven, run synchronized-state replay/forward collection,
7. only then begin new cross-venue information-decay and alpha research.
