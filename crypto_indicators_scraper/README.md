# Crypto Indicators Scraper 🚀

Un scraper Scrapy haute performance pour récupérer les indicateurs techniques du marché crypto minute par minute.

## 🎯 Fonctionnalités

- **Récupération multi-sources** : Binance, CryptoCompare, TaaPI, etc.
- **Rotation de proxy maximale** : Support de multiples sources de proxy (gratuits et premium)
- **Stockage S3 optimisé** : Sauvegarde directe dans AWS S3 au format Parquet
- **Indicateurs techniques avancés** :
  - Moyennes mobiles (SMA, EMA)
  - Momentum (RSI, MACD, Stochastic)
  - Volatilité (ATR, Bollinger Bands)
  - Tendance (ADX, CCI)
  - Support/Résistance (Pivot Points)
  - Volume (OBV, Volume SMA)
- **Traitement par batch** : Optimisation mémoire pour gérer des millions de points de données
- **Déduplication automatique** : Évite les doublons lors de la sauvegarde
- **Calcul d'indicateurs custom** : Pipeline de calcul pour les indicateurs non fournis par les APIs

## 📋 Prérequis

- Python 3.8+
- Compte AWS avec accès S3
- (Optionnel) Clés API pour CryptoCompare, TaaPI

## 🔧 Installation

```bash
cd crypto_indicators_scraper

# Installer les dépendances
pip install -r requirements.txt

# Configurer AWS credentials
aws configure
```

## 🚀 Utilisation

### Test rapide (2-3 cryptos)

```bash
# Tester avec BTCUSDT et ETHUSDT pour 2024
python run_scraper.py \
    --symbols BTCUSDT,ETHUSDT \
    --start-year 2024 \
    --end-year 2024 \
    --no-proxy
```

### Scraping de symboles spécifiques

```bash
# Scraper plusieurs cryptos pour une année
python run_scraper.py \
    --symbols BTCUSDT,ETHUSDT,BNBUSDT,ADAUSDT,SOLUSDT \
    --start-year 2023 \
    --end-year 2024 \
    --proxy-enabled
```

### Scraping complet de toutes les cryptos

```bash
# Scraper TOUTES les cryptos de votre S3 pour toutes les années
python run_scraper.py \
    --start-year 2017 \
    --end-year 2025 \
    --proxy-enabled \
    --concurrent-requests 32

# Ou utiliser le script bash
chmod +x run_full_scrape.sh
./run_full_scrape.sh
```

### Options avancées

```bash
python run_scraper.py \
    --symbols BTCUSDT,ETHUSDT \
    --start-year 2024 \
    --proxy-enabled \
    --concurrent-requests 64 \
    --batch-size 2000 \
    --cryptocompare-api-key YOUR_KEY \
    --taapi-api-key YOUR_KEY \
    --debug
```

## 🔑 Configuration des API Keys

### CryptoCompare (Gratuit)
1. Créer un compte sur [CryptoCompare](https://min-api.cryptocompare.com/)
2. Obtenir votre clé API
3. Ajouter à la commande : `--cryptocompare-api-key YOUR_KEY`

### TaaPI (Technical Analysis API)
1. Créer un compte sur [TaaPI](https://taapi.io/)
2. Obtenir votre clé API (plan gratuit disponible)
3. Ajouter à la commande : `--taapi-api-key YOUR_KEY`

## 🌐 Configuration des Proxies

Le scraper supporte plusieurs sources de proxy :

### Proxies gratuits (par défaut)
- free-proxy-list.net
- proxyscrape.com
- geonode.com
- pubproxy.com

### Proxies personnalisés

Modifier `settings.py` :

```python
PROXY_SOURCES = [
    'free_proxy_list',
    'proxy_scrape',
    'geonode',
    # Ajouter un fichier de proxies
    'file:///path/to/your/proxies.txt',
    # Ajouter une URL de proxies
    'https://your-proxy-api.com/list',
]
```

Format du fichier de proxies :
```
192.168.1.1:8080
http://192.168.1.2:3128
http://user:pass@192.168.1.3:8080
```

### Désactiver les proxies

```bash
python run_scraper.py --no-proxy
```

## 📊 Structure des données sauvegardées

Les données sont sauvegardées dans S3 avec la structure suivante :

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

### Colonnes des fichiers Parquet

- **Identification** : symbol, timestamp, timeframe, source
- **OHLCV** : open, high, low, close, volume
- **Moyennes mobiles** : sma_7, sma_25, sma_99, ema_7, ema_25, ema_99
- **Momentum** : rsi, rsi_14, stoch_k, stoch_d, macd, macd_signal, macd_histogram
- **Volatilité** : atr, bollinger_upper, bollinger_middle, bollinger_lower
- **Volume** : volume_sma, obv
- **Tendance** : adx, cci
- **Support/Résistance** : pivot_point, resistance_1, resistance_2, support_1, support_2
- **Metadata** : scraped_at

## 📈 Utilisation des données scrapées

### Charger les données avec Python

```python
import pandas as pd
import boto3

# Charger un fichier depuis S3
s3 = boto3.client('s3')
s3.download_file('qbia', 'bourse/indicators/indicators_1m_2024/BTCUSDT_2024_01_indicators.parquet', 'local_file.parquet')

df = pd.read_parquet('local_file.parquet')
print(df.head())
```

### Intégrer avec votre pipeline ML

```python
from ai.TRAIN.data.s3_data_source import S3DataSource

# Charger les données OHLCV
s3_source = S3DataSource(bucket='qbia', prefix='bourse/mintrad')
ohlcv_df = s3_source.fetch_symbol_data('BTCUSDT', 2024)

# Charger les indicateurs scrapés
indicators_df = pd.read_parquet('s3://qbia/bourse/indicators/indicators_1m_2024/BTCUSDT_2024_01_indicators.parquet')

# Merger les données
merged = pd.merge(
    ohlcv_df,
    indicators_df,
    on=['symbol', 'timestamp'],
    how='left'
)
```

## ⚙️ Optimisation des performances

### Augmenter le nombre de requêtes concurrentes

```bash
python run_scraper.py --concurrent-requests 64
```

### Augmenter la taille des batchs S3

```bash
python run_scraper.py --batch-size 5000
```

### Modifier les settings directement

Éditer `crypto_indicators_scraper/settings.py` :

```python
CONCURRENT_REQUESTS = 64
CONCURRENT_REQUESTS_PER_DOMAIN = 16
DOWNLOAD_DELAY = 0.25
S3_BATCH_SIZE = 5000
```

## 🐛 Debugging

### Mode debug

```bash
python run_scraper.py --debug
```

### Logs détaillés

Les logs sont affichés en temps réel avec :
- Nombre de proxies chargés
- Nombre d'items scrapés
- Nombre de batches uploadés sur S3
- Erreurs et warnings

### Vérifier les données sur S3

```bash
# Lister les fichiers créés
aws s3 ls s3://qbia/bourse/indicators/indicators_1m_2024/

# Télécharger un fichier pour inspection
aws s3 cp s3://qbia/bourse/indicators/indicators_1m_2024/BTCUSDT_2024_01_indicators.parquet .
```

## 📝 Architecture du projet

```
crypto_indicators_scraper/
├── crypto_indicators_scraper/
│   ├── __init__.py
│   ├── items.py                    # Définition des items Scrapy
│   ├── settings.py                 # Configuration Scrapy
│   ├── middlewares/
│   │   ├── __init__.py
│   │   └── proxy_middleware.py     # Rotation de proxy + User-Agent
│   ├── pipelines/
│   │   ├── __init__.py
│   │   └── s3_pipeline.py          # Sauvegarde S3 + Calcul d'indicateurs
│   └── spiders/
│       ├── __init__.py
│       └── crypto_indicators_spider.py  # Spider principal
├── scrapy.cfg                      # Config Scrapy
├── requirements.txt                # Dépendances Python
├── run_scraper.py                  # Script de lancement principal
├── run_quick_test.sh              # Test rapide
├── run_full_scrape.sh             # Scraping complet
└── README.md                       # Cette documentation
```

## 🔄 Workflow typique

1. **Lancer un test** : Vérifier que tout fonctionne avec quelques cryptos
   ```bash
   ./run_quick_test.sh
   ```

2. **Scraping par étapes** : Scraper année par année pour éviter les timeouts
   ```bash
   python run_scraper.py --start-year 2024 --end-year 2024
   python run_scraper.py --start-year 2023 --end-year 2023
   # etc.
   ```

3. **Vérification** : Vérifier les données sur S3
   ```bash
   aws s3 ls s3://qbia/bourse/indicators/indicators_1m_2024/ | wc -l
   ```

4. **Intégration** : Utiliser les données dans votre pipeline ML

## 🚨 Limitations et considérations

- **Rate limiting** : Certaines APIs ont des limites de requêtes (utiliser les proxies pour les contourner)
- **Coût S3** : Le stockage peut devenir coûteux avec des millions de fichiers (utiliser le batching)
- **Mémoire** : Pour de très gros volumes, ajuster `MEMUSAGE_LIMIT_MB` dans settings.py
- **APIs gratuites** : Les APIs gratuites ont des limitations, envisager des plans payants pour un usage intensif

## 🤝 Contribution

Pour améliorer le scraper :
1. Ajouter de nouvelles sources d'indicateurs dans `crypto_indicators_spider.py`
2. Ajouter de nouveaux calculs d'indicateurs dans `s3_pipeline.py` (CalculatedIndicatorsPipeline)
3. Améliorer le système de proxy dans `proxy_middleware.py`

## 📄 Licence

Projet privé - Tous droits réservés

## 🆘 Support

En cas de problème :
1. Vérifier les logs en mode `--debug`
2. Vérifier vos credentials AWS
3. Vérifier la connectivité aux APIs
4. Vérifier que les proxies fonctionnent (`--no-proxy` pour tester sans)
