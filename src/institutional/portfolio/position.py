"""
src/institutional/portfolio/position.py
─────────────────────────────────────────────────────────────────────────────
Positions MULTI-JAMBES (Phase 37) — comptabilité séparée.

Carry et hedge ne sont pas des trades simples : ce sont des positions à
plusieurs jambes (legs) avec PnL décomposé. Un short n'existe JAMAIS seul :
  - SHORT_HEDGE → linked_position_id obligatoire
  - CARRY_SHORT_PERP → jambe CARRY_LONG_SPOT du même asset obligatoire
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

try:
    from typing import Literal
except ImportError:  # pragma: no cover
    Literal = None  # type: ignore

LEG_TYPES = ("LONG_SPOT", "SHORT_HEDGE", "CARRY_LONG_SPOT", "CARRY_SHORT_PERP")
POSITION_TYPES = ("DIRECTIONAL_LONG", "DELTA_NEUTRAL_CARRY", "PORTFOLIO_HEDGE")

# signe de delta (exposition prix) par jambe
LEG_DELTA_SIGN = {
    "LONG_SPOT": +1.0,
    "CARRY_LONG_SPOT": +1.0,
    "SHORT_HEDGE": -1.0,
    "CARRY_SHORT_PERP": -1.0,
}
# signe de funding reçu par la jambe (short perp reçoit si funding>0)
LEG_FUNDING_SIGN = {
    "CARRY_SHORT_PERP": +1.0,   # short perp reçoit funding positif
    "SHORT_HEDGE": +1.0,        # hedge = short perp → reçoit funding positif
    "LONG_SPOT": 0.0,           # spot ne paie/reçoit pas de funding
    "CARRY_LONG_SPOT": 0.0,
}


@dataclass
class PositionLeg:
    leg_id: str
    position_id: str
    asset: str
    leg_type: str                  # LEG_TYPES

    entry_time: str
    entry_price: float
    qty: float                     # toujours > 0 ; le sens vient de leg_type
    notional: float

    fees_entry: float = 0.0
    slippage_entry: float = 0.0

    # accruals
    funding_pnl_cum: float = 0.0
    fees_exit: float = 0.0
    slippage_exit: float = 0.0

    is_open: bool = True
    exit_time: Optional[str] = None
    exit_price: Optional[float] = None
    mark_price: float = 0.0

    def __post_init__(self):
        if self.leg_type not in LEG_TYPES:
            raise ValueError(f"leg_type invalide: {self.leg_type}")
        if self.qty < 0:
            raise ValueError("qty doit être ≥ 0 (le sens vient de leg_type)")

    def delta_sign(self) -> float:
        return LEG_DELTA_SIGN[self.leg_type]

    def signed_notional(self) -> float:
        """Notional signé par le delta (pour net exposure)."""
        px = self.mark_price or self.entry_price
        return self.delta_sign() * self.qty * px

    def price_pnl(self) -> float:
        """PnL prix (réalisé si fermé, sinon mark-to-market)."""
        px = self.exit_price if (not self.is_open and self.exit_price) else self.mark_price
        if not px:
            return 0.0
        return self.delta_sign() * self.qty * (px - self.entry_price)

    def total_costs(self) -> float:
        return self.fees_entry + self.slippage_entry + self.fees_exit + self.slippage_exit

    def net_pnl(self) -> float:
        return self.price_pnl() + self.funding_pnl_cum - self.total_costs()


@dataclass
class PortfolioPosition:
    position_id: str
    position_type: str             # POSITION_TYPES
    engine_id: str
    asset: str
    opened_at: str
    legs: List[PositionLeg] = field(default_factory=list)

    linked_position_id: Optional[str] = None
    hedge_reason: Optional[str] = None
    funding_regime_at_entry: Optional[str] = None
    is_open: bool = True

    def __post_init__(self):
        if self.position_type not in POSITION_TYPES:
            raise ValueError(f"position_type invalide: {self.position_type}")

    def net_delta_notional(self) -> float:
        return sum(l.signed_notional() for l in self.legs)

    def gross_notional(self) -> float:
        return sum(l.qty * (l.mark_price or l.entry_price) for l in self.legs)

    def price_pnl(self) -> float:
        return sum(l.price_pnl() for l in self.legs)

    def funding_pnl(self) -> float:
        return sum(l.funding_pnl_cum for l in self.legs)

    def costs(self) -> float:
        return sum(l.total_costs() for l in self.legs)

    def net_pnl(self) -> float:
        return sum(l.net_pnl() for l in self.legs)
