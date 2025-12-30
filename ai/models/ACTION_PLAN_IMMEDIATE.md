# Action Immédiate : Migration Régimes Binaires

## Status : 🚨 URGENT - À FAIRE MAINTENANT

Date : 2025-12-29

---

## 1) STOPPER TOUT TRAINING 3-CLASSES

### ⛔ Fichiers à NE PLUS UTILISER

```bash
# OBSOLÈTES (3 classes)
ai/models/training/common/production_regime.py  ← DEPRECATED
ai/models/training/train_regime_classifier_*.py (si 3-classes)
```

**Raison** : impulse n'est PAS un régime → accuracy 46%, impulse recall 31%

---

## 2) UTILISER LES NOUVEAUX MODULES

### ✅ Fichiers CORRECTS (binaires + event)

```python
# Régimes BINAIRES
from regime_classifier_v2 import (
    train_calibrated_regime_classifier,
    evaluate_regime_classifier,
    production_gates,
    DEFAULT_CLASSES  # ['calm', 'reversal']
)

# Impulse EVENT
from impulse_detector import ImpulseDetector

# Meta-control + Execution
from meta_control import MetaControl
from execution_engine import ExecutionEngine

# Gates
from production_gates import RegimeClassifierGates  # UPDATED pour binaire
from impulse_gates import ImpulseGates
```

---

## 3) NOUVEAU WORKFLOW D'ENTRAÎNEMENT

### Étape 1 : Générer labels BINAIRES

```python
# Supprimer impulse des labels
labels_binary = np.where(labels == 1, -1, labels)  # impulse → invalid
labels_binary = labels_binary[labels_binary >= 0]  # drop

# Ou regénérer depuis scratch avec logique binaire
def create_binary_labels(df):
    # CALM: low drift, moderate vol
    is_calm = (
        (abs(df['ret_60m']) < 0.002) &
        (df['rv_60'] < df['rv_60'].quantile(0.6))
    )

    # REVERSAL: momentum reversal
    is_reversal = (
        (np.sign(df['ret_5m']) != np.sign(df['ret_60m'])) &
        (abs(df['ret_5m']) > 0.001)
    )

    labels = np.where(is_calm, 0, np.where(is_reversal, 1, -1))
    return labels[labels >= 0]  # drop ambiguous
```

### Étape 2 : Entraîner modèle binaire

```python
from regime_classifier_v2 import train_calibrated_regime_classifier

# Train
clf = train_calibrated_regime_classifier(
    X_train, y_train,
    class_names=['calm', 'reversal']  # BINARY
)

# Evaluate
metrics = evaluate_regime_classifier(
    clf, X_val, y_val,
    class_names=['calm', 'reversal']
)

# GATES
from production_gates import RegimeClassifierGates
gates = RegimeClassifierGates()
passed, reason = gates.validate(metrics)

if not passed:
    raise ValueError(f"Production gates failed: {reason}")

# Target metrics
assert metrics['accuracy'] > 0.60, "Binary accuracy too low"
assert metrics['ece'] < 0.10, "Calibration too poor"
```

### Étape 3 : Ajouter impulse features

```python
from impulse_detector import ImpulseDetector, create_impulse_features_batch

# Batch features (pour backtest)
df = create_impulse_features_batch(df)
# Ajoute: impulse_score, is_impulse

# Ou online (pour production)
detector = ImpulseDetector(threshold=0.7)
is_impulse, score = detector.detect(
    timestamp=ts,
    ret_1m=ret_1m,
    rv_60=rv_60,
    volume=volume,
    volume_ma=volume_ma,
    volume_std=volume_std,
    spread_z=spread_z,
)
```

### Étape 4 : Valider impulse gates

```python
from impulse_gates import ImpulseGates, validate_impulse_production

# Event metrics
impulse_metrics = detector.get_event_metrics(total_days=30)

# Gates
passed, report = validate_impulse_production(impulse_metrics)

if not passed:
    print(f"Impulse gates failed: {report['failures']}")

# Expected metrics
# - frequency: 1-10/day
# - avg_score: 0.75-0.85
# - avg_pnl: ≥0 or slightly negative
# - cost_ratio: <2x
```

---

## 4) GATES DE PRODUCTION (MODIFIÉS)

### Régimes (BINARY)

```python
from production_gates import RegimeClassifierGates

gates = RegimeClassifierGates(
    min_accuracy=0.60,        # NEW (binary threshold)
    min_macro_f1=0.55,
    max_brier=0.20,
    min_calm_recall=0.50,     # Raised
    min_reversal_recall=0.50, # Raised
    # REMOVED: min_impulse_recall
    max_ece=0.10,
    min_entropy=0.50,         # Adjusted for binary
    max_entropy=0.75,         # log(2) = 0.693
)

passed, reason = gates.validate(metrics)
```

### Impulse (EVENT-LEVEL)

```python
from impulse_gates import ImpulseGates

gates = ImpulseGates(
    min_freq_per_day=0.5,     # Too rare → useless
    max_freq_per_day=20.0,    # Too frequent → false pos
    min_avg_pnl=-0.001,       # Not correlated with losses
    max_cost_multiplier=2.0,  # Max 2x slippage
    max_drawdown_correlation=0.01,
)

passed, failures = gates.check_all(impulse_metrics, normal_metrics)
```

---

## 5) CHECKLIST IMMÉDIATE

### Phase 1 : Arrêt (MAINTENANT)
- [ ] **ARRÊTER tout training 3-classes**
- [ ] Marquer `production_regime.py` comme deprecated
- [ ] Documenter pourquoi (accuracy 46%, impulse recall 31%)

### Phase 2 : Régénération labels (URGENT)
- [ ] Regénérer labels BINAIRES depuis data raw
- [ ] Vérifier distribution (calm/reversal)
- [ ] Sauvegarder labels : `labels_binary_YYYYMMDD.parquet`

### Phase 3 : Re-entraînement (PRIORITÉ)
- [ ] Entraîner regime classifier BINAIRE
- [ ] Valider accuracy >60%
- [ ] Valider gates production
- [ ] Sauvegarder modèle : `regime_binary_v1.pkl`

### Phase 4 : Impulse features (CRITIQUE)
- [ ] Ajouter impulse_score aux features
- [ ] Valider frequency (1-10/day attendu)
- [ ] Tester impulse gates

### Phase 5 : Intégration (P0)
- [ ] Intégrer dans pipeline de trading
- [ ] Tester meta-control (downscale)
- [ ] Tester execution (MAKER/TAKER)

### Phase 6 : Validation (P0)
- [ ] Backtest complet 2019-2023
- [ ] Comparer metrics avant/après
- [ ] Paper trading 7j minimum

---

## 6) COMMANDES RAPIDES

### Test modules

```bash
cd ai/models/training/common
python3 test_integration.py
# Expected: 5/5 tests passed
```

### Demo pipeline

```bash
python3 pipeline_integration_example.py
# Observe: binary regimes + impulse events
```

### Test production gates

```bash
python3 production_gates.py
# Should show binary example passing
```

---

## 7) MÉTRIQUES ATTENDUES

### Avant (3-class) ❌
```
Accuracy:        46%
Impulse recall:  31%
ECE:             >0.10
```

### Après (binary + event) ✅
```
Accuracy:        >65%
Calm recall:     >60%
Reversal recall: >60%
ECE:             <0.10
Impulse freq:    1-10/day (event)
```

---

## 8) FICHIERS DE RÉFÉRENCE

| Action | Fichier |
|--------|---------|
| Entraîner régimes | [regime_classifier_v2.py](training/common/regime_classifier_v2.py) |
| Détecter impulse | [impulse_detector.py](training/common/impulse_detector.py) |
| Gates régimes | [production_gates.py](training/common/production_gates.py) |
| Gates impulse | [impulse_gates.py](training/common/impulse_gates.py) |
| Meta-control | [meta_control.py](training/common/meta_control.py) |
| Execution | [execution_engine.py](training/common/execution_engine.py) |
| Example complet | [pipeline_integration_example.py](training/common/pipeline_integration_example.py) |

---

## 9) SUPPORT

**Guide complet** : [MIGRATION_GUIDE.md](MIGRATION_GUIDE.md)
**Synthèse technique** : [IMPLEMENTATION_COMPLETE.md](IMPLEMENTATION_COMPLETE.md)
**Quick ref** : [README_NEW_ARCHITECTURE.md](README_NEW_ARCHITECTURE.md)

---

## 🚨 ACTION IMMÉDIATE

```bash
# 1. ARRÊTER tout training en cours
pkill -f train_regime

# 2. Tester nouveaux modules
cd ai/models/training/common
python3 test_integration.py

# 3. Générer labels binaires (À IMPLÉMENTER)
python generate_binary_labels.py --input data.parquet --output labels_binary.parquet

# 4. Re-entraîner (À IMPLÉMENTER)
python train_regime_binary.py --labels labels_binary.parquet --output regime_binary_v1.pkl
```

---

*Plan d'action créé le 2025-12-29*
*Architecture corrigée : régimes binaires + impulse event*
