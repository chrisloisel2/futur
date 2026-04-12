"""
Pipeline principal d'ingestion NEWS → Signaux
Orchestration de tous les composants.
"""

from datetime import datetime
from typing import List, Dict
import logging

from models import RawNewsArticle, ProcessedNewsArticle, IngestionStats, EventType
from filters import NewsFilter, SourceClassifier, EntityExtractor, GeographicScopeDetector
from enrichment import NewsEnricher, EventClustering
from classification import SemanticProcessor
from aggregation import MultiWindowNewsAggregator, EventClusterAnalyzer
from signals import NewsSignalGenerator, NewsSignalBatch


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class NewsSignalPipeline:
    """Pipeline complet de traitement NEWS"""

    def __init__(self):
        # Composants
        self.filter = NewsFilter()
        self.source_classifier = SourceClassifier()
        self.entity_extractor = EntityExtractor()
        self.geo_detector = GeographicScopeDetector()
        self.enricher = NewsEnricher()
        self.semantic_processor = SemanticProcessor()
        self.aggregator = MultiWindowNewsAggregator()
        self.signal_generator = NewsSignalGenerator()
        self.clustering = EventClustering()
        self.cluster_analyzer = EventClusterAnalyzer()

        # Stats
        self.stats = {
            "articles_collected": 0,
            "articles_filtered": 0,
            "articles_processed": 0,
            "signals_generated": 0,
            "tier_breakdown": {},
            "rejection_breakdown": {},
            "event_type_breakdown": {}
        }

    def process_batch(self, raw_articles: List[RawNewsArticle]) -> NewsSignalBatch:
        """
        Traiter un batch d'articles NEWS → génération de signaux

        Pipeline:
        1. Filtrage dur
        2. Classification source
        3. Extraction entités
        4. Enrichissement méta-info
        5. Analyse sémantique
        6. Clustering événements
        7. Agrégation temporelle
        8. Génération signaux
        """

        logger.info(f"Processing batch of {len(raw_articles)} articles")

        self.stats["articles_collected"] += len(raw_articles)

        # STEP 1: Filtrage
        filtered_articles = []
        for article in raw_articles:
            passed, reasons = self.filter.filter(article)

            if not passed:
                self.stats["articles_filtered"] += 1
                for reason in reasons:
                    self.stats["rejection_breakdown"][reason] = \
                        self.stats["rejection_breakdown"].get(reason, 0) + 1
                continue

            filtered_articles.append(article)

        logger.info(f"Filtered: {len(filtered_articles)} articles passed")

        # STEP 2: Classification et enrichissement
        processed_articles = []
        for article in filtered_articles:
            # Classification source
            source_tier, is_official = self.source_classifier.classify_source(article)

            # Tier breakdown
            tier_name = source_tier.value
            self.stats["tier_breakdown"][tier_name] = \
                self.stats["tier_breakdown"].get(tier_name, 0) + 1

            # Extraction entités
            entities = self.entity_extractor.extract(article)

            # Enrichissement
            processed = self.enricher.enrich(article, source_tier, is_official)
            processed.detected_entities = entities

            # Geo scope
            geo_scope = self.geo_detector.detect(article, entities)
            from models import GeographicScope
            processed.geographic_scope = GeographicScope(geo_scope)

            # Analyse sémantique
            semantic_result = self.semantic_processor.process(article)
            processed.event_types = semantic_result["event_types"]
            processed.event_status = semantic_result["event_status"]
            processed.surprise_level = semantic_result["surprise_level"]

            # Event type breakdown
            for event_type in processed.event_types:
                self.stats["event_type_breakdown"][event_type.value] = \
                    self.stats["event_type_breakdown"].get(event_type.value, 0) + 1

            # Clustering
            event_hash = self.enricher._calculate_event_hash(article)
            cluster_id = self.clustering.add_to_cluster(article.article_id, event_hash)
            processed.event_cluster_id = cluster_id

            processed_articles.append(processed)

        self.stats["articles_processed"] += len(processed_articles)
        logger.info(f"Processed: {len(processed_articles)} articles enriched")

        # STEP 3: Agrégation temporelle
        timestamp = datetime.utcnow()

        # Récupérer toutes les entités détectées
        all_entities = set()
        all_event_types = set()
        for article in processed_articles:
            all_entities.update(article.detected_entities)
            all_event_types.update(article.event_types)

        # Limiter aux entités/events principaux pour performance
        main_entities = list(all_entities)[:20]  # top 20 entités
        main_events = list(all_event_types)

        # Agréger
        aggregations = self.aggregator.aggregate_all(
            processed_articles,
            main_entities,
            main_events,
            timestamp
        )

        logger.info(f"Aggregated: {len(aggregations)} entities across windows")

        # STEP 4: Génération signaux
        signal_batch = NewsSignalBatch()

        for entity, event_dict in aggregations.items():
            for event_type_str, windows in event_dict.items():
                for window_name, agg in windows.items():
                    # Calculer latence moyenne
                    relevant_articles = [
                        a for a in processed_articles
                        if entity in a.detected_entities
                    ]

                    if relevant_articles:
                        avg_latency = sum(a.latency_ms for a in relevant_articles) / len(relevant_articles)
                        has_corrections = any(a.raw.is_correction for a in relevant_articles)
                    else:
                        avg_latency = 0
                        has_corrections = False

                    # Générer signal
                    signal = self.signal_generator.generate(agg, avg_latency, has_corrections)
                    if signal:
                        signal_batch.add(signal)

        self.stats["signals_generated"] += len(signal_batch.signals)
        logger.info(f"Generated: {len(signal_batch.signals)} signals")

        return signal_batch

    def get_stats(self) -> IngestionStats:
        """Retourne statistiques d'ingestion"""
        return IngestionStats(
            timestamp=datetime.utcnow(),
            articles_collected=self.stats["articles_collected"],
            articles_filtered=self.stats["articles_filtered"],
            articles_processed=self.stats["articles_processed"],
            avg_latency_ms=0.0,  # TODO
            signals_generated=self.stats["signals_generated"],
            tier_breakdown=self.stats["tier_breakdown"],
            rejection_breakdown=self.stats["rejection_breakdown"],
            event_type_breakdown=self.stats["event_type_breakdown"]
        )

    def reset_stats(self):
        """Reset statistiques"""
        self.stats = {
            "articles_collected": 0,
            "articles_filtered": 0,
            "articles_processed": 0,
            "signals_generated": 0,
            "tier_breakdown": {},
            "rejection_breakdown": {},
            "event_type_breakdown": {}
        }


class StreamingNewsPipeline:
    """Pipeline pour streaming temps réel NEWS"""

    def __init__(self, buffer_size: int = 50):
        self.pipeline = NewsSignalPipeline()
        self.buffer = []
        self.buffer_size = buffer_size

    def add_article(self, article: RawNewsArticle) -> NewsSignalBatch:
        """
        Ajouter article au buffer
        Traiter quand buffer plein
        """
        self.buffer.append(article)

        if len(self.buffer) >= self.buffer_size:
            return self.flush()

        return NewsSignalBatch()  # vide

    def flush(self) -> NewsSignalBatch:
        """Forcer traitement du buffer"""
        if not self.buffer:
            return NewsSignalBatch()

        signals = self.pipeline.process_batch(self.buffer)
        self.buffer = []

        return signals
