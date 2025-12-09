"""
Ingest local datasets into the MongoDB "trader" database.

Usage:
    python mongo_ingest.py

Datasets handled:
- datasets/historical_crypto/*.parquet      -> collection historical_ohlcv
- datasets/alpha_trading/dataset_*/*.parquet -> collections alpha_<file_stem>
- datasets/realtime/*.parquet               -> collection realtime_ticks
- metadata/json helpers saved alongside collections
"""
import json
import logging
import math
from pathlib import Path
from typing import Dict, Tuple

import pandas as pd
import numpy as np
from pymongo.errors import PyMongoError

from mongo_utils import (
    HISTORICAL_COLLECTION,
    MONGODB_DB,
    fetch_historical_from_mongo,
    get_db,
    normalize_symbol,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("mongo_ingest")

ROOT_DATA_DIR = Path("datasets")


def _coerce_timestamps(df: pd.DataFrame) -> pd.DataFrame:
    """Convert common time columns to datetime objects."""
    df = df.copy()
    for col in ["timestamp", "close_time", "date", "datetime"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def _clean_sequence(value):
    cleaned = []
    for item in value:
        if isinstance(item, (list, tuple, np.ndarray)):
            cleaned.append(_clean_sequence(item))
        elif isinstance(item, pd.Timestamp):
            cleaned.append(item.to_pydatetime())
        elif isinstance(item, np.generic):
            cleaned.append(item.item())
        elif isinstance(item, float) and math.isnan(item):
            cleaned.append(None)
        else:
            cleaned.append(item)
    return cleaned


def _clean_record(record: Dict) -> Dict:
    """Sanitize a single document to make it Mongo-serializable."""
    for key, value in list(record.items()):
        if isinstance(value, pd.Timestamp):
            record[key] = value.to_pydatetime()
        elif isinstance(value, np.generic):
            record[key] = value.item()
        elif isinstance(value, (list, tuple, np.ndarray)):
            record[key] = _clean_sequence(value)
        elif isinstance(value, float) and math.isnan(value):
            record[key] = None
    return record


def ingest_historical(interval: str = "1h") -> Tuple[int, int]:
    """Load historical parquet files into Mongo (historical_ohlcv)."""
    data_dir = ROOT_DATA_DIR / "historical_crypto"
    if not data_dir.exists():
        logger.warning("Historical data dir %s missing, skipping", data_dir)
        return 0, 0

    files = sorted(data_dir.glob(f"*_{interval}_*.parquet"))
    total_rows = 0
    total_written = 0
    batch = []

    collection = get_db()[HISTORICAL_COLLECTION]
    collection.delete_many({})

    logger.info("Ingesting %d historical files into %s.%s", len(files), MONGODB_DB, HISTORICAL_COLLECTION)

    for file in files:
        df = pd.read_parquet(file)
        df = _coerce_timestamps(df)

        symbol = df["symbol"].iloc[0] if "symbol" in df.columns else file.stem.split(f"_{interval}_")[0].replace("_", "/")
        df["symbol"] = normalize_symbol(symbol)
        df["interval"] = interval

        records = [_clean_record(r) for r in df.to_dict("records")]
        batch.extend(records)
        total_rows += len(df)
        logger.info("  %s -> %s rows prepared", file.name, len(df))

    if batch:
        collection.insert_many(batch, ordered=False)
        total_written = len(batch)

    logger.info("Historical ingest finished: %s rows processed, %s inserted", total_rows, total_written)
    return total_rows, total_written


def _write_frame(collection_name: str, df: pd.DataFrame, extra_filter: Dict = None) -> int:
    """Replace collection entries matching a filter with new dataframe content."""
    db = get_db()
    collection = db[collection_name]
    filter_query = extra_filter or {}

    collection.delete_many(filter_query)
    if df.empty:
        return 0

    df = df.where(pd.notnull(df), None)
    df = _coerce_timestamps(df)

    records = [_clean_record(r) for r in df.to_dict("records")]
    collection.insert_many(records, ordered=False)
    return len(records)


def ingest_alpha_trading() -> Dict[str, int]:
    """Ingest alpha_trading datasets (latest snapshot)."""
    root = ROOT_DATA_DIR / "alpha_trading"
    dataset_dirs = sorted([p for p in root.glob("dataset_*") if p.is_dir()], reverse=True)

    if not dataset_dirs:
        logger.warning("No alpha_trading dataset_* folders found, skipping")
        return {}

    dataset_dir = dataset_dirs[0]  # use latest snapshot
    logger.info("Ingesting alpha_trading dataset: %s", dataset_dir.name)

    stats: Dict[str, int] = {}
    db = get_db()

    for parquet_file in dataset_dir.glob("*.parquet"):
        df = pd.read_parquet(parquet_file)
        df["dataset"] = dataset_dir.name
        collection_name = f"alpha_{parquet_file.stem}"
        written = _write_frame(collection_name, df, extra_filter={"dataset": dataset_dir.name})
        stats[collection_name] = written
        logger.info("  %s -> %s docs into %s", parquet_file.name, written, collection_name)

    # JSON summaries
    json_files = ["alpha_signals_report.json", "metadata.json", "trading_plan.json"]
    for json_file in json_files:
        file_path = dataset_dir / json_file
        if not file_path.exists():
            continue

        with open(file_path, "r") as f:
            data = json.load(f)

        collection_name = f"alpha_{file_path.stem}"
        collection = db[collection_name]
        collection.delete_many({"dataset": dataset_dir.name})

        if isinstance(data, list):
            docs = [{"dataset": dataset_dir.name, **item} for item in data]
        else:
            docs = [{"dataset": dataset_dir.name, **data}]

        if docs:
            collection.insert_many(docs, ordered=False)
            stats[collection_name] = len(docs)
            logger.info("  %s -> %s docs into %s", file_path.name, len(docs), collection_name)

    return stats


def ingest_realtime() -> int:
    """Ingest realtime parquet drops into a single collection."""
    data_dir = ROOT_DATA_DIR / "realtime"
    if not data_dir.exists():
        logger.warning("Realtime data dir %s missing, skipping", data_dir)
        return 0

    files = sorted(data_dir.glob("*.parquet"))
    if not files:
        return 0

    collection_name = "realtime_ticks"
    db = get_db()
    collection = db[collection_name]
    collection.delete_many({})

    total = 0
    batch = []
    for file in files:
        df = pd.read_parquet(file)
        df = _coerce_timestamps(df)
        df["source_file"] = file.name
        df = df.where(pd.notnull(df), None)

        records = [_clean_record(r) for r in df.to_dict("records")]
        batch.extend(records)
        total += len(records)

    if batch:
        collection.insert_many(batch, ordered=False)

    logger.info("Realtime ingest finished: %s files, %s docs", len(files), total)
    return total


def ingest_metadata():
    """Persist basic metadata files for traceability."""
    db = get_db()
    metadata_collection = db["dataset_metadata"]
    metadata_collection.delete_many({})

    metadata_docs = []
    for meta_file in ROOT_DATA_DIR.rglob("metadata.json"):
        with open(meta_file, "r") as f:
            try:
                payload = json.load(f)
            except Exception:
                payload = {"raw": f.read()}

        metadata_docs.append(
            {
                "dataset": meta_file.parent.name,
                "path": str(meta_file),
                "payload": payload,
            }
        )

    if metadata_docs:
        metadata_collection.insert_many(metadata_docs, ordered=False)
        logger.info("Stored %s metadata documents", len(metadata_docs))


def main():
    logger.info("Target Mongo database: %s", MONGODB_DB)

    try:
        hist_rows, hist_written = ingest_historical()
        alpha_stats = ingest_alpha_trading()
        realtime_count = ingest_realtime()
        ingest_metadata()
    except PyMongoError as exc:
        logger.error("Mongo ingest failed: %s", exc)
        raise

    logger.info("--- INGEST SUMMARY ---")
    logger.info("Historical processed: %s rows, %s inserted", hist_rows, hist_written)
    logger.info("Alpha collections: %s", alpha_stats)
    logger.info("Realtime rows: %s", realtime_count)

    # Quick sanity check: ensure one known symbol is present
    sample = fetch_historical_from_mongo("BTC/USDT", limit=1)
    if sample is not None and not sample.empty:
        ts = sample.iloc[-1]["timestamp"]
        price = sample.iloc[-1]["close"]
        logger.info("Sample BTC/USDT row in Mongo: %s close=%s", ts, price)
    else:
        logger.warning("BTC/USDT not found in Mongo after ingest")


if __name__ == "__main__":
    main()
