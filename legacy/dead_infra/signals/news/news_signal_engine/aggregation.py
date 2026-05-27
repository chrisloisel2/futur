"""
Agrégation temporelle multi-fenêtre pour NEWS.
Détection d'accélération informationnelle et clustering d'événements.
"""

from datetime import datetime, timedelta
from typing import List, Dict, Optional
from collections import defaultdict
import numpy as np

from .models import ProcessedNewsArticle, NewsWindowAggregation, EventType, SurpriseLevel, GeographicScope
from enrichment import MetricsCalculator
from config import WINDOWS, BASELINE_WINDOW_DAYS


class NewsTemporalAggregator:
    """Agrégation sur fenêtres temporelles multiples"""

    def __init__(self):
        # Historique pour baseline (7 jours)
        self.historical_volumes = defaultdict(list)  # entity+event -> [(timestamp, volume)]

    def aggregate(
        self,
        articles: List[ProcessedNewsArticle],
        entity: str,
        event_type: Optional[EventType],
        window_name: str,
        end_time: datetime
    ) -> NewsWindowAggregation:
        """
        Agréger articles pour une entité/event sur une fenêtre
        """

        window_seconds = WINDOWS[window_name]
        start_time = end_time - timedelta(seconds=window_seconds)

        # Filtrer articles dans fenêtre
        window_articles = [
            a for a in articles
            if start_time <= a.raw.timestamp_publication <= end_time
            and entity in a.detected_entities
            and (event_type is None or event_type in a.event_types)
        ]

        if len(window_articles) == 0:
            return self._empty_aggregation(
                entity, event_type, window_name, start_time, end_time
            )

        # Calculs d'agrégation
        article_count = len(window_articles)
        unique_sources = len(set(a.raw.source for a in window_articles))

        # Sources indépendantes (tier1/2/official)
        from models import SourceTier
        independent_sources = set(
            a.raw.source for a in window_articles
            if a.source_tier in [SourceTier.TIER1_AGENCY, SourceTier.TIER2_FINANCIAL, SourceTier.OFFICIAL]
        )
        independent_count = len(independent_sources)

        # Intensité informationnelle
        avg_cred = np.mean([a.credibility_score for a in window_articles])
        intensity = MetricsCalculator.calculate_information_intensity(
            article_count, avg_cred, window_seconds
        )

        # Baseline et accélération
        baseline_avg, baseline_std = self._get_baseline(entity, event_type, end_time)
        acceleration = MetricsCalculator.calculate_acceleration(
            article_count, baseline_avg, baseline_std
        )

        # Crédibilité
        high_cred_count = sum(1 for a in window_articles if a.credibility_score > 0.7)
        high_cred_ratio = high_cred_count / article_count

        # Distribution surprise
        surprise_dist = defaultdict(int)
        for a in window_articles:
            surprise_dist[a.surprise_level.value] += 1

        dominant_surprise = self._get_dominant_surprise(surprise_dist)

        # Geographic scope
        geo_scope = self._aggregate_geographic_scope(window_articles)
        affected_regions = list(set(a.raw.country for a in window_articles if a.raw.country))

        # Flags
        single_source = unique_sources == 1
        late_signal = self._is_late_signal(window_articles)
        low_coverage = article_count < 2

        return NewsWindowAggregation(
            entity=entity,
            event_type=event_type,
            window_name=window_name,
            window_seconds=window_seconds,
            start_time=start_time,
            end_time=end_time,
            article_count=article_count,
            unique_sources=unique_sources,
            independent_source_count=independent_count,
            information_intensity=intensity,
            baseline_7d_avg=baseline_avg,
            acceleration_score=acceleration,
            avg_credibility=avg_cred,
            high_credibility_ratio=high_cred_ratio,
            surprise_distribution=dict(surprise_dist),
            dominant_surprise_level=dominant_surprise,
            geographic_scope=geo_scope,
            affected_regions=affected_regions,
            single_source_risk=single_source,
            late_signal=late_signal,
            low_coverage=low_coverage
        )

    def _empty_aggregation(
        self,
        entity: str,
        event_type: Optional[EventType],
        window_name: str,
        start_time: datetime,
        end_time: datetime
    ) -> NewsWindowAggregation:
        """Agrégation vide si pas d'articles"""

        return NewsWindowAggregation(
            entity=entity,
            event_type=event_type,
            window_name=window_name,
            window_seconds=WINDOWS[window_name],
            start_time=start_time,
            end_time=end_time,
            article_count=0,
            unique_sources=0,
            independent_source_count=0,
            information_intensity=0.0,
            baseline_7d_avg=0.0,
            acceleration_score=0.0,
            avg_credibility=0.0,
            high_credibility_ratio=0.0,
            low_coverage=True
        )

    def _get_baseline(
        self,
        entity: str,
        event_type: Optional[EventType],
        current_time: datetime
    ) -> tuple:
        """Retourne (baseline_mean, baseline_std) sur 7 jours"""

        key = f"{entity}_{event_type.value if event_type else 'all'}"
        baseline_start = current_time - timedelta(days=BASELINE_WINDOW_DAYS)

        historical = self.historical_volumes[key]

        # Filtrer sur fenêtre baseline
        baseline_volumes = [
            vol for ts, vol in historical
            if baseline_start <= ts < current_time
        ]

        if len(baseline_volumes) < 5:
            return 0.0, 0.0

        mean = np.mean(baseline_volumes)
        std = np.std(baseline_volumes)

        return mean, std

    def _get_dominant_surprise(self, distribution: Dict[str, int]) -> SurpriseLevel:
        """Surprise level dominant"""

        if not distribution:
            return SurpriseLevel.EXPECTED

        # Plus fréquent
        dominant = max(distribution.items(), key=lambda x: x[1])[0]

        return SurpriseLevel(dominant)

    def _aggregate_geographic_scope(self, articles: List[ProcessedNewsArticle]) -> GeographicScope:
        """Scope géographique agrégé"""

        scopes = [a.geographic_scope for a in articles]

        # Si au moins un global = global
        if GeographicScope.GLOBAL in scopes:
            return GeographicScope.GLOBAL

        # Si au moins un regional = regional
        if GeographicScope.REGIONAL in scopes:
            return GeographicScope.REGIONAL

        return GeographicScope.LOCAL

    def _is_late_signal(self, articles: List[ProcessedNewsArticle]) -> bool:
        """Vérifier si signal tardif (événement > 24h)"""

        from config import LATE_SIGNAL_HOURS

        # Trouver article le plus ancien
        oldest = min(articles, key=lambda a: a.raw.timestamp_publication)
        hours_elapsed = (datetime.utcnow() - oldest.raw.timestamp_publication).total_seconds() / 3600

        return hours_elapsed > LATE_SIGNAL_HOURS

    def update_baseline(
        self,
        entity: str,
        event_type: Optional[EventType],
        timestamp: datetime,
        volume: int
    ):
        """MAJ historique pour baseline"""

        key = f"{entity}_{event_type.value if event_type else 'all'}"
        self.historical_volumes[key].append((timestamp, volume))

        # Nettoyer ancien historique (> 30j)
        cutoff = timestamp - timedelta(days=30)
        self.historical_volumes[key] = [
            (ts, vol) for ts, vol in self.historical_volumes[key]
            if ts > cutoff
        ]


class MultiWindowNewsAggregator:
    """Agrégation sur toutes les fenêtres simultanément"""

    def __init__(self):
        self.aggregators = {
            name: NewsTemporalAggregator()
            for name in WINDOWS.keys()
        }

    def aggregate_all(
        self,
        articles: List[ProcessedNewsArticle],
        entities: List[str],
        event_types: List[EventType],
        timestamp: datetime
    ) -> Dict[str, Dict[str, Dict[str, NewsWindowAggregation]]]:
        """
        Retourne dict[entity][event_type][window_name] = NewsWindowAggregation
        """

        results = defaultdict(lambda: defaultdict(dict))

        for entity in entities:
            for event_type in event_types:
                for window_name in WINDOWS.keys():
                    agg = self.aggregators[window_name].aggregate(
                        articles, entity, event_type, window_name, timestamp
                    )
                    results[entity][event_type.value][window_name] = agg

                    # MAJ baseline
                    self.aggregators[window_name].update_baseline(
                        entity, event_type, timestamp, agg.article_count
                    )

            # Aussi agréger tous event types combinés
            for window_name in WINDOWS.keys():
                agg_all = self.aggregators[window_name].aggregate(
                    articles, entity, None, window_name, timestamp
                )
                results[entity]["all"][window_name] = agg_all

                self.aggregators[window_name].update_baseline(
                    entity, None, timestamp, agg_all.article_count
                )

        return results


class EventClusterAnalyzer:
    """Analyse de clusters d'événements"""

    def __init__(self):
        from enrichment import EventClustering
        self.clustering = EventClustering()

    def analyze_clusters(
        self,
        articles: List[ProcessedNewsArticle]
    ) -> Dict[str, dict]:
        """
        Analyser clusters d'événements
        Retourne dict[cluster_id] = stats
        """

        cluster_stats = defaultdict(lambda: {
            "article_count": 0,
            "source_count": 0,
            "first_seen": None,
            "last_updated": None,
            "avg_credibility": 0.0,
            "event_types": set(),
            "entities": set()
        })

        for article in articles:
            cluster_id = article.event_cluster_id
            if not cluster_id:
                continue

            stats = cluster_stats[cluster_id]
            stats["article_count"] += 1

            # First/last seen
            timestamp = article.raw.timestamp_publication
            if stats["first_seen"] is None or timestamp < stats["first_seen"]:
                stats["first_seen"] = timestamp
            if stats["last_updated"] is None or timestamp > stats["last_updated"]:
                stats["last_updated"] = timestamp

            # Event types et entités
            stats["event_types"].update(article.event_types)
            stats["entities"].update(article.detected_entities)

        # Convertir sets en lists pour JSON
        for cluster_id, stats in cluster_stats.items():
            stats["event_types"] = [et.value for et in stats["event_types"]]
            stats["entities"] = list(stats["entities"])

        return dict(cluster_stats)
