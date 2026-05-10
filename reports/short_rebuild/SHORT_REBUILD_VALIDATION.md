# SHORT REBUILD VALIDATION

## Walk-Forward Multi-Actif

**Date** : 2026-05-10T21:16:28.624598

**Verdict** : `SHORT_REJECTED`

**Actifs** : 50  |  **Folds run** : 5  |  **Folds OK** : 1/5  |  **Catastrophics** : 2

**PF médian** : 1.0079  |  **PF stress médian (OK)** : 1.6182  |  **Total trades** : 2,192

| Fold | Status | Trades | PF | PF_stress | DD% | Sq% | AUC_val |
|------|--------|--------|----|-----------|-----|-----|---------|
| 2022 | OK | 151 | 1.659 | 1.618 | 0.09 | 29.14% | 0.6413 |
| 2023 | CATASTROPHIC | 425 | 0.472 | 0.451 | 0.38 | 33.65% | 0.7080 |
| 2024 | CATASTROPHIC | 578 | 0.559 | 0.542 | 0.57 | 41.18% | 0.7720 |
| 2025 | WEAK | 5 | 7.420 | 7.194 | 0.00 | 20.00% | 0.7447 |
| 2026 | WEAK | 1,033 | 1.008 | 0.955 | 0.16 | 24.59% | 0.7259 |

**Ensemble** : 0.4× Transformer + 0.35× LightGBM + 0.25× TRMShortFleet

