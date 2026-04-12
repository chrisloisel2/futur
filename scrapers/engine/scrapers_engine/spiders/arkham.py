"""
Arkham Intelligence spider - on-chain intelligence
https://intel.arkm.com/
"""

import scrapy
from datetime import datetime
from items import TransactionAlertItem, NewsArticleItem
import json
import re


class ArkhamSpider(scrapy.Spider):
    name = 'arkham'
    allowed_domains = ['intel.arkm.com', 'arkm.com']
    start_urls = [
        'https://intel.arkm.com/',
    ]

    custom_settings = {
        'DOWNLOAD_DELAY': 2,
        'CONCURRENT_REQUESTS_PER_DOMAIN': 1,
    }

    def parse(self, response):
        """Parse Arkham Intelligence main page"""
        # Arkham may be a SPA (Single Page App), so we look for:
        # 1. News/alerts in the page
        # 2. API endpoints in JavaScript
        # 3. RSS/JSON feeds

        # Try to find news items
        news_items = response.css('div.alert-item, div.transaction-item, article.news')

        for item_sel in news_items:
            item = NewsArticleItem()

            # Extract title
            title = item_sel.css('h2::text, h3::text, .title::text').get()
            if not title:
                continue

            item['title'] = title.strip()
            item['source'] = 'Arkham Intelligence'
            item['source_tier'] = 'tier2'

            # Extract URL
            url = item_sel.css('a::attr(href)').get()
            item['url'] = response.urljoin(url) if url else response.url

            # Extract body
            body_parts = item_sel.css('p::text, .description::text, .content::text').getall()
            item['body'] = ' '.join(body_parts).strip()

            # Timestamp
            timestamp = item_sel.css('time::attr(datetime), .timestamp::text, .date::text').get()
            item['published_at'] = self._parse_timestamp(timestamp)
            item['scraped_at'] = datetime.utcnow().isoformat()

            # Metadata
            item['language'] = 'en'
            item['author'] = 'Arkham Intelligence'
            item['categories'] = ['on_chain', 'intelligence']
            item['tags'] = ['arkham', 'on_chain_data']

            # Extract images
            images = item_sel.css('img::attr(src)').getall()
            item['images'] = [response.urljoin(img) for img in images]

            # Extract links
            links = item_sel.css('a::attr(href)').getall()
            item['links'] = [response.urljoin(link) for link in links]

            # Try to identify transaction alerts
            if self._is_transaction_alert(title, item['body']):
                item['event_types'] = ['large_transaction', 'on_chain_movement']

            # Extract crypto entities
            item['crypto_entities'] = self._extract_crypto_mentions(f"{title} {item['body']}")

            yield item

        # Look for links to more pages
        more_links = response.css('a[href*="alerts"], a[href*="news"], a[href*="intelligence"]::attr(href)').getall()
        for link in more_links[:5]:  # Limit follow links
            yield response.follow(link, callback=self.parse)

    def _parse_timestamp(self, timestamp_text):
        """Parse timestamp"""
        if not timestamp_text:
            return datetime.utcnow().isoformat()

        timestamp_text = timestamp_text.strip()

        # ISO format
        try:
            return datetime.fromisoformat(timestamp_text.replace('Z', '+00:00')).isoformat()
        except:
            pass

        # Relative time
        if 'ago' in timestamp_text.lower():
            return datetime.utcnow().isoformat()

        return datetime.utcnow().isoformat()

    def _is_transaction_alert(self, title, body):
        """Check if content is about a transaction"""
        text = f"{title} {body}".lower()
        keywords = ['transferred', 'moved', 'transaction', 'sent', 'received', 'withdrawn', 'deposited']
        return any(keyword in text for keyword in keywords)

    def _extract_crypto_mentions(self, text):
        """Extract cryptocurrency mentions"""
        crypto_patterns = {
            'Bitcoin': r'\b(bitcoin|btc)\b',
            'Ethereum': r'\b(ethereum|eth)\b',
            'Tether': r'\b(tether|usdt)\b',
            'USDC': r'\b(usdc|usd coin)\b',
            'BNB': r'\b(bnb|binance coin)\b',
            'XRP': r'\bxrp\b',
            'Solana': r'\b(solana|sol)\b',
        }

        found = []
        text_lower = text.lower()

        for crypto, pattern in crypto_patterns.items():
            if re.search(pattern, text_lower):
                found.append(crypto)

        return found
