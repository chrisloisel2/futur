# 🚀 Installation et Lancement - Guide Rapide

## Étape 1: Installation des Dépendances

```bash
cd /Users/christopher/Desktop/futur
pip install -r ai/requirements_training.txt
```

Cela installera:
- TensorFlow (avec GPU support)
- NumPy, Pandas, Scikit-learn, SciPy
- PyArrow (pour Parquet)
- Boto3 (pour S3)
- Matplotlib (visualisations)
- PyYAML (configuration)
- psutil (memory monitoring)

**Temps estimé:** 5-10 minutes

## Étape 2: Vérification AWS

Assurez-vous que vos credentials AWS sont configurées:

```bash
aws configure list
```

Devrait afficher votre configuration. Si pas configuré:

```bash
aws configure
# Entrer: AWS Access Key ID
# Entrer: AWS Secret Access Key
# Region: us-east-1 (ou votre région)
```

Test d'accès S3:

```bash
aws s3 ls s3://qbia/bourse/processed/market/interval=1m/quote=USDT/symbol=BTCUSDT/
```

Devrait lister les années disponibles.

## Étape 3: Test du Pipeline

```bash
python3 ai/test_pipeline.py
```

Devrait afficher:
```
✓ TensorFlow 2.x
✓ GPU detected (ou CPU si pas de GPU)
✓ S3 accessible
✓ Model creation OK
✓ Metrics computation OK
ALL TESTS PASSED ✓
```

Si des erreurs, vérifier:
- TensorFlow installé: `pip show tensorflow`
- GPU drivers: `nvidia-smi` (si GPU)
- AWS credentials: `aws configure list`

## Étape 4: Configuration (Optionnel)

Éditer `ai/configs/train_advanced.yaml` selon vos besoins:

### Test Rapide (30-60 min):
```yaml
data:
  years_train: [2023]
  years_test: [2024]

training:
  epochs: 5
  steps_per_epoch: 500
  batch_size: 128  # Réduire si RAM limitée
```

### Production Complète (5-9 heures):
```yaml
data:
  years_train: [2017, 2018, 2019, 2020, 2021, 2022, 2023]
  years_test: [2024]

training:
  epochs: 20
  steps_per_epoch: 2000
  batch_size: 256
```

## Étape 5: Lancement de l'Entraînement

### Option A: Script automatique (recommandé)

```bash
./ai/launch_training.sh
```

Le script vérifie:
- Python installé
- TensorFlow disponible
- GPU détecté
- Confirmation avant lancement

### Option B: Python direct

```bash
python3 ai/train_advanced.py --config ai/configs/train_advanced.yaml
```

## Étape 6: Monitoring en Temps Réel

Dans un **autre terminal**, pendant l'entraînement:

```bash
tensorboard --logdir=training_output/tensorboard/ --port=6006
```

Puis ouvrir dans navigateur: **http://localhost:6006**

Vous verrez:
- Loss curves en temps réel
- Trading metrics (Sharpe, MDD, etc.)
- Classification metrics (Accuracy, F1)
- Memory usage (RAM, GPU)

## Étape 7: Pendant l'Entraînement

L'entraînement affiche à chaque epoch:

```
Epoch 1/20
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 2000/2000 - 720s
loss: 0.0234 - val_loss: 0.0267

=== VALIDATION METRICS (Epoch 1) ===
Trading Performance:
  Sharpe Ratio:        0.876
  Sortino Ratio:       1.123
  Max Drawdown:       -18.4%
  Win Rate:            51.2%
  Profit Factor:       1.12

Classification:
  Accuracy:            54.3%
  Macro F1:            0.487

Regression:
  MAE Returns:         0.0045
  R² Returns:          0.167

Memory:
  RAM Usage:           13.2 GB / 16.0 GB
  GPU Memory:          8.1 GB / 12.0 GB
=====================================
```

**Que surveiller:**
- Sharpe Ratio devrait augmenter (> 1.0 est bon)
- Accuracy devrait améliorer (> 55% est bon pour 3 classes)
- MAE Returns devrait diminuer
- Memory usage < 90%

## Étape 8: Après l'Entraînement

Résultats dans `training_output/`:

```
training_output/
├── checkpoints/
│   ├── best_val_loss.keras     ← Meilleur modèle!
│   └── final_model.keras
├── metrics/
│   ├── final_metrics.json      ← Tous les KPIs
│   └── trading_metrics.csv     ← Historique
└── logs/
    └── predictions_analysis/   ← Visualisations
```

### Charger le meilleur modèle:

```python
import tensorflow as tf

model = tf.keras.models.load_model(
    'training_output/checkpoints/best_val_loss.keras'
)

# Faire des prédictions
predictions = model.predict(X_new)
# predictions['ret']: returns prédits
# predictions['dir']: direction (down/flat/up)
# predictions['rv']: volatility prédite
```

### Analyser les métriques:

```python
import json

with open('training_output/metrics/final_metrics.json') as f:
    metrics = json.load(f)

print(f"Sharpe Ratio: {metrics['sharpe_ratio']:.4f}")
print(f"Max Drawdown: {metrics['max_drawdown']:.2%}")
print(f"Accuracy: {metrics['accuracy']:.2%}")
print(f"Profit Factor: {metrics['profit_factor']:.2f}")
```

### Visualiser l'évolution:

```python
import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_csv('training_output/metrics/trading_metrics.csv')

plt.figure(figsize=(15, 4))

plt.subplot(131)
plt.plot(df['epoch'], df['sharpe_ratio'])
plt.title('Sharpe Ratio par Epoch')
plt.xlabel('Epoch')
plt.ylabel('Sharpe Ratio')

plt.subplot(132)
plt.plot(df['epoch'], df['accuracy'])
plt.title('Direction Accuracy')
plt.xlabel('Epoch')
plt.ylabel('Accuracy')

plt.subplot(133)
plt.plot(df['epoch'], df['max_drawdown'])
plt.title('Maximum Drawdown')
plt.xlabel('Epoch')
plt.ylabel('MDD')

plt.tight_layout()
plt.savefig('training_evolution.png', dpi=150)
plt.show()
```

## Troubleshooting Rapide

### Problème: Out of Memory (OOM)

**Solution:** Réduire batch_size dans config

```yaml
training:
  batch_size: 64  # au lieu de 256
```

### Problème: Training très lent

**Solutions:**
1. Vérifier GPU utilisé: `nvidia-smi`
2. Réduire steps_per_epoch pour tests: `steps_per_epoch: 500`
3. Augmenter prefetch: `prefetch: 4`

### Problème: S3 Access Denied

**Solution:** Vérifier credentials

```bash
aws configure list
aws s3 ls s3://qbia/
```

### Problème: Loss devient NaN

**Solutions:**
1. Réduire learning rate:
   ```yaml
   training:
     lr: 0.0001  # au lieu de 0.0003
   ```
2. Augmenter gradient clipping:
   ```yaml
   training:
     clip_norm: 0.5  # au lieu de 1.0
   ```

## Commandes Utiles

```bash
# Voir l'utilisation GPU en temps réel
watch -n 1 nvidia-smi

# Monitorer la RAM
htop

# Tail les logs
tail -f training_output/logs/train_*.log

# Lister les checkpoints
ls -lh training_output/checkpoints/

# Voir les métriques en temps réel
tail -f training_output/metrics/trading_metrics.csv
```

## Prochaines Étapes

1. **Analyser les résultats** dans TensorBoard
2. **Comparer différentes configs** (learning rate, model size)
3. **Backtester le modèle** sur données 2024
4. **Optimiser hyperparamètres** selon vos objectifs
5. **Déployer en production** avec export ONNX

---

**Bon entraînement! 🚀**

Pour plus de détails, voir:
- [README_TRAINING.md](README_TRAINING.md) - Vue d'ensemble
- [TRAINING_GUIDE.md](TRAINING_GUIDE.md) - Guide complet et détaillé
