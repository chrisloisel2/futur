from __future__ import annotations

from typing import Optional

import pandas as pd
from pydantic import BaseModel

from domain.events.raw import BaseEvent


class TradeEvent(BaseEvent):
    trade_id: str
    price: float
    qty: float
    side: str
    is_maker: Optional[bool] = None
    trade_flags: int = 0


class BookSnapshotEvent(BaseEvent):
    update_id: int
    depth: int
    bid_px: list[float]
    bid_sz: list[float]
    ask_px: list[float]
    ask_sz: list[float]
    mid_price: float
    spread: float
    checksum: Optional[str] = None
    book_flags: int = 0
    is_snapshot: bool = True


class BookUpdateEvent(BaseEvent):
    update_id: int
    depth: int
    bid_px: list[float]
    bid_sz: list[float]
    ask_px: list[float]
    ask_sz: list[float]
    mid_price: float
    spread: float
    checksum: Optional[str] = None
    book_flags: int = 0
    is_snapshot: bool = False


class OHLCVEvent(BaseEvent):
    bar_start: pd.Timestamp
    bar_end: pd.Timestamp
    bar_size_s: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    trades_count: Optional[int] = None
