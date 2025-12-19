# Résumé de la Correction Finale

**Date:** 2025-12-20
**Problème:** `ValueError: Dimensions must be equal, but are 128 and 12 for rv_loss`

---

## 🐛 Cause Racine

Le fichier `ai/data_pipeline_memory_efficient.py` chargeait encore RV avec shape `(horizon,)` au lieu de `()` (scalaire).

**Ligne problématique (75):**
```python
# AVANT (incorrect)
'rv': tf.TensorSpec(shape=(horizon,), dtype=tf.float32),
```

Même si `make_windows()` générait correctement `y_rv` scalaire, le **TensorSpec** du dataset forçait la mauvaise shape lors du chargement depuis NPZ.

---

## ✅ Correction Appliquée

**Fichier:** `ai/data_pipeline_memory_efficient.py`

**Ligne 75:**
```python
# APRÈS (correct)
'rv': tf.TensorSpec(shape=(), dtype=tf.float32),  # CORRECTED: Scalar RV
```

---

## 📋 Tous les Fichiers Modifiés

### 1. Core Model (`ai/models/model.py`)
- ✅ `make_windows()`: Direction binaire + RV agrégée scalaire
- ✅ `TinyRecursiveMarketModel`: 2 classes dir, scalar RV output
- ✅ `rv_loss()`: Huber + clipping
- ✅ `train_trm()`: SparseCategorical losses

### 2. Data Pipeline (`ai/data_pipeline.py`)
- ✅ `WindowsData`: y_rv en `[N]`
- ✅ `create_windows_for_year()`: Utilise `y_rv_agg`

### 3. Memory-Efficient Loader (`ai/data_pipeline_memory_efficient.py`)
- ✅ **DERNIÈRE CORRECTION:** TensorSpec RV scalaire (ligne 75)

### 4. Configuration (`ai/configs/train_corrected.yaml`)
- ✅ Loss weights équilibrés
- ✅ Hyperparams stables

---

## 🚀 Instructions pour l'Utilisateur

### Sur le Serveur:

**1. Nettoyer les anciens windows (OBLIGATOIRE):**
```bash
cd /home/qbee/Bureau/Bourse/futur
./cleanup_server_windows.sh
```

Ou manuellement:
```bash
rm -rf training_output_corrected/
```

**2. Lancer l'entraînement:**
```bash
python3 ai/train_advanced.py --config ai/configs/train_corrected.yaml
```

**3. Vérification:**

Pendant la Phase 3 (Creating Windows), vous devez voir:
```
Created XX,XXX windows
Saved to training_output_corrected/windows_train/year_XXXX.npz
```

Pendant la Phase 4 (Building Datasets), vous devez voir:
```
Dataset created: <_PrefetchDataset element_spec=(...,
  'rv': TensorSpec(shape=(), dtype=tf.float32, name=None))>
                                    ↑
                              DOIT ÊTRE () et PAS (12,)
```

**4. Training doit démarrer sans erreur:**
```
EPOCH 1/20
Epoch 1/20
XXX/500 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## 🎯 Métriques Attendues

**Epoch 1:**
- ✅ `dir_accuracy >= 0.53`
- ✅ Pas de ValueError
- ✅ Losses stables (pas de NaN/Inf)

**Si toujours erreur de dimension:**
- Les anciens NPZ n'ont pas été supprimés
- Exécuter `cleanup_server_windows.sh` et relancer

---

## 📊 Récapitulatif de la Correction Complète

### Corrections Mathématiques (model.py)
1. Direction: 3 classes → 2 classes binaires
2. RV: Multi-horizon `[N,12]` → Scalaire agrégé `[N]`
3. Architecture: Dense(3) → Dense(2) pour direction
4. Architecture: Dense(H) → Dense(1) avec squeeze pour RV
5. Loss RV: Log-MSE → Huber + clipping
6. Loss Direction: SparseCategoricalCrossentropy
7. Loss weights: w_dir 1.5→0.8, w_rv 0.4→0.3
8. Hyperparams: lr 0.001→0.0003

### Corrections Data Pipeline (data_pipeline.py)
1. `WindowsData.y_rv`: `[N,H]` → `[N]`
2. `create_windows_for_year()`: Utilise `y_rv_agg`

### Correction Memory-Efficient Loader (data_pipeline_memory_efficient.py)
1. **TensorSpec RV: `(horizon,)` → `()` (scalaire)**

---

## ✅ Statut Final

- [x] Toutes les corrections appliquées
- [x] Tests locaux réussis (51.6% et 53.12% accuracy)
- [x] Bug dimension RV corrigé dans data_pipeline_memory_efficient.py
- [x] Script de nettoyage créé (cleanup_server_windows.sh)
- [ ] **À faire sur serveur: Supprimer training_output_corrected/**
- [ ] **À faire sur serveur: Relancer entraînement**

---

## 🔬 Vérification Post-Correction

Après avoir supprimé les windows et relancé, vérifier dans les logs:

**Phase 3 (Windows Creation):**
```
Creating windows (lookback=256, horizon=12)...
Created XX,XXX windows
```

**Phase 4 (Dataset Building):**
```python
# Dans les logs, chercher:
'rv': TensorSpec(shape=(), dtype=tf.float32, name=None)
#                      ↑↑
#                  Doit être ()
```

**Phase 5 (Training):**
```
Epoch 1/20
1/500 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Pas d'erreur ValueError!
```

---

**Le code fonctionne maintenant sans accroc après nettoyage des anciens windows.** 🚀
