# Entraînement avec données S3

Ce guide explique comment utiliser le système d'entraînement modifié pour charger des données historiques depuis AWS S3.

## Vue d'ensemble

Le système d'entraînement supporte maintenant deux sources de données :
- **S3** : Charge des données historiques multi-années depuis votre bucket S3
- **CCXT** : Comportement original utilisant les API des exchanges (Binance, etc.)

## Structure des données S3

Votre bucket S3 est organisé comme suit :

```
s3://qbia/bourse/mintrad/
├── klines_1m_TRADING_USDT_2017/
│   ├── BNBUSDT_2017_1m.parquet
│   ├── BTCUSDT_2017_1m.parquet
│   └── ...
├── klines_1m_TRADING_USDT_2018/
├── klines_1m_TRADING_USDT_2019/
├── ...
└── klines_1m_TRADING_USDT_2025/
```

Chaque fichier parquet contient des données klines 1-minute au format Binance (12 colonnes).

## Installation

1. Installer les dépendances :

```bash
cd ai/TRAIN
pip install -r requirements.txt
```

Les nouvelles dépendances ajoutées :
- `boto3` : SDK AWS pour Python
- `s3fs` : Système de fichiers S3
- `pyarrow` : Support des fichiers Parquet

2. Configurer les credentials AWS (si nécessaire) :

```bash
# Option 1: Variables d'environnement
export AWS_ACCESS_KEY_ID="votre_access_key"
export AWS_SECRET_ACCESS_KEY="votre_secret_key"
export AWS_DEFAULT_REGION="us-east-1"

# Option 2: Fichier ~/.aws/credentials
aws configure
```

## Utilisation

### 1. Tester le chargement des données S3

Avant de lancer un entraînement complet, testez que le chargement fonctionne :

```bash
cd /Users/christopher/Desktop/futur/ai
python test_s3_data_source.py
```

Ce script teste :
- Listage des années disponibles
- Listage des symboles disponibles
- Chargement d'un symbole simple
- Chargement multi-années
- Chargement multi-symboles

### 2. Entraînement avec données S3

```bash
cd /Users/christopher/Desktop/futur

# Entraînement avec configuration S3
python ai/train.py --config ai/configs/train_s3.yaml --device mps

# Avec mode debug (1 epoch, moins de batches)
python ai/train.py --config ai/configs/train_s3.yaml --device mps --debug_mode

# Sanity check rapide
python ai/train.py --config ai/configs/train_s3.yaml --device mps --fast_dev_run
```

### 3. Entraînement avec données CCXT (ancien comportement)

```bash
# Comportement original avec CCXT
python ai/train.py --config ai/configs/train_ccxt.yaml --device mps
```

## Configuration

### Configuration S3 ([ai/configs/train_s3.yaml](ai/configs/train_s3.yaml))

```yaml
data:
  data_source: "s3"              # Utiliser S3 comme source
  s3_bucket: "qbia"              # Nom du bucket S3
  s3_prefix: "bourse/mintrad"   # Préfixe dans le bucket
  start_year: 2020               # Année de début (inclusive)
  end_year: 2024                 # Année de fin (inclusive)

  # Filtrer les symboles (vide = tous les symboles)
  symbols_filter:
    - "BTCUSDT"
    - "ETHUSDT"
    - "BNBUSDT"
    # ... ajoutez d'autres symboles

  # Cache local pour éviter de re-télécharger
  local_cache_dir: "/tmp/trading_data_cache"

  # Paramètres de split
  train_split: 0.7
  val_split: 0.15
  test_split: 0.15

  # Paramètres de séquence
  lookback_window: 100
  feature_dim: 128
  batch_size: 32
```

### Paramètres importants

| Paramètre | Description | Valeur par défaut |
|-----------|-------------|-------------------|
| `data_source` | Source de données (`s3` ou `ccxt`) | `ccxt` |
| `s3_bucket` | Nom du bucket S3 | Requis si `data_source=s3` |
| `s3_prefix` | Préfixe dans le bucket | `bourse/mintrad` |
| `start_year` | Première année à charger | `2020` |
| `end_year` | Dernière année à charger | `2024` |
| `symbols_filter` | Liste des symboles (vide = tous) | `[]` |
| `local_cache_dir` | Répertoire de cache local | `None` (pas de cache) |

## Fonctionnalités

### Cache local

Le système peut mettre en cache les fichiers téléchargés localement pour éviter de les re-télécharger :

```yaml
data:
  local_cache_dir: "/tmp/trading_data_cache"
```

Lors du premier téléchargement, les fichiers sont sauvegardés dans ce répertoire. Les exécutions suivantes les chargeront depuis le cache.

### Chargement multi-années

Spécifiez une plage d'années pour charger :

```yaml
data:
  start_year: 2020
  end_year: 2024  # Charge 5 années de données (2020-2024)
```

### Filtrage de symboles

Deux options :

1. **Charger des symboles spécifiques** :
```yaml
data:
  symbols_filter:
    - "BTCUSDT"
    - "ETHUSDT"
```

2. **Charger tous les symboles disponibles** :
```yaml
data:
  symbols_filter: []  # ou omettez le paramètre
```

Le système découvrira automatiquement tous les symboles disponibles pour `end_year`.

## Pipeline de traitement

Le pipeline de données effectue les étapes suivantes :

1. **Téléchargement depuis S3** : Les fichiers parquet sont téléchargés (ou chargés depuis le cache)

2. **Conversion de format** : Les 12 colonnes numériques Binance sont converties en format standard :
   - `timestamp` : Datetime UTC
   - `open`, `high`, `low`, `close`, `volume` : Prix et volumes
   - `symbol` : Identifiant de la paire

3. **Feature engineering** : Génération de 40+ indicateurs techniques :
   - Returns et volatilité
   - Moyennes mobiles (SMA, EMA)
   - Bollinger Bands
   - RSI, MACD, Stochastic, CCI, MFI
   - ATR, ADX, OBV, VWAP
   - Caractéristiques des chandeliers

4. **Création de séquences** : Séquences temporelles de longueur `lookback_window`

5. **Split train/val/test** : Division selon les ratios configurés

6. **DataLoaders PyTorch** : Création des dataloaders pour l'entraînement

## Architecture des fichiers

Nouveaux fichiers créés :

```
ai/
├── TRAIN/
│   ├── data/
│   │   ├── pipeline.py          # Modifié pour supporter S3
│   │   └── s3_data_source.py    # Nouveau module S3
│   └── requirements.txt          # Modifié (ajout boto3, pyarrow)
├── configs/
│   ├── train_s3.yaml            # Config pour S3
│   └── train_ccxt.yaml          # Config pour CCXT
├── test_s3_data_source.py       # Script de test
└── README_S3_TRAINING.md        # Ce fichier
```

## Exemples d'utilisation

### Exemple 1 : Entraînement rapide avec 3 symboles (2024 seulement)

```yaml
# config_quick.yaml
data:
  data_source: "s3"
  s3_bucket: "qbia"
  s3_prefix: "bourse/mintrad"
  start_year: 2024
  end_year: 2024
  symbols_filter: ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
  local_cache_dir: "/tmp/trading_cache"
  batch_size: 64
  lookback_window: 50

training:
  epochs: 10
  debug_mode: true
```

```bash
python ai/train.py --config config_quick.yaml --device mps --debug_mode
```

### Exemple 2 : Entraînement complet avec toutes les données

```yaml
# config_full.yaml
data:
  data_source: "s3"
  s3_bucket: "qbia"
  start_year: 2017
  end_year: 2024
  symbols_filter: []  # Tous les symboles
  local_cache_dir: "/data/trading_cache"
  batch_size: 32
  lookback_window: 100

training:
  epochs: 100
  checkpoint_dir: "checkpoints_full"
```

```bash
python ai/train.py --config config_full.yaml --device mps
```

### Exemple 3 : Fine-tuning sur données récentes

```yaml
# config_recent.yaml
data:
  data_source: "s3"
  s3_bucket: "qbia"
  start_year: 2023
  end_year: 2024
  symbols_filter: ["BTCUSDT", "ETHUSDT"]
  batch_size: 16
  lookback_window: 200

training:
  epochs: 50
  learning_rate: 0.00001  # LR plus faible pour fine-tuning
```

## Dépannage

### Erreur : "s3_bucket must be specified when data_source='s3'"

Solution : Ajoutez `s3_bucket` dans votre config :
```yaml
data:
  s3_bucket: "qbia"
```

### Erreur : Credentials AWS manquantes

Solution : Configurez vos credentials AWS :
```bash
aws configure
```

### Performance lente

Solutions :
1. Activez le cache local :
   ```yaml
   data:
     local_cache_dir: "/tmp/trading_cache"
   ```

2. Réduisez le nombre de symboles ou d'années

3. Utilisez un batch size plus grand :
   ```yaml
   data:
     batch_size: 64
   ```

### Mémoire insuffisante

Solutions :
1. Réduisez `batch_size`
2. Réduisez `lookback_window`
3. Limitez les symboles/années
4. Activez `gradient_accumulation_steps` :
   ```yaml
   training:
     gradient_accumulation_steps: 8
   ```

## Performance

Temps de chargement approximatifs (avec cache local) :

| Configuration | Taille données | Temps de chargement |
|--------------|----------------|---------------------|
| 1 symbole, 1 an | ~500K lignes | ~2-3 secondes |
| 3 symboles, 1 an | ~1.5M lignes | ~5-8 secondes |
| 5 symboles, 3 ans | ~7.5M lignes | ~30-45 secondes |
| Tous symboles, 1 an | ~25M lignes | ~2-3 minutes |

Note : Le premier téléchargement sera plus long (téléchargement depuis S3). Les exécutions suivantes utiliseront le cache.

## Compatibilité

- Compatible avec l'ancien système CCXT (rétrocompatibilité complète)
- Utilise le même pipeline de features
- Même format de sortie (DataLoaders PyTorch)
- Aucune modification requise dans le code de training

## Support

Pour des questions ou problèmes :
1. Testez avec `test_s3_data_source.py`
2. Vérifiez les logs d'erreur
3. Utilisez `--debug_mode` pour un run léger
