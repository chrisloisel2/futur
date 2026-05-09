"""
Analyse sémantique minimale sans sur-interprétation.
Classification stricte uniquement, pas de génération.
"""

import re
from typing import Tuple
from transformers import pipeline
import torch

from models import SentimentDirection, InformationType, CertaintyLevel, RawTweet


class SentimentAnalyzer:
    """Classification de sentiment directionnelle pour crypto"""

    def __init__(self, model_name: str = "cardiffnlp/twitter-roberta-base-sentiment-latest"):
        """Utiliser modèle pré-entraîné spécialisé Twitter"""
        self.classifier = pipeline(
            "sentiment-analysis",
            model=model_name,
            device=0 if torch.cuda.is_available() else -1
        )

        # Mots-clés directionnels crypto-spécifiques
        self.bullish_keywords = [
            "moon", "bullish", "pump", "rally", "breakout", "buy",
            "long", "support", "accumulate", "hodl", "ATH", "new high",
            "approval", "adoption", "partnership", "upgrade"
        ]

        self.bearish_keywords = [
            "dump", "bearish", "crash", "sell", "short", "resistance",
            "reject", "decline", "fear", "panic", "hack", "exploit",
            "regulation", "ban", "warning", "investigation"
        ]

    def analyze(self, tweet: RawTweet) -> Tuple[SentimentDirection, float]:
        """
        Retourne (direction, strength)
        strength ∈ [0, 1]
        """
        text = self._preprocess(tweet.text)

        # 1. Détection par mots-clés (rapide et déterministe)
        keyword_direction, keyword_strength = self._keyword_sentiment(text)

        # 2. ML classification (plus nuancé)
        try:
            ml_result = self.classifier(text[:512])[0]  # limite longueur
            ml_direction, ml_strength = self._parse_ml_result(ml_result)
        except Exception:
            # Fallback sur keywords si erreur ML
            return keyword_direction, keyword_strength

        # 3. Combinaison (priorité aux keywords pour crypto)
        if keyword_strength > 0.5:
            # Keywords forts = fiable pour crypto
            return keyword_direction, keyword_strength
        else:
            # Sinon utiliser ML
            return ml_direction, ml_strength

    def _preprocess(self, text: str) -> str:
        """Nettoyage minimal du texte"""
        # Retirer URLs
        text = re.sub(r'http\S+', '', text)
        # Retirer mentions excessives
        text = re.sub(r'(@\w+ ){3,}', '', text)
        return text.strip()

    def _keyword_sentiment(self, text: str) -> Tuple[SentimentDirection, float]:
        """Sentiment basé mots-clés crypto"""
        text_lower = text.lower()

        bullish_count = sum(1 for kw in self.bullish_keywords if kw in text_lower)
        bearish_count = sum(1 for kw in self.bearish_keywords if kw in text_lower)

        total = bullish_count + bearish_count

        if total == 0:
            return SentimentDirection.NEUTRAL, 0.0

        if bullish_count > bearish_count:
            strength = bullish_count / (total + 1)  # smooth
            return SentimentDirection.BULLISH, min(strength, 1.0)
        elif bearish_count > bullish_count:
            strength = bearish_count / (total + 1)
            return SentimentDirection.BEARISH, min(strength, 1.0)
        else:
            return SentimentDirection.NEUTRAL, 0.3

    def _parse_ml_result(self, result: dict) -> Tuple[SentimentDirection, float]:
        """Parser résultat du modèle HF"""
        label = result['label'].lower()
        score = result['score']

        if 'positive' in label:
            return SentimentDirection.BULLISH, score
        elif 'negative' in label:
            return SentimentDirection.BEARISH, score
        else:
            return SentimentDirection.NEUTRAL, score


class InformationClassifier:
    """Classification du type d'information"""

    def classify(self, tweet: RawTweet) -> InformationType:
        """Détermine le type: rumeur, annonce, confirmation, opinion"""

        text_lower = tweet.text.lower()

        # Patterns pour chaque type
        rumor_patterns = [
            r'\bi heard\b', r'\brumor', r'\breport', r'\ballegedly\b',
            r'\bsources say\b', r'\bapparently\b', r'\bmight\b', r'\bcould\b'
        ]

        announcement_patterns = [
            r'\bannouncing\b', r'\bofficial\b', r'\blaunching\b',
            r'\bbreaking:', r'\bjust announced\b', r'\bnew:\b'
        ]

        confirmation_patterns = [
            r'\bconfirmed\b', r'\bverified\b', r'\bproven\b',
            r'\bfact\b', r'\bactual\b', r'\bcertainly\b'
        ]

        # Vérifier dans l'ordre de priorité
        for pattern in confirmation_patterns:
            if re.search(pattern, text_lower):
                return InformationType.CONFIRMATION

        for pattern in announcement_patterns:
            if re.search(pattern, text_lower):
                return InformationType.ANNOUNCEMENT

        for pattern in rumor_patterns:
            if re.search(pattern, text_lower):
                return InformationType.RUMOR

        # Par défaut = opinion
        return InformationType.OPINION


class CertaintyClassifier:
    """Classification du degré de certitude"""

    def classify(self, tweet: RawTweet) -> CertaintyLevel:
        """Détermine certitude: low, medium, high"""

        text_lower = tweet.text.lower()

        # Patterns certitude élevée
        high_certainty = [
            r'\bconfirmed\b', r'\bdefinitely\b', r'\bcertainly\b',
            r'\bproven\b', r'\b100%\b', r'\bguaranteed\b',
            r'\bwithout doubt\b', r'\bofficial\b'
        ]

        # Patterns certitude faible
        low_certainty = [
            r'\bmaybe\b', r'\bmight\b', r'\bcould\b', r'\bperhaps\b',
            r'\bpossibly\b', r'\brumor\b', r'\ballegedly\b',
            r'\bi think\b', r'\bimo\b', r'\bimho\b', r'\bseems\b'
        ]

        # Check high certainty
        for pattern in high_certainty:
            if re.search(pattern, text_lower):
                return CertaintyLevel.HIGH

        # Check low certainty
        for pattern in low_certainty:
            if re.search(pattern, text_lower):
                return CertaintyLevel.LOW

        # Par défaut = medium
        return CertaintyLevel.MEDIUM


class SemanticProcessor:
    """Orchestration de l'analyse sémantique complète"""

    def __init__(self):
        self.sentiment_analyzer = SentimentAnalyzer()
        self.info_classifier = InformationClassifier()
        self.certainty_classifier = CertaintyClassifier()

    def process(self, tweet: RawTweet) -> dict:
        """
        Retourne dict avec toutes les classifications
        """
        direction, strength = self.sentiment_analyzer.analyze(tweet)
        info_type = self.info_classifier.classify(tweet)
        certainty = self.certainty_classifier.classify(tweet)

        return {
            "sentiment_direction": direction,
            "sentiment_strength": strength,
            "information_type": info_type,
            "certainty_level": certainty
        }
