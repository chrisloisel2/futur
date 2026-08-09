"""
data_v2/normalized/binance_vision_klines.py
─────────────────────────────────────────────────────────────────────────────
Shared fetch/resample core for Data V2 steps 5 (perp) and 6 (spot): pulls a
Binance Vision monthly 1m klines zip into memory, parses it (handling both
the pre-2025 headerless CSV format and the 2025+ headered format), and
resamples to 5m. Never writes the 1m data to disk (aggregate-only, per
2026-08-09 disk-budget decision) -- Vision archives are public/permanent so
1m can always be re-fetched later if needed.
"""
from __future__ import annotations

import io
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data_pipeline.normalization import BINANCE_KLINE_COLUMNS  # noqa: E402

AGG_MAP = {
    "open": "first",
    "high": "max",
    "low": "min",
    "close": "last",
    "volume": "sum",
    "quote_asset_volume": "sum",
    "number_of_trades": "sum",
    "taker_buy_base_asset_volume": "sum",
    "taker_buy_quote_asset_volume": "sum",
}

HEADER_RENAME = {
    "quote_volume": "quote_asset_volume",
    "count": "number_of_trades",
    "taker_buy_volume": "taker_buy_base_asset_volume",
    "taker_buy_quote_volume": "taker_buy_quote_asset_volume",
}


def fetch_month_1m(base_url: str, symbol: str, year: int, month: int, retries: int = 3) -> pd.DataFrame | None:
    url = f"{base_url}/{symbol}/1m/{symbol}-1m-{year:04d}-{month:02d}.zip"
    raw = None
    last_err = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                raw = r.read()
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            last_err = e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e  # transient (DNS blip, read timeout) -- retry with backoff
        if attempt < retries - 1:
            time.sleep(2 ** attempt)
    if raw is None:
        raise last_err

    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        name = [n for n in zf.namelist() if n.endswith(".csv")][0]
        with zf.open(name) as fh:
            first_line = fh.readline()

    has_header = not first_line.split(b",")[0].strip().isdigit()

    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        with zf.open(name) as fh:
            if has_header:
                df = pd.read_csv(fh, low_memory=False)
                df.columns = [c.lower() for c in df.columns]
                df = df.rename(columns={k: v for k, v in HEADER_RENAME.items() if k in df.columns})
            else:
                df = pd.read_csv(fh, header=None, names=BINANCE_KLINE_COLUMNS, low_memory=False)

    if df.empty:
        return None

    unit = "us" if len(str(int(df["open_time"].iloc[0]))) >= 16 else "ms"
    df["timestamp"] = pd.to_datetime(df["open_time"].astype("int64"), unit=unit, utc=True)
    for col in ["open", "high", "low", "close", "volume", "quote_asset_volume",
                "number_of_trades", "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.set_index("timestamp").sort_index()


def resample_5m(df_1m: pd.DataFrame) -> pd.DataFrame:
    agg = {k: v for k, v in AGG_MAP.items() if k in df_1m.columns}
    out = df_1m.resample("5min").agg(agg)
    return out.dropna(subset=["open", "high", "low", "close"])
