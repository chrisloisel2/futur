# 🏗️ Architecture Pipeline v2.0

## Vue d'ensemble

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         CRYPTO DATA PIPELINE v2.0                       │
│                            Production Ready                              │
└─────────────────────────────────────────────────────────────────────────┘

┌────────────────┐     ┌────────────────┐     ┌────────────────┐
│   External     │     │   External     │     │   External     │
│   CCXT API     │────▶│  Glassnode API │────▶│  Redis Cache   │
│  (Binance...)  │     │  (On-chain)    │     │  (Optional)    │
└────────────────┘     └────────────────┘     └────────────────┘
        │                      │                      │
        │                      │                      │
        ▼                      ▼                      ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                            DATA LAYER                                   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌─────────────────────┐        ┌──────────────────────┐              │
│  │  CcxtDataSource     │        │  GlassnodeClient     │              │
│  │  • Circuit Breaker  │        │  • HTTP Client       │              │
│  │  • Error Handling   │        │  • Cache Support     │              │
│  │  • Backoff Strategy │        │  • Timezone UTC      │              │
│  │  • Timezone UTC     │        │                      │              │
│  └─────────────────────┘        └──────────────────────┘              │
│           │                              │                              │
│           └──────────────┬───────────────┘                              │
│                          ▼                                               │
│                  ┌──────────────┐                                       │
│                  │ RedisCache   │                                       │
│                  │ • Fallback   │                                       │
│                  │ • Reconnect  │                                       │
│                  │ • Timeout 2s │                                       │
│                  └──────────────┘                                       │
└─────────────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         VALIDATION LAYER                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │               DataQualityValidator                               │  │
│  │  ┌──────────────┐  ┌─────────────┐  ┌────────────────────┐     │  │
│  │  │ Temporal     │  │   OHLC      │  │  Outlier           │     │  │
│  │  │ Gaps Check   │  │ Consistency │  │  Detection         │     │  │
│  │  └──────────────┘  └─────────────┘  └────────────────────┘     │  │
│  │                                                                  │  │
│  │  ┌──────────────┐  ┌─────────────┐  ┌────────────────────┐     │  │
│  │  │ Volatility   │  │  Missing    │  │  Format            │     │  │
│  │  │ Spikes       │  │  Values     │  │  Changes           │     │  │
│  │  └──────────────┘  └─────────────┘  └────────────────────┘     │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      FEATURE ENGINEERING LAYER                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────────────────┐  ┌──────────────────────┐                    │
│  │  Technical Features  │  │  Advanced Features   │                    │
│  │  • SMA (multi-win)   │  │  • RSI Divergence    │                    │
│  │  • EMA (multi-win)   │  │  • Vol Regimes       │                    │
│  │  • RSI (7,14,21,30)  │  │  • Lag Features      │                    │
│  │  • MACD, Stochastic  │  │  • Cross-market      │                    │
│  │  • ATR, ADX, CCI     │  │                      │                    │
│  └──────────────────────┘  └──────────────────────┘                    │
│                                                                          │
│  ┌──────────────────────┐  ┌──────────────────────┐                    │
│  │  On-chain Features   │  │  Volume Features     │                    │
│  │  • Diff              │  │  • OBV               │                    │
│  │  • Pct Change        │  │  • VWAP              │                    │
│  │  • Z-scores (multi)  │  │  • MFI               │                    │
│  └──────────────────────┘  └──────────────────────┘                    │
└─────────────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      NORMALIZATION LAYER                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                   AdaptiveNormalizer                             │  │
│  │                                                                  │  │
│  │  Training Mode:              Production Mode:                   │  │
│  │  ┌──────────┐                 ┌──────────────┐                 │  │
│  │  │  fit()   │                 │ load_state() │                 │  │
│  │  │  ├─ Train│                 │  ├─ Saved    │                 │  │
│  │  │  │  Data │                 │  │   params  │                 │  │
│  │  │  ▼       │                 │  ▼           │                 │  │
│  │  │ Compute  │                 │ Transform    │                 │  │
│  │  │ Stats    │                 │ New Data     │                 │  │
│  │  └──────────┘                 └──────────────┘                 │  │
│  │       │                              │                          │  │
│  │       ▼                              │                          │  │
│  │  ┌──────────┐                       │                          │  │
│  │  │transform │◀──────────────────────┘                          │  │
│  │  │  (train) │                                                  │  │
│  │  │transform │                                                  │  │
│  │  │  (test)  │                                                  │  │
│  │  └──────────┘                                                  │  │
│  │       │                                                         │  │
│  │       ▼                                                         │  │
│  │  ┌──────────┐                                                  │  │
│  │  │save_state│                                                  │  │
│  │  └──────────┘                                                  │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      OPTIMIZATION LAYER                                 │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────────────────┐  ┌──────────────────────┐                    │
│  │  Memory Optimizer    │  │  Data Downsampler    │                    │
│  │  • Downcast types    │  │  • Keep recent full  │                    │
│  │  • Category convert  │  │  • Downsample old    │                    │
│  │  • -40 to -60% RAM   │  │  • Aggregate OHLCV   │                    │
│  └──────────────────────┘  └──────────────────────┘                    │
└─────────────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      OBSERVABILITY LAYER                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌────────────────────┐  ┌────────────────────┐  ┌──────────────────┐ │
│  │  Structured        │  │  Metrics           │  │  Configuration   │ │
│  │  Logging           │  │  Collector         │  │  Management      │ │
│  │  • JSON format     │  │  • API calls       │  │  • config.yaml   │ │
│  │  • Log levels      │  │  • Cache hit rate  │  │  • .env secrets  │ │
│  │  • Rotation        │  │  • Execution time  │  │  • Validation    │ │
│  │  • Contextual      │  │  • Errors count    │  │                  │ │
│  └────────────────────┘  └────────────────────┘  └──────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

## Flux de données

### Training Pipeline

```
1. FETCH DATA
   ┌──────────────┐
   │ CcxtDataSource│
   │ .fetch_      │
   │  historical  │
   └──────┬───────┘
          │
          ▼
   ┌──────────────┐
   │ ohlcv_to_df  │
   │ (UTC aware)  │
   └──────┬───────┘

2. VALIDATE
          │
          ▼
   ┌──────────────┐
   │ DataQuality  │
   │ Validator    │
   └──────┬───────┘
          │
     [is_valid?]
          │
          ▼

3. FEATURES
   ┌──────────────┐
   │build_feature │
   │    _set      │
   └──────┬───────┘
          │
          ▼
   [60+ features]

4. SPLIT
          │
          ▼
   ┌─────────────┬──────────┐
   │   Train     │   Test   │
   └──────┬──────┴────┬─────┘

5. NORMALIZE
          │           │
          ▼           │
   ┌──────────────┐   │
   │ normalizer   │   │
   │   .fit()     │   │
   └──────┬───────┘   │
          │           │
          ▼           ▼
   ┌──────────────────────┐
   │  .transform(train)   │
   │  .transform(test)    │
   └──────┬───────────────┘

6. SAVE
          │
          ▼
   ┌──────────────┐
   │ .save_state()│
   └──────────────┘
```

### Production Pipeline

```
1. LOAD DATA
   ┌──────────────┐
   │ fetch_ohlcv  │
   │ (last 500)   │
   └──────┬───────┘
          │
          ▼
   ┌──────────────┐
   │ ohlcv_to_df  │
   └──────┬───────┘

2. FEATURES
          │
          ▼
   ┌──────────────┐
   │build_feature │
   │    _set      │
   └──────┬───────┘

3. NORMALIZE
          │
          ▼
   ┌──────────────┐
   │ .load_state()│
   └──────┬───────┘
          │
          ▼
   ┌──────────────┐
   │ .transform() │
   └──────┬───────┘

4. PREDICT
          │
          ▼
   ┌──────────────┐
   │ ML Model     │
   │ .predict()   │
   └──────┬───────┘
          │
          ▼
   [Trading Signal]
```

## Circuit Breaker State Machine

```
┌─────────────────────────────────────────────────────────┐
│                    CIRCUIT BREAKER                      │
└─────────────────────────────────────────────────────────┘

      Initial State
           │
           ▼
    ┌────────────┐
    │   CLOSED   │◀─────────────────┐
    │ (Normal)   │                  │
    └────┬───────┘                  │
         │                           │
         │ Request → Success        │
         │ (reset counter)          │ Success
         │                           │
         │ Request → Fail            │
         │ (increment counter)       │
         │                           │
         ▼                           │
    [Counter >= Threshold?]          │
         │                           │
         │ Yes                       │
         ▼                           │
    ┌────────────┐                  │
    │    OPEN    │                  │
    │ (Blocking) │                  │
    └────┬───────┘                  │
         │                           │
         │ Wait timeout (5min)      │
         │                           │
         ▼                           │
    ┌────────────┐                  │
    │ HALF-OPEN  │                  │
    │ (Testing)  │                  │
    └────┬───────┘                  │
         │                           │
         ├─ Request → Success ───────┘
         │
         └─ Request → Fail ──────┐
                                 │
                                 ▼
                          [Back to OPEN]
```

## Cache Fallback Strategy

```
┌─────────────────────────────────────────────────────────┐
│                      CACHE STRATEGY                     │
└─────────────────────────────────────────────────────────┘

Request Cache Key
      │
      ▼
┌──────────────┐
│ Redis Alive? │
└──────┬───────┘
       │
   ┌───┴────┐
   │ Yes    │ No
   │        │
   ▼        ▼
┌────┐   ┌───────────┐
│Redis│  │Local Cache│
└──┬─┘   └─────┬─────┘
   │           │
   │ Try Get   │ Get
   │           │
   ▼           ▼
[Success?]  [Return]
   │
   ├─ Yes → Return
   │
   └─ Error (Timeout/Connection)
       │
       ├─ Log Warning
       │
       ├─ Mark Redis Down
       │
       ├─ Fallback to Local
       │
       └─ Start Reconnection Timer
              │
              ▼
          [Every 60s]
              │
              ├─ Try Reconnect
              │
              └─ Success → Mark Redis Up
```

## Data Flow Diagram

```
┌──────────┐         ┌──────────┐         ┌──────────┐
│ External │────────▶│  Cache   │────────▶│  Data    │
│   APIs   │         │  Layer   │         │  Layer   │
└──────────┘         └──────────┘         └──────────┘
     │                    │                     │
     │                    │                     │
     ▼                    ▼                     ▼
 [Network]           [Redis/Local]         [DataFrame]
 [Errors]            [Fallback]            [UTC aware]
     │                    │                     │
     └────────────────────┴─────────────────────┘
                          │
                          ▼
                   ┌──────────┐
                   │Validation│
                   │  Layer   │
                   └────┬─────┘
                        │
                        ▼
                  [Quality OK?]
                        │
                ┌───────┴────────┐
                │ Yes            │ No
                │                │
                ▼                ▼
         ┌──────────┐      [Log Errors]
         │ Features │      [Raise Exception]
         │  Layer   │
         └────┬─────┘
              │
              ▼
       ┌──────────┐
       │Normalize │
       │  Layer   │
       └────┬─────┘
            │
            ▼
      ┌──────────┐
      │ Optimize │
      │  Layer   │
      └────┬─────┘
           │
           ▼
     [ML Ready Data]
```

## Module Dependencies

```
config_loader.py
    └─ (no deps)

logging_config.py
    └─ (no deps)

cache.py
    └─ logging_config

data_sources.py
    ├─ cache
    └─ logging_config

data_quality.py
    └─ logging_config

normalization.py
    └─ (no deps)

features.py
    └─ (no deps)

memory_optimizer.py
    └─ logging_config

__init__.py
    ├─ cache
    ├─ config_loader
    ├─ data_quality
    ├─ data_sources
    ├─ features
    ├─ logging_config
    ├─ memory_optimizer
    └─ normalization

example_usage.py
    └─ (imports all from __init__)
```

## Deployment Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    PRODUCTION SETUP                      │
└─────────────────────────────────────────────────────────┘

┌──────────────────┐     ┌──────────────────┐
│   Application    │────▶│   Redis Server   │
│   Server         │     │   (Cache)        │
│   • Pipeline     │     │   Port 6379      │
│   • ML Model     │     └──────────────────┘
└────────┬─────────┘              │
         │                        │
         ▼                        ▼
    [API Calls]            [Cache Ops]
         │                        │
         └────────┬───────────────┘
                  │
                  ▼
         ┌────────────────┐
         │  Log Aggregator│
         │  (ELK/Splunk)  │
         └────────┬───────┘
                  │
                  ▼
         ┌────────────────┐
         │   Monitoring   │
         │   (Grafana)    │
         └────────────────┘

Files needed on production:
• pipeline/ (all .py files)
• config.yaml
• .env (with API keys)
• normalizer.json (saved state)
• requirements.txt
```

## Security Layers

```
┌─────────────────────────────────────────────────────────┐
│                    SECURITY MODEL                        │
└─────────────────────────────────────────────────────────┘

1. Secrets Management
   ┌──────────┐
   │   .env   │ ← Never committed to git
   └────┬─────┘
        │
        ▼
   [Environment Variables]
        │
        └─→ ConfigLoader (validation)

2. API Key Protection
   • Not in code
   • Not in logs
   • Environment only
   • Validation on load

3. Input Validation
   • Data quality checks
   • Type validation
   • Range checks
   • OHLC consistency

4. Error Handling
   • No sensitive data in logs
   • Circuit breaker prevents DoS
   • Timeouts prevent hanging
   • Graceful degradation
```

---

**Architecture Version**: 2.0.0
**Last Updated**: 2024
**Author**: Pipeline Team
