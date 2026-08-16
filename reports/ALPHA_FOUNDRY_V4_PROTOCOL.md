# FUTUR ALPHA FOUNDRY V4

## Mission

Futur V4 is not a single trading model. It is a research and production system that discovers, falsifies, confirms, monetizes and combines economically distinct alpha mechanisms.

The objective is not to produce ten columns called alpha. The objective is to admit at least ten independent sources of net PnL after scientific validation, realistic execution economics and paper-live evidence.

## Non-negotiable definition of an alpha

An alpha counts as an independent sleeve only if all of the following are true:

1. it has a distinct economic mechanism and a distinct `independence_key`;
2. its forward information is causal and PIT-clean;
3. it survives an independent confirmation window;
4. it survives explicit spread, fee, slippage and latency economics;
5. it survives stress gates including DSR, PBO, cost x2, delayed entry and contributor removal;
6. it is positive in paper-live;
7. it is sufficiently orthogonal to accepted sleeves, or has proven positive marginal portfolio contribution.

Different thresholds, venues, depths or model variants of the same underlying mechanism do not count as independent alphas.

## Hard gates

- DSR >= 0.95
- PBO <= 0.10
- cost x2 positive
- delayed entry positive
- top contributors removed positive
- strict PIT
- same-sign halves
- recent period not destructive
- sleeve PF >= 1.30
- capacity >= USD 200,000
- paper-live positive
- independent forward confirmation required
- pairwise Spearman PnL correlation <= 0.25 unless marginal portfolio contribution is independently positive
- target portfolio: >= 10 independent mechanisms

No gate may be lowered after seeing results to manufacture a PASS.

## Research lifecycle

Every mechanism moves through the same append-only research lifecycle:

`HYPOTHESIS -> DEV_DISCOVERY -> INDEPENDENT_CONFIRMATION -> EXECUTION_ECONOMICS -> PAPER_LIVE -> PORTFOLIO_ADMISSION`

Any stage may transition to `REJECTED`. A rejected experiment is not rewritten. A new methodology requires a new experiment ID and preregistration. Independent confirmation must use a new data window.

## Current Phase 5.2 isolation

The running `okx__queue_imbalance_l5 -> +30s` Phase 5.2 confirmation remains untouched.

The current 12-hour confirmation window is reserved for that locked experiment until its verdict is sealed. Alpha Foundry V4 must not inspect that window for new mechanism discovery before the Phase 5.2 verdict is committed. If that window is later reused as DEV material for another mechanism, it is permanently ineligible to serve as that mechanism's independent confirmation window.

## Market Reality Engine

V4 expands the state representation beyond snapshots. Required domains include multi-venue BBO/deep book, event-level book changes, trade flow with preserved modality, perp mark/index/funding/OI/basis/liquidations, spot, public wallet flow, options surface/positioning, on-chain/exchange inventory, PIT event streams, execution traces, and cross-asset innovations/residuals.

Every modality has its own freshness, availability and quality clock. Missing data are never silently forward-filled to increase coverage.

## Target Factory

A mechanism is not forced to predict one generic return. Eligible targets include future fair-value return, leave-one-venue-out return, venue convergence, next mid move, time to next move, queue depletion time, passive fill probability, time to fill, post-fill markout, future spread, future realized volatility, future liquidation intensity, future OI change, future funding, basis convergence, cross-asset residual return and wallet-flow markout.

The target must match the economic mechanism.

## Event-time representation

Trade Tape features include signed/gross notional, trade count, trade-size entropy, large-trade fraction, flow imbalance, acceleration, jerk, CVD, arrival rate, inter-arrival CV, price impact, absorption and cross-venue flow divergence. They are evaluated on both clock-time and event-count windows.

Queue dynamics include add/cancel/remove/execution intensities, depletion hazard, refill rate, refill half-life and sweep/resilience state.

## Leverage Tensor

Per venue the state includes price delta, OI delta/acceleration, funding, expected funding, funding surprise, perp-spot basis, basis velocity/acceleration, mark-index divergence, long/short liquidations, liquidation/depth, liquidation/OI, signed flow and liquidity-vacuum state.

Canonical topology labels: `NEW_LONG_LEVERAGE`, `SHORT_SQUEEZE_DELEVERAGING`, `NEW_SHORT_LEVERAGE`, `LONG_LIQUIDATION_DELEVERAGING`.

## Wallet Intelligence Graph

Public wallet flow is scored with causal historical markout only. Per-wallet state includes markout by horizon, directional hit rate, size/regime conditioned alpha, cross-venue lead score, persistence, turnover and crowding. Aggregate informed flow is a weighted signed-notional sum using only scores available before each trade.

## Cross-asset graph

Cross-asset alpha uses innovations and residuals rather than raw correlation. Leader innovation is the unexplained component of leader returns. Follower residual is the factor-neutral component of follower returns. The lab asks whether the first predicts the second at fixed future horizons.

## Execution Alpha

Execution is both an alpha family and a separate validation stage. Core predictions are P(fill), E(time-to-fill), E(markout|fill) and P(adverse selection|fill).

Expected maker edge is decomposed as:

`fill_probability * (spread_capture + predictive_markout - adverse_selection - fees) - no_fill_probability * missed_opportunity_cost`

Taker economics subtract half-spread, fees, slippage and latency decay from the predicted move.

## Sixteen mechanism laboratories

- A1 Cross-venue price discovery
- A2 Venue dislocation convergence
- A3 Queue depletion hazard
- A4 Liquidity replenishment and resilience
- A5 Toxic trade flow and absorption
- A6 Liquidity-shock propagation
- A7 Liquidation cascade
- A8 Leverage positioning topology
- A9 Funding and basis convergence
- A10 Funding settlement event
- A11 Hyperliquid informed wallet flow
- A12 Cross-asset causal propagation
- A13 Residual relative value
- A14 Options surface shock
- A15 On-chain and exchange flow
- A16 Execution alpha

Each lab has a unique `independence_key`; different implementations of one lab cannot inflate the independent-alpha count.

## Model selection follows mechanism

Queue depletion uses survival/competing hazards; event arrivals use marked point processes; cross-venue discovery uses state-space/VAR-style analysis; basis uses error correction; regimes use HMM/change-point; complex LOB sequence models use DeepLOB/TCN/Transformers only after information proof; cross-asset uses temporal graphs; wallet intelligence uses hierarchical models; execution uses fill and markout models.

Model complexity may not rescue a mechanism that failed information or economic gates.

## Foundry funnel

Research capacity target:

`100+ hypotheses -> 30+ information mechanisms -> 15+ independent confirmations -> 10+ execution-positive mechanisms -> paper-live survivors -> portfolio-admitted independent sleeves`

These counts are research-capacity targets, not promises of survival.

## Portfolio admission

Two candidates with the same `independence_key` can never count as two independent alphas. Candidates from different labs with absolute pairwise PnL Spearman correlation above 0.25 are rejected unless positive marginal portfolio contribution is independently demonstrated.

The Foundry is portfolio-ready only when at least ten independent mechanisms have been admitted.

## Implementation waves

### Wave 0 - Foundation
Contracts, registry, target factory, research lineage, scientific gates, orthogonality, manifest/readiness CLI.

### Wave 1 - Event microstructure
Trade Tape V4, event-count windows, queue hazard, liquidity resilience, cross-venue lead/lag.

### Wave 2 - Derivatives state
OI/funding/basis tensor, liquidation cascade, funding event engine.

### Wave 3 - Identity and graph alpha
Hyperliquid wallet intelligence, cross-asset causal graph, liquid universe expansion.

### Wave 4 - Volatility and alternative state
Options surface, on-chain/exchange flow, PIT event stream.

### Wave 5 - Execution laboratory
Queue-position estimator, passive fill model, post-fill markout, maker/taker router, adverse selection.

### Wave 6 - Alpha portfolio
Paper-live tournament, risk attribution, correlation/marginal contribution, capacity allocation, capital scaling only after evidence.

## Definition of done

For every proposed sleeve the system must answer: What mechanism is exploited? Who pays the rent? Is the data causal? Does information survive independent confirmation? Does it survive realistic execution? Does it survive paper-live? Is it independent of accepted sleeves? Does it add positive marginal portfolio value?

Only then is it an alpha.
