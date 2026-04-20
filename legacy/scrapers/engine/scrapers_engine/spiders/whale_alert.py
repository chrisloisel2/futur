"""
Whale Alert spider - tracks large crypto transactions
https://whale-alert.io/news.html
"""

import scrapy
from datetime import datetime
from items import TransactionAlertItem, NewsArticleItem
import re


class WhaleAlertSpider(scrapy.Spider):
    name = 'whale_alert'
    allowed_domains = ['whale-alert.io']
    start_urls = [
        'https://whale-alert.io/news.html',
    ]

    custom_settings = {
        'DOWNLOAD_DELAY': 2,
        'CONCURRENT_REQUESTS_PER_DOMAIN': 1,
    }

    def parse(self, response):
        """Parse whale alert news page"""
        # Parse news articles about whale transactions
        articles = response.css('div.article-item, article.news-item')

        for article in articles:
            # Extract article data
            title = article.css('h2::text, h3::text, .title::text').get()
            if not title:
                continue

            # Extract transaction details from title/content
            item = NewsArticleItem()
            item['url'] = response.urljoin(article.css('a::attr(href)').get() or response.url)
            item['title'] = title.strip()
            item['source'] = 'Whale Alert'
            item['source_tier'] = 'tier2'

            # Extract body/description
            body_parts = article.css('p::text, .description::text').getall()
            item['body'] = ' '.join(body_parts).strip()

            # Extract timestamp
            timestamp_text = article.css('.date::text, .timestamp::text, time::text').get()
            item['published_at'] = self._parse_timestamp(timestamp_text)
            item['scraped_at'] = datetime.utcnow().isoformat()

            # Set default values
            item['language'] = 'en'
            item['author'] = 'Whale Alert'
            item['categories'] = ['transactions', 'whale_alert']
            item['tags'] = ['whale', 'large_transaction']

            # Extract images
            images = article.css('img::attr(src)').getall()
            item['images'] = [response.urljoin(img) for img in images]

            # Try to extract transaction details from text
            tx_details = self._extract_transaction_details(title, item['body'])
            if tx_details:
                item['event_types'] = ['large_transaction']
                item['crypto_entities'] = [tx_details.get('symbol', '')]

            # Extract links
            links = article.css('a::attr(href)').getall()
            item['links'] = [response.urljoin(link) for link in links]

            # Engagement metrics (if available)
            item['views'] = None
            item['likes'] = None
            item['comments_count'] = None

            yield item

        # Follow pagination
        next_page = response.css('a.next::attr(href), .pagination a[rel="next"]::attr(href)').get()
        if next_page:
            yield response.follow(next_page, callback=self.parse)

    def _parse_timestamp(self, timestamp_text):
        """Parse timestamp from various formats"""
        if not timestamp_text:
            return datetime.utcnow().isoformat()

        timestamp_text = timestamp_text.strip()

        # Try various formats
        formats = [
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%d',
            '%b %d, %Y',
            '%B %d, %Y',
            '%d %b %Y',
        ]

        for fmt in formats:
            try:
                dt = datetime.strptime(timestamp_text, fmt)
                return dt.isoformat()
            except ValueError:
                continue

        # Handle relative times (e.g., "2 hours ago")
        if 'ago' in timestamp_text.lower():
            return datetime.utcnow().isoformat()

        return datetime.utcnow().isoformat()

    def _extract_transaction_details(self, title, body):
        """Extract transaction details from text"""
        text = f"{title} {body}".lower()

        # Extract amount
        amount_patterns = [
            r'(\d+(?:,\d+)*(?:\.\d+)?)\s*(btc|eth|usdt|usdc|bnb|xrp|ada|sol)',
            r'\$(\d+(?:,\d+)*(?:\.\d+)?)\s*(?:million|m|billion|b)?',
        ]

        details = {}

        for pattern in amount_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                if len(match.groups()) > 1:
                    details['amount'] = match.group(1).replace(',', '')
                    details['symbol'] = match.group(2).upper()
                else:
                    details['amount_usd'] = match.group(1).replace(',', '')
                break

        # Extract addresses (basic)
        address_pattern = r'0x[a-fA-F0-9]{40}|[13][a-km-zA-HJ-NP-Z1-9]{25,34}'
        addresses = re.findall(address_pattern, f"{title} {body}")
        if addresses:
            details['addresses'] = addresses[:2]  # First 2 addresses

        return details if details else None
