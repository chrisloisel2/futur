#!/usr/bin/env python3
"""
data_v2/normalized/perp_ohlcv/build_perp_5m.py
─────────────────────────────────────────────────────────────────────────────
Data V2 step 5: perp 5m OHLCV for the full PIT universe, sourced from
Binance Vision Futures UM monthly 1m klines (genuine taker_buy_base/quote
columns -- not the data/enriched placeholder, see data_pipeline.
taker_flow_guard). Per user decision (2026-08-09, disk-budget constrained):
aggregate-only -- the monthly 1m zip is fetched into memory, resampled to
5m, and discarded; nothing raw is written to disk. Binance Vision archives
are public and permanent, so this loses no information that can't be
re-fetched later if 1m granularity is ever needed on disk.

Output: data_v2/normalized/perp_ohlcv/venue=binance/symbol={SYM}/year={Y}/
        perp_5m.parquet -- one file per (symbol, year), idempotent via a
        per-symbol manifest of completed year-months.

Usage:
    /home/qbee/futur/.venv/bin/python3 \\
        data_v2/normalized/perp_ohlcv/build_perp_5m.py --min-free-gb 10
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from data_pipeline.normalization import BINANCE_KLINE_COLUMNS  # noqa: E402

INSTRUMENT_MASTER = ROOT / "data_v2/instruments/instrument_master.parquet"
OUT_DIR = ROOT / "data_v2/normalized/perp_ohlcv/venue=binance"
BASE_URL = "https://data.binance.vision/data/futures/um/monthly/klines"

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


def month_range(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


def fetch_month_1m(symbol: str, year: int, month: int) -> pd.DataFrame | None:
    url = f"{BASE_URL}/{symbol}/1m/{symbol}-1m-{year:04d}-{month:02d}.zip"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        name = [n for n in zf.namelist() if n.endswith(".csv")][0]
        with zf.open(name) as fh:
            first_line = fh.readline()
    # Vision changed format in 2025: old monthly files have no header row
    # (data starts immediately), newer ones do (with different column
    # names) -- detect from whether the first field parses as an integer.
    has_header = not first_line.split(b",")[0].strip().isdigit()
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        with zf.open(name) as fh:
            if has_header:
                df = pd.read_csv(fh, low_memory=False)
                df.columns = [c.lower() for c in df.columns]
                rename = {
                    "quote_volume": "quote_asset_volume",
                    "count": "number_of_trades",
                    "taker_buy_volume": "taker_buy_base_asset_volume",
                    "taker_buy_quote_volume": "taker_buy_quote_asset_volume",
                }
                df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
            else:
                df = pd.read_csv(fh, header=None, names=BINANCE_KLINE_COLUMNS, low_memory=False)
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


def free_gb(path: Path) -> float:
    return shutil.disk_usage(path).free / (1024 ** 3)


def load_manifest(symbol_dir: Path) -> dict:
    mf = symbol_dir / "manifest.json"
    return json.loads(mf.read_text()) if mf.exists() else {"done_months": [], "missing_months": []}


def save_manifest(symbol_dir: Path, manifest: dict) -> None:
    (symbol_dir / "manifest.json").write_text(json.dumps(manifest))


def build_symbol(symbol: str, start: date, end: date) -> dict:
    symbol_dir = OUT_DIR / f"symbol={symbol}"
    symbol_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(symbol_dir)
    done = set(manifest["done_months"]) | set(manifest["missing_months"])

    by_year: dict[int, list[pd.DataFrame]] = {}
    n_new, n_missing = 0, 0
    for y, m in month_range(start, end):
        key = f"{y:04d}-{m:02d}"
        if key in done:
            continue
        df_1m = fetch_month_1m(symbol, y, m)
        if df_1m is None or df_1m.empty:
            manifest["missing_months"].append(key)
            n_missing += 1
            continue
        df_5m = resample_5m(df_1m)
        by_year.setdefault(y, []).append(df_5m)
        manifest["done_months"].append(key)
        n_new += 1

    total_rows = 0
    for y, frames in by_year.items():
        year_dir = symbol_dir / f"year={y}"
        year_dir.mkdir(parents=True, exist_ok=True)
        out_path = year_dir / "perp_5m.parquet"
        new = pd.concat(frames).sort_index()
        new = new[~new.index.duplicated(keep="last")]
        if out_path.exists():
            old = pd.read_parquet(out_path)
            new = pd.concat([old, new]).sort_index()
            new = new[~new.index.duplicated(keep="last")]
        tmp = out_path.with_suffix(".tmp.parquet")
        new.reset_index().to_parquet(tmp, index=False)
        tmp.replace(out_path)
        total_rows += len(new)

    manifest["done_months"] = sorted(set(manifest["done_months"]))
    manifest["missing_months"] = sorted(set(manifest["missing_months"]))
    save_manifest(symbol_dir, manifest)
    return {"symbol": symbol, "new_months": n_new, "missing_months": n_missing, "rows": total_rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-free-gb", type=float, default=10.0)
    ap.add_argument("--symbols", default=None, help="comma-separated override; default = full PIT universe")
    args = ap.parse_args()

    im = pd.read_parquet(INSTRUMENT_MASTER)
    im = im[im["symbol"].str.endswith("USDT")].copy()
    today = date.today() - timedelta(days=2)

    if args.symbols:
        wanted = set(s.strip() for s in args.symbols.split(","))
        im = im[im["symbol"].isin(wanted)]

    print(f"Building perp 5m: {len(im)} symbols, min_free_gb={args.min_free_gb}", flush=True)
    t0 = time.time()
    for i, row in enumerate(im.sort_values("symbol").itertuples(), 1):
        headroom = free_gb(ROOT)
        if headroom < args.min_free_gb:
            print(f"\nSTOP: free space {headroom:.1f}GB < --min-free-gb {args.min_free_gb}GB "
                  f"after {i - 1}/{len(im)} symbols. Resumable -- re-run to continue.", flush=True)
            sys.exit(1)
        start = pd.Timestamp(row.listing_ts).date() if pd.notna(row.listing_ts) else date(2019, 9, 1)
        end = pd.Timestamp(row.delisting_ts).date() if pd.notna(row.delisting_ts) else today
        try:
            r = build_symbol(row.symbol, start, min(end, today))
        except Exception as e:
            print(f"  [{i:3}/{len(im)}] {row.symbol:14} ERROR {type(e).__name__}: {e}", flush=True)
            continue
        print(f"  [{i:3}/{len(im)}] {row.symbol:14} new_months={r['new_months']:3} "
              f"missing={r['missing_months']:3} rows={r['rows']:8} free={headroom:.1f}GB", flush=True)

    print(f"\nDone in {time.time() - t0:.0f}s -> {OUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
