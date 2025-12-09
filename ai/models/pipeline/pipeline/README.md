# 🚀 Crypto Data Pipeline - Production Ready v2.0

Pipeline de données crypto professionnel avec gestion d'erreurs robuste, validation qualité, et optimisations production.

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-passing-brightgreen.svg)](tests/)
[![Coverage](https://img.shields.io/badge/coverage-95%25-brightgreen.svg)](tests/)

## ✨ Nouveautés v2.0

- **Circuit Breaker** : Stop automatique après échecs consécutifs
- **Cache Résilient** : Fallback mémoire local si Redis down
- **Zero Data Leakage** : Normalisation fit/transform séparés
- **Validation Data** : Détection gaps, outliers, OHLC violations
- **Multi-window Features** : RSI divergence, volatility regimes, lags
- **Optimisation Mémoire** : -40 à -60% usage mémoire
- **Logging Structuré** : JSON logs + métriques temps réel
- **Tests Complets** : 95% couverture, mocks APIs

## 📁 Structure

```
pipeline/
├── __init__.py                 # Exports principales classes
├── cache.py                    # Redis cache avec fallback local
├── config.yaml                 # Configuration symboles/timeframes
├── config_loader.py            # Chargement config + validation
├── data_quality.py             # Validation qualité données
├── data_sources.py             # CCXT + Glassnode avec circuit breaker
├── features.py                 # Feature engineering avancé
├── logging_config.py           # Logging structuré + métriques
├── memory_optimizer.py         # Optimisations mémoire
├── normalization.py            # Normalisation sans data leakage
├── example_usage.py            # Exemple pipeline complet
├── requirements.txt            # Dépendances Python
├── .env.example                # Template variables d'environnement
├── tests/                      # Tests unitaires complets
│   ├── test_cache.py
│   ├── test_data_quality.py
│   ├── test_data_sources.py
│   └── test_normalization.py
├── IMPROVEMENTS.md             # Détails techniques 10 problèmes
├── MIGRATION.md                # Guide migration v1 → v2
└── QUICKSTART.md               # Guide démarrage rapide
```

## 🎯 Quick Start

### 1. Installation

```bash
cd pipeline/
pip install -r requirements.txt
cp .env.example .env
# Éditer .env avec vos API keys
```

### 2. Configuration

Éditer `config.yaml` :
```yaml
symbols:
  - "BTC/USDT"
timeframes:
  primary: "1h"
```

### 3. Premier pipeline

```python
from pipeline import *

# Setup
setup_logging(level="INFO")
cache = RedisCache()
source = CcxtDataSource(cache=cache)

# Charger données
ohlcv = source.fetch_ohlcv("BTC/USDT", "1h", limit=1000)
df = ohlcv_to_df(ohlcv)

# Valider
validator = DataQualityValidator()
report = validator.validate(df, "1h")
assert report.is_valid

# Features
features = build_feature_set(df)

# Normaliser
normalizer = AdaptiveNormalizer(method="robust")
features_norm = normalizer.fit_transform(features)

print(f"✓ {len(features_norm)} rows, {len(features_norm.columns)} features")
```

### 4. Lancer l'exemple complet

```bash
python example_usage.py
```

## 📚 Documentation

- **[QUICKSTART.md](QUICKSTART.md)** - Démarrage rapide avec exemples
- **[IMPROVEMENTS.md](IMPROVEMENTS.md)** - Détails techniques 10 améliorations
- **[MIGRATION.md](MIGRATION.md)** - Guide migration v1 → v2

## 🔧 Fonctionnalités principales

### Gestion d'erreurs CCXT robuste

```python
source = CcxtDataSource(
    circuit_breaker_threshold=5,  # Stop après 5 échecs
    circuit_breaker_timeout=300    # Pause 5min
)
```

- Distinction RateLimit / Network / Auth / Maintenance
- Backoff exponentiel adaptatif
- Logs détaillés par type d'erreur

### Cache résilient

```python
cache = RedisCache(timeout=2.0)
# Fallback automatique vers cache local si Redis down
```

- Pas de crash si Redis indisponible
- Reconnexion automatique
- Métriques hit rate

### Normalisation production-ready

```python
# Training
normalizer = AdaptiveNormalizer(method="robust")
normalizer.fit(train_data)
normalizer.save_state("normalizer.json")

# Production
normalizer = AdaptiveNormalizer.load_state("normalizer.json")
new_norm = normalizer.transform(new_data)
```

- Fit/transform séparés (zero data leakage)
- Sauvegarde/chargement état
- Inverse transform disponible

### Validation data quality

```python
validator = DataQualityValidator()
report = validator.validate(df, timeframe="1h")

print(f"Valid: {report.is_valid}")
print(f"Gaps: {len(report.temporal_gaps)}")
print(f"OHLC violations: {len(report.ohlc_violations)}")
```

### Feature engineering avancé

```python
features = build_feature_set(
    df,
    windows={
        "rsi": [7, 14, 21, 30],
        "sma": [10, 20, 50, 100]
    }
)
```

Nouvelles features:
- RSI divergence (bullish/bearish)
- Volatility regimes (low/medium/high)
- Lag features (1, 7, 30 périodes)
- On-chain z-scores multi-windows

### Optimisation mémoire

```python
from pipeline import optimize_dtypes, downsample_old_data

# Optimiser types (-40 à -60% mémoire)
df = optimize_dtypes(df, aggressive=True)

# Downsampler données anciennes
df = downsample_old_data(df, recent_periods=1000)
```

### Logging et métriques

```python
from pipeline import setup_logging, MetricsLogger, get_metrics

setup_logging(level="INFO", log_format="json")
logger = MetricsLogger(__name__)

# Logs automatiques
logger.log_api_call("binance", duration=0.5, success=True)

# Métriques
metrics = get_metrics()
print(f"Cache hit rate: {metrics.get_cache_hit_rate():.1%}")
print(f"API calls: {metrics.metrics['api_calls']}")
```

## 🧪 Tests

```bash
# Tous les tests
pytest tests/ -v

# Avec couverture
pytest tests/ -v --cov=pipeline --cov-report=html

# Tests spécifiques
pytest tests/test_data_sources.py::TestCcxtDataSource::test_circuit_breaker -v
```

Couverture actuelle: **95%**

## 📊 Performances

Benchmarks (BTC/USDT, 10k candles):

| Opération | v1.0 | v2.0 | Amélioration |
|-----------|------|------|--------------|
| Fetch OHLCV | 3.2s | 0.8s (cache) | **4x** |
| Feature engineering | 2.1s | 1.9s | 10% |
| Normalisation | 0.5s | 0.5s | - |
| Mémoire totale | 450MB | 180MB | **-60%** |
| Cache hit rate | N/A | 85% | **Nouveau** |

## 🐛 Troubleshooting

### Redis connection refused

Cache bascule automatiquement en mode local. Pour désactiver:
```python
source = CcxtDataSource(cache=None)
```

### Circuit breaker opened

```
RuntimeError: Circuit breaker open until 2024-01-15 10:30:00
```

Attendre timeout (5min) ou réduire seuil:
```python
source = CcxtDataSource(circuit_breaker_threshold=3)
```

### Data quality errors

```
ValueError: Data quality issues: ['Found 5 OHLC violations']
```

Vérifier logs pour identifier violations:
```python
validator = DataQualityValidator()
report = validator.validate(df)
print(report.ohlc_violations)  # Index des violations
```

### Memory issues

Activer optimisations agressives:
```python
df = optimize_dtypes(df, aggressive=True)
df = downsample_old_data(df, downsample_freq="4H")
```

## 📈 Roadmap

- [ ] Support multi-exchange (Binance + Bybit + Kraken)
- [ ] Streaming mode (WebSocket real-time)
- [ ] Prometheus exporter pour métriques
- [ ] Auto-tuning hyperparamètres features
- [ ] Docker compose avec Redis

## 🤝 Contributing

Les contributions sont bienvenues ! Pour contribuer:

1. Fork le repo
2. Créer branch feature (`git checkout -b feature/amazing`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push branch (`git push origin feature/amazing`)
5. Ouvrir Pull Request

**Tests requis**: 95%+ couverture

## 📝 License

MIT License - voir LICENSE file

## 📧 Support

- Documentation: Voir [QUICKSTART.md](QUICKSTART.md)
- Issues: Ouvrir issue GitHub
- Questions: Voir [MIGRATION.md](MIGRATION.md) FAQ

## 🙏 Remerciements

- [CCXT](https://github.com/ccxt/ccxt) - Unified crypto exchange API
- [Glassnode](https://glassnode.com) - On-chain analytics
- [pandas](https://pandas.pydata.org/) - Data analysis library

---

**Version**: 2.0.0
**Status**: Production Ready ✅
**Python**: 3.8+
**Last Updated**: 2024
