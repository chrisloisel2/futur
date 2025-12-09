# 🚀 Quick Start Guide

Guide de démarrage rapide pour utiliser le pipeline amélioré.

## Installation

```bash
cd pipeline/

# Installer les dépendances
pip install -r requirements.txt

# Copier le fichier d'environnement
cp .env.example .env

# Éditer .env avec vos API keys
nano .env
```

## Configuration

Éditer `config.yaml` pour vos besoins:

```yaml
symbols:
  - "BTC/USDT"
  - "ETH/USDT"

timeframes:
  primary: "1h"

normalization:
  method: "robust"  # ou "standard"
```

## Lancer l'exemple

```bash
python example_usage.py
```

## Tests

```bash
# Tous les tests
pytest tests/ -v

# Avec couverture
pytest tests/ -v --cov=pipeline --cov-report=html

# Tests spécifiques
pytest tests/test_data_sources.py -v
```

## Usage basique

### 1. Récupérer des données OHLCV

```python
from data_sources import CcxtDataSource, ohlcv_to_df
from cache import RedisCache

cache = RedisCache()
source = CcxtDataSource(cache=cache)

ohlcv = source.fetch_historical_range(
    symbol="BTC/USDT",
    timeframe="1h",
    start=datetime(2024, 1, 1),
    end=datetime(2024, 1, 7)
)

df = ohlcv_to_df(ohlcv)
print(df.head())
```

### 2. Valider la qualité des données

```python
from data_quality import DataQualityValidator

validator = DataQualityValidator()
report = validator.validate(df, timeframe="1h")

if not report.is_valid:
    print(f"Errors: {report.errors}")
    print(f"Warnings: {report.warnings}")
```

### 3. Créer des features

```python
from features import build_feature_set

features = build_feature_set(
    df,
    windows={
        "rsi": [14, 21],
        "sma": [20, 50],
    }
)

print(f"Created {len(features.columns)} features")
```

### 4. Normaliser (entraînement)

```python
from normalization import AdaptiveNormalizer

normalizer = AdaptiveNormalizer(method="robust")

# Fit sur données d'entraînement
normalizer.fit(train_data)

# Transform
train_norm = normalizer.transform(train_data)
test_norm = normalizer.transform(test_data)

# Sauvegarder pour production
normalizer.save_state("models/normalizer.json")
```

### 5. Normaliser (production)

```python
from normalization import AdaptiveNormalizer

# Charger le normalizer sauvegardé
normalizer = AdaptiveNormalizer.load_state("models/normalizer.json")

# Transformer nouvelles données
new_data_norm = normalizer.transform(new_data)
```

### 6. Optimiser la mémoire

```python
from memory_optimizer import optimize_dtypes, downsample_old_data

# Optimiser les types
df = optimize_dtypes(df, aggressive=True)

# Downsampler anciennes données
df = downsample_old_data(
    df,
    recent_periods=1000,
    downsample_freq="1D"
)
```

### 7. Logging et métriques

```python
from logging_config import setup_logging, MetricsLogger, get_metrics

# Setup
setup_logging(level="INFO", log_format="json")

# Logger
logger = MetricsLogger(__name__)
logger.log_api_call("binance", duration=0.5, success=True)

# Métriques
metrics = get_metrics()
print(f"Cache hit rate: {metrics.get_cache_hit_rate():.1%}")
print(metrics.summary())
```

## Patterns courants

### Pipeline complet (training)

```python
from datetime import datetime, timedelta
from data_sources import CcxtDataSource, ohlcv_to_df
from features import build_feature_set
from normalization import AdaptiveNormalizer
from data_quality import DataQualityValidator

# 1. Fetch
source = CcxtDataSource()
ohlcv = source.fetch_historical_range("BTC/USDT", "1h", start, end)
df = ohlcv_to_df(ohlcv)

# 2. Validate
validator = DataQualityValidator()
report = validator.validate(df, "1h")
assert report.is_valid, f"Data issues: {report.errors}"

# 3. Features
features = build_feature_set(df, drop_na=True)

# 4. Split
split = int(len(features) * 0.8)
train = features.iloc[:split]
test = features.iloc[split:]

# 5. Normalize
normalizer = AdaptiveNormalizer()
train_norm = normalizer.fit_transform(train)
test_norm = normalizer.transform(test)

# 6. Save
normalizer.save_state("normalizer.json")
train_norm.to_parquet("train_features.parquet")
test_norm.to_parquet("test_features.parquet")
```

### Pipeline production (inference)

```python
from data_sources import CcxtDataSource, ohlcv_to_df
from features import build_feature_set
from normalization import AdaptiveNormalizer

# 1. Fetch latest data
source = CcxtDataSource()
ohlcv = source.fetch_ohlcv("BTC/USDT", "1h", limit=500)
df = ohlcv_to_df(ohlcv)

# 2. Features
features = build_feature_set(df, drop_na=True)

# 3. Load normalizer
normalizer = AdaptiveNormalizer.load_state("normalizer.json")

# 4. Transform
features_norm = normalizer.transform(features)

# 5. Predict
# predictions = model.predict(features_norm)
```

## Troubleshooting

### Redis connection refused

Le cache bascule automatiquement en mode local. Pour désactiver Redis:

```python
source = CcxtDataSource(cache=None)
```

### Circuit breaker opened

Attendre le timeout ou réduire le seuil:

```python
source = CcxtDataSource(
    circuit_breaker_threshold=3,
    circuit_breaker_timeout=60
)
```

### Memory issues

Activer optimisations agressives:

```python
from memory_optimizer import optimize_dtypes

df = optimize_dtypes(df, aggressive=True)
```

### Data quality errors

Vérifier logs et ajuster seuils:

```python
validator = DataQualityValidator(
    max_gap_multiplier=3.0,  # Plus tolérant
    volatility_threshold=20.0
)
```

## Best Practices

1. **Toujours valider les données** avant le feature engineering
2. **Séparer fit/transform** pour éviter data leakage
3. **Sauvegarder l'état du normalizer** avant production
4. **Monitor cache hit rate** (objectif > 70%)
5. **Logger en JSON** pour production
6. **Utiliser méthode "robust"** pour normalisation (plus résistant outliers)
7. **Downsampler** données anciennes si > 10k rows
8. **Tester avec pytest** avant déploiement

## Next Steps

- Lire [IMPROVEMENTS.md](IMPROVEMENTS.md) pour détails techniques
- Adapter [config.yaml](config.yaml) à vos symboles
- Lancer [example_usage.py](example_usage.py) pour voir le pipeline complet
- Implémenter votre modèle ML avec les features normalisées
- Setup monitoring (Prometheus/Grafana) avec les métriques

## Support

Pour questions ou issues:
1. Vérifier logs: `tail -f pipeline.log`
2. Vérifier métriques: `get_metrics().summary()`
3. Lancer tests: `pytest tests/ -v`
