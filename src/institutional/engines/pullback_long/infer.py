"""
PULLBACK_LONG — replis achetables (long-only, fréquence ↑, complémentaire au TRM trend).

Ne cherche pas les gros breakouts : cherche les replis dans un régime non-hostile.
Source d'edge : oversold court terme + structure de tendance intacte (EMA55 non cassée).
Gate : PF≥1.25 · cost×2 PF≥1.05 · WR≥52% · trades/mois≥30 portefeuille.
"""
from __future__ import annotations

from typing import List, Optional

from src.institutional.engines._labels import label_forward_up
from src.institutional.engines.ml_engine import MLEngineSpec, MLSignalEngine

DEFAULT_ASSETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]

# Features pullback (disponibles dans enriched) : repli court terme + structure tendance.
FEATURES: List[str] = [
    "return_5", "return_10", "return_50",
    "distance_ema_20", "distance_ema_50", "distance_ema_200",
    "rsi_14", "rsi_20", "bb_percent_b_20", "stoch_k_20",
    "volume_ratio_20", "realized_volatility_20", "atr_pct_20",
    "momentum_score", "trend_score", "close_position_in_range",
    "efficiency_ratio_20", "choppiness_20",
]


class PullbackLongEngine(MLSignalEngine):
    def __init__(self, status: str = "SHADOW", assets: Optional[List[str]] = None, **kw):
        spec = MLEngineSpec(
            engine_id="PULLBACK_LONG",
            assets=assets or list(DEFAULT_ASSETS),
            feature_cols=FEATURES,
            label_fn=lambda df, h, c: label_forward_up(df, h, c, threshold=0.004),
            horizon_hours=8.0, tau_a=0.58, tau_b=0.50,
            expected_move=0.025, status=status, extra_cols=["close"],
        )
        super().__init__(spec)
