"""Deep learning models for time series forecasting."""

from .dlinear import DLinear
from .timesnet import TimesNet, TimesBlock
from .transformer import NonStationaryTransformer
from .backbone import TimeSeriesBackbone
from .training import TimeSeriesLightningModule
from .fusion import (
    AdvancedFusionModule,
    FusionStrategy,
    MetaFeatureExtractor,
    MarketRegimeDetector,
    CrossBranchAttention,
    AdaptiveGating,
)
from .decision_transformer import (
    DecisionTransformer,
    CausalSelfAttention,
    TransformerBlock,
    TrajectoryDataset,
    compute_returns_to_go,
    reward_shaping,
    create_trading_trajectories,
    train_decision_transformer,
)

__all__ = [
    "DLinear",
    "TimesNet",
    "TimesBlock",
    "NonStationaryTransformer",
    "TimeSeriesBackbone",
    "TimeSeriesLightningModule",
    # Fusion
    "AdvancedFusionModule",
    "FusionStrategy",
    "MetaFeatureExtractor",
    "MarketRegimeDetector",
    "CrossBranchAttention",
    "AdaptiveGating",
    # Decision Transformer
    "DecisionTransformer",
    "CausalSelfAttention",
    "TransformerBlock",
    "TrajectoryDataset",
    "compute_returns_to_go",
    "reward_shaping",
    "create_trading_trajectories",
    "train_decision_transformer",
]
