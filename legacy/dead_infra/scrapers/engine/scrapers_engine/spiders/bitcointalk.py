"""
BitcoinTalk forum spider
https://bitcointalk.org/
"""

import scrapy
from datetime import datetime
from items import ForumPostItem
import re


class BitcoinTalkSpider(scrapy.Spider):
    name = 'bitcointalk'
    allowed_domains = ['bitcointalk.org']

    # Start with key boards
    start_urls = [
        'https://bitcointalk.org/index.php?board=1.0',  # Bitcoin Discussion
        'https://bitcointalk.org/index.php?board=5.0',  # Altcoin Discussion
        'https://bitcointalk.org/index.php?board=159.0',  # Trading Discussion
        'https://bitcointalk.org/index.php?board=7.0',  # Technical Discussion
    ]

    custom_settings = {
        'DOWNLOAD_DELAY': 3,  # Be respectful
        'CONCURRENT_REQUESTS_PER_DOMAIN': 1,
        'DEPTH_LIMIT': 2,  # Don't go too deep
    }

    def parse(self, response):
        """Parse board listing"""
        # Extract threads from board
        threads = response.css('tr.windowbg, tr.windowbg2')

        for thread in threads:
            # Get thread link
            thread_link = thread.css('span[id^="msg_"] a::attr(href)').get()
            if thread_link:
                yield response.follow(thread_link, callback=self.parse_thread)

        # Follow pagination (first 3 pages only)
        next_page = response.css('a.navPages::attr(href)').get()
        current_page = self._get_page_number(response.url)
        if next_page and current_page < 3:
            yield response.follow(next_page, callback=self.parse)

    def parse_thread(self, response):
        """Parse thread page"""
        # Extract posts from thread
        posts = response.css('div.post')

        thread_title = response.css('h1::text, #top_subject::text').get()
        if not thread_title:
            thread_title = response.css('title::text').get() or 'Unknown Thread'

        for post in posts:
            item = ForumPostItem()

            # Thread info
            item['title'] = thread_title.strip()
            item['url'] = response.url
            item['forum'] = 'BitcoinTalk'

            # Extract board name from breadcrumbs
            breadcrumbs = response.css('.nav a::text').getall()
            item['board'] = breadcrumbs[-1] if breadcrumbs else 'Unknown'

            # Post ID
            post_id = post.css('::attr(id)').get()
            item['post_id'] = post_id or ''
            item['thread_id'] = self._extract_thread_id(response.url)

            # Author info
            author = post.css('.poster h4 a::text').get()
            item['author'] = author.strip() if author else 'Anonymous'

            # Author rank
            rank = post.css('.poster li.title::text, .poster .postgroup::text').get()
            item['author_rank'] = rank.strip() if rank else 'Member'

            # Author post count
            posts_text = post.css('.poster li.postcount::text').get()
            item['author_posts_count'] = self._extract_number(posts_text)

            # Post body
            body = post.css('.post .inner::text, .postarea .post::text').getall()
            item['body'] = ' '.join(body).strip()

            # Skip if body is too short
            if len(item['body']) < 20:
                continue

            # Timestamp
            timestamp_text = post.css('.smalltext::text').get()
            item['posted_at'] = self._parse_timestamp(timestamp_text)
            item['scraped_at'] = datetime.utcnow().isoformat()

            # Engagement (not available per post, but we can check thread stats)
            item['views'] = None
            item['replies_count'] = None
            item['upvotes'] = None

            # Classification
            item['is_announcement'] = self._is_announcement(thread_title)
            item['is_technical'] = self._is_technical(thread_title, item['body'])
            item['is_speculation'] = self._is_speculation(thread_title, item['body'])

            # Extract mentioned tokens
            item['mentioned_tokens'] = self._extract_tokens(f"{thread_title} {item['body']}")

            yield item

    def _get_page_number(self, url):
        """Extract page number from URL"""
        match = re.search(r'\.(\d+)$', url)
        return int(match.group(1)) / 20 if match else 0

    def _extract_thread_id(self, url):
        """Extract thread ID from URL"""
        match = re.search(r'topic=(\d+)', url)
        return match.group(1) if match else ''

    def _extract_number(self, text):
        """Extract number from text"""
        if not text:
            return None
        match = re.search(r'(\d+)', text)
        return int(match.group(1)) if match else None

    def _parse_timestamp(self, timestamp_text):
        """Parse BitcoinTalk timestamp"""
        if not timestamp_text:
            return datetime.utcnow().isoformat()

        # BitcoinTalk uses formats like "Today at 01:23:45 PM" or "December 15, 2023, 03:45:12 PM"
        # For simplicity, we'll return current time for "Today"
        if 'today' in timestamp_text.lower():
            return datetime.utcnow().isoformat()

        return datetime.utcnow().isoformat()

    def _is_announcement(self, title):
        """Check if thread is an announcement"""
        keywords = ['announcement', 'ann', 'official', 'release']
        return any(kw in title.lower() for kw in keywords)

    def _is_technical(self, title, body):
        """Check if discussion is technical"""
        text = f"{title} {body}".lower()
        keywords = ['technical', 'code', 'implementation', 'protocol', 'consensus', 'node', 'mining']
        return sum(kw in text for kw in keywords) >= 2

    def _is_speculation(self, title, body):
        """Check if discussion is about price speculation"""
        text = f"{title} {body}".lower()
        keywords = ['price', 'prediction', 'bull', 'bear', 'moon', 'crash', 'pump', 'dump', 'trading']
        return sum(kw in text for kw in keywords) >= 2

    def _extract_tokens(self, text):
        """Extract cryptocurrency token mentions"""
        tokens = []
        text_lower = text.lower()

        # Common tokens
        token_patterns = {
            'BTC': r'\b(btc|bitcoin)\b',
            'ETH': r'\b(eth|ethereum)\b',
            'XRP': r'\bxrp\b',
            'ADA': r'\b(ada|cardano)\b',
            'SOL': r'\b(sol|solana)\b',
            'DOGE': r'\b(doge|dogecoin)\b',
        }

        for token, pattern in token_patterns.items():
            if re.search(pattern, text_lower):
                tokens.append(token)

        return tokens
