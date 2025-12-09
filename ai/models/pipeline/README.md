# 📊 PIPELINE - Data Collection & Processing

## Vue d'ensemble

Le module **PIPELINE** est responsable de la collecte, du traitement et du stockage des données pour le trading algorithmique. Il gère la récupération de données de multiples sources (exchanges, on-chain, sentiment, macro-économie) et leur normalisation.

## 🏗️ Architecture

```
PIPELINE/
├── pipeline/                    # Package principal du pipeline
│   ├── collectors/             # Collecteurs de données par source
│   │   ├── alpaca_collector.py
│   │   ├── polygon_collector.py
│   │   ├── finnhub_collector.py
│   │   └── ...
│   ├── processors/             # Traitement des features
│   │   └── feature_processor.py
│   ├── models/                 # Modèles de séries temporelles
│   ├── cache.py                # Système de cache
│   ├── data_collection.py      # Orchestration de la collecte
│   ├── data_sources.py         # Sources de données
│   ├── normalization.py        # Normalisation des données
│   ├── features.py             # Feature engineering
│   └── realtime_pipeline.py    # Pipeline temps réel
├── mass_data_collector_v2.py   # Collecteur massif de données
├── collect_historical_crypto.py # Collecte historique crypto
├── mongo_ingest.py             # Ingestion MongoDB
├── mongo_utils.py              # Utilitaires MongoDB
├── api_server.py               # Serveur API pour les données
└── requirements.txt            # Dépendances spécifiques
```

## 🚀 Démarrage rapide

### 1. Installation

```bash
cd PIPELINE
pip install -r requirements.txt
```

### 2. Configuration

Créez un fichier `.env` avec vos clés API :

```env
# MongoDB
MONGO_URI=mongodb+srv://...
MONGO_DB=trader2

# API Keys (optionnelles)
BINANCE_API_KEY=your_key
GLASSNODE_API_KEY=your_key
FRED_API_KEY=your_key
```

### 3. Collecte de données

#### Collecte historique massive

```bash
python mass_data_collector_v2.py
```

Ce script collecte :
- Données OHLCV (Binance, CoinGecko)
- Données on-chain (Glassnode, blockchain.info)
- Sentiment (Reddit, Fear & Greed Index)
- Macro-économie (FRED)
- Dérivés (funding rates, open interest)

#### Pipeline temps réel

```bash
python run_realtime_ingestion.py
```

### 4. Serveur API

Pour exposer les données via une API REST :

```bash
python api_server.py
```

L'API sera accessible sur `http://localhost:8000`

## 📋 Fonctionnalités principales

### 1. Collecte de données

- **Multi-sources** : Binance, CoinGecko, Glassnode, Reddit, FRED
- **Proxies rotatifs** : Évite le bannissement IP
- **Rate limiting** : Respect des limites d'API
- **Cache intelligent** : Évite les requêtes redondantes
- **Retry automatique** : Gestion robuste des erreurs

### 2. Traitement des données

- **Normalisation adaptative** : Normalisation robuste aux outliers
- **Feature engineering** : Indicateurs techniques, statistiques
- **Data quality** : Validation et nettoyage
- **Memory optimization** : Optimisation des types de données

### 3. Stockage

- **MongoDB** : Stockage temps réel avec index optimisés
- **Parquet** : Export pour analyse et training
- **Collections séparées** : OHLCV, orderbook, sentiment, on-chain, macro, dérivés

## 🔗 Intégration avec TRAIN

Les données collectées par le PIPELINE sont utilisées par le module TRAIN pour l'entraînement des modèles.

## 📚 Documentation complète

Pour plus de détails, consultez la documentation dans `pipeline/README.md`
