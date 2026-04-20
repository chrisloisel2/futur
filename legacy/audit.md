# AUDIT COMPLET — PROJET TRADING ALGORITHMIQUE CRYPTO

> Dernière mise à jour : 2026-04-11  
> Auteur : audit automatique via exploration codebase

---

## TABLE DES MATIÈRES

1. [Vue d'ensemble du projet](#1-vue-densemble-du-projet)
2. [Structure des dossiers](#2-structure-des-dossiers)
3. [Architecture ML hiérarchique (ai/models/)](#3-architecture-ml-hiérarchique-aimodels)
4. [Système d'entraînement (trading-system/)](#4-système-dentraînement-trading-system)
5. [Pipeline de données (data/)](#5-pipeline-de-données-data)
6. [Moteurs de signaux (signals/)](#6-moteurs-de-signaux-signals)
7. [Scrapers (scrapers/)](#7-scrapers-scrapers)
8. [API & Frontend (frontend_pipeline/)](#8-api--frontend-frontend_pipeline)
9. [Problèmes critiques transversaux](#9-problèmes-critiques-transversaux)
10. [Matrice de production-readiness](#10-matrice-de-production-readiness)
11. [Historique des décisions techniques](#11-historique-des-décisions-techniques)
12. [Recommandations prioritaires](#12-recommandations-prioritaires)

---

## 1. VUE D'ENSEMBLE DU PROJET

### Objectif

Système de trading algorithmique crypto **entièrement automatisé**, capable de :
- Collecter des données de marché (OHLCV, orderbook, on-chain)
- Collecter des signaux alternatifs (Twitter, News, sentiment, géopolitique)
- Prétraiter et créer des features ML
- Prendre des décisions de trading via pipeline ML hiérarchique 7 niveaux
- Gérer le risque (sizing, stops, daily limits)
- Servir les prédictions via API REST + WebSocket
- Visualiser sur un dashboard React

### Actif principal ciblé

BTC/USDT (Binance), avec support ETH et SOL dans les scrapers.

### Stack technique

| Couche | Technologies |
|---|---|
| ML/DL | PyTorch 2.x (EdgeForecaster), TensorFlow/Keras (Levels 1-6), NumPy (Level 0) |
| Data | pandas, pyarrow, AWS S3 (awswrangler), MongoDB, parquet |
| Scrapers | Scrapy, aiohttp, proxy rotation |
| API | FastAPI, uvicorn, WebSocket |
| Frontend | React, Node.js |
| Infra | Docker, Poetry, pyproject.toml |

### Volume de code

- ~64 625 lignes de Python sur ~523 fichiers
- ~36 GB de données (datasets binance_vision_downloads)
- ~1.6 GB de modèles ML/DL

---

## 2. STRUCTURE DES DOSSIERS

```
futur/
├── ai/                            # Modèles ML hiérarchiques
│   └── models/
│       ├── level_0/               # Global Gating (NumPy streaming)
│       ├── level_1/               # Event Classifier (TensorFlow)
│       ├── level_2/               # Edge Scorer (TensorFlow)
│       ├── level_3/               # Conditional Specialists (TensorFlow)
│       ├── level_4/               # Pairwise Comparator (TensorFlow)
│       ├── level_5/               # Decision logic (pure Python)
│       ├── level_6/               # Meta Scaler (TensorFlow)
│       ├── level_7/               # Risk Controller (NumPy)
│       └── training/              # Scripts d'entraînement par niveau
│
├── trading-system/                # Système d'entraînement PyTorch principal
│   ├── src/
│   │   ├── pipeline/
│   │   │   ├── models/
│   │   │   │   ├── edge/          # EdgeForecasterNet (Transformer PyTorch)
│   │   │   │   ├── regime/        # Classifier de régimes
│   │   │   │   └── specialists/   # Placeholder
│   │   │   ├── features/          # Feature factories
│   │   │   ├── ingestion/         # Ingestion batch/live
│   │   │   ├── backtest/          # Moteur de backtest
│   │   │   └── execution/         # Moteur d'exécution
│   │   └── domain/
│   │       ├── state/             # Gestion d'état (placeholder)
│   │       ├── risk/              # Kelly criterion (placeholder)
│   │       └── events/            # Événements
│   ├── scripts/                   # Scripts CLI (run_*.py, train_*.py)
│   ├── configs/                   # YAML (base, dev, prod, backtest)
│   ├── tests/                     # Tests pytest
│   ├── notebooks/                 # Jupyter (00_data → 09_monitoring)
│   ├── docker/                    # Dockerfile + docker-compose
│   ├── train.py                   # Entrypoint entraînement principal
│   └── training_config.py         # Config dataclasses centralisée
│
├── signals/
│   ├── news/                      # Moteur signaux news (ex news_signal_engine)
│   └── twitter/                   # Moteur signaux Twitter (ex twitter_signal_engine)
│
├── scrapers/
│   ├── indicators/                # Scrapers indicateurs crypto (ex crypto_indicators_scraper)
│   └── engine/                    # Scrapers généraux + whale (ex scrapers_engine)
│
├── data/
│   ├── datasets/                  # Preprocessing ML, feature engineering
│   └── loaders/                   # Scripts collecte données historiques
│
└── frontend_pipeline/             # API FastAPI + Dashboard React
    ├── api_server.py
    ├── ml_endpoints.py
    ├── mongo_utils.py
    └── frontend/alpha-dashboard/  # React app
```

---

## 3. ARCHITECTURE ML HIÉRARCHIQUE (ai/models/)

Le cœur du projet est une pipeline de décision en **7 niveaux cascadés**. Chaque niveau consomme le niveau précédent et peut bloquer la progression vers l'exécution.

```
Données marché (OHLCV + features)
        │
        ▼
[LEVEL 0] Global Gating → tradeable? + y_dir (3 classes)
        │
        ▼
[LEVEL 1] Event Classifier → régimes (4 classes) + confidence + entropy
        │
        ▼
[LEVEL 2] Edge Scorer → edge continu + rv prédite
        │
        ▼
[LEVEL 3] Conditional Specialists → experts par régime → ret[H=12] + rv
        │
        ▼
[LEVEL 4] Pairwise Comparator → cohérence [consistent/weak/contradict]
        │
        ▼
[LEVEL 5] Decision → CONFIRM / DELAY / INVALIDATE
        │
        ▼
[LEVEL 6] Meta Scaler → scale ∈ [0, 1]
        │
        ▼
[LEVEL 7] Risk Controller → action, qty, stop_price, take_profit
```

---

### LEVEL 0 — Global Gating

**Fichier** : `ai/models/level_0/gating_global.py` (~688 lignes)  
**Framework** : NumPy pur, streaming-safe  
**Rôle** : Décider si le marché est tradeable + labeler la direction

#### Architecture

- **Ring buffer** : max 4096 bars, warmup 2048 samples
- **P² Quantile Estimator** (Jain & Chlamtac 1985) — quantiles causaux, sans stocker tous les samples
- **36 features** : OHLCV (5) + log-returns (2) + realized volatility 8 horizons + EMA distances (4) + ATR + RSI + VaR/CVaR (6)

#### Configuration (GatingConfig)

```python
lookback = 256
horizon  = 12          # bars forward
q_absR   = 0.70        # seuil quantile |return|
q_RV_hi  = 0.70        # seuil quantile RV
q_DD_lo  = 0.70        # seuil drawdown (inversé)
q_absScore = 0.70      # seuil score composite
use_trinary_label = True
warmup   = 2048
max_buffer = 4096
```

#### Logique de labeling

```
score = R_horizon / (RV + eps)
tradeable = |R| > q_absR ∧ RV > q_RV_hi ∧ DD < q_DD_lo

y_dir = 0 (short)  si score < -q_absScore
y_dir = 1 (flat)   si |score| ≤ q_absScore
y_dir = 2 (long)   si score > q_absScore
```

#### Points forts

- Streaming-safe : quantiles causaux, jamais de lookahead
- Gestion robuste NaN/inf via `_safe_float` + `_clip_float`
- `freeze_thresholds()` pour figer les seuils en production

#### Points faibles / sous-optimaux

- `epsilon=1e-12` dans les divisions → peut amplifier bruit numérique
- Labels "flat" basés uniquement sur |score|, pas sur un seuil de volatilité minimale → bruit sur signaux faibles
- Horizon=12 sur 1m bars = 12 minutes, pas 2h comme parfois commenté dans le code

---

### LEVEL 1 — Event Classifier

**Fichier** : `ai/models/level_1/Event_Classifier.py` (~226 lignes)  
**Framework** : TensorFlow/Keras  
**Rôle** : Classifier le régime de marché + mesure de confiance + entropie

#### Architecture

```
Input: [B, L=256, F=36]
  → Dense(64) → LayerNorm → GELU
  → 3 × TCN causal dilaté (kernel=3, dilation=1/2/4)
  → GlobalAveragePooling + GlobalMaxPooling + stats temporelles (mean, var)
  → Dense shared (64D)
  → Head regime_logits: [B, R=4] → softmax
  → Head confidence: [B, 1] → sigmoid
  → Head entropy: [B, 1] = -Σ(p log p)
```

#### Configuration

```python
d_model   = 64
n_layers  = 3
n_regimes = 4      # hard-codé
dropout   = 0.2
confidence_dropout = 0.1
```

#### Losses

- `RegimeLoss` : CrossEntropy + `entropy_weight × entropy`
- `ConfidenceLoss` : BCE(confidence)

#### Points faibles

- Nombre de régimes hard-codé à 4 → pas configurable dynamiquement
- Entropie pénalisée → peut rejeter des opportunités légitimes en période incertaine
- Pas de skip-connections sur 3 couches TCN → risque de gradient vanishing

---

### LEVEL 2 — Edge Scorer

**Fichier** : `ai/models/level_2/EdgeScorer.py` (~155 lignes)  
**Framework** : TensorFlow/Keras  
**Rôle** : Score directionnel principal du système

#### Architecture

```
Input: [B, L=256, F=36]
  → Dense(96) → TCN 3-layer causal dilaté → GlobalAveragePooling
  → Head edge: [B] score continu ∈ [-5, 5] = R_future / RV_future
  → Head rv: [B] volatilité prédite
```

#### Loss

```python
loss = Huber(delta=1.0)(edge) + 0.2 * Huber(rv)
```

#### Points faibles

- Pas de calibration/confidence → edge est un score brut sans intervalle de confiance
- Pas de masquage sur `tradeable` → apprend sur toutes les données y compris non-tradeables
- RV head entraîné systématiquement même si pas utilisé downstream

---

### LEVEL 3 — Conditional Specialists

**Fichier** : `ai/models/level_3/conditional_specialists.py` (~249 lignes)  
**Framework** : TensorFlow/Keras  
**Rôle** : 4 experts spécialisés par régime → prédictions conditionnelles

#### Architecture

```
4 × TCNExpert indépendants :
  Dense(128) → 3 × TCN causal → Dense → [ret_head[H=12], rv_head[1]]

Routing soft :
  W[i] = P_regime[i] si P[i] ≥ min_active (sinon 0), puis normaliser

Routing hard :
  W[i] = 1 si P[i] ≥ threshold, sinon argmax(P)

CRITICAL : STOP_GRAD sur P et W → routing ne backpropage PAS dans Level 1
```

#### Points forts

- `STOP_GRAD` correct → pas de fuite de gradient depuis EventClassifier
- Experts non-activés ne contribuent pas aux gradients

#### Points faibles

- Routing dur vs doux non documenté → hard par défaut, peut créer des discontinuités
- Fallback sur argmax si aucun expert ≥ threshold → peut causer des sauts de comportement
- Pas de load-balancing → certains experts peuvent ne jamais être activés
- Pas de boucle d'entraînement complète dans trading-system/

---

### LEVEL 4 — Pairwise Comparator

**Fichier** : `ai/models/level_4/PairwiseComparator.py` (~38 lignes)  
**Framework** : TensorFlow/Keras  
**Rôle** : Cohérence inter-signaux (current vs reference)

#### Architecture

```
Encoder(x_now) + Encoder(x_ref)  →  Dense(64)
Concat [z_now, z_ref, z_now-z_ref, |z_now-z_ref|]  →  Dense(64)  →  softmax(3)
Outputs : [p_consistent, p_weak, p_contradict]
```

#### Points faibles

- Architecture extrêmement minimaliste — encoder 1 couche, pas d'attention
- Pas de contexte temporel → compare des snapshots instantanés, pas des séries
- Sémantique de `p_weak` non documentée (différent de `p_consistent` et `p_contradict`)
- Potentiellement deprecated dans le flux réel

---

### LEVEL 5 — Decision

**Fichier** : `ai/models/level_5/decision.py` (~123 lignes)  
**Framework** : Python pur (heuristique, pas ML)  
**Rôle** : Orchestration finale → CONFIRM / DELAY / INVALIDATE

#### Logique

```python
def level3_decision(event_probs, pairwise_probs, edge, thresholds):

    # 1. INVALIDATE si contradiction forte
    if p_contradict >= 0.55:
        return "INVALIDATE"

    # 2. DELAY si l'une des conditions suivantes
    if (p_vol_shock >= 0.60
     or p_no_event >= 0.80
     or p_weak >= 0.50
     or p_consistent < 0.45
     or |edge| < 0.10
     or edge_confidence < 0.55):
        return "DELAY"

    # 3. Sinon confirmer
    return "CONFIRM"
```

#### Points faibles

- Tous les seuils (0.55, 0.60, 0.80…) sont hard-codés, pas optimisés par backtest
- Format `event_probs` variable (3 ou 4 classes) → fragile selon la config Level 1
- Pas de score de confiance en sortie → juste une string

---

### LEVEL 6 — Meta Scaler

**Fichier** : `ai/models/level_6/meta_scaler.py` (~105 lignes)  
**Framework** : TensorFlow/Keras  
**Rôle** : Apprendre le sizing de position optimal

#### Architecture

```
Input : [tradeability, regime_confidence, regime_entropy, pairwise_consistency, recent_roi]
  → 3 × [Dense(64) + Dropout + LayerNorm]
  → Dense(1, sigmoid)  →  scale ∈ [0, 1]
```

#### Points faibles

- `recent_roi` : source non définie → d'où vient ce signal au moment de l'inférence ?
- Clamp entropy ∈ [0, 5], ROI ∈ [-1, 1] → hyperparamètres sensibles non justifiés
- Pas de fonction de perte documentée → comment est-il entraîné ?

---

### LEVEL 7 — Risk Controller

**Fichier** : `ai/models/level_7/RiskController.py` (~218 lignes)  
**Framework** : NumPy pur  
**Rôle** : Sizing final, stops, take-profits, protections journalières

#### Configuration (RiskConfig)

```python
equity                  = 10_000
risk_per_trade          = 0.002     # 0.2% par trade
max_gross_exposure      = 1.0       # 1× equity max
stop_atr_mult           = 2.5
stop_rv_mult            = 3.0
min_stop_pct            = 0.001     # stop min 0.1%
max_stop_pct            = 0.030     # stop max 3%
rr                      = 1.5       # TP = 1.5 × SL
daily_loss_limit_pct    = 0.02      # stop trading si -2% jour
max_consecutive_losses  = 3
cooldown_bars           = 3
min_scale               = 0.15
min_edge                = 0.05
```

#### Logique de sizing

```python
# 1. Vérifications pre-trade
assert price > 0 and daily_stop_ok and tradeable
assert scale >= 0.15 and |edge| >= 0.05
assert bars_since_last_trade >= 3

# 2. Stop distance
stop_dist = max(atr * stop_atr_mult, rv * stop_rv_mult) * price
stop_dist = clamp(stop_dist, min_stop_pct*price, max_stop_pct*price)

# 3. Sizing
risk_budget = equity * risk_per_trade * scale
qty = risk_budget / stop_dist
qty = min(qty, max_notional / price)

# 4. Output
return {action, qty, notional, stop_price, take_profit}
```

#### Points faibles

- `on_fill_pnl()` jamais appelée dans les pipelines → état `day_pnl` jamais mis à jour
- Sizing basé sur risque fixe → ne tient pas compte des corrélations inter-positions
- Cooldown (3 bars = 3 minutes sur 1m) très court → peut over-trader
- ATR en unités prix → la conversion en % peut être approximative selon l'actif
- **CRITIQUE** : RiskController jamais instancié dans les pipelines de trading

---

## 4. SYSTÈME D'ENTRAÎNEMENT (trading-system/)

### EdgeForecasterNet — Architecture PyTorch canonique

**Fichier** : `trading-system/src/pipeline/models/edge/net.py`  
**Framework** : PyTorch  
**Rôle** : Modèle de prédiction directionnel principal du système d'entraînement (distinct du TF Level 2)

#### Configuration (EdgeForecasterConfig)

```python
seq_len     = 32        # 32 bars = 32 minutes sur 1m
d_model     = 192
n_heads     = 6
n_layers    = 5
d_ff        = 512
dropout     = 0.05
attn_dropout = 0.02
quantiles   = (0.05, 0.25, 0.50, 0.75, 0.95)
device      = cpu
dtype       = float32
```

#### Architecture

```
Input : [B, seq_len=32, F]
  → RMSNorm (Root Mean Square Layer Norm)
  → 5 × CausalSelfAttention (ALiBi biases) + FeedForward
  → Heads :
      quantile  → 5 quantiles de return
      dir_hit   → P(direction correcte)
      is_up     → P(hausse)
      rv        → volatilité réalisée prédite
      sigma_tail → queue de distribution
```

#### Points forts

- Attention causale correcte (masque float("-inf"))
- ALiBi (Attention with Linear Biases) → stable vs positional embeddings appris
- Multi-head outputs → quantiles + calibration intégrée

#### Points faibles

- `seq_len=32` court vs Level 0 (lookback=256) → décalage contextuel temporel
- Horizon de supervision dans training_config=15min vs aggregate_features=480min → **décalage critique**
- Double implémentation EdgeScorer (TF, Level 2) et EdgeForecasterNet (PyTorch, trading-system) → risque de divergence

---

### Configuration d'entraînement (training_config.py)

#### DataConfig

```python
symbol        = "BTCUSDT"
timeframe     = "1m"
start_date    = "2023-01-01"
end_date      = "2025-12-31"
train_pct     = 0.70
val_pct       = 0.15
test_pct      = 0.15
n_folds       = 5
min_train_days = 180
use_regime_conditioning = True
```

#### EdgeForecasterConfig (entraînement)

```python
seq_len         = 32
d_model         = 192
n_heads         = 6
n_layers        = 5
epochs          = 40
batch_size      = 256
lr              = 1e-3         # augmenté vs 3e-4 original
weight_decay    = 1e-5
warmup_pct      = 0.10
label_smoothing = 0.02
scheduler       = "cosine"
amp             = True         # mixed precision
temperature_scaling = True
bootstrap_samples   = 100
```

#### Labels (training_config.py)

```python
tp_k   = 3.0    # TP = 3 × ATR
sl_k   = 2.0    # SL = 2 × ATR
# Expected hit_rate ~40-60% over 15min horizon
```

#### Points faibles

- `lr=1e-3` avec `batch_size=256` → potentiellement instable, surtout sans gradient clipping documenté
- Horizon 15min dans training_config vs horizon 480min dans aggregate_features → décalage non résolu
- Splits train/val/test par ratio plutôt que par date absolue → risque de data leakage si pas strictement chronologique

---

### Scripts d'entraînement (trading-system/scripts/)

| Script | Rôle |
|---|---|
| `train_edge_forecaster.py` | Entraîne EdgeForecasterNet |
| `build_features.py` | Construit les features depuis S3 |
| `build_labels.py` | Génère les labels triple-barrier |
| `run_backtest.py` | Lance un backtest |
| `run_inference.py` | Inférence sur données live/batch |
| `run_live.py` | Mode trading live |
| `run_risk_controller.py` | Lance le risk controller |
| `run_monitoring.py` | Monitoring et drift detection |
| `normalize_s3_parquets.py` | Normalise les colonnes des parquets S3 |
| `check_s3_data.py` | Vérifie la structure S3 |
| `verify_samples.py` | Vérifie les échantillons traités |
| `view_results.py` | Affiche les métriques de backtest |

### Notebooks (trading-system/notebooks/)

Séquence de recherche complète de 00 à 09 :

| Notebook | Contenu |
|---|---|
| 00_data_inspection | Inspection des données brutes |
| 01_quality_gate | Gate qualité des données |
| 02_feature_factory | Construction des features |
| 03_regime_model | Modèle de régimes |
| 04_edge_model | Modèle EdgeForecaster |
| 05_calibration | Calibration des prédictions |
| 06_backtest_validation | Validation backtest |
| 07_execution_sim | Simulation d'exécution |
| 08_risk_scenarios | Scénarios de risque |
| 09_monitoring_drift | Monitoring et drift |

### Artifacts de backtest

Des backtests réels ont été produits (dossier `trading-system/artifacts/backtests/`) avec :
- `equity_curve.parquet`
- `fills.parquet`
- `metrics.json`
- `trades.parquet`

Des résultats de backtests datant de fin décembre 2025 sont présents.

---

## 5. PIPELINE DE DONNÉES (data/)

### Feature Engineering (data/datasets/aggregate_features.py)

#### Fenêtres de calcul

```python
vol_windows  = (3, 5, 10, 15, 30, 60, 120, 240, 480, 720)
ema_periods  = (8, 21, 55, 144)
rsi_period   = 14
atr_period   = 14
flow_windows = (5, 15, 60, 240)
zscore_windows = (60, 240, 720)
```

→ **~40-50 features** total après engineering

#### Labels (LabelSpec)

```python
horizon_min       = 480    # 8h forward
u                 = 1.0    # TP en σ
d                 = 1.0    # SL en σ
sigma_floor       = 1e-6
sigma_cap         = 0.10
tradeable_vol_q   = 0.25   # filtre bas-volatilité
```

#### Points faibles

- Horizon 480min (8h) inconsistant avec training_config (15min) et Level 0 (12 bars)
- Labels triple-barrier corrects mais sans gestion des labels "censored" (barrière jamais atteinte)
- Pas de contrôle de stationnarité des features

---

### ML Preprocessing (data/datasets/ml_preprocessing_pipeline.py)

#### Pipeline

```python
1. Drop features inutiles (latency_ms, btc_dominance, eth_btc_ratio)
2. Recompute returns_5m = log(close).diff(5)
3. Recompute rv_zscore
4. Engineer new features
5. log1p transform (heavy tails)
6. Winsorize (quantiles 0.001, 0.999)
7. Temporal split (chronologique strict)
8. RobustScaler (fit sur train uniquement)
```

#### Points forts

- Split temporel strict → pas de data leakage
- Winsorize avant scaling → correct

#### Points faibles

- `log1p` sur des returns → peut distorter si déjà en log-space
- Pas de feature selection → toutes les features utilisées sans validation
- k_target=5min : décalage entre feature time et label time de 5 barres → à documenter

---

### Data Loaders (data/loaders/)

| Fichier | Rôle |
|---|---|
| `collect_historical_crypto.py` | Collecte async OHLCV 30 cryptos via Binance (sans API key) |
| `lastfetch.py` | Charge Bitstamp BTCUSD 1m (2012-2025) + overlay Binance Futures BBO |
| `fetcher.sh` | Télécharge données Bitstamp historiques |
| `download_binance_bbo_depth.sh` | Script Binance Vision avec vérification HTTP 200 avant download, download concurrent (6 workers) |

`download_binance_bbo_depth.sh` gère : Spot klines, aggTrades, Futures klines, markPriceKlines, premiumIndexKlines, fundingRate, openInterestHist, longShortRatio.

---

## 6. MOTEURS DE SIGNAUX (signals/)

### Twitter Signal Engine (signals/twitter/)

#### Modèles de données (models.py)

```python
RawTweet:
    id, text, author, followers, verified
    likes, retweets, replies

ProcessedTweet:
    latency_ms, engagement_velocity
    author_credibility, bot_probability

WindowAggregation:
    entity, sentiment, reach
    burst_score, credibility

TradingSignal:
    sentiment_direction
    attention_burst
    credibility_weighted_score
```

#### Pipeline (pipeline.py)

```
1. Filter (hard filters : langue, followers, âge compte)
2. Entity extraction (BTC, ETH, SOL, FED, ECB, SEC, CFTC...)
3. Enrichment (métadonnées auteur)
4. Semantic analysis (sentiment + certitude)
5. Aggregation multi-fenêtres (5min, 30min, 2h)
6. Signal generation
```

#### Configuration

```python
ACCOUNT_MIN_AGE_DAYS    = 90
ACCOUNT_MIN_FOLLOWERS   = 1_000
BURST_ZSCORE_THRESHOLD  = 2.0
MIN_TWEETS_FOR_SIGNAL   = 5
MIN_CREDIBILITY_SCORE   = 0.4
```

#### Points faibles

- **Semantic analysis non implémentée** → `SemanticProcessor` est un stub
- Credibility weighting non détaillé
- `avg_latency_ms` et `api_calls_used` non comptabilisés
- Pas de connexion API Twitter configurée

---

### News Signal Engine (signals/news/)

#### Architecture similaire à Twitter avec en plus

- **Source tier classification** : Reuters/Bloomberg = 1.0, CoinDesk/Cointelegraph = 0.8, forums = 0.4
- **EventType** fermé : regulation, approval, rejection, hack, exploit, partnership, adoption...
- **GeographicScope** : global, US, EU, APAC, emerging
- **4 fenêtres** d'agrégation : 15min, 1h, 6h, 24h

#### Configuration notable

```python
SOURCE_TIERS = {
    "reuters.com": 1.0,
    "bloomberg.com": 1.0,
    "coindesk.com": 0.8,
    "cointelegraph.com": 0.7,
    "decrypt.co": 0.6,
    ...
}
OFFICIAL_SOURCES = {
    "sec.gov": 1.0,
    "cftc.gov": 1.0,
    "federalreserve.gov": 1.0,
}
MIN_ARTICLES_FOR_SIGNAL  = 2
MIN_CREDIBILITY_SCORE    = 0.5
```

#### Points faibles

- Event clustering non implémenté
- Pas de correction des fausses nouvelles (penalty system)
- Semantic analysis partiellement implémentée

---

## 7. SCRAPERS (scrapers/)

### Engine Scrapers (scrapers/engine/scrapers_engine/)

#### Spiders

| Spider | Données |
|---|---|
| `whale_alert.py` | Transactions whale BTC/ETH/SOL |
| `arkham.py` | Intelligence on-chain Arkham |
| `bitcointalk.py` | Sentiment forum |
| `crypto_news.py` | News crypto générales |
| `asian_crypto.py` | Marché asiatique |
| `coindesk.py` | CoinDesk articles |
| `ethereum_etherscan_spider.py` | Transactions Ethereum |
| `solana_solscan_spider.py` | Transactions Solana |
| `bitcoin_mempool_spider.py` | Mempool BTC |

#### Middlewares

- `MongoDBProxyRotatorMiddleware` : rotation proxy depuis MongoDB
- `UserAgentRotator`
- `ErrorHandling`
- `Enrichment`

#### Pipelines

```python
ValidationPipeline
DeduplicationPipeline
MetadataExtractionPipeline
S3UnifiedPipeline           # Hive partitioning
S3TradingPipeline           # BTC/ETH/SOL only
StoragePipeline             # local JSON
blockchain_whale_mongodb_pipeline
```

#### Settings notables

```python
CONCURRENT_REQUESTS = 16
DOWNLOAD_DELAY      = 2
RETRY_TIMES         = 5
RETRY_HTTP_CODES    = [500, 502, 503, 504, 408, 429, 403, 407]
ALLOWED_ASSETS      = ['BTC', 'ETH', 'SOL']
S3_BUCKET           = "qbia"
S3_PREFIX           = "bourse/raw"
WHALE_MIN_USD_VALUE = 100_000
```

#### Points faibles

- `MONGODB_URI` potentiellement hard-codé dans settings → security risk
- `CONCURRENT_REQUESTS=16` → peut déclencher des bans IP
- Pas de rate limiting adaptatif sur tous les spiders

---

### Indicators Scrapers (scrapers/indicators/)

#### Spiders

- `crypto_indicators_spider` : indicateurs techniques crypto
- `geopolitical_spider` : événements géopolitiques
- `sentiment_spider` : indices de sentiment (Fear & Greed)
- `trends_macro_spider` : Google Trends + macro

#### Settings

```python
CONCURRENT_REQUESTS = 32            # plus agressif
AUTOTHROTTLE:
    start_delay = 0.5
    max_delay   = 10
    target_concurrency = 8
S3_INDICATORS_PREFIX = "bourse/indicators"
PROXY_ROTATION_ENABLED = True
```

#### Pipeline S3

```python
class S3IndicatorsPipeline:
    # Hive partitioning par date
    # Supporte bulk upload
    # Retry sur échec S3
```

#### Points faibles

- Pas d'API keys configurées pour NewsAPI, CryptoCompare → TODO dans le code
- 32 requêtes concurrentes élevé même avec throttle

---

## 8. API & FRONTEND (frontend_pipeline/)

### API Server (api_server.py)

**Framework** : FastAPI + uvicorn

#### Endpoints dataset

```
GET /dataset/summary
GET /dataset/signals
GET /dataset/ohlcv/{symbol}
GET /dataset/funding-rates
GET /dataset/fear-greed
GET /dataset/sentiment
GET /dataset/macro
GET /dataset/derivatives
GET /market/all-cryptos
GET /market/ticker
```

#### Endpoints ML

```
/ml/*  →  router ml_endpoints.py
```

#### Gestion des jobs d'entraînement

```python
training_jobs = {}       # in-memory, perdu au restart
training_lock = Lock()   # thread-safe
```

#### CORS

```python
allow_origins = ["http://localhost:3000"]   # hard-codé
```

#### Points faibles

- Jobs d'entraînement stockés en mémoire → perdus au redémarrage
- Origins CORS hard-codées sur localhost
- WebSocket annoncé mais non fully implémenté pour le streaming ML

---

### ML Endpoints (ml_endpoints.py)

**CRITIQUE** : Tous les endpoints ML sont des **générateurs de données mock**.

```python
# Exemple de la réalité du code :
def get_level0_gating():
    return {
        "tradeable": random.choice([True, False]),
        "confidence": random.uniform(0.5, 0.95),
        ...  # données aléatoires
    }
```

- Level 0 → mock
- Level 1 → mock
- Level 2 → mock
- Level 3 → mock
- Level 4 → mock
- Level 5 → mock
- Level 6, 7 → **absents**
- **Pas de connexion aux modèles réels** dans `ai/` ou `trading-system/`

---

## 9. PROBLÈMES CRITIQUES TRANSVERSAUX

### CRITIQUE 1 — Décalages temporels entre composants

| Composant | Horizon utilisé |
|---|---|
| Level 0 Gating | horizon=12 (12 minutes sur 1m) |
| EdgeForecasterNet | seq_len=32, labels 15min |
| aggregate_features.py | horizon_min=480 (8h) |
| Level 3 Specialists | ret[H=12] (12 minutes) |

**Impact** : Labels de supervision incohérents entre composants → les modèles ne prédisent pas la même fenêtre temporelle.

---

### CRITIQUE 2 — Double implémentation EdgeScorer

- **TensorFlow** : `ai/models/level_2/EdgeScorer.py` (Level 2 du pipeline hiérarchique)
- **PyTorch** : `trading-system/src/pipeline/models/edge/net.py` (EdgeForecasterNet)

Ces deux modèles ont des configs différentes (d_model=96 vs 192, seq_len=256 vs 32, quantiles vs pas de quantiles). Il n'est pas documenté lequel est utilisé en production.

---

### CRITIQUE 3 — Signaux alternatifs non intégrés

Les moteurs Twitter et News produisent des `TradingSignal` mais :
- RiskController (Level 7) n'en reçoit aucun
- EdgeForecaster ne les consomme pas
- Pas de pipeline d'intégration documentée

**Impact** : Toute l'infrastructure signals est construite mais non utilisée dans la décision finale.

---

### CRITIQUE 4 — RiskController jamais appelé

Le code Level 7 est complet mais n'est instancié nulle part dans les pipelines d'exécution.  
`on_fill_pnl()` n'est jamais appelée → `day_pnl` et `consecutive_losses` jamais mis à jour.

**Impact** : Pas de gestion de risque effective → exposition illimitée en théorie.

---

### CRITIQUE 5 — Absence de backtest end-to-end Level 0-7

Les notebooks (06_backtest_validation) et `run_backtest.py` existent mais backtestent uniquement l'EdgeForecaster, pas la pipeline complète (gating → régimes → specialists → décision → sizing → risk).

**Impact** : Impossible de valider la profitabilité du système complet.

---

### CRITIQUE 6 — ML Endpoints tous en mock

Le frontend Dashboard communique avec `ml_endpoints.py` qui retourne des données aléatoires. Il n'y a aucune connexion aux modèles réels.

---

### CRITIQUE 7 — État non persisté

- `RiskState` (day_pnl, consecutive_losses) jamais sauvegardé
- `training_jobs` API in-memory
- Checkpoints modèles non versionnés

---

## 10. MATRICE DE PRODUCTION-READINESS

| Composant | Readiness | Status |
|---|---|---|
| **Level 0 — Global Gating** | 85% | Code solide, causal, streaming-safe. Epsilon et labels "flat" sous-optimaux. |
| **Level 1 — Event Classifier** | 70% | Architecture TCN correcte. 4 régimes hard-codés, pas de skip-connections. |
| **Level 2 — Edge Scorer (TF)** | 75% | Source unique de direction. Pas de calibration, pas de masquage tradeable. |
| **Level 3 — Specialists** | 40% | Routing fragile, fallback argmax non documenté, pas load-balancing. |
| **Level 4 — Comparator** | 30% | Extrêmement minimaliste, pas temporal, potentiellement deprecated. |
| **Level 5 — Decision** | 50% | Logique claire mais seuils hard-codés non calibrés. |
| **Level 6 — Meta Scaler** | 55% | Input "recent_roi" non défini, loss non documentée. |
| **Level 7 — Risk Controller** | 70% | Logique complète mais jamais appelée, state non persisté. |
| **EdgeForecasterNet (PyTorch)** | 80% | Causal, ALiBi, multi-head. seq_len court, horizon mismatch. |
| **Twitter Signals** | 35% | Structure solide. Semantic analysis = stub, pas d'API connectée. |
| **News Signals** | 50% | Source tiers, event types corrects. Clustering non implémenté. |
| **Scrapers engine** | 85% | Multi-source, S3, blockchain. URI MongoDB potentiellement hard-codée. |
| **Scrapers indicators** | 75% | AutoThrottle. API keys manquantes. |
| **Feature Pipeline** | 70% | Features riches. Horizon mismatch avec labels. |
| **ML Preprocessing** | 75% | Split temporel correct, winsorize. log1p sur returns discutable. |
| **API Server** | 60% | Endpoints corrects. CORS hard-codé, jobs in-memory. |
| **ML Endpoints** | 10% | **Tous des mocks.** Aucune connexion aux modèles réels. |
| **Frontend Dashboard** | 55% | React app complète. Données mock uniquement. |
| **Backtest end-to-end** | 20% | Artifacts présents sur EdgeForecaster seul. Pas de pipeline complète. |
| **Intégration Signaux→Trading** | 5% | Aucune intégration implémentée. |

---

## 11. HISTORIQUE DES DÉCISIONS TECHNIQUES

### Architecture binaire régimes (décision passée)

Une migration a été effectuée depuis une classification multi-classes (4+ régimes) vers une architecture **binaire [calm, reversal]**. Des fichiers PATCH_* (maintenant supprimés) documentaient cette transition. La migration incluait :
- `regime_classifier_v2.py` avec `DEFAULT_CLASSES = ["calm", "reversal"]`
- `production_gates.py` avec `min_accuracy = 0.60`
- Suppression de `min_impulse_recall` comme gate de production
- Réintroduction d'un `impulse_detector` séparé comme event detector

Note : Le Level 1 dans `ai/models/level_1/` utilise toujours `n_regimes=4` → incohérence non résolue avec la migration binaire.

### Passage lr 3e-4 → 1e-3

Le learning rate de l'EdgeForecaster a été augmenté (commenté dans training_config.py). Contexte probable : convergence trop lente. Risque d'instabilité non documenté.

### Correction gradient saturation / AMP

Des patches (PATCH_1_1, 1_2, 1_3, maintenant supprimés) ont corrigé :
- Gradient logging
- Saturation checks (vanishing/exploding gradients)
- Mode debug overfitting

Ces corrections sont intégrées dans le code actuel de trading-system/.

### Normalisation parquets S3

`normalize_s3_parquets.py` (maintenant dans scripts/) a été créé pour corriger des inconsistances de colonnes dans les parquets S3 BTC-only.

---

## 12. RECOMMANDATIONS PRIORITAIRES

### Phase 1 — Alignement temporel (Bloquant)

1. Définir un horizon unique pour tout le système (recommandation : 60 minutes)
2. Mettre à jour `aggregate_features.py` : `horizon_min = 60`
3. Mettre à jour `training_config.py` : horizon labels = 60min
4. Aligner Level 0 : `horizon=60` (ou adapter seq_len EdgeForecaster à 256)
5. Documenter explicitement la convention temporelle dans un `CONVENTIONS.md`

### Phase 2 — Connecter les vrais modèles à l'API

1. Implémenter `ml_endpoints.py` avec vrais appels vers `ai/models/`
2. Ajouter un service de chargement des checkpoints modèles au démarrage
3. Implémenter le streaming WebSocket pour les prédictions live

### Phase 3 — Intégrer les signaux alternatifs

1. Implémenter `SemanticProcessor` (BERT-tiny ou lexicon FinBERT)
2. Créer un feature vector `signal_features` depuis Twitter + News outputs
3. Injecter `signal_features` comme input additionnel dans EdgeForecaster

### Phase 4 — Activer le Risk Controller

1. Instancier `RiskController` dans `run_live.py`
2. Appeler `on_fill_pnl()` après chaque fill
3. Persister `RiskState` dans MongoDB ou fichier JSON (journalier)
4. Ajouter un circuit-breaker si `day_pnl < -daily_loss_limit_pct`

### Phase 5 — Backtest end-to-end

1. Créer `run_full_pipeline_backtest.py` qui enchaîne Level 0 → Level 7
2. Ajouter slippage réaliste (4 bps Binance) et fees
3. Valider sur walk-forward (ex. re-train tous les 3 mois)
4. Produire : Sharpe, Sortino, Max Drawdown, Hit Rate, Profit Factor

### Phase 6 — Consolidation architecture

1. Choisir un seul framework ML (TensorFlow ou PyTorch) pour Level 2
2. Supprimer `Level_4/PairwiseComparator.py` ou le remplacer par une vraie architecture temporelle
3. Documenter `recent_roi` pour Level 6 MetaScaler
4. Résoudre l'incohérence binaire/4-régimes entre trading-system et ai/models/level_1

---

*Fin de l'audit — 2026-04-11*
