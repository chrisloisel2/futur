"""
src/institutional/live_alpha_lab/portfolio_config.py
─────────────────────────────────────────────────────────────────────────────
Trois portefeuilles shadow simultanés, figés (aucun tuning en fonction des
résultats live -- instruction utilisateur explicite, item 2/9 de la mission).

Capital de référence identique aux 3 : 200 000 EUR (mission item 12), pour
rester comparables entre eux.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

CAPITAL_EUR = 200_000.0

# risk_bucket connus au 2026-08-31 (voir configs/live_alpha_registry.yaml)
ALL_RISK_BUCKETS = [
    "LIQUIDATION_FAMILY", "POSITIONING_WALLET_FAMILY", "RELATIVE_VALUE_FAMILY",
    "VOLATILITY_FAMILY", "CROSS_SECTIONAL_FAMILY", "MICROSTRUCTURE_FAMILY",
]


@dataclass(frozen=True)
class PortfolioConfig:
    name: str
    capital_eur: float = CAPITAL_EUR
    family_budget_fraction: Dict[str, float] = field(default_factory=dict)
    default_family_budget_fraction: float = 0.0   # familles non listées explicitement
    max_dominant_per_correlation_family: Optional[int] = None  # P2 : 1 alpha max par cluster
    per_alpha_budget_fraction: Optional[float] = None            # P3 : petit budget fixe/alpha
    max_gross_exposure_fraction: float = 1.0
    max_net_exposure_fraction: float = 1.0
    max_per_asset_fraction: float = 0.15
    apply_vol_overlay: bool = False   # True seulement pour P1_VOL_OVERLAY (item 3)


# ── P1_EQUAL_RISK : budget fixe par famille (risk_bucket), poids égal ──────
P1_EQUAL_RISK = PortfolioConfig(
    name="P1_EQUAL_RISK",
    family_budget_fraction={b: 1.0 / len(ALL_RISK_BUCKETS) for b in ALL_RISK_BUCKETS},
    max_gross_exposure_fraction=1.0,
    max_net_exposure_fraction=1.0,
    max_per_asset_fraction=0.15,
)

# ── P1_CONTROL / P1_VOL_OVERLAY : même portefeuille que P1_EQUAL_RISK,
# seule différence = l'overlay VOL_FORECAST_LAYER sur le sizing (item 3) ──
P1_CONTROL = PortfolioConfig(
    name="P1_CONTROL",
    family_budget_fraction={b: 1.0 / len(ALL_RISK_BUCKETS) for b in ALL_RISK_BUCKETS},
    max_gross_exposure_fraction=1.0, max_net_exposure_fraction=1.0,
    max_per_asset_fraction=0.15, apply_vol_overlay=False,
)
P1_VOL_OVERLAY = PortfolioConfig(
    name="P1_VOL_OVERLAY",
    family_budget_fraction={b: 1.0 / len(ALL_RISK_BUCKETS) for b in ALL_RISK_BUCKETS},
    max_gross_exposure_fraction=1.0, max_net_exposure_fraction=1.0,
    max_per_asset_fraction=0.15, apply_vol_overlay=True,
)

# ── P2_DIVERSIFIED : au plus un moteur dominant par cluster économique
# (correlation_family) -- appliqué au moment de l'agrégation, pas ici ──────
P2_DIVERSIFIED = PortfolioConfig(
    name="P2_DIVERSIFIED",
    family_budget_fraction={b: 1.0 / len(ALL_RISK_BUCKETS) for b in ALL_RISK_BUCKETS},
    max_dominant_per_correlation_family=1,
    max_gross_exposure_fraction=1.0, max_net_exposure_fraction=1.0,
    max_per_asset_fraction=0.15,
)

# ── P3_ALL_CANDIDATES : tous les candidats, petits budgets fixes/alpha ────
P3_ALL_CANDIDATES = PortfolioConfig(
    name="P3_ALL_CANDIDATES",
    per_alpha_budget_fraction=0.05,   # 5% du capital par alpha, quel que soit le risk_bucket
    max_gross_exposure_fraction=1.5,  # plus permissif : c'est le portefeuille "tout tester"
    max_net_exposure_fraction=1.5,
    max_per_asset_fraction=0.10,
)

ALL_PORTFOLIOS = {
    "P1_EQUAL_RISK": P1_EQUAL_RISK,
    "P1_CONTROL": P1_CONTROL,
    "P1_VOL_OVERLAY": P1_VOL_OVERLAY,
    "P2_DIVERSIFIED": P2_DIVERSIFIED,
    "P3_ALL_CANDIDATES": P3_ALL_CANDIDATES,
}
