from __future__ import annotations

from datetime import datetime

import pandas as pd

from common.logging.setup import get_logger

logger = get_logger(__name__)


class ClockSyncModel:
    def __init__(self, alpha: float = 0.1):
        self.alpha = alpha
        self.skew_ms_ewma: float | None = None

    def update(self, skew_ms: float) -> float:
        if self.skew_ms_ewma is None:
            self.skew_ms_ewma = skew_ms
        else:
            self.skew_ms_ewma = self.alpha * skew_ms + (1 - self.alpha) * self.skew_ms_ewma
        return self.skew_ms_ewma

    def align_event_time(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        df = df.copy()
        df["event_time"] = pd.to_datetime(df["event_time"], utc=True)
        df["recv_time"] = pd.to_datetime(df["recv_time"], utc=True)
        skew_ms_series = (df["recv_time"] - df["event_time"]).dt.total_seconds() * 1000
        df["skew_ms"] = skew_ms_series.astype(int)
        ewma_values = []
        for val in skew_ms_series:
            ewma_values.append(int(self.update(val)))
        df["skew_ewma_ms"] = ewma_values
        df["event_time_aligned"] = df["event_time"] + pd.to_timedelta(df["skew_ms"], unit="ms")
        return df

    def staleness(self, df: pd.DataFrame, now: datetime | None = None) -> pd.Series:
        now_ts = pd.Timestamp(now or datetime.utcnow(), tz="UTC")
        return (now_ts - pd.to_datetime(df["event_time_aligned"], utc=True)).dt.total_seconds() * 1000
