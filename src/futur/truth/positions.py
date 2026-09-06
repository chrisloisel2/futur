"""src/futur/truth/positions.py -- spot and perpetual position state.

Spot and perp are tracked completely separately, even for the same
underlying symbol (ProductSpec.key already encodes SPOT vs LINEAR_PERP) --
they have different accounting rules (spot moves cash directly on trade;
perp holds margin and marks-to-market) and mixing them into one structure
would make it easy to leak one model's assumptions into the other.

All quantities/prices are Decimal.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.futur.truth.events import ProductSpec
from src.futur.truth.numeric import to_decimal


@dataclass
class SpotPosition:
    instrument: ProductSpec
    quantity: Decimal = Decimal(0)
    last_price: Decimal = Decimal(0)   # last fill price -- exposure fallback when unmarked (margin.py)

    def __post_init__(self) -> None:
        self.quantity = to_decimal(self.quantity)
        self.last_price = to_decimal(self.last_price)

    def market_value(self, mark_price: Decimal) -> Decimal:
        return self.quantity * to_decimal(mark_price)


@dataclass
class PerpPosition:
    instrument: ProductSpec
    quantity: Decimal = Decimal(0)          # signed: + long, - short
    avg_entry_price: Decimal = Decimal(0)   # undefined (0) when quantity == 0

    def __post_init__(self) -> None:
        self.quantity = to_decimal(self.quantity)
        self.avg_entry_price = to_decimal(self.avg_entry_price)

    @property
    def side(self) -> str:
        if self.quantity > 0:
            return "LONG"
        if self.quantity < 0:
            return "SHORT"
        return "FLAT"

    def notional(self, mark_price: Decimal) -> Decimal:
        return abs(self.quantity) * to_decimal(mark_price)

    def unrealized_pnl(self, mark_price: Decimal) -> Decimal:
        """Always priced off the mark, never the last trade price -- a
        position's PnL must not jump just because someone else traded at a
        stale or off-market price elsewhere."""
        return (to_decimal(mark_price) - self.avg_entry_price) * self.quantity
