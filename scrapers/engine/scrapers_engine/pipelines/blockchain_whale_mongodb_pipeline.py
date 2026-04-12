"""
Pipeline MongoDB unifié pour stocker les transactions whale de toutes les blockchains
Support: Bitcoin, Ethereum, Solana
"""

import logging
from datetime import datetime
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError, ConnectionFailure
import hashlib
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.price_service import get_price_service
from utils.address_labeling_service import get_labeling_service


class BlockchainWhaleMongoDBPipeline:
    """Pipeline unifié pour stocker les transactions whale multi-blockchain"""

    def __init__(self, mongo_uri, mongo_db='whale_data', min_usd_value=100000,
                 etherscan_api_key=None):
        self.logger = logging.getLogger(__name__)
        self.mongo_uri = mongo_uri
        self.mongo_db = mongo_db
        self.min_usd_value = min_usd_value
        self.client = None
        self.db = None
        self.collection = None

        # Services
        self.price_service = get_price_service()
        self.labeling_service = get_labeling_service(etherscan_api_key)

        # Stats
        self.stats = {
            'inserted': 0,
            'duplicates': 0,
            'filtered_below_threshold': 0,
            'errors': 0,
            'total_processed': 0,
            'by_blockchain': {}
        }

    @classmethod
    def from_crawler(cls, crawler):
        """Récupère la configuration depuis les settings"""
        mongo_uri = crawler.settings.get(
            'BLOCKCHAIN_MONGODB_URI',
            'mongodb+srv://christoloisel:rose@cluster0.ppyauvl.mongodb.net/'
        )
        mongo_db = crawler.settings.get('BLOCKCHAIN_MONGODB_DATABASE', 'whale_data')
        min_usd_value = crawler.settings.get('WHALE_MIN_USD_VALUE', 100000)
        etherscan_api_key = crawler.settings.get('ETHERSCAN_API_KEY', None)

        return cls(
            mongo_uri=mongo_uri,
            mongo_db=mongo_db,
            min_usd_value=min_usd_value,
            etherscan_api_key=etherscan_api_key
        )

    def open_spider(self, spider):
        """Connexion à MongoDB au démarrage du spider"""
        try:
            self.logger.info(f"🔌 Connecting to MongoDB: {self.mongo_db}")

            # Connexion avec timeout
            self.client = MongoClient(
                self.mongo_uri,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=10000,
                socketTimeoutMS=10000
            )

            # Test de connexion
            self.client.admin.command('ping')

            # Sélection de la base et collection
            self.db = self.client[self.mongo_db]
            self.collection = self.db['whale_transactions']

            # Création des index
            self._create_indexes()

            self.logger.info(f"✅ Connected to MongoDB successfully")
            self.logger.info(f"📊 Database: {self.mongo_db}")
            self.logger.info(f"📦 Collection: whale_transactions")
            self.logger.info(f"💰 Min USD threshold: ${self.min_usd_value:,}")

        except ConnectionFailure as e:
            self.logger.error(f"❌ MongoDB connection failed: {e}")
            raise
        except Exception as e:
            self.logger.error(f"❌ Error opening MongoDB: {e}")
            raise

    def close_spider(self, spider):
        """Fermeture de la connexion MongoDB"""
        if self.client:
            self.client.close()
            self.logger.info(f"🔌 MongoDB connection closed")

        # Affichage des stats
        self.logger.info(f"📊 Pipeline Statistics:")
        self.logger.info(f"  ✅ Inserted: {self.stats['inserted']}")
        self.logger.info(f"  🔄 Duplicates: {self.stats['duplicates']}")
        self.logger.info(f"  ⬇️  Filtered (below threshold): {self.stats['filtered_below_threshold']}")
        self.logger.info(f"  ❌ Errors: {self.stats['errors']}")
        self.logger.info(f"  📦 Total processed: {self.stats['total_processed']}")

        if self.stats['by_blockchain']:
            self.logger.info(f"  📈 By blockchain:")
            for blockchain, count in self.stats['by_blockchain'].items():
                self.logger.info(f"    {blockchain.upper()}: {count}")

        # Stats des services
        price_stats = self.price_service.get_stats()
        label_stats = self.labeling_service.get_stats()
        self.logger.info(f"  💵 Price Service - API calls: {price_stats['api_calls']}, Cache hit rate: {price_stats['cache_hit_rate']}")
        self.logger.info(f"  🏷️  Label Service - Known addresses: {label_stats['known_addresses_count']}, Cache hit rate: {label_stats['cache_hit_rate']}")

    def process_item(self, item, spider):
        """Traite et stocke chaque transaction"""
        self.stats['total_processed'] += 1

        try:
            # Conversion de l'item en dict
            doc = dict(item)

            # Enrichissement: Calcul valeur USD si manquante
            if not doc.get('amount_usd') and doc.get('amount') and doc.get('symbol'):
                timestamp = self._parse_timestamp(doc.get('timestamp'))
                amount_usd = self.price_service.calculate_usd_value(
                    doc['amount'],
                    doc['symbol'],
                    timestamp
                )
                if amount_usd:
                    doc['amount_usd'] = amount_usd
                    self.logger.debug(f"Calculated USD value: ${amount_usd:,.2f}")

            # Filtrage: Ignorer si en dessous du seuil
            if doc.get('amount_usd', 0) < self.min_usd_value:
                self.stats['filtered_below_threshold'] += 1
                return item

            # Enrichissement: Identification des adresses
            blockchain = doc.get('blockchain', 'bitcoin').lower()

            if doc.get('from_address'):
                from_label = self.labeling_service.identify_address(
                    doc['from_address'],
                    blockchain
                )
                doc['from_owner'] = from_label['owner']
                doc['from_type'] = from_label['type']
                doc['labels_from'] = [from_label['owner'], from_label['type']]

            if doc.get('to_address'):
                to_label = self.labeling_service.identify_address(
                    doc['to_address'],
                    blockchain
                )
                doc['to_owner'] = to_label['owner']
                doc['to_type'] = to_label['type']
                doc['labels_to'] = [to_label['owner'], to_label['type']]

            # Classification du type de transaction
            if doc.get('from_owner') and doc.get('to_owner'):
                from_label_dict = {'owner': doc['from_owner'], 'type': doc.get('from_type', 'unknown')}
                to_label_dict = {'owner': doc['to_owner'], 'type': doc.get('to_type', 'unknown')}
                doc['transaction_type'] = self.labeling_service.classify_transaction_type(
                    from_label_dict,
                    to_label_dict
                )

            # Génération d'un ID unique
            doc['_id'] = self._generate_doc_id(doc)

            # Ajout de métadonnées
            doc['inserted_at'] = datetime.utcnow()
            doc['spider_name'] = spider.name

            # Conversion des timestamps ISO en datetime
            if 'timestamp' in doc and isinstance(doc['timestamp'], str):
                doc['timestamp'] = self._parse_timestamp(doc['timestamp'])

            if 'scraped_at' in doc and isinstance(doc['scraped_at'], str):
                doc['scraped_at'] = self._parse_timestamp(doc['scraped_at'])

            # Prix USD de l'asset au moment de la transaction
            if not doc.get('price_usd') and doc.get('symbol') and doc.get('timestamp'):
                price = self.price_service.get_price(doc['symbol'], doc['timestamp'])
                if price:
                    doc['price_usd'] = price

            # Calcul fees USD si fees présents
            if doc.get('fees') and doc.get('symbol') and not doc.get('fees_usd'):
                fees_usd = self.price_service.calculate_usd_value(
                    doc['fees'],
                    doc['symbol'],
                    doc.get('timestamp')
                )
                if fees_usd:
                    doc['fees_usd'] = fees_usd

            # Insertion dans MongoDB
            self.collection.insert_one(doc)
            self.stats['inserted'] += 1

            # Stats par blockchain
            blockchain_key = doc.get('blockchain', 'unknown')
            self.stats['by_blockchain'][blockchain_key] = \
                self.stats['by_blockchain'].get(blockchain_key, 0) + 1

            if self.stats['inserted'] % 100 == 0:
                self.logger.info(f"💾 Saved {self.stats['inserted']} whale transactions to MongoDB")

        except DuplicateKeyError:
            # Transaction déjà existante (normal)
            self.stats['duplicates'] += 1
            self.logger.debug(f"Duplicate transaction: {doc.get('tx_hash', 'unknown')}")
        except Exception as e:
            self.stats['errors'] += 1
            self.logger.error(f"❌ Error saving transaction: {e}")
            self.logger.error(f"Transaction data: {dict(item)}")

        return item

    def _generate_doc_id(self, doc):
        """
        Génère un ID unique pour le document
        Format: blockchain:tx_hash
        """
        blockchain = doc.get('blockchain', 'unknown').lower()
        tx_hash = doc.get('tx_hash', '')

        # Pour garantir l'unicité même si hash manquant
        if not tx_hash:
            unique_str = f"{blockchain}:{doc.get('from_address', '')}:{doc.get('to_address', '')}:{doc.get('timestamp', '')}"
            return hashlib.md5(unique_str.encode()).hexdigest()

        return f"{blockchain}:{tx_hash}"

    def _parse_timestamp(self, timestamp_str):
        """Parse timestamp string to datetime"""
        if not timestamp_str:
            return datetime.utcnow()

        if isinstance(timestamp_str, datetime):
            return timestamp_str

        try:
            # ISO format
            return datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        except Exception:
            try:
                # Unix timestamp
                return datetime.fromtimestamp(float(timestamp_str))
            except Exception:
                self.logger.warning(f"Could not parse timestamp: {timestamp_str}")
                return datetime.utcnow()

    def _create_indexes(self):
        """Crée les index pour optimiser les requêtes"""
        try:
            # Index sur blockchain + timestamp (requêtes par blockchain)
            self.collection.create_index([
                ('blockchain', ASCENDING),
                ('timestamp', DESCENDING)
            ])

            # Index sur le symbole
            self.collection.create_index([('symbol', ASCENDING)])

            # Index sur le type de transaction
            self.collection.create_index([('transaction_type', ASCENDING)])

            # Index sur les montants USD (pour top transactions)
            self.collection.create_index([('amount_usd', DESCENDING)])

            # Index composé pour requêtes complexes
            self.collection.create_index([
                ('symbol', ASCENDING),
                ('timestamp', DESCENDING),
                ('amount_usd', DESCENDING)
            ])

            # Index sur les propriétaires (from/to)
            self.collection.create_index([('from_owner', ASCENDING)])
            self.collection.create_index([('to_owner', ASCENDING)])
            self.collection.create_index([('from_type', ASCENDING)])
            self.collection.create_index([('to_type', ASCENDING)])

            # Index sur block_number
            self.collection.create_index([
                ('blockchain', ASCENDING),
                ('block_number', DESCENDING)
            ])

            # Index sur les adresses
            self.collection.create_index([('from_address', ASCENDING)])
            self.collection.create_index([('to_address', ASCENDING)])

            # Index texte pour recherche
            self.collection.create_index([
                ('from_owner', 'text'),
                ('to_owner', 'text'),
                ('symbol', 'text')
            ])

            self.logger.info("✅ MongoDB indexes created successfully")

        except Exception as e:
            self.logger.warning(f"⚠️ Could not create indexes: {e}")
