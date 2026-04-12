import scrapy


class SignalItem(scrapy.Item):
    source = scrapy.Field()
    source_type = scrapy.Field()      # news, macro, market, onchain, sentiment
    asset = scrapy.Field()            # BTC, ETH, SOL, TOTAL, MACRO, etc.
    title = scrapy.Field()
    text = scrapy.Field()
    url = scrapy.Field()
    author = scrapy.Field()
    published_at = scrapy.Field()
    scraped_at = scrapy.Field()
    language = scrapy.Field()

    event_type = scrapy.Field()
    sentiment = scrapy.Field()
    importance = scrapy.Field()
    confidence = scrapy.Field()

    feature_name = scrapy.Field()
    value = scrapy.Field()
    unit = scrapy.Field()

    metadata = scrapy.Field()
    raw = scrapy.Field()
    fingerprint = scrapy.Field()
