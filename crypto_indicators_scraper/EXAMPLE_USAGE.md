# 📚 Exemples d'utilisation du Scraper

## 🎯 Scénarios d'utilisation courants

### 1. Test initial - Vérifier que tout fonctionne ✅

**Objectif** : Tester le scraper avec 2 cryptos populaires pour une courte période

```bash
cd /Users/christopher/Desktop/futur/crypto_indicators_scraper

# Vérifier d'abord les données disponibles
python check_s3_data.py

# Lancer un test rapide (5-10 minutes)
python run_scraper.py \
    --symbols BTCUSDT,ETHUSDT \
    --start-year 2024 \
    --end-year 2024 \
    --no-proxy \
    --concurrent-requests 4 \
    --debug
```

**Résultat attendu** :
- Logs de scraping en temps réel
- Fichiers créés sur S3 : `s3://qbia/bourse/indicators/indicators_1m_2024/BTCUSDT_2024_*.parquet`
- Stats finales affichées

**Vérification** :
```bash
# Lister les fichiers créés
aws s3 ls s3://qbia/bourse/indicators/indicators_1m_2024/ | grep -E "BTC|ETH"

# Télécharger un exemple
aws s3 cp s3://qbia/bourse/indicators/indicators_1m_2024/BTCUSDT_2024_01_indicators.parquet .

# Analyser avec pandas
python -c "
import pandas as pd
df = pd.read_parquet('BTCUSDT_2024_01_indicators.parquet')
print('Colonnes:', list(df.columns))
print('Nombre de lignes:', len(df))
print(df.head())
"
```

---

### 2. Scraper les top 10 cryptos pour 2024 🔥

**Objectif** : Enrichir vos données pour les cryptos les plus importantes

```bash
python run_scraper.py \
    --symbols BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT,ADAUSDT,DOGEUSDT,DOTUSDT,MATICUSDT,AVAXUSDT \
    --start-year 2024 \
    --end-year 2024 \
    --proxy-enabled \
    --concurrent-requests 16 \
    --batch-size 2000
```

**Durée estimée** : 30-60 minutes

**Optimisation** :
- Si vous avez des API keys, ajoutez :
  ```bash
  --cryptocompare-api-key YOUR_KEY \
  --taapi-api-key YOUR_KEY
  ```

---

### 3. Scraper une année complète (toutes les cryptos) 📅

**Objectif** : Récupérer tous les indicateurs pour 2024

```bash
# Option A : Sans spécifier de symboles (charge tout depuis S3)
python run_scraper.py \
    --start-year 2024 \
    --end-year 2024 \
    --proxy-enabled \
    --concurrent-requests 32 \
    --batch-size 1000

# Option B : Utiliser le script bash
# Éditer d'abord run_full_scrape.sh pour ajuster les années
./run_full_scrape.sh
```

**Durée estimée** : 2-6 heures (350+ cryptos)

**Surveillance** :
```bash
# Pendant le scraping, surveiller les logs
# Dans un autre terminal :
watch -n 60 'aws s3 ls s3://qbia/bourse/indicators/indicators_1m_2024/ | wc -l'
```

---

### 4. Scraping historique par tranches 🕐

**Objectif** : Scraper progressivement pour éviter les timeouts

```bash
# Année par année
for year in {2024..2020}; do
    echo "Scraping year $year..."
    python run_scraper.py \
        --start-year $year \
        --end-year $year \
        --proxy-enabled \
        --concurrent-requests 32

    # Attendre entre chaque année
    sleep 300
done
```

---

### 5. Scraper uniquement les cryptos manquantes 🔍

**Objectif** : Compléter les données pour les cryptos qui n'ont pas encore été scrapées

```bash
# Script pour identifier les cryptos manquantes
python << 'EOF'
import boto3

s3 = boto3.client('s3')

# Cryptos dans les données sources (OHLCV)
response = s3.list_objects_v2(
    Bucket='qbia',
    Prefix='bourse/mintrad/klines_1m_TRADING_USDT_2024/'
)
source_symbols = set()
for obj in response.get('Contents', []):
    filename = obj['Key'].split('/')[-1]
    if filename.endswith('.parquet'):
        symbol = filename.split('_')[0]
        source_symbols.add(symbol)

# Cryptos dans les indicateurs scrapés
response = s3.list_objects_v2(
    Bucket='qbia',
    Prefix='bourse/indicators/indicators_1m_2024/'
)
scraped_symbols = set()
for obj in response.get('Contents', []):
    filename = obj['Key'].split('/')[-1]
    if filename.endswith('.parquet'):
        symbol = filename.split('_')[0]
        scraped_symbols.add(symbol)

# Cryptos manquantes
missing = source_symbols - scraped_symbols
print(f"Cryptos manquantes: {len(missing)}")
print(','.join(sorted(missing)))
EOF

# Puis scraper uniquement celles-ci
python run_scraper.py \
    --symbols $(python get_missing_symbols.py) \
    --start-year 2024 \
    --end-year 2024 \
    --proxy-enabled
```

---

### 6. Mode production - Scraping continu 🔄

**Objectif** : Scraper régulièrement les nouvelles données

```bash
#!/bin/bash
# save as: continuous_scraper.sh

while true; do
    echo "Starting scraping cycle at $(date)"

    # Scraper les données récentes
    python run_scraper.py \
        --start-year $(date +%Y) \
        --end-year $(date +%Y) \
        --proxy-enabled \
        --concurrent-requests 32 \
        --batch-size 2000

    echo "Scraping cycle completed at $(date)"

    # Attendre 6 heures avant le prochain cycle
    echo "Waiting 6 hours before next cycle..."
    sleep 21600
done
```

**Utilisation** :
```bash
chmod +x continuous_scraper.sh
nohup ./continuous_scraper.sh > scraper.log 2>&1 &
```

---

### 7. Scraping avec maximum de proxies 🌐

**Objectif** : Utiliser le maximum de sources de proxy pour éviter les rate limits

```bash
# Créer un fichier de proxies premium (si vous en avez)
cat > premium_proxies.txt << EOF
http://user:pass@premium-proxy1.com:8080
http://user:pass@premium-proxy2.com:8080
http://premium-proxy3.com:3128
EOF

# Modifier settings.py pour ajouter votre fichier
# PROXY_SOURCES = [
#     'free_proxy_list',
#     'proxy_scrape',
#     'geonode',
#     'file:///Users/christopher/Desktop/futur/crypto_indicators_scraper/premium_proxies.txt',
# ]

# Lancer avec proxies
python run_scraper.py \
    --start-year 2024 \
    --proxy-enabled \
    --concurrent-requests 64
```

---

### 8. Intégration avec votre pipeline ML 🤖

**Objectif** : Utiliser les indicateurs scrapés dans votre modèle

```python
# integration_example.py
import pandas as pd
from ai.TRAIN.data.s3_data_source import S3DataSource

def load_enriched_data(symbol, year):
    """
    Charge les données OHLCV + indicateurs scrapés pour un symbole.
    """
    # 1. Charger les données OHLCV de base
    s3_source = S3DataSource(bucket='qbia', prefix='bourse/mintrad')
    ohlcv_df = s3_source.fetch_symbol_data(symbol, year)

    print(f"Loaded OHLCV: {len(ohlcv_df)} rows")

    # 2. Charger tous les fichiers d'indicateurs pour cette année
    indicators_dfs = []
    for month in range(1, 13):
        try:
            file_path = f's3://qbia/bourse/indicators/indicators_1m_{year}/{symbol}_{year}_{month:02d}_indicators.parquet'
            month_df = pd.read_parquet(file_path)
            indicators_dfs.append(month_df)
            print(f"Loaded indicators for month {month}: {len(month_df)} rows")
        except Exception as e:
            print(f"Month {month} not found: {e}")

    if not indicators_dfs:
        print("⚠️  No indicators found, returning OHLCV only")
        return ohlcv_df

    # 3. Concatener tous les mois
    indicators_df = pd.concat(indicators_dfs, ignore_index=True)
    print(f"Total indicators: {len(indicators_df)} rows")

    # 4. Merger avec OHLCV
    merged_df = pd.merge(
        ohlcv_df,
        indicators_df,
        on=['symbol', 'timestamp'],
        how='left',
        suffixes=('_ohlcv', '_indicators')
    )

    print(f"Merged data: {len(merged_df)} rows, {len(merged_df.columns)} columns")

    # 5. Afficher les nouvelles features
    new_features = [col for col in merged_df.columns if col not in ohlcv_df.columns]
    print(f"\nNew features from indicators: {new_features}")

    return merged_df

# Utilisation
if __name__ == '__main__':
    # Charger BTC 2024 avec indicateurs
    btc_data = load_enriched_data('BTCUSDT', 2024)

    # Vérifier les indicateurs disponibles
    print("\nIndicators summary:")
    indicator_columns = ['rsi', 'rsi_14', 'macd', 'sma_7', 'sma_25', 'ema_7', 'ema_25',
                         'bollinger_upper', 'bollinger_lower', 'atr', 'adx']
    for col in indicator_columns:
        if col in btc_data.columns:
            print(f"  {col}: {btc_data[col].notna().sum()} values")

    # Sauvegarder pour utilisation dans le modèle
    btc_data.to_parquet('btc_enriched_2024.parquet')
    print("\nSaved to: btc_enriched_2024.parquet")
```

**Lancer** :
```bash
python integration_example.py
```

---

### 9. Analyse et validation des données 📊

**Objectif** : Vérifier la qualité des données scrapées

```python
# validate_scraped_data.py
import pandas as pd
import boto3

def validate_indicators(symbol, year, month):
    """Valide les indicateurs scrapés."""

    file_path = f's3://qbia/bourse/indicators/indicators_1m_{year}/{symbol}_{year}_{month:02d}_indicators.parquet'

    try:
        df = pd.read_parquet(file_path)
    except Exception as e:
        print(f"❌ File not found: {e}")
        return False

    print(f"\n📊 Validation for {symbol} {year}-{month:02d}")
    print(f"   Total rows: {len(df)}")
    print(f"   Columns: {len(df.columns)}")

    # Check required columns
    required_cols = ['symbol', 'timestamp', 'open', 'high', 'low', 'close', 'volume']
    missing = [col for col in required_cols if col not in df.columns]
    if missing:
        print(f"   ❌ Missing columns: {missing}")
        return False
    else:
        print(f"   ✅ All required columns present")

    # Check for nulls in price data
    null_counts = df[required_cols].isnull().sum()
    if null_counts.any():
        print(f"   ⚠️  Null values found:")
        for col, count in null_counts[null_counts > 0].items():
            print(f"      {col}: {count} nulls ({count/len(df)*100:.2f}%)")
    else:
        print(f"   ✅ No null values in required columns")

    # Check indicator coverage
    indicators = ['rsi', 'macd', 'sma_7', 'ema_7', 'bollinger_upper', 'atr']
    print(f"\n   Indicator coverage:")
    for ind in indicators:
        if ind in df.columns:
            coverage = df[ind].notna().sum() / len(df) * 100
            print(f"      {ind}: {coverage:.1f}%")
        else:
            print(f"      {ind}: Not present")

    return True

# Valider quelques fichiers
validate_indicators('BTCUSDT', 2024, 1)
validate_indicators('ETHUSDT', 2024, 1)
```

---

### 10. Scraping optimisé pour la mémoire 💾

**Objectif** : Scraper avec une faible consommation mémoire

```bash
python run_scraper.py \
    --symbols BTCUSDT,ETHUSDT \
    --start-year 2024 \
    --end-year 2024 \
    --concurrent-requests 8 \
    --batch-size 500 \
    --proxy-enabled
```

**Configuration dans settings.py** :
```python
# Limites mémoire
MEMUSAGE_LIMIT_MB = 1024  # 1 GB max
MEMUSAGE_WARNING_MB = 512  # Warning à 512 MB

# Réduire la concurrence
CONCURRENT_REQUESTS = 8
CONCURRENT_REQUESTS_PER_DOMAIN = 2
```

---

## 📝 Résumé des commandes utiles

```bash
# Vérifier les données disponibles
python check_s3_data.py

# Test rapide
python run_scraper.py --symbols BTCUSDT,ETHUSDT --start-year 2024 --no-proxy

# Top 10 cryptos
python run_scraper.py --symbols BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT,ADAUSDT,DOGEUSDT,DOTUSDT,MATICUSDT,AVAXUSDT --start-year 2024 --proxy-enabled

# Année complète
python run_scraper.py --start-year 2024 --end-year 2024 --proxy-enabled --concurrent-requests 32

# Tout l'historique
./run_full_scrape.sh

# Lister les résultats
aws s3 ls s3://qbia/bourse/indicators/indicators_1m_2024/

# Télécharger un fichier
aws s3 cp s3://qbia/bourse/indicators/indicators_1m_2024/BTCUSDT_2024_01_indicators.parquet .
```

---

## 🎓 Bonnes pratiques

1. **Toujours commencer par un test** avec 2-3 cryptos
2. **Vérifier les résultats sur S3** après chaque scraping
3. **Utiliser les proxies** pour les gros volumes
4. **Scraper année par année** pour éviter les timeouts
5. **Sauvegarder les logs** avec `> scraper.log 2>&1`
6. **Monitorer la consommation mémoire** en mode debug
7. **Configurer des API keys** pour plus d'indicateurs
8. **Valider les données** avant de les utiliser en production

---

**Prêt à enrichir vos données de trading ! 🚀**
