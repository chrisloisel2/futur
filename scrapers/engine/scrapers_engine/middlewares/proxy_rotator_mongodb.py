"""
MongoDB-based Proxy Rotator Middleware
Uses proxies stored in MongoDB (populated by free_vpn_scraper spider)
"""

import random
import logging
import os
from typing import List, Optional
import time
from scrapy import signals
from scrapy.exceptions import NotConfigured
from scrapy.http import Request
from scrapy.downloadermiddlewares.retry import get_retry_request

# Import VPN Manager
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.vpn_manager import VPNManager

logger = logging.getLogger(__name__)


class MongoDBProxyRotatorMiddleware:
    """
    Middleware qui utilise les proxies stockés dans MongoDB.

    Avantages:
    - Proxies partagés entre tous les spiders
    - Tracking de performance centralisé
    - Proxies automatiquement filtrés (actifs/inactifs)
    - Pas besoin de fetch à chaque run
    """

    def __init__(
        self,
        proxy_enabled=True,
        mongo_uri=None,
        mongo_db='scrapers_db',
        collection_name='vpn_proxies',
        max_proxies=200,
        refresh_interval=300,
        delete_on_failure=True
    ):
        self.proxy_enabled = proxy_enabled
        self.mongo_uri = mongo_uri or os.getenv("FUTUR_MONGO_URI", os.getenv("MONGODB_URI", "mongodb://localhost:27017"))
        self.mongo_db = mongo_db
        self.collection_name = collection_name
        self.max_proxies = max_proxies
        self.refresh_interval = refresh_interval
        self.delete_on_failure = delete_on_failure

        # VPN Manager
        self.vpn_manager = None

        # Proxy cache
        self.proxies: List[str] = []
        self.last_refresh = 0

        # Stats
        self.request_count = 0
        self.proxy_rotation_count = 0
        self.success_count = 0
        self.fail_count = 0
        self.deleted_count = 0

    @classmethod
    def from_crawler(cls, crawler):
        """Initialize from crawler settings"""
        proxy_enabled = crawler.settings.getbool('PROXY_ENABLED', True)

        if not proxy_enabled:
            raise NotConfigured('Proxy rotation is disabled')

        # MongoDB settings
        local_mongo_uri = os.getenv("FUTUR_MONGO_URI", os.getenv("MONGODB_URI", "mongodb://localhost:27017"))
        mongo_uri = crawler.settings.get('MONGODB_URI', local_mongo_uri)
        mongo_db = crawler.settings.get('MONGODB_DATABASE', 'scrapers_db')
        collection_name = crawler.settings.get('MONGODB_VPN_COLLECTION', 'vpn_proxies')

        # Proxy settings
        max_proxies = crawler.settings.getint('MAX_PROXIES', 200)
        refresh_interval = crawler.settings.getint('PROXY_REFRESH_INTERVAL', 300)
        delete_on_failure = crawler.settings.getbool('VPN_DELETE_ON_FAILURE', True)

        middleware = cls(
            proxy_enabled=proxy_enabled,
            mongo_uri=mongo_uri,
            mongo_db=mongo_db,
            collection_name=collection_name,
            max_proxies=max_proxies,
            refresh_interval=refresh_interval,
            delete_on_failure=delete_on_failure
        )

        # Connect signals
        crawler.signals.connect(middleware.spider_opened, signal=signals.spider_opened)
        crawler.signals.connect(middleware.spider_closed, signal=signals.spider_closed)

        return middleware

    def spider_opened(self, spider):
        """Called when spider opens"""
        logger.info('🔄 Initialisation du système de rotation de proxies MongoDB')

        if self.delete_on_failure:
            logger.info('🗑️ Mode AUTO-DELETE activé: les VPN défaillants seront supprimés immédiatement')
        else:
            logger.info('⚠️ Mode AUTO-DELETE désactivé: les VPN défaillants seront marqués inactifs')

        try:
            # Initialize VPN Manager
            self.vpn_manager = VPNManager(
                mongo_uri=self.mongo_uri,
                mongo_db=self.mongo_db,
                collection_name=self.collection_name
            )

            # Load proxies
            self._load_proxies()

            if self.proxies:
                logger.info(f'✅ {len(self.proxies)} proxies chargés depuis MongoDB')

                # Show stats
                stats = self.vpn_manager.get_stats()
                logger.info(f'📊 VPN Pool Stats:')
                logger.info(f'   - Total proxies in DB: {stats.get("total_proxies", 0)}')
                logger.info(f'   - Active proxies: {stats.get("active_proxies", 0)}')
                logger.info(f'   - Reliable proxies: {stats.get("reliable_proxies", 0)}')
                logger.info(f'   - Avg success rate: {stats.get("avg_success_rate", 0):.1%}')
            else:
                logger.warning('⚠️ Aucun proxy trouvé dans MongoDB!')
                logger.warning('   Lancez le spider free_vpn_scraper pour collecter des proxies:')
                logger.warning('   scrapy crawl free_vpn_scraper')

        except Exception as e:
            logger.error(f'❌ Erreur initialisation VPNManager: {e}')
            logger.warning('   Proxy rotation désactivée pour ce run')

    def spider_closed(self, spider):
        """Called when spider closes"""
        logger.info('📊 Statistiques finales du proxy rotator:')
        logger.info(f'   - Total requêtes: {self.request_count}')
        logger.info(f'   - Rotations proxy: {self.proxy_rotation_count}')
        logger.info(f'   - Succès: {self.success_count}')
        logger.info(f'   - Échecs: {self.fail_count}')

        if self.delete_on_failure:
            logger.info(f'   - VPN supprimés: {self.deleted_count}')

        if self.request_count > 0:
            success_rate = (self.success_count / self.request_count) * 100
            logger.info(f'   - Taux de succès: {success_rate:.1f}%')

        # Disconnect VPN Manager
        if self.vpn_manager:
            self.vpn_manager.disconnect()

    def process_request(self, request: Request, spider):
        """Process each request - assign a proxy"""
        if not self.proxy_enabled or not self.vpn_manager:
            return None

        # Check if we need to refresh proxies
        current_time = time.time()
        if current_time - self.last_refresh > self.refresh_interval:
            self._load_proxies()

        # Get a random proxy
        proxy = self._get_random_proxy()

        if proxy:
            # Ensure proper format
            if not proxy.startswith('http'):
                proxy = f'http://{proxy}'

            request.meta['proxy'] = proxy
            request.meta['proxy_url'] = proxy  # Store for tracking

            self.request_count += 1
            self.proxy_rotation_count += 1

            logger.debug(f'🔄 Proxy #{self.proxy_rotation_count}: {proxy}')

        return None

    def process_response(self, request, response, spider):
        """Process response - record success"""
        if 'proxy_url' in request.meta and self.vpn_manager:
            proxy_url = request.meta['proxy_url']

            # Record success
            response_time = request.meta.get('download_latency', 0) * 1000  # Convert to ms
            self.vpn_manager.record_success(proxy_url, response_time)

            self.success_count += 1

            logger.debug(f'✅ Proxy success: {proxy_url} ({response_time:.0f}ms)')

        return response

    def process_exception(self, request, exception, spider):
        """Process exception - record failure and retry with new proxy"""
        if 'proxy_url' in request.meta and self.vpn_manager:
            proxy_url = request.meta['proxy_url']

            # Record failure (and delete from DB if configured)
            self.vpn_manager.record_failure(proxy_url, delete_immediately=self.delete_on_failure)

            self.fail_count += 1
            if self.delete_on_failure:
                self.deleted_count += 1

            logger.debug(f'❌ Proxy failed: {proxy_url} - {exception}')

            # Remove from local cache immediately
            if proxy_url in self.proxies:
                self.proxies.remove(proxy_url)
                logger.debug(f'🗑️ Removed from local cache: {proxy_url}')

            # Retry with a new proxy
            retry_req = get_retry_request(
                request,
                spider=spider,
                reason=f'proxy_error: {exception}'
            )

            if retry_req:
                # Remove failed proxy from meta
                retry_req.meta.pop('proxy_url', None)
                return retry_req

        return None

    def _load_proxies(self):
        """Load proxies from MongoDB"""
        try:
            self.proxies = self.vpn_manager.get_active_proxies(
                limit=self.max_proxies,
                use_cache=False
            )
            self.last_refresh = time.time()

            logger.debug(f'🔄 Rechargé {len(self.proxies)} proxies depuis MongoDB')

        except Exception as e:
            logger.error(f'❌ Erreur chargement proxies: {e}')

    def _get_random_proxy(self) -> Optional[str]:
        """Get a random proxy from the cache"""
        if not self.proxies:
            return None

        return random.choice(self.proxies)


class UserAgentRotatorMiddleware:
    """
    Middleware qui rotate les User-Agents pour éviter la détection.
    """

    USER_AGENTS = [
        # Chrome Windows
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',

        # Chrome Mac
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',

        # Firefox Windows
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0',

        # Firefox Mac
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:120.0) Gecko/20100101 Firefox/120.0',

        # Safari Mac
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',

        # Edge Windows
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',

        # Chrome Linux
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',

        # Mobile Chrome
        'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.144 Mobile Safari/537.36',

        # Mobile Safari
        'Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',

        # Opera
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 OPR/106.0.0.0',
    ]

    def process_request(self, request: Request, spider):
        """Assigne un User-Agent aléatoire"""
        request.headers['User-Agent'] = random.choice(self.USER_AGENTS)
        return None


class HeadersRotatorMiddleware:
    """
    Middleware qui randomise les headers HTTP pour éviter la détection.
    """

    ACCEPT_LANGUAGES = [
        'en-US,en;q=0.9',
        'en-GB,en;q=0.9',
        'en-US,en;q=0.9,fr;q=0.8',
        'en-US,en;q=0.9,es;q=0.8',
        'en-US,en;q=0.9,de;q=0.8',
        'zh-CN,zh;q=0.9,en;q=0.8',
        'ja-JP,ja;q=0.9,en;q=0.8',
        'ko-KR,ko;q=0.9,en;q=0.8',
        'fr-FR,fr;q=0.9,en;q=0.8',
    ]

    REFERERS = [
        'https://www.google.com/',
        'https://www.bing.com/',
        'https://www.yahoo.com/',
        'https://www.duckduckgo.com/',
        'https://www.reddit.com/',
        'https://www.twitter.com/',
        'https://www.facebook.com/',
    ]

    def process_request(self, request: Request, spider):
        """Randomise les headers"""
        # Accept-Language
        request.headers['Accept-Language'] = random.choice(self.ACCEPT_LANGUAGES)

        # Referer (seulement 50% du temps pour paraître naturel)
        if random.random() > 0.5:
            request.headers['Referer'] = random.choice(self.REFERERS)

        # Accept
        request.headers['Accept'] = 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8'

        # Accept-Encoding
        request.headers['Accept-Encoding'] = 'gzip, deflate, br'

        # Connection
        request.headers['Connection'] = 'keep-alive'

        # DNT (Do Not Track) - randomisé
        if random.random() > 0.5:
            request.headers['DNT'] = '1'

        # Sec-Fetch-* headers (modern browsers)
        request.headers['Sec-Fetch-Site'] = random.choice(['none', 'same-origin', 'cross-site'])
        request.headers['Sec-Fetch-Mode'] = 'navigate'
        request.headers['Sec-Fetch-Dest'] = 'document'
        request.headers['Sec-Fetch-User'] = '?1'

        # Upgrade-Insecure-Requests
        request.headers['Upgrade-Insecure-Requests'] = '1'

        return None
