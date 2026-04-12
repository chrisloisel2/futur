"""
Calcul des méta-informations critiques pour chaque tweet.
Pas d'approximation, calculs stricts uniquement.
"""

import hashlib
from datetime import datetime
from typing import Optional
import numpy as np

from models import RawTweet, ProcessedTweet
from config import (
    ENGAGEMENT_WEIGHTS,
    CREDIBILITY_WEIGHTS,
    BOT_PROBABILITY_THRESHOLD,
    PRIORITY_ACCOUNTS
)


class TweetEnricher:
    """Enrichissement des tweets avec méta-informations"""

    def __init__(self):
        # Cache pour historique d'engagement par auteur
        self.author_engagement_history = {}

    def enrich(self, raw: RawTweet) -> ProcessedTweet:
        """Calcul de toutes les méta-informations"""

        latency_ms = self._calculate_latency(raw)
        engagement_velocity = self._calculate_engagement_velocity(raw)
        reach_proxy = self._calculate_reach_proxy(raw, engagement_velocity)
        account_age_days = self._calculate_account_age(raw)
        credibility_score = self._calculate_credibility(raw)
        bot_score = self._calculate_bot_probability(raw)
        cluster_id = self._calculate_duplication_cluster(raw)

        return ProcessedTweet(
            raw=raw,
            latency_ms=latency_ms,
            engagement_velocity=engagement_velocity,
            reach_proxy=reach_proxy,
            account_age_days=account_age_days,
            author_credibility_score=credibility_score,
            bot_probability_score=bot_score,
            duplication_cluster_id=cluster_id
        )

    def _calculate_latency(self, tweet: RawTweet) -> float:
        """Latence collecte - publication en ms"""
        delta = tweet.timestamp_collecte - tweet.timestamp_publication
        return delta.total_seconds() * 1000

    def _calculate_engagement_velocity(self, tweet: RawTweet) -> float:
        """(likes + RT + replies pondérés) / minutes écoulées"""
        minutes_elapsed = (
            tweet.timestamp_collecte - tweet.timestamp_publication
        ).total_seconds() / 60

        if minutes_elapsed < 1:
            minutes_elapsed = 1  # éviter division par 0

        weighted_engagement = (
            tweet.likes * ENGAGEMENT_WEIGHTS["likes"] +
            tweet.retweets * ENGAGEMENT_WEIGHTS["retweets"] +
            tweet.replies * ENGAGEMENT_WEIGHTS["replies"]
        )

        return weighted_engagement / minutes_elapsed

    def _calculate_reach_proxy(self, tweet: RawTweet, velocity: float) -> float:
        """followers × engagement_velocity"""
        return tweet.followers_count * velocity

    def _calculate_account_age(self, tweet: RawTweet) -> int:
        """Age du compte en jours"""
        delta = tweet.timestamp_publication - tweet.account_created_at
        return delta.days

    def _calculate_credibility(self, tweet: RawTweet) -> float:
        """Score de crédibilité composite [0-1]"""

        # 1. Followers (normalisé log)
        followers_score = np.log10(max(tweet.followers_count, 1)) / 7  # ~10M followers = 1.0
        followers_score = min(followers_score, 1.0)

        # 2. Account age (normalisé)
        age_days = self._calculate_account_age(tweet)
        age_score = min(age_days / 1825, 1.0)  # 5 ans = 1.0

        # 3. Verified
        verified_score = 1.0 if tweet.verified else 0.0

        # 4. Engagement historique moyen
        engagement_score = self._get_author_engagement_score(tweet.author_id)

        # 5. Whitelist bonus
        whitelist_score = 1.0 if self._is_priority_account(tweet.author_id) else 0.0

        # Combinaison pondérée
        total = (
            followers_score * CREDIBILITY_WEIGHTS["followers"] +
            age_score * CREDIBILITY_WEIGHTS["account_age"] +
            verified_score * CREDIBILITY_WEIGHTS["verified"] +
            engagement_score * CREDIBILITY_WEIGHTS["engagement_history"] +
            whitelist_score * CREDIBILITY_WEIGHTS["whitelist"]
        )

        return min(total, 1.0)

    def _get_author_engagement_score(self, author_id: str) -> float:
        """Score d'engagement historique de l'auteur [0-1]"""
        if author_id not in self.author_engagement_history:
            return 0.5  # neutral si pas d'historique

        history = self.author_engagement_history[author_id]
        avg_engagement = np.mean(history)

        # Normalisation log
        score = np.log10(max(avg_engagement, 1)) / 4  # ~10k eng moyen = 1.0
        return min(score, 1.0)

    def update_author_history(self, author_id: str, engagement: float):
        """MAJ historique engagement auteur"""
        if author_id not in self.author_engagement_history:
            self.author_engagement_history[author_id] = []

        history = self.author_engagement_history[author_id]
        history.append(engagement)

        # Garder seulement 100 derniers tweets
        if len(history) > 100:
            history.pop(0)

    def _is_priority_account(self, author_id: str) -> bool:
        """Check si dans whitelist prioritaire"""
        for accounts in PRIORITY_ACCOUNTS.values():
            if author_id in accounts:
                return True
        return False

    def _calculate_bot_probability(self, tweet: RawTweet) -> float:
        """Heuristique simple de détection de bot [0-1]"""
        score = 0.0

        # Ratio following/followers suspect
        if tweet.followers_count > 0:
            ratio = tweet.following_count / tweet.followers_count
            if ratio > 3:  # suit beaucoup plus qu'il n'a de followers
                score += 0.3

        # Compte jeune + beaucoup de followers = suspect
        age_days = self._calculate_account_age(tweet)
        if age_days < 180 and tweet.followers_count > 10000:
            score += 0.3

        # Pas vérifié + followers élevés = suspect
        if not tweet.verified and tweet.followers_count > 50000:
            score += 0.2

        # Patterns de texte bot (répétitions)
        words = tweet.text.split()
        if len(words) > 0:
            unique_ratio = len(set(words)) / len(words)
            if unique_ratio < 0.5:  # beaucoup de répétitions
                score += 0.2

        return min(score, 1.0)

    def _calculate_duplication_cluster(self, tweet: RawTweet) -> Optional[str]:
        """Hash du texte normalisé pour détecter duplications"""
        # Normalisation
        text = tweet.text.lower()
        text = text.strip()

        # Retirer URLs et mentions
        import re
        text = re.sub(r'http\S+', '', text)
        text = re.sub(r'@\w+', '', text)
        text = re.sub(r'\s+', ' ', text).strip()

        if len(text) < 10:
            return None

        # Hash pour clustering
        return hashlib.md5(text.encode()).hexdigest()[:16]


class MetricsCalculator:
    """Calculs de métriques avancées"""

    @staticmethod
    def calculate_burst_score(
        current_volume: float,
        baseline_mean: float,
        baseline_std: float
    ) -> float:
        """Z-score normalisé [0-1] pour burst detection"""
        if baseline_std == 0:
            return 0.0

        z_score = (current_volume - baseline_mean) / baseline_std

        # Normaliser en [0-1], avec saturation à ±3σ
        normalized = (z_score + 3) / 6
        return np.clip(normalized, 0, 1)

    @staticmethod
    def calculate_sentiment_dispersion(sentiments: list) -> float:
        """Standard deviation du sentiment [0-1]"""
        if len(sentiments) < 2:
            return 0.0

        # Convertir directions en valeurs numériques
        # bearish=-1, neutral=0, bullish=1
        numeric = []
        for s in sentiments:
            if s == "bearish":
                numeric.append(-1)
            elif s == "bullish":
                numeric.append(1)
            else:
                numeric.append(0)

        std = np.std(numeric)
        # Normaliser (max std = 1 pour [-1, 1])
        return min(std, 1.0)

    @staticmethod
    def calculate_data_confidence(
        tweet_count: int,
        avg_latency_ms: float,
        avg_credibility: float,
        max_latency_ms: float = 30000
    ) -> float:
        """Confiance dans les données [0-1]"""

        # 1. Couverture (nombre de tweets)
        coverage_score = min(tweet_count / 50, 1.0)  # 50 tweets = full coverage

        # 2. Fraîcheur (latence)
        latency_score = 1.0 - (avg_latency_ms / max_latency_ms)
        latency_score = max(latency_score, 0)

        # 3. Crédibilité moyenne
        cred_score = avg_credibility

        # Moyenne géométrique
        confidence = (coverage_score * latency_score * cred_score) ** (1/3)
        return confidence
