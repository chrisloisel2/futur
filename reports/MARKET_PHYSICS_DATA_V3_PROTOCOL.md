# MARKET PHYSICS / DATA V3 — protocol

## Mission

Replace the 5-minute-panel-first research paradigm with a point-in-time, event-level market representation. Data V2 remains immutable historical evidence; Data V3 is additive and must never rewrite Data V2 outputs.

## Physical state decomposition

At decision time the system should estimate seven distinct states instead of one monolithic feature vector:

1. **Liquidity:** L2/L3 depth, queue imbalance, cancellations, replenishment, resilience, liquidity vacuum.
2. **Flow:** tick aggressor flow, burst intensity, metaorder persistence, impact efficiency, absorption.
3. **Leverage:** OI, funding, mark/index/premium, real liquidation flow, liquidation pressure relative to absorbable book depth.
4. **Fragmentation:** venue-specific microprice, fair value, stale quotes, cross-venue dislocation, trailing lead/lag.
5. **Options:** ATM IV, term structure, 25d skew/risk reversal, OI/volume state and later dealer-exposure proxies.
6. **External capital:** stablecoin, ETF/CME, macro, exchange/wallet flows and typed event surprise.
7. **Execution truth:** decision/send/ack/fill timestamps, maker/taker, partial fill, slippage and post-fill markouts.

## Canonical event-time contract

Every raw market event must retain:

- `venue`, `symbol`;
- exchange `event_ts_ns`;
- local `receive_ts_ns`;
- exchange sequence/trade/order identifier when available;
- raw event payload or lossless normalized fields.

`receive_ts_ns < event_ts_ns` is invalid until a documented clock-correction layer exists. Raw files are append-only.

## Storage

Partition raw data as:

`market_physics_v3/raw/<event_type>/venue=<venue>/symbol=<symbol>/date=<UTC date>`

Do not aggregate tick/L2 data destructively. Derived 100ms/1s/5s/1m/5m features are separate artifacts.

## Primary P0 acquisition

Tier 0 instruments: BTCUSDT, ETHUSDT, SOLUSDT.

Priority venues: Binance, Bybit, OKX, Hyperliquid, then Coinbase spot.

Required before claiming microstructure readiness:

- full ordered L2 event stream + snapshots/recovery;
- tick-by-tick trades with aggressor;
- BBO;
- OI, funding, mark, index, premium;
- real liquidations where venue publishes them;
- own order execution telemetry.

## No single universal horizon

Each alpha has a natural horizon. Research must measure information decay at 100ms, 500ms, 1s, 5s, 30s, 5m, 30m, 1h, 4h and 24h where data allow it. A microstructure feature is not forced to predict the Data V2 1h residual target.

## Mandatory information audit before model search

For each feature family:

- forward Spearman IC decay by horizon;
- reverse-causality comparison;
- cross-sectional rank IC;
- effective sample size after autocorrelation;
- block-shuffle null;
- stability by month/venue/symbol/regime;
- delayed signal tests;
- fee/spread/impact/adverse-selection accounting.

A family with no stable information content does not proceed to model complexity search.

## Execution-aware economics

`alpha = forecast - fees - spread - market impact - adverse selection - hedging - latency risk`.

Every live/paper order must record decision, send, ack, first fill, final fill and markouts at 100ms/1s/5s/30s/5m where possible. Maker fills are never assumed free: adverse-selection markout is part of cost.

## Sleeve architecture

The target architecture is multiple independent mechanisms rather than one universal predictor:

- microstructure continuation;
- absorption/reversal;
- liquidity vacuum;
- cross-venue price discovery;
- liquidation continuation and liquidation exhaustion;
- funding/basis relative value;
- cross-sectional ranking;
- options volatility/tail state;
- CEX/DEX dislocation;
- external-liquidity/event sleeves;
- execution alpha.

Each sleeve needs its own target, latency budget, cost model, walk-forward validation and kill gate.

## Scientific boundary

This branch creates the representation and audit layer. It does **not** declare alpha, loosen V3.2 gates, or unlock the V3.2 HOLDOUT. Data sources unavailable historically must remain `unknown`, never silently imputed as zero.
