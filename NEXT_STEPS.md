# Prochaines Étapes - Fix du Modèle

## 🎯 Problème Identifié

Votre modèle donnait **5.19% direction accuracy** (catastrophique).

**Cause:** Configuration d'entraînement instable, PAS un problème de données.

✅ Labels corrects
✅ Classes équilibrées (46% DOWN, 47% UP)
✅ Pas de temporal leakage
❌ Modèle trop petit + lr trop élevé + over-régularisation

---

## 📋 Solution: Entraînement en 2 Phases

### Phase 1: Returns Only (MAINTENANT)

**Objectif:** Vérifier que le modèle apprend correctement le signal temporel

**Config:** `ai/configs/train_returns_only.yaml`

**Changements:**
- ✅ Capacité du modèle **restaurée** (d_model=128, n_heads=4)
- ✅ Learning rate **réduit** (0.0003 au lieu de 0.001)
- ✅ Régularisation **réduite** (weight_decay=0.0001)
- ✅ Direction **désactivée** (w_dir=0.0) temporairement

**Lancement:**
```bash
cd /Users/christopher/Desktop/futur
./ai/quick_start_returns.sh
```

**Métriques attendues (après 5 epochs):**
- ✅ `ret_mae` validation < 0.01
- ✅ `loss` train ≈ loss val (pas d'overfitting)
- ✅ Sharpe Ratio > 0.5

**Si succès:**
→ Passer à Phase 2

**Si échec:**
→ Problème plus profond à investiguer

---

### Phase 2: Réactiver Direction (SI PHASE 1 OK)

**Config:** `ai/configs/train_with_direction.yaml` (à créer)

**Changements:**
```yaml
loss_weights:
  w_ret: 1.0
  w_dir: 0.5    # Réactivé graduellement (au lieu de 1.5)
  w_rv: 0.0
```

**Ajouter class weights** pour compenser FLAT minority:
```python
class_weight = {
    0: 1.0,   # DOWN (46%)
    1: 7.0,   # FLAT (7%) - poids augmenté
    2: 1.0    # UP (47%)
}
```

**Métriques attendues:**
- ✅ `dir_accuracy` validation > 40%
- ✅ `ret_mae` reste stable
- ✅ Pas d'overfitting

---

## 🚀 Commandes Rapides

### Lancer Phase 1
```bash
./ai/quick_start_returns.sh
```

### Monitorer TensorBoard
```bash
tensorboard --logdir=training_output_returns_only/tensorboard/ --port=6006
# Ouvrir: http://localhost:6006
```

### Vérifier métriques
```bash
# Pendant l'entraînement
tail -f training_output_returns_only/logs/train_*.log

# Après l'entraînement
cat training_output_returns_only/metrics/final_metrics.json
```

---

## 📊 Fichiers Créés

### Diagnostic
- [`DIAGNOSTIC_REPORT.md`](DIAGNOSTIC_REPORT.md) - Rapport complet des tests

### Configuration
- [`ai/configs/train_returns_only.yaml`](ai/configs/train_returns_only.yaml) - Config Phase 1

### Scripts
- [`ai/quick_start_returns.sh`](ai/quick_start_returns.sh) - Lancement rapide
- [`ai/test_direction_labels.py`](ai/test_direction_labels.py) - Tests validation labels
- [`ai/analyze_s3_direction_distribution.py`](ai/analyze_s3_direction_distribution.py) - Analyse distribution
- [`ai/check_temporal_leakage.py`](ai/check_temporal_leakage.py) - Check temporal leakage

---

## 📈 Interprétation des Résultats

### ✅ Succès Phase 1 (Returns)

Si après 10 epochs:
- `val_ret_mae` < 0.01
- `val_loss` stable (pas de divergence)
- Sharpe Ratio > 0.5

→ **Le modèle apprend correctement!**
→ Passer à Phase 2 (réactiver direction)

### ❌ Échec Phase 1 (Returns)

Si après 10 epochs:
- `val_ret_mae` stagne > 0.02
- `val_loss` > `train_loss` (overfitting)
- Sharpe Ratio < 0

→ **Problème plus profond:**
- Vérifier features (temporal leakage?)
- Vérifier données (qualité S3?)
- Vérifier modèle (architecture?)

---

## 🔍 Debugging

Si problèmes pendant l'entraînement:

### OOM (Out of Memory)
```yaml
# Dans train_returns_only.yaml, réduire:
batch_size: 64        # au lieu de 128
shuffle_buffer: 5000  # au lieu de 10000
```

### Loss = NaN
```yaml
# Réduire learning rate encore plus:
lr: 0.0001  # au lieu de 0.0003
```

### Overfitting immédiat
```yaml
# Augmenter dropout:
dropout: 0.20  # au lieu de 0.15
```

---

## 📞 Questions Fréquentes

**Q: Pourquoi désactiver direction?**
A: Pour isoler le problème. Si returns marche mais pas direction, on sait que c'est un problème de classification, pas de feature engineering.

**Q: Combien de temps Phase 1?**
A: ~4-6 heures pour 20 epochs (CPU), ~1-2h (GPU si CUDA marche)

**Q: Que faire si Phase 1 échoue?**
A: Analyser les logs, vérifier data quality, peut-être le signal est trop bruité pour 1-minute data.

**Q: Peut-on skip Phase 1?**
A: Non recommandé. Returns est plus simple que direction. Si ça marche pas sur returns, ça marchera pas sur direction.

---

## ✅ Checklist

Avant de lancer:
- [ ] Lire [DIAGNOSTIC_REPORT.md](DIAGNOSTIC_REPORT.md)
- [ ] Vérifier config: `cat ai/configs/train_returns_only.yaml`
- [ ] Nettoyer cache: `rm -rf training_output/`
- [ ] Lancer: `./ai/quick_start_returns.sh`
- [ ] Ouvrir TensorBoard pendant l'entraînement
- [ ] Surveiller `ret_mae` et `loss`

Après 5 epochs:
- [ ] `ret_mae` diminue? → Continue
- [ ] `loss` explose? → Stop, debug
- [ ] `overfitting`? → Augmenter dropout

Après 20 epochs:
- [ ] Check `final_metrics.json`
- [ ] Si succès → Préparer Phase 2
- [ ] Si échec → Analyser logs détaillés

---

## 🎓 Apprentissage

Ce diagnostic a montré que:

1. **Debugging méthodique** > tâtonner avec hyperparams
2. **Tester labels** AVANT d'entraîner
3. **Vérifier data quality** (class balance, leakage)
4. **Simplifier d'abord** (returns only) puis complexifier (direction)
5. **Configuration stable** > "optimisations" agressives

**Règle d'or:** Si accuracy < random → Bug dans data/config, PAS dans le modèle.

---

Bonne chance! 🚀
