# ALPHA FOUNDRY V5 — RESEARCH AND PRODUCTION PROTOCOL

## 0. Status

Alpha Foundry V5 is a clean branch from Market Physics V3. It does not inherit the V4 research implementation. V4 may be inspected historically but its evidence objects, search accounting and promotion results are not accepted as V5 evidence.

The currently running Phase 5.2 confirmation on `research/market-physics-data-v3` remains isolated. V5 must not consume that forward window for discovery before the locked Phase 5.2 verdict is sealed.

## 1. Objective

Build an alpha research factory capable of finding and operating **at least ten economically distinct sleeves**. Ten variants of one mechanism do not satisfy the objective.

An admitted sleeve must have:

1. a distinct economic source of PnL;
2. point-in-time-clean data;
3. a preregistered search budget;
4. nested/purged out-of-sample discovery evidence;
5. independent forward confirmation;
6. execution-positive economics under stress;
7. paper-live evidence;
8. acceptable correlation or independently proven positive marginal portfolio contribution.

## 2. Why V4 was rejected

V4 encoded useful ideas but remained insufficient because:

- evidence was represented largely as booleans supplied by callers rather than recomputed from sealed artifacts;
- the 16 labs were mostly registry declarations rather than executable data/feature/target plugins;
- no immutable dataset fingerprint was required;
- no append-only ledger counted every configuration tried before results were seen;
- DSR and PBO were gates, not platform calculations tied to the actual search family;
- no nested model-selection protocol prevented hyperparameter selection from touching outer OOS data;
- no generic PIT auditor validated all availability timestamps;
- no artifact seal prevented post-hoc result replacement;
- execution economics were helper formulas rather than a conservative fill/latency/impact layer;
- multi-symbol transforms could be implemented incorrectly without a hard per-symbol temporal boundary;
- portfolio independence did not measure effective independent bets.

V5 addresses these structurally.

## 3. Architecture

### 3.1 Market Reality Plane

Canonical source remains Market Physics append-only data with both exchange event time and local receive time. Research availability is defined by receive/availability timestamps, never by event timestamp alone.

### 3.2 Dataset Freeze Plane

Every discovery or confirmation run references one immutable `DatasetManifest` containing time window, domains, sources, exact partitions, SHA-256 of every partition, schema version, code commit, PIT policy and clock policy. If one partition changes, verification fails.

### 3.3 Hypothesis Plane

Every hypothesis has an immutable digest over lab, economic source, mechanism, payer, data domains, target, horizon, feature-set identifier, model family, execution style, search budget, lookback and confirmation duration. A changed hypothesis is a new hypothesis and never inherits the old result.

### 3.4 Search Control Plane

Every model configuration is **reserved in the append-only Search Ledger before computation**. The ledger records family, hypothesis digest, experiment digest, config digest, stage, reservation time and completion. A family exceeding its preregistered budget fails closed.

### 3.5 Model Selection Plane

Discovery uses outer expanding walk-forward OOS folds, purge at least max(label horizon, feature lookback), embargo after test boundary, and inner purged folds for configuration selection. Outer predictions are written only after configuration selection is complete. No outer-OOS observation can select a hyperparameter.

The first adapter is ridge regression as a transparent baseline. Survival, point-process, state-space, error-correction, graph and deep sequence adapters must obey the same nested protocol.

### 3.6 Statistical Validation Plane

The platform computes rather than accepts:

- Spearman IC;
- effective sample size;
- block-permutation p-value;
- Benjamini-Hochberg q-value at family scope;
- same-sign temporal halves;
- Deflated Sharpe probability using the observed search family;
- CSCV Probability of Backtest Overfitting using the strategy-return matrix.

Discovery baseline gate:

- |IC| >= 0.015
- q <= 0.05
- block p <= 0.05
- ESS >= 200
- same-sign halves

Independent confirmation baseline gate:

- confirmation window strictly after discovery
- locked-sign IC magnitude at least 0.05
- DSR probability >= 0.95
- PBO <= 0.10
- all mandatory primary symbols pass
- same-sign halves

Mechanism-specific confirmation protocols may be stricter but may not be loosened after seeing the confirmation data.

### 3.7 Execution Plane

Information is not alpha until it survives executable economics.

Taker simulation includes touch/spread cost, explicit fee, square-root impact proxy, latency adverse move and participation via ADV.

Passive simulation includes queue ahead, observed traded-through quantity, conservative partial credit for cancellations ahead, fill probability, maker fee/rebate and post-fill adverse selection.

With L2-only data, queue confidence is explicitly `L2_CONSERVATIVE`; V5 may not pretend it has exact L3 queue position.

Execution gate retains:

- net edge > 0
- costs x2 > 0
- delayed entry > 0
- top contributors removed > 0
- recent period > 0
- PF >= 1.30
- capacity >= $200k

Paper-live must be positive before portfolio admission.

### 3.8 Artifact Plane

Each experiment writes to an immutable artifact directory and is sealed with SHA-256 fingerprints over every result file. After `SEAL.json` exists, the experiment directory is closed. Mutation causes verification failure.

### 3.9 Portfolio Plane

A sleeve carries an `economic_source_id`.

Rules:

- same economic source cannot count twice;
- |Spearman PnL correlation| > 0.25 rejects the later sleeve unless positive marginal contribution was independently demonstrated;
- report effective number of bets from correlation eigenvalues;
- portfolio-ready requires at least 10 unique admitted economic sources and a minimum effective-bet threshold.

Nominal sleeve count is not enough.

## 4. Sixteen executable labs

| Lab | Economic source | Primary mechanism |
|---|---|---|
| A1 | cross_venue_price_discovery | venue innovation transmission |
| A2 | venue_dislocation_convergence | local dislocation convergence |
| A3 | queue_depletion_hazard | queue depletion / next move |
| A4 | liquidity_resilience | sweep/refill continuation vs rejection |
| A5 | toxic_trade_flow | signed flow vs impact / absorption |
| A6 | liquidity_shock_propagation | depth/spread impulse response |
| A7 | liquidation_cascade | forced flow relative to depth/OI |
| A8 | leverage_positioning | price/OI/funding/basis topology |
| A9 | funding_basis_convergence | carry/basis convergence |
| A10 | funding_settlement_event | settlement-boundary inventory effects |
| A11 | wallet_informed_flow | persistent public-wallet markout |
| A12 | cross_asset_propagation | leader innovation -> follower residual |
| A13 | residual_relative_value | factor-neutral convergence |
| A14 | options_surface_shock | IV/skew/term/positioning hedging pressure |
| A15 | onchain_exchange_flow | exchange/stablecoin inventory impulse |
| A16 | execution_alpha | fill probability / adverse selection |

Every lab is fail-closed against required column patterns. Missing data means `BLOCKED`, not zero-filled features.

## 5. Point-in-time rules

The PIT auditor checks every `*_available_ts_ns` and `*_receive_ts_ns` <= `asof_ns`, temporal monotonicity within symbol, and duplicate `(asof_ns, symbol)` keys. Any violation blocks research.

Temporal features and targets are always computed inside symbol groups. A shift may never cross a BTC->ETH boundary.

## 6. Target factory

V5 supports mechanism-specific targets instead of forcing every lab into one price-return label:

- future fair-value return;
- leave-one-venue-out fair-value return;
- future realized volatility;
- next mid move;
- time to next move;
- basis convergence;
- later waves: queue depletion, fill probability, post-fill markout, liquidation intensity, funding event response, wallet-flow response.

Targets are forward-only and computed per symbol.

## 7. Trial accounting and multiple testing

A trial means a configuration that could have been selected based on its result. Changing a feature subset, horizon, model class, hyperparameter, event window, data-selected threshold, target variant or regime rule creates another trial. All such choices must hit the ledger.

Searching hundreds of variants and reporting one as if one test were performed is prohibited.

## 8. Research lifecycle

`HYPOTHESIS -> DEV_DISCOVERY -> INDEPENDENT_CONFIRMATION -> EXECUTION_ECONOMICS -> PAPER_LIVE -> PORTFOLIO_ADMISSION`

Transitions are one-way. A failed confirmation is rejected. A changed mechanism/horizon/target starts a new lineage. Discovery data may be revisited for diagnostics but never promoted to confirmation evidence.

## 9. Model policy

Model complexity is earned. Each lab starts with transparent baselines. A complex model may advance only if its nested-OOS incremental information exceeds the simpler baseline and survives the same search accounting.

Intended model families include survival/competing risks for queue hazard, marked point processes for event flow, state-space/VAR for cross-venue price discovery, error-correction for funding/basis, HMM/change-point for leverage regimes, DeepLOB-style sequence models after baseline survival, hierarchical Bayes for wallet intelligence, graph temporal models for cross-asset propagation, and fill-hazard/adverse-selection models for execution.

A Transformer is not a default architecture.

## 10. Implementation waves

### Wave 0 — control plane
Implemented in this branch: dataset manifests, PIT audit, immutable hypothesis/experiment contracts, search ledger, lineage registry, nested purged walk-forward, BH/FDR, block permutation, DSR, CSCV/PBO, sealed artifacts, stage gates and portfolio independence diagnostics.

### Wave 1 — market event labs
Wire Market Physics book/trade tapes into A1-A6 with stateful event features and queue/sweep episode extraction.

### Wave 2 — leverage/funding
Build a synchronized derivative tape with true freshness by venue, spot/perp basis, OI deltas, funding clock and liquidation episodes for A7-A10.

### Wave 3 — wallet/cross-asset
Build identity-safe wallet history with markout lineage and expand the liquid universe for A11-A13.

### Wave 4 — options/on-chain
Build options surface tape and PIT on-chain/exchange inventory feed for A14-A15.

### Wave 5 — execution laboratory
Capture acknowledgements/fills and estimate venue-specific latency, fill hazard, maker markout and capacity for A16 and every other sleeve.

### Wave 6 — alpha portfolio
Only confirmed, execution-positive, paper-live sleeves enter orthogonality and capital allocation.

## 11. Research references behind the controls

The architecture incorporates the implications of Bailey et al. on Probability of Backtest Overfitting, Bailey & Lopez de Prado on Deflated Sharpe Ratio, Huang-Lehalle-Rosenbaum on queue-reactive order-book models, Zhang-Zohren-Roberts on DeepLOB, and Hawkes-process microstructure literature. These references motivate methods; they do not substitute for independent evidence on crypto data.

## 12. Definition of done

Alpha Foundry V5 is not done when 16 labs exist. It is done only when at least 10 unique economic sources survive all stages, every admitted sleeve has immutable lineage and sealed evidence, PBO/DSR include all relevant trials, execution remains positive at cost x2 and delayed entry, paper-live is positive, pairwise PnL dependence is acceptable or marginal contribution is independently proven, effective number of bets confirms real diversification, and no holdout was used for discovery.

Anything less is research infrastructure, not a ten-alpha portfolio.
