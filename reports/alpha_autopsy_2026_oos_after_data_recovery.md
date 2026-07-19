# Alpha Autopsy — OOS 2026-01-01 → 2026-06-20

Question : le portefeuille perd-il **faute d'alpha** ou par **mauvaise exécution** ?

## 1. Contribution marginale de chaque brique (ablation)

| Brique | Δ ROI |
|---|---:|
| alpha brut (G_all_raw) | -35.0% |
| + allocator (H−G) | +29.2% |
| + exit (I−H) | -11.8% |
| + governor (J−I) | +15.6% |
| = full-stack (J) | -2.1% |

| Run | ROI | PF | trades | t/mois | maxDD | gate |
|---|---:|---:|---:|---:|---:|---|
| A_trm_legacy | +0.0% | inf | 1 | 0.2 | -0.5% | FAIL |
| B_trm_alloc | -0.1% | 0.00 | 1 | 0.2 | -0.1% | FAIL |
| C_trm_alloc_exit | -0.1% | 0.00 | 1 | 0.2 | -0.1% | FAIL |
| D_trm_full | -0.1% | 0.00 | 1 | 0.2 | -0.1% | FAIL |
| E_pullback_raw | -0.7% | 0.94 | 44 | 7.8 | -4.8% | FAIL |
| F_pullback_full | +0.2% | 1.58 | 6 | 1.1 | -0.4% | FAIL |
| G_all_raw | -35.0% | 0.70 | 717 | 126.5 | -40.4% | FAIL |
| H_all_alloc | -5.8% | 0.83 | 441 | 77.8 | -9.1% | FAIL |
| I_all_alloc_exit | -17.7% | 0.69 | 1232 | 217.4 | -18.5% | FAIL |
| J_all_full | -2.1% | 0.73 | 172 | 30.3 | -3.0% | FAIL |

## 2. Qualité du signal par moteur (A_TRADE, realized_shadow_result)

| Moteur | n A | PF | PnL moy | PnL méd | WR | verdict |
|---|---:|---:|---:|---:|---:|---|
| CARRY_BASIS | 2468 | 0.60 | -0.61% | -0.39% | 43% | KILL or REBUILD |
| CROSS_SECTIONAL_LONG | 8162 | 0.74 | -0.33% | -0.18% | 47% | KILL or REBUILD |
| LIQUIDATION_REBOUND | 466 | 0.79 | -0.13% | -0.10% | 48% | KILL or REBUILD |
| PULLBACK_LONG | 168 | 0.72 | -0.44% | -0.38% | 42% | KILL or REBUILD |
| TRM_TREND_LONG | 5 | 0.01 | -2.18% | -2.56% | 20% | INSUFFICIENT_SAMPLE → SHADOW |

## 3. PnL A_TRADE par actif

| Actif | n | PF | PnL moy |
|---|---:|---:|---:|
| ADAUSDT | 749 | 0.53 | -0.88% |
| AVAXUSDT | 725 | 0.68 | -0.51% |
| BNBUSDT | 973 | 0.89 | -0.11% |
| BTCUSDT | 2987 | 0.73 | -0.26% |
| ETHUSDT | 3133 | 0.71 | -0.38% |
| LINKUSDT | 755 | 0.56 | -0.79% |
| SOLUSDT | 975 | 0.72 | -0.45% |
| XRPUSDT | 972 | 0.85 | -0.21% |

## 4. PnL A_TRADE par régime

| Régime | n | PF | PnL moy |
|---|---:|---:|---:|
| NEUTRAL | 5 | 0.01 | -2.18% |
| UNKNOWN | 3102 | 0.62 | -0.53% |
| XS | 8162 | 0.74 | -0.33% |

## 5. PnL A_TRADE par mois (tous moteurs)

| Mois | n | PF | PnL moy |
|---|---:|---:|---:|
| 2026-01 | 1858 | 0.55 | -0.63% |
| 2026-02 | 2099 | 0.63 | -0.74% |
| 2026-03 | 1966 | 0.92 | -0.09% |
| 2026-04 | 1801 | 1.36 | +0.25% |
| 2026-05 | 2095 | 0.79 | -0.18% |
| 2026-06 | 1450 | 0.41 | -1.07% |

## 6. Diagnostic

**FAUTE D'ALPHA** : l'alpha brut (sans exécution) est déjà négatif → le problème est l'inventaire de signaux, pas seulement l'exécution.

- Moteurs à garder/étudier : —
- Moteurs à tuer/reconstruire : ['CARRY_BASIS', 'CROSS_SECTIONAL_LONG', 'LIQUIDATION_REBOUND', 'PULLBACK_LONG', 'TRM_TREND_LONG']
- ⚠ AVAX/BNB/DOT/LINK enriched CORROMPUS → conclusions cross-sectional/multi-actifs invalides tant que non réparés.
- ⚠ Exit engine : audit requis (ablation I−H négatif = churn destructeur).
