#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from market_physics_v3.phase5_audit import load_parquet_dataset
from market_physics_v3.phase5_mechanism import run_mechanism_diagnostics, write_mechanism_diagnostics


def _csv(value):
    return [x.strip() for x in str(value).split(",") if x.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", required=True)
    ap.add_argument("--mechanisms", required=True)
    ap.add_argument("--cadence-ms", type=int, default=100)
    ap.add_argument("--classifications", default="GENERAL_CANDIDATE")
    ap.add_argument("--out", default="reports/market_physics_v3/phase5_1_mechanisms")
    args = ap.parse_args()

    print("[phase5.1] loading tape %s" % args.tape, flush=True)
    frame = load_parquet_dataset(args.tape)
    mechanisms = pd.read_csv(args.mechanisms)
    print(
        "[phase5.1] rows=%s mechanisms=%s selected=%s"
        % (len(frame), len(mechanisms), _csv(args.classifications)),
        flush=True,
    )
    result = run_mechanism_diagnostics(
        frame,
        mechanisms,
        cadence_ms=args.cadence_ms,
        classifications=_csv(args.classifications),
        progress=True,
    )
    paths = write_mechanism_diagnostics(result, args.out)
    print(json.dumps(result["summary"], indent=2, sort_keys=True))
    print(paths["summary"])
    print()
    print("MECHANISM DIAGNOSTICS")
    print(result["mechanisms"].to_string(index=False))
    print()
    print("SYMBOL DIAGNOSTICS")
    cols = [
        "feature", "horizon_ms", "symbol", "ic", "reverse_ic", "momentum_ic",
        "partial_ic_controlling_past", "loo_ic", "loo_partial_ic",
        "third1_ic", "third2_ic", "third3_ic", "past_up_ic", "past_down_ic",
        "top_minus_bottom_bps", "confound_flags",
    ]
    print(result["diagnostics"][cols].to_string(index=False))


if __name__ == "__main__":
    main()
