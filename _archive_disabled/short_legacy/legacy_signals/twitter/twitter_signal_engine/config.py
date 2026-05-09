"""
Configuration stricte pour moteur de signaux Twitter/X crypto
Tous les seuils sont basés sur des contraintes réalistes, pas d'optimisation prématurée.
"""

# === FILTRAGE DUR ===
ACCOUNT_MIN_AGE_DAYS = 90
ACCOUNT_MIN_FOLLOWERS = 1000
SPAM_REPETITION_THRESHOLD = 0.7  # similarité cosine

# === WHITELIST PRIORITAIRE ===
# Comptes à tracker en priorité (à compléter manuellement)
PRIORITY_ACCOUNTS = {
    # Institutionnels
    "institutions": [],
    # Journalistes crypto reconnus
    "journalists": [],
    # Traders avec historique public
    "traders": [],
    # Exchanges et régulateurs officiels
    "official": []
}

# === ENTITÉS PERTINENTES ===
CRYPTO_ENTITIES = ["BTC", "ETH", "USDT", "USDC", "SOL", "XRP", "BNB", "ADA", "DOGE", "MATIC"]
MACRO_ENTITIES = ["FED", "ECB", "FOMC", "CPI", "NFP", "GDP"]
REGULATORY_ENTITIES = ["SEC", "CFTC", "ETF", "regulation", "approval", "sanctions"]
EVENT_ENTITIES = ["hack", "breach", "exploit", "halving", "merge", "fork"]

ALL_ENTITIES = CRYPTO_ENTITIES + MACRO_ENTITIES + REGULATORY_ENTITIES + EVENT_ENTITIES

# === MÉTA-INFORMATIONS ===
MAX_LATENCY_MS = 30000  # 30 secondes max pour signaux court-terme
BOT_PROBABILITY_THRESHOLD = 0.6
DUPLICATION_SIMILARITY_THRESHOLD = 0.85

# === FENÊTRES TEMPORELLES ===
WINDOWS = {
    "short": 300,      # 5 minutes
    "medium": 1800,    # 30 minutes
    "long": 7200       # 2 heures
}

# Baseline pour burst detection (7 jours glissants)
BASELINE_WINDOW_DAYS = 7

# === AGRÉGATION ===
BURST_ZSCORE_THRESHOLD = 2.0  # anomalie si > 2σ
MIN_TWEETS_FOR_SIGNAL = 5     # minimum pour considérer un signal
MIN_CREDIBILITY_SCORE = 0.4   # seuil de crédibilité minimale

# === GARDE-FOUS ===
HIGH_LATENCY_PENALTY = 0.5     # down-weight si latency > seuil
LOW_CREDIBILITY_FLAG = 0.3     # flag si crédibilité < seuil
HIGH_DISPERSION_THRESHOLD = 0.6  # si std(sentiment) > seuil → neutral
EXTREME_SENTIMENT_MIN_VOLUME = 20  # tweets min pour sentiment extrême

# === SCORING ===
# Poids pour calcul credibility_score
CREDIBILITY_WEIGHTS = {
    "followers": 0.2,
    "account_age": 0.15,
    "verified": 0.2,
    "engagement_history": 0.25,
    "whitelist": 0.2
}

# Poids pour calcul engagement_velocity
ENGAGEMENT_WEIGHTS = {
    "likes": 1.0,
    "retweets": 2.0,    # retweets = plus d'impact
    "replies": 0.5
}

# === API RATE LIMITS (Twitter API v2) ===
RATE_LIMIT_REQUESTS_PER_15MIN = 450  # pour Academic Research access
RATE_LIMIT_TWEETS_PER_REQUEST = 100

# === STORAGE ===
RAW_DATA_RETENTION_HOURS = 48
SIGNALS_RETENTION_DAYS = 30
