#!/usr/bin/env python3
from __future__ import annotations

import os

LOCAL_MONGO_URI = "mongodb://localhost:27017"
MONGO_URI = os.getenv(
    "FUTUR_MONGO_URI",
    os.getenv("MONGODB_URI", os.getenv("MONGO_URI", LOCAL_MONGO_URI)),
)
TRADER_DB = os.getenv("FUTUR_MONGO_DB", os.getenv("MONGODB_DB", "trader"))
FEATURE_COLLECTION = os.getenv(
    "FUTUR_MONGO_FEATURE_COLLECTION",
    os.getenv(
        "MONGODB_FEATURE_COLLECTION",
        os.getenv("FUTUR_MONGO_OHLCV_COLLECTION", os.getenv("MONGODB_HIST_COLLECTION", "historical_ohlcv_enriched")),
    ),
)
SOURCE_COLLECTION = os.getenv("FUTUR_MONGO_SOURCE_COLLECTION", os.getenv("MONGODB_SOURCE_COLLECTION", "historical_ohlcv"))
MARKET_DB = os.getenv("MARKETINTEL_MONGO_DB", os.getenv("MONGO_DB", "market_intel"))
PROXY_DB = os.getenv("PROXY_MONGO_DB", "proxy_db")
WHALE_DB = os.getenv("WHALE_MONGODB_DATABASE", os.getenv("BLOCKCHAIN_MONGODB_DATABASE", "whale_data"))


def _mongo_imports():
    try:
        from pymongo import ASCENDING, DESCENDING, MongoClient
    except ImportError as exc:
        raise SystemExit(
            "pymongo is required. Run: python -m pip install -r requirements-ingestion.txt"
        ) from exc
    return ASCENDING, DESCENDING, MongoClient


def main() -> int:
    ASCENDING, DESCENDING, MongoClient = _mongo_imports()
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")

    trader = client[TRADER_DB]
    for collection_name in sorted({SOURCE_COLLECTION, FEATURE_COLLECTION}):
        hist = trader[collection_name]
        hist.create_index(
            [("symbol", ASCENDING), ("interval", ASCENDING), ("timestamp", ASCENDING)],
            unique=True,
            name="uniq_symbol_interval_timestamp",
        )
        hist.create_index([("timestamp", DESCENDING)], name="timestamp_desc")
        hist.create_index([("source", ASCENDING)], name="source")
        hist.create_index([("feature_version", ASCENDING)], name="feature_version")
    trader["realtime_ticks"].create_index([("symbol", ASCENDING), ("timestamp", DESCENDING)])
    trader["dataset_metadata"].create_index([("dataset", ASCENDING), ("path", ASCENDING)])

    market = client[MARKET_DB]
    signals = market["signals"]
    signals.create_index("fingerprint", unique=True)
    for field in ("source", "source_type", "asset", "published_at", "feature_name", "scraped_at"):
        signals.create_index(field)

    proxies = client[PROXY_DB]["proxies"]
    proxies.create_index("url")
    proxies.create_index("proxy")
    proxies.create_index("is_active")

    whales = client[WHALE_DB]["whale_transactions"]
    whales.create_index([("symbol", ASCENDING), ("timestamp", DESCENDING)])
    whales.create_index([("amount_usd", DESCENDING)])
    whales.create_index("blockchain")

    client.close()

    print(f"MongoDB ready at {MONGO_URI}")
    print(f"  {TRADER_DB}.{FEATURE_COLLECTION}")
    print(f"  {TRADER_DB}.{SOURCE_COLLECTION}")
    print(f"  {MARKET_DB}.signals")
    print(f"  {PROXY_DB}.proxies")
    print(f"  {WHALE_DB}.whale_transactions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
