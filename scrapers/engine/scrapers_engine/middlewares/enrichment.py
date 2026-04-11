"""
Metadata enrichment middleware
"""

import logging
from datetime import datetime


class MetadataEnrichmentMiddleware:
    """Add metadata to responses before processing"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    @classmethod
    def from_crawler(cls, crawler):
        return cls()

    def process_spider_input(self, response, spider):
        """Enrich response with metadata"""
        # Add scraping metadata
        response.meta['scraped_at'] = datetime.utcnow().isoformat()
        response.meta['spider_name'] = spider.name

        # Add timing info
        if 'download_latency' in response.flags:
            response.meta['download_latency_ms'] = response.flags.get('download_latency', 0) * 1000

        return None

    def process_spider_exception(self, response, exception, spider):
        """Log exceptions"""
        self.logger.error(f"Spider exception on {response.url}: {exception}")
        return None
