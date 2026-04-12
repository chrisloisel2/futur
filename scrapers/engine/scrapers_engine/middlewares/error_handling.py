"""
Error handling and retry logic
"""

import logging
from scrapy.exceptions import IgnoreRequest


class ErrorHandlingMiddleware:
    """Handle errors gracefully"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.error_counts = {}

    @classmethod
    def from_crawler(cls, crawler):
        return cls()

    def process_spider_exception(self, response, exception, spider):
        """Process exceptions from spiders"""
        url = response.url
        domain = response.meta.get('domain', 'unknown')

        # Track error counts per domain
        self.error_counts[domain] = self.error_counts.get(domain, 0) + 1

        self.logger.warning(
            f"Error processing {url}: {exception.__class__.__name__}: {exception}"
        )

        # If too many errors from this domain, we could pause
        if self.error_counts[domain] > 10:
            self.logger.error(f"Too many errors from {domain}, consider pausing")

        return []

    def process_spider_output(self, response, result, spider):
        """Process spider output"""
        for item in result:
            yield item
