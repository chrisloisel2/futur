"""
src/institutional/portfolio/funding.py
─────────────────────────────────────────────────────────────────────────────
Accrual de funding par jambe (Phase 37).

Convention (perp Binance) :
    funding_rate > 0 → les LONGS paient les SHORTS
    funding_rate < 0 → les SHORTS paient les LONGS

Donc une jambe SHORT (CARRY_SHORT_PERP / SHORT_HEDGE) :
    funding_pnl = + short_notional × funding_rate    (reçoit si >0, paie si <0)
Une jambe spot (LONG_SPOT / CARRY_LONG_SPOT) : pas de funding.

Le funding_pnl est TOUJOURS séparé du price_pnl.
"""
from __future__ import annotations

from src.institutional.portfolio.position import PositionLeg, LEG_FUNDING_SIGN

FUNDING_HOURS = (0, 8, 16)  # cadence funding Binance


def is_funding_hour(timestamp) -> bool:
    return timestamp.hour in FUNDING_HOURS


def accrue_funding_leg(leg: PositionLeg, funding_rate: float, mark_price: float) -> float:
    """
    Crédite/débite le funding d'une jambe pour une période de funding (8h).
    Retourne le funding_pnl de la période (et l'accumule sur la jambe).
    """
    sign = LEG_FUNDING_SIGN.get(leg.leg_type, 0.0)
    if sign == 0.0:
        return 0.0
    notional = leg.qty * (mark_price or leg.entry_price)
    pnl = sign * notional * funding_rate
    leg.funding_pnl_cum += pnl
    return pnl
