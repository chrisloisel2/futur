# FUTUR - Systeme de Trading Algorithmique BTC/USDT

## Vue d'ensemble

**FUTUR** est un systeme complet de trading algorithmique sur Bitcoin (BTCUSDT), allant de la collecte de donnees brutes jusqu'a l'execution d'ordres en production. Il integre du machine learning (Transformer), de l'ingenierie de features avancee, et une infrastructure multi-source.

```
+=====================================================================+
|                        ARCHITECTURE GLOBALE                          |
+=====================================================================+

  +------------------+    +------------------+    +------------------+
  |   DATA SOURCES   |    |  SCRAPERS ENGINE |    |   FRONTEND API   |
  |                  |    |                  |    |                  |
  | Binance Vision   |    | Whale Alert      |    | FastAPI :8000    |
  | Bitstamp 1m      |    | CoinDesk News    |    | WebSocket        |
  | Binance Perp.    |    | Arkham Intel     |    | React :3000      |
  | Binance WS Live  |    | Mempool/Ethscan  |    | MongoDB          |
  +--------+---------+    +--------+---------+    +--------+---------+
           |                       |                       |
           v                       v                       v
  +--------+-----------------------+-----------------------+--------+
  |                      S3 DATA LAKE (qbia)                        |
  |  s3://qbia/bourse/raw/       s3://qbia/bourse/features/        |
  +-----------------------------------------------------------------+
           |
           v
  +------------------+     +------------------+    +------------------+
  | DATASET PIPELINE |---->| ML PREPROCESSING |    | VALIDATION SUITE |
  |                  |     |                  |    |                  |
  | aggregate_       |     | Temporal Split   |    | No-Lookahead     |
  |   features.py    |     | RobustScaler     |    | Walk-Forward     |
  | 130+ features    |     | Winsorization    |    | Shuffle Test     |
  | Triple Barrier   |     | NaN/Inf Guards   |    | IC / SNR         |
  +--------+---------+     +--------+---------+    +------------------+
           |                        |
           v                        v
  +------------------+     +------------------+    +------------------+
  | TRAINING SYSTEM  |     |  EDGE FORECASTER |    | REGIME CLASSIFIER|
  |                  |     |  (Transformer)   |    |                  |
  | train.py (S3)    |     |  5 layers        |    | LogisticRegr.    |
  | train_aggregated |     |  6 heads         |    | 6 regimes        |
  |   .py (local)    |     |  d_model=192     |    | Balanced weights |
  +--------+---------+     +--------+---------+    +--------+---------+
           |                        |                       |
           v                        v                       v
  +--------+-----------------------+-----------------------+--------+
  |                    PRODUCTION PIPELINE                           |
  |  orchestrator.py : Features -> Regime -> Edge -> Decision       |
  |                    -> Risk -> Execution -> Orders                |
  +-----------------------------------------------------------------+
           |
           v
  +------------------+     +------------------+    +------------------+
  | RISK MANAGEMENT  |     |    EXECUTION     |    |   MONITORING     |
  |                  |     |                  |    |                  |
  | Kelly Criterion  |     | Binance API      |    | Drift Detection  |
  | Max DD 15%       |     | Bybit / OKX      |    | Prometheus       |
  | 20 trades/jour   |     | Maker / Taker    |    | OpenTelemetry    |
  +------------------+     +------------------+    +------------------+
```

---

## Structure des Repertoires

```
futur/
|
|-- trading-system/          # Coeur ML + Pipeline de trading
|   |-- src/                 # Code source production
|   |   |-- pipeline/        # ML pipeline (features, models, decision, risk)
|   |   |-- domain/          # Modeles metier (DDD)
|   |   |-- infra/           # Infrastructure (S3, exchanges, messaging)
|   |   +-- common/          # Utilitaires partages
|   |-- configs/             # Configurations YAML (prod, dev, models)
|   |-- artifacts/           # Modeles entraines + evaluations
|   |-- tests/               # Tests unitaires + E2E
|   |-- train.py             # Entrainement via S3 (39 features)
|   |-- train_aggregated.py  # Entrainement local (83 features)
|   +-- training_config.py   # Configuration unifiee
|
|-- datasets/                # Pipeline de donnees + Feature Engineering
|   |-- aggregate_features.py      # Moteur d'aggregation (130+ features)
|   |-- ml_preprocessing_pipeline.py # Preprocessing ML
|   |-- validate_pipeline.py       # Validation qualite
|   |-- binance_vision_downloads/  # Donnees brutes Binance (zips)
|   |-- data_bitstamp/             # Historique Bitstamp (2012-2025)
|   +-- processed/                 # Parquets agreges
|
|-- frontend_pipeline/       # API temps reel + ingestion
|   |-- api_server.py        # FastAPI (endpoints REST + WS)
|   |-- mass_data_collector.py  # Collecteur multi-exchange
|   +-- data/                # Sources alternatives
|
|-- scrapers_engine/         # Scrapers donnees alternatives
|   |-- spiders/             # 7+ spiders (WhaleAlert, News, Arkham...)
|   |-- pipelines/           # MongoDB + S3 storage
|   +-- runner.py            # Orchestrateur
|
|-- scripts/                 # Validation & Audit
|   |-- audit_ml_system.py   # Audit complet du systeme ML
|   |-- validate_edge.py     # Verification no-lookahead
|   |-- run_walk_forward.py  # Validation walk-forward 5-fold
|   |-- run_baselines.py     # Baselines comparatives
|   +-- compute_ic.py        # Information Coefficient
|
|-- Aggregations.py          # Feature factory S3/Athena
|-- lastfetch.py             # Chargement historique 10+ ans
+-- collect_historical_crypto.py  # Collecteur async top-30 cryptos
```

---

## Le Parcours de la Data : De la Source au Trade

Ce diagramme trace le chemin exact d'une donnee depuis sa source brute jusqu'a la decision de trading.

```
========================================================================
ETAPE 1 : COLLECTE DES DONNEES BRUTES
========================================================================

Sources Historiques (offline)                Sources Temps Reel (online)
+----------------------------+              +----------------------------+
| Binance Vision (public)    |              | Binance WebSocket          |
|  - aggTrades 2017-2025     |              |  - trades live             |
|  - klines 1m 2017-2025     |              |  - orderbook L2            |
|                            |              |                            |
| Bitstamp CSV               |              | Kraken / Coinbase          |
|  - BTCUSD 1m 2012-2025     |              |  - prix / volume           |
|                            |              |                            |
| Binance Perpetual          |              | Scrapers (Scrapy)          |
|  - funding rate            |              |  - Whale Alert API         |
|  - open interest           |              |  - CoinDesk / News         |
|  - long/short ratio        |              |  - Arkham Intelligence     |
+----------------------------+              +----------------------------+
             |                                          |
             v                                          v
+----------------------------+              +----------------------------+
| Fichiers ZIP / CSV / GZ    |              | MongoDB                    |
| datasets/binance_vision_   |              |  trader.historical_ohlcv   |
|   downloads/               |              |  whale_data.transactions   |
| datasets/data_bitstamp/    |              |                            |
+----------------------------+              | S3 (qbia)                  |
                                            |  bourse/raw/<type>/        |
                                            +----------------------------+

========================================================================
ETAPE 2 : CONSTRUCTION DU DATASET DE BASE
========================================================================

Fichiers ZIP Binance     Bitstamp CSV.GZ      Perpetual Data
       |                      |                      |
       v                      v                      v
  +---------+            +---------+            +---------+
  | Decomp. |            | Parse   |            | API/S3  |
  | CSV     |            | Datetime|            | Load    |
  +---------+            +---------+            +---------+
       |                      |                      |
       +----------+-----------+                      |
                  |                                   |
                  v                                   |
       +-------------------+                          |
       | Resample 1-minute |                          |
       | OHLCV + Volume    |                          |
       +-------------------+                          |
                  |                                   |
                  v                                   v
       +------------------------------------------------+
       |  microstructure_10y_proxy.parquet               |
       |  ~6.8M lignes, 2012-2025, 1-minute             |
       |  Colonnes: timestamp, open, high, low, close,   |
       |            volume, n_trades, quote_volume,       |
       |            taker_buy_volume, funding_rate,       |
       |            open_interest, long_short_ratio       |
       +------------------------------------------------+

========================================================================
ETAPE 3 : FEATURE ENGINEERING (aggregate_features.py)
========================================================================

              microstructure_10y_proxy.parquet
                            |
                            v
    +-----------------------------------------------+
    |          MOTEUR DE FEATURES (130+)             |
    +-----------------------------------------------+
    |                                               |
    |  1. RETURNS & LOG RETURNS                     |
    |     logret_1m = log(close/close[-1])          |
    |     ret_1m = close.pct_change()               |
    |                                               |
    |  2. VOLATILITE REALISEE (25 features)         |
    |     rv_N = sqrt(sum(logret^2, N))             |
    |       N = 3,5,10,15,30,60,120,240,480,720     |
    |     sig_min_N = rv_N / sqrt(N)                |
    |     ret_norm_N = logret / sig_min_N           |
    |     sigma_min = weighted(sig_min_30,120,480)  |
    |       poids: 0.5, 0.3, 0.2                   |
    |                                               |
    |  3. TREND & EMA (20+ features)                |
    |     EMA(8, 21, 55, 144)                       |
    |     dist_ema_atr = (close-EMA)/ATR            |
    |     ema_slope = EMA.diff(5) / ATR             |
    |                                               |
    |  4. STRUCTURE BOUGIES (7 features)            |
    |     ATR(14), ATR%, range, range/ATR           |
    |     CLV = (close-low)/(high-low)              |
    |     body = |close-open|/range                 |
    |                                               |
    |  5. RSI (2 features)                          |
    |     RSI(14) methode Wilder                    |
    |     rsi_slope_5 = RSI.diff(5)                 |
    |                                               |
    |  6. MICROSTRUCTURE (10+ features)             |
    |     delta_volume = buy_vol - sell_vol         |
    |     buy_pressure = buy_vol / total_vol        |
    |     bs_ratio = buy / sell                     |
    |     CVD cumule sur 5,15,60,240 min            |
    |                                               |
    |  7. Z-SCORES ROBUSTES (8+ features)           |
    |     z = (x - median) / (MAD * 1.4826)        |
    |     logret_z, sigma_min_z, volume_mad         |
    |     Fenetres: 60, 240, 720 minutes            |
    |                                               |
    |  8. LIQUIDITE (3 features)                    |
    |     amihud = |ret| / volume                   |
    |     slippage_proxy = atr / volume_med         |
    |     fill_prob = 1 / (1 + slippage)            |
    |                                               |
    |  9. CALENDRIER (9 features)                   |
    |     hour_sin, hour_cos                        |
    |     day_sin, day_cos                          |
    |     month_sin, month_cos                      |
    |                                               |
    | 10. MULTI-TIMEFRAME (20+ features)            |
    |     Resampling: 5m, 15m, 1h, 4h              |
    |     COMPLETED-ONLY (pas de lookahead)         |
    |     close_5m, logret_5m, atr_5m, etc.         |
    +-----------------------------------------------+
                            |
                            v

========================================================================
ETAPE 4 : GENERATION DES LABELS (Triple Barrier)
========================================================================

    +-----------------------------------------------+
    |        TRIPLE BARRIER LABELING                 |
    +-----------------------------------------------+
    |                                               |
    |  Barrieres adaptatives a la volatilite:       |
    |                                               |
    |  TP = prix * exp(+u * sigma_min * sqrt(H))    |
    |  SL = prix * exp(-d * sigma_min * sqrt(H))    |
    |                                               |
    |  u = d = 1.0 (symetrique)                     |
    |  H = horizon en minutes                       |
    |                                               |
    |  Pour chaque barre, on simule le chemin:      |
    |  - Si TP touche en premier -> BUY (0)         |
    |  - Si SL touche en premier -> SELL (1)        |
    |  - Si timeout -> WAIT (2)                     |
    |                                               |
    |  Labels generes:                              |
    |  - label_policy: 0/1/2                        |
    |  - label_tradeable: 0/1 (vol > Q25)           |
    |  - logret_fwd_Xm (X = 5,15,30,60,120,240)    |
    |  - dir_Xm (direction)                         |
    |  - hit_tp_Xm, hit_sl_Xm                      |
    |  Total: 24 targets + 2 labels = 26            |
    +-----------------------------------------------+
                            |
                            v
    +-----------------------------------------------+
    |  aggregated_latest.parquet                     |
    |  ~6.8M lignes x 154 colonnes                  |
    |  = 5 OHLCV + 130 features + 26 labels         |
    +-----------------------------------------------+

========================================================================
ETAPE 5 : PREPROCESSING ML (ml_preprocessing_pipeline.py)
========================================================================

    aggregated_latest.parquet
              |
              v
    +-------------------+
    | Drop colonnes     |     latency_ms, btc_dominance, eth_btc_ratio
    | inutiles          |
    +-------------------+
              |
              v
    +-------------------+
    | Recalcul returns  |     log(close_5m).diff(5) - verification
    | coherence         |
    +-------------------+
              |
              v
    +-------------------+
    | RV Z-score MAD    |     z = (rv - median) / (MAD * 1.4826)
    +-------------------+
              |
              v
    +-------------------+
    | Log1p transform   |     volume, notional -> log1p(x)
    +-------------------+
              |
              v
    +-------------------+
    | Winsorization     |     clip Q0.001 - Q0.999
    +-------------------+
              |
              v
    +-------------------+      AUCUN SHUFFLE - ordre temporel preserve
    | SPLIT TEMPOREL    |
    |                   |      Train : 2012-2022 (~70%)
    |   PAS de shuffle  |      Val   : 2022-2024 (~15%)
    |                   |      Test  : 2024+     (~15%)
    +-------------------+
              |
              v
    +-------------------+
    | Targets           |     target_ret = log(close).diff(k).shift(-k)
    | shift(-k)         |     shift NEGATIF = regarde le FUTUR
    +-------------------+     (correct: pas de lookahead dans features)
              |
              v
    +-------------------+
    | RobustScaler      |     FIT sur train UNIQUEMENT
    | median / IQR      |     Transform val + test separement
    +-------------------+
              |
              v
    +-------------------+
    | Validation finale |     NaN=0, Inf=0, |ret|<50%
    +-------------------+
              |
              v
    +-----------------------------------------+
    | X_train, y_train  |  ~4.8M lignes       |
    | X_val, y_val      |  ~1.0M lignes       |
    | X_test, y_test    |  ~1.0M lignes       |
    +-----------------------------------------+

========================================================================
ETAPE 6 : ENTRAINEMENT DU MODELE
========================================================================

    X_train (sequences de 32 barres x 39-83 features)
              |
              v
    +-----------------------------------------------+
    |        EDGE FORECASTER (Transformer)           |
    +-----------------------------------------------+
    |                                               |
    |  Input: [batch, 32, n_features]               |
    |                                               |
    |  +------------------+                         |
    |  | Input Projection |  Linear(n_feat, 192)    |
    |  +------------------+                         |
    |           |                                   |
    |           v                                   |
    |  +------------------+                         |
    |  | ALiBi Positional |  Pas de PE apprise      |
    |  | Encoding         |  Biais relatif          |
    |  +------------------+                         |
    |           |                                   |
    |           v                                   |
    |  +------------------+  x5 couches             |
    |  | Transformer Block|                         |
    |  |  - Causal SelfAtt|  6 tetes, masque causal |
    |  |  - RMSNorm       |                         |
    |  |  - FFN (192->512) |                        |
    |  |  - Dropout 5%    |                         |
    |  +------------------+                         |
    |           |                                   |
    |           v                                   |
    |  +------------------+                         |
    |  | Derniere position|  Prend le dernier token  |
    |  +------------------+                         |
    |           |                                   |
    |     +-----+-----+-----+-----+-----+          |
    |     |     |     |     |     |     |          |
    |     v     v     v     v     v     v          |
    |   [q05] [q50] [q95] [dir] [up]  [rv]  [sig] |
    |                                               |
    |  Sorties multi-tache:                         |
    |  - q05, q50, q95 : quantiles rendement 15m    |
    |  - dir_hit : P(direction correcte)            |
    |  - is_up : P(hausse)                          |
    |  - rv_fwd : volatilite future                 |
    |  - sigma_tail : risque de queue               |
    +-----------------------------------------------+
              |
              |  Loss = pinball(quantiles)
              |       + BCE(dir_hit)
              |       + BCE(is_up)
              |       + MSE(rv_fwd)
              |
              v
    +-----------------------------------------------+
    |  BOUCLE D'ENTRAINEMENT                        |
    +-----------------------------------------------+
    |                                               |
    |  Pour chaque epoque (40 max):                 |
    |    1. Forward pass (AMP mixed precision)      |
    |    2. Loss multi-tache                        |
    |    3. Gradient clipping (max_norm=5.0)        |
    |    4. AdamW + Cosine scheduler                |
    |    5. Validation:                             |
    |       - ECE (calibration)                     |
    |       - Brier Score                           |
    |       - Trading proxy (simulated trades)      |
    |    6. Early stopping (patience=15)            |
    |    7. Checkpoint si meilleur score             |
    |                                               |
    |  Paper test toutes les 500 epoques            |
    +-----------------------------------------------+
              |
              v
    +-----------------------------------------------+
    |  CALIBRATION (Temperature Scaling)             |
    +-----------------------------------------------+
    |  T* = argmin NLL(softmax(logits/T), labels)   |
    |  Ajuste les probabilites pour etre fiables    |
    +-----------------------------------------------+
              |
              v
    artifacts/models/best_model.pt

========================================================================
ETAPE 7 : PRODUCTION (orchestrator.py)
========================================================================

    Flux temps reel (WebSocket Binance)
              |
              v
    +-----------------------------------------------+
    |           PRODUCTION PIPELINE                  |
    +-----------------------------------------------+
    |                                               |
    |  1. INGESTION                                 |
    |     Tick -> Buffer -> Resample 1m OHLCV       |
    |                                               |
    |  2. FEATURE FACTORY                           |
    |     fast.py  : MAJ a chaque tick              |
    |     mid.py   : MAJ a chaque barre             |
    |     slow.py  : MAJ quotidien                  |
    |                                               |
    |  3. QUALITY GATE                              |
    |     NaN? Stale? Outlier? -> REJECT            |
    |                                               |
    |  4. REGIME CLASSIFIER                         |
    |     LogReg -> P(impulse/reversal/breakout/    |
    |                 squeeze/calm/chop)             |
    |                                               |
    |  5. EDGE FORECASTER                           |
    |     Sequence 32 barres -> predictions         |
    |     q05, q50, q95, dir_hit, is_up, rv, sig   |
    |                                               |
    |  6. DECISION LOGIC                            |
    |     Score = 0.4*confidence + 0.2*entropy_inv  |
    |           + 0.2*novelty_inv + 0.2*disagree_inv|
    |     > 0.60 -> CONFIRM                         |
    |     < 0.60 -> REJECT                          |
    |                                               |
    |  7. RISK CONTROLLER                           |
    |     Kelly sizing                              |
    |     Max drawdown 15%                          |
    |     Max 20 trades/jour                        |
    |     Leverage 1.0x (pas de levier)             |
    |                                               |
    |  8. EXECUTION                                 |
    |     OrdersPlan -> Binance API                 |
    |     Fee: 4 bps, Slippage: 1-2 bps            |
    |     TP/SL trailing (15-60 min)                |
    +-----------------------------------------------+
```

---

## Categories de Features (130+)

```
+------------------------------------------------------------------+
|                    REPARTITION DES FEATURES                       |
+------------------------------------------------------------------+

  Volatilite (25)       |████████████████████████|  19%
  Trend / EMA (20+)     |████████████████████    |  16%
  Multi-Timeframe (20+) |████████████████████    |  16%
  Microstructure (10+)  |██████████             |   8%
  Z-Scores (8+)         |████████               |   6%
  Calendrier (9)        |█████████              |   7%
  Structure bougies (7) |███████                |   5%
  Flow & Intensite (8+) |████████               |   6%
  Liquidite (3)         |███                    |   2%
  RSI (2)               |██                     |   2%
  Returns (2)           |██                     |   2%
  Perpetual (4)         |████                   |   3%
  OHLCV base (5)        |█████                  |   4%
  Labels (26)           |                       |   -
                        +------------------------+
```

### Detail par categorie :

**Volatilite** - La plus grande famille, capture le regime de marche
```
rv_3, rv_5, rv_10, rv_15, rv_30, rv_60, rv_120, rv_240, rv_480, rv_720
sig_min_3, sig_min_5, ..., sig_min_720
ret_norm_3, ret_norm_5, ..., ret_norm_720
sigma_min (pondere: 50% sig_min_30 + 30% sig_min_120 + 20% sig_min_480)
rv_ann (annualisee)
```

**Trend / EMA** - Detection de tendance relative a la volatilite
```
dist_ema_atr_8, dist_ema_atr_21, dist_ema_atr_55, dist_ema_atr_144
ema_slope_8_5, ema_slope_21_5, ema_slope_55_5, ema_slope_144_5
(distance au EMA en unites ATR + pente normalisee)
```

**Microstructure** - Pression acheteur/vendeur
```
delta_volume = buy_volume - sell_volume
buy_pressure = buy_volume / total_volume
sell_pressure = sell_volume / total_volume
bs_ratio = buy_volume / sell_volume
dv_cum_5, dv_cum_15, dv_cum_60, dv_cum_240  (CVD cumule)
dv_z_60, dv_z_240  (z-score du flux)
```

---

## Architecture du Modele ML

```
+------------------------------------------------------------------+
|                  EDGE FORECASTER v4.2                              |
+------------------------------------------------------------------+
|                                                                  |
|  Input Shape: [batch_size=256, seq_len=32, n_features=39-83]     |
|                                                                  |
|  +------------------------------------------------------------+ |
|  | INPUT PROJECTION                                            | |
|  | Linear(n_features -> 192) + LayerNorm + Dropout(0.05)       | |
|  +------------------------------------------------------------+ |
|                          |                                       |
|                          v                                       |
|  +------------------------------------------------------------+ |
|  | TRANSFORMER ENCODER (x5 couches)                            | |
|  |                                                            | |
|  |  +------------------------------------------------------+ | |
|  |  | CAUSAL SELF-ATTENTION (6 tetes)                       | | |
|  |  |  Q, K, V = Linear(192, 192) x 3                      | | |
|  |  |  head_dim = 192/6 = 32                                | | |
|  |  |  attn = softmax(QK^T/sqrt(32) + ALiBi_bias) * V      | | |
|  |  |  + Masque causal (pas d'attention au futur)           | | |
|  |  +------------------------------------------------------+ | |
|  |                       |                                    | |
|  |                       v                                    | |
|  |  +------------------------------------------------------+ | |
|  |  | RMSNorm + Residual                                    | | |
|  |  +------------------------------------------------------+ | |
|  |                       |                                    | |
|  |                       v                                    | |
|  |  +------------------------------------------------------+ | |
|  |  | FFN: Linear(192->512) -> GELU -> Linear(512->192)     | | |
|  |  +------------------------------------------------------+ | |
|  |                       |                                    | |
|  |                       v                                    | |
|  |  +------------------------------------------------------+ | |
|  |  | RMSNorm + Residual + Dropout(0.05)                    | | |
|  |  +------------------------------------------------------+ | |
|  +------------------------------------------------------------+ |
|                          |                                       |
|                          v                                       |
|  +------------------------------------------------------------+ |
|  | EXTRACTION: Dernier token de la sequence                    | |
|  | shape: [batch, 192]                                         | |
|  +------------------------------------------------------------+ |
|                          |                                       |
|        +---------+-------+-------+---------+---------+           |
|        |         |       |       |         |         |           |
|        v         v       v       v         v         v           |
|    +------+ +------+ +------+ +------+ +------+ +------+        |
|    |  q05 | |  q50 | |  q95 | | dir  | | is_up| |  rv  |        |
|    | Lin  | | Lin  | | Lin  | | Lin  | | Lin  | | Lin  |        |
|    | (1)  | | (1)  | | (1)  | |sigm. | |sigm. | | (1)  |        |
|    +------+ +------+ +------+ +------+ +------+ +------+        |
|                                                                  |
|  Total parametres: ~1.2M                                         |
+------------------------------------------------------------------+

  LOSS FUNCTION:
  +------------------------------------------------------------+
  |  L = w1 * PinballLoss(q05, q50, q95)                       |
  |    + w2 * BCE(dir_hit, target_dir)                          |
  |    + w3 * BCE(is_up, target_is_up)                          |
  |    + w4 * MSE(rv_pred, rv_actual)                           |
  |                                                            |
  |  Optimiseur: AdamW (lr=1e-3, weight_decay=1e-5)           |
  |  Scheduler: Cosine avec warmup 10%                          |
  |  Gradient clip: max_norm=5.0                                |
  |  Mixed precision: AMP (float16 forward, float32 backward)  |
  +------------------------------------------------------------+
```

---

## Split Temporel des Donnees

```
Chronologie BTCUSDT 1-minute
|<================== 6.8 millions de barres ==================>|

2012        2017        2020        2022        2024     2025
  |           |           |           |           |        |
  |   Bitstamp seul       | Binance + Bitstamp   |        |
  |   (early data)        | (donnees riches)      |        |
  |                       |                       |        |
  |<============= TRAIN (70%) ============>|      |        |
  |                                        |      |        |
  |     ~4.8M barres                       |      |        |
  |     2012-01 -> 2022-01                 |      |        |
  |     Fit du scaler ICI                  |      |        |
  |                                        |      |        |
  |                                +-------+------+        |
  |                                | VAL (15%)    |        |
  |                                | ~1.0M barres |        |
  |                                | 2022 -> 2024 |        |
  |                                | Hyperparams  |        |
  |                                | Calibration  |        |
  |                                +--------------+        |
  |                                               |        |
  |                                        +------+--------+
  |                                        | TEST (15%)    |
  |                                        | ~1.0M barres  |
  |                                        | 2024 -> 2025  |
  |                                        | Eval. FINALE  |
  |                                        | JAMAIS touche |
  |                                        +---------------+

  IMPORTANT:
  - Pas de shuffle (ordre temporel strict)
  - Pas de fuite : scaler fit sur train uniquement
  - Embargo implicite : pas de chevauchement
```

---

## Regime Classifier (6 classes)

```
+------------------------------------------------------------------+
|                    CLASSIFICATION DE REGIME                        |
+------------------------------------------------------------------+

  Entree: rv_60, ema_12_dist (+ autres features de regime)
  Modele: Logistic Regression (class_weight='balanced')

  +------------+    +------------+    +------------+
  | IMPULSE    |    | REVERSAL   |    | BREAKOUT   |
  | Mouvement  |    | Retour     |    | Cassure    |
  | directionel|    | a la       |    | de range   |
  | fort       |    | moyenne    |    |            |
  +------------+    +------------+    +------------+

  +------------+    +------------+    +------------+
  | SQUEEZE    |    |    CALM    |    |    CHOP    |
  | Compression|    | Faible     |    | Mouvement  |
  | volatilite |    | volatilite |    | aleatoire  |
  | pre-move   |    | tendance   |    | sans dir.  |
  +------------+    +------------+    +------------+

  Utilisation:
  - Filtrage des signaux (pas de trade en CHOP)
  - Sizing des positions (plus gros en IMPULSE)
  - Selection de specialists (modeles conditionnels)
```

---

## Systeme de Decision Composite

```
+------------------------------------------------------------------+
|                   DECISION SCORING                                 |
+------------------------------------------------------------------+

  Score = 0.40 * confidence      (P(dir_hit) du modele)
        + 0.20 * (1 - entropy)   (certitude du regime)
        + 0.20 * (1 - novelty)   (familiarite des features)
        + 0.20 * (1 - disagree)  (accord entre modeles)

  +----------+----------+----------+----------+
  |   0.00   |   0.40   |   0.60   |   1.00   |
  +----------+----------+----------+----------+
  |          |          |          |          |
  |  REJECT  |  REJECT  | CONFIRM  | STRONG   |
  |          |          |  TRADE   | CONFIRM  |
  +----------+----------+----------+----------+

  Filtres supplementaires:
  - quality_flags != 0 -> INVALIDATE (donnees corrompues)
  - label_tradeable == 0 -> INVALIDATE (vol trop basse)
  - trades/jour >= 20 -> REJECT (limite quotidienne)
  - drawdown >= 15% -> KILLSWITCH (arret total)
```

---

## Risk Management

```
+------------------------------------------------------------------+
|                    CONTROLES DE RISQUE                             |
+------------------------------------------------------------------+

  +--------------------------+
  | KELLY CRITERION SIZING   |
  |                          |
  | f* = (p*b - q) / b      |
  |                          |
  | p = win_rate             |
  | b = avg_win / avg_loss   |
  | q = 1 - p                |
  |                          |
  | Position = f* * equity   |
  | (plafonee a 100%)        |
  +--------------------------+

  Parametres de risque:
  +----------------------------------+--------+
  | Parametre                        | Valeur |
  +----------------------------------+--------+
  | Fee par trade (aller-retour)     | 8 bps  |
  | Slippage estime                  | 1-2 bps|
  | Leverage maximum                 | 1.0x   |
  | Max drawdown avant arret         | 15%    |
  | Max trades par jour              | 20     |
  | Min taille trade (USD)           | $10    |
  | Horizon de detention             | 15-60m |
  +----------------------------------+--------+

  Cout total par trade aller-retour:
  +----------------------------------+
  | Fee maker+taker: 4+4 = 8 bps    |
  | Slippage: ~2 bps                 |
  | Total: ~10 bps (0.10%)           |
  |                                  |
  | => Le modele doit predire des    |
  |    mouvements > 10 bps pour      |
  |    etre profitable               |
  +----------------------------------+
```

---

## Suite de Validation (Anti-Triche ML)

```
+------------------------------------------------------------------+
|                  TESTS DE VALIDATION                               |
+------------------------------------------------------------------+

  TEST 1: NO-LOOKAHEAD
  +--------------------------------------------------+
  | Shuffle les donnees futures -> les features       |
  | au temps t ne changent PAS                        |
  | Script: no_lookahead_test.py (50 iterations)      |
  +--------------------------------------------------+

  TEST 2: WALK-FORWARD (5 folds)
  +--------------------------------------------------+
  | Fold 1: Train 2023.01-09 | Val 10-11 | Test 12   |
  | Fold 2: Train 2023.03-11 | Val 12-01 | Test 02   |
  | Fold 3: Train 2023.05-01 | Val 02-03 | Test 04   |
  | Fold 4: Train 2023.07-03 | Val 04-05 | Test 06   |
  | Fold 5: Train 2023.09-05 | Val 06-07 | Test 08   |
  |                                                  |
  | Criteres: sharpe_mean > 0.3, sharpe_std <= 0.5   |
  +--------------------------------------------------+

  TEST 3: SHUFFLE TEST (1000 permutations)
  +--------------------------------------------------+
  | Permute aleatoirement les trades 1000x            |
  | Calcule distribution null de Sharpe               |
  | p-value < 0.05 -> edge significatif               |
  +--------------------------------------------------+

  TEST 4: BASELINES COMPARATIVES
  +--------------------------------------------------+
  | Momentum (1h, 4h) : trend-following simple        |
  | Mean Reversion (1h) : contre-tendance             |
  | Random (100 runs) : distribution aleatoire        |
  | Le modele ML doit battre TOUS ces baselines       |
  +--------------------------------------------------+

  TEST 5: INFORMATION COEFFICIENT (IC)
  +--------------------------------------------------+
  | IC = corr(feature, return_fwd)                    |
  | Rank IC = spearman(feature, return_fwd)           |
  | Seuil minimum: IC > 0.02 pour top-5 features     |
  +--------------------------------------------------+

  TEST 6: SIGNAL-TO-NOISE RATIO (SNR)
  +--------------------------------------------------+
  | SNR = |mean(return_fwd)| / std(return_fwd)        |
  | < 0.01 -> FAIL (bruit pur)                       |
  | 0.01-0.03 -> WARNING (signal faible)              |
  | > 0.03 -> OK                                      |
  +--------------------------------------------------+

  TEST 7: BARRIERS vs FEES
  +--------------------------------------------------+
  | TP_median >= 1.5x fee_round_trip                  |
  | SL_median >= 1.0x fee_round_trip                  |
  | net_tp <= 0 : max 30% des trades                  |
  +--------------------------------------------------+

  TEST 8: SPLIT TEMPOREL
  +--------------------------------------------------+
  | train_end < val_start                             |
  | val_end < test_start                              |
  | AUCUN chevauchement tolere                        |
  +--------------------------------------------------+
```

---

## Sources de Donnees Alternatives (Scrapers)

```
+------------------------------------------------------------------+
|              DONNEES ALTERNATIVES COLLECTEES                       |
+------------------------------------------------------------------+

  +-------------------+    +-------------------+    +-------------------+
  | WHALE TRACKING    |    | NEWS & SENTIMENT  |    | ON-CHAIN DATA     |
  |                   |    |                   |    |                   |
  | Whale Alert API   |    | CoinDesk          |    | Bitcoin Mempool   |
  |  > $500K txs      |    | Cointelegraph     |    | Ethereum Events   |
  |  Toutes blockchains|   | The Block         |    | Solana (Solscan)  |
  |                   |    | Decrypt           |    |                   |
  | Arkham Intel      |    | CryptoPanic       |    | Etherscan         |
  |  Adresses labellees|   |                   |    | Mempool.space     |
  |  Flux entites     |    | BitcoinTalk       |    |                   |
  +-------------------+    +-------------------+    +-------------------+
          |                        |                        |
          v                        v                        v
  +--------------------------------------------------------------+
  |  MongoDB (whale_data) + S3 (qbia/bourse/raw/)               |
  |  Format Hive-partitioned: source=X/date=YYYY-MM-DD/         |
  +--------------------------------------------------------------+
```

---

## Metriques d'Evaluation du Modele

```
+------------------------------------------------------------------+
|              METRIQUES DE PERFORMANCE                              |
+------------------------------------------------------------------+

  CALIBRATION:
  +--------------------------------+
  | ECE (Expected Calibration      |
  |   Error)                       |     Seuil: < 0.25
  |                                |
  | Brier Score                    |     Plus bas = mieux
  |                                |
  | Temperature Scaling            |     T* optimal sur val
  +--------------------------------+

  TRADING PROXY:
  +--------------------------------+
  | Sharpe Ratio (annualise)       |     Seuil: > 0.5
  |   = mean(ret) / std(ret)      |
  |   * sqrt(525600)              |     (525600 min/an)
  |                                |
  | Win Rate                       |     Seuil: > 45%
  |                                |
  | Max Drawdown                   |     Seuil: < 20%
  |                                |
  | Profit Factor                  |     > 1.0 = profitable
  |   = sum(gains) / sum(pertes)  |
  |                                |
  | ROI net (apres frais)          |     > 0 requis
  +--------------------------------+

  QUALITE SIGNAL:
  +--------------------------------+
  | IC par feature (top-5)         |     > 0.02
  | SNR global                     |     > 0.01
  | p-value shuffle                |     < 0.05
  | Walk-forward stability         |     std < 0.5
  +--------------------------------+
```

---

## Configuration Hyperparametres

```
+------------------------------------------------------------------+
|              HYPERPARAMETRES (training_config.py)                  |
+------------------------------------------------------------------+

  MARCHE:
  +---------------------------+----------+
  | fee_bps                   |    4.0   |  Frais par side
  | slippage_bps              |    2.0   |  Glissement estime
  | leverage                  |    1.0   |  Pas de levier
  | max_drawdown_stop         |   0.15   |  Arret a 15%
  +---------------------------+----------+

  DONNEES:
  +---------------------------+----------+
  | symbol                    | BTCUSDT  |
  | timeframe                 |   1m     |
  | train_pct                 |   70%    |
  | val_pct                   |   15%    |
  | test_pct                  |   15%    |
  | n_folds (walk-forward)    |    5     |
  +---------------------------+----------+

  MODELE EDGE:
  +---------------------------+----------+
  | seq_len                   |   32     |  Fenetre d'entree
  | d_model                   |  192     |  Dimension embeddings
  | n_heads                   |    6     |  Tetes d'attention
  | n_layers                  |    5     |  Profondeur
  | d_ff                      |  512     |  FFN hidden
  | dropout                   |  0.05    |  Regularisation
  | horizon_minutes           |   15     |  Prediction a 15 min
  | tp_k (TP multiplier)      |  3.0     |  x ATR
  | sl_k (SL multiplier)      |  3.0     |  x ATR
  +---------------------------+----------+

  ENTRAINEMENT:
  +---------------------------+----------+
  | epochs                    |   40     |
  | batch_size                |  256     |
  | lr                        | 1e-3     |
  | optimizer                 | AdamW    |
  | scheduler                 | Cosine   |
  | warmup_pct                |  10%     |
  | weight_decay              | 1e-5     |
  | patience (early stop)     |   15     |
  | label_smoothing           | 0.02     |
  | grad_clip_max_norm        |  5.0     |
  | ema_decay                 | 0.999    |
  +---------------------------+----------+

  VALIDATION:
  +---------------------------+----------+
  | min_val_sharpe            |  0.5     |
  | max_val_drawdown          |  0.20    |
  | min_win_rate              |  0.45    |
  | brier_threshold           |  0.25    |
  | lookahead_tests           |   50     |
  +---------------------------+----------+
```

---

## Technologies Utilisees

```
+------------------------------------------------------------------+
|                    STACK TECHNIQUE                                 |
+------------------------------------------------------------------+

  ML & Data:
  +---------------------------+----------------------------------+
  | PyTorch                   | Transformer, training, inference |
  | Pandas / NumPy            | Feature engineering, preprocessing|
  | Parquet (zstd)            | Stockage datasets compresse     |
  | scikit-learn              | Regime classifier, scaler       |
  | Numba                     | Triple barrier acceleration     |
  +---------------------------+----------------------------------+

  Infrastructure:
  +---------------------------+----------------------------------+
  | AWS S3                    | Data lake (qbia bucket)         |
  | MongoDB Atlas             | Real-time + whale data          |
  | FastAPI                   | API REST + WebSocket            |
  | Scrapy                    | Web scraping framework          |
  +---------------------------+----------------------------------+

  Exchanges:
  +---------------------------+----------------------------------+
  | Binance                   | Principal (spot + perp)         |
  | Bybit                     | Secondaire                      |
  | OKX                       | Secondaire                      |
  | Deribit                   | Options / derivees              |
  +---------------------------+----------------------------------+

  Monitoring:
  +---------------------------+----------------------------------+
  | OpenTelemetry             | Tracing distribue               |
  | Prometheus                | Metriques temps reel            |
  | Kafka / Redis Streams     | Messaging asynchrone            |
  +---------------------------+----------------------------------+
```

---

## Resume Executif

| Composant | Statut | Description |
|-----------|--------|-------------|
| **Donnees** | 6.8M barres 1m, 2012-2025 | Binance + Bitstamp, 13 ans d'historique |
| **Features** | 130+ calculees, 39-83 utilisees | Volatilite, trend, microstructure, calendrier |
| **Labels** | Triple Barrier adaptatif | BUY/SELL/WAIT + 24 targets multi-horizon |
| **Modele** | Transformer 5L/6H, ~1.2M params | Multi-tache: quantiles + direction + vol |
| **Split** | 70/15/15 temporel strict | Pas de shuffle, scaler fit sur train |
| **Validation** | 8 tests anti-triche | No-lookahead, walk-forward, shuffle, baselines |
| **Risk** | Kelly + 15% DD stop | 20 trades/jour max, pas de levier |
| **Infra** | S3 + MongoDB + FastAPI | Multi-exchange, scraping alternatif |
| **Audit** | NO-GO (risques critiques) | 3 critiques, 10 hauts a corriger |
