"""
Utility helpers for working with the MongoDB datastore.

- Centralizes connection handling
- Provides helpers to normalize symbols and move OHLCV data between
  Parquet <-> Mongo collections
"""
import logging
import os
from functools import lru_cache
from typing import Optional

import pandas as pd
from pymongo import ASCENDING, MongoClient
from pymongo.collection import Collection
from pymongo.errors import BulkWriteError, PyMongoError
from pymongo.operations import ReplaceOne

# Connection settings (override with env vars if needed)
MONGODB_URI = os.getenv(
    "MONGODB_URI",
    os.getenv("FUTUR_MONGO_URI", os.getenv("MONGO_URI", "mongodb://localhost:27017")),
)
MONGODB_DB = os.getenv("MONGODB_DB", os.getenv("FUTUR_MONGO_DB", "trader"))
HISTORICAL_COLLECTION = os.getenv(
    "FUTUR_MONGO_FEATURE_COLLECTION",
    os.getenv(
        "MONGODB_FEATURE_COLLECTION",
        os.getenv("FUTUR_MONGO_OHLCV_COLLECTION", os.getenv("MONGODB_HIST_COLLECTION", "historical_ohlcv_enriched")),
    ),
)

logger = logging.getLogger(__name__)


@lru_cache()
def get_client() -> MongoClient:
    """Return a cached Mongo client."""
    return MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)


def get_db():
    """Return the configured database handle."""
    return get_client()[MONGODB_DB]


def get_historical_collection() -> Collection:
    """Return the historical collection, ensuring indexes exist."""
    collection = get_db()[HISTORICAL_COLLECTION]
    collection.create_index(
        [("symbol", ASCENDING), ("interval", ASCENDING), ("timestamp", ASCENDING)],
        unique=True,
        name="uniq_symbol_interval_timestamp",
    )
    return collection


def normalize_symbol(symbol: str) -> str:
    """Normalize symbols to a consistent DB-friendly form."""
    return symbol.replace("_", "/").upper()


def symbol_query_variants(symbol: str) -> list:
    compact = str(symbol or "").strip().upper().replace("_", "").replace("-", "").replace("/", "")
    variants = {normalize_symbol(symbol), compact}
    for quote in ("USDT", "USDC", "BUSD", "USD", "BTC", "ETH", "EUR"):
        if compact.endswith(quote) and len(compact) > len(quote):
            base = compact[: -len(quote)]
            variants.add(f"{base}/{quote}")
            if quote == "USD":
                variants.add(f"{base}USDT")
                variants.add(f"{base}/USDT")
            break
    return sorted(variants)


def dedicated_collection_candidates(symbol: str) -> list:
    suffixes = {
        str(value).strip().lower().replace("_", "").replace("-", "").replace("/", "")
        for value in symbol_query_variants(symbol)
    }
    suffixes.discard("")
    return [f"{HISTORICAL_COLLECTION}_{suffix}" for suffix in sorted(suffixes)]


def _to_python_datetime(value):
    """Convert pandas timestamps to Python datetime."""
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    return value


def upsert_historical_dataframe(
    df: Optional[pd.DataFrame], symbol: str, interval: str = "1h"
) -> int:
    """Bulk upsert a dataframe of OHLCV rows into Mongo."""
    if df is None or df.empty:
        return 0

    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
    if "close_time" in df.columns:
        df["close_time"] = pd.to_datetime(df["close_time"]).dt.tz_localize(None)

    df["symbol"] = normalize_symbol(symbol)
    df["interval"] = interval

    operations = []
    for row in df.to_dict("records"):
        ts = _to_python_datetime(row["timestamp"])
        row["timestamp"] = ts
        if "close_time" in row:
            row["close_time"] = _to_python_datetime(row["close_time"])

        operations.append(
            ReplaceOne(
                {"symbol": row["symbol"], "interval": row["interval"], "timestamp": ts},
                row,
                upsert=True,
            )
        )

    collection = get_historical_collection()

    if not operations:
        return 0

    try:
        result = collection.bulk_write(operations, ordered=False)
        return (result.upserted_count or 0) + (result.modified_count or 0)
    except BulkWriteError as bwe:
        duplicates = len(bwe.details.get("writeErrors", [])) if bwe.details else 0
        return max(len(operations) - duplicates, 0)
    except PyMongoError as exc:
        logger.error("Mongo upsert failed for %s: %s", symbol, exc)
        return 0


def fetch_historical_from_mongo(
    symbol: str, limit: Optional[int] = None, interval: str = "1h"
) -> Optional[pd.DataFrame]:
    """Fetch OHLCV rows from Mongo; return None if unavailable."""
    query = {"symbol": {"$in": symbol_query_variants(symbol)}, "interval": interval}

    try:
        data = []
        db = get_db()
        for coll_name in dedicated_collection_candidates(symbol) + [HISTORICAL_COLLECTION]:
            collection = db[coll_name]
            cursor = collection.find(query).sort("timestamp", -1 if limit else 1)
            if limit:
                cursor = cursor.limit(int(limit))
            data = list(cursor)
            if data:
                break
        if not data:
            return None

        df = pd.DataFrame(data)
        df = df.drop(columns=["_id"], errors="ignore")
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        if "close_time" in df.columns:
            df["close_time"] = pd.to_datetime(df["close_time"])

        df = df.sort_values("timestamp").reset_index(drop=True)
        return df
    except PyMongoError as exc:
        logger.error("Mongo query failed for %s: %s", symbol, exc)
        return None
