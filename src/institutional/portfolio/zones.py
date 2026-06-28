"""
src/institutional/portfolio/zones.py
─────────────────────────────────────────────────────────────────────────────
Zones de décision A/B/C — remplace la décision binaire trade / no-trade.

    p ≥ τ_A        → A_TRADE   (trade réel / paper réel)
    τ_B ≤ p < τ_A  → B_SHADOW  (shadow trade OBLIGATOIRE — on apprend dessus)
    p < τ_B        → C_REJECT  (rejet pur)

Pourquoi : on ne peut pas apprendre uniquement sur les trades pris. Il faut
mesurer E[return | p proche du seuil] pour savoir si le modèle est intelligent
ou simplement paralysé. Les seuils sont par asset (BTC plus strict que ETH).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from src.institutional.contracts import ReasonCode


@dataclass(frozen=True)
class ZoneThresholds:
    """Seuils A/B pour un asset donné."""
    tau_a: float
    tau_b: float

    def __post_init__(self) -> None:
        if not (0.0 <= self.tau_b <= self.tau_a <= 1.0):
            raise ValueError(
                f"Seuils invalides : 0 <= tau_b({self.tau_b}) <= tau_a({self.tau_a}) <= 1 requis"
            )


# Seuils par défaut (cf. brief : BTC plus sélectif que ETH).
DEFAULT_THRESHOLDS: Dict[str, ZoneThresholds] = {
    "BTCUSDT": ZoneThresholds(tau_a=0.63, tau_b=0.52),
    "ETHUSDT": ZoneThresholds(tau_a=0.58, tau_b=0.50),
    "SOLUSDT": ZoneThresholds(tau_a=0.60, tau_b=0.50),
    "BNBUSDT": ZoneThresholds(tau_a=0.60, tau_b=0.50),
}

# Fallback pour tout asset non listé.
DEFAULT_FALLBACK = ZoneThresholds(tau_a=0.60, tau_b=0.50)


def get_thresholds(
    asset: str,
    table: Optional[Dict[str, ZoneThresholds]] = None,
) -> ZoneThresholds:
    """Retourne les seuils A/B pour un asset (fallback si inconnu)."""
    table = table or DEFAULT_THRESHOLDS
    return table.get(asset, DEFAULT_FALLBACK)


def classify_zone(
    p: float,
    tau_a: float,
    tau_b: float,
) -> Tuple[str, ReasonCode]:
    """
    Classe une probabilité dans une zone de décision.

    Retourne (decision_zone, reason_code).
    """
    if p >= tau_a:
        return "A_TRADE", ReasonCode.ACCEPT_TRADE
    if p >= tau_b:
        return "B_SHADOW", ReasonCode.ACCEPT_SHADOW
    return "C_REJECT", ReasonCode.REJECT_LOW_PROBA


def classify_for_asset(
    p: float,
    asset: str,
    table: Optional[Dict[str, ZoneThresholds]] = None,
) -> Tuple[str, ReasonCode, ZoneThresholds]:
    """Classe une proba en allant chercher les seuils de l'asset."""
    thr = get_thresholds(asset, table)
    zone, reason = classify_zone(p, thr.tau_a, thr.tau_b)
    return zone, reason, thr
