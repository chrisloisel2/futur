# 🐋 FREE WHALE SCANNER - Système Gratuit de Tracking

## 🎯 Objectif

Système **100% GRATUIT** pour tracker les mouvements de whales (>$100k USD) sur Bitcoin, Ethereum et Solana depuis 2019.

**Économie: $1,788/an** (vs Whale Alert Professional à $149/mois)

---

## 🚀 Installation Rapide (2 minutes)

```bash
cd /Users/christopher/Desktop/futur/scrapers_engine

# Installer dépendances (déjà fait si pymongo présent)
pip install -r requirements.txt

# Rendre les scripts exécutables
chmod +x run_whale_scanner.py view_blockchain_whales.py
```

---

## ⚡ Utilisation Immédiate

### Test Rapide - Bitcoin (10 blocs)

```bash
python run_whale_scanner.py --blockchain btc --test
```

### Scan Bitcoin - 100 derniers blocs

```bash
python run_whale_scanner.py --blockchain btc --limit 100
```

### Scan Ethereum - avec clé API gratuite

```bash
# 1. Obtenir clé gratuite: https://etherscan.io/apis (instantané)
# 2. Lancer:
python run_whale_scanner.py --blockchain eth --limit 50 --api-key YOUR_ETHERSCAN_KEY
```

### Visualiser les Données

```bash
python view_blockchain_whales.py
```

---

## 📊 APIs Gratuites Utilisées

| Blockchain | API | Limite Gratuite | Clé Requise |
|------------|-----|-----------------|-------------|
| **Bitcoin** | Mempool.space | ♾️ Illimité | ❌ Non |
| **Ethereum** | Etherscan | 5 req/sec, 100k/jour | ✅ Oui (gratuit) |
| **Solana** | RPC Public | Raisonnable | ❌ Non |

### Obtenir les Clés API (gratuit)

**Etherscan (Ethereum):**
1. Aller sur https://etherscan.io/apis
2. Créer un compte (email + mot de passe)
3. Générer une clé API (instantané)
4. Utiliser avec `--api-key YOUR_KEY`

**Solscan (Solana - optionnel):**
- Optionnel car RPC public disponible
- https://docs.solscan.io/ si besoin

---

## 🏗️ Architecture

```
Spiders (Scrapy)
    ↓
Mempool.space / Etherscan / Solscan APIs (gratuit)
    ↓
TransactionAlertItem (enrichi)
    ↓
PriceService (CoinGecko gratuit) → Calcul USD
AddressLabelingService (100+ adresses connues) → Identification
    ↓
BlockchainWhaleMongoDBPipeline
    ↓
MongoDB Atlas (whale_data.whale_transactions)
```

---

## 💾 Base de Données MongoDB

**Configuration:**
```
URI: mongodb+srv://christoloisel:rose@cluster0.ppyauvl.mongodb.net/
Database: whale_data
Collection: whale_transactions
```

**Structure Document:**
```javascript
{
  "_id": "bitcoin:abc123...",
  "blockchain": "bitcoin",
  "symbol": "BTC",
  "tx_hash": "abc123...",
  "block_number": 800000,
  "timestamp": ISODate("2023-06-15T14:30:00Z"),

  "amount": 1000.5,        // BTC
  "amount_usd": 42000000,  // USD au moment de la tx
  "fees": 0.0001,
  "fees_usd": 4.2,

  "from_address": "1A1zP1...",
  "to_address": "3J98t1...",
  "from_owner": "binance",
  "from_type": "exchange",
  "to_owner": "unknown",
  "to_type": "wallet",

  "transaction_type": "exchange_to_wallet",  // 📤 Bullish
  "price_usd": 42000,  // Prix BTC à ce moment

  "source": "Mempool.space API",
  "scraped_at": ISODate("2025-12-23T20:00:00Z")
}
```

---

## 📈 Cas d'Usage

### 1. Surveiller Sorties d'Exchanges (Bullish)

```javascript
// MongoDB
db.whale_transactions.find({
  transaction_type: 'exchange_to_wallet',
  amount_usd: {$gte: 10000000}  // > $10M
}).sort({timestamp: -1})
```

### 2. Surveiller Entrées vers Exchanges (Bearish)

```javascript
db.whale_transactions.find({
  transaction_type: 'wallet_to_exchange',
  amount_usd: {$gte: 10000000}
}).sort({timestamp: -1})
```

### 3. Top Transactions par Blockchain

```javascript
db.whale_transactions.find({blockchain: 'bitcoin'})
  .sort({amount_usd: -1})
  .limit(100)
```

### 4. Activité Binance

```javascript
db.whale_transactions.find({
  $or: [
    {from_owner: 'binance'},
    {to_owner: 'binance'}
  ]
}).sort({timestamp: -1})
```

---

## 🎯 Modes d'Utilisation

### Mode Test (10 blocs)

```bash
python run_whale_scanner.py --test
```

### Mode Production - Bitcoin depuis bloc spécifique

```bash
python run_whale_scanner.py --blockchain btc --start-block 750000
```

### Mode Production - Ethereum historique

```bash
# ATTENTION: Ethereum a ~21M blocs depuis 2015
# Utiliser des ranges pour éviter rate limits

python run_whale_scanner.py --blockchain eth \
  --start-block 18000000 \
  --end-block 18010000 \
  --api-key YOUR_KEY
```

### Mode Continu - Tous les blockchains

```bash
# Scanne en continu les nouveaux blocs
python run_whale_scanner.py --blockchain all --limit 100
```

---

## 📋 Fichiers Créés

### Services
- ✅ `utils/price_service.py` - Prix crypto via CoinGecko
- ✅ `utils/address_labeling_service.py` - Identification adresses (100+ connues)

### Spiders
- ✅ `spiders/bitcoin_mempool_spider.py` - Bitcoin (Mempool.space)
- ✅ `spiders/ethereum_etherscan_spider.py` - Ethereum (Etherscan)
- ✅ `spiders/solana_solscan_spider.py` - Solana (RPC/Solscan)

### Pipeline
- ✅ `pipelines/blockchain_whale_mongodb_pipeline.py` - Stockage MongoDB unifié

### Scripts
- ✅ `run_whale_scanner.py` - Script principal de lancement
- ✅ `view_blockchain_whales.py` - Visualisation des données

### Configuration
- ✅ `items.py` - TransactionAlertItem étendu
- ✅ `settings.py` - Configuration blockchain

---

## 🔧 Commandes Utiles

### Voir les Données

```bash
python view_blockchain_whales.py
```

### MongoDB Compass

```
Connexion: mongodb+srv://christoloisel:rose@cluster0.ppyauvl.mongodb.net/
Database: whale_data
Collection: whale_transactions
```

### Scrapy Direct

```bash
# Bitcoin
scrapy crawl bitcoin_mempool -a limit=100

# Ethereum
scrapy crawl ethereum_etherscan -a api_key=YOUR_KEY -a limit=50

# Solana
scrapy crawl solana_solscan -a limit=100
```

---

## 📊 Performance Estimée

### Bitcoin (2019-2025)
- **Blocs**: ~850,000
- **Temps**: 2-3 jours (avec optimisations)
- **Transactions whale**: ~50,000-100,000

### Ethereum (2019-2025)
- **Blocs**: ~15,000,000
- **Temps**: 7-10 jours (batch requests)
- **Transactions whale**: ~500,000-1,000,000

### Solana (2020-2025)
- **Blocs**: Très nombreux (400ms/bloc)
- **Approche**: Par adresses connues
- **Temps**: Variable

---

## 🎁 Avantages vs Whale Alert

| Critère | Whale Alert Pro | Notre Solution |
|---------|----------------|----------------|
| **Coût** | $149/mois | ✅ **GRATUIT** |
| **Historique** | Complet | ✅ **Complet depuis 2019** |
| **Blockchains** | 8 | ✅ **BTC + ETH + SOL** |
| **Contrôle** | Service tiers | ✅ **Total (code source)** |
| **Personnalisation** | Limitée | ✅ **Totale** |
| **Labels** | Propriétaires | ✅ **100+ adresses connues** |

---

## 🐛 Dépannage

### "No module named 'pymongo'"

```bash
pip install pymongo requests
```

### "Etherscan API key required"

```bash
# Obtenir clé gratuite: https://etherscan.io/apis
export ETHERSCAN_API_KEY="your_key"
# Ou utiliser --api-key
```

### "MongoDB connection failed"

- Vérifier votre connexion internet
- Autoriser votre IP dans MongoDB Atlas

### Rate Limit Exceeded

- Bitcoin: Pas de limite (Mempool.space)
- Ethereum: 5 req/sec (respecté automatiquement)
- Réduire `CONCURRENT_REQUESTS` dans settings.py si besoin

---

## 💡 Conseils

1. **Commencer petit**: Test avec `--limit 10`
2. **Bitcoin first**: Pas de clé API requise
3. **Etherscan gratuit**: Créer compte en 2 minutes
4. **MongoDB Atlas**: Gratuit jusqu'à 512MB
5. **Surveillance continue**: Cron job quotidien

---

## 🎉 Résultat Final

Un système **100% gratuit** et **autonome** qui:
- ✅ Récupère les transactions whale (>$100k) sur BTC/ETH/SOL
- ✅ Identifie les exchanges et institutions
- ✅ Stocke dans MongoDB avec enrichissement complet
- ✅ Économise $1,788/an
- ✅ Totalement personnalisable

---

**Status:** ✅ Opérationnel
**Dernière mise à jour:** 2025-12-23
**Économie:** $1,788/an
