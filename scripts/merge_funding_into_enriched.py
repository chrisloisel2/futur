#!/usr/bin/env python3
"""
scripts/merge_funding_into_enriched.py
─────────────────────────────────────────────────────────────────────────────
Phase 4D commit 7: merges the real funding-rate history collected by
collect_funding_rate_binance.py (data/raw/binance_funding_rate/) into the
existing data/enriched/{SYMBOL}_1h_enriched.parquet files produced by the
CANONICAL, unmodified backfill_enriched_from_binance.py pipeline --
kept as a strictly separate step, touching neither feature computation nor
strategy logic, only adding one real, already-fetched column.

Alignment: funding_rate is set ONLY at the exact real funding-event
timestamps (Binance UTC 00:00/08:00/16:00, ~every 8h) -- left NaN at every
other hourly row, never forward-filled or interpolated. This matches
exactly how MultiLegBacktester.fr()/the funding-gate window already
consume it: fr() is only ever called AT funding hours
(`if is_funding_hour(t): ... fr(l.asset, t)`), and the funding-gate window
itself explicitly filters to `index.hour.isin((0, 8, 16))` before use --
so a sparse, non-forward-filled column is the CORRECT representation, not
an approximation of one.

    python3 scripts/merge_funding_into_enriched.py --symbols BTCUSDT,ETHUSDT
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.settings import configure_project_imports
configure_project_imports()

from src.institutional.data.atomic_parquet import atomic_write_parquet

ENRICHED_DIR = ROOT / "data" / "enriched"
FUNDING_RAW_DIR = ROOT / "data" / "raw" / "binance_funding_rate"


def merge_one(symbol: str) -> str:
    enriched_path = ENRICHED_DIR / f"{symbol}_1h_enriched.parquet"
    funding_path = FUNDING_RAW_DIR / f"{symbol}_funding_rate.parquet"
    if not enriched_path.exists():
        return f"SKIP -- {enriched_path.name} absent"
    if not funding_path.exists():
        return f"SKIP -- {funding_path.name} absent"

    df = pd.read_parquet(enriched_path)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)

    funding = pd.read_parquet(funding_path)[["datetime", "fundingRate"]]
    funding["datetime"] = pd.to_datetime(funding["datetime"], utc=True)
    # Real Binance funding timestamps carry a few ms of jitter around the
    # intended hour (e.g. 16:00:00.001 instead of 16:00:00.000) -- floored
    # to the hour before joining. This normalizes the TIMESTAMP's sub-second
    # noise only; the funding RATE values themselves are never touched, and
    # flooring can only ever land on an hour that's already in the grid (no
    # value is invented or moved across a real 8h funding boundary).
    funding["datetime"] = funding["datetime"].dt.floor("h")
    n_raw = len(funding)
    funding = funding.drop_duplicates("datetime").rename(columns={"fundingRate": "funding_rate"})
    n_dupe_dropped = n_raw - len(funding)

    merged = df.drop(columns=["funding_rate"], errors="ignore").merge(
        funding, on="datetime", how="left")
    assert len(merged) == len(df), "row count changed during merge -- must never happen"

    atomic_write_parquet(merged, enriched_path)
    n_set = int(merged["funding_rate"].notna().sum())
    return (f"OK -- {n_set} funding rows merged into {len(merged)} hourly rows "
           f"({n_dupe_dropped} duplicate-hour funding rows dropped after flooring)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", required=True)
    args = ap.parse_args()
    for sym in (s.strip() for s in args.symbols.split(",")):
        print(f"{sym}: {merge_one(sym)}")


if __name__ == "__main__":
    main()
