"""
Deduplication pipeline using content hashing
"""

import hashlib
import logging
from scrapy.exceptions import DropItem


class DeduplicationPipeline:
    """Remove duplicate items based on content hash"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.seen_hashes = set()
        self.stats = {
            'unique': 0,
            'duplicates': 0
        }

    def process_item(self, item, spider):
        """Check if item is duplicate"""
        # Generate content hash
        content_hash = self._generate_hash(item)

        if content_hash in self.seen_hashes:
            self.stats['duplicates'] += 1
            self.logger.debug(f"Duplicate item from {item.get('source')}: {item.get('title', '')[:50]}")
            raise DropItem(f"Duplicate item: {content_hash}")

        self.seen_hashes.add(content_hash)
        self.stats['unique'] += 1
        item['content_hash'] = content_hash

        return item

    def _generate_hash(self, item):
        """Generate hash from item content"""
        # Use URL as primary identifier
        if item.get('url'):
            return hashlib.md5(item['url'].encode('utf-8')).hexdigest()

        # Fallback to title + source
        content = f"{item.get('title', '')}{item.get('source', '')}"
        return hashlib.md5(content.encode('utf-8')).hexdigest()

    def close_spider(self, spider):
        """Log statistics"""
        total = self.stats['unique'] + self.stats['duplicates']
        duplicate_rate = (self.stats['duplicates'] / total * 100) if total > 0 else 0
        self.logger.info(
            f"Deduplication: {self.stats['unique']} unique, "
            f"{self.stats['duplicates']} duplicates ({duplicate_rate:.1f}%)"
        )
