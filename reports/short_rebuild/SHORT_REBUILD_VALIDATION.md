# SHORT REBUILD VALIDATION

## Walk-Forward Multi-Actif

**Date** : 2026-05-11T10:23:09.975516

**Verdict** : `SHORT_PAPER_CANDIDATE`

**Actifs** : 50  |  **Folds run** : 3  |  **Folds OK** : 2/5  |  **Catastrophics** : 0

**PF médian** : 1.3056  |  **PF stress médian (OK)** : 1.6048  |  **Total trades** : 296

| Fold | Status | Trades | PF | PF_stress | DD% | Sq% | AUC_val |
|------|--------|--------|----|-----------|-----|-----|---------|
| 2022 | OK | 178 | 1.306 | 1.273 | 0.07 | 31.46% | 0.6324 |
| 2023 | SKIPPED | 0 | 0.000 | 0.000 | 0.00 | 0.00% | 0.0000 |
| 2024 | SKIPPED | 0 | 0.000 | 0.000 | 0.00 | 0.00% | 0.0000 |
| 2025 | WEAK | 76 | 1.208 | 1.171 | 0.03 | 25.00% | 0.7440 |
| 2026 | OK | 42 | 2.012 | 1.937 | 0.01 | 23.81% | 0.7279 |

**Ensemble** : 0.4× Transformer + 0.35× LightGBM + 0.25× TRMShortFleet

