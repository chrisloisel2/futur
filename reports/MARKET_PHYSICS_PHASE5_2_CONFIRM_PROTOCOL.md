# MARKET PHYSICS V3 — PHASE 5.2 INDEPENDENT CONFIRMATION PROTOCOL

## Status entering Phase 5.2

Phase 5 DEV_PILOT discovered five statistical `GENERAL_CANDIDATE` rows on one six-hour development window. Phase 5.1 then dissected those same data and remains exploratory only.

The four 100 ms spread candidates are **not advanced** to confirmation because they are unsigned directional features and are time-unstable across the DEV window.

The only mechanism advanced is:

- feature: `okx__queue_imbalance_l5`
- horizon: `30000 ms`
- expected sign: positive
- primary symbols: `BTCUSDT`, `ETHUSDT`
- secondary/supporting symbol: `SOLUSDT`

The Phase 5.1 DEV diagnostic showed positive raw, momentum-controlled, leave-OKX-out and leave-OKX-out momentum-controlled IC on all three symbols, with same-sign temporal thirds and past-up/past-down regimes. This is discovery evidence only, not confirmation.

## Independence requirement

Confirmation must use a **new simultaneous four-venue window collected after this protocol is committed**.

The confirmation tape must:

- start strictly after the DEV discovery window end `1786852443241777168 ns`;
- contain at least 12 continuous hours of simultaneous Binance/Bybit/OKX/Hyperliquid health overlap;
- be reconstructed causally at 100 ms from local `receive_ts_ns`;
- preserve the original 1.5 s deep freshness mask;
- use no rows from the original six-hour DEV window.

No threshold may be changed after the confirmation data are inspected.

## Locked target

Because the feature comes from OKX, the confirmation target explicitly excludes OKX from fair value.

For each symbol:

`LOO_FV_t = weighted fair value of Binance + Bybit + Hyperliquid at t`

`target_30s_bps = 1e4 * log(LOO_FV[t+30s] / LOO_FV[t])`

The feature remains `okx__queue_imbalance_l5` and is available only where `okx__depth_fresh=true`.

The past-return control is the corresponding 30 s leave-OKX-out past return.

## Fixed primary confirmation gates

BTCUSDT and ETHUSDT must **each** pass all of:

- paired observations `n >= 10,000`;
- effective sample size `ESS >= 400`;
- leave-OKX-out Spearman IC `>= +0.05`;
- leave-OKX-out partial Spearman IC controlling past 30 s return `>= +0.03`;
- partial retention `abs(partial IC) / abs(IC) >= 0.50`;
- all three chronological thirds have positive IC;
- both past-up and past-down regimes have positive IC;
- two-sided 300 s block-shuffle `p <= 0.05` with 100 repetitions;
- top-decile minus bottom-decile future leave-OKX-out return is positive.

SOLUSDT is reported as supporting evidence but is not required for the primary PASS.

## Confirmation verdict

`CONFIRMED_INFORMATION_CANDIDATE` requires both BTCUSDT and ETHUSDT to pass every locked gate.

Otherwise the verdict is `NOT_CONFIRMED`.

There is no partial rescue, threshold relaxation, symbol substitution, horizon search or feature-family substitution inside Phase 5.2.

## Scientific boundary

A Phase 5.2 PASS confirms repeatable forward information, not profitability.

After a PASS, the next stage must translate the locked information mechanism into executable economics with explicit spread, maker/taker fees, slippage, latency and fill assumptions. The DEV top-minus-bottom effect is sub-basis-point scale over 30 s, so an economically viable implementation may require selective/passive execution or use as a filter rather than naive taker crossing.

A Phase 5.2 FAIL is recorded as a failed independent confirmation. It must not be rescued by retuning on the confirmation window.
