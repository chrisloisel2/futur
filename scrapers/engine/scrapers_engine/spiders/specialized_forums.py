"""
Specialized crypto forums
Covers: Ethereum Magicians, Ethresear.ch, HackerNews, Bitcoin StackExchange
"""

import scrapy
from datetime import datetime
from items import ForumPostItem, NewsArticleItem
import re


class SpecializedForumsSpider(scrapy.Spider):
    name = 'specialized_forums'
    allowed_domains = [
        'ethereum-magicians.org',
        'ethresear.ch',
        'news.ycombinator.com',
        'bitcoin.stackexchange.com'
    ]

    start_urls = [
        'https://ethereum-magicians.org/latest',
        'https://ethresear.ch/latest',
        'https://news.ycombinator.com/news',  # Filter for crypto later
        'https://bitcoin.stackexchange.com/questions',
    ]

    custom_settings = {
        'DOWNLOAD_DELAY': 2,
        'CONCURRENT_REQUESTS_PER_DOMAIN': 2,
        'DEPTH_LIMIT': 2,
    }

    def parse(self, response):
        """Route to appropriate parser based on domain"""
        if 'ethereum-magicians.org' in response.url or 'ethresear.ch' in response.url:
            return self.parse_discourse(response)
        elif 'news.ycombinator.com' in response.url:
            return self.parse_hackernews(response)
        elif 'bitcoin.stackexchange.com' in response.url:
            return self.parse_stackexchange(response)

    def parse_discourse(self, response):
        """Parse Discourse-based forums (Ethereum Magicians, Ethresear.ch)"""
        forum_name = 'Ethereum Magicians' if 'magicians' in response.url else 'Ethresear.ch'

        # Discourse uses topic list
        topics = response.css('tr.topic-list-item, .topic-list tbody tr')

        for topic in topics[:20]:
            # Extract topic URL
            topic_url = topic.css('a.title::attr(href)').get()
            if not topic_url:
                continue

            # Get title
            title = topic.css('a.title::text').get()
            if not title:
                continue

            # Follow to topic page
            yield response.follow(
                topic_url,
                callback=self.parse_discourse_topic,
                meta={
                    'forum': forum_name,
                    'title': title.strip()
                }
            )

    def parse_discourse_topic(self, response):
        """Parse Discourse topic/thread"""
        forum = response.meta['forum']
        title = response.meta['title']

        # Extract posts
        posts = response.css('.topic-post, article[data-post-id]')

        for post in posts[:10]:  # First 10 posts
            item = ForumPostItem()

            item['title'] = title
            item['url'] = response.url
            item['forum'] = forum
            item['board'] = response.css('.category-name::text').get() or 'General'

            # Post ID
            item['post_id'] = post.css('::attr(data-post-id)').get() or ''
            item['thread_id'] = self._extract_discourse_thread_id(response.url)

            # Author
            author = post.css('[itemprop="author"] [itemprop="name"]::text').get()
            item['author'] = author.strip() if author else 'Unknown'

            # Author metadata
            item['author_rank'] = post.css('.user-title::text').get()
            item['author_posts_count'] = None  # Not easily accessible

            # Post body
            body = post.css('[itemprop="text"] p::text, .cooked p::text').getall()
            item['body'] = ' '.join(body).strip()

            if len(item['body']) < 20:
                continue

            # Timestamp
            timestamp = post.css('time::attr(datetime)').get()
            item['posted_at'] = self._parse_timestamp(timestamp)
            item['scraped_at'] = datetime.utcnow().isoformat()

            # Engagement
            likes = post.css('.like-count::text').get()
            item['upvotes'] = int(likes) if likes and likes.isdigit() else None
            item['views'] = None
            item['replies_count'] = None

            # Classification
            item['is_announcement'] = 'announcement' in title.lower()
            item['is_technical'] = True  # These forums are technical
            item['is_speculation'] = False
            item['mentioned_tokens'] = self._extract_tokens(f"{title} {item['body']}")

            yield item

    def parse_hackernews(self, response):
        """Parse HackerNews front page"""
        # Extract stories
        stories = response.css('tr.athing')

        for story in stories:
            title_elem = story.css('.titleline > a')
            title = title_elem.css('::text').get()
            url = title_elem.css('::attr(href)').get()

            if not title or not url:
                continue

            # Only follow crypto-related stories
            if not self._is_crypto_related(title):
                continue

            # Create news item
            item = NewsArticleItem()
            item['title'] = title.strip()
            item['url'] = url if url.startswith('http') else f"https://news.ycombinator.com/{url}"
            item['source'] = 'Hacker News'
            item['source_tier'] = 'tier3'
            item['scraped_at'] = datetime.utcnow().isoformat()
            item['language'] = 'en'

            # Get story metadata
            story_id = story.css('::attr(id)').get()
            subtext = response.css(f'#score_{story_id}').xpath('../..')

            # Points and comments
            points = subtext.css('.score::text').get()
            comments = subtext.css('a:contains("comments")::text').get()

            item['body'] = f"HackerNews discussion: {points or '0'}, {comments or '0 comments'}"
            item['summary'] = title
            item['published_at'] = datetime.utcnow().isoformat()
            item['author'] = subtext.css('.hnuser::text').get()

            item['categories'] = ['discussion', 'hackernews']
            item['tags'] = ['crypto', 'blockchain']
            item['images'] = []
            item['links'] = [item['url']]

            item['event_types'] = []
            item['crypto_entities'] = self._extract_tokens(title)
            item['credibility_score'] = None

            yield item

    def parse_stackexchange(self, response):
        """Parse Bitcoin StackExchange"""
        questions = response.css('.question-summary')

        for question in questions[:20]:
            title = question.css('.question-hyperlink::text').get()
            url = question.css('.question-hyperlink::attr(href)').get()

            if not title or not url:
                continue

            # Create forum post item
            item = ForumPostItem()
            item['title'] = title.strip()
            item['url'] = response.urljoin(url)
            item['forum'] = 'Bitcoin StackExchange'
            item['board'] = 'Questions'

            # Extract question ID
            item['post_id'] = question.css('::attr(data-post-id)').get() or ''
            item['thread_id'] = item['post_id']

            # Extract summary/body from excerpt
            excerpt = question.css('.excerpt::text').getall()
            item['body'] = ' '.join(excerpt).strip()

            # Author
            author = question.css('.user-details a::text').get()
            item['author'] = author.strip() if author else 'Unknown'

            # Metadata
            item['author_rank'] = None
            item['author_posts_count'] = None

            # Timestamps
            timestamp = question.css('.relativetime::attr(title)').get()
            item['posted_at'] = self._parse_timestamp(timestamp)
            item['scraped_at'] = datetime.utcnow().isoformat()

            # Engagement
            votes = question.css('.vote-count-post strong::text').get()
            answers = question.css('.status strong::text').get()
            views = question.css('.views::attr(title)').get()

            item['upvotes'] = int(votes) if votes and votes.lstrip('-').isdigit() else None
            item['replies_count'] = int(answers) if answers and answers.isdigit() else None
            item['views'] = int(views.split()[0].replace(',', '')) if views else None

            # Classification
            item['is_announcement'] = False
            item['is_technical'] = True
            item['is_speculation'] = False
            item['mentioned_tokens'] = self._extract_tokens(title)

            yield item

    def _extract_discourse_thread_id(self, url):
        """Extract thread ID from Discourse URL"""
        match = re.search(r'/t/[^/]+/(\d+)', url)
        return match.group(1) if match else ''

    def _is_crypto_related(self, title):
        """Check if title is crypto-related"""
        keywords = [
            'bitcoin', 'btc', 'ethereum', 'eth', 'crypto', 'cryptocurrency',
            'blockchain', 'defi', 'nft', 'web3', 'dao', 'token', 'coin',
            'mining', 'wallet', 'exchange', 'binance', 'coinbase'
        ]
        title_lower = title.lower()
        return any(keyword in title_lower for keyword in keywords)

    def _extract_tokens(self, text):
        """Extract token mentions"""
        tokens = []
        text_lower = text.lower()

        token_patterns = {
            'BTC': r'\b(btc|bitcoin)\b',
            'ETH': r'\b(eth|ethereum)\b',
            'SOL': r'\b(sol|solana)\b',
            'ADA': r'\b(ada|cardano)\b',
        }

        for token, pattern in token_patterns.items():
            if re.search(pattern, text_lower):
                tokens.append(token)

        return tokens

    def _parse_timestamp(self, timestamp_str):
        """Parse timestamp"""
        if not timestamp_str:
            return datetime.utcnow().isoformat()

        try:
            return datetime.fromisoformat(timestamp_str.replace('Z', '+00:00')).isoformat()
        except:
            pass

        return datetime.utcnow().isoformat()
