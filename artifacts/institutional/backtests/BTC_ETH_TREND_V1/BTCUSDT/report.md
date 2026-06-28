# Backtest Report — BTC_ETH_TREND_V1 (INSTITUTIONAL_BTC_ETH_TREND)

## Métriques principales
| Métrique | Valeur |
|---|---|
| PF cost×1   | 0.1457 |
| PF cost×2   | 0.1163 |
| PF cost×3   | 0.0905 |
| Sharpe      | -0.7681 |
| Sortino     | -0.0792 |
| CAGR        | -0.72% |
| Max DD      | -3.74% |
| N trades    | 9 |
| Hit rate    | 22.22% |
| Expectancy  | -31.3961 USD |
| Avg Win     | 24.1048 USD |
| Avg Loss    | -47.2535 USD |
| W/L ratio   | 0.5101 |
| Avg holding | 24.0h |
| Turnover    | 4.58× |

## PnL
- Gross PnL : -260.36 USD
- Net PnL   : -282.56 USD
- Fees paid : 22.20 USD
- Slippage  : 8.83 USD

## PnL par année
| Année | PnL net |
|---|---|
| 2022 | -322.74 USD |
| 2025 | +40.18 USD |

## PnL par fold modèle
| Fold | PnL net |
|---|---|
| fold_2022 | -322.74 USD |
| fold_2025 | +40.18 USD |

## Verdict

**REJECT**

| Gate    | Critères |
|---|---|
| REJECT  | PF×1<1.00 OU PF×2<1.00 OU N<30 OU Sharpe<0 |
| INCUBATE| PF×1∈[1.05,1.25) ET N≥50 ET MaxDD<20% |
| PAPER   | PF×1≥1.25 ET PF×2≥1.05 ET Sharpe≥0.60 ET N≥100 ET MaxDD<20% |
| PROMOTE | PF×1≥1.30 ET PF×2≥1.10 ET PF×3≥1.05 ET Sharpe≥0.80 ET N≥150 ET MaxDD<18% |