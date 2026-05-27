"""
Génération de signaux exploitables avec garde-fous.
Sortie strictement JSON, aucune interprétation.
"""

from datetime import datetime
from typing import List, Optional
import numpy as np

from .models import WindowAggregation, TradingSignal, SentimentDirection
from enrichment import MetricsCalculator
from config import (
    MIN_TWEETS_FOR_SIGNAL,
    MIN_CREDIBILITY_SCORE,
    HIGH_LATENCY_PENALTY,
    LOW_CREDIBILITY_FLAG,
    HIGH_DISPERSION_THRESHOLD,
    EXTREME_SENTIMENT_MIN_VOLUME,
    MAX_LATENCY_MS
)


class SignalGenerator:
    """Génération de signaux de trading depuis agrégations"""

    def __init__(self):
        self.metrics = MetricsCalculator()

    def generate(
        self,
        aggregation: WindowAggregation,
        avg_latency_ms: float
    ) -> Optional[TradingSignal]:
        """
        Génère signal si conditions validées
        Retourne None si aucun signal exploitable
        """

        # Garde-fou 1: volume minimum
        if aggregation.tweet_count < MIN_TWEETS_FOR_SIGNAL:
            return None

        # Garde-fou 2: crédibilité minimale
        if aggregation.avg_credibility < MIN_CREDIBILITY_SCORE:
            return None

        # Garde-fou 3: low coverage
        if aggregation.low_coverage:
            return None

        # Calculer composants du signal
        direction, strength = self._compute_sentiment_signal(aggregation)
        burst = self._compute_burst_signal(aggregation)
        composite_score = self._compute_composite_score(aggregation, avg_latency_ms)
        confidence = self._compute_confidence(aggregation, avg_latency_ms)
        warnings = self._generate_warnings(aggregation, avg_latency_ms)

        # Garde-fou 4: si dispersion élevée → neutraliser
        if aggregation.sentiment_dispersion > HIGH_DISPERSION_THRESHOLD:
            direction = SentimentDirection.NEUTRAL
            strength = 0.0
            warnings.append("high_sentiment_dispersion")

        # Garde-fou 5: sentiment extrême sans volume
        if strength > 0.7 and aggregation.tweet_count < EXTREME_SENTIMENT_MIN_VOLUME:
            warnings.append("extreme_sentiment_low_volume")
            return None  # ignorer

        # Garde-fou 6: burst sans crédibilité = manipulation
        if aggregation.manipulation_risk:
            warnings.append("manipulation_detected")
            return None  # ignorer

        # Si trop de warnings critiques, rejeter
        critical_warnings = [
            "manipulation_detected",
            "high_bot_risk",
            "extreme_sentiment_low_volume"
        ]
        if any(w in warnings for w in critical_warnings):
            return None

        return TradingSignal(
            entity=aggregation.entity,
            window=aggregation.window_name,
            timestamp=aggregation.end_time,
            sentiment_direction=direction,
            sentiment_strength=strength,
            attention_burst=burst,
            credibility_weighted_score=composite_score,
            data_confidence=confidence,
            warning_flags=warnings
        )

    def _compute_sentiment_signal(
        self,
        agg: WindowAggregation
    ) -> tuple:
        """Retourne (direction, strength) filtré"""

        direction = agg.sentiment_direction
        strength = agg.sentiment_strength

        # Ajuster strength selon crédibilité
        strength = strength * agg.avg_credibility

        return direction, min(strength, 1.0)

    def _compute_burst_signal(self, agg: WindowAggregation) -> float:
        """Attention burst normalisé [0-1]"""
        return min(agg.burst_score, 1.0)

    def _compute_composite_score(
        self,
        agg: WindowAggregation,
        avg_latency_ms: float
    ) -> float:
        """
        Score composite pondéré par:
        - sentiment strength
        - burst score
        - credibility
        - latency penalty
        """

        # Composants
        sentiment_component = agg.sentiment_strength * 0.3
        burst_component = agg.burst_score * 0.3
        credibility_component = agg.avg_credibility * 0.4

        base_score = sentiment_component + burst_component + credibility_component

        # Pénalité latence
        if avg_latency_ms > MAX_LATENCY_MS:
            base_score *= HIGH_LATENCY_PENALTY

        return min(base_score, 1.0)

    def _compute_confidence(
        self,
        agg: WindowAggregation,
        avg_latency_ms: float
    ) -> float:
        """Confiance dans les données [0-1]"""

        return self.metrics.calculate_data_confidence(
            tweet_count=agg.tweet_count,
            avg_latency_ms=avg_latency_ms,
            avg_credibility=agg.avg_credibility,
            max_latency_ms=MAX_LATENCY_MS
        )

    def _generate_warnings(
        self,
        agg: WindowAggregation,
        avg_latency_ms: float
    ) -> List[str]:
        """Liste de warning flags"""
        warnings = []

        # Bot risk
        if agg.bot_risk:
            warnings.append("high_bot_risk")

        # Manipulation risk
        if agg.manipulation_risk:
            warnings.append("manipulation_risk")

        # Low coverage
        if agg.low_coverage:
            warnings.append("low_tweet_coverage")

        # High latency
        if avg_latency_ms > MAX_LATENCY_MS:
            warnings.append("high_latency")

        # Low credibility
        if agg.avg_credibility < LOW_CREDIBILITY_FLAG:
            warnings.append("low_credibility")

        # High dispersion
        if agg.sentiment_dispersion > HIGH_DISPERSION_THRESHOLD:
            warnings.append("high_sentiment_dispersion")

        # Weak burst with strong sentiment (suspect)
        if agg.sentiment_strength > 0.7 and agg.burst_score < 0.3:
            warnings.append("sentiment_without_volume_spike")

        return warnings


class SignalBatch:
    """Batch de signaux avec métadonnées"""

    def __init__(self):
        self.signals: List[TradingSignal] = []
        self.generation_timestamp = datetime.utcnow()

    def add(self, signal: TradingSignal):
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

    def filter_by_confidence(self, min_confidence: float) -> 'SignalBatch':
        """Filtrer signaux par confiance minimale"""
        filtered = SignalBatch()
        filtered.generation_timestamp = self.generation_timestamp
        filtered.signals = [
            s for s in self.signals
            if s.data_confidence >= min_confidence
        ]
        return filtered

    def filter_by_entity(self, entities: List[str]) -> 'SignalBatch':
        """Filtrer par entités spécifiques"""
        filtered = SignalBatch()
        filtered.generation_timestamp = self.generation_timestamp
        filtered.signals = [
            s for s in self.signals
            if s.entity in entities
        ]
        return filtered

    def get_top_signals(self, n: int = 10) -> List[TradingSignal]:
        """Top N signaux par composite score"""
        sorted_signals = sorted(
            self.signals,
            key=lambda s: s.credibility_weighted_score,
            reverse=True
        )
        return sorted_signals[:n]


class SignalAggregator:
    """Agrégation de signaux multi-fenêtres pour consensus"""

    @staticmethod
    def find_consensus(signals: List[TradingSignal], entity: str) -> Optional[dict]:
        """
        Cherche consensus sur plusieurs fenêtres pour une entité
        Retourne None si pas de consensus clair
        """
        entity_signals = [s for s in signals if s.entity == entity]

        if len(entity_signals) < 2:
            return None

        # Vérifier alignement directionnel
        directions = [s.sentiment_direction for s in entity_signals]
        bullish_count = sum(1 for d in directions if d == SentimentDirection.BULLISH)
        bearish_count = sum(1 for d in directions if d == SentimentDirection.BEARISH)

        total = len(entity_signals)
        consensus_threshold = 0.7  # 70% alignement requis

        if bullish_count / total >= consensus_threshold:
            direction = SentimentDirection.BULLISH
        elif bearish_count / total >= consensus_threshold:
            direction = SentimentDirection.BEARISH
        else:
            return None  # pas de consensus

        # Moyennes des métriques
        avg_strength = np.mean([s.sentiment_strength for s in entity_signals])
        avg_burst = np.mean([s.attention_burst for s in entity_signals])
        avg_score = np.mean([s.credibility_weighted_score for s in entity_signals])
        avg_confidence = np.mean([s.data_confidence for s in entity_signals])

        # Combiner warnings
        all_warnings = []
        for s in entity_signals:
            all_warnings.extend(s.warning_flags)
        unique_warnings = list(set(all_warnings))

        return {
            "entity": entity,
            "consensus_direction": direction.value,
            "consensus_strength": round(avg_strength, 3),
            "avg_attention_burst": round(avg_burst, 3),
            "avg_credibility_score": round(avg_score, 3),
            "avg_confidence": round(avg_confidence, 3),
            "signal_count": total,
            "windows_aligned": [s.window for s in entity_signals],
            "warnings": unique_warnings
        }
