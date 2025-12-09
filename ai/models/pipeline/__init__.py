"""
Pipeline package for crypto data processing - Production Ready.

Major improvements:
- Robust error handling with circuit breaker
- Redis cache with local fallback
- Data leakage prevention in normalization
- Comprehensive data quality validation
- Memory optimization for large datasets
- Structured logging and metrics
- Full test coverage
- Advanced preprocessing with fractional differentiation
- Deep learning models (DLinear, TimesNet, Transformer)
- Tabular models (FT-Transformer, TabNet, benchmarking)
- Advanced fusion (cross-attention, adaptive gating, regime detection)
- Decision Transformer (RL via sequence modeling, RTG conditioning)
"""

from .cache import RedisCache
from .config_loader import ConfigLoader, get_config
from .data_collection import DEFAULT_COLLECTION_CONFIG, TradingDataCollector
from .data_quality import DataQualityValidator
from .data_sources import CcxtDataSource, GlassnodeClient, merge_onchain_asof, ohlcv_to_df
from .features import build_feature_set
from .logging_config import MetricsLogger, get_metrics, setup_logging
from .memory_optimizer import downsample_old_data, optimize_dtypes
from .normalization import AdaptiveNormalizer
from .backtest import (
    BacktestConfig,
    Backtester,
    WalkForwardValidator,
    plot_backtest_results,
    plot_walk_forward_results,
    print_metrics,
)
from .preprocessor import (
    AdvancedPreprocessor,
    FeatureSelector,
    FractionalDifferentiator,
    PurgedWalkForward,
    RollingNormalizer,
    TemporalInterpolator,
)

# Deep learning models (optional - requires torch)
try:
    from .models import (
        DLinear,
        TimesNet,
        NonStationaryTransformer,
        TimeSeriesBackbone,
        TimeSeriesLightningModule,
        # Fusion
        AdvancedFusionModule,
        FusionStrategy,
        MetaFeatureExtractor,
        MarketRegimeDetector,
        CrossBranchAttention,
        AdaptiveGating,
    )
    from .models.tabular import (
        FTTransformer,
        TabNetModel,
        TabularBenchmark,
        HAS_TABNET,
    )
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

__version__ = "2.5.0"

__all__ = [
    # Data sources
    "CcxtDataSource",
    "GlassnodeClient",
    "ohlcv_to_df",
    "merge_onchain_asof",
    "TradingDataCollector",
    "DEFAULT_COLLECTION_CONFIG",
    # Features
    "build_feature_set",
    # Normalization
    "AdaptiveNormalizer",
    # Backtesting
    "Backtester",
    "BacktestConfig",
    "WalkForwardValidator",
    "plot_backtest_results",
    "plot_walk_forward_results",
    "print_metrics",
    # Cache
    "RedisCache",
    # Configuration
    "ConfigLoader",
    "get_config",
    # Data quality
    "DataQualityValidator",
    # Memory optimization
    "optimize_dtypes",
    "downsample_old_data",
    # Logging
    "setup_logging",
    "MetricsLogger",
    "get_metrics",
    # Advanced preprocessing
    "AdvancedPreprocessor",
    "FractionalDifferentiator",
    "RollingNormalizer",
    "FeatureSelector",
    "TemporalInterpolator",
    "PurgedWalkForward",
]
