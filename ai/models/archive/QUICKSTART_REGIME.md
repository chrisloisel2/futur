# Quickstart - Architecture à Détection de Régimes

**Temps total : 20 minutes** pour valider et comprendre l'architecture.

---

## Étape 1 : Vérification (5 min) ✓

### Tests Unitaires

```bash
cd /Users/christopher/Desktop/futur/ai/models
python test_regime_model.py
```

**Résultat attendu :**
```
================================================================================
TEST SUMMARY
================================================================================
Passed: 8/8
Failed: 0/8

🎉 ALL TESTS PASSED 🎉
```

### Si les tests échouent

| Erreur | Solution |
|--------|----------|
| `ModuleNotFoundError: No module named 'tensorflow'` | `pip install tensorflow` |
| `ModuleNotFoundError: No module named 'numpy'` | `pip install numpy` |
| Tests timeout | Réduire `n_samples` dans le test |

---

## Étape 2 : Démonstration (10 min) 🎬

### Lancer l'exemple complet

```bash
python example_regime_usage.py
```

**Ce script fait :**
1. Génère 20k samples de données synthétiques avec patterns de régimes
2. Entraîne le modèle (10 epochs)
3. Évalue la performance par régime
4. Démontre l'inférence sur un sample

**Sortie attendue :**

```
================================================================================
STEP 1: GENERATING SYNTHETIC MARKET DATA
================================================================================
Generating 25256 timesteps with 44 features...
Creating windows (lookback=256, horizon=12)...
Computing regime labels...

Regime distribution:
  regime_0_TREND_pct: 24.82
  regime_1_MEAN_REVERT_pct: 19.45
  regime_2_HIGH_VOL_pct: 18.23
  ...

================================================================================
STEP 2: TRAINING REGIME-AWARE MODEL
================================================================================
Creating model...
  Total parameters: 338,949
  Classifier params: 52,421
  Expert params (×5): 25,092 each

Training...
Epoch 1/10
  Train Loss: 0.0234 | Regime Acc: 45.23% | Val Loss: 0.0198
  ✓ Best val loss
...

================================================================================
STEP 3: EVALUATING MODEL
================================================================================
Per-Regime Performance:

TREND:
  Samples: 623
  Return MAE: 0.0112
  Volatility MAE: 0.0089
  Directional Accuracy: 56.34%
  ✓ Beats random (> 50%)

...

================================================================================
STEP 4: INFERENCE EXAMPLE
================================================================================
Predictions:
  Current Regime: TREND (confidence: 67.82%)
  Cumulative Return (12 steps): 0.0234
  Predicted Volatility: 0.0156

Regime Probabilities:
  TREND           67.82% ██████████████████████████████████
  MEAN_REVERT     12.45% ██████
  HIGH_VOL        08.91% ████
  LOW_VOL         06.12% ███
  RANGE           04.70% ██

Example Trading Logic:
  → LONG SIGNAL (uptrend detected with positive forecast)
```

**Temps d'exécution :** ~8-12 minutes (dépend CPU/GPU)

---

## Étape 3 : Comprendre (5 min) 📖

### Lire le README

```bash
# Ouvrir dans votre éditeur préféré
open REGIME_MODEL_README.md
# ou
cat REGIME_MODEL_README.md | less
```

**Sections clés à lire (15 min) :**
1. Vue d'Ensemble (5 min)
2. Architecture (5 min)
3. Quickstart (5 min)

### Concepts Essentiels

**Les 5 régimes :**

| Régime | Description | Exemple |
|--------|-------------|---------|
| **TREND** | Tendance forte | BTC monte de 10% en 3 jours |
| **MEAN_REVERT** | Retour à moyenne | RSI > 70, prix revient |
| **HIGH_VOL** | Volatilité élevée | Mouvements > 5% intraday |
| **LOW_VOL** | Volatilité faible | Mouvements < 0.5% intraday |
| **RANGE** | Consolidation | Prix oscille ±2% autour EMA |

**Architecture en 2 modules :**

```
Input Features → RegimeClassifier → p_regime [5]
                       ↓
                 5 Experts (un par régime)
                       ↓
                 Gating (soft/hard)
                       ↓
                 Output: {ret, rv}
```

**Pourquoi ça marche :**
- Un modèle global apprend une **moyenne** de distributions incompatibles
- Chaque expert se **spécialise** dans un régime homogène
- Variance conditionnelle **réduite** → meilleure prédiction

---

## Étape 4 : Production (si prêt) 🚀

### Prérequis

1. **Données S3 :**
   ```bash
   export S3_BUCKET="your-bucket"
   export S3_PREFIX="btc/1m/"
   export AWS_PROFILE="default"  # optionnel
   ```

2. **Vérifier accès S3 :**
   ```bash
   aws s3 ls s3://$S3_BUCKET/$S3_PREFIX --profile $AWS_PROFILE | head
   ```

### Lancer Pipeline Complet

```bash
python regime_pipeline.py
```

**Durée estimée :** 2-4h pour 10M+ timesteps

**Outputs produits :**
```
regime_out/
├── final_model.keras              # Modèle complet
├── best_weights.h5                # Meilleurs poids (early stopping)
├── evaluation_results.json        # Métriques par régime
└── regime_statistics.json         # Distribution des régimes
```

### Valider le Modèle

**Critères de succès :**

```bash
# Lire les résultats
cat regime_out/evaluation_results.json | python -m json.tool
```

Vérifier :
- [ ] `regime_classification_acc > 0.60`
- [ ] Tous les régimes ont `directional_acc > 0.50`
- [ ] `switching_rate` entre 50-200

---

## Prochaines Étapes

### Court Terme (Aujourd'hui)

- [x] ✓ Tests passent
- [x] ✓ Exemple tourne
- [ ] Lire [REGIME_MODEL_README.md](REGIME_MODEL_README.md) complet
- [ ] Explorer [regime_aware_model.py](regime_aware_model.py)

### Moyen Terme (Cette Semaine)

- [ ] Entraîner sur données réelles (S3)
- [ ] Valider critères de succès
- [ ] Backtest sur 3 mois out-of-sample
- [ ] Tuner hyperparamètres si nécessaire

### Long Terme (Ce Mois)

- [ ] Comparer avec modèle existant ([model.py](model.py))
- [ ] Ensemble des deux modèles ?
- [ ] Deploy en production
- [ ] Monitoring + alerting

---

## Aide Rapide

### Problèmes Courants

| Problème | Solution |
|----------|----------|
| Tests timeout | Réduire `n_samples` dans les tests |
| `regime_acc` stagne à 20% | Augmenter `regime_d_model` dans config |
| `entropy` trop bas | Augmenter `entropy_weight` |
| Experts ne battent pas hasard | Vérifier labels de régimes (inspect samples) |
| OOM (Out of Memory) | Réduire `batch_size` |

### Où Trouver Plus d'Infos ?

| Question | Fichier |
|----------|---------|
| **Comment ça marche ?** | [REGIME_MODEL_README.md](REGIME_MODEL_README.md) |
| **Pourquoi cette approche ?** | [REGIME_ARCHITECTURE_GUIDE.md](REGIME_ARCHITECTURE_GUIDE.md) |
| **Hyperparamètres ?** | [REGIME_MODEL_README.md](REGIME_MODEL_README.md) § Configuration |
| **Debugging ?** | [REGIME_MODEL_README.md](REGIME_MODEL_README.md) § Troubleshooting |
| **Index complet ?** | [REGIME_INDEX.md](REGIME_INDEX.md) |

---

## Commandes Utiles

```bash
# Tests unitaires
python test_regime_model.py

# Exemple démo
python example_regime_usage.py

# Production (avec S3)
python regime_pipeline.py

# Voir paramètres du modèle
python -c "from regime_aware_model import RegimeConfig; print(RegimeConfig())"

# Compter lignes de code
wc -l regime_*.py test_*.py example_*.py
```

---

## Résumé en 30 Secondes

**Ce que vous avez :**
- Architecture à 5 régimes (trend, mean_revert, high_vol, low_vol, range)
- Mixture of Experts (5 experts spécialisés)
- Pipeline complet (S3 → entraînement → évaluation)
- Tests + Documentation + Exemple

**Ce que vous devez faire :**
1. `python test_regime_model.py` → vérifier tests
2. `python example_regime_usage.py` → voir démo
3. Lire [REGIME_MODEL_README.md](REGIME_MODEL_README.md)

**Prêt pour production :**
- Configurer S3
- `python regime_pipeline.py`
- Valider critères de succès

---

**Questions ?** Consulter [REGIME_INDEX.md](REGIME_INDEX.md) pour navigation complète.

**Bonne exploration ! 🚀**
