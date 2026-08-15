# MARKET PHYSICS V3 — PHASE 4 SYNCHRONIZED STATE TAPE PROTOCOL

## Status entering Phase 4

Phase 3 live modality evidence on qbee established:

- all 12 venue x symbol cells have reconstructible deep books;
- all 12 cells have usable BBO, explicit or derived from proven deep state;
- all 12 cells have fresh event-level trade flow;
- `ready_for_synchronized_books = true`;
- Binance BTC/ETH/SOL trade flow is `aggTrade` and remains `granularity=aggregate`, so the strict generic `tick_trades` gate remains blocked;
- Bybit, OKX and Hyperliquid show individual trade events;
- funding, mark and index are observed across the P0 matrix;
- open interest remains partial because it is not proven in every cell;
- historical OKX dead letters remain append-only and are not evidence of failure in a later clean run.

This is a representation/readiness PASS, not an alpha PASS.

## Why a new simultaneous run is mandatory

The Phase 3 venue smokes were run sequentially. They prove that every venue can be reconstructed, but they do not provide a common market interval across all four venues.

Phase 4 therefore requires one collector invocation in which Binance, Bybit, OKX and Hyperliquid run concurrently. State-tape generation is fail-closed and refuses health files with no common overlap or with excessive start-time skew.

## Fixed state-tape cadences

The initial forward research tape is sampled at:

- 100 ms
- 250 ms
- 500 ms
- 1,000 ms

These cadences are preregistered before inspecting predictive economics. They are not tuned from PnL.

## Fixed synchronization gates

Initial gates:

- max receive age: 1,500 ms
- max transport lag: 5,000 ms
- max cross-venue receive-time span: 1,000 ms
- all four P0 venues required for a `ready=true` deep-book state

A row that fails these conditions remains in diagnostic output with `ready=false`; it is not silently imputed or forward-filled into a valid state.

## Initial state variables

For each venue/symbol:

- best bid / ask
- mid
- spread bps
- microprice and microprice offset
- queue imbalance L1/L5/L10
- depth within 5/25 bps
- notional required to move 10 bps
- weighted depth distance
- grid OFI L1
- receive age
- transport lag
- cross-venue dislocation
- fair-value weight
- order-count / quantity-per-order fragmentation when the venue exposes it

Cross-venue state includes:

- fair value
- dispersion bps
- receive-time sync span
- venue weights
- venue dislocations
- readiness/reason diagnostics

## Causality

Replay is ordered strictly by `receive_ts_ns`. A market event with an earlier exchange timestamp but a later local receipt cannot affect a state row before it was received.

The state tape is therefore a reconstruction of what qbee could have known, not a reconstruction of an omniscient exchange-time history.

## Phase 4 output

Each concurrent run produces Parquet tapes under:

`data/market_physics_v3/state_tape/run=<overlap_start>-<overlap_stop>/`

with one file per cadence plus `SUMMARY.json`.

## Gate before information audit

No alpha search begins unless the concurrent run shows useful synchronized coverage. The first diagnostic is the `ready_fraction` by cadence and symbol.

A low ready fraction is treated as an infrastructure/synchronization problem, not solved by loosening gates after seeing returns.

After a usable tape exists, the next step is an information audit, not a neural network search. Candidate physical features will be tested for forward information decay at preregistered horizons with receive-time causality, effective-sample-size correction and block-shuffle controls.
