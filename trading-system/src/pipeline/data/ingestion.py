from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional, Protocol

import pandas as pd

from common.logging.setup import get_logger
from infra.storage.object_store import S3ParquetReader, S3ParquetWriter
from infra.storage.timeseries_db import MongoBufferReader, MongoBufferWriter
from pipeline.data.event_time import EventTimeAligner
from pipeline.data.normalization import Normalizer

logger = get_logger(__name__)


class SourceConnector(Protocol):
    name: str
    venue: str
    source: str

    def fetch(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        ...


class BaseConnector:
    """
    Base connector with retry/backoff, ready for WS/REST implementations.
    Subclasses override `_fetch_impl` to return raw events.
    """

    def __init__(self, name: str, venue: str, source: str, max_retries: int = 3, backoff_seconds: float = 0.5):
        self.name = name
        self.venue = venue
        self.source = source
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds

    def _fetch_impl(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        raise NotImplementedError

    def fetch(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        last_err: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                df = self._fetch_impl(symbol, start, end)
                if df.empty:
                    return df
                df["venue"] = self.venue
                df["source"] = self.source
                return df
            except Exception as exc:  # pragma: no cover - defensive
                last_err = exc
                sleep_for = self.backoff_seconds * attempt
                logger.warning({"msg": "connector_fetch_failed", "connector": self.name, "attempt": attempt, "sleep_s": sleep_for, "error": str(exc)})
                time.sleep(sleep_for)
        logger.error({"msg": "connector_fetch_gave_up", "connector": self.name, "error": str(last_err)})
        return pd.DataFrame()


class BinanceSpotConnector(BaseConnector):
    def __init__(self):
        super().__init__("binance_spot", venue="binance", source="spot")

    def _fetch_impl(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        return pd.DataFrame()  # TODO: implement WS/REST fetch


class BinanceFuturesConnector(BaseConnector):
    def __init__(self):
        super().__init__("binance_futures", venue="binance", source="futures")

    def _fetch_impl(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        return pd.DataFrame()


class BybitConnector(BaseConnector):
    def __init__(self):
        super().__init__("bybit", venue="bybit", source="futures")

    def _fetch_impl(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        return pd.DataFrame()


class OKXConnector(BaseConnector):
    def __init__(self):
        super().__init__("okx", venue="okx", source="futures")

    def _fetch_impl(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        return pd.DataFrame()


class DeribitConnector(BaseConnector):
    def __init__(self):
        super().__init__("deribit", venue="deribit", source="options")

    def _fetch_impl(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        return pd.DataFrame()


class MacroConnector(BaseConnector):
    def __init__(self):
        super().__init__("macro", venue="macro_feed", source="macro")

    def _fetch_impl(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        return pd.DataFrame()


class CrossVenueConnector(BaseConnector):
    def __init__(self):
        super().__init__("cross_venue", venue="aggregator", source="cross_venue")

    def _fetch_impl(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        return pd.DataFrame()


@dataclass
class IngestionConfig:
    mongo_uri: str
    mongo_db: str
    mongo_collection: str
    s3_prefix: str
    ingest_run_id: str
    ttl_seconds: int = 3600
    payload_version: int = 1
    partitioning: Optional[Dict[str, str]] = None
    backfill_chunk_minutes: int = 15
    write_to_s3: bool = True
    write_to_mongo: bool = True


class IngestionOrchestrator:
    """
    Live ingestion orchestrator:
    - pulls from multiple connectors
    - normalizes + aligns event_time
    - writes to Mongo buffer (TTL) and S3 parquet partitioned
    """

    def __init__(
        self,
        connectors: Iterable[SourceConnector],
        config: IngestionConfig,
        normalizer: Optional[Normalizer] = None,
        aligner: Optional[EventTimeAligner] = None,
        mongo_writer: Optional[MongoBufferWriter] = None,
        s3_writer: Optional[S3ParquetWriter] = None,
    ) -> None:
        self.connectors = list(connectors)
        self.config = config
        self.normalizer = normalizer or Normalizer()
        self.aligner = aligner or EventTimeAligner()
        self.mongo_writer = mongo_writer or MongoBufferWriter(config.mongo_uri, config.mongo_db, config.mongo_collection)
        self.s3_writer = s3_writer or S3ParquetWriter()

    def _persist(self, df: pd.DataFrame, connector: SourceConnector) -> pd.DataFrame:
        if df.empty:
            return df
        df["ingest_run_id"] = self.config.ingest_run_id
        df["payload_version"] = self.config.payload_version
        df["is_snapshot"] = df.get("is_snapshot", False)
        df = self.normalizer.normalize_events(df)
        df = self.normalizer.deduplicate(df)
        df = self.aligner.align(df)

        if self.config.write_to_mongo:
            self.mongo_writer.write_events(df, ttl_seconds=self.config.ttl_seconds)

        if self.config.write_to_s3:
            df["dt"] = df["event_time_aligned"].dt.strftime("%Y-%m-%d")
            self.s3_writer.write(
                df,
                f"{self.config.s3_prefix}/data/raw/{connector.source}",
                partition_cols=["dt", "symbol", "venue", "source"],
            )
        return df

    def run_live_once(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        frames: List[pd.DataFrame] = []
        for connector in self.connectors:
            df = connector.fetch(symbol, start, end)
            if df.empty:
                continue
            df = self._persist(df, connector)
            frames.append(df)
        if not frames:
            return pd.DataFrame()
        out = pd.concat(frames).sort_values("event_time_aligned")
        logger.info({"msg": "live_ingest_complete", "rows": len(out), "connectors": [c.name for c in self.connectors]})
        return out

    def run_live_loop(self, symbol: str, lookback_minutes: int = 5, poll_seconds: int = 2) -> None:
        """
        Simple polling loop for live ingestion (wrap with process manager/threads as needed).
        """
        window = timedelta(minutes=lookback_minutes)
        while True:  # pragma: no cover - long running loop
            end = datetime.utcnow()
            start = end - window
            self.run_live_once(symbol, start, end)
            time.sleep(poll_seconds)

    def run_batch_from_s3(self, prefix: str, filters: Optional[dict] = None) -> pd.DataFrame:
        reader = S3ParquetReader()
        df = reader.read(prefix, filters=filters or {})
        df = self.normalizer.deduplicate(self.aligner.align(df))
        logger.info({"msg": "batch_rebuild_loaded", "rows": len(df)})
        return df


def read_raw_events(
    symbol: str,
    start: datetime,
    end: datetime,
    sources: Optional[List[str]] = None,
    prefer: str = "s3_then_mongo",
    s3_prefix: Optional[str] = None,
    mongo_uri: Optional[str] = None,
    mongo_db: Optional[str] = None,
    mongo_collection: Optional[str] = None,
) -> pd.DataFrame:
    """
    Read raw events with preference strategy. Deduplicates and sorts by event_time_aligned.
    """
    sources = sources or []
    frames: List[pd.DataFrame] = []
    if prefer.startswith("s3") and s3_prefix:
        reader = S3ParquetReader()
        for src in sources:
            path = f"{s3_prefix}/data/raw/{src}"
            try:
                df = reader.read(path, filters={"symbol": symbol})
            except FileNotFoundError:
                continue
            df = df[(df["event_time_aligned"] >= pd.to_datetime(start)) & (df["event_time_aligned"] <= pd.to_datetime(end))]
            frames.append(df)
    if (not frames or prefer.endswith("mongo")) and mongo_uri and mongo_db and mongo_collection:
        mreader = MongoBufferReader(mongo_uri, mongo_db, mongo_collection)
        df = mreader.fetch_events(symbol, start, end, sources=sources)
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames)
    if "event_time_aligned" not in out.columns:
        out["event_time_aligned"] = pd.to_datetime(out["event_time"])
    out = out.sort_values("event_time_aligned")
    out = out.drop_duplicates(subset=["symbol", "venue", "source", "event_type", "seq", "event_time_aligned"])
    return out
