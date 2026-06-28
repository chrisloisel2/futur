"""
CARRY_BASIS — rendement en marché plat (funding-aware long), faible corrélation au trend.

Sécurité (cohérent SHORT_REJECTED) :
    SHORT_DIRECTIONAL_ENABLED = False
    HEDGE_SHORT_ENABLED       = True   (jambe de neutralisation uniquement)
    NAKED_SHORT_ALLOWED       = False

⚠️ V1 : le backtester portefeuille est long-only ; le mode delta-neutral
(long spot + short perp hedge) n'est pas simulé ici. CARRY_BASIS V1 émet un
LONG funding-aware (long quand funding/structure favorisent le portage) sur
horizon long (24h). Le hedge lié est géré par le RiskGovernor (état "hedged").
Horizon 24h. Gate : rendement net>0.5%/mois · DD<1.5% · aucun short nu.
"""
from __future__ import annotations

from typing import List, Optional

from src.institutional.engines._labels import label_forward_up
from src.institutional.engines.ml_engine import MLEngineSpec, MLSignalEngine

SHORT_DIRECTIONAL_ENABLED = False
HEDGE_SHORT_ENABLED = True
NAKED_SHORT_ALLOWED = False

DEFAULT_ASSETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]

FEATURES: List[str] = [
    "funding_rate", "funding_z_7d", "funding_z_30d", "funding_extreme",
    "realized_volatility_20", "distance_ema_200", "trend_score", "momentum_score",
    "return_50", "efficiency_ratio_20", "noise_ratio_20", "atr_pct_20",
]


class CarryBasisEngine(MLSignalEngine):
    def __init__(self, status: str = "SHADOW", assets: Optional[List[str]] = None, **kw):
        assert not (SHORT_DIRECTIONAL_ENABLED or NAKED_SHORT_ALLOWED), "Short nu interdit"
        spec = MLEngineSpec(
            engine_id="CARRY_BASIS",
            assets=assets or list(DEFAULT_ASSETS),
            feature_cols=FEATURES,
            label_fn=lambda df, h, c: label_forward_up(df, h, c, threshold=0.0),
            horizon_hours=24.0, tau_a=0.56, tau_b=0.50,
            expected_move=0.02, status=status, extra_cols=["close"],
        )
        super().__init__(spec)
