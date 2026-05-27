# SHORT v2 Improvement Loop

Dataset: `data_out/result`

Execution mode: hedge-only, stress cost = 15 bps fee + slippage x2.

## Iterations

| Iteration | Main change | OOS trades | Outcome |
|---|---|---:|---|
| Baseline v2 dataout | Multi-asset event labels, fixed 8h exit | 883 | Rejected: PF stress 0.50, negative gross edge, heavy macro/crowding overtrading |
| Pass 1 | Rare events, removed macro entry, portfolio timestamp cap, stricter labels | 17 | Still rejected: 2022 crowded-longs false positives |
| Pass 2 | Removed crowded-longs entry, added conservative stop/take-profit outcome | 6 | No catastrophic fold, but 2024 bear-continuation failed OOS |
| Pass 3 | Removed bear-continuation entry | 0 | `SHORT_V2_NO_TRADE_EDGE` |
| Multi-horizon | Separate 2h/4h/8h/12h/24h labels, models, thresholds, trades, reports | 0 | `SHORT_V2_NO_TRADE_EDGE` on every horizon |

## Final Decision

No SHORT signal survived the stricter event gates, stress-cost thresholding, and OOS validation.

This is an intentional fail-closed result: the current enriched datasets do not contain a deployable short edge once weak contexts are removed.

## Multi-Horizon Results

| Horizon | Take-profit | Stop-loss | OOS trades | Verdict |
|---:|---:|---:|---:|---|
| 2h | 1.50% | 0.90% | 0 | `SHORT_V2_NO_TRADE_EDGE` |
| 4h | 2.12% | 1.27% | 0 | `SHORT_V2_NO_TRADE_EDGE` |
| 8h | 3.00% | 1.80% | 0 | `SHORT_V2_NO_TRADE_EDGE` |
| 12h | 3.67% | 2.20% | 0 | `SHORT_V2_NO_TRADE_EDGE` |
| 24h | 5.20% | 3.12% | 0 | `SHORT_V2_NO_TRADE_EDGE` |

## Remaining Blocker

The dataset still uses liquidation proxies rather than real liquidation flow, so `deployment_grade_data=false`.
