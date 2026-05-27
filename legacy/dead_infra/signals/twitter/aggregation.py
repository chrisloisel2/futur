"""
Agrégation temporelle multi-fenêtre pour détection de burst.
Calculs stricts sur fenêtres glissantes.
"""

from datetime import datetime, timedelta
from typing import List, Dict
from collections import defaultdict
import numpy as np

from .models import ProcessedTweet, WindowAggregation, SentimentDirection
from config import WINDOWS, BASELINE_WINDOW_DAYS, BURST_ZSCORE_THRESHOLD


class TemporalAggregator:
    """Agrégation sur fenêtres temporelles multiples"""

    def __init__(self):
        # Historique pour calcul de baseline (7 jours glissants)
        self.historical_volumes = defaultdict(list)  # entity -> [(timestamp, volume)]

    def aggregate(
        self,
        tweets: List[ProcessedTweet],
        entity: str,
        window_name: str,
        end_time: datetime
    ) -> WindowAggregation:
        """
        Agréger tweets pour une entité sur une fenêtre donnée
        """
        window_seconds = WINDOWS[window_name]
        start_time = end_time - timedelta(seconds=window_seconds)

        # Filtrer tweets dans la fenêtre
        window_tweets = [
            t for t in tweets
            if start_time <= t.raw.timestamp_publication <= end_time
            and entity in t.detected_entities
        ]

        if len(window_tweets) == 0:
            return self._empty_aggregation(entity, window_name, start_time, end_time)

        # Calculs d'agrégation
        tweet_count = len(window_tweets)
        unique_authors = len(set(t.raw.author_id for t in window_tweets))

        sentiment_dir, sentiment_str, sentiment_disp = self._aggregate_sentiment(window_tweets)
        total_reach = sum(t.reach_proxy for t in window_tweets)
        avg_velocity = np.mean([t.engagement_velocity for t in window_tweets])
        avg_cred = np.mean([t.author_credibility_score for t in window_tweets])

        high_cred_count = sum(1 for t in window_tweets if t.author_credibility_score > 0.6)
        high_cred_ratio = high_cred_count / tweet_count

        # Baseline et burst
        baseline_avg, baseline_std = self._get_baseline(entity, end_time)
        burst_score = self._calculate_burst(tweet_count, baseline_avg, baseline_std)

        # Flags de risque
        bot_risk = self._detect_bot_risk(window_tweets)
        manip_risk = self._detect_manipulation_risk(window_tweets, burst_score, avg_cred)
        low_cov = tweet_count < 5

        return WindowAggregation(
            entity=entity,
            window_name=window_name,
            window_seconds=window_seconds,
            start_time=start_time,
            end_time=end_time,
            tweet_count=tweet_count,
            unique_authors=unique_authors,
            sentiment_direction=sentiment_dir,
            sentiment_strength=sentiment_str,
            sentiment_dispersion=sentiment_disp,
            total_reach=total_reach,
            avg_engagement_velocity=avg_velocity,
            baseline_7d_avg=baseline_avg,
            burst_score=burst_score,
            avg_credibility=avg_cred,
            high_credibility_ratio=high_cred_ratio,
            bot_risk=bot_risk,
            manipulation_risk=manip_risk,
            low_coverage=low_cov
        )

    def _empty_aggregation(
        self,
        entity: str,
        window_name: str,
        start_time: datetime,
        end_time: datetime
    ) -> WindowAggregation:
        """Agrégation vide quand pas de tweets"""
        return WindowAggregation(
            entity=entity,
            window_name=window_name,
            window_seconds=WINDOWS[window_name],
            start_time=start_time,
            end_time=end_time,
            tweet_count=0,
            unique_authors=0,
            sentiment_direction=SentimentDirection.NEUTRAL,
            sentiment_strength=0.0,
            sentiment_dispersion=0.0,
            total_reach=0.0,
            avg_engagement_velocity=0.0,
            baseline_7d_avg=0.0,
            burst_score=0.0,
            avg_credibility=0.0,
            high_credibility_ratio=0.0,
            low_coverage=True
        )

    def _aggregate_sentiment(
        self,
        tweets: List[ProcessedTweet]
    ) -> tuple:
        """
        Retourne (direction, strength, dispersion)
        Sentiment pondéré par credibility
        """
        if not tweets:
            return SentimentDirection.NEUTRAL, 0.0, 0.0

        # Pondération par crédibilité
        sentiments = []
        weights = []

        for t in tweets:
            sentiments.append(t.sentiment_direction)
            weights.append(t.author_credibility_score)

        # Convertir en valeurs numériques
        numeric_sentiments = []
        for s in sentiments:
            if s == SentimentDirection.BULLISH:
                numeric_sentiments.append(1.0)
            elif s == SentimentDirection.BEARISH:
                numeric_sentiments.append(-1.0)
            else:
                numeric_sentiments.append(0.0)

        # Moyenne pondérée
        weighted_avg = np.average(numeric_sentiments, weights=weights)

        # Direction finale
        if weighted_avg > 0.2:
            direction = SentimentDirection.BULLISH
        elif weighted_avg < -0.2:
            direction = SentimentDirection.BEARISH
        else:
            direction = SentimentDirection.NEUTRAL

        # Strength = abs de la moyenne
        strength = abs(weighted_avg)

        # Dispersion (variance pondérée)
        weighted_var = np.average(
            [(x - weighted_avg)**2 for x in numeric_sentiments],
            weights=weights
        )
        dispersion = np.sqrt(weighted_var)

        return direction, strength, dispersion

    def _get_baseline(self, entity: str, current_time: datetime) -> tuple:
        """
        Retourne (baseline_mean, baseline_std) sur 7 jours glissants
        """
        baseline_start = current_time - timedelta(days=BASELINE_WINDOW_DAYS)

        # Récupérer volumes historiques
        historical = self.historical_volumes[entity]

        # Filtrer sur fenêtre baseline
        baseline_volumes = [
            vol for ts, vol in historical
            if baseline_start <= ts < current_time
        ]

        if len(baseline_volumes) < 10:
            # Pas assez de données historiques
            return 0.0, 0.0

        mean = np.mean(baseline_volumes)
        std = np.std(baseline_volumes)

        return mean, std

    def _calculate_burst(self, current_volume: int, baseline_mean: float, baseline_std: float) -> float:
        """Z-score du volume actuel vs baseline"""
        if baseline_std == 0:
            if current_volume > baseline_mean:
                return 1.0
            else:
                return 0.0

        z_score = (current_volume - baseline_mean) / baseline_std

        # Normaliser en [0, 1]
        # z > 2 = burst significatif
        normalized = min(max(z_score / 4, 0), 1)  # saturation à z=4
        return normalized

    def _detect_bot_risk(self, tweets: List[ProcessedTweet]) -> bool:
        """Détecter si burst provient de bots"""
        if not tweets:
            return False

        avg_bot_score = np.mean([t.bot_probability_score for t in tweets])
        return avg_bot_score > 0.5

    def _detect_manipulation_risk(
        self,
        tweets: List[ProcessedTweet],
        burst_score: float,
        avg_credibility: float
    ) -> bool:
        """Détecter manipulation (burst sans crédibilité)"""
        # Burst fort + crédibilité faible = suspect
        if burst_score > 0.7 and avg_credibility < 0.3:
            return True

        # Duplication élevée
        cluster_ids = [t.duplication_cluster_id for t in tweets if t.duplication_cluster_id]
        if cluster_ids:
            unique_ratio = len(set(cluster_ids)) / len(cluster_ids)
            if unique_ratio < 0.3:  # > 70% duplications
                return True

        return False

    def update_baseline(self, entity: str, timestamp: datetime, volume: int):
        """MAJ historique pour baseline"""
        self.historical_volumes[entity].append((timestamp, volume))

        # Nettoyer ancien historique (> 30 jours)
        cutoff = timestamp - timedelta(days=30)
        self.historical_volumes[entity] = [
            (ts, vol) for ts, vol in self.historical_volumes[entity]
            if ts > cutoff
        ]


class MultiWindowAggregator:
    """Agrégation sur toutes les fenêtres simultanément"""

    def __init__(self):
        self.aggregators = {
            name: TemporalAggregator()
            for name in WINDOWS.keys()
        }

    def aggregate_all(
        self,
        tweets: List[ProcessedTweet],
        entities: List[str],
        timestamp: datetime
    ) -> Dict[str, Dict[str, WindowAggregation]]:
        """
        Retourne dict[entity][window_name] = WindowAggregation
        """
        results = defaultdict(dict)

        for entity in entities:
            for window_name in WINDOWS.keys():
                agg = self.aggregators[window_name].aggregate(
                    tweets, entity, window_name, timestamp
                )
                results[entity][window_name] = agg

                # MAJ baseline
                self.aggregators[window_name].update_baseline(
                    entity, timestamp, agg.tweet_count
                )

        return results
