# 🚀 Lancement Rapide - Entraînement avec S3

## Résumé

Votre script d'entraînement a été modifié avec succès ! Il peut maintenant charger les données historiques depuis votre bucket S3 `s3://qbia/bourse/mintrad/`.

## ✅ Tests effectués

Tous les tests sont **RÉUSSIS** :

1. ✅ Chargement des données S3 (337 symboles disponibles pour 2024)
2. ✅ Pipeline de features (52 indicateurs techniques générés)
3. ✅ DataLoaders PyTorch (23,048 batches train)
4. ✅ Cache local fonctionnel

## 🎯 Lancement rapide

### Option 1 : Script interactif (recommandé)

```bash
./ai/quick_start.sh
```

Le script vous propose :
1. Test rapide du chargement S3
2. Test du pipeline complet
3. Entraînement DEBUG (1 epoch, test rapide)
4. Entraînement QUICK (10 epochs, 5 symboles)
5. Entraînement COMPLET (50 epochs, 8 symboles)

### Option 2 : Commandes manuelles

#### Test rapide
```bash
# Tester le chargement S3
python ai/test_s3_data_source.py

# Tester le pipeline complet
python ai/test_pipeline_s3.py
```

#### Entraînement DEBUG (recommandé pour premier test)
```bash
cd ai
PYTHONPATH=TRAIN:$PYTHONPATH python3 train.py \
    --config configs/train_s3.yaml \
    --device mps \
    --debug_mode
```

#### Entraînement COMPLET
```bash
cd ai
PYTHONPATH=TRAIN:$PYTHONPATH python3 train.py \
    --config configs/train_s3.yaml \
    --device mps
```

## 📊 Données disponibles

### Votre bucket S3

- **Bucket** : `s3://qbia/bourse/mintrad/`
- **Années** : 2017 à 2025 (9 années)
- **Symboles** : 337 paires USDT pour 2024
- **Format** : Klines 1-minute Binance
- **Taille** : ~500,000 lignes par symbole/an

### Symboles populaires disponibles

BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT, ADAUSDT, DOGEUSDT, MATICUSDT, DOTUSDT, AVAXUSDT, LINKUSDT, UNIUSDT, ATOMUSDT, LTCUSDT, ETCUSDT, etc.

## ⚙️ Configuration

### Configuration par défaut ([ai/configs/train_s3.yaml](ai/configs/train_s3.yaml))

```yaml
data:
  data_source: "s3"
  start_year: 2020
  end_year: 2024
  symbols_filter: ["BTCUSDT", "ETHUSDT", "BNBUSDT", ...]
  local_cache_dir: "/tmp/trading_data_cache"
  lookback_window: 100
  batch_size: 32

model:
  params:
    d_model: 512
    n_heads: 8
    feature_dim: 52  # 52 features techniques générées

training:
  epochs: 50
  learning_rate: 0.0001
```

### Personnalisation

Pour modifier la configuration, éditez `ai/configs/train_s3.yaml` :

**Changer les symboles** :
```yaml
symbols_filter:
  - "BTCUSDT"
  - "ETHUSDT"
  - "SOLUSDT"
```

**Changer la période** :
```yaml
start_year: 2022
end_year: 2024
```

**Charger TOUS les symboles** :
```yaml
symbols_filter: []  # Laissez vide
```

## 📈 Features générées

Le système génère automatiquement **52 features techniques** :

### Returns & Volatilité (7)
- Returns (1, 4, 12 périodes)
- Volatilité (14, 30, 60 périodes)

### Moyennes Mobiles (15)
- SMA (5, 10, 20, 50, 100, 200)
- EMA (8, 12, 21, 34, 55, 89)
- Ratios close/SMA

### Momentum (15)
- RSI (7, 14, 21)
- Stochastic (K, D)
- MACD (line, signal, histogram)
- CCI (20, 50)
- MFI (14)

### Volatilité & Trend (6)
- Bollinger Bands (up, low, width)
- ATR (14)
- ADX (14)

### Volume (5)
- OBV, VWAP
- Volume SMA (20, 50)
- Volume Z-score

### Patterns (4)
- Range, Body, Shadows
- Close over rolling max/min

## 🎓 Exemples de configurations

### Configuration 1 : Test rapide (< 5 minutes)
```yaml
start_year: 2024
end_year: 2024
symbols_filter: ["BTCUSDT", "ETHUSDT"]
lookback_window: 50
batch_size: 64

training:
  epochs: 1
  debug_mode: true
```

### Configuration 2 : Développement (~ 30 minutes)
```yaml
start_year: 2023
end_year: 2024
symbols_filter: ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
lookback_window: 100
batch_size: 32

training:
  epochs: 10
```

### Configuration 3 : Production (plusieurs heures)
```yaml
start_year: 2020
end_year: 2024
symbols_filter: []  # Tous les symboles
lookback_window: 200
batch_size: 16

training:
  epochs: 100
  gradient_accumulation_steps: 8
```

## 📁 Structure des fichiers

```
ai/
├── configs/
│   ├── train_s3.yaml          # Config S3 (NOUVEAU)
│   └── train_ccxt.yaml        # Config CCXT (ancien comportement)
│
├── TRAIN/
│   ├── data/
│   │   ├── pipeline.py        # Modifié
│   │   └── s3_data_source.py  # NOUVEAU
│   └── requirements.txt       # Modifié (boto3 ajouté)
│
├── train.py                   # Script principal (inchangé)
├── test_s3_data_source.py     # NOUVEAU
├── test_pipeline_s3.py        # NOUVEAU
├── quick_start.sh             # NOUVEAU
│
├── README_S3_TRAINING.md      # Documentation complète
├── MODIFICATIONS_SUMMARY.md   # Résumé technique
└── LANCEMENT_RAPIDE.md        # Ce fichier
```

## 🔧 Dépannage

### Erreur : "No module named 'boto3'"
```bash
pip install boto3 s3fs pyarrow
```

### Erreur : Credentials AWS
```bash
aws configure
# Entrez vos credentials AWS
```

### Erreur : Mémoire insuffisante
Réduisez le `batch_size` dans la config :
```yaml
batch_size: 16  # Au lieu de 32
```

### Performance lente
Le cache local accélère les chargements suivants :
```yaml
local_cache_dir: "/tmp/trading_data_cache"
```

## 📊 Monitoring

### Pendant l'entraînement

Les logs affichent :
- Progression des epochs
- Loss (train/val)
- Métriques de performance
- Temps par epoch

### Après l'entraînement

Les checkpoints sont sauvegardés dans :
- `checkpoints_s3/` (par défaut)
- Format : `model_YYYYMMDD_HHMM.pt`

## 🎯 Commandes utiles

```bash
# Vérifier l'accès S3
aws s3 ls s3://qbia/bourse/mintrad/

# Lister les symboles disponibles pour 2024
aws s3 ls s3://qbia/bourse/mintrad/klines_1m_TRADING_USDT_2024/

# Nettoyer le cache local
rm -rf /tmp/trading_data_cache

# Voir les logs en temps réel
python ai/train.py --config ai/configs/train_s3.yaml --device mps --log_level DEBUG

# Utiliser TensorBoard
tensorboard --logdir checkpoints_s3/
```

## 🚀 Prochaines étapes

1. **Testez le système** :
   ```bash
   ./ai/quick_start.sh
   # Choisissez option 3 (DEBUG)
   ```

2. **Ajustez la configuration** selon vos besoins

3. **Lancez l'entraînement complet** :
   ```bash
   python ai/train.py --config ai/configs/train_s3.yaml --device mps
   ```

4. **Évaluez les résultats** et itérez

## 📚 Documentation

- [README_S3_TRAINING.md](ai/README_S3_TRAINING.md) - Guide complet
- [MODIFICATIONS_SUMMARY.md](ai/MODIFICATIONS_SUMMARY.md) - Détails techniques
- [train_s3.yaml](ai/configs/train_s3.yaml) - Configuration exemple

## ✅ Checklist avant le lancement

- [ ] Credentials AWS configurés (`aws configure`)
- [ ] Dépendances installées (`pip install -r ai/TRAIN/requirements.txt`)
- [ ] Tests réussis (`./ai/quick_start.sh` option 1 & 2)
- [ ] Configuration ajustée (`ai/configs/train_s3.yaml`)
- [ ] Espace disque suffisant pour checkpoints (~1-2 GB)

---

**Vous êtes prêt ! 🎉**

Lancez simplement :
```bash
bash ./ai/quick_start.sh
```

Ou directement :
```bash
cd ai
PYTHONPATH=TRAIN:$PYTHONPATH python3 train.py --config configs/train_s3.yaml --device mps
```

**Bon entraînement ! 🚀📈**
