"""
News Signal Engine
Moteur d'ingestion et d'analyse NEWS pour trading crypto
"""

__version__ = "1.0.0"

from .models import (
    RawNewsArticle,
    ProcessedNewsArticle,
    NewsWindowAggregation,
    NewsSignal,
    EventType,
    EventStatus,
    SurpriseLevel,
    GeographicScope,
    SourceTier
)

from .pipeline import NewsSignalPipeline, StreamingNewsPipeline
from .collector import NewsAPICollector, NewsdataIOCollector, MultiSourceCollector
from .signals import NewsSignalGenerator, NewsSignalBatch, MarketImpactEstimator

__all__ = [
    "RawNewsArticle",
    "ProcessedNewsArticle",
    "NewsWindowAggregation",
    "NewsSignal",
    "EventType",
    "EventStatus",
    "SurpriseLevel",
    "GeographicScope",
    "SourceTier",
    "NewsSignalPipeline",
    "StreamingNewsPipeline",
    "NewsAPICollector",
    "NewsdataIOCollector",
    "MultiSourceCollector",
    "NewsSignalGenerator",
    "NewsSignalBatch",
    "MarketImpactEstimator"
]
