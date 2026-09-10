# cross_exchange_dislocation_v1 — COST_WALL

The signal is under the cost floor: it needs to capture 1215% of the move it predicts.

## Hypothesis

When the Binance and OKX perpetual mid prices for the same symbol diverge by an unusual amount, the gap closes within sixty seconds by more than the round trip paid on both legs.

**Why it should exist.** Two venues quote the same risk. A gap between them is a local liquidity shock on one of the two, not a change in the value of the asset, so it is mechanically pulled back by anyone holding inventory on both venues. The compensation is paid for supplying that inventory across a fragmented market, and it survives only as long as capital cannot move between venues instantly, which is a real constraint: margin sits on one venue at a time.

Family `cross_exchange`, horizon `60s`, side `market_neutral`, universe of 3 symbols.

## Numbers

| quantity | value |
| --- | --- |
| gross edge | 1.97 bps |
| cost | 24.00 bps |
| net edge | -22.03 bps |
| net edge at double cost | -46.03 bps |
| breakeven capture | 12.15 |
| profit factor | 0.000 |
| profit factor at double cost | 0.000 |
| Sharpe | -4642.70 |
| max drawdown | 54196 bps |
| decisions | 2450 |
| independent episodes | 934 |
| t on gross | 64.990 |
| threshold for this family | 1.645 |
| placebo percentile (worst) | n/a |

The t is computed on the gross series. The same t on the net series would be -724.918, which measures the cost constant rather than the signal.

## Gates

- passed — gate 0 data quality
- passed — gate 1 mechanism and latency (143 ms measured against a 15000 ms budget)
- FAILED — gate 2 cost: gross 1.97 bps < 3 x cost 24.00 bps

## Cost sensitivity

| multiplier | cost bps | net bps |
| --- | --- | --- |
| 1.0x | 24.00 | -22.03 |
| 1.5x | 36.00 | -34.03 |
| 2.0x | 48.00 | -46.03 |
| 3.0x | 72.00 | -70.03 |

## Data quality

- rows: 2450

## Provenance

- rules hash: `939325d0b46ae6e837b16e0ca3cdc3ebae253fe0504d3ea543e6a74bad50d1d7`
- kernel version: 0.1.0
- code commit: `29396b3c34f9809697ede0db29d860758916d328`
- run recorded at: 2026-09-09T22:37:37Z
- forward seal: none
- data manifests: microstructure_reduced_bbo_binance, microstructure_reduced_bbo_okx
