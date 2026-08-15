#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Direct script execution starts with `scripts/` on sys.path.  Add the repo
# root before importing market_physics_v3 so the collector works outside
# pytest and without requiring callers to export PYTHONPATH manually.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from market_physics_v3.collectors.runtime import run_many


async def _run(venues, symbols, root, health_dir, duration_seconds):
    if duration_seconds is None or duration_seconds <= 0:
        await run_many(venues, symbols, root, health_dir)
        return
    try:
        await asyncio.wait_for(
            run_many(venues, symbols, root, health_dir), timeout=float(duration_seconds)
        )
    except asyncio.TimeoutError:
        # A bounded smoke ending by timeout is success; run_many cancels and
        # flushes every append-only writer in its finally blocks.
        return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--venues", default="binance,bybit,okx,hyperliquid")
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    ap.add_argument("--root", default="data/market_physics_v3")
    ap.add_argument("--health-dir", default="reports/market_physics_v3/health")
    ap.add_argument("--duration-seconds", type=float, default=None)
    args = ap.parse_args()
    venues = [x.strip().lower() for x in args.venues.split(",") if x.strip()]
    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    asyncio.run(_run(venues, symbols, args.root, args.health_dir, args.duration_seconds))


if __name__ == "__main__":
    main()
