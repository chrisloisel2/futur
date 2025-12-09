# 🚀 QUICKSTART - Self-Supervised Learning

## Installation (5 minutes)

### 1. Installer les dépendances

```bash
cd ai/SELF_SUPERVISED
pip install -r requirements.txt
```

### 2. Tester l'installation

```bash
cd ..
python SELF_SUPERVISED/test_ssl.py
```

Vous devriez voir :
```
✅ All tests passed!
```

---

## Configuration (2 minutes)

### Éditer config_ssl.yaml

```yaml
data:
  source: mongodb
  mongo_uri: "mongodb+srv://christoloisel:rose@cluster0.ppyauvl.mongodb.net/"
  db_name: "trader2"
  collection_name: "historical_ohlcv"
  symbols: ["BTC/USDT", "ETH/USDT", "BNB/USDT"]
  sequence_length: 100

training:
  batch_size: 64
  epochs: 100
  device: "auto"
```

---

## Lancer un pré-entraînement (30 min - 2h)

### Option 1 : TS2Vec (Recommandé ⭐)

```bash
cd ai
python SELF_SUPERVISED/example_usage.py --mode ts2vec
```

**Résultat** : Checkpoint dans `SELF_SUPERVISED/checkpoints/ts2vec/`

### Option 2 : MAE

```bash
python SELF_SUPERVISED/example_usage.py --mode mae
```

**Résultat** : Checkpoint dans `SELF_SUPERVISED/checkpoints/mae/`

### Option 3 : SimCLR

```bash
python SELF_SUPERVISED/example_usage.py --mode simclr
```

**Résultat** : Checkpoint dans `SELF_SUPERVISED/checkpoints/simclr/`

---

## Utiliser le modèle pré-entraîné

### 1. Extraction de features

```python
from SELF_SUPERVISED import TS2VecModel
import torch

# Charger
device = torch.device('mps')
model = TS2VecModel(input_dim=8, hidden_dim=64, output_dim=320).to(device)

checkpoint = torch.load("SELF_SUPERVISED/checkpoints/ts2vec/ts2vec_final.pt", map_location=device)
model.load_state_dict(checkpoint['model_state_dict'])
model.eval()

# Encoder
with torch.no_grad():
    embeddings = model.encode(x, return_all=False)  # [batch, 320]
```

### 2. Transfer learning

```python
# Geler encoder
for param in model.parameters():
    param.requires_grad = False

# Ajouter tête de prédiction
prediction_head = torch.nn.Linear(320, 3).to(device)  # BUY/SELL/HOLD

# Forward
embeddings = model.encode(x, return_all=False)
predictions = prediction_head(embeddings)
```

---

## Workflow complet

```bash
# 1. Collecter données (frontend_pipeline)
cd ../../frontend_pipeline
python mass_data_collector_v2.py

# 2. Pré-entraîner SSL (SELF_SUPERVISED)
cd ../ai
python SELF_SUPERVISED/example_usage.py --mode ts2vec

# 3. Fine-tuner pour trading (TRAIN)
cd TRAIN
# Modifier train.py pour charger encoder pré-entraîné
python train.py --config config/training_config.yaml
```

---

## Monitoring

### Logs

```bash
# Voir les logs d'entraînement
tail -f SELF_SUPERVISED/checkpoints/ts2vec/training.log
```

### Checkpoints

```bash
# Lister les checkpoints
ls -lh SELF_SUPERVISED/checkpoints/ts2vec/

# Charger un checkpoint spécifique
ts2vec_epoch_50.pt
ts2vec_final.pt
```

---

## Troubleshooting

### Out of Memory

```yaml
# Réduire dans config_ssl.yaml
training:
  batch_size: 32  # Au lieu de 64
data:
  sequence_length: 50  # Au lieu de 100
```

### Loss ne descend pas

```yaml
# Augmenter dans config_ssl.yaml
training:
  lr: 0.005  # Au lieu de 0.001
ts2vec:
  temperature: 0.1  # Au lieu de 0.2
```

### Pas de données MongoDB

```yaml
# Utiliser données Parquet
data:
  source: parquet
  file_path: "../PIPELINE/datasets/alpha_trading/.../coingecko_minutely.parquet"
```

---

## Comparaison des modèles

| Modèle | Temps entraînement* | Qualité embeddings | Recommandation |
|--------|---------------------|-------------------|----------------|
| TS2Vec | ~1h | ⭐⭐⭐⭐⭐ | **Meilleur pour crypto** |
| MAE | ~1.5h | ⭐⭐⭐⭐ | Bon pour long terme |
| SimCLR | ~45min | ⭐⭐⭐ | Baseline rapide |

*Sur M1/M2 avec batch_size=64, 100 epochs

---

## Next steps

Après le pré-entraînement :

1. ✅ **Évaluer** : Tester sur tâche downstream
2. ✅ **Fine-tuner** : Affiner pour trading
3. ✅ **Intégrer** : Utiliser dans TRAIN
4. ✅ **Backtester** : Tester en conditions réelles

---

## Ressources

- **README complet** : [README.md](README.md)
- **Documentation structure** : [../../EXPLICATION_STRUCTURE.md](../../EXPLICATION_STRUCTURE.md)
- **Exemples** : [example_usage.py](example_usage.py)

---

**Vous êtes prêt ! 🚀**

Lancez `python SELF_SUPERVISED/example_usage.py --mode ts2vec` et regardez la magie opérer !
