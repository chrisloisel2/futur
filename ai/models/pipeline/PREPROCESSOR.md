# 🧪 Advanced Preprocessor - Documentation

Module de preprocessing avancé pour séries temporelles financières.

## 📋 Table des matières

- [Vue d'ensemble](#vue-densemble)
- [Composants](#composants)
- [Usage](#usage)
- [Théorie](#théorie)
- [Exemples](#exemples)
- [FAQ](#faq)

## Vue d'ensemble

Le module `preprocessor.py` implémente des techniques avancées de preprocessing pour machine learning sur données financières:

```
Raw Data → Interpolation → Frac Diff → Rolling Norm → Feature Selection → ML Ready
```

### Fonctionnalités clés

✅ **Stationnarisation** via différenciation fractionnaire (FI^d)
✅ **Feature selection** avec Mutual Information + BorutaPy
✅ **Normalisation rolling** (z-score 30 jours lookback)
✅ **Gestion NaN** avec interpolation temporelle
✅ **Split temporel** purged walk-forward CV
✅ **Test stationnarité** avec ADF (Augmented Dickey-Fuller)

## Composants

### 1. FractionalDifferentiator

Différenciation fractionnaire pour obtenir la stationnarité tout en préservant la mémoire.

**Théorie**:
- Différenciation classique (d=1): Stationnaire mais perte totale de mémoire
- Pas de différenciation (d=0): Mémoire complète mais non-stationnaire
- Différenciation fractionnaire (0 < d < 1): Compromis optimal

**Usage**:
```python
from pipeline import FractionalDifferentiator

diff = FractionalDifferentiator(d=0.5, threshold=1e-5)

# Apply
series_diff = diff.fit_transform(price_series)

# Test stationarity
result = diff.test_stationarity(series_diff)
print(f"Stationary: {result['is_stationary']}")
print(f"P-value: {result['p_value']:.4f}")
```

**Paramètres**:
- `d` (float): Ordre de différenciation (0-1). Plus élevé = plus stationnaire
- `threshold` (float): Seuil minimum pour les poids (efficacité computationnelle)

**Output**:
- Série différenciée fractionnellement
- Résultats ADF test

###

 2. RollingNormalizer

Normalisation z-score avec fenêtre glissante (pas de data leakage).

**Théorie**:
```
z = (x - μ_rolling) / σ_rolling
```

Utilise uniquement les données passées → Pas de look-ahead bias.

**Usage**:
```python
from pipeline import RollingNormalizer

normalizer = RollingNormalizer(window=30, min_periods=10)

# Apply
df_normalized = normalizer.fit_transform(df)
```

**Paramètres**:
- `window` (int): Fenêtre de lookback pour mean/std
- `min_periods` (int): Minimum d'observations requises

### 3. FeatureSelector

Sélection de features par Mutual Information et BorutaPy.

**Théorie**:
- **Mutual Information**: Mesure dépendance non-linéaire avec target
- **BorutaPy**: Wrapper method avec Random Forest

**Usage**:
```python
from pipeline import FeatureSelector

selector = FeatureSelector(
    target_col="target",
    mi_threshold=0.01,
    use_boruta=True
)

# Fit
selector.fit(X, y)

# Transform
X_selected = selector.transform(X)

# Check selected features
print(f"Selected: {selector.selected_features_}")
print(f"MI scores: {selector.mi_scores_}")
```

**Paramètres**:
- `target_col` (str): Nom de la colonne target
- `mi_threshold` (float): Seuil MI minimum
- `use_boruta` (bool): Utiliser BorutaPy
- `random_state` (int): Seed aléatoire

### 4. TemporalInterpolator

Interpolation temporelle pour gérer les NaN.

**Usage**:
```python
from pipeline import TemporalInterpolator

interpolator = TemporalInterpolator(
    method="time",  # ou 'linear', 'spline', 'polynomial'
    limit=5
)

df_clean = interpolator.fit_transform(df)
```

**Méthodes**:
- `time`: Interpolation basée sur timestamps
- `linear`: Interpolation linéaire
- `spline`: Spline cubique
- `polynomial`: Interpolation polynomiale

**Paramètres**:
- `method` (str): Méthode d'interpolation
- `limit` (int): Max NaN consécutifs à interpoler

### 5. PurgedWalkForward

Cross-validation temporelle avec purge pour éviter data leakage.

**Théorie**:
```
Split 1:  |===train===|--purge--|==test==|--embargo--|
Split 2:              |===train===|--purge--|==test==|--embargo--|
Split 3:                          |===train===|--purge--|==test==|
```

**Usage**:
```python
from pipeline import PurgedWalkForward

cv = PurgedWalkForward(
    n_splits=5,
    test_size=100,
    purge_gap=10,
    embargo_gap=5
)

for train_idx, test_idx in cv.split(df):
    X_train = X.iloc[train_idx]
    X_test = X.iloc[test_idx]
    # Train and evaluate...
```

**Paramètres**:
- `n_splits` (int): Nombre de splits
- `test_size` (int): Taille du test set
- `purge_gap` (int): Observations purgées avant test
- `embargo_gap` (int): Observations embargées après test

### 6. AdvancedPreprocessor

Pipeline complet combinant tous les composants.

**Usage**:
```python
from pipeline import AdvancedPreprocessor

preprocessor = AdvancedPreprocessor(
    target_col="target",
    frac_diff_d=0.5,
    rolling_window=30,
    mi_threshold=0.01,
    use_boruta=True,
    interpolation_method="time",
    test_stationarity=True
)

# Full pipeline
df_processed = preprocessor.fit_transform(df)

# Get CV splits
cv = preprocessor.get_cv_splits(df_processed, n_splits=5)
```

## Usage

### Exemple complet

```python
from pipeline import AdvancedPreprocessor, build_feature_set
from pipeline import CcxtDataSource, ohlcv_to_df
from datetime import datetime, timedelta

# 1. Load data
source = CcxtDataSource()
ohlcv = source.fetch_historical_range(
    "BTC/USDT", "1h",
    start=datetime.now() - timedelta(days=60),
    end=datetime.now()
)
df = ohlcv_to_df(ohlcv)

# 2. Build features
features = build_feature_set(df)
features = features.reset_index()

# 3. Create target
features["target"] = features["close"].pct_change().shift(-1)
features = features.dropna(subset=["target"])
features = features.set_index("timestamp")

# 4. Advanced preprocessing
preprocessor = AdvancedPreprocessor(
    target_col="target",
    frac_diff_d=0.5,
    rolling_window=30,
    mi_threshold=0.01
)

df_processed = preprocessor.fit_transform(features)

# 5. Train/test split with CV
X = df_processed.drop(columns=["target"])
y = df_processed["target"]

cv = preprocessor.get_cv_splits(df_processed, n_splits=5)

for train_idx, test_idx in cv.split(df_processed):
    X_train, y_train = X.iloc[train_idx], y.iloc[train_idx]
    X_test, y_test = X.iloc[test_idx], y.iloc[test_idx]

    # Train model...
    # model.fit(X_train, y_train)
    # predictions = model.predict(X_test)
```

### Exemple minimal

```python
from pipeline import AdvancedPreprocessor

# Assuming you have a DataFrame with features + target
preprocessor = AdvancedPreprocessor(target_col="target")
df_ready = preprocessor.fit_transform(df)

print(f"Original: {df.shape}")
print(f"Processed: {df_ready.shape}")
print(f"Selected features: {len(preprocessor.selected_features_)}")
```

## Théorie

### Différenciation fractionnaire

**Formule**:
```
Δ^d X_t = Σ_{k=0}^∞ w_k X_{t-k}
```

Où les poids sont:
```
w_0 = 1
w_k = -w_{k-1} * (d - k + 1) / k
```

**Avantages**:
- Stationnarité sans perte complète de mémoire
- Paramètre d contrôle le trade-off
- Basé sur "Advances in Financial ML" (Lopez de Prado)

### ADF Test (Augmented Dickey-Fuller)

**Hypothèses**:
- H0: Série non-stationnaire (unit root)
- H1: Série stationnaire

**Interprétation**:
- p-value < 0.05: Rejeter H0 → Stationnaire ✅
- p-value > 0.05: Ne pas rejeter H0 → Non-stationnaire ❌

### Purged Walk-Forward CV

**Problème**: Overlap entre train/test crée data leakage

**Solution**:
1. **Purge gap**: Retire observations avant test
2. **Embargo gap**: Retire observations après test
3. **Walk-forward**: Test toujours après train

**Bénéfices**:
- Évalue performance out-of-sample réaliste
- Évite overfitting sur séries temporelles
- Simule conditions de trading réelles

## Exemples

### 1. Tester différents ordres de différenciation

```python
from pipeline import FractionalDifferentiator

for d in [0.3, 0.5, 0.7, 0.9]:
    diff = FractionalDifferentiator(d=d)
    series_diff = diff.fit_transform(price_series)

    result = diff.test_stationarity(series_diff)
    print(f"d={d}: p-value={result['p_value']:.4f}, "
          f"stationary={result['is_stationary']}")
```

### 2. Feature selection avec BorutaPy

```python
from pipeline import FeatureSelector

selector = FeatureSelector(
    target_col="return",
    mi_threshold=0.01,
    use_boruta=True,
    boruta_n_estimators=100
)

X_selected = selector.fit_transform(X, y)

# Top features
top_features = selector.mi_scores_.nlargest(10)
print(top_features)
```

### 3. Custom CV avec métriques

```python
from pipeline import PurgedWalkForward
from sklearn.metrics import mean_squared_error

cv = PurgedWalkForward(n_splits=5, test_size=100)

scores = []
for train_idx, test_idx in cv.split(df):
    X_train, y_train = X.iloc[train_idx], y.iloc[train_idx]
    X_test, y_test = X.iloc[test_idx], y.iloc[test_idx]

    model.fit(X_train, y_train)
    y_pred = model.predict(X_test)

    mse = mean_squared_error(y_test, y_pred)
    scores.append(mse)

print(f"CV MSE: {np.mean(scores):.4f} ± {np.std(scores):.4f}")
```

## FAQ

### Q: Quel ordre d pour la différenciation fractionnaire?

**A**: Commencer avec `d=0.5`, puis ajuster:
- d=0.3-0.4: Conservation maximum de mémoire
- d=0.5-0.6: Bon compromis (recommandé)
- d=0.7-0.9: Stationnarité maximale

Tester avec ADF test et choisir le plus petit d qui donne p-value < 0.05.

### Q: BorutaPy est lent, comment accélérer?

**A**: Plusieurs options:
```python
selector = FeatureSelector(
    use_boruta=False,  # Utiliser MI uniquement
    mi_threshold=0.05   # Augmenter seuil
)
```

Ou réduire les hyperparamètres Boruta:
```python
selector = FeatureSelector(
    boruta_n_estimators=50,  # Au lieu de 100
    boruta_max_depth=3       # Au lieu de 5
)
```

### Q: Combien de splits pour le CV?

**A**: Dépend de la taille des données:
- 1000 samples: n_splits=3-5
- 5000 samples: n_splits=5-10
- 10000+ samples: n_splits=10-20

Toujours vérifier que test_size est suffisant (min 50-100 observations).

### Q: Que faire si trop de features sélectionnées?

**A**: Augmenter seuils:
```python
preprocessor = AdvancedPreprocessor(
    mi_threshold=0.05,  # Au lieu de 0.01
    use_boruta=True     # Boruta filtre encore plus
)
```

Ou filtrer manuellement après:
```python
# Garder top 20 features
top_features = preprocessor.feature_selector.mi_scores_.nlargest(20).index
X_top = X[top_features]
```

### Q: Comment gérer les NaN aux bords?

**A**: L'interpolateur fait forward/backward fill:
```python
interpolator = TemporalInterpolator(
    method="time",
    limit=5  # Max 5 NaN consécutifs
)
```

Ou simplement drop:
```python
df = df.dropna()
```

### Q: La normalisation rolling crée-t-elle du data leakage?

**A**: Non! Elle utilise uniquement les données passées:
```python
# Pour le point t, utilise [t-window : t]
rolling_mean = series.rolling(window=30).mean()
```

Contrairement à:
```python
# LEAKAGE: utilise toutes les données
mean = series.mean()  # Inclut le futur!
```

### Q: Purge gap vs Embargo gap?

**A**:
- **Purge gap**: Avant test (overlap features)
- **Embargo gap**: Après test (empêche train sur target futur)

```
|===train===|--purge--|==test==|--embargo--|
            ↑          ↑         ↑
         overlap   prevent   prevent
                   leakage   leakage
```

## Performance

### Benchmarks

Testé sur 60 jours de données BTC hourly (~1440 samples):

| Étape | Temps |
|-------|-------|
| Interpolation | 0.1s |
| Frac Diff | 0.5s |
| Rolling Norm | 0.3s |
| MI Feature Selection | 1.2s |
| BorutaPy (100 features → 20) | 30s |
| **Total** | **~32s** |

### Optimisations

Pour accélérer:
1. Désactiver BorutaPy: `-28s`
2. Réduire window rolling: `window=15` vs `30`
3. Réduire n_estimators Boruta: `50` vs `100`

## Références

- Lopez de Prado, M. (2018). "Advances in Financial Machine Learning"
- Hosking, J. R. M. (1981). "Fractional differencing"
- Kursa, M. B., & Rudnicki, W. R. (2010). "Feature selection with Boruta"
- Dickey, D. A., & Fuller, W. A. (1979). "Distribution of estimators for AR time series"

---

**Version**: 2.1.0
**Module**: `preprocessor.py`
**Tests**: `tests/test_preprocessor.py`
**Example**: `example_preprocessor.py`
