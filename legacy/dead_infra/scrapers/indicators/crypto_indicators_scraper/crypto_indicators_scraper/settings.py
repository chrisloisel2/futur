"""
Scrapy settings for crypto_indicators_scraper project.

Optimized for high-performance web scraping with proxy rotation.
"""

BOT_NAME = 'crypto_indicators_scraper'

SPIDER_MODULES = ['crypto_indicators_scraper.spiders']
NEWSPIDER_MODULE = 'crypto_indicators_scraper.spiders'

# Crawl responsibly by identifying yourself
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

# Obey robots.txt rules
ROBOTSTXT_OBEY = False

# Configure maximum concurrent requests performed by Scrapy
CONCURRENT_REQUESTS = 32
CONCURRENT_REQUESTS_PER_DOMAIN = 8
CONCURRENT_REQUESTS_PER_IP = 8

# Configure a delay for requests for the same website
DOWNLOAD_DELAY = 0.5
RANDOMIZE_DOWNLOAD_DELAY = True

# Disable cookies (enabled by default)
COOKIES_ENABLED = False

# Disable Telnet Console (enabled by default)
TELNETCONSOLE_ENABLED = False

# Override the default request headers
DEFAULT_REQUEST_HEADERS = {
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}

# Enable or disable spider middlewares
SPIDER_MIDDLEWARES = {
    'crypto_indicators_scraper.middlewares.proxy_middleware.ProxyRotationMiddleware': 543,
}

# Enable or disable downloader middlewares
DOWNLOADER_MIDDLEWARES = {
    'crypto_indicators_scraper.middlewares.proxy_middleware.ProxyRotationMiddleware': 350,
    'crypto_indicators_scraper.middlewares.proxy_middleware.UserAgentRotationMiddleware': 400,
    'scrapy.downloadermiddlewares.useragent.UserAgentMiddleware': None,
    'scrapy.downloadermiddlewares.retry.RetryMiddleware': 550,
    'scrapy.downloadermiddlewares.httpproxy.HttpProxyMiddleware': None,
}

# Configure item pipelines
ITEM_PIPELINES = {
    'crypto_indicators_scraper.pipelines.s3_pipeline.ValidationPipeline': 100,
    'crypto_indicators_scraper.pipelines.s3_pipeline.CalculatedIndicatorsPipeline': 200,
    'crypto_indicators_scraper.pipelines.s3_pipeline.S3IndicatorsPipeline': 300,
}

# Enable and configure the AutoThrottle extension
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 0.5
AUTOTHROTTLE_MAX_DELAY = 10
AUTOTHROTTLE_TARGET_CONCURRENCY = 2.0
AUTOTHROTTLE_DEBUG = False

# Enable and configure HTTP caching
HTTPCACHE_ENABLED = False
HTTPCACHE_EXPIRATION_SECS = 0
HTTPCACHE_DIR = 'httpcache'
HTTPCACHE_IGNORE_HTTP_CODES = [500, 502, 503, 504, 400, 403, 404, 408]
HTTPCACHE_STORAGE = 'scrapy.extensions.httpcache.FilesystemCacheStorage'

# Retry settings
RETRY_ENABLED = True
RETRY_TIMES = 5
RETRY_HTTP_CODES = [500, 502, 503, 504, 400, 403, 404, 408, 429]

# Download timeout
DOWNLOAD_TIMEOUT = 30

# AWS S3 Settings
S3_BUCKET = 'qbia'
S3_INDICATORS_PREFIX = 'bourse/indicators'
S3_BATCH_SIZE = 1000
AWS_REGION = 'us-east-1'

# Proxy Settings
PROXY_ROTATION_ENABLED = True
PROXY_SOURCES = [
    'free_proxy_list',
    'proxy_scrape',
    'geonode',
    # You can add custom proxy sources:
    # 'file:///path/to/proxies.txt',
    # 'https://yourproxyapi.com/list',
]

# API Keys (can also be set via environment variables)
CRYPTOCOMPARE_API_KEY = ''  # Get from: https://min-api.cryptocompare.com/
TAAPI_API_KEY = ''  # Get from: https://taapi.io/
TWELVEDATA_API_KEY = ''  # Get from: https://twelvedata.com/

# Logging
LOG_LEVEL = 'INFO'
LOG_FORMAT = '%(asctime)s [%(name)s] %(levelname)s: %(message)s'
LOG_DATEFORMAT = '%Y-%m-%d %H:%M:%S'

# Memory optimization
MEMUSAGE_ENABLED = True
MEMUSAGE_LIMIT_MB = 2048
MEMUSAGE_WARNING_MB = 1024

# Stats
STATS_DUMP = True

# Request fingerprinter
REQUEST_FINGERPRINTER_IMPLEMENTATION = '2.7'

# Twisted reactor
TWISTED_REACTOR = 'twisted.internet.asyncioreactor.AsyncioSelectorReactor'

# Feed exports (optional - for debugging)
FEEDS = {
    # 'output/indicators_%(time)s.json': {
    #     'format': 'json',
    #     'encoding': 'utf8',
    #     'indent': 2,
    # },
}
