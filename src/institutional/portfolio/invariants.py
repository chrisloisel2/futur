"""
src/institutional/portfolio/invariants.py
─────────────────────────────────────────────────────────────────────────────
Invariants de sécurité multi-jambes (Phase 37). Si un short nu apparaît,
le backtest DOIT crasher (InvariantViolation).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from src.institutional.portfolio.position import PortfolioPosition

NAKED_SHORT_ALLOWED = False


@dataclass
class InvariantLimits:
    carry_delta_tolerance: float = 0.10     # |net delta| / gross ≤ 10%
    max_hedge_cap: float = 0.30             # hedge ≤ 30% capital
    max_gross_exposure: float = 1.00
    max_net_long_exposure: float = 0.75


class InvariantViolation(Exception):
    pass


def check_position_invariants(pos: PortfolioPosition, limits: InvariantLimits) -> None:
    leg_types = [l.leg_type for l in pos.legs]

    # 2/4 : tout short doit être lié
    for l in pos.legs:
        if l.leg_type == "SHORT_HEDGE" and not pos.linked_position_id:
            raise InvariantViolation(f"SHORT_HEDGE sans linked_position_id (pos {pos.position_id})")
        if l.leg_type == "CARRY_SHORT_PERP" and "CARRY_LONG_SPOT" not in leg_types:
            raise InvariantViolation(f"CARRY_SHORT_PERP sans CARRY_LONG_SPOT (pos {pos.position_id})")

    # 1 : pas de short nu (un short isolé sans long correspondant)
    if not NAKED_SHORT_ALLOWED:
        has_short = any(l.delta_sign() < 0 for l in pos.legs)
        has_long = any(l.delta_sign() > 0 for l in pos.legs)
        if has_short and not has_long and not pos.linked_position_id:
            raise InvariantViolation(f"SHORT NU détecté (pos {pos.position_id})")

    # 5 : carry delta-neutral dans la tolérance
    if pos.position_type == "DELTA_NEUTRAL_CARRY":
        gross = pos.gross_notional()
        if gross > 0:
            rel = abs(pos.net_delta_notional()) / gross
            if rel > limits.carry_delta_tolerance:
                raise InvariantViolation(
                    f"Carry delta {rel:.1%} > tolérance {limits.carry_delta_tolerance:.0%} (pos {pos.position_id})")


def check_portfolio_invariants(
    positions: List[PortfolioPosition],
    equity: float,
    limits: InvariantLimits,
) -> Dict[str, float]:
    """Vérifie les invariants globaux + retourne les expositions."""
    for p in positions:
        if p.is_open:
            check_position_invariants(p, limits)

    open_legs = [l for p in positions if p.is_open for l in p.legs if l.is_open]
    gross = sum(l.qty * (l.mark_price or l.entry_price) for l in open_legs)
    net = sum(l.signed_notional() for l in open_legs)
    long_exp = sum(l.qty * (l.mark_price or l.entry_price) for l in open_legs if l.delta_sign() > 0)
    hedge_exp = sum(l.qty * (l.mark_price or l.entry_price)
                    for l in open_legs if l.leg_type == "SHORT_HEDGE")
    carry_exp = sum(l.qty * (l.mark_price or l.entry_price)
                    for l in open_legs if l.leg_type in ("CARRY_LONG_SPOT", "CARRY_SHORT_PERP"))

    eq = max(equity, 1e-9)
    # 3 : hedge borné par cap (tolérance pour le drift mark-to-market entre
    # la décision du governor et la vérification d'invariant)
    if hedge_exp / eq > limits.max_hedge_cap * 1.05:
        raise InvariantViolation(f"hedge_exposure {hedge_exp/eq:.1%} > cap {limits.max_hedge_cap:.0%}")

    return {
        "gross_exposure": gross / eq, "net_exposure": net / eq,
        "net_long_exposure": long_exp / eq, "short_hedge_exposure": hedge_exp / eq,
        "carry_exposure": carry_exp / eq,
    }
