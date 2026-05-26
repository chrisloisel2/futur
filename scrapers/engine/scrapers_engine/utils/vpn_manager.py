"""
VPN Manager Utility
Retrieves and manages VPN proxies from MongoDB for use in scrapers
"""

import logging
import os
from datetime import datetime, timedelta
from pymongo import MongoClient
import random
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


class VPNManager:
    """
    Manages VPN/Proxy retrieval from MongoDB.

    Features:
    - Fetch active proxies
    - Smart selection (prioritize reliable proxies)
    - Performance tracking
    - Automatic retry logic
    """

    def __init__(
        self,
        mongo_uri=None,
        mongo_db='scrapers_db',
        collection_name='vpn_proxies'
    ):
        self.mongo_uri = mongo_uri or os.getenv("FUTUR_MONGO_URI", os.getenv("MONGODB_URI", "mongodb://localhost:27017"))
        self.mongo_db = mongo_db
        self.collection_name = collection_name

        self.client = None
        self.db = None
        self.collection = None

        # Proxy cache
        self.proxy_cache = []
        self.cache_timestamp = None
        self.cache_duration = 300  # 5 minutes

    def connect(self):
        """Connect to MongoDB"""
        if not self.client:
            try:
                self.client = MongoClient(self.mongo_uri)
                self.db = self.client[self.mongo_db]
                self.collection = self.db[self.collection_name]
                logger.info(f"✅ VPNManager connected to MongoDB: {self.mongo_db}.{self.collection_name}")
            except Exception as e:
                logger.error(f"❌ Failed to connect to MongoDB: {e}")
                raise

    def disconnect(self):
        """Disconnect from MongoDB"""
        if self.client:
            self.client.close()
            self.client = None
            logger.info("🔌 VPNManager disconnected from MongoDB")

    def get_active_proxies(
        self,
        limit: int = 200,
        protocol: Optional[str] = None,
        min_success_count: int = 0,
        use_cache: bool = True
    ) -> List[str]:
        """
        Get list of active proxy URLs.

        Args:
            limit: Maximum number of proxies to return
            protocol: Filter by protocol (http, https, socks5, etc.)
            min_success_count: Minimum success count (for reliability)
            use_cache: Use cached proxies if available

        Returns:
            List of proxy URLs (e.g., ['http://1.2.3.4:8080', ...])
        """
        # Check cache
        if use_cache and self._is_cache_valid():
            logger.debug(f"📦 Using cached proxies ({len(self.proxy_cache)} proxies)")
            return random.sample(self.proxy_cache, min(limit, len(self.proxy_cache)))

        # Fetch from MongoDB
        self.connect()

        try:
            # Build query
            query = {'is_active': True}

            if protocol:
                query['protocol'] = protocol

            if min_success_count > 0:
                query['success_count'] = {'$gte': min_success_count}

            # Fetch proxies, sorted by reliability
            proxies = list(self.collection.find(
                query,
                {
                    'proxy_url': 1,
                    'ip': 1,
                    'port': 1,
                    'protocol': 1,
                    'success_count': 1,
                    'fail_count': 1,
                    'response_time': 1,
                    '_id': 0
                }
            ).sort([
                ('success_count', -1),  # Higher success count first
                ('fail_count', 1),       # Lower fail count first
                ('response_time', 1)     # Faster proxies first
            ]).limit(limit))

            # Extract URLs
            proxy_urls = [p['proxy_url'] for p in proxies if 'proxy_url' in p]

            # Update cache
            self.proxy_cache = proxy_urls
            self.cache_timestamp = datetime.utcnow()

            logger.info(f"✅ Fetched {len(proxy_urls)} active proxies from MongoDB")

            return proxy_urls

        except Exception as e:
            logger.error(f"❌ Error fetching proxies from MongoDB: {e}")
            return []

    def get_random_proxy(
        self,
        protocol: Optional[str] = None,
        exclude: Optional[List[str]] = None
    ) -> Optional[str]:
        """
        Get a single random proxy.

        Args:
            protocol: Filter by protocol
            exclude: List of proxy URLs to exclude

        Returns:
            Single proxy URL or None
        """
        proxies = self.get_active_proxies(limit=200, protocol=protocol)

        if exclude:
            proxies = [p for p in proxies if p not in exclude]

        if proxies:
            return random.choice(proxies)

        return None

    def record_success(self, proxy_url: str, response_time: Optional[float] = None):
        """
        Record a successful proxy use.

        Args:
            proxy_url: The proxy URL that succeeded
            response_time: Response time in milliseconds (optional)
        """
        self.connect()

        try:
            # Extract IP:PORT from URL
            unique_key = self._extract_unique_key(proxy_url)

            if not unique_key:
                logger.warning(f"⚠️ Invalid proxy URL format: {proxy_url}")
                return

            # Update MongoDB
            update_fields = {
                'last_success': datetime.utcnow().isoformat(),
                'last_checked': datetime.utcnow().isoformat(),
                'updated_at': datetime.utcnow().isoformat(),
                'is_active': True,  # Ensure it's active
            }

            if response_time is not None:
                update_fields['response_time'] = response_time

            self.collection.update_one(
                {'_unique_key': unique_key},
                {
                    '$set': update_fields,
                    '$inc': {'success_count': 1}
                }
            )

            logger.debug(f"✅ Recorded success for proxy: {proxy_url}")

            # Invalidate cache
            self.cache_timestamp = None

        except Exception as e:
            logger.error(f"❌ Error recording success: {e}")

    def record_failure(self, proxy_url: str, delete_immediately: bool = True):
        """
        Record a failed proxy use and optionally delete it from database.

        Args:
            proxy_url: The proxy URL that failed
            delete_immediately: If True, delete the VPN from database immediately (default: True)
        """
        self.connect()

        try:
            # Extract IP:PORT from URL
            unique_key = self._extract_unique_key(proxy_url)

            if not unique_key:
                logger.warning(f"⚠️ Invalid proxy URL format: {proxy_url}")
                return

            if delete_immediately:
                # SUPPRIMER IMMÉDIATEMENT le VPN de la base de données
                result = self.collection.delete_one({'_unique_key': unique_key})

                if result.deleted_count > 0:
                    logger.info(f"🗑️ DELETED failed VPN from database: {proxy_url}")
                else:
                    logger.warning(f"⚠️ VPN not found in database: {proxy_url}")

                # Remove from cache if present
                if proxy_url in self.proxy_cache:
                    self.proxy_cache.remove(proxy_url)
                    logger.debug(f"🗑️ Removed VPN from cache: {proxy_url}")
            else:
                # Old behavior: increment fail count and mark inactive
                result = self.collection.find_one_and_update(
                    {'_unique_key': unique_key},
                    {
                        '$set': {
                            'last_checked': datetime.utcnow().isoformat(),
                            'updated_at': datetime.utcnow().isoformat(),
                        },
                        '$inc': {'fail_count': 1}
                    },
                    return_document=True
                )

                if result:
                    fail_count = result.get('fail_count', 0)
                    success_count = result.get('success_count', 0)

                    # Mark as inactive if too many failures
                    if fail_count >= 10 or (fail_count > 0 and success_count / (success_count + fail_count) < 0.2):
                        self.collection.update_one(
                            {'_unique_key': unique_key},
                            {'$set': {'is_active': False}}
                        )
                        logger.warning(f"⚠️ Marked proxy as inactive (too many failures): {proxy_url}")

                logger.debug(f"❌ Recorded failure for proxy: {proxy_url}")

            # Invalidate cache
            self.cache_timestamp = None

        except Exception as e:
            logger.error(f"❌ Error recording failure: {e}")

    def get_stats(self) -> Dict:
        """
        Get VPN pool statistics.

        Returns:
            Dictionary with stats
        """
        self.connect()

        try:
            total_count = self.collection.count_documents({})
            active_count = self.collection.count_documents({'is_active': True})
            inactive_count = total_count - active_count

            # Get proxies with at least 1 success
            reliable_count = self.collection.count_documents({
                'is_active': True,
                'success_count': {'$gte': 1}
            })

            # Get average success rate
            pipeline = [
                {
                    '$match': {
                        'success_count': {'$gt': 0}
                    }
                },
                {
                    '$addFields': {
                        'total_attempts': {'$add': ['$success_count', '$fail_count']},
                        'success_rate': {
                            '$divide': [
                                '$success_count',
                                {'$add': ['$success_count', '$fail_count']}
                            ]
                        }
                    }
                },
                {
                    '$group': {
                        '_id': None,
                        'avg_success_rate': {'$avg': '$success_rate'},
                        'avg_response_time': {'$avg': '$response_time'}
                    }
                }
            ]

            agg_result = list(self.collection.aggregate(pipeline))
            avg_success_rate = agg_result[0]['avg_success_rate'] if agg_result else 0
            avg_response_time = agg_result[0]['avg_response_time'] if agg_result else None

            stats = {
                'total_proxies': total_count,
                'active_proxies': active_count,
                'inactive_proxies': inactive_count,
                'reliable_proxies': reliable_count,
                'avg_success_rate': avg_success_rate,
                'avg_response_time': avg_response_time,
            }

            return stats

        except Exception as e:
            logger.error(f"❌ Error getting stats: {e}")
            return {}

    def refresh_proxies(self) -> int:
        """
        Force refresh of proxy cache from MongoDB.

        Returns:
            Number of proxies loaded
        """
        self.cache_timestamp = None  # Invalidate cache
        proxies = self.get_active_proxies(use_cache=False)
        return len(proxies)

    def _is_cache_valid(self) -> bool:
        """Check if cache is still valid"""
        if not self.proxy_cache or not self.cache_timestamp:
            return False

        age = (datetime.utcnow() - self.cache_timestamp).total_seconds()
        return age < self.cache_duration

    def _extract_unique_key(self, proxy_url: str) -> Optional[str]:
        """Extract IP:PORT from proxy URL"""
        try:
            # Remove protocol
            if '://' in proxy_url:
                proxy_url = proxy_url.split('://', 1)[1]

            # Remove any path
            if '/' in proxy_url:
                proxy_url = proxy_url.split('/', 1)[0]

            # Now should be IP:PORT
            if ':' in proxy_url:
                return proxy_url
            else:
                return None

        except Exception as e:
            logger.error(f"❌ Error extracting unique key from {proxy_url}: {e}")
            return None

    def __enter__(self):
        """Context manager entry"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.disconnect()


# Singleton instance
_vpn_manager = None


def get_vpn_manager(
    mongo_uri=None,
    mongo_db='scrapers_db',
    collection_name='vpn_proxies'
) -> VPNManager:
    """
    Get singleton VPNManager instance.

    Returns:
        VPNManager instance
    """
    global _vpn_manager

    if _vpn_manager is None:
        _vpn_manager = VPNManager(
            mongo_uri=mongo_uri,
            mongo_db=mongo_db,
            collection_name=collection_name
        )

    return _vpn_manager
