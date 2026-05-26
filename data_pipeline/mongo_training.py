from __future__ import annotations

import os
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import pandas as pd


LOCAL_MONGO_URI = "mongodb://localhost:27017"
DEFAULT_MONGO_URI = os.getenv(
    "FUTUR_MONGO_URI",
    os.getenv("MONGODB_URI", os.getenv("MONGO_URI", LOCAL_MONGO_URI)),
)
DEFAULT_MONGO_DB = os.getenv("FUTUR_MONGO_DB", os.getenv("MONGODB_DB", "trader"))
DEFAULT_FEATURE_COLLECTION = os.getenv(
    "FUTUR_MONGO_FEATURE_COLLECTION",
    os.getenv(
        "MONGODB_FEATURE_COLLECTION",
        os.getenv("FUTUR_MONGO_OHLCV_COLLECTION", os.getenv("MONGODB_HIST_COLLECTION", "historical_ohlcv_enriched")),
    ),
)


def is_mongo_training_uri(value: object) -> bool:
    return isinstance(value, str) and value.startswith("mongo://")


def normalize_symbol_variants(symbol: str) -> List[str]:
    raw = str(symbol or "").strip().upper().replace("_", "").replace("-", "").replace("/", "")
    if not raw:
        return []
    variants = {raw}
    quotes = ("USDT", "USDC", "BUSD", "USD", "BTC", "ETH", "EUR")
    for quote in quotes:
        if raw.endswith(quote) and len(raw) > len(quote):
            base = raw[: -len(quote)]
            variants.add("%s/%s" % (base, quote))
            if quote == "USD":
                variants.add("%sUSDT" % base)
                variants.add("%s/USDT" % base)
            break
    return sorted(variants)


def compact_symbol_suffix(symbol: str) -> str:
    return str(symbol or "").strip().lower().replace("_", "").replace("-", "").replace("/", "")


def dedicated_collection_candidates(collection_name: str, symbol: str) -> List[str]:
    suffixes = {compact_symbol_suffix(value) for value in normalize_symbol_variants(symbol)}
    suffixes.discard("")
    return ["%s_%s" % (collection_name, suffix) for suffix in sorted(suffixes)]


def parse_mongo_training_uri(uri: str) -> Dict[str, object]:
    parsed = urlparse(uri)
    symbol = (parsed.netloc + parsed.path).strip("/")
    query = parse_qs(parsed.query)
    return {
        "symbol": symbol,
        "interval": query.get("interval", ["1h"])[0],
        "collection": query.get("collection", [DEFAULT_FEATURE_COLLECTION])[0],
        "limit": int(query["limit"][0]) if query.get("limit") else None,
    }


def build_mongo_training_uri(
    symbol: str,
    *,
    interval: str = "1h",
    collection: str = DEFAULT_FEATURE_COLLECTION,
) -> str:
    return "mongo://%s?interval=%s&collection=%s" % (symbol, interval, collection)


def load_ohlcv_features_from_mongo(
    symbol: str,
    *,
    interval: str = "1h",
    collection_name: str = DEFAULT_FEATURE_COLLECTION,
    mongo_uri: str = DEFAULT_MONGO_URI,
    mongo_db: str = DEFAULT_MONGO_DB,
    limit: Optional[int] = None,
) -> pd.DataFrame:
    try:
        from pymongo import ASCENDING, DESCENDING, MongoClient
    except ImportError as exc:
        raise RuntimeError("pymongo is required to load training data from MongoDB") from exc

    symbols = normalize_symbol_variants(symbol)
    if not symbols:
        raise RuntimeError("Invalid Mongo training symbol: %r" % symbol)

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    try:
        db = client[mongo_db]
        query = {"symbol": {"$in": symbols}, "interval": interval}
        sort_dir = DESCENDING if limit else ASCENDING
        records = []
        for candidate in dedicated_collection_candidates(collection_name, symbol) + [collection_name]:
            coll = db[candidate]
            cursor = coll.find(query).sort("timestamp", sort_dir)
            if limit:
                cursor = cursor.limit(int(limit))
            records = list(cursor)
            if records:
                break
    finally:
        client.close()

    if not records:
        raise RuntimeError(
            "No Mongo training rows found in %s.%s or dedicated per-symbol collections for symbol variants %s interval=%s"
            % (mongo_db, collection_name, symbols, interval)
        )

    df = pd.DataFrame(records).drop(columns=["_id"], errors="ignore")
    df["datetime"] = pd.to_datetime(df.get("timestamp", df.get("datetime")), utc=True, errors="coerce")
    df = df.dropna(subset=["datetime"]).sort_values("datetime")
    if limit:
        df = df.tail(int(limit))
    return df.reset_index(drop=True)


def load_mongo_training_uri(uri: str) -> pd.DataFrame:
    spec = parse_mongo_training_uri(uri)
    return load_ohlcv_features_from_mongo(
        str(spec["symbol"]),
        interval=str(spec["interval"]),
        collection_name=str(spec["collection"]),
        limit=spec["limit"],
    )


def mongo_training_available(
    symbol: str,
    *,
    interval: str = "1h",
    collection_name: str = DEFAULT_FEATURE_COLLECTION,
    mongo_uri: str = DEFAULT_MONGO_URI,
    mongo_db: str = DEFAULT_MONGO_DB,
) -> bool:
    try:
        from pymongo import MongoClient
    except ImportError:
        return False
    symbols = normalize_symbol_variants(symbol)
    if not symbols:
        return False
    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=1500)
    try:
        db = client[mongo_db]
        query = {"symbol": {"$in": symbols}, "interval": interval}
        for candidate in dedicated_collection_candidates(collection_name, symbol) + [collection_name]:
            if db[candidate].count_documents(query, limit=1) > 0:
                return True
        return False
    except Exception:
        return False
    finally:
        client.close()
