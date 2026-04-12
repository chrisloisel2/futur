import os
from dotenv import load_dotenv

load_dotenv()

# ── Mongo données ─────────────────────────────────────────────────────────────
MONGO_URI        = os.getenv("MONGO_URI",        "mongodb://admin:admin123@192.168.88.17/")
MONGO_DB         = os.getenv("MONGO_DB",         "market_intel")
MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "signals")

# ── Mongo proxies ─────────────────────────────────────────────────────────────
PROXY_MONGO_URI       = os.getenv("PROXY_MONGO_URI",       "mongodb://admin:admin123@100.93.248.105/")
PROXY_MONGO_DB        = os.getenv("PROXY_MONGO_DB",        "proxy_db")
PROXY_COLLECTION      = os.getenv("PROXY_COLLECTION",      "proxies")
PROXY_REFRESH_INTERVAL = int(os.getenv("PROXY_REFRESH_INTERVAL", "300"))  # secondes

# ── APIs externes ─────────────────────────────────────────────────────────────
BINANCE_BASE_URL       = os.getenv("BINANCE_BASE_URL",       "https://fapi.binance.com")
COINGECKO_BASE_URL     = os.getenv("COINGECKO_BASE_URL",     "https://api.coingecko.com/api/v3")
ALTERNATIVE_ME_BASE_URL = os.getenv("ALTERNATIVE_ME_BASE_URL", "https://api.alternative.me")
FRED_BASE_URL          = os.getenv("FRED_BASE_URL",          "https://api.stlouisfed.org")
NEWSAPI_BASE_URL       = os.getenv("NEWSAPI_BASE_URL",       "https://newsapi.org/v2")

FRED_API_KEY    = os.getenv("FRED_API_KEY",    "")
NEWSAPI_API_KEY = os.getenv("NEWSAPI_API_KEY", "")

# ── HTTP ──────────────────────────────────────────────────────────────────────
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))
USER_AGENT      = os.getenv("USER_AGENT", "marketintel-collector/1.0")
