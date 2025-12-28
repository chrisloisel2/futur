from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from domain.orders.order_plan import ExecutionDirective, OrderIntent, TimeInForce, OrderType
from domain.orders.order_types import OrderSide


class TakerExecutor:
    def __init__(self, config: Dict):
        self.config = config

    def split_order(self, intent: OrderIntent) -> List[OrderIntent]:
        splits = self.config.get("split_count", intent.directive.split_count)
        splits = max(1, splits)
        qty = intent.qty / splits
        out: List[OrderIntent] = []
        for i in range(splits):
            out.append(
                OrderIntent(
                    symbol=intent.symbol,
                    order_type=OrderType.LIMIT_TO_MARKET,
                    side=intent.side,
                    qty=qty,
                    price=intent.price,
                    reduce_only=intent.reduce_only,
                    time_in_force=TimeInForce.IOC,
                    book=intent.book,
                    risk_tags=intent.risk_tags,
                    directive=intent.directive,
                )
            )
        return out

    def execute(self, intent: OrderIntent, ref_price: float) -> List[dict]:
        intents = self.split_order(intent)
        events = []
        for child in intents:
            exec_price = ref_price * (1 + self.config.get("limit_to_market_offset_bps", 2.0) / 10_000) if child.side == OrderSide.BUY else ref_price * (1 - self.config.get("limit_to_market_offset_bps", 2.0) / 10_000)
            events.append({"symbol": child.symbol, "side": child.side.value if hasattr(child.side, 'value') else str(child.side), "qty": child.qty, "price": exec_price})
        return events
