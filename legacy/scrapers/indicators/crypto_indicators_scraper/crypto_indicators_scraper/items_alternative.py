"""
Items for alternative/abstract data sources (sentiment, geopolitics, trends, etc.)
"""
import scrapy


class SentimentDataItem(scrapy.Item):
    """Item for social media sentiment and engagement data."""

    # Identification
    symbol = scrapy.Field()
    timestamp = scrapy.Field()
    source = scrapy.Field()  # twitter, reddit, telegram, etc.

    # Twitter/X Metrics
    tweet_volume = scrapy.Field()
    positive_tweets = scrapy.Field()
    negative_tweets = scrapy.Field()
    neutral_tweets = scrapy.Field()
    sentiment_score = scrapy.Field()  # -1 to 1
    retweet_count = scrapy.Field()
    like_count = scrapy.Field()
    reply_count = scrapy.Field()
    influencer_mentions = scrapy.Field()
    top_hashtags = scrapy.Field()

    # Reddit Metrics
    reddit_posts = scrapy.Field()
    reddit_comments = scrapy.Field()
    reddit_upvotes = scrapy.Field()
    reddit_sentiment = scrapy.Field()
    subreddit_activity = scrapy.Field()

    # Telegram Metrics
    telegram_messages = scrapy.Field()
    telegram_members_growth = scrapy.Field()
    telegram_engagement = scrapy.Field()

    # General Sentiment
    fear_greed_index = scrapy.Field()  # 0-100
    social_dominance = scrapy.Field()  # % of social volume
    social_volume_change = scrapy.Field()  # % change

    # Metadata
    scraped_at = scrapy.Field()


class GeopoliticalEventItem(scrapy.Item):
    """Item for geopolitical events and news."""

    # Identification
    timestamp = scrapy.Field()
    source = scrapy.Field()  # news_api, gdelt, etc.

    # Event Details
    event_type = scrapy.Field()  # regulation, ban, adoption, conflict, etc.
    country = scrapy.Field()
    region = scrapy.Field()
    severity = scrapy.Field()  # 1-10
    impact_score = scrapy.Field()  # Predicted impact on crypto

    # Event Description
    title = scrapy.Field()
    description = scrapy.Field()
    keywords = scrapy.Field()
    entities = scrapy.Field()  # Countries, organizations mentioned

    # Regulation Specific
    regulation_type = scrapy.Field()  # ban, tax, legal_tender, etc.
    affected_cryptos = scrapy.Field()

    # Sentiment
    news_sentiment = scrapy.Field()  # -1 to 1
    tone = scrapy.Field()  # positive, negative, neutral

    # Metadata
    url = scrapy.Field()
    scraped_at = scrapy.Field()


class TrendDataItem(scrapy.Item):
    """Item for trend and search data."""

    # Identification
    symbol = scrapy.Field()
    timestamp = scrapy.Field()
    source = scrapy.Field()  # google_trends, youtube, etc.

    # Google Trends
    search_volume = scrapy.Field()
    search_volume_change = scrapy.Field()  # % change
    rising_queries = scrapy.Field()
    top_queries = scrapy.Field()
    regional_interest = scrapy.Field()  # By country

    # YouTube
    video_count = scrapy.Field()
    view_count = scrapy.Field()
    positive_videos = scrapy.Field()
    negative_videos = scrapy.Field()

    # News Coverage
    news_articles_count = scrapy.Field()
    mainstream_media_mentions = scrapy.Field()
    news_sentiment = scrapy.Field()

    # Metadata
    scraped_at = scrapy.Field()


class MacroEconomicDataItem(scrapy.Item):
    """Item for macro-economic indicators."""

    # Identification
    timestamp = scrapy.Field()
    source = scrapy.Field()  # fred, world_bank, imf, etc.

    # US Economic Indicators
    fed_rate = scrapy.Field()
    inflation_rate = scrapy.Field()
    unemployment_rate = scrapy.Field()
    gdp_growth = scrapy.Field()
    m2_money_supply = scrapy.Field()
    dollar_index = scrapy.Field()  # DXY

    # Global Markets
    sp500 = scrapy.Field()
    nasdaq = scrapy.Field()
    gold_price = scrapy.Field()
    oil_price = scrapy.Field()
    vix_index = scrapy.Field()  # Fear index

    # Crypto-specific Macro
    total_market_cap = scrapy.Field()
    btc_dominance = scrapy.Field()
    stable_coin_supply = scrapy.Field()
    exchange_reserves = scrapy.Field()

    # Correlations
    btc_sp500_correlation = scrapy.Field()
    btc_gold_correlation = scrapy.Field()
    btc_dollar_correlation = scrapy.Field()

    # Metadata
    scraped_at = scrapy.Field()


class OnChainDataItem(scrapy.Item):
    """Item for on-chain metrics."""

    # Identification
    symbol = scrapy.Field()
    timestamp = scrapy.Field()
    source = scrapy.Field()  # glassnode, santiment, etc.

    # Network Activity
    active_addresses = scrapy.Field()
    new_addresses = scrapy.Field()
    transaction_count = scrapy.Field()
    transaction_volume = scrapy.Field()
    avg_transaction_value = scrapy.Field()

    # Holder Behavior
    exchange_inflow = scrapy.Field()
    exchange_outflow = scrapy.Field()
    exchange_net_flow = scrapy.Field()
    whale_transactions = scrapy.Field()  # > $100k
    supply_on_exchanges = scrapy.Field()

    # HODLer Metrics
    supply_last_active_1y = scrapy.Field()
    long_term_holder_supply = scrapy.Field()
    realized_cap = scrapy.Field()
    mvrv_ratio = scrapy.Field()  # Market Value to Realized Value

    # Mining/Staking
    hash_rate = scrapy.Field()
    mining_difficulty = scrapy.Field()
    miner_revenue = scrapy.Field()
    staking_ratio = scrapy.Field()

    # Derivatives
    futures_open_interest = scrapy.Field()
    futures_funding_rate = scrapy.Field()
    options_put_call_ratio = scrapy.Field()
    liquidations_long = scrapy.Field()
    liquidations_short = scrapy.Field()

    # Metadata
    scraped_at = scrapy.Field()


class DeFiMetricsItem(scrapy.Item):
    """Item for DeFi protocol metrics."""

    # Identification
    protocol = scrapy.Field()  # uniswap, aave, compound, etc.
    timestamp = scrapy.Field()
    source = scrapy.Field()  # defillama, dune, etc.

    # TVL Metrics
    tvl_usd = scrapy.Field()
    tvl_change_24h = scrapy.Field()
    tvl_change_7d = scrapy.Field()

    # Volume Metrics
    volume_24h = scrapy.Field()
    volume_7d = scrapy.Field()
    fees_24h = scrapy.Field()
    revenue_24h = scrapy.Field()

    # User Metrics
    unique_users = scrapy.Field()
    user_growth = scrapy.Field()
    retention_rate = scrapy.Field()

    # Token Metrics
    token_price = scrapy.Field()
    circulating_supply = scrapy.Field()
    market_cap = scrapy.Field()

    # Metadata
    scraped_at = scrapy.Field()


class WhaleAlertItem(scrapy.Item):
    """Item for whale transactions and large movements."""

    # Identification
    timestamp = scrapy.Field()
    source = scrapy.Field()  # whale_alert, etc.

    # Transaction Details
    symbol = scrapy.Field()
    amount = scrapy.Field()
    amount_usd = scrapy.Field()
    transaction_type = scrapy.Field()  # transfer, exchange_deposit, etc.

    # From/To
    from_address = scrapy.Field()
    from_owner = scrapy.Field()  # If known (exchange, whale name)
    to_address = scrapy.Field()
    to_owner = scrapy.Field()

    # Transaction Hash
    tx_hash = scrapy.Field()
    blockchain = scrapy.Field()

    # Metadata
    scraped_at = scrapy.Field()


class InfluencerDataItem(scrapy.Item):
    """Item for crypto influencer activity and predictions."""

    # Identification
    influencer_name = scrapy.Field()
    platform = scrapy.Field()  # twitter, youtube, etc.
    timestamp = scrapy.Field()
    source = scrapy.Field()

    # Influencer Metrics
    follower_count = scrapy.Field()
    engagement_rate = scrapy.Field()
    influence_score = scrapy.Field()  # 1-100

    # Content
    post_content = scrapy.Field()
    mentioned_symbols = scrapy.Field()
    sentiment = scrapy.Field()
    prediction = scrapy.Field()  # bullish, bearish, neutral

    # Engagement
    likes = scrapy.Field()
    shares = scrapy.Field()
    comments = scrapy.Field()
    views = scrapy.Field()

    # Metadata
    post_url = scrapy.Field()
    scraped_at = scrapy.Field()
