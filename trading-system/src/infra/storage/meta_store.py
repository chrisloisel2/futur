from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List

import pandas as pd

from infra.storage.object_store import S3ParquetReader
from infra.storage.timeseries_db import MongoBufferReader


class MetaStore:
    def __init__(self, s3_prefix: str, mongo_uri: str | None = None, mongo_db: str | None = None):
        self.s3_prefix = s3_prefix.rstrip('/')
        self.mongo_uri = mongo_uri
        self.mongo_db = mongo_db
        self.reader = S3ParquetReader()

    def load_perf_snapshot(self, symbols: List[str], window_minutes: int = 60) -> Dict:
        if self.mongo_uri and self.mongo_db:
            try:
                mongo = MongoBufferReader(self.mongo_uri, self.mongo_db, "perf_snapshot_cache")
                end = datetime.utcnow()
                start = end - timedelta(minutes=window_minutes)
                df = mongo.fetch_events(symbols[0], start, end) if symbols else None
                if df is not None and not df.empty:
                    return df.iloc[-1].to_dict()
            except Exception:
                pass
        path = f"{self.s3_prefix}/artifacts/monitoring/perf_snapshots"
        try:
            df = self.reader.read(path)
            if df.empty:
                return {}
            return df.sort_values("event_time").iloc[-1].to_dict()
        except FileNotFoundError:
            return {}

    def load_drift_snapshot(self, symbols: List[str], window_minutes: int = 60) -> Dict:
        if self.mongo_uri and self.mongo_db:
            try:
                mongo = MongoBufferReader(self.mongo_uri, self.mongo_db, "drift_snapshot_cache")
                end = datetime.utcnow()
                start = end - timedelta(minutes=window_minutes)
                df = mongo.fetch_events(symbols[0], start, end) if symbols else None
                if df is not None and not df.empty:
                    return df.iloc[-1].to_dict()
            except Exception:
                pass
        path = f"{self.s3_prefix}/artifacts/monitoring/drift_snapshots"
        try:
            df = self.reader.read(path)
            if df.empty:
                return {}
            return df.sort_values("event_time").iloc[-1].to_dict()
        except FileNotFoundError:
            return {}
