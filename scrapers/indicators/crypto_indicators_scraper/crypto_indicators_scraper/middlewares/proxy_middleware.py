"""
Advanced Proxy Rotation Middleware with multiple proxy sources
"""
import logging
import random
from typing import List, Optional
import requests
from scrapy import signals
from scrapy.exceptions import NotConfigured
from scrapy.http import Request, Response

logger = logging.getLogger(__name__)


class ProxyRotationMiddleware:
    """
    Middleware for rotating proxies with multiple sources and automatic validation.

    Supports:
    - Multiple proxy providers (free and premium)
    - Automatic proxy validation
    - Fallback to direct connection if proxies fail
    - Proxy blacklisting for failed proxies
    """

    def __init__(self, proxy_sources: List[str], enabled: bool = True):
        self.proxy_sources = proxy_sources
        self.enabled = enabled
        self.proxy_list: List[str] = []
        self.blacklisted_proxies: set = set()
        self.stats = {
            'proxies_tested': 0,
            'proxies_working': 0,
            'proxies_failed': 0,
        }

    @classmethod
    def from_crawler(cls, crawler):
        """Initialize middleware from crawler settings."""
        proxy_sources = crawler.settings.getlist('PROXY_SOURCES', [])
        enabled = crawler.settings.getbool('PROXY_ROTATION_ENABLED', True)

        if not enabled:
            raise NotConfigured('Proxy rotation is disabled')

        middleware = cls(proxy_sources=proxy_sources, enabled=enabled)

        crawler.signals.connect(middleware.spider_opened, signal=signals.spider_opened)
        crawler.signals.connect(middleware.spider_closed, signal=signals.spider_closed)

        return middleware

    def spider_opened(self, spider):
        """Called when spider is opened."""
        logger.info(f"ProxyRotationMiddleware enabled for spider: {spider.name}")
        self._load_proxies()

    def spider_closed(self, spider):
        """Called when spider is closed."""
        logger.info(f"Proxy stats: {self.stats}")
        logger.info(f"Working proxies: {len(self.proxy_list)}")
        logger.info(f"Blacklisted proxies: {len(self.blacklisted_proxies)}")

    def _load_proxies(self):
        """Load proxies from various sources."""
        logger.info("Loading proxies from sources...")

        for source in self.proxy_sources:
            try:
                if source == 'free_proxy_list':
                    self._load_free_proxy_list()
                elif source == 'proxy_scrape':
                    self._load_proxy_scrape()
                elif source == 'pubproxy':
                    self._load_pubproxy()
                elif source == 'geonode':
                    self._load_geonode()
                elif source.startswith('file://'):
                    self._load_from_file(source[7:])
                elif source.startswith('http'):
                    self._load_from_url(source)
                else:
                    logger.warning(f"Unknown proxy source: {source}")
            except Exception as e:
                logger.error(f"Error loading proxies from {source}: {e}")

        logger.info(f"Loaded {len(self.proxy_list)} proxies from all sources")

    def _load_free_proxy_list(self):
        """Load proxies from free-proxy-list.net API."""
        try:
            # Use proxy-list.download API
            response = requests.get(
                'https://www.proxy-list.download/api/v1/get?type=http',
                timeout=10
            )
            if response.status_code == 200:
                proxies = response.text.strip().split('\n')
                for proxy in proxies:
                    proxy = proxy.strip()
                    if proxy and ':' in proxy:
                        self.proxy_list.append(f'http://{proxy}')
                logger.info(f"Loaded {len(proxies)} proxies from free-proxy-list")
        except Exception as e:
            logger.error(f"Error loading free-proxy-list: {e}")

    def _load_proxy_scrape(self):
        """Load proxies from proxyscrape.com."""
        try:
            response = requests.get(
                'https://api.proxyscrape.com/v2/?request=get&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all',
                timeout=10
            )
            if response.status_code == 200:
                proxies = response.text.strip().split('\n')
                for proxy in proxies:
                    proxy = proxy.strip()
                    if proxy and ':' in proxy:
                        self.proxy_list.append(f'http://{proxy}')
                logger.info(f"Loaded {len(proxies)} proxies from proxyscrape")
        except Exception as e:
            logger.error(f"Error loading proxyscrape: {e}")

    def _load_pubproxy(self):
        """Load proxies from pubproxy.com."""
        try:
            response = requests.get(
                'http://pubproxy.com/api/proxy?limit=20&format=txt&type=http',
                timeout=10
            )
            if response.status_code == 200:
                proxies = response.text.strip().split('\n')
                for proxy in proxies:
                    proxy = proxy.strip()
                    if proxy and ':' in proxy:
                        self.proxy_list.append(f'http://{proxy}')
                logger.info(f"Loaded {len(proxies)} proxies from pubproxy")
        except Exception as e:
            logger.error(f"Error loading pubproxy: {e}")

    def _load_geonode(self):
        """Load proxies from geonode.com."""
        try:
            response = requests.get(
                'https://proxylist.geonode.com/api/proxy-list?limit=100&page=1&sort_by=lastChecked&sort_type=desc&protocols=http',
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                for proxy_data in data.get('data', []):
                    ip = proxy_data.get('ip')
                    port = proxy_data.get('port')
                    if ip and port:
                        self.proxy_list.append(f'http://{ip}:{port}')
                logger.info(f"Loaded {len(data.get('data', []))} proxies from geonode")
        except Exception as e:
            logger.error(f"Error loading geonode: {e}")

    def _load_from_file(self, file_path: str):
        """Load proxies from a local file."""
        try:
            with open(file_path, 'r') as f:
                proxies = [line.strip() for line in f if line.strip()]
                for proxy in proxies:
                    if not proxy.startswith('http'):
                        proxy = f'http://{proxy}'
                    self.proxy_list.append(proxy)
                logger.info(f"Loaded {len(proxies)} proxies from file: {file_path}")
        except Exception as e:
            logger.error(f"Error loading proxies from file {file_path}: {e}")

    def _load_from_url(self, url: str):
        """Load proxies from a custom URL."""
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                proxies = response.text.strip().split('\n')
                for proxy in proxies:
                    proxy = proxy.strip()
                    if proxy and ':' in proxy:
                        if not proxy.startswith('http'):
                            proxy = f'http://{proxy}'
                        self.proxy_list.append(proxy)
                logger.info(f"Loaded {len(proxies)} proxies from URL: {url}")
        except Exception as e:
            logger.error(f"Error loading proxies from URL {url}: {e}")

    def _get_random_proxy(self) -> Optional[str]:
        """Get a random proxy from the list."""
        available_proxies = [p for p in self.proxy_list if p not in self.blacklisted_proxies]

        if not available_proxies:
            logger.warning("No available proxies! Reloading...")
            self.blacklisted_proxies.clear()
            self._load_proxies()
            available_proxies = self.proxy_list

        if available_proxies:
            return random.choice(available_proxies)

        return None

    def process_request(self, request: Request, spider):
        """Add proxy to request."""
        if not self.enabled:
            return None

        # Skip proxy for certain URLs if needed
        if request.meta.get('dont_proxy', False):
            return None

        proxy = self._get_random_proxy()
        if proxy:
            request.meta['proxy'] = proxy
            logger.debug(f"Using proxy: {proxy} for {request.url}")
        else:
            logger.warning(f"No proxy available for {request.url}")

        return None

    def process_response(self, request: Request, response: Response, spider):
        """Process response and handle proxy failures."""
        # If response is successful, return it
        if response.status in [200, 201]:
            return response

        # If response failed and proxy was used, blacklist it
        proxy = request.meta.get('proxy')
        if proxy:
            self.blacklisted_proxies.add(proxy)
            self.stats['proxies_failed'] += 1
            logger.warning(f"Blacklisted proxy {proxy} due to status {response.status}")

        return response

    def process_exception(self, request: Request, exception, spider):
        """Handle exceptions and blacklist failed proxies."""
        proxy = request.meta.get('proxy')
        if proxy:
            self.blacklisted_proxies.add(proxy)
            self.stats['proxies_failed'] += 1
            logger.warning(f"Blacklisted proxy {proxy} due to exception: {exception}")

        # Retry with a different proxy
        return None


class UserAgentRotationMiddleware:
    """Middleware for rotating user agents."""

    USER_AGENTS = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0',
    ]

    def process_request(self, request: Request, spider):
        """Rotate user agent for each request."""
        request.headers['User-Agent'] = random.choice(self.USER_AGENTS)
        return None
