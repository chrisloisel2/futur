# 📊 S3 Data Integration - Trading System

**Date**: 28 Décembre 2024
**Status**: ✅ **FONCTIONNEL**

---

## 🎯 Intégration Complétée

Le trading system est maintenant connecté aux **vraies données de marché** stockées dans S3.

### Données Disponibles

#### 📍 Source de Données PROCESSED (Recommandée)

```
s3://qbia/bourse/processed/market/
Structure: interval=1m/quote=USDT/symbol={SYMBOL}/year={YEAR}/*.zstd.parquet
```

**Contenu** :
- **OHLCV de base** : Open, High, Low, Close, Volume
- **Indicateurs techniques** : EMA (20/50/100/200), RSI, ATR
- **Volatilité** : RV (realized volatility) sur multiples horizons
- **Risk metrics** : VaR, CVaR (99%)
- **Labels** : `label_policy`, `label_tradeable`
- **Total** : 70 colonnes enrichies

#### 📍 Alternative RAW (Non utilisée)

```
s3://qbia/bourse/raw/market/
Structure: interval=1m/quote=USDT/symbol={SYMBOL}/year={YEAR}/*.parquet
```

**Contenu** : OHLCV basique uniquement (16 colonnes)

---

## 🔧 Fichiers Créés

### 1. S3 Data Loader

**Fichier** : [src/infra/data/s3_loader.py](src/infra/data/s3_loader.py)

```python
from infra.data.s3_loader import S3MarketDataLoader, normalize_columns

loader = S3MarketDataLoader()
df = loader.load("BTCUSDT", "2024-01-01", "2024-12-01")
df = normalize_columns(df)  # Normalize column names
```

**Features** :
- Chargement automatique par année
- Filtrage par date avec timezone support
- Normalisation des noms de colonnes
- Logging détaillé

### 2. Main.py Mis à Jour

**Fichier** : [src/app/main.py](src/app/main.py:45-182)

Ajout du paramètre `use_real_data=True` (par défaut) :

```python
def run_backtest(
    config_path: str,
    start_date: str,
    end_date: str,
    symbols: list[str],
    use_real_data: bool = True,  # NEW
) -> int:
    # Load from S3 if use_real_data=True
    # Use mock data if use_real_data=False
```

### 3. Scripts de Lancement

**Fichier** : [backtest_real_data.sh](backtest_real_data.sh)

```bash
./backtest_real_data.sh
```

Lance un backtest complet sur 11 mois de données BTCUSDT (2024).

---

## 📊 Résultats Backtest (Stratégie Simplifiée)

### Test Initial : BTCUSDT 2024-01-01 → 2024-12-01

```json
{
  "trades": 1918,
  "gross_pnl": -65710.23,
  "net_pnl": -197229.03,
  "total_costs": 131518.80,
  "sharpe_ratio": -2.36,
  "win_rate": 38.9%,
  "profit_factor": 0.63
}
```

### ❌ Assessment : FAILED

**Problèmes identifiés** :

1. **Stratégie trop simpliste**
   ```python
   # Momentum naïf : acheter si prix monte
   signal = (close > close.shift(1))
   ```
   - Win rate 38.9% (très mauvais)
   - Pas de filtrage de qualité
   - Aucun stop-loss

2. **Coûts élevés**
   - $131k de coûts sur 1918 trades
   - ~$68 par trade en moyenne
   - Mode TAKER = 10 bps fees

3. **Pas de risk management**
   - Position size fixe (1.0 BTC)
   - Pas de Kelly sizing
   - Pas de killswitch

---

## 🎯 Prochaines Étapes

### Phase 1 : Pipeline Complet (URGENT)

Actuellement le backtest utilise une stratégie simplifiée. **Il faut implémenter le pipeline complet** :

```python
# TODO dans main.py ligne 94-134
# Remplacer par :

# 1. Features Factory
features = FeatureFactory().build(df)

# 2. Regime Classifier
regimes = RegimeClassifierModel().predict(features)

# 3. Edge Forecaster
edge = EdgeForecasterModel().predict(features)

# 4. Decision Logic
signals = DecisionLogic().apply(edge, regimes)

# 5. Risk Controller
targets = RiskController().process(signals)

# 6. Generate orders
orders = OrderPlan().from_targets(targets)
```

### Phase 2 : Calibration & Optimization

1. **Grid Search sur Seuils**
   ```python
   for min_score in [0.55, 0.60, 0.65, 0.70]:
       for min_confidence in [0.45, 0.50, 0.55, 0.60]:
           results = backtest(min_score, min_confidence)
           # Optimize for Sharpe > 1.5
   ```

2. **Walk-Forward Validation**
   - Train: 6 mois
   - Test: 1 mois
   - Roll forward

3. **Multi-Symbol Backtest**
   ```bash
   ./backtest_real_data.sh --symbols BTCUSDT ETHUSDT SOLUSDT
   ```

### Phase 3 : Advanced Features

1. **MAKER mode** (2 bps vs 10 bps)
2. **Dynamic position sizing** (Kelly criterion)
3. **Trailing stops**
4. **Correlation-aware allocation**

---

## 💡 Analyse des Données S3

### Période Disponible

```python
# BTCUSDT processed data
2019-2024 : 5+ ans de données
~500k rows par an (1min candles)
```

### Colonnes Clés

| Catégorie | Colonnes | Usage |
|-----------|----------|-------|
| **OHLCV** | open, high, low, close, volume | Prix et volume |
| **Volatility** | rv_5, rv_15, rv_60, rv_240 | Realized vol (5min → 4h) |
| **EMA** | ema_20, ema_50, ema_100, ema_200 | Trend detection |
| **EMA Slopes** | ema_20_slope_5, ema_50_slope_20 | Momentum |
| **RSI** | rsi_14, rsi_slope_5 | Overbought/oversold |
| **Risk** | var_99_60, cvar_99_60 | VaR/CVaR metrics |
| **Labels** | label_policy, label_tradeable | Pour ML training |

### Qualité des Données

✅ **Avantages** :
- Nettoyées et validées
- Features pré-calculées (gain de temps)
- Labels pour ML supervisé
- Compression efficace (.zstd)

⚠️ **Attention** :
- Timezone UTC (géré dans le loader)
- Noms de colonnes capitalisés (normalisés automatiquement)
- Gaps possibles (vérifier avec quality_flags)

---

## 🚀 Utilisation

### Backtest avec Données Réelles

```bash
# Méthode 1: Script helper
./backtest_real_data.sh

# Méthode 2: Commande directe
PYTHONPATH="$(pwd)/src:$PYTHONPATH" python -m src.app.main backtest \
  --start-date 2024-01-01 \
  --end-date 2024-12-01 \
  --symbols BTCUSDT \
  --config configs/base.yaml
```

### Backtest avec Mock Data (testing)

```python
# Dans main.py, passer use_real_data=False
run_backtest(config, start, end, symbols, use_real_data=False)
```

### Voir les Résultats

```bash
python view_results.py
```

---

## 🔍 Dépannage

### Problème 1: Timezone Error

**Erreur** :
```
TypeError: Invalid comparison between dtype=datetime64[ns, UTC] and Timestamp
```

**Fix** : Appliqué dans [s3_loader.py:111-115](src/infra/data/s3_loader.py#L111-L115)
```python
if df['datetime'].dt.tz is not None:
    start = start.tz_localize('UTC')
    end = end.tz_localize('UTC')
```

### Problème 2: AWS Credentials

**Erreur** :
```
botocore.exceptions.NoCredentialsError
```

**Fix** :
```bash
aws configure
# Ou configurer ~/.aws/credentials
```

### Problème 3: ModuleNotFoundError

**Erreur** :
```
ModuleNotFoundError: No module named 's3fs'
```

**Fix** :
```bash
python -m pip install s3fs pyarrow
```

---

## 📝 TODO Prioritaire

### 🔴 Critiques (Semaine 1)

- [ ] **Implémenter pipeline complet** dans main.py
  - Features → Regime → Edge → Decision → Risk → Orders
- [ ] **Tester avec pipeline** sur 2024 data
- [ ] **Vérifier Sharpe > 1.0** avant d'optimiser

### 🟡 Importantes (Semaine 2)

- [ ] Grid search sur thresholds (min_score, min_confidence)
- [ ] Walk-forward validation (6 mois train, 1 mois test)
- [ ] Multi-symbol backtest (BTC + ETH + SOL)

### 🟢 Améliorations (Semaine 3+)

- [ ] MAKER mode implementation
- [ ] Dynamic Kelly position sizing
- [ ] Trailing stops
- [ ] Feature selection (top 20 from 70)

---

## ✅ Status

| Composant | Status | Notes |
|-----------|--------|-------|
| **S3 Loader** | ✅ Done | Fonctionne avec PROCESSED data |
| **Timezone Fix** | ✅ Done | UTC compatibility |
| **Column Normalization** | ✅ Done | Capitalized → lowercase |
| **Real Data Backtest** | ✅ Done | 1918 trades sur 11 mois |
| **Pipeline Complet** | ❌ TODO | Actuellement stratégie simplifiée |
| **Optimization** | ❌ TODO | Grid search nécessaire |

---

**Prochaine Action** : Implémenter le pipeline complet (Features → Models → Signals → Orders) pour remplacer la stratégie momentum simpliste.

L'infrastructure est prête. Il faut maintenant brancher la vraie logique de trading ! 🚀
