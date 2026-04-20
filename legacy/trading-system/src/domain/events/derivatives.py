from __future__ import annotations

import pandas as pd
from pydantic import BaseModel

from domain.events.raw import BaseEvent


class FundingEvent(BaseEvent):
    funding_rate: float
    funding_time: pd.Timestamp


class OpenInterestEvent(BaseEvent):
    open_interest: float


class LiquidationEvent(BaseEvent):
    liq_side: str
    liq_qty: float
    liq_price: float
