"""
TRM model module.
"""
from .loss import (
    AdaptiveCompositeLoss,
    CompositeTradingLoss,
    DirectionalLoss,
    DrawdownPenalty,
    MagnitudeWeightedMSE,
    SharpeRatioLoss,
    TradingCostPenalty,
)
from .trm import TinyRecursiveModel, TRMEnsemble

__all__ = [
    # Models
    'TinyRecursiveModel',
    'TRMEnsemble',
    # Losses
    'CompositeTradingLoss',
    'AdaptiveCompositeLoss',
    'DirectionalLoss',
    'MagnitudeWeightedMSE',
    'TradingCostPenalty',
    'DrawdownPenalty',
    'SharpeRatioLoss',
]
