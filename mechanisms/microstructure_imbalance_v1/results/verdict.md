# microstructure_imbalance_v1 — COST_WALL

The signal is under the cost floor: it needs to capture 1599% of the move it predicts.

## Hypothesis

On Binance USDT-M perpetuals, when the notional resting at the best bid exceeds the notional at the best ask by an unusual margin, the mid price drifts toward the heavy side over the following sixty seconds, and the drift is larger than the round trip paid to take it.

**Why it should exist.** Displayed size at the touch is the visible part of an order flow imbalance that has to clear. A market maker facing a queue that is heavily one-sided is exposed to being run over and widens or steps away on that side, so the mid moves before the imbalance resolves. The compensation exists because taking the other side of a one-sided queue is exactly the risk the maker is refusing, and it decays within seconds, which is why it is not arbitraged away by slower capital.

Family `microstructure`, horizon `60s`, side `long_short`, universe of 3 symbols.

## Numbers

| quantity | value |
| --- | --- |
| gross edge | 0.78 bps |
| cost | 12.55 bps |
| net edge | -11.77 bps |
| net edge at double cost | -24.32 bps |
| breakeven capture | 15.99 |
| profit factor | 0.000 |
| profit factor at double cost | 0.000 |
| Sharpe | -414.03 |
| max drawdown | 17871 bps |
| decisions | 1491 |
| independent episodes | 232 |
| t on gross | 4.303 |
| threshold for this family | 1.645 |
| placebo percentile (worst) | n/a |

The t is computed on the gross series. The same t on the net series would be -64.515, which measures the cost constant rather than the signal.

## Gates

- passed — gate 0 data quality
- passed — gate 1 mechanism and latency (143 ms measured against a 15000 ms budget)
- FAILED — gate 2 cost: gross 0.78 bps < 3 x cost 12.55 bps

## Cost sensitivity

| multiplier | cost bps | net bps |
| --- | --- | --- |
| 1.0x | 12.55 | -11.77 |
| 1.5x | 18.83 | -18.04 |
| 2.0x | 25.10 | -24.32 |
| 3.0x | 37.65 | -36.87 |

## Data quality

- rows: 1491

## Provenance

- rules hash: `c019bedcde0a16a43d988f622479f886cc3ea6d5b92d0e5dcf0512feea309751`
- kernel version: 0.1.0
- code commit: `29396b3c34f9809697ede0db29d860758916d328`
- run recorded at: 2026-09-09T22:37:28Z
- forward seal: none
- data manifests: microstructure_reduced_bbo_binance
