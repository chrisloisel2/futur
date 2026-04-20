"""
Spider for scraping crypto sentiment data from social media and news.
"""
import json
import logging
from datetime import datetime, timedelta
from typing import List
import scrapy
from scrapy.http import Request

from ..items_alternative import SentimentDataItem, InfluencerDataItem

logger = logging.getLogger(__name__)


class CryptoSentimentSpider(scrapy.Spider):
    """
    Spider for scraping crypto sentiment from multiple sources.

    Sources:
    - LunarCrush API (social metrics)
    - CryptoPanic (news sentiment)
    - Alternative.me (Fear & Greed Index)
    - Santiment (social volume)
    """

    name = 'crypto_sentiment'
    allowed_domains = [
        'api.lunarcrush.com',
        'cryptopanic.com',
        'api.alternative.me',
        'api.santiment.net',
    ]

    def __init__(self, symbols=None, lunarcrush_api_key=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.symbols = self._parse_symbols(symbols) if symbols else ['BTC', 'ETH', 'BNB']
        self.lunarcrush_api_key = lunarcrush_api_key

    def _parse_symbols(self, symbols_arg) -> List[str]:
        """Parse symbols from command line."""
        if isinstance(symbols_arg, str):
            # Remove USDT suffix for social data
            return [s.strip().replace('USDT', '').upper() for s in symbols_arg.split(',')]
        return symbols_arg

    def start_requests(self):
        """Generate initial requests."""
        for symbol in self.symbols:
            # 1. LunarCrush - Social metrics
            if self.lunarcrush_api_key:
                yield from self._lunarcrush_requests(symbol)

            # 2. CryptoPanic - News sentiment
            yield from self._cryptopanic_requests(symbol)

            # 3. Alternative.me - Fear & Greed
            yield from self._feargreed_requests(symbol)

    def _lunarcrush_requests(self, symbol: str):
        """Generate LunarCrush API requests."""
        url = f"https://api.lunarcrush.com/v2?data=assets&key={self.lunarcrush_api_key}&symbol={symbol}"
        yield Request(
            url=url,
            callback=self.parse_lunarcrush,
            meta={'symbol': symbol},
            errback=self.handle_error,
        )

    def _cryptopanic_requests(self, symbol: str):
        """Generate CryptoPanic API requests."""
        # CryptoPanic uses currency codes
        currency_map = {'BTC': 'BTC', 'ETH': 'ETH', 'BNB': 'BNB'}
        currency = currency_map.get(symbol, symbol)

        url = f"https://cryptopanic.com/api/v1/posts/?auth_token=free&currencies={currency}&public=true"
        yield Request(
            url=url,
            callback=self.parse_cryptopanic,
            meta={'symbol': symbol},
            errback=self.handle_error,
        )

    def _feargreed_requests(self, symbol: str):
        """Generate Fear & Greed Index requests."""
        # Fear & Greed is global, but we'll associate it with BTC
        if symbol == 'BTC':
            url = "https://api.alternative.me/fng/?limit=1"
            yield Request(
                url=url,
                callback=self.parse_feargreed,
                meta={'symbol': symbol},
                errback=self.handle_error,
            )

    def parse_lunarcrush(self, response):
        """Parse LunarCrush social metrics."""
        try:
            data = json.loads(response.text)
            symbol = response.meta['symbol']

            if data.get('data') and len(data['data']) > 0:
                asset_data = data['data'][0]

                item = SentimentDataItem()
                item['symbol'] = symbol + 'USDT'
                item['timestamp'] = datetime.now()
                item['source'] = 'lunarcrush'

                # Social metrics
                item['tweet_volume'] = asset_data.get('tweets')
                item['social_volume_change'] = asset_data.get('social_volume_24h_change')
                item['social_dominance'] = asset_data.get('social_dominance')
                item['sentiment_score'] = asset_data.get('sentiment')

                # Engagement
                item['retweet_count'] = asset_data.get('tweet_spam')
                item['like_count'] = asset_data.get('social_score')

                # Reddit
                item['reddit_posts'] = asset_data.get('reddit_posts_24h')
                item['reddit_comments'] = asset_data.get('reddit_comments_24h')

                item['scraped_at'] = datetime.now()

                yield item

        except Exception as e:
            logger.error(f"Error parsing LunarCrush data: {e}")

    def parse_cryptopanic(self, response):
        """Parse CryptoPanic news sentiment."""
        try:
            data = json.loads(response.text)
            symbol = response.meta['symbol']

            if data.get('results'):
                # Aggregate sentiment from recent posts
                posts = data['results']

                positive = sum(1 for p in posts if p.get('votes', {}).get('positive', 0) > p.get('votes', {}).get('negative', 0))
                negative = sum(1 for p in posts if p.get('votes', {}).get('negative', 0) > p.get('votes', {}).get('positive', 0))
                neutral = len(posts) - positive - negative

                item = SentimentDataItem()
                item['symbol'] = symbol + 'USDT'
                item['timestamp'] = datetime.now()
                item['source'] = 'cryptopanic'

                item['positive_tweets'] = positive
                item['negative_tweets'] = negative
                item['neutral_tweets'] = neutral

                # Calculate sentiment score
                if len(posts) > 0:
                    item['sentiment_score'] = (positive - negative) / len(posts)

                item['tweet_volume'] = len(posts)
                item['scraped_at'] = datetime.now()

                yield item

        except Exception as e:
            logger.error(f"Error parsing CryptoPanic data: {e}")

    def parse_feargreed(self, response):
        """Parse Fear & Greed Index."""
        try:
            data = json.loads(response.text)
            symbol = response.meta['symbol']

            if data.get('data') and len(data['data']) > 0:
                fg_data = data['data'][0]

                item = SentimentDataItem()
                item['symbol'] = symbol + 'USDT'
                item['timestamp'] = datetime.fromtimestamp(int(fg_data['timestamp']))
                item['source'] = 'feargreed'

                item['fear_greed_index'] = int(fg_data['value'])
                item['sentiment_score'] = (int(fg_data['value']) - 50) / 50  # Normalize to -1 to 1

                item['scraped_at'] = datetime.now()

                yield item

        except Exception as e:
            logger.error(f"Error parsing Fear & Greed data: {e}")

    def handle_error(self, failure):
        """Handle request failures."""
        logger.error(f"Request failed: {failure.request.url}")
        logger.error(f"Error: {failure.value}")
