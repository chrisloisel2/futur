# 📊 Guide des Données Alternatives pour Crypto

## 🎯 Vue d'ensemble

En plus des indicateurs techniques traditionnels, le scraper collecte maintenant des **données alternatives et abstraites** qui influencent le marché crypto :

- 🐦 **Sentiment social** (Twitter, Reddit, Telegram)
- 🌍 **Événements géopolitiques** (régulations, conflits, adoptions)
- 📈 **Tendances de recherche** (Google Trends, YouTube)
- 💰 **Indicateurs macro-économiques** (Fed, inflation, marchés traditionnels)
- ⛓️ **Métriques on-chain** (whale movements, exchange flows, hash rate)
- 🏦 **DeFi & Activité protocole**

Ces données permettent de comprendre le **contexte global** et d'anticiper les mouvements de marché avant qu'ils ne se produisent.

---

## 📦 Nouveaux Items créés

### 1. SentimentDataItem 🐦
**Sentiment social et engagement**

```python
{
    'symbol': 'BTCUSDT',
    'timestamp': datetime,
    'source': 'twitter/reddit/telegram',

    # Twitter Metrics
    'tweet_volume': 15432,
    'positive_tweets': 8500,
    'negative_tweets': 3200,
    'neutral_tweets': 3732,
    'sentiment_score': 0.42,  # -1 to 1
    'retweet_count': 45000,
    'like_count': 125000,
    'influencer_mentions': 15,
    'top_hashtags': ['#Bitcoin', '#BTC'],

    # Reddit
    'reddit_posts': 250,
    'reddit_comments': 1500,
    'reddit_upvotes': 8500,
    'reddit_sentiment': 0.35,

    # General
    'fear_greed_index': 65,  # 0-100
    'social_dominance': 45.2,  # % of social volume
    'social_volume_change': 15.3,  # % change
}
```

### 2. GeopoliticalEventItem 🌍
**Événements géopolitiques et régulations**

```python
{
    'timestamp': datetime,
    'source': 'newsapi/gdelt',

    # Event
    'event_type': 'regulation/ban/adoption/conflict',
    'country': 'USA',
    'region': 'North America',
    'severity': 7,  # 1-10
    'impact_score': 0.7,

    # Description
    'title': 'SEC Approves Bitcoin ETF',
    'description': '...',
    'keywords': ['bitcoin', 'etf', 'sec'],
    'entities': ['SEC', 'USA'],

    # Sentiment
    'news_sentiment': 0.8,  # -1 to 1
    'tone': 'positive',

    # Regulation
    'regulation_type': 'etf',
    'affected_cryptos': ['BTCUSDT'],
}
```

### 3. TrendDataItem 📈
**Tendances de recherche et popularité**

```python
{
    'symbol': 'BTCUSDT',
    'timestamp': datetime,
    'source': 'google_trends/youtube',

    # Google Trends
    'search_volume': 85,  # 0-100
    'search_volume_change': 12.5,
    'rising_queries': ['buy bitcoin', 'bitcoin price'],
    'top_queries': ['bitcoin', 'btc usd'],
    'regional_interest': {'US': 100, 'UK': 75},

    # YouTube
    'video_count': 250,
    'view_count': 1500000,
    'positive_videos': 180,
    'negative_videos': 70,

    # News
    'news_articles_count': 450,
    'mainstream_media_mentions': 35,
    'news_sentiment': 0.45,
}
```

### 4. MacroEconomicDataItem 💰
**Indicateurs macro-économiques**

```python
{
    'timestamp': datetime,
    'source': 'fred/world_bank',

    # US Indicators
    'fed_rate': 4.75,
    'inflation_rate': 3.2,
    'unemployment_rate': 3.8,
    'gdp_growth': 2.1,
    'm2_money_supply': 21000000000000,
    'dollar_index': 103.5,

    # Global Markets
    'sp500': 4500,
    'nasdaq': 14000,
    'gold_price': 2000,
    'oil_price': 85,
    'vix_index': 15,  # Fear index

    # Crypto Macro
    'total_market_cap': 2500000000000,
    'btc_dominance': 52.5,
    'stable_coin_supply': 150000000000,

    # Correlations
    'btc_sp500_correlation': 0.65,
    'btc_gold_correlation': 0.42,
}
```

### 5. OnChainDataItem ⛓️
**Métriques on-chain**

```python
{
    'symbol': 'BTCUSDT',
    'timestamp': datetime,
    'source': 'glassnode/santiment',

    # Network Activity
    'active_addresses': 850000,
    'new_addresses': 45000,
    'transaction_count': 320000,
    'transaction_volume': 5000000000,

    # Holder Behavior
    'exchange_inflow': 15000,
    'exchange_outflow': 25000,
    'exchange_net_flow': -10000,  # Negative = outflow
    'whale_transactions': 150,
    'supply_on_exchanges': 2500000,

    # HODLer Metrics
    'long_term_holder_supply': 14000000,
    'mvrv_ratio': 1.8,

    # Mining
    'hash_rate': 450000000,
    'mining_difficulty': 65000000000000,

    # Derivatives
    'futures_open_interest': 18000000000,
    'futures_funding_rate': 0.01,
    'liquidations_long': 50000000,
    'liquidations_short': 30000000,
}
```

---

## 🚀 Utilisation des nouveaux spiders

### 1. Scraper le Sentiment Social 🐦

```bash
cd /Users/christopher/Desktop/futur/crypto_indicators_scraper

# Avec LunarCrush API key (recommandé)
scrapy crawl crypto_sentiment \
    -a symbols=BTC,ETH,BNB \
    -a lunarcrush_api_key=YOUR_KEY

# Sans API key (données limitées mais gratuites)
scrapy crawl crypto_sentiment -a symbols=BTC,ETH
```

**Sources utilisées :**
- LunarCrush (social metrics) - API key gratuite
- CryptoPanic (news sentiment) - Gratuit
- Alternative.me (Fear & Greed) - Gratuit

**Durée** : 2-5 minutes pour 10 cryptos

### 2. Scraper les Événements Géopolitiques 🌍

```bash
# Avec NewsAPI key (recommandé)
scrapy crawl geopolitical \
    -a newsapi_key=YOUR_KEY

# Sans API key (sources publiques uniquement)
scrapy crawl geopolitical
```

**Sources utilisées :**
- NewsAPI (regulatory news) - API key gratuite (100 req/jour)
- CoinDesk RSS (crypto news) - Gratuit
- GDELT (global events) - Gratuit

**Durée** : 5-10 minutes

### 3. Scraper Tendances & Macro 📈💰

```bash
# Complet avec toutes les API keys
scrapy crawl trends_macro \
    -a symbols=BTC,ETH \
    -a glassnode_api_key=YOUR_KEY \
    -a fred_api_key=YOUR_KEY

# Sans API keys (données limitées)
scrapy crawl trends_macro -a symbols=BTC,ETH
```

**Sources utilisées :**
- CoinGecko (market trends) - Gratuit
- Glassnode (on-chain) - API key payante
- FRED (macro economics) - API key gratuite
- Alternative.me (Fear & Greed) - Gratuit

**Durée** : 3-8 minutes pour 10 cryptos

---

## 🔑 API Keys recommandées

### Gratuites ✅

1. **LunarCrush** (Social Metrics)
   - Site : https://lunarcrush.com/developers
   - Plan gratuit : 1000 requêtes/jour
   - Utilisation : `scrapy crawl crypto_sentiment -a lunarcrush_api_key=YOUR_KEY`

2. **NewsAPI** (News & Events)
   - Site : https://newsapi.org/
   - Plan gratuit : 100 requêtes/jour
   - Utilisation : `scrapy crawl geopolitical -a newsapi_key=YOUR_KEY`

3. **FRED** (Macro Economics)
   - Site : https://fred.stlouisfed.org/docs/api/api_key.html
   - Plan gratuit : Illimité
   - Utilisation : `scrapy crawl trends_macro -a fred_api_key=YOUR_KEY`

### Payantes mais puissantes 💎

4. **Glassnode** (On-Chain Metrics)
   - Site : https://glassnode.com/
   - Prix : À partir de $29/mois
   - Valeur : Métriques on-chain professionnelles

5. **Santiment** (Social & On-Chain)
   - Site : https://santiment.net/
   - Prix : À partir de $49/mois
   - Valeur : Données sociales + on-chain combinées

---

## 📊 Structure des données sur S3

Les données alternatives sont sauvegardées séparément :

```
s3://qbia/bourse/alternative_data/
├── sentiment/
│   └── 2024/
│       ├── 2024_01_sentiment.parquet
│       ├── 2024_02_sentiment.parquet
│       └── ...
├── geopolitical/
│   └── 2024/
│       ├── 2024_01_events.parquet
│       └── ...
├── trends/
│   └── 2024/
│       └── ...
├── macro/
│   └── 2024/
│       └── ...
└── onchain/
    └── 2024/
        └── ...
```

---

## 💡 Cas d'usage concrets

### 1. Prédire les pumps via sentiment social

```python
import pandas as pd

# Charger sentiment data
sentiment_df = pd.read_parquet('s3://qbia/bourse/alternative_data/sentiment/2024/2024_12_sentiment.parquet')

# Filtrer BTC
btc_sentiment = sentiment_df[sentiment_df['symbol'] == 'BTCUSDT'].copy()

# Trouver les pics de sentiment
btc_sentiment['sentiment_spike'] = (
    btc_sentiment['sentiment_score'] > btc_sentiment['sentiment_score'].rolling(24).mean() +
    btc_sentiment['sentiment_score'].rolling(24).std()
)

# Combiner avec prix
from ai.TRAIN.data.s3_data_source import S3DataSource
s3_source = S3DataSource(bucket='qbia', prefix='bourse/mintrad')
price_df = s3_source.fetch_symbol_data('BTCUSDT', 2024)

# Merger
combined = pd.merge(price_df, btc_sentiment, on='timestamp', how='left')

# Analyser : Les spikes de sentiment précèdent-ils les hausses de prix ?
print("Correlation sentiment vs prix futur (1h) :",
      combined['sentiment_score'].corr(combined['close'].shift(-60)))
```

### 2. Détecter les événements géopolitiques majeurs

```python
# Charger events
events_df = pd.read_parquet('s3://qbia/bourse/alternative_data/geopolitical/2024/2024_12_events.parquet')

# Filtrer événements majeurs
major_events = events_df[
    (events_df['severity'] >= 7) &
    (events_df['event_type'].isin(['ban', 'regulation', 'adoption']))
].sort_values('timestamp')

print("Événements majeurs :")
for _, event in major_events.iterrows():
    print(f"{event['timestamp']}: {event['title']} (severity: {event['severity']})")

# Analyser l'impact sur le prix
# (Charger prix et voir les mouvements dans les 24h après l'événement)
```

### 3. Suivre les whale movements

```python
# Charger on-chain data
onchain_df = pd.read_parquet('s3://qbia/bourse/alternative_data/onchain/2024/2024_12_onchain.parquet')

btc_onchain = onchain_df[onchain_df['symbol'] == 'BTCUSDT'].copy()

# Analyser exchange net flow
btc_onchain['exchange_net_flow_ma'] = btc_onchain['exchange_net_flow'].rolling(24).mean()

# Negative net flow = accumulation (bullish)
# Positive net flow = distribution (bearish)
print("Exchange net flow moyen (24h) :", btc_onchain['exchange_net_flow_ma'].iloc[-1])

# Combiner avec prix
if btc_onchain['exchange_net_flow_ma'].iloc[-1] < -5000:
    print("⚠️  ACCUMULATION DÉTECTÉE - Potentiel bullish")
elif btc_onchain['exchange_net_flow_ma'].iloc[-1] > 5000:
    print("⚠️  DISTRIBUTION DÉTECTÉE - Potentiel bearish")
```

### 4. Macro correlations

```python
# Charger macro data
macro_df = pd.read_parquet('s3://qbia/bourse/alternative_data/macro/2024/2024_12_macro.parquet')

# Voir corrélation BTC vs markets traditionnels
print("Corrélation BTC-SP500 :", macro_df['btc_sp500_correlation'].iloc[-1])
print("Corrélation BTC-Gold :", macro_df['btc_gold_correlation'].iloc[-1])
print("BTC Dominance :", macro_df['btc_dominance'].iloc[-1])

# Si corrélation BTC-SP500 > 0.7 et VIX > 20 → Risk-off environment
```

---

## 🎓 Stratégies de trading basées sur données alternatives

### Stratégie 1 : Sentiment Reversal

**Logique** : Quand le sentiment est extrêmement négatif (< -0.5) ET Fear & Greed < 20, c'est souvent un bon point d'entrée.

```python
# Conditions
sentiment = btc_sentiment['sentiment_score'].iloc[-1]
fear_greed = btc_sentiment['fear_greed_index'].iloc[-1]

if sentiment < -0.5 and fear_greed < 20:
    print("🟢 BUY SIGNAL : Extreme fear")
elif sentiment > 0.7 and fear_greed > 80:
    print("🔴 SELL SIGNAL : Extreme greed")
```

### Stratégie 2 : Regulatory Catalyst

**Logique** : Les approbations d'ETF, adoptions légales sont bullish à moyen terme.

```python
# Filtrer événements positifs majeurs
positive_events = events_df[
    (events_df['event_type'].isin(['adoption', 'etf'])) &
    (events_df['severity'] >= 7) &
    (events_df['news_sentiment'] > 0.5)
]

if len(positive_events) > 0:
    print("🟢 BULLISH CATALYST DÉTECTÉ")
    print(positive_events[['timestamp', 'title']])
```

### Stratégie 3 : Whale Activity

**Logique** : Les whales accumulent avant les pumps.

```python
# Si exchange outflow fort + sentiment positif = bullish
exchange_outflow = btc_onchain['exchange_net_flow'].iloc[-24:].mean()
sentiment = btc_sentiment['sentiment_score'].iloc[-1]

if exchange_outflow < -3000 and sentiment > 0.3:
    print("🟢 ACCUMULATION PHASE - Potential breakout soon")
```

---

## 🔄 Automatisation du scraping

### Script de scraping quotidien

```bash
#!/bin/bash
# daily_alternative_scraping.sh

echo "Starting daily alternative data scraping..."

# 1. Sentiment
scrapy crawl crypto_sentiment \
    -a symbols=BTC,ETH,BNB,SOL,XRP,ADA,DOGE \
    -a lunarcrush_api_key=$LUNARCRUSH_KEY

# 2. Geopolitical
scrapy crawl geopolitical \
    -a newsapi_key=$NEWSAPI_KEY

# 3. Trends & Macro
scrapy crawl trends_macro \
    -a symbols=BTC,ETH \
    -a glassnode_api_key=$GLASSNODE_KEY \
    -a fred_api_key=$FRED_KEY

echo "Daily scraping completed at $(date)"
```

**Utilisation** :
```bash
chmod +x daily_alternative_scraping.sh

# Lancer manuellement
./daily_alternative_scraping.sh

# Ou avec cron (tous les jours à 1h du matin)
crontab -e
# Ajouter : 0 1 * * * /path/to/daily_alternative_scraping.sh >> /path/to/scraping.log 2>&1
```

---

## 📚 Résumé des avantages

### Pourquoi ces données sont cruciales

1. **Anticipation** : Sentiment et tendances changent AVANT les prix
2. **Context** : Comprendre le "pourquoi" derrière les mouvements
3. **Edge** : Les algos traditionnels n'utilisent pas ces données
4. **Risk Management** : Détecter les risques géopolitiques en avance
5. **Confirmation** : Valider vos signaux techniques avec sentiment

### Données les plus utiles par ordre d'importance

1. 🥇 **On-Chain Metrics** (exchange flows, whale activity)
2. 🥈 **Sentiment Social** (Twitter, Reddit volume + sentiment)
3. 🥉 **Macro Economics** (Fed rate, inflation, correlations)
4. **Geopolitical Events** (regulations majeures)
5. **Trends** (Google Trends pour détecter FOMO)

---

## 🎯 Prochaines étapes

1. ✅ Lancer un test : `scrapy crawl crypto_sentiment -a symbols=BTC`
2. ✅ Obtenir les API keys gratuites (LunarCrush, NewsAPI, FRED)
3. ✅ Scraper 1 semaine de données
4. ✅ Analyser les corrélations prix vs sentiment
5. ✅ Intégrer dans votre modèle ML
6. ✅ Backtester des stratégies basées sur données alternatives

**Ces données peuvent transformer vos prédictions ! 🚀**

---

**Créé le** : 15 Décembre 2025
**Documentation complète** : Voir README.md
