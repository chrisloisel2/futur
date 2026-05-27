#!/usr/bin/env python3
"""
scripts/fetch_1m_history.py
============================
Télécharge l'historique complet des klines 1-MINUTE depuis Binance.
Multiples symboles : BTC, ETH, SOL, BNB, XRP, ADA.

BTC/USDT 1m depuis 2021 ≈ 2,600,000 bars
ETH/USDT 1m depuis 2021 ≈ 2,600,000 bars

Stockage dans MongoDB : collections ohlcv_1m (par symbole)
Sauvegarde aussi en Parquet local : data/ohlcv_1m/

Usage :
  python scripts/fetch_1m_history.py                  # Tous symboles depuis 2021
  python scripts/fetch_1m_history.py --symbol BTCUSDT --since 2022-01-01
  python scripts/fetch_1m_history.py --update          # Incrémental depuis dernière barre
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import requests
from pymongo import MongoClient, UpdateOne, ASCENDING

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fetch_1m")

MONGO_URI   = os.getenv("FUTUR_MONGO_URI", os.getenv("MONGODB_URI", "mongodb://localhost:27017"))
DB_NAME     = os.getenv("FUTUR_MONGO_DB",  os.getenv("MONGODB_DB",  "trader"))
COLL_NAME   = "ohlcv_1m"
DATA_DIR    = ROOT / "data" / "ohlcv_1m"

BINANCE_URL = "https://api.binance.com/api/v3/klines"

SYMBOLS = [
    ("BTCUSDT",  "2021-01-01"),   # ~2.3M bars
    ("ETHUSDT",  "2021-01-01"),   # ~2.3M bars
    ("SOLUSDT",  "2021-09-01"),   # ~1.8M bars
    ("BNBUSDT",  "2021-01-01"),   # ~2.3M bars
    ("XRPUSDT",  "2021-01-01"),   # ~2.3M bars
]

_session = requests.Session()
_session.headers["User-Agent"] = "futur-1m-fetcher/1.0"


def get_db():
    return MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)[DB_NAME]


def get_last_ts(symbol: str) -> Optional[pd.Timestamp]:
    """Retourne le timestamp de la dernière barre en MongoDB."""
    coll = get_db()[COLL_NAME]
    coll.create_index([("symbol", ASCENDING), ("timestamp", ASCENDING)], unique=True,
                      name=f"uniq_{COLL_NAME}", background=True)
    doc = coll.find_one({"symbol": symbol}, sort=[("timestamp", -1)],
                        projection={"timestamp": 1})
    if doc and doc.get("timestamp"):
        return pd.Timestamp(doc["timestamp"], tz="UTC")
    return None


def fetch_page(symbol: str, start_ms: int, end_ms: int) -> list:
    for attempt in range(5):
        try:
            r = _session.get(BINANCE_URL, params={
                "symbol": symbol, "interval": "1m",
                "startTime": start_ms, "endTime": end_ms, "limit": 1000,
            }, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == 4:
                log.error(f"  Binance error: {e}")
                return []
            time.sleep(2 ** attempt)
    return []


def rows_to_df(rows: list, symbol: str) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    cols = ["open_time","open","high","low","close","volume",
            "close_time","quote_volume","trades",
            "taker_buy_base","taker_buy_quote","_x"]
    df = pd.DataFrame(rows, columns=cols)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ("open","high","low","close","volume","quote_volume",
               "taker_buy_base","taker_buy_quote"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["trades"] = pd.to_numeric(df["trades"], errors="coerce").fillna(0).astype(int)
    df["symbol"]   = symbol
    df["interval"] = "1m"
    df["taker_buy_ratio"] = df["taker_buy_base"] / (df["volume"] + 1e-12)

    # Features 1m calculées à la volée
    df = df.sort_values("timestamp").reset_index(drop=True)
    close   = df["close"]
    volume  = df["volume"]
    taker_r = df["taker_buy_ratio"]

    df["ret_1m"]    = close.pct_change()
    df["ret_5m"]    = close.pct_change(5)
    df["ret_15m"]   = close.pct_change(15)
    df["ret_1h"]    = close.pct_change(60)

    df["vol_20m"]   = volume.rolling(20, min_periods=1).mean()
    df["vol_ratio"] = volume / (df["vol_20m"] + 1e-9)

    df["rvol_5m"]   = close.pct_change().rolling(5,  min_periods=1).std() * np.sqrt(5  * 252 * 390)
    df["rvol_30m"]  = close.pct_change().rolling(30, min_periods=1).std() * np.sqrt(30 * 252 * 390)

    # RSI 14 (rapide)
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(span=14, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(span=14, adjust=False).mean()
    df["rsi_14_1m"] = 100 - (100 / (1 + gain / (loss + 1e-9)))

    # EMA micro
    df["ema_9_1m"]  = close.ewm(span=9,  adjust=False).mean()
    df["ema_21_1m"] = close.ewm(span=21, adjust=False).mean()
    df["ema_slope"] = (df["ema_9_1m"] - df["ema_9_1m"].shift(5)) / (close + 1e-9) * 100

    # Taker delta
    df["taker_delta_5m"]  = taker_r.rolling(5,  min_periods=1).mean() - 0.5
    df["taker_delta_30m"] = taker_r.rolling(30, min_periods=1).mean() - 0.5

    # VWAP intra-session (rolling 60m)
    df["vwap_60m"] = (close * volume).rolling(60, min_periods=1).sum() / (volume.rolling(60, min_periods=1).sum() + 1e-9)
    df["dist_vwap"] = (close - df["vwap_60m"]) / (df["vwap_60m"] + 1e-9) * 100

    keep = ["timestamp","symbol","interval","open","high","low","close","volume",
            "quote_volume","trades","taker_buy_base","taker_buy_quote","taker_buy_ratio",
            "ret_1m","ret_5m","ret_15m","ret_1h",
            "vol_20m","vol_ratio","rvol_5m","rvol_30m",
            "rsi_14_1m","ema_9_1m","ema_21_1m","ema_slope",
            "taker_delta_5m","taker_delta_30m","vwap_60m","dist_vwap"]
    return df[[c for c in keep if c in df.columns]]


def upsert_batch(df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    coll = get_db()[COLL_NAME]
    ops  = []
    for row in df.to_dict("records"):
        ts  = row["timestamp"]
        sym = row["symbol"]
        if hasattr(ts, "to_pydatetime"):
            ts = ts.to_pydatetime()
        doc = {}
        for k, v in row.items():
            if isinstance(v, float) and (v != v or abs(v) == float("inf")):
                v = None
            elif hasattr(v, "item"):
                v = v.item()
            doc[k] = v
        doc["timestamp"] = ts
        ops.append(UpdateOne(
            {"symbol": sym, "timestamp": ts},
            {"$set": doc},
            upsert=True,
        ))
    if not ops:
        return 0
    res = coll.bulk_write(ops, ordered=False)
    return res.upserted_count + res.modified_count


def save_parquet(df: pd.DataFrame, symbol: str, year: int) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{symbol}_{year}.parquet"
    df.to_parquet(path, index=False)
    log.info(f"  Parquet: {path} ({len(df):,} bars)")


def fetch_symbol(symbol: str, since: str, update: bool = False) -> int:
    """Télécharge tout l'historique 1m pour un symbole."""
    log.info(f"{'='*60}")
    log.info(f"Symbole: {symbol}  since: {since}  update: {update}")

    if update:
        last = get_last_ts(symbol)
        if last is not None:
            # Repart 2h avant pour recalculer les features rolling
            since_ts = last - pd.Timedelta(hours=2)
            since    = since_ts.strftime("%Y-%m-%d %H:%M")
            log.info(f"  Mode update — depuis {since} (dernière barre: {last})")

    start_ms = int(pd.Timestamp(since, tz="UTC").timestamp() * 1000)
    end_ms   = int(datetime.now(timezone.utc).timestamp() * 1000)
    now_ms   = end_ms

    total_inserted = 0
    page = 0
    buffer: list = []
    current_year: Optional[int] = None
    buffer_df_list: list = []

    while start_ms < now_ms:
        rows = fetch_page(symbol, start_ms, now_ms)
        if not rows:
            break

        buffer.extend(rows)
        last_ts_ms = int(rows[-1][0])
        last_dt    = pd.Timestamp(last_ts_ms, unit="ms", tz="UTC")
        year       = last_dt.year
        page      += 1

        if page % 10 == 0:
            log.info(f"  {symbol} page {page:4d} | total {len(buffer):>8,} | {last_dt.date()}")

        # Flush par lot de 10 000 barres
        if len(buffer) >= 10_000:
            df_batch = rows_to_df(buffer, symbol)
            n = upsert_batch(df_batch)
            total_inserted += n
            buffer_df_list.append(df_batch)
            buffer = []

        if len(rows) < 1000:
            break

        start_ms = last_ts_ms + 60_000  # +1 minute
        time.sleep(0.04)  # ~25 req/s — safe pour Binance

    # Flush final
    if buffer:
        df_batch = rows_to_df(buffer, symbol)
        n = upsert_batch(df_batch)
        total_inserted += n
        buffer_df_list.append(df_batch)

    # Sauvegarde Parquet par année
    if buffer_df_list:
        df_all = pd.concat(buffer_df_list, ignore_index=True)
        df_all["year"] = df_all["timestamp"].dt.year
        for yr, grp in df_all.groupby("year"):
            save_parquet(grp.drop(columns=["year"]), symbol, int(yr))

    total = get_db()[COLL_NAME].count_documents({"symbol": symbol})
    log.info(f"  ✓ {symbol}: {total:,} bars total en MongoDB ({total_inserted} nouvelles)")
    return total


def main():
    parser = argparse.ArgumentParser(description="Télécharge l'historique 1m Binance")
    parser.add_argument("--symbol", help="Symbole unique (ex: BTCUSDT)")
    parser.add_argument("--since",  default="2021-01-01", help="Date de début (ISO)")
    parser.add_argument("--update", action="store_true",  help="Mode incrémental")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("HISTORIQUE 1-MINUTE BINANCE")
    log.info(f"MongoDB: {MONGO_URI} / {DB_NAME} / {COLL_NAME}")
    log.info("=" * 60)

    if args.symbol:
        fetch_symbol(args.symbol.upper(), args.since, args.update)
    else:
        grand_total = 0
        for sym, default_since in SYMBOLS:
            n = fetch_symbol(sym, args.since or default_since, args.update)
            grand_total += n
            time.sleep(1)

        log.info("=" * 60)
        log.info(f"TOTAL: {grand_total:,} bars 1m en MongoDB")
        log.info("=" * 60)


if __name__ == "__main__":
    main()
