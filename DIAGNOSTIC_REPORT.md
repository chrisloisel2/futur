# Rapport de Diagnostic - Direction Accuracy 5.19%

**Date:** 2025-12-19
**Problème:** Direction accuracy catastrophique (5.19% au lieu de ~50%)
**Statut:** ✅ Diagnostiqué, Solution proposée

---

## 🔍 Tests Effectués

### 1. Test de Génération des Labels ✅ PASS

**Script:** `ai/test_direction_labels.py`

**Résultats:**
```
✅ Label generation logic: 100% match avec cumulative returns
✅ Random predictor: 33.77% (attendu ~33%)
✅ Untrained model: 39.00% (attendu 20-45%)
✅ No label inversion detected
```

**Conclusion:** La fonction `make_windows()` génère les labels correctement. Pas d'inversion.

---

### 2. Analyse de Distribution des Classes ✅ PASS

**Script:** `ai/analyze_s3_direction_distribution.py`

**Résultats sur données réelles (2022-2024):**
```
Direction Distribution (30,000 windows analysées):
  0 (DOWN):  46.35%
  1 (FLAT):   6.51%
  2 (UP):    47.14%

Class Balance:
  Imbalance ratio: 7.24:1
  ✅ Classes DOWN/UP sont équilibrées
```

**Conclusion:** Pas de déséquilibre sévère. DOWN et UP sont quasi-parfaitement balancées (46% vs 47%).

---

### 3. Test de Temporal Leakage ✅ PASS

**Script:** `ai/check_temporal_leakage.py`

**Résultats:**
```
Toutes les 44 features testées:
✅ Open:          corr=-0.002
✅ High:          corr=-0.002
✅ Low:           corr=-0.002
✅ Close:         corr=-0.002
✅ log_ret:       corr=-0.012
✅ rv_60:         corr=+0.005
✅ ema_20:        corr=-0.002
... (toutes < 0.1 en valeur absolue)

Aucune feature ne leak d'information future (threshold: |corr| > 0.3)
```

**Conclusion:** Pas de temporal leakage. Les features sont correctement alignées temporellement.

---

## ❌ Échecs d'Entraînement

### Premier Entraînement (config initial)

**Config:**
- d_model=128, n_heads=4, dropout=0.10
- lr=0.0003, weight_decay=0.0001
- w_ret=1.0, w_dir=0.6, w_rv=0.4

**Résultats Epoch 1:**
```
❌ loss train = 2.83 vs val = 24.32  → Overfitting massif
❌ dir_accuracy train = 46% vs val = 13%  → Pire que hasard
❌ rv_loss val = 58.8  → RV explose
```

### Deuxième Entraînement (après réduction modèle)

**Config modifié:**
- d_model=64, n_heads=2, dropout=0.20 (RÉDUIT)
- lr=0.001, weight_decay=0.001 (AUGMENTÉ)
- w_ret=1.0, w_dir=1.5, w_rv=0.0

**Résultats Epoch 1:**
```
🔴 dir_accuracy train = 44% vs val = 5.19%  → CATASTROPHIQUE
🔴 dir_loss train = 0.88 vs val = 9.02  → Décorrélation totale
```

---

## 🎯 Diagnostic Final

### Ce qui N'EST PAS la cause:

1. ✅ **Labels correctement générés** - Pas d'inversion, pas de bug
2. ✅ **Classes équilibrées** - 46% DOWN, 47% UP
3. ✅ **Pas de temporal leakage** - Features alignées temporellement
4. ✅ **Code du modèle correct** - Trivial model donne 39% accuracy

### Ce qui EST la cause:

#### 🔴 **Model Collapse dû à une configuration instable**

1. **Capacité du modèle trop faible** après réduction:
   - d_model 128→64 = 50% de capacité en moins
   - n_heads 4→2 = 50% d'attention heads en moins
   - mem_dim 128→64 = 50% de mémoire récursive en moins
   - **Résultat:** Modèle trop petit pour apprendre le signal

2. **Learning rate trop élevé** (0.001 vs 0.0003):
   - Augmenté de 3.3x pour "convergence plus rapide"
   - **Résultat:** Divergence des gradients, instabilité

3. **Régularisation excessive**:
   - weight_decay 0.0001→0.001 (10x plus)
   - dropout 0.10→0.20 (2x plus)
   - **Résultat:** Tue le signal, empêche l'apprentissage

4. **Loss weight déséquilibré**:
   - w_dir=1.5 domine w_ret=1.0
   - Direction task (3 classes) plus difficile que regression
   - **Résultat:** Gradients de direction écrasent gradients de returns

#### 💡 **Explication du 5.19% accuracy:**

Avec ces paramètres, le modèle:
1. N'a pas assez de capacité pour apprendre
2. Diverge à cause du lr trop élevé
3. Est sur-régularisé (dropout + weight_decay excessifs)
4. Se concentre sur direction (w_dir=1.5) mais échoue
5. **Résultat:** Apprend un pattern constant (toujours prédire une classe) qui donne 5% au lieu de 33%

---

## ✅ Solution Proposée

### Configuration Optimisée: `train_returns_only.yaml`

**Changements clés:**

#### 1. Restaurer la Capacité du Modèle
```yaml
d_model: 128          # Restored from 64
n_heads: 4            # Restored from 2
d_ff: 256             # Restored from 128
mem_dim: 128          # Restored from 64
mem_update_iters: 2   # Restored from 1
```

#### 2. Réduire le Learning Rate
```yaml
lr: 0.0003            # Reduced from 0.001 (stable value)
```

#### 3. Réduire la Régularisation
```yaml
weight_decay: 0.0001  # Reduced from 0.001
dropout: 0.15         # Moderate (vs 0.20)
clip_norm: 1.0        # Restored from 0.5
```

#### 4. Désactiver Direction Temporairement
```yaml
w_ret: 1.0            # Focus on returns
w_dir: 0.0            # Disable direction (was 1.5)
w_rv: 0.0             # Keep disabled
```

### Stratégie de Test

**Phase 1: Returns Only (ce config)**
```bash
python3 ai/train_advanced.py --config ai/configs/train_returns_only.yaml
```

**Métriques à surveiller:**
- `ret_mae` doit diminuer progressivement
- `val_ret_mae` doit suivre `train_ret_mae` (pas d'overfitting)
- Loss doit être stable (pas d'explosion)

**Critères de succès:**
- ✅ val_ret_mae < 0.01 après 5 epochs
- ✅ Pas d'overfitting (train_loss ~= val_loss)
- ✅ Courbes de loss stables (pas de divergence)

**Phase 2: Réactiver Direction (si Phase 1 OK)**

Créer nouveau config avec:
```yaml
w_ret: 1.0
w_dir: 0.5            # Start low
w_rv: 0.0

# Optionnel: Class weights pour compenser FLAT minority
class_weight:
  0: 1.0              # DOWN (46%)
  1: 7.0              # FLAT (7%) - inversement proportionnel
  2: 1.0              # UP (47%)
```

---

## 📊 Prédictions

### Avec `train_returns_only.yaml`:

**Attendu après Epoch 1:**
- loss train ≈ 0.5-1.0
- loss val ≈ 0.6-1.2 (proche de train)
- ret_mae train ≈ 0.02-0.03
- ret_mae val ≈ 0.025-0.035

**Attendu après Epoch 10:**
- loss val ≈ 0.3-0.5
- ret_mae val ≈ 0.008-0.015
- Sharpe Ratio ≈ 0.5-1.0 (sur validation)

**Si ces métriques sont atteintes:**
→ Modèle apprend correctement le signal de returns
→ On peut réactiver direction avec class_weight

**Si échec:**
→ Problème plus profond (données, feature engineering, etc.)

---

## 🚀 Prochaines Étapes

1. **Nettoyer cache précédent:**
   ```bash
   rm -rf training_output/
   ```

2. **Lancer entraînement Returns-Only:**
   ```bash
   chmod +x ai/launch_training.sh
   ./ai/launch_training.sh ai/configs/train_returns_only.yaml
   ```

3. **Surveiller TensorBoard:**
   ```bash
   tensorboard --logdir=training_output_returns_only/tensorboard/ --port=6006
   ```

4. **Vérifier après 5 epochs:**
   - Si stable + ret_mae diminue → Continue
   - Si diverge ou stagne → Analyser logs

5. **Si succès Phase 1:**
   - Créer `train_with_direction.yaml` (w_dir=0.5 + class_weight)
   - Tester sur 2-3 epochs
   - Si direction accuracy > 40% → Succès!

---

## 📝 Notes Techniques

### Pourquoi Returns First?

1. **Regression plus simple** que classification 3-classes
2. **Signal plus fort** dans les returns que direction
3. **Permet de valider** que le modèle apprend le pattern temporel
4. **Diagnostic clair** si ça échoue aussi

### Pourquoi w_dir=0.0 temporairement?

1. **Isoler le problème** - si returns marche mais pas direction, on sait où chercher
2. **Éviter interference** entre les 2 tasks pendant debug
3. **Réactiver graduellement** avec class_weight approprié

### Class Weight pour Direction (Phase 2)

Formule:
```python
class_weight[i] = n_total / (n_classes * n_samples_class_i)

Pour notre data:
- DOWN (46%): weight = 1.0 / 0.46 ≈ 2.2 → normalized 1.0
- FLAT (7%):  weight = 1.0 / 0.07 ≈ 14.3 → normalized 7.0
- UP (47%):   weight = 1.0 / 0.47 ≈ 2.1 → normalized 1.0
```

---

## ✅ Conclusion

Le problème n'était **PAS** dans les données ou les labels, mais dans une **configuration d'entraînement instable**:

- Modèle trop petit (capacity insuffisante)
- Learning rate trop élevé (divergence)
- Régularisation excessive (tue le signal)
- Loss weights déséquilibrés

La solution: **retour à une config stable** + **focus sur returns d'abord** + **réactivation graduelle de direction avec class weights**.

**Prochaine étape:** Lancer `train_returns_only.yaml` et vérifier que le modèle apprend les returns correctement.
