import os

BOT_NAME = "marketintel"

SPIDER_MODULES = ["marketintel.spiders"]
NEWSPIDER_MODULE = "marketintel.spiders"

ROBOTSTXT_OBEY = False

CONCURRENT_REQUESTS = 8
DOWNLOAD_DELAY = 1.5
RANDOMIZE_DOWNLOAD_DELAY = True

RETRY_ENABLED = True
RETRY_TIMES = 5
RETRY_HTTP_CODES = [403, 407, 408, 429, 500, 502, 503, 504]

AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 1.0
AUTOTHROTTLE_MAX_DELAY = 20.0
AUTOTHROTTLE_TARGET_CONCURRENCY = 2.0

DEFAULT_REQUEST_HEADERS = {
    "Accept": "application/json, text/html, application/xml;q=0.9, */*;q=0.8",
}

ITEM_PIPELINES = {
    "marketintel.pipelines.MongoPipeline": 300,
}

DOWNLOADER_MIDDLEWARES = {
    # Désactiver le middleware UA natif Scrapy
    "scrapy.downloadermiddlewares.useragent.UserAgentMiddleware": None,
    # Proxy MongoDB
    "marketintel.middlewares.MongoProxyMiddleware": 350,
    # User-Agent aléatoire
    "marketintel.middlewares.RandomUserAgentMiddleware": 400,
    # Headers réalistes
    "marketintel.middlewares.HeadersRotatorMiddleware": 410,
}

# ── Mongo données ─────────────────────────────────────────────────────────────
MONGO_URI      = os.getenv("MONGO_URI",      "mongodb://admin:admin123@192.168.88.17/")
MONGO_DATABASE = os.getenv("MONGO_DATABASE", "market_intel")

# ── Mongo proxies ─────────────────────────────────────────────────────────────
PROXY_ENABLED          = True
PROXY_MONGO_URI        = os.getenv("PROXY_MONGO_URI",        "mongodb://admin:admin123@100.93.248.105/")
PROXY_MONGO_DB         = os.getenv("PROXY_MONGO_DB",         "proxy_db")
PROXY_COLLECTION       = os.getenv("PROXY_COLLECTION",       "proxies")
PROXY_REFRESH_INTERVAL = int(os.getenv("PROXY_REFRESH_INTERVAL", "300"))
