"""ai/alphas — Portfolio of Alphas (Layer 2)"""

from ai.alphas.base import AlphaBase, AlphaSignal
from ai.alphas.registry import AlphaRegistry, BlendedSignal
from ai.alphas.funding_carry import FundingCarryAlpha
from ai.alphas.oi_momentum import OIMomentumAlpha
from ai.alphas.liquidation_cascade import LiquidationCascadeAlpha
from ai.alphas.sentiment_extreme import SentimentExtremeAlpha
from ai.alphas.vol_expansion import VolExpansionAlpha
from ai.alphas.overnight_drift import OvernightDriftAlpha


def build_default_registry() -> AlphaRegistry:
    """Registre par défaut avec tous les micro-alphas."""
    reg = AlphaRegistry()
    reg.register(FundingCarryAlpha())
    reg.register(OIMomentumAlpha())
    reg.register(LiquidationCascadeAlpha())
    reg.register(SentimentExtremeAlpha())
    reg.register(VolExpansionAlpha())
    reg.register(OvernightDriftAlpha())
    return reg


__all__ = [
    "AlphaBase", "AlphaSignal",
    "AlphaRegistry", "BlendedSignal",
    "FundingCarryAlpha", "OIMomentumAlpha",
    "LiquidationCascadeAlpha", "SentimentExtremeAlpha",
    "VolExpansionAlpha", "OvernightDriftAlpha",
    "build_default_registry",
]
