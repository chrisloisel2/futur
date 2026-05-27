"""
Filtrage dur des tweets avant toute analyse.
Rejeter immédiatement tout ce qui n'est pas exploitable.
"""

import re
from datetime import datetime, timedelta
from typing import List, Tuple
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .models import RawTweet
from config import (
    ACCOUNT_MIN_AGE_DAYS,
    ACCOUNT_MIN_FOLLOWERS,
    SPAM_REPETITION_THRESHOLD,
    ALL_ENTITIES,
    PRIORITY_ACCOUNTS
)


class TweetFilter:
    """Filtrage strict multi-critères"""

    def __init__(self):
        self.vectorizer = TfidfVectorizer(max_features=100)
        self.recent_tweets_cache = []  # pour détecter duplications
        self.cache_max_size = 1000

    def filter(self, tweet: RawTweet) -> Tuple[bool, List[str]]:
        """
        Retourne (passed, rejection_reasons)
        Si passed=False, le tweet est immédiatement rejeté.
        """
        reasons = []

        # 1. Age du compte
        if not self._check_account_age(tweet):
            reasons.append("account_too_young")

        # 2. Followers
        if not self._check_followers(tweet):
            reasons.append("insufficient_followers")

        # 3. Spam detection
        if self._is_spam(tweet):
            reasons.append("spam_detected")

        # 4. Entité pertinente
        if not self._has_relevant_entity(tweet):
            reasons.append("no_relevant_entity")

        # 5. Langue
        if not self._check_language(tweet):
            reasons.append("language_not_supported")

        # 6. Duplication
        if self._is_duplicate(tweet):
            reasons.append("duplicate_content")

        passed = len(reasons) == 0
        return passed, reasons

    def _check_account_age(self, tweet: RawTweet) -> bool:
        """Rejeter comptes < 90 jours"""
        age_days = (tweet.timestamp_publication - tweet.account_created_at).days
        return age_days >= ACCOUNT_MIN_AGE_DAYS

    def _check_followers(self, tweet: RawTweet) -> bool:
        """Rejeter si < 1000 followers, sauf si whitelist"""
        if self._is_whitelisted(tweet.author_id):
            return True
        return tweet.followers_count >= ACCOUNT_MIN_FOLLOWERS

    def _is_whitelisted(self, author_id: str) -> bool:
        """Check si compte en whitelist prioritaire"""
        for category in PRIORITY_ACCOUNTS.values():
            if author_id in category:
                return True
        return False

    def _is_spam(self, tweet: RawTweet) -> bool:
        """Détection de spam basique"""
        text = tweet.text.lower()

        # Répétitions de caractères (ex: "BUY NOW!!!!!!!")
        if re.search(r'(.)\1{4,}', text):
            return True

        # Trop de hashtags
        if len(tweet.hashtags) > 10:
            return True

        # Patterns spam connus
        spam_patterns = [
            r'click here',
            r'earn \$\d+',
            r'guaranteed profit',
            r'risk.?free',
            r'100x',
            r'pump.*group',
            r'signals.*channel'
        ]
        for pattern in spam_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return True

        # Liens suspects (plusieurs liens courts)
        short_links = re.findall(r'https?://\S{1,15}', text)
        if len(short_links) > 2:
            return True

        return False

    def _has_relevant_entity(self, tweet: RawTweet) -> bool:
        """Vérifier présence d'au moins une entité pertinente"""
        text_upper = tweet.text.upper()

        # Check cashtags
        for cashtag in tweet.cashtags:
            if cashtag.upper() in ALL_ENTITIES:
                return True

        # Check entités dans texte
        for entity in ALL_ENTITIES:
            # Word boundary pour éviter faux positifs
            if re.search(r'\b' + re.escape(entity) + r'\b', text_upper):
                return True

        return False

    def _check_language(self, tweet: RawTweet) -> bool:
        """Accepter seulement EN pour l'instant"""
        return tweet.lang == 'en'

    def _is_duplicate(self, tweet: RawTweet) -> bool:
        """Détection de duplication/copie"""
        if len(self.recent_tweets_cache) == 0:
            self._add_to_cache(tweet)
            return False

        # Calcul similarité avec tweets récents
        try:
            recent_texts = [t.text for t in self.recent_tweets_cache]
            all_texts = recent_texts + [tweet.text]

            vectors = self.vectorizer.fit_transform(all_texts)
            similarities = cosine_similarity(vectors[-1:], vectors[:-1])[0]

            max_similarity = np.max(similarities)
            if max_similarity > SPAM_REPETITION_THRESHOLD:
                return True

        except Exception:
            # Si erreur dans vectorisation, on laisse passer
            pass

        self._add_to_cache(tweet)
        return False

    def _add_to_cache(self, tweet: RawTweet):
        """Ajouter au cache de déduplication"""
        self.recent_tweets_cache.append(tweet)
        if len(self.recent_tweets_cache) > self.cache_max_size:
            self.recent_tweets_cache.pop(0)


class EntityExtractor:
    """Extraction des entités pertinentes depuis le texte"""

    @staticmethod
    def extract(tweet: RawTweet) -> List[str]:
        """Retourne liste d'entités détectées"""
        entities = []
        text_upper = tweet.text.upper()

        # 1. Depuis cashtags
        for cashtag in tweet.cashtags:
            clean = cashtag.upper().replace('$', '')
            if clean in ALL_ENTITIES:
                entities.append(clean)

        # 2. Depuis texte (word boundaries)
        for entity in ALL_ENTITIES:
            if re.search(r'\b' + re.escape(entity) + r'\b', text_upper):
                if entity not in entities:
                    entities.append(entity)

        return entities
