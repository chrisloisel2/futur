#!/usr/bin/env python3
"""
data_v2/normalized/spot_ohlcv/build_spot_5m.py
─────────────────────────────────────────────────────────────────────────────
Data V2 step 6: spot 5m OHLCV for the same PIT universe as perp 5m, sourced
from Binance Vision SPOT monthly 1m klines. Adds spot_close/spot_vwap so
data_v2/features/basis.py can causally join perp_spot_basis. Same
aggregate-only policy as perp 5m (see build_perp_5m.py docstring): nothing
raw written to disk, Vision archives are re-fetchable later if needed.

Bounded to the SAME window as the symbol's perp listing (not spot's own,
often earlier, listing date) -- basis needs both legs to exist simultaneously,
so backfilling spot history that predates the perp is not useful here.

Output: data_v2/normalized/spot_ohlcv/venue=binance/symbol={SYM}/year={Y}/
        spot_5m.parquet

Usage:
    /home/qbee/futur/.venv/bin/python3 \\
        data_v2/normalized/spot_ohlcv/build_spot_5m.py --min-free-gb 10
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from data_v2.normalized.binance_vision_klines import fetch_month_1m, resample_5m  # noqa: E402

INSTRUMENT_MASTER = ROOT / "data_v2/instruments/instrument_master.parquet"
OUT_DIR = ROOT / "data_v2/normalized/spot_ohlcv/venue=binance"
BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"


def month_range(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


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
        df_1m = fetch_month_1m(BASE_URL, symbol, y, m)
        if df_1m is None or df_1m.empty:
            manifest["missing_months"].append(key)
            n_missing += 1
            continue
        df_5m = resample_5m(df_1m)
        df_5m["spot_close"] = df_5m["close"]
        df_5m["spot_vwap"] = (df_5m["quote_asset_volume"] / df_5m["volume"].replace(0, pd.NA))
        by_year.setdefault(y, []).append(df_5m)
        manifest["done_months"].append(key)
        n_new += 1

    total_rows = 0
    for y, frames in by_year.items():
        year_dir = symbol_dir / f"year={y}"
        year_dir.mkdir(parents=True, exist_ok=True)
        out_path = year_dir / "spot_5m.parquet"
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

    print(f"Building spot 5m: {len(im)} symbols, min_free_gb={args.min_free_gb}", flush=True)
    t0 = time.time()
    n_no_spot = 0
    for i, row in enumerate(im.sort_values("symbol").itertuples(), 1):
        headroom = free_gb(ROOT)
        if headroom < args.min_free_gb:
            print(f"\nSTOP: free space {headroom:.1f}GB < --min-free-gb {args.min_free_gb}GB "
                  f"after {i - 1}/{len(im)} symbols. Resumable -- re-run to continue.", flush=True)
            sys.exit(1)
        # bounded by PERP listing_ts (basis needs both legs), not spot's own listing
        start = pd.Timestamp(row.listing_ts).date() if pd.notna(row.listing_ts) else date(2019, 9, 1)
        end = pd.Timestamp(row.delisting_ts).date() if pd.notna(row.delisting_ts) else today
        try:
            r = build_symbol(row.symbol, start, min(end, today))
        except Exception as e:
            print(f"  [{i:3}/{len(im)}] {row.symbol:14} ERROR {type(e).__name__}: {e}", flush=True)
            continue
        if r["rows"] == 0 and r["new_months"] == 0 and r["missing_months"] > 0:
            n_no_spot += 1
        print(f"  [{i:3}/{len(im)}] {row.symbol:14} new_months={r['new_months']:3} "
              f"missing={r['missing_months']:3} rows={r['rows']:8} free={headroom:.1f}GB", flush=True)

    print(f"\nDone in {time.time() - t0:.0f}s -> {OUT_DIR} ({n_no_spot} symbols with no spot market at all)", flush=True)


if __name__ == "__main__":
    main()
