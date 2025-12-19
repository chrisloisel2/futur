# 🚀 Checklist de Déploiement Serveur

## ✅ Statut Local
- [x] Toutes les corrections appliquées
- [x] Tests locaux réussis (51.6% et 53.12% accuracy)
- [x] Pipeline complet fonctionnel

---

## 📋 Étapes sur le Serveur

### 1. Nettoyage des Anciens Windows (OBLIGATOIRE!)

```bash
cd /Users/christopher/Desktop/futur

# Supprimer TOUS les anciens outputs
rm -rf training_output/
rm -rf training_output_returns_only/
rm -rf training_output_corrected/

# Vérifier
ls -la | grep training_output
# (Doit être vide)
```

**Pourquoi?** Les anciens NPZ ont `y_rv: [N, 12]`, mais le modèle attend `y_rv: [N]`.

---

### 2. Vérifier les Fichiers Modifiés

```bash
# Vérifier que les corrections sont présentes
grep -n "y_rv_agg = np.zeros((N,)" ai/models/model.py
# Doit montrer la ligne avec [N] et pas [N, horizon]

grep -n "Dense(2)" ai/models/model.py | grep "dir_head"
# Doit montrer Dense(2) pour direction binaire

grep -n "y_rv_agg" ai/data_pipeline.py
# Doit montrer l'utilisation de y_rv_agg
```

---

### 3. Lancer l'Entraînement

```bash
# Option 1: Script automatique (recommandé)
chmod +x ai/launch_corrected_training.sh
./ai/launch_corrected_training.sh

# Option 2: Manuel
python3 ai/train_advanced.py --config ai/configs/train_corrected.yaml
```

---

### 4. Monitoring en Temps Réel

**Terminal 1 (Training):**
```bash
./ai/launch_corrected_training.sh
```

**Terminal 2 (TensorBoard):**
```bash
tensorboard --logdir=training_output_corrected/tensorboard/ --port=6006
```

Ouvrir: [http://localhost:6006](http://localhost:6006)

**Terminal 3 (Logs):**
```bash
tail -f training_output_corrected/logs/train_advanced.log
```

---

### 5. Vérifications Epoch 1

**Attendu après Epoch 1:**

```
Epoch 1/20
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ XXXX/XXXX

✅ dir_accuracy >= 0.53 (50% + marge statistique)
✅ val_loss ≈ train_loss (pas d'overfitting massif)
✅ ret_mae < 0.02
✅ rv_mae < 0.01
✅ Pas de NaN/Inf dans les losses
```

**Si dir_accuracy < 0.50:** ARRÊTER et vérifier les données

**Si 0.50 <= dir_accuracy < 0.53:** Observer epoch 2-3 (peut converger)

**Si dir_accuracy >= 0.53:** ✅ Le modèle apprend correctement!

---

### 6. Vérifications Epoch 5

**Attendu:**

```
✅ dir_accuracy >= 0.55 (progression)
✅ val_loss décroît progressivement
✅ ret_mae < 0.015
✅ Stable (pas de divergence)
```

---

### 7. Cas d'Erreurs

#### Erreur: "Dimensions must be equal, but are 128 and 12"

**Cause:** Anciens windows pas supprimés

**Solution:**
```bash
rm -rf training_output*
# Relancer l'entraînement
```

#### Erreur: "ValueError: Arguments target and output must have the same rank"

**Cause:** Mauvaise loss function pour direction

**Solution:**
```python
# Dans model.py, vérifier:
losses = {
    "dir": tf.keras.losses.SparseCategoricalCrossentropy(),  # Pas BinaryCrossentropy!
}
```

#### NaN/Inf dans les losses

**Cause:** Learning rate trop élevé ou batch trop petit

**Solution:**
```yaml
# Dans train_corrected.yaml
training:
  lr: 0.0001  # Réduire de 0.0003 à 0.0001
  batch_size: 256  # Augmenter si possible
```

#### Direction accuracy stagnante à ~50%

**Causes possibles:**
1. Données S3 corrompues → Vérifier avec `validate_crypto_data.py`
2. Features mal calculées → Vérifier `compute_features()`
3. Scaler mal fitted → Régénérer scaler

**Debug:**
```bash
# Test avec données synthétiques
python3 ai/quick_pipeline_test.py
# Doit donner >= 50%

# Si OK → problème dans les données S3
# Si KO → problème dans le code
```

---

## 📊 Dashboard TensorBoard - Ce qu'il faut surveiller

### Scalars à Observer

1. **Losses (Global)**
   - `loss` (train) doit décroître
   - `val_loss` doit suivre (pas diverger)
   - Ratio `val_loss / loss` doit rester entre 0.9 et 1.3

2. **Direction**
   - `dir_acc` (train) doit monter progressivement
   - `val_dir_acc` >= 0.53 dès epoch 1
   - Écart train/val < 5% (sinon overfitting)

3. **Returns**
   - `ret_mae` doit décroître
   - Target: < 0.015 après 5 epochs

4. **Volatility**
   - `rv_mae` doit être stable et < 0.01
   - Pas de spikes

### Distributions

- **Weights:** Pas de saturation (tout à 0 ou 1)
- **Gradients:** Pas d'explosion (> 100) ou vanishing (< 1e-6)

---

## 🎯 Critères de Succès

### Minimum Acceptable (Epoch 1)

```
✅ Training complète sans crash
✅ dir_accuracy >= 0.53
✅ Losses finies (pas NaN/Inf)
```

### Objectif (Epoch 5)

```
✅ dir_accuracy >= 0.55
✅ ret_mae < 0.015
✅ val_loss décroît
✅ Sharpe Ratio > 0.5 (si métrique disponible)
```

### Excellent (Epoch 20)

```
✅ dir_accuracy >= 0.58
✅ ret_mae < 0.012
✅ Sharpe Ratio > 1.0
✅ Max Drawdown < 15%
```

---

## 🛑 Critères d'Arrêt Immédiat

### STOP si:

1. **dir_accuracy < 0.48 après 3 epochs**
   - Le modèle n'apprend rien
   - Vérifier données et features

2. **NaN/Inf dans les losses**
   - Réduire learning rate
   - Vérifier data quality

3. **val_loss > 2 × train_loss**
   - Overfitting massif
   - Augmenter dropout ou réduire model capacity

4. **OOM (Out of Memory)**
   - Réduire batch_size
   - Réduire d_model ou n_heads

---

## 📁 Outputs Générés

Après entraînement complet:

```
training_output_corrected/
├── checkpoints/
│   ├── best_val_loss.keras      # Meilleur modèle (val_loss)
│   ├── best_sharpe.keras        # Meilleur Sharpe (si disponible)
│   └── epoch_020.keras          # Dernier epoch
├── tensorboard/
│   └── events.out.tfevents...   # Logs TensorBoard
├── metrics/
│   ├── training_metrics.csv     # Métriques epoch-by-epoch
│   └── final_metrics.json       # Tous les KPIs finaux
└── logs/
    └── train_advanced.log       # Logs texte
```

---

## 📞 Checklist Finale

Avant de lancer:

- [ ] `rm -rf training_output*` exécuté
- [ ] Fichiers modifiés vérifiés (model.py, data_pipeline.py)
- [ ] Config train_corrected.yaml présente
- [ ] TensorBoard prêt sur port 6006

Pendant training:

- [ ] Epoch 1: dir_acc >= 0.53 ✅
- [ ] Epoch 3: dir_acc >= 0.54 ✅
- [ ] Epoch 5: val_loss décroît ✅
- [ ] Pas de NaN/Inf ✅

Après training:

- [ ] Meilleur modèle sauvegardé
- [ ] Métriques exportées (CSV + JSON)
- [ ] Logs archivés
- [ ] TensorBoard accessible pour analyse

---

## 🚀 Commande Rapide

```bash
# All-in-one
cd /Users/christopher/Desktop/futur && \
rm -rf training_output* && \
./ai/launch_corrected_training.sh

# Dans un autre terminal
tensorboard --logdir=training_output_corrected/tensorboard/ --port=6006
```

---

**Bon entraînement!** 🎉
