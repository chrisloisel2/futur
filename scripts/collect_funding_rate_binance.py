#!/usr/bin/env python3
"""
scripts/collect_funding_rate_binance.py
─────────────────────────────────────────────────────────────────────────────
Phase 4D commit 7: minimal, deterministic ingestion collector for Binance
USD-M perpetual funding rate history -- the ONE raw data category no
existing collector in this repo fetches (backfill_enriched_from_binance.py
explicitly documents "Macro/funding/OI absents"; nothing under data/raw/
has a funding_rate column). Added because commit 7 point 6 authorizes
exactly this when no existing collector is compatible: ingestion only, no
feature computation, no strategy logic.

Fetches GET /fapi/v1/fundingRate (public, no auth) for the requested
symbols and start date, paginated to "now", deduplicated and sorted, and
writes the raw response as-is (fundingTime, fundingRate, symbol) to
data/raw/binance_funding_rate/{SYMBOL}_funding_rate.parquet -- immutable
raw data, never merged or feature-computed here (see
scripts/merge_funding_into_enriched.py for that, kept as a SEPARATE step
so this collector's only job is ingestion).

    python3 scripts/collect_funding_rate_binance.py --start 2024-07-01 --symbols BTCUSDT,ETHUSDT
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAPI = "https://fapi.binance.com"
RAW_DIR = ROOT / "data" / "raw" / "binance_funding_rate"


def _get(url: str, tries: int = 4) -> object:
    for k in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 400:
                return None
            if k == tries - 1:
                raise
            time.sleep(1.0 + k)
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(1.0 + k)
    return None


def fetch_funding_rate(symbol: str, start_ms: int) -> list[dict]:
    """Paginates GET /fapi/v1/fundingRate from start_ms to now. Each page's
    last fundingTime + 1ms becomes the next startTime -- the endpoint
    itself guarantees no gaps within what it returns (a real Binance
    funding event every 8h, 3/day), this only paginates past the 1000-row
    page limit."""
    rows: list[dict] = []
    cur = start_ms
    now_ms = int(time.time() * 1000)
    while cur < now_ms:
        data = _get(f"{FAPI}/fapi/v1/fundingRate?symbol={symbol}&startTime={cur}&limit=1000")
        if not data:
            break
        rows.extend(data)
        last = data[-1]["fundingTime"]
        if last <= cur:
            break
        cur = last + 1
        time.sleep(0.15)
    return rows


def build_one(symbol: str, start_ms: int) -> str:
    rows = fetch_funding_rate(symbol, start_ms)
    if not rows:
        return "UNAVAILABLE"
    df = pd.DataFrame(rows)[["symbol", "fundingTime", "fundingRate"]]
    df["fundingTime"] = pd.to_numeric(df["fundingTime"], errors="coerce")
    df["fundingRate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
    df["datetime"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    df = df.dropna(subset=["fundingTime", "fundingRate"])
    df = df.drop_duplicates("fundingTime").sort_values("fundingTime").reset_index(drop=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out = RAW_DIR / f"{symbol}_funding_rate.parquet"
    df.to_parquet(out, index=False)
    return f"OK {len(df)} rows -> {out.relative_to(ROOT)}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--symbols", required=True, help="comma-separated, e.g. BTCUSDT,ETHUSDT")
    args = ap.parse_args()
    start_ms = int(pd.Timestamp(args.start, tz="UTC").timestamp() * 1000)
    syms = [s.strip() for s in args.symbols.split(",")]

    print(f"Collect funding rate (Binance USD-M) -- {len(syms)} symbols, start {args.start}")
    for i, sym in enumerate(syms, 1):
        t0 = time.time()
        try:
            res = build_one(sym, start_ms)
        except Exception as e:
            res = f"ERROR {e!r:.80}"
        print(f"  [{i}/{len(syms)}] {sym:<12} {res}  ({time.time() - t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
