"""
Multi-site crypto news spider
Covers: CoinDesk, Cointelegraph, The Block, Decrypt, etc.
"""

import scrapy
from datetime import datetime
from items import NewsArticleItem
import re


class CryptoNewsSpider(scrapy.Spider):
    name = 'crypto_news'
    allowed_domains = [
        'coindesk.com',
        'cointelegraph.com',
        'theblock.co',
        'decrypt.co',
        'bitcoinmagazine.com',
        'cryptoslate.com',
        'cryptobriefing.com',
        'ambcrypto.com',
        'u.today',
        'hackernoon.com'
    ]

    # Define start URLs for each source
    start_urls = [
        'https://www.coindesk.com/livewire/',
        'https://www.coindesk.com/business/',
        'https://cointelegraph.com/news',
        'https://www.theblock.co/latest',
        'https://decrypt.co/news',
        'https://bitcoinmagazine.com/news',
        'https://cryptoslate.com/news/',
        'https://cryptobriefing.com/news/',
        'https://ambcrypto.com/news/',
        'https://u.today/latest-cryptocurrency-news',
    ]

    custom_settings = {
        'DOWNLOAD_DELAY': 2,
        'CONCURRENT_REQUESTS_PER_DOMAIN': 2,
        'DEPTH_LIMIT': 2,
    }

    # Source tier mapping
    SOURCE_TIERS = {
        'coindesk.com': 'tier2',
        'cointelegraph.com': 'tier2',
        'theblock.co': 'tier2',
        'decrypt.co': 'tier2',
        'bitcoinmagazine.com': 'tier3',
        'cryptoslate.com': 'tier3',
        'cryptobriefing.com': 'tier3',
        'ambcrypto.com': 'tier3',
        'u.today': 'tier3',
        'hackernoon.com': 'tier4',
    }

    def parse(self, response):
        """Parse news listing pages"""
        domain = self._get_domain(response.url)

        # Generic selectors for article listings
        article_selectors = [
            'article',
            '.article-item',
            '.news-item',
            '.post-item',
            '[class*="article"]',
            '[class*="post-"]',
        ]

        articles = None
        for selector in article_selectors:
            articles = response.css(selector)
            if articles:
                break

        if not articles:
            self.logger.warning(f"No articles found on {response.url}")
            return

        for article in articles[:20]:  # Limit to first 20 articles
            # Extract article URL
            article_url = article.css('a::attr(href)').get()
            if not article_url:
                continue

            # Follow article URL
            yield response.follow(
                article_url,
                callback=self.parse_article,
                meta={'domain': domain}
            )

    def parse_article(self, response):
        """Parse individual article"""
        domain = response.meta.get('domain', self._get_domain(response.url))
        source_name = self._get_source_name(domain)

        item = NewsArticleItem()

        # Basic metadata
        item['url'] = response.url
        item['source'] = source_name
        item['source_tier'] = self.SOURCE_TIERS.get(domain, 'tier3')
        item['scraped_at'] = datetime.utcnow().isoformat()
        item['language'] = 'en'

        # Extract title
        title = (
            response.css('h1::text').get() or
            response.css('article h1::text').get() or
            response.css('.article-title::text').get() or
            response.css('meta[property="og:title"]::attr(content)').get()
        )
        item['title'] = title.strip() if title else ''

        if not item['title'] or len(item['title']) < 5:
            return  # Skip if no valid title

        # Extract author
        author = (
            response.css('.author::text').get() or
            response.css('[class*="author"] a::text').get() or
            response.css('meta[name="author"]::attr(content)').get() or
            response.css('[rel="author"]::text').get()
        )
        item['author'] = author.strip() if author else None

        # Extract body
        body_selectors = [
            'article .article-body',
            '.article-content',
            '.post-content',
            '[class*="content"] p',
            'article p',
        ]

        body_parts = []
        for selector in body_selectors:
            body_parts = response.css(f'{selector}::text').getall()
            if body_parts:
                break

        item['body'] = ' '.join(p.strip() for p in body_parts if p.strip())

        # Skip if body too short
        if len(item['body']) < 100:
            return

        # Extract summary/description
        summary = (
            response.css('meta[property="og:description"]::attr(content)').get() or
            response.css('meta[name="description"]::attr(content)').get()
        )
        item['summary'] = summary.strip() if summary else item['body'][:200]

        # Extract timestamp
        timestamp = (
            response.css('time::attr(datetime)').get() or
            response.css('[class*="date"]::attr(datetime)').get() or
            response.css('meta[property="article:published_time"]::attr(content)').get()
        )
        item['published_at'] = self._parse_timestamp(timestamp)

        # Extract categories
        categories = response.css('.category::text, .tag::text, [rel="category tag"]::text').getall()
        item['categories'] = [cat.strip() for cat in categories if cat.strip()][:5]

        # Extract tags
        tags = response.css('.tags a::text, [rel="tag"]::text').getall()
        item['tags'] = [tag.strip() for tag in tags if tag.strip()][:10]

        # Extract images
        images = []
        img_urls = response.css('article img::attr(src), .article-image img::attr(src)').getall()
        for img in img_urls[:3]:  # Max 3 images
            images.append(response.urljoin(img))
        item['images'] = images

        # Extract links
        links = response.css('article a::attr(href)').getall()
        item['links'] = [response.urljoin(link) for link in links[:10]]

        # Set defaults for enrichment
        item['event_types'] = []
        item['crypto_entities'] = []
        item['institutional_entities'] = []
        item['geographic_scope'] = 'global'
        item['credibility_score'] = None  # Will be calculated in pipeline

        yield item

    def _get_domain(self, url):
        """Extract domain from URL"""
        match = re.search(r'https?://(?:www\.)?([^/]+)', url)
        return match.group(1) if match else ''

    def _get_source_name(self, domain):
        """Get readable source name from domain"""
        names = {
            'coindesk.com': 'CoinDesk',
            'cointelegraph.com': 'Cointelegraph',
            'theblock.co': 'The Block',
            'decrypt.co': 'Decrypt',
            'bitcoinmagazine.com': 'Bitcoin Magazine',
            'cryptoslate.com': 'CryptoSlate',
            'cryptobriefing.com': 'Crypto Briefing',
            'ambcrypto.com': 'AMBCrypto',
            'u.today': 'U.Today',
            'hackernoon.com': 'HackerNoon',
        }
        return names.get(domain, domain)

    def _parse_timestamp(self, timestamp_str):
        """Parse timestamp from various formats"""
        if not timestamp_str:
            return datetime.utcnow().isoformat()

        # ISO format
        try:
            return datetime.fromisoformat(timestamp_str.replace('Z', '+00:00')).isoformat()
        except:
            pass

        # Fallback
        return datetime.utcnow().isoformat()
