from __future__ import annotations

import random
import uuid
from typing import Optional

import numpy as np
import pandas as pd
from pydantic import BaseModel

from common.logging.setup import get_logger

logger = get_logger(__name__)


class ExecutionSimConfig(BaseModel):
    mode: str = "taker"
    fill_probability: float = 0.95
    latency_ms: int = 50
    cancel_probability: float = 0.02
    partial_fill_probability: float = 0.1
    slippage_bps: float = 1.0


class ExecutionSimulator:
    def __init__(self, config: ExecutionSimConfig, rng: Optional[random.Random] = None):
        self.config = config
        self.rng = rng or random.Random()

    def simulate_orders(self, orders: pd.DataFrame) -> pd.DataFrame:
        fills = []
        for _, order in orders.iterrows():
            if self.rng.random() > self.config.fill_probability:
                continue
            partial = self.rng.random() < self.config.partial_fill_probability
            qty = float(order.get("qty", 0))
            fill_qty = qty * (0.5 if partial else 1.0)
            sign = 1 if str(order.get("side", "buy")).lower().startswith("b") else -1
            base_px = float(order.get("entry_px", order.get("price", 0)))
            slip = base_px * (self.config.slippage_bps / 10_000) * sign
            fill_px = base_px + slip
            fill = {
                "fill_id": str(uuid.uuid4()),
                "order_id": str(order.get("order_id", uuid.uuid4())),
                "event_time": pd.to_datetime(order.get("event_time")),
                "symbol": order.get("symbol", ""),
                "side": order.get("side", "buy"),
                "qty": fill_qty,
                "px": fill_px,
                "fee": abs(fill_qty * fill_px) * 0.0,
                "liquidity": self.config.mode,
                "latency_ms": int(self.config.latency_ms + self.rng.random() * 5),
                "partial": partial,
            }
            if "exit_px" in order:
                fill["exit_px"] = float(order.get("exit_px"))
            fills.append(fill)
        df = pd.DataFrame(fills)
        logger.info({"msg": "simulated orders", "fills": len(df)})
        return df
