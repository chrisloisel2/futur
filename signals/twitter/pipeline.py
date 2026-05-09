"""
Pipeline principal d'ingestion Twitter → Signaux
Orchestration de tous les composants.
"""

from datetime import datetime
from typing import List, Dict
import logging

from .models import RawTweet, ProcessedTweet, IngestionStats
from filters import TweetFilter, EntityExtractor
from enrichment import TweetEnricher
from sentiment import SemanticProcessor
from aggregation import MultiWindowAggregator
from signals import SignalGenerator, SignalBatch
from config import ALL_ENTITIES


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TwitterSignalPipeline:
    """Pipeline complet de traitement"""

    def __init__(self):
        # Composants
        self.filter = TweetFilter()
        self.entity_extractor = EntityExtractor()
        self.enricher = TweetEnricher()
        self.semantic_processor = SemanticProcessor()
        self.aggregator = MultiWindowAggregator()
        self.signal_generator = SignalGenerator()

        # Stats
        self.stats = {
            "tweets_collected": 0,
            "tweets_filtered": 0,
            "tweets_processed": 0,
            "signals_generated": 0,
            "rejection_breakdown": {}
        }

    def process_batch(self, raw_tweets: List[RawTweet]) -> SignalBatch:
        """
        Traiter un batch de tweets → génération de signaux

        Pipeline:
        1. Filtrage dur
        2. Extraction entités
        3. Enrichissement méta-info
        4. Analyse sémantique
        5. Agrégation temporelle
        6. Génération signaux
        """

        logger.info(f"Processing batch of {len(raw_tweets)} tweets")

        self.stats["tweets_collected"] += len(raw_tweets)

        # STEP 1: Filtrage
        filtered_tweets = []
        for raw_tweet in raw_tweets:
            passed, reasons = self.filter.filter(raw_tweet)

            if not passed:
                self.stats["tweets_filtered"] += 1
                for reason in reasons:
                    self.stats["rejection_breakdown"][reason] = \
                        self.stats["rejection_breakdown"].get(reason, 0) + 1
                continue

            filtered_tweets.append(raw_tweet)

        logger.info(f"Filtered: {len(filtered_tweets)} tweets passed")

        # STEP 2: Extraction entités
        processed_tweets = []
        for raw_tweet in filtered_tweets:
            entities = self.entity_extractor.extract(raw_tweet)

            # STEP 3: Enrichissement
            processed = self.enricher.enrich(raw_tweet)
            processed.detected_entities = entities

            # STEP 4: Analyse sémantique
            semantic_result = self.semantic_processor.process(raw_tweet)
            processed.sentiment_direction = semantic_result["sentiment_direction"]
            processed.sentiment_strength = semantic_result["sentiment_strength"]
            processed.information_type = semantic_result["information_type"]
            processed.certainty_level = semantic_result["certainty_level"]

            processed_tweets.append(processed)

            # MAJ historique engagement
            engagement = (
                raw_tweet.likes +
                raw_tweet.retweets * 2 +
                raw_tweet.replies * 0.5
            )
            self.enricher.update_author_history(raw_tweet.author_id, engagement)

        self.stats["tweets_processed"] += len(processed_tweets)
        logger.info(f"Processed: {len(processed_tweets)} tweets enriched")

        # STEP 5: Agrégation temporelle
        timestamp = datetime.utcnow()

        # Récupérer toutes les entités détectées
        all_detected_entities = set()
        for tweet in processed_tweets:
            all_detected_entities.update(tweet.detected_entities)

        # Agréger sur toutes les fenêtres
        aggregations = self.aggregator.aggregate_all(
            processed_tweets,
            list(all_detected_entities),
            timestamp
        )

        logger.info(f"Aggregated: {len(aggregations)} entities across windows")

        # STEP 6: Génération signaux
        signal_batch = SignalBatch()

        for entity, windows in aggregations.items():
            for window_name, agg in windows.items():
                # Calculer latence moyenne pour cette fenêtre
                window_tweets = [
                    t for t in processed_tweets
                    if entity in t.detected_entities
                ]

                if window_tweets:
                    avg_latency = sum(t.latency_ms for t in window_tweets) / len(window_tweets)
                else:
                    avg_latency = 0

                # Générer signal
                signal = self.signal_generator.generate(agg, avg_latency)
                if signal:
                    signal_batch.add(signal)

        self.stats["signals_generated"] += len(signal_batch.signals)
        logger.info(f"Generated: {len(signal_batch.signals)} signals")

        return signal_batch

    def get_stats(self) -> IngestionStats:
        """Retourne statistiques d'ingestion"""
        return IngestionStats(
            timestamp=datetime.utcnow(),
            tweets_collected=self.stats["tweets_collected"],
            tweets_filtered=self.stats["tweets_filtered"],
            tweets_processed=self.stats["tweets_processed"],
            avg_latency_ms=0.0,  # TODO: calculer
            api_calls_used=0,  # TODO: tracker
            signals_generated=self.stats["signals_generated"],
            rejection_breakdown=self.stats["rejection_breakdown"]
        )

    def reset_stats(self):
        """Reset statistiques"""
        self.stats = {
            "tweets_collected": 0,
            "tweets_filtered": 0,
            "tweets_processed": 0,
            "signals_generated": 0,
            "rejection_breakdown": {}
        }


class StreamingPipeline:
    """Pipeline pour streaming temps réel"""

    def __init__(self, buffer_size: int = 100):
        self.pipeline = TwitterSignalPipeline()
        self.buffer = []
        self.buffer_size = buffer_size

    def add_tweet(self, tweet: RawTweet) -> SignalBatch:
        """
        Ajouter tweet au buffer
        Traiter quand buffer plein
        """
        self.buffer.append(tweet)

        if len(self.buffer) >= self.buffer_size:
            return self.flush()

        return SignalBatch()  # vide

    def flush(self) -> SignalBatch:
        """Forcer traitement du buffer"""
        if not self.buffer:
            return SignalBatch()

        signals = self.pipeline.process_batch(self.buffer)
        self.buffer = []

        return signals
