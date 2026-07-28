"""src/futur/truth/orders.py -- order/fill domain and state-transition rules.

Order state machine (terminal states: FILLED, CANCELLED, REJECTED -- no
event can follow any of them):

    CREATED -> SUBMITTED -> ACKNOWLEDGED -> PARTIALLY_FILLED -> FILLED
                         \\-> REJECTED     \\-> FILLED           ^
                                            \\-> CANCELLED   ----'
                                                              (more fills)
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.futur.truth.events import Instrument

_EPS = 1e-9


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    CREATED = "CREATED"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


TERMINAL_STATUSES = frozenset({OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED})

# Explicit transition table -- anything not listed here is invalid.
_VALID_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.CREATED: frozenset({OrderStatus.SUBMITTED}),
    OrderStatus.SUBMITTED: frozenset({OrderStatus.ACKNOWLEDGED, OrderStatus.REJECTED}),
    OrderStatus.ACKNOWLEDGED: frozenset({
        OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCELLED,
    }),
    OrderStatus.PARTIALLY_FILLED: frozenset({
        OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCELLED,
    }),
    OrderStatus.FILLED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
}


class InvalidOrderTransition(Exception):
    pass


def validate_transition(current: OrderStatus, target: OrderStatus) -> None:
    if target not in _VALID_TRANSITIONS[current]:
        raise InvalidOrderTransition(f"{current.value} -> {target.value} is not a valid transition")


@dataclass
class Order:
    order_id: str
    client_order_id: str
    instrument: Instrument
    side: OrderSide
    order_type: OrderType
    quantity: float
    limit_price: float | None = None
    status: OrderStatus = OrderStatus.CREATED
    filled_quantity: float = 0.0

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError(f"order quantity must be > 0, got {self.quantity!r}")
        if self.order_type == OrderType.LIMIT and self.limit_price is None:
            raise ValueError("LIMIT order requires limit_price")

    @property
    def remaining_quantity(self) -> float:
        return self.quantity - self.filled_quantity

    def transition_to(self, target: OrderStatus) -> None:
        validate_transition(self.status, target)
        self.status = target

    def apply_fill(self, fill_quantity: float) -> None:
        """Mutates filled_quantity and status together -- the only path by
        which filled_quantity changes, so "filled_quantity <= quantity" and
        "no fill after a terminal status" are enforced at the single point
        that could violate them, not just checked after the fact."""
        if self.status in TERMINAL_STATUSES:
            raise InvalidOrderTransition(
                f"cannot fill an order in terminal status {self.status.value}")
        if self.status not in (OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED):
            raise InvalidOrderTransition(
                f"cannot fill an order in status {self.status.value} "
                f"(must be ACKNOWLEDGED or PARTIALLY_FILLED)")
        if fill_quantity <= 0:
            raise ValueError(f"fill_quantity must be > 0, got {fill_quantity!r}")
        new_filled = self.filled_quantity + fill_quantity
        if new_filled > self.quantity + _EPS:
            raise ValueError(
                f"fill would over-fill order {self.order_id}: "
                f"{new_filled} > {self.quantity}")
        self.filled_quantity = min(new_filled, self.quantity)
        self.status = (OrderStatus.FILLED if self.remaining_quantity <= _EPS
                       else OrderStatus.PARTIALLY_FILLED)


@dataclass(frozen=True)
class Fill:
    fill_id: str
    order_id: str
    instrument: Instrument
    price: float
    quantity: float
    side: OrderSide
    fee: float
    fee_ccy: str
    liquidity: str | None = None
    venue: str = ""
    external_id: str | None = None

    def __post_init__(self) -> None:
        if self.price <= 0:
            raise ValueError(f"fill price must be > 0, got {self.price!r}")
        if self.quantity <= 0:
            raise ValueError(f"fill quantity must be > 0, got {self.quantity!r}")
        if self.fee < 0:
            raise ValueError(f"fill fee must be >= 0, got {self.fee!r}")
