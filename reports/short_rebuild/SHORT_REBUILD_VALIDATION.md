# SHORT REBUILD VALIDATION

## Walk-Forward Multi-Actif

**Date** : 2026-05-13T10:34:34.444560

**Verdict** : `SHORT_REJECTED`

**Actifs** : 50  |  **Folds run** : 4  |  **Folds OK** : 0/5  |  **Catastrophics** : 4

**PF médian** : 0.6731  |  **PF stress médian (OK)** : 0.0000  |  **Total trades** : 239

| Fold | Status | Trades | PF | PF_stress | DD% | Sq% | AUC_val |
|------|--------|--------|----|-----------|-----|-----|---------|
| 2020 | SKIPPED | 0 | 0.000 | 0.000 | 0.00 | 0.00% | 0.0000 |
| 2021 | CATASTROPHIC | 140 | 0.848 | 0.821 | 0.06 | 50.71% | 0.7282 |
| 2022 | CATASTROPHIC | 64 | 0.498 | 0.486 | 0.10 | 43.75% | 0.6159 |
| 2023 | SKIPPED | 0 | 0.000 | 0.000 | 0.00 | 0.00% | 0.0000 |
| 2024 | SKIPPED | 0 | 0.000 | 0.000 | 0.00 | 0.00% | 0.0000 |
| 2025 | CATASTROPHIC | 25 | 0.296 | 0.286 | 0.05 | 56.00% | 0.7191 |
| 2026 | CATASTROPHIC | 10 | 0.855 | 0.835 | 0.02 | 60.00% | 0.7082 |

**Ensemble** : 0.0× Transformer + 0.65× LightGBM + 0.35× TRMShortFleet

