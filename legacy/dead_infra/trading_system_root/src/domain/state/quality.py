from __future__ import annotations

from enum import Enum, IntFlag, auto
from typing import Dict

from pydantic import BaseModel


class QualityFlag(IntFlag):
    SCHEMA_INVALID = 1 << 0
    MISSING_FIELDS = 1 << 1
    STALE_EVENT = 1 << 2
    LATE_EVENT = 1 << 3
    DUPLICATE_EVENT = 1 << 4
    TIME_TRAVEL = 1 << 5
    SEQ_GAP = 1 << 6
    OUTLIER_PRICE = 1 << 7
    OUTLIER_QTY = 1 << 8
    BOOK_INVALID = 1 << 9
    SPREAD_ANOMALY = 1 << 10
    BOOK_EMPTY = 1 << 11
    BOOK_EVAPORATION = 1 << 12
    HALT_DETECTED = 1 << 13
    CROSS_SOURCE_MISMATCH = 1 << 14
    CLOCK_SKEW_HIGH = 1 << 15
    MICROSTRUCTURE_TOXIC = 1 << 16


FLAG_DESCRIPTIONS: Dict[QualityFlag, str] = {
    QualityFlag.SCHEMA_INVALID: "Schema validation failed",
    QualityFlag.MISSING_FIELDS: "Missing required fields",
    QualityFlag.STALE_EVENT: "Event too old",
    QualityFlag.LATE_EVENT: "Event arrived after watermark",
    QualityFlag.DUPLICATE_EVENT: "Duplicate event",
    QualityFlag.TIME_TRAVEL: "Event time ordering violated",
    QualityFlag.SEQ_GAP: "Sequence gap detected",
    QualityFlag.OUTLIER_PRICE: "Price outlier",
    QualityFlag.OUTLIER_QTY: "Quantity outlier",
    QualityFlag.BOOK_INVALID: "Book sanity failed",
    QualityFlag.SPREAD_ANOMALY: "Spread anomaly",
    QualityFlag.BOOK_EMPTY: "Book empty",
    QualityFlag.BOOK_EVAPORATION: "Book evaporation",
    QualityFlag.HALT_DETECTED: "Trading halt detected",
    QualityFlag.CROSS_SOURCE_MISMATCH: "Cross source mismatch",
    QualityFlag.CLOCK_SKEW_HIGH: "Clock skew too high",
    QualityFlag.MICROSTRUCTURE_TOXIC: "Microstructure toxicity",
}


class QualityDecision(str, Enum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    QUARANTINE = "QUARANTINE"


class QualityFlagsSnapshot(BaseModel):
    event_time: str
    symbol: str
    venue: str
    quality_flags: int
    tradeable: bool
    data_ok: bool
    microstructure_ok: bool
    cross_source_ok: bool
    stale: bool
    halted: bool
    toxic: bool
    skew_ewma_ms: int
    staleness_ms: int
    gate_run_id: str

    def to_int(self) -> int:
        return int(self.quality_flags)

    @classmethod
    def from_int(cls, value: int, **kwargs) -> "QualityFlagsSnapshot":
        return cls(quality_flags=int(value), **kwargs)
