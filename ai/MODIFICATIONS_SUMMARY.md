# Résumé des modifications - Script d'entraînement avec S3

## Modifications effectuées

Votre script d'entraînement a été modifié pour charger les données depuis votre bucket AWS S3 (`s3://qbia/bourse/mintrad/`) au lieu d'utiliser CCXT pour récupérer les données des exchanges en temps réel.

## Fichiers créés

### 1. Module S3DataSource
**Fichier**: [ai/TRAIN/data/s3_data_source.py](ai/TRAIN/data/s3_data_source.py)

Nouveau module pour charger les données depuis S3 :
- Listing des années disponibles (2017-2025)
- Listing des symboles disponibles par année
- Chargement de fichiers parquet individuels
- Chargement multi-années et multi-symboles
- Cache local pour éviter les re-téléchargements
- Conversion automatique du format Binance (12 colonnes) vers format OHLCV standard

### 2. Configurations d'entraînement

**Fichier S3**: [ai/configs/train_s3.yaml](ai/configs/train_s3.yaml)
- Configuration pour utiliser les données S3
- Exemples de filtres de symboles
- Configuration du cache local
- Plage d'années configurable (2020-2024 par défaut)

**Fichier CCXT**: [ai/configs/train_ccxt.yaml](ai/configs/train_ccxt.yaml)
- Configuration pour conserver l'ancien comportement CCXT
- Assure la rétrocompatibilité

### 3. Scripts de test

**Test source S3**: [ai/test_s3_data_source.py](ai/test_s3_data_source.py)
- Teste le listing des années/symboles
- Teste le chargement de données
- Teste le cache local
- Vérifie l'intégrité des données

**Test pipeline complet**: [ai/test_pipeline_s3.py](ai/test_pipeline_s3.py)
- Teste l'intégration complète avec DataPipeline
- Vérifie la génération de features
- Teste la création des DataLoaders PyTorch

### 4. Documentation

**README**: [ai/README_S3_TRAINING.md](ai/README_S3_TRAINING.md)
- Guide complet d'utilisation
- Exemples de configurations
- Troubleshooting
- Métriques de performance

**Ce fichier**: [ai/MODIFICATIONS_SUMMARY.md](ai/MODIFICATIONS_SUMMARY.md)

## Fichiers modifiés

### 1. Pipeline de données
**Fichier**: [ai/TRAIN/data/pipeline.py](ai/TRAIN/data/pipeline.py)

Modifications apportées :
- Ajout de paramètres S3 dans `__init__()`:
  - `data_source_type` : "s3" ou "ccxt"
  - `s3_bucket`, `s3_prefix`
  - `start_year`, `end_year`
  - `local_cache_dir`
  - `symbols_filter`

- Nouvelle méthode `_fetch_ohlcv_from_s3()` :
  - Initialise S3DataSource
  - Détermine les symboles à charger (filtrés ou tous)
  - Charge les données multi-années

- Modification de `_fetch_ohlcv()` :
  - Router selon `data_source_type`
  - Appelle `_fetch_ohlcv_from_s3()` ou code CCXT existant

### 2. Dépendances
**Fichier**: [ai/TRAIN/requirements.txt](ai/TRAIN/requirements.txt)

Ajouts :
```txt
pyarrow>=14.0.0          # Support parquet
fastparquet>=2023.10.0   # Engine parquet alternatif
boto3>=1.26.0            # SDK AWS
s3fs>=2023.1.0           # Système de fichiers S3
```

### 3. Script d'entraînement
**Fichier**: [ai/train.py](ai/train.py)

**Aucune modification** - Le script est déjà bien conçu et fonctionne avec le nouveau système via les fichiers de configuration YAML.

## Structure des données S3

Votre bucket contient :
- **9 années** de données (2017-2025)
- **337 symboles** pour 2024 (BTCUSDT, ETHUSDT, etc.)
- Format : **Klines 1-minute** Binance
- Taille : ~500K lignes par symbole/an
- Format fichier : **Parquet** (haute compression)

Organisation :
```
s3://qbia/bourse/mintrad/
├── klines_1m_TRADING_USDT_2017/
│   ├── BNBUSDT_2017_1m.parquet
│   ├── BTCUSDT_2017_1m.parquet
│   └── ...
├── klines_1m_TRADING_USDT_2018/
...
└── klines_1m_TRADING_USDT_2025/
```

## Utilisation

### Installation
```bash
cd ai/TRAIN
pip install -r requirements.txt
```

### Tests
```bash
# Test du chargement S3
python ai/test_s3_data_source.py

# Test du pipeline complet
python ai/test_pipeline_s3.py
```

### Entraînement

**Avec données S3** (recommandé) :
```bash
python ai/train.py --config ai/configs/train_s3.yaml --device mps
```

**Avec données CCXT** (ancien comportement) :
```bash
python ai/train.py --config ai/configs/train_ccxt.yaml --device mps
```

**Mode debug** :
```bash
python ai/train.py --config ai/configs/train_s3.yaml --device mps --debug_mode
```

## Résultats des tests

### Test S3DataSource
✅ **TOUS LES TESTS RÉUSSIS**
- Listage de 9 années disponibles
- Listage de 337 symboles pour 2024
- Chargement BTCUSDT 2024 : 527,040 lignes
- Chargement ETHUSDT 2023-2024 : 1,052,560 lignes
- Chargement multi-symboles : 1,581,120 lignes (3 symboles)

### Test Pipeline complet
✅ **PIPELINE S3 FONCTIONNEL**
- Chargement : 2 symboles, 1,054,080 lignes (2024)
- Feature engineering : 52 features générées
- DataLoaders créés :
  - Train : 23,048 batches
  - Validation : 4,939 batches
  - Test : 4,939 batches
- Format batch : (32, 50, 52) = (batch_size, lookback, features)

## Features générées

Le système génère automatiquement **52 features techniques** :

**Returns & Volatilité** :
- ret_1, ret_log_1, ret_4, ret_12
- vol_14, vol_30, vol_60

**Moyennes mobiles** :
- SMA (5, 10, 20, 50, 100, 200)
- EMA (8, 12, 21, 34, 55, 89)

**Indicateurs techniques** :
- Bollinger Bands (up, low, width)
- RSI (7, 14, 21)
- Stochastic (K, D)
- MACD (line, signal, histogram)
- ATR, ADX, OBV, CCI, MFI, VWAP

**Patterns** :
- Range, body, shadows
- Close over rolling max/min
- Volume indicators

## Configuration recommandée

Pour un entraînement optimal avec vos données S3 :

```yaml
data:
  data_source: "s3"
  s3_bucket: "qbia"
  s3_prefix: "bourse/mintrad"
  start_year: 2020          # 5 ans de données
  end_year: 2024
  symbols_filter:           # Top cryptos par volume
    - "BTCUSDT"
    - "ETHUSDT"
    - "BNBUSDT"
    - "SOLUSDT"
    - "XRPUSDT"
  local_cache_dir: "/tmp/trading_data_cache"  # Cache local
  lookback_window: 100      # 100 minutes d'historique
  batch_size: 32
  train_split: 0.7
  val_split: 0.15
  test_split: 0.15

model:
  params:
    d_model: 512
    n_heads: 8
    n_layers: 6
    dropout: 0.1
    feature_dim: 52         # Ajusté automatiquement

training:
  epochs: 50
  learning_rate: 0.0001
  gradient_accumulation_steps: 4
  checkpoint_dir: "checkpoints_s3"
```

## Avantages de l'approche

1. ✅ **Rétrocompatible** : CCXT fonctionne toujours
2. ✅ **Multi-années** : Charge facilement plusieurs années
3. ✅ **Performance** : Cache local évite les re-téléchargements
4. ✅ **Flexible** : Filtre de symboles configurable
5. ✅ **Scalable** : Supporte tous les symboles disponibles
6. ✅ **Robuste** : Gestion d'erreurs et logging complet

## Prochaines étapes

1. **Lancer un entraînement de test** :
   ```bash
   python ai/train.py --config ai/configs/train_s3.yaml --device mps --debug_mode
   ```

2. **Ajuster la configuration** selon vos besoins :
   - Modifier les symboles
   - Ajuster la plage d'années
   - Configurer les hyperparamètres

3. **Lancer l'entraînement complet** :
   ```bash
   python ai/train.py --config ai/configs/train_s3.yaml --device mps
   ```

4. **Monitorer** via TensorBoard ou Weights & Biases

## Support

Pour toute question, consultez :
- [README_S3_TRAINING.md](ai/README_S3_TRAINING.md) - Documentation complète
- [test_s3_data_source.py](ai/test_s3_data_source.py) - Exemples d'utilisation
- Logs d'entraînement pour débuggage

---

**Système prêt pour l'entraînement ! 🚀**

Utilisez :
```bash
python ai/train.py --config ai/configs/train_s3.yaml --device mps
```
