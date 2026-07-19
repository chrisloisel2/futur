"""
src/institutional/risk/intra_position_governor.py
─────────────────────────────────────────────────────────────────────────────
Intra-Position Drawdown Governor (Phase 44) — logique SURVIE.

Réduit/ferme les longs DÉJÀ ouverts quand le drawdown glissant dépasse les
seuils. ≠ ratchet multi-année : DD local (par position depuis son pic) +
DD portefeuille glissant.

Version SIMPLE close-only (recommandée tant que le ledger ne gère pas les
sorties partielles proprement) : pas de REDUCE_HALF.
"""
from __future__ import annotations

from dataclasses import dataclass

POSITION_RISK_ACTIONS = (
    "HOLD", "CLOSE_POSITION", "CLOSE_ALL_DIRECTIONAL_LONGS", "KILL",
)


@dataclass
class IntraGovernorConfig:
    # 1.0% provoque un whipsaw (bruit horaire crypto) → 2.0% (mesuré : 1% détruisait l'alpha)
    position_dd_close: float = 0.020      # DD position / equity ≥ 2.0% → close
    portfolio_dd_close_all: float = 0.025  # DD portefeuille ≥ 2.5% → close all longs
    portfolio_dd_kill: float = 0.030       # DD portefeuille ≥ 3.0% → kill


@dataclass
class PositionRiskDecision:
    position_id: str
    asset: str
    action: str
    position_dd_on_equity: float
    portfolio_dd: float
    reason: str


def decide_position_risk(
    position_type: str,
    position_dd_on_equity: float,    # ≥ 0 : ampleur du DD de la position / equity
    portfolio_dd: float,             # ≤ 0 : drawdown glissant du portefeuille
    cfg: IntraGovernorConfig = IntraGovernorConfig(),
) -> str:
    """Action close-only (HOLD/CLOSE_POSITION/CLOSE_ALL_DIRECTIONAL_LONGS/KILL)."""
    pdd = abs(min(0.0, portfolio_dd))
    if pdd >= cfg.portfolio_dd_kill:
        return "KILL"
    if pdd >= cfg.portfolio_dd_close_all:
        return "CLOSE_ALL_DIRECTIONAL_LONGS"
    if position_type == "DIRECTIONAL_LONG" and position_dd_on_equity >= cfg.position_dd_close:
        return "CLOSE_POSITION"
    return "HOLD"
