# lsr_globacct_x_v2 — COST_WALL

The signal is under the cost floor: it needs to capture 167% of the move it predicts.

## Hypothesis

Across Binance USDT-M perpetuals, the assets where the retail account base is least long outperform the assets where it is most long over the following day, on a market-neutral basis, by more than the round-trip cost of trading both baskets.

**Why it should exist.** The global account ratio counts accounts, not notional, so it is dominated by small retail positions. Those accounts are the ones forced out first: they carry the least margin buffer, they are liquidated into the move rather than out of it, and market makers who absorb their flow require compensation for holding the other side. The compensation is paid to whoever takes the unpopular side, which is what this rule does. It is not arbitraged away instantly because the position is only recoverable over hours to a day and requires balance-sheet on both legs.

Family `crowd_positioning`, horizon `1d`, side `market_neutral`, universe of 1 symbols.

## Numbers

| quantity | value |
| --- | --- |
| gross edge | 9.60 bps |
| cost | 16.00 bps |
| net edge | -6.40 bps |
| net edge at double cost | -22.40 bps |
| breakeven capture | 1.67 |
| profit factor | 0.972 |
| profit factor at double cost | 0.904 |
| Sharpe | -0.63 |
| max drawdown | 203343 bps |
| decisions | 25440 |
| independent episodes | 848 |
| t on gross | 2.036 |
| threshold for this family | 3.254 |
| placebo percentile (worst) | n/a |

The t is computed on the gross series. The same t on the net series would be -1.358, which measures the cost constant rather than the signal.

## Gates

- passed — gate 0 data quality
- passed — gate 1 mechanism and latency (600000 ms measured against a 21600000 ms budget)
- FAILED — gate 2 cost: gross 9.60 bps < 3 x cost 16.00 bps

## Cost sensitivity

| multiplier | cost bps | net bps |
| --- | --- | --- |
| 1.0x | 16.00 | -6.40 |
| 1.5x | 24.00 | -14.40 |
| 2.0x | 32.00 | -22.40 |
| 3.0x | 48.00 | -38.40 |

## Data quality

- rows: 25440
- warning — 314 screened symbols have no vision metrics file (1000000BOBUSDT, 1000BTTCUSDT, 1000CATUSDT, 1000CHEEMSUSDT, 1000XECUSDT...)

## Provenance

- rules hash: `6f4b5a7809ba39d630f60fd404082ec5ac526e5eb2932a42dfac901b4ca451b2`
- kernel version: 0.1.0
- code commit: `29396b3c34f9809697ede0db29d860758916d328`
- run recorded at: 2026-09-09T22:26:06Z
- forward seal: none
- data manifests: binance_vision_metrics_5m, binance_um_klines_1d
- warning: 314 screened symbols have no vision metrics file (1000000BOBUSDT, 1000BTTCUSDT, 1000CATUSDT, 1000CHEEMSUSDT, 1000XECUSDT...)
