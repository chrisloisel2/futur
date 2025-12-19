"""
Items definition for scraped data

Structured data models for different types of content
"""

import scrapy
from datetime import datetime
from typing import Optional, List, Dict


class BaseArticleItem(scrapy.Item):
    """Base item for all article types"""
    # Identifiers
    article_id = scrapy.Field()
    url = scrapy.Field()

    # Content
    title = scrapy.Field()
    body = scrapy.Field()
    summary = scrapy.Field()

    # Metadata
    source = scrapy.Field()
    source_tier = scrapy.Field()
    author = scrapy.Field()

    # Timestamps
    published_at = scrapy.Field()
    scraped_at = scrapy.Field()
    updated_at = scrapy.Field()

    # Language and location
    language = scrapy.Field()
    country = scrapy.Field()

    # Categorization
    categories = scrapy.Field()
    tags = scrapy.Field()

    # Rich content
    images = scrapy.Field()
    videos = scrapy.Field()
    links = scrapy.Field()

    # Engagement metrics (if available)
    views = scrapy.Field()
    likes = scrapy.Field()
    comments_count = scrapy.Field()
    shares = scrapy.Field()


class NewsArticleItem(BaseArticleItem):
    """News article with event detection"""
    # Event classification
    event_types = scrapy.Field()  # List[EventType]
    event_status = scrapy.Field()  # rumor, leak, official, confirmation
    surprise_level = scrapy.Field()  # expected, unexpected

    # Entities mentioned
    crypto_entities = scrapy.Field()  # BTC, ETH, etc.
    macro_entities = scrapy.Field()  # Fed, ECB, etc.
    institutional_entities = scrapy.Field()  # BlackRock, etc.
    people_entities = scrapy.Field()  # Names of people

    # Geographic scope
    geographic_scope = scrapy.Field()  # local, regional, global
    affected_regions = scrapy.Field()

    # Quality indicators
    credibility_score = scrapy.Field()
    originality_score = scrapy.Field()
    is_correction = scrapy.Field()
    is_update = scrapy.Field()


class TransactionAlertItem(scrapy.Item):
    """Whale alert / large transaction tracking"""
    # Transaction data
    tx_hash = scrapy.Field()
    blockchain = scrapy.Field()
    amount = scrapy.Field()
    amount_usd = scrapy.Field()
    symbol = scrapy.Field()

    # Addresses
    from_address = scrapy.Field()
    to_address = scrapy.Field()
    from_owner = scrapy.Field()  # Known entity if identified
    to_owner = scrapy.Field()

    # Classification
    transaction_type = scrapy.Field()  # exchange_to_wallet, wallet_to_exchange, etc.

    # Metadata
    timestamp = scrapy.Field()
    scraped_at = scrapy.Field()
    source = scrapy.Field()
    url = scrapy.Field()


class ForumPostItem(scrapy.Item):
    """Forum discussion post"""
    # Identifiers
    post_id = scrapy.Field()
    thread_id = scrapy.Field()
    url = scrapy.Field()

    # Content
    title = scrapy.Field()  # Thread title
    body = scrapy.Field()
    author = scrapy.Field()
    author_rank = scrapy.Field()  # Member rank/reputation
    author_posts_count = scrapy.Field()

    # Metadata
    forum = scrapy.Field()  # bitcointalk, ethereum-magicians, etc.
    board = scrapy.Field()  # Sub-forum/category

    # Timestamps
    posted_at = scrapy.Field()
    scraped_at = scrapy.Field()

    # Engagement
    views = scrapy.Field()
    replies_count = scrapy.Field()
    upvotes = scrapy.Field()

    # Classification
    is_announcement = scrapy.Field()
    is_technical = scrapy.Field()
    is_speculation = scrapy.Field()
    mentioned_tokens = scrapy.Field()


class SocialSentimentItem(scrapy.Item):
    """Social media sentiment data"""
    # Identifiers
    post_id = scrapy.Field()
    platform = scrapy.Field()  # stocktwits, etc.
    url = scrapy.Field()

    # Content
    text = scrapy.Field()
    author = scrapy.Field()
    author_followers = scrapy.Field()

    # Sentiment
    sentiment = scrapy.Field()  # bullish, bearish, neutral
    sentiment_score = scrapy.Field()  # -1 to 1

    # Metadata
    symbols = scrapy.Field()  # Mentioned tickers
    timestamp = scrapy.Field()
    scraped_at = scrapy.Field()

    # Engagement
    likes = scrapy.Field()
    reshares = scrapy.Field()
    replies = scrapy.Field()


class OnChainMetricsItem(scrapy.Item):
    """On-chain metrics and analytics"""
    # Identifiers
    metric_name = scrapy.Field()
    blockchain = scrapy.Field()
    symbol = scrapy.Field()

    # Metrics
    value = scrapy.Field()
    unit = scrapy.Field()
    change_24h = scrapy.Field()
    change_7d = scrapy.Field()

    # Context
    source = scrapy.Field()
    timestamp = scrapy.Field()
    scraped_at = scrapy.Field()

    # Classification
    category = scrapy.Field()  # network_activity, holder_behavior, etc.


class RegulatorAnnouncementItem(scrapy.Item):
    """Regulatory announcements and official statements"""
    # Identifiers
    announcement_id = scrapy.Field()
    url = scrapy.Field()

    # Content
    title = scrapy.Field()
    body = scrapy.Field()

    # Source
    regulator = scrapy.Field()  # SEC, CFTC, etc.
    country = scrapy.Field()
    region = scrapy.Field()

    # Classification
    action_type = scrapy.Field()  # approval, rejection, investigation, fine, etc.
    affected_entities = scrapy.Field()  # Companies/projects affected
    affected_assets = scrapy.Field()  # Crypto assets affected

    # Timestamps
    announcement_date = scrapy.Field()
    effective_date = scrapy.Field()
    scraped_at = scrapy.Field()

    # Impact
    severity = scrapy.Field()  # high, medium, low
    geographic_scope = scrapy.Field()


class VPNProxyItem(scrapy.Item):
    """Free VPN/Proxy for rotation"""
    # Proxy details
    ip = scrapy.Field()
    port = scrapy.Field()
    protocol = scrapy.Field()  # http, https, socks4, socks5
    country = scrapy.Field()
    country_code = scrapy.Field()

    # Full proxy URL
    proxy_url = scrapy.Field()  # http://ip:port

    # Source and metadata
    source = scrapy.Field()  # Where it was scraped from
    scraped_at = scrapy.Field()

    # Performance tracking
    last_checked = scrapy.Field()
    last_success = scrapy.Field()
    success_count = scrapy.Field()
    fail_count = scrapy.Field()
    response_time = scrapy.Field()  # milliseconds

    # Status
    is_active = scrapy.Field()  # True/False
    is_anonymous = scrapy.Field()  # True/False/None

    # Additional info
    anonymity_level = scrapy.Field()  # transparent, anonymous, elite
    speed = scrapy.Field()  # slow, medium, fast
    uptime = scrapy.Field()  # percentage
