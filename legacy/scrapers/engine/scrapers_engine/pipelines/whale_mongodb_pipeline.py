"""
MongoDB Pipeline pour stocker les transactions Whale Alert
"""

import logging
from datetime import datetime
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError, ConnectionFailure
import hashlib


class WhaleMongoDBPipeline:
    """Pipeline pour stocker les transactions whale dans MongoDB"""

    def __init__(self, mongo_uri, mongo_db='whale_data'):
        self.logger = logging.getLogger(__name__)
        self.mongo_uri = mongo_uri
        self.mongo_db = mongo_db
        self.client = None
        self.db = None
        self.collection = None

        # Stats
        self.stats = {
            'inserted': 0,
            'duplicates': 0,
            'errors': 0,
            'total_processed': 0
        }

    @classmethod
    def from_crawler(cls, crawler):
        """Récupère la configuration depuis les settings"""
        mongo_uri = crawler.settings.get('WHALE_MONGODB_URI',
                                        'mongodb+srv://christoloisel:rose@cluster0.ppyauvl.mongodb.net/')
        mongo_db = crawler.settings.get('WHALE_MONGODB_DATABASE', 'whale_data')

        return cls(
            mongo_uri=mongo_uri,
            mongo_db=mongo_db
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
        self.logger.info(f"  ❌ Errors: {self.stats['errors']}")
        self.logger.info(f"  📦 Total processed: {self.stats['total_processed']}")

    def process_item(self, item, spider):
        """Traite et stocke chaque transaction"""
        self.stats['total_processed'] += 1

        try:
            # Conversion de l'item en dict
            doc = dict(item)

            # Génération d'un ID unique basé sur le hash de transaction
            doc['_id'] = self._generate_doc_id(doc)

            # Ajout de métadonnées
            doc['inserted_at'] = datetime.utcnow()
            doc['spider_name'] = spider.name

            # Conversion des timestamps ISO en datetime
            if 'timestamp' in doc and isinstance(doc['timestamp'], str):
                doc['timestamp'] = datetime.fromisoformat(doc['timestamp'].replace('Z', '+00:00'))

            if 'scraped_at' in doc and isinstance(doc['scraped_at'], str):
                doc['scraped_at'] = datetime.fromisoformat(doc['scraped_at'].replace('Z', '+00:00'))

            # Insertion dans MongoDB
            self.collection.insert_one(doc)
            self.stats['inserted'] += 1

            if self.stats['inserted'] % 100 == 0:
                self.logger.info(f"💾 Saved {self.stats['inserted']} transactions to MongoDB")

        except DuplicateKeyError:
            # Transaction déjà existante (normal)
            self.stats['duplicates'] += 1
        except Exception as e:
            self.stats['errors'] += 1
            self.logger.error(f"❌ Error saving transaction: {e}")
            self.logger.error(f"Transaction data: {dict(item)}")

        return item

    def _generate_doc_id(self, doc):
        """Génère un ID unique pour le document"""
        # Utilise le hash de transaction + blockchain comme ID unique
        tx_hash = doc.get('tx_hash', '')
        blockchain = doc.get('blockchain', '')
        timestamp = doc.get('timestamp', '')

        unique_str = f"{blockchain}:{tx_hash}:{timestamp}"
        return hashlib.md5(unique_str.encode()).hexdigest()

    def _create_indexes(self):
        """Crée les index pour optimiser les requêtes"""
        try:
            # Index sur le timestamp (pour les requêtes temporelles)
            self.collection.create_index([('timestamp', DESCENDING)])

            # Index sur le symbole (BTC, ETH, etc.)
            self.collection.create_index([('symbol', ASCENDING)])

            # Index sur le type de transaction
            self.collection.create_index([('transaction_type', ASCENDING)])

            # Index sur les montants
            self.collection.create_index([('amount_usd', DESCENDING)])

            # Index composé pour les requêtes complexes
            self.collection.create_index([
                ('symbol', ASCENDING),
                ('timestamp', DESCENDING)
            ])

            # Index sur les adresses
            self.collection.create_index([('from_address', ASCENDING)])
            self.collection.create_index([('to_address', ASCENDING)])

            # Index sur les propriétaires (exchanges, etc.)
            self.collection.create_index([('from_owner', ASCENDING)])
            self.collection.create_index([('to_owner', ASCENDING)])

            self.logger.info("✅ MongoDB indexes created successfully")

        except Exception as e:
            self.logger.warning(f"⚠️ Could not create indexes: {e}")
