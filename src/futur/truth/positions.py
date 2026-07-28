"""src/futur/truth/positions.py -- spot and perpetual position state.

Spot and perp are tracked completely separately, even for the same
underlying symbol (Instrument.key already encodes SPOT vs PERPETUAL) --
they have different accounting rules (spot moves cash directly on trade;
perp holds margin and marks-to-market) and mixing them into one structure
would make it easy to leak one model's assumptions into the other.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.futur.truth.events import Instrument


@dataclass
class SpotPosition:
    instrument: Instrument
    quantity: float = 0.0
    last_price: float = 0.0    # last fill price -- exposure fallback when unmarked (margin.py)

    def market_value(self, mark_price: float) -> float:
        return self.quantity * mark_price


@dataclass
class PerpPosition:
    instrument: Instrument
    quantity: float = 0.0          # signed: + long, - short
    avg_entry_price: float = 0.0   # undefined (0.0) when quantity == 0

    @property
    def side(self) -> str:
        if self.quantity > 0:
            return "LONG"
        if self.quantity < 0:
            return "SHORT"
        return "FLAT"

    def notional(self, mark_price: float) -> float:
        return abs(self.quantity) * mark_price

    def unrealized_pnl(self, mark_price: float) -> float:
        """Always priced off the mark, never the last trade price -- a
        position's PnL must not jump just because someone else traded at a
        stale or off-market price elsewhere."""
        return (mark_price - self.avg_entry_price) * self.quantity
