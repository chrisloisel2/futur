"""
Twitter Signal Engine
Moteur d'ingestion et d'analyse Twitter/X pour trading crypto court-terme
"""

__version__ = "1.0.0"

from .models import (
    RawTweet,
    ProcessedTweet,
    WindowAggregation,
    TradingSignal,
    SentimentDirection,
    InformationType,
    CertaintyLevel
)

from .pipeline import TwitterSignalPipeline, StreamingPipeline
from .collector import TwitterCollector, StreamCollector
from .signals import SignalGenerator, SignalBatch, SignalAggregator

__all__ = [
    "RawTweet",
    "ProcessedTweet",
    "WindowAggregation",
    "TradingSignal",
    "SentimentDirection",
    "InformationType",
    "CertaintyLevel",
    "TwitterSignalPipeline",
    "StreamingPipeline",
    "TwitterCollector",
    "StreamCollector",
    "SignalGenerator",
    "SignalBatch",
    "SignalAggregator"
]
