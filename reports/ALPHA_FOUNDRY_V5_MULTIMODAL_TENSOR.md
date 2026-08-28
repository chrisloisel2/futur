# Alpha Foundry V5 — Multimodal Market Tensor

## Purpose

This implementation converts the existing Market Physics append-only capture into a single causal research tensor by joining:

- the validated 100ms book state tape;
- raw L2 book events;
- raw normalized trades;
- raw normalized derivative events;
- explicit source availability clocks.

The builder never reuses event time as an availability proxy when receive time exists. Every raw record enters the tensor only after `receive_ts_ns <= asof_ns`.

## Components

### Generic causal replay

`alpha_foundry_v5/planes/replay.py`

Normalized storage is partitioned by event date while causality is defined by local receive time. The replayer:

1. validates every physical JSONL file by `receive_ts_ns`;
2. externally stable-sorts only an inverted physical file;
3. heap-merges adjacent event-date files;
4. heap-merges venue/symbol partitions;
5. finally merges book, trade and derivative modalities on the same receive-time axis.

This mirrors the long-run repair already required by the Market Physics state tape.

### Event / Trade Microstructure Plane

`alpha_foundry_v5/planes/event_trade.py`

Clock windows:

- 100ms
- 500ms
- 2s
- 10s
- 60s

Event-count windows:

- last 10 trades
- last 50 trades
- last 250 trades

Features include:

- signed/gross notional;
- flow imbalance and CVD;
- flow acceleration and jerk;
- trade count/rate and inter-arrival CV;
- trade price impact and signed-notional absorption per bp;
- aggregate-vs-individual trade fractions;
- bid/ask add, modify, update, remove and cancel counts/intensities;
- replenishment imbalance;
- removal imbalance;
- true cancellation imbalance when true cancel attribution exists;
- depletion pressure;
- book-event intensity.

`remove` and `cancel` remain separate. A zero-size L2 level removal is never silently relabelled as a true cancellation.

High-volume book actions are first accumulated into the base research grid, then rolling counters are updated per grid. This avoids multiplying tens of millions of book events by every rolling window.

### Derivatives / Leverage Plane

`alpha_foundry_v5/planes/derivatives.py`

Per venue:

- OI level;
- OI event change and acceleration;
- funding and change;
- mark;
- index;
- premium;
- mark-index basis and basis sync span;
- basis velocity/acceleration;
- liquidation count/notional/intensity by 1s/5s/30s/60s/5m;
- long/short liquidation notional and imbalance.

Cross venue:

- median OI event change;
- OI-change dispersion;
- funding dispersion;
- mark-index-basis dispersion;
- premium dispersion;
- 30s liquidation notional aggregate.

Raw OI is never summed across venues because units can differ. OI changes are event-level and emitted once on the first grid that can observe the new event, preventing stale deltas from inflating readiness or effective sample size.

A funding clock is emitted only if a normalized derivative record explicitly carries `next_funding_ts_ns`. No fixed 8h schedule is invented.

### Multimodal Tensor Builder

`alpha_foundry_v5/planes/tensor.py`

The builder uses the existing causal book tape as the grid of record and enriches each exact `(asof_ns, symbol)` row with the event/trade and derivative planes.

Book availability is reconstructed from the existing causal receive-age columns:

- `venue__price_available_ts_ns`
- `venue__depth_available_ts_ns`

The new planes carry:

- `venue__trade_available_ts_ns`
- `venue__book_event_available_ts_ns`
- `venue__open_interest_available_ts_ns`
- `venue__funding_available_ts_ns`
- `venue__mark_available_ts_ns`
- `venue__index_available_ts_ns`
- `venue__premium_available_ts_ns`
- `venue__liquidation_available_ts_ns`
- derived basis/funding-clock availability clocks.

Every finite `*_available_ts_ns` is validated against the row's `asof_ns` before writing.

Artifacts:

- chunked `part-*.parquet`;
- `_SUCCESS`;
- `SUMMARY.json`;
- `AVAILABILITY_CONTRACT.json`.

## Cross-plane features

The first version adds:

- fair-value one-grid return;
- four price/OI leverage topology indicators;
- liquidation-to-10bps-book-depth ratio when quote-notional depth exists.

It deliberately does **not** compute liquidation/OI ratios because the raw OI unit is not proven quote-notional and comparable across venues.

## Lab impact

Expected from the existing raw Market Physics capture:

- A3: newly addressable from action intensities + existing OFI/queue imbalance;
- A4: newly addressable from trade flow + existing depth;
- A5: newly addressable from flow/impact/absorption;
- A7: newly addressable only when enough real liquidation and OI-change activity exists;
- A8: newly addressable when OI plus funding/mark-index state has sufficient coverage;
- A10: only addressable when explicit next-funding timestamps exist in normalized records.

A9 remains deliberately blocked. Its current contract requires **executable perp-vs-spot basis**. Mark-index basis is not a substitute and the current 6h Market Physics capture does not contain synchronized executable spot books.

## Scientific boundary

Data readiness is not alpha evidence. Any newly ready lab still enters the full V5 lifecycle:

`DATA READY -> DEV DISCOVERY -> INDEPENDENT CONFIRMATION -> EXECUTION ECONOMICS -> PAPER LIVE -> PORTFOLIO ADMISSION`
