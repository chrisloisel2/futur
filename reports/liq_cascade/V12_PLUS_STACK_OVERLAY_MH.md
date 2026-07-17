# Overlay mesuré — 3 jambes (tapes mh)

Fenêtre 2023-01-12 → 2026-03-31

## Corrélations quotidiennes

|                               |   V1.2 (socle) |   STACK 3 moteurs MH (2%/trade) |   BASIS_TERM (quarterly, 50%) |
|:------------------------------|---------------:|--------------------------------:|------------------------------:|
| V1.2 (socle)                  |          1     |                           0.108 |                        -0.102 |
| STACK 3 moteurs MH (2%/trade) |          0.108 |                           1     |                        -0.026 |
| BASIS_TERM (quarterly, 50%)   |         -0.102 |                          -0.026 |                         1     |

## Statistiques

| label                         |   roi_total |   roi_ann |   maxdd |   sharpe |
|:------------------------------|------------:|----------:|--------:|---------:|
| V1.2 (socle)                  |      0.3199 |    0.0902 | -0.0211 |     2.65 |
| STACK 3 moteurs MH (2%/trade) |      0.2144 |    0.0623 | -0.0322 |     1.55 |
| BASIS_TERM (quarterly, 50%)   |      0.2891 |    0.0822 | -0.0295 |     2.17 |
| COMBINÉ (3 jambes)            |      1.0661 |    0.2533 | -0.031  |     3.62 |

## Stack — ROI par année

2023 +2.20% · 2024 +14.08% · 2025 +5.22% · 2026 -1.01%


## Réserves

- Overlay = produit des equity (capital partagé) ; gross combiné à
  vérifier en intégration multileg (portfolio margin requis).
- Stack = tapes OOS (prix 5-min implicite, slippage cascade à valider) ;
  2022 exclu du stack (folds sans entraînement).
- BASIS_TERM : S≈perp close (approx documentée), liquidité quarterly
  moindre que perp, MTM réel via klines 1d.
- Promotion : SHADOW forward ≥30 j requis (timer déployé).