"""
MongoDB Pipeline for VPN/Proxy storage
Stores scraped VPNs in MongoDB with deduplication and indexing
"""

import logging
from datetime import datetime
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

logger = logging.getLogger(__name__)


class VPNMongoDBPipeline:
    """
    Pipeline to store VPN proxies in MongoDB.

    Features:
    - Deduplication by IP:PORT
    - Automatic indexing
    - Performance tracking
    - Expiration of old/failed proxies
    """

    def __init__(self, mongo_uri, mongo_db, collection_name):
        self.mongo_uri = mongo_uri
        self.mongo_db = mongo_db
        self.collection_name = collection_name

        self.client = None
        self.db = None
        self.collection = None

        # Stats
        self.stats = {
            'items_processed': 0,
            'items_inserted': 0,
            'items_updated': 0,
            'items_skipped': 0,
            'errors': 0,
        }

    @classmethod
    def from_crawler(cls, crawler):
        """Initialize from crawler settings"""
        return cls(
            mongo_uri=crawler.settings.get('MONGODB_URI', 'mongodb://localhost:27017/'),
            mongo_db=crawler.settings.get('MONGODB_DATABASE', 'scrapers_db'),
            collection_name=crawler.settings.get('MONGODB_VPN_COLLECTION', 'vpn_proxies'),
        )

    def open_spider(self, spider):
        """Open MongoDB connection and create indexes"""
        try:
            self.client = MongoClient(self.mongo_uri)
            self.db = self.client[self.mongo_db]
            self.collection = self.db[self.collection_name]

            # Create indexes for efficient queries
            self._create_indexes()

            logger.info(f"📊 VPNMongoDBPipeline opened: {self.mongo_db}.{self.collection_name}")
            logger.info(f"🔗 Connected to MongoDB: {self.mongo_uri}")

            # Log current count
            count = self.collection.count_documents({})
            logger.info(f"📦 Current VPN count in database: {count}")

        except Exception as e:
            logger.error(f"❌ Failed to connect to MongoDB: {e}")
            raise

    def close_spider(self, spider):
        """Close MongoDB connection and log stats"""
        logger.info("=" * 80)
        logger.info("📊 VPNMongoDBPipeline FINAL STATS")
        logger.info("=" * 80)
        logger.info(f"Items processed: {self.stats['items_processed']}")
        logger.info(f"Items inserted: {self.stats['items_inserted']}")
        logger.info(f"Items updated: {self.stats['items_updated']}")
        logger.info(f"Items skipped: {self.stats['items_skipped']}")
        logger.info(f"Errors: {self.stats['errors']}")

        # Log final count
        if self.collection is not None:
            active_count = self.collection.count_documents({'is_active': True})
            total_count = self.collection.count_documents({})
            logger.info(f"📦 Total VPNs in database: {total_count}")
            logger.info(f"✅ Active VPNs: {active_count}")
            logger.info(f"❌ Inactive VPNs: {total_count - active_count}")

        logger.info("=" * 80)

        # Close MongoDB connection
        if self.client is not None:
            self.client.close()
            logger.info("🔌 MongoDB connection closed")

    def process_item(self, item, spider):
        """Process and store VPN item in MongoDB"""
        self.stats['items_processed'] += 1

        try:
            # Convert item to dict
            vpn_data = dict(item)

            # Create unique key (IP + Port)
            unique_key = f"{vpn_data['ip']}:{vpn_data['port']}"

            # Check if proxy already exists
            existing = self.collection.find_one({'_unique_key': unique_key})

            if existing:
                # Update existing proxy
                self._update_existing_proxy(unique_key, vpn_data, existing)
                self.stats['items_updated'] += 1
            else:
                # Insert new proxy
                self._insert_new_proxy(unique_key, vpn_data)
                self.stats['items_inserted'] += 1

            return item

        except Exception as e:
            logger.error(f"❌ Error processing VPN item: {e}")
            logger.error(f"   Item: {item}")
            self.stats['errors'] += 1
            return item

    def _insert_new_proxy(self, unique_key, vpn_data):
        """Insert a new proxy into MongoDB"""
        try:
            # Add metadata
            vpn_data['_unique_key'] = unique_key
            vpn_data['created_at'] = datetime.utcnow().isoformat()
            vpn_data['updated_at'] = datetime.utcnow().isoformat()

            # Initialize performance tracking if not set
            if 'success_count' not in vpn_data:
                vpn_data['success_count'] = 0
            if 'fail_count' not in vpn_data:
                vpn_data['fail_count'] = 0

            # Insert
            result = self.collection.insert_one(vpn_data)

            logger.debug(f"✅ Inserted new VPN: {vpn_data['proxy_url']} (source: {vpn_data['source']})")

        except DuplicateKeyError:
            # Race condition: another process inserted it
            logger.debug(f"⚠️ Duplicate VPN (race condition): {unique_key}")
            self.stats['items_skipped'] += 1

        except Exception as e:
            logger.error(f"❌ Error inserting VPN: {e}")
            self.stats['errors'] += 1

    def _update_existing_proxy(self, unique_key, new_data, existing_data):
        """Update existing proxy with new information"""
        try:
            update_fields = {
                'updated_at': datetime.utcnow().isoformat(),
                'source': new_data.get('source', existing_data.get('source')),
                'scraped_at': new_data.get('scraped_at'),
            }

            # Re-activate if was inactive (proxy seen again in new scrape)
            if not existing_data.get('is_active', True):
                update_fields['is_active'] = True
                logger.debug(f"🔄 Re-activating VPN: {new_data['proxy_url']}")

            # Update fields that might have changed
            if 'country' in new_data and new_data['country']:
                update_fields['country'] = new_data['country']
            if 'country_code' in new_data and new_data['country_code']:
                update_fields['country_code'] = new_data['country_code']
            if 'anonymity_level' in new_data and new_data['anonymity_level']:
                update_fields['anonymity_level'] = new_data['anonymity_level']

            # Update in DB
            self.collection.update_one(
                {'_unique_key': unique_key},
                {'$set': update_fields}
            )

            logger.debug(f"🔄 Updated VPN: {new_data['proxy_url']}")

        except Exception as e:
            logger.error(f"❌ Error updating VPN: {e}")
            self.stats['errors'] += 1

    def _create_indexes(self):
        """Create MongoDB indexes for efficient queries"""
        try:
            # Unique index on IP:PORT combination
            self.collection.create_index(
                [('_unique_key', ASCENDING)],
                unique=True,
                name='unique_ip_port'
            )

            # Index on is_active for filtering
            self.collection.create_index(
                [('is_active', ASCENDING)],
                name='active_status'
            )

            # Index on success/fail ratio for sorting by reliability
            self.collection.create_index(
                [('success_count', DESCENDING), ('fail_count', ASCENDING)],
                name='reliability_score'
            )

            # Index on response time for sorting by speed
            self.collection.create_index(
                [('response_time', ASCENDING)],
                name='response_time'
            )

            # Index on last_success for finding recently working proxies
            self.collection.create_index(
                [('last_success', DESCENDING)],
                name='last_success'
            )

            # Index on protocol for filtering
            self.collection.create_index(
                [('protocol', ASCENDING)],
                name='protocol'
            )

            # Index on country for geo-filtering
            self.collection.create_index(
                [('country_code', ASCENDING)],
                name='country'
            )

            # Compound index for active + reliable proxies
            self.collection.create_index(
                [
                    ('is_active', ASCENDING),
                    ('success_count', DESCENDING),
                    ('fail_count', ASCENDING)
                ],
                name='active_reliable'
            )

            logger.info("✅ MongoDB indexes created successfully")

        except Exception as e:
            logger.warning(f"⚠️ Error creating indexes (may already exist): {e}")


class VPNCleanupPipeline:
    """
    Optional pipeline to clean up old/failed proxies.
    Run this periodically to keep the database clean.
    """

    def __init__(self, mongo_uri, mongo_db, collection_name):
        self.mongo_uri = mongo_uri
        self.mongo_db = mongo_db
        self.collection_name = collection_name

        self.client = None
        self.db = None
        self.collection = None

        # Cleanup thresholds
        self.max_fail_count = 10  # Mark inactive after 10 failures
        self.max_fail_ratio = 0.8  # 80% fail rate = inactive

    @classmethod
    def from_crawler(cls, crawler):
        """Initialize from crawler settings"""
        return cls(
            mongo_uri=crawler.settings.get('MONGODB_URI', 'mongodb://localhost:27017/'),
            mongo_db=crawler.settings.get('MONGODB_DATABASE', 'scrapers_db'),
            collection_name=crawler.settings.get('MONGODB_VPN_COLLECTION', 'vpn_proxies'),
        )

    def open_spider(self, spider):
        """Open MongoDB connection"""
        self.client = MongoClient(self.mongo_uri)
        self.db = self.client[self.mongo_db]
        self.collection = self.db[self.collection_name]

        # Run cleanup
        self._cleanup_failed_proxies()

    def close_spider(self, spider):
        """Close connection"""
        if self.client:
            self.client.close()

    def process_item(self, item, spider):
        """Pass through - cleanup happens in open_spider"""
        return item

    def _cleanup_failed_proxies(self):
        """Mark proxies as inactive if they have too many failures"""
        try:
            # Find proxies with high fail count
            result = self.collection.update_many(
                {
                    'is_active': True,
                    'fail_count': {'$gte': self.max_fail_count}
                },
                {
                    '$set': {
                        'is_active': False,
                        'updated_at': datetime.utcnow().isoformat()
                    }
                }
            )

            if result.modified_count > 0:
                logger.info(f"🧹 Marked {result.modified_count} proxies as inactive (high fail count)")

            # Find proxies with high fail ratio
            # Note: This requires aggregation pipeline
            pipeline = [
                {
                    '$match': {
                        'is_active': True,
                        'success_count': {'$gt': 0}  # Has been tested
                    }
                },
                {
                    '$addFields': {
                        'total_attempts': {'$add': ['$success_count', '$fail_count']},
                        'fail_ratio': {
                            '$divide': [
                                '$fail_count',
                                {'$add': ['$success_count', '$fail_count']}
                            ]
                        }
                    }
                },
                {
                    '$match': {
                        'fail_ratio': {'$gte': self.max_fail_ratio}
                    }
                }
            ]

            failed_proxies = list(self.collection.aggregate(pipeline))

            if failed_proxies:
                ids = [p['_id'] for p in failed_proxies]
                result = self.collection.update_many(
                    {'_id': {'$in': ids}},
                    {
                        '$set': {
                            'is_active': False,
                            'updated_at': datetime.utcnow().isoformat()
                        }
                    }
                )
                logger.info(f"🧹 Marked {result.modified_count} proxies as inactive (high fail ratio)")

        except Exception as e:
            logger.error(f"❌ Error during cleanup: {e}")
