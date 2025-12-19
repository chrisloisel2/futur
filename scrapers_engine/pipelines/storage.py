"""
Storage pipeline - save to database and integrate with news_signal_engine
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
import sys

# Add parent directory to path to import news_signal_engine
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

try:
    from news_signal_engine.models import RawNewsArticle, EventType, SourceTier
    NEWS_ENGINE_AVAILABLE = True
except ImportError:
    NEWS_ENGINE_AVAILABLE = False


class StoragePipeline:
    """Store scraped items"""

    def __init__(self, storage_path='data/raw_articles'):
        self.logger = logging.getLogger(__name__)
        self.storage_path = storage_path
        self.stats = {
            'saved': 0,
            'errors': 0
        }

        # Create storage directory
        os.makedirs(storage_path, exist_ok=True)

        # Output files
        self.jsonl_file = None
        self.news_articles = []

    @classmethod
    def from_crawler(cls, crawler):
        storage_path = crawler.settings.get('STORAGE_PATH', 'data/raw_articles')
        return cls(storage_path)

    def open_spider(self, spider):
        """Open storage files"""
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        jsonl_path = os.path.join(self.storage_path, f'{spider.name}_{timestamp}.jsonl')

        self.jsonl_file = open(jsonl_path, 'w', encoding='utf-8')
        self.logger.info(f"Saving items to {jsonl_path}")

    def close_spider(self, spider):
        """Close storage files"""
        if self.jsonl_file:
            self.jsonl_file.close()

        # Save to news_signal_engine if available
        if NEWS_ENGINE_AVAILABLE and self.news_articles:
            self._save_to_news_engine()

        self.logger.info(f"Storage stats: {self.stats}")

    def process_item(self, item, spider):
        """Save item to storage"""
        try:
            # Convert to dict
            item_dict = dict(item)

            # Save to JSONL
            self.jsonl_file.write(json.dumps(item_dict, default=str, ensure_ascii=False) + '\n')
            self.jsonl_file.flush()

            # If it's a news article, prepare for news_signal_engine
            if self._is_news_article(item):
                self.news_articles.append(self._convert_to_raw_news_article(item_dict))

            self.stats['saved'] += 1

        except Exception as e:
            self.stats['errors'] += 1
            self.logger.error(f"Error saving item: {e}")

        return item

    def _is_news_article(self, item) -> bool:
        """Check if item is a news article"""
        return 'body' in item and 'title' in item and 'published_at' in item

    def _convert_to_raw_news_article(self, item_dict: dict):
        """Convert scraped item to RawNewsArticle"""
        if not NEWS_ENGINE_AVAILABLE:
            return None

        try:
            # Parse timestamps
            published_at = self._parse_timestamp(item_dict.get('published_at'))
            scraped_at = self._parse_timestamp(item_dict.get('scraped_at'))

            return RawNewsArticle(
                article_id=item_dict.get('content_hash', item_dict.get('article_id', '')),
                title=item_dict.get('title', ''),
                body=item_dict.get('body', ''),
                lang=item_dict.get('language', 'en'),
                source=item_dict.get('source', 'Unknown'),
                source_url=item_dict.get('url'),
                timestamp_publication=published_at or datetime.utcnow(),
                timestamp_collecte=scraped_at or datetime.utcnow(),
                country=item_dict.get('country'),
                categories=item_dict.get('categories', []),
                links=[item_dict.get('url', '')],
                images=item_dict.get('images', []),
            )

        except Exception as e:
            self.logger.error(f"Error converting to RawNewsArticle: {e}")
            return None

    def _parse_timestamp(self, timestamp_str):
        """Parse timestamp string"""
        if not timestamp_str:
            return None

        if isinstance(timestamp_str, datetime):
            return timestamp_str

        try:
            return datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        except Exception:
            return None

    def _save_to_news_engine(self):
        """Save articles to news_signal_engine for processing"""
        save_path = os.path.join(self.storage_path, 'news_engine_export.json')

        try:
            export_data = []
            for article in self.news_articles:
                if article:
                    export_data.append({
                        'article_id': article.article_id,
                        'title': article.title,
                        'body': article.body,
                        'lang': article.lang,
                        'source': article.source,
                        'source_url': article.source_url,
                        'timestamp_publication': article.timestamp_publication.isoformat(),
                        'timestamp_collecte': article.timestamp_collecte.isoformat(),
                        'country': article.country,
                        'categories': article.categories,
                        'links': article.links,
                        'images': article.images,
                    })

            with open(save_path, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False)

            self.logger.info(f"Exported {len(export_data)} articles for news_signal_engine to {save_path}")

        except Exception as e:
            self.logger.error(f"Error exporting to news_signal_engine: {e}")
