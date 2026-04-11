"""
Asian crypto news sources spider
Covers: 8btc, jinse, chainnews, feixiaohao (Chinese), coinpost (Japanese)
"""

import scrapy
from datetime import datetime
from items import NewsArticleItem
import re


class AsianCryptoSpider(scrapy.Spider):
    name = 'asian_crypto'
    allowed_domains = [
        '8btc.com',
        'jinse.com',
        'chainnews.com',
        'feixiaohao.com',
        'coinpost.jp'
    ]

    start_urls = [
        'https://www.8btc.com/news',
        'https://www.jinse.com/news',
        'https://www.chainnews.com/',
        'https://www.feixiaohao.com/news/',
        'https://coinpost.jp/',
    ]

    custom_settings = {
        'DOWNLOAD_DELAY': 3,  # Be more respectful with international sites
        'CONCURRENT_REQUESTS_PER_DOMAIN': 1,
        'DEPTH_LIMIT': 2,
    }

    # Language detection by domain
    DOMAIN_LANGUAGES = {
        '8btc.com': 'zh',
        'jinse.com': 'zh',
        'chainnews.com': 'zh',
        'feixiaohao.com': 'zh',
        'coinpost.jp': 'ja',
    }

    # Source tier mapping
    SOURCE_TIERS = {
        '8btc.com': 'tier2',
        'jinse.com': 'tier2',
        'chainnews.com': 'tier3',
        'feixiaohao.com': 'tier3',
        'coinpost.jp': 'tier2',
    }

    def parse(self, response):
        """Parse news listing pages"""
        domain = self._get_domain(response.url)

        # Generic article selectors
        article_selectors = [
            'article',
            '.news-item',
            '.article-item',
            '[class*="article"]',
            '[class*="news"]',
            'li.item',
        ]

        articles = None
        for selector in article_selectors:
            articles = response.css(selector)
            if articles:
                break

        if not articles:
            self.logger.warning(f"No articles found on {response.url}")
            return

        for article in articles[:15]:  # Limit
            article_url = article.css('a::attr(href)').get()
            if not article_url:
                continue

            yield response.follow(
                article_url,
                callback=self.parse_article,
                meta={'domain': domain}
            )

    def parse_article(self, response):
        """Parse individual article"""
        domain = response.meta.get('domain', self._get_domain(response.url))
        source_name = self._get_source_name(domain)
        language = self.DOMAIN_LANGUAGES.get(domain, 'en')

        item = NewsArticleItem()

        # Basic metadata
        item['url'] = response.url
        item['source'] = source_name
        item['source_tier'] = self.SOURCE_TIERS.get(domain, 'tier3')
        item['scraped_at'] = datetime.utcnow().isoformat()
        item['language'] = language
        item['country'] = 'CN' if language == 'zh' else 'JP'

        # Extract title
        title = (
            response.css('h1::text').get() or
            response.css('article h1::text').get() or
            response.css('.title::text').get() or
            response.css('meta[property="og:title"]::attr(content)').get()
        )
        item['title'] = title.strip() if title else ''

        if not item['title'] or len(item['title']) < 5:
            return

        # Extract author
        author = (
            response.css('.author::text').get() or
            response.css('[class*="author"]::text').get()
        )
        item['author'] = author.strip() if author else None

        # Extract body
        body_selectors = [
            'article .content',
            '.article-content',
            '.news-content',
            '[class*="content"] p',
            'article p',
        ]

        body_parts = []
        for selector in body_selectors:
            body_parts = response.css(f'{selector}::text').getall()
            if body_parts:
                break

        item['body'] = ' '.join(p.strip() for p in body_parts if p.strip())

        if len(item['body']) < 50:
            return

        # Extract summary
        summary = response.css('meta[property="og:description"]::attr(content)').get()
        item['summary'] = summary.strip() if summary else item['body'][:200]

        # Extract timestamp
        timestamp = (
            response.css('time::attr(datetime)').get() or
            response.css('.time::text, .date::text').get() or
            response.css('meta[property="article:published_time"]::attr(content)').get()
        )
        item['published_at'] = self._parse_timestamp(timestamp)

        # Categories and tags
        categories = response.css('.category::text, .tag::text').getall()
        item['categories'] = [cat.strip() for cat in categories if cat.strip()][:5]
        item['tags'] = []

        # Images
        images = response.css('article img::attr(src), .content img::attr(src)').getall()
        item['images'] = [response.urljoin(img) for img in images[:3]]

        # Links
        links = response.css('article a::attr(href)').getall()
        item['links'] = [response.urljoin(link) for link in links[:5]]

        # Set defaults
        item['event_types'] = []
        item['crypto_entities'] = []
        item['institutional_entities'] = []
        item['geographic_scope'] = 'regional'  # Asian sources = regional
        item['credibility_score'] = None

        yield item

    def _get_domain(self, url):
        """Extract domain from URL"""
        match = re.search(r'https?://(?:www\.)?([^/]+)', url)
        return match.group(1) if match else ''

    def _get_source_name(self, domain):
        """Get readable source name"""
        names = {
            '8btc.com': '8BTC',
            'jinse.com': 'Jinse',
            'chainnews.com': 'ChainNews',
            'feixiaohao.com': 'Feixiaohao',
            'coinpost.jp': 'CoinPost',
        }
        return names.get(domain, domain)

    def _parse_timestamp(self, timestamp_str):
        """Parse timestamp"""
        if not timestamp_str:
            return datetime.utcnow().isoformat()

        try:
            return datetime.fromisoformat(timestamp_str.replace('Z', '+00:00')).isoformat()
        except:
            pass

        return datetime.utcnow().isoformat()
