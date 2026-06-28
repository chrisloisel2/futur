"""
src/institutional/features/feature_registry.py
─────────────────────────────────────────────────────────────────────────────
Registre des features institutionnelles.

Chaque feature est documentée avec ses propriétés :
  - causal_safe : garantit aucun lookahead
  - inputs : colonnes sources requises
  - window : fenêtre temporelle (en barres)
  - asset_scope : "single" ou "cross_section"
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(frozen=True)
class FeatureDef:
    name: str
    description: str
    family: str          # "returns" | "volatility" | "trend" | "derivatives" | "cross_section"
    inputs: List[str]
    window_bars: int
    causal_safe: bool    # vérifié par code review
    asset_scope: str     # "single" | "cross_section"
    version: str = "1.0"
    owner: str = "institutional_engine"
    requires_cross_section: bool = False

    def validate(self) -> None:
        if not self.causal_safe:
            raise ValueError(f"Feature {self.name!r} non validée causalement")


FEATURE_REGISTRY: Dict[str, FeatureDef] = {

    # ─── Returns ──────────────────────────────────────────────────────────────
    "log_ret_1h": FeatureDef(
        name="log_ret_1h", description="Log-return 1 barre",
        family="returns", inputs=["close"], window_bars=1,
        causal_safe=True, asset_scope="single",
    ),
    "log_ret_24h": FeatureDef(
        name="log_ret_24h", description="Log-return 24h (1 jour)",
        family="returns", inputs=["close"], window_bars=24,
        causal_safe=True, asset_scope="single",
    ),
    "log_ret_168h": FeatureDef(
        name="log_ret_168h", description="Log-return 168h (7 jours)",
        family="returns", inputs=["close"], window_bars=168,
        causal_safe=True, asset_scope="single",
    ),
    "ret_zscore_60h": FeatureDef(
        name="ret_zscore_60h", description="Z-score rolling du log-ret 1h sur 60 barres",
        family="returns", inputs=["close"], window_bars=60,
        causal_safe=True, asset_scope="single",
    ),
    "ret_zscore_240h": FeatureDef(
        name="ret_zscore_240h", description="Z-score rolling du log-ret 1h sur 240 barres",
        family="returns", inputs=["close"], window_bars=240,
        causal_safe=True, asset_scope="single",
    ),

    # ─── Volatilité ───────────────────────────────────────────────────────────
    "rv_24h": FeatureDef(
        name="rv_24h", description="Volatilité réalisée 24h (annualisée)",
        family="volatility", inputs=["close"], window_bars=24,
        causal_safe=True, asset_scope="single",
    ),
    "rv_168h": FeatureDef(
        name="rv_168h", description="Volatilité réalisée 168h (annualisée)",
        family="volatility", inputs=["close"], window_bars=168,
        causal_safe=True, asset_scope="single",
    ),
    "parkinson_vol_24h": FeatureDef(
        name="parkinson_vol_24h", description="Vol Parkinson 24h",
        family="volatility", inputs=["high", "low"], window_bars=24,
        causal_safe=True, asset_scope="single",
    ),
    "vol_regime": FeatureDef(
        name="vol_regime", description="Régime de vol discret 0/1/2",
        family="volatility", inputs=["close"], window_bars=24 * 252,
        causal_safe=True, asset_scope="single",
    ),

    # ─── Trend / Momentum ─────────────────────────────────────────────────────
    "ema_dist_8h": FeatureDef(
        name="ema_dist_8h", description="Distance EMA-8 normalisée",
        family="trend", inputs=["close"], window_bars=8,
        causal_safe=True, asset_scope="single",
    ),
    "ema_dist_55h": FeatureDef(
        name="ema_dist_55h", description="Distance EMA-55 normalisée",
        family="trend", inputs=["close"], window_bars=55,
        causal_safe=True, asset_scope="single",
    ),
    "mom_24h": FeatureDef(
        name="mom_24h", description="Momentum 24h",
        family="trend", inputs=["close"], window_bars=24,
        causal_safe=True, asset_scope="single",
    ),
    "mom_168h": FeatureDef(
        name="mom_168h", description="Momentum 7j",
        family="trend", inputs=["close"], window_bars=168,
        causal_safe=True, asset_scope="single",
    ),
    "donchian_pos_20h": FeatureDef(
        name="donchian_pos_20h", description="Position dans canal Donchian 20h [-1,1]",
        family="trend", inputs=["close", "high", "low"], window_bars=20,
        causal_safe=True, asset_scope="single",
    ),
    "rsi_14h": FeatureDef(
        name="rsi_14h", description="RSI 14h (feature secondaire uniquement)",
        family="trend", inputs=["close"], window_bars=14,
        causal_safe=True, asset_scope="single",
    ),

    # ─── Funding ──────────────────────────────────────────────────────────────
    "funding_zscore": FeatureDef(
        name="funding_zscore", description="Z-score rolling funding rate",
        family="derivatives", inputs=["funding_rate"], window_bars=90,
        causal_safe=True, asset_scope="single",
    ),
    "funding_cum_24h": FeatureDef(
        name="funding_cum_24h", description="Funding cumulé 24h",
        family="derivatives", inputs=["funding_rate"], window_bars=3,
        causal_safe=True, asset_scope="single",
    ),
    "funding_ann": FeatureDef(
        name="funding_ann", description="Funding annualisé",
        family="derivatives", inputs=["funding_rate"], window_bars=1,
        causal_safe=True, asset_scope="single",
    ),

    # ─── Open Interest ────────────────────────────────────────────────────────
    "oi_zscore_168h": FeatureDef(
        name="oi_zscore_168h", description="Z-score OI rolling 168h",
        family="derivatives", inputs=["oi_sum"], window_bars=168,
        causal_safe=True, asset_scope="single",
    ),
    "price_oi_div_24h": FeatureDef(
        name="price_oi_div_24h", description="Corrélation rolling prix/OI 24h",
        family="derivatives", inputs=["close", "oi_sum"], window_bars=24,
        causal_safe=True, asset_scope="single",
    ),

    # ─── Cross-sectional ──────────────────────────────────────────────────────
    "rank_mom_24h": FeatureDef(
        name="rank_mom_24h", description="Rang cross-sectionnel momentum 24h",
        family="cross_section", inputs=["close"], window_bars=24,
        causal_safe=True, asset_scope="cross_section",
        requires_cross_section=True,
    ),
    "composite_rank": FeatureDef(
        name="composite_rank", description="Score composite de relative strength",
        family="cross_section", inputs=["close"], window_bars=168,
        causal_safe=True, asset_scope="cross_section",
        requires_cross_section=True,
    ),
}


def get_feature_families() -> Dict[str, List[str]]:
    """Retourne les features regroupées par famille."""
    families: Dict[str, List[str]] = {}
    for name, feat in FEATURE_REGISTRY.items():
        families.setdefault(feat.family, []).append(name)
    return families


def get_features_by_scope(scope: str) -> List[str]:
    return [name for name, f in FEATURE_REGISTRY.items() if f.asset_scope == scope]


def get_causal_features() -> List[str]:
    return [name for name, f in FEATURE_REGISTRY.items() if f.causal_safe]
