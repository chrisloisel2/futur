# MARKET PHYSICS / DATA V3 — PRE-REGISTERED RESEARCH PROTOCOL

## 0. Purpose

Alpha Discovery V3.x showed that more transformations on the existing 5-minute panel were not enough to create a robust candidate. Data V3 moves the representation boundary downward to event-level market physics while keeping Data V2 and Alpha Discovery V3.2 untouched.

The objective is not to manufacture a PASS. The objective is to observe additional causal state that was previously destroyed or absent: order-book structure, tick flow, venue fragmentation, leverage/liquidation state, options state, external capital context and execution truth.

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

## 3. Feed-quality states

A feed is classified as one of:

- `EVENT_LEVEL`
- `PIT_AGGREGATED`
- `AGGREGATED_ONLY`
- `STRANDED`
- `MISSING`
- `UNKNOWN`

An aggregated 5-minute series can never satisfy an event-level requirement.

## 4. Event freshness versus backfill

Event-level storage is lossless: delayed/bootstrap/reconnect messages are retained rather than discarded.

Live qualification is stricter. The collector measures transport lag per normalized event:

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

Hyperliquid requires freshness telemetry explicitly before promotion because startup/reconnect flows may include older trades. Historical delayed records remain on disk and are not silently deleted.

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

- L2/order-book state
- BBO
- trade flow
- mark/index/premium where available
- open interest/funding where available
- liquidation state where available
- raw replay stream

Venue qualification does not automatically promote generic `l2_book_events`, `tick_trades` or `bbo`; those require a later transverse semantic coverage gate.

## 6. Exchange-specific semantic rules

### Binance

Incremental depth received before a complete local-book bootstrap remains an `update`; it is not falsely labeled as `add` or `modify`. BBO snapshots do not wipe deeper incremental state.

### Bybit

Order-book snapshots/deltas, public trades, ticker derivatives and liquidation messages are normalized independently. Live venue status is evidence-based, not hard-coded.

### OKX

Incremental sequence continuity is tracked by channel. `bbo-tbt` / `books5` snapshot streams do not advance the incremental `books` sequence cursor and do not wipe the deeper book. Historical dead letters remain append-only, while qualification inspects only the current smoke window.

### Hyperliquid

The normalized contract preserves:

- `WsLevel.n` as `order_count`,
- buyer/seller wallet addresses when present,
- transaction hash when present,
- robust trade identity based on `(block_time, coin, tid)`.

L2 is treated as snapshot state. BBO does not wipe deeper state. Older trades delivered around startup/reconnect are retained but classified by receive-time lag before live qualification.

## 7. External context

Stablecoin daily state is currently bridged from existing data with conservative T+1 UTC research availability and is classified `PIT_AGGREGATED`.

News and macro data stay `STRANDED` until their historical `available_at` semantics are proven. Source publication time must not be confused with collector availability time.

## 8. Multi-horizon research

No single universal target is imposed on all physical signals.

Candidate horizons include:

- 100 ms-5 s for queue/microprice/order-flow state,
- 5 s-5 min for absorption, liquidity depletion and venue convergence,
- 1-60 min for liquidation/forced-flow regimes,
- 30 min-4 h for funding/OI/options/cross-sectional regimes,
- 4 h-24 h+ for external capital, macro and slower derivatives state.

Each alpha sleeve must be tested at its economically natural horizon.

## 9. Information audit before alpha modeling

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

## 10. Execution truth

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

## 11. Current qualification evidence

As of 2026-08-15 on qbee:

- 38/38 pre-freshness-extension unit tests passed on Python 3.8.10.
- Stablecoin bridge: 3,154 daily rows, 2017-11-29 through 2026-07-18, conservative T+1 availability.
- Bybit: qualified `EVENT_LEVEL` from 2,244 messages / 7,452 events, with zero parse errors, sequence gaps, subscription errors and dead letters.
- OKX: qualified `EVENT_LEVEL` from 2,602 messages / 20,444 events (19,686 book, 159 trade, 599 derivative), 22 subscription ACKs, zero parse errors/gaps/reconnects/subscription errors and clean shutdown.
- Hyperliquid lossless smoke captured L2 order counts and trade wallet metadata, but exposed startup trades tens of seconds older than receive time. Hyperliquid remains `UNKNOWN` until the new freshness-aware smoke passes.

## 12. Next gate

The next P0 step is:

1. run the freshness-aware Hyperliquid smoke,
2. qualify only if fresh book/trade/derivative evidence exists,
3. build a transverse venue x symbol x modality coverage matrix,
4. reconstruct synchronized local books,
5. only then begin new cross-venue alpha research.
