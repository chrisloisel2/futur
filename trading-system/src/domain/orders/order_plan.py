from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from domain.orders.order_types import OrderSide, OrderType, TimeInForce


@dataclass
class ExecutionDirective:
    mode: str = "TAKER"
    max_slippage_bps: float = 10.0
    max_latency_ms: int = 500
    reduce_only: bool = False
    post_only: bool = False
    price_band_bps: float = 10.0
    split_count: int = 1
    cancel_repost_seconds: int = 3
    client_order_id_prefix: str = "exec"


@dataclass
class OrderIntent:
    symbol: str
    order_type: OrderType
    side: OrderSide
    qty: float
    price: Optional[float] = None
    reduce_only: bool = False
    time_in_force: TimeInForce = TimeInForce.IOC
    book: str = ""
    risk_tags: List[str] = field(default_factory=list)
    directive: ExecutionDirective = field(default_factory=ExecutionDirective)


@dataclass
class StopIntent:
    symbol: str
    side: OrderSide
    stop_type: str
    stop_price: float
    reduce_only: bool = True
    book: str = ""
    risk_tags: List[str] = field(default_factory=list)


@dataclass
class TimeStopIntent:
    symbol: str
    close_after_seconds: int
    reduce_only: bool = True
    book: str = ""
    risk_tags: List[str] = field(default_factory=list)


@dataclass
class OrdersPlan:
    event_time: object
    run_id: str
    orders: List[OrderIntent]
    stops: List[StopIntent]
    time_stops: List[TimeStopIntent]
    risk_state_ref: str
    caps_applied: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
