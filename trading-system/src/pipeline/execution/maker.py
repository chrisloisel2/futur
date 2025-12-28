from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

import pandas as pd

from domain.orders.order_plan import ExecutionDirective


@dataclass
class QuoteResult:
    bid_price: float
    ask_price: float
    bid_size: float
    ask_size: float


class MakerExecutor:
    def __init__(self, config: Dict):
        self.config = config

    def apply_quotes(self, symbol: str, target_delta: float, microstructure: pd.Series, directive: ExecutionDirective) -> QuoteResult:
        spread_bps = float(microstructure.get("x_fast_spread_bps", 5.0) or 5.0)
        base_dist = self.config.get("base_quote_distance_bps", 1.5)
        distance = min(self.config.get("max_quote_distance_bps", 12.0), base_dist + spread_bps / 2)
        mid = float(microstructure.get("mid_price", microstructure.get("price", 0)) or 0)
        bid = mid * (1 - distance / 10_000)
        ask = mid * (1 + distance / 10_000)
        size = min(abs(target_delta), self.config.get("max_size_usd", 5_000))
        return QuoteResult(bid_price=bid, ask_price=ask, bid_size=size if target_delta > 0 else 0, ask_size=size if target_delta < 0 else 0)
