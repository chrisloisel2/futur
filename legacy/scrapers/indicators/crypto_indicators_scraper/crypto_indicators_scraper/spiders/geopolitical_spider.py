"""
Spider for scraping geopolitical events and regulatory news affecting crypto.
"""
import json
import logging
from datetime import datetime, timedelta
import scrapy
from scrapy.http import Request

from ..items_alternative import GeopoliticalEventItem

logger = logging.getLogger(__name__)


class GeopoliticalSpider(scrapy.Spider):
    """
    Spider for scraping geopolitical and regulatory events.

    Sources:
    - NewsAPI (crypto regulatory news)
    - CoinDesk/CoinTelegraph (crypto news)
    - GDELT Project (global events)
    - Regulation tracker APIs
    """

    name = 'geopolitical'
    allowed_domains = [
        'newsapi.org',
        'api.gdeltproject.org',
        'www.coindesk.com',
        'cointelegraph.com',
    ]

    # Keywords for crypto-related geopolitical events
    CRYPTO_KEYWORDS = [
        'bitcoin regulation',
        'crypto ban',
        'cryptocurrency law',
        'crypto adoption',
        'bitcoin legal tender',
        'crypto tax',
        'SEC crypto',
        'crypto ETF',
        'CBDC',
        'stablecoin regulation',
    ]

    def __init__(self, newsapi_key=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.newsapi_key = newsapi_key

    def start_requests(self):
        """Generate initial requests."""
        # 1. NewsAPI - Regulatory news
        if self.newsapi_key:
            yield from self._newsapi_requests()

        # 2. CoinDesk - Crypto news aggregator
        yield from self._coindesk_requests()

        # 3. GDELT - Global events
        yield from self._gdelt_requests()

    def _newsapi_requests(self):
        """Generate NewsAPI requests for crypto regulatory news."""
        # Get news from last 7 days
        from_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')

        for keyword in self.CRYPTO_KEYWORDS:
            url = (
                f"https://newsapi.org/v2/everything?"
                f"q={keyword.replace(' ', '%20')}"
                f"&from={from_date}"
                f"&sortBy=publishedAt"
                f"&language=en"
                f"&apiKey={self.newsapi_key}"
            )

            yield Request(
                url=url,
                callback=self.parse_newsapi,
                meta={'keyword': keyword},
                errback=self.handle_error,
            )

    def _coindesk_requests(self):
        """Generate CoinDesk RSS requests."""
        # CoinDesk RSS feeds
        feeds = [
            'https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=json',
        ]

        for feed_url in feeds:
            yield Request(
                url=feed_url,
                callback=self.parse_coindesk,
                errback=self.handle_error,
            )

    def _gdelt_requests(self):
        """Generate GDELT requests for global events."""
        # GDELT 2.0 API for crypto-related events
        query = 'cryptocurrency OR bitcoin OR crypto OR blockchain'
        url = f"https://api.gdeltproject.org/api/v2/doc/doc?query={query}&mode=artlist&maxrecords=250&format=json"

        yield Request(
            url=url,
            callback=self.parse_gdelt,
            errback=self.handle_error,
        )

    def parse_newsapi(self, response):
        """Parse NewsAPI results."""
        try:
            data = json.loads(response.text)
            keyword = response.meta['keyword']

            if data.get('articles'):
                for article in data['articles']:
                    item = GeopoliticalEventItem()

                    item['timestamp'] = datetime.fromisoformat(article['publishedAt'].replace('Z', '+00:00'))
                    item['source'] = 'newsapi'

                    item['title'] = article.get('title', '')
                    item['description'] = article.get('description', '')
                    item['url'] = article.get('url', '')

                    # Determine event type from keyword
                    item['event_type'] = self._classify_event_type(keyword)

                    # Extract keywords
                    item['keywords'] = [keyword] + self._extract_keywords(article.get('title', ''))

                    # Sentiment analysis (simple keyword-based)
                    item['news_sentiment'] = self._analyze_sentiment(article.get('title', '') + ' ' + article.get('description', ''))
                    item['tone'] = 'positive' if item['news_sentiment'] > 0 else 'negative' if item['news_sentiment'] < 0 else 'neutral'

                    # Estimate severity and impact
                    item['severity'] = self._estimate_severity(keyword, article.get('title', ''))
                    item['impact_score'] = item['severity'] / 10.0

                    item['scraped_at'] = datetime.now()

                    yield item

        except Exception as e:
            logger.error(f"Error parsing NewsAPI data: {e}")

    def parse_coindesk(self, response):
        """Parse CoinDesk news."""
        try:
            data = json.loads(response.text)

            if isinstance(data, list):
                articles = data
            elif isinstance(data, dict):
                articles = data.get('items', []) or data.get('articles', [])
            else:
                articles = []

            for article in articles[:50]:  # Limit to 50 most recent
                item = GeopoliticalEventItem()

                # Parse timestamp
                pub_date = article.get('publishedAt') or article.get('pubDate') or article.get('published')
                if pub_date:
                    try:
                        item['timestamp'] = datetime.fromisoformat(pub_date.replace('Z', '+00:00'))
                    except:
                        item['timestamp'] = datetime.now()
                else:
                    item['timestamp'] = datetime.now()

                item['source'] = 'coindesk'

                item['title'] = article.get('title', '')
                item['description'] = article.get('description', '') or article.get('summary', '')
                item['url'] = article.get('link', '') or article.get('url', '')

                # Classify event
                item['event_type'] = self._classify_from_content(item['title'], item['description'])

                # Extract entities
                item['keywords'] = self._extract_keywords(item['title'])
                item['entities'] = self._extract_entities(item['title'] + ' ' + item['description'])

                # Sentiment
                content = item['title'] + ' ' + item['description']
                item['news_sentiment'] = self._analyze_sentiment(content)
                item['tone'] = 'positive' if item['news_sentiment'] > 0 else 'negative' if item['news_sentiment'] < 0 else 'neutral'

                item['severity'] = self._estimate_severity(item['event_type'], item['title'])
                item['impact_score'] = item['severity'] / 10.0

                item['scraped_at'] = datetime.now()

                yield item

        except Exception as e:
            logger.error(f"Error parsing CoinDesk data: {e}")

    def parse_gdelt(self, response):
        """Parse GDELT events."""
        try:
            data = json.loads(response.text)

            if data.get('articles'):
                for article in data['articles'][:100]:  # Limit to 100
                    item = GeopoliticalEventItem()

                    item['timestamp'] = datetime.fromisoformat(article.get('seendate', datetime.now().isoformat()))
                    item['source'] = 'gdelt'

                    item['title'] = article.get('title', '')
                    item['description'] = ''
                    item['url'] = article.get('url', '')

                    # GDELT provides tone
                    tone_value = float(article.get('tone', 0))
                    item['news_sentiment'] = tone_value / 100.0  # Normalize
                    item['tone'] = 'positive' if tone_value > 0 else 'negative' if tone_value < 0 else 'neutral'

                    # Extract location/country
                    item['country'] = self._extract_country(article.get('locations', ''))

                    # Classify event
                    item['event_type'] = self._classify_from_content(item['title'], '')

                    item['keywords'] = self._extract_keywords(item['title'])
                    item['severity'] = abs(tone_value) / 10.0
                    item['impact_score'] = item['severity'] / 10.0

                    item['scraped_at'] = datetime.now()

                    yield item

        except Exception as e:
            logger.error(f"Error parsing GDELT data: {e}")

    def _classify_event_type(self, keyword: str) -> str:
        """Classify event type from keyword."""
        if 'ban' in keyword.lower():
            return 'ban'
        elif 'regulation' in keyword.lower() or 'law' in keyword.lower():
            return 'regulation'
        elif 'adoption' in keyword.lower() or 'legal tender' in keyword.lower():
            return 'adoption'
        elif 'tax' in keyword.lower():
            return 'tax'
        elif 'etf' in keyword.lower():
            return 'etf'
        elif 'cbdc' in keyword.lower():
            return 'cbdc'
        else:
            return 'general'

    def _classify_from_content(self, title: str, description: str) -> str:
        """Classify event from content."""
        content = (title + ' ' + description).lower()

        if any(word in content for word in ['ban', 'banned', 'banning', 'prohibit']):
            return 'ban'
        elif any(word in content for word in ['regulation', 'regulate', 'regulatory', 'law', 'legal']):
            return 'regulation'
        elif any(word in content for word in ['adopt', 'adoption', 'legal tender', 'accept']):
            return 'adoption'
        elif any(word in content for word in ['war', 'conflict', 'sanction', 'crisis']):
            return 'conflict'
        elif 'etf' in content:
            return 'etf'
        elif 'hack' in content or 'breach' in content:
            return 'security'
        elif 'cbdc' in content or 'central bank' in content:
            return 'cbdc'
        else:
            return 'general'

    def _analyze_sentiment(self, text: str) -> float:
        """Simple sentiment analysis based on keywords."""
        text = text.lower()

        # Positive keywords
        positive_words = [
            'bullish', 'surge', 'rally', 'adoption', 'growth', 'positive',
            'approve', 'approved', 'acceptance', 'milestone', 'breakthrough',
            'success', 'gains', 'soar', 'moon', 'pump'
        ]

        # Negative keywords
        negative_words = [
            'bearish', 'crash', 'ban', 'banned', 'decline', 'drop', 'fall',
            'reject', 'rejected', 'crisis', 'concern', 'fear', 'warning',
            'risk', 'dump', 'collapse', 'hack', 'scam', 'fraud'
        ]

        pos_count = sum(1 for word in positive_words if word in text)
        neg_count = sum(1 for word in negative_words if word in text)

        if pos_count + neg_count == 0:
            return 0.0

        return (pos_count - neg_count) / (pos_count + neg_count)

    def _extract_keywords(self, text: str) -> list:
        """Extract important keywords."""
        keywords = []
        crypto_terms = ['bitcoin', 'ethereum', 'crypto', 'blockchain', 'btc', 'eth', 'defi', 'nft']

        text_lower = text.lower()
        for term in crypto_terms:
            if term in text_lower:
                keywords.append(term)

        return keywords

    def _extract_entities(self, text: str) -> list:
        """Extract countries and organizations."""
        entities = []

        # Common countries in crypto news
        countries = [
            'United States', 'USA', 'China', 'Japan', 'Singapore', 'UK',
            'European Union', 'EU', 'India', 'Russia', 'South Korea',
            'El Salvador', 'Switzerland', 'Germany', 'France'
        ]

        # Organizations
        orgs = ['SEC', 'CFTC', 'Fed', 'ECB', 'IMF', 'World Bank', 'Treasury']

        for country in countries:
            if country.lower() in text.lower():
                entities.append(country)

        for org in orgs:
            if org.lower() in text.lower():
                entities.append(org)

        return entities

    def _extract_country(self, locations: str) -> str:
        """Extract country from GDELT locations."""
        if not locations:
            return 'Unknown'

        # Parse locations string
        # Usually in format: "Country1;Country2"
        if ';' in locations:
            return locations.split(';')[0]
        return locations

    def _estimate_severity(self, event_type: str, title: str) -> int:
        """Estimate event severity (1-10)."""
        severity_map = {
            'ban': 9,
            'regulation': 6,
            'adoption': 7,
            'conflict': 8,
            'etf': 7,
            'security': 8,
            'cbdc': 5,
            'tax': 5,
            'general': 3,
        }

        base_severity = severity_map.get(event_type, 3)

        # Adjust based on keywords in title
        title_lower = title.lower()
        if any(word in title_lower for word in ['major', 'significant', 'historic', 'unprecedented']):
            base_severity = min(10, base_severity + 2)

        return base_severity

    def handle_error(self, failure):
        """Handle request failures."""
        logger.error(f"Request failed: {failure.request.url}")
        logger.error(f"Error: {failure.value}")
