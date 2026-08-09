"""
research/edge_factory/multileg_engine/instrument.py — InstrumentMaster (interface 1/5).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

PERP = "perp"
DATED_FUTURE = "dated_future"


@dataclass(frozen=True)
class Instrument:
    venue: str                       # binance | bybit | hyperliquid
    symbol: str                      # BTC, ETH, ... (sans suffixe USDT)
    instrument_type: str             # perp | dated_future
    expiry: Optional[date] = None    # requis pour dated_future, interdit pour perp
    listed_from: Optional[date] = None
    delisted_at: Optional[date] = None

    def __post_init__(self) -> None:
        if self.instrument_type not in (PERP, DATED_FUTURE):
            raise ValueError(f"instrument_type inconnu: {self.instrument_type}")
        if self.instrument_type == PERP and self.expiry is not None:
            raise ValueError("un perp ne porte pas d'échéance")
        if self.instrument_type == DATED_FUTURE and self.expiry is None:
            raise ValueError("un dated_future doit porter une échéance")

    @property
    def key(self) -> str:
        suffix = f":{self.expiry.isoformat()}" if self.expiry else ""
        return f"{self.venue}:{self.symbol}:{self.instrument_type}{suffix}"
