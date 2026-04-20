from __future__ import annotations

from typing import Optional

import pandas as pd
from pydantic import BaseModel, ConfigDict

from domain.events.raw import BaseEvent


class CleanEvent(BaseEvent):
    quality_flags: int = 0
    is_valid: bool = True
    decision: str = "ACCEPT"
    staleness_ms: int = 0
    late_event: bool = False
    duplicate: bool = False
    outlier: bool = False
    schema_ok: bool = True
    cross_source_ok: bool = True
    cross_source_error_code: int = 0
    skew_ewma_ms: Optional[int] = None
    check_version: int = 1
    quality_run_id: Optional[str] = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


class CleanTradeEvent(CleanEvent):
    trade_id: Optional[str] = None
    price: Optional[float] = None
    qty: Optional[float] = None
    side: Optional[str] = None
    is_maker: Optional[bool] = None
    cvd_delta: Optional[float] = None


class CleanBookEvent(CleanEvent):
    update_id: Optional[int] = None
    depth: Optional[int] = None
    bid_px: Optional[list[float]] = None
    bid_sz: Optional[list[float]] = None
    ask_px: Optional[list[float]] = None
    ask_sz: Optional[list[float]] = None
    mid_price: Optional[float] = None
    spread: Optional[float] = None
    imbalance: Optional[float] = None
    book_ok: Optional[bool] = None
    book_error_code: Optional[int] = None


class CleanOHLCVEvent(CleanEvent):
    bar_start: Optional[pd.Timestamp] = None
    bar_end: Optional[pd.Timestamp] = None
    bar_size_s: Optional[int] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[float] = None


class CleanDerivativesEvent(CleanEvent):
    funding_rate: Optional[float] = None
    open_interest: Optional[float] = None
    liq_side: Optional[str] = None
    liq_qty: Optional[float] = None
    liq_price: Optional[float] = None


class CleanMacroEvent(CleanEvent):
    macro_symbol: Optional[str] = None
    value: Optional[float] = None


class CleanCrossVenueEvent(CleanEvent):
    ref_venue: Optional[str] = None
    ref_price: Optional[float] = None
    target_venue: Optional[str] = None
    target_price: Optional[float] = None
    premium_bps: Optional[float] = None
    basis_bps: Optional[float] = None
    cross_source_ok: bool = True
    cross_source_error_code: int = 0
