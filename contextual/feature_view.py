from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd
from pymongo import ASCENDING, DESCENDING, MongoClient, UpdateOne

DEFAULT_MONGO_URI = os.getenv("FUTUR_MONGO_URI", os.getenv("MONGODB_URI", os.getenv("MONGO_URI", "mongodb://localhost:27017")))
DEFAULT_TRADER_DB = os.getenv("FUTUR_MONGO_DB", os.getenv("MONGODB_DB", "trader"))
DEFAULT_MARKET_DB = os.getenv("MARKETINTEL_MONGO_DB", os.getenv("MONGO_DB", "market_intel"))
DEFAULT_OUTPUT_COLLECTION = os.getenv("CONTEXT_FEATURE_COLLECTION", "context_features_hourly")

ASSET_TO_SYMBOL = {
    "BTC": "BTC/USDT",
    "ETH": "ETH/USDT",
    "SOL": "SOL/USDT",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _numeric(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if np.isnan(value) or np.isinf(value):
        return None
    return value


def _rolling_zscore(series: pd.Series, window: int) -> pd.Series:
    mean = series.rolling(window, min_periods=max(3, window // 4)).mean()
    std = series.rolling(window, min_periods=max(3, window // 4)).std().replace(0, np.nan)
    return ((series - mean) / std).replace([np.inf, -np.inf], np.nan)


def fetch_ohlcv_index(
    client: MongoClient,
    *,
    trader_db: str,
    symbol: str,
    since: datetime,
) -> pd.DatetimeIndex:
    cursor = client[trader_db]["historical_ohlcv"].find(
        {"symbol": symbol, "interval": "1h", "timestamp": {"$gte": since.replace(tzinfo=None)}},
        {"_id": 0, "timestamp": 1},
    ).sort("timestamp", ASCENDING)
    timestamps = [pd.Timestamp(row["timestamp"], tz="UTC") for row in cursor]
    return pd.DatetimeIndex(timestamps, name="timestamp")


def fetch_market_signals(
    client: MongoClient,
    *,
    market_db: str,
    asset: str,
    since: datetime,
) -> List[Dict[str, Any]]:
    cursor = client[market_db]["signals"].find(
        {"asset": asset, "published_at": {"$gte": since.isoformat()}},
        {"_id": 0},
    ).sort("published_at", ASCENDING)
    return list(cursor)


def signals_to_hourly_features(
    docs: Sequence[Dict[str, Any]],
    index: pd.DatetimeIndex,
    *,
    asset: str,
) -> pd.DataFrame:
    frame = pd.DataFrame(index=index).sort_index()
    if len(frame.index) == 0:
        return frame

    frame["asset"] = asset
    frame["symbol"] = ASSET_TO_SYMBOL.get(asset, f"{asset}/USDT")
    frame["signal_count_1h"] = 0.0
    frame["source_binance_count_1h"] = 0.0
    frame["source_coingecko_count_1h"] = 0.0
    frame["source_alternative_me_count_1h"] = 0.0

    rows = []
    for doc in docs:
        ts = _to_datetime(doc.get("published_at"))
        if ts is None:
            continue
        hour = pd.Timestamp(ts).tz_convert("UTC").floor("h")
        row = {
            "timestamp": hour,
            "source": doc.get("source"),
            "feature_name": doc.get("feature_name"),
            "value": _numeric(doc.get("value")),
            "event_type": doc.get("event_type"),
            "metadata": doc.get("metadata") or {},
        }
        rows.append(row)

    if not rows:
        return frame.fillna(0.0)

    signals = pd.DataFrame(rows).set_index("timestamp").sort_index()

    counts = signals.resample("1h").size().rename("signal_count_1h")
    frame["signal_count_1h"] = counts.reindex(frame.index).fillna(0.0)
    for source in ("binance", "coingecko", "alternative_me"):
        source_counts = signals[signals["source"] == source].resample("1h").size()
        frame[f"source_{source}_count_1h"] = source_counts.reindex(frame.index).fillna(0.0)

    numeric = signals[signals["value"].notna()].copy()
    if not numeric.empty:
        pivot = numeric.pivot_table(
            index=numeric.index,
            columns=["source", "feature_name"],
            values="value",
            aggfunc="last",
        )
        pivot.columns = [f"{source}_{feature}" for source, feature in pivot.columns]
        pivot = pivot.resample("1h").last().reindex(frame.index)
        frame = frame.join(pivot)

    # Expand selected numeric metadata that has direct trading use.
    metadata_rows = []
    for row in rows:
        metadata = row["metadata"]
        if not metadata:
            continue
        expanded = {"timestamp": row["timestamp"], "source": row["source"]}
        for key, value in metadata.items():
            number = _numeric(value)
            if number is not None:
                expanded[f"{row['source']}_{key}"] = number
        metadata_rows.append(expanded)

    if metadata_rows:
        meta = pd.DataFrame(metadata_rows).set_index("timestamp").sort_index()
        value_cols = [col for col in meta.columns if col != "source"]
        if value_cols:
            meta = meta[value_cols].resample("1h").last().reindex(frame.index)
            frame = frame.join(meta)

    # Source-specific forward-fill horizons. Funding updates every 8h; F&G daily; CoinGecko snapshots are live.
    ffill_limits = {
        "binance_funding_rate": 8,
        "binance_mark_price": 8,
        "alternative_me_fear_greed_index": 30,
        "coingecko_market_cap_rank": 30,
        "coingecko_current_price": 30,
        "coingecko_market_cap": 30,
        "coingecko_total_volume": 30,
        "coingecko_price_change_percentage_24h": 30,
        "coingecko_circulating_supply": 30,
    }
    for col, limit in ffill_limits.items():
        if col in frame.columns:
            frame[col] = frame[col].ffill(limit=limit)

    if "binance_funding_rate" in frame.columns:
        funding = frame["binance_funding_rate"].fillna(0.0)
        frame["funding_rate_abs"] = funding.abs()
        frame["funding_rate_sum_24h"] = funding.rolling(24, min_periods=1).sum()
        frame["funding_rate_z_24"] = _rolling_zscore(funding, 24).fillna(0.0)
        frame["funding_rate_z_72"] = _rolling_zscore(funding, 72).fillna(0.0)

    if "alternative_me_fear_greed_index" in frame.columns:
        fng = frame["alternative_me_fear_greed_index"]
        frame["fear_greed_z_72"] = _rolling_zscore(fng.ffill(), 72).fillna(0.0)

    count_cols = [col for col in frame.columns if col.endswith("_count_1h") or col == "signal_count_1h"]
    for col in count_cols:
        frame[f"{col.replace('_1h', '')}_24h"] = frame[col].rolling(24, min_periods=1).sum()

    numeric_cols = frame.select_dtypes(include=[np.number]).columns
    frame[numeric_cols] = frame[numeric_cols].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    frame = frame.reset_index()
    return frame


def _clean_record(record: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = {}
    for key, value in record.items():
        if isinstance(value, pd.Timestamp):
            value = value.to_pydatetime()
            if value.tzinfo is not None:
                value = value.astimezone(timezone.utc).replace(tzinfo=None)
        elif isinstance(value, np.generic):
            value = value.item()
        elif isinstance(value, float) and (np.isnan(value) or np.isinf(value)):
            value = None
        cleaned[key] = value
    cleaned["materialized_at"] = _utc_now()
    return cleaned


def upsert_feature_view(
    client: MongoClient,
    *,
    trader_db: str,
    collection_name: str,
    frame: pd.DataFrame,
) -> int:
    collection = client[trader_db][collection_name]
    collection.create_index(
        [("asset", ASCENDING), ("symbol", ASCENDING), ("timestamp", ASCENDING)],
        unique=True,
        name="uniq_asset_symbol_timestamp",
    )
    collection.create_index([("timestamp", DESCENDING)], name="timestamp_desc")

    ops = []
    for record in frame.to_dict("records"):
        doc = _clean_record(record)
        ops.append(
            UpdateOne(
                {"asset": doc["asset"], "symbol": doc["symbol"], "timestamp": doc["timestamp"]},
                {"$set": doc},
                upsert=True,
            )
        )

    if not ops:
        return 0
    result = collection.bulk_write(ops, ordered=False)
    return (result.upserted_count or 0) + (result.modified_count or 0)


def materialize_context_features(
    *,
    mongo_uri: str = DEFAULT_MONGO_URI,
    trader_db: str = DEFAULT_TRADER_DB,
    market_db: str = DEFAULT_MARKET_DB,
    output_collection: str = DEFAULT_OUTPUT_COLLECTION,
    asset: str = "BTC",
    lookback_days: int = 90,
) -> Dict[str, Any]:
    asset = asset.upper()
    symbol = ASSET_TO_SYMBOL.get(asset, f"{asset}/USDT")
    since = _utc_now() - timedelta(days=lookback_days)

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    try:
        index = fetch_ohlcv_index(client, trader_db=trader_db, symbol=symbol, since=since)
        docs = fetch_market_signals(client, market_db=market_db, asset=asset, since=since)
        frame = signals_to_hourly_features(docs, index, asset=asset)
        written = upsert_feature_view(
            client,
            trader_db=trader_db,
            collection_name=output_collection,
            frame=frame,
        )
        return {
            "asset": asset,
            "symbol": symbol,
            "signals": len(docs),
            "rows": len(frame),
            "written": written,
            "collection": f"{trader_db}.{output_collection}",
            "columns": list(frame.columns),
        }
    finally:
        client.close()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize hourly contextual features from MarketIntel signals")
    parser.add_argument("--mongo-uri", default=DEFAULT_MONGO_URI)
    parser.add_argument("--trader-db", default=DEFAULT_TRADER_DB)
    parser.add_argument("--market-db", default=DEFAULT_MARKET_DB)
    parser.add_argument("--collection", default=DEFAULT_OUTPUT_COLLECTION)
    parser.add_argument("--asset", default="BTC")
    parser.add_argument("--lookback-days", type=int, default=90)
    args = parser.parse_args(argv)

    result = materialize_context_features(
        mongo_uri=args.mongo_uri,
        trader_db=args.trader_db,
        market_db=args.market_db,
        output_collection=args.collection,
        asset=args.asset,
        lookback_days=args.lookback_days,
    )
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
