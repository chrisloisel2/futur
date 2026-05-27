"""
Proxy rotation middleware (optional)
"""

import random
from scrapy import signals


class ProxyMiddleware:
    """
    Rotate proxies if configured
    Set PROXY_LIST in settings to enable
    """

    def __init__(self, proxy_list=None):
        self.proxy_list = proxy_list or []

    @classmethod
    def from_crawler(cls, crawler):
        proxy_list = crawler.settings.getlist('PROXY_LIST', [])
        s = cls(proxy_list)
        crawler.signals.connect(s.spider_opened, signal=signals.spider_opened)
        return s

    def process_request(self, request, spider):
        """Set random proxy if available"""
        if self.proxy_list:
            request.meta['proxy'] = random.choice(self.proxy_list)

    def spider_opened(self, spider):
        if self.proxy_list:
            spider.logger.info(f'ProxyMiddleware enabled with {len(self.proxy_list)} proxies')
        else:
            spider.logger.info('ProxyMiddleware enabled but no proxies configured')
