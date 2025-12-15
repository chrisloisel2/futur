# TRM Quick Start Guide

Guide rapide pour entraîner votre premier modèle TRM en 5 minutes.

## Prerequisites

```bash
# Vérifier que Python 3.8+ est installé
python --version

# Vérifier que PyTorch est installé
python -c "import torch; print(torch.__version__)"

# Si PyTorch n'est pas installé:
pip install torch pandas numpy boto3 pyyaml
```

## Step 1: Vérifier les données S3

```bash
# Vérifier que vous avez accès aux données S3
cd /Users/christopher/Desktop/futur/ai/TRAIN

python -c "
from data.s3_data_source import S3DataSource
s3 = S3DataSource(bucket='qbia', prefix='bourse/mintrad')
years = s3.list_available_years()
print(f'Années disponibles: {years}')
symbols = s3.list_available_symbols(2024)
print(f'Nombre de symbols: {len(symbols)}')
"
```

## Step 2: Configurer le modèle

Éditer `trm/config.yaml`:

```yaml
# Configuration minimale pour un test rapide
data:
  symbol_filter: "BTCUSDT"  # Commencer avec BTC uniquement
  start_year: 2023          # 2 ans de données
  end_year: 2024

training:
  max_epochs: 10            # Test rapide (normalement 100)
  batch_size: 128

model:
  latent_dim: 32            # Modèle tiny
  num_iterations: 5
```

## Step 3: Lancer l'entraînement

```bash
cd /Users/christopher/Desktop/futur/ai/TRAIN/trm

# Test rapide (10 epochs, 1 symbol)
python train_trm.py --config config.yaml --epochs 10 --symbol BTCUSDT

# Entraînement complet (recommandé)
python train_trm.py --config config.yaml
```

## Step 4: Surveiller l'entraînement

Vous devriez voir:

```
============================================================
TINY RECURSIVE MODEL (TRM) TRAINING
============================================================
Device: cuda
Seed: 42

============================================================
STEP 1: LOADING DATA
============================================================
Loading BTCUSDT 2023 from S3: s3://qbia/bourse/mintrad/klines_1m_TRADING_USDT_2023/BTCUSDT_2023_1m.parquet
...
Data loaded successfully:
  Train samples: 250000
  Val samples:   50000
  Test samples:  50000
  Features:      10
  Lookback:      60

============================================================
STEP 2: CREATING MODEL
============================================================
Model created:
  Total parameters:      15,234
  Trainable parameters:  15,234
  Latent dimension:      32
  Recursive iterations:  5

============================================================
STEP 4: TRAINING MODEL
============================================================
Epoch 1/10 | Train Loss: 0.123456 | Val Loss: 0.134567 | Val Sharpe: 0.234 | LR: 1.00e-04
Epoch 2/10 | Train Loss: 0.098765 | Val Loss: 0.109876 | Val Sharpe: 0.456 | LR: 9.90e-05
New best validation Sharpe: 0.456
...
```

## Step 5: Vérifier les résultats

Après l'entraînement, vous verrez:

```
============================================================
TRADING PERFORMANCE METRICS
============================================================

Profitability:
  Total Return:        12.34%
  Sharpe Ratio:        1.23
  Sortino Ratio:       1.56

Risk:
  Max Drawdown:        8.45%

Trade Statistics:
  Win Rate:            55.67%
  Profit Factor:       1.34
  Total Trades:        1234

============================================================
```

## Step 6: Fichiers générés

```
trm/
├── checkpoints/
│   ├── checkpoint_best.pt      # Meilleur modèle (use this!)
│   └── checkpoint_latest.pt    # Dernier checkpoint
├── logs/
│   └── trm_training.log        # Logs complets
└── plots/  (si activé)
```

## Utiliser le modèle entraîné

```python
import torch
from trm import TinyRecursiveModel

# Charger le modèle
model = TinyRecursiveModel(num_features=10, latent_dim=32, num_iterations=5)
checkpoint = torch.load('checkpoints/checkpoint_best.pt')
model.load_state_dict(checkpoint['model_state_dict'])
model.eval()

# Préparer des features (exemple: 1 sample, 60 timesteps, 10 features)
# Dans la vraie vie, ces features viennent de votre pipeline de données
features = torch.randn(1, 60, 10)

# Prédiction
with torch.no_grad():
    prediction = model(features)
    direction = torch.sign(prediction)      # -1 (short), 0 (neutral), 1 (long)
    confidence = torch.abs(prediction)

print(f"Signal: {'LONG' if direction > 0 else 'SHORT'}")
print(f"Confidence: {confidence.item():.4f}")
```

## Troubleshooting

### Erreur: "No data loaded from S3"

```bash
# Vérifier credentials AWS
aws configure list

# Vérifier accès au bucket
aws s3 ls s3://qbia/bourse/mintrad/
```

### Erreur: "CUDA out of memory"

```bash
# Réduire batch size dans config.yaml
training:
  batch_size: 64  # Au lieu de 128
```

### Entraînement trop lent

```bash
# Utiliser moins de données
data:
  start_year: 2024  # Une seule année
  end_year: 2024

# Ou réduire lookback window
data:
  lookback_window: 30  # Au lieu de 60
```

## Next Steps

### 1. Entraîner sur plusieurs symbols

```yaml
data:
  symbol_filter: null  # Tous les symbols
  # ou
  symbols: ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
```

### 2. Optimiser les hyperparamètres

```yaml
model:
  latent_dim: 64        # Essayer 16, 32, 64
  num_iterations: 7     # Essayer 3, 5, 7

training:
  learning_rate: 5e-5   # Essayer 1e-4, 5e-5, 1e-5
```

### 3. Walk-Forward Validation

```yaml
evaluation:
  walk_forward:
    enabled: true
    train_window_size: 100000  # ~70 jours de 1-min bars
    test_window_size: 10000    # ~7 jours
    step_size: 10000           # Avancer de 7 jours
```

### 4. Production Deployment

Voir [README.md](README.md) section "Production Deployment" pour:
- Intégration avec votre système de trading
- Monitoring de performance
- Stratégie de réentraînement
- Gestion des risques

## Performance Expectations

### Test rapide (10 epochs, BTCUSDT 2023-2024)
- **Training time**: ~5-10 minutes (GPU), ~30 minutes (CPU)
- **Expected Sharpe**: 0.5-1.0 (pas optimal, juste pour tester)

### Entraînement complet (100 epochs, multi-symbols, 2020-2024)
- **Training time**: ~1-2 heures (GPU), ~6-8 heures (CPU)
- **Target Sharpe**: >1.0 sur test set
- **Target Return**: >10% annualisé
- **Max Drawdown**: <20%

## Best Practices

1. **Toujours** commencer par un test rapide (10 epochs, 1 symbol)
2. **Valider** que les données se chargent correctement
3. **Vérifier** que le modèle converge (loss décroît)
4. **Analyser** les métriques de trading (pas juste la loss)
5. **Tester** la robustesse avant la production

## Support

- Lire [README.md](README.md) pour la documentation complète
- Lire [README_TRM_ARCHITECTURE.md](README_TRM_ARCHITECTURE.md) pour les détails théoriques
- Vérifier les logs dans `logs/trm_training.log`

---

**Bonne chance! Remember: Less is More. 🚀**
