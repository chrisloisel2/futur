"""
Filtrage dur des articles NEWS avant toute analyse.
Rejeter immédiatement sources non fiables, opinions, reprises.
"""

import re
from datetime import datetime, timedelta
from typing import List, Tuple, Optional
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from models import RawNewsArticle, SourceTier
from config import (
    SOURCE_TIERS,
    OFFICIAL_SOURCES,
    MIN_ARTICLE_LENGTH,
    REJECTED_TYPES,
    DUPLICATION_THRESHOLD,
    ALL_ENTITIES,
    NEWS_SEARCH_LANGUAGES
)


class NewsFilter:
    """Filtrage strict multi-critères pour articles NEWS"""

    def __init__(self):
        self.vectorizer = TfidfVectorizer(max_features=200, stop_words='english')
        self.recent_articles_cache = []  # déduplication
        self.cache_max_size = 500

    def filter(self, article: RawNewsArticle) -> Tuple[bool, List[str]]:
        """
        Retourne (passed, rejection_reasons)
        Si passed=False, l'article est immédiatement rejeté.
        """
        reasons = []

        # 1. Source identifiée et crédible
        if not self._check_source_credibility(article):
            reasons.append("unidentified_or_unreliable_source")

        # 2. Type d'article (pas opinion/editorial)
        if self._is_opinion_piece(article):
            reasons.append("opinion_editorial_piece")

        # 3. Longueur minimale
        if not self._check_length(article):
            reasons.append("article_too_short")

        # 4. Langue supportée
        if not self._check_language(article):
            reasons.append("unsupported_language")

        # 5. Entité pertinente
        if not self._has_relevant_entity(article):
            reasons.append("no_relevant_entity")

        # 6. Duplication
        if self._is_duplicate(article):
            reasons.append("duplicate_content")

        passed = len(reasons) == 0
        return passed, reasons

    def _check_source_credibility(self, article: RawNewsArticle) -> bool:
        """Vérifier si source est identifiée et crédible"""
        source_name = article.source.strip()

        if not source_name or len(source_name) < 3:
            return False

        # Check si dans tiers connus
        for tier_name, tier_data in SOURCE_TIERS.items():
            if tier_name == "tier4":  # blogs = rejet sauf whitelist
                continue

            sources_list = tier_data.get("agencies", []) or tier_data.get("financial_media", []) or tier_data.get("secondary_media", [])

            for known_source in sources_list:
                if known_source.lower() in source_name.lower():
                    return True

        # Check si source officielle
        for category, sources in OFFICIAL_SOURCES.items():
            if category == "credibility_score":
                continue
            for official in sources:
                if official.lower() in source_name.lower():
                    return True

        # Si pas reconnu = rejet
        return False

    def _is_opinion_piece(self, article: RawNewsArticle) -> bool:
        """Détecter articles opinion/editorial/tribune"""

        # Check dans catégories
        for category in article.categories:
            if category.lower() in REJECTED_TYPES:
                return True

        # Check dans titre
        title_lower = article.title.lower()
        opinion_indicators = [
            "opinion:", "editorial:", "comment:", "analysis:",
            "tribune:", "op-ed:", "viewpoint:", "perspective:",
            "i think", "in my view", "we believe"
        ]

        for indicator in opinion_indicators:
            if indicator in title_lower:
                return True

        # Check dans body (premiers 200 chars)
        body_start = article.body[:200].lower()
        for indicator in opinion_indicators:
            if indicator in body_start:
                return True

        return False

    def _check_length(self, article: RawNewsArticle) -> bool:
        """Vérifier longueur minimale"""
        return len(article.body) >= MIN_ARTICLE_LENGTH

    def _check_language(self, article: RawNewsArticle) -> bool:
        """Vérifier langue supportée"""
        return article.lang in NEWS_SEARCH_LANGUAGES

    def _has_relevant_entity(self, article: RawNewsArticle) -> bool:
        """Vérifier présence d'au moins une entité pertinente"""

        # Combiner titre + début du body
        text = (article.title + " " + article.body[:500]).lower()

        for entity in ALL_ENTITIES:
            # Case insensitive, word boundary
            pattern = r'\b' + re.escape(entity.lower()) + r'\b'
            if re.search(pattern, text):
                return True

        return False

    def _is_duplicate(self, article: RawNewsArticle) -> bool:
        """Détection de duplication/reprise sans valeur ajoutée"""

        if len(self.recent_articles_cache) == 0:
            self._add_to_cache(article)
            return False

        # Similarité titre + début body
        article_text = article.title + " " + article.body[:300]

        try:
            recent_texts = [
                a.title + " " + a.body[:300]
                for a in self.recent_articles_cache
            ]
            all_texts = recent_texts + [article_text]

            vectors = self.vectorizer.fit_transform(all_texts)
            similarities = cosine_similarity(vectors[-1:], vectors[:-1])[0]

            max_similarity = np.max(similarities)

            if max_similarity > DUPLICATION_THRESHOLD:
                return True

        except Exception:
            # Si erreur vectorisation, laisser passer
            pass

        self._add_to_cache(article)
        return False

    def _add_to_cache(self, article: RawNewsArticle):
        """Ajouter au cache de déduplication"""
        self.recent_articles_cache.append(article)
        if len(self.recent_articles_cache) > self.cache_max_size:
            self.recent_articles_cache.pop(0)


class SourceClassifier:
    """Classification de la source (tier + official)"""

    @staticmethod
    def classify_source(article: RawNewsArticle) -> Tuple[SourceTier, bool]:
        """
        Retourne (source_tier, is_official)
        """
        source_name = article.source.lower()

        # 1. Check official sources (priorité)
        for category, sources in OFFICIAL_SOURCES.items():
            if category == "credibility_score":
                continue
            for official in sources:
                if official.lower() in source_name:
                    return SourceTier.OFFICIAL, True

        # 2. Check tiers
        # Tier 1 - Agencies
        for agency in SOURCE_TIERS["tier1"]["agencies"]:
            if agency.lower() in source_name:
                return SourceTier.TIER1_AGENCY, False

        # Tier 2 - Financial media
        for media in SOURCE_TIERS["tier2"]["financial_media"]:
            if media.lower() in source_name:
                return SourceTier.TIER2_FINANCIAL, False

        # Tier 3 - Secondary
        for secondary in SOURCE_TIERS["tier3"]["secondary_media"]:
            if secondary.lower() in source_name:
                return SourceTier.TIER3_SECONDARY, False

        # Default tier 4
        return SourceTier.TIER4_BLOG, False

    @staticmethod
    def get_credibility_score(source_tier: SourceTier, is_official: bool) -> float:
        """Retourne score de crédibilité [0-1]"""

        if is_official:
            return OFFICIAL_SOURCES["credibility_score"]

        tier_mapping = {
            SourceTier.TIER1_AGENCY: SOURCE_TIERS["tier1"]["credibility_score"],
            SourceTier.TIER2_FINANCIAL: SOURCE_TIERS["tier2"]["credibility_score"],
            SourceTier.TIER3_SECONDARY: SOURCE_TIERS["tier3"]["credibility_score"],
            SourceTier.TIER4_BLOG: SOURCE_TIERS["tier4"]["credibility_score"]
        }

        return tier_mapping.get(source_tier, 0.3)


class EntityExtractor:
    """Extraction des entités pertinentes depuis l'article"""

    @staticmethod
    def extract(article: RawNewsArticle) -> List[str]:
        """Retourne liste d'entités détectées"""
        entities = []

        # Combiner titre + body
        text = (article.title + " " + article.body).lower()

        # Extraction par pattern matching
        for entity in ALL_ENTITIES:
            pattern = r'\b' + re.escape(entity.lower()) + r'\b'
            if re.search(pattern, text):
                # Normaliser (utiliser forme standard)
                if entity not in entities:
                    entities.append(entity)

        return entities


class GeographicScopeDetector:
    """Détection du scope géographique"""

    @staticmethod
    def detect(article: RawNewsArticle, entities: List[str]) -> str:
        """Retourne scope: local / regional / global"""

        # Indicateurs global
        global_indicators = [
            "worldwide", "global", "international", "all countries",
            "Federal Reserve", "ECB", "G7", "G20", "IMF", "World Bank",
            "United Nations", "Bitcoin", "Ethereum"  # crypto = global par défaut
        ]

        text = (article.title + " " + article.body[:500]).lower()

        for indicator in global_indicators:
            if indicator.lower() in text:
                return "global"

        # Indicateurs regional
        regional_indicators = [
            "European Union", "EU", "eurozone", "Asia-Pacific",
            "Latin America", "Middle East", "Africa"
        ]

        for indicator in regional_indicators:
            if indicator.lower() in text:
                return "regional"

        # Si pays spécifique mentionné uniquement = local
        return "local"
