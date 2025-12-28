from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class Fill:
    symbol: str
    side: str
    qty: float
    price: float
    fee_usd: float
    exchange: str
    order_id: str
    client_order_id: str
    event_time: object
    liquidity: str


@dataclass
class ExecutionCostsSnapshot:
    by_order: List[dict] = field(default_factory=list)
    aggregate: dict | None = None


@dataclass
class ExecutedFills:
    event_time: object
    fills: List[Fill]
    aggregate_costs: ExecutionCostsSnapshot
