# Backtest Report — BNB_EVENT_V1 (INSTITUTIONAL_TRM_EVENT)

## Métriques principales
| Métrique | Valeur |
|---|---|
| PF cost×1   | 0.0000 |
| PF cost×2   | 0.0000 |
| PF cost×3   | 0.0000 |
| Sharpe      | 0.0000 |
| Sortino     | 0.0000 |
| CAGR        | 0.00% |
| Max DD      | 0.00% |
| N trades    | 0 |
| Hit rate    | 0.00% |
| Expectancy  | 0.0000 USD |
| Avg Win     | 0.0000 USD |
| Avg Loss    | 0.0000 USD |
| W/L ratio   | 0.0000 |
| Avg holding | 0.0h |
| Turnover    | 0.00× |

## PnL
- Gross PnL : 0.00 USD
- Net PnL   : 0.00 USD
- Fees paid : 0.00 USD
- Slippage  : 0.00 USD

## Verdict

**REJECT**

| Gate    | Critères |
|---|---|
| REJECT  | PF×1<1.00 OU PF×2<1.00 OU N<30 OU Sharpe<0 |
| INCUBATE| PF×1∈[1.05,1.25) ET N≥50 ET MaxDD<20% |
| PAPER   | PF×1≥1.25 ET PF×2≥1.05 ET Sharpe≥0.60 ET N≥100 ET MaxDD<20% |
| PROMOTE | PF×1≥1.30 ET PF×2≥1.10 ET PF×3≥1.05 ET Sharpe≥0.80 ET N≥150 ET MaxDD<18% |