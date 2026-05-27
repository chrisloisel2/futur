from __future__ import annotations

import pandas as pd

from infra.storage.object_store import S3ParquetReader


class FeatureStore:
    def __init__(self, prefix: str):
        self.prefix = prefix.rstrip('/')
        self.reader = S3ParquetReader()

    def load_state(self, symbol: str, start: str, end: str, feature_set: str = "v1") -> pd.DataFrame:
        path = f"{self.prefix}/data/state"
        df = self.reader.read(path, filters={"symbol": symbol, "feature_set": feature_set})
        df["event_time"] = pd.to_datetime(df["event_time"])
        mask = (df["event_time"] >= pd.to_datetime(start)) & (df["event_time"] <= pd.to_datetime(end))
        return df[mask]
