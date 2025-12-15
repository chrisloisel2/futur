# 📁 Structure du Projet Crypto Indicators Scraper

## 🎯 Vue d'ensemble

Ce projet est un scraper Scrapy professionnel pour récupérer les indicateurs techniques du marché crypto minute par minute depuis plusieurs sources (Binance, CryptoCompare, TaaPI, etc.) et les sauvegarder sur AWS S3.

## 📂 Arborescence complète

```
crypto_indicators_scraper/
│
├── 📄 scrapy.cfg                          # Configuration Scrapy
├── 📄 requirements.txt                    # Dépendances Python
├── 📄 .env.example                        # Template variables d'environnement
│
├── 📖 README.md                           # Documentation complète
├── 📖 QUICK_START.md                      # Guide de démarrage rapide
├── 📖 STRUCTURE.md                        # Ce fichier
│
├── 🔧 run_scraper.py                      # Script principal de lancement ⭐
├── 🔧 check_s3_data.py                    # Vérification des données S3
├── 🔧 run_quick_test.sh                   # Test rapide (2 cryptos)
├── 🔧 run_full_scrape.sh                  # Scraping complet (toutes cryptos)
│
└── crypto_indicators_scraper/             # Package principal
    │
    ├── __init__.py
    │
    ├── 📝 items.py                        # Définition des items Scrapy
    │   └── CryptoIndicatorItem            # Item avec 30+ champs d'indicateurs
    │
    ├── ⚙️ settings.py                      # Configuration Scrapy ⭐
    │   ├── Concurrent requests (32)
    │   ├── Proxy rotation
    │   ├── AutoThrottle
    │   ├── Retry policy
    │   └── S3 settings
    │
    ├── middlewares/                       # Middlewares Scrapy
    │   ├── __init__.py
    │   └── proxy_middleware.py            # Rotation de proxy ⭐
    │       ├── ProxyRotationMiddleware    # Gestion multi-sources proxy
    │       └── UserAgentRotationMiddleware # Rotation User-Agent
    │
    ├── pipelines/                         # Pipelines Scrapy
    │   ├── __init__.py
    │   └── s3_pipeline.py                 # Sauvegarde S3 ⭐
    │       ├── ValidationPipeline         # Validation des données
    │       ├── CalculatedIndicatorsPipeline # Calcul d'indicateurs custom
    │       └── S3IndicatorsPipeline       # Upload vers S3 en batch
    │
    └── spiders/                           # Spiders Scrapy
        ├── __init__.py
        └── crypto_indicators_spider.py    # Spider principal ⭐
            └── CryptoIndicatorsSpider     # Scraping multi-sources
```

## 🔑 Fichiers clés (⭐)

### 1. `run_scraper.py` - Script de lancement

**Rôle** : Point d'entrée principal pour lancer le scraping

**Usage** :
```bash
python run_scraper.py --symbols BTCUSDT,ETHUSDT --start-year 2024
```

**Paramètres** :
- `--symbols` : Cryptos à scraper (ou auto depuis S3)
- `--start-year` / `--end-year` : Période à scraper
- `--proxy-enabled` / `--no-proxy` : Activer/désactiver proxies
- `--concurrent-requests` : Nombre de requêtes simultanées
- `--batch-size` : Taille des batchs S3
- `--cryptocompare-api-key` : Clé API CryptoCompare
- `--taapi-api-key` : Clé API TaaPI
- `--debug` : Mode debug

### 2. `settings.py` - Configuration

**Rôle** : Toute la configuration Scrapy

**Paramètres importants** :
```python
CONCURRENT_REQUESTS = 32              # Requêtes simultanées
DOWNLOAD_DELAY = 0.5                  # Délai entre requêtes
RETRY_TIMES = 5                       # Nombre de retry
S3_BUCKET = 'qbia'                    # Bucket S3
S3_INDICATORS_PREFIX = 'bourse/indicators'  # Préfixe S3
PROXY_ROTATION_ENABLED = True         # Activer proxies
```

### 3. `proxy_middleware.py` - Gestion des proxies

**Rôle** : Rotation automatique de proxy depuis multiple sources

**Sources supportées** :
- `free_proxy_list` : free-proxy-list.net
- `proxy_scrape` : proxyscrape.com
- `geonode` : geonode.com
- `pubproxy` : pubproxy.com
- `file://path/to/proxies.txt` : Fichier local
- `https://api.com/proxies` : URL custom

**Features** :
- Blacklist automatique des proxies qui échouent
- Fallback sur connexion directe si nécessaire
- Rotation User-Agent
- Statistiques de proxy

### 4. `s3_pipeline.py` - Sauvegarde S3

**Rôle** : Traiter les items et les sauvegarder sur S3

**3 pipelines** :

#### ValidationPipeline (priorité 100)
- Valide les champs requis
- Valide les types de données
- Drop les items invalides

#### CalculatedIndicatorsPipeline (priorité 200)
- Calcule les indicateurs non fournis par APIs :
  - SMA 7, 25, 99
  - EMA 7, 25, 99
  - RSI 14
  - Pivot Points
  - Support/Résistance

#### S3IndicatorsPipeline (priorité 300)
- Batch les items par (symbol, année, mois)
- Sauvegarde en Parquet sur S3
- Merge avec données existantes
- Déduplication automatique

### 5. `crypto_indicators_spider.py` - Spider principal

**Rôle** : Scraper les indicateurs depuis plusieurs APIs

**Sources d'indicateurs** :

#### Binance API (Gratuit)
- OHLCV de base
- Volume
- Trades count

#### CryptoCompare API (Gratuit avec clé)
- OHLCV alternatif
- Volume from/to
- Validation croisée

#### TaaPI (Payant)
- RSI
- MACD (value, signal, histogram)
- Bollinger Bands (upper, middle, lower)
- Stochastic (K, D)
- ADX
- CCI
- ATR

**Workflow** :
1. Charge les symboles depuis S3 (ou liste fournie)
2. Génère les requêtes par (symbol, année, mois)
3. Fait les requêtes aux différentes APIs
4. Parse les réponses
5. Yield les items vers les pipelines

## 📊 Format des données

### Structure S3

```
s3://qbia/bourse/indicators/
└── indicators_1m_YYYY/
    └── SYMBOL_YYYY_MM_indicators.parquet
```

### Exemple

```
s3://qbia/bourse/indicators/
├── indicators_1m_2024/
│   ├── BTCUSDT_2024_01_indicators.parquet
│   ├── BTCUSDT_2024_02_indicators.parquet
│   ├── ETHUSDT_2024_01_indicators.parquet
│   └── ...
├── indicators_1m_2023/
│   └── ...
└── ...
```

### Colonnes Parquet

| Catégorie | Champs |
|-----------|--------|
| **Identification** | symbol, timestamp, timeframe, source |
| **OHLCV** | open, high, low, close, volume |
| **Moving Averages** | sma_7, sma_25, sma_99, ema_7, ema_25, ema_99 |
| **Momentum** | rsi, rsi_14, stoch_k, stoch_d, macd, macd_signal, macd_histogram |
| **Volatility** | atr, bollinger_upper, bollinger_middle, bollinger_lower |
| **Volume** | volume_sma, obv |
| **Trend** | adx, cci |
| **Support/Resistance** | pivot_point, resistance_1, resistance_2, support_1, support_2 |
| **Metadata** | scraped_at |

## 🔄 Workflow de scraping

```
┌─────────────────┐
│  run_scraper.py │
└────────┬────────┘
         │
         ▼
┌──────────────────────────────┐
│  CryptoIndicatorsSpider      │
│  - Load symbols from S3      │
│  - Generate requests         │
└────────┬─────────────────────┘
         │
         ▼
┌──────────────────────────────┐
│  ProxyRotationMiddleware     │
│  - Assign proxy to request   │
└────────┬─────────────────────┘
         │
         ▼
┌──────────────────────────────┐
│  Download (Binance, CC, etc) │
└────────┬─────────────────────┘
         │
         ▼
┌──────────────────────────────┐
│  Parse response              │
│  - parse_binance()           │
│  - parse_cryptocompare()     │
│  - parse_taapi()             │
└────────┬─────────────────────┘
         │
         ▼
┌──────────────────────────────┐
│  ValidationPipeline          │
│  - Check required fields     │
└────────┬─────────────────────┘
         │
         ▼
┌──────────────────────────────┐
│  CalculatedIndicatorsPipeline│
│  - Calculate SMA/EMA/RSI     │
└────────┬─────────────────────┘
         │
         ▼
┌──────────────────────────────┐
│  S3IndicatorsPipeline        │
│  - Batch items               │
│  - Save to S3 (Parquet)      │
└──────────────────────────────┘
```

## 🎛️ Configuration avancée

### Modifier les sources de proxy

Éditer `settings.py` :

```python
PROXY_SOURCES = [
    'free_proxy_list',           # Gratuit
    'proxy_scrape',              # Gratuit
    'geonode',                   # Gratuit
    'file:///path/proxies.txt',  # Fichier local
    'https://api.com/proxies',   # API custom
]
```

### Modifier les APIs d'indicateurs

Éditer `crypto_indicators_spider.py`, méthode `_generate_api_requests()` :

```python
# Ajouter une nouvelle API
new_api_url = f"https://newapi.com/indicators?symbol={symbol}"
yield Request(
    url=new_api_url,
    callback=self.parse_new_api,
    meta=meta,
)
```

### Modifier les indicateurs calculés

Éditer `s3_pipeline.py`, classe `CalculatedIndicatorsPipeline` :

```python
def _calculate_indicators(self, item, historical_data):
    df = pd.DataFrame(historical_data)

    # Ajouter un nouvel indicateur
    item['custom_indicator'] = df['close'].rolling(20).std()
```

## 📈 Performance et optimisation

### Paramètres de performance

| Paramètre | Valeur par défaut | Recommandé | Maximum |
|-----------|-------------------|------------|---------|
| `CONCURRENT_REQUESTS` | 32 | 32-64 | 128 |
| `DOWNLOAD_DELAY` | 0.5s | 0.25-1s | 0s |
| `S3_BATCH_SIZE` | 1000 | 1000-5000 | 10000 |
| `RETRY_TIMES` | 5 | 3-10 | 20 |

### Bottlenecks potentiels

1. **Rate limiting API** → Utiliser proxies
2. **S3 upload lent** → Augmenter batch size
3. **Mémoire** → Réduire concurrent requests ou batch size
4. **Proxies lents** → Désactiver ou utiliser proxies premium

## 🧪 Testing

### Test minimal (2 cryptos, 1 mois)
```bash
python run_scraper.py --symbols BTCUSDT,ETHUSDT --start-year 2024 --end-year 2024 --no-proxy
```
**Durée** : ~5-10 minutes

### Test moyen (10 cryptos, 1 an)
```bash
python run_scraper.py --symbols BTCUSDT,ETHUSDT,BNBUSDT --start-year 2024 --end-year 2024
```
**Durée** : ~30-60 minutes

### Test complet (toutes cryptos, toutes années)
```bash
./run_full_scrape.sh
```
**Durée** : Plusieurs heures à jours

## 🔧 Maintenance

### Logs
- Les logs sont affichés en temps réel
- Mode debug : `--debug`
- Format : `YYYY-MM-DD HH:MM:SS [nom] LEVEL: message`

### Monitoring
- Stats de scraping affichées à la fin
- Stats de proxy (working, failed, blacklisted)
- Stats de pipeline (items processed, saved, errors)

### Mise à jour
1. Ajouter de nouvelles sources d'indicateurs
2. Améliorer les calculs d'indicateurs
3. Optimiser les proxies
4. Ajouter de nouveaux champs à `CryptoIndicatorItem`

---

**Version** : 1.0
**Auteur** : Christopher
**Date** : Décembre 2025
