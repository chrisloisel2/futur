"""
MongoDB Pipeline for VPN/Proxy storage WITH TESTING
Stores ONLY working VPNs in MongoDB after testing them
"""

import logging
from datetime import datetime
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError
import requests
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue
import threading

logger = logging.getLogger(__name__)


class VPNMongoDBPipelineWithTesting:
    """
    Pipeline to test and store VPN proxies in MongoDB.

    Features:
    - Tests each VPN before storing
    - Only stores working VPNs
    - Measures response time
    - Batch testing with threading
    - Deduplication by IP:PORT
    """

    # Test URLs (rapides et fiables)
    TEST_URLS = [
        'http://httpbin.org/ip',
        'https://api.ipify.org?format=json',
        'http://ip-api.com/json/',
    ]

    def __init__(
        self,
        mongo_uri,
        mongo_db,
        collection_name,
        test_vpn=True,
        test_timeout=10,
        max_workers=50,
        batch_size=100
    ):
        self.mongo_uri = mongo_uri
        self.mongo_db = mongo_db
        self.collection_name = collection_name
        self.test_vpn = test_vpn
        self.test_timeout = test_timeout
        self.max_workers = max_workers
        self.batch_size = batch_size

        self.client = None
        self.db = None
        self.collection = None

        # Batch queue pour tester en lot
        self.test_queue = Queue()
        self.batch_lock = threading.Lock()

        # Stats
        self.stats = {
            'items_received': 0,
            'items_tested': 0,
            'items_passed': 0,
            'items_failed': 0,
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
            test_vpn=crawler.settings.getbool('VPN_TEST_BEFORE_STORE', True),
            test_timeout=crawler.settings.getint('VPN_TEST_TIMEOUT', 10),
            max_workers=crawler.settings.getint('VPN_TEST_WORKERS', 50),
            batch_size=crawler.settings.getint('VPN_TEST_BATCH_SIZE', 100),
        )

    def open_spider(self, spider):
        """Open MongoDB connection and create indexes"""
        try:
            self.client = MongoClient(self.mongo_uri)
            self.db = self.client[self.mongo_db]
            self.collection = self.db[self.collection_name]

            # Create indexes for efficient queries
            self._create_indexes()

            logger.info(f"📊 VPNMongoDBPipeline (WITH TESTING) opened: {self.mongo_db}.{self.collection_name}")
            logger.info(f"🔗 Connected to MongoDB: {self.mongo_uri}")
            logger.info(f"🧪 VPN Testing: {'ENABLED' if self.test_vpn else 'DISABLED'}")
            logger.info(f"⚙️  Test timeout: {self.test_timeout}s, Workers: {self.max_workers}, Batch: {self.batch_size}")

            # Log current count
            count = self.collection.count_documents({})
            logger.info(f"📦 Current VPN count in database: {count}")

        except Exception as e:
            logger.error(f"❌ Failed to connect to MongoDB: {e}")
            raise

    def close_spider(self, spider):
        """Close MongoDB connection and log stats"""
        # Process remaining items in queue
        if not self.test_queue.empty():
            logger.info(f"🧪 Testing remaining {self.test_queue.qsize()} VPNs...")
            self._process_test_batch()

        logger.info("=" * 80)
        logger.info("📊 VPNMongoDBPipeline (WITH TESTING) FINAL STATS")
        logger.info("=" * 80)
        logger.info(f"Items received: {self.stats['items_received']}")
        logger.info(f"Items tested: {self.stats['items_tested']}")
        logger.info(f"✅ Items PASSED test: {self.stats['items_passed']}")
        logger.info(f"❌ Items FAILED test: {self.stats['items_failed']}")
        logger.info(f"Items inserted: {self.stats['items_inserted']}")
        logger.info(f"Items updated: {self.stats['items_updated']}")
        logger.info(f"Items skipped: {self.stats['items_skipped']}")
        logger.info(f"Errors: {self.stats['errors']}")

        if self.stats['items_tested'] > 0:
            success_rate = (self.stats['items_passed'] / self.stats['items_tested']) * 100
            logger.info(f"📈 VPN Success Rate: {success_rate:.1f}%")

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
        self.stats['items_received'] += 1

        try:
            # Convert item to dict
            vpn_data = dict(item)

            if self.test_vpn:
                # Add to test queue
                self.test_queue.put(vpn_data)

                # Process batch when full
                if self.test_queue.qsize() >= self.batch_size:
                    self._process_test_batch()
            else:
                # Store without testing
                self._store_vpn(vpn_data, tested=False)

            return item

        except Exception as e:
            logger.error(f"❌ Error processing VPN item: {e}")
            self.stats['errors'] += 1
            return item

    def _process_test_batch(self):
        """Process a batch of VPNs for testing"""
        with self.batch_lock:
            # Collect batch
            batch = []
            while not self.test_queue.empty() and len(batch) < self.batch_size:
                batch.append(self.test_queue.get())

            if not batch:
                return

            logger.info(f"🧪 Testing batch of {len(batch)} VPNs...")

            # Test VPNs in parallel
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {executor.submit(self._test_vpn, vpn): vpn for vpn in batch}

                for future in as_completed(futures):
                    vpn_data = futures[future]
                    try:
                        is_working, response_time = future.result()

                        self.stats['items_tested'] += 1

                        if is_working:
                            self.stats['items_passed'] += 1
                            vpn_data['response_time'] = response_time
                            vpn_data['is_active'] = True
                            vpn_data['last_success'] = datetime.utcnow().isoformat()
                            vpn_data['last_checked'] = datetime.utcnow().isoformat()
                            self._store_vpn(vpn_data, tested=True)
                        else:
                            self.stats['items_failed'] += 1
                            # Ne pas stocker les VPN qui échouent au test
                            logger.debug(f"❌ VPN failed test: {vpn_data.get('proxy_url', 'unknown')}")

                    except Exception as e:
                        logger.error(f"❌ Error testing VPN: {e}")
                        self.stats['errors'] += 1

            # Log progress
            if self.stats['items_tested'] > 0:
                success_rate = (self.stats['items_passed'] / self.stats['items_tested']) * 100
                logger.info(f"📊 Progress: {self.stats['items_tested']} tested, {self.stats['items_passed']} passed ({success_rate:.1f}%)")

    def _test_vpn(self, vpn_data):
        """
        Test a single VPN to see if it works.

        Returns:
            (is_working, response_time) tuple
        """
        proxy_url = vpn_data.get('proxy_url')
        if not proxy_url:
            return False, None

        # Try with each test URL
        for test_url in self.TEST_URLS:
            try:
                start_time = time.time()

                # Make request through proxy
                response = requests.get(
                    test_url,
                    proxies={'http': proxy_url, 'https': proxy_url},
                    timeout=self.test_timeout,
                    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                )

                response_time = (time.time() - start_time) * 1000  # Convert to ms

                # Check if response is valid
                if response.status_code == 200:
                    logger.debug(f"✅ VPN working: {proxy_url} ({response_time:.0f}ms)")
                    return True, response_time

            except requests.exceptions.ProxyError:
                # Proxy connection failed
                continue
            except requests.exceptions.Timeout:
                # Timeout
                continue
            except requests.exceptions.RequestException:
                # Other request errors
                continue
            except Exception as e:
                # Unexpected error
                logger.debug(f"⚠️ Unexpected error testing {proxy_url}: {e}")
                continue

        # All test URLs failed
        return False, None

    def _store_vpn(self, vpn_data, tested=False):
        """Store VPN in MongoDB"""
        try:
            # Create unique key (IP + Port)
            unique_key = f"{vpn_data['ip']}:{vpn_data['port']}"

            # Check if proxy already exists
            existing = self.collection.find_one({'_unique_key': unique_key})

            if existing:
                # Update existing proxy
                self._update_existing_proxy(unique_key, vpn_data, existing, tested)
                self.stats['items_updated'] += 1
            else:
                # Insert new proxy
                self._insert_new_proxy(unique_key, vpn_data, tested)
                self.stats['items_inserted'] += 1

        except Exception as e:
            logger.error(f"❌ Error storing VPN: {e}")
            self.stats['errors'] += 1

    def _insert_new_proxy(self, unique_key, vpn_data, tested=False):
        """Insert a new proxy into MongoDB"""
        try:
            # Add metadata
            vpn_data['_unique_key'] = unique_key
            vpn_data['created_at'] = datetime.utcnow().isoformat()
            vpn_data['updated_at'] = datetime.utcnow().isoformat()
            vpn_data['tested'] = tested

            # Initialize performance tracking if not set
            if 'success_count' not in vpn_data:
                vpn_data['success_count'] = 1 if tested and vpn_data.get('is_active') else 0
            if 'fail_count' not in vpn_data:
                vpn_data['fail_count'] = 0

            # Insert
            self.collection.insert_one(vpn_data)

            if tested:
                logger.debug(f"✅ Inserted TESTED VPN: {vpn_data['proxy_url']} ({vpn_data.get('response_time', 'N/A')}ms)")
            else:
                logger.debug(f"➕ Inserted untested VPN: {vpn_data['proxy_url']}")

        except DuplicateKeyError:
            # Race condition: another process inserted it
            self.stats['items_skipped'] += 1

        except Exception as e:
            logger.error(f"❌ Error inserting VPN: {e}")
            self.stats['errors'] += 1

    def _update_existing_proxy(self, unique_key, new_data, existing_data, tested=False):
        """Update existing proxy with new information"""
        try:
            update_fields = {
                'updated_at': datetime.utcnow().isoformat(),
                'source': new_data.get('source', existing_data.get('source')),
                'scraped_at': new_data.get('scraped_at'),
            }

            if tested:
                # Update with test results
                update_fields['last_checked'] = new_data.get('last_checked')
                update_fields['last_success'] = new_data.get('last_success')
                update_fields['response_time'] = new_data.get('response_time')
                update_fields['is_active'] = True
                update_fields['tested'] = True

                # Increment success count
                self.collection.update_one(
                    {'_unique_key': unique_key},
                    {
                        '$set': update_fields,
                        '$inc': {'success_count': 1}
                    }
                )
            else:
                # Re-activate if was inactive (proxy seen again in new scrape)
                if not existing_data.get('is_active', True):
                    update_fields['is_active'] = True

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

            # Index on tested flag
            self.collection.create_index(
                [('tested', ASCENDING)],
                name='tested_flag'
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

            # Compound index for active + tested + reliable proxies
            self.collection.create_index(
                [
                    ('is_active', ASCENDING),
                    ('tested', ASCENDING),
                    ('success_count', DESCENDING),
                    ('fail_count', ASCENDING)
                ],
                name='active_tested_reliable'
            )

            logger.info("✅ MongoDB indexes created successfully")

        except Exception as e:
            logger.warning(f"⚠️ Error creating indexes (may already exist): {e}")
