"""
research/edge_factory/multileg_engine/pit_universe.py — PointInTimeUniverse (interface 2/5).
"""
from __future__ import annotations

from datetime import date
from typing import Callable, List

from .instrument import Instrument

MembershipFn = Callable[[date], List[Instrument]]


class PointInTimeUniverse:
    """Membership à une date donnée, sans lookahead. Ne jamais appliquer
    l'univers d'aujourd'hui au passé — c'est exactement le biais qui a
    invalidé CTREND v0 (commit 859ebad)."""

    def __init__(self, membership_fn: MembershipFn):
        self._membership_fn = membership_fn

    def as_of(self, as_of_date: date) -> List[Instrument]:
        return self._membership_fn(as_of_date)

    @classmethod
    def static(cls, instruments: List[Instrument]) -> "PointInTimeUniverse":
        """Cas dégénéré : liste fixe, ignore la date. Utilisé par
        funding_relative_value_cross_venue_v1 et calendar_basis_v1 aujourd'hui
        (4 et 2 actifs respectivement, pas de ranking). Seul
        cross_sectional_momentum_v1 a besoin d'un membership variable dans le
        temps — réutiliser build_membership() de scripts/backtest_ctrend_v1.py
        pour ce cas-là plutôt que de le réimplémenter ici."""
        fixed = list(instruments)
        return cls(lambda _as_of: fixed)
