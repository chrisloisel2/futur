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

## Simultaneous run requirement

The Phase 3 venue smokes were sequential. Phase 4 therefore requires one collector invocation in which Binance, Bybit, OKX and Hyperliquid run concurrently. State-tape generation is fail-closed and refuses health files with no common overlap or with excessive start-time skew.

## Fixed state-tape cadences

The initial forward research tape is sampled at:

- 100 ms
- 250 ms
- 500 ms
- 1,000 ms

These cadences were preregistered before inspecting predictive economics. They are not tuned from PnL.

## Original strict synchronization gate

The original `ready` / `strict_ready` contract remains:

- max deep receive age: 1,500 ms
- max transport lag: 5,000 ms
- max cross-venue deep receive-time span: 1,000 ms
- all four P0 venues required

A row that fails remains in diagnostic output with `strict_ready=false`; it is not silently imputed or forward-filled into a valid deep state.

## Phase 4 qbee result — 2026-08-15

The first simultaneous 4-venue run covered about 300.15 seconds with only ~9.5 ms collector start skew and loaded 541,862 normalized book events.

Strict ready fractions were stable across cadence and symbol at roughly 19%:

- 100 ms: 0.1922
- 250 ms: 0.1911
- 500 ms: 0.1933
- 1,000 ms: 0.1944

This result was not treated as a reason to loosen the preregistered 1,500 ms gate.

A rejection-cause diagnostic showed one dominant structural cause. At 100 ms cadence:

- `hyperliquid:receive_stale`: 6,412 rejected rows out of 9,003 total rows;
- Hyperliquid deep receive-age p50: ~2,640 ms;
- Hyperliquid deep receive-age p90: ~4,813 ms;
- Binance deep receive-age p50: ~52 ms;
- Bybit deep receive-age p50: ~39 ms;
- OKX deep receive-age p50: ~41 ms.

The same pattern held at 250/500/1,000 ms and independently for BTC/ETH/SOL. Therefore the ~19% strict-ready fraction is primarily a consequence of treating Hyperliquid deep-snapshot recency as if it were the same concept as current price/BBO availability.

Hyperliquid exposes `l2Book` and `bbo` as distinct streams. The observed live data similarly shows BBO updates independently of the slower deep snapshot cadence.

## Phase 4.1 — multi-clock state semantics

Phase 4.1 does **not** replace or relax the original strict gate. It adds a second readiness concept so two different questions are no longer conflated.

### `strict_ready`

Answers:

> Are all four deep books simultaneously available under the original 1,500 ms receive-age and 1,000 ms deep sync-span gates?

This preserves the historical ~19% result and remains the hard mask for features that require all four deep books to be simultaneously fresh.

### `price_ready`

Answers:

> Is a causal point-in-time top-of-book price state available from every required venue with acceptable transport lag?

Price state uses:

- explicit BBO for Binance, OKX and Hyperliquid;
- a one-level best-bid/best-ask view derived from the proven Bybit deep book because the current Bybit collector does not use a redundant BBO channel.

Change-driven BBO streams are not declared invalid merely because no quote change occurred within 1.5 seconds. Price receive age remains explicit and continues to affect fair-value weighting; transport lag remains hard-gated.

`price_ready` does not make deep features fresh.

## Per-venue deep feature freshness

Every deep feature row now carries:

- `depth_receive_age_ms`
- `depth_transport_lag_ms`
- `depth_fresh`

The fixed original 1,500 ms / 5,000 ms deep quality thresholds are retained.

Depth-derived queue imbalance, depth, fragmentation and deep OFI can therefore be masked by `depth_fresh` rather than forcing the entire cross-venue price state to disappear.

## Price-state features

Price-clock fields are emitted separately, including:

- `price_best_bid`
- `price_best_ask`
- `price_mid`
- `price_spread_bps`
- `price_microprice`
- `price_microprice_offset_bps`
- `price_queue_imbalance_l1`
- `price_receive_age_ms`
- `price_transport_lag_ms`
- `price_dislocation_bps`
- `price_weight`
- `price_ofi_l1_grid`

Cross-venue price state includes:

- `price_fair_value`
- `price_dispersion_bps`
- price-state venue weights/dislocations
- price-state diagnostic reasons and availability.

This avoids mixing a one-level BBO clock with a slower full-depth clock under one boolean.

## Deep state variables

For each venue/symbol the original deep state remains available:

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
- order-count / quantity-per-order fragmentation when exposed.

## Causality

Replay is ordered strictly by `receive_ts_ns`. A market event with an earlier exchange timestamp but a later local receipt cannot affect a state row before it was received.

The state tape is therefore a reconstruction of what qbee could have known, not a reconstruction of an omniscient exchange-time history.

## Phase 4 output

Each concurrent run produces Parquet tapes under:

`data/market_physics_v3/state_tape/run=<overlap_start>-<overlap_stop>/`

with one file per cadence plus `SUMMARY.json`.

The summary now reports both:

- `ready_fraction` / strict deep readiness;
- `price_ready_fraction` / price-clock readiness;
- per-venue `depth_fresh_fraction`;
- strict and price rejection diagnostics;
- deep and price receive-age quantiles.

## Gate before information audit

Phase 4.1 must first be replayed on the already-collected simultaneous run. No new alpha search begins until the resulting `price_ready_fraction` and `depth_fresh_fraction` are inspected.

The old ~19% `strict_ready` result remains valid and is not post-hoc rewritten.

If price-state availability is useful, the next step is an information audit, not a neural-network search. Candidate physical features will be tested for forward information decay at preregistered horizons with receive-time causality, explicit feature-validity masks, effective-sample-size correction and block-shuffle controls.
