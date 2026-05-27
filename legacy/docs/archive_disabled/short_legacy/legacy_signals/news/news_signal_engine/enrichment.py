"""
Calcul des méta-informations critiques pour chaque article NEWS.
Latence, crédibilité, originalité, couverture.
"""

import hashlib
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import numpy as np

from models import RawNewsArticle, ProcessedNewsArticle, SourceTier, GeographicScope
from config import ORIGINALITY_WEIGHTS, MIN_INDEPENDENT_SOURCES


class NewsEnricher:
    """Enrichissement des articles avec méta-informations"""

    def __init__(self):
        # Cache pour tracking couverture événements
        self.event_coverage = {}  # event_hash -> [source_tier, timestamp]

        # Cache originalité
        self.first_reports = {}  # event_hash -> timestamp

    def enrich(self, raw: RawNewsArticle, source_tier: SourceTier, is_official: bool) -> ProcessedNewsArticle:
        """Calcul de toutes les méta-informations"""

        from filters import SourceClassifier

        latency_ms = self._calculate_latency(raw)
        credibility_score = SourceClassifier.get_credibility_score(source_tier, is_official)

        # Event hash pour tracking
        event_hash = self._calculate_event_hash(raw)

        originality_score = self._calculate_originality(raw, event_hash)
        coverage_score = self._calculate_coverage(event_hash, source_tier)

        return ProcessedNewsArticle(
            raw=raw,
            source_tier=source_tier,
            is_official_source=is_official,
            latency_ms=latency_ms,
            credibility_score=credibility_score,
            originality_score=originality_score,
            coverage_score=coverage_score,
            geographic_scope=GeographicScope.LOCAL  # sera mis à jour
        )

    def _calculate_latency(self, article: RawNewsArticle) -> float:
        """Latence collecte - publication en ms"""
        delta = article.timestamp_collecte - article.timestamp_publication
        return delta.total_seconds() * 1000

    def _calculate_event_hash(self, article: RawNewsArticle) -> str:
        """
        Hash de l'événement pour tracking couverture
        Basé sur entités principales + date
        """
        # Normaliser titre
        title_normalized = article.title.lower().strip()

        # Extraire tokens principaux
        import re
        tokens = re.findall(r'\b[a-z]{4,}\b', title_normalized)

        # Top 5 tokens + date publication
        key_tokens = sorted(tokens)[:5]
        date_str = article.timestamp_publication.strftime('%Y%m%d')

        hash_input = " ".join(key_tokens) + date_str
        return hashlib.md5(hash_input.encode()).hexdigest()[:16]

    def _calculate_originality(self, article: RawNewsArticle, event_hash: str) -> float:
        """
        Score d'originalité [0-1]
        1.0 = premier à reporter
        0.1 = reprise tardive
        """

        timestamp = article.timestamp_publication

        # Si premier report de cet événement
        if event_hash not in self.first_reports:
            self.first_reports[event_hash] = timestamp
            return ORIGINALITY_WEIGHTS["first_source"]

        # Sinon, calculer délai par rapport au premier
        first_timestamp = self.first_reports[event_hash]
        delay_hours = (timestamp - first_timestamp).total_seconds() / 3600

        # Early (< 1h) = 0.7
        # Late (1-6h) = 0.3
        # Very late (>6h) = 0.1
        if delay_hours < 1:
            return ORIGINALITY_WEIGHTS["early_source"]
        elif delay_hours < 6:
            return ORIGINALITY_WEIGHTS["late_source"]
        else:
            return ORIGINALITY_WEIGHTS["repost"]

    def _calculate_coverage(self, event_hash: str, source_tier: SourceTier) -> float:
        """
        Score de couverture [0-1]
        Basé sur nombre de sources indépendantes confirmant
        """

        # Ajouter cette source
        if event_hash not in self.event_coverage:
            self.event_coverage[event_hash] = []

        self.event_coverage[event_hash].append({
            "tier": source_tier,
            "timestamp": datetime.utcnow()
        })

        # Compter sources indépendantes (tier1/2 + official)
        sources = self.event_coverage[event_hash]
        independent_count = sum(
            1 for s in sources
            if s["tier"] in [SourceTier.TIER1_AGENCY, SourceTier.TIER2_FINANCIAL, SourceTier.OFFICIAL]
        )

        # Normaliser [0-1], saturation à MIN_INDEPENDENT_SOURCES
        score = min(independent_count / MIN_INDEPENDENT_SOURCES, 1.0)
        return score

    def get_coverage_count(self, event_hash: str) -> int:
        """Nombre de sources couvrant cet événement"""
        if event_hash not in self.event_coverage:
            return 0
        return len(self.event_coverage[event_hash])

    def get_independent_coverage_count(self, event_hash: str) -> int:
        """Nombre de sources indépendantes (tier1/2/official)"""
        if event_hash not in self.event_coverage:
            return 0

        sources = self.event_coverage[event_hash]
        return sum(
            1 for s in sources
            if s["tier"] in [SourceTier.TIER1_AGENCY, SourceTier.TIER2_FINANCIAL, SourceTier.OFFICIAL]
        )


class MetricsCalculator:
    """Calculs de métriques avancées NEWS"""

    @staticmethod
    def calculate_information_intensity(
        article_count: int,
        avg_credibility: float,
        window_seconds: int
    ) -> float:
        """
        Intensité informationnelle [0-1]
        volume × credibility, normalisé par temps
        """

        # Nombre d'articles par heure
        articles_per_hour = (article_count / window_seconds) * 3600

        # Pondéré par crédibilité
        weighted_volume = articles_per_hour * avg_credibility

        # Normalisation (saturation à 10 articles/h de haute qualité)
        intensity = min(weighted_volume / 10, 1.0)
        return intensity

    @staticmethod
    def calculate_acceleration(
        current_volume: float,
        baseline_mean: float,
        baseline_std: float
    ) -> float:
        """Accélération normalisée [0-1] pour burst detection"""

        if baseline_std == 0:
            if current_volume > baseline_mean:
                return 1.0
            else:
                return 0.0

        z_score = (current_volume - baseline_mean) / baseline_std

        # Normaliser [0-1], saturation à ±3σ
        normalized = (z_score + 3) / 6
        return np.clip(normalized, 0, 1)

    @staticmethod
    def calculate_data_confidence(
        article_count: int,
        independent_source_count: int,
        avg_credibility: float,
        avg_latency_ms: float,
        max_latency_ms: float = 300000
    ) -> float:
        """
        Confiance dans les données [0-1]
        Basé sur: couverture, sources indépendantes, crédibilité, fraîcheur
        """

        # 1. Couverture (nombre d'articles)
        coverage_score = min(article_count / 5, 1.0)  # 5 articles = full

        # 2. Sources indépendantes
        independence_score = min(independent_source_count / MIN_INDEPENDENT_SOURCES, 1.0)

        # 3. Crédibilité moyenne
        cred_score = avg_credibility

        # 4. Fraîcheur (latence)
        latency_score = 1.0 - (avg_latency_ms / max_latency_ms)
        latency_score = max(latency_score, 0)

        # Moyenne géométrique (pénalise les faiblesses)
        confidence = (coverage_score * independence_score * cred_score * latency_score) ** (1/4)
        return confidence

    @staticmethod
    def calculate_late_signal_penalty(first_seen: datetime, current_time: datetime) -> float:
        """
        Pénalité si signal tardif
        Retourne multiplicateur [0-1]
        """
        from config import LATE_SIGNAL_HOURS

        hours_elapsed = (current_time - first_seen).total_seconds() / 3600

        if hours_elapsed < 1:
            return 1.0  # pas de pénalité
        elif hours_elapsed < 6:
            return 0.8
        elif hours_elapsed < LATE_SIGNAL_HOURS:
            return 0.5
        else:
            return 0.2  # très tardif


class EventClustering:
    """Clustering d'articles sur le même événement"""

    def __init__(self):
        self.clusters = {}  # cluster_id -> [article_ids]
        self.article_to_cluster = {}  # article_id -> cluster_id

    def add_to_cluster(self, article_id: str, event_hash: str) -> str:
        """
        Ajouter article à un cluster d'événement
        Retourne cluster_id
        """

        # Event hash = cluster ID
        cluster_id = event_hash

        if cluster_id not in self.clusters:
            self.clusters[cluster_id] = []

        self.clusters[cluster_id].append(article_id)
        self.article_to_cluster[article_id] = cluster_id

        return cluster_id

    def get_cluster_size(self, cluster_id: str) -> int:
        """Nombre d'articles dans le cluster"""
        return len(self.clusters.get(cluster_id, []))

    def get_cluster_articles(self, cluster_id: str) -> List[str]:
        """Liste des article IDs dans le cluster"""
        return self.clusters.get(cluster_id, [])
