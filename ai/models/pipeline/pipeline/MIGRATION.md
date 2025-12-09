# 🔄 Migration Guide v1.0 → v2.0

Guide de migration du pipeline v1 vers la version production-ready v2.

## Changements Breaking

### 1. AdaptiveNormalizer

**Avant (v1.0)**:
```python
normalizer = AdaptiveNormalizer()
normalized = normalizer.fit_transform(df)
```

**Après (v2.0)**:
```python
normalizer = AdaptiveNormalizer(method="robust")

# Training
normalizer.fit(train_df)
train_normalized = normalizer.transform(train_df)
test_normalized = normalizer.transform(test_df)

# Sauvegarder
normalizer.save_state("normalizer.json")

# Production
normalizer = AdaptiveNormalizer.load_state("normalizer.json")
new_normalized = normalizer.transform(new_df)
```

**Raison**: Éviter data leakage + permettre réutilisation en production

---

### 2. build_feature_set

**Avant (v1.0)**:
```python
features = build_feature_set(df, onchain_column="value")
```

**Après (v2.0)**:
```python
features = build_feature_set(
    df,
    onchain_column="onchain_value",  # Changement de nom
    windows={
        "rsi": [7, 14, 21, 30],  # Multiples windows
        "sma": [10, 20, 50]
    }
)
```

**Nouvelles features**:
- RSI divergence
- Volatility regimes
- Lag features (1, 7, 30)
- On-chain z-scores multiples

---

### 3. CcxtDataSource

**Avant (v1.0)**:
```python
source = CcxtDataSource(exchange, cache)
```

**Après (v2.0)**:
```python
source = CcxtDataSource(
    exchange=exchange,
    cache=cache,
    circuit_breaker_threshold=5,  # Nouveau
    circuit_breaker_timeout=300    # Nouveau
)
```

**Gestion erreurs améliorée**:
- Circuit breaker automatique
- Distinction types d'erreurs
- Pause maintenance mode
- Backoff exponentiel

---

### 4. RedisCache

**Avant (v1.0)**:
```python
cache = RedisCache(url="redis://localhost:6379/0")
value = cache.get_json(key)
```

**Après (v2.0)**:
```python
cache = RedisCache(
    url="redis://localhost:6379/0",
    timeout=2.0,              # Nouveau
    reconnect_backoff=1.0     # Nouveau
)

# Même API mais avec fallback automatique
value = cache.get_json(key)  # Fallback local si Redis down
```

**Nouveautés**:
- Fallback cache mémoire local
- Reconnexion automatique
- Timeouts configurables
- Pas de crash si Redis indisponible

---

## Nouvelles fonctionnalités

### 1. Configuration externalisée

**Créer `config.yaml`**:
```yaml
symbols:
  - "BTC/USDT"
timeframes:
  primary: "1h"
normalization:
  method: "robust"
```

**Créer `.env`**:
```bash
GLASSNODE_API_KEY=your_key_here
```

**Utiliser**:
```python
from pipeline import get_config

config = get_config()
symbols = config.get("symbols")
api_key = config.get_env("GLASSNODE_API_KEY")
```

---

### 2. Validation data quality

**Nouveau**:
```python
from pipeline import DataQualityValidator

validator = DataQualityValidator()
report = validator.validate(df, timeframe="1h")

if not report.is_valid:
    print(f"Errors: {report.errors}")
    # Arrêter ou corriger les données
```

**Checks effectués**:
- Gaps temporels
- Violations OHLC (high < low)
- Outliers extrêmes
- Volatility spikes

---

### 3. Optimisation mémoire

**Nouveau**:
```python
from pipeline import optimize_dtypes, downsample_old_data

# Optimiser types
df = optimize_dtypes(df, aggressive=True)
# Réduction: 40-60%

# Downsampler anciennes données
df = downsample_old_data(df, recent_periods=1000, downsample_freq="1D")
```

---

### 4. Logging structuré

**Nouveau**:
```python
from pipeline import setup_logging, MetricsLogger, get_metrics

# Setup
setup_logging(level="INFO", log_format="json", log_file="pipeline.log")

# Logger
logger = MetricsLogger(__name__)
logger.log_api_call("binance", duration=0.5, success=True)

# Métriques
metrics = get_metrics()
print(f"Cache hit rate: {metrics.get_cache_hit_rate():.1%}")
```

---

### 5. Gestion timezone UTC

**Automatique** - Plus besoin de gérer manuellement:
```python
df = ohlcv_to_df(ohlcv)
# df["timestamp"] est automatiquement timezone-aware UTC

merged = merge_onchain_asof(ohlcv_df, onchain_df)
# Conversion automatique vers UTC si nécessaire
```

---

## Plan de migration étape par étape

### Étape 1: Installation

```bash
cd pipeline/
pip install -r requirements.txt
cp .env.example .env
# Éditer .env avec vos API keys
```

### Étape 2: Configuration

Créer `config.yaml` avec vos paramètres:
```yaml
symbols:
  - "BTC/USDT"
  - "ETH/USDT"

timeframes:
  primary: "1h"

normalization:
  method: "robust"
  window: 500

features:
  windows:
    rsi: [7, 14, 21, 30]
    sma: [10, 20, 50, 100]
```

### Étape 3: Migrer le code data loading

**Avant**:
```python
source = CcxtDataSource(exchange)
ohlcv = source.fetch_ohlcv("BTC/USDT", "1h")
df = ohlcv_to_df(ohlcv)
```

**Après**:
```python
from pipeline import CcxtDataSource, ohlcv_to_df, DataQualityValidator

cache = RedisCache()  # Ajout cache
source = CcxtDataSource(cache=cache)

ohlcv = source.fetch_ohlcv("BTC/USDT", "1h")
df = ohlcv_to_df(ohlcv)

# Nouveau: validation
validator = DataQualityValidator()
report = validator.validate(df, "1h")
assert report.is_valid
```

### Étape 4: Migrer feature engineering

**Avant**:
```python
features = build_feature_set(df)
```

**Après**:
```python
from pipeline import build_feature_set

features = build_feature_set(
    df,
    windows={
        "rsi": [14, 21],
        "sma": [20, 50]
    }
)
# Nouvelles features: divergence, regimes, lags
```

### Étape 5: Migrer normalisation

**Avant**:
```python
normalizer = AdaptiveNormalizer()
data_normalized = normalizer.fit_transform(data)
```

**Après**:
```python
from pipeline import AdaptiveNormalizer

normalizer = AdaptiveNormalizer(method="robust")

# Séparer fit et transform
normalizer.fit(train_data)
train_norm = normalizer.transform(train_data)
test_norm = normalizer.transform(test_data)

# Sauvegarder pour production
normalizer.save_state("models/normalizer.json")
```

### Étape 6: Ajouter logging

**Nouveau**:
```python
from pipeline import setup_logging, MetricsLogger

setup_logging(level="INFO", log_format="json")
logger = MetricsLogger(__name__)

# Dans votre code
with logger.metrics.timer("feature_engineering"):
    features = build_feature_set(df)

logger.log_metrics_summary()
```

### Étape 7: Ajouter optimisation mémoire (optionnel)

**Si > 10k rows**:
```python
from pipeline import optimize_dtypes, downsample_old_data

df = optimize_dtypes(df, aggressive=True)

if len(df) > 10000:
    df = downsample_old_data(df, recent_periods=1000)
```

### Étape 8: Tests

```bash
# Lancer les tests
pytest pipeline/tests/ -v

# Avec couverture
pytest pipeline/tests/ -v --cov=pipeline
```

---

## Checklist migration complète

- [ ] Installer nouvelles dépendances (`pip install -r requirements.txt`)
- [ ] Créer `.env` avec API keys
- [ ] Créer `config.yaml` avec vos symboles
- [ ] Migrer `AdaptiveNormalizer` (séparer fit/transform)
- [ ] Ajouter `DataQualityValidator` après chargement données
- [ ] Ajouter gestion circuit breaker à `CcxtDataSource`
- [ ] Migrer cache Redis avec fallback
- [ ] Ajouter windows multiples à `build_feature_set`
- [ ] Setup logging structuré
- [ ] Ajouter optimisation mémoire si gros datasets
- [ ] Lancer tests pour valider
- [ ] Sauvegarder normalizer state pour production

---

## Code patterns recommandés

### Pattern 1: Training pipeline

```python
from pipeline import *

# Setup
setup_logging(level="INFO")
config = get_config()

# Load
source = CcxtDataSource(cache=RedisCache())
ohlcv = source.fetch_historical_range(symbol, timeframe, start, end)
df = ohlcv_to_df(ohlcv)

# Validate
validator = DataQualityValidator()
report = validator.validate(df, timeframe)
assert report.is_valid

# Features
features = build_feature_set(df)

# Split
train, test = train_test_split(features)

# Normalize
normalizer = AdaptiveNormalizer(method="robust")
train_norm = normalizer.fit_transform(train)
test_norm = normalizer.transform(test)

# Save
normalizer.save_state("normalizer.json")
```

### Pattern 2: Production pipeline

```python
from pipeline import *

# Setup
setup_logging(level="INFO", log_format="json")

# Load latest data
source = CcxtDataSource(cache=RedisCache())
ohlcv = source.fetch_ohlcv(symbol, timeframe, limit=500)
df = ohlcv_to_df(ohlcv)

# Features
features = build_feature_set(df)

# Load normalizer
normalizer = AdaptiveNormalizer.load_state("normalizer.json")
features_norm = normalizer.transform(features)

# Predict
predictions = model.predict(features_norm)
```

---

## Rollback (si problèmes)

Si besoin de revenir à v1:

```bash
git checkout v1.0
pip install -r requirements_v1.txt
```

Ou utiliser l'ancien code en parallèle:
```python
# Garder v1 dans old_pipeline/
from old_pipeline import AdaptiveNormalizer as OldNormalizer
```

---

## Questions fréquentes

**Q: Dois-je refitter le normalizer après migration ?**
R: Oui, les paramètres ont changé (méthode robust vs standard). Retrain recommandé.

**Q: Redis est-il obligatoire ?**
R: Non, le cache bascule automatiquement en mode local si Redis absent.

**Q: Les anciennes features sont-elles compatibles ?**
R: Oui, mais les nouvelles features (divergence, regimes) apportent de la valeur.

**Q: Temps de migration estimé ?**
R: 1-2h pour un pipeline simple, 1 journée pour pipeline complexe.

**Q: Performance impact ?**
R: Amélioration générale grâce au cache et optimisations mémoire.

---

## Support

En cas de problème:
1. Consulter les logs: `tail -f pipeline.log`
2. Lancer les tests: `pytest tests/ -v`
3. Vérifier métriques: `get_metrics().summary()`
4. Lire [IMPROVEMENTS.md](IMPROVEMENTS.md) pour détails techniques
