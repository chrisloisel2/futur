# Optimisation du sleeve LONG — mesuré 2022→2026 (net de coûts)

| config | ROI/an | vol | maxDD | Sharpe |
|---|---:|---:|---:|---:|
| A · régime seul (ACTUEL) | +26.2% | 44.3% | -46.3% | 0.74 |
| B · +trend filter | +19.0% | 31.9% | -33.6% | 0.7 |
| C · +inverse-vol | +16.3% | 28.9% | -30.9% | 0.66 |
| D · +vol targeting | +10.1% | 20.6% | -27.5% | 0.57 |

Poids inverse-vol actuels : {'BTCUSDT': 0.444, 'ETHUSDT': 0.301, 'SOLUSDT': 0.254}
Trend actuel (>MA20) : {'BTCUSDT': True, 'ETHUSDT': True, 'SOLUSDT': True} · régime bull : False