from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional

import pandas as pd

from data_pipeline.normalization import ensure_raw_schema, normalize_symbol


def write_partitioned_parquet(
    df: pd.DataFrame,
    *,
    root: Path,
    source: str,
    market_type: str,
    symbol: Optional[str],
    interval: Optional[str],
    timestamp_col: str = "timestamp",
    dedupe_keys: Optional[Iterable[str]] = None,
) -> List[Path]:
    if df.empty:
        return []

    frame = ensure_raw_schema(df, source=source, symbol=symbol, market_type=market_type, interval=interval)
    frame[timestamp_col] = pd.to_datetime(frame[timestamp_col], utc=True, errors="coerce")
    frame = frame.dropna(subset=[timestamp_col])
    frame["year"] = frame[timestamp_col].dt.year.astype(int)
    frame["month"] = frame[timestamp_col].dt.month.astype(int)

    clean_symbol = normalize_symbol(symbol or str(frame["symbol"].iloc[0]))
    clean_interval = interval or "snapshot"
    base = root / source / market_type / clean_symbol / clean_interval

    written: List[Path] = []
    keys = list(dedupe_keys or ["source", "symbol", "interval", "timestamp"])
    for (year, month), group in frame.groupby(["year", "month"], sort=True):
        out_dir = base / ("year=%04d" % year) / ("month=%02d" % month)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "data.parquet"
        payload = group.drop(columns=["year", "month"]).copy()
        if out_path.exists():
            existing = pd.read_parquet(out_path)
            payload = pd.concat([existing, payload], ignore_index=True)
        payload = payload.sort_values(timestamp_col)
        present_keys = [key for key in keys if key in payload.columns]
        if present_keys:
            payload = payload.drop_duplicates(subset=present_keys, keep="last")
        payload.to_parquet(out_path, index=False)
        written.append(out_path)
    return written


def read_partitioned_parquet(
    root: Path,
    *,
    source: Optional[str] = None,
    market_type: Optional[str] = None,
    symbol: Optional[str] = None,
    interval: Optional[str] = None,
) -> pd.DataFrame:
    parts = [root]
    if source:
        parts.append(Path(source))
    if market_type:
        parts.append(Path(market_type))
    if symbol:
        parts.append(Path(normalize_symbol(symbol)))
    if interval:
        parts.append(Path(interval))
    search_root = Path(*[str(part) for part in parts])
    files = sorted(search_root.glob("**/*.parquet"))
    if not files:
        return pd.DataFrame()
    frames = [pd.read_parquet(path) for path in files]
    frame = pd.concat(frames, ignore_index=True)
    if "timestamp" in frame.columns:
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
        frame = frame.sort_values("timestamp")
    return frame


def write_mongo_snapshots(
    df: pd.DataFrame,
    *,
    mongo_uri: str,
    database: str,
    collection: str,
    source: str,
    market_type: str,
    symbol: Optional[str],
    interval: Optional[str],
) -> int:
    """Upsert normalized public snapshots to MongoDB when a runtime URI is provided."""

    if df.empty:
        return 0
    from pymongo import MongoClient

    frame = ensure_raw_schema(df, source=source, symbol=symbol, market_type=market_type, interval=interval)
    frame = frame.where(pd.notna(frame), None)
    for col in frame.columns:
        if pd.api.types.is_datetime64_any_dtype(frame[col]):
            frame[col] = pd.to_datetime(frame[col], utc=True, errors="coerce").dt.to_pydatetime()

    client = MongoClient(mongo_uri)
    coll = client[database][collection]
    count = 0
    for doc in frame.to_dict("records"):
        key = {
            "source": doc.get("source"),
            "symbol": doc.get("symbol"),
            "interval": doc.get("interval"),
            "timestamp": doc.get("timestamp"),
        }
        coll.update_one(key, {"$set": doc}, upsert=True)
        count += 1
    return count
