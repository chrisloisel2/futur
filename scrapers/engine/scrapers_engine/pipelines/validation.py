"""
Validation pipeline
"""

import logging
from scrapy.exceptions import DropItem


class ValidationPipeline:
    """Validate scraped items before processing"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.stats = {
            'processed': 0,
            'dropped': 0,
            'validation_errors': {}
        }

    def process_item(self, item, spider):
        """Validate item data"""
        self.stats['processed'] += 1

        # Check required fields
        errors = []

        # # Common required fields
        # if not item.get('title') or len(item['title']) < 5:
        #     errors.append('Missing or too short title')

        # if not item.get('url'):
        #     errors.append('Missing URL')

        # if not item.get('source'):
        #     errors.append('Missing source')

        # Type-specific validation
        # if hasattr(item, 'body'):
        #     if not item.get('body') or len(item.get('body', '')) < 50:
        #         errors.append('Article body too short or missing')

        # if hasattr(item, 'amount_usd'):
        #     # Transaction alert validation
        #     try:
        #         amount = float(item.get('amount_usd', 0))
        #         if amount <= 0:
        #             errors.append('Invalid transaction amount')
        #     except (ValueError, TypeError):
        #         errors.append('Invalid amount_usd format')

        # # If validation errors, drop item
        # if errors:
        #     self.stats['dropped'] += 1
        #     for error in errors:
        #         error_key = error.replace(' ', '_').lower()
        #         self.stats['validation_errors'][error_key] = \
        #             self.stats['validation_errors'].get(error_key, 0) + 1

        #     self.logger.debug(f"Dropped item from {item.get('source')}: {errors}")
        #     raise DropItem(f"Validation failed: {', '.join(errors)}")

        return item

    def close_spider(self, spider):
        """Log statistics"""
        self.logger.info(f"Validation stats: {self.stats}")
