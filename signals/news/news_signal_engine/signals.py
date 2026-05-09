"""
Génération de signaux NEWS exploitables avec garde-fous stricts.
Sortie strictement JSON, aucune interprétation.
"""

from datetime import datetime
from typing import List, Optional
import numpy as np

from .models import NewsWindowAggregation, NewsSignal, EventType, EventStatus, SurpriseLevel, GeographicScope
from enrichment import MetricsCalculator
from config import (
    MIN_ARTICLES_FOR_SIGNAL,
    MIN_CREDIBILITY_SCORE,
    SINGLE_SOURCE_PENALTY,
    HIGH_LATENCY_PENALTY,
    CORRECTION_PENALTY,
    MAX_LATENCY_MS,
    MIN_INDEPENDENT_SOURCES
)


class NewsSignalGenerator:
    """Génération de signaux de trading depuis agrégations NEWS"""

    def __init__(self):
        self.metrics = MetricsCalculator()

    def generate(
        self,
        aggregation: NewsWindowAggregation,
        avg_latency_ms: float,
        has_corrections: bool = False
    ) -> Optional[NewsSignal]:
        """
        Génère signal si conditions validées
        Retourne None si aucun signal exploitable
        """

        # Garde-fou 1: volume minimum
        if aggregation.article_count < MIN_ARTICLES_FOR_SIGNAL:
            return None

        # Garde-fou 2: crédibilité minimale
        if aggregation.avg_credibility < MIN_CREDIBILITY_SCORE:
            return None

        # Garde-fou 3: low coverage
        if aggregation.low_coverage:
            return None

        # Garde-fou 4: source unique
        if aggregation.single_source_risk:
            # Peut passer mais avec warning fort
            pass

        # Calculer composants du signal
        intensity = aggregation.information_intensity
        composite_score = self._compute_composite_score(
            aggregation, avg_latency_ms, has_corrections
        )
        confidence = self._compute_confidence(aggregation, avg_latency_ms)
        warnings = self._generate_warnings(aggregation, avg_latency_ms, has_corrections)

        # Garde-fou 5: si trop de warnings critiques, rejeter
        critical_warnings = ["single_source", "very_late_signal", "low_credibility"]
        critical_count = sum(1 for w in warnings if w in critical_warnings)

        if critical_count >= 2:
            return None  # trop de problèmes

        # Déterminer event type dominant
        if not aggregation.event_type:
            return None  # besoin d'un event type clair

        return NewsSignal(
            entity=aggregation.entity,
            event_type=aggregation.event_type,
            event_status=EventStatus.OFFICIAL_ANNOUNCEMENT,  # défaut
            surprise_level=aggregation.dominant_surprise_level,
            window=aggregation.window_name,
            timestamp=aggregation.end_time,
            information_intensity=intensity,
            credibility_weighted_score=composite_score,
            geographic_scope=aggregation.geographic_scope,
            data_confidence=confidence,
            source_count=aggregation.unique_sources,
            independent_source_count=aggregation.independent_source_count,
            warning_flags=warnings
        )

    def _compute_composite_score(
        self,
        agg: NewsWindowAggregation,
        avg_latency_ms: float,
        has_corrections: bool
    ) -> float:
        """
        Score composite pondéré par:
        - information intensity
        - credibility
        - independent sources
        - pénalités (latency, single source, corrections)
        """

        # Composants
        intensity_component = agg.information_intensity * 0.3
        credibility_component = agg.avg_credibility * 0.3

        # Independent sources (saturation à MIN_INDEPENDENT_SOURCES)
        independence_score = min(
            agg.independent_source_count / MIN_INDEPENDENT_SOURCES, 1.0
        )
        independence_component = independence_score * 0.4

        base_score = intensity_component + credibility_component + independence_component

        # Pénalités
        penalty = 1.0

        # Source unique
        if agg.single_source_risk:
            penalty *= SINGLE_SOURCE_PENALTY

        # Latence élevée
        if avg_latency_ms > MAX_LATENCY_MS:
            penalty *= HIGH_LATENCY_PENALTY

        # Corrections
        if has_corrections:
            penalty *= CORRECTION_PENALTY

        # Signal tardif
        if agg.late_signal:
            penalty *= 0.5

        final_score = base_score * penalty
        return min(final_score, 1.0)

    def _compute_confidence(
        self,
        agg: NewsWindowAggregation,
        avg_latency_ms: float
    ) -> float:
        """Confiance dans les données [0-1]"""

        return self.metrics.calculate_data_confidence(
            article_count=agg.article_count,
            independent_source_count=agg.independent_source_count,
            avg_credibility=agg.avg_credibility,
            avg_latency_ms=avg_latency_ms,
            max_latency_ms=MAX_LATENCY_MS
        )

    def _generate_warnings(
        self,
        agg: NewsWindowAggregation,
        avg_latency_ms: float,
        has_corrections: bool
    ) -> List[str]:
        """Liste de warning flags"""

        warnings = []

        # Source unique
        if agg.single_source_risk:
            warnings.append("single_source")

        # Low coverage
        if agg.low_coverage:
            warnings.append("low_coverage")

        # High latency
        if avg_latency_ms > MAX_LATENCY_MS:
            warnings.append("delayed_release")

        # Corrections
        if has_corrections:
            warnings.append("article_corrected")

        # Signal tardif
        if agg.late_signal:
            warnings.append("very_late_signal")

        # Low credibility
        if agg.avg_credibility < 0.5:
            warnings.append("low_credibility")

        # Peu de sources indépendantes
        if agg.independent_source_count < 2:
            warnings.append("insufficient_independent_sources")

        # Accélération faible malgré intensité
        if agg.information_intensity > 0.7 and agg.acceleration_score < 0.3:
            warnings.append("intensity_without_acceleration")

        return warnings


class NewsSignalBatch:
    """Batch de signaux NEWS avec métadonnées"""

    def __init__(self):
        self.signals: List[NewsSignal] = []
        self.generation_timestamp = datetime.utcnow()

    def add(self, signal: NewsSignal):
        """Ajouter signal au batch"""
        if signal is not None:
            self.signals.append(signal)

    def to_json(self) -> dict:
        """Export JSON complet"""
        return {
            "generation_timestamp": self.generation_timestamp.isoformat(),
            "signal_count": len(self.signals),
            "signals": [s.to_json() for s in self.signals]
        }

    def filter_by_confidence(self, min_confidence: float) -> 'NewsSignalBatch':
        """Filtrer signaux par confiance minimale"""
        filtered = NewsSignalBatch()
        filtered.generation_timestamp = self.generation_timestamp
        filtered.signals = [
            s for s in self.signals
            if s.data_confidence >= min_confidence
        ]
        return filtered

    def filter_by_event_type(self, event_types: List[EventType]) -> 'NewsSignalBatch':
        """Filtrer par types d'événement"""
        filtered = NewsSignalBatch()
        filtered.generation_timestamp = self.generation_timestamp
        filtered.signals = [
            s for s in self.signals
            if s.event_type in event_types
        ]
        return filtered

    def filter_by_surprise(self, min_surprise: SurpriseLevel) -> 'NewsSignalBatch':
        """Filtrer par niveau de surprise minimum"""
        surprise_order = {
            SurpriseLevel.EXPECTED: 0,
            SurpriseLevel.PARTIALLY_EXPECTED: 1,
            SurpriseLevel.UNEXPECTED: 2
        }

        min_level = surprise_order[min_surprise]

        filtered = NewsSignalBatch()
        filtered.generation_timestamp = self.generation_timestamp
        filtered.signals = [
            s for s in self.signals
            if surprise_order[s.surprise_level] >= min_level
        ]
        return filtered

    def get_top_signals(self, n: int = 10) -> List[NewsSignal]:
        """Top N signaux par composite score"""
        sorted_signals = sorted(
            self.signals,
            key=lambda s: s.credibility_weighted_score,
            reverse=True
        )
        return sorted_signals[:n]

    def group_by_entity(self) -> dict:
        """Grouper signaux par entité"""
        from collections import defaultdict

        grouped = defaultdict(list)
        for signal in self.signals:
            grouped[signal.entity].append(signal)

        return dict(grouped)

    def group_by_event_type(self) -> dict:
        """Grouper signaux par type d'événement"""
        from collections import defaultdict

        grouped = defaultdict(list)
        for signal in self.signals:
            grouped[signal.event_type.value].append(signal)

        return dict(grouped)


class MarketImpactEstimator:
    """Estimation potentiel d'impact marché (heuristique)"""

    @staticmethod
    def estimate_impact_level(signal: NewsSignal) -> str:
        """
        Retourne: low / medium / high / critical
        Basé sur: type événement + surprise + scope + crédibilité
        """

        # Event types à fort impact
        high_impact_events = [
            EventType.REGULATION, EventType.MONETARY_POLICY,
            EventType.APPROVAL, EventType.REJECTION,
            EventType.HACK, EventType.BANKRUPTCY,
            EventType.SANCTION, EventType.GEOPOLITICAL_CONFLICT
        ]

        # Score base
        impact_score = 0.0

        # 1. Type événement
        if signal.event_type in high_impact_events:
            impact_score += 0.4
        else:
            impact_score += 0.2

        # 2. Niveau surprise
        surprise_weights = {
            SurpriseLevel.EXPECTED: 0.1,
            SurpriseLevel.PARTIALLY_EXPECTED: 0.2,
            SurpriseLevel.UNEXPECTED: 0.3
        }
        impact_score += surprise_weights[signal.surprise_level]

        # 3. Scope géographique
        scope_weights = {
            GeographicScope.LOCAL: 0.1,
            GeographicScope.REGIONAL: 0.2,
            GeographicScope.GLOBAL: 0.3
        }
        impact_score += scope_weights[signal.geographic_scope]

        # Pondérer par crédibilité
        impact_score *= signal.credibility_weighted_score

        # Classification
        if impact_score >= 0.7:
            return "critical"
        elif impact_score >= 0.5:
            return "high"
        elif impact_score >= 0.3:
            return "medium"
        else:
            return "low"

    @staticmethod
    def estimate_volatility_window_hours(signal: NewsSignal) -> int:
        """
        Estimer fenêtre de volatilité attendue en heures
        """

        # Macro events = impact prolongé
        macro_events = [
            EventType.MONETARY_POLICY,
            EventType.MACRO_DATA_RELEASE,
            EventType.GEOPOLITICAL_CONFLICT
        ]

        if signal.event_type in macro_events:
            if signal.geographic_scope == GeographicScope.GLOBAL:
                return 72  # 3 jours
            else:
                return 24  # 1 jour

        # Events crypto-spécifiques
        if signal.event_type in [EventType.HACK, EventType.EXPLOIT, EventType.BANKRUPTCY]:
            return 48  # 2 jours

        # Régulation
        if signal.event_type == EventType.REGULATION:
            return 24

        # Autres événements
        return 6  # 6 heures
