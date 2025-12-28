# 🚀 Démarrage Rapide - Whale Alert

Guide en 5 minutes pour commencer à récupérer les données de mouvements de whales Bitcoin.

## ⚡ Installation (2 minutes)

### 1. Installer les dépendances

```bash
cd /Users/christopher/Desktop/futur/scrapers_engine
pip install -r requirements.txt
```

### 2. Obtenir une clé API Whale Alert

🔗 **Inscrivez-vous gratuitement**: https://whale-alert.io/

**⚠️ IMPORTANT - Limitations de l'API gratuite:**

| Plan | Coût | Rate Limit | Historique |
|------|------|------------|------------|
| **Gratuit** | $0 | 20 req/min | **24h seulement** ❌ |
| **Starter** | $39/mois | 50 req/min | **30 jours** |
| **Professional** | $149/mois | 200 req/min | **Illimité** ✅ |

Pour récupérer les données **depuis 2019**, vous devez souscrire au plan **Professional ou supérieur**.

### 3. Tester l'installation

```bash
python test_whale_alert.py
```

Vous devriez voir:
```
✅ OK - MongoDB
✅ OK - Dépendances
✅ OK - Fichiers
```

## 🎯 Utilisation (3 minutes)

### Option A: Avec votre clé API (Recommandé)

```bash
# 1. Définir la clé API
export WHALE_ALERT_API_KEY="votre_clé_api_ici"

# 2. Lancer la récupération
python fetch_whale_data.py
```

### Option B: Passer la clé en paramètre

```bash
python fetch_whale_data.py --api-key "votre_clé_api_ici"
```

### Option C: Période personnalisée

```bash
# Récupérer seulement les données de 2024
python fetch_whale_data.py \
  --api-key "votre_clé" \
  --start-date 2024-01-01 \
  --end-date 2024-12-31
```

## 📊 Vérification des données

### Dans MongoDB Compass ou CLI

```javascript
// Connexion
mongodb+srv://christoloisel:rose@cluster0.ppyauvl.mongodb.net/

// Database: whale_data
// Collection: whale_transactions

// Voir les dernières transactions
db.whale_transactions.find({symbol: 'BTC'})
  .sort({timestamp: -1})
  .limit(10)

// Compter le total
db.whale_transactions.countDocuments({symbol: 'BTC'})
```

### Exemple de résultat

```json
{
  "_id": "abc123...",
  "tx_hash": "7f2a...",
  "blockchain": "bitcoin",
  "symbol": "BTC",
  "amount": 1523.45,
  "amount_usd": 98500000,
  "from_address": "1A1zP1...",
  "to_address": "3J98t1...",
  "from_owner": "binance",
  "to_owner": "unknown",
  "transaction_type": "exchange_to_wallet",
  "timestamp": "2024-12-15T14:30:00Z"
}
```

## 🎓 Cas d'usage

### 1. Surveiller les sorties d'exchanges (Bullish)

Les sorties massives d'exchanges indiquent souvent que les investisseurs accumulent:

```javascript
db.whale_transactions.find({
  transaction_type: 'exchange_to_wallet',
  amount_usd: {$gte: 10000000}  // > 10M USD
}).sort({timestamp: -1})
```

### 2. Surveiller les entrées vers exchanges (Bearish)

Les entrées vers exchanges peuvent indiquer une volonté de vendre:

```javascript
db.whale_transactions.find({
  transaction_type: 'wallet_to_exchange',
  amount_usd: {$gte: 10000000}
}).sort({timestamp: -1})
```

### 3. Identifier les patterns avant les mouvements de prix

```javascript
// Agrégation par jour
db.whale_transactions.aggregate([
  {$match: {symbol: 'BTC'}},
  {$group: {
    _id: {
      $dateToString: {format: '%Y-%m-%d', date: '$timestamp'}
    },
    exchange_inflow: {
      $sum: {
        $cond: [
          {$eq: ['$transaction_type', 'wallet_to_exchange']},
          '$amount_usd',
          0
        ]
      }
    },
    exchange_outflow: {
      $sum: {
        $cond: [
          {$eq: ['$transaction_type', 'exchange_to_wallet']},
          '$amount_usd',
          0
        ]
      }
    }
  }},
  {$sort: {_id: -1}}
])
```

## ⚙️ Configuration avancée

### Modifier la valeur minimale

Par défaut: 500,000 USD. Pour capturer des transactions plus petites:

```bash
python fetch_whale_data.py \
  --api-key "votre_clé" \
  --min-value 100000  # 100K USD minimum
```

### Récupérer Ethereum au lieu de Bitcoin

```bash
python fetch_whale_data.py \
  --api-key "votre_clé" \
  --currency eth
```

### Mode test (dry-run)

Vérifier les paramètres sans faire de requêtes:

```bash
python fetch_whale_data.py \
  --api-key "votre_clé" \
  --dry-run
```

## 🔧 Dépannage

### "No API key provided"

```bash
# Solution 1
export WHALE_ALERT_API_KEY="votre_clé"

# Solution 2
python fetch_whale_data.py --api-key "votre_clé"
```

### "MongoDB connection failed"

- Vérifier que votre IP est autorisée dans MongoDB Atlas
- Aller sur: https://cloud.mongodb.com/
- Network Access → Add IP Address → Add Current IP Address

### "Historical data not available"

L'API gratuite ne donne accès qu'aux dernières 24h. Pour l'historique complet:

1. Upgrader vers un plan Professional: https://whale-alert.io/pricing
2. Ou utiliser une période récente avec l'API gratuite:

```bash
python fetch_whale_data.py \
  --api-key "votre_clé" \
  --start-date $(date -v-1d +%Y-%m-%d) \  # Hier
  --end-date $(date +%Y-%m-%d)              # Aujourd'hui
```

## 📈 Performance

### Avec API Gratuite (20 req/min)

- Données: Dernières 24h uniquement
- Temps: ~1-2 minutes

### Avec API Professional (200 req/min)

Pour récupérer **2019 à aujourd'hui** (environ 2200 jours):

- Requêtes nécessaires: ~2200
- Temps estimé: ~11 minutes
- Transactions attendues: ~500,000 à 1,000,000

## 📚 Ressources

- 📖 [Guide complet](WHALE_ALERT_GUIDE.md)
- 🌐 [Documentation API](https://docs.whale-alert.io/)
- 💰 [Pricing](https://whale-alert.io/pricing)
- 🐙 [Code source](https://github.com/anthropics/claude-code)

## 💡 Conseils

1. **Commencez petit**: Testez d'abord avec l'API gratuite sur 24h
2. **Planifiez**: Pour l'historique complet, prévoyez un abonnement Professional
3. **Automatisez**: Configurez un cron job pour récupérer les nouvelles données quotidiennement
4. **Analysez**: Combinez avec les données de prix pour identifier les patterns

## 🎯 Exemple complet

```bash
# 1. Installation
cd /Users/christopher/Desktop/futur/scrapers_engine
pip install -r requirements.txt

# 2. Définir la clé API
export WHALE_ALERT_API_KEY="votre_clé_api_professional"

# 3. Récupérer les données depuis 2019
python fetch_whale_data.py \
  --start-date 2019-01-01 \
  --min-value 1000000

# 4. Vérifier dans MongoDB
mongosh "mongodb+srv://christoloisel:rose@cluster0.ppyauvl.mongodb.net/"
use whale_data
db.whale_transactions.countDocuments()
```

Bonne analyse ! 🐋📊
