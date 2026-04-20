from __future__ import annotations
from typing import List
import pandas as pd
import awswrangler as wr

def read_year_df(base: str, symbol: str, quote: str, interval: str, year: int, cols: List[str]) -> pd.DataFrame:
    path = f"{base.rstrip('/')}/interval={interval}/quote={quote}/symbol={symbol}/year={year}/"
    if not wr.s3.list_objects(path):
        raise RuntimeError(f"Missing S3 prefix: {path}")
    df = wr.s3.read_parquet(path, columns=cols, dataset=False)

    if "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    elif "Open Time" in df.columns:
        df["datetime"] = pd.to_datetime(df["Open Time"], unit="ms", utc=True)
    elif "open_time" in df.columns:
        df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    else:
        raise RuntimeError("No datetime column")

    return df.sort_values("datetime").reset_index(drop=True)

def count_total_windows(
    base: str, symbol: str, quote: str, interval: str, years: List[int],
    lookback: int, horizon: int
) -> int:
    total = 0
    bridge = lookback + horizon
    tail = None

    for y in years:
        df = read_year_df(base, symbol, quote, interval, y, ["datetime"])
        if tail is not None:
            df = pd.concat([tail, df], ignore_index=True)
        T = len(df)
        total += max(0, T - lookback - horizon)
        tail = df.iloc[-bridge:].copy() if T >= bridge else df.copy()

    return int(total)
