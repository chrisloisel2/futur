from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pandas as pd
from pydantic import BaseModel, ConfigDict


class BaseEvent(BaseModel):
    event_time: pd.Timestamp
    recv_time: pd.Timestamp
    event_time_aligned: Optional[pd.Timestamp] = None
    skew_ms: Optional[int] = None
    symbol: str
    venue: str
    source: str
    event_type: str
    seq: int = 0
    ingest_run_id: Optional[str] = None
    payload_version: int = 1
    is_snapshot: bool = False

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def to_record(self) -> Dict[str, Any]:
        data = self.dict()
        data["event_time"] = pd.to_datetime(data["event_time"])
        data["recv_time"] = pd.to_datetime(data["recv_time"])
        if data.get("event_time_aligned") is not None:
            data["event_time_aligned"] = pd.to_datetime(data["event_time_aligned"])
        return data


@dataclass
class RawEventStreamBatch:
    df: pd.DataFrame

    @classmethod
    def from_events(cls, events: List[BaseEvent]) -> "RawEventStreamBatch":
        return cls(pd.DataFrame([e.to_record() for e in events]))

    @classmethod
    def from_records(cls, records: List[Dict[str, Any]]) -> "RawEventStreamBatch":
        return cls(pd.DataFrame(records))

    def to_dataframe(self) -> pd.DataFrame:
        return self.df.copy()
