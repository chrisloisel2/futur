"""
Scrapy settings for scrapers_engine project

Configuration pour un scraping respectueux et efficace.
"""

BOT_NAME = 'scrapers_engine'

SPIDER_MODULES = ['spiders']
NEWSPIDER_MODULE = 'spiders'

# Crawl responsibly
ROBOTSTXT_OBEY = True
CONCURRENT_REQUESTS = 16
DOWNLOAD_DELAY = 2
CONCURRENT_REQUESTS_PER_DOMAIN = 4
CONCURRENT_REQUESTS_PER_IP = 4

# Cookies
COOKIES_ENABLED = True

# Telnet Console disabled for security
TELNETCONSOLE_ENABLED = False

# Override default request headers
DEFAULT_REQUEST_HEADERS = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en,zh,ja,ko',
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
}

# Enable or disable spider middlewares
SPIDER_MIDDLEWARES = {
    'middlewares.MetadataEnrichmentMiddleware': 543,
    'middlewares.ErrorHandlingMiddleware': 544,
}

# Enable or disable downloader middlewares
DOWNLOADER_MIDDLEWARES = {
    # Désactiver les middlewares par défaut
    'scrapy.downloadermiddlewares.useragent.UserAgentMiddleware': None,
    'scrapy.downloadermiddlewares.retry.RetryMiddleware': None,

    # MongoDB-based proxy rotation (nouveau système)
    'middlewares.proxy_rotator_mongodb.MongoDBProxyRotatorMiddleware': 350,
    'middlewares.proxy_rotator_mongodb.UserAgentRotatorMiddleware': 400,
    'middlewares.proxy_rotator_mongodb.HeadersRotatorMiddleware': 410,

    # Retry middleware personnalisé (après les proxies)
    'scrapy.downloadermiddlewares.retry.RetryMiddleware': 550,
}

# Enable or disable extensions
EXTENSIONS = {
    'scrapy.extensions.telnet.TelnetConsole': None,
}

# Configure item pipelines
ITEM_PIPELINES = {
    'pipelines.validation.ValidationPipeline': 100,
    'pipelines.deduplication.DeduplicationPipeline': 200,
    'pipelines.metadata_extraction.MetadataExtractionPipeline': 300,
    'pipelines.s3_unified_pipeline.S3UnifiedPipeline': 350,  # S3 avec architecture Hive (général)
    'pipelines.s3_trading_pipeline.S3TradingPipeline': 360,  # S3 Trading (BTC/ETH/SOL uniquement)
    'pipelines.storage.StoragePipeline': 400,  # Backup local
}

# AutoThrottle extension
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 1
AUTOTHROTTLE_MAX_DELAY = 10
AUTOTHROTTLE_TARGET_CONCURRENCY = 2.0
AUTOTHROTTLE_DEBUG = False

# HTTP Cache
HTTPCACHE_ENABLED = True
HTTPCACHE_EXPIRATION_SECS = 3600  # 1 hour
HTTPCACHE_DIR = 'httpcache'
HTTPCACHE_IGNORE_HTTP_CODES = [500, 502, 503, 504, 408, 429]

# Retry settings
RETRY_ENABLED = True
RETRY_TIMES = 5  # Augmenté pour gérer les proxies défaillants
RETRY_HTTP_CODES = [500, 502, 503, 504, 408, 429, 403, 407]  # Ajout codes proxy

# ============================================================================
# PROXY ROTATION SETTINGS (FREE PROXIES)
# ============================================================================
PROXY_ENABLED = False
MAX_PROXIES = 200  # Nombre maximum de proxies à charger
PROXY_REFRESH_INTERVAL = 300  # Rafraîchir les proxies toutes les 5 minutes
PROXY_ROTATION_MODE = 'random'  # 'random' ou 'sequential'

# Timeouts
DOWNLOAD_TIMEOUT = 30
DNS_TIMEOUT = 10

# Feed exports (output format)
FEEDS = {
    'data/scraped_data_%(time)s.jsonl': {
        'format': 'jsonlines',
        'encoding': 'utf8',
        'store_empty': False,
        'fields': None,
        'indent': None,
        'overwrite': False,
    }
}

# Custom settings
STORAGE_PATH = 'data/raw_articles'
DATABASE_URL = 'sqlite:///data/scraped_articles.db'

# MongoDB Configuration (for VPN storage)
MONGODB_URI = 'mongodb://localhost:27017/'
MONGODB_DATABASE = 'scrapers_db'
MONGODB_VPN_COLLECTION = 'vpn_proxies'

# VPN Testing Configuration (for vpn_mongodb_pipeline_with_test.py)
VPN_TEST_BEFORE_STORE = True  # Enable/disable VPN testing before storage
VPN_TEST_TIMEOUT = 10  # Timeout per VPN test in seconds
VPN_TEST_WORKERS = 50  # Number of concurrent test workers
VPN_TEST_BATCH_SIZE = 100  # Number of VPNs to test per batch

# VPN Auto-Delete Configuration
VPN_DELETE_ON_FAILURE = True  # Delete VPN from database immediately when it fails (recommended)

# AWS S3 Configuration
S3_BUCKET = 'qbia'
S3_PREFIX = 'bourse/raw'
AWS_REGION = 'eu-west-3'
S3_BATCH_SIZE = 100

# Trading-specific S3 Configuration
S3_TRADING_BUCKET = 'qbia'
S3_TRADING_PREFIX = 'bourse/raw'
S3_TRADING_BATCH_SIZE = 100

# Trading: Assets autorisés UNIQUEMENT
ALLOWED_ASSETS = ['BTC', 'ETH', 'SOL']

# Trading: Mots-clés pour détection automatique
ASSET_KEYWORDS = {
    'BTC': [
        'bitcoin', 'btc', 'btcusd', 'btc/usd', 'xbt',
        'btcusdt', 'satoshi', 'sats', 'btceur'
    ],
    'ETH': [
        'ethereum', 'eth', 'ethusd', 'eth/usd', 'ethusdt',
        'ether', 'etheur', 'vitalik'
    ],
    'SOL': [
        'solana', 'sol', 'solusd', 'sol/usd', 'solusdt',
        'soleur', 'solana network'
    ]
}

# Rate limiting per site
SITE_RATE_LIMITS = {
    'whale-alert.io': 1,  # 1 req/sec
    'bitcointalk.org': 0.5,  # 1 req/2sec
    'intel.arkm.com': 1,
    'coindesk.com': 2,
    'cointelegraph.com': 2,
    'default': 1
}

# API Keys (à mettre dans .env en production)
API_KEYS = {
    'newsapi': None,  # Set via environment
    'cryptocompare': None,
}

# Metadata extraction config
EXTRACT_SENTIMENT = True
EXTRACT_ENTITIES = True
EXTRACT_KEYWORDS = True
DETECT_EVENT_TYPE = True

# Logging
LOG_LEVEL = 'INFO'
LOG_FORMAT = '%(asctime)s [%(name)s] %(levelname)s: %(message)s'
LOG_DATEFORMAT = '%Y-%m-%d %H:%M:%S'
