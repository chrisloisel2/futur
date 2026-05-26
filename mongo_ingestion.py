from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

LOCAL_MONGO_URI = "mongodb://localhost:27017"
DEFAULT_MONGO_URI = os.getenv(
    "FUTUR_MONGO_URI",
    os.getenv("MONGODB_URI", os.getenv("MONGO_URI", LOCAL_MONGO_URI)),
)
DEFAULT_TRADER_DB = os.getenv(
    "FUTUR_MONGO_DB",
    os.getenv("MONGODB_DB", "trader"),
)
DEFAULT_HISTORICAL_COLLECTION = os.getenv(
    "FUTUR_MONGO_FEATURE_COLLECTION",
    os.getenv(
        "MONGODB_FEATURE_COLLECTION",
        os.getenv("FUTUR_MONGO_OHLCV_COLLECTION", os.getenv("MONGODB_HIST_COLLECTION", "historical_ohlcv_enriched")),
    ),
)

_TRAINING_TO_MONGO_COLUMNS = {
    "datetime": "timestamp",
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Volume": "volume",
    "Taker_Buy_Base": "taker_buy_base",
    "Taker_Buy_Quote": "taker_buy_quote",
    "Trades": "trades",
    "Quote_Volume": "quote_volume",
}


def normalize_symbol(symbol: str) -> str:
    value = str(symbol).strip().upper().replace("_", "/")
    if "/" in value:
        return value

    quote_assets = ("USDT", "USDC", "BUSD", "USD", "BTC", "ETH", "EUR")
    for quote in quote_assets:
        if value.endswith(quote) and len(value) > len(quote):
            return f"{value[:-len(quote)]}/{quote}"
    return value


def _mongo_imports():
    try:
        from pymongo import ASCENDING, DESCENDING, MongoClient, ReplaceOne
    except ImportError as exc:
        raise RuntimeError(
            "pymongo is required for MongoDB ingestion. "
            "Install the ingestion dependencies with: "
            "python -m pip install -r requirements-ingestion.txt"
        ) from exc
    return ASCENDING, DESCENDING, MongoClient, ReplaceOne


def _clean_value(value: Any) -> Any:
    try:
        import numpy as np
        import pandas as pd
    except ImportError:
        np = None
        pd = None

    if pd is not None and isinstance(value, pd.Timestamp):
        if value.tzinfo is not None:
            value = value.tz_convert("UTC").tz_localize(None)
        return value.to_pydatetime()

    if np is not None and isinstance(value, np.generic):
        return _clean_value(value.item())

    if isinstance(value, float) and math.isnan(value):
        return None

    if isinstance(value, dict):
        return {k: _clean_value(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [_clean_value(v) for v in value]

    return value


def _records_from_dataframe(df: Any, symbol: str, interval: str, source: str) -> Iterable[Dict[str, Any]]:
    import pandas as pd

    frame = df.copy()
    if frame.index.name or not isinstance(frame.index, pd.RangeIndex):
        frame = frame.reset_index()

    frame = frame.rename(columns=_TRAINING_TO_MONGO_COLUMNS)
    if "index" in frame.columns and "timestamp" not in frame.columns:
        frame = frame.rename(columns={"index": "timestamp"})
    if "timestamp" not in frame.columns:
        raise ValueError("MongoDB OHLCV ingestion requires a datetime/timestamp column or index")

    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    frame = frame[frame["timestamp"].notna()].copy()

    normalized_symbol = normalize_symbol(symbol)
    ingested_at = datetime.now(timezone.utc)

    for record in frame.to_dict("records"):
        record = {str(key): _clean_value(value) for key, value in record.items()}
        record["symbol"] = normalized_symbol
        record["interval"] = interval
        record["source"] = source
        record["ingested_at"] = ingested_at
        yield record


def ensure_trader_indexes(
    *,
    mongo_uri: str = DEFAULT_MONGO_URI,
    mongo_db: str = DEFAULT_TRADER_DB,
    collection_name: str = DEFAULT_HISTORICAL_COLLECTION,
) -> None:
    ASCENDING, DESCENDING, MongoClient, _ = _mongo_imports()
    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    try:
        db = client[mongo_db]
        collection = db[collection_name]
        collection.create_index(
            [("symbol", ASCENDING), ("interval", ASCENDING), ("timestamp", ASCENDING)],
            unique=True,
            name="uniq_symbol_interval_timestamp",
        )
        collection.create_index([("timestamp", DESCENDING)], name="timestamp_desc")
        collection.create_index([("source", ASCENDING)], name="source")
    finally:
        client.close()


def upsert_ohlcv_dataframe(
    df: Any,
    *,
    symbol: str,
    interval: str = "1h",
    source: str = "binance",
    mongo_uri: str = DEFAULT_MONGO_URI,
    mongo_db: str = DEFAULT_TRADER_DB,
    collection_name: str = DEFAULT_HISTORICAL_COLLECTION,
    batch_size: int = 1000,
) -> Dict[str, int]:
    """Upsert OHLCV/features rows into MongoDB using symbol+interval+timestamp as key."""
    ASCENDING, DESCENDING, MongoClient, ReplaceOne = _mongo_imports()
    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    stats = {"processed": 0, "upserted": 0, "modified": 0, "matched": 0}

    try:
        db = client[mongo_db]
        collection = db[collection_name]
        collection.create_index(
            [("symbol", ASCENDING), ("interval", ASCENDING), ("timestamp", ASCENDING)],
            unique=True,
            name="uniq_symbol_interval_timestamp",
        )
        collection.create_index([("timestamp", DESCENDING)], name="timestamp_desc")
        collection.create_index([("source", ASCENDING)], name="source")

        operations = []
        for record in _records_from_dataframe(df, symbol=symbol, interval=interval, source=source):
            stats["processed"] += 1
            operations.append(
                ReplaceOne(
                    {
                        "symbol": record["symbol"],
                        "interval": record["interval"],
                        "timestamp": record["timestamp"],
                    },
                    record,
                    upsert=True,
                )
            )

            if len(operations) >= batch_size:
                result = collection.bulk_write(operations, ordered=False)
                stats["upserted"] += result.upserted_count or 0
                stats["modified"] += result.modified_count or 0
                stats["matched"] += result.matched_count or 0
                operations.clear()

        if operations:
            result = collection.bulk_write(operations, ordered=False)
            stats["upserted"] += result.upserted_count or 0
            stats["modified"] += result.modified_count or 0
            stats["matched"] += result.matched_count or 0

        return stats
    finally:
        client.close()


def get_latest_ohlcv_timestamp(
    *,
    symbol: str,
    interval: str = "1h",
    mongo_uri: str = DEFAULT_MONGO_URI,
    mongo_db: str = DEFAULT_TRADER_DB,
    collection_name: str = DEFAULT_HISTORICAL_COLLECTION,
) -> Optional[datetime]:
    _, DESCENDING, MongoClient, _ = _mongo_imports()
    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    try:
        doc = client[mongo_db][collection_name].find_one(
            {"symbol": normalize_symbol(symbol), "interval": interval},
            {"timestamp": 1, "_id": 0},
            sort=[("timestamp", DESCENDING)],
        )
        return doc["timestamp"] if doc else None
    finally:
        client.close()
