# 🚀 Guide de Démarrage Rapide

## ✅ Vérifications préalables

Votre projet dispose de :
- ✅ **417 cryptos** pour 2025
- ✅ **352 cryptos** pour 2024
- ✅ **289 cryptos** pour 2023
- ✅ Données historiques de **2017 à 2025**

## 📋 Étape 1 : Vérifier vos données S3

```bash
cd /Users/christopher/Desktop/futur/crypto_indicators_scraper
python check_s3_data.py
```

## 🧪 Étape 2 : Test rapide (RECOMMANDÉ)

Testez avec 2 cryptos pour vous assurer que tout fonctionne :

```bash
python run_scraper.py \
    --symbols BTCUSDT,ETHUSDT \
    --start-year 2024 \
    --end-year 2024 \
    --no-proxy \
    --concurrent-requests 4
```

**Durée estimée** : 5-10 minutes
**Ce qui sera scrapé** : Indicateurs pour BTC et ETH en 2024

## 🎯 Étape 3 : Scraping ciblé par année

### Option A : Une année à la fois (RECOMMANDÉ)

```bash
# Scraper 2024 (plus récent)
python run_scraper.py \
    --start-year 2024 \
    --end-year 2024 \
    --proxy-enabled \
    --concurrent-requests 32

# Puis 2023
python run_scraper.py \
    --start-year 2023 \
    --end-year 2023 \
    --proxy-enabled \
    --concurrent-requests 32
```

### Option B : Cryptos top (BTC, ETH, BNB, etc.)

```bash
python run_scraper.py \
    --symbols BTCUSDT,ETHUSDT,BNBUSDT,ADAUSDT,SOLUSDT,XRPUSDT,DOGEUSDT \
    --start-year 2020 \
    --end-year 2024 \
    --proxy-enabled \
    --concurrent-requests 16
```

## 🔥 Étape 4 : Scraping complet (ATTENTION : Long)

⚠️ **Ceci va scraper TOUTES les cryptos pour TOUTES les années (2017-2025)**

```bash
# Utiliser le script bash
./run_full_scrape.sh

# OU utiliser la commande directe
python run_scraper.py \
    --start-year 2017 \
    --end-year 2025 \
    --proxy-enabled \
    --concurrent-requests 32 \
    --batch-size 1000
```

**Durée estimée** : Plusieurs heures à plusieurs jours selon :
- Le nombre de cryptos (1500+ au total)
- Les limites API
- La vitesse des proxies

## 📊 Étape 5 : Vérifier les résultats

```bash
# Lister les fichiers générés
aws s3 ls s3://qbia/bourse/indicators/

# Vérifier une année spécifique
aws s3 ls s3://qbia/bourse/indicators/indicators_1m_2024/

# Compter les fichiers
aws s3 ls s3://qbia/bourse/indicators/indicators_1m_2024/ | wc -l

# Télécharger un exemple pour inspection
aws s3 cp s3://qbia/bourse/indicators/indicators_1m_2024/BTCUSDT_2024_01_indicators.parquet .
```

## 💡 Conseils d'optimisation

### Pour aller plus vite

1. **Augmenter la concurrence** :
   ```bash
   --concurrent-requests 64
   ```

2. **Désactiver les proxies si vous n'avez pas de rate limiting** :
   ```bash
   --no-proxy
   ```

3. **Augmenter la taille des batchs** :
   ```bash
   --batch-size 5000
   ```

### Pour économiser les ressources

1. **Réduire la concurrence** :
   ```bash
   --concurrent-requests 8
   ```

2. **Scraper par tranches** :
   ```bash
   # Faire année par année
   for year in {2017..2025}; do
       python run_scraper.py --start-year $year --end-year $year --proxy-enabled
   done
   ```

## 🔑 Configuration des API Keys (Optionnel mais recommandé)

Pour obtenir plus d'indicateurs :

### 1. CryptoCompare (GRATUIT)
- Créer compte : https://min-api.cryptocompare.com/
- Obtenir clé API (plan gratuit : 100,000 requêtes/mois)
- Utiliser :
  ```bash
  --cryptocompare-api-key YOUR_KEY
  ```

### 2. TaaPI (Indicateurs techniques avancés)
- Créer compte : https://taapi.io/
- Plan gratuit : 50 requêtes/jour
- Plan Basic : $9.99/mois, 10,000 requêtes/mois
- Utiliser :
  ```bash
  --taapi-api-key YOUR_KEY
  ```

## 🐛 En cas de problème

### Le scraper ne démarre pas
```bash
# Vérifier les dépendances
pip install -r requirements.txt

# Vérifier AWS credentials
aws s3 ls s3://qbia/
```

### Erreurs de proxy
```bash
# Désactiver les proxies
python run_scraper.py --no-proxy ...
```

### Erreurs API
```bash
# Activer le mode debug
python run_scraper.py --debug ...
```

### Mémoire insuffisante
```bash
# Réduire la taille des batchs
python run_scraper.py --batch-size 500 ...
```

## 📈 Utiliser les données scrapées

### Exemple Python

```python
import pandas as pd

# Lire un fichier d'indicateurs
df = pd.read_parquet('s3://qbia/bourse/indicators/indicators_1m_2024/BTCUSDT_2024_01_indicators.parquet')

# Voir les colonnes disponibles
print(df.columns)

# Voir les premières lignes
print(df.head())

# Statistiques
print(df.describe())
```

### Intégrer avec votre pipeline ML

```python
from ai.TRAIN.data.s3_data_source import S3DataSource
import pandas as pd

# Charger OHLCV
s3_source = S3DataSource(bucket='qbia', prefix='bourse/mintrad')
ohlcv = s3_source.fetch_symbol_data('BTCUSDT', 2024)

# Charger indicateurs
indicators = pd.read_parquet('s3://qbia/bourse/indicators/indicators_1m_2024/BTCUSDT_2024_01_indicators.parquet')

# Merger
data = pd.merge(ohlcv, indicators, on=['symbol', 'timestamp'], how='left')
```

## ✅ Checklist de démarrage

- [ ] Vérifier les données S3 avec `python check_s3_data.py`
- [ ] Faire un test rapide avec 2 cryptos
- [ ] Vérifier que les fichiers sont créés sur S3
- [ ] Choisir votre stratégie de scraping (année par année, cryptos spécifiques, ou tout)
- [ ] (Optionnel) Configurer les API keys
- [ ] Lancer le scraping
- [ ] Vérifier les résultats sur S3
- [ ] Intégrer les données dans votre pipeline ML

## 🎓 Prochaines étapes

Une fois le scraping terminé, vous pourrez :

1. **Enrichir votre modèle ML** avec les nouveaux indicateurs
2. **Créer de nouvelles features** en combinant indicateurs
3. **Backtester** vos stratégies avec des données enrichies
4. **Prédire** les mouvements de marché avec plus de précision

---

**Besoin d'aide ?** Consultez le [README.md](README.md) pour la documentation complète.
