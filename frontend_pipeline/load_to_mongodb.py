#!/usr/bin/env python3
"""
Script pour charger tous les datasets dans MongoDB
Database: trader
Collections:
- historical_ohlcv: Données historiques crypto (29 cryptos × 1 an)
- realtime_ticks: Données temps réel websocket
- alpha_* : Datasets alpha trading (binance_ohlcv, funding_rates, etc.)
- dataset_metadata: Métadonnées des datasets
"""

import os
import pandas as pd
import pymongo
from pymongo import MongoClient, ASCENDING, DESCENDING
from pathlib import Path
from datetime import datetime
import logging
from typing import Dict, List
import json

# Configuration logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration MongoDB
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://christoloisel:rose@cluster0.ppyauvl.mongodb.net//")
DATABASE_NAME = os.getenv("MONGO_DB", "trader")

# Chemins des datasets
BASE_DIR = Path(__file__).parent
HISTORICAL_DIR = BASE_DIR / "datasets" / "historical_crypto"
REALTIME_DIR = BASE_DIR / "datasets" / "realtime"
ALPHA_DIR = BASE_DIR / "datasets" / "alpha_trading"


class MongoDBLoader:
    """Charge tous les datasets dans MongoDB"""

    def __init__(self, mongo_uri: str, database: str):
        """Initialize MongoDB connection"""
        self.client = MongoClient(mongo_uri)
        self.db = self.client[database]
        logger.info(f"Connected to MongoDB database: {database}")

    def create_indexes(self):
        """Créer les index pour optimiser les requêtes"""
        logger.info("Creating indexes...")

        # Index pour historical_ohlcv
        self.db.historical_ohlcv.create_index([("symbol", ASCENDING), ("timestamp", DESCENDING)])
        self.db.historical_ohlcv.create_index([("timestamp", DESCENDING)])

        # Index pour realtime_ticks
        self.db.realtime_ticks.create_index([("symbol", ASCENDING), ("timestamp", DESCENDING)])
        self.db.realtime_ticks.create_index([("timestamp", DESCENDING)])

        # Index pour alpha collections
        for collection_name in ["alpha_binance_ohlcv", "alpha_funding_rates",
                               "alpha_long_short_ratio", "alpha_open_interest"]:
            self.db[collection_name].create_index([("symbol", ASCENDING), ("timestamp", DESCENDING)])

        logger.info("✅ Indexes created")

    def load_historical_crypto(self, batch_size: int = 1000):
        """Charger les données historiques crypto (29 fichiers Parquet)"""
        logger.info("Loading historical crypto data...")

        if not HISTORICAL_DIR.exists():
            logger.warning(f"Historical directory not found: {HISTORICAL_DIR}")
            return

        parquet_files = list(HISTORICAL_DIR.glob("*_1h_*.parquet"))
        logger.info(f"Found {len(parquet_files)} historical crypto files")

        collection = self.db.historical_ohlcv
        total_inserted = 0

        for file_path in parquet_files:
            try:
                # Extraire le symbole du nom de fichier
                # Format: BTC_USDT_1h_20251130.parquet
                symbol_parts = file_path.stem.split('_')
                symbol = f"{symbol_parts[0]}/{symbol_parts[1]}"  # BTC/USDT

                logger.info(f"Loading {symbol} from {file_path.name}...")

                # Charger le fichier Parquet
                df = pd.read_parquet(file_path)

                # Ajouter le symbole à chaque ligne
                df['symbol'] = symbol

                # Convertir en dictionnaires
                records = df.to_dict('records')

                # Insérer par batch
                for i in range(0, len(records), batch_size):
                    batch = records[i:i + batch_size]
                    collection.insert_many(batch, ordered=False)

                total_inserted += len(records)
                logger.info(f"✅ {symbol}: {len(records):,} rows inserted")

            except Exception as e:
                logger.error(f"Error loading {file_path.name}: {e}")

        logger.info(f"✅ Historical crypto data loaded: {total_inserted:,} total rows")
        return total_inserted

    def load_realtime_data(self, batch_size: int = 1000):
        """Charger les données temps réel websocket"""
        logger.info("Loading realtime websocket data...")

        if not REALTIME_DIR.exists():
            logger.warning(f"Realtime directory not found: {REALTIME_DIR}")
            return

        parquet_files = list(REALTIME_DIR.glob("*.parquet"))
        logger.info(f"Found {len(parquet_files)} realtime files")

        collection = self.db.realtime_ticks
        total_inserted = 0

        for file_path in parquet_files:
            try:
                # Extraire le symbole du nom de fichier
                # Format: BINANCE:BTCUSDT_20251130_002812.parquet
                filename = file_path.stem
                symbol_part = filename.split('_')[0]  # BINANCE:BTCUSDT

                logger.info(f"Loading {symbol_part} from {file_path.name}...")

                # Charger le fichier Parquet
                df = pd.read_parquet(file_path)

                # Convertir en dictionnaires
                records = df.to_dict('records')

                # Insérer par batch
                for i in range(0, len(records), batch_size):
                    batch = records[i:i + batch_size]
                    try:
                        collection.insert_many(batch, ordered=False)
                    except pymongo.errors.BulkWriteError:
                        # Ignorer les doublons
                        pass

                total_inserted += len(records)
                logger.info(f"✅ {symbol_part}: {len(records):,} ticks inserted")

            except Exception as e:
                logger.error(f"Error loading {file_path.name}: {e}")

        logger.info(f"✅ Realtime data loaded: {total_inserted:,} total ticks")
        return total_inserted

    def load_alpha_trading_data(self, batch_size: int = 1000):
        """Charger les datasets alpha trading"""
        logger.info("Loading alpha trading datasets...")

        if not ALPHA_DIR.exists():
            logger.warning(f"Alpha directory not found: {ALPHA_DIR}")
            return

        # Trouver le dataset le plus récent
        dataset_dirs = sorted(ALPHA_DIR.glob("dataset_*"), reverse=True)
        if not dataset_dirs:
            logger.warning("No alpha trading datasets found")
            return

        latest_dataset = dataset_dirs[0]
        logger.info(f"Using latest dataset: {latest_dataset.name}")

        # Mapping des fichiers vers les collections
        file_to_collection = {
            "binance_ohlcv.parquet": "alpha_binance_ohlcv",
            "funding_rates.parquet": "alpha_funding_rates",
            "long_short_ratio.parquet": "alpha_long_short_ratio",
            "open_interest.parquet": "alpha_open_interest",
            "fear_greed_index.parquet": "alpha_fear_greed_index",
            "fred_economic.parquet": "alpha_fred_economic",
            "stock_indices.parquet": "alpha_stock_indices",
            "reddit_sentiment.parquet": "alpha_reddit_sentiment",
            "onchain_metrics.parquet": "alpha_onchain_metrics",
            "exchange_flows.parquet": "alpha_exchange_flows",
        }

        total_stats = {}

        for filename, collection_name in file_to_collection.items():
            file_path = latest_dataset / filename

            if not file_path.exists():
                logger.warning(f"File not found: {filename}")
                continue

            try:
                logger.info(f"Loading {filename}...")

                # Charger le fichier Parquet
                df = pd.read_parquet(file_path)

                # Convertir en dictionnaires
                records = df.to_dict('records')

                if not records:
                    logger.warning(f"{filename} is empty")
                    continue

                collection = self.db[collection_name]

                # Insérer par batch
                inserted = 0
                for i in range(0, len(records), batch_size):
                    batch = records[i:i + batch_size]
                    try:
                        result = collection.insert_many(batch, ordered=False)
                        inserted += len(result.inserted_ids)
                    except pymongo.errors.BulkWriteError as e:
                        # Compter les insertions réussies
                        inserted += e.details.get('nInserted', 0)

                total_stats[collection_name] = inserted
                logger.info(f"✅ {collection_name}: {inserted:,} rows inserted")

            except Exception as e:
                logger.error(f"Error loading {filename}: {e}")

        # Sauvegarder les métadonnées
        metadata = {
            "dataset_folder": latest_dataset.name,
            "loaded_at": datetime.now(),
            "collections": total_stats,
            "total_rows": sum(total_stats.values())
        }

        self.db.alpha_metadata.insert_one(metadata)
        logger.info(f"✅ Alpha trading data loaded: {metadata['total_rows']:,} total rows")

        return metadata

    def load_trading_plan(self):
        """Charger le plan de trading s'il existe"""
        alpha_dirs = sorted(ALPHA_DIR.glob("dataset_*"), reverse=True)
        if not alpha_dirs:
            return

        latest_dataset = alpha_dirs[0]
        plan_file = latest_dataset / "trading_plan.json"

        if plan_file.exists():
            try:
                with open(plan_file, 'r') as f:
                    plan = json.load(f)
                    plan['loaded_at'] = datetime.now()
                    self.db.alpha_trading_plan.insert_one(plan)
                    logger.info("✅ Trading plan loaded")
            except Exception as e:
                logger.error(f"Error loading trading plan: {e}")

    def get_database_stats(self) -> Dict:
        """Obtenir les statistiques de la base de données"""
        stats = {
            "database": DATABASE_NAME,
            "collections": {}
        }

        for collection_name in self.db.list_collection_names():
            count = self.db[collection_name].count_documents({})
            stats["collections"][collection_name] = count

        stats["total_documents"] = sum(stats["collections"].values())

        return stats

    def close(self):
        """Fermer la connexion MongoDB"""
        self.client.close()
        logger.info("MongoDB connection closed")


def main():
    """Fonction principale"""
    logger.info("=" * 60)
    logger.info("MongoDB Data Loader - Starting")
    logger.info("=" * 60)

    start_time = datetime.now()

    try:
        # Initialiser le loader
        loader = MongoDBLoader(MONGO_URI, DATABASE_NAME)

        # Créer les index
        loader.create_indexes()

        # Charger les données historiques crypto
        hist_count = loader.load_historical_crypto()

        # Charger les données temps réel
        realtime_count = loader.load_realtime_data()

        # Charger les datasets alpha trading
        alpha_meta = loader.load_alpha_trading_data()

        # Charger le plan de trading
        loader.load_trading_plan()

        # Afficher les statistiques
        logger.info("=" * 60)
        logger.info("Database Statistics:")
        logger.info("=" * 60)

        stats = loader.get_database_stats()
        for collection, count in sorted(stats["collections"].items()):
            logger.info(f"  {collection:30} {count:,} documents")

        logger.info("=" * 60)
        logger.info(f"Total documents: {stats['total_documents']:,}")

        duration = (datetime.now() - start_time).total_seconds()
        logger.info(f"Duration: {duration:.2f} seconds")
        logger.info("=" * 60)
        logger.info("✅ MONGODB LOADING COMPLETED SUCCESSFULLY!")
        logger.info("=" * 60)

        # Fermer la connexion
        loader.close()

    except Exception as e:
        logger.error(f"❌ Error during MongoDB loading: {e}", exc_info=True)
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
