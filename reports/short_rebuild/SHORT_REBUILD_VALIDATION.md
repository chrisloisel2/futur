# SHORT REBUILD VALIDATION

## Walk-Forward Multi-Actif

**Date** : 2026-05-10T23:56:36.082874

**Verdict** : `SHORT_REJECTED`

**Actifs** : 50  |  **Folds run** : 5  |  **Folds OK** : 0/5  |  **Catastrophics** : 1

**PF médian** : 0.6927  |  **PF stress médian (OK)** : 0.0000  |  **Total trades** : 2,945

| Fold | Status | Trades | PF | PF_stress | DD% | Sq% | AUC_val |
|------|--------|--------|----|-----------|-----|-----|---------|
| 2022 | WEAK | 967 | 1.099 | 1.066 | 0.16 | 31.44% | 0.6324 |
| 2023 | WEAK | 1 | 0.000 | 0.000 | 0.00 | 0.00% | 0.7104 |
| 2024 | CATASTROPHIC | 1,895 | 0.693 | 0.656 | 0.66 | 27.18% | 0.7761 |
| 2025 | WEAK | 8 | 0.045 | 0.041 | 0.03 | 62.50% | 0.7440 |
| 2026 | WEAK | 74 | 1.079 | 1.041 | 0.03 | 25.68% | 0.7279 |

**Ensemble** : 0.4× Transformer + 0.35× LightGBM + 0.25× TRMShortFleet

