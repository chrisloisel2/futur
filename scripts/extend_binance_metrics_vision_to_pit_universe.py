#!/usr/bin/env python3
"""
scripts/extend_binance_metrics_vision_to_pit_universe.py
─────────────────────────────────────────────────────────────────────────────
Data V2 step 4: extend scripts/backfill_binance_metrics_vision.py (real 5m
sum_open_interest from Binance Vision) from the 49 symbols already covered
to the full 312-symbol PIT universe (data_v2/instruments/instrument_master.
parquet). Not a rewrite -- reuses backfill_symbol()/OUT_DIR from the
existing, idempotent collector as-is.

Per-symbol start date is bounded by the symbol's own listing_ts (never
2021-01-01 for a coin that listed in 2024) to avoid wasting requests on
guaranteed-404 pre-listing days -- this is also what keeps a 312-symbol run
from taking multiples longer than necessary.

Disk safety: this shares a filesystem with live trading services. Checks
free space before each symbol and stops (not crashes, not deletes anything)
if free space drops under --min-free-gb, leaving the manifest in a resumable
state -- re-running this script picks up exactly where it left off.

Usage:
    /home/qbee/futur/.venv/bin/python3 \\
        scripts/extend_binance_metrics_vision_to_pit_universe.py \\
        --min-free-gb 10 --workers 8
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from backfill_binance_metrics_vision import backfill_symbol, OUT_DIR  # noqa: E402

INSTRUMENT_MASTER = ROOT / "data_v2/instruments/instrument_master.parquet"
VISION_METRICS_START = date(2020, 9, 1)  # Binance Vision metrics history floor


def free_gb(path: Path) -> float:
    return shutil.disk_usage(path).free / (1024 ** 3)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--min-free-gb", type=float, default=10.0)
    ap.add_argument("--end", default=None, help="default: J-2")
    args = ap.parse_args()

    end = date.fromisoformat(args.end) if args.end else date.today() - timedelta(days=2)

    im = pd.read_parquet(INSTRUMENT_MASTER)
    im = im[im["symbol"].str.endswith("USDT")].copy()
    im["start_date"] = im["listing_ts"].apply(
        lambda ts: max(pd.Timestamp(ts).date(), VISION_METRICS_START) if pd.notna(ts) else VISION_METRICS_START
    )
    symbols = im.sort_values("symbol")[["symbol", "start_date"]].to_records(index=False)

    print(f"Extending OI Vision metrics: {len(symbols)} PIT symbols, workers={args.workers}, "
          f"min_free_gb={args.min_free_gb}", flush=True)

    t0 = time.time()
    results = []
    for i, (sym, start) in enumerate(symbols, 1):
        headroom = free_gb(ROOT)
        if headroom < args.min_free_gb:
            print(f"\nSTOP: free space {headroom:.1f}GB < --min-free-gb {args.min_free_gb}GB "
                  f"after {i - 1}/{len(symbols)} symbols. Manifest is resumable -- re-run this "
                  f"script once space is freed.", flush=True)
            sys.exit(1)
        r = backfill_symbol(sym, start, end, workers=args.workers)
        results.append(r)
        print(f"  [{i:3}/{len(symbols)}] {sym:14} start={start} new={r.get('new', 0):5} "
              f"404={r.get('n404', 0):5} err={r.get('errors', 0):3} rows={r.get('rows_total', '-')} "
              f"free={headroom:.1f}GB", flush=True)

    done = sum(1 for r in results if r.get("rows_total"))
    print(f"\nDone: {done}/{len(symbols)} symbols with data, elapsed {time.time() - t0:.0f}s "
          f"-> {OUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
