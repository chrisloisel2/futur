"""
research/edge_factory/multileg_engine/multileg_order.py — MultiLegOrder (interface 4/5).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .instrument import Instrument

LONG = "long"
SHORT = "short"


@dataclass(frozen=True)
class Leg:
    instrument: Instrument
    side: str          # long | short
    size: float         # unités de l'instrument (positif)

    def __post_init__(self) -> None:
        if self.side not in (LONG, SHORT):
            raise ValueError(f"side inconnu: {self.side}")
        if self.size <= 0:
            raise ValueError("size doit être positif ; le signe vient de side")

    @property
    def signed_size(self) -> float:
        return self.size if self.side == LONG else -self.size


@dataclass(frozen=True)
class MultiLegOrder:
    legs: List[Leg]
    delta_target: float = 0.0   # 0 pour les trois moteurs actuels (market-neutral)
