"""
LIQUIDATION_REBOUND — acheter la capitulation (convexité, faible corrélation au trend).

⚠️ DONNÉES : le feed liquidations/OI/taker n'est pas dans enriched (seul
funding_rate l'est). V1 utilise donc un PROXY de capitulation basé prix/vol
(chute brutale + volume extrême + expansion de range + funding) au lieu des
vraies liquidations. À remplacer par le feed liquidations/OI dès qu'il est ingéré.

Horizon court (6h). Gate : PF≥1.40 · avg_win/loss≥1.5 · WR≥45% · cost×2 PF≥1.15.
"""
from __future__ import annotations

from typing import List, Optional

from src.institutional.engines._labels import label_rebound
from src.institutional.engines.ml_engine import MLEngineSpec, MLSignalEngine

DEFAULT_ASSETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]

# Proxy capitulation : chute/vol/volume + funding (pas de vraies liquidations).
FEATURES: List[str] = [
    "return_5", "return_10", "realized_volatility_20", "atr_pct_20",
    "volume_ratio_20", "bb_percent_b_20", "bb_width_20", "rsi_14",
    "distance_ema_20", "distance_ema_50", "high_low_range_pct",
    "close_position_in_range", "funding_rate", "funding_z_7d", "body_to_range",
]


class LiquidationReboundEngine(MLSignalEngine):
    def __init__(self, status: str = "SHADOW", assets: Optional[List[str]] = None, **kw):
        spec = MLEngineSpec(
            engine_id="LIQUIDATION_REBOUND",
            assets=assets or list(DEFAULT_ASSETS),
            feature_cols=FEATURES,
            label_fn=lambda df, h, c: label_rebound(df, h, c, threshold=0.0),
            horizon_hours=6.0, tau_a=0.60, tau_b=0.50,
            expected_move=0.04, status=status, extra_cols=["close"],
        )
        super().__init__(spec)
