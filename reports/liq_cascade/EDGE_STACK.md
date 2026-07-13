# EDGE STACK — analyse inter-moteurs (trades OOS walk-forward)

- **LIQ_CASCADE** : 7152 trades OOS, 2022-01-02 → 2026-07-04, net total (sizé 10%) +152.7%
- **CROWDING_REVERSAL** : 290 trades OOS, 2024-01-03 → 2026-06-23, net total (sizé 10%) +115.2%
- **PREMIUM_DISLOCATION** : 3885 trades OOS, 2022-01-01 → 2026-06-30, net total (sizé 10%) +140.4%

## Corrélation des PnL mensuels

|                     |   LIQ_CASCADE |   CROWDING_REVERSAL |   PREMIUM_DISLOCATION |
|:--------------------|--------------:|--------------------:|----------------------:|
| LIQ_CASCADE         |         1     |               0.123 |                 0.567 |
| CROWDING_REVERSAL   |         0.123 |               1     |                 0.171 |
| PREMIUM_DISLOCATION |         0.567 |               0.171 |                 1     |

## Chevauchement des trades (même symbole ± 4h)

- LIQ_CASCADE ∩ CROWDING_REVERSAL : 396/7152 (5.5% des trades de LIQ_CASCADE)
- LIQ_CASCADE ∩ PREMIUM_DISLOCATION : 1501/7152 (21.0% des trades de LIQ_CASCADE)
- CROWDING_REVERSAL ∩ PREMIUM_DISLOCATION : 126/290 (43.4% des trades de CROWDING_REVERSAL)

## STACK (union, cap 6, 10%/trade)

- trades pris : 6205/11327 | equity finale : -50.1%

|   year |    n |   pf | roi    | mix                                                                        |
|-------:|-----:|-----:|:-------|:---------------------------------------------------------------------------|
|   2022 | 1754 | 0.85 | -31.2% | {'LIQ_CASCADE': 1197, 'PREMIUM_DISLOCATION': 557}                          |
|   2023 |  360 | 1.53 | +14.7% | {'LIQ_CASCADE': 324, 'PREMIUM_DISLOCATION': 36}                            |
|   2024 | 1795 | 0.95 | -10.9% | {'LIQ_CASCADE': 1033, 'PREMIUM_DISLOCATION': 709, 'CROWDING_REVERSAL': 53} |
|   2025 | 1175 | 0.9  | -14.6% | {'LIQ_CASCADE': 602, 'PREMIUM_DISLOCATION': 560, 'CROWDING_REVERSAL': 13}  |
|   2026 | 1121 | 0.84 | -16.9% | {'LIQ_CASCADE': 626, 'PREMIUM_DISLOCATION': 470, 'CROWDING_REVERSAL': 25}  |

## STACK SIM B (gross-cap 60%, 2%/trade, pas de cap de nombre)

- trades pris : 9583/11327 | equity finale : -4.7%

|   year |    n |   pf | roi    |
|-------:|-----:|-----:|:-------|
|   2022 | 2601 | 0.86 | -9.6%  |
|   2023 |  536 | 2.15 | +7.7%  |
|   2024 | 2871 | 1.19 | +14.7% |
|   2025 | 1905 | 0.75 | -14.6% |
|   2026 | 1670 | 1    | -0.1%  |

## STACK SIM C (priorité score par batch, gross ≤ 60%, 2%/trade)

- trades pris : 9589/11327 | equity finale : -5.1%

|   year |    n |   pf | roi    |
|-------:|-----:|-----:|:-------|
|   2022 | 2601 | 0.86 | -9.6%  |
|   2023 |  536 | 2.15 | +7.8%  |
|   2024 | 2877 | 1.19 | +14.1% |
|   2025 | 1905 | 0.74 | -14.6% |
|   2026 | 1670 | 1    | +0.0%  |

## STACK SIM D (books séparés, 20% gross/moteur, 2%/trade)

| engine              | roi_book   |
|:--------------------|:-----------|
| LIQ_CASCADE         | -9.5%      |
| CROWDING_REVERSAL   | +17.8%     |
| PREMIUM_DISLOCATION | -4.3%      |

- somme des books : +4.0% (sur la fenêtre de chaque tape)