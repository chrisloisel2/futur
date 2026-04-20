"""
Configuration stricte pour moteur de signaux NEWS crypto
Tous les seuils sont basés sur des contraintes réalistes.
"""

# === SOURCES TIER CLASSIFICATION ===
SOURCE_TIERS = {
    "tier1": {
        "agencies": [
            "Reuters", "Bloomberg", "Associated Press", "AFP", "Dow Jones",
            "Financial Times", "The Wall Street Journal"
        ],
        "credibility_score": 1.0
    },
    "tier2": {
        "financial_media": [
            "CNBC", "MarketWatch", "Yahoo Finance", "Investing.com",
            "CoinDesk", "The Block", "Decrypt", "Cointelegraph"
        ],
        "credibility_score": 0.8
    },
    "tier3": {
        "secondary_media": [
            "Forbes Crypto", "Business Insider", "Fortune Crypto",
            "TechCrunch", "Benzinga"
        ],
        "credibility_score": 0.6
    },
    "tier4": {
        "blogs": [],  # à compléter
        "credibility_score": 0.3
    }
}

# Sources officielles (priorité absolue)
OFFICIAL_SOURCES = {
    "regulators": [
        "SEC", "CFTC", "FinCEN", "OCC", "FINRA",  # US
        "FCA", "ECB", "BaFin",  # Europe
        "FSA", "JFSA",  # Asie
    ],
    "central_banks": [
        "Federal Reserve", "ECB", "Bank of England", "Bank of Japan",
        "People's Bank of China"
    ],
    "governments": [
        "US Treasury", "European Commission", "UK Treasury"
    ],
    "credibility_score": 1.0
}

# === FILTRAGE DUR ===
MIN_ARTICLE_LENGTH = 100  # caractères minimum
MAX_LATENCY_MS = 300000  # 5 minutes max pour signaux rapides
DUPLICATION_THRESHOLD = 0.85  # similarité cosine

# Types d'articles à rejeter
REJECTED_TYPES = [
    "opinion", "editorial", "tribune", "analysis",
    "sponsored", "advertisement", "promoted"
]

# === ENTITÉS PERTINENTES ===
CRYPTO_ENTITIES = [
    "Bitcoin", "BTC", "Ethereum", "ETH", "Tether", "USDT", "USDC",
    "Solana", "SOL", "XRP", "Ripple", "Cardano", "ADA",
    "Binance", "BNB", "Polygon", "MATIC", "Dogecoin", "DOGE"
]

MACRO_ENTITIES = [
    "Federal Reserve", "Fed", "FOMC", "ECB", "Bank of England",
    "inflation", "CPI", "NFP", "GDP", "interest rate", "rate hike",
    "quantitative easing", "QE", "monetary policy"
]

REGULATORY_ENTITIES = [
    "SEC", "Securities and Exchange Commission",
    "CFTC", "Commodity Futures Trading Commission",
    "ETF", "spot ETF", "Bitcoin ETF",
    "regulation", "compliance", "license", "approval", "rejection"
]

INSTITUTIONAL_ENTITIES = [
    "BlackRock", "Fidelity", "Grayscale", "MicroStrategy",
    "Coinbase", "Kraken", "Gemini", "Binance",
    "JPMorgan", "Goldman Sachs", "Morgan Stanley"
]

GEOPOLITICAL_ENTITIES = [
    "United States", "European Union", "China", "Russia",
    "sanction", "ban", "prohibition", "restriction",
    "war", "conflict", "embargo"
]

ALL_ENTITIES = (
    CRYPTO_ENTITIES + MACRO_ENTITIES + REGULATORY_ENTITIES +
    INSTITUTIONAL_ENTITIES + GEOPOLITICAL_ENTITIES
)

# === EVENT TYPES (classification fermée) ===
EVENT_TYPES = [
    "regulation",
    "monetary_policy",
    "approval",
    "rejection",
    "hack",
    "exploit",
    "sanction",
    "lawsuit",
    "bankruptcy",
    "partnership",
    "macro_data_release",
    "geopolitical_conflict",
    "exchange_listing",
    "delisting",
    "protocol_upgrade",
    "security_breach",
    "fraud_allegation",
    "investigation"
]

# === EVENT STATUS ===
EVENT_STATUS = ["rumor", "leak", "official_announcement", "confirmation"]

# === SURPRISE LEVEL ===
SURPRISE_LEVELS = ["expected", "partially_expected", "unexpected"]

# === GEOGRAPHIC SCOPE ===
GEOGRAPHIC_SCOPES = ["local", "regional", "global"]

# === FENÊTRES TEMPORELLES ===
WINDOWS = {
    "ultra_short": 900,     # 15 minutes
    "short": 3600,          # 1 heure
    "medium": 21600,        # 6 heures
    "long": 86400           # 24 heures
}

# Baseline pour accélération (7 jours)
BASELINE_WINDOW_DAYS = 7

# === AGRÉGATION ===
ACCELERATION_ZSCORE_THRESHOLD = 2.0
MIN_ARTICLES_FOR_SIGNAL = 2
MIN_CREDIBILITY_SCORE = 0.5

# === GARDE-FOUS ===
SINGLE_SOURCE_PENALTY = 0.5
HIGH_LATENCY_PENALTY = 0.6
CORRECTION_PENALTY = 0.7
LATE_SIGNAL_HOURS = 24  # si event > 24h = tardif

# === SCORING ===
# Poids pour originality_score
ORIGINALITY_WEIGHTS = {
    "first_source": 1.0,
    "early_source": 0.7,
    "late_source": 0.3,
    "repost": 0.1
}

# Poids pour coverage_score
MIN_INDEPENDENT_SOURCES = 3  # confirmation robuste

# === NEWS API CONFIGURATION ===
# Exemple: NewsAPI, Newsdata.io, etc.
NEWS_API_RATE_LIMIT = 100  # requêtes/heure
NEWS_SEARCH_LANGUAGES = ["en"]  # anglais uniquement

# === STORAGE ===
RAW_NEWS_RETENTION_HOURS = 168  # 7 jours
SIGNALS_RETENTION_DAYS = 90
