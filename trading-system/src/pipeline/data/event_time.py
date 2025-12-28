from __future__ import annotations

import numpy as np
import pandas as pd

from common.logging.setup import get_logger

logger = get_logger(__name__)


class ClockSkewEstimator:
    def estimate_skew_ms(self, df: pd.DataFrame) -> float:
        if df.empty:
            return 0.0
        skew = (pd.to_datetime(df["recv_time"]) - pd.to_datetime(df["event_time"])).dt.total_seconds() * 1000
        return float(skew.median())


class EventTimeAligner:
    def __init__(self, watermark_ms: int = 5_000):
        self.watermark_ms = watermark_ms
        self.skew_estimator = ClockSkewEstimator()

    def align(self, events_df: pd.DataFrame) -> pd.DataFrame:
        if events_df.empty:
            return events_df
        df = events_df.copy()
        skew_ms = self.skew_estimator.estimate_skew_ms(df)
        df["skew_ms"] = skew_ms
        df["event_time"] = pd.to_datetime(df["event_time"], utc=True)
        df["recv_time"] = pd.to_datetime(df["recv_time"], utc=True)
        df["event_time_aligned"] = df["event_time"] + pd.to_timedelta(skew_ms, unit="ms")
        df = df.sort_values("event_time_aligned")
        return df

    def detect_time_travel(self, events_df: pd.DataFrame) -> pd.DataFrame:
        if events_df.empty:
            return events_df
        df = events_df.copy().sort_values("event_time_aligned")
        df["time_travel"] = df["event_time_aligned"].diff().dt.total_seconds() < 0
        return df

    def watermark(self, events_df: pd.DataFrame) -> pd.DataFrame:
        if events_df.empty:
            return events_df
        df = events_df.copy()
        latest = df["event_time_aligned"].max()
        df["is_late"] = (latest - df["event_time_aligned"]).dt.total_seconds() * 1000 > self.watermark_ms
        return df
