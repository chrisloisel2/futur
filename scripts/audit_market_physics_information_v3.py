#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from market_physics_v3.phase5_audit import DEFAULT_HORIZONS_MS, load_parquet_dataset, run_information_audit


def _ints(value):
    return [int(x.strip()) for x in str(value).split(",") if x.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", required=True, help="Parquet file or streaming cadence directory with part-*.parquet")
    ap.add_argument("--cadence-ms", type=int, default=100)
    ap.add_argument("--horizons-ms", default=",".join(str(x) for x in DEFAULT_HORIZONS_MS))
    ap.add_argument("--min-duration-hours", type=float, default=6.0)
    ap.add_argument("--allow-short-smoke", action="store_true")
    ap.add_argument("--block-shuffle-repeats", type=int, default=100)
    ap.add_argument("--max-block-shortlist", type=int, default=40)
    ap.add_argument("--out", default="reports/market_physics_v3/phase5_information")
    args = ap.parse_args()

    print("[phase5] loading tape %s" % args.tape, flush=True)
    frame = load_parquet_dataset(args.tape)
    print("[phase5] loaded rows=%s columns=%s" % (len(frame), len(frame.columns)), flush=True)
    result = run_information_audit(
        frame,
        cadence_ms=args.cadence_ms,
        horizons_ms=_ints(args.horizons_ms),
        min_duration_hours=args.min_duration_hours,
        allow_short_smoke=args.allow_short_smoke,
        block_shuffle_repeats=args.block_shuffle_repeats,
        max_block_shortlist=(0 if args.allow_short_smoke else args.max_block_shortlist),
        progress=True,
    )
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tests = result.pop("tests")
    mechanisms = result.pop("mechanisms")

    # A short smoke exists only to test the pipeline. It is never allowed to
    # emit a research candidate, even if a chance correlation looks strong.
    if result.get("verdict") == "SHORT_SMOKE_ONLY":
        if "symbol_candidate" in tests:
            tests["symbol_candidate"] = False
        if "classification" in mechanisms:
            mechanisms["classification"] = "NO_CANDIDATE_YET"
            mechanisms["candidate_symbols"] = 0
        result["general_candidates"] = 0
        result["single_symbol_watches"] = 0

    tests_path = out / "feature_tests.csv"
    mechanisms_path = out / "mechanisms.csv"
    summary_path = out / "SUMMARY.json"
    tests.to_csv(tests_path, index=False)
    mechanisms.to_csv(mechanisms_path, index=False)
    summary_path.write_text(json.dumps(result, indent=2, sort_keys=True))

    print(json.dumps(result, indent=2, sort_keys=True))
    print(tests_path)
    print(mechanisms_path)
    print(summary_path)
    print()
    print("TOP MECHANISMS")
    if mechanisms.empty:
        print("none")
    else:
        print(mechanisms.head(30).to_string(index=False))


if __name__ == "__main__":
    main()
