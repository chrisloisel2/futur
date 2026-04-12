"""
Middlewares Scrapy pour marketintel.

  MongoProxyMiddleware      — rotation de proxies depuis proxy_db MongoDB
  RandomUserAgentMiddleware — pool large de User-Agents réalistes
  HeadersRotatorMiddleware  — headers HTTP variés (Accept-Language, Referer, Sec-Fetch-*)
"""
import logging
import random
import time

from scrapy import signals
from scrapy.downloadermiddlewares.retry import get_retry_request
from scrapy.exceptions import NotConfigured

log = logging.getLogger(__name__)


# ── User Agents ───────────────────────────────────────────────────────────────

_USER_AGENTS = [
    # Chrome Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    # Chrome Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    # Chrome Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    # Firefox Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    # Firefox Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:124.0) Gecko/20100101 Firefox/124.0",
    # Firefox Linux
    "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
    # Safari Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    # Edge
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    # Mobile Chrome
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.6312.86 Mobile Safari/537.36",
    # Mobile Safari
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
]

_ACCEPT_LANGUAGES = [
    "en-US,en;q=0.9",
    "en-GB,en;q=0.9",
    "en-US,en;q=0.9,fr;q=0.8",
    "en-US,en;q=0.9,de;q=0.8",
    "en-US,en;q=0.9,es;q=0.8",
    "fr-FR,fr;q=0.9,en;q=0.8",
    "de-DE,de;q=0.9,en;q=0.8",
    "ja-JP,ja;q=0.9,en;q=0.8",
    "zh-CN,zh;q=0.9,en;q=0.8",
]

_REFERERS = [
    "https://www.google.com/",
    "https://www.bing.com/",
    "https://www.duckduckgo.com/",
    "https://www.reddit.com/",
    "https://t.co/",
]


# ─────────────────────────────────────────────────────────────────────────────
# Proxy middleware
# ─────────────────────────────────────────────────────────────────────────────

class MongoProxyMiddleware:
    """
    Rotation de proxies depuis proxy_db MongoDB.
    Utilise api_collectors.proxy.ProxyPool (singleton, thread-safe).

    Settings Scrapy attendus (tous optionnels, héritent de api_collectors/config.py) :
      PROXY_ENABLED            (bool, défaut True)
      PROXY_MONGO_URI          mongodb://admin:admin123@100.93.248.105/
      PROXY_MONGO_DB           proxy_db
      PROXY_COLLECTION         proxies
      PROXY_REFRESH_INTERVAL   300
    """

    def __init__(self, enabled: bool):
        if not enabled:
            raise NotConfigured("MongoProxyMiddleware désactivé (PROXY_ENABLED=False)")
        self._pool = None
        self._req_count = 0
        self._fail_count = 0

    @classmethod
    def from_crawler(cls, crawler):
        enabled = crawler.settings.getbool("PROXY_ENABLED", True)
        inst = cls(enabled)
        crawler.signals.connect(inst._on_open,  signal=signals.spider_opened)
        crawler.signals.connect(inst._on_close, signal=signals.spider_closed)
        return inst

    # ── Signals ───────────────────────────────────────────────────────────────

    def _on_open(self, spider):
        try:
            # Surcharge éventuelle des settings Scrapy dans les variables d'env
            # utilisées par ProxyPool.get_instance()
            import scrapy
            settings = spider.crawler.settings
            for env_key, setting_key in [
                ("PROXY_MONGO_URI",        "PROXY_MONGO_URI"),
                ("PROXY_MONGO_DB",         "PROXY_MONGO_DB"),
                ("PROXY_COLLECTION",       "PROXY_COLLECTION"),
                ("PROXY_REFRESH_INTERVAL", "PROXY_REFRESH_INTERVAL"),
            ]:
                val = settings.get(setting_key)
                if val:
                    import os; os.environ.setdefault(env_key, str(val))

            from api_collectors.proxy import ProxyPool
            self._pool = ProxyPool.get_instance()
            log.info("MongoProxyMiddleware : %d proxies disponibles", self._pool.available)
        except Exception:
            log.exception("MongoProxyMiddleware : échec initialisation ProxyPool")

    def _on_close(self, spider):
        log.info(
            "MongoProxyMiddleware : %d requêtes — %d échecs proxy — %d disponibles",
            self._req_count, self._fail_count,
            self._pool.available if self._pool else 0,
        )

    # ── Hooks requête ─────────────────────────────────────────────────────────

    def process_request(self, request, spider):
        if not self._pool or "proxy" in request.meta:
            return None

        proxy_url = self._pool.get()
        if proxy_url:
            request.meta["proxy"] = proxy_url
            request.meta["_proxy_url"] = proxy_url
            self._req_count += 1
        return None

    def process_response(self, request, response, spider):
        # Pas de retrait sur succès : on garde le proxy dans le pool
        return response

    def process_exception(self, request, exception, spider):
        proxy_url = request.meta.get("_proxy_url")
        if proxy_url and self._pool:
            self._pool.mark_failed(proxy_url)
            self._fail_count += 1
            log.debug("Proxy échoué : %s (%s)", proxy_url, type(exception).__name__)

            # Retry avec un nouveau proxy
            retry = get_retry_request(request, spider=spider, reason=f"proxy_error:{type(exception).__name__}")
            if retry:
                retry.meta.pop("_proxy_url", None)
                retry.meta.pop("proxy", None)
                return retry

        return None


# ─────────────────────────────────────────────────────────────────────────────
# User-Agent middleware
# ─────────────────────────────────────────────────────────────────────────────

class RandomUserAgentMiddleware:
    def process_request(self, request, spider):
        request.headers["User-Agent"] = random.choice(_USER_AGENTS)


# ─────────────────────────────────────────────────────────────────────────────
# Headers middleware
# ─────────────────────────────────────────────────────────────────────────────

class HeadersRotatorMiddleware:
    def process_request(self, request, spider):
        request.headers["Accept-Language"] = random.choice(_ACCEPT_LANGUAGES)
        request.headers["Accept-Encoding"] = "gzip, deflate, br"
        request.headers["Connection"] = "keep-alive"
        request.headers["Upgrade-Insecure-Requests"] = "1"

        if random.random() > 0.5:
            request.headers["Referer"] = random.choice(_REFERERS)

        # Sec-Fetch-* (navigateurs modernes)
        request.headers["Sec-Fetch-Mode"] = "navigate"
        request.headers["Sec-Fetch-Dest"] = "document"
        request.headers["Sec-Fetch-Site"] = random.choice(["none", "same-origin", "cross-site"])
        request.headers["Sec-Fetch-User"] = "?1"
