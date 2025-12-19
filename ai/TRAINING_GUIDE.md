# Guide d'Entraînement Avancé - TinyRecursiveMarketModel

## Vue d'Ensemble

Ce système d'entraînement charge progressivement les données de trading depuis S3 (année par année) et entraîne le modèle TinyRecursiveMarketModel avec tracking de **30+ KPIs** en temps réel.

### Caractéristiques

✅ **Chargement streaming** année par année (optimisé pour RAM limitée 8-16 GB)
✅ **30+ KPIs** incluant Sharpe Ratio, Sortino, Max Drawdown, etc.
✅ **TensorBoard** pour monitoring temps réel
✅ **CSV exports** pour analyse post-training
✅ **Visualisations** automatiques des prédictions
✅ **Memory monitoring** (RAM + GPU)
✅ **Checkpointing** automatique des meilleurs modèles

---

## Installation

### 1. Prérequis

- Python 3.9+
- GPU NVIDIA avec CUDA (recommandé)
- AWS credentials configurées pour accès S3
- 8-16 GB RAM minimum

### 2. Installation des dépendances

```bash
cd /Users/christopher/Desktop/futur/ai
pip install -r requirements_training.txt
```

### 3. Vérification de l'installation

```python
python3 -c "import tensorflow as tf; print(f'TensorFlow: {tf.__version__}'); print(f'GPUs: {tf.config.list_physical_devices(\"GPU\")}')"
```

---

## Structure du Projet

```
ai/
├── models/
│   └── model.py                    # TinyRecursiveMarketModel (NE PAS MODIFIER)
├── s3_parquet_loader.py            # Chargement données S3
├── data_pipeline.py                # Pipeline streaming optimisé
├── advanced_metrics.py             # 30+ KPIs
├── training_callbacks.py           # Callbacks customs
├── train_advanced.py               # Script principal ⭐
├── configs/
│   └── train_advanced.yaml         # Configuration
├── requirements_training.txt       # Dépendances
├── launch_training.sh              # Script de lancement
└── training_output/                # Outputs (créé automatiquement)
    ├── scaler.pkl
    ├── windows_train/
    ├── windows_test/
    ├── checkpoints/
    ├── tensorboard/
    ├── metrics/
    └── logs/
```

---

## Configuration

### Fichier: `ai/configs/train_advanced.yaml`

```yaml
data:
  bucket: "qbia"
  years_train: [2017, 2018, 2019, 2020, 2021, 2022, 2023]
  years_test: [2024]

model:
  lookback: 256      # 4+ heures de contexte
  horizon: 12        # Prédire 12 minutes ahead
  d_model: 128       # Tiny model pour expérimentation rapide

training:
  batch_size: 256    # Réduire à 128 si OOM
  epochs: 20
  steps_per_epoch: 2000
  lr: 0.0003
```

**Paramètres ajustables:**

- `batch_size`: Réduire si "Out of Memory"
- `steps_per_epoch`: Augmenter si vous avez beaucoup de données
- `epochs`: Nombre d'epochs d'entraînement
- `years_train`: Années à utiliser pour l'entraînement

---

## Lancement de l'Entraînement

### Méthode 1: Script automatique (recommandé)

```bash
cd /Users/christopher/Desktop/futur
./ai/launch_training.sh
```

### Méthode 2: Python direct

```bash
cd /Users/christopher/Desktop/futur
python3 ai/train_advanced.py --config ai/configs/train_advanced.yaml
```

---

## Phases d'Entraînement

### Phase 1: Fitting du Scaler (~10-20 min)

Charge chaque année et collecte des statistiques pour normalisation robuste.

```
Phase 1/4: Fitting scaler on year 2017...
  Loading 525,600 rows...
  Updating scaler...
```

**Output:** `training_output/scaler.pkl`

### Phase 2: Création des Windows (~30-60 min)

Compute features techniques (EMAs, RSI, ATR, VaR, etc.) et crée des windows.

```
Phase 2/4: Creating windows for year 2017...
  Computing features on 525,600 rows...
  Scaling features...
  Creating 525,000+ windows...
  Saved to training_output/windows_train/year_2017.npz
```

**Output:** `training_output/windows_train/*.npz` et `windows_test/*.npz`

### Phase 3: Construction des Datasets (~5 min)

Charge les windows et crée des tf.data.Dataset optimisés.

```
Phase 3/4: Building TensorFlow Datasets...
  Total windows: 3,500,000+
  Memory usage: 12.5 GB
```

### Phase 4: Entraînement (~4-8 heures pour 20 epochs)

```
Epoch 1/20
━━━━━━━━━━━━━━━━━━━━━━━━━ 2000/2000 - 720s
loss: 0.0234 - val_loss: 0.0267

=== VALIDATION METRICS (Epoch 1) ===
Trading Performance:
  Sharpe Ratio:        0.876
  Sortino Ratio:       1.123
  Max Drawdown:       -18.4%
  Calmar Ratio:        0.54
  Win Rate:            51.2%
  Profit Factor:       1.12

Classification:
  Accuracy:            54.3%
  Macro F1:            0.487
  Cohen's Kappa:       0.315

Regression:
  MAE Returns:         0.0045
  R² Returns:          0.167
  MAE Volatility:      0.0018

Memory:
  RAM Usage:           13.2 GB / 16.0 GB
  GPU Memory:          8.1 GB / 12.0 GB
=====================================
```

---

## Monitoring en Temps Réel

### TensorBoard

Dans un autre terminal:

```bash
tensorboard --logdir=training_output/tensorboard/ --port=6006
```

Puis ouvrir: http://localhost:6006

**Métriques disponibles:**

- **Scalars:** Loss, metrics par epoch
- **Trading:** Sharpe, Sortino, MDD, Win Rate, Profit Factor
- **Classification:** Accuracy, F1, Kappa
- **Regression:** MAE, R², RMSE
- **Memory:** RAM et GPU usage
- **Custom:** Horizon-specific metrics

---

## KPIs Trackés (30+)

### A. Trading Performance (10 KPIs)
1. Sharpe Ratio (annualisé)
2. Sortino Ratio
3. Maximum Drawdown
4. Calmar Ratio
5. Win Rate
6. Profit Factor
7. Avg Win/Loss Ratio
8. Total Return
9. Annualized Return
10. Volatility (annualisée)

### B. Classification (8 KPIs)
11. Accuracy
12. Confusion Matrix (3x3)
13. Precision per class (down/flat/up)
14. Recall per class
15. F1-Score per class
16. Macro F1
17. Weighted F1
18. Cohen's Kappa

### C. Regression (6 KPIs)
19. MAE Returns
20. RMSE Returns
21. R² Returns
22. MAE Volatility
23. RMSE Volatility
24. R² Volatility

### D. Distribution Analysis (4 KPIs)
25. Prediction Bias
26. Error Skewness
27. Error Kurtosis
28. 95th Percentile Error

### E. Horizon-Specific (2 × 12 KPIs)
29. MAE per horizon step (t+1 à t+12)
30. Directional accuracy per horizon

---

## Outputs Générés

```
training_output/
├── scaler.pkl                      # Scaler normalisé
├── windows_train/                  # Windows d'entraînement
│   ├── year_2017.npz
│   ├── year_2018.npz
│   └── ...
├── windows_test/                   # Windows de test
│   └── year_2024.npz
├── checkpoints/
│   ├── best_val_loss.keras        # Meilleur modèle (val_loss)
│   ├── epoch_001.keras            # Checkpoints par epoch
│   ├── epoch_020.keras
│   └── final_model.keras          # Modèle final
├── tensorboard/
│   └── events.out.tfevents...     # Logs TensorBoard
├── metrics/
│   ├── training_metrics.csv       # Trading metrics par epoch
│   ├── trading_metrics.csv        # Tous les KPIs par epoch
│   ├── training_log.csv           # Logs Keras standard
│   └── final_metrics.json         # Métriques finales sur test set
└── logs/
    ├── train_*.log                 # Logs texte
    └── predictions_analysis/
        ├── predictions_epoch_005.png
        ├── predictions_epoch_010.png
        └── ...
```

---

## Analyse Post-Training

### 1. Métriques finales

```python
import json

with open('training_output/metrics/final_metrics.json') as f:
    metrics = json.load(f)

print(f"Sharpe Ratio: {metrics['sharpe_ratio']:.4f}")
print(f"Max Drawdown: {metrics['max_drawdown']:.2%}")
print(f"Accuracy: {metrics['accuracy']:.2%}")
```

### 2. Charger le meilleur modèle

```python
import tensorflow as tf

model = tf.keras.models.load_model('training_output/checkpoints/best_val_loss.keras')

# Faire des prédictions
predictions = model.predict(X_new)
```

### 3. Comparer les epochs

```python
import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_csv('training_output/metrics/trading_metrics.csv')

plt.figure(figsize=(12, 4))
plt.subplot(131)
plt.plot(df['epoch'], df['sharpe_ratio'])
plt.title('Sharpe Ratio')

plt.subplot(132)
plt.plot(df['epoch'], df['accuracy'])
plt.title('Direction Accuracy')

plt.subplot(133)
plt.plot(df['epoch'], df['mae_returns'])
plt.title('MAE Returns')

plt.tight_layout()
plt.show()
```

---

## Troubleshooting

### Out of Memory (OOM)

**Symptôme:** `ResourceExhaustedError` ou crash

**Solutions:**
1. Réduire `batch_size` dans config (256 → 128 → 64)
2. Réduire `shuffle_buffer` (50000 → 10000)
3. Fermer autres applications

### Entraînement très lent

**Symptôme:** < 50 steps/seconde

**Solutions:**
1. Vérifier que GPU est utilisé: `nvidia-smi`
2. Augmenter `prefetch` (2 → 4)
3. Réduire `steps_per_epoch` pour tests rapides

### Data non trouvée S3

**Symptôme:** `NoSuchKey` ou `AccessDenied`

**Solutions:**
1. Vérifier AWS credentials: `aws s3 ls s3://qbia/`
2. Vérifier le path dans config YAML
3. Vérifier les permissions du bucket

### NaN losses

**Symptôme:** Loss devient NaN après quelques steps

**Solutions:**
1. Réduire learning rate (`lr: 0.0003` → `0.0001`)
2. Augmenter gradient clipping (`clip_norm: 1.0` → `0.5`)
3. Vérifier les données (pas de NaN/Inf)

---

## Optimisation des Hyperparamètres

### Pour améliorer les performances

1. **Augmenter la capacité du modèle:**
   ```yaml
   model:
     d_model: 256    # au lieu de 128
     n_heads: 8      # au lieu de 4
     d_ff: 512       # au lieu de 256
   ```

2. **Learning rate schedule:**
   - Le cosine warmup est déjà activé
   - Essayer des valeurs: `[1e-4, 3e-4, 1e-3]`

3. **Loss weights:**
   ```yaml
   loss_weights:
     w_ret: 1.5     # Mettre plus d'emphase sur returns
     w_dir: 0.5
     w_rv: 0.3
   ```

4. **Plus de contexte:**
   ```yaml
   model:
     lookback: 512   # au lieu de 256 (8+ heures)
   ```

---

## Timeline Estimé

Pour un run complet (2017-2024):

| Phase | Durée | Description |
|-------|-------|-------------|
| Phase 1 | 10-20 min | Fitting scaler (8 années) |
| Phase 2 | 30-60 min | Création windows + features |
| Phase 3 | 5 min | Build TF datasets |
| Phase 4 | 4-8 heures | Entraînement (20 epochs) |
| **Total** | **~5-9 heures** | Run complet |

**Conseils:**
- Lancer overnight pour un run complet
- Tester d'abord sur 1-2 années (`years_train: [2023]`)
- Utiliser `epochs: 5` pour tests rapides

---

## Tests Rapides

### Test sur 1 année seulement

```yaml
data:
  years_train: [2023]
  years_test: [2024]

training:
  epochs: 5
  steps_per_epoch: 500
```

Durée: ~30-60 minutes total

### Test sur données cached

Si vous avez déjà créé les windows:

```python
# Le script détecte automatiquement et skip les étapes déjà faites
# Phase 1: Scaler exists, skipping...
# Phase 2: Windows exist, skipping...
# Phase 3: Direct to training
```

---

## Support

Pour questions ou problèmes:

1. Vérifier les logs: `training_output/logs/train_*.log`
2. Vérifier TensorBoard pour anomalies
3. Consulter la documentation TensorFlow/Keras

---

## Prochaines Étapes

Après le premier entraînement réussi:

1. **Backtesting:** Utiliser le modèle pour simuler trading sur 2024
2. **Hyperparameter tuning:** Tester différentes configurations
3. **Ensemble models:** Combiner plusieurs modèles entraînés
4. **Production deployment:** Export ONNX ou TFLite pour inference optimisée

Bon entraînement! 🚀
