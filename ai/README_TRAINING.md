# Advanced Training System - TinyRecursiveMarketModel

## 🎯 Résumé

Système d'entraînement ultra-poussé avec:
- ✅ Chargement streaming année par année depuis S3
- ✅ 30+ KPIs (Sharpe, Sortino, MDD, F1, R², etc.)
- ✅ TensorBoard + CSV logging
- ✅ Optimisé pour RAM limitée (8-16 GB)
- ✅ GPU support avec mixed precision FP16

## 📦 Fichiers Créés

```
ai/
├── s3_parquet_loader.py            ✅ Chargement S3 Parquet
├── data_pipeline.py                ✅ Pipeline streaming optimisé
├── advanced_metrics.py             ✅ 30+ KPIs avancés
├── training_callbacks.py           ✅ Callbacks TensorBoard/CSV
├── train_advanced.py               ✅ Script principal ⭐
├── configs/
│   └── train_advanced.yaml         ✅ Configuration
├── requirements_training.txt       ✅ Dépendances
├── launch_training.sh              ✅ Launcher bash
├── test_pipeline.py                ✅ Tests validation
├── TRAINING_GUIDE.md               ✅ Guide complet
└── README_TRAINING.md              ✅ Ce fichier
```

## 🚀 Quick Start

### 1. Installation

```bash
cd /Users/christopher/Desktop/futur
pip install -r ai/requirements_training.txt
```

### 2. Test du système

```bash
python3 ai/test_pipeline.py
```

Doit afficher:
```
✓ TensorFlow, NumPy, Pandas
✓ GPU detected
✓ S3 accessible (8 years)
✓ Model creation OK
✓ Metrics computation OK
ALL TESTS PASSED ✓
```

### 3. Lancement entraînement

**Option A: Script automatique**
```bash
./ai/launch_training.sh
```

**Option B: Python direct**
```bash
python3 ai/train_advanced.py --config ai/configs/train_advanced.yaml
```

### 4. Monitoring TensorBoard

Dans un autre terminal:
```bash
tensorboard --logdir=training_output/tensorboard/ --port=6006
```

Ouvrir: http://localhost:6006

## ⚙️ Configuration

### Fichier: `ai/configs/train_advanced.yaml`

**Test rapide (30-60 min):**
```yaml
data:
  years_train: [2023]
  years_test: [2024]

training:
  epochs: 5
  steps_per_epoch: 500
```

**Production complète (5-9 heures):**
```yaml
data:
  years_train: [2017, 2018, 2019, 2020, 2021, 2022, 2023]
  years_test: [2024]

training:
  epochs: 20
  steps_per_epoch: 2000
```

## 📊 KPIs Trackés

### Trading (10 KPIs)
- Sharpe Ratio, Sortino Ratio
- Maximum Drawdown, Calmar Ratio
- Win Rate, Profit Factor
- Avg Win/Loss, Returns, Volatility

### Classification (8 KPIs)
- Accuracy, Confusion Matrix
- Precision/Recall/F1 per class
- Macro F1, Weighted F1, Cohen's Kappa

### Regression (6 KPIs)
- MAE/RMSE/R² Returns
- MAE/RMSE/R² Volatility

### Distribution (4 KPIs)
- Prediction Bias, Skewness
- Kurtosis, 95th Percentile

### Horizon-Specific (24 KPIs)
- MAE per horizon (t+1 à t+12)
- Accuracy per horizon (12 steps)

**Total: 30+ KPIs** trackés à chaque epoch!

## 📈 Outputs Générés

```
training_output/
├── scaler.pkl                      # Scaler normalisé
├── windows_train/year_*.npz        # Windows train
├── windows_test/year_*.npz         # Windows test
├── checkpoints/
│   ├── best_val_loss.keras         # Meilleur modèle
│   └── final_model.keras           # Modèle final
├── tensorboard/                    # Logs TensorBoard
├── metrics/
│   ├── trading_metrics.csv         # KPIs par epoch
│   ├── training_log.csv            # Logs standard
│   └── final_metrics.json          # Métriques finales
└── logs/
    ├── train_*.log                  # Logs texte
    └── predictions_analysis/*.png   # Visualisations
```

## 🔍 Exemple Output

```
Epoch 5/20
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 2000/2000 - 720s
loss: 0.0234 - ret_loss: 0.0156 - dir_loss: 0.0068 - rv_loss: 0.0010
val_loss: 0.0267 - val_ret_loss: 0.0178 - val_dir_loss: 0.0078

=== VALIDATION METRICS (Epoch 5) ===
Trading Performance:
  Sharpe Ratio:        1.234
  Sortino Ratio:       1.567
  Max Drawdown:       -12.3%
  Calmar Ratio:        0.89
  Win Rate:            54.2%
  Profit Factor:       1.45

Classification:
  Accuracy:            58.3%
  Macro F1:            0.521
  Cohen's Kappa:       0.412

Regression:
  MAE Returns:         0.0034
  R² Returns:          0.234

Memory:
  RAM Usage:           14.2 GB / 16.0 GB
  GPU Memory:          7.8 GB / 12.0 GB
=====================================
```

## 🛠️ Troubleshooting

### Out of Memory
```yaml
training:
  batch_size: 128  # au lieu de 256
```

### Training trop lent
- Vérifier GPU: `nvidia-smi`
- Augmenter prefetch: `prefetch: 4`

### S3 Access Error
```bash
aws configure
aws s3 ls s3://qbia/
```

## 📚 Documentation Complète

Voir: [TRAINING_GUIDE.md](TRAINING_GUIDE.md)

Contient:
- Installation détaillée
- Description de toutes les phases
- Analyse post-training
- Optimisation hyperparamètres
- Troubleshooting avancé

## ⏱️ Timeline

| Phase | Durée | Description |
|-------|-------|-------------|
| Scaler | 10-20 min | Fitting normalization |
| Windows | 30-60 min | Feature engineering |
| Datasets | 5 min | TF dataset build |
| Training | 4-8h | 20 epochs @ 2000 steps |
| **Total** | **~5-9h** | Full run |

## 🎓 Architecture du Modèle

```
TinyRecursiveMarketModel
├── Input: [B, 256, 50] (4.3h context, 50 features)
├── TransformerBlock #1 (d_model=128, n_heads=4)
├── TinyRecursiveMemory (mem_dim=128, iters=2)
├── TransformerBlock #2
├── Multi-scale Pooling (mean + last + mem)
└── Heads:
    ├── Returns: [B, 12] (regression)
    ├── Direction: [B, 3] (classification)
    └── Volatility: [B, 12] (regression)
```

## 📝 Prochaines Étapes

Après premier entraînement:

1. **Analyser les résultats**
   ```python
   import json
   with open('training_output/metrics/final_metrics.json') as f:
       metrics = json.load(f)
   ```

2. **Comparer epochs dans TensorBoard**
   - Trading metrics trends
   - Overfitting detection

3. **Hyperparameter tuning**
   - Learning rate: `[1e-4, 3e-4, 1e-3]`
   - Model size: `d_model=[128, 256, 512]`
   - Loss weights: ajuster selon objectif

4. **Backtesting**
   - Utiliser best model sur 2024
   - Calculer returns réels

5. **Production deployment**
   - Export ONNX pour inference rapide
   - API serving avec FastAPI

## 🤝 Support

Problèmes? Vérifier:
1. `training_output/logs/train_*.log`
2. TensorBoard: graphes et anomalies
3. Test pipeline: `python3 ai/test_pipeline.py`

---

**Bon entraînement! 🚀**

Pour toute question, consulter le guide complet: [TRAINING_GUIDE.md](TRAINING_GUIDE.md)
