import scrapy


class CryptoIndicatorItem(scrapy.Item):
    """Item for storing crypto market indicators."""

    # Identification
    symbol = scrapy.Field()
    timestamp = scrapy.Field()
    timeframe = scrapy.Field()  # 1m, 5m, 15m, etc.
    source = scrapy.Field()  # tradingview, cryptocompare, etc.

    # Price data
    open = scrapy.Field()
    high = scrapy.Field()
    low = scrapy.Field()
    close = scrapy.Field()
    volume = scrapy.Field()

    # Moving Averages
    sma_7 = scrapy.Field()
    sma_25 = scrapy.Field()
    sma_99 = scrapy.Field()
    ema_7 = scrapy.Field()
    ema_25 = scrapy.Field()
    ema_99 = scrapy.Field()

    # Momentum Indicators
    rsi = scrapy.Field()
    rsi_14 = scrapy.Field()
    stoch_k = scrapy.Field()
    stoch_d = scrapy.Field()
    macd = scrapy.Field()
    macd_signal = scrapy.Field()
    macd_histogram = scrapy.Field()

    # Volatility Indicators
    atr = scrapy.Field()
    bollinger_upper = scrapy.Field()
    bollinger_middle = scrapy.Field()
    bollinger_lower = scrapy.Field()

    # Volume Indicators
    volume_sma = scrapy.Field()
    obv = scrapy.Field()  # On-Balance Volume

    # Trend Indicators
    adx = scrapy.Field()
    cci = scrapy.Field()  # Commodity Channel Index

    # Support/Resistance
    pivot_point = scrapy.Field()
    resistance_1 = scrapy.Field()
    resistance_2 = scrapy.Field()
    support_1 = scrapy.Field()
    support_2 = scrapy.Field()

    # Market Sentiment
    fear_greed_index = scrapy.Field()
    buy_sell_ratio = scrapy.Field()

    # Metadata
    scraped_at = scrapy.Field()
