# Backtest Report — BTC_ETH_TREND_V1 (INSTITUTIONAL_BTC_ETH_TREND)

## Métriques principales
| Métrique | Valeur |
|---|---|
| PF cost×1   | 0.8479 |
| PF cost×2   | 0.7815 |
| PF cost×3   | 0.7228 |
| Sharpe      | -0.1312 |
| Sortino     | -0.0216 |
| CAGR        | -0.36% |
| Max DD      | -5.37% |
| N trades    | 21 |
| Hit rate    | 33.33% |
| Expectancy  | -6.7279 USD |
| Avg Win     | 112.5093 USD |
| Avg Loss    | -66.3464 USD |
| W/L ratio   | 1.6958 |
| Avg holding | 24.0h |
| Turnover    | 10.62× |

## PnL
- Gross PnL : -88.62 USD
- Net PnL   : -141.28 USD
- Fees paid : 52.67 USD
- Slippage  : 21.05 USD

## PnL par année
| Année | PnL net |
|---|---|
| 2022 | -141.25 USD |

## PnL par fold modèle
| Fold | PnL net |
|---|---|
| fold_2022 | -141.25 USD |

## Verdict

**REJECT**

| Gate    | Critères |
|---|---|
| REJECT  | PF×1<1.00 OU PF×2<1.00 OU N<30 OU Sharpe<0 |
| INCUBATE| PF×1∈[1.05,1.25) ET N≥50 ET MaxDD<20% |
| PAPER   | PF×1≥1.25 ET PF×2≥1.05 ET Sharpe≥0.60 ET N≥100 ET MaxDD<20% |
| PROMOTE | PF×1≥1.30 ET PF×2≥1.10 ET PF×3≥1.05 ET Sharpe≥0.80 ET N≥150 ET MaxDD<18% |