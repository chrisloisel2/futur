# MARKET PHYSICS V3 — PHASE 5.1 MECHANISM DISSECTION

## Status

Phase 5 DEV_PILOT is complete on the first 6-hour causal 100 ms tape.

The original preregistered Phase 5 result is preserved unchanged:

- 56 features;
- 7 horizons;
- 1,176 symbol-level tests;
- 5 `GENERAL_CANDIDATE` mechanisms;
- 29 `SINGLE_SYMBOL_WATCH` mechanisms.

The five discovery mechanisms are:

1. `okx__queue_imbalance_l5`, 30 s, positive median IC;
2. `bybit__price_spread_bps`, 100 ms, negative median IC;
3. `binance__price_spread_bps`, 100 ms, negative median IC;
4. `hyperliquid__price_spread_bps`, 100 ms, negative median IC for the median/cross-symbol mechanism, with SOL showing an opposite-sign discovery result;
5. `okx__price_spread_bps`, 100 ms, negative median IC.

These are discovery candidates, not causal claims and not economic alphas.

## Why Phase 5.1 exists

The first DEV window reveals two confound classes that must be understood before building a strategy:

- several slow queue/microprice signals have large `reverse_ic`, meaning the book state may partly encode a price move that already happened;
- raw spread is unsigned, yet the discovery result is directional at 100 ms, so regime dependence or target-construction effects must be ruled out.

The original Phase 5 candidate gate is not rewritten after seeing the data. Phase 5.1 is an additive exploratory diagnostic on the same DEV window.

## Fixed diagnostics

For every selected discovery mechanism x symbol, Phase 5.1 records:

- original forward Spearman IC;
- reverse IC versus same-horizon past return;
- past-return momentum IC versus future return;
- partial Spearman IC between feature and future return controlling same-horizon past return;
- chronological IC in three equal thirds;
- IC conditional on positive versus negative same-horizon past return;
- top-decile minus bottom-decile future return response;
- leave-one-venue-out (`LOO`) forward IC where the tested venue is excluded from the fair-value target;
- LOO partial IC controlling the LOO past return;
- explicit diagnostic flags for reverse dominance, partial sign flips, LOO sign flips, time instability, regime sign flips, and unsigned directional features.

## Interpretation boundary

Phase 5.1 outputs are labeled `EXPLORATORY_DEV_DIAGNOSTIC_ONLY`.

They cannot promote or demote the original Phase 5 discovery classification on the same 6-hour data. They are used only to understand the mechanism and freeze a forward-confirmation hypothesis.

## Candidate-family interpretation

### Spread family — 100 ms

The four venue-specific spread candidates are treated as one mechanism family until independent evidence proves otherwise.

A raw spread is unsigned. A directional spread effect therefore needs an explicit regime-symmetry test before it can be interpreted as a stable directional signal.

### OKX queue imbalance L5 — 30 s

The principal confound is momentum/reaction. A future confirmation must show that the queue state retains incremental information after conditioning on past return.

### SOL cross-venue dislocation cluster

The SOL venue-dislocation results are retained as exploratory watches, not promoted to the five discovery mechanisms. Their opposite-sign reverse/forward pattern is scientifically interesting and may motivate a separately preregistered convergence/lead-lag hypothesis later.

## Frozen Phase 5.2 forward-confirmation rules

A fresh confirmation window must begin after this protocol exists in git. No Phase 5.1 same-window diagnostic can satisfy these rules.

For a discovery mechanism to advance toward economic testing on a fresh independent window:

1. the exact feature, horizon and expected sign are frozen from Phase 5 discovery;
2. the new simultaneous four-venue window is at least 6 continuous hours and is rebuilt causally at 100 ms;
3. the original Phase 5 symbol gate still passes on the fresh window: n >= 1,000, ESS >= 200, |IC| >= 0.015, BH q <= 0.05, block-shuffle p <= 0.05, same-sign halves;
4. at least two symbols pass with the frozen mechanism sign;
5. venue-specific signals must have leave-one-venue-out IC with the same sign and |LOO IC| >= 0.015 on at least two symbols;
6. partial IC controlling same-horizon past return must preserve the frozen sign and have |partial IC| >= 0.01 on at least two symbols;
7. at least two of three chronological thirds must preserve the frozen sign on each passing symbol;
8. for the unsigned spread family specifically, both positive-past-return and negative-past-return regimes must preserve the frozen directional sign on at least two symbols;
9. no gate may be lowered or selectively removed after the fresh-window result is observed.

Passing Phase 5.2 is still not an economic alpha. The next stage must translate the mechanism into executable entries/exits and explicitly charge spread, fees, slippage and latency. The 100 ms spread family in particular must not be treated as market-order executable without a dedicated latency/execution study.
