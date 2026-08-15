# MARKET PHYSICS V3 — PHASE 5 INFORMATION AUDIT PROTOCOL

## Status entering Phase 5

Phase 4.1 is a representation/readiness PASS on qbee, not an alpha PASS.

On the reproduced ~300 s concurrent Binance/Bybit/OKX/Hyperliquid run:

- price-clock readiness is ~99.59-99.78% across 100/250/500/1000 ms grids;
- strict all-four-deep readiness remains ~19.1-19.4% and is intentionally preserved;
- Binance/Bybit/OKX depth freshness is ~99.6-100%;
- Hyperliquid depth freshness is ~28.5-29.6% under the original fixed 1.5 s deep-age gate;
- the low strict fraction is explained by Hyperliquid deep snapshot cadence, not by a general collector outage;
- explicit BBO and deep-book clocks remain separate, so fresh price state cannot silently promote stale depth.

The five-minute technical run is insufficient for alpha inference.

## Phase 5 objective

Measure whether the newly observed physical market state contains causal forward information before fitting any predictive model.

Phase 5 does not optimize PnL, leverage, thresholds, architecture or execution parameters.

## First DEV_PILOT data requirement

A Phase 5 DEV_PILOT requires one continuous simultaneous four-venue run of at least 6 hours.

Shorter runs may be used only with an explicit smoke override and must be labeled `SHORT_SMOKE_ONLY`. They cannot create an alpha candidate.

The initial long-run state tape is built at 100 ms. Longer horizons are generated causally from this tape rather than by collecting separate grids.

Because the event volume is large, the long-run builder must stream append-only partitions in `receive_ts_ns` order and emit bounded-memory chunked Parquet output. Loading the entire raw run into RAM is not an accepted Phase 5 path.

## Fixed target

The first target family is future return of the causal multi-venue `price_fair_value`:

`target_h_bps = 1e4 * log(price_fair_value[t+h] / price_fair_value[t])`

Both the current and future fair-value observations must be `price_ready`.

Initial preregistered horizons:

- 100 ms
- 500 ms
- 1 s
- 2 s
- 5 s
- 10 s
- 30 s

These horizons are fixed before inspecting Phase 5 economics.

## Feature families

### Price-clock features

Eligible whenever `price_ready=true`:

- cross-venue price dispersion;
- venue price dislocation;
- BBO/top-of-book microprice offset;
- BBO/top-of-book queue imbalance;
- grid OFI from price snapshots;
- spread;
- cross-venue dislocation range and maximum absolute dislocation.

### Deep-clock features

Eligible only for the venue/row where `depth_fresh=true` under the original Phase 4 strict deep gate:

- queue imbalance L1/L5/L10;
- deep-book OFI;
- 5 bps and 25 bps bid/ask depth imbalance;
- 10 bps notional-to-move imbalance;
- weighted depth-distance imbalance;
- quantity-per-order fragmentation where order-count metadata is available.

A stale deep feature becomes missing. It is never forward-filled into a fresh observation merely to increase coverage.

## Causality

All state is constructed in local `receive_ts_ns` order.

Targets use only future grid observations. No future market event or delayed message may enter the feature state before local receipt.

## Statistical screen

For every feature x horizon x symbol test, Phase 5 records:

- observation count;
- Spearman IC;
- IC in first and second halves;
- same-sign-half indicator;
- reverse/past-return IC diagnostic;
- effective sample size, conservatively bounded by both feature and target ESS;
- ESS-adjusted two-sided significance approximation;
- Benjamini-Hochberg q-value across the full test family.

Only the statistical shortlist receives the more expensive block-shuffle null test. The block length is at least 30 seconds and at least 10x the prediction horizon. Block-shuffle selection is not used to rescue failed tests.

## Preregistered symbol-level candidate gate

A symbol-level candidate requires all of:

- n >= 1,000 paired observations;
- ESS >= 200;
- |Spearman IC| >= 0.015;
- BH q <= 0.05;
- block-shuffle two-sided p <= 0.05;
- first-half and second-half IC have the same non-zero sign.

Reverse causality is reported and inspected but is not automatically used to discard momentum/reversal mechanisms, because legitimate market mechanisms can depend on prior returns.

## Mechanism classifications

For each feature x horizon:

- `GENERAL_CANDIDATE`: at least 2 symbols pass the symbol candidate gate with the same sign as the median IC;
- `SINGLE_SYMBOL_WATCH`: at least one symbol passes, but cross-symbol generality is not established;
- `NO_CANDIDATE_YET`: otherwise.

This is still an information candidate, not a trading strategy.

## No-rescue rule

If the preregistered DEV_PILOT produces no candidate:

- do not lower IC, ESS, q-value or block-shuffle gates after seeing results;
- do not increase leverage;
- do not search a neural-network architecture to manufacture a pass;
- document the result and create a new hypothesis/version before any changed methodology is evaluated.

## Evidence required after a Phase 5 candidate

A Phase 5 information candidate must still pass later stages before paper trading:

1. independent longer/repeated forward windows on different market conditions;
2. mechanism-specific event study and IC decay confirmation;
3. translation into an executable signal with explicit fees/spread/slippage/latency;
4. walk-forward and structural-break tests;
5. portfolio marginal contribution and correlation gates;
6. paper-live tournament against other independent sleeves.

No Phase 5 statistical candidate alone is deployable or evidence for a monthly return target.
