# Alpha Autopsy — OOS 2026-01-01 → 2026-06-20

Question : le portefeuille perd-il **faute d'alpha** ou par **mauvaise exécution** ?

## 1. Contribution marginale de chaque brique (ablation)

| Brique | Δ ROI |
|---|---:|
| alpha brut (G_all_raw) | -26.3% |
| + allocator (H−G) | +19.3% |
| + exit (I−H) | -9.5% |
| + governor (J−I) | +14.1% |
| = full-stack (J) | -2.4% |

| Run | ROI | PF | trades | t/mois | maxDD | gate |
|---|---:|---:|---:|---:|---:|---|
| A_trm_legacy | +0.0% | inf | 1 | 0.2 | -0.5% | FAIL |
| B_trm_alloc | -0.1% | 0.00 | 1 | 0.2 | -0.1% | FAIL |
| C_trm_alloc_exit | -0.1% | 0.00 | 1 | 0.2 | -0.1% | FAIL |
| D_trm_full | -0.1% | 0.00 | 1 | 0.2 | -0.1% | FAIL |
| E_pullback_raw | -0.7% | 0.94 | 44 | 7.8 | -4.8% | FAIL |
| F_pullback_full | +0.2% | 1.58 | 6 | 1.1 | -0.4% | FAIL |
| G_all_raw | -26.3% | 0.77 | 627 | 110.6 | -34.3% | FAIL |
| H_all_alloc | -7.0% | 0.77 | 369 | 65.1 | -9.5% | FAIL |
| I_all_alloc_exit | -16.5% | 0.66 | 1013 | 178.7 | -17.8% | FAIL |
| J_all_full | -2.4% | 0.65 | 147 | 25.9 | -3.5% | FAIL |

## 2. Qualité du signal par moteur (A_TRADE, realized_shadow_result)

| Moteur | n A | PF | PnL moy | PnL méd | WR | verdict |
|---|---:|---:|---:|---:|---:|---|
| CARRY_BASIS | 2465 | 0.61 | -0.61% | -0.39% | 43% | KILL or REBUILD |
| CROSS_SECTIONAL_LONG | 8106 | 0.79 | -0.27% | -0.17% | 47% | KILL or REBUILD |
| LIQUIDATION_REBOUND | 466 | 0.79 | -0.13% | -0.10% | 48% | KILL or REBUILD |
| PULLBACK_LONG | 168 | 0.72 | -0.44% | -0.38% | 42% | KILL or REBUILD |
| TRM_TREND_LONG | 5 | 0.01 | -2.18% | -2.56% | 20% | INSUFFICIENT_SAMPLE → SHADOW |

## 3. PnL A_TRADE par actif

| Actif | n | PF | PnL moy |
|---|---:|---:|---:|
| ADAUSDT | 1528 | 0.73 | -0.44% |
| BTCUSDT | 3161 | 0.74 | -0.25% |
| ETHUSDT | 3416 | 0.72 | -0.37% |
| SOLUSDT | 1486 | 0.73 | -0.45% |
| XRPUSDT | 1619 | 0.81 | -0.26% |

## 4. PnL A_TRADE par régime

| Régime | n | PF | PnL moy |
|---|---:|---:|---:|
| NEUTRAL | 5 | 0.01 | -2.18% |
| UNKNOWN | 3099 | 0.63 | -0.53% |
| XS | 8106 | 0.79 | -0.27% |

## 5. PnL A_TRADE par mois (tous moteurs)

| Mois | n | PF | PnL moy |
|---|---:|---:|---:|
| 2026-01 | 1858 | 0.62 | -0.55% |
| 2026-02 | 2099 | 0.66 | -0.68% |
| 2026-03 | 1966 | 0.92 | -0.09% |
| 2026-04 | 1801 | 1.53 | +0.37% |
| 2026-05 | 2095 | 0.67 | -0.29% |
| 2026-06 | 1391 | 0.49 | -0.91% |

## 6. Diagnostic

**FAUTE D'ALPHA** : l'alpha brut (sans exécution) est déjà négatif → le problème est l'inventaire de signaux, pas seulement l'exécution.

- Moteurs à garder/étudier : —
- Moteurs à tuer/reconstruire : ['CARRY_BASIS', 'CROSS_SECTIONAL_LONG', 'LIQUIDATION_REBOUND', 'PULLBACK_LONG', 'TRM_TREND_LONG']
- ⚠ AVAX/BNB/DOT/LINK enriched CORROMPUS → conclusions cross-sectional/multi-actifs invalides tant que non réparés.
- ⚠ Exit engine : audit requis (ablation I−H négatif = churn destructeur).
