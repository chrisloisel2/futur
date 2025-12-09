# 📊 Module Frontend Pipeline - Pipeline de Données et Affichage

Ce dossier contient toute la partie **pipeline de données**, **collecte**, **API** et **affichage frontend**.

## 📁 Structure

```
frontend_pipeline/
├── pipeline/            # Pipeline de traitement des données
├── frontend/            # Dashboard et interface utilisateur
├── data/                # Sources de données et datasets
├── api_server.py        # API REST pour le frontend
├── mass_data_collector_v2.py  # Collecteur massif de données
├── mass_data_collector.py     # Version précédente du collecteur
├── mongo_ingest.py      # Ingestion MongoDB
├── mongo_utils.py       # Utilitaires MongoDB
├── load_to_mongodb.py   # Chargement des données dans MongoDB
├── pipeline_api_connector.py  # Connecteur API pour pipeline
├── collect_historical_crypto.py  # Collecte historique crypto
├── validate_crypto_data.py       # Validation des données
├── history_coverage_report.py    # Rapport de couverture historique
├── quick_start.py       # Démarrage rapide
├── test_websocket_collectors.py  # Tests collectors WebSocket
├── run_realtime_ingestion.py     # Ingestion temps réel
├── demo_realtime_ingestion.py    # Démo ingestion temps réel
├── start_crypto_dashboard.sh     # Script de démarrage dashboard
├── test_api.sh          # Test de l'API
└── verify_setup.sh      # Vérification de la configuration
```

## 🎯 Objectif

Ce module se concentre sur:
- **Collecte de données** multi-sources (Binance, CoinGecko, etc.)
- **Traitement et normalisation** des données
- **Stockage MongoDB** avec ingestion temps réel
- **API REST** pour exposer les données
- **Dashboard frontend** pour visualisation

## 🚀 Utilisation

### Collecter les données

```bash
cd frontend_pipeline
python mass_data_collector_v2.py
```

### Démarrer l'API server

```bash
python api_server.py
```

### Lancer le dashboard

```bash
./start_crypto_dashboard.sh
```

### Ingestion temps réel

```bash
python run_realtime_ingestion.py
```

## 🔗 Dépendances

Le pipeline utilise principalement:
- aiohttp (requêtes async)
- MongoDB / PyMongo
- Pandas / NumPy
- FastAPI / Flask (API)
- React (frontend)

## 📦 Sources de Données

Le pipeline collecte des données depuis:
- **Marché**: Binance, CoinGecko, Kraken
- **On-Chain**: Glassnode, Blockchain.info
- **Sentiment**: Reddit, Fear & Greed Index
- **Macro**: FRED, Alpha Vantage
- **Dérivés**: Funding rates, Open Interest

---

Pour la partie **modèles d'IA** et **entraînement**, voir le dossier [ai/](../ai/)
