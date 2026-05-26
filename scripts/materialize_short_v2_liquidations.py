#!/usr/bin/env python3
"""
Materialize real liquidation flow for SHORT v2.

Reads MongoDB liquidation events and joins hourly liquidation USD columns into
the enriched 1h parquet files used by scripts/walk_forward_short_v2.py.

Expected Mongo fields:
  timestamp, symbol, amount_usd, side, source

Binance force-order side convention used here:
  SELL -> long liquidation flow
  BUY  -> short liquidation flow
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Dict, List

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "data" / "enriched"
DEFAULT_OUT_DIR = ROOT / "data" / "enriched_short_v2"


def _mongo_client():
    try:
        from pymongo import MongoClient
    except ImportError as exc:
        raise RuntimeError(
            "pymongo is required. Install ingestion dependencies first."
        ) from exc

    uri = os.getenv("FUTUR_MONGO_URI", os.getenv("MONGODB_URI", os.getenv("MONGO_URI", "mongodb://localhost:27017")))
    return MongoClient(uri, serverSelectionTimeoutMS=5000)


def _normalize_symbol(value: str) -> str:
    value = str(value or "").upper().replace("/", "").replace("-", "")
    if not value.endswith("USDT"):
        value = f"{value}USDT"
    return value


def load_liquidation_events(
    *,
    mongo_db: str,
    collection: str,
    symbols: List[str],
) -> pd.DataFrame:
    client = _mongo_client()
    try:
        coll = client[mongo_db][collection]
        symbol_variants = sorted({s.replace("USDT", "") for s in symbols} | set(symbols))
        cursor = coll.find(
            {"symbol": {"$in": symbol_variants}},
            {"_id": 0, "timestamp": 1, "symbol": 1, "amount_usd": 1, "side": 1, "source": 1},
        )
        rows = list(cursor)
    finally:
        client.close()

    if not rows:
        return pd.DataFrame(columns=["datetime", "symbol", "long_liquidation_usd", "short_liquidation_usd"])

    df = pd.DataFrame(rows)
    df["datetime"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce").dt.floor("h")
    df["symbol"] = df["symbol"].map(_normalize_symbol)
    df["amount_usd"] = pd.to_numeric(df["amount_usd"], errors="coerce").fillna(0.0)
    side = df["side"].astype(str).str.upper()
    df["long_liquidation_usd"] = df["amount_usd"].where(side == "SELL", 0.0)
    df["short_liquidation_usd"] = df["amount_usd"].where(side == "BUY", 0.0)
    df = df[df["datetime"].notna()]

    hourly = (
        df.groupby(["symbol", "datetime"], as_index=False)[
            ["long_liquidation_usd", "short_liquidation_usd"]
        ]
        .sum()
        .sort_values(["symbol", "datetime"])
    )
    return hourly


def join_liquidations(
    parquet_path: Path,
    liq: pd.DataFrame,
    out_dir: Path,
) -> Dict:
    symbol = parquet_path.stem.replace("_1h_enriched", "").upper()
    df = pd.read_parquet(parquet_path)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True).dt.floor("h")
    df = df.drop(
        columns=[
            "long_liquidation_usd",
            "short_liquidation_usd",
            "liq_long_usd",
            "liq_short_usd",
        ],
        errors="ignore",
    )

    liq_sym = liq[liq["symbol"] == symbol].copy()
    if liq_sym.empty:
        df["long_liquidation_usd"] = 0.0
        df["short_liquidation_usd"] = 0.0
    else:
        df = df.merge(
            liq_sym[["datetime", "long_liquidation_usd", "short_liquidation_usd"]],
            on="datetime",
            how="left",
        )
        df["long_liquidation_usd"] = df["long_liquidation_usd"].fillna(0.0)
        df["short_liquidation_usd"] = df["short_liquidation_usd"].fillna(0.0)

    df["liq_long_usd"] = df["long_liquidation_usd"]
    df["liq_short_usd"] = df["short_liquidation_usd"]

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / parquet_path.name
    df.to_parquet(out_path, index=False)
    return {
        "symbol": symbol,
        "rows": int(len(df)),
        "liq_rows": int((df["long_liquidation_usd"] + df["short_liquidation_usd"] > 0).sum()),
        "out": str(out_path),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Join real liquidation flow into SHORT v2 parquets")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--mongo-db", default=os.getenv("FUTUR_MONGO_DB", os.getenv("MONGODB_DB", "trader")))
    parser.add_argument("--collection", default=os.getenv("FUTUR_LIQUIDATION_COLLECTION", "liquidation_events"))
    parser.add_argument("--symbols", nargs="+", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data_dir = Path(args.data_dir)
    paths = sorted(data_dir.glob("*_1h_enriched.parquet"))
    if args.symbols:
        wanted = {s.upper() for s in args.symbols}
        paths = [p for p in paths if p.stem.replace("_1h_enriched", "").upper() in wanted]
    if not paths:
        raise SystemExit(f"No enriched parquets found in {data_dir}")

    symbols = [p.stem.replace("_1h_enriched", "").upper() for p in paths]
    liq = load_liquidation_events(
        mongo_db=args.mongo_db,
        collection=args.collection,
        symbols=symbols,
    )
    print(f"loaded liquidation rows: {len(liq):,}")

    results = [join_liquidations(path, liq, Path(args.out_dir)) for path in paths]
    for item in results:
        print(f"{item['symbol']}: rows={item['rows']:,} liq_rows={item['liq_rows']:,} -> {item['out']}")


if __name__ == "__main__":
    main()
