"""
Structures de données strictes pour le pipeline Twitter
Pas de données libres, tout est typé et validé.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional
from enum import Enum


class SentimentDirection(Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class InformationType(Enum):
    RUMOR = "rumor"
    ANNOUNCEMENT = "announcement"
    CONFIRMATION = "confirmation"
    OPINION = "opinion"


class CertaintyLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class RawTweet:
    """Données brutes collectées depuis Twitter API"""
    tweet_id: str
    text: str
    lang: str
    timestamp_publication: datetime
    timestamp_collecte: datetime

    # Author info
    author_id: str
    followers_count: int
    following_count: int
    account_created_at: datetime
    verified: bool

    # Engagement
    impressions: Optional[int] = None
    likes: int = 0
    retweets: int = 0
    replies: int = 0

    # Content metadata
    has_media: bool = False
    has_links: bool = False
    hashtags: List[str] = field(default_factory=list)
    cashtags: List[str] = field(default_factory=list)

    # Context
    is_quote: bool = False
    is_reply: bool = False
    quoted_tweet_id: Optional[str] = None
    reply_to_tweet_id: Optional[str] = None


@dataclass
class ProcessedTweet:
    """Tweet après filtrage et enrichissement"""
    raw: RawTweet

    # Meta-informations calculées
    latency_ms: float
    engagement_velocity: float
    reach_proxy: float
    account_age_days: int
    author_credibility_score: float
    bot_probability_score: float
    duplication_cluster_id: Optional[str] = None

    # Analyse sémantique
    detected_entities: List[str] = field(default_factory=list)
    sentiment_direction: SentimentDirection = SentimentDirection.NEUTRAL
    sentiment_strength: float = 0.0  # 0-1
    information_type: InformationType = InformationType.OPINION
    certainty_level: CertaintyLevel = CertaintyLevel.LOW

    # Flags
    passed_filters: bool = True
    rejection_reasons: List[str] = field(default_factory=list)


@dataclass
class WindowAggregation:
    """Agrégation sur fenêtre temporelle"""
    entity: str
    window_name: str  # "short", "medium", "long"
    window_seconds: int
    start_time: datetime
    end_time: datetime

    # Métriques
    tweet_count: int
    unique_authors: int

    # Sentiment agrégé
    sentiment_direction: SentimentDirection
    sentiment_strength: float  # moyenne pondérée par credibility
    sentiment_dispersion: float  # std du sentiment

    # Attention
    total_reach: float
    avg_engagement_velocity: float
    baseline_7d_avg: float
    burst_score: float  # (current - baseline) / std

    # Crédibilité
    avg_credibility: float
    high_credibility_ratio: float  # % tweets avec cred > seuil

    # Flags
    bot_risk: bool = False
    manipulation_risk: bool = False
    low_coverage: bool = False


@dataclass
class TradingSignal:
    """Signal final exploitable par moteur de décision"""
    entity: str
    window: str
    timestamp: datetime

    # Direction et force
    sentiment_direction: SentimentDirection
    sentiment_strength: float  # 0-1

    # Attention anomale
    attention_burst: float  # 0-1, normalisé

    # Score composite
    credibility_weighted_score: float  # 0-1

    # Confiance
    data_confidence: float  # 0-1, basé sur couverture + latence

    # Warnings
    warning_flags: List[str] = field(default_factory=list)

    def to_json(self) -> Dict:
        """Export strictement JSON sans interprétation"""
        return {
            "entity": self.entity,
            "window": self.window,
            "timestamp": self.timestamp.isoformat(),
            "sentiment_direction": self.sentiment_direction.value,
            "sentiment_strength": round(self.sentiment_strength, 3),
            "attention_burst": round(self.attention_burst, 3),
            "credibility_weighted_score": round(self.credibility_weighted_score, 3),
            "data_confidence": round(self.data_confidence, 3),
            "warning_flags": self.warning_flags
        }


@dataclass
class IngestionStats:
    """Stats système pour monitoring"""
    timestamp: datetime
    tweets_collected: int
    tweets_filtered: int
    tweets_processed: int
    avg_latency_ms: float
    api_calls_used: int
    signals_generated: int

    # Par raison de rejet
    rejection_breakdown: Dict[str, int] = field(default_factory=dict)
