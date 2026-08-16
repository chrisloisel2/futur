#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from market_physics_v3.phase5_audit import load_parquet_dataset
from market_physics_v3.phase5_confirm import run_locked_confirmation, write_locked_confirmation


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", required=True)
    ap.add_argument("--cadence-ms", type=int, default=100)
    ap.add_argument("--min-duration-hours", type=float, default=12.0)
    ap.add_argument("--block-shuffle-repeats", type=int, default=100)
    ap.add_argument("--out", default="reports/market_physics_v3/phase5_2_confirm")
    args = ap.parse_args()

    print("[phase5.2] loading %s" % args.tape, flush=True)
    frame = load_parquet_dataset(args.tape)
    print("[phase5.2] loaded rows=%s columns=%s" % (len(frame), len(frame.columns)), flush=True)
    result = run_locked_confirmation(
        frame,
        cadence_ms=args.cadence_ms,
        min_duration_hours=args.min_duration_hours,
        block_shuffle_repeats=args.block_shuffle_repeats,
        progress=True,
    )
    paths = write_locked_confirmation(result, args.out)
    print(json.dumps(result["summary"], indent=2, sort_keys=True))
    print(paths["symbols"])
    print(paths["summary"])
    print()
    print("SYMBOL CONFIRMATION")
    print(result["symbols"].to_string(index=False))


if __name__ == "__main__":
    main()
