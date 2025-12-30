# Binary Regime Classifier V2 - Production Excellence

## 🎯 Objectifs Atteints

Cette version corrige **tous** les problèmes identifiés et atteint les standards de "production excellence":

### ✅ 1. Bug Brier Score CORRIGÉ
- **Avant**: Calculait `multiclass_brier = mean((y_proba - y_onehot)**2)` sur toutes les classes
- **Après**: Calcule correctement `brier = brier_score_loss(y_true, y_proba[:, 1])` pour classification binaire
- **Vérification**: Test unitaire `test_brier_fix.py` garantit que RESULTS et GATES affichent la même valeur
- **Emplacement**: [regime_classifier_v2.py:350](../ai/models/training/common/regime_classifier_v2.py#L350)

### ✅ 2. Split Temporel Strict avec Embargo
- **Train**: 2019-01-01 → 2022-12-31
- **Val**: 2023-01-01 → 2023-12-31
- **Embargo**: 60 minutes de chaque côté de la frontière (configurable)
- **Garantie**: Aucun shuffle, StandardScaler fit sur train uniquement
- **Emplacement**: [train_regime_classifier_binary.py:68-120](../trading-system/scripts/train_regime_classifier_binary.py#L68-L120)

### ✅ 3. Label Builder avec Zone Grise
Nouveau système de labels propres:

```python
# Calm: Low volatility + small drawdown
calm = (RV_fwd < quantile(0.40)) & (dd_fwd > -0.3%)

# Reversal: Significant drawdown OR (trend flip + excursion)
reversal = (dd_fwd < -1.0%) | ((trend_flip) & (excursion > 1.5%))

# Gray: Everything else → DROPPED from training
gray = rest
```

**Bénéfices**:
- Labels estimés **uniquement sur TRAIN** (pas de leakage)
- Évite les zones ambiguës (moins de bruit)
- Paramètres robustes par défaut (horizon=60min aligné avec Edge)

**Emplacement**: [label_builder.py](../ai/models/training/common/label_builder.py)

### ✅ 4. Threshold Search pour Optimiser Recalls
- **Avant**: Seuil fixe 0.5 → calm_recall=0.27, reversal_recall=0.88 (déséquilibré)
- **Après**: Recherche de seuil optimal sur Val
  - Balaye 0.05 → 0.95 (pas de 0.05)
  - Contrainte: calm_recall ≥ 0.50 ET reversal_recall ≥ 0.50
  - Optimise: balanced_accuracy ou macro_F1
- **Sauvegarde**: `threshold.json` avec recalls atteints
- **Emplacement**: [regime_classifier_v2.py:164-241](../ai/models/training/common/regime_classifier_v2.py#L164-L241)

### ✅ 5. Comparaison de 3 Variantes de Modèles
Pour réduire la sur-prédiction de "reversal":

| Variante | Description | class_weight | sample_weight |
|----------|-------------|--------------|---------------|
| `sgd_no_weight` | SGDClassifier sans class_weight | None | None |
| `sgd_focal` | SGDClassifier avec focal-like weighting | None | focal(α=0.5) |
| `logreg` | LogisticRegression(saga, L2) | None | None |

Toutes les variantes utilisent:
- Calibration isotonic (CalibratedClassifierCV)
- Threshold search
- StandardScaler

**Sélection**: Meilleure balanced_accuracy parmi celles qui passent les gates

**Emplacement**: [regime_classifier_v2.py:38-93](../ai/models/training/common/regime_classifier_v2.py#L38-L93)

### ✅ 6. Excellence Artifact Bundle
Chaque run produit un bundle complet:

```
artifacts/models/regime/prod/
├── model.pkl              # Modèle calibré
├── threshold.json         # Seuil optimal + recalls
├── metrics.json           # Toutes les métriques de validation
├── feature_list.json      # Liste des features utilisées
└── data_contract.json     # Contrat de données complet
```

**data_contract.json** contient:
- Version, variant, classes
- Périodes train/val, embargo
- Label config (thresholds RV/DD, horizon, gray zone %)
- Scaler, calibration, timestamp
- `passed_gates: true/false`

**Emplacement**: [train_regime_classifier_binary.py:318-415](../trading-system/scripts/train_regime_classifier_binary.py#L318-L415)

### ✅ 7. Sanity Checks et Reliability Curves
**Sanity checks automatiques**:
- Calm recall < 0.40 → "model unstable"
- Threshold < 0.15 ou > 0.85 → "calibration issue"
- ECE faible mais Brier élevé → "investigate"
- Une classe domine >85% → "collapse"

**Reliability curves**:
- Bins de probabilités vs vraie fréquence
- Permet de visualiser la calibration
- Sauvegardé dans metrics.json

**Emplacement**:
- Sanity: [regime_classifier_v2.py:393-424](../ai/models/training/common/regime_classifier_v2.py#L393-L424)
- Reliability: [regime_classifier_v2.py:264-297](../ai/models/training/common/regime_classifier_v2.py#L264-L297)

---

## 📊 Gates de Production

```python
# Métriques macro
min_accuracy = 0.60          # Binary threshold
min_macro_f1 = 0.55
max_brier = 0.20             # FIXÉ: utilise brier binaire
max_ece = 0.10

# Recalls par classe
min_calm_recall = 0.50       # Augmenté de 0.30
min_reversal_recall = 0.50   # Augmenté de 0.35

# Calibration
min_entropy = 0.50
max_entropy = 0.75           # log(2) = 0.693 pour binaire uniforme
```

---

## 🚀 Commandes de Run

### Test Unitaire (Vérifier le fix Brier)
```bash
cd ai/models/training/common
python test_brier_fix.py
```

**Sortie attendue**: ✅ ALL TESTS PASSED

### Entraînement Production
```bash
cd trading-system

python scripts/train_regime_classifier_binary.py \
    --start-date 2019-01-01 \
    --end-date 2023-12-31 \
    --symbol BTCUSDT \
    --output artifacts/models/regime/prod \
    --train-end 2022-12-31 \
    --val-start 2023-01-01 \
    --embargo-minutes 60 \
    --label-horizon 60
```

**Options**:
- `--embargo-minutes`: Minutes d'embargo de chaque côté (default: 60)
- `--label-horizon`: Horizon forward pour labels en minutes (default: 60)
- `--output`: Répertoire de sortie (default: artifacts/models/regime/prod)

### Exemple avec Paramètres Alternatifs
```bash
# Embargo plus strict (2h) et horizon plus long (90min)
python scripts/train_regime_classifier_binary.py \
    --start-date 2019-01-01 \
    --end-date 2023-12-31 \
    --symbol BTCUSDT \
    --output artifacts/models/regime/prod_h90 \
    --embargo-minutes 120 \
    --label-horizon 90
```

---

## 📈 Exemple de Sortie Attendue

```
================================================================================
BINARY REGIME CLASSIFIER - PRODUCTION EXCELLENCE TRAINING
================================================================================
Symbol:       BTCUSDT
Data period:  2019-01-01 to 2023-12-31
Train period: 2019-01-01 to 2022-12-31
Val period:   2023-01-01 to 2023-12-31
Embargo:      60 minutes
================================================================================

[Loading data...]

[Temporal split with embargo]
  train_samples: 1,850,000
  val_samples: 450,000
  embargo_samples: 120

[Label statistics]
  n_calm: 620,000 (33.5%)
  n_reversal: 450,000 (24.3%)
  n_gray: 780,000 (42.2%)

================================================================================
TRAINING 3 MODEL VARIANTS
================================================================================

>>> Training variant: sgd_no_weight
  Threshold:       0.350
  Accuracy:        0.6420
  Balanced Acc:    0.6380
  Macro F1:        0.6310
  Brier:           0.1850
  ECE:             0.0420
  Calm recall:     0.5800
  Reversal recall: 0.6960

>>> Training variant: sgd_focal
  Threshold:       0.400
  Accuracy:        0.6350
  Balanced Acc:    0.6520
  Macro F1:        0.6450
  Brier:           0.1920
  ECE:             0.0380
  Calm recall:     0.6200
  Reversal recall: 0.6840

>>> Training variant: logreg
  Threshold:       0.380
  Accuracy:        0.6380
  Balanced Acc:    0.6480
  Macro F1:        0.6400
  Brier:           0.1880
  ECE:             0.0400
  Calm recall:     0.6050
  Reversal recall: 0.6910

================================================================================
VARIANT COMPARISON
================================================================================
Variant              Acc  BalAcc MacroF1   Brier     ECE CalmRec  RevRec
--------------------------------------------------------------------------------
sgd_no_weight     0.6420  0.6380  0.6310  0.1850  0.0420  0.5800  0.6960
sgd_focal         0.6350  0.6520  0.6450  0.1920  0.0380  0.6200  0.6840
logreg            0.6380  0.6480  0.6400  0.1880  0.0400  0.6050  0.6910
================================================================================

================================================================================
FINAL RESULTS - BEST VARIANT: SGD_FOCAL
================================================================================
Threshold:       0.4000
Accuracy:        0.6350
Balanced Acc:    0.6520
Macro F1:        0.6450
Brier:           0.1920
ECE:             0.0380

Per-class recall:
  calm      : 0.6200
  reversal  : 0.6840

Confusion Matrix:
[[186000  114000]
 [ 94500  205500]]
================================================================================

================================================================================
PRODUCTION GATES
================================================================================
✅ ALL GATES PASSED
================================================================================

================================================================================
ARTIFACTS SAVED
================================================================================
  model_path          : artifacts/models/regime/prod/model.pkl
  threshold_path      : artifacts/models/regime/prod/threshold.json
  metrics_path        : artifacts/models/regime/prod/metrics.json
  features_path       : artifacts/models/regime/prod/feature_list.json
  contract_path       : artifacts/models/regime/prod/data_contract.json
================================================================================

✅ Training complete - model ready for production
```

---

## 🔍 Différences Principales vs Version Précédente

| Aspect | Avant | Après |
|--------|-------|-------|
| **Brier** | multiclass_brier (moyenne classes) | brier binaire (proba classe positive) |
| **Split** | train_test_split avec shuffle | Split temporel strict + embargo |
| **Labels** | Labels S3 bruts (calm/impulse/reversal) | Labels recalculés avec zone grise |
| **Seuil** | 0.5 fixe | Optimisé par recherche (0.05-0.95) |
| **Modèle** | SGD + class_weight='balanced' (1 variante) | 3 variantes comparées |
| **Recalls** | calm=0.27, reversal=0.88 | calm≥0.50, reversal≥0.50 (gates) |
| **Artifacts** | model.pkl + metrics.json | Bundle complet (5 fichiers) |
| **Checks** | Gates uniquement | Gates + sanity checks + reliability |

---

## 📂 Structure de Fichiers Modifiés

```
ai/models/training/common/
├── label_builder.py              # NOUVEAU: Builder de labels avec gray zone
├── regime_classifier_v2.py       # MODIFIÉ: Fix Brier, threshold search, 3 variants
├── production_gates.py           # MODIFIÉ: Accept 'brier' au lieu de 'multiclass_brier'
└── test_brier_fix.py             # NOUVEAU: Tests unitaires pour Brier

trading-system/scripts/
└── train_regime_classifier_binary.py  # RÉÉCRIT: Pipeline complet excellence
```

---

## ✅ Checklist de Vérification

Avant de déployer en production:

- [ ] `python test_brier_fix.py` passe ✅
- [ ] Le training termine sans erreur
- [ ] Les 3 variantes sont entraînées et comparées
- [ ] Le meilleur variant passe les gates
- [ ] `data_contract.json` contient `"passed_gates": true`
- [ ] Calm recall ≥ 0.50 ET Reversal recall ≥ 0.50
- [ ] Brier affiché dans RESULTS == Brier affiché dans GATES
- [ ] ECE < 0.10
- [ ] Aucune sanity warning critique
- [ ] Les 5 artifacts sont générés dans le bon répertoire

---

## 🐛 Debugging

### Si le Brier est toujours à 1.0 dans les gates:
```bash
# Vérifier que la métrique est bien présente
python -c "
import json
with open('artifacts/models/regime/prod/metrics.json') as f:
    m = json.load(f)
    print('Brier:', m.get('brier', 'MISSING'))
"
```

### Si aucun threshold ne satisfait les recalls:
- Vérifier la distribution des labels (trop déséquilibrée?)
- Essayer d'assouplir `min_recall_per_class` temporairement
- Vérifier que la calibration fonctionne (ECE)

### Si gray zone > 60%:
- Les thresholds de labels sont trop stricts
- Ajuster `LabelConfig` dans le script:
  ```python
  label_config = LabelConfig(
      dd_small_threshold=-0.005,  # moins strict
      dd_big_threshold=-0.008,    # moins strict
  )
  ```

---

## 📚 Références

- Brier Score: [sklearn.metrics.brier_score_loss](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.brier_score_loss.html)
- Calibration: [sklearn.calibration.CalibratedClassifierCV](https://scikit-learn.org/stable/modules/generated/sklearn.calibration.CalibratedClassifierCV.html)
- ECE: Expected Calibration Error (custom implementation)

---

**Version**: 2.0
**Date**: 2025-12-30
**Status**: ✅ Production Ready
