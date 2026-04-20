from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class OrderEvent:
    event_time: object
    event_type: str
    symbol: str
    client_order_id: str
    exchange_order_id: str | None
    details: dict


@dataclass
class OrderEvents:
    event_time: object
    events: List[OrderEvent]
