"""
Social sentiment spider - StockTwits and similar platforms
https://stocktwits.com/
"""

import scrapy
from datetime import datetime
from items import SocialSentimentItem
import re
import json


class SocialSentimentSpider(scrapy.Spider):
    name = 'social_sentiment'
    allowed_domains = ['stocktwits.com']

    # Major crypto symbols on StockTwits
    CRYPTO_SYMBOLS = ['BTC.X', 'ETH.X', 'SOL.X', 'ADA.X', 'XRP.X', 'DOGE.X']

    def start_requests(self):
        """Generate requests for each crypto symbol"""
        for symbol in self.CRYPTO_SYMBOLS:
            url = f'https://stocktwits.com/symbol/{symbol}'
            yield scrapy.Request(url, callback=self.parse, meta={'symbol': symbol})

    custom_settings = {
        'DOWNLOAD_DELAY': 3,
        'CONCURRENT_REQUESTS_PER_DOMAIN': 1,
    }

    def parse(self, response):
        """Parse StockTwits symbol page"""
        symbol = response.meta['symbol']

        # StockTwits may be a SPA, so we look for:
        # 1. Initial data in script tags
        # 2. Messages in HTML
        # 3. API endpoints

        # Try to find messages
        messages = response.css('.message, [data-testid="message"], article.stream-message')

        if not messages:
            # Try to find data in script tags
            scripts = response.css('script::text').getall()
            for script in scripts:
                if 'messages' in script or 'stream' in script:
                    try:
                        # Try to extract JSON data
                        data = self._extract_json_from_script(script)
                        if data and 'messages' in data:
                            for msg in data['messages']:
                                yield self._parse_message_from_json(msg, symbol)
                    except:
                        continue

        # Parse HTML messages
        for message in messages:
            item = SocialSentimentItem()

            # Basic info
            item['platform'] = 'StockTwits'
            item['symbols'] = [symbol]

            # Extract post ID
            post_id = message.css('::attr(data-id)').get() or message.css('::attr(id)').get()
            item['post_id'] = post_id or ''

            # URL
            message_link = message.css('a[href*="/message/"]::attr(href)').get()
            item['url'] = response.urljoin(message_link) if message_link else response.url

            # Author
            author = message.css('.user-name::text, .username::text, [data-testid="username"]::text').get()
            item['author'] = author.strip() if author else 'Unknown'

            # Author followers (if available)
            followers = message.css('.followers-count::text').get()
            item['author_followers'] = self._extract_number(followers)

            # Text content
            text_parts = message.css('.message-text::text, .body::text, [data-testid="message-body"]::text').getall()
            item['text'] = ' '.join(text_parts).strip()

            if not item['text'] or len(item['text']) < 10:
                continue

            # Sentiment (StockTwits has explicit bullish/bearish)
            sentiment_indicator = message.css('.sentiment-bullish, .sentiment-bearish, [data-sentiment]')
            if sentiment_indicator:
                sentiment_class = sentiment_indicator.css('::attr(class)').get() or ''
                sentiment_data = sentiment_indicator.css('::attr(data-sentiment)').get() or ''

                if 'bullish' in sentiment_class.lower() or sentiment_data.lower() == 'bullish':
                    item['sentiment'] = 'bullish'
                    item['sentiment_score'] = 0.7
                elif 'bearish' in sentiment_class.lower() or sentiment_data.lower() == 'bearish':
                    item['sentiment'] = 'bearish'
                    item['sentiment_score'] = -0.7
                else:
                    item['sentiment'] = 'neutral'
                    item['sentiment_score'] = 0.0
            else:
                # Infer sentiment from text
                sentiment_data = self._infer_sentiment(item['text'])
                item['sentiment'] = sentiment_data['sentiment']
                item['sentiment_score'] = sentiment_data['score']

            # Timestamp
            timestamp = message.css('time::attr(datetime), .timestamp::attr(datetime)').get()
            item['timestamp'] = self._parse_timestamp(timestamp)
            item['scraped_at'] = datetime.utcnow().isoformat()

            # Engagement
            likes = message.css('.like-count::text, [data-testid="like-count"]::text').get()
            reshares = message.css('.reshare-count::text').get()
            replies = message.css('.reply-count::text').get()

            item['likes'] = self._extract_number(likes)
            item['reshares'] = self._extract_number(reshares)
            item['replies'] = self._extract_number(replies)

            yield item

        # Pagination (if available)
        next_page = response.css('a[rel="next"]::attr(href), .load-more::attr(href)').get()
        if next_page:
            yield response.follow(next_page, callback=self.parse, meta={'symbol': symbol})

    def _extract_json_from_script(self, script_text):
        """Try to extract JSON from script tag"""
        # Look for JSON objects
        try:
            # Find JSON between braces
            start = script_text.find('{')
            end = script_text.rfind('}') + 1
            if start != -1 and end > start:
                json_str = script_text[start:end]
                return json.loads(json_str)
        except:
            pass
        return None

    def _parse_message_from_json(self, msg_data, symbol):
        """Parse message from JSON data"""
        item = SocialSentimentItem()

        item['platform'] = 'StockTwits'
        item['post_id'] = str(msg_data.get('id', ''))
        item['url'] = f"https://stocktwits.com/message/{item['post_id']}"

        # Author
        user = msg_data.get('user', {})
        item['author'] = user.get('username', 'Unknown')
        item['author_followers'] = user.get('followers', None)

        # Content
        item['text'] = msg_data.get('body', '')
        item['symbols'] = [symbol]

        # Sentiment
        entities = msg_data.get('entities', {})
        sentiment_data = entities.get('sentiment', {})
        if sentiment_data:
            basic = sentiment_data.get('basic', 'neutral')
            item['sentiment'] = basic
            item['sentiment_score'] = 0.7 if basic == 'bullish' else (-0.7 if basic == 'bearish' else 0.0)
        else:
            sentiment_inferred = self._infer_sentiment(item['text'])
            item['sentiment'] = sentiment_inferred['sentiment']
            item['sentiment_score'] = sentiment_inferred['score']

        # Timestamp
        item['timestamp'] = msg_data.get('created_at', datetime.utcnow().isoformat())
        item['scraped_at'] = datetime.utcnow().isoformat()

        # Engagement
        item['likes'] = msg_data.get('likes', {}).get('total', 0)
        item['reshares'] = 0  # Not in basic data
        item['replies'] = 0

        return item

    def _infer_sentiment(self, text):
        """Infer sentiment from text"""
        text_lower = text.lower()

        bullish_keywords = [
            'bullish', 'bull', 'moon', 'pump', 'long', 'buy', 'calls',
            'breakout', 'rally', 'gains', 'profit', 'up', 'higher'
        ]

        bearish_keywords = [
            'bearish', 'bear', 'dump', 'short', 'sell', 'puts',
            'breakdown', 'crash', 'losses', 'down', 'lower', 'falling'
        ]

        bullish_count = sum(1 for kw in bullish_keywords if kw in text_lower)
        bearish_count = sum(1 for kw in bearish_keywords if kw in text_lower)

        total = bullish_count + bearish_count
        if total == 0:
            return {'sentiment': 'neutral', 'score': 0.0}

        score = (bullish_count - bearish_count) / total

        if score > 0.3:
            return {'sentiment': 'bullish', 'score': score}
        elif score < -0.3:
            return {'sentiment': 'bearish', 'score': score}
        else:
            return {'sentiment': 'neutral', 'score': score}

    def _extract_number(self, text):
        """Extract number from text"""
        if not text:
            return None

        # Handle K, M suffixes
        text = text.strip().upper()
        match = re.search(r'(\d+(?:\.\d+)?)\s*([KM])?', text)

        if match:
            num = float(match.group(1))
            suffix = match.group(2)

            if suffix == 'K':
                num *= 1000
            elif suffix == 'M':
                num *= 1000000

            return int(num)

        return None

    def _parse_timestamp(self, timestamp_str):
        """Parse timestamp"""
        if not timestamp_str:
            return datetime.utcnow().isoformat()

        try:
            return datetime.fromisoformat(timestamp_str.replace('Z', '+00:00')).isoformat()
        except:
            pass

        return datetime.utcnow().isoformat()
