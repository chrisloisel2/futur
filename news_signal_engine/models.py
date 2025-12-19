"""
Structures de données strictes pour le pipeline NEWS
Tout est typé et validé.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional
from enum import Enum


class SourceTier(Enum):
    TIER1_AGENCY = "tier1_agency"
    TIER2_FINANCIAL = "tier2_financial"
    TIER3_SECONDARY = "tier3_secondary"
    TIER4_BLOG = "tier4_blog"
    OFFICIAL = "official"


class EventType(Enum):
    REGULATION = "regulation"
    MONETARY_POLICY = "monetary_policy"
    APPROVAL = "approval"
    REJECTION = "rejection"
    HACK = "hack"
    EXPLOIT = "exploit"
    SANCTION = "sanction"
    LAWSUIT = "lawsuit"
    BANKRUPTCY = "bankruptcy"
    PARTNERSHIP = "partnership"
    MACRO_DATA_RELEASE = "macro_data_release"
    GEOPOLITICAL_CONFLICT = "geopolitical_conflict"
    EXCHANGE_LISTING = "exchange_listing"
    DELISTING = "delisting"
    PROTOCOL_UPGRADE = "protocol_upgrade"
    SECURITY_BREACH = "security_breach"
    FRAUD_ALLEGATION = "fraud_allegation"
    INVESTIGATION = "investigation"


class EventStatus(Enum):
    RUMOR = "rumor"
    LEAK = "leak"
    OFFICIAL_ANNOUNCEMENT = "official_announcement"
    CONFIRMATION = "confirmation"


class SurpriseLevel(Enum):
    EXPECTED = "expected"
    PARTIALLY_EXPECTED = "partially_expected"
    UNEXPECTED = "unexpected"


class GeographicScope(Enum):
    LOCAL = "local"
    REGIONAL = "regional"
    GLOBAL = "global"


@dataclass
class RawNewsArticle:
    """Données brutes collectées depuis NEWS API"""
    article_id: str
    title: str
    body: str
    lang: str
    source: str
    source_url: Optional[str]
    timestamp_publication: datetime
    timestamp_collecte: datetime

    # Métadonnées source
    country: Optional[str] = None
    region: Optional[str] = None
    categories: List[str] = field(default_factory=list)

    # Contenu
    links: List[str] = field(default_factory=list)
    images: List[str] = field(default_factory=list)

    # Updates
    is_updated: bool = False
    is_correction: bool = False
    original_article_id: Optional[str] = None


@dataclass
class ProcessedNewsArticle:
    """Article après filtrage et enrichissement"""
    raw: RawNewsArticle

    # Classification source
    source_tier: SourceTier
    is_official_source: bool

    # Meta-informations calculées
    latency_ms: float
    credibility_score: float
    originality_score: float
    coverage_score: float  # combien de sources indépendantes confirment
    geographic_scope: GeographicScope

    # Analyse sémantique
    detected_entities: List[str] = field(default_factory=list)
    event_types: List[EventType] = field(default_factory=list)
    event_status: EventStatus = EventStatus.OFFICIAL_ANNOUNCEMENT
    surprise_level: SurpriseLevel = SurpriseLevel.EXPECTED

    # Clustering
    event_cluster_id: Optional[str] = None  # articles sur même événement

    # Flags
    passed_filters: bool = True
    rejection_reasons: List[str] = field(default_factory=list)


@dataclass
class NewsWindowAggregation:
    """Agrégation sur fenêtre temporelle"""
    entity: str
    event_type: Optional[EventType]
    window_name: str
    window_seconds: int
    start_time: datetime
    end_time: datetime

    # Métriques
    article_count: int
    unique_sources: int
    independent_source_count: int  # sources tier1/2 uniquement

    # Intensité informationnelle
    information_intensity: float  # volume × credibility
    baseline_7d_avg: float
    acceleration_score: float  # (current - baseline) / std

    # Crédibilité
    avg_credibility: float
    high_credibility_ratio: float

    # Surprise
    surprise_distribution: Dict[str, int] = field(default_factory=dict)
    dominant_surprise_level: SurpriseLevel = SurpriseLevel.EXPECTED

    # Geographic
    geographic_scope: GeographicScope = GeographicScope.LOCAL
    affected_regions: List[str] = field(default_factory=list)

    # Flags
    single_source_risk: bool = False
    late_signal: bool = False
    low_coverage: bool = False


@dataclass
class NewsSignal:
    """Signal final exploitable par moteur de décision"""
    entity: str
    event_type: EventType
    event_status: EventStatus
    surprise_level: SurpriseLevel

    # Fenêtre
    window: str
    timestamp: datetime

    # Intensité et scoring
    information_intensity: float  # 0-1
    credibility_weighted_score: float  # 0-1
    geographic_scope: GeographicScope

    # Confiance
    data_confidence: float  # 0-1
    source_count: int
    independent_source_count: int

    # Warnings
    warning_flags: List[str] = field(default_factory=list)

    def to_json(self) -> Dict:
        """Export strictement JSON sans interprétation"""
        return {
            "entity": self.entity,
            "event_type": self.event_type.value,
            "event_status": self.event_status.value,
            "surprise_level": self.surprise_level.value,
            "window": self.window,
            "timestamp": self.timestamp.isoformat(),
            "information_intensity": round(self.information_intensity, 3),
            "credibility_weighted_score": round(self.credibility_weighted_score, 3),
            "geographic_scope": self.geographic_scope.value,
            "data_confidence": round(self.data_confidence, 3),
            "source_count": self.source_count,
            "independent_source_count": self.independent_source_count,
            "warning_flags": self.warning_flags
        }


@dataclass
class EventCluster:
    """Cluster d'articles sur le même événement"""
    cluster_id: str
    event_type: EventType
    primary_entity: str
    first_seen: datetime
    last_updated: datetime

    # Articles du cluster
    article_ids: List[str] = field(default_factory=list)
    source_count: int = 0

    # Évolution
    status_progression: List[EventStatus] = field(default_factory=list)
    surprise_evolution: List[SurpriseLevel] = field(default_factory=list)


@dataclass
class IngestionStats:
    """Stats système pour monitoring"""
    timestamp: datetime
    articles_collected: int
    articles_filtered: int
    articles_processed: int
    avg_latency_ms: float
    signals_generated: int

    # Par source tier
    tier_breakdown: Dict[str, int] = field(default_factory=dict)

    # Par raison de rejet
    rejection_breakdown: Dict[str, int] = field(default_factory=dict)

    # Par type d'événement
    event_type_breakdown: Dict[str, int] = field(default_factory=dict)
