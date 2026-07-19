# Overlay mesuré — 3 jambes (tapes legacy)

Fenêtre 2023-01-01 → 2026-03-31

## Corrélations quotidiennes

|                             |   V1.2 (socle) |   STACK 3 moteurs (2%/trade) |   BASIS_TERM (quarterly, 50%) |
|:----------------------------|---------------:|-----------------------------:|------------------------------:|
| V1.2 (socle)                |          1     |                        0.09  |                        -0.102 |
| STACK 3 moteurs (2%/trade)  |          0.09  |                        1     |                        -0.034 |
| BASIS_TERM (quarterly, 50%) |         -0.102 |                       -0.034 |                         1     |

## Statistiques

| label                       |   roi_total |   roi_ann |   maxdd |   sharpe |
|:----------------------------|------------:|----------:|--------:|---------:|
| V1.2 (socle)                |      0.3242 |    0.0904 | -0.0211 |     2.67 |
| STACK 3 moteurs (2%/trade)  |      0.11   |    0.0327 | -0.0342 |     0.98 |
| BASIS_TERM (quarterly, 50%) |      0.2891 |    0.0814 | -0.0295 |     2.16 |
| COMBINÉ (3 jambes)          |      0.8947 |    0.2177 | -0.0319 |     3.41 |

## Stack — ROI par année

2022 -3.98% · 2023 +2.29% · 2024 +6.52% · 2025 +2.70% · 2026 -1.13%


## Réserves

- Overlay = produit des equity (capital partagé) ; gross combiné à
  vérifier en intégration multileg (portfolio margin requis).
- Stack = tapes OOS (prix 5-min implicite, slippage cascade à valider) ;
  2022 exclu du stack (folds sans entraînement).
- BASIS_TERM : S≈perp close (approx documentée), liquidité quarterly
  moindre que perp, MTM réel via klines 1d.
- Promotion : SHADOW forward ≥30 j requis (timer déployé).